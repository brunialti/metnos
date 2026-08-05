#!/usr/bin/env bash
# Metnos E2E runner — convenience entry point.
#
# Usage:
#   ./run.sh                              # fast suite (lifecycle + import)
#   ./run.sh --slow                       # include chat_quality (~15min)
#   METNOS_E2E_LLM_JUDGE=0 ./run.sh       # lint only (no LLM judge)
#   ./run.sh scenarios/test_skill_import.py  # singolo file
#
# Convergence loop:
#   ./run.sh --loop          # itera 'cleanup + run' finche' tutti pass
#
# Stochastic verification:
#   ./run.sh --loop --verify-stochastic
#     Dopo convergenza (0 fail), esegue un ulteriore run di verifica.
#     Se anche il secondo passa → convergenza vera; se il secondo fail
#     → era convergenza stocastica (LLM nondeterministico, retry).
#
# Quality gate:
#   ./run.sh --quality-gate
#     Esegue tutta la suite slow con judge attivo e richiede due run puliti
#     consecutivi sulla stessa matrice. Soglie: quality_targets.json.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
METNOS_PYTHON="${METNOS_VENV:-${REPO_ROOT}/.venv}/bin/python"
if [ ! -x "$METNOS_PYTHON" ]; then
    echo "Metnos Python environment not found: $METNOS_PYTHON" >&2
    exit 2
fi
cd "$SCRIPT_DIR"

REPORT_DIR="reports/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$REPORT_DIR"

# Parse args
LOOP=0
SLOW=0
VERIFY=0
QUALITY=0
RUN_INDEX=0
JUNIT_FILES=()
PYTEST_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --loop)
            LOOP=1
            ;;
        --slow)
            SLOW=1
            export METNOS_E2E_RUN_SLOW=1
            ;;
        --verify-stochastic)
            VERIFY=1
            ;;
        --quality-gate)
            QUALITY=1
            VERIFY=1
            SLOW=1
            export METNOS_E2E_RUN_SLOW=1
            export METNOS_E2E_LLM_JUDGE=1
            ;;
        *)
            PYTEST_ARGS+=("$arg")
            ;;
    esac
done

# Il gate usa uno snapshot fresco e immutabile per entrambe le esecuzioni.
# I run ordinari conservano lo snapshot esistente per restare rapidi.
if [ "$QUALITY" -eq 1 ] || [ ! -f corpus/corpus.sqlite ]; then
    echo "Refreshing corpus snapshot from live logs..."
    "$METNOS_PYTHON" corpus/extract.py
fi

# Default target = tutta la suite
if [ ${#PYTEST_ARGS[@]} -eq 0 ]; then
    PYTEST_ARGS=("scenarios/")
fi

echo "Running pytest ${PYTEST_ARGS[*]}"
echo "Judge LLM: ${METNOS_E2E_LLM_JUDGE:-1} (1=on default, 0=lint only)"
echo "Slow chat tier: ${SLOW:-0}"
echo "Loop until pass: ${LOOP:-0}"
echo "Quality gate: ${QUALITY:-0}"
echo "Report dir: $REPORT_DIR"
echo

# Cleanup tmp before each run
cleanup() {
    rm -rf tmp/*
}

run_once() {
    cleanup
    RUN_INDEX=$((RUN_INDEX + 1))
    local junit="$REPORT_DIR/junit-run-${RUN_INDEX}.xml"
    local log="$REPORT_DIR/log-run-${RUN_INDEX}.txt"
    JUNIT_FILES+=("$junit")
    set +e
    "$METNOS_PYTHON" -m pytest "${PYTEST_ARGS[@]}" \
        -v \
        --tb=short \
        --color=yes \
        -o junit_family=xunit2 \
        --junit-xml="$junit" \
        2>&1 | tee "$log"
    local rc=${PIPESTATUS[0]}
    set -e
    local quality_args=()
    for report in "${JUNIT_FILES[@]}"; do
        quality_args+=(--junit "$report")
    done
    "$METNOS_PYTHON" quality_gate.py "${quality_args[@]}" \
        --output "$REPORT_DIR/quality-summary.json" >/dev/null || true
    return "$rc"
}

enforce_quality() {
    if [ "$QUALITY" -ne 1 ]; then
        return 0
    fi
    local quality_args=()
    for report in "${JUNIT_FILES[@]}"; do
        quality_args+=(--junit "$report")
    done
    "$METNOS_PYTHON" quality_gate.py "${quality_args[@]}" \
        --output "$REPORT_DIR/quality-summary.json" --enforce
}

verify_stochastic() {
    if [ "$VERIFY" -ne 1 ]; then
        return 0
    fi
    echo
    echo "=== Stochastic verification run ==="
    echo "    First run: 0 fail. Re-running to exclude LLM stochasticity..."
    if run_once; then
        echo "✓ Converged (verified deterministic across 2 runs)"
        enforce_quality
        return $?
    fi
    echo "✗ Stochastic divergence: 2° run fail dopo 1° pass."
    echo "  Probabile LLM nondeterministico o race condition test."
    return 1
}

if [ "$LOOP" -eq 1 ]; then
    ITER=0
    MAX_ITER=10
    while [ $ITER -lt $MAX_ITER ]; do
        ITER=$((ITER + 1))
        echo
        echo "=== Iteration $ITER/$MAX_ITER ==="
        if run_once; then
            echo "Converged at iteration $ITER"
            if verify_stochastic; then
                exit 0
            fi
            echo "Re-loop dopo stochastic divergence"
            continue
        fi
        echo "Iter $ITER failed, retry..."
    done
    echo "Failed to converge after $MAX_ITER iterations"
    exit 1
else
    run_once
    rc=$?
    if [ $rc -eq 0 ]; then
        verify_stochastic || exit 1
        enforce_quality || exit 1
    fi
    exit $rc
fi
