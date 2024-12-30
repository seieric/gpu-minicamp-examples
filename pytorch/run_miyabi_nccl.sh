#!/bin/sh

cuda_name=cuda
cuda_version=12.6
path_name=pytorch-gpu

cd $PBS_O_WORKDIR
cp -r data /local

MPI_PROC=`wc -l $PBS_NODEFILE | awk '{print $1}'`
NODE_NUM=`sort -u $PBS_NODEFILE | wc -l`
MPI_PROC_PER_NODE=`expr $MPI_PROC / $NODE_NUM`

NODE0_IP_OR_HOSTNAME=`sort -u $PBS_NODEFILE | head -n 1`

module load $cuda_name/$cuda_version
module load $path_name
singularity run --bind /local/data --nv $PYTORCH_GPU_IMG \
    python3 -m torch.distributed.run --nnodes=$NODE_NUM --nproc_per_node=$MPI_PROC_PER_NODE \
    --rdzv_id="GpuMinicampExamplesNCCL" --rdzv_backend=c10d --rdzv_endpoint=$NODE0_IP_OR_HOSTNAME \
    pytorch/native/pytorch_distributed_run_example.py --use-nccl --input-path /local/data --num-epochs 4 --batch-size 1800 --output-path models --logging-interval 1
