#!/usr/bin/env bash
# export-public.sh — produce l'albero PUBBLICO di Metnos (subset run-essentials).
#
# Cornice (decisioni 5/6/2026, [[project-public-release-initiative]]):
#   - Baseline = versione in ESERCIZIO (/opt/metnos). NIENTE fork divergenti:
#     una sola sorgente, il pubblico e' un EXPORT-subset deterministico di questa.
#   - GitHub pubblico = SOLO run-essentials e certificazione pubblica:
#     `tests/portable/**` e `tests/windows_identity/**` sono i soli sottoalberi
#     di test pubblicati. Restano
#     esclusi supporto, bench, stress, simulator, history, stato runtime e
#     documentazione interna (CLAUDE.md).
#   - ADR e rapporti interni non vanno su GitHub. La documentazione pubblica
#     validata in docs/ viene invece distribuita: Tutor deve poter ricostruire
#     lo stesso corpus anche in un'installazione nuova.
#   - Sorgente dei componenti runtime = file tracciati piu' nuovi file non
#     ignorati, filtrati dal confine run-essential e dal gate PII. L'albero del
#     sito usa l'inventario pubblico validato, che e' anche il corpus di Tutor.
#   - I default funzionali locali (IP RFC1918 .33) sono sanificati QUI -> localhost.
#   - I manifest FIRMATI e i .sig NON vengono toccati (la firma deve restare valida).
#
# Uso:
#   scripts/export-public.sh [DEST]      # default DEST=dist/metnos-public
#   scripts/export-public.sh --check     # solo audit del subset, niente copia
#
# Deterministico (§7.9): nessun LLM, solo regex + git. Idempotente.
set -euo pipefail

REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"
PYTHON="${METNOS_VENV:-${REPO}/.venv}/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "ERRORE: ambiente Python di Metnos non trovato: $PYTHON" >&2
  exit 1
fi

# L'inventario usato dal sito e da Tutor e' un unico confine canonico. Il
# preflight rifiuta anche qualsiasi pagina collocata sotto `internal/`.
"$PYTHON" runtime/published_docs.py validate >/dev/null

CHECK_ONLY=0
DEST="dist/metnos-public"
case "${1:-}" in
  --check) CHECK_ONLY=1 ;;
  "") : ;;
  *) DEST="$1" ;;
esac

# --- EXCLUDE: anchored ERE su path tracciato. Cio' che NON e' run-essential. ---
EXCLUDE='^(
tests/|
conftest\.py$|
Documenti/|
internal/|
data/|
tools/research/|
workspace/|
_history/|
\.claude/|
runtime_stub/|
deploy/|
decisions/|
docs/([^/]+/)*internal/|
docs/drafts/|
runtime/testing/|
runtime/static/[^/]+\.html$|
runtime/prompts/(en|it)/_pending/|
runtime/bench_|
runtime/smoke|
scripts/(scrub-scan|audit_introvertiva|audit_quality_with_email|myclaw-unattended|migrate-syspath-to-package|rename-myclaw-to-metnos)|
scripts/(build_quick_tour_pdf|generate_builtin_executor_contracts|manage_complex_query_fixture)\.py$|
scripts/scrub_names\.txt$|
scripts/export-public\.sh$|
scripts/publish-public\.sh$|
scripts/(docs-align-nightly|nightly_docs_language)\.sh$|
deploy\.sh$|
claude_persistent\.sh$|
codex_persistent\.sh$|
executors/_retired/|
tutor/cards/retired/|
CLAUDE(\.mutabile)?\.md$|
AGENTS\.md$|
\.review_status\.md$|
report_llm_locale_vs_opus.*\.md$|
bench_prefilter_new_rules\.py$|
decisions/executor_requests\.jsonl$|
decisions/executor_diary\.md$
)'
# compatta le righe in un singolo ERE
EXCLUDE_RE=$(printf '%s' "$EXCLUDE" | tr -d '\n ')

# Estensioni binarie/dati mai distribuite (modelli/stato scaricati a parte).
BIN_RE='\.(gguf|onnx|safetensors|sqlite|sqlite-journal|env|key|pem|p12|db)$'

# Uniche eccezioni al confine `tests/`: prove di certificazione autonome che
# devono poter girare anche dal repository pubblico. Le esclusioni binarie
# continuano ad applicarsi al loro contenuto.
PUBLIC_TEST_RE='^tests/(portable|windows_identity)/'

# Eccezione binari: seed RUN-ESSENTIAL all'install (i18n) che NON e' stato/modello
# scaricabile a parte. Incluso, ma SANIFICATO via SQL piu' sotto (sed lo
# corromperebbe: le sostituzioni cambiano la lunghezza delle stringhe).
BIN_KEEP_RE='^install/data/i18n_seed\.sqlite$'

# Un indice temporaneo permette al release gate di includere nuovi file
# run-essential gia' validati senza alterare lo staging dell'operatore. Il
# valore non viene mai passato ai comandi Git del repository pubblico.
if [ -n "${METNOS_PUBLIC_INDEX_FILE:-}" ]; then
  mapfile -t ALL < <(GIT_INDEX_FILE="$METNOS_PUBLIC_INDEX_FILE" \
    git ls-files --cached --others --exclude-standard)
else
  mapfile -t ALL < <(git ls-files --cached --others --exclude-standard)
fi
# Le pagine canoniche appena generate possono non essere ancora nell'indice
# Git. L'inventario pubblico e' gia' stato validato sopra ed e' l'autorita' per
# docs/, quindi entra esplicitamente nell'insieme da esportare.
PUBLIC_DOCS_RAW=$("$PYTHON" runtime/published_docs.py files)
mapfile -t PUBLIC_DOCS <<<"$PUBLIC_DOCS_RAW"
ALL+=("${PUBLIC_DOCS[@]}")
mapfile -t ALL < <(printf '%s\n' "${ALL[@]}" | LC_ALL=C sort -u)
KEEP=(); DROP=()
for f in "${ALL[@]}"; do
  # Un path cancellato nel worktree puo' restare nell'indice fino al commit
  # che registra il ritiro. L'export rappresenta lo stato corrente e non deve
  # provare a copiare un artefatto che non esiste piu'.
  if [ ! -e "$f" ] && [ ! -L "$f" ]; then
    DROP+=("$f")
    continue
  fi
  if [[ "$f" =~ $BIN_KEEP_RE ]]; then
    KEEP+=("$f"); continue
  fi
  if [[ "$f" =~ $BIN_RE ]]; then
    DROP+=("$f")
  elif [[ "$f" =~ $EXCLUDE_RE ]] && [[ ! "$f" =~ $PUBLIC_TEST_RE ]]; then
    DROP+=("$f")
  else
    KEEP+=("$f")
  fi
done

echo "== Export subset =="
echo "tracciati totali : ${#ALL[@]}"
echo "INCLUSI          : ${#KEEP[@]}"
echo "ESCLUSI          : ${#DROP[@]}"

# --- Copia nel DEST preservando l'albero ---
rm -rf "$DEST"
mkdir -p "$DEST"
printf '%s\0' "${KEEP[@]}" | rsync -a --files-from=- --from0 ./ "$DEST/" 2>/dev/null \
  || { while IFS= read -r -d '' f; do mkdir -p "$DEST/$(dirname "$f")"; cp -p "$f" "$DEST/$f"; done < <(printf '%s\0' "${KEEP[@]}"); }

# Un workflow pubblico non deve poter citare una suite che il filtro ha
# escluso. Il controllo usa soltanto riferimenti letterali sotto `tests/` e
# fallisce prima della pubblicazione, invece di lasciare che GitHub scopra il
# disallineamento dopo il push.
if [ -d "$DEST/.github/workflows" ]; then
  mapfile -t WORKFLOW_TEST_PATHS < <(
    grep -rhoE 'tests/[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*' \
      "$DEST/.github/workflows" 2>/dev/null | LC_ALL=C sort -u || true
  )
  for relative in "${WORKFLOW_TEST_PATHS[@]}"; do
    if [ ! -e "$DEST/$relative" ]; then
      # Un percorso dichiarato come output del workflow nasce durante il job e
      # non deve esistere nel commit sorgente. Gli input e le suite citati
      # soltanto come argomenti restano invece soggetti al controllo.
      if grep -Rqs -- "--output $relative" "$DEST/.github/workflows"; then
        continue
      fi
      echo "ERRORE: workflow pubblico riferisce un percorso escluso: $relative" >&2
      exit 1
    fi
  done
fi

# --- Sanificazione contenuto nei file esportati -------------------------------
# Mappa IP/host RFC1918 locali -> range documentazione (RFC5737/RFC3849), cosi'
# gli esempi restano realistici ma non rivelano la rete reale.
#   192.168.1.33 (host)  -> 192.0.2.10      192.168.1.20 (NAS) -> 192.0.2.20
#   IPv6 ULA fda2:...    -> 2001:db8::1      nas.local          -> host.local
#   enp197s0 (iface)     -> eth0
#   "CLAUDE.md" (doc interno non pubblicato) -> "the design guide".
# I manifest firmati, i relativi metadati e tutti i payload dichiarati in
# `[code].files` non vengono toccati. Anche cambiare un commento dopo la firma
# invaliderebbe il digest. Se un payload firmato contiene PII, il gate finale
# deve abortire: va corretto e rifirmato nella sorgente, mai sanificato qui.
declare -A SIGNED_PAYLOADS=()
while IFS= read -r -d '' relative; do
  SIGNED_PAYLOADS["$relative"]=1
done < <("$PYTHON" - "$DEST" <<'PY'
import os
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1]).resolve()
for manifest_path in sorted(root.rglob("manifest.toml")):
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        continue
    for filename in (manifest.get("code") or {}).get("files", []):
        if not isinstance(filename, str):
            continue
        payload = (manifest_path.parent / filename).resolve()
        try:
            relative = payload.relative_to(root).as_posix()
        except ValueError:
            continue
        sys.stdout.buffer.write(os.fsencode(relative) + b"\0")
PY
)
while IFS= read -r -d '' f; do
  relative="${f#"$DEST"/}"
  if [[ -n "${SIGNED_PAYLOADS[$relative]+present}" ]]; then
    continue
  fi
  case "$f" in
    *.sig) continue ;;                              # mai le firme
    "$DEST"/executors/*/manifest.toml) continue ;;  # manifest firmati (gia' puliti a monte)
    "$DEST"/executors/*/manifest.lang_state.json) continue ;;
    "$DEST"/runtime/builtin_executor_contracts/*/manifest.toml) continue ;;
    "$DEST"/runtime/builtin_executor_contracts/*/manifest.lang_state.json) continue ;;
    *.sqlite) continue ;;                           # binario: sed lo corromperebbe (sanific. SQL piu' sotto)
  esac
  sed -i -E \
    -e 's,192\.168\.0\.0/([0-9]+),@@PRIV_RFC1918_\1@@,g' \
    -e 's/192\.168\.1\.33/192.0.2.10/g' \
    -e 's/192\.168\.1\.20/192.0.2.20/g' \
    -e 's/192\.168\.[0-9]+\.[0-9]+/192.0.2.0/g' \
    -e 's,@@PRIV_RFC1918_([0-9]+)@@,192.168.0.0/\1,g' \
    -e 's/fd[0-9a-f]{2}:[0-9a-f:]+/2001:db8::1/g' \
    -e 's/\bnas\.local\b/host.local/g' \
    -e 's/\benp197s0\b/eth0/g' \
    -e 's,/home/roberto,/home/user,g' \
    -e 's/[Ii]acopo[_ ][Bb]runialti/guest_user/g' \
    -e 's/[Rr]oberto [Bb]runialti/the owner/g' \
    -e 's/CLAUDE\.md/the design guide/g' \
    -e 's/[Ii]acopo/guest_user/g' \
    -e 's/metnos_roberto/metnos_secondary/gI' \
    -e 's/mykleos/account_personal/gI' \
    -e 's/knowcastle/account_work/gI' \
    -e 's/tiscali/account_isp/gI' \
    -e 's/\bimap\.register\.it\b/imap.example.com/g' \
    -e 's/\bauthsmtp\.securemail\.pro\b/smtp.example.com/g' \
    -e 's/\bregister\.it\b/example.com/g' \
    -e 's/\bsecuremail\.pro\b/example.com/g' \
    "$f" 2>/dev/null || true
done < <(find "$DEST" -type f -print0)

# --- Sanificazione del seed i18n (binario) via SQL ----------------------------
# sed corromperebbe il .sqlite (offset/lunghezze). Le stesse mappe di scrub
# applicate alle colonne testo del DB. Idempotente. Deterministico (§7.9).
SEED="$DEST/install/data/i18n_seed.sqlite"
if [ -f "$SEED" ]; then
  "$PYTHON" - "$SEED" <<'PY'
import re, sqlite3, sys
def scrub(s):
    if not isinstance(s, str):
        return s
    s = s.replace('/home/roberto', '/home/user')
    # Protegge la rete RFC1918 generica `192.168.0.0/N` (NON è PII: range privato
    # standard) dallo scrub host: altrimenti `192.168.0.0/16`→`192.0.2.0/16` =
    # CIDR INVALIDO (host bits set) → boot crash. Stessa difesa del sed sopra.
    s = re.sub(r'192\.168\.0\.0/([0-9]+)', r'@@PRIV_RFC1918_\1@@', s)
    s = re.sub(r'192\.168\.1\.33', '192.0.2.10', s)
    s = re.sub(r'192\.168\.1\.20', '192.0.2.20', s)
    s = re.sub(r'192\.168\.[0-9]+\.[0-9]+', '192.0.2.0', s)
    s = re.sub(r'@@PRIV_RFC1918_([0-9]+)@@', r'192.168.0.0/\1', s)
    s = re.sub(r'fd[0-9a-f]{2}:[0-9a-f:]+', '2001:db8::1', s)
    s = re.sub(r'\bnas\.local\b', 'host.local', s)
    s = re.sub(r'\benp197s0\b', 'eth0', s)
    s = re.sub(r'[Ii]acopo[_ ][Bb]runialti', 'guest_user', s)
    s = re.sub(r'[Rr]oberto [Bb]runialti', 'the owner', s)
    s = s.replace('CLAUDE.md', 'the design guide')
    s = re.sub(r'\bmetnos_roberto\b', 'metnos_secondary', s)
    s = re.sub(r'\bmykleos\b', 'account_personal', s)
    s = re.sub(r'\bknowcastle\b', 'account_work', s)
    s = re.sub(r'\btiscali\b', 'account_isp', s)
    s = re.sub(r'\bimap\.register\.it\b', 'imap.example.com', s)
    s = re.sub(r'\bauthsmtp\.securemail\.pro\b', 'smtp.example.com', s)
    s = re.sub(r'\bregister\.it\b', 'example.com', s)
    s = re.sub(r'\bsecuremail\.pro\b', 'example.com', s)
    return s
db = sqlite3.connect(sys.argv[1])
cur = db.cursor()
cols = [r[1] for r in cur.execute("PRAGMA table_info(i18n)")]
text_cols = [c for c in ("text", "key") if c in cols]
changed = 0
for col in text_cols:
    rows = cur.execute(f"SELECT rowid, {col} FROM i18n").fetchall()
    for rid, val in rows:
        new = scrub(val)
        if new != val:
            cur.execute(f"UPDATE i18n SET {col}=? WHERE rowid=?", (new, rid))
            changed += 1
db.commit()
cur.execute("VACUUM")
db.close()
print(f"   seed i18n sanificato: {changed} valori scrubbati")
PY
fi

# Strip cruft eventuale (bytecode generato da run nel tree).
find "$DEST" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$DEST" -type f -name '*.pyc' -delete 2>/dev/null || true

# --- Audit del subset FINALE (post-scrub, sul DEST realmente spedito) ---------
# Gate larga: email reali, path personali, IP interni, IPv6 ULA, host/username.
PII_EMAIL='roberto\.brunialti@|mykleos@|@knowcastle\.com|@migadu\.com'
# path personali (no trailing-slash) + nomi propri di TERZI (famiglia).
# Leciti (NON in gate): 'github.com/brunialti/metnos' (URL repo) e
# 'author = "Roberto Brunialti"' (attribuzione del proprietario nel suo repo).
# NB: niente `\b` sui nomi/account — un nome DENTRO un identificatore snake_case
# (es. `guest_iacopo`, `MYKLEOS_MAIL_USER`) non ha boundary e sfuggiva al gate;
# il grep è case-insensitive (`-i`) per prendere anche le forme MAIUSCOLE.
PII_PERSON='/home/roberto|iacopo|silvia|matteo'
# 192.168.X.Y solo se è un HOST specifico (3°/4° ottetto ≠ 0): la rete RFC1918
# generica `192.168.0.0`/`192.168.0.0/16` è un costante standard (NON PII), tenuta
# apposta (trusted-LAN) → esente dal gate.
PII_NET='192\.168\.[0-9]+\.[1-9][0-9]*|192\.168\.[1-9][0-9]*\.[0-9]+|fd[0-9a-f]{2}:|\bnas\.local\b|\benp197s0\b'
# Topologia account di posta reale (nomi-account + host personali): rivela
# datore/ISP/provider del proprietario. Lo scrub sopra li sostituisce; questo
# gate aborta se qualcosa sopravvive (es. un manifest firmato non sterilizzato).
# Canonical account identifiers (metnos_roberto/mykleos/knowcastle/tiscali)
# are functional vocabulary values retained for backward-compatible routing;
# they are not credentials. Real hostnames and account addresses remain gated.
PII_MAIL='register\.it|securemail\.pro'
# Il tunnel remoto del maintainer e' configurazione privata dell'istanza, non
# un componente distribuibile. Il gate copre nome unit, dominio e descrizioni
# brandizzate per impedire che rientrino nel runtime o nella documentazione.
PRIVATE_TUNNEL='cloudflared|chat\.metnos\.com|cloudflare.{0,80}tunnel|tunnel.{0,80}cloudflare'
fail=0
hits_email=$(grep -rIlE "$PII_EMAIL" "$DEST" 2>/dev/null || true)
hits_person=$(grep -rIlE "$PII_PERSON" "$DEST" 2>/dev/null || true)
hits_net=$(grep -rIlE "$PII_NET" "$DEST" 2>/dev/null || true)
hits_mail=$(grep -rIlE "$PII_MAIL" "$DEST" 2>/dev/null || true)
hits_tunnel=$(grep -rIliE "$PRIVATE_TUNNEL" "$DEST" 2>/dev/null || true)
if [ -n "$hits_email" ];  then echo "!! PII email nel subset:";  echo "$hits_email";  fail=1; fi
if [ -n "$hits_person" ]; then echo "!! PII persona/path nel subset:"; echo "$hits_person"; fail=1; fi
if [ -n "$hits_net" ];    then echo "!! rete/host interni nel subset:"; echo "$hits_net"; fail=1; fi
if [ -n "$hits_mail" ];   then echo "!! account/host posta reali nel subset:"; echo "$hits_mail"; fail=1; fi
if [ -n "$hits_tunnel" ]; then echo "!! tunnel privato nel subset:"; echo "$hits_tunnel"; fail=1; fi
[ "$fail" = 0 ] && echo "audit subset     : OK (0 PII/host/account/tunnel privato)"

if [ "$CHECK_ONLY" = 1 ]; then
  rm -rf "$DEST"
  echo "DEST             : (rimosso, --check)"
  exit "$fail"
fi

echo "DEST             : $DEST  (subset copiato + sanificato + audited)"
echo "Nota: i modelli (gguf/onnx) e lo stato runtime si generano in install/."
exit "$fail"
