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
"""MNIST simple image classifier example.

To run: python simple_image_classifier.py --trainer.max_epochs=50
"""
import torch
from torch.nn import functional as F
from torchmetrics import Accuracy

import pytorch_lightning as pl
from pl_examples import cli_lightning_logo
from pl_examples.basic_examples.mnist_examples.image_classifier_1_pytorch import Net
from pl_examples.basic_examples.mnist_examples.mnist_datamodule import MNISTDataModule
from pytorch_lightning.utilities.cli import LightningCLI


class LitClassifier(pl.LightningModule):
    """
    >>> LitClassifier()  # doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
    LitClassifier(
      (l1): Linear(...)
      (l2): Linear(...)
    )
    """

    def __init__(self, lr: float = 1.0, gamma: float = 0.7):
        super().__init__()
        self.save_hyperparameters()
        self.model = Net()
        self.val_acc = Accuracy()

    def forward(self, x):
        return self.model(x)

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        loss = F.nll_loss(y_hat, y)
        self.log("train_loss", loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        loss = F.nll_loss(y_hat, y)
        self.log("valid_loss", loss)
        self.val_acc(y_hat, y)
        self.log("val_acc", self.val_acc)

    def test_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self(x)
        loss = F.nll_loss(y_hat, y)
        self.log("test_loss", loss)

    def configure_optimizers(self):
        optimizer = torch.optim.Adadelta(self.parameters(), lr=self.hparams.lr)
        return [optimizer], [torch.optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=self.hparams.gamma)]


def cli_main():
    cli = LightningCLI(
        LitClassifier, MNISTDataModule, seed_everything_default=1234, save_config_overwrite=True, run=False
    )
    cli.trainer.fit(cli.model, datamodule=cli.datamodule)
    cli.trainer.test(ckpt_path="best", datamodule=cli.datamodule)


if __name__ == "__main__":
    cli_lightning_logo()
    cli_main()
