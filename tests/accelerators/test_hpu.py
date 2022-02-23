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
import os
from argparse import ArgumentParser
from typing import Optional

import pytest
import torch
import torch.nn.functional as F

from pytorch_lightning import Callback, seed_everything, Trainer
from pytorch_lightning.accelerators import CPUAccelerator, HPUAccelerator
from pytorch_lightning.core.lightning import LightningModule
from pytorch_lightning.plugins import HPUPrecisionPlugin
from pytorch_lightning.strategies.hpu_parallel import HPUParallelStrategy
from pytorch_lightning.strategies.hpu import HPUStrategy
from pytorch_lightning.utilities import _HPU_AVAILABLE
from pytorch_lightning.utilities.exceptions import MisconfigurationException
from tests.helpers.boring_model import BoringModel
from tests.helpers.datamodules import ClassifDataModule
from tests.helpers.runif import RunIf
from tests.helpers.simple_models import ClassificationModel

if _HPU_AVAILABLE:
    import habana_frameworks.torch.core as htcore

    os.environ["PL_TORCH_DISTRIBUTED_BACKEND"] = "hccl"


class HPUModel(BoringModel):
    def training_step(self, batch, batch_idx):
        output = self(batch)
        loss = self.loss(batch, output)
        return loss

    def validation_step(self, batch, batch_idx):
        output = self(batch)
        loss = self.loss(batch, output)
        return loss

    def test_step(self, batch, batch_idx):
        output = self(batch)
        loss = self.loss(batch, output)
        return loss

    def training_epoch_end(self, outputs) -> None:
        pass

    def validation_epoch_end(self, outputs) -> None:
        pass

    def test_epoch_end(self, outputs) -> None:
        pass


class HPUClassificationModel(ClassificationModel):
    def training_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        acc = self.accuracy(logits, y)
        return acc

    def test_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        acc = self.accuracy(logits, y)
        return acc

    def accuracy(self, logits, y):
        acc = torch.sum(torch.eq(torch.argmax(logits, -1), y).to(torch.float32)) / len(y)
        return acc

    def validation_epoch_end(self, outputs) -> None:
        self.log("val_acc", torch.stack(outputs).mean())

    def test_epoch_end(self, outputs) -> None:
        self.log("test_acc", torch.stack(outputs).mean())


def test_availability():
    assert HPUAccelerator.is_available()

@pytest.mark.skipif(_HPU_AVAILABLE, reason="test requires non-HPU machine")
def test_fail_if_no_hpus(tmpdir):
    with pytest.raises(MisconfigurationException, match="HPU Accelerator requires HPU devices to run"):
        Trainer(default_root_dir=tmpdir, accelerator="hpu", devices=1)

    with pytest.raises(MisconfigurationException, match="HPU Accelerator requires HPU devices to run"):
        Trainer(default_root_dir=tmpdir, devices=1, accelerator="hpu")


@RunIf(hpu=True)
def test_accelerator_selected(tmpdir):
    assert HPUAccelerator.is_available()
    trainer = Trainer(default_root_dir=tmpdir, accelerator="hpu", devices=1)
    assert isinstance(trainer.accelerator, HPUAccelerator)


@RunIf(hpu=True)
def test_no_warning_plugin(tmpdir):
    with pytest.warns(None) as record:
        Trainer(default_root_dir=tmpdir, max_epochs=1, strategy=HPUStrategy(device=torch.device("hpu")))
    assert len(record) == 0


@RunIf(hpu=True)
def test_all_stages(tmpdir):
    model = HPUModel()
    trainer = Trainer(default_root_dir=tmpdir, fast_dev_run=True, accelerator="hpu", devices=1)
    trainer.fit(model)
    trainer.validate(model)
    trainer.test(model)
    trainer.predict(model)


@RunIf(hpu=True)
def test_optimization(tmpdir):
    seed_everything(42)

    dm = ClassifDataModule(length=1024)
    model = HPUClassificationModel()

    trainer = Trainer(default_root_dir=tmpdir, max_epochs=1, accelerator="hpu", devices=1)

    # fit model
    trainer.fit(model, dm)
    assert trainer.state.finished, f"Training failed with {trainer.state}"
    assert dm.trainer is not None

    # validate
    result = trainer.validate(datamodule=dm)
    assert dm.trainer is not None
    assert result[0]["val_acc"] > 0.7

    # test
    result = trainer.test(model, datamodule=dm)
    assert dm.trainer is not None
    test_result = result[0]["test_acc"]
    assert test_result > 0.6

    # test saved model
    model_path = os.path.join(tmpdir, "model.pt")
    trainer.save_checkpoint(model_path)

    model = HPUClassificationModel.load_from_checkpoint(model_path)

    trainer = Trainer(default_root_dir=tmpdir, accelerator="hpu", devices=1)

    result = trainer.test(model, datamodule=dm)
    saved_result = result[0]["test_acc"]
    assert saved_result == test_result


@RunIf(hpu=True)
def test_mixed_precision(tmpdir, hmp_params):
    class TestCallback(Callback):
        def setup(self, trainer: Trainer, pl_module: LightningModule, stage: Optional[str] = None) -> None:
            assert trainer.strategy.model.precision == "bf16"
            raise SystemExit

    model = HPUModel()
    trainer = Trainer(
        strategy=HPUStrategy(device=torch.device("hpu"),
            precision_plugin=HPUPrecisionPlugin(precision="bf16", hmp_params=hmp_params)
        ),
        default_root_dir=tmpdir,
        fast_dev_run=True,
        accelerator="hpu",
        devices=1,
        callbacks=TestCallback(),
    )
    assert isinstance(trainer.strategy, HPUStrategy)
    assert isinstance(trainer.strategy.precision_plugin, HPUPrecisionPlugin)
    assert trainer.strategy.precision_plugin.precision == "bf16"
    with pytest.raises(SystemExit):
        trainer.fit(model)


@RunIf(hpu=True)
def test_pure_half_precision(tmpdir, hmp_params):
    class TestCallback(Callback):
        def on_train_start(self, trainer: Trainer, pl_module: LightningModule) -> None:
            assert trainer.strategy.model.precision == 16
            for param in trainer.strategy.model.parameters():
                assert param.dtype == torch.float16
            raise SystemExit

    model = HPUModel()
    model = model.half()
    trainer = Trainer(
        strategy=HPUStrategy(device=torch.device("hpu"),
            precision_plugin=HPUPrecisionPlugin(precision=16, hmp_params=None)
        ),
        default_root_dir=tmpdir,
        fast_dev_run=True,
        accelerator="hpu",
        devices=1,
        callbacks=TestCallback(),
    )

    assert isinstance(trainer.strategy, HPUStrategy)
    assert isinstance(trainer.strategy.precision_plugin, HPUPrecisionPlugin)
    assert trainer.strategy.precision_plugin.precision == 16

    with pytest.raises(SystemExit):
        trainer.fit(model)


@RunIf(hpu=True)
def test_stages_correct(tmpdir):
    """Ensure all stages correctly are traced correctly by asserting the output for each stage."""

    class StageModel(HPUModel):
        def training_step(self, batch, batch_idx):
            loss = super().training_step(batch, batch_idx)
            # tracing requires a loss value that depends on the model.
            # force it to be a value but ensure we use the loss.
            return (loss - loss) + torch.tensor(1)

        def validation_step(self, batch, batch_idx):
            loss = super().validation_step(batch, batch_idx)
            return (loss - loss) + torch.tensor(2)

        def test_step(self, batch, batch_idx):
            loss = super().validation_step(batch, batch_idx)
            return (loss - loss) + torch.tensor(3)

        def predict_step(self, batch, batch_idx, dataloader_idx=None):
            output = super().predict_step(batch, batch_idx)
            return (output - output) + torch.tensor(4)

    class TestCallback(Callback):
        def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx, dataloader_idx) -> None:
            assert outputs["loss"].item() == 1

        def on_validation_batch_end(self, trainer, pl_module, outputs, batch, batch_idx, dataloader_idx) -> None:
            assert outputs.item() == 2

        def on_test_batch_end(self, trainer, pl_module, outputs, batch, batch_idx, dataloader_idx) -> None:
            assert outputs.item() == 3

        def on_predict_batch_end(self, trainer, pl_module, outputs, batch, batch_idx, dataloader_idx) -> None:
            assert torch.all(outputs == 4).item()

    model = StageModel()
    trainer = Trainer(default_root_dir=tmpdir, fast_dev_run=True, accelerator="hpu", devices=1, callbacks=TestCallback())
    trainer.fit(model)
    trainer.test(model)
    trainer.validate(model)
    trainer.predict(model, model.test_dataloader())


@RunIf(hpu=True)
def test_precision_plugin(tmpdir, hmp_params):

    plugin = HPUPrecisionPlugin(precision="bf16", hmp_params=hmp_params)
    assert plugin.precision == "bf16"


@RunIf(hpu=True)
def test_accelerator_hpu():

    trainer = Trainer(accelerator="hpu", devices=1)
    assert isinstance(trainer.accelerator, HPUAccelerator)

    trainer = Trainer(accelerator="hpu")
    assert isinstance(trainer.accelerator, HPUAccelerator)

    trainer = Trainer(accelerator="auto", devices=8)
    assert isinstance(trainer.accelerator, HPUAccelerator)


@RunIf(hpu=True)
def test_accelerator_hpu_with_single_device():

    trainer = Trainer(accelerator="hpu", devices=1)

    assert isinstance(trainer.strategy, HPUStrategy)
    assert isinstance(trainer.accelerator, HPUAccelerator)


def test_accelerator_hpu_with_multiple_devices():

    trainer = Trainer(accelerator="hpu", devices=8)

    assert isinstance(trainer.strategy, HPUParallelStrategy)
    assert isinstance(trainer.accelerator, HPUAccelerator)


@RunIf(hpu=True)
def test_accelerator_auto_with_devices_hpu():

    trainer = Trainer(accelerator="auto", devices=8)

    assert isinstance(trainer.strategy, HPUParallelStrategy)


@RunIf(hpu=True)
def test_set_devices_if_none_hpu():

    trainer = Trainer(accelerator="hpu", devices=8)
    assert trainer.devices == 8


@RunIf(hpu=True)
def test_strategy_choice_hpu_plugin(tmpdir):
    trainer = Trainer(strategy=HPUStrategy(device=torch.device("hpu")), accelerator="hpu", devices=1)
    assert isinstance(trainer.strategy, HPUStrategy)


@RunIf(hpu=True)
def test_strategy_choice_hpu_parallel_plugin(tmpdir):
    trainer = Trainer(strategy=HPUParallelStrategy(parallel_devices=[torch.device("hpu")]*8), accelerator="hpu", devices=8)
    assert isinstance(trainer.strategy, HPUParallelStrategy)


@RunIf(hpu=True)
def test_device_type_when_training_plugin_hpu_passed(tmpdir):

    trainer = Trainer(strategy=HPUStrategy(device=torch.device("hpu")), accelerator="hpu", devices=1)
    assert isinstance(trainer.strategy, HPUStrategy)
    assert isinstance(trainer.accelerator, HPUAccelerator)


@RunIf(hpu=True)
def test_devices_auto_choice_hpu():
    trainer = Trainer(accelerator="auto", devices="auto")
    assert trainer.devices == 8


@RunIf(hpu=True)
@pytest.mark.parametrize("hpus", [1])
def test_inference_only(tmpdir, hpus):
    model = HPUModel()

    trainer = Trainer(default_root_dir=tmpdir, fast_dev_run=True, accelerator="hpu", devices=hpus)
    trainer.validate(model)
    trainer.test(model)
    trainer.predict(model)
