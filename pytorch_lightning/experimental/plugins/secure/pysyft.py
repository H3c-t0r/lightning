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
from types import ModuleType
from typing import Any, Dict, Generator, Union

import torch
from torch.nn import Module
from torch.optim import Optimizer

from pytorch_lightning.core.lightning import LightningModule
from pytorch_lightning.experimental.plugins.secure.base import BaseSecurePlugin
from pytorch_lightning.utilities.imports import _PYSYFT_AVAILABLE


class PySyftPlugin(BaseSecurePlugin):

    @staticmethod
    def optimizer_state(optimizer: Optimizer) -> Dict[str, torch.Tensor]:
        """
        Returns state of an optimizer. Allows for syncing/collating optimizer state from processes in custom
        plugins.
        """
        return optimizer.state_dict().get(request_block=True, delete_obj=False)

    @staticmethod
    def save_function(trainer, filepath: str, save_weights_only: bool) -> None:
        model_ref = trainer.lightning_module
        sy_model = model_ref.get_model()
        sy_model.device = model_ref.device
        sy_model.hparams = model_ref.hparams
        sy_model.on_save_checkpoint = model_ref.on_save_checkpoint
        trainer.training_type_plugin.model = sy_model
        trainer.accelerator.optimizer_state = PySyftPlugin.optimizer_state
        trainer.save_checkpoint(filepath, save_weights_only)
        trainer.training_type_plugin.model = model_ref


if _PYSYFT_AVAILABLE:
    import syft as sy

    SyModuleProxyType = Union[ModuleType, Module]
    SyModelProxyType = Union[Module, sy.Module]

    # cant use lib_ast during test search time
    TorchTensorPointerType = Any  # sy.lib_ast.torch.Tensor.pointer_type
    SyTensorProxyType = Union[torch.Tensor, TorchTensorPointerType]  # type: ignore

    class SyLightningModule(LightningModule):

        def __init__(self, module: sy.Module, request_parameters: bool = False, run_locally: bool = False) -> None:
            super().__init__()
            """
            This class is used to wrap the ``sy.Module`` and simplify the interface with Pytorch LightningModule
            """
            # Those are helpers to easily work with `sy.Module`
            self.module = module
            self.duet = sy.client_cache["duet"]
            self.remote_torch = sy.client_cache["duet"].torch
            self.local_torch = globals()["torch"]
            self.request_parameters = request_parameters
            self.run_locally = run_locally

        def setup(self, stage: str):
            self.get = self.module.get
            self.send = self.module.send
            self.send_model()

        def is_remote(self) -> bool:
            # Training / Evaluating is done remotely and Testing is done locally unless run_locally is True
            return not self.run_locally and not self.trainer.testing

        @property
        def torch(self) -> SyModuleProxyType:
            return self.remote_torch if self.is_remote() else self.local_torch

        @property
        def model(self) -> SyModelProxyType:
            if self.is_remote():
                return self.remote_model
            if self.request_parameters:
                return self.get_model()
            return self.module

        def send_model(self) -> None:
            self.remote_model = self.module.send(self.duet)

        def get_model(self) -> Module:  # type: ignore
            return self.remote_model.get(request_block=True)

        def parameters(self) -> Generator:
            return self.module.parameters()

        def state_dict(self) -> Dict:
            return self.module.state_dict()

        def load_state_dict(self, state_dict: Dict, strict: bool = True) -> None:
            return self.module.load_state_dict(state_dict)

        def forward(self, x: SyTensorProxyType) -> SyTensorProxyType:
            return self.model(x)

        def on_train_start(self) -> None:
            self.request_parameters = False

        def on_test_start(self) -> None:
            self.request_parameters = True
