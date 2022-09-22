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
"""Test deprecated functionality which will be removed in v1.8.0."""
import time
from unittest import mock
from unittest.mock import Mock

import numpy as np
import pytest

from pytorch_lightning import Callback, Trainer
from pytorch_lightning.callbacks import ModelCheckpoint
from pytorch_lightning.demos.boring_classes import BoringDataModule, BoringModel
from pytorch_lightning.loggers import CSVLogger, Logger
from pytorch_lightning.profilers import AdvancedProfiler, SimpleProfiler
from pytorch_lightning.strategies.ipu import LightningIPUModule
from pytorch_lightning.trainer.configuration_validator import _check_datamodule_checkpoint_hooks
from pytorch_lightning.trainer.states import RunningStage
from pytorch_lightning.utilities.rank_zero import rank_zero_only


def test_v1_8_0_on_init_start_end(tmpdir):
    class TestCallback(Callback):
        def on_init_start(self, trainer):
            print("Starting to init trainer!")

        def on_init_end(self, trainer):
            print("Trainer is init now")

    model = BoringModel()

    trainer = Trainer(
        callbacks=[TestCallback()],
        max_epochs=1,
        fast_dev_run=True,
        enable_progress_bar=False,
        logger=False,
        default_root_dir=tmpdir,
    )
    with pytest.deprecated_call(
        match="The `on_init_start` callback hook was deprecated in v1.6 and will be removed in v1.8"
    ):
        trainer.fit(model)
    with pytest.deprecated_call(
        match="The `on_init_end` callback hook was deprecated in v1.6 and will be removed in v1.8"
    ):
        trainer.validate(model)


def test_v1_8_0_deprecated_call_hook():
    trainer = Trainer(
        max_epochs=1,
        limit_val_batches=0.1,
        limit_train_batches=0.2,
        enable_progress_bar=False,
        logger=False,
    )
    with pytest.deprecated_call(match="was deprecated in v1.6 and will be removed in v1.8."):
        trainer.call_hook("test_hook")


def test_v1_8_0_deprecated_run_stage():
    trainer = Trainer()
    trainer._run_stage = Mock()
    with pytest.deprecated_call(match="`Trainer.run_stage` is deprecated in v1.6 and will be removed in v1.8."):
        trainer.run_stage()


def test_v1_8_0_trainer_verbose_evaluate():
    trainer = Trainer()
    with pytest.deprecated_call(match="verbose_evaluate` property has been deprecated and will be removed in v1.8"):
        assert trainer.verbose_evaluate

    with pytest.deprecated_call(match="verbose_evaluate` property has been deprecated and will be removed in v1.8"):
        trainer.verbose_evaluate = False


@pytest.mark.parametrize("fn_prefix", ["validated", "tested", "predicted"])
def test_v1_8_0_trainer_ckpt_path_attributes(fn_prefix: str):
    test_attr = f"{fn_prefix}_ckpt_path"
    trainer = Trainer()
    with pytest.deprecated_call(match=f"{test_attr}` attribute was deprecated in v1.6 and will be removed in v1.8"):
        _ = getattr(trainer, test_attr)
    with pytest.deprecated_call(match=f"{test_attr}` attribute was deprecated in v1.6 and will be removed in v1.8"):
        setattr(trainer, test_attr, "v")


def test_v1_8_0_deprecated_trainer_should_rank_save_checkpoint(tmpdir):
    trainer = Trainer()
    with pytest.deprecated_call(
        match=r"`Trainer.should_rank_save_checkpoint` is deprecated in v1.6 and will be removed in v1.8."
    ):
        _ = trainer.should_rank_save_checkpoint


def test_v1_8_0_trainer_optimizers_mixin():
    trainer = Trainer()
    model = BoringModel()
    trainer.strategy.connect(model)
    trainer.lightning_module.trainer = trainer

    with pytest.deprecated_call(
        match=r"`TrainerOptimizersMixin.init_optimizers` was deprecated in v1.6 and will be removed in v1.8."
    ):
        trainer.init_optimizers(model)

    with pytest.deprecated_call(
        match=r"`TrainerOptimizersMixin.convert_to_lightning_optimizers` was deprecated in v1.6 and will be removed in "
        "v1.8."
    ):
        trainer.convert_to_lightning_optimizers()


def test_v1_8_0_deprecate_trainer_data_loading_mixin():
    trainer = Trainer(max_epochs=1)
    model = BoringModel()
    dm = BoringDataModule()
    trainer.fit(model, datamodule=dm)

    with pytest.deprecated_call(
        match=r"`TrainerDataLoadingMixin.prepare_dataloader` was deprecated in v1.6 and will be removed in v1.8.",
    ):
        trainer.prepare_dataloader(dataloader=model.train_dataloader, shuffle=False)
    with pytest.deprecated_call(
        match=r"`TrainerDataLoadingMixin.request_dataloader` was deprecated in v1.6 and will be removed in v1.8.",
    ):
        trainer.request_dataloader(stage=RunningStage.TRAINING)


def test_v_1_8_0_deprecated_device_stats_monitor_prefix_metric_keys():
    from pytorch_lightning.callbacks.device_stats_monitor import prefix_metric_keys

    with pytest.deprecated_call(match="in v1.6 and will be removed in v1.8"):
        prefix_metric_keys({"foo": 1.0}, "bar")


def test_v1_8_0_deprecated_lightning_optimizers():
    trainer = Trainer()
    with pytest.deprecated_call(
        match="Trainer.lightning_optimizers` is deprecated in v1.6 and will be removed in v1.8"
    ):
        assert trainer.lightning_optimizers == {}


def test_v1_8_0_logger_agg_parameters():
    class CustomLogger(Logger):
        @rank_zero_only
        def log_hyperparams(self, params):
            pass

        @rank_zero_only
        def log_metrics(self, metrics, step):
            pass

        @property
        def name(self):
            pass

        @property
        def version(self):
            pass

    with pytest.deprecated_call(
        match="The `agg_key_funcs` parameter for `Logger` was deprecated in v1.6" " and will be removed in v1.8."
    ):
        CustomLogger(agg_key_funcs={"mean", np.mean})

    with pytest.deprecated_call(
        match="The `agg_default_func` parameter for `Logger` was deprecated in v1.6" " and will be removed in v1.8."
    ):
        CustomLogger(agg_default_func=np.mean)

    # Should have no deprecation warning
    logger = CustomLogger()

    with pytest.deprecated_call(match="`Logger.update_agg_funcs` was deprecated in v1.6 and will be removed in v1.8."):
        logger.update_agg_funcs()


def test_v1_8_0_deprecated_agg_and_log_metrics_override(tmpdir):
    class AggregationOverrideLogger(CSVLogger):
        @rank_zero_only
        def agg_and_log_metrics(self, metrics, step):
            self.log_metrics(metrics=metrics, step=step)

    logger = AggregationOverrideLogger(tmpdir)
    logger2 = CSVLogger(tmpdir)
    logger3 = CSVLogger(tmpdir)

    # Test single loggers
    with pytest.deprecated_call(
        match="`Logger.agg_and_log_metrics` is deprecated in v1.6 and will be removed"
        " in v1.8. `Trainer` will directly call `Logger.log_metrics` so custom"
        " loggers should not implement `Logger.agg_and_log_metrics`."
    ):
        Trainer(logger=logger)
    # Should have no deprecation warning
    Trainer(logger=logger2)

    # Test multiple loggers
    with pytest.deprecated_call(
        match="`Logger.agg_and_log_metrics` is deprecated in v1.6 and will be removed"
        " in v1.8. `Trainer` will directly call `Logger.log_metrics` so custom"
        " loggers should not implement `Logger.agg_and_log_metrics`."
    ):
        Trainer(logger=[logger, logger3])
    # Should have no deprecation warning
    Trainer(logger=[logger2, logger3])


@pytest.mark.flaky(reruns=3)
@pytest.mark.parametrize(["action", "expected"], [("a", [3, 1]), ("b", [2]), ("c", [1])])
def test_simple_profiler_iterable_durations(tmpdir, action: str, expected: list):
    """Ensure the reported durations are reasonably accurate."""

    def _sleep_generator(durations):
        """the profile_iterable method needs an iterable in which we can ensure that we're properly timing how long
        it takes to call __next__"""
        for duration in durations:
            time.sleep(duration)
            yield duration

    def _get_python_cprofile_total_duration(profile):
        return sum(x.inlinetime for x in profile.getstats())

    simple_profiler = SimpleProfiler()
    iterable = _sleep_generator(expected)

    with pytest.deprecated_call(
        match="`SimpleProfiler.profile_iterable` is deprecated in v1.6 and will be removed in v1.8."
    ):
        for _ in simple_profiler.profile_iterable(iterable, action):
            pass

    # we exclude the last item in the recorded durations since that's when StopIteration is raised
    np.testing.assert_allclose(simple_profiler.recorded_durations[action][:-1], expected, rtol=0.2)

    advanced_profiler = AdvancedProfiler(dirpath=tmpdir, filename="profiler")

    iterable = _sleep_generator(expected)

    with pytest.deprecated_call(
        match="`AdvancedProfiler.profile_iterable` is deprecated in v1.6 and will be removed in v1.8."
    ):
        for _ in advanced_profiler.profile_iterable(iterable, action):
            pass

    recorded_total_duration = _get_python_cprofile_total_duration(advanced_profiler.profiled_actions[action])
    expected_total_duration = np.sum(expected)
    np.testing.assert_allclose(recorded_total_duration, expected_total_duration, rtol=0.2)


def test_v1_8_0_datamodule_checkpointhooks():
    class CustomBoringDataModuleSave(BoringDataModule):
        def on_save_checkpoint(self, checkpoint):
            print("override on_save_checkpoint")

    class CustomBoringDataModuleLoad(BoringDataModule):
        def on_load_checkpoint(self, checkpoint):
            print("override on_load_checkpoint")

    trainer = Mock()

    trainer.datamodule = CustomBoringDataModuleSave()
    with pytest.deprecated_call(
        match="`LightningDataModule.on_save_checkpoint` was deprecated in"
        " v1.6 and will be removed in v1.8. Use `state_dict` instead."
    ):
        _check_datamodule_checkpoint_hooks(trainer)

    trainer.datamodule = CustomBoringDataModuleLoad()
    with pytest.deprecated_call(
        match="`LightningDataModule.on_load_checkpoint` was deprecated in"
        " v1.6 and will be removed in v1.8. Use `load_state_dict` instead."
    ):
        _check_datamodule_checkpoint_hooks(trainer)


def test_v1_8_0_deprecated_lightning_ipu_module():
    with pytest.deprecated_call(match=r"has been deprecated in v1.7.0 and will be removed in v1.8."):
        _ = LightningIPUModule(BoringModel(), 32)


def test_deprecated_mc_save_checkpoint():
    mc = ModelCheckpoint()
    trainer = Trainer()
    with mock.patch.object(trainer, "save_checkpoint"), pytest.deprecated_call(
        match=r"ModelCheckpoint.save_checkpoint\(\)` was deprecated in v1.6"
    ):
        mc.save_checkpoint(trainer)


def test_v1_8_0_callback_on_load_checkpoint_hook(tmpdir):
    class TestCallbackLoadHook(Callback):
        def on_load_checkpoint(self, trainer, pl_module, callback_state):
            print("overriding on_load_checkpoint")

    model = BoringModel()
    trainer = Trainer(
        callbacks=[TestCallbackLoadHook()],
        max_epochs=1,
        fast_dev_run=True,
        enable_progress_bar=False,
        logger=False,
        default_root_dir=tmpdir,
    )
    with pytest.deprecated_call(
        match="`TestCallbackLoadHook.on_load_checkpoint` will change its signature and behavior in v1.8."
        " If you wish to load the state of the callback, use `load_state_dict` instead."
        r" In v1.8 `on_load_checkpoint\(..., checkpoint\)` will receive the entire loaded"
        " checkpoint dictionary instead of callback state."
    ):
        trainer.fit(model)


def test_v1_8_0_callback_on_save_checkpoint_hook(tmpdir):
    class TestCallbackSaveHookReturn(Callback):
        def on_save_checkpoint(self, trainer, pl_module, checkpoint):
            return {"returning": "on_save_checkpoint"}

    class TestCallbackSaveHookOverride(Callback):
        def on_save_checkpoint(self, trainer, pl_module, checkpoint):
            print("overriding without returning")

    model = BoringModel()
    trainer = Trainer(
        callbacks=[TestCallbackSaveHookReturn()],
        max_epochs=1,
        fast_dev_run=True,
        enable_progress_bar=False,
        logger=False,
        default_root_dir=tmpdir,
    )
    trainer.fit(model)
    with pytest.deprecated_call(
        match="Returning a value from `TestCallbackSaveHookReturn.on_save_checkpoint` is deprecated in v1.6"
        " and will be removed in v1.8. Please override `Callback.state_dict`"
        " to return state to be saved."
    ):
        trainer.save_checkpoint(tmpdir + "/path.ckpt")

    trainer.callbacks = [TestCallbackSaveHookOverride()]
    trainer.save_checkpoint(tmpdir + "/pathok.ckpt")
