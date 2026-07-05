#!/usr/bin/env bash
# c7-validate-mutants-remote.sh — C7 Area-2 CP4: MUTANTI sul device con UNDO
# round-trip. Prova REALE col client Rust (≥0.2.10) su server isolato:
#   1) write_files SUL device (via invoke_executor + target_device) → file
#      creato nell'albero del device + record undo {pending,done,device=…};
#   2) undo_last_turn dal SERVER → reverse (delete_files) ACCODATO AL DEVICE
#      → file SPARITO sul device (§2.9: mai ribaltato sul server);
#   3) move_files sul device → undo → file TORNATO alla sorgente;
#   4) gate placement: delete_files NON è device_ok → con target device gira
#      comunque LOCALE (asimmetria voluta: la chat non cancella sul PC,
#      l'undo sì ma solo ciò che ha creato lui).
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$REPO/runtime"
CLIENT_BIN="$REPO/client-rs/target/release/metnos-client"
PORT="${METNOS_C7M_PORT:-8796}"
SERVER="http://127.0.0.1:$PORT"

TMP="$(mktemp -d)"
export XDG_DATA_HOME="$TMP/xdg-data" XDG_CACHE_HOME="$TMP/xdg-cache" XDG_CONFIG_HOME="$TMP/xdg-config"
export METNOS_DEVICES_DB="$TMP/devices.db" METNOS_AGENT_LOCKFILE="$TMP/agent.lock" METNOS_AGENT_PORT="$PORT"
export METNOS_USER_DATA="$TMP/user-data"   # isola undo.jsonl + storage
mkdir -p "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$METNOS_USER_DATA"
STATE_JSON="$XDG_DATA_HOME/metnos/state.json"
SERVER_PID=""; CLIENT_PID=""; FAILED=0
pass(){ printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail(){ printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
info(){ printf '==> %s\n' "$*"; }
cleanup(){ [ -n "$CLIENT_PID" ] && kill "$CLIENT_PID" 2>/dev/null; [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; wait 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT
py(){ ( cd "$RUNTIME" && python3 "$@" ); }

DEVTREE="$TMP/devtree"; mkdir -p "$DEVTREE/srcdir"
printf 'sposta-me\n' > "$DEVTREE/srcdir/doc.txt"

[ -x "$CLIENT_BIN" ] || { echo "client non buildato: $CLIENT_BIN"; exit 1; }
pkill -f "agent_server.py --host 127.0.0.1 --port $PORT" 2>/dev/null || true; sleep 0.5
info "server isolato $SERVER"
( cd "$RUNTIME" && exec python3 agent_server.py --host 127.0.0.1 --port "$PORT" >>"$TMP/server.log" 2>&1 ) &
SERVER_PID=$!
for _ in $(seq 1 50); do curl -fsS "$SERVER/agent/health" >/dev/null 2>&1 && break; sleep 0.2; done

TOKEN="$(py devices.py generate-token c7m-laptop)"
"$CLIENT_BIN" register --server "$SERVER" --token "$TOKEN" >"$TMP/register.log" 2>&1 && pass "register OK" || { fail "register"; cat "$TMP/register.log"; }
DEVICE_ID="$(py -c "import json;print(json.load(open('$STATE_JSON'))['device_id'])")"
"$CLIENT_BIN" run --server "$SERVER" >>"$TMP/client.log" 2>&1 & CLIENT_PID=$!
sleep 1

# ---- 1) WRITE sul device via choke-point (registra undo con device) --------
WOUT=$(py -c "
import sys, json
import agent_runtime
from loader import load_catalog
ex = next(e for e in load_catalog() if e.name=='write_files')
obs = agent_runtime.invoke_executor(
    ex, {'entries':[{'path': sys.argv[1]+'/nuovo/nota.txt', 'content':'da-annullare'}], 'client':'local'},
    timeout_s=40, turn_id='turn-c7m-1', actor='host', channel='e2e',
    target_device='c7m-laptop')
print(json.dumps({'ok': obs.get('ok'), 'dev': obs.get('_ran_on_device')}))
" "$DEVTREE")
echo "    write: $WOUT"
echo "$WOUT" | grep -q "\"ok\": true" && echo "$WOUT" | grep -q "c7m-laptop" \
  && pass "write_files ESEGUITO SUL DEVICE" || { fail "write device"; tail -20 "$TMP/client.log"; }
[ -f "$DEVTREE/nuovo/nota.txt" ] && pass "file creato nell'albero device" || fail "file NON creato"

UREC=$(py -c "
import json
from undo import UndoLog
recs=[r for r in UndoLog()._iter_records()]
pend=[r for r in recs if r['type']=='pending']
done=[r for r in recs if r['type']=='done']
print(json.dumps({'pending': len(pend), 'done': len(done),
                  'device': pend[-1].get('device','') if pend else ''}))
")
echo "    undo-log: $UREC"
echo "$UREC" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d['pending']>=1 and d['done']>=1 and d['device'] else 1)" \
  && pass "record undo con device stampigliato" || fail "record undo assente/senza device"

# ---- 2) UNDO dal server → reverse accodato al device -----------------------
UOUT=$(py -c "
import sys, json
sys.path.insert(0, '$REPO/executors/undo_last_turn')
import undo_last_turn as ult
out = ult.invoke({})
print(json.dumps({'ok': out.get('ok'), 'undone': out.get('undone_count'),
                  'st': [d.get('status') for d in out.get('details',[])]}))
import sys as _s; print(json.dumps(out.get('details',[]), default=str)[:1200], file=_s.stderr)
")
echo "    undo: $UOUT"
echo "$UOUT" | grep -q "\"undone\": 1" && pass "undo_last_turn → undone=1 (reverse sul device)" || { fail "undo non riuscito"; tail -20 "$TMP/client.log"; }
[ ! -f "$DEVTREE/nuovo/nota.txt" ] && pass "file RIMOSSO dal device (round-trip §2.9)" || fail "file ancora presente dopo undo"

# ---- 3) MOVE sul device + undo → torna alla sorgente ------------------------
MOUT=$(py -c "
import sys, json
import agent_runtime
from loader import load_catalog
ex = next(e for e in load_catalog() if e.name=='move_files')
obs = agent_runtime.invoke_executor(
    ex, {'entries':[{'src': sys.argv[1]+'/srcdir/doc.txt'}], 'dst_template': sys.argv[1]+'/dstdir/doc.txt', 'client':'local'},
    timeout_s=40, turn_id='turn-c7m-2', actor='host', channel='e2e',
    target_device='c7m-laptop')
print(json.dumps({'ok': obs.get('ok'), 'dev': obs.get('_ran_on_device')}))
" "$DEVTREE")
echo "    move: $MOUT"
[ -f "$DEVTREE/dstdir/doc.txt" ] && [ ! -f "$DEVTREE/srcdir/doc.txt" ] \
  && pass "move_files eseguito sul device (src→dst)" || fail "move non avvenuto"

UOUT2=$(py -c "
import sys, json
sys.path.insert(0, '$REPO/executors/undo_last_turn')
import undo_last_turn as ult
out = ult.invoke({})
print(json.dumps({'ok': out.get('ok'), 'undone': out.get('undone_count')}))
")
echo "    undo-move: $UOUT2"
[ -f "$DEVTREE/srcdir/doc.txt" ] && [ ! -f "$DEVTREE/dstdir/doc.txt" ] \
  && pass "undo del move: file TORNATO alla sorgente sul device" || fail "undo move fallito"

# ---- 4) Gate: delete_files con target device gira LOCALE (no device_ok) ----
GOUT=$(py -c "
import sys, json, tempfile, pathlib
import agent_runtime
from loader import load_catalog
tmpf = pathlib.Path(tempfile.mkdtemp())/'x.txt'; tmpf.write_text('x')
ex = next(e for e in load_catalog() if e.name=='delete_files')
obs = agent_runtime.invoke_executor(
    ex, {'paths':[str(tmpf)], 'client':'local'},
    timeout_s=40, turn_id='turn-c7m-3', actor='host', channel='e2e',
    target_device='c7m-laptop')
print(json.dumps({'ok': obs.get('ok'), 'dev': obs.get('_ran_on_device', None)}))
")
echo "    delete-gate: $GOUT"
echo "$GOUT" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d['ok'] and not d['dev'] else 1)" \
  && pass "delete_files con target device → LOCALE (gate placement, blob non remotabile)" || fail "gate delete violato"

echo
if [ "$FAILED" -eq 0 ]; then printf '\033[32m==> C7 MUTANTI sul device + UNDO round-trip: VALIDATO\033[0m\n'; else printf '\033[31m==> C7 mutanti: FALLITO\033[0m\n'; fi
exit "$FAILED"
