#!/usr/bin/env bash
# Comparativa LLM per il task NLU-extract: stessa schema, stessi 17 casi
# multilingui, modelli diversi. NON tocca il server prod :8080 (baseline a
# parte). Ogni candidato gira su :8099 transitorio, poi killato.
set -u
LS=/home/roberto/llama.cpp/build-mtp-next/bin/llama-server
PORT=8099
POC=/opt/metnos/runtime/poc
declare -a MODELS=(
  "qwen3-0.6b|/home/roberto/models/Qwen3-0.6B-Q4_K_M.gguf"
  "gemma-4-e2b|/home/roberto/models/gemma-4-E2B-it-Q4_K_M.gguf"
  "qwen35-9b-q4|/home/roberto/models/qwen35-9b/Qwen3.5-9B-Q4_K_M.gguf"
)

run_bench () { # endpoint label
  METNOS_LLM_ENDPOINT="$1" METNOS_LANG=it timeout 180 python3 "$POC/bench.py" 2>/dev/null \
    | grep -E "ACCURACY|LATENCY" | sed "s/^/[$2] /"
}

echo "### BASELINE: Qwen3.6-35B-A3B (prod :8080, MTP) ###"
run_bench "http://127.0.0.1:8080" "35b-prod"

for entry in "${MODELS[@]}"; do
  label="${entry%%|*}"; path="${entry##*|}"
  echo "### ${label} ###"
  [ -f "$path" ] || { echo "[$label] MODELLO ASSENTE: $path"; continue; }
  "$LS" -m "$path" -ngl 999 -fa on --jinja --host 127.0.0.1 --port $PORT \
     -c 8192 --threads 8 >/tmp/ls_${label}.log 2>&1 &
  pid=$!
  # attesa health (max ~90s)
  ok=0
  for i in $(seq 1 90); do
    if curl -s -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok"'; then ok=1; break; fi
    sleep 1
  done
  if [ "$ok" = "1" ]; then
    run_bench "http://127.0.0.1:$PORT" "$label"
  else
    echo "[$label] server non pronto (vedi /tmp/ls_${label}.log)"
    tail -3 /tmp/ls_${label}.log | sed "s/^/[$label] /"
  fi
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  sleep 2
done
echo "### FINE COMPARATIVA ###"
