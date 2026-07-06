# SPEC IMPLEMENTATIVA — CP5 grammar-on-args (constrained decoding sugli argomenti)

> **Destinatario**: LLM esecutore (Opus o inferiore). File:riga verificati, criteri di done, test, metrica.
> **Obiettivo**: vincolare via GBNF gli args a DOMINIO CHIUSO (enum) generati dal proposer, per ridurre i guard reattivi che oggi li correggono a posteriori. Spike misurato dietro flag, A/B.
> **Autore analisi**: Fable, 6/7/2026. **Già formalmente aperto** in ADR 0177 T2/M4/D2 e mandato Fable CP5·M4.

---

## 0. Il quadro (fatti verificati)

Esistono **3 grammatiche GBNF** nel repo; solo UNA è sul path del proposer runtime:

1. **`runtime/engine/grammar_framework.py`** — grammatica del proposer runtime. Vincola la FORMA Framework + i NOMI-tool (`toolName ::=` alternation del pool, `:53-77`). **Gli args sono JSON LIBERO**: `args ::= "{" ... kv ... "}"`, `value ::= string|number|...` (`:29-36`). **È il gap.** Wiring: `runtime/engine/proposer.py:458` (`METNOS_PROPOSER_GRAMMAR`, default ON), `:522-530` (`build_framework_grammar`). Metis: `proposer_metis.py:284-410`.
2. **`runtime/naming_grammar.py`** — grammatica vocab §2.2 per proporre NOMI NUOVI (path telos/synt, non runtime).
3. **`runtime/tool_grammar.py`** — macchina schema-args→GBNF completa (ADR 0133, 43 test), del planner LEGACY **rimosso il 4/7**. **ORFANA ma riusabile**: `_emit_value(schema)` (`:230-290`) mappa JSON Schema→GBNF, `enum → alternation di literal` (`:246-251`) = esattamente il vincolo dominio-chiuso. `_emit_tool_args` (`:376-409`), `generate_tool_grammar` (`:487`).

Provider: `runtime/llm_provider.py:342-408` — `chat(grammar=...)` passa `payload["grammar"]` al llama-server per-richiesta; forza `think=False` con la grammar.

**Il debito** (GUARD_PIPELINE, `runtime/engine/dispatch.py:2195-2224`, 14 guard): ~7-10 esistono per correggere args emessi sbagliati dal proposer libero:

| Guard | riga | Corregge |
|---|---|---|
| `_overwrite_phantom_install_args` | `:1906-1933` | rimuove base_path/path fantasma |
| `_fill_clause_args` | `:1402-1556` | riempie pattern/date/store; corregge `pattern='*'` |
| `_demote_overtight_caps` | `:1337-1363` | toglie cap non richiesti |
| `_promote_count_cap` | `:1366-1399` | inietta cap dal conteggio |
| `_decontaminate_reader_qualifier` | `:2055-2085` | demote formato contaminato |
| `_scope_sink_provider_to_clause` | `:2011-2052` | client esplicito sul sink |
| `_align_provider_client` | pipeline | allinea client/provider |
| `_route_mail_delete_to_trash` | pipeline | dst_folder |
| `_degenerate_find_to_list` | `:1936-1998` | riscrive args find→list |

---

## 1. PERCHÉ vale la pena (e perché grammar-on-verbs NO)

grammar-on-verbs fu **accantonata** (ADR 0174 D4, CLAUDE.md §11) per 2 motivi:
1. **Ridondanza**: `_align_framework_objects`+`_enforce_missing_clauses` già garantiscono la struttura.
2. **Amplifica garbage-in**: verbo/oggetto derivano da `intent.actions` (output LLM dell'extractor); vincolarli irrigidisce un input incerto.

**Per gli args NESSUNO dei due vale:**
1. **Non-ridondanza**: NON esiste un enforce sui VALORI degli args — la garanzia è affidata ai ~7-10 guard reattivi + un hint SOFT nel prompt (`proposer.py:131-142`) che il modello locale sotto contesa ignora (documentato ADR 0156:29-32, 0174:137).
2. **Non amplifica**: il vincolo-args si àncora allo **schema del manifest** (`Executor.args_schema`, dominio-chiuso §2.4) = **ground-truth deterministica indipendente dall'LLM**, non a `intent.actions`. È il caso della naming grammar (ADR 0156: naming-valid 73%→100% by construction).

**Limite**: vale SOLO per enum/dominio-chiuso §2.4. Testo libero (path, pattern, query, destinatari) → la GBNF può solo imporre il TIPO, non il valore → lì i guard restano.

---

## 2. IMPLEMENTAZIONE

### Passo 2.1 — `build_framework_grammar_typed(pool, catalog)`
Nuovo builder in `runtime/engine/grammar_framework.py`, estende `build_framework_grammar` (`:53-77`). Sostituisce l'`args` libero (`:29`) con una **union discriminata per-tool**:
```
step ::= createEventsStep | findFilesStep | ...   # una regola per tool del pool
createEventsStep ::= "{" ws "\"tool\":\"create_events\"," ws "\"args\":" createEventsArgs ws "}"
createEventsArgs ::= "{" ... required-first ... "}"   # da _emit_tool_args
```
- **Riuso**: importare da `runtime/tool_grammar.py` le funzioni `_emit_tool_args`, `_emit_value`, `_emit_string_literal_alt` (già testate, ADR 0133). NON riscriverle.
- Per ogni tool del pool: leggere `executor.args_schema` dal catalog; generare la regola args. Enum → alternation; string/int/bool/array → tipo; required-first + optional in ordine libero; **vietare chiavi fuori-schema**.
- **ESCLUDERE dal vincolo gli args `runtime_resolved`** (client/account/provider): sono nascosti all'LLM (`proposer.py:114-121`) — non devono comparire nella grammatica args.
- Workaround llama.cpp NOTI (rispettarli, ADR 0133 + `naming_grammar.py:194-198`): rule-name camelCase (no underscore), no multiline rule body, no `{n,m}` quantifier.
- **Fallback**: manifest senza `args_schema` completo → per quel tool usare l'`args` libero (jsonObject) come oggi. Mai bloccare.

### Passo 2.2 — Flag A/B
`METNOS_PROPOSER_GRAMMAR_ARGS` (default `0` = OFF). In `proposer.py:522-530` (e Metis `:324-410`): se ON → `build_framework_grammar_typed`, altrimenti l'attuale `build_framework_grammar`. Affiancato, non sostituito.

### Passo 2.3 — Contatore guard-fire (la metrica)
I guard già loggano quando scattano (`dispatch.py:1926` `[phantom_install]`, `:1991` `[degenerate_find]`, `:2047` `[sink_provider]`). Aggiungere un contatore per-guard: un dict modulo-livello in `dispatch.py` `_GUARD_FIRE_COUNTS` incrementato in ogni guard args quando muta il framework; esposto via una funzione `guard_fire_counts()` per il bench. NON cambiare il comportamento dei guard — solo strumentare.

---

## 3. MISURA (bench A/B)

**Metrica primaria**: `guard_fire(args-family)` con vs senza `METNOS_PROPOSER_GRAMMAR_ARGS`, sui corpora:
- banco routing 29/29 (v3) — cercare `bench/` per il file (l'agente cita "banco routing 29/29 v3", ADR 0177:199).
- `bench/compound_*.py` (compound spreadsheet/extract).

**Target**: azzerare i fire di `_promote_count_cap`/`_demote_overtight_caps` (cap enum-adiacenti) e ridurre `_fill_clause_args` sugli enum; `_scope_sink_provider_to_clause` invariato (è client, escluso dal vincolo).

**Metriche secondarie**: parse-rate (già ~100%), first-arg-valid-rate (arg enum dentro dominio senza guard), latenza (la grammar-args allarga ~KB/tool, atteso trascurabile ADR 0156:250), determinismo invariato (seed §11).

**Invariante di sicurezza**: i guard RESTANO come rete (ADR 0177:107,113). Lo spike NON li rimuove — misura quanti diventano **no-op** per poterli spegnere DOPO con evidenza. La suite `test_guard_pipeline_contract.py` (idempotenza `guard(guard(fw))==guard(fw)`) è il gate: la grammar-args non deve romperla.

---

## 4. DONE + ORDINE

1. Passo 2.1 + test `runtime/tests/test_grammar_args_typed.py`: per un tool con enum (es. `list_dirs.sort∈{name,mtime,size}`), la GBNF generata contiene l'alternation `"name"|"mtime"|"size"` e NON accetta `"recent"`. Per un tool con args testo-libero, la GBNF impone il tipo string ma non il valore.
2. Passo 2.2 + 2.3.
3. Passo 3: girare il bench A/B, produrre `internal/reports/grammar_args_ab_<data>.md` con la tabella guard-fire before/after.
4. **Cancello per Roberto**: se il bench mostra riduzione guard-fire senza regressione di parse-rate/latenza/idempotenza → decisione se promuovere a default ON + quali guard spegnere.

**RISCHI**:
- Schema manifest incompleto → grammar non protegge (fallback jsonObject). Mitiga: misurare la copertura schema prima (quanti tool hanno args_schema completo).
- Grammar troppo grande (union di N tool × M args) → latenza. Mitiga: la grammar è già per-pool (filtrato); misurare.
- Interazione con `think`: la grammar forza `think=False` (già così per i nomi). Nessun cambiamento.
- **NON toccare i guard** in questo spike: solo misurare. Rimuoverli è una decisione successiva con evidenza.
