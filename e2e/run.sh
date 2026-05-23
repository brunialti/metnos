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
set -euo pipefail

cd "$(dirname "$0")"

# Pre-flight: corpus DB esiste? Altrimenti estrai dal live (rigenerabile).
if [ ! -f corpus/corpus.sqlite ]; then
    echo "corpus.sqlite missing, extracting from live logs..."
    python3 corpus/extract.py
fi

REPORT_DIR="reports/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$REPORT_DIR"

# Parse args
LOOP=0
SLOW=0
VERIFY=0
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
        *)
            PYTEST_ARGS+=("$arg")
            ;;
    esac
done

# Default target = tutta la suite
if [ ${#PYTEST_ARGS[@]} -eq 0 ]; then
    PYTEST_ARGS=("scenarios/")
fi

echo "Running pytest ${PYTEST_ARGS[*]}"
echo "Judge LLM: ${METNOS_E2E_LLM_JUDGE:-1} (1=on default, 0=lint only)"
echo "Slow chat tier: ${SLOW:-0}"
echo "Loop until pass: ${LOOP:-0}"
echo "Report dir: $REPORT_DIR"
echo

# Cleanup tmp before each run
cleanup() {
    rm -rf tmp/*
}

run_once() {
    cleanup
    python3 -m pytest "${PYTEST_ARGS[@]}" \
        -v \
        --tb=short \
        --color=yes \
        -o junit_family=xunit2 \
        --junit-xml="$REPORT_DIR/junit.xml" \
        2>&1 | tee "$REPORT_DIR/log.txt"
    return ${PIPESTATUS[0]}
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
        return 0
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
    fi
    exit $rc
fi
