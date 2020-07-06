from abc import ABC, abstractmethod
from typing import Any, Optional
import numbers

import torch
from torch import nn
import numpy as np

from pytorch_lightning.metrics.converters import (
    sync_ddp_if_available, convert_to_tensor, convert_to_numpy)
from pytorch_lightning.utilities.apply_func import apply_to_collection
from pytorch_lightning.utilities.device_dtype_mixin import DeviceDtypeModuleMixin


class Metric(DeviceDtypeModuleMixin, nn.Module, ABC):
    """
    Abstract base class for metric implementation.

    Should be used to implement metrics that
    1. Return multiple Outputs
    2. Handle their own DDP sync

    Metric hooks that can be implemented are:
        input_convert: pre-forward hook that takes care of input conversion
        output_convert: post-forward hook that takes care of output convertion
        ddp_sync: implementation of ddp sync
        compute: post-ddp sync for additional metric computations

    Call order:
        input_convert -> forward -> output_convert -> ddp_sync -> compute

    """

    def __init__(self, name: str):
        """
        Args:
            name: the metric's name

        """
        super().__init__()
        self.name = name
        self._dtype = torch.get_default_dtype()
        self._device = torch.device('cpu')
        self.register_forward_pre_hook(self.input_convert)
        self.register_forward_hook(self.output_convert)
        self.register_forward_hook(self.ddp_sync)
        self.register_forward_hook(self.compute)

    @abstractmethod
    def forward(self, *args, **kwargs):
        """
        Implements the actual metric computation.

        Returns:
            metric value or metric state

        """
        raise NotImplementedError

    def compute(self, module: nn.Module, input: Any, output: Any):
        """
        Implement additionally metric computations to be done after the ddp sync

        Args:
            module: current metric module

            input: input to forward method

            output: output from forward method

        Returns:
            final metric value

        """
        return output

    def ddp_sync(self, module: nn.Module, input: Any, output: Any):
        """
        Implement how the outputs from forward should be synced

        Args:
            module: current metric module

            input: input to forward method

            output: output from forward method

        Returns:
            synced output

        """
        return output

    def input_convert(self, module: nn.Module, input: Any):
        """
        Implement how the inputs should be casted before calling forward

        Args:
            module: current metric module

            input: input to forward method

        Returns:
            casted input
        """
        return input

    def output_convert(self, module: nn.Module, input: Any, output: Any):
        """
        Implement how outputs from forward should be casted

        Args:
            module: current metric module

            input: input to forward method

            output: output from forward method

        Returns:
            casted outputs
        """
        return output


class TensorMetric(Metric):
    """
    Base class for metric implementation operating directly on tensors.
    All inputs and outputs will be casted to tensors if necessary.
    Already handles DDP sync and input/output conversions.
    """

    def __init__(self, name: str,
                 reduce_group: Optional[Any] = None,
                 reduce_op: Optional[Any] = None,
                 ddp_normalize: bool = False):
        """

        Args:
            name: the metric's name
            reduce_group: the process group for DDP reduces (only needed for DDP training).
                Defaults to all processes (world)
            reduce_op: the operation to perform during reduction within DDP (only needed for DDP training).
                Defaults to sum.
            ddp_normalize: if true, will divide the DDP reduce result by the world rank
        """
        super().__init__(name)
        self.reduce_group = reduce_group
        self.reduce_op = reduce_op
        self.ddp_normalize = ddp_normalize

    def input_convert(self, module: nn.Module, input: Any):
        return apply_to_collection(input,
                                   (torch.Tensor, np.ndarray, numbers.Number),
                                   convert_to_tensor,
                                   self.dtype, self.device)

    def output_convert(self, module: nn.Module, input: Any, output: Any):
        return apply_to_collection(output, torch.Tensor, convert_to_tensor,
                                   self.dtype, self.device)

    def ddp_sync(self, module: nn.Module, input: Any, output: Any):
        return apply_to_collection(output, torch.Tensor, sync_ddp_if_available,
                                   self.reduce_group, self.reduce_op, self.ddp_normalize)


class TensorCollectionMetric(Metric):
    """
    Base class for metric implementation operating directly on tensors.
    All inputs will be casted to tensors if necessary. Outputs won't be casted.
    Already handles DDP sync and input conversions.

    This class differs from :class:`TensorMetric`, as it assumes all outputs to
    be collections of tensors and does not explicitly convert them. This is
    necessary, since some collections (like for ROC, Precision-Recall Curve etc.)
    cannot be converted to tensors at the highest level.
    All numpy arrays and numbers occuring in these outputs will still be converted.

    Use this class as a baseclass, whenever you want to ensure inputs are
    tensors and outputs cannot be converted to tensors automatically

    """

    def __init__(self, name: str,
                 reduce_group: Optional[Any] = None,
                 reduce_op: Optional[Any] = None,
                 ddp_normalize: bool = False):
        """

        Args:
            name: the metric's name
            reduce_group: the process group for DDP reduces (only needed for DDP training).
                Defaults to all processes (world)
            reduce_op: the operation to perform during reduction within DDP (only needed for DDP training).
                Defaults to sum.
            ddp_normalize: if true, will divide the DDP reduce result by the world rank
        """
        super().__init__(name)
        self.reduce_group = reduce_group
        self.reduce_op = reduce_op
        self.ddp_normalize = ddp_normalize

    def input_convert(self, module: nn.Module, input: Any):
        return apply_to_collection(input,
                                   (torch.Tensor, np.ndarray, numbers.Number),
                                   convert_to_tensor,
                                   self.dtype, self.device)

    def output_convert(self, module: nn.Module, input: Any, output: Any):
        return apply_to_collection(output,
                                   (torch.Tensor, np.ndarray, numbers.Number),
                                   convert_to_tensor,
                                   self.dtype, self.device)

    def ddp_sync(self, module: nn.Module, input: Any, output: Any):
        return apply_to_collection(output, torch.Tensor, sync_ddp_if_available,
                                   self.reduce_group, self.reduce_op, self.ddp_normalize)


class NumpyMetric(Metric):
    """
    Base class for metric implementation operating on numpy arrays.
    All inputs will be casted to numpy if necessary and all outputs will
    be casted to tensors if necessary.
    Already handles DDP sync and input/output conversions.
    """

    def __init__(self, name: str,
                 reduce_group: Optional[Any] = None,
                 reduce_op: Optional[Any] = None,
                 ddp_normalize: bool = False):
        """

        Args:
            name: the metric's name
            reduce_group: the process group for DDP reduces (only needed for DDP training).
                Defaults to all processes (world)
            reduce_op: the operation to perform during reduction within DDP (only needed for DDP training).
                Defaults to sum.
            ddp_normalize: if true, will divide the DDP reduce result by the world rank
        """
        super().__init__(name)
        self.reduce_group = reduce_group
        self.reduce_op = reduce_op
        self.ddp_normalize = ddp_normalize

    def input_convert(self, module: nn.Module, input: Any):
        return apply_to_collection(input,
                                   (torch.Tensor, np.ndarray, numbers.Number),
                                   convert_to_numpy)

    def output_convert(self, module: nn.Module, input: Any, output: Any):
        return apply_to_collection(output,
                                   (torch.Tensor, np.ndarray, numbers.Number),
                                   convert_to_tensor,
                                   self.dtype, self.device)

    def ddp_sync(self, module: nn.Module, input: Any, output: Any):
        return apply_to_collection(output, torch.Tensor, sync_ddp_if_available,
                                   self.reduce_group, self.reduce_op, self.ddp_normalize)
