# SPEC IMPLEMENTATIVA — Architettura di PROVENIENZA degli args (il lavoro grosso, una volta)

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

(La spec `spec_guard_registry.md` FASE 1, integrata qui.) Convertire `GUARD_PIPELINE` (`dispatch.py:2195`) in `tuple[Guard,...]` con `Guard(name, fn, v3_only, scope, writes, reads, rationale, adr)`. `writes` usa i nomi-arg → si incrocia con `arg_provenance`: un test verifica che **nessun guard scrive un arg di provenienza `runtime` o `clause`** una volta che gli stage li possiedono (FASE 3). All'inizio è solo documentazione; dopo FASE 3 diventa un invariante.

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
`test_provenance_equivalence.py`: sul corpus esistente (`test_guard_pipeline_contract._cases()` + `bench/corpus_snapshot.jsonl` + i flagship compound), il framework prodotto da `[stage-clausola + guard residui]` deve essere **byte-identico** (o semanticamente-equivalente con diff spiegato) a quello prodotto dai `[guard attuali]`. Ogni divergenza è un caso da capire PRIMA di procedere. Questo è ciò che garantisce che errore=0 non regredisca: se l'output è lo stesso sui casi che funzionano, il comportamento osservabile è preservato.

### Done 2
Oracolo verde (equivalenza su tutto il corpus); i 4 guard sussunti a 0-fire sul corpus (fire-counter); rimossi dal registro; contratto+compound+turno reale verdi.

---

## FASE 3 — Chiusura: coerce unico + residuo dichiarato

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
