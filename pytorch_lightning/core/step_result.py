from typing import Optional, Dict
from torch import Tensor
import torch


class Result(Dict):

    def __init__(self,
                 minimize: Optional[Tensor] = None,
                 early_stop_on: Tensor = None,
                 checkpoint_on: Tensor = None,
                 hiddens: Optional[Tensor] = None):
        """
        WIP! Split over many PRs... DO NOT USE YET

        TrainResult is an OrderedDict that gives type hints, allowed fields and validation for bad user input.

        Use as the return value for:
        - training_step

        .. note:: Plain dictionary returns are supported but are more prone to errors

        We automatically detach anything here for you to avoid holding references to graphs

        Args:
            minimize: Metric to minimize
            early_stop_on: Metric for early stopping. Ignored with a validation loop.
            checkpoint_on: Metric for checkpointing. Ignored with a validation loop otherwise defaults
                to `minimize` value.
            hiddens: tensor of hiddens to pass to next step when using TBPTT

        .. code-block: python

            # all options:
            def training_step(...):
                return TrainResult(
                    minimize=loss,
                    checkpoint_on=loss,
                )

                # equivalent
                return TrainResult(loss)

            # if you have no validation loop, you can still early_stop and/or checkpoint on a metric
            # only checkpointing is applied by default here
            return TrainResult(loss, early_stop_on=accuracy, checkpoint_on=bleu_score)

            result = TrainResult(loss)

            # logging will log to your logger(s) at the end of the batch
            result.log('train_nce_loss', loss)

            # you can log at the end of the batch, or epoch or both
            result.log('train_nce_loss', loss, on_batch_end=True, on_epoch_end=False)

            # same thing for the progress bar
            result.to_pbar(train_nce_loss', loss)
            result.to_pbar('train_nce_loss', loss, on_batch_end=True, on_epoch_end=False)

        Although 99% of the time we are interested in a metric for each training batch,
        (ie: loss decrease over the epoch), sometimes you may want to know something like the average loss
        for the full epoch. You can either define the `training_epoch_end` method for something fancy,
        or use the `on_epoch_end` argument with your custom reduce function

        .. code-block: python

            # maybe sum `log_probs` across all the training batches
            result.log('log_probs', log_probs, reduce_fx=torch.sum)

            # or do something weird to `log_probs` across all the training batches
            def my_weird_reduction(all_log_probs):
                all_log_probs = F.softmax(torch.cat(all_log_probs), dim=1)
                return all_log_probs

            result.log('log_probs', log_probs, reduce_fx=my_weird_reduction)
        """

        super().__init__()

        self.early_stop_on = early_stop_on
        self.checkpoint_on = checkpoint_on

        # TODO: should hiddens detach?
        self.hiddens = hiddens
        self.minimize = minimize

    @classmethod
    def union(cls, outputs, result=None):
        if result is None:
            result = Result()

        for out in outputs:
            for k, v in out.items():
                if k in ['reduce_fx_on_epoch_end']:
                    continue

                if k not in result and isinstance(v, (dict, Result)):
                    result[k] = Result()

                if isinstance(v, dict):
                    v = cls.union([v], result[k])

                if isinstance(v, list) and len(v) == 1:
                    v = v[0]
                result[k] = v

        return result

    @classmethod
    def from_result_dict(cls, dict_result, trainer):
        result = Result()

        if 'log' in dict_result:
            result.log_metrics(dict_result['log'])
        if 'progress_bar' in dict_result:
            result.pbar_metrics(dict_result['progress_bar'])

        # add the early stop metric
        if trainer.early_stop_callback is not None:
            early_stop_metric = trainer.early_stop_callback.monitor
            if early_stop_metric in dict_result:
                result.early_stop_on = dict_result[early_stop_metric]

        # add the checkpoint metric
        if trainer.checkpoint_callback is not None:
            checkpoint_metric = trainer.checkpoint_callback.monitor
            if checkpoint_metric in dict_result:
                result.checkpoint_on = dict_result[checkpoint_metric]

        return result

    def __reduce_on_callback(self, callback_name, name, metric, log, pbar, reduce_fx):
        assert isinstance(metric, torch.Tensor), f'{name} must be a torch.Tensor'

        keys = [f'reduce_{callback_name}']
        if log:
            keys.append(f'log_{callback_name}')
        if pbar:
            keys.append(f'pbar_{callback_name}')

        for key in keys:
            if key not in self:
                self[key] = {}

            if 'log' in key or 'pbar' in key:
                metric = metric.detach()

            metrics = self[key]
            metrics[name] = metric

        key = f'reduce_fx_{callback_name}'
        if key not in self:
            self[key] = {}

        metrics = self[key]
        metrics[name] = reduce_fx

    def pbar_metric(self, name: str, value: Tensor, on_batch_end=False, on_epoch_end=True, reduce_fx=torch.mean):
        if on_batch_end:
            self.__reduce_on_callback('on_batch_end', name, value, log=False, pbar=True, reduce_fx=reduce_fx)
        if on_epoch_end:
            self.__reduce_on_callback('on_epoch_end', name, value, log=False, pbar=True, reduce_fx=reduce_fx)

    def pbar_metrics(self, values: dict, on_batch_end=False, on_epoch_end=True, reduce_fx=torch.mean):
        for name, value in values.items():
            if on_batch_end:
                self.__reduce_on_callback('on_batch_end', name, value, log=False, pbar=True, reduce_fx=reduce_fx)
            if on_epoch_end:
                self.__reduce_on_callback('on_epoch_end', name, value, log=False, pbar=True, reduce_fx=reduce_fx)

    def log_metric(self, name: str, value: Tensor, on_batch_end=False, on_epoch_end=True, reduce_fx=torch.mean):
        if on_batch_end:
            self.__reduce_on_callback('on_batch_end', name, value, log=True, pbar=False, reduce_fx=reduce_fx)
        if on_epoch_end:
            self.__reduce_on_callback('on_epoch_end', name, value, log=True, pbar=False, reduce_fx=reduce_fx)

    def log_metrics(self, values: dict, on_batch_end=False, on_epoch_end=True, reduce_fx=torch.mean):
        for name, value in values.items():
            if on_batch_end:
                self.__reduce_on_callback('on_batch_end', name, value, log=True, pbar=False, reduce_fx=reduce_fx)
            if on_epoch_end:
                self.__reduce_on_callback('on_epoch_end', name, value, log=True, pbar=False, reduce_fx=reduce_fx)

    @property
    def log_on_batch_end(self):
        return self.__getitem__('log_on_batch_end')

    @log_on_batch_end.setter
    def log_on_batch_end(self, x):
        if x is not None:
            assert isinstance(x, dict), 'log_on_batch_end must be a dict'
            self.__setitem__('log_on_batch_end', x)

    @property
    def pbar_on_batch_end(self):
        return self.__getitem__('pbar_on_batch_end')

    @pbar_on_batch_end.setter
    def pbar_on_batch_end(self, x):
        if x is not None:
            assert isinstance(x, dict), 'pbar_on_batch_end must be a dict'
            self.__setitem__('pbar_on_batch_end', x)

    @property
    def log_on_epoch_end(self):
        return self.__getitem__('log_on_epoch_end')

    @log_on_epoch_end.setter
    def log_on_epoch_end(self, x):
        if x is not None:
            assert isinstance(x, dict), 'log_on_epoch_end must be a dict'
            self.__setitem__('log_on_epoch_end', x)

    @property
    def pbar_on_epoch_end(self):
        return self.__getitem__('pbar_on_epoch_end')

    @pbar_on_epoch_end.setter
    def pbar_on_epoch_end(self, x):
        if x is not None:
            assert isinstance(x, dict), 'pbar_on_epoch_end must be a dict'
            self.__setitem__('pbar_on_epoch_end', x)

    @property
    def progress_bar(self):
        return self.__getitem__('progress_bar')

    @progress_bar.setter
    def progress_bar(self, x):
        if x is not None:
            assert isinstance(x, dict), 'progress_bar_logs must be a dict'
            self.__setitem__('progress_bar', x)

    @property
    def logs(self):
        return self.__getitem__('logs')

    @logs.setter
    def logs(self, x):
        if x is not None:
            assert isinstance(x, dict), 'logs must be a dict'
            self.__setitem__('logs', x)

    @property
    def hiddens(self):
        return self._hiddens

    @hiddens.setter
    def hiddens(self, x):
        if x is not None:
            assert isinstance(x, Tensor), 'hiddens must be a torch.Tensor'
            self._hiddens = x
            self.__setitem__('hiddens', x)

    @property
    def checkpoint_on(self):
        # use minimize as default if no checkpoint_on is passed
        if 'checkpoint_on' not in self:
            minimize = self.__getitem__('minimize')
            self.__setitem__('checkpoint_on', minimize)

        return self.__getitem__('checkpoint_on')

    @checkpoint_on.setter
    def checkpoint_on(self, x):
        if x is not None:
            assert isinstance(x, Tensor), 'checkpoint_on must be a torch.Tensor'
            self.__setitem__('checkpoint_on', x.detach())

    @property
    def early_stop_on(self):
        # use minimize as default if no checkpoint_on is passed
        if 'early_stop_on' not in self:
            minimize = self.__getitem__('minimize')
            self.__setitem__('early_stop_on', minimize)

        return self.__getitem__('early_stop_on')

    @early_stop_on.setter
    def early_stop_on(self, x):
        if x is not None:
            assert isinstance(x, Tensor), 'early_stop_on must be a torch.Tensor'
            self.__setitem__('early_stop_on', x.detach())

    @property
    def minimize(self):
        return self.__getitem__('minimize')

    @minimize.setter
    def minimize(self, x):
        if x is not None:
            assert isinstance(x, Tensor), 'metric to minimize must be a torch.Tensor'
            m = 'the metric to minimize must have a computational graph. Minimize ' \
                'can only be used in training_end, training_step_end, training_epoch_end'
            assert x.grad_fn is not None, m
            self.__setitem__('minimize', x)


if __name__ == '__main__':
    import torch
    result = Result()
    result.log_metrics({'a': 2})
    result.minimize = torch.tensor(1)
