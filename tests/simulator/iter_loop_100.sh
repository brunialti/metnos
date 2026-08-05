#!/bin/bash
# Loop su 100 query verso 99% top-2 accepted.
# Esce quando top-2 >= 99% o iter >= 20.
cd /opt/metnos/tests/simulator
LOG=/tmp/iter_loop_100.log
echo "===START $(date)" > "$LOG"

for i in $(seq 1 20); do
  iter=$(printf '%02d' $i)
  out=/tmp/iter_100_${iter}.out
  PYTHONUNBUFFERED=1 timeout 600 python3 -u run_simulation_v2.py > "$out" 2>&1
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "[iter $iter] rc=$rc (timeout/error)" | tee -a "$LOG"
    continue
  fi
  pct=$(grep 'top-2 accepted' "$out" | grep -oE '[0-9]+%' | tail -1 | tr -d '%')
  if [ -z "$pct" ]; then
    echo "[iter $iter] no metrics" | tee -a "$LOG"
    continue
  fi
  echo "[iter $iter] top-2=${pct}% $(date '+%H:%M:%S')" | tee -a "$LOG"
  if [ "$pct" -ge 99 ]; then
    echo "===DONE iter $iter at ${pct}%" | tee -a "$LOG"
    break
  fi
done
echo "===END $(date)" >> "$LOG"
