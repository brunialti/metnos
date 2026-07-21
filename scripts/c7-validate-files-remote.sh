#!/usr/bin/env bash
# c7-validate-files-remote.sh — C7 Area-2 CP1-3: gli executor FILES read-only
# (find_files, read_files) girano su un device remoto ora che lo shim spedisce
# la CHIUSURA ad albero (backends/files/local + platform_policy + config) e i
# dispatcher hanno il lazy-gw. Pattern isolato di c7-validate-list-dirs.sh
# (server alt-port, XDG temporanei, client Rust vero ≥0.2.10). Prova REALE:
# prima del CP1 find/read fallivano ModuleNotFoundError a module-load.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$REPO/runtime"
CLIENT_BIN="$REPO/client-rs/target/release/metnos-client"
PORT="${METNOS_C7F_PORT:-8797}"
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

# albero di prova: 2 txt + 1 log + contenuto noto per il read
TESTDIR="$TMP/tree"; mkdir -p "$TESTDIR"
printf 'contenuto-alpha-c7\n' > "$TESTDIR/alpha.txt"
printf 'contenuto-gamma-c7\n' > "$TESTDIR/gamma.txt"
echo b > "$TESTDIR/beta.log"

[ -x "$CLIENT_BIN" ] || { echo "client non buildato: $CLIENT_BIN"; exit 1; }
pkill -f "agent_server.py --host 127.0.0.1 --port $PORT" 2>/dev/null || true; sleep 0.5
info "client: $CLIENT_BIN"; info "server isolato $SERVER"
( cd "$RUNTIME" && exec python3 agent_server.py --host 127.0.0.1 --port "$PORT" >>"$TMP/server.log" 2>&1 ) &
SERVER_PID=$!
for _ in $(seq 1 50); do curl -fsS "$SERVER/agent/health" >/dev/null 2>&1 && break; sleep 0.2; done

# shim serve l'ALBERO della chiusura?
if curl -fsS "$SERVER/agent/shim" | py -c "import sys,json; f=json.load(sys.stdin)['files']; need={'backends/__init__.py','backends/files/__init__.py','backends/files/local.py','platform_policy.py','config.py'}; exit(0 if need <= set(f) else 1)"; then
  pass "shim spedisce la chiusura ad albero (backends/… + policy + config)"
else
  fail "shim NON contiene la chiusura ad albero"
fi

TOKEN="$(py devices.py generate-token c7f-laptop)"
"$CLIENT_BIN" register --server "$SERVER" --token "$TOKEN" >"$TMP/register.log" 2>&1 && pass "register OK" || { fail "register"; cat "$TMP/register.log"; }
DEVICE_ID="$(py -c "import json;print(json.load(open('$STATE_JSON'))['device_id'])")"

INV_FIND="$(py -c "import invocations,sys;print(invocations.enqueue_invocation(sys.argv[1],'find_files',{'base_path':sys.argv[2],'pattern':'*.txt','client':'local'}))" "$DEVICE_ID" "$TESTDIR")"
INV_READ="$(py -c "import invocations,sys;print(invocations.enqueue_invocation(sys.argv[1],'read_files',{'paths':[sys.argv[2]+'/alpha.txt'],'client':'local'}))" "$DEVICE_ID" "$TESTDIR")"
INV_LIST="$(py -c "import invocations,sys;print(invocations.enqueue_invocation(sys.argv[1],'list_dirs',{'path':sys.argv[2]}))" "$DEVICE_ID" "$TESTDIR")"
[ -n "$INV_FIND" ] && [ -n "$INV_READ" ] && [ -n "$INV_LIST" ] && pass "find/read/list accodati" || fail "enqueue"

"$CLIENT_BIN" run --server "$SERVER" >>"$TMP/client.log" 2>&1 & CLIENT_PID=$!
end=$(( SECONDS + 60 ))
while [ "$SECONDS" -lt "$end" ]; do
  done_n=$(py -c "
import invocations,sys
n=0
for inv in sys.argv[1:]:
    i=invocations.get_invocation(inv)
    if i and i['state']=='done': n+=1
print(n)" "$INV_FIND" "$INV_READ" "$INV_LIST")
  [ "$done_n" = "3" ] && break; sleep 0.5
done
kill "$CLIENT_PID" 2>/dev/null; wait "$CLIENT_PID" 2>/dev/null; CLIENT_PID=""

if [ "$done_n" = "3" ]; then
  pass "find_files + read_files + list_dirs -> done sul device"
else
  fail "non tutti done (done=$done_n/3)"; tail -30 "$TMP/client.log"
fi

# find: SOLO i .txt (2), niente beta.log
py -c "
import invocations,sys
r=invocations.get_invocation(sys.argv[1])['result']
assert r.get('ok') is True, f'find ok non true: {r}'
names=sorted((e.get('name') or '') for e in r.get('entries',[]))
assert names==['alpha.txt','gamma.txt'], f'find sbagliato: {names}'
print('    find entries=%s' % names)
" "$INV_FIND" && pass "find_files filtra *.txt sul device (2/2, no beta.log)" || { fail "find_files result"; }

# read: contenuto REALE del file remoto
py -c "
import invocations,sys
r=invocations.get_invocation(sys.argv[1])['result']
assert r.get('ok') is True, f'read ok non true: {r}'
ents=r.get('entries',[])
blob=' '.join(str(e.get('content') or e.get('text') or '') for e in ents)
assert 'contenuto-alpha-c7' in blob, f'contenuto mancante: {ents}'
print('    read content OK (contenuto-alpha-c7)')
" "$INV_READ" && pass "read_files ritorna il CONTENUTO dal device" || { fail "read_files result"; }

# regressione list_dirs
py -c "
import invocations,sys
r=invocations.get_invocation(sys.argv[1])['result']
assert r.get('ok') is True
names=[(e.get('name') or '') for e in r.get('entries',[])]
assert 'alpha.txt' in names and 'beta.log' in names, names
" "$INV_LIST" && pass "list_dirs regressione OK" || fail "list_dirs regressione"

echo
if [ "$FAILED" -eq 0 ]; then printf '\033[32m==> C7 files read-only sul device: VALIDATO\033[0m\n'; else printf '\033[31m==> C7 files read-only: FALLITO\033[0m\n'; fi
exit "$FAILED"
