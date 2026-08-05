#!/usr/bin/env bash
# Codex CLI — sessione persistente in tmux
#
# Uso locale:   bash /opt/metnos/codex_persistent.sh
# Uso da SSH:   ssh -t roberto@192.168.1.33 bash /opt/metnos/codex_persistent.sh
#
# Se la sessione tmux esiste, si riconnette. Se non esiste, prova a riprendere
# l'ultima sessione Codex del repository e, se non disponibile, ne avvia una.
# L'istanza remota deve poter eseguire E2E HTTP/sidecar sul Beelink.
# Il profilo puo' essere ridotto impostando CODEX_SANDBOX nell'ambiente.
set -euo pipefail

SESSION="${CODEX_TMUX_SESSION:-metnos-codex}"
WORKDIR="${CODEX_WORKDIR:-/opt/metnos}"
CODEX_SANDBOX="${CODEX_SANDBOX:-danger-full-access}"
CODEX_APPROVAL="${CODEX_APPROVAL_POLICY:-never}"
CODEX_BYPASS="${CODEX_BYPASS_SANDBOX:-1}"

apply_tuning() {
    tmux set-option -t "$SESSION" -g history-limit 1000 >/dev/null 2>&1 || true
    tmux set-window-option -t "$SESSION" -g aggressive-resize on >/dev/null 2>&1 || true
    tmux set-option -sg escape-time 10 >/dev/null 2>&1 || true
}

codex_new_command() {
    if [[ "$CODEX_BYPASS" == "1" ]]; then
        printf -v CODEX_NEW '%q ' codex -C "$WORKDIR" --dangerously-bypass-approvals-and-sandbox
    else
        printf -v CODEX_NEW '%q ' codex -C "$WORKDIR" -s "$CODEX_SANDBOX" -a "$CODEX_APPROVAL"
    fi
    printf '%s' "${CODEX_NEW% }"
}

codex_resume_command() {
    if [[ "$CODEX_BYPASS" == "1" ]]; then
        printf -v CODEX_RESUME '%q ' codex resume --last -C "$WORKDIR" --dangerously-bypass-approvals-and-sandbox
    else
        printf -v CODEX_RESUME '%q ' codex resume --last -C "$WORKDIR" -s "$CODEX_SANDBOX" -a "$CODEX_APPROVAL"
    fi
    printf '%s' "${CODEX_RESUME% }"
}

start_codex() {
    local resume_cmd new_cmd
    resume_cmd="$(codex_resume_command)"
    new_cmd="$(codex_new_command)"
    # --last evita il picker; la fallback copre il primo avvio senza sessioni.
    tmux send-keys -t "$SESSION" "${resume_cmd} || ${new_cmd}" Enter
}

if [ -n "${TMUX:-}" ]; then
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        apply_tuning
        exec tmux switch-client -t "$SESSION"
    fi
    tmux new-session -d -s "$SESSION" -c "$WORKDIR"
    apply_tuning
    start_codex
    exec tmux switch-client -t "$SESSION"
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
    apply_tuning
    echo "Sessione '$SESSION' trovata, riconnessione..."
    exec tmux attach -t "$SESSION"
fi

echo "Creo nuova sessione '$SESSION'..."
tmux new-session -d -s "$SESSION" -c "$WORKDIR"
apply_tuning
start_codex
exec tmux attach -t "$SESSION"
