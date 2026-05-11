---
id: 0057
title: Stage 5 synt — architettura modulare verb→prompt
date: 2026-04-29
status: accepted
area: synt
related:
  - 0051  # synt-multistage 5 stages
  - 0053  # stage 1 bilingual mapping
  - 0056  # tool routing, budget, thinking
complements:
  - 0051  # estende stage 5 dello stesso framework (5 stage invariati,
          #   solo il prompt del 5° stage diventa modulare per verbo)
modifies: []
supersedes: []
---

## Context

Il 29/4/2026 sera, dopo 4 cicli synt consecutivi falliti su
`move_messages` (con bug diversi: SyntaxError, account default
errato, `from messages import messages` auto-reference, fallback
`try/except ImportError` che maschera errore reale), e' diventato
chiaro che il prompt monolitico STAGE5_PROMPT_TEMPLATE non scala.

Lo state al momento della decisione:
- Vocabolario chiuso a 21 verbi × 11 oggetti.
- 4 cicli synt da ~150s ognuno, 0 mail effettivamente spostate, 16
  mail eliminate da knowcastle/INBOX in un test precedente per via
  di un bug COPY-then-EXPUNGE-without-check.
- 6+ ore di iterazioni sul singolo prompt monolitico, ognuna fixava
  un bug ma scopriva il prossimo: chiaro segnale che la causa root
  e' strutturale, non puntuale.

Il prompt monolitico mescolava in un unico blocco:
- Principi universali (vettorialita', schema output, main(), best-effort).
- Pattern verbo-specifici (Pattern A IMAP move ~80 righe Python misto
  pseudocodice e commenti meta-istruttivi).
- API runtime enumerate con signature.
- Anti-allucinazione.
- Contratto from_step.

Il modello (Gemma 4 26B wise) non riesce a tenere insieme tutti i
vincoli, e quando il pattern by-example e' specifico per un dominio
(IMAP) tende a copiarlo letteralmente anche per altri verbi (es.
compress_files, write_files).

## Decision

Refactor incrementale del prompt stage 5 in **architettura modulare
verbo→prompt**, in 4 step.

### Step 1 (eseguito 29/4 sera) — riscrittura prompt generico
- Rinominato `STAGE5_PROMPT_TEMPLATE` → `STAGE5_GENERIC_PROMPT`.
- Eliminato il template specifico IMAP misto (Pattern A ~80 righe).
- Sostituito con principi STRUTTURALI generali in stile DO/DON'T/OK/ERRORE
  (`feedback_prompt_writing_method`):
  - Vincoli librerie (stdlib + PIL + openpyxl).
  - API runtime enumerate con signature complete.
  - Contratto `from_step → args["entries"]`.
  - Schema output `{ok, ok_count, fail_count, results|entries, failed}`.
  - Vettorialita' by default.
  - Integrita' dati per verbi destructive (COPY-CHECK-DELETE, results
    schema completo, identita' dedotta da entries).
  - Best-effort (entry fallita non blocca le altre).
  - Anti-allucinazione (no API non listate).
- Lunghezza: ~1900 token (vs ~2400 precedenti).
- Pattern B (hashing) e C (msg_or fallback) mantenuti come pattern
  generici riutilizzabili per molti verbi.
- File: `/opt/myclaw/runtime/synt_multistage.py:446`.

### Step 2 (eseguito 29/4 sera) — tabella VERB_PROMPTS
- Dizionario `VERB_PROMPTS` con 22 entry: `_default` + 21 verbi del
  vocabolario chiuso (read, write, move, delete, create, find, list,
  filter, sort, group, classify, get, set, fetch, send, describe,
  render, extract, compress, compute, compare).
- Tutti i verbi inizialmente puntano a `STAGE5_GENERIC_PROMPT`
  (zero regressioni rispetto al baseline pre-refactor).
- `run_stage5` legge `stage1["action"]` e seleziona il prompt:
  `VERB_PROMPTS.get(verb) or VERB_PROMPTS["_default"]`.
- Validazione: `format()` test su tutti i 22 prompt con args fittizi,
  zero KeyError (graffe correttamente escapate dove servono).

### Step 3 (eseguito 29/4/2026 sera, batch unico) — prompt specializzati
Tutti i verbi gia' usati almeno una volta nel corpus executor (13/21)
sono stati specializzati in una sessione, non incrementalmente come previsto:

Critici (DESTRUCT/CRITICAL):
1. `move` — COPY-CHECK-DELETE rigoroso FS+IMAP, schema results con
   message_id per swap_src_dst, anti-regression sul caso 16 mail perse.
2. `delete` — snapshot/blob backup pre-unlink se revertible=true,
   platform_policy gating, recursive esplicito.
3. `send` — fail-fast credenziali, anti-spam cap, idempotency_key persistito,
   no leak di segreti negli errori, manifest revertible=false.
4. `write` — overwrite gate, atomic write (tmp + os.replace), prev_blob_sha256
   per restore_blob_backup, encoding esplicito.
5. `extract` — path traversal protection per zip/tar slip, cap
   max_entries/max_bytes_total con truncated visibility, dispatch per format,
   output sempre lista.

READ/TRANSFORM:
6. `read` — read-only, manifest revertible=false, cap output con truncated
   visibility, encoding fallback, body_preview cap su IMAP.
7. `create` — idempotente (exist_ok), tracking ancestor effettivamente creati
   per delete_created_dirs bottom-up, collisione di tipo gestita.
8. `find` — schema entries completo, cap max_entries, protected_path skip
   silenzioso, patterns sempre lista.
9. `list` — listing flat (NO recursive default), ordinamento stabile esplicito,
   cap, schema entries identico a find.
10. `get` — enrichment-style (output `entries` non `results`), preserva campi
    originali, fields esplicito, cache locale per network costs.
11. `filter` — pure transformation, criterion semantics
    (substring/regex/numeric/date/enum), case-insensitive default,
    filtered_out_count visibile.
12. `fetch` — read-only outbound (GET/HEAD), urllib stdlib, timeout per request,
    max_bytes_per_response cap, manifest revertible=false.
13. `describe` — pure compute readonly, determinismo (no random unseeded, no
    now()), NaN/Inf coerenti in JSON, sampling esplicito per dataset grandi.

Composizione: `_compose_verb_prompt(addendum)` splica l'addendum subito prima
del blocco "I/O CONTRACT" del generico (drift mitigation: principi universali
restano nel generico, regole di dominio si aggiungono).

### Step 4 (futuro emergente)
Restanti 8 verbi del vocabolario chiuso ancora su `_default` (nessun executor
li usa al 29/4/2026): `sort`, `group`, `classify`, `set`, `render`, `compress`,
`compute`, `compare`. Saranno specializzati alla prima necessita' o al primo
bug ricorrente.

## Alternatives considered

### A. Continuare iter sul prompt monolitico
Scartato dopo 4 cicli falliti con bug diversi (`feedback_invest_in_recurring_fix`).
Patch puntuali stavano sopprimendo sintomi senza fixare la causa
strutturale (mescolanza di vincoli universali + dominio-specifici in
un unico blocco).

### B. Scrivere un template per ogni combinazione verbo×oggetto
Scartato come "follia" da Roberto: 21 × 11 = 231 prompt. Ingestibile
e duplicativo (le regole verbo si ripeterebbero per ogni oggetto).

### C. Promuovere primitive critiche a SEED scritte a mano
Considerato come uscita pragmatica per `move_messages`. Dichiarato
come eccezione consentita a `feedback_no_manual_executor_rewrite`
quando un verbo specializzato non converge in N≤3 cicli synt. NON
in conflitto con questa ADR: il prompt verbo-specifico copre i casi
ricorrenti generici, l'eccezione SEED resta possibile per primitive
con data integrity critica che non convergono.

### D. Frontier LLM per autoring del prompt verbo
Riflessione architetturale: usare Claude Opus 4.7 / GPT-5 / etc.
per scrivere il prompt verbo-specifico in background quando il synt
fallisce N volte sul `_default`. Documentato in
`metnos_frontier_llm_for_prompt_authoring`. Non implementato ora —
da rivisitare dopo 5+ verbi a mano.

## Consequences

### Positive
- Bug isolati per verbo: fix in un file mirato, niente disturbo agli altri verbi.
- Aggiungere verbo nuovo = aggiungere 1 entry alla tabella + scrivere il prompt
  (scaling lineare, non combinatorio).
- Documentazione vivente per dominio: ogni prompt verbo specializzato e' anche
  la specifica architetturale di quel verbo.
- Zero regressioni iniziali: step 2 con tutti i verbi su `_default` mantiene
  il comportamento attuale.

### Negative
- Investimento iniziale ~6-8 ore per scrivere i 5 prompt verbo critici (step 3).
- Rischio drift fra i 21 prompt: cambio di principio comune (es. nuovo schema
  output) richiede update propagato.
- Selezione prompt dipende da stage 1 corretto sul verbo. Se stage 1 sbaglia,
  prompt sbagliato. Mitigato da fallback `_default`.

### Mitigazioni
- `_default` come fallback per verbi non specializzati o stage1 outlier.
- Il prompt generico contiene tutti i principi universali, quindi qualunque
  verbo non-specializzato e' coperto dalle regole base.
- Birth tests post-install (`_validate_birth_tests` in synth_request.py)
  rifiutano executor synth con SyntaxError o test failures, indipendentemente
  dal prompt.

## Status

Step 1+2 in `accepted` (eseguiti 29/4 sera, validazione `format()` ok).
Step 3 in `accepted` (eseguito 29/4 sera, batch unico anziche' incrementale —
13 verbi specializzati: move/delete/send/write/extract/read/create/find/list/
get/filter/fetch/describe; format() OK su tutti i 22 prompt; synt module 56/56
verde dopo refactor).
Step 4 in `proposed` (8 verbi non ancora usati restano su `_default`: sort,
group, classify, set, render, compress, compute, compare).
