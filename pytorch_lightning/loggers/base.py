import argparse
from abc import ABC, abstractmethod
from argparse import Namespace
from functools import wraps
from typing import Union, Optional, Dict, Iterable, Any, Callable, List

import torch


def rank_zero_only(fn: Callable):
    """Decorate a logger method to run it only on the process with rank 0.

    Args:
        fn: Function to decorate
    """

    @wraps(fn)
    def wrapped_fn(self, *args, **kwargs):
        if self.rank == 0:
            fn(self, *args, **kwargs)

    return wrapped_fn


class LightningLoggerBase(ABC):
    """Base class for experiment loggers."""

    def __init__(self):
        self._rank = 0

    @property
    @abstractmethod
    def experiment(self) -> Any:
        """Return the experiment object associated with this logger"""

    @abstractmethod
    def log_metrics(self, metrics: Dict[str, float], step: Optional[int] = None):
        """Record metrics.

        Args:
            metrics: Dictionary with metric names as keys and measured quantities as values
            step: Step number at which the metrics should be recorded
        """

    @staticmethod
    def _convert_params(params: Union[Dict[str, Any], Namespace]) -> Dict[str, Any]:
        # in case converting from namespace
        if isinstance(params, Namespace):
            params = vars(params)

        if params is None:
            params = {}

        return params

    @staticmethod
    def _sanitize_params(params: Dict[str, Any]) -> Dict[str, Any]:
        """Returns params with non-primitvies converted to strings for logging

        >>> params = {"float": 0.3,
        ...           "int": 1,
        ...           "string": "abc",
        ...           "bool": True,
        ...           "list": [1, 2, 3],
        ...           "namespace": Namespace(foo=3),
        ...           "layer": torch.nn.BatchNorm1d}
        >>> import pprint
        >>> pprint.pprint(LightningLoggerBase._sanitize_params(params))  # doctest: +NORMALIZE_WHITESPACE
        {'bool': True,
         'float': 0.3,
         'int': 1,
         'layer': "<class 'torch.nn.modules.batchnorm.BatchNorm1d'>",
         'list': '[1, 2, 3]',
         'namespace': 'Namespace(foo=3)',
         'string': 'abc'}
        """
        return {k: v if type(v) in [bool, int, float, str, torch.Tensor] else str(v) for k, v in params.items()}

    @abstractmethod
    def log_hyperparams(self, params: argparse.Namespace):
        """Record hyperparameters.

        Args:
            params: argparse.Namespace containing the hyperparameters
        """

    def save(self) -> None:
        """Save log data."""
        pass

    def finalize(self, status: str) -> None:
        """Do any processing that is necessary to finalize an experiment.

        Args:
            status: Status that the experiment finished with (e.g. success, failed, aborted)
        """
        pass

    def close(self) -> None:
        """Do any cleanup that is necessary to close an experiment."""
        pass

    @property
    def rank(self) -> int:
        """Process rank. In general, metrics should only be logged by the process with rank 0."""
        return self._rank

    @rank.setter
    def rank(self, value: int) -> None:
        """Set the process rank."""
        self._rank = value

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the experiment name."""

    @property
    @abstractmethod
    def version(self) -> Union[int, str]:
        """Return the experiment version."""


class LoggerCollection(LightningLoggerBase):
    """The `LoggerCollection` class is used to iterate all logging actions over the given `logger_iterable`.

    Args:
        logger_iterable: An iterable collection of loggers
    """

    def __init__(self, logger_iterable: Iterable[LightningLoggerBase]):
        super().__init__()
        self._logger_iterable = logger_iterable

    def __getitem__(self, index: int) -> LightningLoggerBase:
        return [logger for logger in self._logger_iterable][index]

    @property
    def experiment(self) -> List[Any]:
        return [logger.experiment for logger in self._logger_iterable]

    def log_metrics(self, metrics: Dict[str, float], step: Optional[int] = None) -> None:
        [logger.log_metrics(metrics, step) for logger in self._logger_iterable]

    def log_hyperparams(self, params: Union[Dict[str, Any], Namespace]) -> None:
        [logger.log_hyperparams(params) for logger in self._logger_iterable]

    def save(self) -> None:
        [logger.save() for logger in self._logger_iterable]

    def finalize(self, status: str) -> None:
        [logger.finalize(status) for logger in self._logger_iterable]

    def close(self) -> None:
        [logger.close() for logger in self._logger_iterable]

    @LightningLoggerBase.rank.setter
    def rank(self, value: int) -> None:
        self._rank = value
        for logger in self._logger_iterable:
            logger.rank = value

    @property
    def name(self) -> str:
        return '_'.join([str(logger.name) for logger in self._logger_iterable])

    @property
    def version(self) -> str:
        return '_'.join([str(logger.version) for logger in self._logger_iterable])
