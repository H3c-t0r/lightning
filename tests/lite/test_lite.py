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

from copy import deepcopy
from unittest import mock
from unittest.mock import Mock, PropertyMock

import pytest
import torch
import torch.distributed
import torch.nn.functional
from torch import nn
from torch.utils.data import DataLoader, DistributedSampler, Sampler

from pytorch_lightning import seed_everything
from pytorch_lightning.accelerators import Accelerator
from pytorch_lightning.lite import LightningLite
from pytorch_lightning.lite.wrappers import _LiteDataLoader
from pytorch_lightning.plugins import DeepSpeedPlugin, PrecisionPlugin, TrainingTypePlugin
from pytorch_lightning.utilities.exceptions import MisconfigurationException
from tests.helpers.boring_model import RandomDataset
from tests.helpers.runif import RunIf


class EmptyLite(LightningLite):
    def run(self):
        pass


class BoringModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.layer = torch.nn.Linear(32, 2, bias=False)

    def forward(self, x):
        x = self.layer(x)
        return torch.nn.functional.mse_loss(x, torch.ones_like(x))


def configure_optimizers(module: nn.Module):
    return torch.optim.SGD(module.parameters(), lr=0.0001)


@pytest.mark.parametrize("accelerator", ["coconut", Mock(spec=Accelerator)])
def test_unsupported_accelerator(accelerator):
    with pytest.raises(MisconfigurationException, match=f"`accelerator={repr(accelerator)}` is not a valid choice"):
        EmptyLite(accelerator=accelerator)


@pytest.mark.parametrize("strategy", ["coconut", Mock(spec=TrainingTypePlugin)])
def test_unsupported_strategy(strategy):
    with pytest.raises(MisconfigurationException, match=f"`strategy={repr(strategy)}` is not a valid choice"):
        EmptyLite(strategy=strategy)


def test_setup_dataloaders_return_type():
    """Test that the setup method returns the dataloaders wrapped as LiteDataLoader and in the right order."""
    lite = EmptyLite()

    # single dataloader
    lite_dataloader = lite.setup_dataloaders(DataLoader(range(2)))
    assert isinstance(lite_dataloader, _LiteDataLoader)

    # multiple dataloaders
    dataset0 = Mock()
    dataset1 = Mock()
    dataloader0 = DataLoader(dataset0)
    dataloader1 = DataLoader(dataset1)
    lite_dataloader0, lite_dataloader1 = lite.setup_dataloaders(dataloader0, dataloader1)
    assert isinstance(lite_dataloader0, _LiteDataLoader)
    assert isinstance(lite_dataloader1, _LiteDataLoader)
    assert lite_dataloader0.dataset is dataset0
    assert lite_dataloader1.dataset is dataset1


def test_lite_with_iterable():
    """Test that the setup_dataloaders method fails when provided with an iterable."""
    lite = EmptyLite()
    with pytest.raises(MisconfigurationException, match="Only PyTorch DataLoader are currently supported"):
        lite.setup_dataloaders(range(2))


@mock.patch(
    "pytorch_lightning.lite.lite.LightningLite.device",
    new_callable=PropertyMock,
    return_value=torch.device("cuda", 1),
)
def test_setup_dataloaders_move_to_device(lite_device_mock):
    """Test that the setup configures LiteDataLoader to move the data to the device automatically."""
    lite = EmptyLite()
    lite_dataloaders = lite.setup_dataloaders(DataLoader(Mock()), DataLoader(Mock()), move_to_device=False)
    assert all(dl.device is None for dl in lite_dataloaders)
    lite_device_mock.assert_not_called()

    lite = EmptyLite()
    lite_dataloaders = lite.setup_dataloaders(DataLoader(Mock()), DataLoader(Mock()), move_to_device=True)
    assert all(dl.device == torch.device("cuda", 1) for dl in lite_dataloaders)
    lite_device_mock.assert_called()


def test_setup_dataloaders_distributed_sampler_not_needed():
    """Test that replace_sampler option has no effect when no distributed sampler is needed."""
    custom_sampler = Mock(spec=Sampler)
    dataloader = DataLoader(Mock(), sampler=custom_sampler)

    # keep the custom sampler when not needed to replace
    lite = EmptyLite()
    lite_dataloader = lite.setup_dataloaders(dataloader, replace_sampler=True)
    assert lite_dataloader.sampler is custom_sampler


@pytest.mark.parametrize("strategy", LightningLite._supported_strategy_types())
def test_setup_dataloaders_replace_custom_sampler(strategy):
    """Test that asking to replace a custom sampler results in an error when a distributed sampler would be
    needed."""
    custom_sampler = Mock(spec=Sampler)
    dataloader = DataLoader(Mock(), sampler=custom_sampler)

    # explicitly asking to replace when a custom sampler is already configured raises an exception
    lite = EmptyLite(accelerator="cpu", strategy=strategy, devices=2)
    if lite._accelerator_connector.is_distributed:
        with pytest.raises(MisconfigurationException, match="You seem to have configured a sampler in your DataLoader"):
            lite.setup_dataloaders(dataloader, replace_sampler=True)

    # setting `replace_sampler=False` leaves the sampler untouched
    lite_dataloader = lite.setup_dataloaders(dataloader, replace_sampler=False)
    assert lite_dataloader.sampler is custom_sampler


@pytest.mark.parametrize("strategy", LightningLite._supported_strategy_types())
@pytest.mark.parametrize("shuffle", [True, False])
def test_setup_dataloaders_replace_standard_sampler(shuffle, strategy):
    """Test that Lite replaces the default samplers with DistributedSampler automatically."""
    lite = EmptyLite(accelerator="cpu", strategy=strategy, devices=2)
    is_distributed = lite._accelerator_connector.is_distributed
    lite_dataloader = lite.setup_dataloaders(DataLoader(range(3), shuffle=shuffle))
    assert not is_distributed or isinstance(lite_dataloader.sampler, DistributedSampler)


@pytest.mark.parametrize(
    "accelerator, expected",
    [
        ("cpu", torch.device("cpu")),
        pytest.param("gpu", torch.device("cuda", 0), marks=RunIf(min_gpus=1)),
        pytest.param("tpu", torch.device("xla", 0), marks=RunIf(tpu=True)),
    ],
)
def test_to_device(accelerator, expected):
    """Test that the to_device method can move various objects to the device determined by the accelerator."""
    lite = EmptyLite(accelerator=accelerator, devices=1)

    # module
    module = torch.nn.Linear(2, 3)
    module = lite.to_device(module)
    assert all(param.device == expected for param in module.parameters())

    # tensor
    tensor = torch.rand(2, 2)
    tensor = lite.to_device(tensor)
    assert tensor.device == expected

    # collection
    collection = {"data": torch.rand(2, 2), "int": 1}
    collection = lite.to_device(collection)
    assert collection["data"].device == expected


def test_rank_properties():
    """Test that the rank properties are determined by the strategy."""
    lite = EmptyLite()
    lite._strategy = Mock(spec=TrainingTypePlugin)
    lite._strategy.world_size = 1000
    assert lite.world_size == 1000
    lite._strategy.global_rank = 100
    assert lite.global_rank == 100
    lite._strategy.local_rank = 10
    assert lite.local_rank == 10
    lite._strategy.node_rank = 1
    assert lite.node_rank == 1


def test_backward():
    """Test that backward() calls into the precision plugin."""
    lite = EmptyLite()
    lite._precision_plugin = Mock(spec=PrecisionPlugin)
    loss = Mock()
    lite.backward(loss, "arg", keyword="kwarg")
    lite._precision_plugin._run_backward.assert_called_with(loss, None, "arg", keyword="kwarg")


def test_lightning_lite_setup():
    class LiteRunner(LightningLite):
        def run(self, pass_model: bool = True):
            model = BoringModel()
            optimizer = configure_optimizers(model)
            model_lite, optimizer_lite = self.setup(model, optimizer)
            if pass_model:
                self.setup(model_lite, optimizer)
            else:
                self.setup(model, optimizer_lite)

    with pytest.raises(MisconfigurationException, match="A module should be passed only once to the"):
        runner = LiteRunner()
        runner.run()

    with pytest.raises(MisconfigurationException, match="An optimizer should be passed only once to the"):
        runner = LiteRunner()
        runner.run(pass_model=False)


def test_lightning_lite_setup_dataloaders():
    class LiteRunner(LightningLite):
        def run(self):

            dataloader = DataLoader(RandomDataset(32, 64))
            dataloader_lite = self.setup_dataloaders(dataloader)
            _ = self.setup_dataloaders(dataloader_lite)

    with pytest.raises(MisconfigurationException, match="A dataloader should be passed only once to the"):
        runner = LiteRunner()
        runner.run()


def test_lightning_lite_track_model_setup():
    class LiteRunner(LightningLite):
        def run(self):
            model = BoringModel()
            optimizer = configure_optimizers(model)

            assert self._num_models == 0
            self.setup(model, optimizer)
            assert self._num_models == 1

            model = BoringModel()
            optimizer = configure_optimizers(model)
            self.setup(model, optimizer)
            assert self._num_models == 2

    runner = LiteRunner()
    runner.run()


# TODO: This test does not assert any functionality: use Mock to assert how DeepSpeedPlugin gets called
@mock.patch("pytorch_lightning.plugins.DeepSpeedPlugin.setup_distributed", lambda x: x)
def test_lightning_lite_deepspeed_backward():
    class LiteRunner(LightningLite):
        def run(self):
            def fn(*args):
                return args

            self._strategy._setup_model_and_optimizer = fn
            model = BoringModel()
            optimizer = configure_optimizers(model)
            self.setup(model, optimizer)

            model = BoringModel()
            optimizer = configure_optimizers(model)
            self.setup(model, optimizer)

            x = model(torch.randn(1, 32))
            loss = x.sum()
            self.backward(loss)

    with pytest.raises(MisconfigurationException, match="please provide the model used to perform"):
        runner = LiteRunner(strategy="deepspeed")
        runner.run()


@RunIf(min_gpus=2, deepspeed=True, special=True)
def test_deepspeed_multiple_models():
    class LiteRunner(LightningLite):
        def run(self):
            model = BoringModel()
            optimizer = configure_optimizers(model)
            model, optimizer = self.setup(model, optimizer)
            state_dict = deepcopy(model.state_dict())

            for _ in range(2):
                optimizer.zero_grad()
                x = model(torch.randn(1, 32).to(self.device))
                loss = x.sum()
                self.backward(loss, model=model)
                optimizer.step()

            for mw_b, mw_a in zip(state_dict.values(), model.state_dict().values()):
                assert not torch.equal(mw_b, mw_a)

            seed_everything(42)
            model_1 = BoringModel()
            optimizer_1 = configure_optimizers(model_1)

            seed_everything(42)
            model_2 = BoringModel()
            optimizer_2 = configure_optimizers(model_2)

            for mw_1, mw_2 in zip(model_1.state_dict().values(), model_2.state_dict().values()):
                assert torch.equal(mw_1, mw_2)

            model_1, optimizer_1 = self.setup(model_1, optimizer_1)
            model_2, optimizer_2 = self.setup(model_2, optimizer_2)

            seed_everything(42)
            data_list = []
            for _ in range(2):
                optimizer_1.zero_grad()
                data = torch.randn(1, 32).to(self.device)
                data_list.append(data)
                x = model_1(data)
                loss = x.sum()
                self.backward(loss, model=model_1)
                optimizer_1.step()

            for mw_1, mw_2 in zip(model_1.state_dict().values(), model_2.state_dict().values()):
                assert not torch.equal(mw_1, mw_2)

            for data in data_list:
                optimizer_2.zero_grad()
                x = model_2(data)
                loss = x.sum()
                self.backward(loss, model=model_2)
                optimizer_2.step()

            for mw_1, mw_2 in zip(model_1.state_dict().values(), model_2.state_dict().values()):
                assert torch.equal(mw_1, mw_2)

            # Verify collectives works as expected
            ranks = self.all_gather(torch.tensor([self.local_rank]).to(self.device))
            assert torch.equal(ranks.cpu(), torch.tensor([[0], [1]]))
            assert self.broadcast(True)
            assert self.is_global_zero == (self.local_rank == 0)

    LiteRunner(strategy=DeepSpeedPlugin(stage=3, logging_batch_size_per_gpu=1), devices=2, accelerator="gpu").run()
