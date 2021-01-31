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
from typing import List

import torch

from pytorch_lightning.core.step_result import Result
from pytorch_lightning.overrides.data_parallel import LightningDataParallel
from pytorch_lightning.plugins.training_type.parallel import ParallelPlugin


class DataParallelPlugin(ParallelPlugin):

    def __init__(self, parallel_devices: List[torch.device]):
        super().__init__(parallel_devices=parallel_devices, cluster_environment=None)

    def setup(self, model):
        self._model = LightningDataParallel(model, self.parallel_devices)

    def reduce(self, output, *args, **kwargs):
        if isinstance(output, Result):
            output.dp_reduce()

        elif isinstance(output, torch.Tensor):
            output = output.mean()

        return output

    @property
    def root_device(self):
        return self.parallel_devices[0]

    @property
    def lightning_module(self):
        return self._model.module

    def model_to_device(self):
        # no need to do anything when model is wrapped in torch.nn.DataParallel
        pass

    def barrier(self, *args, **kwargs):
        pass

    def broadcast(self, obj: object, src: int = 0) -> object:
        return obj

    def reduce_early_stopping_decision(self, should_stop: bool) -> bool:
        return should_stop
