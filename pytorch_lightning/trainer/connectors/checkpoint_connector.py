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

import torch

import pytorch_lightning
from pytorch_lightning.core.lightning import LightningModule
from pytorch_lightning.utilities import (
    _APEX_AVAILABLE,
    _OMEGACONF_AVAILABLE,
    AMPType,
    DeviceType,
    rank_zero_info,
    rank_zero_warn,
)
from pytorch_lightning.utilities.cloud_io import get_filesystem
from pytorch_lightning.utilities.exceptions import MisconfigurationException
from pytorch_lightning.utilities.upgrade_checkpoint import KEYS_MAPPING as DEPRECATED_CHECKPOINT_KEYS

if _APEX_AVAILABLE:
    from apex import amp

if _OMEGACONF_AVAILABLE:
    from omegaconf import Container


class CheckpointConnector:

    def __init__(self, trainer):
        self.trainer = trainer

        # used to validate checkpointing logic
        self.has_trained = False

    def restore_weights(self) -> None:
        """
        Attempt to restore a checkpoint (e.g. weights) in this priority:
        1. from `resume_from_checkpoint` file
        2. don't restore
        """
        # clear cache before restore
        if self.trainer._device_type == DeviceType.GPU:
            torch.cuda.empty_cache()

        # 1. Attempt to restore states from `resume_from_checkpoint` file
        if self.trainer.resume_from_checkpoint is not None:
            self.restore(self.trainer.resume_from_checkpoint, on_gpu=self.trainer._device_type == DeviceType.GPU)

        # wait for all to catch up
        self.trainer.training_type_plugin.barrier('TrainerIOMixin.restore_weights')

        # clear cache after restore
        if self.trainer._device_type == DeviceType.GPU:
            torch.cuda.empty_cache()

    def restore(self, checkpoint_path: str, on_gpu: bool) -> bool:
        """
        Load model/training states from a 'PyTorch-Lightning checkpoint' file through file-read and state-restore.
        All restored states are listed in return value description of `dump_checkpoint`.
        """
        # Try to read the checkpoint file at `checkpoint_path`. If not exist, do not restore checkpoint.
        fs = get_filesystem(checkpoint_path)
        if not fs.exists(checkpoint_path):
            raise FileNotFoundError(f"Checkpoint at {checkpoint_path} not found. Aborting training.")

        checkpoint, load_optimizer_states = self.trainer.training_type_plugin.restore_model_state_from_ckpt_path(
            checkpoint_path, map_location=lambda storage, loc: storage
        )

        model = self.trainer.lightning_module

        if on_gpu:
            model.cuda(self.trainer.root_gpu)

        # restore training state
        self.restore_training_state(checkpoint, load_optimizer_states)

        rank_zero_info(f"Restored states from the checkpoint file at {checkpoint_path}")
        return True

    def restore_model_state(self, model: LightningModule, checkpoint) -> None:
        """
        Restore model states from a 'PyTorch-Lightning checkpoint' dictionary object
        """

        # restore datamodule states
        if self.trainer.datamodule is not None:
            self.trainer.datamodule.on_load_checkpoint(checkpoint)

        # hook: give user access to checkpoint if needed.
        model.on_load_checkpoint(checkpoint)

        # restore model state_dict
        model.load_state_dict(checkpoint['state_dict'])

    def restore_training_state(self, checkpoint, load_optimizer_states: bool = True):
        """
        Restore trainer state.
        Model will get its change to update
        :param checkpoint:
        :return:
        """
        # validation
        if load_optimizer_states and ('optimizer_states' not in checkpoint or 'lr_schedulers' not in checkpoint):
            raise KeyError(
                'Trying to restore training state but checkpoint contains only the model.'
                ' This is probably due to `ModelCheckpoint.save_weights_only` being set to `True`.'
            )

        if any([key in checkpoint for key in DEPRECATED_CHECKPOINT_KEYS]):
            raise ValueError(
                "The checkpoint you're attempting to load follows an"
                " outdated schema. You can upgrade to the current schema by running"
                " `python -m pytorch_lightning.utilities.upgrade_checkpoint --file model.ckpt`"
                " where `model.ckpt` is your checkpoint file."
            )

        # restore amp scaling
        if self.trainer.amp_backend == AMPType.NATIVE and 'native_amp_scaling_state' in checkpoint:
            self.trainer.scaler.load_state_dict(checkpoint['native_amp_scaling_state'])
        elif self.trainer.amp_backend == AMPType.APEX and 'amp_scaling_state' in checkpoint:
            amp.load_state_dict(checkpoint['amp_scaling_state'])

        # restore callback states
        self.trainer.on_load_checkpoint(checkpoint)

        self.trainer.train_loop.global_step = checkpoint['global_step']
        self.trainer.train_loop.current_epoch = checkpoint['epoch']

        # crash if max_epochs is lower then the current epoch from the checkpoint
        if self.trainer.max_epochs is not None and self.trainer.current_epoch > self.trainer.max_epochs:
            m = f"""
            you restored a checkpoint with current_epoch={self.trainer.current_epoch}
            but the Trainer(max_epochs={self.trainer.max_epochs})
            """
            raise MisconfigurationException(m)

        # Division deals with global step stepping once per accumulated batch
        # Inequality deals with different global step for odd vs even num_training_batches
        n_accum = 1 if self.trainer.accumulate_grad_batches is None else self.trainer.accumulate_grad_batches
        expected_steps = self.trainer.num_training_batches / n_accum
        if self.trainer.num_training_batches != 0 and self.trainer.global_step % expected_steps > 1:
            rank_zero_warn(
                "You're resuming from a checkpoint that ended mid-epoch."
                " Training will start from the beginning of the next epoch."
                " This can cause unreliable results if further training is done,"
                " consider using an end of epoch checkpoint."
            )

        if not load_optimizer_states:
            return

        # restore the optimizers
        optimizer_states = checkpoint['optimizer_states']
        for optimizer, opt_state in zip(self.trainer.optimizers, optimizer_states):
            optimizer.load_state_dict(opt_state)

            # move optimizer to GPU 1 weight at a time
            # avoids OOM
            if self.trainer.root_gpu is not None:
                for state in optimizer.state.values():
                    for k, v in state.items():
                        if isinstance(v, torch.Tensor):
                            state[k] = v.cuda(self.trainer.root_gpu)

        # restore the lr schedulers
        lr_schedulers = checkpoint['lr_schedulers']
        for scheduler, lrs_state in zip(self.trainer.lr_schedulers, lr_schedulers):
            scheduler['scheduler'].load_state_dict(lrs_state)

    def dump_checkpoint(self, weights_only: bool = False) -> dict:
        """Creating a model checkpoint dictionary object from various component states.
        Args:
            weights_only: saving model weights only
        Return:
            structured dictionary: {
                'epoch':                     training epoch
                'global_step':               training global step
                'pytorch-lightning_version': PyTorch Lightning's version
                'callbacks':                 "callback specific state"[] # if not weights_only
                'optimizer_states':          "PT optim's state_dict"[]   # if not weights_only
                'lr_schedulers':             "PT sched's state_dict"[]   # if not weights_only
                'native_amp_scaling_state':  PT amp's state_dict         # if not weights_only and use native amp
                'amp_scaling_state':         Apex's state_dict           # if not weights_only and use apex amp
                'state_dict':                Model's state_dict (e.g. network weights)
                CHECKPOINT_HYPER_PARAMS_NAME:
                CHECKPOINT_HYPER_PARAMS_KEY:
                CHECKPOINT_HYPER_PARAMS_TYPE:
                something_cool_i_want_to_save: anything you define through model.on_save_checkpoint
                LightningDataModule.__class__.__name__: pl DataModule's state
            }
        """

        # dump epoch/global_step/pytorch-lightning_version
        current_epoch = self.trainer.current_epoch
        global_step = self.trainer.global_step
        has_reached_max_steps = self.trainer.max_steps and self.trainer.max_steps <= global_step

        global_step += 1
        if not has_reached_max_steps:
            current_epoch += 1

        model = self.trainer.lightning_module

        checkpoint = {
            'epoch': current_epoch,
            'global_step': global_step,
            'pytorch-lightning_version': pytorch_lightning.__version__,
            'state_dict': self.trainer.accelerator.lightning_module_state_dict(),
        }

        if not weights_only:
            # dump callbacks
            checkpoint['callbacks'] = self.trainer.on_save_checkpoint(checkpoint)

            optimizer_states = []
            for i, optimizer in enumerate(self.trainer.optimizers):
                # Rely on accelerator to dump optimizer state
                optimizer_state = self.trainer.accelerator.optimizer_state(optimizer)
                optimizer_states.append(optimizer_state)

            checkpoint['optimizer_states'] = optimizer_states

            # dump lr schedulers
            lr_schedulers = []
            for scheduler in self.trainer.lr_schedulers:
                lr_schedulers.append(scheduler['scheduler'].state_dict())
            checkpoint['lr_schedulers'] = lr_schedulers

            # dump amp scaling
            if (
                self.trainer.amp_backend == AMPType.NATIVE and self.trainer._device_type != DeviceType.TPU
                and self.trainer.scaler is not None
            ):
                checkpoint['native_amp_scaling_state'] = self.trainer.scaler.state_dict()
            elif self.trainer.amp_backend == AMPType.APEX:
                checkpoint['amp_scaling_state'] = amp.state_dict()

        # dump hyper-parameters
        if model.hparams:
            if hasattr(model, '_hparams_name'):
                checkpoint[LightningModule.CHECKPOINT_HYPER_PARAMS_NAME] = model._hparams_name
            # dump arguments
            if _OMEGACONF_AVAILABLE and isinstance(model.hparams, Container):
                checkpoint[LightningModule.CHECKPOINT_HYPER_PARAMS_KEY] = model.hparams
                checkpoint[LightningModule.CHECKPOINT_HYPER_PARAMS_TYPE] = type(model.hparams)
            else:
                checkpoint[LightningModule.CHECKPOINT_HYPER_PARAMS_KEY] = dict(model.hparams)

        # give the model a chance to dump a few things
        model.on_save_checkpoint(checkpoint)
        if self.trainer.datamodule is not None:
            self.trainer.datamodule.on_save_checkpoint(checkpoint)

        return checkpoint

    def save_checkpoint(self, filepath, weights_only: bool = False) -> None:
        """Save model/training states as a checkpoint file through state-dump and file-write.

        Args:
            filepath: write-target file's path
            weights_only: saving model weights only
        """
        _checkpoint = self.dump_checkpoint(weights_only)
        self.trainer.accelerator.save_checkpoint(_checkpoint, filepath)
