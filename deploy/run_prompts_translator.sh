#!/bin/bash
# Wrapper per metnos-prompts-translator.service.
#
# Logica deterministica (CLAUDE.md §7.9), pattern latest-wins simmetrico:
#   1. Layer 1 (prompts .j2): align_prompts() — qualsiasi lingua editata
#      diventa edit-source per le altre. Hash content (no mtime per detect).
#   2. Layer 2 (manifest [description]): align_manifest_descriptions()
#   3. Layer 3 (DB i18n.sqlite): align_messages() — marca needs_translation=1
#      su rows con source_text_hash divergente, poi run_one_cycle traduce.
#   4. Conta candidati in _pending/ e notifica via Telegram se > 0.
#
# Storia: lo step (1) precedente usava find -mtime -7 e mtime compare;
# fragile a touch/sed/edit fuori canale. Sostituito da align_prompts()
# che fa hash content compare (deterministico, robusto).

set -e
INSTALL_ROOT="${METNOS_INSTALL_ROOT:-/opt/metnos}"
PYTHON="${METNOS_VENV:-$INSTALL_ROOT/.venv}/bin/python"
RUNTIME="$INSTALL_ROOT/runtime"
PROMPTS=$RUNTIME/prompts
LOG=/var/log/metnos/prompts-translator.log
mkdir -p "$(dirname "$LOG")" || LOG=/tmp/metnos-prompts-translator.log

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

# ============================================================================
# Tier resolution priority (decrescente):
#   1. METNOS_TRANSLATOR_QUALITY env var (set in systemd unit o shell)
#   2. ~/.config/metnos/translator_tier.toml `[translator] tier`
#      (scritto da `metnos-prompts audit-quality --apply`)
#   3. default 'fidelity' (contratto locale ad alta fedelta')
# ============================================================================
TIER="${METNOS_TRANSLATOR_QUALITY:-}"
if [ -z "$TIER" ]; then
    CONF_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/metnos/translator_tier.toml"
    if [ -f "$CONF_FILE" ]; then
        # Estrazione deterministica via python (evita dipendenza tomlq/yq).
        TIER=$("$PYTHON" -c "
import sys, tomllib
try:
    with open('$CONF_FILE','rb') as f:
        d = tomllib.load(f)
    print(d.get('translator', {}).get('tier', '') or '')
except Exception:
    print('')
" 2>/dev/null || true)
    fi
fi
if [ -z "$TIER" ]; then
    TIER="fidelity"
fi

log "translator job start (tier=$TIER)"

# Lingue secondarie (escludi 'it' canonical, escludi _pending/)
langs=()
for d in "$PROMPTS"/*/; do
    name=$(basename "$d")
    [[ "$name" == "it" ]] && continue
    [[ "$name" == _* ]] && continue
    langs+=("$name")
done

if [ ${#langs[@]} -eq 0 ]; then
    log "no secondary langs found; nothing to do"
    exit 0
fi

cd "$RUNTIME"

# ============================================================================
# Layer 1: prompts .j2 — pattern latest-wins simmetrico (estensione ADR 0092).
# Sostituisce il vecchio find -mtime -7 (fragile a touch/sed). Hash content
# compare per detect edit; mtime usato solo per tie-break edit-source fra
# lang edite simultaneamente.
# ============================================================================
log "aligning prompts (Layer 1, latest-wins)"
align_prompts_tmp=$(mktemp)
if "$PYTHON" -m i18n_translator align-prompts --quality="$TIER" >>"$align_prompts_tmp" 2>&1; then
    cat "$align_prompts_tmp" >> "$LOG"
    log "prompts align: completed (see log)"
else
    cat "$align_prompts_tmp" >> "$LOG"
    log "prompts align: FAILED (see log)"
fi
rm -f "$align_prompts_tmp"
n_translated=0  # contabilizzato dentro align_prompts via candidates

# Conta candidati in _pending/
n_pending=0
for lang in "${langs[@]}"; do
    if [ -d "$PROMPTS/$lang/_pending" ]; then
        cnt=$(find "$PROMPTS/$lang/_pending" -maxdepth 1 -name '*.j2.candidate' | wc -l)
        n_pending=$((n_pending + cnt))
    fi
done

# ============================================================================
# Layer 2: manifest [description] — gia' implementato Phase 4 (ADR 0092).
# Pattern latest-wins simmetrico via align_manifest_descriptions.
# ============================================================================
log "aligning manifest descriptions (Layer 2)"
align_log_tmp=$(mktemp)
if "$PYTHON" -m i18n_translator align-manifests --quality="$TIER" >>"$align_log_tmp" 2>&1; then
    cat "$align_log_tmp" >> "$LOG"
    n_aligned=$(grep -c "aligned:" "$align_log_tmp" 2>/dev/null || echo 0)
    log "manifest descriptions align: completed (see log)"
else
    cat "$align_log_tmp" >> "$LOG"
    log "manifest descriptions align: FAILED (see log for details)"
fi
rm -f "$align_log_tmp"

# ============================================================================
# Layer 3: DB i18n.sqlite — pattern latest-wins simmetrico (estensione ADR 0092).
# align_messages() marca needs_translation=1 sulle row con source_text_hash
# divergente dalla edit-source; run_one_cycle() processa la coda.
# ============================================================================
log "aligning DB i18n.sqlite messages (Layer 3, latest-wins)"
align_messages_tmp=$(mktemp)
if "$PYTHON" -m i18n_translator align-messages --quality="$TIER" >>"$align_messages_tmp" 2>&1; then
    cat "$align_messages_tmp" >> "$LOG"
    log "messages align: completed (see log)"
else
    cat "$align_messages_tmp" >> "$LOG"
    log "messages align: FAILED (see log)"
fi
rm -f "$align_messages_tmp"

# Translator daemon esistente: traduce le row marcate needs_translation=1
# dal Layer 3 align_messages sopra.
log "running run_one_cycle (translate pending DB rows)"
"$PYTHON" -m i18n_translator --once >>"$LOG" 2>&1 || log "run_one_cycle FAILED (see log)"

log "translator job end: pending_review=$n_pending"

# Notifica Telegram se ci sono candidati pending
if [ "$n_pending" -gt 0 ]; then
    msg="$n_pending prompt translations pending review (run: metnos-prompts sync-status)"
    log "  notifying host: $msg"
    "$PYTHON" -c "
import sys
sys.path.insert(0, '/opt/metnos/runtime')
try:
    from notifier import notify_host
    notify_host('${msg}')
except Exception as e:
    print(f'notify failed: {e}', file=sys.stderr)
" >>"$LOG" 2>&1 || true
fi

exit 0
