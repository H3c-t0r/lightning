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
import gc
import time

import numpy as np
import pytest
import torch
from tqdm import tqdm

from pytorch_lightning import seed_everything, Trainer
from tests.base.models import ParityModuleMNIST, ParityModuleRNN


def assert_speed_parity_absolute(pl_times, pt_times, nb_epochs, max_diff: float = 0.55):
    # assert speeds
    diffs = np.asarray(pl_times) - np.asarray(pt_times)
    # norm by vanilla time
    diffs = diffs / nb_epochs
    assert np.alltrue(diffs < max_diff), \
        f"lightning {diffs} was slower than PT (threshold {max_diff})"


def assert_memory_parity_relative(pl_memory, pt_memory, max_diff: float = 0.1):
    # assert speeds
    diffs = np.asarray(pl_memory) - np.asarray(pt_memory)
    # norm by vanilla time
    diffs = diffs / np.mean(pl_memory)
    assert np.alltrue(diffs < max_diff), \
        f"lightning {diffs} takes more memory than PT (threshold {max_diff})"


# ParityModuleMNIST runs with num_workers=1
@pytest.mark.parametrize('cls_model,max_diff_speed,max_diff_memory', [
    (ParityModuleRNN, 0.05, 0.0),
    (ParityModuleMNIST, 0.25, 0.0),  # todo: lower this thr
])
@pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires GPU machine")
def test_pytorch_parity(tmpdir, cls_model, max_diff_speed: float, max_diff_memory: float, num_epochs: int = 4, num_runs: int = 3):
    """
    Verify that the same  pytorch and lightning models achieve the same results
    """
    lightning = measure_loops(cls_model, kind="PT Lightning", num_epochs=num_epochs, num_runs=num_runs)
    vanilla = measure_loops(cls_model, kind="Vanilla PT", num_epochs=num_epochs, num_runs=num_runs)

    # make sure the losses match exactly  to 5 decimal places
    print(f"Losses are for... \n vanilla: {vanilla['losses']} \n lightning: {lightning['losses']}")
    for pl_out, pt_out in zip(lightning['losses'], vanilla['losses']):
        np.testing.assert_almost_equal(pl_out, pt_out, 5)


    # the first run initialize dataset (download & filter)
    assert_speed_parity_absolute(
        lightning['durations'][1:], vanilla['durations'][1:], nb_epochs=num_epochs, max_diff=max_diff_speed
    )

    assert_memory_parity_relative(lightning['memory'], vanilla['memory'], max_diff=max_diff_memory)


def measure_loops(cls_model, kind, num_runs=10, num_epochs=10):
    """
    Returns an array with the last loss from each epoch for each run
    """
    hist_losses = []
    hist_durations = []
    hist_memory = []

    device = torch.device('cuda' if torch.cuda.is_available() else "cpu")
    torch.backends.cudnn.deterministic = True
    for i in tqdm(range(num_runs), desc=f'{kind} with {cls_model.__name__}'):
        gc.collect()
        if device == 'cuda':
            torch.cuda.empty_cache()
        time.sleep(1)

        time_start = time.perf_counter()

        if kind == "Vanilla PT":
            final_loss = vanilla_loop(cls_model, idx=i, device=device, num_epochs=num_epochs)
        elif kind == "PT Lightning":
            final_loss = lightning_loop(cls_model, idx=i, device=device, num_epochs=num_epochs)
        else:
            raise ValueError(f"Unsupported loop '{kind}'.")

        time_end = time.perf_counter()
        if device == 'cuda':
            torch.cuda.synchronize()
            max_memory = torch.cuda.max_memory_allocated() / 2 ** 20
        else:
            max_memory = 0

        hist_losses.append(final_loss)
        hist_durations.append(time_end - time_start)
        hist_memory.append(max_memory)

    return {
        'losses': hist_losses,
        'durations': hist_durations,
        'memory': hist_memory,
    }


def vanilla_loop(cls_model, idx, device = 'cuda', num_epochs=10):
    # set seed
    seed_everything(idx)

    # init model parts
    model = cls_model()
    dl = model.train_dataloader()
    optimizer = model.configure_optimizers()

    # model to GPU
    model = model.to(device)

    epoch_losses = []
    # as the first run is skipped, no need to run it long
    for epoch in range(num_epochs if idx > 0 else 1):

        # run through full training set
        for j, batch in enumerate(dl):
            batch = [x.to(device) for x in batch]
            loss_dict = model.training_step(batch, j)
            loss = loss_dict['loss']
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()

        # track last epoch loss
        epoch_losses.append(loss.item())

    return epoch_losses[-1]


def lightning_loop(cls_model, idx, device='cuda', num_epochs=10):
    seed_everything(idx)

    model = cls_model()
    # init model parts
    trainer = Trainer(
        # as the first run is skipped, no need to run it long
        max_epochs=num_epochs if idx > 0 else 1,
        progress_bar_refresh_rate=0,
        weights_summary=None,
        gpus=1 if device == 'cuda' else 0,
        checkpoint_callback=False,
        deterministic=True,
        logger=False,
        replace_sampler_ddp=False,
    )
    trainer.fit(model)

    return trainer.train_loop.running_loss.last().item()
