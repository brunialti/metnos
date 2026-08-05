#!/usr/bin/env bash
# e2e-fresh-install.sh — verifica END-TO-END di un'installazione pulita di Metnos
# a partire dall'export pubblico, ISOLATA dall'ambiente in esercizio.
#
# NON un container (su .33 non c'è docker/podman): usa una tempdir + venv DEDICATO
# + config/data isolati + porta != 8770. Zero tocco a /opt/metnos in esercizio,
# ~/.local/share/metnos, ~/.config/metnos, systemd, o il daemon di produzione.
#
# Stadi verificati: deps (pip -r requirements.txt) → sign-all (catalogo) →
# boot server → catalogo PIENO via loader.
#
# Uso: tests/e2e/tools/e2e-fresh-install.sh [--keep]   (--keep: non cancella la tempdir)
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1
T=$(mktemp -d /tmp/metnos-e2e.XXXXXX)
PORT=8779
echo "== E2E fresh install — tempdir $T, porta $PORT =="

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
PASS=(); FAIL=()
ok()   { PASS+=("$1"); echo "  ✓ $1"; }
bad()  { FAIL+=("$1"); echo "  ✗ $1"; }

# 0. "clone": export pubblico fresco → tempdir (simula git clone)
step "0. clone (export pubblico)"
bash scripts/export-public.sh "$T/repo" >/dev/null 2>&1
rm -rf "$T/repo/.git"
N=$(find "$T/repo" -type f | wc -l)
[ "$N" -gt 800 ] && ok "albero esportato ($N file)" || bad "albero troppo piccolo ($N)"
mkdir -p "$T/data" "$T/config"

# 1. deps: venv fresco + pip -r requirements.txt
# Rete .33 instabile (SSL bad-record-mac intermittente sui transfer grandi):
# retry dell'INTERO install fino a 4 volte (l'errore è transitorio).
step "1. dipendenze (venv fresco + requirements.txt)"
python3 -m venv "$T/venv"
PY="$T/venv/bin/python"
"$T/venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
pip_ok=0
for attempt in 1 2 3 4; do
  echo "  tentativo $attempt/4…"
  if "$T/venv/bin/pip" install --no-cache-dir --timeout 90 --retries 5 \
       -r "$T/repo/requirements.txt" >"$T/pip.log" 2>&1; then
    pip_ok=1; break
  fi
  echo "    (fallito: $(grep -oE 'SSLError|bad record mac|Could not|No matching' "$T/pip.log" | head -1))"
  sleep 4
done
if [ "$pip_ok" = 1 ]; then ok "pip install requirements.txt"; else
  bad "pip install requirements.txt dopo 4 tentativi (rete .33 instabile, vedi $T/pip.log)"
  tail -3 "$T/pip.log"
fi

# env isolato comune
ENVV=(env -i HOME="$T" PATH="/usr/bin:/bin" LANG=C.UTF-8 \
  PYTHONPATH="$T/repo:$T/repo/runtime" \
  METNOS_INSTALL_ROOT="$T/repo" METNOS_USER_DATA="$T/data" METNOS_USER_CONFIG="$T/config")

# 2. import core col venv FRESCO (non quello di esercizio)
step "2. import core (venv fresco)"
if "${ENVV[@]}" "$PY" -c "import sys;sys.path.insert(0,'$T/repo/runtime');import agent_runtime, metnos_http_server, loader" 2>"$T/imp.err"; then
  ok "import agent_runtime + http server + loader"
else
  bad "import core"; tail -3 "$T/imp.err"
fi

# 3. catalogo PRIMA di sign-all (atteso: pochi builtin)
step "3. catalogo pre-firma"
PRE=$("${ENVV[@]}" "$PY" -c "import sys;sys.path.insert(0,'$T/repo/runtime');import loader;print(len(loader.load_catalog(verify=True).executors))" 2>/dev/null)
echo "  executor pre-sign: $PRE"

# 4. sign-all (come l'installer phase3)
step "4. sign-all"
if "${ENVV[@]}" "$PY" "$T/repo/runtime/sign.py" sign-all >"$T/sign.log" 2>&1; then
  ok "$(grep -o 'sign-all: .*' "$T/sign.log" | head -1)"
else
  bad "sign-all"; tail -3 "$T/sign.log"
fi

# 5. catalogo DOPO sign-all (atteso: pieno)
step "5. catalogo post-firma"
POST=$("${ENVV[@]}" "$PY" -c "import sys;sys.path.insert(0,'$T/repo/runtime');import loader;print(len(loader.load_catalog(verify=True).executors))" 2>/dev/null)
echo "  executor post-sign: $POST"
[ "${POST:-0}" -ge 80 ] && ok "catalogo pieno ($POST executor)" || bad "catalogo incompleto ($POST)"

# 6. Tutor: il clone pubblico deve contenere le fonti pubbliche e nessuna
# sorgente internal, anche se il checkout di sviluppo che ha prodotto l'export
# le possiede.
step "6. confine fonti Tutor"
TUTOR_COUNTS=$("${ENVV[@]}" "$PY" -c \
  "from tutor.sources import build_knowledge_units as b; u=b(); r=[x.source_ref for x in u]; print(len(u),sum(x.startswith('docs/') for x in r),sum('internal/' in x for x in r))" \
  2>"$T/tutor.err")
read -r TUTOR_ALL TUTOR_PUBLIC TUTOR_INTERNAL <<<"${TUTOR_COUNTS:-0 0 1}"
echo "  unità: ${TUTOR_ALL:-0}; documentazione pubblica: ${TUTOR_PUBLIC:-0}; internal: ${TUTOR_INTERNAL:-1}"
if [ "${TUTOR_PUBLIC:-0}" -gt 0 ] && [ "${TUTOR_INTERNAL:-1}" -eq 0 ]; then
  ok "Tutor usa documentazione pubblica e zero fonti internal"
else
  bad "confine fonti Tutor"; tail -3 "$T/tutor.err"
fi

# 7. boot server + /agent/health
step "7. boot server"
"${ENVV[@]}" METNOS_HTTP_PORT=$PORT "$PY" -m runtime.metnos_http_server --host 127.0.0.1 --port $PORT >"$T/srv.log" 2>&1 &
SRV=$!; sleep 9
H=$(curl -s --max-time 5 http://127.0.0.1:$PORT/agent/health 2>/dev/null)
echo "  health: ${H:-<nessuna risposta>}"
echo "$H" | grep -q '"ok": true' && ok "server boota + /agent/health" || bad "boot/health"
kill $SRV 2>/dev/null; sleep 1; kill -9 $SRV 2>/dev/null

# riepilogo
step "RIEPILOGO E2E"
echo "  PASS: ${#PASS[@]}  | FAIL: ${#FAIL[@]}"
for f in "${FAIL[@]}"; do echo "   ✗ $f"; done
[ "$KEEP" = 1 ] && echo "  tempdir tenuta: $T" || rm -rf "$T"
[ "${#FAIL[@]}" -eq 0 ] && { echo "  E2E: PASS ✅"; exit 0; } || { echo "  E2E: FAIL ❌"; exit 1; }
