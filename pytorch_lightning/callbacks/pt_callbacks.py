import os
import shutil

import numpy as np

from pytorch_lightning.pt_overrides.override_data_parallel import LightningDistributedDataParallel


class Callback(object):
    """Abstract base class used to build new callbacks.
    # Properties
        params: dict. Training parameters
            (eg. verbosity, batch size, number of epochs...).
        model: instance of `keras.models.Model`.
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
            print('EarlyStopping mode %s is unknown, fallback to auto mode.' % mode)
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
            print('Early stopping conditioned on metric `%s` '
                  'which is not available. Available metrics are: %s' %
                  (self.monitor, ','.join(list(logs.keys()))), RuntimeWarning)
            exit(-1)

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
            print('Epoch %05d: early stopping' % (self.stopped_epoch + 1))


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
        save_top_k: if `save_top_k == k`,
            the best k models according to
            the quantity monitored will be saved.
            if `save_top_k == 0`, the models are saved every `period` epochs.
        mode: one of {auto, min, max}.
            If `save_top_k > 0`, the decision
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
                 save_top_k=0, save_weights_only=False,
                 mode='auto', period=1, prefix=''):
        super(ModelCheckpoint, self).__init__()
        self.monitor = monitor
        self.verbose = verbose
        self.filepath = filepath
        self.save_top_k = save_top_k
        self.save_weights_only = save_weights_only
        self.period = period
        self.epochs_since_last_check = 0
        self.prefix = prefix
        self.best_k_models = {}
        # {epoch: monitor}
        self.best = 0

        if mode not in ['auto', 'min', 'max']:
            print('ModelCheckpoint mode %s is unknown, '
                  'fallback to auto mode.' % (mode), RuntimeWarning)
            mode = 'auto'

        if mode == 'min':
            self.monitor_op = np.less
            self.kth_value = np.Inf
            self.mode = 'min'
        elif mode == 'max':
            self.monitor_op = np.greater
            self.kth_value = -np.Inf
            self.mode = 'max'
        else:
            if 'acc' in self.monitor or self.monitor.startswith('fmeasure'):
                self.monitor_op = np.greater
                self.kth_value = -np.Inf
                self.mode = 'max'
            else:
                self.monitor_op = np.less
                self.kth_value = np.Inf
                self.mode = 'min'

    def _del_model(self, filepath):
        dirpath = os.path.dirname(filepath)

        # make paths
        os.makedirs(dirpath, exist_ok=True)

        try:
            shutil.rmtree(filepath)
        except OSError:
            os.remove(filepath)

    def _save_model(self, filepath, overwrite):
        dirpath = os.path.dirname(filepath)

        # make paths
        os.makedirs(dirpath, exist_ok=True)

        # delegate the saving to the model
        self.save_function(filepath)

    def check_monitor_top_k(self, current):
        return ((len(self.best_k_models.keys()) < self.save_top_k) or
                (self.monitor_op(current, self.best_k_models[self.kth_value])))

    def on_epoch_end(self, epoch, logs=None):
        logs = logs or {}
        self.epochs_since_last_check += 1
        if self.epochs_since_last_check >= self.period:
            self.epochs_since_last_check = 0
            filepath = '{}/{}_ckpt_epoch_{}.ckpt'.format(self.filepath, self.prefix, epoch + 1)
            if self.save_top_k:
                current = logs.get(self.monitor)
                if current is None:
                    print('Can save best model only with %s available,'
                          ' skipping.' % (self.monitor), RuntimeWarning)
                else:
                    if (self.check_monitor_top_k(current)):
                        if len(self.best_k_models.keys()) == self.save_top_k:
                            # need to pop the kth
                            delpath = '{}/{}_ckpt_epoch_{}.ckpt'.format(
                                self.filepath, self.prefix, self.kth_value + 1)
                            self.best_k_models.pop(self.kth_value)
                            self._del_model(delpath)
                        self.best_k_models[epoch] = current
                        if len(self.best_k_models.keys()) == self.save_top_k:
                            # monitor dict has reached k elements
                            if self.mode == 'min':
                                self.kth_value = max(self.best_k_models, key=self.best_k_models.get)
                            else:
                                self.kth_value = min(self.best_k_models, key=self.best_k_models.get)
                        if self.mode == 'min':
                            self.best = min(self.best_k_models.values())
                        else:
                            self.best = max(self.best_k_models.values())
                        if self.verbose > 0:
                            print('\nEpoch %05d: %s reached %s (best %s),'
                                  ' saving model to %s as top %d'
                                  % (epoch + 1, self.monitor, current, self.best,
                                     filepath, self.save_top_k))
                        self._save_model(filepath, overwrite=False)

                    else:
                        if self.verbose > 0:
                            print('\nEpoch %05d: %s was not in top %d' %
                                  (epoch + 1, self.monitor, self.save_top_k))
            else:
                if self.verbose > 0:
                    print('\nEpoch %05d: saving model to %s' % (epoch + 1, filepath))
                self._save_model(filepath, overwrite=False)


if __name__ == '__main__':
    c = EarlyStopping(min_delta=0.9, patience=2, verbose=True)
    losses = [10, 9, 8, 8, 6, 4.3, 5, 4.4, 2.8, 2.5]
    for i, loss in enumerate(losses):
        should_stop = c.on_epoch_end(i, logs={'val_loss': loss})
        print(loss)
        if should_stop:
            break

    def my_own_save_function(filepath):
        open(filepath, 'a').close()

    def init_save_dir():
        root_dir = os.path.dirname(os.path.realpath(__file__))
        save_dir = os.path.join(root_dir, 'save_dir')

        if os.path.exists(save_dir):
            shutil.rmtree(save_dir)

        os.makedirs(save_dir, exist_ok=True)

        return save_dir

    def clear_save_dir():
        root_dir = os.path.dirname(os.path.realpath(__file__))
        save_dir = os.path.join(root_dir, 'save_dir')
        if os.path.exists(save_dir):
            shutil.rmtree(save_dir)

    save_dir = init_save_dir()
    print(save_dir)

    w = ModelCheckpoint(save_dir, save_top_k=0, verbose=1)
    w.save_function = my_own_save_function
    for i, loss in enumerate(losses):
        w.on_epoch_end(i, logs={'val_loss': loss})

    file_lists = os.listdir(save_dir)

    assert len(file_lists) == 10, "Should save 10 models when save_top_k=0"

    clear_save_dir()

    w = ModelCheckpoint(save_dir, save_top_k=1, verbose=1)
    w.save_function = my_own_save_function
    for i, loss in enumerate(losses):
        w.on_epoch_end(i, logs={'val_loss': loss})

    file_lists = os.listdir(save_dir)

    assert len(file_lists) == 1, "Should save 1 model when save_top_k=1"

    clear_save_dir()

    w = ModelCheckpoint(save_dir, save_top_k=2, verbose=1)
    w.save_function = my_own_save_function
    for i, loss in enumerate(losses):
        w.on_epoch_end(i, logs={'val_loss': loss})

    file_lists = os.listdir(save_dir)

    assert len(file_lists) == 2, "Should save 2 model when save_top_k=2"

    clear_save_dir()
