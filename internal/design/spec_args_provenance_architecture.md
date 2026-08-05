# SPEC IMPLEMENTATIVA — Architettura di PROVENIENZA degli args (il lavoro grosso, una volta)

> ⚠️ **ESITO (6/7/2026, dopo FASE 1-2 — commit `9edc914`/`a298a0e`/`7466fea`)**: la tesi «eliminare le categorie A/B per costruzione» **NON regge alla prova del codice**. `fill_clause_args` È GIÀ lo stage clause-derive ben costruito (split per-chunk, count-cap nidificati dentro); `client` è clause-derived sui tool multi-provider (da testo→gw, non runtime puro); l'ordine dei guard è load-bearing. Consegnato il valore REALE: registro `Guard` tipizzato (PROV.1), oracolo di equivalenza (PROV.2), fonte unica `args_extractor.CLAUSE_DERIVABLE_NAMES` + invariante «nessun guard scrive args semantic» (PROV.3). **FASE 3 da RISCOPIRE prima di eseguirla**: il residuo concreto è la marcatura dei ~30 config-args senza `runtime_resolved` (il proposer LI VEDE — `proposer.py:114` è path prod), con cautela: `client` sui tool multi-provider NON va marcato. NON ri-tentare l'eliminazione di massa sulla base delle sezioni sotto.
>
> ✅ **MARCATURA FATTA (6/7/2026 sera)**: 20 config-args marcati (16 + 4 riscoperti alla prova degli executor: move_files e trio `*_files_doc` sono MONO-provider reali), 10 esenzioni intent-bearing (client files multi-provider clause-derived; move_messages.client metnos|gmail SENZA owner runtime; `account` mail — il resolver delega i casi 2+ al planner). Politica bloccata da `tests/runtime/infra/test_config_args_marking_policy.py` (tabella = fonte unica; `n_unmarked_config==0` = invariante); esenzioni in `arg_provenance.is_intent_bearing_config`. **Bonus scovato dalla marcatura**: `resolve_backend_arg` iniettava il default per-OBJECT ignorando l'enum del TOOL (share_files gw-only riceveva `client="local"` → ERR_NOT_APPLICABLE su ogni share senza marker drive) → clamp enum-aware sul DEFAULT, MAI sull'esplicito («sposta su drive» su tool local-only mantiene l'errore onesto «client non applicabile»). Enum stantii allineati agli executor: write_files (lazy-gw reale → multi), find_events_empty (handler gw reale → multi). Il residuo FASE 3 pieno (coerce unico + cat. C/D) resta decisione di scope di Roberto.

> **Destinatario**: LLM esecutore (Opus o inferiore). **Ambizione ALTA per scelta di Roberto** («mira alto, un lavoro impegnativo una volta sola, non tanti interventi»). File:riga, criteri di done, oracolo di equivalenza.
> **Obiettivo**: sostituire i ~10 guard-args reattivi con UN modello di proprietà deterministico. Non «abbellire i guard» — ELIMINARE le categorie A (derivazione) e B (contaminazione) per costruzione, lasciando solo il residuo strutturale/routing legittimo.
> **Autore analisi**: Fable, 6/7/2026. Target coerente con ADR 0177 (S4 resolver-registry, T-resolver).
> **VINCOLO SUPREMO**: il compound è a **errore=0** (lavoro 5/7). Questo refactor NON può regredirlo. La garanzia è un **ORACOLO DI EQUIVALENZA**: sul corpus esistente il nuovo pipeline deve produrre framework IDENTICI ai guard attuali. Se diverge su un caso che oggi funziona → il caso va capito, non «accettato».

---

## 0. La tesi (perché è UN lavoro, non tanti)

I guard-args proliferano (ADR 0177 S2, «crescono per accumulo») perché **nessuno possiede gli args**: l'LLM li genera tutti liberamente e poi 10 guard rincorrono i suoi sbagli. La cura non è un guard migliore — è un **modello di proprietà**: ogni argomento ha UN solo proprietario legittimo, e ogni proprietario lo imposta in modo AUTORITATIVO. Quando la proprietà è chiara, i guard di categoria A e B non hanno più nulla da correggere.

### I 3 proprietari
| Proprietario | Args | Chi li imposta | Oggi |
|---|---|---|---|
| **RUNTIME** | `client`, `account`, `provider` (config, `runtime_resolved`) | il runtime inietta | ✅ già così (nascosti all'LLM) |
| **CLAUSOLA** (deterministico) | `path`, `pattern`, `time_window`, `max_results`, **enum-da-testo** (`sort`, `op`, `mode`…) | derivazione dal chunk della clausola | ⚠ oggi FALLBACK dopo l'LLM (guard) → deve diventare AUTORITATIVO |
| **LLM** (vincolato) | i genuinamente semantici (`columns`, `summary`, il glue) | grammar-args gli concede SOLO questi | ⚠ oggi l'LLM può toccare tutto |

### Come collassano i guard
- **Categoria A** (`_fill_clause_args`, count-cap): NON sono guard — sono lo stage CLAUSOLA. Diventano uno stage positivo sempre-attivo, non una pezza.
- **Categoria B** (`_decontaminate_reader_qualifier`, `_scope_sink_provider_to_clause`): SPARISCONO. Sono contaminazione cross-clausola. Se la derivazione è PER-CLAUSOLA (vede solo il proprio chunk), la contaminazione è **impossibile**, non «corretta». (Memoria `contamination-is-function-not-prompt`.)
- **Categoria C** (`_overwrite_phantom_install_args`): rete per una fonte già fixata → rimovibile dopo conferma (come il legacy planner).
- **Categoria D** (`_degenerate_find_to_list`): è routing (tool-choice), non args → resta ma va etichettata `scope=routing`, non `args`.
- **RESIDUO che RESTA** (legittimo, è il planner compound): `enforce_missing_clauses`, `enforce_missing_objects`, `ensure_extract_clause`, `conform_to_intent_order`, `align_framework_objects`, `resolve_store_field_refs`. Toccano STRUTTURA/ORDINE, non valori-args.

---

## FASE 0 — PIETRA ANGOLARE: classificatore di provenienza (comportamento INVARIANTE)

Tutto il resto poggia su questo. È dati puri, zero cambio comportamento — si può fare per primo e in parallelo.

### Passo 0.1 — `runtime/arg_provenance.py`
`classify_arg(tool_name, arg_name, arg_schema, catalog) -> str` ∈ `{"runtime","clause","semantic"}`:
- **runtime**: `arg_schema.get("runtime_resolved")` True (marker esistente). Fonte: manifest.
- **clause**: derivabile deterministicamente dal testo. Criterio: (a) è un enum (`arg_schema.get("enum")`) → estraibile via detection_lexicon; OPPURE (b) `args_extractor` sa estrarlo — path/pattern/time_window/count/glob (verificare quali campi `args_extractor.regex_extract` copre, `runtime/args_extractor.py`). Costruire il set `_CLAUSE_DERIVABLE_FIELDS` dalla capacità reale dell'extractor + gli enum.
- **semantic**: tutto il resto (né runtime né clause).

`provenance_map(tool, catalog) -> dict[arg,str]` per un tool; `provenance_report(catalog) -> dict` per tutto il catalogo (per l'analisi + la dashboard).

### Done 0
`test_arg_provenance.py`: `classify_arg` su casi noti — `write_files.client`→runtime, `list_dirs.sort`(enum)→clause, `find_files.pattern`→clause, `create_files_spreadsheet.columns`→semantic. `provenance_report(catalog)` non solleva e classifica il 100% degli args dichiarati (nessun arg senza classe).

### Valore immediato (anche senza il resto)
`provenance_report` STAMPA la mappa: quanti args per classe, quali tool hanno args semantic (i «difficili»), quali sono 100%-deterministici. È la mappa che guida tutto il refactor + dice a Roberto la distribuzione reale. **Questo pezzo, da solo, è già conoscenza azionabile.**

---

## FASE 1 — Substrato: registro guard tipizzato con scope+provenance

La precedente specifica separata del registro Guard è stata integrata qui e
rimossa come duplicato. `GUARD_PIPELINE` è una `tuple[Guard,...]` con
`Guard(name, fn, v3_only, scope, writes, reads, rationale, adr)`. `writes` usa i
nomi-arg e si incrocia con `arg_provenance`: il test verifica che nessun guard
scriva un arg di provenienza `runtime` o `clause` fuori dal contratto ammesso.

### Done 1
Contratto+idempotenza+compound verdi (comportamento invariato). `test_guard_registry`: ogni guard ha metadati; `writes` coerente con quello che il guard tocca davvero (verificabile: applicare il guard a un corpus e controllare quali campi cambiano ⊆ `writes` dichiarato).

---

## FASE 2 — Stage CLAUSOLA autoritativo (il cuore, cambia comportamento → oracolo)

### Passo 2.1 — `clause_owned_args(step, clause, schema, ctx) -> dict`
In un nuovo `runtime/clause_resolver.py`. Per uno step: dal SUO chunk-clausola, deriva TUTTI gli args di provenienza `clause` (via `arg_provenance` + `args_extractor` + detection_lexicon per gli enum-da-testo). Ritorna il dict degli args posseduti dalla clausola.

### Passo 2.2 — applicarlo AUTORITATIVO (non fallback)
Nel dispatch, PRIMA dei guard residui: per ogni step, `args_clause = clause_owned_args(...)`; per ogni arg di provenienza `clause`, **la clausola VINCE** su quello che l'LLM ha messo (oggi è il contrario: l'LLM vince e il guard corregge). Questo è il cambio di paradigma. Gli args `runtime` li inietta il runtime; gli `semantic` restano dell'LLM.

### Passo 2.3 — ritirare i guard sussunti
Una volta che lo stage-clausola possiede gli args `clause` per-chunk: `_fill_clause_args`, `_reconcile_count_cap` (ex promote/demote), `_scope_sink_provider_to_clause`, `_decontaminate_reader_qualifier` diventano **no-op per costruzione**. Il fire-counter (CP5.4) lo PROVA sul corpus: devono andare a 0 fire. Solo ALLORA rimuoverli dal registro.

### ORACOLO DI EQUIVALENZA (il gate che protegge errore=0)
`test_provenance_equivalence.py`: sul corpus esistente (`test_guard_pipeline_contract._cases()` + `tests/benchmarks/corpus_snapshot.jsonl` + i flagship compound), il framework prodotto da `[stage-clausola + guard residui]` deve essere **byte-identico** (o semanticamente-equivalente con diff spiegato) a quello prodotto dai `[guard attuali]`. Ogni divergenza è un caso da capire PRIMA di procedere. Questo è ciò che garantisce che errore=0 non regredisca: se l'output è lo stesso sui casi che funzionano, il comportamento osservabile è preservato.

### Done 2
Oracolo verde (equivalenza su tutto il corpus); i 4 guard sussunti a 0-fire sul corpus (fire-counter); rimossi dal registro; contratto+compound+turno reale verdi.

---

## FASE 3 — Chiusura: coerce unico + residuo dichiarato

> ✅ **CONCLUSA (7/7/2026, decisione Roberto «fase 3 concludere»)**:
> - **3.1 FATTO** — `engine/coerce_args.py`, Guard #0 `coerce_args_to_schema` (v3, PRIMO per costruzione: tocca solo l'output grezzo del proposer). Drop fuori-schema + leak `runtime_resolved`, enum case-normalize o drop (mai snap). **Esenzione load-bearing**: gli arg dichiarati nei `writes` dei guard a valle (registro PROV.1, es. `client`) non vengono MAI toccati — pena l'oscillazione della catena (idempotenza T4, che protegge anche la ri-applicazione sugli hit cache ADR 0174). Il backstop ha subito scovato un buco reale: `read_files` usava `name` (lettura Drive per nome, 4/7) senza dichiararlo nello schema → dichiarato + `requires_one_of` esteso. Oracolo PROV.2: unica divergenza capita = junk `top_k` pulito dal caso dirty → regen deliberato. Test `test_coerce_args.py`.
> - **3.2 D GIÀ CONCLUSA** — `degenerate_find_to_list` era già `scope="routing"` dal PROV.1.
> - **3.2 C = criterio datato** — `overwrite_phantom_install_args` logga `[phantom_install]` a ogni fire: rimovibile con evidenza journal 0-fire su ≥14 giorni di traffico reale (finestra dal 7/7/2026, verifica ≥21/7). Niente fire-counter env in prod: il journal è l'evidenza durevole, il counter in-process muore al restart.
> - **Done 3 raggiunto** nella forma onesta post-PROV.3: runtime-resolve (resolver a esecuzione) · clause-derive (`fill_clause_args`) · coerce-schema (nuovo, #0) · residuo strutturale/routing DICHIARATO nel registro. Nessuna FASE 4: la spec finisce qui.

### Passo 3.1 — `coerce_args_to_schema(args, schema) -> args`
Backstop deterministico unico (se grammar-args è off o l'LLM sfugge): scarta args fuori-schema, snap/valida enum (al valore valido più vicino o drop), rimuove leak di `runtime_resolved`. Sostituisce la logica enum sparsa.

### Passo 3.2 — categoria C e D
- C (`_overwrite_phantom_install_args`): dopo N giorni di 0-fire su traffico reale (fire-counter in prod) → rimuovere (la fonte è fixata: filtro install-root sull'apprendimento).
- D (`_degenerate_find_to_list`): ri-etichettare `scope=routing`; valutare se spostarla nel name-grammar/routing (fuori da questa spec).

### Done 3 (il traguardo)
Da ~10 guard-args a: **3 stage principiati** (runtime-resolve · clause-owned autoritativo · coerce-schema) + **residuo strutturale dichiarato** (enforce/conform/align, che sono il planner compound, non args). Il registro (FASE 1) documenta ogni pezzo. Il fire-counter (CP5.4) è l'osservabilità permanente. grammar-args (CP5) è l'enforcement del confine LLM.

---

## ORDINE, RISCHIO, PROTEZIONE errore=0
1. **FASE 0** (provenance) — zero rischio, dati puri, FALLA PER PRIMA (anche in parallelo). È la mappa.
2. **FASE 1** (registro) — basso rischio, comportamento invariato, substrato.
3. **FASE 2** (clausola autoritativa) — ALTO rischio (cambia la risoluzione args). **Protezione: l'oracolo di equivalenza sul corpus.** Un guard alla volta a 0-fire prima di rimuoverlo. Mai rimuovere un guard che ancora spara.
4. **FASE 3** — chiusura.

**Il patto con errore=0**: non si «spera» che il refactor non rompa il compound. Si DIMOSTRA con l'oracolo che sui casi funzionanti l'output è identico. Il refactor è ambizioso nel DISEGNO (un modello, non pezze) ma conservativo nell'OSSERVABILE (equivalenza provata). Questo è «mira alto, una volta sola» fatto in sicurezza.
