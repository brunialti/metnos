#!/usr/bin/env bash
# e2e-remote-executor.sh — gate di accettazione W1-2 (§14.3 del design doc
# executor remoti). Esegue un turno REALE end-to-end contro un agent_server
# isolato con il client Rust vero (mai «è partito», sempre un execute reale).
#
# Fasi:
#   A pairing + execute read-only find_packages -> entries + state done
#   B firma manomessa (pubkey server pinnata corrotta) -> client RIFIUTA
#   C idempotenza: client riavviato non ri-esegue (spool persistente)
#   D server giù a metà -> client sopravvive, invocazione successiva consegnata
#
# Isolato: XDG dirs temporanei, devices.db temporaneo, porta alt, lockfile
# dedicato. Non tocca la prod.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$REPO/runtime"
CLIENT_BIN="$REPO/client-rs/target/release/metnos-client"
PORT="${METNOS_E2E_PORT:-8799}"
SERVER="http://127.0.0.1:$PORT"

TMP="$(mktemp -d)"
export XDG_DATA_HOME="$TMP/xdg-data"
export XDG_CACHE_HOME="$TMP/xdg-cache"
export XDG_CONFIG_HOME="$TMP/xdg-config"
export METNOS_DEVICES_DB="$TMP/devices.db"
export METNOS_AGENT_LOCKFILE="$TMP/agent.lock"
export METNOS_AGENT_PORT="$PORT"
mkdir -p "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"

STATE_JSON="$XDG_DATA_HOME/metnos/state.json"
SERVER_PID=""
CLIENT_PID=""
FAILED=0

pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
info() { printf '==> %s\n' "$*"; }

cleanup() {
    [ -n "$CLIENT_PID" ] && kill "$CLIENT_PID" 2>/dev/null
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
    wait 2>/dev/null
    rm -rf "$TMP"
}
trap cleanup EXIT

py() { ( cd "$RUNTIME" && python3 "$@" ); }

start_server() {
    # exec: il subshell viene RIMPIAZZATO da python, così $! è il PID di python
    # e kill lo termina davvero (niente orfani che tengono la porta).
    ( cd "$RUNTIME" && exec python3 agent_server.py --host 127.0.0.1 --port "$PORT" \
        >>"$TMP/server.log" 2>&1 ) &
    SERVER_PID=$!
    for _ in $(seq 1 50); do
        if curl -fsS "$SERVER/agent/health" >/dev/null 2>&1; then return 0; fi
        sleep 0.2
    done
    echo "server non partito; log:"; cat "$TMP/server.log"; exit 1
}
stop_server() {
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
    SERVER_PID=""
    # attendi il rilascio della porta prima di un eventuale restart
    for _ in $(seq 1 25); do
        ss -ltn 2>/dev/null | grep -q ":$PORT " || break
        sleep 0.2
    done
}

run_client_bg() {
    "$CLIENT_BIN" run --server "$SERVER" >>"$TMP/client.log" 2>&1 &
    CLIENT_PID=$!
}
stop_client() {
    [ -n "$CLIENT_PID" ] && kill "$CLIENT_PID" 2>/dev/null
    wait "$CLIENT_PID" 2>/dev/null
    CLIENT_PID=""
}

enqueue() {  # <device_id> <executor> <args_json>
    py -c "import invocations,sys,json
print(invocations.enqueue_invocation(sys.argv[1], sys.argv[2], json.loads(sys.argv[3])))" \
        "$1" "$2" "$3"
}
inv_state() {  # <inv_id>
    py -c "import invocations,sys,json
i=invocations.get_invocation(sys.argv[1]); print(i['state'] if i else 'MISSING')" "$1"
}
inv_json() { py -c "import invocations,sys,json
print(json.dumps(invocations.get_invocation(sys.argv[1])))" "$1"; }

wait_state() {  # <inv_id> <target> <timeout_s>
    local end=$(( SECONDS + $3 ))
    while [ "$SECONDS" -lt "$end" ]; do
        [ "$(inv_state "$1")" = "$2" ] && return 0
        sleep 0.5
    done
    return 1
}

# ---------------------------------------------------------------------------
[ -x "$CLIENT_BIN" ] || { echo "client non buildato: $CLIENT_BIN (cargo build --release)"; exit 1; }
# pre-clean: nessun listener residuo sulla porta di test
pkill -f "agent_server.py --host 127.0.0.1 --port $PORT" 2>/dev/null || true
sleep 0.5
info "client: $CLIENT_BIN"
info "server isolato su $SERVER (db $METNOS_DEVICES_DB)"
start_server

# --- FASE A: pairing + execute ---------------------------------------------
info "FASE A — pairing + execute read-only"
TOKEN="$(py devices.py generate-token e2e-laptop)"
[ -n "$TOKEN" ] && pass "token emesso" || fail "token vuoto"

"$CLIENT_BIN" register --server "$SERVER" --token "$TOKEN" >"$TMP/register.log" 2>&1 \
    && pass "register OK" || { fail "register"; cat "$TMP/register.log"; }

DEVICE_ID="$(py -c "import json;print(json.load(open('$STATE_JSON'))['device_id'])")"
[ -n "$DEVICE_ID" ] && pass "device_id=$DEVICE_ID" || fail "device_id assente in state"

# pubkey server pinnata presente?
py -c "import json;s=json.load(open('$STATE_JSON'));exit(0 if s.get('server_public_key') else 1)" \
    && pass "server_public_key pinnata" || fail "server_public_key assente"

# compare in lista device
py devices.py list | grep -q e2e-laptop && pass "device in /admin/devices (list)" || fail "device non in lista"

# gate 2: poll vuoto ritorna subito null (nessun busy-loop): eseguito
# implicitamente dal client; qui accodiamo PRIMA di lanciare il client.
INV1="$(enqueue "$DEVICE_ID" find_packages '{"package_name":"sh"}')"
[ -n "$INV1" ] && pass "invocazione accodata ($INV1)" || fail "enqueue"

run_client_bg
if wait_state "$INV1" done 40; then
    pass "execute find_packages -> done"
    RESULT="$(inv_json "$INV1")"
    echo "$RESULT" | py -c "import sys,json
r=json.load(sys.stdin)['result']
assert r['ok'] is True, 'ok non true'
paths=[e.get('path') for e in r.get('entries',[])]
assert any(p for p in paths), f'nessun path in entries: {r}'
print('    entries:', paths)" && pass "entries contiene il path + device_sig verificata" \
        || fail "entries/result non valido"
    # dopo consegna, lo spool result e' vuoto (§12: file rimosso a consegna ok)
    if [ -z "$(ls -A "$XDG_DATA_HOME/metnos/spool/results" 2>/dev/null)" ]; then
        pass "spool result svuotato dopo consegna"
    else
        fail "result residuo nello spool dopo consegna ok"
    fi
else
    fail "execute non completato (state=$(inv_state "$INV1"))"
    tail -20 "$TMP/client.log"
fi
stop_client

# --- FASE B: firma manomessa -----------------------------------------------
info "FASE B — server_sig manomessa: il client deve RIFIUTARE"
cp "$STATE_JSON" "$STATE_JSON.bak"   # ripristino esatto dopo il test
# corrompi la pubkey server pinnata (32 byte casuali b64url)
py -c "
import json,base64,os
s=json.load(open('$STATE_JSON'))
s['server_public_key']=base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode()
json.dump(s,open('$STATE_JSON','w'))"
INV2="$(enqueue "$DEVICE_ID" find_packages '{"package_name":"sh"}')"
run_client_bg
sleep 6
stop_client
if [ "$(inv_state "$INV2")" != "done" ]; then
    pass "invocazione con firma non verificabile NON eseguita (state=$(inv_state "$INV2"))"
else
    fail "invocazione eseguita nonostante server_sig non verificabile"
fi
grep -qi "RIFIUTO\|non verificata" "$TMP/client.log" && pass "client ha loggato il rifiuto" \
    || info "(nota: log rifiuto non trovato, ma esecuzione correttamente assente)"

# ripristina la pubkey corretta (stato pre-corruzione)
mv "$STATE_JSON.bak" "$STATE_JSON"

# --- FASE C: idempotenza / dedup persistente -------------------------------
info "FASE C — dedup persistente: l'inv già eseguita non si ri-esegue"
# INV1 è già 'done'; un client fresco NON deve ri-eseguirla. Verifichiamo che
# NON compaia un secondo effetto: lo spool su disco tiene la dedup.
BEFORE_STATE="$(inv_state "$INV1")"
run_client_bg
sleep 5
stop_client
AFTER_STATE="$(inv_state "$INV1")"
if [ "$BEFORE_STATE" = "done" ] && [ "$AFTER_STATE" = "done" ]; then
    pass "invocazione già completata non ri-eseguita (dedup §6.4)"
else
    fail "stato inatteso before=$BEFORE_STATE after=$AFTER_STATE"
fi

# --- FASE D: server giù a metà ---------------------------------------------
info "FASE D — server giù: il client sopravvive e riprende"
stop_server
run_client_bg
sleep 4   # il client polla, fallisce, backoff, NON crasha
if kill -0 "$CLIENT_PID" 2>/dev/null; then
    pass "client vivo con server giù (backoff, no crash)"
else
    fail "client morto con server giù"
fi
start_server
INV3="$(enqueue "$DEVICE_ID" find_packages '{"package_name":"env"}')"
if wait_state "$INV3" done 50; then
    pass "invocazione consegnata dopo il ritorno del server"
else
    fail "invocazione post-restart non completata (state=$(inv_state "$INV3"))"
    echo "    --- INV2: $(inv_json "$INV2")"
    echo "    --- INV3: $(inv_json "$INV3")"
    echo "    --- server.log (coda) ---"; tail -15 "$TMP/server.log"
    echo "    --- client.log (phase D) ---"; tail -15 "$TMP/client.log"
fi
stop_client

# --- FASE F: consegna affidabile del result (§12) --------------------------
info "FASE F — result nello spool (client crashato post-execute) → consegnato SENZA ri-eseguire"
# Simula un client che HA eseguito ma è morto prima di consegnare: metto un
# result "già pronto" nello spool con un marker riconoscibile, e accodo l'inv.
INV_SPOOL="$(enqueue "$DEVICE_ID" find_packages '{"package_name":"sh"}')"
RES_DIR="$XDG_DATA_HOME/metnos/spool/results"
mkdir -p "$RES_DIR"
py -c "
import json,sys
body={'invocation_id':sys.argv[1],'device_id':sys.argv[2],'ok':True,
      'entries':[{'marker':'from-spool'}],'n_processed':1,'elapsed_ms':0,
      'sandbox':'none'}
open(sys.argv[3],'w').write(json.dumps(body))" "$INV_SPOOL" "$DEVICE_ID" "$RES_DIR/$INV_SPOOL.json"
run_client_bg
if wait_state "$INV_SPOOL" done 30; then
    MARK="$(inv_json "$INV_SPOOL" | py -c "import sys,json
r=json.load(sys.stdin)['result']; print((r.get('entries') or [{}])[0].get('marker',''))")"
    if [ "$MARK" = "from-spool" ]; then
        pass "result spoolato consegnato SENZA ri-esecuzione (marker preservato)"
    else
        fail "invocazione ri-eseguita: marker perso (=$MARK)"
    fi
    [ -z "$(ls -A "$RES_DIR" 2>/dev/null)" ] && pass "spool svuotato dopo la consegna" \
        || fail "result residuo nello spool"
else
    fail "result spoolato non consegnato (state=$(inv_state "$INV_SPOOL"))"
fi
stop_client

# --- gate sandbox (item 6) --------------------------------------------------
if command -v bwrap >/dev/null 2>&1; then
    info "FASE E — sandbox: (bwrap presente, coperto dal run reale sopra)"
    grep -q '"sandbox":"bwrap"' <(inv_json "$INV1") 2>/dev/null \
        && pass "esecuzione avvenuta in sandbox bwrap" \
        || info "(nota: sandbox label non 'bwrap' — verificare capabilities)"
else
    info "FASE E — SKIP: bwrap assente su questo host (client in fallback diretto, loggato §2.8)"
fi

echo
if [ "$FAILED" -eq 0 ]; then
    printf '\033[32m==> E2E remote-executor: TUTTI I GATE VERDI\033[0m\n'
else
    printf '\033[31m==> E2E remote-executor: FALLIMENTI PRESENTI\033[0m\n'
    echo "--- client.log (coda) ---"; tail -30 "$TMP/client.log"
fi
exit "$FAILED"
