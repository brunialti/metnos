# Engine v3 compound — FINDINGS (sessione 18-19/6/2026)

Riferimento durevole per riprendere il lavoro. Tutti i dati sono PROVATI con
test mirati, non assunti. Per dettagli di sessione: memoria
`project_session_18_6_engine_v3.md`.

## Numeri (provati)

| metrica | runs=1 | runs=2 | note |
|---|---|---|---|
| struttura (tool giusti + ordine) | 96.6% | ~81-84% | runs=2 dominato da rumore MTP |
| args end-to-end (struttura AND args) | 93.2% | — | stesso riquadro 6×5 verde |
| **riquadro ≤6 azioni × ≤5 domini** | **100%** | stabile | il limite affidabile |

**Il numero che conta** NON è il totale grezzo runs=2 (rumore MTP): è che il
riquadro 6×5 è solido e gli errori STABILI (reali) sono pochi.

## Causa-radice della "casualità" (PROVATA, con controprova)

1. Intent extractor: 5/5 deterministico (isolato).
2. Proposer: 5/5 deterministico (isolato).
3. Pipeline completa: FLAKY.
4. La flakiness è correlata alla **LUNGHEZZA dell'output**: chiamate corte
   (intent, output ~50 char) = DETERMINISTICHE anche col server condiviso;
   chiamate lunghe (proposer, output 2800+ char) = FLAKY. Stessa chiamata HTTP
   identica (system+user+seed+slot) con stato-slot diverso prima → output diversi.
5. **DUE controprove (19/6, decisive) — 2 ipotesi smentite, causa vera trovata**:
   - **Controprova A (MTP)**: 2° llama-server STESSO modello SENZA `--spec-type
     draft-mtp` (+`--parallel 1`) su :8081, output lungo (800 tok): **CON MTP
     flaky 2/3 E SENZA MTP flaky 2/3** → **MTP NON è la causa**.
   - **Controprova B (cache_prompt)**: prod, output lungo, cache_prompt ON vs OFF:
     **ON = DETERMINISTICO 3/3, OFF = FLAKY 3/3** → il riuso-KV (cache_prompt)
     RENDE STABILE, non instabile. L'OPPOSTO dell'ipotesi.
   - **CAUSA VERA (coerente con TUTTI i dati)**: **non-determinismo numerico
     floating-point su generazioni LUNGHE**, amplificato dal calcolo GPU batched
     (`--parallel`/cont-batching). L'ordine di riduzione FP non è deterministico;
     su migliaia di token le micro-differenze si accumulano e l'output diverge.
     Spiega tutto: intent corto (poche FP-ops)=stabile; proposer lungo (migliaia,
     batched)=flaky; cache_prompt ON + chiamata IDENTICA = stesso percorso di
     calcolo = stabile; OFF o stato-slot diverso = percorso diverso = flaky.
   - **Onestà**: ho sbagliato DUE ipotesi (MTP, poi "cache cattiva"); solo gli
     esperimenti hanno portato alla causa reale (FP non-determinismo + batching).

## La scoperta che RIBALTA il problema (PROVATA)

I **guard deterministici a valle assorbono 14/15 flaky** → STABILE-OK su 4 run.
L'LLM oscilla, ma `_conform`/`_enforce` normalizzano → piano finale corretto.
La "casualità" quasi mai raggiunge l'utente. → **il fix MTP NON è necessario per
la correttezza** (servirebbe solo per riproducibilità del bench).

Corollario metodologico: **un bench a runs=1 (o 2) con LLM MTP è un termometro
rumoroso**. Due grid dello STESSO codice danno numeri diversi (19/20/21 err) e
25 errori "ballano" tra i run = rumore, non regressione. Per giudicare un fix
DETERMINISTICO: confronto per-cella a runs alti SULLE celle che il fix tocca,
MAI il totale grezzo.

## Bug STABILI residui (8, reali — presenti in entrambi i grid)

a4d1, a4d4, a6d3, a6d5, a6d6, a7d2, a7d3, a8d7. Diagnosi (provata):
- **CONTAMINAZIONE cross-clausola** (maggioranza): a 7-8 clausole l'LLM ancora
  una clausola all'oggetto di una vicina (« trova le FOTO » dopo « trova i FILE »
  → erroneamente files). NON gap-vocab (isolato foto→images ✓), NON capacità
  (tier wise identico al fast). È bias d'attenzione su frasi lunghe.
- **SAME-OBJECT multiplo**: 2+ clausole sullo stesso oggetto (find_urls +
  read_urls; write_entries + delete_entries) → il proposer ne droppa una /
  duplica l'altra; il conform fatica a ordinarle.
- **mis-map verbo**: « togli »→filter invece di delete (ma isolato → delete ✓);
  « apri/riassumi i risultati »→render invece di read.

## Fix applicati questa sessione (committati)

- **Engine v3 swappable** (`METNOS_ENGINE=v3` ↔ metis rollback istantaneo).
- **P1 provider-gating** (pool deterministico per-provider sui compound).
- **P2 reorder + enforce object-level + transform-producer** (§2.2).
- **P3 set_fields** (bug §2.8: status mai aggiornato).
- **_fill_clause_args** (args per-clausola, allineamento object-aware).
- **_decontaminate_clause_objects** (de-contaminazione via `_OBJECT_HINTS`,
  funzione canonica, no liste; chiude a8d4 1/4→4/4 provato).
- **intent**: clause-independence + approval/db-locale (gold 25/25).

## CODA per Fable / ripresa futura

1. **MTP determinismo** (decisione Roberto pendente): processo monouso §11 per
   routing? `--parallel 1` per il bench? O accettare (i guard assorbono) +
   documentare 6×5 come limite. NB: MTP = +84 t/s in prod, non buttare alla cieca.
2. **8 bug stabili**: fix SAME-OBJECT nel conform (a4d1/urls duplicati) +
   contaminazione residua a 7-8 clausole. Ognuno deterministico, gate gold/routing/suite.
3. **Bench a prova di MTP**: aggiungere modo runs=N con "pass se il piano POST-GUARD
   è stabile" (non il piano grezzo). Misura il numero utente-reale.
4. **_VERB_TO_CANONICAL** (task #14): deprecato ADR 0058 ma vivo in 5+ call-site;
   strumentare l'uso reale prima di abolire.
5. **Riavvio servizio** → re-gate routing 29/29 (le 2 fail erano flakiness MTP del
   proposer, non codice — provato su metis + intent stabile).
6. **Bench-harness**: ogni nuovo step in run_turn va replicato in
   `compound_dryrun.plan_only` o il bench diverge dalla produzione (già successo
   con _decontaminate).
