#!/usr/bin/env bash
# publish-public.sh — sincronizza lo sviluppo (/opt/metnos) col repo PUBBLICO
# (brunialti/metnos) "a regime", in modo sicuro e ripetibile.
#
# Cosa fa, in un comando:
#   1. rigenera l'export-subset deterministico (scripts/export-public.sh)
#   2. CANCELLO DURO anti-PII/secret sull'albero da pubblicare → ABORTA se trova
#      qualcosa (email reali, /home/roberto/, token/secret pattern, il PAT stesso)
#   3. pubblica su GitHub:
#        - conserva la storia pubblica (commit incrementali, mai force-push)
#
# Il PAT GitHub viene letto dal credential-store cifrato (runtime/credentials),
# mai passato in argv (§10.1). Repo override: env METNOS_PUBLIC_REPO.
#
# Uso:
#   scripts/publish-public.sh -m "messaggio"        # aggiornamento incrementale
#   scripts/publish-public.sh --incremental -m "…"  # con storia pubblica
#   scripts/publish-public.sh --check               # solo gate, niente push
set -euo pipefail
# Every child of this script runs against a tree that a later gate measures.
# Bytecode written along the way is ignored by the public .gitignore, so it
# ends up present on disk and absent from the index, and the publication stops
# on `filesystem-index-divergence` - which is what the gate is for, but the
# divergence is ours. Not creating it is simpler than cleaning it up.
export PYTHONDONTWRITEBYTECODE=1
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
PYTHON="${METNOS_VENV:-${REPO_ROOT}/.venv}/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "ABORT: ambiente Python di Metnos non trovato: $PYTHON" >&2
  exit 1
fi

REPO="${METNOS_PUBLIC_REPO:-brunialti/metnos}"
DEST="dist/metnos-public"
MSG="Metnos — public snapshot"
MODE="incremental"
CHECK_ONLY=0

# RM-0008 G6-B3: the private development tree and the scrubbed public
# projection are two different reviewed source profiles.  Never derive either
# expected value from the candidate being published: changing source bytes
# requires an explicit review and an update of the corresponding fixed pin.
PRIVATE_SOURCE_REVIEW_SHA256="sha256:89433bde50fee8abf6a4a080bf1df1dabbd536423ada97d7008cf5421d5a19cd"
PRIVATE_SOURCE_REVIEW_COUNT=767
PUBLIC_SOURCE_REVIEW_SHA256="sha256:c484d61e8e276dc96016362c812e84017e260665378ae49c8283ddc3a13d70c5"
PUBLIC_SOURCE_REVIEW_COUNT=755
SOURCE_REVIEW_TOOL="$REPO_ROOT/internal/tools/rm0008_public_source_review.py"
BOUNDARY_POLICY_CHECKER="$REPO_ROOT/scripts/check_contract_boundary_policy.py"

source_review_gate() {
  "$PYTHON" "$SOURCE_REVIEW_TOOL" "$@"
}

while [ $# -gt 0 ]; do
  case "$1" in
    -m|--message) MSG="$2"; shift 2;;
    --incremental) MODE="incremental"; shift;;
    --check) CHECK_ONLY=1; shift;;
    *) echo "arg sconosciuto: $1" >&2; exit 2;;
  esac
done

echo "== -1. verifico proiezione policy del Birth gate =="
"$PYTHON" -I -B -S "$BOUNDARY_POLICY_CHECKER"

echo "== 0. verifico radice sorgenti privata RM-0008 =="
source_review_gate \
  private-fs "$REPO_ROOT" \
  "$PRIVATE_SOURCE_REVIEW_SHA256" "$PRIVATE_SOURCE_REVIEW_COUNT"
echo "   radice privata: $PRIVATE_SOURCE_REVIEW_SHA256 ($PRIVATE_SOURCE_REVIEW_COUNT sorgenti)"

echo "== 1. rigenero export =="
bash scripts/export-public.sh "$DEST" >/dev/null
"$PYTHON" -I -B -S "$DEST/scripts/check_contract_boundary_policy.py"
source_review_gate \
  public-fs-pin "$DEST" \
  "$PUBLIC_SOURCE_REVIEW_SHA256" "$PUBLIC_SOURCE_REVIEW_COUNT" \
  "$PRIVATE_SOURCE_REVIEW_SHA256"
echo "   file: $(find "$DEST" -type f -not -path '*/.git/*' | wc -l)"
echo "   radice pubblica: $PUBLIC_SOURCE_REVIEW_SHA256 ($PUBLIC_SOURCE_REVIEW_COUNT sorgenti)"

echo "== 2. CANCELLO DURO anti-PII/secret =="
fail=0
# PII reale
if grep -rIlE 'roberto\.brunialti@|mykleos@|@knowcastle\.com|@migadu\.com|/home/roberto/|587627005' "$DEST" 2>/dev/null \
   | grep -v 'scrub-scan.sh\|export-public.sh\|publish-public.sh'; then
  echo "   !! PII trovata ^"; fail=1
fi
# secret pattern
if grep -rIlE 'ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|sk-(ant|proj)?-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----' "$DEST" 2>/dev/null; then
  echo "   !! secret pattern trovato ^"; fail=1
fi
# file sensibili per estensione
if find "$DEST" -type f \( -name '*.env' -o -name '*.age' -o -name '*.key' -o -name '*.pem' -o -name 'google_token.json' -o -name '*client_secret*' \) 2>/dev/null | grep .; then
  echo "   !! file sensibile presente ^"; fail=1
fi
# Configurazione del tunnel personale: non fa parte del prodotto pubblico.
if grep -rIliE 'cloudflared|chat\.metnos\.com|cloudflare.{0,80}tunnel|tunnel.{0,80}cloudflare' "$DEST" 2>/dev/null; then
  echo "   !! riferimento al tunnel privato trovato ^"; fail=1
fi
# il PAT stesso nell'albero
TOK=$("$PYTHON" -c "import sys; sys.path.insert(0,'runtime'); import credentials; d=credentials.load('github'); print((d or {}).get('password',''))" 2>/dev/null || true)
if [ -n "$TOK" ] && GHTOKEN="$TOK" "$PYTHON" -c 'import os,sys; from pathlib import Path; token=os.environ["GHTOKEN"].encode(); found=any(token in p.read_bytes() for p in Path(sys.argv[1]).rglob("*") if p.is_file()); sys.exit(0 if found else 1)' "$DEST"; then
  echo "   !! IL TOKEN GITHUB È NELL'ALBERO ^^^"; fail=1
fi
if [ "$fail" != 0 ]; then
  echo "   ABORT: l'export non è pulito. Niente push."; exit 1
fi
echo "   filtro preliminare OK; segue classificazione GII completa"
GHTOKEN="$TOK" "$PYTHON" -B "$REPO_ROOT/internal/tools/public_gii_gate.py" "$DEST"

if [ "$CHECK_ONLY" = 1 ]; then echo "== --check: stop (nessun push) =="; exit 0; fi
[ -z "$TOK" ] && { echo "ABORT: PAT GitHub non disponibile nel credential-store"; exit 1; }

# .gitignore pubblico minimo (no regole dev di /opt/metnos)
write_pub_gitignore() {
  cat > "$1/.gitignore" <<'GI'
__pycache__/
*.py[cod]
.pytest_cache/
dist/
build/
google_token.json
google_client_secret.json
*.json.age
*.env
*.key
*.pem
.DS_Store
GI
}

# RM-0008 §16.14.4: l'inventario Python nasce dall'indice del worktree
# pubblico materializzato, non dal repository sorgente privato. Il generatore
# non sceglie filtri o classi: legge soltanto `git ls-files --cached` dopo che
# l'export finale e' stato interamente aggiunto all'indice.
refresh_rm0008_public_inventory() {
  local public_tree="$1"
  local generator="tests/portable/rm0008_2a_acceptance/generate_production_inventory_v1.py"
  local inventory="tests/portable/rm0008_2a_acceptance/production-python-inventory-v1.json"
  if [ ! -f "$public_tree/$generator" ] || [ ! -f "$public_tree/$inventory" ]; then
    echo "ABORT: gate RM-0008 incompleto nell'export pubblico" >&2
    return 1
  fi
  # -B, and not a cleanup afterwards: this runs inside the very tree the next
  # gate measures, and the bytecode it would otherwise leave behind is ignored
  # by the public .gitignore. Present on disk, absent from the index, the gate
  # reports `filesystem-index-divergence` and the publication stops.
  (
    cd "$public_tree"
    PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -B "$generator" --write
  )
  git -C "$public_tree" add "$inventory"
}

GIT_AUTH=(-c "credential.helper=!f() { echo username=x-access-token; echo \"password=\$GHTOKEN\"; }; f")
export GHTOKEN="$TOK"

echo "== 3. pubblico su $REPO (mode=$MODE) =="
if [ "$MODE" = "incremental" ]; then
  WC="dist/.public-repo"
  if [ ! -d "$WC/.git" ]; then
    rm -rf "$WC"
    git "${GIT_AUTH[@]}" clone -q "https://github.com/$REPO.git" "$WC"
    git -C "$WC" config user.name "brunialti"
    git -C "$WC" config user.email "brunialti@users.noreply.github.com"
  fi
  [ ! -e "$WC/.git/index.lock" ] || { echo "ABORT: indice pubblico occupato"; exit 1; }
  # sostituisci il contenuto tracciato col nuovo export (preserva .git)
  find "$WC" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
  cp -r "$DEST"/. "$WC"/
  write_pub_gitignore "$WC"
  git -C "$WC" add -A
  refresh_rm0008_public_inventory "$WC"
  source_review_gate \
    public-index "$WC" \
    "$PUBLIC_SOURCE_REVIEW_SHA256" "$PUBLIC_SOURCE_REVIEW_COUNT"
  "$PYTHON" -B "$REPO_ROOT/internal/tools/public_gii_gate.py" "$WC" --index
  if git -C "$WC" diff --cached --quiet; then
    # Un tentativo precedente può avere creato il commit locale ma fallito il
    # push (per esempio per un'interruzione di rete). In quel caso l'export è
    # invariato, ma il branch locale è ancora avanti rispetto al tracking ref:
    # riprendi la pubblicazione invece di dichiararla conclusa.
    if [ "$(git -C "$WC" rev-list --count origin/main..HEAD)" -gt 0 ]; then
      echo "   export invariato, riprendo il push del commit locale pendente"
      git -C "$WC" "${GIT_AUTH[@]}" push -q origin main
      echo "   ✓ pubblicato su $REPO"
      echo "   commit: $(git -C "$WC" rev-parse --short HEAD)  ($MODE)"
      exit 0
    fi
    echo "   nessuna differenza dal pubblico — niente da pushare"; exit 0
  fi
  git -C "$WC" commit -q -m "$MSG"
  git -C "$WC" "${GIT_AUTH[@]}" push -q origin main
fi
echo "   ✓ pubblicato su $REPO"
echo "   commit: $(git -C "${WC:-$DEST}" rev-parse --short HEAD)  ($MODE)"
