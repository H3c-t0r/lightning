from typing import Any, Callable, Optional, TYPE_CHECKING, Union

import torch
from torch.optim import Optimizer

from pytorch_lightning.accelerators.accelerator import Accelerator
from pytorch_lightning.plugins.precision import MixedPrecisionPlugin
from pytorch_lightning.plugins.training_type.single_tpu import SingleTPUPlugin
from pytorch_lightning.plugins.training_type.tpu_spawn import TPUSpawnPlugin
from pytorch_lightning.utilities import _XLA_AVAILABLE
from pytorch_lightning.utilities.exceptions import MisconfigurationException

if _XLA_AVAILABLE:
    import torch_xla.core.xla_model as xm
    from torch_xla._patched_functions import clip_grad_norm_

if TYPE_CHECKING:
    from pytorch_lightning.core.lightning import LightningModule
    from pytorch_lightning.trainer.trainer import Trainer


class TPUAccelerator(Accelerator):

    def setup(self, trainer: 'Trainer', model: 'LightningModule') -> None:
        """
        Raises:
            MisconfigurationException:
                If AMP is used with TPU, or if TPUs are not using a single TPU core or TPU spawn training.
        """
        if isinstance(self.precision_plugin, MixedPrecisionPlugin):
            raise MisconfigurationException(
                "amp + tpu is not supported. "
                "Only bfloats are supported on TPU. Consider using TPUHalfPrecisionPlugin"
            )

        if not isinstance(self.training_type_plugin, (SingleTPUPlugin, TPUSpawnPlugin)):
            raise MisconfigurationException("TPUs only support a single tpu core or tpu spawn training.")
        return super().setup(trainer, model)

    def run_optimizer_step(
        self, optimizer: Optimizer, optimizer_idx: int, lambda_closure: Callable, **kwargs: Any
    ) -> None:
        xm.optimizer_step(optimizer, barrier=False, optimizer_args={'closure': lambda_closure, **kwargs})

    def all_gather(self, tensor: torch.Tensor, group: Optional[Any] = None, sync_grads: bool = False) -> torch.Tensor:
        """
        Function to gather a tensor from several distributed processes
        Args:
            tensor: tensor of shape (batch, ...)
            group: the process group to gather results from. Defaults to all processes (world)
            sync_grads: flag that allows users to synchronize gradients for all_gather op
        Return:
            A tensor of shape (world_size, batch, ...)
        """
        # todo: Add support for backward with all_gather
        if torch.distributed.is_initialized():
            return xm.all_gather(tensor, group=group, sync_grads=sync_grads)
        return tensor

    def clip_gradients(self, optimizer: Optimizer, grad_clip_val: Union[float, int], norm_type: float = 2.0):

        model = self.lightning_module
        parameters = model.parameters()
        max_norm = grad_clip_val

        clip_grad_norm_(parameters, max_norm, norm_type)
