# V26.5 — compact normal-form design probe

Stato: **SOLO DESIGN OFFLINE, NON FROZEN, ZERO RETE**.

Questa directory contiene una prova dell'adattatore strutturale minimo:

- l'LLM non emette `clause_id`, `atom_id` o `output_index`;
- le clausole sono derivate dagli span e ordinate dal codice;
- gli atomi sono identificati dalla posizione nell'array topologico;
- l'unico dato numerico conservato sugli edge è `source_ordinal`, perché indica
  quale risultato alimenta realmente un'azione;
- l'adattatore ricostruisce il formato V26.4.1 e usa senza modifiche il suo
  validator congelato.

Non è un candidato live: schema, prompt e runner offline esistono, ma la review
indipendente lo blocca prima di freeze e gate. Non attribuire accuratezza
post-hoc ai 34 risultati V26.4.1.

## Schema compatto

Lo schema decoder di design è ora presente in
`metnos_v265_compact.schema.json`, generato meccanicamente dallo schema
V26.4.1 congelato. L'audit `metnos_v265_compact_schema_audit.py` passa 30/30:

- 19/19 grafi positivi accettati;
- 5/5 mutazioni dei campi rimossi respinte;
- JSON Schema Draft 2020-12 valido;
- assenti `clause_id`, `atom_id`, `output_index` e `from_atom_output`;
- nessun lessico sorgente aggiunto: gli enum sono identici al parent salvo la
  sostituzione tecnica `from_atom_output` -> `from_prior_atom`; l'unica nuova
  proprietà è `source_ordinal`.

Resta design-only: non esistono freeze, runner live o autorizzazione alla rete.

## Prompt compatto

Il prompt di design è ora presente in `metnos_v265_compact.prompt.txt`. È
derivato dal prompt V26.4.1 auditato tramite tre sole sostituzioni di riga; 56
righe su 59 sono byte-invariate. Il prompt finale è 8.154 byte / 1.140 token,
contro 8.170 / 1.147 del parent.

L'audit redatto passa 7/7: zero righe surface/mixed, zero query intere e zero
overlap di almeno tre token sui dataset 109 + 34 + 70. I vecchi identificatori
sono assenti; compaiono soltanto i termini tecnici dello schema compatto.
Resta design-only: nessun runner live, freeze, gate o run V26.5.

## Runner offline-only

`metnos_v265_offline_runner.py` collega schema compatto, adattatore puro e
validator V26.4.1. Espone soltanto `--self-test`: non ha endpoint o percorso
di inferenza e installa un audit hook che rifiuta operazioni di rete.

Self-test 34/34: 17 round-trip sul registro congelato, 2 controlli con registro
sintetico dichiarato separatamente, probe storico 125/125, hash/audit verdi e
due errori strutturali fail-closed. `network_events=[]`; zero output candidati.
Il runner resta offline-only e non costituisce freeze o autorizzazione live.

## Verdetto review indipendente — 2026-08-09

**BLOCK prima di freeze, gate o run.** I byte semantici correnti superano i
controlli adapter/schema/prompt e i casi multi-azione, multi-dominio e
ambiguità, ma il bundle non è riproducibile senza `/tmp`, la catena hash è
verificata dopo gli import e non è transitiva, e il verde storico delle
mutazioni non attraversa il percorso compatto canonico.

- rapporto MD: `metnos_v265_independent_static_review.md`, SHA-256
  `49b096b67e44fd9366a70517caac931237bf1b7801e4c5cb06f9bd26a9087e77`;
- rapporto JSON: `metnos_v265_independent_static_review.json`, SHA-256
  `34ec814faa6ed4634f461938473b2d6fbaa81af39229c31be27761c1683890d9`.

La review non autorizza rete, inferenza, freeze o gate e non ha modificato gli
artefatti di design del candidato.
