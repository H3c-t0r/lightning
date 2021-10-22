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
from abc import ABC, abstractmethod
from collections import Callable
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Sequence, Tuple, Union

import torch
import torch.nn as nn
from torch import Tensor
from torch.optim import Optimizer
from torch.utils.data import DataLoader, DistributedSampler, RandomSampler, SequentialSampler

from pytorch_lightning import Trainer
from pytorch_lightning.accelerators import Accelerator
from pytorch_lightning.lite.wrappers import _LiteDataLoader, _LiteModule, _LiteOptimizer
from pytorch_lightning.plugins import (
    DDPShardedPlugin,
    DDPSpawnPlugin,
    DeepSpeedPlugin,
    PLUGIN_INPUT,
    TPUSpawnPlugin,
    TrainingTypePlugin,
)
from pytorch_lightning.trainer.connectors.accelerator_connector import AcceleratorConnector
from pytorch_lightning.trainer.data_loading import TrainerDataLoadingMixin
from pytorch_lightning.utilities import DeviceType, DistributedType, move_data_to_device
from pytorch_lightning.utilities.apply_func import apply_to_collection, convert_to_tensors
from pytorch_lightning.utilities.data import has_iterable_dataset
from pytorch_lightning.utilities.exceptions import MisconfigurationException


class LightningLite(ABC):
    """Lite accelerates your PyTorch training or inference code with minimal changes required.

    - Automatic placement of models and data onto the device
    - Automatic support for mixed and double precision (smaller memory footprint)
    - Seamless switching between hardware (CPU, GPU, TPU) and distributed training strategies
      (data-parallel training, sharded training, etc.)
    - Automated spawning of processes, no launch utilities required
    - Multi-node support

    Args:
        accelerator: The hardware to run on. Possible choices are: cpu, gpu, tpu.
        strategy: Strategy for how to run across multiple devices. Possible choices are:
            dp, ddp, ddp_spawn, tpu_spawn, deepspeed, ddp_sharded.
        devices: Number of devices to train on (int) or which GPUs to train on (list or str). The value applies
            per node.
        num_nodes: Number of GPU nodes for distributed training.
        precision: Double precision (64), full precision (32), half precision (16) or bfloat16 precision (bf16).
        plugins: One or several custom plugins
        gpus: Provides the same function as the ``devices`` argument but implies ``accelerator="gpu"``.
        tpu_cores: Provides the same function as the ``devices`` argument but implies ``accelerator="tpu"``.
    """

    def __init__(
        self,
        accelerator: Optional[Union[str, Accelerator]] = None,
        strategy: Optional[Union[str, TrainingTypePlugin]] = None,
        devices: Optional[Union[List[int], str, int]] = None,
        num_nodes: int = 1,
        precision: Union[int, str] = 32,
        plugins: Optional[Union[PLUGIN_INPUT, List[PLUGIN_INPUT]]] = None,
        gpus: Optional[Union[List[int], str, int]] = None,
        tpu_cores: Optional[Union[List[int], str, int]] = None,
    ) -> None:
        self._check_accelerator_support(accelerator)
        self._check_strategy_support(strategy)
        gpu_ids, tpu_cores = Trainer._parse_devices(gpus=gpus, auto_select_gpus=False, tpu_cores=tpu_cores)
        self._accelerator_connector = AcceleratorConnector(
            num_processes=1,
            devices=devices,
            tpu_cores=tpu_cores,
            ipus=None,
            accelerator=accelerator,
            strategy=strategy,
            gpus=gpus,
            gpu_ids=gpu_ids,
            num_nodes=num_nodes,
            sync_batchnorm=False,  # TODO: add support?
            benchmark=False,
            replace_sampler_ddp=True,
            deterministic=False,
            precision=precision,
            amp_type="native",
            amp_level=None,
            plugins=plugins,
        )
        self._accelerator = self._accelerator_connector.accelerator
        self._strategy = self._accelerator.training_type_plugin
        self._precision_plugin = self._accelerator.precision_plugin

        # wrap the run method so we can inject setup logic or spawn processes for the user
        setattr(self, "run", self._run_wrapper(self.run))

        self._num_models: int = 0

    @property
    def device(self) -> torch.device:
        """The current device this process runs on.

        Use this to create tensors directly on the device if needed.
        """
        return self._accelerator.root_device

    @property
    def global_rank(self) -> int:
        """The global index of the current process across all devices and nodes."""
        return getattr(self._strategy, "global_rank", 0)

    @property
    def local_rank(self) -> int:
        """The index of the current process among the processes running on the local node."""
        return getattr(self._strategy, "local_rank", 0)

    @property
    def node_rank(self) -> int:
        """The index of the current node."""
        return getattr(self._strategy, "node_rank", 0)

    @property
    def world_size(self) -> int:
        """The total number of processes running across all devices and nodes."""
        return getattr(self._strategy, "world_size", 1)

    @property
    def is_global_zero(self) -> bool:
        """Wether this rank is rank zero."""
        return self._strategy.is_global_zero

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        """All the code inside this run method gets accelerated by Lite.

        Args:
            *args: Add any positional arguments you need, e.g., the hyperparameters for your model
            **kwargs: Add any keyword arguments you need, e.g., the hyperparameters for your model
        """

    def setup(
        self,
        model: nn.Module,
        optimizers: Union[Optimizer, List[Optimizer]],
        move_to_device: bool = True,
    ) -> Tuple[_LiteModule, Union[_LiteOptimizer, List[_LiteOptimizer]]]:
        """Setup a model and its optimizers for accelerated training.

        Args:
            model: A model to setup
            optimizers: A list of optimizers to setup
            move_to_device: If set ``True`` (default), moves the model to the correct device. Set this to ``False``
                and alternatively use :meth:`to_device` manually.

        Returns:
            The tuple of the wrapped model and list of optimizers, in the same order they were passed in.
        """
        # wrap all objects passed in and return them in the same order
        optimizers = [optimizers] if isinstance(optimizers, Optimizer) else optimizers

        self._validate_setup(model, optimizers)

        if move_to_device:
            params_on_cpu = dict(model.named_parameters())
            model = self.to_device(model)
            params_on_device = dict(model.named_parameters())

            # When the user creates the optimizer, they reference the parameters on the CPU.
            # However, when running with TPU the parameters get copied and the reference in the optimizer
            # remains invalid. We need to update the references to point to the parameter tensors on the device.
            mapping = {param: params_on_device[name] for name, param in params_on_cpu.items()}
            for optimizer in optimizers:
                for param_group in optimizer.param_groups:
                    param_group["params"] = [mapping.get(p, p) for p in param_group["params"]]

        model, optimizers = self._setup_model_and_optimizers(model, optimizers)
        optimizers = optimizers[0] if isinstance(optimizers, Sequence) and len(optimizers) == 1 else optimizers
        self._num_models += 1
        return model, optimizers

    def setup_dataloaders(
        self, *dataloaders: DataLoader, replace_sampler: bool = True, move_to_device: bool = True
    ) -> Union[DataLoader, List[DataLoader], Iterable]:
        """Setup one or multiple dataloaders for accelerated training. If you need different settings for each
        dataloader, call this method individually for each one.

        Args:
            *dataloaders: A single dataloader or a sequence of dataloaders.
            replace_sampler: If set ``True`` (default), automatically wraps or replaces the sampler on the dataloader(s)
                for distributed training. If you have a custom sampler defined, set this to this argument to ``False``.
            move_to_device: If set ``True`` (default), moves the data returned by the dataloader(s) automatially to
                the correct device. Set this to ``False`` and alternatively use :meth:`to_device` manually on the
                returned data.

        Returns:
            The wrapped dataloaders, in the same order they were passed in.
        """
        self._validate_setup_dataloaders(*dataloaders)
        # user can call this method independently instead of the general purpose setup method
        dataloaders = [
            self._setup_dataloader(dataloader, replace_sampler=replace_sampler, move_to_device=move_to_device)
            for dataloader in dataloaders
        ]
        dataloaders = dataloaders[0] if len(dataloaders) == 1 else dataloaders
        return dataloaders

    def _setup_dataloader(
        self, dataloader: Union[Iterable, DataLoader], replace_sampler: bool = True, move_to_device: bool = True
    ) -> Union[Iterable, DataLoader]:
        """Setup a single dataloader for accelerated training.

        Args:
            dataloader: The dataloader to accelerate.
            replace_sampler: If set ``True`` (default), automatically wraps or replaces the sampler on the dataloader
                for distributed training. If you have a custom sampler defined, set this to this argument to ``False``.
            move_to_device: If set ``True`` (default), moves the data returned by the dataloader automatially to
                the correct device. Set this to ``False`` and alternatively use :meth:`to_device` manually on the
                returned data.

        Returns:
            The wrapped dataloader.
        """
        if isinstance(dataloader, DataLoader):
            if replace_sampler and self._requires_distributed_sampler(dataloader):
                if not isinstance(dataloader.sampler, (SequentialSampler, RandomSampler)):
                    raise MisconfigurationException(
                        "You seem to have configured a sampler in your DataLoader. This will be replaced "
                        " by `DistributedSampler` since `replace_sampler_ddp` is True and you are using"
                        " distributed training. Either remove the sampler from your DataLoader or set"
                        " `replace_sampler=False` if you want to use your custom sampler."
                    )
                sampler = self._get_distributed_sampler(dataloader, **self._strategy.distributed_sampler_kwargs)

            kwargs = TrainerDataLoadingMixin._get_dataloader_init_kwargs(dataloader, sampler)
            device = self.device if move_to_device else None
            if isinstance(self._strategy, TPUSpawnPlugin):
                dataloader = DataLoader(**kwargs)
            else:
                dataloader = _LiteDataLoader(device=device, **kwargs)
        return self._strategy.process_dataloader(dataloader)

    def backward(self, tensor: Tensor, *args: Any, model: Optional[_LiteModule] = None, **kwargs: Any) -> None:
        """Replaces ``loss.backward()`` in your training loop. Handles precision and automatically for you.

        Args:
            tensor: The tensor (loss) to back-propagate gradients from.
            *args: Optional positional arguments passed to the underlying backward function.
            model: Optional model instance for plugins that require the model for backward().
            **kwargs: Optional named keyword arguments passed to the underlying backward function.

        Note:
            When using ``strategy='deepspeed'`` and multiple models were setup, it is required to pass in the
            model as argument here.
        """
        if self._num_models > 0 and isinstance(self._strategy, DeepSpeedPlugin):
            if not isinstance(model, _LiteModule):
                raise MisconfigurationException(
                    "When using multiple models + deepspeed, please provide the model used to perform the optimization."
                )

            # requires to attach the current `DeepSpeedEngine` for the `_LiteOptimizer.step` call.
            self._strategy.model = model.module

        assert self._strategy.model
        self._precision_plugin._run_backward(tensor, self._strategy.model, *args, **kwargs)

    @contextmanager
    def cast(self) -> Generator[None, None, None]:
        """A context manager to automatically convert operations for the chosen precision.

        Use this only if the `forward` method of your model does not cover all operations you wish to run with the
        chosen precision setting.
        """
        with self._precision_plugin.forward_context():
            yield

    def to_device(self, obj: Union[nn.Module, Tensor, Any]) -> Union[nn.Module, Tensor, Any]:
        """Move a :class:`torch.nn.Module` or a collection of tensors to the current device, if it is not already
        on that device.

        Args:
            obj: An object to move to the device. Can be an instance of :class:`torch.nn.Module`, a tensor, or a
                 (nested) collection of tensors (e.g., a dictionary).

        Returns:
            A reference to the object that was moved to the new device.
        """
        if isinstance(obj, nn.Module):
            if self.device.type == "cuda":
                # need to call this manually here again in case we spawned with DDPSpawnPlugin
                # TODO: refactor to let plugin handle this cleanly
                torch.cuda.set_device(self.device)
            return obj.to(self.device)
        return move_data_to_device(obj, device=self.device)

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Print something only on the first process.

        Arguments passed to this method are forwarded to the Python built-in :func:`print` function.
        """
        if self.local_rank == 0:
            print(*args, **kwargs)

    def barrier(self) -> None:
        """Wait for all processes to enter this call. Use this to synchronize all parallel processes, but only if
        necessary, otherwise the overhead of synchronization will cause your program to slow down.

        Example::

            if self.global_rank == 0:
                # let process 0 download the dataset
                dataset.download_files()

            # let all processes wait before reading the dataset
            self.barrier()

            # now all processes can read the files and start training
        """
        self._strategy.barrier()

    def reduce_decision(self, decision: bool) -> bool:
        """Reduce a boolean decision across processes.

        Use this for example to determine an early stopping condition, in which case you want to stop if any of
        the processes determine to stop.

        Args:
            decision: The decision on the current process

        Return:
            If at least one of the processes enters with ``decision=True``, then all processes will return `True`.
            Otherwise returns ``False``.
        """
        return self._strategy.reduce_boolean_decision(decision)

    def all_gather(
        self, data: Union[torch.Tensor, Dict, List, Tuple], group: Optional[Any] = None, sync_grads: bool = False
    ) -> Union[torch.Tensor, Dict, List, Tuple]:
        r"""
        Gather tensors or collections of tensors from multiple processes.

        Args:
            data: int, float, tensor of shape (batch, ...), or a (possibly nested) collection thereof.
            group: the process group to gather results from. Defaults to all processes (world)
            sync_grads: flag that allows users to synchronize gradients for the all_gather operation

        Return:
            A tensor of shape (world_size, batch, ...), or if the input was a collection
            the output will also be a collection with tensors of this shape.
        """
        group = group if group is not None else torch.distributed.group.WORLD
        data = convert_to_tensors(data, device=self.device)
        return apply_to_collection(data, torch.Tensor, self._strategy.all_gather, group=group, sync_grads=sync_grads)

    def broadcast(self, obj: object, src: int = 0) -> object:
        return self._strategy.broadcast(obj, src=src)

    def save_checkpoint(self, filepath: Union[str, Path], content: Dict[str, Any]) -> None:
        """Save a checkpoint contents to a file.

        How and which processes save gets determined by the `strategy`. For example, the `ddp` strategy
        saves checkpoints only on process 0.

        Args:
            filepath: A path to where the file should be saved
            content: A dictionary with contents, i.e., the state dict of your model
        """
        self._strategy.save_checkpoint(content, filepath)

    def execute_on_rank(self, func: Callable, rank: int, *args: Any, **kwargs: Any) -> None:
        """Execute the given function only on the given process.

        Args:
            func: The function to execute
            rank: The index of the process across all devices and nodes (global rank). This value must be an integer
                in the range ``[0, self.world_size - 1]``.
            *args: Optional positional arguments passed to the function
            **kwargs: Optional named arguments passed to the function
        """
        if self.global_rank == rank:
            func(*args, **kwargs)

    def _run_wrapper(self, run_method: Callable) -> Callable:
        return partial(self._run_impl, run_method)

    def _run_impl(self, run_method: Callable, *args: Any, **kwargs: Any) -> Any:
        self._set_plugin_specific_precision_variables()
        self._accelerator.setup_environment()

        # apply sharded context to prevent OOM
        run_method = partial(self._run_with_sharded_context, run_method)

        if isinstance(self._strategy, DDPSpawnPlugin):
            return self._strategy.spawn(run_method, *args, **kwargs)
        else:
            return run_method(*args, **kwargs)

    def _run_with_sharded_context(self, run_method: Callable, *args: Any, **kwargs: Any) -> Any:
        with self._strategy.model_sharded_context():
            return run_method(*args, **kwargs)

    def _set_plugin_specific_precision_variables(self) -> None:
        # todo: these are hacks as plugins rely on access to the precision plugin
        if isinstance(self._strategy, DeepSpeedPlugin):
            self._set_deepspeed_precision_variables()
        if isinstance(self._strategy, DDPShardedPlugin):
            self._strategy._precision = self._accelerator_connector.precision

    def _set_deepspeed_precision_variables(self) -> None:
        amp_type = self._accelerator_connector.amp_type
        amp_level = self._accelerator_connector.amp_level
        precision = self._accelerator_connector.precision
        self._strategy.amp_level, self._strategy.amp_type, self._strategy._precision = amp_level, amp_type, precision

    def _setup_model_and_optimizers(
        self,
        model: nn.Module,
        optimizers: List[Optimizer],
    ) -> Tuple[_LiteModule, Union[_LiteOptimizer, List[_LiteOptimizer]]]:
        # Let accelerator/plugin wrap and connect the models and optimizers
        [model], optimizers = self._strategy._setup_models_and_optimizers([model], optimizers)
        model = _LiteModule(model, self._accelerator)
        optimizers = [_LiteOptimizer(optimizer=optimizer, accelerator=self._accelerator) for optimizer in optimizers]
        return model, optimizers

    def _requires_distributed_sampler(self, dataloader: DataLoader) -> bool:
        return (
            self._accelerator_connector.is_distributed
            and not isinstance(dataloader.sampler, DistributedSampler)
            and not has_iterable_dataset(dataloader)
        )

    @staticmethod
    def _get_distributed_sampler(dataloader: DataLoader, **kwargs: Any) -> DistributedSampler:
        kwargs.setdefault("seed", int(os.getenv("PL_GLOBAL_SEED", 0)))
        return DistributedSampler(dataloader.dataset, **kwargs)

    def _check_accelerator_support(self, accelerator: Optional[Union[str, Accelerator]]) -> None:
        if accelerator is None:
            return
        supported = [t.lower() for t in self._supported_device_types()]
        if not isinstance(accelerator, (Accelerator, str)) or accelerator not in supported:
            raise MisconfigurationException(
                f"`accelerator={repr(accelerator)}` is not a valid choice."
                f" Choose one of {supported} or pass in a `Accelerator` instance."
            )

    def _check_strategy_support(self, strategy: Optional[Union[str, TrainingTypePlugin]]) -> None:
        if strategy is None:
            return
        supported = [t.lower() for t in self._supported_strategy_types()]
        if not isinstance(strategy, (TrainingTypePlugin, str)) and strategy not in supported:
            raise MisconfigurationException(
                f"`strategy={repr(strategy)}` is not a valid choice."
                f" Choose one of {supported} or pass in a `TrainingTypePlugin` instance."
            )

    @staticmethod
    def _supported_device_types() -> Sequence[DeviceType]:
        return (
            DeviceType.CPU,
            DeviceType.GPU,
            DeviceType.TPU,
        )

    @staticmethod
    def _supported_strategy_types() -> Sequence[str]:
        return (
            DistributedType.DP,
            DistributedType.DDP,
            DistributedType.DDP_SPAWN,
            DistributedType.TPU_SPAWN,
            DistributedType.DP,
            DistributedType.DEEPSPEED,
            DistributedType.DDP_SHARDED,
            DistributedType.DDP_SHARDED_SPAWN,
        )

    @staticmethod
    def _validate_setup(model: nn.Module, optimizers: List[Optimizer]) -> None:
        if isinstance(model, _LiteModule):
            raise MisconfigurationException("A module should be passed only once to the ``setup`` method")

        if any(isinstance(opt, _LiteOptimizer) for opt in optimizers):
            raise MisconfigurationException("An optimizer should be passed only once to the ``setup`` method")

    @staticmethod
    def _validate_setup_dataloaders(*dataloaders: Union[DataLoader, List[DataLoader]]) -> None:
        if any(isinstance(dl, _LiteDataLoader) for dl in dataloaders):
            raise MisconfigurationException(
                "A dataloader should be passed only once to the ``setup_dataloaders`` method"
            )

        if any(not isinstance(dl, DataLoader) for dl in dataloaders):
            raise MisconfigurationException("Only PyTorch DataLoader are currently supported.")
