#!/bin/bash
# 04_wire_supra.sh — registra i backend installati nei tier supra.
#
# Esegue:
#  1. Verifica suprastructure installato
#  2. Verifica llama-server systemd unit running per ogni modello dichiarato
#  3. Per ogni mappatura tier→backend in /opt/metnos/install/public/tier_map.toml:
#     - chiama `supra-model register --tier=<X> --provider=<Y> --url=<Z> --model=<M>`
#  4. Verifica health di ogni tier (chiamata ping)
#  5. Update Metnos `llm_tiers.toml` con `provider=supra` per ogni tier wired
#  6. Stampa report finale: tier → endpoint reale
#
# Idempotente: re-run sicuro (replace mode di default)
#
# Esempi tier_map.toml utente standard:
#   [tiny]
#   provider = "llamacpp"
#   model    = "qwen3.5-9b"
#   url      = "http://localhost:8082"
#
#   [fast]
#   provider = "llamacpp"
#   model    = "gemma-4-26B"
#   url      = "http://localhost:8080"
#
#   [middle]
#   provider = "llamacpp"
#   model    = "gemma-4-26B"
#   url      = "http://localhost:8080"
#
#   [wise]
#   provider = "llamacpp"
#   model    = "gemma-4-26B"
#   url      = "http://localhost:8080"
#
#   [frontier]
#   provider = "anthropic"
#   model    = "claude-opus-4-7"
#   # api_key letta da ~/.config/metnos/anthropic.key

set -euo pipefail

cat <<'EOF'
=== Metnos public install — 04 wire supra ===

[SCAFFOLD]: scheletro post-bench.

Comportamento atteso:
  - Parse tier_map.toml utente (o default se assente)
  - Per ogni tier: supra-model register <args>
  - Health check ogni tier (HTTP ping a base_url/health)
  - Patch ~/.config/metnos/llm_tiers.toml con provider=supra
  - Stampa tabella finale
  - Suggerisce systemctl --user start metnos-http.service

Modalità:
  ./04_wire_supra.sh                  # initial wiring da tier_map.toml
  ./04_wire_supra.sh --tier=fast --replace  # cambia 1 tier dopo install
  ./04_wire_supra.sh --check          # solo health check, nessuna modifica
EOF
exit 0
