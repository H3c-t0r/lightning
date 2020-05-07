"""Helper functions to help with reproducibility of models """
import os

import numpy as np
import random
import torch

from pytorch_lightning import _logger as log


def seed_everything(seed: int = None):
    """Function that sets seed for pseudo-random number generators  in:
        pytorch, numpy, python.random and sets PYTHONHASHSEED environment variable.
    """
    max_seed_value = np.iinfo(np.uint32).max
    min_seed_value = np.iinfo(np.uint32).min

    try:
        seed = int(seed)
    except (TypeError, ValueError):
        seed = _select_seed_randomly(min_seed_value, max_seed_value)

    if (seed > max_seed_value) or (seed < min_seed_value):
        log.warning(
            f"{seed} is not in bounds, \
            numpy accepts from {min_seed_value} to {max_seed_value}"
        )
        seed = _select_seed_randomly(min_seed_value, max_seed_value)

    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    # fixing non-deterministic part of horovod
    # https://github.com/PyTorchLightning/pytorch-lightning/pull/1572/files#r420279383
    os.environ["HOROVOD_FUSION_THRESHOLD"] = str(0)


def _select_seed_randomly(min_seed_value, max_seed_value) -> int:
    seed = random.randint(min_seed_value, max_seed_value)
    log.warning(f"No correct seed found, seed set to {seed}")
    return seed
