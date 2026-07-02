---
id: 0180
title: Igiene della filiera proposte — regola dei livelli operativa
date: 2026-07-02
status: accepted
area: runtime
related:
  - 0122  # auto-evaluator (killer framework): qui si aggiunge layer_overlap
  - 0156  # telos engine (lenti + governance vocab)
  - 0158  # change_intents (vista unificata /admin/changes)
  - 0170  # tassonomia skill: confini fra livelli
modifies:
  - 0077  # specialize introvertiva: generatore ritirato
  - 0158  # adapter attivi ridotti a 4; accept di materialize_pipeline ridefinito
---

# Igiene della filiera proposte — regola dei livelli operativa

## Contesto

L'analisi del 2/7/2026 (`~/.local/state/metnos/telos_autogen_analysis.md`,
richiesta esplicita di Roberto) ha esaminato le proposte Telos e TUTTI i
meccanismi di autogenerazione. Esito: i contenuti erano in maggioranza
sensati e allineati ai fini dichiarati, ma la filiera a valle era rotta o
ridondante in più punti.

Evidenza raccolta:

- **Regola dei livelli violata dai generatori.** La regola (Roberto,
  13/6/2026, vincolante) dice: un default d'argomento è SEMPRE compito di
  L0 (il fastpath memoizza query→piano, args inclusi); una pipeline
  ricorrente è territorio di L1 (autopath); un executor nuovo si giustifica
  SOLO per un percorso molto richiesto. Eppure: `candidates_specialize`
  produceva per costruzione soltanto default-da-fissare (42 proposte
  fresche il 1/7, tutte della stessa classe); `candidates_generalize`
  duplicava con evidenza più debole ciò che L1 e il promoter ETA fanno già
  sui turni reali.
- **Accept delle pipeline Telos sempre fallito.** L'adapter emetteva un
  body privo di `path_shape_hash` (contratto dell'applier, pensato per la
  famiglia multi_tool) e leggeva una chiave inesistente
  (`pipeline_tools` invece di `pipeline_tools_mentioned`): ogni
  accettazione umana terminava in FAILED. Nessuno se n'era accorto —
  una sola decisione umana nello store.
- **Store senza scrittore proiettati in UI.** `canonical_query_log`
  (scritto dal planner legacy, disattivato dal 30/6) e
  `multi_tool_paths` (rinforzo rimosso l'11/6) erano fermi al 25/5, ma i
  loro adapter rimaterializzavano ogni notte ~130 righe morte in
  /admin/changes.
- **Rumore non filtrato.** Il ramo default dell'adapter Telos mandava
  anche i `name_status` non azionabili (`new_invalid`,
  `existing_redundant` — il 35% dello store) a «Crea executor».
- **Conflitti fra telos senza peso.** `alignment_engine` v1.3 usava
  fit ∈ [0,1] con gate: una proposta che AVANZA un telos ma ne VIOLA un
  altro (es. modello a pagamento contro `t.parsimonia`) non pagava mai il
  conflitto.

## Decisioni

1. **Ritiro dei generatori che violano la regola dei livelli.**
   `candidates_specialize` e `candidates_generalize` sono rimossi
   (con l'infrastruttura di scansione turni ormai orfana). L'introvertiva
   conserva la sola operazione con valore unico: `candidates_dedupe`
   (segnalazione di mnest orfani/duplicati; l'accept fa alias+deprecate).
   Le righe storiche restano leggibili dall'adapter.

2. **Killer `layer_overlap` nell'auto-evaluator** (difesa in profondità,
   estende ADR 0122): respinge (a) il default-da-fissare su singolo
   executor a parità di chiavi args (il caso che TRIVIALITY non vede) con
   ragione «superseded by L0»; (b) il percorso multi-step non molto
   richiesto (`path_call_count_60d` sotto
   `METNOS_HIGHLY_REQUESTED_FREQ_60D`, default 30, 0=off) con ragione
   «covered by L1» — SOLO in presenza dell'evidenza di frequenza (mai
   bloccare senza evidenza).

3. **Adapter Telos a cluster-head.** L'adapter proietta le TESTE di
   cluster (`recompose_clusters`) e non le righe grezze: ~27 intenti al
   posto di ~500, punteggio = `cluster_score` (EA massima + bonus di
   convergenza fra lenti distinte), riassunto dal miglior membro. I
   `name_status` non azionabili non vengono proiettati.

4. **Accettare una pipeline = eseguirla una volta.**
   `apply_materialize_pipeline` esegue la `suggested_query` della proposta
   come turno reale dentro `scheduled_turn_scope` (valgono il gate di
   consenso per l'invio, la soppressione delle notifiche a vuoto e le
   guardie del vaglio). L'esito onesto finisce nell'`applied_effect`;
   l'observer giudica come per ogni altra applicazione. Se il turno
   funziona, L0/L1 imparano DA SOLI dal turno vero: niente depositi
   paralleli, niente evidenza sintetica.

5. **Ritiro degli adapter `canonical` e `multi_tool`.** Store senza
   scrittore = famiglia morta: i moduli restano in git, le righe residue
   sono state respinte con ragione esplicita (vedi Bonifica).

6. **`alignment_engine` v1.4 — fit con segno.** Il giudice può dichiarare
   fit ∈ [-1, 1]: il valore negativo significa che la proposta LAVORA
   CONTRO quel telos. La penalità NON passa dal gate (il danno conta
   sempre, anche sotto soglia): `ea_base −= α · Σ peso_i · |fit_i⁻|`.
   La composizione per soli fit positivi resta identica alla v1.3.

## Bonifica (2/7/2026, con copie di sicurezza)

Copie: `proposals_state.db.bak-20260702-bonifica-layer-overlap`,
`change_intents.sqlite.bak-20260702-*`. Tutte le modifiche eseguite via
codice (`mark_action`, `apply_decision`), mai con SQL diretto.

- `proposals_state`: 22 righe specialize → `blocked` (memoria durevole
  della decisione); le `applied` storiche preservate.
- `change_intents`: 68 default-da-fissare + 102 righe delle famiglie morte
  + 51 righe stantie di maggio → `rejected` con ragione. Le
  finalized/observed (storia) intatte.
- Risultato: le proposte in attesa passano da ~250 a **30, tutte segnale**
  (22 teste Telos azionabili, 4 dedupe, 3 reject-pattern dell'utente,
  1 synt).

## Conseguenze

- /admin/changes mostra solo famiglie vive e proposte azionabili; il
  punteggio riflette la convergenza fra lenti.
- Accettare una proposta Telos produce un effetto reale e osservabile
  (il turno), invece di un errore di contratto.
- Il costo di generazione notturna cala (una sola operazione introvertiva,
  niente rimaterializzazione di store morti).
- Il vincolo architetturale «nessuna sovrapposizione fra livelli» è ora
  applicato da un killer deterministico, non solo dichiarato.

## Non fatto (deliberato)

- Elenco dei nomi di catalogo nel contesto delle lenti (anti riferimenti a
  executor rinominati): l'annotazione a valle già declassa i bersagli
  morti; da riconsiderare se il tasso resta alto.
- Potatura fisica dello store grezzo Telos: le righe decadono già per
  annotazione; solo cosmetica.
- Rinomina di `telos_synth_consumer` (oggi serve `fastpath_promote`, non
  Telos): toccherebbe le chiavi dei task schedulati per un beneficio solo
  di chiarezza.
