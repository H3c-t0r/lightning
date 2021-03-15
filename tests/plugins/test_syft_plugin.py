from types import ModuleType
from typing import Any, List, Optional, Union

import torch

from pytorch_lightning import Trainer
from pytorch_lightning.utilities.imports import _PYSYFT_AVAILABLE, is_syft_initialized
from tests.helpers.runif import RunIf

if _PYSYFT_AVAILABLE:
    import syft as sy
    from syft.ast.module import Module

    from pytorch_lightning.experimental.plugins.secure.pysyft import SyLightningModule


@RunIf(syft=True)
def test_syft(tmpdir):
    SyModuleProxyType = Union[ModuleType, Module]

    # cant use lib_ast during test search time
    TorchTensorPointerType = Any  # sy.lib_ast.torch.Tensor.pointer_type
    SyTensorProxyType = Union[torch.Tensor, TorchTensorPointerType]  # type: ignore

    sy.logger.remove()
    alice = sy.VirtualMachine(name="alice")
    duet = alice.get_root_client()
    # bookkeeping
    sy.client_cache["duet"] = duet
    assert is_syft_initialized()

    class BoringSyNet(sy.Module):

        def __init__(self, torch_ref: SyModuleProxyType) -> None:
            super(BoringSyNet, self).__init__(torch_ref=torch_ref)
            self.fc2 = self.torch_ref.nn.Linear(32, 2)

        def forward(self, x: SyTensorProxyType) -> SyTensorProxyType:
            return self.fc2(x)

    class LiftSyLightningModule(SyLightningModule):

        def __init__(
            self,
            module: sy.Module,
            duet: Any
        ) -> None:
            super().__init__(module, duet)

        def training_step(self, batch: SyTensorProxyType, batch_idx: Optional[int]) -> SyTensorProxyType:
            data_ptr = batch
            output = self.forward(data_ptr)
            return self.torch.nn.functional.mse_loss(output, self.torch.ones_like(output))

        def test_step(self, batch: SyTensorProxyType, batch_idx: Optional[int]) -> None:
            output = self.forward(batch)
            loss = self.loss(output, self.torch.ones_like(output))
            self.log("test_loss", loss, on_step=True, on_epoch=True, prog_bar=True)

        def configure_optimizers(self) -> List:
            optimizer = self.torch.optim.SGD(self.model.parameters(), lr=0.1)  # type: ignore
            return [optimizer]

        def train_dataloader(self):
            return self.torch.utils.data.DataLoader(self.torch.randn(64, 32))

    module = BoringSyNet(torch)
    model = LiftSyLightningModule(module=module, duet=duet)

    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        limit_train_batches=2,
        limit_test_batches=2,
    )

    trainer.fit(model)
    trainer.test()
    trainer.test(model)

    model = LiftSyLightningModule.load_from_checkpoint(
        trainer.checkpoint_callback.best_model_path, module=module, duet=duet)
    trainer.fit(model)

    del sy.client_cache["duet"]
    assert not is_syft_initialized()
