#!/usr/bin/env bash
# build-client.sh — compila metnos-client per i target supportati e
# pubblica i binari nel mirror server-side.
#
# Pattern coerente con deploy.sh per il sito: una bash, una chiave, una
# directory. Niente CI esterna.
#
# Uso:
#   ./scripts/build-client.sh <version>
# es.:
#   ./scripts/build-client.sh 0.1.0
#
# Pre-requisiti su .33:
#   - rustup (toolchain stable)
#   - target installati: x86_64-unknown-linux-gnu, x86_64-pc-windows-gnu
#   - mingw-w64 (per il target windows-gnu)
#
# Output:
#   $METNOS_MIRROR_ROOT/client/<version>/<target>/metnos-client[.exe]
#   $METNOS_MIRROR_ROOT/client/manifest.json

set -euo pipefail

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    echo "usage: $0 <version>" >&2
    exit 1
fi

REPO="/opt/metnos"
CLIENT_DIR="$REPO/client-rs"
MIRROR_ROOT="${METNOS_MIRROR_ROOT:-$HOME/.local/share/metnos/mirror}"
CLIENT_OUT="$MIRROR_ROOT/client"

echo "==> building metnos-client v$VERSION"

# Carica rustup se serve
if ! command -v cargo >/dev/null 2>&1; then
    if [ -f "$HOME/.cargo/env" ]; then
        # shellcheck disable=SC1091
        source "$HOME/.cargo/env"
    fi
fi

cd "$CLIENT_DIR"

TARGETS=(
    "x86_64-unknown-linux-gnu"
    "x86_64-pc-windows-gnu"
)

for TARGET in "${TARGETS[@]}"; do
    echo "==> target: $TARGET"
    cargo build --release --target "$TARGET"

    case "$TARGET" in
        *windows*) BIN_NAME="metnos-client.exe" ;;
        *)         BIN_NAME="metnos-client"     ;;
    esac

    SRC="$CLIENT_DIR/target/$TARGET/release/$BIN_NAME"
    if [ ! -f "$SRC" ]; then
        echo "ERROR: $SRC not produced" >&2
        exit 1
    fi

    DST_DIR="$CLIENT_OUT/$VERSION/$TARGET"
    mkdir -p "$DST_DIR"
    install -m 0644 "$SRC" "$DST_DIR/$BIN_NAME"

    SIZE=$(stat -c '%s' "$DST_DIR/$BIN_NAME")
    SHA=$(sha256sum "$DST_DIR/$BIN_NAME" | awk '{print $1}')
    echo "    OK $BIN_NAME size=$SIZE sha256=$SHA"
done

# Manifest
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

# Latest convenience symlink (one per target) so /agent/client/metnos-client.exe risolve sempre la versione corrente.
for TARGET in "${TARGETS[@]}"; do
    case "$TARGET" in
        *windows*) BIN_NAME="metnos-client.exe" ;;
        *)         BIN_NAME="metnos-client"     ;;
    esac
    ln -sfn "$VERSION/$TARGET/$BIN_NAME" "$CLIENT_OUT/$BIN_NAME-$TARGET"
done
ln -sfn "$VERSION/x86_64-pc-windows-gnu/metnos-client.exe" "$CLIENT_OUT/metnos-client.exe"
ln -sfn "$VERSION/x86_64-unknown-linux-gnu/metnos-client" "$CLIENT_OUT/metnos-client"

echo "==> manifest written: $MANIFEST"
echo "==> binaries in $CLIENT_OUT (latest: $VERSION)"
