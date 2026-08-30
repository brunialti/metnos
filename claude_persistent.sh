#!/usr/bin/env bash
# Claude Code — sessione persistente in tmux (SOLO Claude)
#
# Uso locale:   bash <repo>/claude_persistent.sh
# Uso da SSH:   ssh -t roberto@192.168.1.33 bash <repo>/claude_persistent.sh
#
# Questo script possiede UNA sessione tmux dedicata a Claude su UNA cartella
# di lavoro. Se la sessione esiste e Claude ci gira, si riconnette; se esiste
# ma Claude non c'e' piu' (uscita o crash), lo fa ripartire; se non esiste,
# la crea. Si ferma invece, senza attaccarsi, quando:
#   - nella sessione gira Codex (quella sessione e' di codex_persistent.sh);
#   - Claude gira gia' su questa cartella in un'altra sessione tmux, o due
#     volte nella stessa: due istanze sullo stesso repository si pestano i
#     piedi, e chi si attacca non ha modo di accorgersene.
#
# Lo script vale per qualunque repository: nome sessione, nome finestra,
# colore e cartella di lavoro si ricavano dalla sua posizione.
#
# NOTA sui target tmux. Un `-t nome` senza `=` e' un target di FINESTRA e fa
# match di prefisso: `-t metnos` trova la sessione `metnos-codex`, e
# `-t claude` trova la finestra chiamata `claude` di un'altra sessione. E'
# cosi' che i due ruoli si erano mescolati. Qui ogni target e' esatto: `=`
# per la sessione, `:` quando serve indicarla come sessione.
set -euo pipefail

AGENT="claude"
OTHER_AGENT="codex"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
OTHER_SCRIPT="$SCRIPT_DIR/codex_persistent.sh"
WORKDIR="${CLAUDE_WORKDIR:-$SCRIPT_DIR}"

# Identita' derivata dalla cartella, non scritta a mano: l'ultimo livello del
# percorso piu' il nome dell'agente. Cosi' lo script si puo' copiare in un
# altro repository senza toccare nulla, e due repository non possono
# rivendicare lo stesso nome.
PROJECT="$(basename "$WORKDIR")"
SESSION="${CLAUDE_TMUX_SESSION:-${PROJECT}-${AGENT}}"
CLAUDE_INIT_DELAY="${CLAUDE_INIT_DELAY:-6}"
CLAUDE_SESSION_NAME="${CLAUDE_SESSION_NAME:-$(printf '%s-%s' "$PROJECT" "$AGENT" | tr '[:lower:]' '[:upper:]')}"

# Il colore dice l'AGENTE, non il progetto: uguale in ogni repository, cosi'
# a colpo d'occhio si sa se si sta guardando Claude o Codex. Il progetto lo
# dicono gia' nome della finestra e barra di stato. `/color` accetta i nomi
# di Claude Code, tmux i propri: sono la stessa tinta scritta in due lingue.
CLAUDE_COLOR="${CLAUDE_COLOR:-blue}"
SESSION_COLOR="${CLAUDE_TMUX_COLOR:-blue}"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        printf 'Errore: comando richiesto non trovato: %s\n' "$1" >&2
        exit 1
    fi
}

# Tuning anti-lag su SSH. Ridotto scrollback (default tmux 2000 righe
# diventano ~4 MB di buffer per pane con output colorato di Claude Code;
# su link lenti ogni redraw rispedisce quei MB). aggressive-resize evita
# redraw inutili quando più client sono attaccati.
apply_tuning() {
    tmux set-option -t "=$SESSION:" -g history-limit 1000 >/dev/null 2>&1 || true
    tmux set-window-option -t "=$SESSION:" -g aggressive-resize on >/dev/null 2>&1 || true
    tmux set-option -sg escape-time 10 >/dev/null 2>&1 || true
}

# Nome e colore della sessione tmux. Si riapplicano ad ogni avvio: sono
# derivati, quindi riscriverli non puo' che riportare l'identita' voluta.
apply_identity() {
    tmux rename-window -t "=$SESSION:" "$CLAUDE_SESSION_NAME" >/dev/null 2>&1 || true
    tmux set-option -t "=$SESSION" status-style "fg=black,bg=$SESSION_COLOR" >/dev/null 2>&1 || true
    tmux set-option -t "=$SESSION" status-left "[$CLAUDE_SESSION_NAME] " >/dev/null 2>&1 || true
}

session_exists() {
    tmux has-session -t "=$SESSION" 2>/dev/null
}

# Censisce gli agenti CLI vivi in TUTTE le sessioni tmux: una riga
# "sessione<TAB>agente<TAB>pid<TAB>cartella" per ognuno.
#
# Si guardano SOLO i figli diretti del pannello: e' li' che vive la CLI, e
# cosi' un transitorio `bash claude_persistent.sh` non viene contato come
# Claude. L'eventuale interprete iniziale (node e simili) viene scartato,
# quindi conta il nome del programma e non la riga di comando. La cartella
# e' quella reale del processo, non quella del pannello: e' l'unica che dice
# su quale repository sta lavorando l'agente.
agent_inventory() {
    local session pane_pid child token cwd
    local -a argv
    while IFS= read -r session; do
        [ -n "$session" ] || continue
        while IFS= read -r pane_pid; do
            [ -n "$pane_pid" ] || continue
            for child in $(ps --ppid "$pane_pid" -o pid= 2>/dev/null); do
                mapfile -t -d '' argv < "/proc/$child/cmdline" 2>/dev/null || continue
                token="${argv[0]##*/}"
                case "$token" in
                    node|bun|deno|python|python3)
                        token="${argv[1]:-}"; token="${token##*/}" ;;
                esac
                case "$token" in
                    claude|codex) ;;
                    *) continue ;;
                esac
                cwd="$(readlink -f "/proc/$child/cwd" 2>/dev/null || printf '?')"
                printf '%s\t%s\t%s\t%s\n' "$session" "$token" "$child" "$cwd"
            done
        done < <(tmux list-panes -s -t "=$session:" -F '#{pane_pid}' 2>/dev/null)
    done < <(tmux list-sessions -F '#{session_name}' 2>/dev/null)
}

# Righe del censimento filtrate per agente, e opzionalmente per sessione.
inventory_rows() {
    local want_agent="$1" want_session="${2:-}"
    agent_inventory | awk -F'\t' -v a="$want_agent" -v s="$want_session" \
        '$2 == a && (s == "" || $1 == s)'
}

# La sessione deve ospitare questo agente e nessun altro.
require_disjoint_session() {
    if [ -n "$(inventory_rows "$OTHER_AGENT" "$SESSION")" ]; then
        printf 'Errore: nella sessione tmux "%s" gira %s.\n' "$SESSION" "$OTHER_AGENT" >&2
        printf 'Questo script apre solo %s. Usa %s, oppure chiudi %s in quella sessione.\n' \
            "$AGENT" "$OTHER_SCRIPT" "$OTHER_AGENT" >&2
        exit 1
    fi
}

# Questo agente deve girare al piu' una volta su questa cartella di lavoro.
# Un agente sulla stessa cartella in un'altra sessione e' un doppione; lo
# stesso agente in un altro repository non lo e' e non viene toccato.
require_single_instance() {
    local rows elsewhere
    rows="$(inventory_rows "$AGENT" | awk -F'\t' -v w="$WORKDIR" '$4 == w')"
    [ -n "$rows" ] || return 0
    elsewhere="$(printf '%s\n' "$rows" | awk -F'\t' -v s="$SESSION" '$1 != s')"
    if [ -z "$elsewhere" ] && [ "$(printf '%s\n' "$rows" | wc -l)" -le 1 ]; then
        return 0
    fi
    printf 'Errore: %s risulta gia in esecuzione su %s:\n' "$AGENT" "$WORKDIR" >&2
    printf '%s\n' "$rows" | awk -F'\t' '{printf "  sessione %s, pid %s\n", $1, $3}' >&2
    printf 'Riconnettiti a quella sessione (tmux attach -t "=<sessione>"), oppure chiudila prima di riprovare.\n' >&2
    exit 1
}

agent_is_running() {
    [ -n "$(inventory_rows "$AGENT" "$SESSION")" ]
}

# Riprende la sessione Claude precedente; `--resume` copre anche il primo
# avvio, che parte pulito quando non c'e' nulla da riprendere.
start_agent() {
    tmux send-keys -t "=$SESSION:" 'claude --resume' Enter
}

# Nome e colore DENTRO Claude Code, via slash command. Va in secondo piano e
# staccato: il ritardo serve al picker di --resume per stabilizzarsi, e
# aspettarlo in primo piano ritarderebbe di altrettanto l'attach. Invocata
# SOLO quando la sessione tmux viene creata: i --resume successivi trovano
# gia' applicato quanto fatto la volta prima.
apply_claude_session_settings() {
    (
        sleep "$CLAUDE_INIT_DELAY"
        tmux send-keys -t "=$SESSION:" "/rename $CLAUDE_SESSION_NAME" Enter
        sleep 1
        tmux send-keys -t "=$SESSION:" "/color $CLAUDE_COLOR" Enter
    ) >/dev/null 2>&1 &
    disown 2>/dev/null || true
}

# Porta la sessione allo stato voluto: esiste, e dentro gira un solo Claude.
ensure_session() {
    if session_exists; then
        require_disjoint_session
        require_single_instance
        apply_tuning
        apply_identity
        if agent_is_running; then
            echo "Sessione '$SESSION' trovata con $AGENT attivo, riconnessione..."
        else
            echo "Sessione '$SESSION' trovata senza $AGENT, lo riavvio..."
            start_agent
        fi
        return
    fi
    require_single_instance
    echo "Creo nuova sessione '$SESSION' per $AGENT..."
    tmux new-session -d -s "$SESSION" -c "$WORKDIR"
    apply_tuning
    apply_identity
    start_agent
    apply_claude_session_settings
}

require_command tmux
require_command claude

ensure_session

# Se siamo già dentro tmux, switch invece di attach (evita nesting).
if [ -n "${TMUX:-}" ]; then
    exec tmux switch-client -t "=$SESSION"
fi
exec tmux attach -t "=$SESSION"
