# CP5 grammar-on-args — LOG DI AVANZAMENTO (per ripresa da Opus)

> Spec di riferimento: `internal/design/spec_cp5_grammar_on_args.md`.
> Aggiornato man mano. Se questa sessione si esaurisce, Opus riprende dall'ultimo ▶ IN CORSO.
> Branch `session/detection-lexicon-i18n` (non pushato). Prod=v3. Restart: `sudo -n systemctl restart metnos-http.service`.

## Stato task
- [ ] CP5.1 riuso macchina schema→GBNF (tool_grammar.py, orfana, 43 test)
- [ ] CP5.2 build_framework_grammar_typed
- [ ] CP5.3 flag METNOS_PROPOSER_GRAMMAR_ARGS + wiring
- [ ] CP5.4 contatore guard-fire
- [ ] CP5.5 bench A/B + report + cancello

## Principio (perché CP5 vale, per non perdere il filo)
Gli args si àncorano allo SCHEMA-MANIFEST (ground-truth deterministica §2.4), NON a intent.actions (output LLM). Per questo grammar-on-args NON amplifica garbage-in come grammar-on-verbs (accantonata ADR 0174 D4). Solo enum/dominio-chiuso; testo libero resta jsonStr. I guard RESTANO come rete — si misura quanti diventano no-op.

## Diario (append-only, il più recente in fondo)

### CP5.1 ✅ FATTO (6/7)
`runtime/tool_grammar.py` INTEGRA (55 test verdi, non deprecato — usato da Praxis pool-filter). Macchina schema→GBNF verificata:
- `_emit_tool_args(name, schema) -> (rule_name, lines: list[str], used: set)` — NB ritorna TUPLA, non stringa.
- `_emit_value(schema, used, depth, _extra_rules, _tool_prefix)` — enum→alternation di literal (`("\"max\"" | "\"min\"" | ...)`), string→jsonStr, int→jsonNum, bool→jsonBool, array→jsonArray. Fallback jsonObject se schema None/oneOf/anyOf/allOf o props vuote.
- VERIFICATO su catalogo reale: compute_entries.op→6-way alt; list_dirs.sort→3-way; write_files.mode→enum. find_files.pattern→jsonStr (testo libero, corretto).
- Workaround llama.cpp già dentro: camelCase rule names (`_sanitize_rule_name`), optional ordinati alfabeticamente con `(sep prop)?` una volta sola (anti repeat-loop), no `{n,m}`.
- `args_complexity`/`is_complex` esistono (soglia COMPLEXITY_THRESHOLD) ma B2-recursive NON usa più is_complex come fallback (esplora ricorsivo).
- `generate_tool_grammar(tools, allow_final_answer, allow_disambiguation, include_canonical_query)` = union discriminata `{"name":..., "arguments":...}` per il tool_call PROTOCOL (planner legacy). NON è la forma Framework runtime (steps list) — CP5.2 deve adattarla.

RIUSO per CP5.2: `_emit_tool_args` + `_emit_value` + `_emit_string_literal_alt` + `_emit_primitives`/`_expand_deps` (per le regole primitive jsonStr/jsonNum/...). NON riscrivere.

### CP5.2 ✅ FATTO (6/7)
`runtime/engine/grammar_framework.py`: `build_framework_grammar_typed(pool_names, catalog)` + helper `_strip_runtime_resolved`. Template `_GRAMMAR_FRAMEWORK_TYPED_TMPL` (senza args/kv/value liberi, con string/number/ws/fillers), `_FREE_ARGS_RULES` (argsFree per tool senza schema + final_answer).
- Union discriminata: `step ::= stepX | stepY | …`, ogni `stepX ::= "{" "tool":"x" "," "args": argsX "}"`.
- argsX da `_emit_tool_args(x, stripped_schema)`; enum→alternation, testo→jsonStr.
- runtime_resolved (client/account/provider) STRIPPATI da props+required (marker `.get("runtime_resolved")`).
- DEDUP per nome-regola OBBLIGATORIA (template vince): `ws` era doppio (template+primitive) → GBNF invalida scartata in silenzio (bug 2/6). Bug trovato e fixato.
- Fallback: catalog None / typed_count==0 → build_framework_grammar. Empty pool → GRAMMAR_FRAMEWORK.
- **VALIDATO LIVE**: `prov.chat(grammar=g)` → llama-server accetta, output Framework valido, `sort:"mtime"` (enum rispettato). ChatResult ha `.text`.
- 9 test `test_grammar_args_typed.py`.
GOTCHA per il seguito: negli assert usare substring SENZA quote (`"mtime"` nella grammar è `\"mtime\"` escaped → cerca `mtime` nudo). Il pattern `[a-zA-Z]+` sui RHS cattura i valori-enum dentro i literal → maschera i literal (`re.sub(r'"(?:\\.|[^"\\])*"',...)`) prima di cercare dangling.

### CP5.3 ▶ PROSSIMO
flag METNOS_PROPOSER_GRAMMAR_ARGS (default 0). Wiring: proposer.py:522-530 (SimpleProposer) e proposer_metis.py:324-410 (_generate_grammar_multi delega a Simple → un solo punto?). Verificare se basta cambiare in SimpleProposer. Passare il catalog (già disponibile? proposer.propose ha catalog=... param).

### CP5.3 ✅ FATTO (6/7)
`runtime/engine/proposer.py:~522`: flag `METNOS_PROPOSER_GRAMMAR_ARGS` (default 0). Se 1 → `build_framework_grammar_typed(effective_pool, catalog)`, altrimenti `build_framework_grammar(effective_pool)`. `catalog` e `effective_pool` già in scope.
- **UN SOLO PUNTO copre entrambi gli engine**: MetisProposer delega a `self._simple.propose` (proposer_metis.py:238,386) → il flag nel SimpleProposer vale anche per Metis/v3.
- Smoke A/B: solo-nomi len 980 (no enum), typed len 2723 (con enum). Turno reale flag ON: `list_dirs`+`sort_entries` ok, answer. StepLog usa `raw_args`/`resolved_args` (NON `args`).

### CP5.4 ▶ PROSSIMO
Contatore per-guard in dispatch.py. I guard args loggano già (`[phantom_install]` :1926, `[degenerate_find]` :1991, `[sink_provider]` :2047). Aggiungere dict `_GUARD_FIRE_COUNTS` + `guard_fire_counts()`/`reset_guard_fire_counts()`. Incrementare quando il guard MUTA il framework (confronto pre/post o flag interno). Non cambiare comportamento.

### CP5.4 ✅ FATTO (6/7)
`runtime/engine/dispatch.py`: `_GUARD_FIRE_COUNTS` + `guard_fire_counts()`/`reset_guard_fire_counts()`. In `_apply_deterministic_structure_guards`: se `METNOS_GUARD_FIRE_COUNT=1` (default off) snapshotta `framework.to_dict()` pre/post ogni guard; se muta → incrementa il contatore per-nome. Passivo, non cambia comportamento. `import os as _os` locale (dispatch non ha `os` a modulo — gotcha).
- Smoke: piano avvelenato → `{fill_clause_args:1, degenerate_find_to_list:1}`.
- Contratto+typed test verdi (nessun cambio comportamento).

### CP5.5 ▶ PROSSIMO (ultimo)
Bench A/B: girare N query (banco routing + compound) con METNOS_GUARD_FIRE_COUNT=1, una passata METNOS_PROPOSER_GRAMMAR_ARGS=0 e una =1, confrontare guard_fire_counts totali e per-guard. Cercare il banco: bench/ (routing 29/29 v3, compound_*). Report internal/reports/grammar_args_ab_2026-07-06.md. Verificare parse-rate (nessun None in più) + latenza. Cancello per Roberto: se guard-fire scende senza regressione → proposta default ON + quali guard spegnere.
NB: il bench deve chiamare il PROPOSER REALE (LLM) per vedere l'effetto della grammar sugli args generati — non basta applicare i guard a piani statici. Riusare l'harness di bench/ esistente che fa run_turn o proposer.propose.

### CP5.5 ▶ IN CORSO (6/7)
`bench/grammar_args_ab.py` scritto e lanciato in background (bltz9c60e). Corpus 18 query mirate agli enum-args (sort/op/compress/mode) + compound + count. Confronta A (GRAMMAR_ARGS=0) vs B (=1) con GUARD_FIRE_COUNT=1. Misura: guard_fire tot+per-guard, parse_rate, arg_err (ERR_ARG_*), latenza mediana.
- **ONESTÀ CRUCIALE (§8.3) da mettere nel report**: la grammar-args vincola gli ENUM. Ma i guard-args attuali fixano soprattutto STRUTTURA (base_path fantasma, degenere find→list) e TESTO-LIBERO (pattern, count int, client runtime_resolved) — NON valori enum. Quindi il guard_fire potrebbe NON scendere molto. Il valore VERO della grammar-args è impedire ENUM-INVALIDI (sort:"recent") che l'executor rifiuterebbe a runtime (ERR_ARG) → misurato da `n_arg_err`, non solo da guard_fire. Se guard_fire non scende ma arg_err sì → il valore è "correttezza a runtime", non "meno guard". Il bench deve dire la verità: quale delle due dimensioni si muove.
- Turno lento (~30-60s cold-start+exec); 36 turni ≈ 20-40 min in background.
- Esito → report `internal/reports/grammar_args_ab_2026-07-06.md` + ADR-pending + cancello Roberto.
