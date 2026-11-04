#!/usr/bin/env bash
# c7-validate-selfupdate.sh — W4: SELF-UPDATE AUTOMATICO del client (≥0.2.11).
# Prova REALE su server isolato + mirror isolato:
#   1) mirror pubblica una versione "9.9.9" = binario reale + 1 byte in coda
#      (sha diverso, ELF ancora eseguibile) e manifest latest=9.9.9;
#   2) il client parte sotto un SUPERVISORE (come systemd o il Task di
#      Windows), il poll porta server_client_version≠propria → scarica il
#      DESCRITTORE FIRMATO, verifica con la pubkey pinnata, scarica il
#      binario, swap <exe>→<exe>.old, marker Probation, ed ESCE: a rilanciarlo
#      e' il supervisore;
#   3) il NUOVO processo (sha == manifest) conferma al primo poll: .old sparisce
#      e non ricicla (idempotenza sha);
#   4) mancata conferma: col server spento il nuovo binario non conferma, al
#      boot successivo torna da solo al binario precedente.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$REPO/runtime"
CLIENT_BIN_SRC="$REPO/client-rs/target/release/metnos-client"
PORT="${METNOS_C7U_PORT:-8795}"
SERVER="http://127.0.0.1:$PORT"

TMP="$(mktemp -d)"
# shellcheck source=scripts/c7-isolate.sh
source "$REPO/scripts/c7-isolate.sh"
export XDG_DATA_HOME="$TMP/xdg-data" XDG_CACHE_HOME="$TMP/xdg-cache" XDG_CONFIG_HOME="$TMP/xdg-config"
export METNOS_DEVICES_DB="$TMP/devices.db" METNOS_AGENT_LOCKFILE="$TMP/agent.lock" METNOS_AGENT_PORT="$PORT"
export METNOS_MIRROR_ROOT="$TMP/mirror"    # mirror ISOLATO
mkdir -p "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$METNOS_USER_DATA"
STATE_JSON="$XDG_DATA_HOME/metnos/state.json"
SERVER_PID=""; CLIENT_PID=""; FAILED=0
pass(){ printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail(){ printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
info(){ printf '==> %s\n' "$*"; }
cleanup(){ touch "$TMP/stop"; [ -n "$CLIENT_PID" ] && kill "$CLIENT_PID" 2>/dev/null; pkill -f "$TMP/bin/metnos-client" 2>/dev/null; [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; wait 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT
py(){ ( cd "$RUNTIME" && python3 "$@" ); }

[ -x "$CLIENT_BIN_SRC" ] || { echo "client non buildato: $CLIENT_BIN_SRC"; exit 1; }

# Il supervisore: rilancia il client ogni volta che esce, e con «$TMP/hold»
# aspetta prima di farlo.
supervise(){
  while [ ! -e "$TMP/stop" ]; do
    "$CLIENT_BIN" run --server "$SERVER" >>"$TMP/client.log" 2>&1
    sleep 0.5
    while [ -e "$TMP/hold" ] && [ ! -e "$TMP/stop" ]; do sleep 0.1; done
  done
}
wait_for_sha(){
  local want="$1" end=$(( SECONDS + 40 ))
  while [ "$SECONDS" -lt "$end" ]; do
    [ "$(sha256sum "$CLIENT_BIN" 2>/dev/null | cut -d' ' -f1)" = "$want" ] && return 0
    sleep 0.2
  done
  return 1
}

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
python3 - "$METNOS_MIRROR_ROOT/client/manifest.json" "$NEWSHA" "$TARGET" <<'PYEOF'
import json, sys
path, sha, target = sys.argv[1:]
json.dump({"latest": "9.9.9", "versions": {"9.9.9": {target: {"client": {
    "filename": "metnos-client", "path": f"9.9.9/{target}/metnos-client", "sha256": sha}}}}},
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
supervise & CLIENT_PID=$!

wait_for_sha "$NEWSHA" && pass "swap del binario avvenuto (sha = 9.9.9)" || { fail "swap non avvenuto"; tail -20 "$TMP/client.log"; exit 1; }

# Il NUOVO processo conferma al primo poll riuscito: .old sparisce.
end=$(( SECONDS + 40 ))
while [ "$SECONDS" -lt "$end" ] && [ -e "$TMP/bin/metnos-client.old" ]; do sleep 0.2; done
[ ! -e "$TMP/bin/metnos-client.old" ] && pass "il client rilanciato conferma al primo poll" || { fail "il client rilanciato non conferma"; tail -20 "$TMP/client.log"; exit 1; }

# Niente loop di update: un solo swap nei log.
NSWAP=$(grep -c "swap completato" "$TMP/client.log" || true)
[ "$NSWAP" = "1" ] && pass "idempotenza sha: UN solo swap (no loop)" || fail "swap ripetuti: $NSWAP"

# Mancata conferma: stesso aggiornamento dal binario originale, ma il server
# si spegne prima che il nuovo processo possa confermare.
info "mancata conferma"
touch "$TMP/hold" "$TMP/stop"
pkill -f "$TMP/bin/metnos-client" 2>/dev/null; wait "$CLIENT_PID" 2>/dev/null
rm -f "$TMP/stop"
cp "$CLIENT_BIN_SRC" "$CLIENT_BIN"
supervise & CLIENT_PID=$!
wait_for_sha "$NEWSHA" && pass "secondo swap avvenuto" || fail "secondo swap non avvenuto"
kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; SERVER_PID=""
rm -f "$TMP/hold"
sleep 2
pkill -f "$TMP/bin/metnos-client" 2>/dev/null
wait_for_sha "$SHA_BEFORE" && pass "ritorno automatico al binario precedente" || fail "ritorno non avvenuto"

echo
if [ "$FAILED" -eq 0 ]; then printf '\033[32m==> SELF-UPDATE automatico: VALIDATO\033[0m\n'; else printf '\033[31m==> self-update: FALLITO\033[0m\n'; fi
exit "$FAILED"
