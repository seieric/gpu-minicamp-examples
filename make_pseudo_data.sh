#!/bin/sh
module load aquarius python/3.11.7
. .venv/bin/activate
python3 make_pseudo_data.py \
  --num-images 100000 --num-classes 100 \
  --outdir ./data/ --val-ratio 0.2