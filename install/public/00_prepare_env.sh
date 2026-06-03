#!/bin/bash
# 00_prepare_env.sh — system check + .env scaffolding per Metnos public install.
#
# Esegue:
#  1. Detect OS (Linux/macOS), arch (x86_64/aarch64)
#  2. Detect GPU (NVIDIA via nvidia-smi, AMD via rocminfo, Apple via sysctl)
#  3. Detect RAM totale (per scegliere modelli compatibili)
#  4. Detect Python ≥ 3.11
#  5. Detect llama.cpp presente o richiede build
#  6. Scrive /opt/metnos/.env.public con paths + capabilities rilevate
#
# Output: .env.public consumato da step 02-04 per scelte default sensate.
# Niente download grossi qui.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${METNOS_INSTALL_ROOT:-/opt/metnos}/.env.public"

# TODO: implementare detection
# detect_os() { ... }
# detect_gpu() { ... }
# detect_ram_gb() { ... }
# detect_python() { ... }
# detect_llamacpp_build() { ... }
# scaffold_env_file() { ... }

cat <<'EOF'
=== Metnos public install — 00 prepare env ===

[SCAFFOLD]: questo script è uno scheletro. Implementazione TODO post-bench.

Comportamento atteso quando completo:
  1. Stampa profilo sistema rilevato
  2. Chiede conferma utente prima di scrivere .env
  3. Scrive METNOS_INSTALL_ROOT, METNOS_LLAMACPP_DIR, METNOS_GPU_KIND,
     METNOS_RAM_GB, METNOS_RECOMMENDED_MODELS
  4. Exit 0 se setup ok, 1 se sistema incompatibile (RAM <16GB, ecc.)

Per ora: edita manualmente .env.public da .env.example
EOF
exit 0
