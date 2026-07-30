#!/usr/bin/env bash
# build-client.sh — compila metnos-client per i target supportati, FIRMA i
# binari Ed25519 con la chiave 'author' del server, e pubblica tutto nel
# mirror server-side (binari + .sig + install.sh/install.ps1 con pubkey pinnata).
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

sign_blob() {
    python3 "$SIGNER" sign "$1" >/dev/null
}

for TARGET in "${TARGETS[@]}"; do
    echo "==> target: $TARGET"
    cargo build --release --target "$TARGET"

    case "$TARGET" in
        *windows*) BIN_NAME="metnos-client.exe" ;;
        *)         BIN_NAME="metnos-client"     ;;
    esac

    SRC="$CLIENT_DIR/target/$TARGET/release/$BIN_NAME"
    [ -f "$SRC" ] || { echo "ERROR: $SRC not produced" >&2; exit 1; }

    DST_DIR="$CLIENT_OUT/$VERSION/$TARGET"
    mkdir -p "$DST_DIR"
    install -m 0644 "$SRC" "$DST_DIR/$BIN_NAME"
    sign_blob "$DST_DIR/$BIN_NAME"

    SIZE=$(stat -c '%s' "$DST_DIR/$BIN_NAME")
    SHA=$(sha256sum "$DST_DIR/$BIN_NAME" | awk '{print $1}')
    echo "    OK $BIN_NAME size=$SIZE sha256=$SHA (firmato)"
done

# --- manifest.json --------------------------------------------------------
MANIFEST="$CLIENT_OUT/manifest.json"
{
    echo "{"
    echo "  \"latest\": \"$VERSION\","
    echo "  \"versions\": {"
    echo "    \"$VERSION\": {"
    LAST_IDX=$((${#TARGETS[@]} - 1))
    for i in "${!TARGETS[@]}"; do
        TARGET="${TARGETS[$i]}"
        case "$TARGET" in
            *windows*) BIN_NAME="metnos-client.exe" ;;
            *)         BIN_NAME="metnos-client"     ;;
        esac
        F="$CLIENT_OUT/$VERSION/$TARGET/$BIN_NAME"
        SHA=$(sha256sum "$F" | awk '{print $1}')
        SIZE=$(stat -c '%s' "$F")
        TRAILING=","
        [ "$i" -eq "$LAST_IDX" ] && TRAILING=""
        echo "      \"$TARGET\": {"
        echo "        \"filename\": \"$BIN_NAME\","
        echo "        \"path\": \"$VERSION/$TARGET/$BIN_NAME\","
        echo "        \"size\": $SIZE,"
        echo "        \"sha256\": \"$SHA\""
        echo "      }$TRAILING"
    done
    echo "    }"
    echo "  }"
    echo "}"
} > "$MANIFEST"
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
