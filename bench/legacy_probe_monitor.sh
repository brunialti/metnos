#!/usr/bin/env bash
# Monitoraggio sonda legacy (ADR 0177 M1, scadenza 2026-06-30).
# Stampa il contatore persistente + i marker nel journal. 0 = gated.
set -euo pipefail
cd /opt/metnos
echo "=== legacy_planner_probe @ $(date -Iseconds) ==="
python3 -c "import sys; sys.path.insert(0,'runtime'); import legacy_planner_probe as P; import json; print('counter:', json.dumps(P.count_entries()))" 2>/dev/null | grep -v WARNING
echo "journal markers (last 24h):"
journalctl -u metnos-http.service --since "24 hours ago" 2>/dev/null | grep -c "LEGACY_PLANNER_ENTRY" || echo "0 (or journal unavailable without sudo)"
echo "VERDICT: $(python3 -c "import sys; sys.path.insert(0,'runtime'); import legacy_planner_probe as P; print('GATED (0 entries) → rimuovi legacy' if P.count_entries()['total']==0 else 'NON gated: '+str(P.count_entries()))" 2>/dev/null | grep -v WARNING)"
