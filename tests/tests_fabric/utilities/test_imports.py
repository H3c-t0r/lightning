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
import subprocess
import sys
from textwrap import dedent

from tests_fabric.helpers.runif import RunIf


def test_import_fabric_with_torch_dist_unavailable():
    """Test that the package can be imported regardless of whether torch.distributed is available."""
    code = dedent(
        """
        import torch
        torch.distributed.is_available = lambda: False  # pretend torch.distributed not available
        import lightning.fabric
        """
    )
    # run in complete isolation
    assert subprocess.call([sys.executable, "-c", code]) == 0


@RunIf(deepspeed=True)
def test_import_deepspeed_lazily():
    """Test that we are importing deepspeed only when necessary."""
    code = dedent(
        """
        import lightning.fabric
        import sys

        assert 'deepspeed' not in sys.modules
        from lightning.fabric.strategies import DeepSpeedStrategy
        from lightning.fabric.plugins import DeepSpeedPrecision
        assert 'deepspeed' not in sys.modules

        import deepspeed
        assert 'deepspeed' in sys.modules
        """
    )
    # run in complete isolation
    assert subprocess.call([sys.executable, "-c", code]) == 0
