#!/bin/bash
# Loop iterativo intelligente: prova varianti penalty config, traccia best.
# In silenzio in BG. Esce a 99% o iter 30.
cd /opt/metnos/tests/simulator
LOG=/tmp/iter_loop_v2.log
echo "===START $(date)" > "$LOG"

# Penalty config experiments — applied via env to graph_search rank tuning
declare -a CONFIGS=(
  # qualif_pen, canonical_bonus, mut_bonus, l1_only=0
  "1.0 0.2 0.5 0"
  "1.5 0.4 0.5 0"
  "2.0 0.4 0.7 0"
  "1.0 0.4 0.5 1"
  "1.5 0.4 0.7 0"
  "2.0 0.5 0.8 0"
  "2.5 0.5 1.0 0"
  "3.0 0.5 1.0 0"
)

BEST_PCT=0
BEST_CFG=""

for i in "${!CONFIGS[@]}"; do
  iter=$(printf '%02d' $((i+1)))
  cfg=${CONFIGS[$i]}
  read qp cb mb l1 <<< "$cfg"
  out=/tmp/iter_v2_${iter}.out
  RANK_QUALIF_PEN=$qp RANK_CANONICAL=$cb RANK_MUT=$mb RANK_L1_ONLY=$l1 \
    BENCH_500=1 PYTHONUNBUFFERED=1 timeout 240 python3 -u run_simulation_v2.py > "$out" 2>&1
  rc=$?
  pct=$(grep 'top-2 accepted' "$out" | grep -oE '[0-9]+%' | tail -1 | tr -d '%')
  if [ -z "$pct" ]; then pct=0; fi
  pct1=$(grep 'top-1 accepted' "$out" | grep -oE '[0-9]+%' | tail -1 | tr -d '%')
  echo "[iter $iter] cfg=($cfg) top-1=${pct1}% top-2=${pct}% $(date '+%H:%M:%S')" | tee -a "$LOG"
  if [ "$pct" -gt "$BEST_PCT" ]; then
    BEST_PCT=$pct
    BEST_CFG="$cfg"
    cp "$out" /tmp/iter_v2_best.out
  fi
  if [ "$pct" -ge 99 ]; then
    echo "===DONE iter $iter at top-2=${pct}%" | tee -a "$LOG"
    break
  fi
done
echo "===BEST top-2=${BEST_PCT}% cfg=($BEST_CFG)" | tee -a "$LOG"
echo "===END $(date)" >> "$LOG"
