#!/usr/bin/env python
# Copyright (c) Microsoft Corporation
# Licensed under the MIT license.
# Modified script from https://github.com/microsoft/DeepSpeed/blob/master/deepspeed/utils/zero_to_fp32.py
#
# This script extracts fp32 consolidated weights from a zero 2 and 3 DeepSpeed checkpoints. It gets
# copied into the top level checkpoint dir, so the user can easily do the conversion at any point in
# the future. Once extracted, the weights don't require DeepSpeed and can be used in any
# application. Additionally the script has been modified to ensure we keep the lightning state inside the state dict
# for being able to run Model.load_from_checkpoint('...').
#
# example usage within the lightning checkpoint directory where 'latest' is found:
#
# from pytorch_lightning.utilities.deepspeed import convert_zero_checkpoint_to_fp32_state_dict
#
# lightning deepspeed has saved a directory instead of a file
# save_path = "lightning_logs/version_0/checkpoints/epoch=0-step=0.ckpt/"
# output_path = "lightning_model.pt"
# convert_zero_checkpoint_to_fp32_state_dict(save_path, output_path)

import os

import torch

from pytorch_lightning.utilities import _DEEPSPEED_AVAILABLE

if _DEEPSPEED_AVAILABLE:
    from deepspeed.utils.zero_to_fp32 import (
        get_fp32_state_dict_from_zero_checkpoint,
        get_model_state_file,
        get_optim_files,
    )

device = torch.device("cpu")


def ds_checkpoint_dir(checkpoint_dir: str, tag: str = None):
    if tag is None:
        latest_path = os.path.join(checkpoint_dir, "latest")
        if os.path.isfile(latest_path):
            with open(latest_path) as fd:
                tag = fd.read().strip()
        else:
            raise ValueError(f"Unable to find 'latest' file at {latest_path}")

    directory = os.path.join(checkpoint_dir, tag)

    if not os.path.isdir(directory):
        raise FileNotFoundError(f"Directory '{ds_checkpoint_dir}' doesn't exist")
    return directory


def convert_zero_checkpoint_to_fp32_state_dict(checkpoint_dir: str, output_file: str, tag: str = None):
    """
    Convert ZeRO 2 or 3 checkpoint into a single fp32 consolidated ``state_dict`` file that can be
    loaded with ``torch.load(file)`` + ``load_state_dict()`` and used for training without DeepSpeed.
    Args:
        - ``checkpoint_dir``: path to the desired checkpoint folder.
                (one that contains the tag-folder, like ``global_step14``)
        - ``output_file``: path to the pytorch fp32 state_dict output file (e.g. path/pytorch_model.bin)
        - ``tag``: checkpoint tag used as a unique identifier for checkpoint.
                If not provided will attempt to load tag in the file named ``latest`` in the checkpoint folder,
                    e.g., ``global_step14``
    """

    state_dict = get_fp32_state_dict_from_zero_checkpoint(checkpoint_dir, tag)

    # additional logic to ensure we keep the lightning state dict as well from rank 0.
    deepspeed_states = [
        "module",
        "optimizer",
        "lr_scheduler",
        "csr_tensor_module_names",
        "skipped_steps",
        "global_steps",
        "dp_world_size",
        "mp_world_size",
    ]
    checkpoint_dir = ds_checkpoint_dir(checkpoint_dir)
    optim_files = get_optim_files(checkpoint_dir)
    optim_state = torch.load(optim_files[0], map_location=device)
    zero_stage = optim_state["optimizer_state_dict"]["zero_stage"]
    model_file = get_model_state_file(checkpoint_dir, zero_stage)
    client_state = torch.load(model_file, map_location=device)
    client_state = {key: value for key, value in client_state.items() if key not in deepspeed_states}
    # State dict keys will include reference to wrapper LightningDeepSpeedModule
    # Delete `module` prefix before saving.
    state_dict = {k.partition("module.")[2]: state_dict[k] for k in state_dict.keys()}
    client_state["state_dict"] = state_dict

    print(f"Saving fp32 state dict to {output_file}")
    torch.save(client_state, output_file)
