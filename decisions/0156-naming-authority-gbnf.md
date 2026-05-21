---
id: 0156
title: Naming Authority centralizzata + 4° livello descriptor (kebab-case) + GBNF
date: 2026-05-21
status: accepted
area: naming
related:
  - 0045  # vocab chiuso §2.2 originale
  - 0133  # constrained generation tool_call (precedente uso GBNF)
  - 0150  # multi-tool fast-path memoization (genera naming derivato)
  - 0114  # synth admission policy (4 layers, name collision)
complements:
  - 0045
---

## Context

A monte del telos engine fase 2 (21/5/2026), 6 generatori di proposte
introspettive emettevano nomi di executor:
- `synt_multistage` stage 1 NAMING (synth nuovo)
- `introvertiva` (specialize/generalize)
- `multi_tool_promote` (synth derivato da chain L2)
- `telos_lenses/scamper` (modifica/nuovo executor)
- `telos_lenses/*` (8 lenti pending)
- `skill_importer` (mapping verbo provider → vocab Metnos)

Ognuno duplicava il vincolo del vocabolario chiuso §2.2 nel proprio
prompt. Bench Gemma 4 26B (21/5) ha mostrato 27% anti-pattern rate
in SCAMPER (verbi `audit_*`/`check_*`, qualifier `_aggregate`, ecc.)
nonostante il vocab fosse inline. Gemma "parrota" i vincoli ma li
applica peggio man mano che il prompt cresce.

Inoltre, l'iniziativa speculativa "telos engine" deve girare in
background (vincolo `docs/it/architecture/telos.html` §3) su modello
locale (parsimonia), ma:
- la qualita' Gemma scende sotto i 30 pt vs Sonnet 4.6 senza grammar;
- aggiungere `<verb>_<object>[_<qualifier>]` come vincolo prompt-only
  non basta (anti-pattern persiste);
- esiste gia' infrastruttura GBNF in `runtime/tool_grammar.py` (ADR
  0133) per il PLANNER, ma non e' generalizzata.

Inoltre, la pressione di generazione del telos engine fase 3 (8 lenti
× 7 telos × N variant) puo' produrre molti nomi con stesso
canonical 3-livello e semantica differente. Servono varianti
distinguibili senza estendere il vocab chiuso.

## Decision

### A. Naming Authority centralizzata

Nuovo modulo `runtime/naming_grammar.py` parsa `runtime/vocab.py`
(single source of truth) ed espone tre API:

1. `validate_name(name)` — deterministico §7.9, ritorna
   `ValidationResult(ok, reason, components)`. Catches:
   - verb fuori vocab (`audit`/`check`/`verify`/...)
   - object fuori vocab
   - qualifier fuori vocab
   - eccezioni semantiche (`entries`: no find/read/get_entries)
   - system pseudo-verbs riservati
   - descriptor 4-livello fuori sintassi
2. `naming_grammar_fragment(live_executors)` — genera fragment GBNF
   con enum vocab + sintassi canonical+descriptor. Inseribile in
   grammar piu' grandi (es. SCAMPER JSON outer).
3. `scamper_json_grammar(naming_fragment)` — GBNF completo per
   output array SCAMPER (executor_target + new_op_name + free-text
   action/rationale).

Tutti i generatori di proposte introspettive consultano la Naming
Authority. Centralizzazione = §7.3 (no hardcoded, single source).

### B. 4° livello descriptor — schema POSIZIONALE, separatore `_` unico

Estende §2.2 con un livello opzionale, SEMPRE in 4ª posizione (dopo
qualifier), separato da `_` come tutti i livelli canonical:

| Livello | Vocabolario | Sintassi | Esempio |
|---------|-------------|----------|---------|
| 1: action | CHIUSO 23 verbi | enum | `compute` |
| 2: object | CHIUSO 19 oggetti | enum | `files` |
| 3: qualifier | CHIUSO 4 famiglie | enum | `loc` |
| 4: descriptor | **APERTO** | kebab-case interno | `per-language` |

Schema canonical: `verb_object[_qualifier[_descriptor]]`

Regola d'oro: **il 4° livello ESTENDE, non RIMPIAZZA il 3°.**
Il descriptor puo' apparire SOLO se il qualifier e' presente.
Se serve estendere un nome a 2 livelli, la risposta giusta e':
- (a) usare un qualifier esistente, oppure
- (b) proporre nuovo qualifier in vocab §2.2 (escalation), oppure
- (c) lasciare nome 2-livello e mettere il contesto in proposed_action.

Regole descriptor (enforce da `validate_name`):
- regex `^[a-z0-9]+(-[a-z0-9]+)*$` (kebab-case interno)
- max 30 caratteri
- NO underscore (riservato a separatore livelli)
- NO leading/trailing hyphen, NO doppi hyphen
- NON puo' coincidere con verbo/oggetto §2.2 (anti pseudo-canonical)

### Regole semantiche aggiuntive (refinement 21/5/2026 v3)

**R1 — Un livello alla volta**: una proposta non puo' introdurre un
nuovo qualifier (3°) E un nuovo descriptor (4°) insieme. Implementazione:
`validate_name(name, live_canonicals=set)` rifiuta nomi 4-livello il cui
canonical 3-livello non e' gia' nel `live_canonicals` (catalog vivo).

Razionale: ogni livello che si aggiunge richiede giustificazione propria
(che qualifier? perche'? quale dominio?). Stack di nuovi token a un solo
colpo nasconde l'analisi e produce nomi senza fondamenta.

Sequenza giusta:
- Proposta 1: introduce `create_events_promotion` (3-livello, vocab
  governance: 3 criteri necessario/generale/comprensibile per `_promotion`
  applicato a `events`). Va a review.
- Proposta 2 (dopo accettazione 1): `create_events_promotion_nightly`
  (4-livello variant).

GBNF v3: `canonical-4-with-descriptor` enum filtrato a `canonical-3-live`
(executor con esattamente 3 parti gia' nel catalog).

**R2 — Descriptor = modificatore comportamentale**: il descriptor (4°)
deve cambiare *come* l'executor opera **a parita' di argomenti**.

OK pattern: `_dry-run`, `_per-language`, `_excluding-tests`, `_recursive`,
`_incremental`, `_streaming`, `_unified` (se sussume varianti reali),
`_v2` (nuovo contratto).

ERRORE pattern: `_nightly` (timing, va in scheduler `tasks`),
`_invoice-lifecycle` (dominio applicativo, va nel `proposed_action`),
`_meeting-reminders` (etichetta caso d'uso, non comportamento).

R2 e' euristica: validate_name oggi NON la enforce (richiederebbe
classificazione semantica). Va nel prompt come regola DEVI/NON DEVI
con pattern OK/ERRORE; il vaglio LLM judge a valle e' la rete di
sicurezza.

### Governance estensione vocab §2.2

Proporre un nuovo token (verbo, oggetto, qualifier) richiede TRE criteri
congiunti:
1. **Necessario** — nessun token della stessa classe e' semanticamente
   equivalente al proposto (solo lessicalmente diverso).
2. **Generale** — semantica riusabile, compositiva, non domain-specific.
3. **Comprensibile** — un LLM medium (Gemma 26B) coglie il significato
   senza glossa.

La proposta marca nel rationale "RICHIEDE estensione vocab §2.2:
<motivazione_3_criteri>". Va a review umana al digest serale.

Esempi validi:
- `compute_files_loc_per-language` (4-livello)
- `compute_files_loc_excluding-tests` (4-livello)
- `find_dirs_empty_recursive` (4-livello)
- `change_files_format_dry-run` (4-livello)
- `set_tasks` (2-livello)
- `delete_dirs_empty` (3-livello)

Esempi INVALIDI:
- `set_tasks_invoice-lifecycle` (descriptor senza qualifier)
- `create_events_from-file-metadata` (descriptor senza qualifier)
- `compute_files_loc_per_language` (descriptor con underscore → 5 parti, rifiutato)

Razionale separatore `_` posizionale (refinement 21/5/2026):
- Eliminato l'iniziale `#` separator: era asimmetrico (LLM con `_` emette
  `set_tasks_lifecycle` indistinguibile fra "lifecycle=qualifier" vs
  "lifecycle=descriptor"). Posizionale = univoco.
- Split deterministico `name.split("_")` → 2/3/4 parti.
- Descriptor con `-` interno (kebab) NON collide con `_` separator esterno.
- Forzare qualifier-prima-di-descriptor cattura naturalmente l'errore
  "context label" vs "behavior modifier": un context label (es.
  `invoice-lifecycle`) tipicamente non ha un qualifier-vocab che gli sta
  prima, mentre un behavior modifier (es. `per-language`) ha sempre
  ragionevolmente un qualifier base (es. `loc`).

### C. GBNF generator

`naming_grammar_fragment` produce regole GBNF compatibili llama.cpp:

```gbnf
target-name ::= "\"<live_exec_1>\"" | "\"<live_exec_2>\"" | ...
verb ::= "read" | "write" | "move" | ...           (23 alternative)
object-token ::= "files" | "dirs" | ...            (19 alternative)
qualifier-token ::= "csv" | "size" | ...           (N alternative)
canonical-only ::= "\"" verb "_" object-token ("_" qualifier-token)? "\""
desc-alnum ::= [a-z0-9]
desc-segment ::= desc-alnum desc-alnum*
canonical-with-descriptor ::= "\"" verb "_" object-token ("_" qualifier-token)? "#" desc-segment ("-" desc-segment)* "\""
new-op-name ::= canonical-only | canonical-with-descriptor | "null"
```

Wiring: `LlamaCppProvider.chat(grammar=)` (ADR 0133 estensione minima
del path expect_tools=False).

Bug fix llama.cpp GBNF documentati nel codice:
1. NON accetta `_` nei rule names (usiamo kebab-case interno).
2. NON accetta rule body multiline (item su singola riga).
3. NON supporta `{n,m}` repetition (usiamo `+`/`*` + cap via Python validator).

## Alternatives considered

### Alt 1 — Validator deterministico only (no grammar)

Pro: piu' semplice, niente debugging GBNF.

Contro: l'LLM continua a proporre nomi rotti, scartati post-hoc.
Spreca cicli LLM (Gemma 27% anti-pattern rate). Non scala alla
pressione di generazione del telos engine.

### Alt 2 — Fire dedicato (separato llama-server per naming)

Pro: isolamento massimo, possibile config differente.

Contro: +18GB RAM Gemma 26B (duplicato), complessita' systemd,
nessun beneficio reale visto che `:8080` e' idle a 03:30 AM.
id_slot=2 sullo stesso server e' equivalente con 0 RAM extra.

### Alt 3 — Estendere vocab chiuso §2.2 con nuovi qualifier

Pro: tutto resta 3-livello, nessun nuovo concetto.

Contro: vocab esplode (oggi 37 qualifier; con varianti diventerebbero
80+). Perdita di leggibilita' per LLM medium (§2.5). Inoltre, le
varianti che servono (per-language, excluding-tests, dry-run) sono
CONTESTUALI, non semantiche di dominio.

### Alt 4 — Tuple `(name, variant_id)` invece di stringa

Pro: schema piu' espressivo, separation of concerns netta.

Contro: rompe il pattern stringa-canonical usato ovunque (manifest,
mnestoma, multi_tool_paths, telemetria, dispatch). Refactor invasivo
senza valore aggiunto vs `name#variant`.

## Consequences

### Diventa piu' facile

- Aggiungere nuove lenti telos / generatori di proposte: usano la
  Naming Authority via 3 chiamate (`validate_name`, `naming_grammar_fragment`).
  Zero vocab inline da duplicare.
- Estendere il vocab §2.2 (in un solo punto: `runtime/vocab.py`); tutti
  i generatori si aggiornano automaticamente.
- Tracciare varianti di un canonical via descriptor (era impossibile
  prima senza estendere vocab o creare alias).
- Bench LLM su naming compliance (validator dato sempre 100%
  reproducibile, no jitter di prompt).

### Diventa piu' costoso

- Genera grammar GBNF per ogni chiamata (~2-5KB stringa per ~500
  executor + vocab completo). Costo trascurabile vs latency LLM.
- Debug GBNF richiede esperienza (3 bug llama.cpp scoperti in
  bring-up). Mitigato da documenti commento nel codice.

### Porte chiuse

- Lenti che propongono "concetti" (telos, super-verbi, vincoli) non
  fittano lo schema canonical. Per loro: disabilita grammar
  (`LENSES_NO_GRAMMAR`), affida ai soli vincoli prompt + paternalismo
  filter. Decisione esplicita nel dispatcher.

### Porte aperte

- Naming Authority estendibile a `suggest_canonical(intent_hint)` per
  importer skill (oggi mapping statico in `runtime/skill_vocab_map.json`).
- Telemetria per-grammar-rule (chi viola cosa, da quale generatore).
- Wiring grammar mode per consult_frontier (ADR 0142) per portare
  vocab compliance anche a Sonnet/Opus quando consultato.

### Bench risultati (21/5/2026)

| Setup | LLM | Utile | Anti-pattern | Naming valid | Costo |
|-------|-----|-------|--------------|--------------|-------|
| Sonnet 4.6 baseline | Sonnet | 61.9% | 0% | n/a | ~$0.40/127 |
| Gemma 26B fix prompt only | Gemma | 40.0% | 26.7% | n/a | $0 |
| **Gemma 26B + GBNF** | Gemma | **64.3%** | **0%** | **100%** | $0 |

GBNF batte Sonnet su rate-utile, a costo zero, con vocab 100% by
construction. Adottato come default opt-in per il telos engine.

### Bench iterazioni v2&ndash;v8 (21/5/2026)

| Iter | Setup | Lenti | Proposte | Naming valid | Errori principali |
|------|-------|-------|----------|--------------|-------------------|
| v2 | `#` sep | 9 | 29 | n/a | 6 context-label |
| v3 | `_` posizionale R1 | 9 | 26 | 26/26 | 6 qual-obj mismatch |
| v5b | R2 (canonical-3-must-exist) | 9 | 44 | 33/33 | 4 qual-obj mismatch |
| v6 | R4 qual-object compat map | 9 | 41 | 30/30 | 0 |
| v7 | + SCAMPER anti-fixation | 9 | 39 | 28/28 | scamper dup 26%&rarr;87% unici |
| **v8** | + counterfactual + constitutional | **10** | TBD | TBD | TBD |

### Le 10 lenti del telos engine (riferimenti)

`runtime/telos_lenses/` (modulare: aggiungere lente = 3 modifiche).

| Lens | Riferimento bibliografico |
|------|---------------------------|
| scamper | Eberle 1971, Osborn 1953 (Applied Imagination) |
| oulipo | Queneau &amp; Le Lionnais 1960 (Ouvroir de Litt&eacute;rature Potentielle) |
| inverse_rl | Russell 1998 (Learning agents for uncertain environments) |
| endgame_book | Thompson 1986 (chess endgame tablebases) |
| analogy_transfer | Hofstadter 1979 (GEB); Mitchell 2001 (Analogy-making as Perception) |
| boden_transformational | Boden 1990 (The Creative Mind: Myths &amp; Mechanisms) |
| compression | Schmidhuber 2010 (Formal Theory of Creativity, IEEE TAMD) |
| pattern_language | Alexander 1977 (A Pattern Language); Gamma 1994 (Design Patterns) |
| generative_design | Bentley 1999 (Evolutionary Design); Krish 2011 (Generative Design Method) |
| counterfactual | Shinn et al. 2023 (Reflexion, NeurIPS) |
| constitutional | Bai et al. 2022 (Constitutional AI, Anthropic) |

**10 lenti in produzione + 1 scartata dopo bench v8.**

`compression` (Schmidhuber 2010) e' stata implementata con prompt
restretto (forza cluster ≥2 executor) ma in 3 attempts consecutivi
Gemma 26B ha proposto `find_entries` come super-verbo che sussume
`find_files` + `find_dirs`. Questo viola l'eccezione semantica §2.2:
"entries e' meta-oggetto in-memory turno (niente find/read/get_entries)".
La convergence fallisce con invalid_naming=1 al primo tentativo, poi
n=0 nei due retry. Selezione meritocratica: lens rimossa dal registry
`_LENS_NAMES`. Idea Schmidhuber resta interessante: una versione futura
potrebbe richiedere al super-verbo di NON usare `entries` come object,
oppure scartare proposte che mappano N originali in 1 con object diverso
dall'unione semantica dei loro object.

Bench v8 finale: 10/10 lenti accettate convergono al primo tentativo
(0 paternalismo, 0 invalid naming, 0 qualifier-object mismatch). 44
proposte totali su t.tempo, ~17 acceptable, 12 borderline.
