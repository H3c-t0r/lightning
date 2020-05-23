r"""
Early Stopping
==============

Monitor a validation metric and stop training when it stops improving.

"""
from copy import deepcopy

import numpy as np
import torch

from pytorch_lightning import _logger as log
from pytorch_lightning.callbacks.base import Callback
from pytorch_lightning.utilities import rank_zero_warn

torch_inf = torch.tensor(np.Inf)


class EarlyStopping(Callback):
    r"""

    Args:
        monitor: quantity to be monitored. Default: ``'val_loss'``.
        min_delta: minimum change in the monitored quantity
            to qualify as an improvement, i.e. an absolute
            change of less than `min_delta`, will count as no
            improvement. Default: ``0``.
        patience: number of epochs with no improvement
            after which training will be stopped. Default: ``0``.
        verbose: verbosity mode. Default: ``False``.
        mode: one of {auto, min, max}. In `min` mode,
            training will stop when the quantity
            monitored has stopped decreasing; in `max`
            mode it will stop when the quantity
            monitored has stopped increasing; in `auto`
            mode, the direction is automatically inferred
            from the name of the monitored quantity. Default: ``'auto'``.
        strict: whether to crash the training if `monitor` is
            not found in the validation metrics. Default: ``True``.

    Example::

        >>> from pytorch_lightning import Trainer
        >>> from pytorch_lightning.callbacks import EarlyStopping
        >>> early_stopping = EarlyStopping('val_loss')
        >>> trainer = Trainer(early_stop_callback=early_stopping)
    """
    mode_dict = {
        'min': torch.lt,
        'max': torch.gt,
    }

    def __init__(self, monitor: str = 'val_loss', min_delta: float = 0.0, patience: int = 3,
                 verbose: bool = False, mode: str = 'auto', strict: bool = True):
        super().__init__()
        self.monitor = monitor
        self.patience = patience
        self.verbose = verbose
        self.strict = strict
        self.min_delta = min_delta
        self.wait = 0
        self.stopped_epoch = 0
        self.mode = mode

        if mode not in self.mode_dict:
            if self.verbose > 0:
                log.info(f'EarlyStopping mode {mode} is unknown, fallback to auto mode.')
            self.mode = 'auto'

        if self.mode == 'auto':
            if self.monitor == 'acc':
                self.mode = 'max'
            else:
                self.mode = 'min'
            if self.verbose > 0:
                log.info(f'EarlyStopping mode set to {self.mode} for monitoring {self.monitor}.')

        self.min_delta *= 1 if self.monitor_op == np.greater else -1
        self.best = np.Inf if self.monitor_op == np.less else -np.Inf

    def _validate_condition_metric(self, logs):
        """
        Checks that the condition metric for early stopping is good
        :param logs: callback metrics from validation output
        :return: True if specified metric is available
        """
        monitor_val = logs.get(self.monitor)
        error_msg = (f'Early stopping conditioned on metric `{self.monitor}`'
                     f' which is not available. Either add `{self.monitor}` to the return of '
                     f' validation_epoch end or modify your EarlyStopping callback to use any of the '
                     f'following: `{"`, `".join(list(logs.keys()))}`')

        if monitor_val is None:
            if self.strict:
                raise RuntimeError(error_msg)
            if self.verbose > 0:
                rank_zero_warn(error_msg, RuntimeWarning)

            return False

        return True

    @property
    def monitor_op(self):
        return self.mode_dict[self.mode]

    def state_dict(self):
        return {
            'wait': self.wait,
            'stopped_epoch': self.stopped_epoch,
            'best': self.best,
            'patience': self.patience
        }

    def load_state_dict(self, state_dict):
        state_dict = deepcopy(state_dict)
        self.wait = state_dict['wait']
        self.stopped_epoch = state_dict['stopped_epoch']
        self.best = state_dict['best']
        self.patience = state_dict['patience']

    def on_sanity_check_end(self, trainer, pl_module):
        logs = trainer.callback_metrics
        self._validate_condition_metric(logs)

    def on_epoch_end(self, trainer, pl_module):
        logs = trainer.callback_metrics
        if not self._validate_condition_metric(logs):
            return  # short circuit if metric not present

        stop_training = False
        current = logs.get(self.monitor)
        if not isinstance(current, torch.Tensor):
            current = torch.tensor(current)

        if self.monitor_op(current - self.min_delta, self.best):
            self.best = current
            self.wait = 0
        else:
            self.wait += 1
            if self.wait >= self.patience:
                self.stopped_epoch = trainer.current_epoch
                stop_training = True

        if stop_training:
            trainer.should_stop = True

    def on_train_end(self, trainer, pl_module):
        if self.stopped_epoch > 0 and self.verbose > 0:
            rank_zero_warn('Displayed epoch numbers by `EarlyStopping` start from "1" until v0.6.x,'
                           ' but will start from "0" in v0.8.0.', DeprecationWarning)
            log.info(f'Epoch {self.stopped_epoch + 1:05d}: early stopping')
