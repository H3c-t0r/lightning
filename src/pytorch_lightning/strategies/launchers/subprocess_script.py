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
import os
import subprocess
import sys
from time import sleep
from typing import Any, Callable, List, Optional, Tuple

import __main__
import numpy as np
import torch

import pytorch_lightning as pl
from pytorch_lightning.plugins.environments.cluster_environment import ClusterEnvironment
from pytorch_lightning.strategies.launchers.base import _Launcher
from pytorch_lightning.utilities import _HYDRA_AVAILABLE

if _HYDRA_AVAILABLE:
    from hydra.core.hydra_config import HydraConfig
    from hydra.utils import to_absolute_path


class _SubprocessScriptLauncher(_Launcher):
    r"""
    A process laucher that invokes the current script as many times as desired in a single node.

    This launcher needs to be invoked on each node.
    In its default behavior, the main process in each node then spawns N-1 child processes via :func:`subprocess.Popen`,
    where N is the number of devices (e.g. GPU) per node. It is very similar to how :mod:`torch.distributed.run`
    launches processes.

    For example, if the script gets invoked with the command

    .. code-block:: bash

        python train.py --devices 4

    The launcher will create three additional subprocesses that get called like so:

    .. code-block:: bash

        LOCAL_RANK=1 python train.py --devices 4
        LOCAL_RANK=2 python train.py --devices 4
        LOCAL_RANK=3 python train.py --devices 4

    It is implied that the main process which launched the others has ``LOCAL_RANK=0``.
    Beside the local rank, the following other environment variables also get set, but unlike the local rank, these
    get determined by the cluster environment:

    1. `MASTER_ADDR`: The IP address of the main node.
    2. `MASTER_PORT`: The port number of the main node through which all processes communicate.
    3. `NODE_RANK`: The index of the node the current process is running on. Ranges from 0 to ``num_nodes - 1``.
    4. `WORLD_SIZE`: The total number of processes across all nodes, i.e., ``num_processes * num_nodes``.

    Arguments:
        cluster_environment: A cluster environment that provides access to world size, node rank, etc.
        num_processes: The number of processes to launch in the current node.
        num_nodes: The total number of nodes that participate in this process group.
    """

    @property
    def is_interactive_compatible(self) -> bool:
        return False

    def __init__(self, cluster_environment: ClusterEnvironment, num_processes: int, num_nodes: int) -> None:
        super().__init__()
        self.cluster_environment = cluster_environment
        self.num_processes = num_processes
        self.num_nodes = num_nodes

    def launch(self, function: Callable, *args: Any, trainer: Optional["pl.Trainer"] = None, **kwargs: Any) -> Any:
        """Creates new processes, then calls the given function.

        Arguments:
            function: A callback function to execute after all processes have been created.
                It is up to the implementation of this function to synchronize the processes, e.g., with barriers.
            *args: Optional positional arguments to be passed to the given function.
            trainer: Optional reference to the :class:`~pytorch_lightning.trainer.trainer.Trainer`.
            **kwargs: Optional keyword arguments to be passed to the given function.
        """
        if not self.cluster_environment.creates_processes_externally:
            self._call_children_scripts()
        return function(*args, **kwargs)

    def _get_complete_path(self, command: str) -> str:
        return os.path.abspath(command)

    def _get_launch_command(self, command: List[str], local_rank: int) -> Tuple[List[str], Optional[str]]:
        return command, None

    def _call_children_scripts(self) -> None:
        # bookkeeping of spawned processes
        self._check_can_spawn_children()

        # DDP Environment variables
        os.environ["MASTER_ADDR"] = self.cluster_environment.main_address
        os.environ["MASTER_PORT"] = str(self.cluster_environment.main_port)

        # allow the user to pass the node rank
        os.environ["NODE_RANK"] = str(self.cluster_environment.node_rank())
        os.environ["LOCAL_RANK"] = str(self.cluster_environment.local_rank())

        # Check if the current calling command looked like `python a/b/c.py` or `python -m a.b.c`
        # See https://docs.python.org/3/reference/import.html#main-spec
        if __main__.__spec__ is None:  # pragma: no-cover
            # pull out the commands used to run the script and resolve the abs file path
            command = sys.argv
            try:
                full_path = self._get_complete_path(command[0])
            except Exception:
                full_path = os.path.abspath(command[0])

            command[0] = full_path
            # use the same python interpreter and actually running
            command = [sys.executable] + command
        else:  # Script called as `python -m a.b.c`
            command = [sys.executable, "-m", __main__.__spec__.name] + sys.argv[1:]

        os.environ["WORLD_SIZE"] = f"{self.num_processes * self.num_nodes}"

        for local_rank in range(1, self.num_processes):
            env_copy = os.environ.copy()
            env_copy["LOCAL_RANK"] = f"{local_rank}"

            # remove env var if global seed not set
            if os.environ.get("PL_GLOBAL_SEED") is None and "PL_GLOBAL_SEED" in env_copy:
                del env_copy["PL_GLOBAL_SEED"]

            # start process
            command, cwd = self._get_launch_command(command, local_rank)
            subprocess.Popen(command, env=env_copy, cwd=cwd)

            # starting all processes at once can cause issues
            # with dataloaders delay between 1-10 seconds
            delay = np.random.uniform(1, 5, 1)[0]
            sleep(delay)

    def _check_can_spawn_children(self) -> None:
        if self.cluster_environment.local_rank() != 0:
            raise RuntimeError(
                "Lightning attempted to launch new distributed processes with `local_rank > 0`. This should not happen."
                " Possible reasons: 1) LOCAL_RANK environment variable was incorrectly modified by the user,"
                " 2) `ClusterEnvironment.creates_processes_externally` incorrectly implemented."
            )


class _HydraSubprocessScriptLauncher(_SubprocessScriptLauncher):
    """Hydra Launcher to support Hydra commands."""

    def _get_complete_path(self, command: str) -> str:
        return to_absolute_path(command)

    def _get_launch_command(self, command: List[str], local_rank: int) -> Tuple[List[str], Optional[str]]:
        """Modifies the command to support Hydra initiated processes."""
        if not HydraConfig.initialized():
            return command, None

        # If Hydra is initialized:
        #   1) Set `cwd` to the hydra working directory
        #   2) Use the stored configuration in `hydra_cfg.output_subdir / config.yaml` to spawn a new child

        cwd = os.getcwd()
        os_cwd = f'"{cwd}"'  # this is needed to handle characters like `=` in the directory name

        hydra_cfg = HydraConfig.get()
        hydra_output = os.path.join(cwd, hydra_cfg.output_subdir)

        if __main__.__spec__ is None:  # pragma: no-cover
            command_no_args = command[:2]
        else:
            # this fails for `python -m pdb -m a.b.c <args>`
            command_no_args = command[:3]

        command = command_no_args

        # run the Hydra job using the current job configuration
        # - typically located in:
        #        RUN MODE: hydra.run.dir/.hydra/config.ayml
        #        MULTIRUN MODE: hydra.sweep.dir/hydra.sweep.subdir/.hydra/config.yaml
        command += ["-cp", hydra_output, "-cn", "config.yaml"]

        # hydra.output_subdir=.pl_ddp_hydra_{local_rank}
        #   Store process config in its own to avoid overwriting
        #   and allow the user to very that each spawned job uses
        #   the same configuration
        # hydra.run.dir={os_cwd}
        #   This makes sure to run this job, log, and store any outputs
        #   in the current experiment directory
        #
        # hydra.job.name=train_ddp_process_{local_rank}
        #   This defines the logging output file for the process
        command += [
            f"hydra.output_subdir=.pl_ddp_hydra_{local_rank}",
            f"hydra.run.dir={os_cwd}",
            f"hydra.job.name=train_ddp_process_{local_rank}",
        ]
        return command, cwd

    def launch(self, function: Callable, *args: Any, trainer: Optional["pl.Trainer"] = None, **kwargs: Any) -> Any:
        """Creates new processes, then calls the given function.

        Arguments:
            function: A callback function to execute after all processes have been created.
                It is up to the implementation of this function to synchronize the processes, e.g., with barriers.
            *args: Optional positional arguments to be passed to the given function.
            trainer: Optional reference to the :class:`~pytorch_lightning.trainer.trainer.Trainer`.
            **kwargs: Optional keyword arguments to be passed to the given function.
        """
        results = super().launch(function, *args, **kwargs)
        _teardown_ddp_for_hydra_multirun()
        return results


def _teardown_ddp_for_hydra_multirun():
    if HydraConfig.initialized():
        # shutdown any distributed process groups
        if torch.distributed.is_initialized():
            torch.distributed.destroy_process_group()

        envs = (
            "LOCAL_RANK",
            "NODE_RANK",
            "WORLD_SIZE",
            "MASTER_ADDR",
            "MASTER_PORT",
        )
        for name in envs:
            os.environ.pop(name, None)
