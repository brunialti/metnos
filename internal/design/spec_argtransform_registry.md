# Spec — Registro `ArgTransform` (unificazione della famiglia resolver)

> Stato: PIANO (7/7/2026). Nessun codice ancora. Refactor a sé, behavior-invariant,
> a guardia della suite. Modello: `GUARD_PIPELINE` (dispatch.py, ADR 0177 T3).

## 1. Obiettivo
Portare la **catena cablata a mano** di trasformazioni-arg pre-esecuzione di
`engine/executor.py` a un **registro tipizzato** `ArgTransform`, gemello di
`Guard`: entry-dato con metadati dichiarati (`scope/reads/writes/rationale/adr`),
driver unico, punto di registrazione unico, verificabilità (non-collisione,
idempotenza, equivalenza golden). Chiude il debito che il 7/7 ha generato
l'hardcoding dei sinonimi (nessun «dove metto la risoluzione?»).

**Non** è un merge in `GUARD_PIPELINE`: i Guard operano su `framework` a
dispatch-time, questi su `args` per-step a exec-time → **registro gemello**,
stesso modello, granularità diversa.

## 2. Inventario (stato attuale — Executor.run ~1513-1612)

| # | Transform | Firma | Legge extra | Scrive | Gruppo | In registro |
|---|---|---|---|---|---|---|
| 1 | `_resolve_from_step` | (args, history, schema) | history | args pipati | data-piping | **NO** (history, non normalizzatore) |
| 2 | `_resolve_fillers` | (args, fillers, llm, query) | fillers, LLM | args riempiti | filler | **NO** (LLM) |
| 3 | `_resolve_runtime_placeholders` | (args, runtime_ctx) | runtime_ctx | placeholder | placeholder | **NO** (concern diverso: sostituzione ${RUNTIME:}) |
| 4 | `resolve_backend_arg` | (tool, args, query, args_schema) | query, schema | args.<provider> | **exec-only** | **SÌ** |
| 5 | `resolve_self_recipient` | (tool, args, query) | query | args.to | **exec-only** | **SÌ** |
| 6 | `resolve_calendar` | (tool, args, query) | query | args.calendar | **exec-only** | **SÌ** |
| 7a | `resolve_mail_account` | (tool, args, query) | query | args.account | **query-det** | **SÌ** |
| 7b | `resolve_from_contains` | (tool, args, query) | query | args.where_* | **query-det** | **SÌ** |
| 7c | `resolve_junk_mail` | (tool, args, query) | query | args.where_* | **query-det** | **SÌ** |
| 7d | `resolve_time_window` | (tool, args, query, args_schema) | query, schema | args.time_window | **query-det** | **SÌ** |
| 7e | `resolve_photo_fields` | (tool, args, query) | query, dl | args.fields | **query-det** | **SÌ** |
| 8 | `resolve_scope_args` | (tool, args, schema, actor, query) POST-invoke | schema, actor, post-invoke | args.<scope> | post-invoke | **NO** (cattura valore DOPO l'invoke, ciclo di vita diverso) |
| 9 | `_entries_to_2d_matrix` | (args["values"]) | — | args.values | matrix | **NO** (single-arg triviale) |

**IN = 8 resolver** (5 query-det + 3 exec-only). Firme «pure» `(tool, args, query)`;
solo `time_window`/`backend_arg` vogliono `args_schema` → passato via `ctx` leggero.
**OUT = 5** (from_step, fillers, placeholders, scope_args, matrix): firme/cicli-di-vita
irregolari → restano cablati, con il **perché** documentato al call-site (§7.2).

## 3. Design target

```python
@dataclass(frozen=True)
class ArgTransform:
    name: str
    fn: Callable            # fn(tool, args, query, ctx) -> args
    scope: str              # "query-det" | "exec-only"
    reads: frozenset        # {"query","args_schema","creds","dl:<concept>"}
    writes: frozenset       # {"args.fields","args.account",...}
    rationale: str
    adr: str

ARG_TRANSFORM_PIPELINE: tuple = (
    # --- query-deterministici (riapplicabili: esecuzione E record L0) ---
    ArgTransform("mail_account",  lambda t,a,q,c: resolve_mail_account(t,a,q), "query-det", ...),
    ArgTransform("from_contains", lambda t,a,q,c: resolve_from_contains(t,a,q), "query-det", ...),
    ArgTransform("junk_mail",     lambda t,a,q,c: resolve_junk_mail(t,a,q),     "query-det", ...),
    ArgTransform("time_window",   lambda t,a,q,c: resolve_time_window(t,a,q,args_schema=c.get("args_schema")), "query-det", ...),
    ArgTransform("photo_fields",  lambda t,a,q,c: resolve_photo_fields(t,a,q),  "query-det", ...),
    # --- execution-only (dipendono da runtime ctx/creds) ---
    ArgTransform("backend_arg",     lambda t,a,q,c: resolve_backend_arg(t,a,q,args_schema=c.get("args_schema")), "exec-only", ...),
    ArgTransform("self_recipient",  lambda t,a,q,c: resolve_self_recipient(t,a,q), "exec-only", ...),
    ArgTransform("calendar",        lambda t,a,q,c: resolve_calendar(t,a,q),       "exec-only", ...),
)

def apply_arg_transforms(tool, args, query, ctx, *, scope):
    """Driver unico (gemello del loop GUARD_PIPELINE). Best-effort per entry."""
    for t in ARG_TRANSFORM_PIPELINE:
        if t.scope != scope:
            continue
        try:
            args = t.fn(tool, args, query, ctx)
        except Exception as e:               # noop loggato (come oggi)
            log.debug("%s noop: %r", t.name, e)
    return args
```

Wiring (2 punti, invariati come SEMANTICA):
- `resolve_query_canonical_args(tool, args, query, args_schema)` → **diventa**
  `apply_arg_transforms(tool, args, query, {"args_schema": args_schema}, scope="query-det")`.
  Preserva i 2 chiamanti esterni: `Executor.run:1592` (esecuzione) + `dispatch.py:138`
  (`_maybe_record_fastpath`, record L0).
- Il blocco exec-only di `Executor.run` (backend/self_recipient/calendar) → **diventa**
  `apply_arg_transforms(tool, args, query, {"args_schema": ...}, scope="exec-only")`.

Il campo `scope` rende **strutturale** il confine critico query-det/exec-only:
il record L0 chiama SOLO `scope="query-det"` (set invariato); un edit non puo'
piu' far scivolare per errore un resolver ctx-dipendente nel path riapplicabile
(avvelenamento L0). Oggi e' solo un commento in prosa.

## 4. Test (mirror dei Guard)
- `test_argtransform_pipeline_contract.py` (gemello `test_guard_pipeline_contract`):
  ogni entry ha `scope` valido; **non-collisione** — due entry stesso-scope non
  dichiarano `writes` sovrapposti senza motivo; `reads/writes` non vuoti/coerenti.
- **Idempotenza** (gemello `test_guard_idempotence`, proprieta' gia' vera dei
  resolver query-det): applicare due volte == una. Assert su corpus.
- **Equivalenza golden** (gemello `test_provenance_equivalence`, oracolo PROV):
  per un corpus di `(tool, args, query)` reali, output registro == output
  cablato-attuale. E' la prova **behavior-invariant** del refactor.
- **Fire-count** opz. (gemello `METNOS_GUARD_FIRE_COUNT`): env
  `METNOS_ARGTRANSFORM_FIRE_COUNT` per scovare entry morte/rare.
- I test per-resolver esistenti (`test_junk_mail_resolver`, `test_photo_fields_resolver`,
  `test_backend_resolver_*`, …) restano verdi INVARIATI (le `fn` non cambiano).

## 5. Migrazione (incrementale, ogni passo verde)
1. Introdurre `ArgTransform` + `ARG_TRANSFORM_PIPELINE` + `apply_arg_transforms`
   (nessun cambio di wiring). Aggiungere il contract-test. → pytest verde.
2. Riscrivere il CORPO di `resolve_query_canonical_args` come
   `apply_arg_transforms(scope="query-det")` (stessi 5 resolver, stesso ORDINE).
   → resolver tests + pytest + golden-equivalence verdi.
3. Riscrivere il blocco exec-only di `Executor.run` come
   `apply_arg_transforms(scope="exec-only")`. → suite completa + **E2E reali**
   (mail-account, time_window, junk, calendar, backend, photo) §8.5.
4. Aggiungere golden/idempotenza/fire-count. Aggiornare indice anti-regression
   §10.6 + nuovo ADR (o estendere 0177).

## 6. Rischi + mitigazioni
- **Ordine** regredisce → la tuple replica l'ordine cablato attuale; golden-equivalence lo blinda.
- **Semantica L0-record** → `scope`; il path record chiama solo query-det (set invariato); test sul record.
- **Path caldo** (arg-resolution di OGNI step) → behavior-invariant + E2E sui domini toccati (§8.5).
- **Leak ctx** → `ctx` = dict minimo `{args_schema}`; solo time_window/backend lo leggono.
- **Firme irregolari** → NON forzate nel registro (restano cablate); il registro copre solo le 8 «pure».

## 7. Non-goal / rimandati
- Merge in `GUARD_PIPELINE` (granularita' diversa: fw vs args) → registro gemello, non unico.
- from_step/fillers/placeholders/scope_args/matrix → cablati, documentati al call-site.
- Registro mutabile con `register()` a caldo → NO: tuple statica come `GUARD_PIPELINE` (deterministico, ispezionabile a import-time).

## 8. Stima
~13 transform toccati (8 migrati, 5 lasciati con commento), 1 classe + 1 driver +
2 riscritture di wiring + 3-4 test nuovi. Rischio: MEDIO (path caldo) mitigato
da golden-equivalence + E2E. Valore: architetturale (un modello-registro per TUTTI
i transform deterministici) + verificabilita' della famiglia + enforcement L0.
