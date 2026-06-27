#!/usr/bin/env bash
# nightly_docs_language.sh — riscrittura LINGUISTICA notturna dei doc tecnici di metnos.com.
#
# Cron HOST (sopravvive alle sessioni Claude; il cron del harness e' session-only e muore).
# Agente Claude headless autonomo che riprende la passata lingua+fattuale documentata in
# ~/.claude/projects/-opt-metnos/memory/project_docs_language_rewrite.md e lavora UN doc
# (IT+EN) per notte, in-place sui file docs/ (copia di lavoro NON pubblicata).
#
# MULTI-NOTTE FINCHE' NON FINITO (Roberto 25/6, revisione di "una notte sola"): ogni
# notte porta avanti il PIU' POSSIBILE della lista DA FARE, valida e fa ./deploy.sh su
# Cloudflare (progresso incrementale su metnos.com). Si auto-disattiva (rimuove la propria
# riga di crontab) SOLO quando l'agente dichiara la lista DA FARE VUOTA scrivendo la
# sentinella $HOME/.metnos/docs-language.done. Finche' resta lavoro il cron sopravvive e
# riprende la notte dopo. NIENTE commit/merge git, NIENTE restart servizi. Tocca SOLO file
# sotto docs/{it,en}/. Mai dialoghi/landing/Prospettive. Report + memoria aggiornati ogni notte.
#
#   Crontab (ora locale Europe/Rome), invocato via 'bash' (un +x perso non lo uccide muto):
#     13 2 * * * bash /opt/metnos/scripts/nightly_docs_language.sh
set -uo pipefail

REPO="${METNOS_INSTALL_ROOT:-/opt/metnos}"
CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
LOG_DIR="$HOME/.metnos"
LOG="$LOG_DIR/docs-language.log"
LOCK="$LOG_DIR/docs-language.lock"
DONE="$LOG_DIR/docs-language.done"   # sentinella: l'agente la scrive quando DA FARE è vuota
MEM="$HOME/.claude/projects/-opt-metnos/memory/project_docs_language_rewrite.md"
REPORT_DIR="$REPO/internal/reports"
mkdir -p "$LOG_DIR" "$REPORT_DIR"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
# Sentinella stantia (run precedente) → rimossa: conta solo la decisione di stanotte.
rm -f "$DONE"

# Un solo run alla volta.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -Is) [docs-language] altro run in corso, skip" >> "$LOG"
  exit 0
fi

cd "$REPO" || { echo "$(date -Is) [docs-language] cd fallito" >> "$LOG"; exit 1; }

# La memoria di handoff DEVE esistere: e' la fonte autosufficiente del lavoro.
if [ ! -f "$MEM" ]; then
  echo "$(date -Is) [docs-language] memoria handoff assente ($MEM) -> skip" >> "$LOG"
  exit 0
fi

MODEL="claude-opus-4-8"
DATE="$(date +%Y%m%d)"
echo "==== $(date -Is) docs-language START model=$MODEL ====" >> "$LOG"

read -r -d '' PROMPT <<PROMPT_EOF
Sei un agente notturno autonomo per la documentazione pubblica di Metnos (/opt/metnos).
Lavori in silenzio, senza prompt interattivi, e decidi da solo. Massima cura linguistica.

LEGGI PRIMA, per intero: ${MEM}
E' la fonte autosufficiente: contiene mandato, fatti verificati, lista DA FARE con righe/
stringhe esatte, e il vocabolario anglicismi->IT. Segui quella.

COMPITO DI STANOTTE (hai fino a 3 ore; il lavoro PROSEGUE le notti successive finche' la
lista DA FARE non e' vuota): riprendi la riscrittura e porta a termine il PIU' POSSIBILE
della lista DA FARE (ogni doc: IT + gemello EN), nell'ordine indicato. Non fermarti a uno:
prosegui finche' hai tempo/contesto. MA la qualita' viene prima della quantita': meglio
cinque doc fatti benissimo che dieci sciatti. A fine notte lo script pubblica su metnos.com
cio' che hai prodotto, quindi lascia solo lavoro di cui ti fidi: se un doc non e'
all'altezza, NON chiuderlo a meta' — lascialo in DA FARE (lo riprendi domani notte).

REGOLE VINCOLANTI:
- SOLO documentazione tecnica: docs/{it,en}/architecture/* e i libretti tecnici
  (QuickTour, Architettura_Intro, Glossario, code.html). MAI toccare: dialoghi
  (Metnos_Dialogo*), landing (docs/{it,en}/index.html), saggi Metnos_Prospettive_*.
- Italiano VERO, non tradotto parola-per-parola: il difetto da evitare e' la prosa che
  "suona sintetizzata". Modello di registro = la landing e i dialoghi (NON editarli, solo
  leggerli come riferimento di stile). Riscrivi la PROSA a mano tu; usa eventuali subagent
  SOLO per localizzare stringhe, MAI per scrivere prosa.
- Inglese idiomatico nel gemello EN, non IT tradotto.
- LLM SEMPRE astratto = il tier (fast/middle/wise/frontier); MAI "Qwen"/"Gemma"/nome modello
  in prosa/SVG/figcaption/tabelle. ECCEZIONE: i valori model="...gguf" dentro blocchi <code>
  (esempi di config TOML) si TENGONO. Niente etichette di versione interne ("Engine v2/v3")
  nei doc: di' "il motore"/"il pianificatore".
- Termini propri TENUTI (non tradurre): path, fastpath, autopath, executor, intent, framework,
  cache, embedding, tier, mnest, mnestoma, telos, vaglio, synt, praxis, scratchpad, skill,
  backend, sandbox, pairing, channel, ReAct, hash, GBNF, LRU, TTL.
- Applica anche i FIX FATTUALI elencati nella memoria (es. lifecycle IT: 4->6 sorgenti +
  /admin/proposals -> /admin/changes; eventuali ULID mnest_01HW -> mn_...).

PROCEDURA per ogni doc:
1. Read il file IT intero. Riscrivi la prosa in italiano naturale; applica i fix fattuali.
2. Read il gemello EN; rendilo idiomatico con la stessa astrazione e gli stessi fix.
3. Valida ENTRAMBI: python3 -c "import html.parser;p=html.parser.HTMLParser();p.feed(open('FILE').read());print('ok')"
   e grep -n "Qwen\\|Gemma\\|Engine v\\|mnest_01HW" FILE (atteso: nessun match in prosa).

LIMITI DURI (NON oltrepassare): NON fare git commit / merge / push. NON riavviare servizi.
NON toccare codice runtime/, install/, ne' file fuori da docs/. Il deploy lo fa lo SCRIPT
dopo di te (non lanciarlo tu): tu lascia i docs/ pronti e validati.

ALLA FINE:
- Aggiorna ${MEM}: sposta i doc completati da DA FARE a FATTO, con una riga di cosa hai cambiato.
- Scrivi un report: ${REPORT_DIR}/docs-language-${DATE}.md (doc toccati, sintesi modifiche,
  esito validazione con numeri reali, cosa resta).
- SOLO SE la lista DA FARE in ${MEM} e' ora COMPLETAMENTE VUOTA (tutti i doc tecnici
  rivisti, IT+EN): crea il file sentinella ${DONE} (anche vuoto, `touch`). E' il segnale
  allo script che il lavoro e' finito e che puo' auto-disattivare il cron. Se resta anche
  un solo doc DA FARE, NON creare ${DONE}: il cron deve riprendere domani notte.
- Stampa un RIEPILOGO conciso (3-6 righe).
PROMPT_EOF

# Agente headless autonomo. Timeout di sicurezza 3 ore.
timeout 10800 "$CLAUDE" -p "$PROMPT" \
  --model "$MODEL" \
  --permission-mode bypassPermissions \
  --add-dir "$REPO" \
  >> "$LOG" 2>&1
rc=$?
echo "$(date -Is) [docs-language] agente headless rc=$rc" >> "$LOG"

# Validazione di sicurezza: tutti gli HTML toccati sotto docs/ devono essere ben formati,
# e nessun leak di modello/versione in prosa. Se fallisce, NON deployare.
DEPLOY_OK=1
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if ! python3 - "$f" <<'PY' >> "$LOG" 2>&1
import sys, html.parser
p = html.parser.HTMLParser(); p.feed(open(sys.argv[1]).read())
PY
  then echo "$(date -Is) [docs-language] HTML malformato: $f -> NO deploy" >> "$LOG"; DEPLOY_OK=0; fi
done < <(git -C "$REPO" status --porcelain -- 'docs/*.html' 'docs/**/*.html' | awk '{print $2}')

# Deploy su Cloudflare SOLO se la validazione e' pulita.
if [ "$DEPLOY_OK" = "1" ]; then
  echo "==== $(date -Is) docs-language DEPLOY start ====" >> "$LOG"
  if bash "$REPO/deploy.sh" >> "$LOG" 2>&1; then
    echo "==== $(date -Is) docs-language DEPLOY ok ====" >> "$LOG"
  else
    echo "==== $(date -Is) docs-language DEPLOY FALLITO (rc=$?) ====" >> "$LOG"
  fi
else
  echo "$(date -Is) [docs-language] validazione fallita -> deploy saltato" >> "$LOG"
fi

# Auto-disattivazione SOLO a lista DA FARE vuota: l'agente ha creato la sentinella $DONE.
# Finche' resta lavoro il cron sopravvive e riprende la notte dopo (multi-notte).
if [ -f "$DONE" ]; then
  TMP_CRON="$(mktemp)"
  if crontab -l 2>/dev/null | grep -v "nightly_docs_language.sh" > "$TMP_CRON"; then
    crontab "$TMP_CRON" && echo "$(date -Is) [docs-language] DA FARE vuota → cron auto-rimosso" >> "$LOG"
  fi
  rm -f "$TMP_CRON"
else
  echo "$(date -Is) [docs-language] DA FARE non vuota (no sentinella) → cron resta per domani" >> "$LOG"
fi

echo "==== $(date -Is) docs-language END (rc=$rc) ====" >> "$LOG"
exit 0
