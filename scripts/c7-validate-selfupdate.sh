#!/usr/bin/env bash
# c7-validate-selfupdate.sh — W4: SELF-UPDATE AUTOMATICO del client (≥0.2.11).
# Prova REALE su server isolato + mirror isolato:
#   1) mirror pubblica una versione "9.9.9" = binario reale + 1 byte in coda
#      (sha diverso, ELF ancora eseguibile) e manifest latest=9.9.9;
#   2) il client parte, il poll porta server_client_version≠propria →
#      scarica il DESCRITTORE FIRMATO, verifica con la pubkey pinnata,
#      scarica il binario, swap <exe>→<exe>.old, respawn;
#   3) il NUOVO processo (sha == manifest) NON ricicla (idempotenza sha)
#      e continua a lavorare: un'invocazione list_dirs va a done;
#   4) rollback file: <exe>.old presente = binario precedente.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$REPO/runtime"
CLIENT_BIN_SRC="$REPO/client-rs/target/release/metnos-client"
PORT="${METNOS_C7U_PORT:-8795}"
SERVER="http://127.0.0.1:$PORT"

TMP="$(mktemp -d)"
export XDG_DATA_HOME="$TMP/xdg-data" XDG_CACHE_HOME="$TMP/xdg-cache" XDG_CONFIG_HOME="$TMP/xdg-config"
export METNOS_DEVICES_DB="$TMP/devices.db" METNOS_AGENT_LOCKFILE="$TMP/agent.lock" METNOS_AGENT_PORT="$PORT"
export METNOS_USER_DATA="$TMP/user-data"
export METNOS_MIRROR_ROOT="$TMP/mirror"    # mirror ISOLATO
mkdir -p "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$METNOS_USER_DATA"
STATE_JSON="$XDG_DATA_HOME/metnos/state.json"
SERVER_PID=""; CLIENT_PID=""; FAILED=0
pass(){ printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail(){ printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
info(){ printf '==> %s\n' "$*"; }
cleanup(){ [ -n "$CLIENT_PID" ] && kill "$CLIENT_PID" 2>/dev/null; pkill -f "$TMP/bin/metnos-client" 2>/dev/null; [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; wait 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT
py(){ ( cd "$RUNTIME" && python3 "$@" ); }

[ -x "$CLIENT_BIN_SRC" ] || { echo "client non buildato: $CLIENT_BIN_SRC"; exit 1; }

# Il client va eseguito da una COPIA scrivibile (lo swap tocca il file exe).
mkdir -p "$TMP/bin"
CLIENT_BIN="$TMP/bin/metnos-client"
cp "$CLIENT_BIN_SRC" "$CLIENT_BIN"

# Mirror isolato: versione "9.9.9" = binario + 1 byte (sha diverso, gira).
TARGET="x86_64-unknown-linux-musl"
mkdir -p "$METNOS_MIRROR_ROOT/client/9.9.9/$TARGET"
NEWBIN="$METNOS_MIRROR_ROOT/client/9.9.9/$TARGET/metnos-client"
cp "$CLIENT_BIN_SRC" "$NEWBIN"; printf '\0' >> "$NEWBIN"
NEWSHA=$(sha256sum "$NEWBIN" | cut -d' ' -f1)
python3 - "$METNOS_MIRROR_ROOT/client/manifest.json" "$NEWSHA" <<'PYEOF'
import json, sys
path, sha = sys.argv[1], sys.argv[2]
json.dump({"latest": "9.9.9",
           "versions": {"9.9.9": {"x86_64-unknown-linux-musl": {"sha256": sha}}}},
          open(path, "w"))
PYEOF

pkill -f "agent_server.py --host 127.0.0.1 --port $PORT" 2>/dev/null || true; sleep 0.5
info "server isolato $SERVER (mirror latest=9.9.9)"
( cd "$RUNTIME" && exec python3 agent_server.py --host 127.0.0.1 --port "$PORT" >>"$TMP/server.log" 2>&1 ) &
SERVER_PID=$!
for _ in $(seq 1 50); do curl -fsS "$SERVER/agent/health" >/dev/null 2>&1 && break; sleep 0.2; done

# Descrittore firmato esposto?
curl -fsS "$SERVER/agent/client/update/$TARGET" | py -c "
import sys, json
d = json.load(sys.stdin)
assert d['version']=='9.9.9' and d['sha256'] and d['sig'], d
print('    descrittore:', d['version'], d['sha256'][:12]+'…')
" && pass "descrittore update firmato servito" || fail "descrittore assente"

TOKEN="$(py devices.py generate-token c7u-laptop)"
"$CLIENT_BIN" register --server "$SERVER" --token "$TOKEN" >"$TMP/register.log" 2>&1 && pass "register OK" || { fail "register"; cat "$TMP/register.log"; }
DEVICE_ID="$(py -c "import json;print(json.load(open('$STATE_JSON'))['device_id'])")"

SHA_BEFORE=$(sha256sum "$CLIENT_BIN" | cut -d' ' -f1)
"$CLIENT_BIN" run --server "$SERVER" >>"$TMP/client.log" 2>&1 & CLIENT_PID=$!

# Attendi lo swap (il poll gira subito): exe sha → NEWSHA
end=$(( SECONDS + 40 )); SWAPPED=0
while [ "$SECONDS" -lt "$end" ]; do
  CUR=$(sha256sum "$CLIENT_BIN" 2>/dev/null | cut -d' ' -f1)
  [ "$CUR" = "$NEWSHA" ] && SWAPPED=1 && break
  sleep 0.5
done
[ "$SWAPPED" = "1" ] && pass "swap del binario avvenuto (sha = 9.9.9)" || { fail "swap non avvenuto"; tail -20 "$TMP/client.log"; }
[ -f "$TMP/bin/metnos-client.old" ] && [ "$(sha256sum "$TMP/bin/metnos-client.old" | cut -d' ' -f1)" = "$SHA_BEFORE" ] \
  && pass "rollback file .old = binario precedente" || fail ".old assente o sbagliato"

# Il NUOVO processo lavora: invocazione reale → done. (CLIENT_PID è il padre
# morto; il figlio respawnato è un processo nuovo — pkill in cleanup.)
TESTDIR="$TMP/tree"; mkdir -p "$TESTDIR"; echo x > "$TESTDIR/a.txt"
sleep 2
INV="$(py -c "import invocations,sys;print(invocations.enqueue_invocation(sys.argv[1],'list_dirs',{'path':sys.argv[2]}))" "$DEVICE_ID" "$TESTDIR")"
end=$(( SECONDS + 40 )); st=""
while [ "$SECONDS" -lt "$end" ]; do
  st=$(py -c "import invocations,sys;i=invocations.get_invocation(sys.argv[1]);print(i['state'] if i else 'MISSING')" "$INV")
  [ "$st" = "done" ] && break; sleep 0.5
done
[ "$st" = "done" ] && pass "il client respawnato ESEGUE (list_dirs → done)" || { fail "client respawnato muto (state=$st)"; tail -25 "$TMP/client.log"; }

# Niente loop di update: un solo swap nei log.
NSWAP=$(grep -c "swap completato" "$TMP/client.log" || true)
[ "$NSWAP" = "1" ] && pass "idempotenza sha: UN solo swap (no loop)" || fail "swap ripetuti: $NSWAP"

echo
if [ "$FAILED" -eq 0 ]; then printf '\033[32m==> SELF-UPDATE automatico: VALIDATO\033[0m\n'; else printf '\033[31m==> self-update: FALLITO\033[0m\n'; fi
exit "$FAILED"
