import os
import shutil
import logging
import warnings
import numpy as np

from pytorch_lightning.pt_overrides.override_data_parallel import LightningDistributedDataParallel


class Callback(object):
    """Abstract base class used to build new callbacks.
    # Properties
        params: dict. Training parameters
            (eg. verbosity, batch size, number of epochs...).
            Reference of the model being trained.
    The `logs` dictionary that callback methods
    take as argument will contain keys for quantities relevant to
    the current batch or epoch.
    Currently, the `.fit()` method of the `Sequential` model class
    will include the following quantities in the `logs` that
    it passes to its callbacks:
        on_epoch_end: logs include `acc` and `loss`, and
            optionally include `val_loss`
            (if validation is enabled in `fit`), and `val_acc`
            (if validation and accuracy monitoring are enabled).
        on_batch_begin: logs include `size`,
            the number of samples in the current batch.
        on_batch_end: logs include `loss`, and optionally `acc`
            (if accuracy monitoring is enabled).
    """

    def __init__(self):
        self.validation_data = None
        self.model = None

    def set_params(self, params):
        self.params = params

    def set_model(self, model):
        if type(model) is LightningDistributedDataParallel:
            model = model.module
        self.model = model

    def on_epoch_begin(self, epoch, logs=None):
        pass

    def on_epoch_end(self, epoch, logs=None):
        pass

    def on_batch_begin(self, batch, logs=None):
        pass

    def on_batch_end(self, batch, logs=None):
        pass

    def on_train_begin(self, logs=None):
        pass

    def on_train_end(self, logs=None):
        pass


class EarlyStopping(Callback):
    """Stop training when a monitored quantity has stopped improving.
    # Arguments
        monitor: quantity to be monitored.
        min_delta: minimum change in the monitored quantity
            to qualify as an improvement, i.e. an absolute
            change of less than min_delta, will count as no
            improvement.
        patience: number of epochs with no improvement
            after which training will be stopped.
        verbose: verbosity mode.
        mode: one of {auto, min, max}. In `min` mode,
            training will stop when the quantity
            monitored has stopped decreasing; in `max`
            mode it will stop when the quantity
            monitored has stopped increasing; in `auto`
            mode, the direction is automatically inferred
            from the name of the monitored quantity.
    """

    def __init__(self, monitor='val_loss',
                 min_delta=0.0, patience=0, verbose=0, mode='auto'):
        super(EarlyStopping, self).__init__()

        self.monitor = monitor
        self.patience = patience
        self.verbose = verbose
        self.min_delta = min_delta
        self.wait = 0
        self.stopped_epoch = 0

        if mode not in ['auto', 'min', 'max']:
            logging.info(f'EarlyStopping mode {mode} is unknown, fallback to auto mode.')
            mode = 'auto'

        if mode == 'min':
            self.monitor_op = np.less
        elif mode == 'max':
            self.monitor_op = np.greater
        else:
            if 'acc' in self.monitor:
                self.monitor_op = np.greater
            else:
                self.monitor_op = np.less

        if self.monitor_op == np.greater:
            self.min_delta *= 1
        else:
            self.min_delta *= -1

        self.on_train_begin()

    def on_train_begin(self, logs=None):
        # Allow instances to be re-used
        self.wait = 0
        self.stopped_epoch = 0
        self.best = np.Inf if self.monitor_op == np.less else -np.Inf

    def on_epoch_end(self, epoch, logs=None):
        current = logs.get(self.monitor)
        stop_training = False
        if current is None:
            warnings.warn(
                f'Early stopping conditioned on metric `{self.monitor}`'
                f' which is not available. Available metrics are: {",".join(list(logs.keys()))}',
                RuntimeWarning)
            stop_training = True
            return stop_training

        if self.monitor_op(current - self.min_delta, self.best):
            self.best = current
            self.wait = 0
        else:
            self.wait += 1
            if self.wait >= self.patience:
                self.stopped_epoch = epoch
                stop_training = True
                self.on_train_end()

        return stop_training

    def on_train_end(self, logs=None):
        if self.stopped_epoch > 0 and self.verbose > 0:
            logging.info(f'Epoch {self.stopped_epoch + 1:05d}: early stopping')


class ModelCheckpoint(Callback):
    """Save the model after every epoch.
    `filepath` can contain named formatting options,
    which will be filled the value of `epoch` and
    keys in `logs` (passed in `on_epoch_end`).
    For example: if `filepath` is `weights.{epoch:02d}-{val_loss:.2f}.hdf5`,
    then the model checkpoints will be saved with the epoch number and
    the validation loss in the filename.
    # Arguments
        filepath: string, path to save the model file.
        monitor: quantity to monitor.
        verbose: verbosity mode, 0 or 1.
        saving_mode: either a string (one of {best, last, all, none})
            or a tuple with a combination of the first three.
            `best` saves a checkpoint with the latest best model according to
            the quantity monitored and is will be saved with suffix `best`.
            `last` saves a checkpoint of the last epoch with suffix `last`.
            `all` saves a checkpoint after every epoch with the epoch number.
        mode: one of {auto, min, max}.
            If `saving_mode` is or includes `best`, the decision
            to overwrite the current save file is made
            based on either the maximization or the
            minimization of the monitored quantity. For `val_acc`,
            this should be `max`, for `val_loss` this should
            be `min`, etc. In `auto` mode, the direction is
            automatically inferred from the name of the monitored quantity.
        save_weights_only: if True, then only the model's weights will be
            saved (`model.save_weights(filepath)`), else the full model
            is saved (`model.save(filepath)`).
        period: Interval (number of epochs) between checkpoints.
    """

    def __init__(self, filepath, monitor='val_loss', verbose=0,
                 saving_mode='best',
                 save_weights_only=False,
                 mode='auto', period=1, prefix=''):
        super(ModelCheckpoint, self).__init__()
        if (
            save_best_only and
            os.path.isdir(filepath) and
            len(os.listdir(filepath)) > 0
        ):
            warnings.warn(
                f"Checkpoint directory {filepath} exists and is not empty with save_best_only=True."
                "All files in this directory will be deleted when a checkpoint is saved!"
            )

        self.monitor = monitor
        self.verbose = verbose
        self.filepath = filepath
        if isinstance(saving_mode, tuple):
            self.save_best = 'best' in saving_mode
            self.save_last = 'last' in saving_mode
            self.save_all = 'all' in saving_mode
        else:
            self.save_best = 'best' == saving_mode
            self.save_last = 'last' == saving_mode
            self.save_all = 'all' == saving_mode
            if saving_mode not in ('best', 'last', 'all', 'none'):
                print('ModelCheckpoint saving_mode %s is unknown, ',
                      'falling back to only saving best.' % (mode), RuntimeWarning)
                self.save_best = True

        self.save_weights_only = save_weights_only
        self.period = period
        self.epochs_since_last_save = 0
        self.prefix = prefix

        if mode not in ['auto', 'min', 'max']:
            warnings.warn(
                f'ModelCheckpoint mode {mode} is unknown, '
                'fallback to auto mode.', RuntimeWarning)
            mode = 'auto'

        if mode == 'min':
            self.monitor_op = np.less
            self.best = np.Inf
        elif mode == 'max':
            self.monitor_op = np.greater
            self.best = -np.Inf
        else:
            if 'acc' in self.monitor or self.monitor.startswith('fmeasure'):
                self.monitor_op = np.greater
                self.best = -np.Inf
            else:
                self.monitor_op = np.less
                self.best = np.Inf

    def save_model(self, filepath, new_filename, overwrite):
        # make paths
        os.makedirs(filepath, exist_ok=True)

        if overwrite:
            for filename in os.listdir(filepath):
                if filename == new_filename:
                    path_to_delete = os.path.join(filepath, filename)
                    try:
                        shutil.rmtree(path_to_delete)
                    except OSError:
                        os.remove(path_to_delete)

        # delegate the saving to the model
        self.save_function('{}/{}'.format(filepath, new_filename))

    def on_epoch_end(self, epoch, logs=None):
        logs = logs or {}
        self.epochs_since_last_save += 1
        if self.epochs_since_last_save >= self.period:
            self.epochs_since_last_save = 0
            if self.save_all:
                filename = '{}_ckpt_epoch_{}.ckpt'.format(self.prefix, epoch + 1)
                self.save_model(self.filepath, filename, overwrite=False)
                if self.verbose > 0:
                    print('\nEpoch %05d: saving model to %s' % (epoch + 1, '{}/{}'.format(
                        self.filepath, filename)))
            if self.save_last:
                filename = f'{self.prefix}_ckpt_epoch_{epoch + 1}_last.ckpt'
                self.save_model(self.filepath, filename, overwrite=True)
                if self.verbose > 0:
                    print('\nEpoch %05d: saving model to %s' % (epoch + 1, '{}/{}'.format(
                        self.filepath, filename)))
            if self.save_best:
                current = logs.get(self.monitor)
                if current is None:
                    warnings.warn(
                        f'Can save best model only with {self.monitor} available,'
                        ' skipping.', RuntimeWarning)
                else:
                    if self.monitor_op(current, self.best):
                        filename = f'{self.prefix}_ckpt_epoch_{epoch + 1}_best.ckpt'
                        if self.verbose > 0:
                            logging.info(
                                f'\nEpoch {epoch + 1:05d}: {self.monitor} improved'
                                f' from {self.best:0.5f} to {current:0.5f},'
                                f' saving model to {filepath}')
                        self.best = current
                        self.save_model(self.filepath, filename, overwrite=True)

                    else:
                        if self.verbose > 0:
                            logging.info(
                                f'\nEpoch {epoch + 1:05d}: {self.monitor} did not improve')
            else:
                if self.verbose > 0:
                    logging.info(f'\nEpoch {epoch + 1:05d}: saving model to {filepath}')
                self.save_model(filepath, overwrite=False)


class GradientAccumulationScheduler(Callback):
    """Change gradient accumulation factor according to scheduling.
    # Arguments
        scheduling: dict, scheduling in format {epoch: accumulation_factor}
    """

    def __init__(self, scheduling: dict):
        if scheduling == {}:  # empty dict error
            raise TypeError("Empty dict cannot be interpreted correct")

        for key in scheduling.keys():
            if not isinstance(key, int) or not isinstance(scheduling[key], int):
                raise TypeError("All epoches and accumulation factor must be integers")

        minimal_epoch = min(scheduling.keys())
        if minimal_epoch < 1:
            msg = f"Epochs indexing from 1, epoch {minimal_epoch} cannot be interpreted correct"
            raise IndexError(msg)
        elif minimal_epoch != 1:  # if user didnt define first epoch accumulation factor
            scheduling.update({1: 1})

        self.scheduling = scheduling
        self.epochs = sorted(scheduling.keys())

    def on_epoch_begin(self, epoch, trainer):
        epoch += 1  # indexing epochs from 1
        for i in reversed(range(len(self.epochs))):
            if epoch >= self.epochs[i]:
                trainer.accumulate_grad_batches = self.scheduling.get(self.epochs[i])
                break


if __name__ == '__main__':
    c = EarlyStopping(min_delta=0.9, patience=2, verbose=True)
    losses = [10, 9, 8, 8, 6, 4.3, 5, 4.4, 2.8, 2.5]
    for i, loss in enumerate(losses):
        should_stop = c.on_epoch_end(i, logs={'val_loss': loss})
        logging.info(loss)
        if should_stop:
            break
