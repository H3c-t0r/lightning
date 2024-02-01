"""Root package info."""
import logging
import os
import sys

# explicitly don't set root logger's propagation and leave this to subpackages to manage
_logger = logging.getLogger(__name__)
_logger.setLevel(logging.INFO)

_console = logging.StreamHandler()
_console.setLevel(logging.INFO)

formatter = logging.Formatter("%(levelname)s: %(message)s")
_console.setFormatter(formatter)
_logger.addHandler(_console)

from lightning.__about__ import *  # noqa: E402, F403
from lightning.__version__ import version as __version__  # noqa: E402

# This enables us to control imports in a more granular way for performance reasons
lazy_import = bool(int(os.getenv("LIGHTNING_LAZY_IMPORTS", "0")))

if not lazy_import:
    from lightning.fabric.fabric import Fabric
    from lightning.fabric.utilities.seed import seed_everything
    from lightning.pytorch.callbacks import Callback
    from lightning.pytorch.core import LightningDataModule, LightningModule
    from lightning.pytorch.trainer import Trainer

    __all__ = [
        "Trainer",
        "LightningDataModule",
        "LightningModule",
        "Callback",
        "seed_everything",
        "Fabric",
        "__version__",
    ]




def _cli_entry_point() -> None:
    from lightning_utilities.core.imports import ModuleAvailableCache, RequirementCache

    if not (
        ModuleAvailableCache("lightning.app")
        if RequirementCache("lightning-utilities<0.10.0")
        else RequirementCache(module="lightning.app")  # type: ignore[call-arg]
    ):
        print("The `lightning` command requires additional dependencies: `pip install lightning[app]`")
        sys.exit(1)

    from lightning.app.cli.lightning_cli import main

    main()
