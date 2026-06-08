#!/bin/bash
# 03_install_metnos.sh — install Metnos (questo repo) come applicazione.
#
# Esegue:
#  1. Verifica .env.public esistente (step 00 eseguito)
#  2. Crea venv Python ≥ 3.11 in /opt/metnos/.venv
#  3. Installa requirements.txt
#  4. Sign degli executor (`python3 runtime/sign.py sign-all`)
#  5. Genera ~/.config/metnos/admin.key (mode 0600)
#  6. Crea ~/.local/share/metnos/ + ~/.local/state/metnos/ structure
#  7. Installa systemd user units (metnos-http.service)
#  8. NON avvia ancora: serve step 04 (wire supra) prima

set -euo pipefail

cat <<'EOF'
=== Metnos public install — 03 install Metnos ===

[SCAFFOLD]: scheletro post-bench.

Comportamento atteso:
  - venv Python 3.12 (no 3.11 deprecation warning per minijinja2)
  - pip install -r requirements.txt
  - python3 runtime/sign.py sign-all (firma tutti gli executor)
  - mkdir paths + 0600 admin.key
  - copia systemd/*.service in ~/.config/systemd/user/
  - systemctl --user daemon-reload
  - NIENTE start: serve wire supra prima → step 04

Output: ./metnos-installer riprende qui se launched via wrapper.
EOF
exit 0
