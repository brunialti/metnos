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

## PARALLELO — Architettura provenienza args (mandato Roberto «mira alto, una volta»)
Spec: `internal/design/spec_args_provenance_architecture.md` (target ambizioso) + `spec_guard_registry.md` (substrato FASE 1).
### FASE 0 ✅ FATTO in parallelo al bench (6/7, commit 2132aab)
`runtime/arg_provenance.py` — classify_arg/provenance_map/provenance_report. Zero comportamento (dati puri). Mappa reale 537 args: runtime 38 / clause 114 / semantic 385. Scoperta: 30 config args senza marker runtime_resolved (causa dei guard provider). 7 test. Report `internal/reports/args_provenance_map_2026-07-06.md`.
### PROSSIMI (dopo cancello CP5, effort focalizzato — NON in parallelo, protetti da oracolo di equivalenza)
- FASE 1: registro guard tipizzato (Guard dataclass, scope/reads/writes/rationale). Basso rischio.
- FASE 2: stage clausola AUTORITATIVO (clause_resolver) + ritiro guard sussunti (a 0-fire provato dal counter CP5.4). ALTO rischio → oracolo `test_provenance_equivalence.py` sul corpus esistente.
- FASE 3: coerce_args_to_schema unico + rimozione categoria C/D.

### CP5.5 ✅ FATTO (6/7) — ESITO NEGATIVO ONESTO per grammar-args, ma spike riuscito
Bench v2 (proposer diretto, no cache/exec — la v1 aveva cache+describe-fanout che invalidavano l'A/B, ridisegnata). Risultato: A OFF = B ON identici (enum_invalid 0→0, guard_fire 6→6). Probe avversario: baseline mai enum-invalido (RAR→zip, KOI8→latin-1). Il proposer rispetta già gli enum via hint soft; i guard caldi (fill_clause_args×5, align×1) sono clausola/struttura, non raggiungibili dalla grammar.
CONCLUSIONE: grammar-args NON è il lever (stessa lezione di grammar-on-verbs). Ma lo spike ha INDICATO il lever: fill_clause_args caldo = il clause-derive autoritativo (FASE 2 provenienza) lo sussume. Infra CP5 = capitale (provenance FASE 0, fire-counter, grammar dormiente). Report `internal/reports/grammar_args_ab_2026-07-06.md`.
RACCOMANDAZIONE AL CANCELLO: NON promuovere grammar-args ON (nessun beneficio); procedere con architettura provenienza args (FASE 1→2), protetta da oracolo equivalenza.

## CP5 CHIUSO. Prossimo = architettura provenienza args (FASE 1 registro → FASE 2 clause-derive), «un lavoro una volta» come da mandato Roberto.

## FASE 1 + FASE 2 provenienza args ✅ FATTE (6/7) — esito ONESTO
Commit: PROV.1 `9edc914`, PROV.2 `a298a0e`, PROV.3 `7466fea`.

### PROV.1 (FASE 1) ✅ — registro guard tipizzato
`GUARD_PIPELINE` da `tuple(name,v3only,fn)` a `tuple[Guard,...]`. Dataclass `Guard` (name/fn/v3_only/scope/writes/reads/rationale/adr). 14 guard annotati. `test_guard_pipeline_contract`: +3 test (metadata completi, writes ⊆ mutazioni osservate, no per-clause write prima di cross-clause sullo stesso campo). Basso rischio, behavior-equivalent.

### PROV.2 (FASE 2) ✅ — oracolo di equivalenza
`test_provenance_equivalence.py` + golden `data/guard_equivalence_golden.json`. 11 casi (7 strutturali dal contratto + 4 args-pesanti). `regen()` congela l'output ATTUALE della pipeline; il test verifica riproduzione BYTE-IDENTICA. È la rete che protegge errore=0 in ogni fase.

### PROV.3 (FASE 2) ✅ — ESITO ONESTO: NO eliminazione di massa, SÌ leggibilità+invarianti
**Scoperta chiave**: la premessa «14 accrezioni reattive da spazzar via» NON regge alla prova del codice. `fill_clause_args` è GIÀ lo stage clause-derive ben costruito (split per-chunk, count-cap `_promote/_demote` GIÀ nidificati dentro, non guard separati). `client` è clause-derived (da «google drive»→gw), non runtime puro — la mappa a 3 è più sfumata. Fondere fill/scope_sink/align_provider cambierebbe l'ordine LOAD-BEARING per un guadagno cosmetico → NON fatto (§7.2: no rischio per estetica; §8.3: riporta la verità).

Due interventi concreti e SICURI (oracolo verde):
1. **Fonte unica nomi clause-derivabili**. `arg_provenance._CLAUSE_DERIVABLE_NAMES` era copia a mano dei rami di `regex_extract` — GIÀ divergente (`recipient` fantasma vs `to_user/to_users` mancanti). Estratti in `args_extractor.CLAUSE_DERIVABLE_NAMES` (gruppi accanto ai rami), importati da provenance. Zero drift per costruzione. Mappa corretta: clause 114→115 (un `to_user` era misclassificato).
2. **Invariante di proprietà** (`test_guards_do_not_write_semantic_args`): incrocia `Guard.writes` × `arg_provenance` → NESSUN guard sovrascrive un arg `semantic` (dell'LLM). Regge empiricamente, 1 eccezione documentata (`route_mail`→`dst_folder` 'Trash', regola §5). Cattura una classe di bug futuri.

Suite 3334 pass. Oracolo+contratto+invariante verdi.

### FASE 3 (coerce_args_to_schema + rimozione cat. C/D) — RIVALUTARE lo scope
Roberto aveva chiesto «fasi 1-2», FASE 3 separata. **Ma l'esito PROV.3 cambia il calcolo di FASE 3**: se non c'è eliminazione di massa da fare (i guard sono già principiati), anche «coerce unico + rimozione cat. C/D» va rivalutato — probabilmente più piccolo dello spec. Il substrato (mappa, registro tipizzato, oracolo, invariante, fire-counter) è la vera consegna durevole: rende OGNI cambio futuro ai guard meccanico e sicuro. Decisione di scope FASE 3 → a Roberto.

## MARCATURA config-args ✅ FATTA (6/7 sera) — residuo FASE 3 chiuso
La lista `unmarked_config` (30) è stata lavorata **per-tool alla prova dell'executor**, non per convenzione di nome:
- **20 MARCATI** `runtime_resolved` (+ re-sign §7.10): dirs×3, contacts×2, find_events_empty, urls×3, login_session, mail-plumbing client×4 (read/send/reply/set_messages), files mono-provider×6 (delete/move/share + trio `*_files_doc` — move e doc-trio riscoperti mono alla lettura dei `_HANDLERS`).
- **10 ESENTI** (intent-bearing, restano visibili all'LLM): client dei files MULTI-provider (clause-derived, PROV.3), move_messages.client (metnos|gmail, nessun owner runtime → l'LLM è l'unico scrittore del ramo Gmail), `account` mail×3 (il mail_account_resolver delega per costruzione i casi 2+ account al planner; send-from è intento).
- **Politica = invariante**: `test_config_args_marking_policy.py` (tabella fonte-unica, 6 test) + `arg_provenance.is_intent_bearing_config` (regole) + `n_unmarked_config==0` da qui in poi = drift reale.
- **Bug adiacente trovato e chiuso**: `resolve_backend_arg` iniettava il default per-OBJECT ignorando l'enum del TOOL → share_files (gw-only, object files default local) rompeva OGNI share senza marker drive. Fix: clamp enum-aware sul DEFAULT (mai sull'esplicito — l'errore onesto resta). `test_backend_resolver_enum_clamp.py` (6 test). Enum stantii allineati: write_files (lazy-gw → multi), find_events_empty (gw handler reale → multi + description famiglia events).
- **Effetto pool**: su share_files il client esce dalla finestra visibile; sui tool con >8 args il marker libera uno slot del cap `[:8]` per un arg d'intento. Suite 3346 pass. Turni live: mail/calendar/drive/files ok post-restart.
