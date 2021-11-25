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

"""Here is an example Fault Tolerant (using PyTorch 1.7.1)

1. Launch `python pl_examples/fault_tolerant/automatic.py --should_fail 0`.
    - You should see `[-1.0939, -0.4306]` in the logs.


2. Launch `python pl_examples/fault_tolerant/automatic.py --should_fail 0`.
    - You should see `kill -SIGTERM {PID}` in the logs.
3. Run this command within another terminal.
    - You should see `Received signal 15. Saving a fault-tolerant checkpoint and terminating.` in the logs.
4. Launch `python pl_examples/fault_tolerant/automatic.py --should_fail 0` again.
    - You should see `Restored all states from the checkpoint file at ./.pl_auto_save.ckpt`
    - And you should see `[-1.0939, -0.4306]` in the logs.

This means the weights with the failure matches the weight without and
the training has been properly resumed and is fully reproduced.
"""

import os
import random as python_random
from argparse import ArgumentParser
from time import sleep

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

from pytorch_lightning import _logger as log
from pytorch_lightning import LightningModule, seed_everything, Trainer


class SimpleMLP(LightningModule):
    def __init__(self, fail_on_step: int = -1):
        super().__init__()
        self.layer = torch.nn.Linear(1, 2)
        self.seen_batches = []
        self.fail_on_step = fail_on_step

    def training_step(self, batch, batch_idx):
        if self.global_step == self.fail_on_step:
            log.info(f"READY TO BE KILLED WITH SIGTERM SIGNAL. Run `kill -SIGTERM {os.getpid()}`")
            # this line is used to wait for you to send the signal to exit gracefully.
            while not self.trainer._terminate_gracefully:
                sleep(0.1)
        batch = batch["data"] if isinstance(batch, dict) else batch
        self.seen_batches.append(torch.stack(batch) if isinstance(batch, list) else batch)
        loss = sum(self.layer(b).sum() for b in batch)
        return loss

    def configure_optimizers(self):
        return torch.optim.SGD(self.layer.parameters(), lr=0.1)


class RandomGetItemDataset(Dataset):
    """A dataset with random elements generated using global rng from torch, numpy and python."""

    def __init__(self, length, size):
        self.size = size
        self.len = length

    def __getitem__(self, index):
        t = torch.rand(self.size)
        n = torch.from_numpy(np.random.rand(self.size))
        p = torch.tensor([python_random.random() for _ in range(self.size)])
        sample = (index + (t + n + p) / 10).float()
        return sample

    def __len__(self):
        return self.len


def _run_training(trainer_kwargs, fail_on_step: int = -1, ckpt_path=None):
    seed_everything(1)
    train_dataloader = DataLoader(RandomGetItemDataset(3, 1))
    model = SimpleMLP(fail_on_step=fail_on_step)
    trainer = Trainer(**trainer_kwargs)
    trainer.fit(model, train_dataloaders=train_dataloader, ckpt_path=ckpt_path)
    return model.seen_batches, model.parameters()


def main(args):
    seed_everything(42)
    os.environ["PL_FAULT_TOLERANT_TRAINING"] = "1"  # active fault tolerant automatic

    ckpt_path = os.path.join(".", ".pl_auto_save.ckpt")
    auto_restart_ckpt_path_exists = os.path.exists(ckpt_path)
    if args.should_fail:
        fail_on_step = -1 if auto_restart_ckpt_path_exists else 4
        completed_batches = 4 if auto_restart_ckpt_path_exists else 5
    else:
        fail_on_step = -1
        completed_batches = 9

    trainer_kwargs = dict(
        default_root_dir=".",
        max_epochs=3,
        enable_progress_bar=False,
        enable_model_summary=False,
    )

    # Perform a failure
    complete_batches, weights = _run_training(trainer_kwargs, fail_on_step=fail_on_step)
    assert len(complete_batches) == completed_batches

    if not auto_restart_ckpt_path_exists and args.should_fail:
        assert os.path.exists(ckpt_path)

    if auto_restart_ckpt_path_exists or not args.should_fail:
        log.info([w for w in weights])


if __name__ == "__main__":
    parser = ArgumentParser(description="Fault Tolerant Example")
    parser.add_argument("--should_fail", type=int, default=1, help="Whether the training should fail.")
    main(parser.parse_args())
