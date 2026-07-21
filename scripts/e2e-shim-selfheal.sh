#!/usr/bin/env bash
# e2e-shim-selfheal.sh — valida l'auto-guarigione dello shim (runner.rs).
#
# BUG (C7, 2026-07-04): il client scarica lo shim UNA volta per processo e lo
# memoizza. Se un modulo runtime viene aggiunto al bundle server DOPO l'avvio
# del client, il client gia' in esecuzione non lo vedrebbe mai (list_dirs sul
# PC falliva con `ModuleNotFoundError: path_alias`). FIX: su output non-JSON con
# import fallito, il client rigenera lo shim e riprova UNA volta.
#
# Riproduzione FEDELE: la memoizzazione e' per-processo, quindi lo shim va
# corrotto MENTRE LO STESSO client e' vivo, fra due invocazioni:
#   1. inv A (find_packages) -> il client fetcha+memoizza lo shim completo;
#   2. rimuovo executor_helpers.py dalla cache shim (simula shim stantio,
#      scaricato prima che il modulo esistesse) — il client NON lo rifetcha da
#      solo (memoizzato);
#   3. inv B (find_packages, STESSO client vivo) -> import fallisce -> il fix
#      rigenera lo shim dal server (completo) -> retry -> done.
# Con il codice VECCHIO la inv B resterebbe non-'done'.
#
# Isolato: XDG temporanei, devices.db temporaneo, porta alt. Non tocca la prod.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$REPO/runtime"
CLIENT_BIN="$REPO/client-rs/target/release/metnos-client"
PORT="${METNOS_E2E_PORT:-8798}"
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
SERVER_PID=""; CLIENT_PID=""; FAILED=0

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
    ( cd "$RUNTIME" && exec python3 agent_server.py --host 127.0.0.1 --port "$PORT" \
        >>"$TMP/server.log" 2>&1 ) &
    SERVER_PID=$!
    for _ in $(seq 1 50); do
        curl -fsS "$SERVER/agent/health" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "server non partito; log:"; cat "$TMP/server.log"; exit 1
}
run_client_bg() { "$CLIENT_BIN" run --server "$SERVER" >>"$TMP/client.log" 2>&1 & CLIENT_PID=$!; }
stop_client() { [ -n "$CLIENT_PID" ] && kill "$CLIENT_PID" 2>/dev/null; wait "$CLIENT_PID" 2>/dev/null; CLIENT_PID=""; }

enqueue() {
    py -c "import invocations,sys,json
print(invocations.enqueue_invocation(sys.argv[1], sys.argv[2], json.loads(sys.argv[3])))" "$1" "$2" "$3"
}
inv_state() { py -c "import invocations,sys
i=invocations.get_invocation(sys.argv[1]); print(i['state'] if i else 'MISSING')" "$1"; }
inv_json() { py -c "import invocations,sys,json
print(json.dumps(invocations.get_invocation(sys.argv[1])))" "$1"; }
wait_state() {
    local end=$(( SECONDS + $3 ))
    while [ "$SECONDS" -lt "$end" ]; do
        [ "$(inv_state "$1")" = "$2" ] && return 0
        sleep 0.5
    done
    return 1
}

# ---------------------------------------------------------------------------
[ -x "$CLIENT_BIN" ] || { echo "client non buildato: $CLIENT_BIN (cargo build --release)"; exit 1; }
pkill -f "agent_server.py --host 127.0.0.1 --port $PORT" 2>/dev/null || true
sleep 0.5
info "client: $CLIENT_BIN"
info "server isolato su $SERVER"
start_server

# --- pairing ----------------------------------------------------------------
info "pairing"
TOKEN="$(py devices.py generate-token e2e-heal)"
"$CLIENT_BIN" register --server "$SERVER" --token "$TOKEN" >"$TMP/register.log" 2>&1 \
    && pass "register OK" || { fail "register"; cat "$TMP/register.log"; exit 1; }
DEVICE_ID="$(py -c "import json;print(json.load(open('$STATE_JSON'))['device_id'])")"
[ -n "$DEVICE_ID" ] && pass "device_id=$DEVICE_ID" || { fail "device_id assente"; exit 1; }

# --- passo 1: primo execute → il client fetcha+memoizza lo shim -------------
info "passo 1 — primo execute: il client fetcha e MEMOIZZA lo shim (processo vivo)"
run_client_bg
INV_A="$(enqueue "$DEVICE_ID" find_packages '{"package_name":"sh"}')"
if wait_state "$INV_A" done 40; then
    pass "inv A completata (shim memoizzato nel processo client vivo)"
else
    fail "inv A non completata (state=$(inv_state "$INV_A"))"; tail -20 "$TMP/client.log"; exit 1
fi

# --- passo 2: corrompo lo shim MENTRE il client è vivo ----------------------
info "passo 2 — rendo lo shim stantio: rimuovo executor_helpers.py dalla cache"
SHIM_DIR="$(find "$XDG_CACHE_HOME" -type d -name shim 2>/dev/null | head -1)"
SHIM_MOD="$SHIM_DIR/executor_helpers.py"
if [ -n "$SHIM_DIR" ] && [ -f "$SHIM_MOD" ]; then
    rm -f "$SHIM_MOD"
    [ ! -f "$SHIM_MOD" ] && pass "executor_helpers.py rimosso ($SHIM_DIR)" || fail "rm non effettivo"
else
    fail "shim cache non trovato (SHIM_DIR=$SHIM_DIR)"; exit 1
fi

# --- passo 3: seconda invocazione, STESSO client → auto-guarigione ----------
info "passo 3 — seconda invocazione (stesso client memoizzato): deve auto-guarire"
INV_B="$(enqueue "$DEVICE_ID" find_packages '{"package_name":"env"}')"
if wait_state "$INV_B" done 40; then
    R="$(inv_json "$INV_B")"
    echo "$R" | py -c "import sys,json;r=json.load(sys.stdin)['result'];assert r['ok'] is True,r;print('    ok=True dopo heal')" \
        && pass "inv B completata ok=True DOPO auto-guarigione" || fail "result non ok dopo heal"
else
    fail "inv B non completata dopo heal (state=$(inv_state "$INV_B"))"; tail -25 "$TMP/client.log"
fi
# prove ANTI-GAMING: il refetch è realmente avvenuto (log) + il modulo è tornato
grep -qi "shim stantio rigenerato\|modulo shim mancante" "$TMP/client.log" \
    && pass "log conferma refetch+retry dello shim (heal path esercitato)" \
    || fail "nessuna traccia di auto-guarigione nel log — l'inv B non è passata dal heal"
[ -f "$SHIM_MOD" ] && pass "executor_helpers.py ripristinato dal refetch" \
    || fail "modulo shim non ripristinato dopo il refetch"
stop_client

echo
if [ "$FAILED" -eq 0 ]; then
    printf '\033[32m==> E2E shim self-heal: VERDE\033[0m\n'
else
    printf '\033[31m==> E2E shim self-heal: FALLITO\033[0m\n'
    echo "--- client.log (coda) ---"; tail -30 "$TMP/client.log"
fi
exit "$FAILED"
