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
from unittest.mock import Mock

import pytest
import torch

from pytorch_lightning import seed_everything, Trainer
from pytorch_lightning.demos.boring_classes import BoringModel
from pytorch_lightning.loops import FitLoop


def test_outputs_format(tmpdir):
    """Tests that outputs objects passed to model hooks and methods are consistent and in the correct format."""

    class HookedModel(BoringModel):
        def training_step(self, batch, batch_idx):
            output = super().training_step(batch, batch_idx)
            self.log("foo", 123)
            output["foo"] = 123
            return output

        @staticmethod
        def _check_output(output):
            assert "loss" in output
            assert "foo" in output
            assert output["foo"] == 123

        def on_train_batch_end(self, outputs, batch, batch_idx):
            HookedModel._check_output(outputs)
            super().on_train_batch_end(outputs, batch, batch_idx)

        def training_epoch_end(self, outputs):
            assert len(outputs) == 2
            [HookedModel._check_output(output) for output in outputs]
            super().training_epoch_end(outputs)

    model = HookedModel()

    # fit model
    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        limit_val_batches=1,
        limit_train_batches=2,
        limit_test_batches=1,
        enable_progress_bar=False,
        enable_model_summary=False,
    )
    trainer.fit(model)


@pytest.mark.parametrize("seed_once", (True, False))
def test_training_starts_with_seed(tmpdir, seed_once):
    """Test the behavior of seed_everything on subsequent Trainer runs in combination with different settings of
    num_sanity_val_steps (which must not affect the random state)."""

    class SeededModel(BoringModel):
        def __init__(self):
            super().__init__()
            self.seen_batches = []

        def training_step(self, batch, batch_idx):
            self.seen_batches.append(batch.view(-1))
            return super().training_step(batch, batch_idx)

    def run_training(**trainer_kwargs):
        model = SeededModel()
        trainer = Trainer(**trainer_kwargs)
        trainer.fit(model)
        return torch.cat(model.seen_batches)

    if seed_once:
        seed_everything(123)
        sequence0 = run_training(default_root_dir=tmpdir, max_steps=2, num_sanity_val_steps=0)
        sequence1 = run_training(default_root_dir=tmpdir, max_steps=2, num_sanity_val_steps=2)
        assert not torch.allclose(sequence0, sequence1)
    else:
        seed_everything(123)
        sequence0 = run_training(default_root_dir=tmpdir, max_steps=2, num_sanity_val_steps=0)
        seed_everything(123)
        sequence1 = run_training(default_root_dir=tmpdir, max_steps=2, num_sanity_val_steps=2)
        assert torch.allclose(sequence0, sequence1)


@pytest.mark.parametrize(["max_epochs", "batch_idx_"], [(2, 5), (3, 8), (4, 12)])
def test_on_train_batch_start_return_minus_one(max_epochs, batch_idx_, tmpdir):
    class CurrentModel(BoringModel):
        def on_train_batch_start(self, batch, batch_idx):
            if batch_idx == batch_idx_:
                return -1

    model = CurrentModel()
    trainer = Trainer(default_root_dir=tmpdir, max_epochs=max_epochs, limit_train_batches=10)
    trainer.fit(model)
    if batch_idx_ > trainer.num_training_batches - 1:
        assert trainer.fit_loop.batch_idx == trainer.num_training_batches - 1
        assert trainer.global_step == trainer.num_training_batches * max_epochs
    else:
        assert trainer.fit_loop.batch_idx == batch_idx_
        assert trainer.global_step == batch_idx_ * max_epochs


def test_should_stop_mid_epoch(tmpdir):
    """Test that training correctly stops mid epoch and that validation is still called at the right time."""

    class TestModel(BoringModel):
        def __init__(self):
            super().__init__()
            self.validation_called_at = None

        def training_step(self, batch, batch_idx):
            if batch_idx == 4:
                self.trainer.should_stop = True
            return super().training_step(batch, batch_idx)

        def validation_step(self, *args):
            self.validation_called_at = (self.trainer.current_epoch, self.trainer.global_step)
            return super().validation_step(*args)

    model = TestModel()
    trainer = Trainer(default_root_dir=tmpdir, max_epochs=1, limit_train_batches=10, limit_val_batches=1)
    trainer.fit(model)

    # even though we stopped mid epoch, the fit loop finished normally and the current epoch was increased
    assert trainer.current_epoch == 1
    assert trainer.global_step == 5
    assert model.validation_called_at == (0, 5)


def test_fit_loop_done_log_messages(caplog):
    fit_loop = FitLoop(max_epochs=1)
    trainer = Mock(spec=Trainer)
    fit_loop.trainer = trainer

    trainer.should_stop = False
    trainer.num_training_batches = 5
    assert not fit_loop.done
    assert not caplog.messages

    trainer.num_training_batches = 0
    assert fit_loop.done
    assert "No training batches" in caplog.text
    caplog.clear()
    trainer.num_training_batches = 5

    epoch_loop = Mock()
    epoch_loop.global_step = 10
    fit_loop.connect(epoch_loop=epoch_loop)
    fit_loop.max_steps = 10
    assert fit_loop.done
    assert "max_steps=10` reached" in caplog.text
    caplog.clear()
    fit_loop.max_steps = 20

    fit_loop.epoch_progress.current.processed = 3
    fit_loop.max_epochs = 3
    trainer.should_stop = True
    assert fit_loop.done
    assert "max_epochs=3` reached" in caplog.text
    caplog.clear()
    fit_loop.max_epochs = 5

    fit_loop.epoch_loop.min_steps = 0
    with caplog.at_level(level=logging.DEBUG, logger="pytorch_lightning.utilities.rank_zero"):
        assert fit_loop.done
    assert "should_stop` was set" in caplog.text

    fit_loop.epoch_loop.min_steps = 100
    assert not fit_loop.done
    assert "was signaled to stop but" in caplog.text


def test_warning_valid_train_step_end(tmpdir):
    class ValidTrainStepEndModel(BoringModel):
        def training_step(self, batch, batch_idx):
            output = self(batch)
            return {"output": output, "batch": batch}

        def training_step_end(self, outputs):
            loss = self.loss(outputs["batch"], outputs["output"])
            return loss

    # No error is raised
    model = ValidTrainStepEndModel()
    trainer = Trainer(default_root_dir=tmpdir, fast_dev_run=1)

    trainer.fit(model)
