# SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import argparse
import glob
import multiprocessing as mp
import os
import socket
import time

import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data.distributed import DistributedSampler

import torchvision
import torchvision.models as models
import torchvision.transforms as transforms


def _check_pytorch_version():
    version_info = tuple(map(int, torch.__version__.split(".")[:2]))
    if version_info[0] not in (1, 2):
        # Not supported version.
        return False
    return True


def main():
    # NOTE: For safer execution, a process start method is changed.
    # https://pytorch.org/docs/stable/notes/multiprocessing.html#cuda-in-multiprocessing
    mp.set_start_method("forkserver")

    is_expected_version = _check_pytorch_version()
    if not is_expected_version:
        raise RuntimeError(
            (
                "Your PyTorch version is not expected in this example code."
                "1.x or 2.x are required."
            )
        )

    args = parse_args()

    if not args.use_older_api:
        # Setup distributed process group.
        # NOTE:
        # In PyTorch 1.8 or earlier, the user needs to pass
        # several information like rank.
        # But, after 1.9+, the user no longer gives these values in
        # the typical case.
        # Please see more details at "Important Notices:" in the page below.
        # https://pytorch.org/docs/stable/elastic/run.html
        dist.init_process_group(backend="mpi")

        # NOTE:
        # Before PyTorch 1.8, `--local_rank` must be added into
        # script argeuments.
        # But, after 1.9, this argument is not necessary.
        # For more details, please read
        # "Transitioning from torch.distributed.launch to
        # torch.distributed.run" below.
        # https://pytorch.org/docs/stable/elastic/run.html#transitioning-from-torch-distributed-launch-to-torch-distributed-run
        local_rank = int(os.environ["OMPI_COMM_WORLD_LOCAL_RANK"])
        global_rank = int(os.environ["OMPI_COMM_WORLD_RANK"])
        world_size = int(os.environ["OMPI_COMM_WORLD_SIZE"])
    else:
        # NOTE:
        # Due to some reasons, if you need to use older API,
        # torch.distributed.launch, please switch to here.
        local_rank = args.local_rank
        global_rank = int(os.environ["RANK"])
        world_size = int(os.environ["WORLD_SIZE"])

        dist.init_process_group(
            backend="nccl",
            init_method="env://",
            rank=global_rank,
            world_size=world_size,
        )
    print(
        (
            f"[{socket.gethostbyname(socket.gethostname())}] "
            "job information: (local_rank, global_rank, world_size) = "
            f"({local_rank}, {global_rank}, {world_size})"
        )
    )

    device = torch.device(
        f"cuda:{local_rank}" if torch.cuda.is_available() else "cpu"
    )

    # Prepare output directory.
    if global_rank == 0:
        if not os.path.exists(args.output_path):
            os.makedirs(args.output_path)
        if not os.path.isdir(args.output_path):
            raise RuntimeError(
                f"{args.output_path} exists, but it is not a directory."
            )
    dist.barrier()

    trainloader, valloader, sampler, n_classes = prepare_dataset(
        args.input_path,
        args.batch_size,
        num_workers=args.num_workers,
        no_validation=args.no_validation,
    )

    model = build_model(n_classes)
    model = model.to(device)
    # Make model distributed version.
    model = DDP(model, device_ids=[local_rank])

    criterion = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.SGD(model.parameters(), lr=args.lr)
    # NOTE:
    # If you are interested in the acceleration by Tensor Cores,
    # please read the following doc.
    # https://pytorch.org/docs/stable/amp.html
    scaler = torch.amp.GradScaler('cuda')

    for epoch in range(args.num_epochs):
        if global_rank == 0:
            print(f"Epoch {epoch+1}/{args.num_epochs}")

        # NOTE:
        # `sampler.set_epoch()` must be called when just starting each epoch.
        # For more detalis, please see the warning comment of
        # "DistributedSampler" in the page below.
        # https://pytorch.org/docs/stable/data.html
        sampler.set_epoch(epoch)

        running_loss = 0.0
        # NOTE:
        # This is a simplified way to measure the time.
        # You should use more precise method to know the performance.
        starttime = time.perf_counter()
        for i, data in enumerate(trainloader):
            # NOTE:
            # If you want to overlap the data transferring between CPU-GPU,
            # you need to additionally implement custom dataloader,
            # or use pinned memory.
            # Following pages could help you.
            # https://github.com/NVIDIA/DeepLearningExamples/blob/f24917b3ee73763cfc888ceb7dbb9eb62343c81e/PyTorch/Classification/ConvNets/image_classification/dataloaders.py#L347
            # https://pytorch.org/docs/stable/data.html#memory-pinning
            inputs = data[0].to(device, non_blocking=True)
            labels = data[1].to(device, non_blocking=True)

            optimizer.zero_grad()

            with torch.amp.autocast('cuda'):
                outputs = model(inputs)
                loss = criterion(outputs, labels)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

            if global_rank != 0:
                # Skip showing training loss.
                continue

            # Calculate and show current average loss.
            running_loss += loss.item()
            if (i % args.logging_interval) == (args.logging_interval - 1):
                print(
                    (
                        f"\t [iter={i+1:05d}] training loss = "
                        f"{running_loss / (i+1):.3f}"
                    )
                )

        # End of each epoch.

        # Calculate validation result.
        if not args.no_validation:
            running_valloss = 0.0
            n_valiter = len(valloader)
            model.eval()
            with torch.no_grad():
                for valdata in valloader:
                    val_in = valdata[0].to(device, non_blocking=True)
                    val_label = valdata[1].to(device, non_blocking=True)
                    valout = model(val_in)
                    valloss = criterion(valout, val_label)
                    running_valloss += valloss.item()
            model.train()

            # Sync all validation results.
            valinfo = torch.tensor([running_valloss, n_valiter], device=device)
            dist.all_reduce(valinfo)
            running_valloss = valinfo[0].item()
            n_valiter = valinfo[1].item()

        if global_rank != 0:
            # Directly goes to next epoch.
            continue

        # Show this epoch time and training&validation losses.
        # NOTE: This time includes vaidation time.
        duration = time.perf_counter() - starttime
        if args.no_validation:
            print(
                (
                    f"\t [iter={i+1:05d}] "
                    f"{duration:.3f}s {duration*1000. / i:.3f}ms/step, "
                    f"loss = {running_loss / (i+1):.3f}"
                )
            )
        else:
            print(
                (
                    f"\t [iter={i+1:05d}] "
                    f"{duration:.3f}s {duration*1000. / i:.3f}ms/step, "
                    f"loss = {running_loss / (i+1):.3f}, "
                    f"val_loss = {running_valloss / n_valiter:.3f}"
                )
            )

    # Save model.
    if global_rank == 0:
        model_filepath = os.path.join(args.output_path, "model.pth")
        torch.save(model.state_dict(), model_filepath)

        # Send a notification.
        print(
            (
                f"[ranks:{local_rank} / {global_rank}] "
                "rank0 is sending a notification."
            )
        )
        dist.barrier()
        print(
            (
                f"[ranks:{local_rank} / {global_rank}] "
                "notification from rank0 has been sent."
            )
        )
    else:
        # Wait for a notification from rank0.
        print(
            (
                f"[ranks:{local_rank} / {global_rank}] "
                "worker rank is waiting for saving model complesion..."
            )
        )
        dist.barrier()
        print(
            (
                f"[ranks:{local_rank} / {global_rank}] "
                "worker rank received a notification from rank0."
            )
        )

    # Finalize.
    dist.destroy_process_group()
    print("done.")


def prepare_dataset(datadir, batch_size, num_workers=8, no_validation=False):
    n_classes = len(glob.glob(os.path.join(datadir, "train", "cls_*")))

    # Prepare transform ops.
    # Basically, PIL object should be converted into tensor.
    transform = transforms.Compose([transforms.ToTensor()])

    # Prepare train dataset.
    # NOTE:
    # ImageFolder assumes that `root` directory contains
    # several class directories like below.
    # root/cls_000, root/cls_001, root/cls_002, ...
    trainset = torchvision.datasets.ImageFolder(
        root=os.path.join(datadir, "train"), transform=transform
    )

    # NOTE:
    # When using Sampler, `shuffle` on DataLoader must *NOT* be specified.
    # For more details, please read DataLoader API reference.
    # https://pytorch.org/docs/stable/data.html
    sampler = DistributedSampler(trainset)
    trainloader = torch.utils.data.DataLoader(
        trainset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        sampler=sampler,
    )

    # Prepare val dataset.
    if no_validation:
        valset = []  # NOTE: To show a message later.
        valloader = None
    else:
        valset = torchvision.datasets.ImageFolder(
            root=os.path.join(datadir, "val"), transform=transform
        )
        sampler = DistributedSampler(valset)
        valloader = torch.utils.data.DataLoader(
            valset,
            batch_size=batch_size,
            shuffle=False,
            num_workers=num_workers,
            sampler=sampler,
        )

    print(f"trainset.size = {len(trainset)}")
    print(f"valset.size = {len(valset)}")

    return trainloader, valloader, sampler, n_classes


def build_model(n_classes):
    model = models.resnet50(weights="DEFAULT")
    n_fc_in_feats = model.fc.in_features
    model.fc = torch.nn.Sequential(
        torch.nn.Linear(n_fc_in_feats, 512), torch.nn.Linear(512, n_classes)
    )
    return model


def parse_args():
    parser = argparse.ArgumentParser(
        description="PyTorch torch.distributed.run Example",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input-path",
        type=str,
        default="./images",
        help="a parent directory path to input image files",
    )

    parser.add_argument(
        "--batch-size", type=int, default=64, help="input batch size"
    )
    parser.add_argument(
        "--lr", type=float, default=0.001, help="learning rate"
    )
    parser.add_argument(
        "--num-epochs", type=int, default=10, help="number of epochs"
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=8,
        help="number of workers for data loading",
    )

    parser.add_argument(
        "--output-path",
        type=str,
        default="./models",
        help="output path to store saved model",
    )

    parser.add_argument(
        "--no-validation", action="store_true", help="Disable validation."
    )

    parser.add_argument("--use-older-api", action="store_true")

    parser.add_argument(
        "--logging-interval", type=int, default=10, help="logging interval"
    )

    args, unknown_args = parser.parse_known_args()
    if args.use_older_api:
        older_parser = argparse.ArgumentParser()
        older_parser.add_argument(
            "--local_rank", type=int, help="local rank info."
        )
        args = older_parser.parse_args(unknown_args, namespace=args)
    print(args)

    return args


if __name__ == "__main__":
    main()
