#!/usr/bin/env bash
# c7-validate-list-dirs.sh — validazione C7 (opzione 1): list_dirs gira su un
# device remoto ora che lo shim spedisce path_alias. Riusa il pattern isolato
# di e2e-remote-executor.sh (server alt-port, XDG temporanei, client Rust vero).
# Prova REALE: prima del fix list_dirs falliva ModuleNotFoundError sul device.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$REPO/runtime"
CLIENT_BIN="$REPO/client-rs/target/release/metnos-client"
PORT="${METNOS_C7_PORT:-8798}"
SERVER="http://127.0.0.1:$PORT"

TMP="$(mktemp -d)"
export XDG_DATA_HOME="$TMP/xdg-data" XDG_CACHE_HOME="$TMP/xdg-cache" XDG_CONFIG_HOME="$TMP/xdg-config"
export METNOS_DEVICES_DB="$TMP/devices.db" METNOS_AGENT_LOCKFILE="$TMP/agent.lock" METNOS_AGENT_PORT="$PORT"
mkdir -p "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"
STATE_JSON="$XDG_DATA_HOME/metnos/state.json"
SERVER_PID=""; CLIENT_PID=""; FAILED=0
pass(){ printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail(){ printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
info(){ printf '==> %s\n' "$*"; }
cleanup(){ [ -n "$CLIENT_PID" ] && kill "$CLIENT_PID" 2>/dev/null; [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; wait 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT
py(){ ( cd "$RUNTIME" && python3 "$@" ); }

# albero di prova: 2 file + 1 sottocartella
TESTDIR="$TMP/tree"; mkdir -p "$TESTDIR/sub"
echo a > "$TESTDIR/alpha.txt"; echo b > "$TESTDIR/beta.log"

[ -x "$CLIENT_BIN" ] || { echo "client non buildato: $CLIENT_BIN"; exit 1; }
pkill -f "agent_server.py --host 127.0.0.1 --port $PORT" 2>/dev/null || true; sleep 0.5
info "client: $CLIENT_BIN"; info "server isolato $SERVER"
( cd "$RUNTIME" && exec python3 agent_server.py --host 127.0.0.1 --port "$PORT" >>"$TMP/server.log" 2>&1 ) &
SERVER_PID=$!
for _ in $(seq 1 50); do curl -fsS "$SERVER/agent/health" >/dev/null 2>&1 && break; sleep 0.2; done

# shim serve path_alias.py?
if curl -fsS "$SERVER/agent/shim" | py -c "import sys,json; f=json.load(sys.stdin)['files']; exit(0 if 'path_alias.py' in f else 1)"; then
  pass "shim spedisce path_alias.py"
else
  fail "shim NON contiene path_alias.py"
fi

TOKEN="$(py devices.py generate-token c7-laptop)"
"$CLIENT_BIN" register --server "$SERVER" --token "$TOKEN" >"$TMP/register.log" 2>&1 && pass "register OK" || { fail "register"; cat "$TMP/register.log"; }
DEVICE_ID="$(py -c "import json;print(json.load(open('$STATE_JSON'))['device_id'])")"

INV="$(py -c "import invocations,sys,json;print(invocations.enqueue_invocation(sys.argv[1],'list_dirs',{'path':sys.argv[2]}))" "$DEVICE_ID" "$TESTDIR")"
[ -n "$INV" ] && pass "list_dirs accodato ($INV)" || fail "enqueue"

"$CLIENT_BIN" run --server "$SERVER" >>"$TMP/client.log" 2>&1 & CLIENT_PID=$!
end=$(( SECONDS + 45 ))
while [ "$SECONDS" -lt "$end" ]; do
  st=$(py -c "import invocations,sys;i=invocations.get_invocation(sys.argv[1]);print(i['state'] if i else 'MISSING')" "$INV")
  [ "$st" = "done" ] && break; sleep 0.5
done
kill "$CLIENT_PID" 2>/dev/null; wait "$CLIENT_PID" 2>/dev/null; CLIENT_PID=""

if [ "$st" = "done" ]; then
  pass "list_dirs -> done sul device"
  py -c "
import invocations,sys,json
r=invocations.get_invocation(sys.argv[1])['result']
assert r.get('ok') is True, f'ok non true: {r}'
names=[ (e.get('name') or e.get('path') or '') for e in r.get('entries',[]) ]
blob=' '.join(names)
assert 'alpha.txt' in blob and 'beta.log' in blob and 'sub' in blob, f'contenuti mancanti: {names}'
print('    sandbox=%s  entries=%s' % (r.get('sandbox'), [n.split('/')[-1] for n in names]))
" "$INV" && pass "entries elencano alpha.txt/beta.log/sub (path_alias risolto, no ModuleNotFoundError)" || { fail "entries non valide"; tail -20 "$TMP/client.log"; }
else
  fail "list_dirs non completato (state=$st)"; tail -25 "$TMP/client.log"
fi

echo
if [ "$FAILED" -eq 0 ]; then printf '\033[32m==> C7 list_dirs: VALIDATO\033[0m\n'; else printf '\033[31m==> C7 list_dirs: FALLITO\033[0m\n'; fi
exit "$FAILED"
