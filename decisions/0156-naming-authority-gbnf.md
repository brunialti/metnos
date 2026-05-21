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

### B. 4° livello descriptor — open, kebab-case, fuori grammar canonical

Estende §2.2 con un livello opzionale, separato da `#`:

| Livello | Vocabolario | Sintassi | Esempio |
|---------|-------------|----------|---------|
| 1: action | CHIUSO 23 verbi | enum | `compute` |
| 2: object | CHIUSO 19 oggetti | enum | `files` |
| 3: qualifier | CHIUSO 4 famiglie | enum | `loc` |
| 4: descriptor | **APERTO** | kebab-case | `per-language` |

Regole descriptor (enforce da `validate_name`):
- regex `^[a-z0-9]+(-[a-z0-9]+)*$` (kebab-case)
- max 30 caratteri
- NO underscore (riservato canonical 3-livello)
- NO leading/trailing hyphen
- NO doppi hyphen
- NON puo' coincidere con un verbo o oggetto §2.2 (anti pseudo-canonical)

Esempi validi:
- `compute_files_loc#per-language`
- `compute_files_loc#excluding-tests`
- `find_dirs_empty#recursive`
- `create_events#google-workspace`

Razionale separatore `#` + kebab-case:
- `#` visivo come "anchor" tipico (URL hash, markdown).
- kebab-case allineato a slug filename-safe / URL-safe / pipeline esterne.
- Separazione visuale netta dal canonical (underscore vs hyphen).
- Parsing robusto: `name.partition("#")` deterministico.

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
