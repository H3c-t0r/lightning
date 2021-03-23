# Copyright The PyTorch Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
import os
import platform
import time
from copy import deepcopy
from distutils.version import LooseVersion
from pathlib import Path

import numpy as np
import pytest
import torch

from pytorch_lightning import Callback, Trainer
from pytorch_lightning.profiler import AdvancedProfiler, PyTorchProfiler, SimpleProfiler
from pytorch_lightning.profiler.pytorch import RegisterRecordFunction
from pytorch_lightning.utilities.exceptions import MisconfigurationException
from pytorch_lightning.utilities.imports import _TORCH_GREATER_EQUAL_1_8
from tests.helpers import BoringModel
from tests.helpers.runif import RunIf

PROFILER_OVERHEAD_MAX_TOLERANCE = 0.0005


def _get_python_cprofile_total_duration(profile):
    return sum([x.inlinetime for x in profile.getstats()])


def _sleep_generator(durations):
    """
    the profile_iterable method needs an iterable in which we can ensure that we're
    properly timing how long it takes to call __next__
    """
    for duration in durations:
        time.sleep(duration)
        yield duration


@pytest.fixture
def simple_profiler():
    return SimpleProfiler()


@pytest.mark.parametrize(["action", "expected"], [
    pytest.param("a", [3, 1]),
    pytest.param("b", [2]),
    pytest.param("c", [1]),
])
def test_simple_profiler_durations(simple_profiler, action: str, expected: list):
    """Ensure the reported durations are reasonably accurate."""

    for duration in expected:
        with simple_profiler.profile(action):
            time.sleep(duration)

    # different environments have different precision when it comes to time.sleep()
    # see: https://github.com/PyTorchLightning/pytorch-lightning/issues/796
    np.testing.assert_allclose(simple_profiler.recorded_durations[action], expected, rtol=0.2)


@pytest.mark.parametrize(["action", "expected"], [
    pytest.param("a", [3, 1]),
    pytest.param("b", [2]),
    pytest.param("c", [1]),
])
def test_simple_profiler_iterable_durations(simple_profiler, action: str, expected: list):
    """Ensure the reported durations are reasonably accurate."""
    iterable = _sleep_generator(expected)

    for _ in simple_profiler.profile_iterable(iterable, action):
        pass

    # we exclude the last item in the recorded durations since that's when StopIteration is raised
    np.testing.assert_allclose(simple_profiler.recorded_durations[action][:-1], expected, rtol=0.2)


def test_simple_profiler_overhead(simple_profiler, n_iter=5):
    """Ensure that the profiler doesn't introduce too much overhead during training."""
    for _ in range(n_iter):
        with simple_profiler.profile("no-op"):
            pass

    durations = np.array(simple_profiler.recorded_durations["no-op"])
    assert all(durations < PROFILER_OVERHEAD_MAX_TOLERANCE)


def test_simple_profiler_value_errors(simple_profiler):
    """Ensure errors are raised where expected."""

    action = "test"
    with pytest.raises(ValueError):
        simple_profiler.stop(action)

    simple_profiler.start(action)

    with pytest.raises(ValueError):
        simple_profiler.start(action)

    simple_profiler.stop(action)


def test_simple_profiler_deepcopy(tmpdir):
    simple_profiler = SimpleProfiler(dirpath=tmpdir, filename="test")
    simple_profiler.describe()
    assert deepcopy(simple_profiler)


def test_simple_profiler_log_dir(tmpdir):
    """Ensure the profiler dirpath defaults to `trainer.log_dir` when not present"""
    profiler = SimpleProfiler(filename="profiler")
    assert profiler._log_dir is None

    model = BoringModel()
    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        profiler=profiler,
    )
    trainer.fit(model)

    expected = profiler.dirpath
    assert trainer.log_dir == expected
    assert profiler._log_dir == trainer.log_dir
    assert Path(os.path.join(profiler.dirpath, "fit-profiler.txt")).exists()


@RunIf(skip_windows=True)
def test_simple_profiler_distributed_files(tmpdir):
    """Ensure the proper files are saved in distributed"""
    profiler = SimpleProfiler(dirpath=tmpdir, filename='profiler')
    model = BoringModel()
    trainer = Trainer(
        default_root_dir=tmpdir,
        fast_dev_run=2,
        accelerator="ddp_cpu",
        num_processes=2,
        profiler=profiler,
        logger=False,
    )
    trainer.fit(model)
    trainer.validate(model)
    trainer.test(model)

    actual = set(os.listdir(profiler.dirpath))
    expected = {f"{stage}-profiler-{rank}.txt" for stage in ("fit", "validate", "test") for rank in (0, 1)}
    assert actual == expected

    for f in profiler.dirpath.listdir():
        assert f.read_text('utf-8')


def test_simple_profiler_logs(tmpdir, caplog, simple_profiler):
    """Ensure that the number of printed logs is correct"""
    model = BoringModel()
    trainer = Trainer(
        default_root_dir=tmpdir,
        fast_dev_run=2,
        profiler=simple_profiler,
        logger=False,
    )
    with caplog.at_level(logging.INFO, logger="pytorch_lightning.profiler.profilers"):
        trainer.fit(model)
        trainer.test(model)

    assert caplog.text.count("Profiler Report") == 2


@pytest.fixture
def advanced_profiler(tmpdir):
    return AdvancedProfiler(dirpath=tmpdir, filename="profiler")


@pytest.mark.parametrize(["action", "expected"], [
    pytest.param("a", [3, 1]),
    pytest.param("b", [2]),
    pytest.param("c", [1]),
])
def test_advanced_profiler_durations(advanced_profiler, action: str, expected: list):

    for duration in expected:
        with advanced_profiler.profile(action):
            time.sleep(duration)

    # different environments have different precision when it comes to time.sleep()
    # see: https://github.com/PyTorchLightning/pytorch-lightning/issues/796
    recored_total_duration = _get_python_cprofile_total_duration(advanced_profiler.profiled_actions[action])
    expected_total_duration = np.sum(expected)
    np.testing.assert_allclose(recored_total_duration, expected_total_duration, rtol=0.2)


@pytest.mark.parametrize(["action", "expected"], [
    pytest.param("a", [3, 1]),
    pytest.param("b", [2]),
    pytest.param("c", [1]),
])
def test_advanced_profiler_iterable_durations(advanced_profiler, action: str, expected: list):
    """Ensure the reported durations are reasonably accurate."""
    iterable = _sleep_generator(expected)

    for _ in advanced_profiler.profile_iterable(iterable, action):
        pass

    recored_total_duration = _get_python_cprofile_total_duration(advanced_profiler.profiled_actions[action])
    expected_total_duration = np.sum(expected)
    np.testing.assert_allclose(recored_total_duration, expected_total_duration, rtol=0.2)


def test_advanced_profiler_overhead(advanced_profiler, n_iter=5):
    """
    ensure that the profiler doesn't introduce too much overhead during training
    """
    for _ in range(n_iter):
        with advanced_profiler.profile("no-op"):
            pass

    action_profile = advanced_profiler.profiled_actions["no-op"]
    total_duration = _get_python_cprofile_total_duration(action_profile)
    average_duration = total_duration / n_iter
    assert average_duration < PROFILER_OVERHEAD_MAX_TOLERANCE


def test_advanced_profiler_describe(tmpdir, advanced_profiler):
    """
    ensure the profiler won't fail when reporting the summary
    """
    # record at least one event
    with advanced_profiler.profile("test"):
        pass
    # log to stdout and print to file
    advanced_profiler.describe()
    path = advanced_profiler.dirpath / f"{advanced_profiler.filename}.txt"
    data = path.read_text("utf-8")
    assert len(data) > 0


def test_advanced_profiler_value_errors(advanced_profiler):
    """Ensure errors are raised where expected."""

    action = "test"
    with pytest.raises(ValueError):
        advanced_profiler.stop(action)

    advanced_profiler.start(action)
    advanced_profiler.stop(action)


def test_advanced_profiler_deepcopy(advanced_profiler):
    advanced_profiler.describe()
    assert deepcopy(advanced_profiler)


@pytest.fixture
def pytorch_profiler(tmpdir):
    return PyTorchProfiler(dirpath=tmpdir, filename="profiler")


@pytest.mark.skipif(_TORCH_GREATER_EQUAL_1_8, reason="This feature isn't support with PyTorch 1.8 profiler")
def test_pytorch_profiler_describe(pytorch_profiler):
    """Ensure the profiler won't fail when reporting the summary."""
    pytorch_profiler.start("on_test_start")
    with pytorch_profiler.profile("test_step"):
        pass

    # log to stdout and print to file
    pytorch_profiler.describe()
    path = pytorch_profiler.dirpath / f"{pytorch_profiler.filename}.txt"
    data = path.read_text("utf-8")
    assert len(data) > 0


def test_pytorch_profiler_value_errors(pytorch_profiler):
    """Ensure errors are raised where expected."""
    action = "test_step"
    pytorch_profiler.start(action)
    pytorch_profiler.stop(action)

    with pytest.raises(MisconfigurationException, match="profiled_functions` and `PyTorchProfiler.record"):
        PyTorchProfiler(profiled_functions=["a"], record_functions=["b"])
    pytorch_profiler.teardown()


@RunIf(min_torch="1.6.0")
def test_advanced_profiler_cprofile_deepcopy(tmpdir):
    """Checks for pickle issue reported in #6522"""
    model = BoringModel()
    trainer = Trainer(
        default_root_dir=tmpdir,
        fast_dev_run=True,
        profiler="advanced",
        stochastic_weight_avg=True,
    )
    trainer.fit(model)


@RunIf(min_gpus=2, special=True)
def test_pytorch_profiler_trainer_ddp(tmpdir):
    """Ensure that the profiler can be given to the training and default step are properly recorded. """
    pytorch_profiler = PyTorchProfiler(dirpath=None, filename="profiler")
    model = BoringModel()
    trainer = Trainer(
        max_epochs=1,
        limit_train_batches=5,
        limit_val_batches=5,
        profiler=pytorch_profiler,
        accelerator="ddp",
        gpus=2,
    )
    trainer.fit(model)

    if not _TORCH_GREATER_EQUAL_1_8:
        expected = ('validation_step', 'training_step_and_backward', 'training_step', 'backward')
        for name in expected:
            assert len([e for e in pytorch_profiler.function_events if name == e.name]) > 0

        assert len(pytorch_profiler.summary()) > 0
        assert set(pytorch_profiler.profiled_actions) == {'training_step_and_backward', 'validation_step'}

        files = sorted(f for f in os.listdir(pytorch_profiler.dirpath) if "fit" in f)
        rank = int(os.getenv("LOCAL_RANK", "0"))
        expected = f"fit-profiler-{rank}.txt"
        assert files[rank] == expected

        path = os.path.join(pytorch_profiler.dirpath, expected)
        data = Path(path).read_text("utf-8")
        assert len(data) > 0
    else:
        files = os.listdir(tmpdir if pytorch_profiler == PyTorchProfiler else trainer.profiler.path_to_export_trace)
        files = sorted([file for file in files if file.endswith('.json')])
        assert 'training_step_and_backward_0' in files[0]
        assert 'validation_step_0' in files[1]
        assert len(files) == 2


def test_pytorch_profiler_trainer_test(tmpdir):
    """Ensure that the profiler can be given to the trainer and test step are properly recorded. """
    pytorch_profiler = PyTorchProfiler(
        output_filename=os.path.join(tmpdir, "profiler.txt"), local_rank=0, path_to_export_trace=tmpdir
    )
    model = BoringModel()
    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        limit_test_batches=10,
        profiler=pytorch_profiler,
    )
    trainer.test(model)

    if not _TORCH_GREATER_EQUAL_1_8:
        assert len([e for e in pytorch_profiler.function_events if 'test_step' == e.name]) > 0
        path = pytorch_profiler.dirpath / f"test-{pytorch_profiler.filename}.txt"
        assert path.read_text("utf-8")
    else:
        files = sorted([file for file in os.listdir(tmpdir) if file.endswith('.json')])
        assert 'test_step_0' in files[0]


def test_pytorch_profiler_trainer_predict(tmpdir):
    """Ensure that the profiler can be given to the trainer and predict function are properly recorded. """
    pytorch_profiler = PyTorchProfiler(
        output_filename=os.path.join(tmpdir, "profiler.txt"), local_rank=0, path_to_export_trace=tmpdir
    )
    model = BoringModel()
    model.predict_dataloader = model.train_dataloader
    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        limit_test_batches=2,
        profiler=pytorch_profiler,
    )
    trainer.predict(model)

    if not _TORCH_GREATER_EQUAL_1_8:
        assert len([e for e in pytorch_profiler.function_events if 'predict' == e.name]) > 0
        data = Path(pytorch_profiler.output_fname).read_text()
        assert len(data) > 0
    else:
        files = sorted([file for file in os.listdir(tmpdir) if file.endswith('.json')])
        assert 'predict_0' in files[0]


@RunIf(min_gpus=1, special=True)
@pytest.mark.skipif(_TORCH_GREATER_EQUAL_1_8, reason="This feature isn't support with PyTorch 1.8 profiler")
def test_pytorch_profiler_nested_emit_nvtx(tmpdir):
    """
    This test check emit_nvtx is correctly supported
    """
    pytorch_profiler = PyTorchProfiler(use_cuda=True, emit_nvtx=True)

    model = BoringModel()
    trainer = Trainer(
        default_root_dir=tmpdir,
        fast_dev_run=True,
        profiler=pytorch_profiler,
        gpus=1,
    )
    trainer.fit(model)
    path = pytorch_profiler.dirpath / f"predict-{pytorch_profiler.filename}.txt"
    assert path.read_text("utf-8")


def test_pytorch_profiler_trainer_validate(tmpdir, pytorch_profiler):
    """Ensure that the profiler can be given to the trainer and validate function are properly recorded. """
    model = BoringModel()
    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        limit_test_batches=2,
        profiler=pytorch_profiler,
    )
    trainer.validate(model)

    assert len([e for e in pytorch_profiler.function_events if 'validation_step' == e.name]) > 0

    path = pytorch_profiler.dirpath / f"validate-{pytorch_profiler.filename}.txt"
    assert path.read_text("utf-8")


def test_pytorch_profiler_nested(tmpdir):
    """Ensure that the profiler handles nested context"""

    pytorch_profiler = PyTorchProfiler(
        profiled_functions=["a", "b", "c"], use_cuda=False, dirpath=tmpdir, filename="profiler"
    )

    with pytorch_profiler.profile("a"):
        a = torch.ones(42)
        with pytorch_profiler.profile("b"):
            b = torch.zeros(42)
        with pytorch_profiler.profile("c"):
            _ = a + b

    pytorch_profiler.describe()

    events_name = {e.name for e in pytorch_profiler.function_events}

    if platform.system() == "Windows":
        expected = {'a', 'add', 'b', 'c', 'profiler::_record_function_enter', 'profiler::_record_function_exit'}
    else:
        expected = {
            'signed char', 'add', 'profiler::_record_function_exit', 'bool', 'char', 'profiler::_record_function_enter'
        }

    if LooseVersion(torch.__version__) >= LooseVersion("1.6.0"):
        expected = {'add', 'zeros', 'ones', 'zero_', 'b', 'fill_', 'c', 'a', 'empty'}

    if LooseVersion(torch.__version__) >= LooseVersion("1.7.0"):
        expected = {
            'aten::zeros', 'aten::add', 'aten::zero_', 'c', 'b', 'a', 'aten::fill_', 'aten::empty', 'aten::ones'
        }

    assert events_name == expected, (events_name, torch.__version__, platform.system())


@pytest.mark.skipif(not _TORCH_GREATER_EQUAL_1_8, reason="Need at least PyTorch 1.8")
@pytest.mark.parametrize('profiler', ('pytorch', PyTorchProfiler))
def test_pytorch_profiler_trainer_new_api(tmpdir, profiler):
    """Ensure that the profiler can be given to the training and default step are properly recorded. """

    model = BoringModel()
    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        limit_train_batches=10,
        limit_val_batches=10,
        profiler=profiler if isinstance(profiler, str) else profiler(path_to_export_trace=tmpdir),
    )
    trainer.fit(model)

    files = os.listdir(tmpdir if profiler == PyTorchProfiler else trainer.profiler.path_to_export_trace)
    files = sorted([file for file in files if file.endswith('.json')])
    assert 'training_step_and_backward_0' in files[0]
    assert 'validation_step_0' in files[1]
    assert len(files) == 2


@RunIf(min_torch="1.5.0")
def test_register_record_function(tmpdir):

    use_cuda = torch.cuda.is_available()

    pytorch_profiler = PyTorchProfiler(
        export_to_chrome=False,
        record_functions=["a"],
        use_cuda=use_cuda,
        output_filename=os.path.join(tmpdir, "profiler.txt")
    )

    class TestModel(BoringModel):

        def __init__(self):
            super().__init__()
            self.layer = torch.nn.Sequential(torch.nn.Linear(32, 32), torch.nn.ReLU(), torch.nn.Linear(32, 2))

    model = TestModel()
    input = torch.rand((1, 32))

    if use_cuda:
        model = model.cuda()
        input = input.cuda()

    with pytorch_profiler.profile("a"):
        with RegisterRecordFunction(model):
            model(input)

    pytorch_profiler.describe()
    event_names = [e.name for e in pytorch_profiler.function_events]
    assert 'torch.nn.modules.container.Sequential: layer' in event_names
    assert 'torch.nn.modules.linear.Linear: layer.0' in event_names
    assert 'torch.nn.modules.activation.ReLU: layer.1' in event_names
    assert 'torch.nn.modules.linear.Linear: layer.2' in event_names


@pytest.mark.parametrize("cls", (SimpleProfiler, AdvancedProfiler, PyTorchProfiler))
def test_profiler_teardown(tmpdir, cls):
    """
    This test checks if profiler teardown method is called when trainer is exiting.
    """

    class TestCallback(Callback):

        def on_fit_end(self, trainer, *args, **kwargs) -> None:
            # describe sets it to None
            assert trainer.profiler._output_file is None

    profiler = cls(dirpath=tmpdir, filename="profiler")
    model = BoringModel()
    trainer = Trainer(default_root_dir=tmpdir, fast_dev_run=True, profiler=profiler, callbacks=[TestCallback()])
    trainer.fit(model)

    assert profiler._output_file is None


@pytest.mark.skipif(_TORCH_GREATER_EQUAL_1_8, reason="currently not supported for PyTorch 1.8")
def test_pytorch_profiler_deepcopy(pytorch_profiler):
    pytorch_profiler.start("on_train_start")
    pytorch_profiler.describe()
    assert deepcopy(pytorch_profiler)
