import torch
from pytorch_lightning import Trainer
from tests.base.datamodules import TrialMNISTDataModule
from tests.base import EvalModelTemplate


def test_train_loop_only(tmpdir):
    dm = TrialMNISTDataModule(tmpdir)
    dm.prepare_data()
    dm.setup()

    model = EvalModelTemplate()
    model.validation_step = None
    model.validation_step_end = None
    model.validation_epoch_end = None
    model.test_step = None
    model.test_step_end = None
    model.test_epoch_end = None

    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=3,
        weights_summary=None,
    )
    trainer.fit(model, dm)

    # fit model
    result = trainer.fit(model)
    assert result == 1
    assert trainer.callback_metrics['loss'] < 0.50


def test_train_val_loop_only(tmpdir):
    dm = TrialMNISTDataModule(tmpdir)
    dm.prepare_data()
    dm.setup()

    model = EvalModelTemplate()
    model.validation_step = None
    model.validation_step_end = None
    model.validation_epoch_end = None

    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=3,
        weights_summary=None,
    )
    trainer.fit(model, dm)

    # fit model
    result = trainer.fit(model)
    assert result == 1
    assert trainer.callback_metrics['loss'] < 0.50


def test_full_loop(tmpdir):
    dm = TrialMNISTDataModule(tmpdir)
    dm.prepare_data()
    dm.setup()

    model = EvalModelTemplate()

    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=3,
        weights_summary=None,
    )
    trainer.fit(model, dm)

    # fit model
    result = trainer.fit(model)
    assert result == 1

    # test
    result = trainer.test(datamodule=dm)
    result = result[0]
    assert result['test_acc'] > 0.8


@pytest.mark.skipif(torch.cuda.device_count() < 1, reason="test requires multi-GPU machine")
def test_full_loop_single_gpu(tmpdir):
    dm = TrialMNISTDataModule(tmpdir)
    dm.prepare_data()
    dm.setup()

    model = EvalModelTemplate()

    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=3,
        weights_summary=None,
        gpus=1
    )
    trainer.fit(model, dm)

    # fit model
    result = trainer.fit(model)
    assert result == 1

    # test
    result = trainer.test(datamodule=dm)
    result = result[0]
    assert result['test_acc'] > 0.8


@pytest.mark.skipif(torch.cuda.device_count() < 2, reason="test requires multi-GPU machine")
def test_full_loop_dp(tmpdir):
    dm = TrialMNISTDataModule(tmpdir)
    dm.prepare_data()
    dm.setup()

    model = EvalModelTemplate()

    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=3,
        weights_summary=None,
        distributed_backend='dp',
        gpus=2
    )
    trainer.fit(model, dm)

    # fit model
    result = trainer.fit(model)
    assert result == 1

    # test
    result = trainer.test(datamodule=dm)
    result = result[0]
    assert result['test_acc'] > 0.8


@pytest.mark.skipif(torch.cuda.device_count() < 2, reason="test requires multi-GPU machine")
def test_full_loop_ddp_spawn(tmpdir):
    dm = TrialMNISTDataModule(tmpdir)
    dm.prepare_data()
    dm.setup()

    model = EvalModelTemplate()

    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=3,
        weights_summary=None,
        distributed_backend='ddp_spawn',
        gpus=2
    )
    trainer.fit(model, dm)

    # fit model
    result = trainer.fit(model)
    assert result == 1

    # test
    result = trainer.test(datamodule=dm)
    result = result[0]
    assert result['test_acc'] > 0.8