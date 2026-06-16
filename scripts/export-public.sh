#!/usr/bin/env bash
# export-public.sh — produce l'albero PUBBLICO di Metnos (subset run-essentials).
#
# Cornice (decisioni 5/6/2026, [[project-public-release-initiative]]):
#   - Baseline = versione in ESERCIZIO (/opt/metnos). NIENTE fork divergenti:
#     una sola sorgente, il pubblico e' un EXPORT-subset deterministico di questa.
#   - GitHub pubblico = SOLO run-essentials: niente ambienti di test/supporto,
#     bench, stress, simulator, history, stato runtime, doc interne (CLAUDE.md).
#   - ADR (decisions/) e docs/ NON vanno su GitHub: rationale interno, vivono su
#     metnos.com. Restano tracciati nel git di lavoro (baseline), fuori dall'export.
#   - Sorgente di verita' dei file = `git ls-files` (solo tracciati).
#   - I default funzionali locali (IP RFC1918 .33) sono sanificati QUI -> localhost.
#   - I manifest FIRMATI e i .sig NON vengono toccati (la firma deve restare valida).
#
# Uso:
#   scripts/export-public.sh [DEST]      # default DEST=dist/metnos-public
#   scripts/export-public.sh --check     # solo audit del subset, niente copia
#
# Deterministico (§7.9): nessun LLM, solo regex + git. Idempotente.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

CHECK_ONLY=0
DEST="dist/metnos-public"
case "${1:-}" in
  --check) CHECK_ONLY=1 ;;
  "") : ;;
  *) DEST="$1" ;;
esac

# --- EXCLUDE: anchored ERE su path tracciato. Cio' che NON e' run-essential. ---
EXCLUDE='^(
e2e/|
internal/|
bench/|
workspace/|
_history/|
\.claude/|
runtime_stub/|
deploy/|
decisions/|
docs/|
runtime/tests/|
runtime/testing/|
runtime/poc/|
runtime/stress/|
runtime/bench_|
runtime/smoke|
runtime/run_all_tests\.py$|
scripts/(scrub-scan|audit_introvertiva|audit_quality_with_email|myclaw-unattended|migrate-syspath-to-package|rename-myclaw-to-metnos)|
scripts/scrub_names\.txt$|
scripts/export-public\.sh$|
scripts/publish-public\.sh$|
scripts/docs-align-nightly\.sh$|
scripts/e2e-fresh-install\.sh$|
deploy\.sh$|
claude_persistent\.sh$|
CLAUDE\.md$|
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

# Eccezione binari: seed RUN-ESSENTIAL all'install (i18n) che NON e' stato/modello
# scaricabile a parte. Incluso, ma SANIFICATO via SQL piu' sotto (sed lo
# corromperebbe: le sostituzioni cambiano la lunghezza delle stringhe).
BIN_KEEP_RE='^install/data/i18n_seed\.sqlite$'

mapfile -t ALL < <(git ls-files)
KEEP=(); DROP=()
for f in "${ALL[@]}"; do
  if [[ "$f" =~ $BIN_KEEP_RE ]]; then
    KEEP+=("$f"); continue
  fi
  if [[ "$f" =~ $EXCLUDE_RE ]] || [[ "$f" =~ $BIN_RE ]]; then
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

# --- Sanificazione contenuto nei file esportati -------------------------------
# Mappa IP/host RFC1918 locali -> range documentazione (RFC5737/RFC3849), cosi'
# gli esempi restano realistici ma non rivelano la rete reale.
#   192.168.1.33 (host)  -> 192.0.2.10      192.168.1.20 (NAS) -> 192.0.2.20
#   IPv6 ULA fda2:...    -> 2001:db8::1      nas.local          -> host.local
#   enp197s0 (iface)     -> eth0
#   "CLAUDE.md" (doc interno non pubblicato) -> "the design guide".
# I manifest executor FIRMATI e i .sig non vengono toccati (firma valida); la
# sorgente dei manifest e' gia' sanificata (es. get_processes), quindi safe.
while IFS= read -r -d '' f; do
  case "$f" in
    *.sig) continue ;;                              # mai le firme
    "$DEST"/executors/*/manifest.toml) continue ;;  # manifest firmati (gia' puliti a monte)
    *.sqlite) continue ;;                           # binario: sed lo corromperebbe (sanific. SQL piu' sotto)
  esac
  sed -i -E \
    -e 's/192\.168\.1\.33/192.0.2.10/g' \
    -e 's/192\.168\.1\.20/192.0.2.20/g' \
    -e 's/192\.168\.[0-9]+\.[0-9]+/192.0.2.0/g' \
    -e 's/fd[0-9a-f]{2}:[0-9a-f:]+/2001:db8::1/g' \
    -e 's/\bnas\.local\b/host.local/g' \
    -e 's/\benp197s0\b/eth0/g' \
    -e 's,/home/roberto,/home/user,g' \
    -e 's/[Ii]acopo[_ ][Bb]runialti/guest_user/g' \
    -e 's/[Rr]oberto [Bb]runialti/the owner/g' \
    -e 's/CLAUDE\.md/the design guide/g' \
    -e 's/\bmetnos_roberto\b/metnos_secondary/g' \
    -e 's/\bmykleos\b/account_personal/g' \
    -e 's/\bknowcastle\b/account_work/g' \
    -e 's/\btiscali\b/account_isp/g' \
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
  python3 - "$SEED" <<'PY'
import re, sqlite3, sys
def scrub(s):
    if not isinstance(s, str):
        return s
    s = s.replace('/home/roberto', '/home/user')
    s = re.sub(r'192\.168\.1\.33', '192.0.2.10', s)
    s = re.sub(r'192\.168\.1\.20', '192.0.2.20', s)
    s = re.sub(r'192\.168\.[0-9]+\.[0-9]+', '192.0.2.0', s)
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
PII_PERSON='/home/roberto|\b[Ii]acopo\b|\b[Ss]ilvia\b|\b[Mm]atteo\b'
PII_NET='192\.168\.[0-9]+\.[0-9]+|fd[0-9a-f]{2}:|\bnas\.local\b|\benp197s0\b'
# Topologia account di posta reale (nomi-account + host personali): rivela
# datore/ISP/provider del proprietario. Lo scrub sopra li sostituisce; questo
# gate aborta se qualcosa sopravvive (es. un manifest firmato non sterilizzato).
PII_MAIL='\bmetnos_roberto\b|\bmykleos\b|\bknowcastle\b|\btiscali\b|register\.it|securemail\.pro'
fail=0
hits_email=$(grep -rlE "$PII_EMAIL" "$DEST" 2>/dev/null || true)
hits_person=$(grep -rlE "$PII_PERSON" "$DEST" 2>/dev/null || true)
hits_net=$(grep -rlE "$PII_NET" "$DEST" 2>/dev/null || true)
hits_mail=$(grep -rlE "$PII_MAIL" "$DEST" 2>/dev/null || true)
if [ -n "$hits_email" ];  then echo "!! PII email nel subset:";  echo "$hits_email";  fail=1; fi
if [ -n "$hits_person" ]; then echo "!! PII persona/path nel subset:"; echo "$hits_person"; fail=1; fi
if [ -n "$hits_net" ];    then echo "!! rete/host interni nel subset:"; echo "$hits_net"; fail=1; fi
if [ -n "$hits_mail" ];   then echo "!! account/host posta reali nel subset:"; echo "$hits_mail"; fail=1; fi
[ "$fail" = 0 ] && echo "audit subset     : OK (0 email, 0 nomi/path, 0 IP/host, 0 account posta)"

if [ "$CHECK_ONLY" = 1 ]; then
  rm -rf "$DEST"
  echo "DEST             : (rimosso, --check)"
  exit "$fail"
fi

echo "DEST             : $DEST  (subset copiato + sanificato + audited)"
echo "Nota: i modelli (gguf/onnx) e lo stato runtime si generano in install/."
exit "$fail"
