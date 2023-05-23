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
r"""
ChannelsLast
===============

changes the model memory format to channels_last
"""

from typing import Optional

import lightning.pytorch as pl
from lightning.pytorch.callbacks.callback import Callback


class ChannelsLast(Callback):
    """The `ChannelsLast` callback changes the model memory format to `torch.channels_last` before training starts.
    <https://\\pytorch.org/tutorials/intermediate/memory_format_tutorial.html>`_.

    This usually improves GPU utilization.

    Runs on setup, so it can set the memory format before the model is DDP wrapped. 
    
    Has no parameters.
    """

    def setup(self, trainer: Trainer, pl_module: LightningModule, stage: Optional[str] = None) -> None:
        pl_module.to(memory_format=torch.channels_last)