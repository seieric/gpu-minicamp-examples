#!/bin/sh
module load singularity/3.9.5
singularity run --nv --bind `pwd` $HOME/pytorch-mpi-singularity/container.sif python3 pytorch/native/pytorch_distributed_run_example.py --input-path data/ --num-epochs 4 --batch-size 256 --output-path models --logging-interval 30
