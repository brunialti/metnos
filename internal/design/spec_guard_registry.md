# SPEC IMPLEMENTATIVA — Guard pipeline robusta e manutenibile (CP3-bis)

> **Destinatario**: LLM esecutore (Opus o inferiore). File:riga, criteri di done, test.
> **Obiettivo**: rendere i ~14 guard deterministici di `dispatch.py` ROBUSTI e MANUTENIBILI **senza cambiarne il comportamento** — da «accrezioni implicite che ri-derivano stato» a «registro di funzioni pure con scope dichiarato, ordine verificato dalla macchina, contratti testati, fire-rate osservato».
> **Autore analisi**: Fable, 6/7/2026. Segue il contratto `GUARD_PIPELINE` (ADR 0177 T3) e il fire-counter (CP5.4). **INVARIANTE FERREO: comportamento IDENTICO** — la suite `test_guard_pipeline_contract.py` (ordine + idempotenza) e la suite compound (errore=0) sono il gate a ogni passo.

---

## 0. Stato attuale (fatti verificati)

- `GUARD_PIPELINE: tuple` in `runtime/engine/dispatch.py:2195-2224` — 14 entry `(name: str, v3_only: bool, fn: callable)`. `fn` firma `(fw, intent, query, catalog) -> fw`.
- Applicata da `_apply_deterministic_structure_guards` (`runtime/engine/dispatch.py:2247`), con il fire-counter CP5.4 (`_GUARD_FIRE_COUNTS`, `METNOS_GUARD_FIRE_COUNT=1`).
- Contratto: `runtime/tests/test_guard_pipeline_contract.py` — `EXPECTED_PIPELINE` (`:32-47`) pinna nome+v3only in ordine; `test_full_chain_idempotent_on_corpus` + `test_each_guard_idempotent_on_corpus` (T4).
- **Fragilità**: (a) ordine load-bearing ma solo commentato (S2: extract PRIMA di conform, fill DOPO ordine); (b) ogni guard ri-deriva `catalog_names(catalog)`, `split_query_chunks(query)`, schema per-step; (c) nessuna dichiarazione di cosa ogni guard legge/scrive.

---

## FASE 1 — Registro tipizzato con metadati (#2, prerequisito di tutto)

### Passo 1.1 — dataclass `Guard`
In `dispatch.py`, prima di `GUARD_PIPELINE`:
```python
@dataclass(frozen=True)
class Guard:
    name: str
    fn: Callable                    # (fw, intent, query, catalog) -> fw  (invariata)
    v3_only: bool = False
    scope: str = "structure"        # "per-clause" | "cross-clause" | "structure" | "routing"
    writes: frozenset = frozenset() # campi toccati, es. {"args.client","args.pattern","step.tool"}
    reads: frozenset = frozenset()  # {"query","catalog","intent.actions","clause"}
    rationale: str = ""             # 1 frase: cosa garantisce
    adr: str = ""                   # ADR di riferimento
```

### Passo 1.2 — convertire le 14 entry
`GUARD_PIPELINE` diventa `tuple[Guard, ...]`. Per OGNI guard, compilare i metadati dai commenti/codice esistenti. Esempi (verificare sul codice reale):
- `overwrite_phantom_install_args`: scope=`structure`, writes=`{"args.base_path","args.path"}`, reads=`{"query"}`, rationale="rimuove base_path/path install-root non nominati", adr="0182".
- `enforce_missing_clauses`: scope=`cross-clause`, writes=`{"step"}`, reads=`{"intent.actions","catalog","query"}`, adr="0177".
- `enforce_missing_objects`: scope=`cross-clause`, writes=`{"step"}`, reads=`{"intent.actions","catalog"}`.
- `decontaminate_reader_qualifier`: scope=`cross-clause`, writes=`{"step.tool"}`, reads=`{"query","catalog"}`.
- `fill_clause_args`: scope=`per-clause`, writes=`{"args.*"}`, reads=`{"clause","catalog"}`.
- `promote_count_cap`/`demote_overtight_caps` (o il fuso `reconcile_count_cap`): scope=`per-clause`, writes=`{"args.max_results","args.max_total","args.top_k"}`, reads=`{"clause"}`.
- `scope_sink_provider_to_clause`: scope=`cross-clause`, writes=`{"args.client"}`, reads=`{"clause","query"}`.
- `degenerate_find_to_list`: scope=`routing`, writes=`{"step.tool","args.*"}`, reads=`{"query","catalog"}`.
- (compilare gli altri: align_framework_objects, ensure_extract_clause, conform_to_intent_order, resolve_store_field_refs, route_mail_delete_to_trash, route_filename_pattern_to_find, align_provider_client).

### Passo 1.3 — adeguare i consumatori
- `_apply_deterministic_structure_guards` (`:2247`): iterare `for g in GUARD_PIPELINE: if g.v3_only and not _v3: continue; ... g.fn(...)`. Il fire-counter usa `g.name`.
- `test_guard_pipeline_contract.py`: `EXPECTED_PIPELINE` legge `(g.name, g.v3_only)` dal nuovo campo. `test_guard_pipeline_callables` idem.

### Done 1
Suite `test_guard_pipeline_contract.py` verde (ordine + idempotenza invariati); sweep compound (`bench/compound_extract_create_bench.py`) 8/8; un turno reale verde. **Zero cambio comportamento.**

### Test 1 (NUOVO — la robustezza che sblocca)
`test_guard_registry.py`:
- `test_all_guards_have_metadata`: ogni Guard ha scope∈{...}, rationale non vuoto, adr non vuoto.
- `test_scope_ordering`: nessun guard `per-clause` che scrive `args.X` gira PRIMA di un `cross-clause` che scrive `args.X` (l'ordine load-bearing diventa verificato). Se la regola ha eccezioni legittime, documentarle in una whitelist esplicita nel test.
- `test_no_conflicting_writes`: due guard che scrivono lo STESSO campo devono avere scope compatibili (cross-clause prima di per-clause) o essere in una whitelist di override intenzionali.

---

## FASE 2 — GuardContext (#1, elimina la ri-derivazione)

### Passo 2.1 — dataclass `GuardContext`
Calcolato UNA volta in `_apply_deterministic_structure_guards`, passato a ogni guard:
```python
@dataclass
class GuardContext:
    intent: object
    query: str
    catalog: list
    catalog_names: set          # catalog_names(catalog), una volta
    chunks: list                # split_query_chunks(query), una volta
    schema_by_tool: dict        # {tool_name: args_schema}, una volta
```

### Passo 2.2 — migrare le firme guard
Firma nuova: `fn(fw, ctx: GuardContext) -> fw`. Ogni guard legge da `ctx` invece di ri-derivare. **Migrazione INCREMENTALE e SICURA**: un adapter `_ctx_adapter(fn_old)` che costruisce `ctx` dai vecchi 4 arg, così puoi migrare un guard alla volta tenendo la suite verde. NON migrare tutti in un colpo.

### Done 2
Ogni guard migrato: contratto+idempotenza+compound verdi dopo ognuno. `catalog_names`/`split_query_chunks` chiamati UNA volta per turno (verificabile con un contatore di chiamate nel test).

**RISCHIO FASE 2**: alta (tocca 14 firme). Mitiga: un guard alla volta, suite dopo ognuno, adapter di compatibilità. Se il budget è limitato, FASE 1 da sola già dà il grosso della manutenibilità.

---

## FASE 3 — Contratti + fusione + resolver (#3,#4,#5, riduce superficie)

### Passo 3.1 — postcondizioni dichiarate
Aggiungere a `Guard` un campo opzionale `postcondition: Callable[[Framework, GuardContext], bool]`. Per i guard con invariante chiara (es. `reconcile_count_cap` → «se clausola chiede N e cap dichiarato, cap≤N»), scriverla. Test: `test_postconditions_hold_on_corpus` verifica ogni postcondizione sul corpus incorporato.

### Passo 3.2 — fondere le coppie simmetriche
`_promote_count_cap` + `_demote_overtight_caps` → `_reconcile_count_cap` (una funzione: se clausola chiede N → cap=min(cap or ∞, N) col vincolo dichiarato; se non chiede → togli cap più stretto del default). Una entry nel registro, un test. Behavior-identico (unione dei due comportamenti già gemelli).

### Passo 3.3 — resolver clausola→args unico
`clause_arg_resolver(step, clause, schema, ctx) -> dict` che centralizza split-chunk + args_extractor + count. `_fill_clause_args`, `_reconcile_count_cap`, `_scope_sink_provider_to_clause` lo chiamano invece di duplicare l'estrazione.

### Done 3
Superficie ridotta (meno funzioni), postcondizioni testate, comportamento invariato.

---

## ORDINE + RISCHIO
1. **FASE 1** (registro+metadati+test scope) — **basso rischio, alta resa**: rende i guard leggibili/testabili SUBITO. È il prerequisito di tutto (una volta che ogni guard dichiara reads/writes/scope, il resto diventa meccanico). **Fare per prima, anche da sola vale.**
2. FASE 2 (context) — medio rischio (14 firme), fare incrementale con adapter.
3. FASE 3 (contratti+fusione+resolver) — riduce superficie, dopo FASE 1+2.

**Gate a ogni passo**: `test_guard_pipeline_contract.py` (ordine+idempotenza) + `bench/compound_extract_create_bench.py` (8/8) + un turno reale. Se uno rompe → STOP, il comportamento è cambiato (non ammesso).

**Connessione**: il fire-counter (CP5.4, già in prod dietro `METNOS_GUARD_FIRE_COUNT`) è l'osservabilità permanente — guard che non sparano mai su traffico reale = candidati rimozione (con evidenza); guard caldi = debolezza proposer da curare alla fonte. FASE 1 rende questa lettura azionabile (il registro dice a chi appartiene ogni arg).
