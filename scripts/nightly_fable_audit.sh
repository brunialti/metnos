#!/usr/bin/env bash
# nightly_fable_audit.sh — analisi+miglioramento notturno del processo Metnos.
#
# Cron LOCALE 05:00 Europe/Rome (DOPO le attività notturne: docs-align 02:00,
# backup 03:30-03:40, prompts-translator 04:30). Agente Claude Code headless
# autonomo che analizza UNA dimensione a rotazione del percorso END-TO-END
# (query multitool/multidominio → intent → prefilter → proposer → executor →
# risposta), applica UN miglioramento su un branch, lo VERIFICA (suite + bench +
# sonda) e SOLO se tutto è verde lo mette in produzione (merge su main + restart).
# Modello: Fable fino al 22/6/2026 (gratis), poi Opus 4.8. Silenzioso: tutto su log.
#
#   Crontab (ora locale Europe/Rome). Invocato via 'bash' cosi' un eventuale
#   bit +x perso non lo uccide in silenzio (bug 11-12/6/2026: lo script aveva
#   perso +x dopo un edit -> cron falliva muto, una settimana di notti a vuoto):
#     0 5 * * * bash /opt/metnos/scripts/nightly_fable_audit.sh
set -uo pipefail

REPO="${METNOS_INSTALL_ROOT:-/opt/metnos}"
CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
LOG_DIR="$HOME/.metnos"
LOG="$LOG_DIR/fable-audit.log"
LOCK="$LOG_DIR/fable-audit.lock"
REPORT_DIR="$REPO/internal/reports"
mkdir -p "$LOG_DIR" "$REPORT_DIR"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

# Un solo run alla volta.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -Is) [fable-audit] altro run in corso, skip" >> "$LOG"
  exit 0
fi

cd "$REPO" || { echo "$(date -Is) [fable-audit] cd fallito" >> "$LOG"; exit 1; }

# Guard: parti SOLO da un working tree pulito sui file TRACCIATI (gli untracked —
# candidate i18n, report — non sono "modifiche di sessione" e non vanno bloccati).
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "$(date -Is) [fable-audit] tracked sporco → skip (no run su tree non pulito)" >> "$LOG"
  exit 0
fi

# Modello: Fable gratis fino al 22/6/2026, poi Opus 4.8.
TODAY="$(date +%Y%m%d)"
if [ "$TODAY" -le 20260622 ]; then MODEL="claude-fable-5"; else MODEL="claude-opus-4-8"; fi

# Rotazione: una dimensione per notte (ciclo di 5, indicizzato sul giorno dell'anno).
DIMS=("architettura" "algoritmi" "ingegneria-del-software" "analisi-del-codice" "ottimizzazione")
IDX=$(( (10#$(date +%j) - 1) % 5 ))
DIM="${DIMS[$IDX]}"
DATE="$(date +%Y%m%d)"
BRANCH="nightly/fable-${DATE}-${DIM}"

echo "==== $(date -Is) fable-audit START dim=$DIM model=$MODEL branch=$BRANCH ====" >> "$LOG"

# Branch di lavoro SEMPRE da main (force): mai dal branch corrente, altrimenti
# l'auto-merge su main trascinerebbe lavoro di sessione non revisionato.
# Il tree e' gia' garantito pulito dalla guardia sopra, quindi il checkout e' safe.
git checkout main >> "$LOG" 2>&1 || { echo "$(date -Is) [fable-audit] checkout main fallito" >> "$LOG"; exit 1; }
git checkout -B "$BRANCH" >> "$LOG" 2>&1 || { echo "$(date -Is) [fable-audit] branch fallito" >> "$LOG"; exit 1; }

# Prompt dell'agente. Heredoc NON quotato: espande $DIM/$BRANCH/$DATE; i $ letterali
# del corpo (env-var nei comandi) sono escapati con \$.
read -r -d '' PROMPT <<PROMPT_EOF
Sei un agente notturno autonomo di INGEGNERIA per Metnos (/opt/metnos). Lavori in
silenzio, senza prompt interattivi, e decidi da solo.

DIMENSIONE DI STANOTTE: ${DIM} (rotazione: una per notte).
BRANCH DI LAVORO: ${BRANCH} (già creato e attivo). Lavora SOLO qui; NON toccare main
finché il GATE non è verde.

OBIETTIVO: analizzare a fondo il processo END-TO-END di Metnos — da una query
multitool/multidominio (intent → prefilter → proposer → executor → risposta) — lungo
la dimensione di stanotte, e applicare UN miglioramento ben circoscritto e ad alto
valore. UNA cosa fatta bene e VERIFICATA vale più di dieci abbozzi.

⚠️ PRIORITÀ ASSOLUTA (mandato di Roberto, 11/6) — PRIMA della dimensione di rotazione:
leggi internal/reports/mandate_processi_analisi_generazione.md e dagli precedenza.
Migliora i processi di ANALISI/GENERAZIONE (synt/autocreazione, mnest/mnestoma,
telos/proposals, analisi dei CLUSTER) perché diventino EFFETTIVAMENTE utili/creativi/potenti:
alza la soglia desiderio-sintesi (meno proto-mnest, più forti); sintetizza dai CLUSTER, non
dalle singole istanze; sviluppa algoritmi che rendano evidenti i percorsi possibili/utili/usati;
chiudi il loop proposta-accettazione (oggi 1017 proposte / 2 accettate). Solo se quel fronte
è coperto, passa alla dimensione di rotazione di stanotte.

SIGNIFICATO DELLE DIMENSIONI:
- architettura: confini fra layer, accoppiamenti, SoT (single source of truth), duplicazioni strutturali.
- algoritmi: correttezza/efficienza di routing/ranking/parsing/scheduling.
- ingegneria-del-software: leggibilità, modularità, codice morto, error-handling (§2.8), copertura test.
- analisi-del-codice: bug latenti, edge case, race condition, gestione risorse.
- ottimizzazione: latenza/memoria/chiamate-LLM ridondanti, SENZA sacrificare correttezza.

VINCOLI (the design guide, vincolanti): §7.9 deterministico>LLM; §7.2 semplicità; §7.3 nessun
hardcoding, soluzione generale; §2.1 executor vettoriali; §2.2 vocabolario chiuso; §2.8
no silent failure. NON toccare: install/, credenziali/segreti, schema manifest,
baseline/gold dei bench, le sonde di test. Se modifichi un executor → ri-firmalo
(python3 runtime/sign.py sign executors/<nome>). NON indebolire MAI test/bench/gold per
far passare il gate (anti-gaming): se non riesci a migliorare ONESTAMENTE, NON fare
nulla e riportalo.

PROCEDURA:
1. Studia il percorso e2e; individua il SINGOLO miglioramento più utile nella dimensione
   di stanotte. Motiva la scelta.
2. Implementalo sul branch attivo. Aggiorna/aggiungi i test relativi.
3. GATE DI VERIFICA (TUTTI verdi, altrimenti NIENTE produzione). Aspetta che ogni comando
   STAMPI il risultato, non dedurlo:
   a. python3 -m py_compile <file toccati>
   b. python3 -m pytest runtime/tests/ -q -p no:cacheprovider   → ZERO fallimenti nuovi
      (preesistente noto e accettato: nessuno al 9/6/2026; se ne trovi, è una regressione).
   c. METNOS_ENGINE=metis METNOS_PROPOSER_GRAMMAR=1 METNOS_PROPOSER_VERB_FILTER=1 \\
      METNOS_PREFILTER_RULES=1 METNOS_ENGINE_POOL_SIZE=12 \\
      python3 bench/routing_subset_bench.py --runs 1 --baseline bench/routing_baseline.json
      → tutti i casi OK, "no regression" (oggi 26/26).
   d. se esiste /tmp/proposer_trap_probe.py: python3 /tmp/proposer_trap_probe.py → ACCURACY
      non inferiore all'ultimo valore noto (oggi 18/18).
4. ESITO (AUTO-MERGE — decisione Roberto 11/6: se il GATE è VERDE, vai in produzione da solo):
   - GATE VERDE → committa sul branch ${BRANCH} (messaggio chiaro + corpo di cosa/perché),
     poi metti in PRODUZIONE: git checkout main && git merge --no-ff ${BRANCH}
     -m "nightly(${DIM}): <sintesi>" ; poi riavvia: sudo -n systemctl restart metnos-http.service.
   - GATE ROSSO → NON mergere, NON riavviare. Lascia il branch ${BRANCH} per revisione umana;
     spiega nel report perché il gate è fallito. (Il gate suite+bench+sonda È il filtro: mai indebolirlo.)
5. Scrivi SEMPRE un report: internal/reports/fable-audit-${DATE}-${DIM}.md — cosa hai
   analizzato, il miglioramento (o perché niente), file toccati, ESITO COMPLETO del gate
   (numeri reali), e cosa hai segnalato ma non toccato.

Alla fine stampa un RIEPILOGO conciso (3-6 righe): dimensione, miglioramento, esito gate,
prodotto sì/no.
PROMPT_EOF

# Agente headless autonomo. Timeout di sicurezza 3 ore.
timeout 10800 "$CLAUDE" -p "$PROMPT" \
  --model "$MODEL" \
  --permission-mode bypassPermissions \
  --add-dir "$REPO" \
  >> "$LOG" 2>&1
rc=$?

# Se l'agente è uscito su main, ok; se è rimasto sul branch (gate rosso), torna a main pulito.
git checkout main >> "$LOG" 2>&1 || true
echo "==== $(date -Is) fable-audit END (rc=$rc) ====" >> "$LOG"
exit 0
