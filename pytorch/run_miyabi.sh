#!/bin/sh

cuda_name=cuda
cuda_version=12.6
path_name=pytorch-gpu

cd $PBS_O_WORKDIR
cp -r data /local

module load $cuda_name/$cuda_version
module load $path_name
singularity run --bind /local/data --nv $PYTORCH_GPU_IMG python3 pytorch/native/pytorch_distributed_run_example.py --input-path /local/data --num-epochs 4 --batch-size 1800 --output-path models --logging-interval 1
