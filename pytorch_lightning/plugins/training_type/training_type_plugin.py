import os
from abc import ABC, abstractmethod
from pytorch_lightning.core.lightning import LightningModule
from typing import Optional

import torch

from pytorch_lightning import _logger as log
from pytorch_lightning.plugins.base_plugin import Plugin


class TrainingTypePlugin(Plugin, ABC):
    """A Plugin to change the behaviour of the training, validation and test-loop.

    """
    def __init__(self):
        self._model = None
        self._results = None
        self.global_rank = 0

    @property
    @abstractmethod
    def on_gpu(self) -> bool:
        """Returns whether the current process is done on GPU
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def root_device(self) -> torch.device:
        """Returns the root device
        """
        raise NotImplementedError

    @abstractmethod
    def model_to_device(self):
        """Moves the model to the correct device
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def is_global_zero(self) -> bool:
        """Whether the current process is the rank zero process not only on the local node, but for all nodes.
        """
        raise NotImplementedError

    @abstractmethod
    def reduce(self, output, *args, **kwargs):
        """Reduces the given output (e.g. across GPUs/Processes)
        """
        raise NotImplementedError

    @abstractmethod
    def barrier(self, name: Optional[str] = None):
        """Forces all possibly joined processes to wait for each other
        """
        raise NotImplementedError

    @abstractmethod
    def broadcast(self, obj: object, src: int = 0) -> object:
        """Broadcasts an object to all processes
        """
        raise NotImplementedError

    # TODO method this is currently unused. Check after complete refactors are pushed
    def set_nvidia_flags(self, is_slurm_managing_tasks, device_ids):
        if device_ids is None:
            return

        # set the correct cuda visible devices (using pci order)
        os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        all_gpu_ids = ",".join([str(x) for x in range(torch.cuda.device_count())])
        devices = os.environ.get("CUDA_VISIBLE_DEVICES", all_gpu_ids)
        log.info(f'LOCAL_RANK: {self.trainer.local_rank} - CUDA_VISIBLE_DEVICES: [{devices}]')

    def reduce_early_stopping_decision(self, should_stop: bool) -> bool:
        """Reduce the early stopping decision across all possibly spawned processes
        """
        return should_stop

    @property
    def model(self) -> torch.nn.Module:
        """Returns the potentially wrapped LightningModule

        """
        return self._model

    @model.setter
    def model(self, new_model: torch.nn.Module):
        self._model = new_model

    @property
    def lightning_module(self) -> LightningModule:
        """Returns the pure LightningModule without potential wrappers

        """
        return self._model

    @property
    def results(self):
        """
        The results of the last training/testing run will be cached here.
        In distributed training, we make sure to transfer the results to the appropriate master process.
        """
        # TODO: improve these docs
        return self._results

    @property
    def rpc_enabled(self) -> bool:
        return False

    def start_training(self, trainer: 'Trainer') -> None:
        # double dispatch to initiate the training loop
        self._results = trainer.train()

    def start_testing(self, trainer: 'Trainer') -> None:
        # double dispatch to initiate the test loop
        self._results = trainer.run_test()
