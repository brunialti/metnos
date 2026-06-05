#!/usr/bin/env bash
# scrub-scan.sh — one-shot audit di publishing readiness del repo Metnos.
#
# Uso:
#   scripts/scrub-scan.sh           # report stdout, exit 0 sempre
#   scripts/scrub-scan.sh --strict  # exit 1 se trova violazioni (CI / pre-push)
#
# Implementa ADR 0145 §"Scrub policy" layer L2+L4 deterministico §7.9.
# NON modifica nulla: solo scansione + report.
#
# Categorie ricercate:
#   - Email personali / company-specific
#   - Hostname / IP locali
#   - Nomi propri di persone reali
#   - Pattern di secret (API key, PAT, BOT_TOKEN)
#   - File binari/dati che non devono essere pushati
set -euo pipefail

STRICT=0
if [ "${1:-}" = "--strict" ]; then STRICT=1; fi

cd "$(git rev-parse --show-toplevel)"

VIOLATIONS=0
SECTION() { printf '\n=== %s ===\n' "$1"; }

count_matches() {
  local n
  # Esclude lo script stesso: contiene i pattern come stringhe-definizione
  # (self-match), non PII reale.
  n=$(git grep -lE "$1" -- . ':(exclude)scripts/scrub-scan.sh' 2>/dev/null | wc -l)
  echo "$n"
}

SECTION "L2.1 — Email personali / company"
PATTERN_EMAIL='roberto\.brunialti@|mykleos@|metnos@knowcastle|@knowcastle\.com|@migadu\.com'
N=$(count_matches "$PATTERN_EMAIL")
if [ "$N" -gt 0 ]; then
  echo "VIOLAZIONI: $N file"
  git grep -lE "$PATTERN_EMAIL" 2>/dev/null | head -30
  VIOLATIONS=$((VIOLATIONS + N))
fi

SECTION "L2.2 — Hostname / IP locali"
PATTERN_HOST='\bbeelink\b|\b192\.168\.1\.3[0-9]\b|\b192\.168\.1\.31\b|/home/roberto/'
N=$(count_matches "$PATTERN_HOST")
if [ "$N" -gt 0 ]; then
  echo "VIOLAZIONI: $N file"
  git grep -lE "$PATTERN_HOST" 2>/dev/null | head -30
  VIOLATIONS=$((VIOLATIONS + N))
fi

SECTION "L2.3 — Pattern secret"
# Tokenized: GitHub PAT, OpenAI key, Anthropic key, Bot token, Fernet key
PATTERN_SECRET='ghp_[A-Za-z0-9]{36}|sk-ant-[A-Za-z0-9_-]{40,}|sk-proj-[A-Za-z0-9_-]{40,}|sk-[A-Za-z0-9]{40,}|[0-9]{8,}:[A-Za-z0-9_-]{30,}'
N=$(count_matches "$PATTERN_SECRET")
if [ "$N" -gt 0 ]; then
  echo "VIOLAZIONI: $N file con potenziali secret"
  git grep -lE "$PATTERN_SECRET" 2>/dev/null | head
  VIOLATIONS=$((VIOLATIONS + N))
fi

SECTION "L2.4 — Nomi propri (configurabile via scripts/scrub_names.txt)"
NAMES_FILE="scripts/scrub_names.txt"
if [ -f "$NAMES_FILE" ]; then
  while IFS= read -r name; do
    [ -z "$name" ] && continue
    [ "${name:0:1}" = "#" ] && continue
    M=$(git grep -lE "\\b$name\\b" 2>/dev/null | wc -l)
    if [ "$M" -gt 0 ]; then
      echo "  $name: $M file"
      VIOLATIONS=$((VIOLATIONS + M))
    fi
  done < "$NAMES_FILE"
else
  echo "(nessun $NAMES_FILE — crea il file con un nome per riga per attivare)"
fi

SECTION "L4.1 — File binari/dati che non devono essere tracciati"
BAD=$(git ls-files | grep -E '\.(gguf|onnx|safetensors|jsonl|sqlite|sqlite-journal|env|key|pem|p12)$' || true)
if [ -n "$BAD" ]; then
  echo "VIOLAZIONI: $(echo "$BAD" | wc -l) file tracciati"
  echo "$BAD" | head -20
  VIOLATIONS=$((VIOLATIONS + $(echo "$BAD" | wc -l)))
fi

SECTION "L4.2 — Path/dir non distribuibili gia' nel index"
BAD2=$(git ls-files | grep -E '^(workspace/|_history/|\.claude/|decisions/synt_stress/|decisions/executor_requests\.jsonl|decisions/executor_diary\.md|claude_persistent\.sh|\.review_status\.md|x\.png|deploy\.sh)' || true)
if [ -n "$BAD2" ]; then
  echo "VIOLAZIONI: $(echo "$BAD2" | wc -l) file tracciati"
  echo "$BAD2" | head -20
  VIOLATIONS=$((VIOLATIONS + $(echo "$BAD2" | wc -l)))
fi

SECTION "Riepilogo"
echo "Totale violazioni: $VIOLATIONS"

if [ "$STRICT" = "1" ] && [ "$VIOLATIONS" -gt 0 ]; then
  echo "STRICT mode: exit 1"
  exit 1
fi
exit 0
