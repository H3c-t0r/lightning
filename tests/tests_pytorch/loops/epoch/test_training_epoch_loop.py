# Copyright The Lightning AI team.
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
from unittest.mock import patch

import pytest

from lightning.pytorch.demos.boring_classes import BoringModel
from lightning.pytorch.trainer.trainer import Trainer


def test_no_val_on_train_epoch_loop_restart(tmpdir):
    """Test that training validation loop doesn't get triggered at the beginning of a restart."""
    trainer_kwargs = {
        "max_epochs": 1,
        "limit_train_batches": 1,
        "limit_val_batches": 1,
        "num_sanity_val_steps": 0,
        "enable_checkpointing": False,
    }
    trainer = Trainer(**trainer_kwargs)
    model = BoringModel()
    trainer.fit(model)
    ckpt_path = str(tmpdir / "last.ckpt")
    trainer.save_checkpoint(ckpt_path)

    trainer_kwargs["max_epochs"] = 2
    trainer = Trainer(**trainer_kwargs)

    with patch.object(
        trainer.fit_loop.epoch_loop.val_loop, "advance", wraps=trainer.fit_loop.epoch_loop.val_loop.advance
    ) as advance_mocked:
        trainer.fit(model, ckpt_path=ckpt_path)
        assert advance_mocked.call_count == 1


@pytest.mark.parametrize(
    "min_epochs, min_steps, current_epoch, global_step, early_stop, epoch_loop_done, raise_info_msg",
    [
        (None, None, 1, 4, True, True, False),
        (None, None, 1, 10, True, True, False),
        (4, None, 1, 4, False, False, True),
        (4, 2, 1, 4, False, False, True),
        (4, None, 1, 10, False, True, False),
        (4, 3, 1, 3, False, False, True),
        (4, 10, 1, 10, False, True, False),
        (None, 4, 1, 4, True, True, False),
    ],
)
def test_should_stop_early_stopping_conditions_not_met(
    caplog, min_epochs, min_steps, current_epoch, global_step, early_stop, epoch_loop_done, raise_info_msg
):
    """Test that checks that info message is logged when users sets `should_stop` but min conditions are not
    met."""
    trainer = Trainer(min_epochs=min_epochs, min_steps=min_steps, limit_val_batches=0)
    trainer.num_training_batches = 10
    trainer.should_stop = True
    trainer.fit_loop.epoch_loop.automatic_optimization.optim_progress.optimizer.step.total.completed = global_step
    trainer.fit_loop.epoch_loop.batch_progress.current.ready = global_step
    trainer.fit_loop.epoch_progress.current.completed = current_epoch - 1

    message = f"min_epochs={min_epochs}` or `min_steps={min_steps}` has not been met. Training will continue"
    with caplog.at_level(logging.INFO, logger="lightning.pytorch.loops"):
        assert trainer.fit_loop.epoch_loop.done is epoch_loop_done

    assert (message in caplog.text) is raise_info_msg
    assert trainer.fit_loop._can_stop_early is early_stop


def test_regression(tmp_path):
    

    model = BoringModel()
    trainer = Trainer(
        default_root_dir=tmp_path,
        num_sanity_val_steps=0,
        limit_val_batches=2,
        limit_train_batches=2,
        max_epochs=3,
        min_epochs=3,
        enable_model_summary=False,
        enable_checkpointing=False,
    )
    trainer.should_stop = True
    trainer.fit_loop.epoch_loop.val_loop.run = Mock()
    trainer.fit(model)
    assert trainer.fit_loop.epoch_loop.val_loop.run.call_count == 3
