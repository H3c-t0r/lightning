"""
DCGAN - Adapted from pytorch/examples

Launch it with this command:

torchelastic --nproc_per_node=2
python -m torch.distributed.launch --nproc_per_node=2 gan_example.py --accelerator ddp --gpus 2 --precision 16

"""
from __future__ import print_function
import argparse
import os
import random
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.optim as optim
import torch.utils.data
import torchvision.datasets as dset
import torchvision.transforms as transforms
import torchvision.utils as vutils
from torch.nn import DataParallel
from torch.nn.parallel import DistributedDataParallel
from torch.utils.data import DistributedSampler

from pl_examples.automator_examples.models import weights_init, Generator, Discriminator
from pytorch_lightning.lite.automator import LightningLite, AutomatedModel

parser = argparse.ArgumentParser()
parser.add_argument("--workers", type=int, help="number of data loading workers", default=0)
parser.add_argument("--batchSize", type=int, default=64, help="input batch size")
parser.add_argument(
    "--imageSize",
    type=int,
    default=64,
    help="the height / width of the input image to network",
)
parser.add_argument("--niter", type=int, default=25, help="number of epochs to train for")
parser.add_argument("--lr", type=float, default=0.0002, help="learning rate, default=0.0002")
parser.add_argument("--beta1", type=float, default=0.5, help="beta1 for adam. default=0.5")

parser.add_argument("--netG", default="", help="path to netG (to continue training)")
parser.add_argument("--netD", default="", help="path to netD (to continue training)")
parser.add_argument("--outf", default="./lightning_logs", help="folder to output images and model checkpoints")


# ------------------------------------------------------------------------------------------------------------
# Available LightningLite Flags
# ------------------------------------------------------------------------------------------------------------
parser.add_argument("--accelerator", type=str, default=None, choices=["ddp", "ddp_cpu", "dp"])
parser.add_argument("--gpus", type=int, default=0)
parser.add_argument("--num_processes", type=int, default=1)
parser.add_argument("--precision", type=int, default=32, choices=[16, 32])
parser.add_argument("--amp_backend", type=str, default="native", choices=["native"])

# required by torch.distributed.launch
# TODO: we need a lightning launcher
parser.add_argument("--local_rank", type=int, default=0)

opt = parser.parse_args()
os.makedirs(opt.outf, exist_ok=True)

nz = 100


def main():
    random.seed(123)
    torch.manual_seed(123)

    # TODO: how do we handle this in Accelerator?
    # torch.cuda.set_device(opt.local_rank)
    # TODO: how do we handle this?
    os.environ["LOCAL_RANK"] = str(opt.local_rank)
    # os.environ["NODE_RANK"] = str(opt.local_rank)
    os.environ["PL_IN_DDP_SUBPROCESS"] = "1"

    automator = LightningLite(
        accelerator=opt.accelerator,
        gpus=opt.gpus,
        num_processes=opt.num_processes,
        precision=opt.precision,
        amp_backend=opt.amp_backend,
    )
    # automatorD = LiteModel(**kargs)
    # automatorG = LiteModel(**kargs)
    #
    # automatorD.setup_optimizer(opt, model1)
    dataset = dset.MNIST(
        root=".",
        download=True,
        transform=transforms.Compose(
            [
                transforms.Resize(opt.imageSize),
                transforms.ToTensor(),
                transforms.Normalize((0.5,), (0.5,)),
            ]
        ),
    )
    dataloader = torch.utils.data.DataLoader(dataset, batch_size=opt.batchSize, shuffle=True, num_workers=opt.workers)

    dataloader = automator.setup_dataloader(dataloader)

    if opt.accelerator == "ddp":
        assert isinstance(dataloader.sampler, DistributedSampler)

    netG = Generator()
    netG.apply(weights_init)

    netD = Discriminator()
    netD.apply(weights_init)

    automator.to_device(netG)
    automator.to_device(netD)

    optimizerD = optim.Adam(netD.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
    optimizerG = optim.Adam(netG.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))

    (netG, netD), (optimizerG, optimizerD) = automator.setup(models=(netG, netD), optimizers=(optimizerG, optimizerD))

    if opt.accelerator == "ddp":
        assert isinstance(netG, AutomatedModel)
        assert isinstance(netD, AutomatedModel)
        assert isinstance(netG.module, DistributedDataParallel)
        assert isinstance(netD.module, DistributedDataParallel)
    if opt.accelerator == "dp":
        assert isinstance(netD.module, DataParallel)
        assert isinstance(netG.module, DataParallel)

    criterion = nn.BCELoss()

    fixed_noise = torch.randn(opt.batchSize, nz, 1, 1, device=automator.device)
    real_label = 1
    fake_label = 0

    for epoch in range(opt.niter):
        for i, data in enumerate(dataloader, 0):
            ############################
            # (1) Update D network: maximize log(D(x)) + log(1 - D(G(z)))
            ###########################
            # train with real
            netD.zero_grad()
            real_cpu = automator.to_device(data[0])
            batch_size = real_cpu.size(0)
            label = torch.full((batch_size,), real_label, dtype=real_cpu.dtype, device=automator.device)
            output = netD(real_cpu)
            output = output.float()  # required if precision = 16

            errD_real = criterion(output, label)

            automator.backward(errD_real)

            D_x = output.mean().item()

            # train with fake
            noise = torch.randn(batch_size, nz, 1, 1, device=automator.device)
            fake = netG(noise)

            label.fill_(fake_label)
            output = netD(fake.detach())

            output = output.float()  # required if precision = 16

            errD_fake = criterion(output, label)
            automator.backward(errD_fake)
            D_G_z1 = output.mean().item()
            errD = errD_real + errD_fake

            optimizerD.step()  # model inside?

            ############################
            # (2) Update G network: maximize log(D(G(z)))
            ###########################
            netG.zero_grad()
            label.fill_(real_label)  # fake labels are real for generator cost
            output = netD(fake)

            output = output.float()  # required if precision = 16

            errG = criterion(output, label)

            # document
            automator.backward(errG)

            D_G_z2 = output.mean().item()
            optimizerG.step()

            print(
                "[%d/%d][%d/%d] Loss_D: %.4f Loss_G: %.4f D(x): %.4f D(G(z)): %.4f / %.4f"
                % (
                    epoch,
                    opt.niter,
                    i,
                    len(dataloader),
                    errD.item(),
                    errG.item(),
                    D_x,
                    D_G_z1,
                    D_G_z2,
                )
            )
            if i % 100 == 0:
                vutils.save_image(real_cpu, "%s/real_samples.png" % opt.outf, normalize=True)
                fake = netG(fixed_noise)
                vutils.save_image(
                    fake.detach(),
                    "%s/fake_samples_epoch_%03d.png" % (opt.outf, epoch),
                    normalize=True,
                )
        # do checkpointing
        torch.save(netG.state_dict(), "%s/netG_epoch_%d.pth" % (opt.outf, epoch))
        torch.save(netD.state_dict(), "%s/netD_epoch_%d.pth" % (opt.outf, epoch))


if __name__ == "__main__":
    main()
