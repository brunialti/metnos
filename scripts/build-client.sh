#!/usr/bin/env bash
# build-client.sh — compila i programmi che Metnos installa sui device, li
# FIRMA (Ed25519, chiave 'author' del server) e li pubblica nel mirror
# server-side (binari + .sig + install.sh/install.ps1 con pubkey pinnata).
#
# I componenti sono DUE e viaggiano insieme, perche' insieme devono stare:
#   - metnos-client, che gira come l'utente;
#   - metnos-helper, l'aiutante elevato di Windows (ADR 0210 D).
# Un aiutante che si potesse portare su una macchina solo a mano sarebbe un
# programma installato dall'amministratore, non da Metnos.
#
# Pattern coerente con deploy.sh: una bash, una chiave, una directory. Niente
# CI esterna (ADR 0046). §5.3/§8 del design doc executor remoti.
#
# Uso:   ./scripts/build-client.sh <version>
# es.:   ./scripts/build-client.sh 0.1.0
#
# Pre-requisiti su .33:
#   - rustup (toolchain stable) + target: x86_64-unknown-linux-musl,
#     x86_64-pc-windows-gnu (mingw-w64 per il target windows).
#   - chiave 'author' in ~/.config/metnos/keys/ (sign.py keygen).
#
# Output in $METNOS_MIRROR_ROOT/client/:
#   <version>/<target>/metnos-client[.exe]     binario
#   <version>/<target>/metnos-client[.exe].sig firma Ed25519 (raw)
#   manifest.json                              indice (latest + sha256)
#   install.sh, install.ps1                    installer con pubkey pinnata

set -euo pipefail

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    echo "usage: $0 <version>" >&2
    exit 1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT_DIR="$REPO/client-rs"
MIRROR_ROOT="${METNOS_MIRROR_ROOT:-$HOME/.local/share/metnos/mirror}"
CLIENT_OUT="$MIRROR_ROOT/client"
SIGNER="$REPO/scripts/client_signing.py"

echo "==> building metnos-client v$VERSION"

if ! command -v cargo >/dev/null 2>&1; then
    if [ -f "$HOME/.cargo/env" ]; then
        # shellcheck disable=SC1091
        source "$HOME/.cargo/env"
    fi
fi

cd "$CLIENT_DIR"

# Target di distribuzione: static-link musl su Linux (§ADR 0037), mingw su
# Windows. Override con METNOS_CLIENT_TARGETS="a b c".
read -r -a TARGETS <<< "${METNOS_CLIENT_TARGETS:-x86_64-unknown-linux-musl x86_64-pc-windows-gnu}"

# nome:cartella-del-crate:eseguibile:dove-esiste
# L'aiutante e' un servizio di Windows: fuori da li' non ha significato, e
# «solo windows» e' un fatto suo, non una politica del pubblicatore.
COMPONENTS=(
    "client:$REPO/client-rs:metnos-client:ovunque"
    "helper:$REPO/helper-rs:metnos-helper:windows"
)

# Vero quando il componente esiste per quel target.
componente_vale_per() {
    case "$2" in ovunque) return 0 ;; esac
    case "$1" in *windows*) return 0 ;; *) return 1 ;; esac
}

sign_blob() {
    python3 "$SIGNER" sign "$1" >/dev/null
}

for TARGET in "${TARGETS[@]}"; do
    echo "==> target: $TARGET"
    for SPEC in "${COMPONENTS[@]}"; do
        IFS=":" read -r COMP CRATE_DIR BIN_BASE DOVE <<< "$SPEC"
        componente_vale_per "$TARGET" "$DOVE" || continue

        case "$TARGET" in
            *windows*) BIN_NAME="$BIN_BASE.exe" ;;
            *)         BIN_NAME="$BIN_BASE"     ;;
        esac

        ( cd "$CRATE_DIR" && cargo build --release --target "$TARGET" )

        SRC="$CRATE_DIR/target/$TARGET/release/$BIN_NAME"
        [ -f "$SRC" ] || { echo "ERROR: $SRC not produced" >&2; exit 1; }

        DST_DIR="$CLIENT_OUT/$VERSION/$TARGET"
        mkdir -p "$DST_DIR"
        install -m 0644 "$SRC" "$DST_DIR/$BIN_NAME"
        sign_blob "$DST_DIR/$BIN_NAME"

        SIZE=$(stat -c '%s' "$DST_DIR/$BIN_NAME")
        SHA=$(sha256sum "$DST_DIR/$BIN_NAME" | awk '{print $1}')
        echo "    OK [$COMP] $BIN_NAME size=$SIZE sha256=$SHA (firmato)"
    done
done

# --- manifest.json --------------------------------------------------------
# Scritto in Python: costruire JSON a mano in bash e' il posto dove una
# virgola di troppo passa inosservata fino al primo aggiornamento fallito.
MANIFEST="$CLIENT_OUT/manifest.json"
python3 - "$CLIENT_OUT" "$VERSION" "$MANIFEST" "${TARGETS[@]}" <<'PYEOF'
import hashlib, json, sys
from pathlib import Path

out, version, manifest_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
targets = sys.argv[4:]

# Il nome del componente e' la chiave: chi cerca «helper» chiede «helper», e
# non deve sapere come si chiama il file su quel sistema.
componenti = {"client": "metnos-client", "helper": "metnos-helper"}

per_target = {}
for target in targets:
    trovati = {}
    for nome, base in componenti.items():
        binario = out / version / target / (base + (".exe" if "windows" in target else ""))
        if not binario.is_file():
            continue  # componente che non esiste per questo sistema
        dati = binario.read_bytes()
        trovati[nome] = {
            "filename": binario.name,
            "path": f"{version}/{target}/{binario.name}",
            "size": len(dati),
            "sha256": hashlib.sha256(dati).hexdigest(),
        }
    if trovati:
        per_target[target] = trovati

manifest_path.write_text(json.dumps(
    {"latest": version, "versions": {version: per_target}}, indent=2) + "\n")
PYEOF
echo "==> manifest written: $MANIFEST"

# --- installer con pubkey server pinnata ----------------------------------
PUBKEY_DER_B64=$(python3 "$SIGNER" pubkey-der-b64)
[ -n "$PUBKEY_DER_B64" ] || { echo "ERROR: pubkey server non disponibile" >&2; exit 1; }

sed "s|@@SERVER_PUBKEY_DER_B64@@|$PUBKEY_DER_B64|g" \
    "$CLIENT_DIR/install/install.sh.in" > "$CLIENT_OUT/install.sh"
sed "s|@@SERVER_PUBKEY_DER_B64@@|$PUBKEY_DER_B64|g" \
    "$CLIENT_DIR/install/install.ps1.in" > "$CLIENT_OUT/install.ps1"
chmod 0644 "$CLIENT_OUT/install.sh" "$CLIENT_OUT/install.ps1"
echo "==> installer generati (pubkey pinnata: ${PUBKEY_DER_B64:0:16}...)"

echo "==> binaries in $CLIENT_OUT (latest: $VERSION)"
