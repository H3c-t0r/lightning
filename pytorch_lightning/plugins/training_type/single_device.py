from typing import Any, Union

import torch

from pytorch_lightning.plugins.training_type.training_type_plugin import TrainingTypePlugin


class SingleDevicePlugin(TrainingTypePlugin):

    def __init__(self, device: torch.device):
        super().__init__()
        self.device: torch.device = device

    @property
    def on_tpu(self) -> bool:
        return False

    @property
    def on_gpu(self) -> bool:
        return self.device.type == "cuda" and torch.cuda.is_available()

    def reduce(self, tensor: Union[Any, torch.Tensor], *args: Any, **kwargs: Any) -> Union[Any, torch.Tensor]:
        """
        Reduces a tensor from several distributed processes to one aggregated tensor.
        As this plugin only operates with a single device, the reduction is simply the identity.

        Args:
            tensor: the tensor to sync and reduce
            *args: ignored
            **kwargs: ignored

        Return:
            the unmodified input as reduction is not needed for single process operation
        """
        return tensor

    @property
    def root_device(self) -> torch.device:
        return self.device

    def model_to_device(self) -> None:
        if self.on_gpu:
            torch.cuda.set_device(self.root_device)

        self._model.to(self.root_device)

    def connect(self, model: torch.nn.Module) -> torch.nn.Module:
        self._model = model
        self.model_to_device()
        return self.model

    @property
    def is_global_zero(self) -> bool:
        return True

    def barrier(self, *args, **kwargs) -> None:
        pass

    def broadcast(self, obj: object, src: int = 0) -> object:
        return obj
