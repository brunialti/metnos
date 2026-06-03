#!/bin/bash
# Ablation matrix per Qwen3.5-9B: test diversi config ranker
# Run AFTER initial 501 bench complete (parse_cache_dedicated populated).
set -e
cd /opt/metnos/e2e/simulator

declare -A CONFIGS=(
  [T_default]="SIM_LAYER0_SIZE=0 SIM_DENSE_ENTRY=0 SIM_BACKWARD=0"
  [layer0_5]="SIM_LAYER0_SIZE=5 SIM_DENSE_ENTRY=0 SIM_BACKWARD=0"
  [layer0_12]="SIM_LAYER0_SIZE=12 SIM_DENSE_ENTRY=0 SIM_BACKWARD=0"
  [dense_entry]="SIM_LAYER0_SIZE=0 SIM_DENSE_ENTRY=1 SIM_BACKWARD=0"
  [backward]="SIM_LAYER0_SIZE=0 SIM_DENSE_ENTRY=0 SIM_BACKWARD=1"
  [all_on]="SIM_LAYER0_SIZE=12 SIM_DENSE_ENTRY=1 SIM_BACKWARD=1"
)

for name in T_default layer0_5 layer0_12 dense_entry backward all_on; do
  out=/tmp/iter_500_qwen_${name}.out
  env_vars=${CONFIGS[$name]}
  echo "=== $name ($env_vars) ==="
  eval "$env_vars BENCH_500=1 PYTHONUNBUFFERED=1 timeout 600 python3 -u run_sim_dedicated.py" > "$out" 2>&1
  tail -8 "$out" | grep "accepted\|top-1 prefix"
  echo
done
