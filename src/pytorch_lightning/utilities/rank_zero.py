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
"""Utilities that can be used for calling functions on a particular rank."""
import logging

import lightning_utilities.core.rank_zero as rank_zero_module

# note: we want to keep these indirections so the `rank_zero_module.log` is set (on import) for PL users
from lightning_utilities.core.rank_zero import (  # noqa: F401
    rank_zero_debug,
    rank_zero_deprecation,
    rank_zero_info,
    rank_zero_only,
    rank_zero_warn,
)

# backwards compatibility
from lightning_lite.utilities.rank_zero import LightningDeprecationWarning  # noqa: F401

rank_zero_module.log = logging.getLogger(__name__)
