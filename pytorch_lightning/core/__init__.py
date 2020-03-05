"""
A LightningModule organizes your PyTorch code into the following sections:

- The model/system definition (__init__)
- The model/system computations (forward)
- What happens in the training loop (training_step)
- What happens in the validation loop (validation_step, validation_epoch_end)
- What happens in the test loop (test_step, test_epoch_end)
- What optimizers to use (configure_optimizers)
- What data to use (train_dataloader, val_dataloader, test_dataloader)

.. note:: LightningModule is a torch.nn.Module but with added functionality.

------------

Minimal Example
---------------

Most methods are optional. Here's a minimal example.

.. code-block:: python

    import os
    import torch
    from torch.nn import functional as F
    from torch.utils.data import DataLoader
    from torchvision.datasets import MNIST
    import torchvision.transforms as transforms

    import pytorch_lightning as pl

    class CoolModel(pl.LightningModule):

        def __init__(self):
            super(CoolModel, self).__init__()
            self.l1 = torch.nn.Linear(28 * 28, 10)

        def forward(self, x):
            return torch.relu(self.l1(x.view(x.size(0), -1)))

        def training_step(self, batch, batch_idx):
            x, y = batch
            y_hat = self.forward(x)
            return {'loss': F.cross_entropy(y_hat, y)}

        def train_dataloader(self):
            return DataLoader(MNIST(os.getcwd(), train=True, download=True,
                              transform=transforms.ToTensor()), batch_size=32)

        def configure_optimizers(self):
            return torch.optim.Adam(self.parameters(), lr=0.02)

Which you can train by doing:

.. code-block:: python

   trainer = pl.Trainer()
   model = CoolModel()

   trainer.fit(model)

----------

Training loop structure
-----------------------

The general pattern is that each loop (training, validation, test loop)
has 2 methods, ```___step, ___epoch_end```

To show how lightning calls these, let's use the validation loop as an example

.. code-block:: python

    val_outs = []
    for val_batch in val_data:
        # do something with each batch
        out = validation_step(val_batch)
        val_outs.append(out)

    # do something with the outputs for all batches
    # like calculate validation set accuracy or loss
    validation_epoch_end(val_outs)

Thus, if we wanted to add a validation loop you would add this to your LightningModule

.. code-block:: python

        class CoolModel(pl.LightningModule):
            def validation_step(self, batch, batch_idx):
                x, y = batch
                y_hat = self.forward(x)
                return {'val_loss': F.cross_entropy(y_hat, y)}

            def validation_epoch_end(self, outputs):
                val_loss_mean = torch.stack([x['val_loss'] for x in outputs]).mean()
                return {'val_loss': val_loss_mean}

            def val_dataloader(self):
                # can also return a list of val dataloaders
                return DataLoader(MNIST(os.getcwd(), train=True, download=True,
                                  transform=transforms.ToTensor()), batch_size=32)

Or add a test loop

.. code_block:: python

        class CoolModel(pl.LightningModule):

            def test_step(self, batch, batch_idx):
                x, y = batch
                y_hat = self.forward(x)
                return {'test_loss': F.cross_entropy(y_hat, y)}

            def test_epoch_end(self, outputs):
                test_loss_mean = torch.stack([x['test_loss'] for x in outputs]).mean()
                return {'test_loss': test_loss_mean}

            def test_dataloader(self):
                # OPTIONAL
                # can also return a list of test dataloaders
                return DataLoader(MNIST(os.getcwd(), train=False, download=True,
                                  transform=transforms.ToTensor()), batch_size=32)

Check out this
`COLAB <https://colab.research.google.com/drive/1F_RNcHzTfFuQf-LeKvSlud6x7jXYkG31#scrollTo=HOk9c4_35FKg>`_
for a live demo.

.. note:: Remove all .cuda() or .to() calls from LightningModules. See:
    `the multi-gpu training guide for details <multi_gpu.rst>`_.

"""

from .decorators import data_loader
from .lightning import LightningModule

__all__ = ['LightningModule', 'data_loader']
# __call__ = __all__
