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
from typing import Any, Callable, Optional, Dict

import torch
from torch import nn as nn, Tensor
from torch.optim import Optimizer
from torch.utils.data import DataLoader

from pytorch_lightning.accelerators import Accelerator
from pytorch_lightning.utilities.apply_func import apply_to_collection, move_data_to_device


# TODO: add attributes and methods from Optimizer
class _LiteOptimizer(Optimizer):
    def __init__(self, optimizer: Optimizer, accelerator: Accelerator) -> None:
        super().__init__(params=optimizer.param_groups, defaults=getattr(optimizer, "defaults", {}))  # type: ignore[call-arg]
        self._optimizer = optimizer
        self._accelerator = accelerator

    @property
    def optimizer(self) -> Optimizer:
        return self._optimizer

    def step(self, closure: Optional[Callable] = None) -> None:
        self._accelerator.optimizer_step(
            self._optimizer,
            lambda_closure=closure,
            model=None,
        )


class _LiteModule(nn.Module):
    def __init__(self, module: nn.Module, accelerator: Accelerator) -> None:
        super().__init__()
        self._module = module
        self._accelerator = accelerator

    @property
    def module(self) -> nn.Module:
        return self._module

    def forward(self, *args: Any, **kwargs: Any) -> Any:
        with self._accelerator.forward_context():
            output = self.module.forward(*args, **kwargs)

        output = apply_to_collection(output, function=lambda t: t.to(torch.get_default_dtype()), dtype=Tensor)
        return output


class _LiteDataLoader(DataLoader):
    def __init__(self, device: Optional[torch.device] = None, **dl_kwargs):
        super().__init__(**dl_kwargs)
        self._device = device

    def __iter__(self):
        iterator = super().__iter__()
        if self._device is None:
            return iterator

        for item in iterator:
            yield move_data_to_device(item, self._device)


#
# def iterator_wrapper(iter_method: Callable):
#     iterator = iter_method()
#     for item in iterator:
#         print("additional")
#         yield item
#
#
# def iterator_decorator(fn):
#     def _it():
#         return iterator_wrapper(fn)
#
#     return _it
#
#
# if __name__ == "__main__":
#     dset = BoringModel().train_dataloader().dataset
#     loader = DataLoader(dset, num_workers=2)
#     loader.__iter__ = iterator_decorator(loader.__iter__)
#     for x in iter(loader):
#         print()
