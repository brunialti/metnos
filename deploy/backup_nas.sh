#!/bin/bash
# =============================================================================
# Metnos — Backup giornaliero su NAS
#
# Pattern parallelo a giorgio2 (deploy/backup_nas.sh): mount NAS Asustor,
# rsync codice + dati runtime + secrets + service systemd, manifest, rotazione.
#
# Invocazione (da systemd timer):
#   /opt/metnos/deploy/backup_nas.sh
#
# Esecuzione manuale (debug):
#   sudo bash /opt/metnos/deploy/backup_nas.sh
#
# Sorgenti incluse:
#   - /opt/metnos/                  (codice + decisions + executors + manifest + CLAUDE.md)
#   - /home/roberto/.local/share/metnos/  (runtime data: scratchpad, undo, locations,
#                                          executors synth, vaglio, turns, index,
#                                          introvertiva, mirror, i18n.sqlite, ...)
#   - /home/roberto/.local/state/metnos/ (devices.db, executor_stats.db, lockfile)
#   - /home/roberto/.config/metnos/ (admin.key, credentials.env, mail.env, ...)
#   - /home/roberto/.claude/projects/-opt-myclaw/memory/ (memorie persistenti Claude)
#   - /home/roberto/.config/systemd/user/metnos-*.service
#   - /etc/systemd/system/metnos-*.service|timer            (incluso self-backup)
#   - /etc/fstab                                            (per mount NAS)
#
# Esclusioni (ridownloadabili o volatili):
#   - node_modules/             (deploy.sh deps Cloudflare, ricreato da npm)
#   - __pycache__/, *.pyc       (build Python)
#   - .venv*/                   (virtualenv, ricreati da pip install)
#   - Immagini/                 (CIFS MOUNT al NAS stesso — escludere è CRITICO,
#                                altrimenti rsync entra in loop ricorsivo NAS→NAS
#                                e riempie il filesystem)
#   - thumbcache/               (rigenerabili da photo_endpoint on-demand)
#   - _history/*/blob/          (blob backup undo, voluminoso, vita breve)
#   - cap_pending/              (state effimero)
#   - location_pending/         (state effimero)
#   - get_inputs/               (dialog pending state effimero)
#   - models/onnx/              (modelli ML scaricati separatamente)
#   - /home/roberto/models/*.gguf (LLM ~57GB, ridownloadabili da HuggingFace)
#
# turns/ (storia turni, ~40MB) e index/ (foto SigLIP, ~95MB) INCLUSI: sono
# stato di valore (audit + risultati build lunghi 50min CPU).
#
# Rotazione: mantenuti ultimi MAX_BACKUPS (default 20).
# =============================================================================

set -e

# --- Configurazione ---
METNOS_DIR="/opt/metnos"
USER_HOME="/home/roberto"
NAS_MOUNT="/mnt/nas"
BACKUP_BASE="$NAS_MOUNT/backup/BEELINK/metnos"
MAX_BACKUPS=20
LOG_FILE="/opt/metnos/data/backup_nas.log"
DATE_TAG=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$BACKUP_BASE/metnos_$DATE_TAG"

# --- Logging ---
mkdir -p "$(dirname "$LOG_FILE")"
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

log "=== Backup avviato ==="

# --- Monta NAS ---
NAS_MOUNTED_BY_US=false
if ! mountpoint -q "$NAS_MOUNT"; then
    if ! mount "$NAS_MOUNT" 2>/dev/null; then
        log "ERRORE: Impossibile montare NAS ($NAS_MOUNT). Aborting."
        exit 1
    fi
    log "NAS montato"
    NAS_MOUNTED_BY_US=true
else
    log "NAS gia' montato"
fi

# --- Crea directory backup ---
mkdir -p "$BACKUP_DIR/myclaw"
mkdir -p "$BACKUP_DIR/dotlocal"
mkdir -p "$BACKUP_DIR/dotconfig"
mkdir -p "$BACKUP_DIR/claude_memory"
mkdir -p "$BACKUP_DIR/systemd"

# --- Backup codice e workspace ---
log "Backup codice + workspace ($METNOS_DIR -> myclaw/)..."
rsync -aL \
    --exclude='node_modules/' \
    --exclude='.venv/' \
    --exclude='.venv-image-poc/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='data/backup_nas.log' \
    "$METNOS_DIR/" "$BACKUP_DIR/myclaw/"

# --- Backup runtime data (~/.local/share/metnos) ---
# CRITICAL: escludere `Immagini/` perche' e' un mount CIFS sullo stesso NAS.
# Senza questa esclusione rsync entrerebbe in loop ricorsivo NAS->NAS e
# riempirebbe il filesystem.
log "Backup runtime data (~/.local/share/metnos -> dotlocal/)..."
if [ -d "$USER_HOME/.local/share/metnos" ]; then
    # -aL: follow symlinks (CIFS NAS non li supporta nativamente, errore code 23
    # sui binari Rust client che usano symlink di alias).
    rsync -aL \
        --exclude='Immagini/' \
        --exclude='Immagini' \
        --exclude='thumbcache/' \
        --exclude='_history/*/blob/' \
        --exclude='__pycache__/' \
        "$USER_HOME/.local/share/metnos/" "$BACKUP_DIR/dotlocal/"
else
    log "WARN: ~/.local/share/metnos assente"
fi

# --- Backup user state (~/.local/state/metnos) — pending stateful ---
if [ -d "$USER_HOME/.local/state/metnos" ]; then
    log "Backup user state (~/.local/state/metnos -> dotlocal_state/)..."
    mkdir -p "$BACKUP_DIR/dotlocal_state"
    rsync -aL \
        --exclude='cap_pending/' \
        --exclude='location_pending/' \
        --exclude='get_inputs/' \
        --exclude='*.lock' \
        "$USER_HOME/.local/state/metnos/" "$BACKUP_DIR/dotlocal_state/"
fi

# --- Backup secrets / config ---
log "Backup secrets + config (~/.config/metnos -> dotconfig/)..."
if [ -d "$USER_HOME/.config/metnos" ]; then
    rsync -aL "$USER_HOME/.config/metnos/" "$BACKUP_DIR/dotconfig/"
fi

# --- Backup memorie Claude (~/.claude/projects/-opt-myclaw/memory/) ---
CLAUDE_MEM="$USER_HOME/.claude/projects/-opt-myclaw/memory"
if [ -d "$CLAUDE_MEM" ]; then
    log "Backup memorie Claude ($CLAUDE_MEM -> claude_memory/)..."
    rsync -aL "$CLAUDE_MEM/" "$BACKUP_DIR/claude_memory/"
fi

# --- Backup systemd units ---
log "Backup systemd units..."
# user
for f in "$USER_HOME/.config/systemd/user/"metnos-*.service \
         "$USER_HOME/.config/systemd/user/"metnos-*.timer; do
    [ -f "$f" ] && cp "$f" "$BACKUP_DIR/systemd/" 2>/dev/null || true
done
# system
for f in /etc/systemd/system/metnos-*.service \
         /etc/systemd/system/metnos-*.timer \
         /etc/systemd/system/myclaw-docs.service; do
    [ -f "$f" ] && cp "$f" "$BACKUP_DIR/systemd/" 2>/dev/null || true
done
cp /etc/fstab "$BACKUP_DIR/systemd/fstab" 2>/dev/null || true

# --- Crea manifest ---
log "Creazione manifest..."
EXCLUDED_NOTE="venv*/, node_modules/, __pycache__/, Immagini/ (CIFS NAS), thumbcache/, _history/blob/, cap_pending/, location_pending/, get_inputs/, *.lock, /home/roberto/models/*.gguf (~57GB ridownloadabili)"
{
    echo "Metnos Backup"
    echo "==============="
    echo "Data: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Hostname: $(hostname)"
    echo "IP: $(hostname -I | awk '{print $1}')"
    echo "Python: $(python3 --version 2>&1)"
    echo "OS: $(grep PRETTY_NAME /etc/os-release | cut -d'\"' -f2)"
    echo "Uptime: $(uptime -p)"
    echo
    echo "Sezioni backup:"
    echo "  myclaw/         $(find "$BACKUP_DIR/myclaw" -type f 2>/dev/null | wc -l) file, $(du -sh "$BACKUP_DIR/myclaw" 2>/dev/null | cut -f1)"
    echo "  dotlocal/       $(find "$BACKUP_DIR/dotlocal" -type f 2>/dev/null | wc -l) file, $(du -sh "$BACKUP_DIR/dotlocal" 2>/dev/null | cut -f1)"
    echo "  dotlocal_state/ $(find "$BACKUP_DIR/dotlocal_state" -type f 2>/dev/null | wc -l) file, $(du -sh "$BACKUP_DIR/dotlocal_state" 2>/dev/null | cut -f1)"
    echo "  dotconfig/      $(find "$BACKUP_DIR/dotconfig" -type f 2>/dev/null | wc -l) file, $(du -sh "$BACKUP_DIR/dotconfig" 2>/dev/null | cut -f1)"
    echo "  claude_memory/  $(find "$BACKUP_DIR/claude_memory" -type f 2>/dev/null | wc -l) file, $(du -sh "$BACKUP_DIR/claude_memory" 2>/dev/null | cut -f1)"
    echo "  systemd/        $(find "$BACKUP_DIR/systemd" -type f 2>/dev/null | wc -l) file"
    echo
    echo "Database/state interessanti:"
    echo "  scratchpad:    $(ls -lh "$BACKUP_DIR/dotlocal/scratchpad.db" 2>/dev/null | awk '{print $5}')"
    echo "  i18n DB:       $(ls -lh "$BACKUP_DIR/dotlocal/i18n.sqlite" 2>/dev/null | awk '{print $5}')"
    echo "  users DB:      $(ls -lh "$BACKUP_DIR/dotlocal/users.db" 2>/dev/null | awk '{print $5}')"
    echo "  pairings DB:   $(ls -lh "$BACKUP_DIR/dotlocal/pairings.db" 2>/dev/null | awk '{print $5}')"
    echo "  undo:          $(ls -lh "$BACKUP_DIR/dotlocal/undo.jsonl" 2>/dev/null | awk '{print $5}')"
    echo "  turns:         $(find "$BACKUP_DIR/dotlocal/turns" -name '*.jsonl' 2>/dev/null | wc -l) jsonl, $(du -sh "$BACKUP_DIR/dotlocal/turns" 2>/dev/null | cut -f1)"
    echo "  index image:   $(du -sh "$BACKUP_DIR/dotlocal/index" 2>/dev/null | cut -f1)"
    echo "  exec synth:    $(ls "$BACKUP_DIR/dotlocal/executors/" 2>/dev/null | wc -l) executor"
    echo "  proposals:     $(ls "$BACKUP_DIR/dotlocal/synt_proposals/" 2>/dev/null | wc -l) proposte"
    echo "  devices DB:    $(ls -lh "$BACKUP_DIR/dotlocal_state/devices.db" 2>/dev/null | awk '{print $5}')"
    echo
    echo "Esclusi (ricostruibili / volatili):"
    echo "  $EXCLUDED_NOTE"
    echo
    echo "Restore quick:"
    echo "  rsync -a myclaw/ /opt/metnos/"
    echo "  rsync -a dotlocal/ ~/.local/share/metnos/"
    echo "  rsync -a dotlocal_state/ ~/.local/state/metnos/"
    echo "  rsync -a dotconfig/ ~/.config/metnos/"
    echo "  rsync -a claude_memory/ ~/.claude/projects/-opt-myclaw/memory/"
    echo "  cp systemd/*.service systemd/*.timer /etc/systemd/system/  &&  systemctl daemon-reload"
} > "$BACKUP_DIR/manifest.txt"

log "Backup completato: $BACKUP_DIR"

# --- Rotazione: mantieni solo ultimi MAX_BACKUPS ---
log "Rotazione backup (max $MAX_BACKUPS)..."
BACKUP_COUNT=$(ls -1d "$BACKUP_BASE"/metnos_* 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
    EXCESS=$((BACKUP_COUNT - MAX_BACKUPS))
    ls -1d "$BACKUP_BASE"/metnos_* | sort | head -n "$EXCESS" | while read OLD_BACKUP; do
        log "  Rimuovo vecchio backup: $(basename "$OLD_BACKUP")"
        rm -rf "$OLD_BACKUP"
    done
    log "Rimossi $EXCESS backup vecchi"
else
    log "Backup presenti: $BACKUP_COUNT/$MAX_BACKUPS (nessuna rotazione)"
fi

# --- Smonta NAS se montato da noi ---
if [ "$NAS_MOUNTED_BY_US" = true ]; then
    umount "$NAS_MOUNT" 2>/dev/null || true
    log "NAS smontato"
fi

log "=== Backup terminato con successo ==="
