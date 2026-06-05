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
workspace/|
_history/|
\.claude/|
runtime_stub/|
deploy/|
decisions/|
docs/|
runtime/tests/|
runtime/testing/|
runtime/stress/|
runtime/bench_|
runtime/smoke|
runtime/run_all_tests\.py$|
scripts/(scrub-scan|audit_introvertiva|audit_quality_with_email|myclaw-unattended|migrate-syspath-to-package|rename-myclaw-to-metnos)|
scripts/scrub_names\.txt$|
scripts/export-public\.sh$|
scripts/publish-public\.sh$|
scripts/docs-align-nightly\.sh$|
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

mapfile -t ALL < <(git ls-files)
KEEP=(); DROP=()
for f in "${ALL[@]}"; do
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

# --- Audit del subset: nessuna PII residua tra i file INCLUSI ---
PII_EMAIL='roberto\.brunialti@|mykleos@|@knowcastle\.com|@migadu\.com'
PII_PATH='/home/roberto/'
fail=0
hits_email=$(printf '%s\n' "${KEEP[@]}" | git grep -lE "$PII_EMAIL" --no-index --stdin 2>/dev/null || true)
# git grep --stdin non universale: fallback a grep diretto sui file inclusi
hits_email=$(printf '%s\0' "${KEEP[@]}" | xargs -0 grep -lE "$PII_EMAIL" 2>/dev/null || true)
hits_path=$(printf '%s\0' "${KEEP[@]}" | xargs -0 grep -lE "$PII_PATH" 2>/dev/null || true)
if [ -n "$hits_email" ]; then echo "!! PII email nel subset:"; echo "$hits_email"; fail=1; fi
if [ -n "$hits_path" ]; then echo "!! PII path nel subset:"; echo "$hits_path"; fail=1; fi
[ "$fail" = 0 ] && echo "audit PII subset : OK (0 email, 0 /home/roberto/)"

if [ "$CHECK_ONLY" = 1 ]; then
  exit "$fail"
fi

# --- Copia nel DEST preservando l'albero ---
rm -rf "$DEST"
mkdir -p "$DEST"
printf '%s\0' "${KEEP[@]}" | rsync -a --files-from=- --from0 ./ "$DEST/" 2>/dev/null \
  || { while IFS= read -r -d '' f; do mkdir -p "$DEST/$(dirname "$f")"; cp -p "$f" "$DEST/$f"; done < <(printf '%s\0' "${KEEP[@]}"); }

# --- Sanificazione contenuto nei file esportati -------------------------------
# Regole:
#   - IP funzionale locale 192.168.1.33 -> localhost.
#   - Riferimenti penzolanti al doc interno "CLAUDE.md" (non pubblicato) ->
#     "the design guide" (i §X restano coerenti come rationale interno).
# I manifest executor FIRMATI e i .sig non vengono toccati (firma valida);
# nessun manifest firmato cita CLAUDE.md, quindi nessuna perdita.
while IFS= read -r -d '' f; do
  case "$f" in
    *.sig) continue ;;                       # mai le firme
    "$DEST"/executors/*/manifest.toml) continue ;;  # manifest firmati
  esac
  if grep -q '192\.168\.1\.33' "$f" 2>/dev/null; then
    sed -i 's/192\.168\.1\.33/localhost/g' "$f"
  fi
  if grep -q 'CLAUDE\.md' "$f" 2>/dev/null; then
    sed -i 's/CLAUDE\.md/the design guide/g' "$f"
  fi
done < <(find "$DEST" -type f -print0)

echo "DEST             : $DEST  (subset copiato + sanificato)"
echo "Nota: i modelli (gguf/onnx) e lo stato runtime si generano in install/."
exit 0
