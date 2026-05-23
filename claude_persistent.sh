#!/usr/bin/env bash
# Claude Code — sessione persistente in tmux
#
# Uso locale:   bash /opt/metnos/claude_persistent.sh
# Uso da SSH:   ssh -t roberto@192.168.1.33 bash /opt/metnos/claude_persistent.sh
#
# Se la sessione tmux "myclaw" esiste, si riconnette.
# Se non esiste, la crea, applica tuning anti-lag, e avvia claude --resume.
set -euo pipefail

SESSION="metnos"
CLAUDE_INIT_DELAY="${CLAUDE_INIT_DELAY:-4}"
CLAUDE_SESSION_NAME="${CLAUDE_SESSION_NAME:-METNOS}"
CLAUDE_COLOR="${CLAUDE_COLOR:-blu}"

# Tuning anti-lag su SSH. Ridotto scrollback (default tmux 2000 righe
# diventano ~4 MB di buffer per pane con output colorato di Claude Code;
# su link lenti ogni redraw rispedisce quei MB). aggressive-resize evita
# redraw inutili quando più client sono attaccati.
apply_tuning() {
    tmux set-option -t "$SESSION" -g history-limit 1000 >/dev/null 2>&1 || true
    tmux set-window-option -t "$SESSION" -g aggressive-resize on >/dev/null 2>&1 || true
    tmux set-option -sg escape-time 10 >/dev/null 2>&1 || true
}

# Imposta nome sessione + colore Claude Code via slash command. Invocata
# SOLO al primo avvio (nuova sessione tmux). I successivi --resume
# preservano il rename gia' applicato dalla volta precedente.
apply_claude_session_settings() {
    sleep "$CLAUDE_INIT_DELAY"
    tmux send-keys -t "$SESSION" "/rename $CLAUDE_SESSION_NAME" Enter
    sleep 1
    tmux send-keys -t "$SESSION" "/color $CLAUDE_COLOR" Enter
}

# Guard: se siamo già dentro tmux, switch invece di attach (evita nesting)
if [ -n "${TMUX:-}" ]; then
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        apply_tuning
        exec tmux switch-client -t "$SESSION"
    else
        tmux new-session -d -s "$SESSION" -c /opt/metnos
        apply_tuning
        tmux send-keys -t "$SESSION" 'claude --resume' Enter
        apply_claude_session_settings
        exec tmux switch-client -t "$SESSION"
    fi
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
    apply_tuning
    echo "Sessione '$SESSION' trovata, riconnessione..."
    exec tmux attach -t "$SESSION"
fi

echo "Creo nuova sessione '$SESSION'..."
tmux new-session -d -s "$SESSION" -c /opt/metnos
apply_tuning
tmux send-keys -t "$SESSION" 'claude --resume' Enter
apply_claude_session_settings
exec tmux attach -t "$SESSION"
