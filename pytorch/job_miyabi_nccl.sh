#!/bin/sh
#PBS -q debug-g
#PBS -W group_list=gt01
#PBS -l walltime=0:15:00
#PBS -l select=4:mpiprocs=1
#PBS -j oe
#PBS -N gpu-minicamp-examples-nccl
#PBS -m n
#PBS -r n

cd $PBS_O_WORKDIR

module purge
module load gcc
module load ompi

MPI_PROC=`wc -l $PBS_NODEFILE | awk '{print $1}'`
NODE_NUM=`sort -u $PBS_NODEFILE | wc -l`
MPI_PROC_PER_NODE=`expr $MPI_PROC / $NODE_NUM`

mpirun -n ${MPI_PROC} --map-by ppr:${MPI_PROC_PER_NODE}:node \
	-mca pml ob1 -mca btl self,tcp -mca btl_tcp_if_include ibP2s2 \
	-hostfile ${PBS_NODEFILE} \
	-x PATH -x LD_LIBRARY_PATH \
	./pytorch/run_miyabi_nccl.sh
