#!/usr/bin/env bash
# /opt/metnos/install/download_models.sh
#
# Scarica i modelli ML necessari a Metnos in `/opt/metnos/models/`.
# Idempotente: se il file esiste e ha sha256 corretto, salta.
# Se il manifest dichiara `<TBD-on-download>`, scarica e stampa lo sha256
# perche' venga incollato nel manifest al primo run.
#
# Modelli scaricati:
#   1. SigLIP-base-patch16-224 (Xenova ONNX, quantized int8)
#      → $MODELS_DIR/siglip/
#   2. InsightFace buffalo_l (RetinaFace det_10g + ArcFace w600k_r50)
#      → $MODELS_DIR/face/
#
# MODELS_DIR deriva da METNOS_MODELS_DIR o da METNOS_INSTALL_ROOT (§7.11,
# rename-resilient), default <install_root>/models.
#
# Nota: il modello text-embedding (MiniLM/BGE) e' usato in-process via il
# backend embedding (ai_backend). Su un'installazione condivisa puo' essere
# gia' presente (env METNOS_EMBEDDING_MODEL_DIR); su install ex-novo va
# fornito/scaricato a parte. Questo script scarica solo SigLIP + face.
#
# Uso:
#   ./download_models.sh           # scarica tutto
#   ./download_models.sh --dry-run # stampa cosa farebbe, non scarica
#   ./download_models.sh siglip    # solo SigLIP
#   ./download_models.sh face      # solo face pack
#
# Requisiti: curl, sha256sum, unzip.

set -euo pipefail

DRY_RUN=0
TARGETS=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        siglip|face) TARGETS+=("$arg") ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "Argomento sconosciuto: $arg" >&2
            exit 1
            ;;
    esac
done
[[ ${#TARGETS[@]} -eq 0 ]] && TARGETS=(siglip face)

# §7.11 rename-resilient: niente path assoluto hardcoded. Override esplicito
# via METNOS_MODELS_DIR; altrimenti derivato da METNOS_INSTALL_ROOT (ADR 0148),
# default <install_root>/models.
MODELS_DIR="${METNOS_MODELS_DIR:-${METNOS_INSTALL_ROOT:-/opt/metnos}/models}"
SIGLIP_DIR="${MODELS_DIR}/siglip"
FACE_DIR="${MODELS_DIR}/face"

# ── Helper ───────────────────────────────────────────────────────────

log() { printf '[download_models] %s\n' "$*"; }
err() { printf '[download_models] ERROR: %s\n' "$*" >&2; }

# Esegue argv DIRETTAMENTE (niente `eval`: i path derivano da env
# METNOS_MODELS_DIR controllabile dall'utente → eval = injection). I caller
# passano gli argomenti separati, non una stringa shell.
run_or_print() {
    if [[ $DRY_RUN -eq 1 ]]; then
        printf '  [dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

ensure_dir() {
    local d="$1"
    if [[ ! -d "$d" ]]; then
        log "mkdir -p $d"
        run_or_print mkdir -p "$d"
    fi
}

# Scarica un file solo se manca o sha256 diverso. Stampa lo sha256
# osservato a fine download per permettere di aggiornare il manifest.
fetch() {
    local url="$1"
    local dest="$2"
    local expected_sha="${3:-}"

    if [[ -f "$dest" ]]; then
        if [[ -n "$expected_sha" && "$expected_sha" != "<TBD-on-download>" && "$expected_sha" != "<TBD-on-extract>" ]]; then
            local actual
            actual=$(sha256sum "$dest" | awk '{print $1}')
            if [[ "$actual" == "$expected_sha" ]]; then
                log "skip $dest (sha256 ok)"
                return 0
            else
                log "sha256 mismatch su $dest, ridownload"
                run_or_print rm -f "$dest"
            fi
        else
            log "skip $dest (gia' presente, sha256 TBD)"
            local actual
            [[ $DRY_RUN -eq 0 ]] && {
                actual=$(sha256sum "$dest" | awk '{print $1}')
                log "  sha256 osservato = $actual  (incolla nel manifest)"
            }
            return 0
        fi
    fi

    # Solo https, e nessun downgrade su redirect (--proto-redir '=https'):
    # questi blob vengono eseguiti/firmati, un MITM su http sarebbe RCE.
    case "$url" in
        https://*) : ;;
        *) err "URL non-https rifiutato (integrità non garantibile): $url"; return 1 ;;
    esac
    if [[ -z "$expected_sha" || "$expected_sha" == "<TBD-on-download>" || "$expected_sha" == "<TBD-on-extract>" ]]; then
        log "  ! nessun sha256 atteso per $dest — integrità NON verificata (TOFU)"
    fi
    log "GET $url → $dest"
    run_or_print curl -fL --proto '=https' --proto-redir '=https' \
        --progress-bar --output "$dest.partial" "$url"
    run_or_print mv "$dest.partial" "$dest"

    if [[ $DRY_RUN -eq 0 ]]; then
        local actual
        actual=$(sha256sum "$dest" | awk '{print $1}')
        log "  sha256 = $actual"
        if [[ -n "$expected_sha" && "$expected_sha" != "<TBD-on-download>" && "$actual" != "$expected_sha" ]]; then
            err "sha256 mismatch su $dest"
            err "  atteso : $expected_sha"
            err "  ottenuto: $actual"
            return 1
        fi
    fi
}

# ── SigLIP ───────────────────────────────────────────────────────────

download_siglip() {
    log "=== SigLIP (Xenova ONNX, quantized int8) ==="
    ensure_dir "$SIGLIP_DIR"

    local base="https://huggingface.co/Xenova/siglip-base-patch16-224/resolve/main"

    fetch "$base/onnx/text_model_quantized.onnx"   "$SIGLIP_DIR/text_model_quantized.onnx"   "<TBD-on-download>"
    fetch "$base/onnx/vision_model_quantized.onnx" "$SIGLIP_DIR/vision_model_quantized.onnx" "<TBD-on-download>"
    fetch "$base/tokenizer.json"                   "$SIGLIP_DIR/tokenizer.json"              "<TBD-on-download>"
    fetch "$base/tokenizer_config.json"            "$SIGLIP_DIR/tokenizer_config.json"       "<TBD-on-download>"
    fetch "$base/preprocessor_config.json"         "$SIGLIP_DIR/preprocessor_config.json"    "<TBD-on-download>"
    fetch "$base/config.json"                      "$SIGLIP_DIR/config.json"                 "<TBD-on-download>"
    fetch "$base/special_tokens_map.json"          "$SIGLIP_DIR/special_tokens_map.json"     "<TBD-on-download>"
    fetch "$base/spiece.model"                     "$SIGLIP_DIR/spiece.model"                "<TBD-on-download>"

    log "SigLIP OK"
}

# ── Face (buffalo_l) ─────────────────────────────────────────────────

download_face() {
    log "=== Face pack (InsightFace buffalo_l) ==="
    ensure_dir "$FACE_DIR"

    local zip_url="https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
    local zip_dest="$FACE_DIR/buffalo_l.zip"

    fetch "$zip_url" "$zip_dest" "<TBD-on-download>"

    if [[ ! -f "$FACE_DIR/det_10g.onnx" || ! -f "$FACE_DIR/w600k_r50.onnx" ]]; then
        log "unzip $zip_dest → $FACE_DIR (selettivo: det_10g, w600k_r50)"
        # Estrazione selettiva per NOME esatto (basename) in -d FACE_DIR:
        # niente path-traversal possibile (entry arbitrarie ignorate).
        run_or_print unzip -o -j "$zip_dest" det_10g.onnx w600k_r50.onnx -d "$FACE_DIR"
    else
        log "skip unzip (det_10g.onnx + w600k_r50.onnx gia' presenti)"
    fi

    if [[ $DRY_RUN -eq 0 ]]; then
        for f in det_10g.onnx w600k_r50.onnx; do
            if [[ -f "$FACE_DIR/$f" ]]; then
                local sha
                sha=$(sha256sum "$FACE_DIR/$f" | awk '{print $1}')
                log "  $f sha256 = $sha"
            fi
        done
    fi

    log "Face pack OK"
}

# ── Main ─────────────────────────────────────────────────────────────

ensure_dir "$MODELS_DIR"

for t in "${TARGETS[@]}"; do
    case "$t" in
        siglip) download_siglip ;;
        face)   download_face ;;
    esac
done

log "DONE. Aggiorna /opt/metnos/install/manifest.toml con gli sha256 stampati."
