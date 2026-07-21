#!/usr/bin/env bash
# /opt/metnos/install/setup.sh
#
# Scaffold dello script di setup di Metnos su nodo nuovo.
#
# Idempotente: se un passo e' gia' stato eseguito (directory presente,
# pacchetto installato, secret esistente), salta.
#
# Suppone:
#  - Repo Metnos clonato in /opt/metnos
#  - Distro Debian/Ubuntu derivata
#  - Utente di lavoro = $USER (default: chi esegue lo script)
#
# Passi:
#  1. Verifica python_min (>= 3.12)
#  2. Installa pacchetti di sistema (apt-get) — richiede sudo
#  3. Crea directory dati/config/state
#  4. Genera secret rigenerabili (admin.key, signing keys)
#  5. Installa systemd units (sudo cp + systemctl enable)
#  6. (Opzionale) lancia install/download_models.sh per i blob ML
#  7. (Opzionale) installa pacchetti Python aggiuntivi nel venv esistente
#
# Uso:
#  ./setup.sh                 # tutto + interattivo
#  ./setup.sh --no-sudo       # salta passi che richiedono sudo (apt + systemd)
#  ./setup.sh --skip-models   # salta download dei modelli
#  ./setup.sh --dry-run       # stampa cosa farebbe, non esegue

set -euo pipefail

DRY_RUN=0
NO_SUDO=0
SKIP_MODELS=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --no-sudo) NO_SUDO=1 ;;
        --skip-models) SKIP_MODELS=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *) echo "Argomento sconosciuto: $arg" >&2; exit 1 ;;
    esac
done

WORKING_DIR="/opt/metnos"
INSTALL_DIR="$WORKING_DIR/install"
MANIFEST="$INSTALL_DIR/manifest.toml"

CONFIG_DIR="$HOME/.config/metnos"
DATA_DIR="$HOME/.local/share/metnos"
STATE_DIR="$HOME/.local/state/metnos"
USER_SYSTEMD_DIR="$HOME/.config/systemd/user"

log() { printf '[setup] %s\n' "$*"; }
err() { printf '[setup] ERROR: %s\n' "$*" >&2; }

run_or_print() {
    if [[ $DRY_RUN -eq 1 ]]; then
        printf '  [dry-run] %s\n' "$*"
    else
        eval "$@"
    fi
}

run_sudo() {
    if [[ $NO_SUDO -eq 1 ]]; then
        log "skip (no-sudo): $*"
        return 0
    fi
    if [[ $DRY_RUN -eq 1 ]]; then
        printf '  [dry-run sudo] %s\n' "$*"
    else
        sudo "$@" || {
            err "sudo $* fallito (skip o esegui manualmente)"
            return 1
        }
    fi
}

# ── 1. Verifica Python ────────────────────────────────────────────

step_python() {
    log "[1/7] Verifica Python >= 3.12"
    if ! command -v python3 >/dev/null; then
        err "python3 non trovato. Installa Python 3.12+ prima di proseguire."
        return 1
    fi
    local ver
    ver=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    log "  Python $ver"
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'; then
        log "  OK (>= 3.12)"
    else
        err "Python $ver < 3.12. Aggiorna l'interprete."
        return 1
    fi
}

# ── 2. System packages ─────────────────────────────────────────────

step_system_packages() {
    log "[2/7] Pacchetti di sistema (apt)"
    # Estraggo la lista da manifest.toml senza dipendenze esterne (grep + sed).
    if [[ ! -f "$MANIFEST" ]]; then
        err "manifest non trovato: $MANIFEST"; return 1
    fi
    # Lista pacchetti debian = required (escludo *_optional)
    local pkgs
    pkgs=$(python3 - <<EOF
import sys
import tomllib
with open("$MANIFEST", "rb") as f:
    m = tomllib.load(f)
print(" ".join(m.get("system_packages", {}).get("debian", [])))
EOF
)
    log "  pacchetti: $pkgs"
    if [[ -n "$pkgs" ]]; then
        run_sudo apt-get update -qq
        # shellcheck disable=SC2086
        run_sudo apt-get install -y $pkgs
    fi
}

# ── 3. Directories ─────────────────────────────────────────────────

step_directories() {
    log "[3/7] Crea directory dati/config/state"
    python3 - <<EOF
import os
import sys
import tomllib

with open("$MANIFEST", "rb") as f:
    m = tomllib.load(f)

home = os.path.expanduser("~")
for entry in m.get("directories", {}).get("entry", []):
    p = entry["path"].replace("~", home)
    mode = int(entry.get("mode", "0755"), 8)
    if not os.path.isdir(p):
        os.makedirs(p, mode=mode, exist_ok=True)
        os.chmod(p, mode)
        print(f"  mkdir {p} (mode {oct(mode)})")
    else:
        print(f"  skip {p} (esistente)")
EOF
}

# ── 4. Secrets ────────────────────────────────────────────────────

step_secrets() {
    log "[4/7] Secret rigenerabili"
    python3 - <<EOF
import os
import secrets
import sys
import tomllib

with open("$MANIFEST", "rb") as f:
    m = tomllib.load(f)

home = os.path.expanduser("~")
for entry in m.get("secrets", {}).get("entry", []):
    p = entry["path"].replace("~", home)
    if os.path.exists(p):
        print(f"  skip {entry['name']} (esistente: {p})")
        continue
    gen = entry.get("generator", "")
    if gen == "secrets.token_hex(32)":
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(p, int(entry.get("mode", "0600"), 8))
        print(f"  GEN {entry['name']} → {p}")
    elif gen == "ed25519_keypair_pem":
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        priv = Ed25519PrivateKey.generate()
        priv_pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f: f.write(priv_pem)
        os.chmod(p, int(entry.get("mode", "0600"), 8))
        pub_path = entry.get("public_path", "").replace("~", home)
        if pub_path:
            with open(pub_path, "wb") as f: f.write(pub_pem)
            os.chmod(pub_path, 0o644)
        print(f"  GEN {entry['name']} → {p} + {pub_path}")
    else:
        print(f"  WARN: generator '{gen}' non implementato per {entry['name']}")
EOF
}

# ── 4-ter. Runtime config (~/.config/metnos/runtime.toml) ──────────

step_runtime_config() {
    log "[4-ter] Runtime config (toml persistente)"
    python3 - <<EOF
import sys
sys.path.insert(0, "$INSTALL_DIR/runtime")
try:
    from runtime_settings import ensure_default_config
    created = ensure_default_config()
    if created:
        print(f"  GEN runtime.toml con default fast-path / multi_tool_fast_path")
    else:
        print(f"  skip runtime.toml (esistente, preservato)")
except Exception as ex:
    print(f"  WARN: runtime_settings non disponibile: {ex!r}")
EOF
}

# ── 4-bis. Script symlinks (/usr/local/bin) ───────────────────────

step_scripts() {
    log "[4-bis] Script symlinks"
    if [[ $NO_SUDO -eq 1 ]]; then
        log "  skip (no-sudo): symlink in /usr/local/bin richiede sudo"
        return 0
    fi
    python3 - <<EOF
import os
import sys
import tomllib

with open("$MANIFEST", "rb") as f:
    m = tomllib.load(f)

for entry in m.get("scripts", {}).get("entry", []):
    name = entry.get("name", "?")
    src = entry["src"]
    dest = entry["dest"]
    if not os.path.exists(src):
        print(f"  WARN: {src} non esiste, salto {name}")
        continue
    if not os.access(src, os.X_OK):
        print(f"  WARN: {src} non e' eseguibile, salto {name}")
        continue
    print(f"  {name}: ln -sf {src} {dest}")
EOF
    # Esegue effettivamente i symlink (idempotenti grazie a -f).
    python3 - <<EOF
import os, subprocess, tomllib
with open("$MANIFEST", "rb") as f:
    m = tomllib.load(f)
for entry in m.get("scripts", {}).get("entry", []):
    src = entry["src"]
    dest = entry["dest"]
    if not os.path.exists(src) or not os.access(src, os.X_OK):
        continue
    rc = subprocess.call(["sudo", "ln", "-sf", src, dest])
    if rc != 0:
        print(f"  WARN: ln -sf {src} {dest} ha fallito (rc={rc})")
EOF
}

# ── 5. Systemd units ──────────────────────────────────────────────

step_systemd() {
    log "[5/7] Systemd units"
    if [[ $NO_SUDO -eq 1 ]]; then
        log "  skip (no-sudo)"
        return 0
    fi
    # Estrai units dal manifest
    python3 - <<EOF
import os
import shutil
import sys
import subprocess
import tomllib

with open("$MANIFEST", "rb") as f:
    m = tomllib.load(f)

home = os.path.expanduser("~")
for entry in m.get("services", {}).get("entry", []):
    name = entry["name"]
    typ = entry.get("type", "systemd")
    src = entry.get("unit_file_local", "").replace("~", home)
    dst = entry.get("unit_file_dest", "").replace("~", home)
    if not src or not dst:
        print(f"  skip {name} (manca path)")
        continue
    if not os.path.exists(src):
        print(f"  WARN: {src} non esiste, salto {name}")
        continue
    print(f"  {name}: {src} → {dst} (type={typ})")
EOF
    log "  NB: ogni unit richiede 'sudo cp <src> <dst>' + 'sudo systemctl daemon-reload' + 'sudo systemctl enable <name>'."
    log "  In setup automatico questo step e' interattivo: copia manuale richiesta per scelte di sicurezza."
}

# ── 6. Modelli ─────────────────────────────────────────────────────

step_models() {
    log "[6/8] Modelli ML"
    if [[ $SKIP_MODELS -eq 1 ]]; then
        log "  skip (--skip-models)"
        return 0
    fi
    if [[ -x "$INSTALL_DIR/download_models.sh" ]]; then
        run_or_print "$INSTALL_DIR/download_models.sh"
    else
        err "download_models.sh non trovato/eseguibile"
    fi
}

# ── 7. Intent classifier (Qwen3-Embedding-0.6B fine-tuned) ─────────

step_intent_classifier() {
    log "[7/8] Intent classifier — Qwen3-Embedding-0.6B fine-tuned"
    if [[ $SKIP_MODELS -eq 1 ]]; then
        log "  skip (--skip-models)"
        return 0
    fi

    local data_dir="${DATA_DIR:-$HOME/.local/share/metnos}/intent_classifier"
    if [[ -d "$data_dir/v1" ]] && ls "$data_dir/v1"/*.safetensors >/dev/null 2>&1; then
        log "  Già installato in $data_dir/v1 — skip."
        return 0
    fi

    log "  AVVISO: Metnos installa un modello di intent classification (~1.2 GB)"
    log "          + esegue un training locale iniziale (~5 min CPU)."
    log "          Rimpiazza il vocabolario hardcoded con un classificatore"
    log "          che si auto-migliora dal turn log via re-train weekly."
    log "          Senza, Metnos cade in fallback hardcoded+affinity (~62% acc)."
    log ""
    if [[ $DRY_RUN -eq 1 ]]; then
        log "  [dry-run] Would: hf download Qwen/Qwen3-Embedding-0.6B"
        log "  [dry-run] Would: python -m runtime.intent_classifier.train --initial"
        return 0
    fi
    if [[ -t 0 ]]; then
        read -p "  Procedere con download + training iniziale? [Y/n] " resp
        if [[ "$resp" =~ ^[nN] ]]; then
            log "  Skipped per scelta utente."
            return 0
        fi
    fi

    log "  Download modello base Qwen3-Embedding-0.6B (~1.2 GB)..."
    if ! python3 -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3-Embedding-0.6B')" 2>&1 | tail -3; then
        err "  Download fallito. Verificare connettività (vedi reference_mtu_fix se SSL bad record mac)."
        return 1
    fi

    log "  Training iniziale (5 epoch su ~870 pair seed bundled, ~5 min CPU)..."
    if ! (cd "$INSTALL_DIR/.." && python3 -m runtime.intent_classifier.train --initial 2>&1 | tail -10); then
        err "  Training fallito. Fallback automatico a hardcoded+affinity al boot."
        return 1
    fi

    log "  ✓ Intent classifier pronto in $data_dir/v1/"
    log "  Re-train automatico settimanale via scheduler v2 (daily@04:15)."
}

# ── 7. Pacchetti Python ────────────────────────────────────────────

step_python_packages() {
    log "[8/8] Pacchetti Python"
    local req="${METNOS_REPO_DIR:-$(dirname "$MANIFEST")/..}/requirements.txt"
    if [ -f "$req" ]; then
        log "  Le dipendenze runtime sono dichiarate in requirements.txt."
        log "  Per un venv stand-alone (consigliato per l'install ex-novo):"
        log "    python3 -m venv ~/.venvs/metnos"
        log "    ~/.venvs/metnos/bin/pip install -r $req"
        log "  Opzionali (skill): aggiungi -r requirements-optional.txt"
    else
        log "  ATTENZIONE: requirements.txt non trovato — il runtime potrebbe non avviarsi."
        log "    python3 -m venv ~/.venvs/metnos && pip install -r requirements.txt"
    fi
}

# ── Main ──────────────────────────────────────────────────────────

log "=== Metnos setup ==="
log "  manifest    : $MANIFEST"
log "  config_dir  : $CONFIG_DIR"
log "  data_dir    : $DATA_DIR"
log "  state_dir   : $STATE_DIR"
log "  dry_run     : $DRY_RUN"
log "  no_sudo     : $NO_SUDO"
log "  skip_models : $SKIP_MODELS"

step_python
step_system_packages
step_directories
step_secrets
step_runtime_config
step_scripts
step_systemd
step_models
step_intent_classifier
step_python_packages

log "=== Setup completato ==="
log "Prossimi passi manuali:"
log "  1. Configurare ~/.config/metnos/owned_domains.json e trusted_origins.json"
log "  2. Configurare ~/.config/metnos/mail/mail.env (IMAP/SMTP)"
log "  3. Pairare Telegram via /pair-channel (se canale Telegram desiderato)"
log "  4. Avviare il servizio: sudo systemctl start metnos-http"
