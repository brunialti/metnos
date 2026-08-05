# SPEC IMPLEMENTATIVA — Telos «generazione a secco» (§G) + analisi distribuzione EA

> **Destinatario**: LLM esecutore. **PRIMA di eseguire, leggere §0: la premessa del mandato è SBAGLIATA.**
> **Obiettivo**: strumento di dry-run che genera le proposte Telos col LLM reale SENZA scrivere nello store, aggrega la distribuzione (per-lente/telos/name_status, istogramma EA, diversità, tasso decisioni) e simula i filtri di persistenza read-only.
> **Autore analisi**: Fable, 6/7/2026.

---

## 0. ⚠ PREMESSA CHE RIBALTA IL MANDATO — Telos è GIÀ ON in produzione

Il mandato dice «studiare la distribuzione prima di lasciarla girare in produzione». **È già in produzione dal 12/6/2026**:
- Drop-in `/etc/systemd/system/metnos-http.service.d/telos-nightly.conf` → `METNOS_TELOS_NIGHTLY=1` (mtime 2026-06-12 11:47).
- Gira ogni 72h (`METNOS_TELOS_INTROSPECT_INTERVAL_H` default 72, `runtime/scheduler_v2/builtin_callbacks.py:32-33`).
- Store vivo `~/.local/share/metnos/telos_proposals.jsonl` con righe fino a oggi (ultima infornata 6/7 06:21-06:39).

**Quindi «a secco» qui = esecuzione osservata SENZA scrittura, per misurare la distribuzione generativa isolata dai filtri di persistenza** — non «prima della prima esecuzione». E ci sono **~7 settimane di dati reali** già analizzabili subito.

### La distribuzione REALE misurata ORA (fatto, non ipotesi)
Raw `telos_proposals.jsonl`: **116 righe**, EA mean 0.243 / mediana 0.308 / max 0.536, 0 flag paternalismo.
- **name_status (merged+enriched)**: existing_pipeline **74 (64%)**, existing_redundant **33 (28%)**, existing_parametric 9 (8%), **new_valid 0**.
- Post-recompose: **25 head, 24 azionabili**; convergenza altissima (top target `create_events`/`consult_frontier`/`compute_entries` con TUTTE le 10 lenti → `cluster_score` fino 0.724) ma **diversità bassa** (~25 target distinti su 116).
- **`stats()`**: total 116, **accepted 0, rejected 1, staged 0, pending 115** → **UNA sola decisione umana in tutta la storia**.

**Due finding strutturali già evidenti** (da confermare con la dry-run, ma i dati li mostrano): (1) l'engine **non propone MAI executor nuovi validi** (new_valid=0) — è un suggeritore di pipeline (territorio L1), non un creatore di executor; (2) la coda si accumula non-triageata (115 pending) — nessuno la guarda.

---

## 1. Architettura Telos (file:riga)

- **Orchestratore** `runtime/telos_introspect.py`: `run_for_telos(telos, *, catalog, llm_invoke, lenses, operators, persist=True)`; `run_all_telos(*, persist=True)`; `_persist(record)` (**qui vivono i filtri**: `rejected_targets()`, dedup `(target,lens)`); `_llm_invoke_tier` risolve il tier logico `middle` tramite `LLMRouter`, senza fissare provider, modello o endpoint. CLI (`--persist`, `--mock-llm`).
- **10 lenti** `runtime/telos_lenses/__init__.py:36-47` (`scamper, oulipo, inverse_rl, endgame_book, analogy_transfer, boden_transformational, pattern_language, generative_design, counterfactual, constitutional`). `run_lens` in `_base.py:149`. SCAMPER = 7 operatori (7 call); le altre single-call.
- **Store** `runtime/telos_proposals_store.py`: `load_all` `:120`, `annotate_naming(prop)` `:788` (calcola `name_status`, `_classify_name_status` `:545-583`), `recompose_clusters(rows)` `:246`, `cluster_score(ea_max, n_lenses)` `:233`, anti-resurrezione `rejected_targets()` `:406` + `rejected_signatures_relaxed()` (~`:380`) + `compute_signature_relaxed()` `:424`, `stats()` `:891` (**solo total/accepted/rejected/staged/pending — nessun breakdown per lente/EA**).
- **Telos sorgente** `workspace/TELOS.md` + `runtime/telos_loader.py`: 6 telos (t.tempo 0.25/t.puntualita 0.20/t.protezione 0.20/t.ordine 0.15/t.discrezione 0.10/t.parsimonia 0.10). `t.coltivazione_strumenti` rimosso v1.2 ma sopravvive stantio nello store.
- **EA/giudice** `runtime/alignment_engine.py`: `estimate_fit()` `:277`, `compose()` `:102` (penalità v1.4 a segno `:147-159`), CLI `--recompose --dry-run` `:340-346`.
- **UI** `/admin/changes`: facet `family` (`runtime/http_routes_admin.py:215,227`, `family_counts` `:248`), adapter `change_intent_adapters/telos.py::iter_telos:42` (cluster-head, skip non-azionabili). **NON esiste facet per lente o banda EA.**

### Mattoni dry-run già esistenti
1. `run_all_telos(persist=False)` `:409` — genera con LLM+EA reali, ritorna `list[dict]` SENZA scrivere. **Spina dorsale.**
2. CLI senza `--persist` — già dry ma stampa solo JSON grezzo, zero aggregazione.
3. `alignment_engine --recompose --dry-run` — ricompone EA senza scrivere (deterministico dai fit salvati).

### I buchi (cosa manca)
- **Nessun aggregatore di distribuzione** (per-lente/telos/name_status, istogramma EA, entropia/diversità target, tasso accept/reject).
- **name_status assente nei record dry**: `run_for_telos` NON chiama `annotate_naming` (calcolato solo al load-time). La dry-run deve applicarlo in memoria.
- **I filtri (dedup + rejected) sono DENTRO `_persist`**: `persist=False` NON li esercita → la dry vede la distribuzione GREZZA pre-dedup (utile), ma per «convergenza senza rigenerare le rifiutate» va replicata read-only.
- **Costo LLM**: ogni proposta = 1 call lente + 1 call giudice EA (~9s/proposta). 10 lenti × 6 telos → decine di minuti-ore sul Qwen locale.

---

## 2. IMPLEMENTAZIONE — lo strumento dry-run

### Passo 2.1 — Nuovo runner `runtime/telos_dry_run.py`
Funzione `dry_run(*, telos_ids=None, lenses=None, max_per_lens=None) -> dict`:
1. `records = telos_introspect.run_all_telos(persist=False)` (LLM+EA reali, nessuna scrittura). Passare `lenses=list(LENSES.keys())` per tutte.
2. Per ogni record: `telos_proposals_store.annotate_naming(rec)` (`:788`) → popola `name_status` in memoria.
3. `annotate_clusters` + `recompose_clusters(records)` (`:246`) + `cluster_score` (`:233`) in memoria — SENZA toccare i file (mutano solo i dict).
4. **Simulazione filtri read-only**: caricare `rejected_targets()` (`:406`) e i `_stored_target_lens_pairs()` (`telos_introspect.py:51`) come PREDICATI; contare quante proposte sarebbero state dedotte/soppresse (NON scrivere).
5. Aggregare e ritornare il dict distribuzione (§2.2).

**INVARIANTE**: read-only totale. L'unico side-effect ammesso sono le chiamate LLM locali (che non scrivono su disco con `persist=False`). NON scrivere nei 3 file JSONL dello store, NON in change_intents, NON in telos_decisions.

### Passo 2.2 — L'aggregato distribuzione
Il dict ritornato (e stampato/salvato in `internal/reports/telos_dry_run_<data>.json`):
```
{
  "n_raw": int, "n_after_dedup": int, "n_after_rejected": int,
  "by_lens": {lens: count}, "by_telos": {telos: count},
  "by_name_status": {new_valid, new_invalid, existing_parametric, existing_pipeline, existing_redundant},
  "ea_histogram": {bucket: count},  # bande 0-0.1, 0.1-0.2, ...
  "ea_stats": {min, max, mean, median},
  "clusters": {n_head, n_actionable, top_targets: [{target, n_lenses, cluster_score}]},
  "diversity": {distinct_targets, entropy},   # entropia di Shannon sui target
  "paternalism_flags": int,
}
```

### Passo 2.3 — CLI + confronto col cumulato
`python3 -m runtime.telos_dry_run [--telos t.tempo,...] [--lenses scamper,...] [--out report.json] [--compare-store]`. Con `--compare-store`: aggregare ANCHE lo store esistente (116 righe già lì) e produrre un diff dry-vs-cumulato (baseline).

---

## 3. DOMANDE APERTE PER ROBERTO (bloccano la definizione della metrica-obiettivo)

Da chiarire PRIMA di eseguire (l'esecutore deve chiederle o usare i default indicati):
1. **Telos è già ON dal 12/6**: (a) analizzare il cumulato già presente (116 raw/25 cluster, disponibile SUBITO senza costo LLM), (b) spegnere + dry-run a freddo + riaccendere, o (c) entrambe? **Default suggerito: (a)+(c)** — il cumulato è gratis e la dry aggiunge il pre-dedup.
2. **Cosa pesa**: istogramma EA per-riga, `cluster_score` post-recompose, o entrambi + diversità? **Default: entrambi + diversità.**
3. **Filtri in dry**: in-memory puro (replico predicati) o file-ombra diffabile? La differenza pre/post-dedup è enorme (docstring `:88-92`: 1017→75, −93%). **Default: in-memory puro.**
4. **`new_valid=0` è strutturale**: KPI-patologia da riportare o comportamento atteso? **Default: riportarlo come finding (è la prova che l'engine è un suggeritore di pipeline, non un creatore di executor — allineato all'analisi 2/7 e ADR 0180).**
5. **Diversità vs convergenza**: il valore cercato è convergenza (poche molto-votate) o varietà (esplorazione)? Cambia la metrica-obiettivo. **Da decidere con Roberto.**
6. **Costo/budget**: congelare i 6 telos di TELOS.md (escluso lo stale)? Budget tempo max per la dry reale sul Qwen? **Default: 6 telos, budget da concordare.**
7. **Sbocco (ADR 0180)**: l'accept di una pipeline telos ESEGUE un turno reale. La dry si ferma a generazione+EA o simula anche il tasso di azionabilità a valle (quante head passerebbero l'adapter)? **Default: fermarsi a generazione+EA; l'azionabilità a valle è già data da `recompose_clusters` (24/25 azionabili).**

---

## 4. RISCHI

- **Costo LLM reale** (decine di min-ore): dimensionare, magari 1 telos alla volta. La CLI `--telos` lo permette.
- **Non è veramente "a secco" se qualcuno scrive**: verificare che `persist=False` non abbia percorsi di scrittura nascosti (l'agente conferma che i filtri+scrittura sono in `_persist`, non chiamato con persist=False — ma testare).
- **Interazione con W1 learning-loop** (ADR 0185, aggiunto ieri): sia Telos sia W1 alimentano change_intents. La dry-run NON tocca change_intents (read-only), ma l'analisi deve notare la potenziale sovrapposizione dei target per la decisione futura.
- **Il finding vero potrebbe essere "spegnere Telos"**: se la dry conferma new_valid=0 + 115-pending-mai-triageati + diversità bassa, la conclusione onesta potrebbe essere che l'auto-introspezione notturna produce rumore che nessuno consuma → candidato allo spegnimento o al ridimensionamento (poche lenti). Questo è un ESITO LEGITTIMO dell'analisi, da non nascondere.
