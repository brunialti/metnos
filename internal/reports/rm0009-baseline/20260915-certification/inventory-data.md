# RM-0009 D-G0.5 — ricontrollo inventario DATA

Data: 2026-09-15 (Europe/Rome). Baseline sorgente fissata:
`09a58c7cf33e2610ce1506b129db3b3d4a37b735`.

Il risultato leggibile dalla macchina è `inventory-data.json`, SHA-256
`d41074253174cc3cc1a734378f93dc109cac4bd0e701b084697b3ddfe620798d`.
Il rapporto consolidato precedente in
`internal/reports/rm0009-baseline/20260915-consolidated/` resta immutato.

## Esito

Nel perimetro dei 110 input manifestati, l'inventario DATA conserva la stessa
semantica del baseline `1c308922839f7659a3cf54d988f995bba0f215d6`:

- 57 store esistenti, tutti assegnati a **D-P2.9**;
- 8 ingressi di crescita;
- 3 producer TurnLog, 3 append fisici e 15 chiamate `log.write()`;
- 4 terminali correnti, 4 adapter di crescita attivi, 6 kind correnti e 3
  kind futuri chiusi;
- versioni sorgente invariate; i numeri futuri restano candidati, non
  prenotazioni approvate né osservazioni su database installati.

`D-F6.6` non possiede nessuno dei 57 store già esistenti: aggiunge soltanto i
nuovi record v2/di enforcement, quando saranno implementati. `owner_id` e
provenienza sono metadati globali di attribuzione, non un cancello di
autorizzazione. Le ACL operative private restano circoscritte al principal
autenticato nei confini di effetto, lettura, cancellazione e replay.

Questo è un ricontrollo di identità e semantica nel perimetro
manifestato, **non** una certificazione di completezza delle scansioni e non
chiude G0.5. Percorsi costruiti dinamicamente, servizi esterni e artefatti
installati fuori dal commit restano fuori. Nessun DB, JSONL runtime, log,
segreto, servizio, writer, migrazione, import runtime o test è stato aperto o
eseguito.

## Sei scansioni, due passate

Ogni coppia è stabile. Gli hash includono l'output esatto dei comandi sul
commit completo; cambiano rispetto al rapporto precedente anche perché
`git grep` prefissa alle righe l'identità del commit.

| Scansione | Match | SHA-256 delle due passate |
|---|---:|---|
| `owner_persistence` | 3209 | `af513407638e60d93926cbde9dd3e9f5d6636cc720c96bee0206e2c4d6c45ef2` |
| `turn_writers` | 44 | `14d686a1a028c9c9e1e79a926dc7c672a17feec456cbe5b0af98695712e5014f` |
| `migrations` | 88 | `8064d1f1f3fee1ed049647a75e919ba2f0ac4f5a064e4d3b8f303657e7d9c6c7` |
| `growth_registry` | 203 | `f60a99b25d2022a1be3b9abe243e34131a75ecbcccabe1757ac02c448bc017e3` |
| `image_and_durable` | 167 | `963ecde75c814142f52ea7620b372d55db0fa3a5e7b16d80f8b9c23c7cdd5a22` |
| `requested_test_roots` | 261 | `b71d62d35c8b4c15cb85bd6708ee17a60d1d7e87a8da9176841c407338741a07` |

Totale: 3972 match. L'aumento 260→261 nell'ultima scansione è soltanto
`tests/runtime/infra/test_project_license.py`; non aggiunge copertura DATA.
I comandi esatti e i due hash distinti per passata sono nel JSON.

## Identità invariate

I 57 ID store, in ordine canonico del rapporto consolidato, sono:

`identity.users`, `identity.active_sessions`, `identity.pairing`,
`identity.approvals`, `identity.policy_grants`, `identity.devices`,
`identity.invocations`, `identity.playwright_sessions`,
`identity.playwright_profiles`, `identity.uploads`, `dialog.recurring`,
`dialog.scheduler`, `dialog.chat_target`, `dialog.deferred_turns`,
`dialog.pending`, `dialog.capability_pending`, `dialog.location_pending`,
`dialog.locations`, `dialog.notices`, `dialog.oauth_pending`,
`dialog.turn_events`, `dialog.tutor_conversation`, `dialog.tutor_probes`,
`turns.turnlog`, `turns.feedback`, `turns.undo`, `turns.protected_undo`,
`turns.scratchpad`, `turns.mnestoma`, `turns.fastpath`, `turns.autopath`,
`turns.argument_defaults`, `turns.terminator_lacunae`,
`turns.tutor_gaps_associations`, `biometric.persons`, `durable.state`,
`durable.resource_reservations`, `durable.artifacts`,
`durable.source_authority`, `image.folder_context_cache`,
`image.build_workspace`, `image.generations`, `image.thumbnails`,
`image.web_photo_cache`, `cache.file_hashes`, `cache.http`,
`cache.consult_frontier`, `telemetry.prefilter`, `telemetry.vaglio`,
`telemetry.observability_dashboard`, `telemetry.proposals_eta`,
`sites.audit`, `global.credentials`, `growth.change_intents`,
`growth.telos_proposals`, `growth.proposals_state`, `growth.markers`.

Sono 57 valori univoci. L'hash SHA-256 della lista ordinata e serializzata
come JSON compatto con newline è
`6187981c622ae195a47233ff6e6606bca103518f40f48ab695ad100d98f432d3`.

Gli 8 ingressi di crescita invariati sono:

1. `growth.adapter.synt`
2. `growth.adapter.telos`
3. `growth.adapter.introvertiva`
4. `growth.adapter.user_feedback`
5. `growth.direct.terminator`
6. `growth.marker.telos`
7. `growth.marker.fastpath`
8. `growth.direct.birth`

L'hash della lista ordinata è
`825b2efc1852f8d1c00e460d7ed3d1f42c00095c3f7bedd4af19fb7061fab3c0`.
I producer TurnLog restano `turn.run_turn`, `turn.http_pending` e
`turn.tutor_async`; hash della lista ordinata
`9d3e8f4a9cebfc0fb11a4a3b6081cde8340f702e98f94c8115added5073e8c9f`.

## Differenza semantica e byte

Dei 110 input manifestati, 101 sono identici fra `1c308922…` e `09a58c7…`.
I 9 restanti cambiano esclusivamente la prima riga:
`SPDX-License-Identifier: AGPL-3.0-only` diventa
`SPDX-License-Identifier: MIT`. Non cambia codice eseguibile, schema, writer,
store o arco di crescita.

| Input cambiato | Byte prima | Byte dopo |
|---|---:|---:|
| `runtime/args_defaults.py` | 5387 | 5377 |
| `runtime/playwright_sidecar/session_broker.py` | 231601 | 231591 |
| `runtime/proposal_actions.py` | 9294 | 9284 |
| `runtime/sites_audit.py` | 3941 | 3931 |
| `runtime/telos_introspect.py` | 19998 | 19988 |
| `runtime/telos_proposals_store.py` | 35463 | 35453 |
| `runtime/telos_synth_consumer.py` | 8401 | 8391 |
| `runtime/turn_events.py` | 14326 | 14316 |
| `runtime/turn_feedback.py` | 16435 | 16425 |
| **Totale** | **344846** | **344756** |

Delta netto: -90 byte. Le nove righe sostituite contano 369 byte prima e
279 dopo. Il diff più ampio del baseline contiene inoltre licenza MIT,
aggiornamenti di identità/digest della pubblicazione e i preparatori RM-0009;
nessuno modifica la semantica DATA degli input inventariati.

## Coerenza store, writer, crescita e versioni

- Tutti i 110 file correnti coincidono byte per byte con i blob Git del
  `source_commit`; `input_sha256` usa soltanto percorsi relativi reali e tutti
  i 110 digest sono stati ricalcolati dai blob.
- Gli ID sono 57/57, 8/8 e 3/3 univoci. I tre append TurnLog restano
  `runtime/agent_runtime.py:5604`, `runtime/http_routes_agent.py:594` e
  `runtime/tutor/telemetry.py:105`.
- Adapter correnti: `synt`, `telos`, `introvertiva`, `user_feedback`; ritirati:
  `multi_tool`, `canonical`. Kind correnti: `create_executor`,
  `extend_executor`, `dedupe_executors`, `materialize_pipeline`,
  `cache_pattern`, `reject_pattern`; set futuro chiuso:
  `create_executor`, `extend_executor`, `promote_plan`. `rejection_rule` resta
  governance, non un effetto.
- I valori nel sorgente restano Birth epoch 2, producer 5, retention 2,
  durable 7, source authority 3, image index 4 e file-hash 1. I successivi
  3/6/3/8/4/5/2 e gli eventuali 1 per store oggi non versionati sono soltanto
  candidati. Non è stato letto alcun database installato.

## Residuo per G0.5

Serve ancora il work manifest congelato, senza glob, che per ciascuno dei 57
store e degli 8 ingressi assegni API/file, migrazione fisica, prova di
cancellazione-revoca-replay-riavvio e test esatto. Le esclusioni globali
devono diventare allowlist provate. Fino a quel momento le identità sono
ricontrollate sul nuovo commit, ma la completezza e G0.5 restano aperti.
