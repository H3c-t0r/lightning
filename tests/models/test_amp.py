import os

import pytest
import torch

import tests.base.develop_pipelines as tpipes
import tests.base.develop_utils as tutils
from pytorch_lightning import Trainer
from pytorch_lightning.utilities.exceptions import MisconfigurationException
from tests.base import EvalModelTemplate


@pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires GPU machine")
def test_amp_single_gpu_dp(tmpdir):
    """Make sure DP/DDP + AMP work."""
    tutils.reset_seed()

    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        gpus=1,
        distributed_backend='dp',
        precision=16,
    )

    model = EvalModelTemplate()
    # tutils.run_model_test(trainer_options, model)
    result = trainer.fit(model)

    assert result == 1


@pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires GPU machine")
def test_amp_single_gpu_ddp_spawn(tmpdir):
    """Make sure DP/DDP + AMP work."""
    tutils.reset_seed()
    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        gpus=1,
        distributed_backend='ddp_spawn',
        precision=16,
    )

    model = EvalModelTemplate()
    # tutils.run_model_test(trainer_options, model)
    result = trainer.fit(model)

    assert result == 1


@pytest.mark.spawn
@pytest.mark.parametrize("backend", ['dp', 'ddp'])
@pytest.mark.skipif(torch.cuda.device_count() < 2, reason="test requires multi-GPU machine")
def test_amp_multi_gpu(tmpdir, backend):
    """Make sure DP/DDP + AMP work."""
    tutils.set_random_master_port()

    model = EvalModelTemplate()

    trainer_options = dict(
        default_root_dir=tmpdir,
        max_epochs=1,
        # gpus=2,
        gpus='0, 1',  # test init with gpu string
        distributed_backend=backend,
        precision=16,
    )

    # tutils.run_model_test(trainer_options, model)
    trainer = Trainer(**trainer_options)
    result = trainer.fit(model)
    assert result


@pytest.mark.spawn
@pytest.mark.parametrize("backend", ['dp', 'ddp'])
@pytest.mark.skipif(torch.cuda.device_count() < 2, reason="test requires multi-GPU machine")
def test_multi_gpu_wandb(tmpdir, backend):
    """Make sure DP/DDP + AMP work."""
    from pytorch_lightning.loggers import WandbLogger
    tutils.set_random_master_port()

    model = EvalModelTemplate()
    logger = WandbLogger(name='utest')

    trainer_options = dict(
        default_root_dir=tmpdir,
        max_epochs=1,
        gpus=2,
        distributed_backend=backend,
        precision=16,
        logger=logger,

    )
    # tutils.run_model_test(trainer_options, model)
    trainer = Trainer(**trainer_options)
    result = trainer.fit(model)
    assert result
    trainer.test(model)


@pytest.mark.spawn
@pytest.mark.skipif(torch.cuda.device_count() < 2, reason="test requires multi-GPU machine")
def test_amp_gpu_ddp_slurm_managed(tmpdir):
    """Make sure DDP + AMP work."""
    # simulate setting slurm flags
    tutils.set_random_master_port()
    os.environ['SLURM_LOCALID'] = str(0)

    model = EvalModelTemplate()

    # exp file to get meta
    logger = tutils.get_default_logger(tmpdir)

    # exp file to get weights
    checkpoint = tutils.init_checkpoint_callback(logger)

    # fit model
    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        gpus=[0],
        distributed_backend='ddp_spawn',
        precision=16,
        checkpoint_callback=checkpoint,
        logger=logger,
    )
    trainer.is_slurm_managing_tasks = True
    result = trainer.fit(model)

    # correct result and ok accuracy
    assert result == 1, 'amp + ddp model failed to complete'

    # test root model address
    assert trainer.resolve_root_node_address('abc') == 'abc'
    assert trainer.resolve_root_node_address('abc[23]') == 'abc23'
    assert trainer.resolve_root_node_address('abc[23-24]') == 'abc23'
    assert trainer.resolve_root_node_address('abc[23-24, 45-40, 40]') == 'abc23'


def test_cpu_model_with_amp(tmpdir):
    """Make sure model trains on CPU."""
    trainer_options = dict(
        default_root_dir=tmpdir,
        progress_bar_refresh_rate=0,
        max_epochs=1,
        limit_train_batches=0.4,
        limit_val_batches=0.4,
        precision=16
    )

    model = EvalModelTemplate()

    with pytest.raises((MisconfigurationException, ModuleNotFoundError)):
        tpipes.run_model_test(trainer_options, model, on_gpu=False)
