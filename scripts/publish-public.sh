#!/usr/bin/env bash
# publish-public.sh — sincronizza lo sviluppo (/opt/metnos) col repo PUBBLICO
# (brunialti/metnos) "a regime", in modo sicuro e ripetibile.
#
# Cosa fa, in un comando:
#   1. rigenera l'export-subset deterministico (scripts/export-public.sh)
#   2. CANCELLO DURO anti-PII/secret sull'albero da pubblicare → ABORTA se trova
#      qualcosa (email reali, /home/roberto/, token/secret pattern, il PAT stesso)
#   3. pubblica su GitHub:
#        - default: commit SINGOLO + force-push (storia pulita, zero PII storica)
#        - --incremental: mantiene la storia pubblica (commit incrementali)
#
# Il PAT GitHub viene letto dal credential-store cifrato (runtime/credentials),
# mai passato in argv (§10.1). Repo override: env METNOS_PUBLIC_REPO.
#
# Uso:
#   scripts/publish-public.sh -m "messaggio"        # snapshot pulito (default)
#   scripts/publish-public.sh --incremental -m "…"  # con storia pubblica
#   scripts/publish-public.sh --check               # solo gate, niente push
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

REPO="${METNOS_PUBLIC_REPO:-brunialti/metnos}"
DEST="dist/metnos-public"
MSG="Metnos — public snapshot"
MODE="snapshot"     # snapshot | incremental
CHECK_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    -m|--message) MSG="$2"; shift 2;;
    --incremental) MODE="incremental"; shift;;
    --check) CHECK_ONLY=1; shift;;
    *) echo "arg sconosciuto: $1" >&2; exit 2;;
  esac
done

echo "== 1. rigenero export =="
bash scripts/export-public.sh "$DEST" >/dev/null
echo "   file: $(find "$DEST" -type f -not -path '*/.git/*' | wc -l)"

echo "== 2. CANCELLO DURO anti-PII/secret =="
fail=0
# PII reale
if grep -rlE 'roberto\.brunialti@|mykleos@|@knowcastle\.com|@migadu\.com|/home/roberto/|587627005' "$DEST" 2>/dev/null \
   | grep -v 'scrub-scan.sh\|export-public.sh\|publish-public.sh'; then
  echo "   !! PII trovata ^"; fail=1
fi
# secret pattern
if grep -rlE 'ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|sk-(ant|proj)?-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----' "$DEST" 2>/dev/null; then
  echo "   !! secret pattern trovato ^"; fail=1
fi
# file sensibili per estensione
if find "$DEST" -type f \( -name '*.env' -o -name '*.age' -o -name '*.key' -o -name '*.pem' -o -name 'google_token.json' -o -name '*client_secret*' \) 2>/dev/null | grep .; then
  echo "   !! file sensibile presente ^"; fail=1
fi
# il PAT stesso nell'albero
TOK=$(python3 -c "import sys; sys.path.insert(0,'runtime'); import credentials; d=credentials.load('github'); print((d or {}).get('password',''))" 2>/dev/null || true)
if [ -n "$TOK" ] && grep -rlF "$TOK" "$DEST" 2>/dev/null | grep .; then
  echo "   !! IL TOKEN GITHUB È NELL'ALBERO ^^^"; fail=1
fi
if [ "$fail" != 0 ]; then
  echo "   ABORT: l'export non è pulito. Niente push."; exit 1
fi
echo "   gate OK: 0 PII, 0 secret, 0 file sensibili, token assente"

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

GIT_AUTH=(-c "credential.helper=!f() { echo username=x-access-token; echo \"password=\$GHTOKEN\"; }; f")
export GHTOKEN="$TOK"

echo "== 3. pubblico su $REPO (mode=$MODE) =="
if [ "$MODE" = "snapshot" ]; then
  rm -rf "$DEST/.git"; write_pub_gitignore "$DEST"
  git -C "$DEST" init -q -b main
  git -C "$DEST" config user.name "Roberto Brunialti"
  git -C "$DEST" config user.email "brunialti@users.noreply.github.com"
  git -C "$DEST" add -A
  git -C "$DEST" commit -q -m "$MSG"
  git -C "$DEST" remote add origin "https://github.com/$REPO.git"
  git -C "$DEST" "${GIT_AUTH[@]}" push --force -q origin main
else
  WC="dist/.public-repo"
  if [ ! -d "$WC/.git" ]; then
    rm -rf "$WC"
    git "${GIT_AUTH[@]}" clone -q "https://github.com/$REPO.git" "$WC"
    git -C "$WC" config user.name "Roberto Brunialti"
    git -C "$WC" config user.email "brunialti@users.noreply.github.com"
  fi
  # sostituisci il contenuto tracciato col nuovo export (preserva .git)
  find "$WC" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
  cp -r "$DEST"/. "$WC"/; rm -rf "$WC/.git/index.lock" 2>/dev/null || true
  write_pub_gitignore "$WC"
  git -C "$WC" add -A
  if git -C "$WC" diff --cached --quiet; then
    echo "   nessuna differenza dal pubblico — niente da pushare"; exit 0
  fi
  git -C "$WC" commit -q -m "$MSG"
  git -C "$WC" "${GIT_AUTH[@]}" push -q origin main
fi
echo "   ✓ pubblicato su $REPO"
echo "   commit: $(git -C "${WC:-$DEST}" rev-parse --short HEAD)  ($MODE)"
