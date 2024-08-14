#!/bin/sh

#PJM -g <your group>
#PJM --fs /work
#PJM -j
#PJM --norestart
#PJM -L rscgrp=regular-a
#PJM -L node=2
#PJM -L elapse=0:15:00
#PJM -L jobenv=singularity
#PJM --mpi proc=16

BASE_DIR=$HOME/gpu-minicamp-examples
module load nvidia/22.7 ompi/4.1.4
mpirun -np ${PJM_MPI_PROC} -machinefile ${PJM_O_NODEINF} \
  -map-by ppr:8:node -mca pml ob1 --mca btl self,tcp -mca btl_tcp_if_include ib0,ib1,ib2,ib3 \
  --wdir ${BASE_DIR} \
  ${BASE_DIR}/pytorch/run2.sh
