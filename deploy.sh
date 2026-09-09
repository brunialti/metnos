#!/usr/bin/env bash
# Pubblica la documentazione di Metnos su Cloudflare Pages.
# Le credenziali sono lette da /etc/metnos/deploy.env (chmod 600 o 400),
# fuori dal repository e indipendentemente dall'utente che lo ha clonato.
#
# Preparazione una tantum:
#   sudo install -d -m 755 /etc/metnos
#   sudo editor /etc/metnos/deploy.env
# Il file deve contenere:
#
#   CLOUDFLARE_API_TOKEN=cfut_xxx...
#   CLOUDFLARE_ACCOUNT_ID=...
#   CLOUDFLARE_PAGES_PROJECT=...
#
#   sudo chown "$(id -un):$(id -gn)" /etc/metnos/deploy.env
#   chmod 600 /etc/metnos/deploy.env
set -euo pipefail

REPO="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ENV_FILE="${METNOS_DEPLOY_ENV:-/etc/metnos/deploy.env}"
PYTHON="${METNOS_VENV:-${REPO}/.venv}/bin/python"
export PATH="${REPO}/node_modules/.bin:$PATH"

# An audited public checkout can publish static pages without changing the
# local Tutor database or requiring its private signing authority.
STATIC_ONLY=0
if [ "${1:-}" = "--static-only" ]; then
    STATIC_ONLY=1
    shift
fi

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
: "${CLOUDFLARE_PAGES_PROJECT:?manca CLOUDFLARE_PAGES_PROJECT in $ENV_FILE}"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: ambiente Python di Metnos non trovato: $PYTHON" >&2
    exit 1
fi

cd "$REPO"
GENERATOR_ARGS=()
if [ "$STATIC_ONLY" = 1 ]; then
    GENERATOR_ARGS=(--check)
fi
"$PYTHON" scripts/generate_executor_catalog.py "${GENERATOR_ARGS[@]}"
"$PYTHON" scripts/generate_domain_reference.py "${GENERATOR_ARGS[@]}"
"$PYTHON" scripts/generate_ui_reference.py "${GENERATOR_ARGS[@]}"
"$PYTHON" runtime/published_docs.py validate
if [ "$STATIC_ONLY" = 0 ]; then
    "$PYTHON" scripts/compile_tutor_catalog.py --force
else
    echo "Static documentation only: the local Tutor catalog is unchanged."
fi
exec wrangler pages deploy docs \
    --project-name="$CLOUDFLARE_PAGES_PROJECT" \
    --commit-dirty=true \
    --branch=main \
    "$@"
