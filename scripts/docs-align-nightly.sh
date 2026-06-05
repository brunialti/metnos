#!/usr/bin/env bash
# docs-align-nightly.sh — allineamento notturno della documentazione al codice.
#
# Eseguito da cron LOCALE (sopravvive alle sessioni). Lancia un agente Claude
# Code headless e autonomo che allinea docs/ + README al codice reale, poi
# committa, deploya su metnos.com e (se il README cambia) sincronizza il repo
# pubblico. Silenzioso: tutto su log.
#
# Crontab (ora locale Europe/Rome):
#   0 2 * * * /opt/metnos/scripts/docs-align-nightly.sh
set -uo pipefail

REPO="${METNOS_INSTALL_ROOT:-/opt/metnos}"
CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
LOG_DIR="$HOME/.metnos"
LOG="$LOG_DIR/docs-align.log"
LOCK="$LOG_DIR/docs-align.lock"
mkdir -p "$LOG_DIR"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

# Un solo run alla volta
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -Is) [docs-align] altro run in corso, skip" >> "$LOG"
  exit 0
fi

echo "==== $(date -Is) docs-align START ====" >> "$LOG"

read -r -d '' PROMPT <<'PROMPT_EOF'
Sei un agente notturno autonomo per Metnos (/opt/metnos). Obiettivo: rendere la
documentazione ASSOLUTAMENTE allineata al codice reale. Lavora in silenzio e
committa/deploya da solo. NON modificare mai codice runtime. NON riavviare servizi.

GROUND TRUTH = il codice. Se una doc afferma qualcosa che il codice non conferma,
correggi la doc (mai il codice).

AMBITO: docs/it/architecture/*.html, docs/en/architecture/*.html, README.md.

REGOLE DI CONTENUTO (vincolanti):
1. RIMUOVI ogni riferimento a versioni precedenti di Metnos e alla storia evolutiva
   ("prima era…", "v0.0.1", "in passato", changelog narrativi). History do-not-care:
   descrivi solo lo STATO ATTUALE.
2. NON referenziare gli ADR in quanto tali: vietato scrivere "ADR 0xxx" o "vedi ADR".
   Va benissimo ILLUSTRARE la decisione progettuale e il perché — ma senza citare la
   sigla del documento di decisione.
3. DOCUMENTI SUPERATI: individua le pagine la cui materia non esiste più nel codice
   (modulo/funzione/feature rimossi o sostituiti). Se una pagina è chiaramente
   obsoleta → CANCELLALA e rimuovi TUTTI i link/riferimenti ad essa (indice, sitemap,
   cross-link in altre pagine). Nel dubbio NON cancellare: segnala nel riepilogo.
4. TONO: sempre "technical-but-accessible" (technical dummy): chiaro, concreto,
   leggibile da un tecnico non specialista. Niente gergo non spiegato.
5. Allinea numeri/nomi/flussi/env-var/endpoint al codice. Correggi link morti.
6. IT ed EN devono restare SIMMETRICI (stesso set di pagine, stesse sezioni).

PROCEDURA:
- Trova cosa è cambiato di recente: git -C /opt/metnos log --since="36 hours ago"
  --name-only -- runtime/ executors/ install/ scripts/ ; concentra la revisione
  sulle pagine che descrivono quelle aree, ma applica le REGOLE DI CONTENUTO ovunque
  le incontri.
- Per ogni pagina toccata: verifica contro il codice, correggi, mantieni IT==EN.
- Guard: se esistono, esegui scripts/pre-commit-symmetry-it-en.sh e
  runtime/prompts_lint.py; non procedere al deploy se segnalano errori sulle pagine
  che hai toccato.
- Commit: git add -A (solo docs/ e README.md) && commit con messaggio
  "docs: allineamento notturno al codice" + breve corpo di cosa hai cambiato.
- Deploy: se hai cambiato docs/, esegui ./deploy.sh (aggiorna metnos.com). Se hai
  cambiato README.md, esegui anche scripts/publish-public.sh -m "docs: nightly alignment".
- Se NON c'è nulla da allineare: NON committare, NON deployare; stampa
  "nothing to align".

Alla fine stampa un RIEPILOGO conciso: pagine toccate, doc cancellate (con motivo),
riferimenti-ADR/versioni rimossi, e cosa hai segnalato ma non toccato.
PROMPT_EOF

cd "$REPO" || { echo "$(date -Is) [docs-align] cd fallito" >> "$LOG"; exit 1; }

# Agente headless autonomo (no prompt interattivi). Timeout di sicurezza 90 min.
timeout 5400 "$CLAUDE" -p "$PROMPT" \
  --permission-mode bypassPermissions \
  --add-dir "$REPO" \
  >> "$LOG" 2>&1
rc=$?
echo "==== $(date -Is) docs-align END (rc=$rc) ====" >> "$LOG"
exit 0
