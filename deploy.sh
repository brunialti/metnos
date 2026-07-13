#!/usr/bin/env bash
# Deploy docs/ di Mykleos a Cloudflare Pages.
# Token letto da ~/.config/mykleos/deploy.env (chmod 600), non da ambiente interattivo né da memoria.
#
# Preparazione una tantum:
#   mkdir -p ~/.config/mykleos
#   chmod 700 ~/.config/mykleos
#   cat > ~/.config/mykleos/deploy.env <<'EOF'
#   CLOUDFLARE_API_TOKEN=cfut_xxx...
#   CLOUDFLARE_ACCOUNT_ID=cfd806df1fda3110b9bc0a2d2d0eb3b0
#   EOF
#   chmod 600 ~/.config/mykleos/deploy.env
set -euo pipefail

ENV_FILE="${HOME}/.config/mykleos/deploy.env"
REPO="/opt/metnos"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE non esiste." >&2
    echo "Crealo come descritto nell'header di questo script." >&2
    exit 1
fi

# Controllo permessi: deve essere leggibile solo dall'utente.
perms=$(stat -c '%a' "$ENV_FILE")
if [ "$perms" != "600" ] && [ "$perms" != "400" ]; then
    echo "ERROR: $ENV_FILE ha permessi $perms. Deve essere 600 o 400." >&2
    echo "Esegui: chmod 600 $ENV_FILE" >&2
    exit 1
fi

set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a

: "${CLOUDFLARE_API_TOKEN:?manca CLOUDFLARE_API_TOKEN in $ENV_FILE}"
: "${CLOUDFLARE_ACCOUNT_ID:?manca CLOUDFLARE_ACCOUNT_ID in $ENV_FILE}"

cd "$REPO"
python3 scripts/generate_executor_catalog.py
exec node_modules/.bin/wrangler pages deploy docs \
    --project-name=mykleos \
    --commit-dirty=true \
    --branch=main \
    "$@"
