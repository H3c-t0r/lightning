from lightning.fabric.utilities.spike import SpikeDetection, _TORCHMETRICS_GREATER_EQUAL_1_0_0, TrainingSpikeException
import pytest
from functools import partial
from lightning import LightningModule, Trainer
import torch

def spike_detection_test(fabric, global_rank_spike):
    loss_vals = [1 / i for i in range(1, 10)]
    if fabric.global_rank == global_rank_spike:
        loss_vals[4] = 3

    for i in range(len(loss_vals)):
        if i == 4:
            context = pytest.raises(TrainingSpikeException)
        else:
            context = contextlib.nullcontext()

        with context:
            fabric.call(
                "on_train_batch_end",
                fabric,
                torch.tensor(loss_vals[i], device=fabric.device),
                None,
                i,
            )

@pytest.mark.parametrize(
    "global_rank_spike, num_devices",
    [pytest.param(0, 1), pytest.param(0, 2), pytest.param(0, 1)],
)
@pytest.mark.skipif(not _TORCHMETRICS_GREATER_EQUAL_1_0_0, reason="requires torchmetrics>=1.0.0")
def test_fabric_spike_detection_integration(tmpdir, global_rank_spike, num_devices):
    fabric = Fabric(
        accelerator="cpu",
        devices=num_devices,
        callbacks=[FabricSpikeDetection(exclude_batches_path=tmpdir)],
        strategy="ddp_spawn",
    )
    fabric.launch(partial(spike_detection_test, global_rank_spike=global_rank_spike))
