# RM-0009 D-G0.5 — inventario dati consolidato

Data: 2026-09-15 (Europe/Rome). Baseline sorgente: commit completo
`1c308922839f7659a3cf54d988f995bba0f215d6` nel tree
`/opt/metnos/.claude/worktrees/rm0009-development`.

Il risultato machine-readable autorevole di questa ricognizione è
`inventory-data.json`. Questa è una vista leggibile dello stesso inventario.
Sono stati letti soltanto file sorgente tracciati al commit; nessun DB, JSONL
runtime, log, chiave, credenziale, payload personale o servizio è stato aperto.
Non sono stati eseguiti test, writer o migrazioni.

## Esito

- 57 store o superfici persistenti/RAM personali, owner-bearing o derivati;
- 3 producer TurnLog, 3 append fisici e 15 chiamate `log.write()` nel flusso
  principale;
- 4 terminali correnti: `answer`, `ask`, `error`, `loop_break`;
- 4 adapter di crescita attivi, 6 kind legacy e 3 soli kind futuri chiusi;
- copertura aggiunta e verificata per indexing immagini, build e generazioni,
  cache contesto cartelle, thumbnails, file-hash cache, resource reservations,
  durable state/artifacts/source authority;
- nessun errore di scansione o verifica hash.

Questo risultato **non chiude D-G0.5**. La chiusura richiede ancora il work
manifest del coordinatore che assegni ogni ID di `stores` e
`growth_ingress.routes` a unità, file e test esatti.

## Riproducibilità e limiti

Le sei scansioni sono state eseguite due volte sul commit, con ordinamento
`LC_ALL=C`; ogni coppia ha lo stesso SHA-256.

| Scansione | Match | SHA-256 di entrambe le passate |
|---|---:|---|
| `owner_persistence` | 3209 | `ed32aca58aaf1a2a36a2e1cee783595ac161478060be5bd109c24d8bc30abda1` |
| `turn_writers` | 44 | `62a1b8376c95cc3b810391c62437a8efbea112322b7405d60903859a0d9e24b5` |
| `migrations` | 88 | `bb9bec2a76ba2f0c7d59c3d70a4464699baaf64d46a4f5562875eff788a1ad63` |
| `growth_registry` | 203 | `4323315301b3e47b6565b5c727dbf991982c939e5a5b6e278c2084e71fc33d4e` |
| `image_and_durable` | 167 | `f1a82b3b5ff57889c23a1a67feb024874298d944b5306879c722a4b622c6bb23` |
| `requested_test_roots` | 260 | `bf70d3ff418669aa5c8037fe76a9ec114415d27198b1eedb8a76763161be8931` |

I comandi esatti sono in `scans` nel JSON. Il manifest `input_sha256` contiene
110 chiavi, tutte path relativi tracciati al commit; ogni digest è stato
ricontrollato con `git show <commit>:<path>`. Roadmap rev8 e report precedente
sono input normativi/contestuali fuori dal commit e quindi, intenzionalmente,
non sono chiavi del manifest.

Le scansioni sono strumenti di discovery, non una prova di copertura globale.
La copertura dichiarata è limitata ai file sorgente adjudicati e manifestati;
path costruiti dinamicamente, servizi esterni e artefatti installati fuori dal
commit restano fuori perimetro. Le esclusioni globali devono diventare una
allowlist provata al freeze: l’assenza di `owner_user_id` non dimostra
anonimato.

## Confine di cancellazione osservato

Il confine corrente è `runtime/users.py:346-487`, coordinato dal lock in
`runtime/user_lifecycle.py:18-105`. Quest’ultimo non è il registry futuro:
serializza soltanto writer che adottano `owner_session` e controlla il
tombstone corrente. `delete_user` invoca purger per device/invocation,
recurring, deferred, pending dialog/capability/location, locations, OAuth RAM,
notices e cache tutor, poi cancella sessioni/conversazioni/preferenze/canali.

Restano fuori TurnLog e derivati, undo/scratchpad, cache engine, TurnEvent RAM,
auth sender-keyed, browser/uploads, telemetry, Sites, image index e cache,
durable state/artifacts/source snapshots e crescita globale. Inoltre il
tombstone conserva ancora il raw `user_id`.

Il candidato owner è:

- **D-P2.9** per il registry locale obbligatorio, mapping owner, delete-pepper,
  HMAC di revoca, tombstone casuale scollegato, purge e negazione di
  delayed/replay;
- **D-F6.6 tramite il registry D-P2.9** per sessioni/authorization remote,
  invocation e durable work, lease/reservation, outbox, artifacts e source
  grants.

## Inventario degli store

Le colonne “P2.9” e “F6.6” sono owner candidati di lifecycle, non
implementazione già presente. Writer e reader esatti, path multipli, purge
corrente e gap tecnico per ogni riga sono in `stores` nel JSON.

| Area | ID inventario | Mezzo / dato | Owner candidato | Gap dominante |
|---|---|---|---|---|
| identità | `identity.users` | `users.db`, utenti/canali/preferenze/tombstone raw | D-P2.9 | tombstone HMAC/random e registry shared-DB |
| identità | `identity.active_sessions` | `users.db` + RAM | D-P2.9 | delayed writer/restart proof |
| identità | `identity.pairing` | SQLite sender/channel | D-P2.9 | mapping owner autorevole e purge fisico |
| identità | `identity.approvals` | SQLite sender-keyed | D-P2.9 | owner mapping + purge/revoca |
| identità | `identity.policy_grants` | SQLite sender-keyed | D-P2.9 | owner mapping + purge/revoca |
| identità | `identity.devices` | SQLite owner/device/token | D-P2.9 | registrare purger oggi implicito |
| identità | `identity.invocations` | coda SQLite owner-bearing | D-F6.6 via D-P2.9 | delayed/replay denial indipendente |
| browser | `identity.playwright_sessions` | RAM sessioni/pending opens | D-F6.6 via D-P2.9 | close owner live e callback tardive |
| browser | `identity.playwright_profiles` | profili/cache filesystem | D-F6.6 via D-P2.9 | mapping per-owner o esclusione provata |
| ingressi | `identity.uploads` | file temporanei sender-scoped | D-P2.9 | mapping sender→owner, purge immediato |
| dialogo | `dialog.recurring` | SQLite | D-P2.9 | replay scheduler dopo purge |
| dialogo | `dialog.scheduler` | SQLite, owner in payload/prefix | D-P2.9 | colonna owner immutabile |
| dialogo | `dialog.chat_target` | SQLite, owner solo nello scope hash | D-P2.9 | mapping reversibile |
| dialogo | `dialog.deferred_turns` | JSONL | D-P2.9 | compaction e replay gate |
| dialogo | `dialog.pending` | JSON | D-P2.9 | race delete/save |
| dialogo | `dialog.capability_pending` | JSON | D-P2.9 | registry centrale |
| dialogo | `dialog.location_pending` | JSON | D-P2.9 | delayed resolve gate |
| dialogo | `dialog.locations` | JSONL | D-P2.9 | legacy unscoped + rewrite |
| dialogo | `dialog.notices` | JSONL owner-hashed | D-P2.9 | disponibilità mapping hash |
| auth | `dialog.oauth_pending` | RAM | D-F6.6 via D-P2.9 | callback e revoca remote |
| dialogo | `dialog.turn_events` | RAM | D-P2.9 | purge owner e publisher live |
| tutor | `dialog.tutor_conversation` | RAM | D-P2.9 | multiprocess/restart semantics |
| tutor | `dialog.tutor_probes` | RAM | D-P2.9 | delayed probe gate |
| turni | `turns.turnlog` | JSONL giornalieri | D-P2.9 | tre append, nessun purge |
| turni | `turns.feedback` | JSONL turn-linked | D-P2.9 | record senza owner persistito |
| turni | `turns.undo` | JSONL + blob | D-P2.9 | actor mapping e blob purge |
| turni | `turns.protected_undo` | SQLite/filesystem | D-P2.9 | ownership/adjudication completa |
| turni | `turns.scratchpad` | SQLite turn-linked | D-P2.9 | turn→owner prima del source purge |
| apprendimento | `turns.mnestoma` | SQLite query/evidence | D-P2.9 | source ledger + purge derivati |
| apprendimento | `turns.fastpath` | SQLite query-derived | D-P2.9 | provenance o redazione globale |
| apprendimento | `turns.autopath` | SQLite turn/intent/embedding | D-P2.9 | owner/source normalizzati |
| preferenze | `turns.argument_defaults` | SQLite actor/value | D-P2.9 | owner mapping + value purge |
| gap | `turns.terminator_lacunae` | SQLite raw query | D-P2.9 | privacy transform prima di crescita |
| tutor | `turns.tutor_gaps_associations` | SQLite owner-hashed | D-P2.9 | alias hash esaustivi |
| biometria | `biometric.persons` | SQLite + face examples | D-P2.9 | autorità globale/per-owner esplicita |
| durable | `durable.state` | SQLite custom schema 7 | D-F6.6 via D-P2.9 | hook delete, fence lease, restart/replay |
| durable | `durable.resource_reservations` | `resources_json` + lease/budget derivati | D-F6.6 via D-P2.9 | rilascio reservation e re-admission deny |
| durable | `durable.artifacts` | SQLite + blob filesystem | D-F6.6 via D-P2.9 | purge metadata+bytes coordinato |
| durable | `durable.source_authority` | SQLite schema 3 + snapshot | D-F6.6 via D-P2.9 | revoke/purge owner-wide |
| immagini | `image.folder_context_cache` | RAM LRU | D-P2.9 | purge owner/in-flight indexing |
| immagini | `image.build_workspace` | `.builds`, snapshot e parti | D-P2.9 | owner→corpus digest mapping |
| immagini | `image.generations` | SQLite/JSONL/NumPy/meta | D-P2.9 | inactive generation + publish fencing |
| immagini | `image.thumbnails` | filesystem cache | D-P2.9 | source/owner mapping e derivative purge |
| immagini | `image.web_photo_cache` | filesystem cache | D-P2.9 | owner/source mapping |
| cache | `cache.file_hashes` | SQLite per scope, schema 1 | D-P2.9 | scope hash non è lifecycle mapping |
| cache | `cache.http` | JSON URL/header/body | D-P2.9 | owner binding o allowlist globale |
| cache | `cache.consult_frontier` | JSON query/path-derived | D-P2.9 | owner perso nella chiave hash |
| telemetry | `telemetry.prefilter` | hash breve/query scores | D-P2.9 | provenance o anonimato provato |
| telemetry | `telemetry.vaglio` | raw intent/sender | D-P2.9 | togliere payload o purge owner |
| telemetry | `telemetry.observability_dashboard` | HTML derivato | D-P2.9 | invalidare output dopo purge |
| telemetry | `telemetry.proposals_eta` | SQLite derivato dai turni | D-P2.9 | owner/source watermark |
| Sites | `sites.audit` | JSONL owner-bearing | D-F6.6 via D-P2.9 | purge + revoca side effect remoto |
| secret/auth | `global.credentials` | file cifrati domain-keyed | D-F6.6 via D-P2.9 | esclusione globale o account mapping |
| crescita | `growth.change_intents` | SQLite globale | D-P2.9 | source ledger, codec e revoca fonte |
| crescita | `growth.telos_proposals` | SQLite/file | D-P2.9 | owner/source/retention comuni |
| crescita | `growth.proposals_state` | SQLite derivato | D-P2.9 | aggregazione purge-safe |
| crescita | `growth.markers` | marker filesystem | D-P2.9 | operation journal e replay revocation |

### Focus immagini e reservation

`runtime/durable_workloads/image_indexing.py` definisce il plan
`images.index.v1` e l’executor `create_images_indices`. Il contesto cartelle è
una LRU RAM da 1024 entry la cui chiave include owner, lingua, prompt, modello
e label, ma non esiste `purge_owner`. Le fasi conservano `resources_json`; per
l’executor viene forzato `vlm=1` anche nelle fasi senza chiamata modello.

`runtime/durable_workloads/storage.py:122-199` mostra che la reservation non è
una tabella autonoma: nasce da `resources_json`, catalog snapshot e budget
della revision, più attempt/lease fenced attivi nelle CTE
`reservation_revisions` e `model_reservations`. È dunque parte del lifecycle
durable F6.6, inclusi release e divieto di nuova ammissione dopo revoca.

`runtime/image_index_build.py` conserva path sorgente e snapshot sotto
`.builds/<generation>`; pubblica `lookup.sqlite`, `entries.jsonl`, embedding
NumPy e `meta.json` sotto `.generations/<generation>`. La root corpus è un hash
del path canonico, non dell’owner: senza una mappa owner→corpus, il purge
utente non è ricostruibile. La cancellazione esplicita di un indice non copre
il confine di cancellazione utente, né la race con publish.

### Fonti globali

Cache di catalogo in `affinity_semantic`, FTS5 prefilter e import/mirror sono
candidati a esclusione perché gli input osservati sono system/catalog-only;
anche il sink costi è candidato a esclusione perché non conserva prompt o
owner. Queste restano decisioni da provare con allowlist. Credenziali cifrate
e persons/face data non sono escluse: cifratura o assenza di owner non
risolvono autorità e lifecycle.

## TurnLog

La classe canonica è `runtime/agent_runtime.py:4043`; conserva
`durable_admission` a `:4057`, owner a `:4104`, e scrive a
`:5110-5608`. I terminali ammessi sono controllati a `:5124-5126`.

| Producer | Costruzione | Persistenza | Gap D-P2.9 |
|---|---|---|---|
| `turn.run_turn` | `runtime/agent_runtime.py:7515` | `TurnLog.write`, append `:5604` | schema/origin/outcome e purge centrale |
| `turn.http_pending` | `runtime/http_routes_agent.py:577` | `asdict`, append diretto `:594` | bypassa invarianti di `TurnLog.write` |
| `turn.tutor_async` | dict `runtime/tutor/telemetry.py:37-78` | append diretto `:105` | writer dict asincrono in race con delete |

Le 15 chiamate del flusso principale sono alle righe
`7131, 7155, 7192, 7555, 7600, 7635, 7757, 7765, 7781, 7802, 7845,
7893, 7902, 7966, 8012`. I ritorni che passano da
`_finalize_engine_result` sono a `:7680, :7951, :7998`; il producer
`loop_break` è `runtime/loop_detect.py:19`.

## Schema e numeri futuri non confliggenti

Non è stata eseguita né proposta come già approvata alcuna migrazione. I
numeri futuri sono **reservation candidate** che il coordinatore deve
congelare in D-G0.6.

| Store fisico | Meccanismo corrente | Corrente | Prossimo non confliggente |
|---|---|---:|---:|
| Birth epoch | `PRAGMA user_version` | 2 | 3 |
| Birth producer | `PRAGMA user_version` | 5 | 6 |
| Birth retention | `PRAGMA user_version` | 2 | 3 |
| durable state + artifact repository | ledger `durable_schema` condiviso | 7 | 8 |
| durable source authority | ledger custom | 3 | 4 |
| image index | `meta.json::schema_version` | 4 | 5 |
| file-hash cache | marker per riga | 1 | 2 |
| `users.db` condiviso | DDL/introspection, equivalente base 0 | 0 | 1 per D-P2.9 |
| change-intents ledger | DDL/introspection, equivalente base 0 | 0 | 1 per D-P2.1 |
| altro SQLite indipendente e non versionato | DDL/introspection | 0 | 1 solo quando assegnato |

Il numero appartiene al DB fisico: moduli che condividono `users.db` o il DB
durable non possono riservare ciascuno una propria versione 1/8. Se P2.9
modifica uno schema già versionato, deve usare rispettivamente durable 8,
source authority 4, image index 5 o file-hash 2, non ricominciare da 1.

## Crescita: registry, marker e Birth diretto

Il registry attivo in `runtime/change_intent_adapters/__init__.py` comprende
`synt`, `telos`, `introvertiva`, `user_feedback`; `multi_tool` e `canonical`
sono ritirati. I kind correnti in `runtime/change_intents.py:134-144` sono:

`create_executor`, `extend_executor`, `dedupe_executors`,
`materialize_pipeline`, `cache_pattern`, `reject_pattern`.

Il set futuro chiuso è invece soltanto:

`create_executor`, `extend_executor`, `promote_plan`.

Una `rejection_rule` è governance separata e non un effect. Non va quindi
convertito il legacy `reject_pattern` in un quarto kind futuro.

| ID ingresso | Producer → consumer | Stato/gap |
|---|---|---|
| `growth.adapter.synt` | adapter synt → materializer → upsert | create; fonte marker, non append-only |
| `growth.adapter.telos` | adapter telos → materializer → upsert | create/extend + materialize legacy |
| `growth.adapter.introvertiva` | adapter introvertiva → materializer → upsert | extend + dedupe legacy |
| `growth.adapter.user_feedback` | feedback → materializer → upsert | reject legacy; feedback senza owner persistito |
| `growth.direct.terminator` | terminator → learning loop → upsert | bypassa registry e include query raw |
| `growth.marker.telos` | introspect/store/actions → marker → synth consumer | percorso parallelo senza operation journal |
| `growth.marker.fastpath` | fastpath promote → marker o synth diretto | bypassa closed registry/replay ledger |
| `growth.direct.birth` | synth request → Birth submit | Birth raggiungibile fuori dalla porta futura |

Apply, observer e rollback hanno oggi sei handler, uno per ciascun kind legacy.
`cache_pattern` non ha più un producer attivo dopo il ritiro di `canonical`.
Ogni arco sopra deve avere owner D-P2.9 e un test producer→consumer→effect che
fallisca quando l’arco viene rimosso; i side effect remoti/durable prodotti
dall’arco ricadono anche in F6.6.

## Test esistenti e path da proporre

I path seguenti sono esatti; non contengono glob. Esistenti nei quattro root
richiesti:

- `tests/runtime/contracts/test_executor_birth_authoring.py`
- `tests/runtime/contracts/test_executor_birth_authority_gate.py`
- `tests/runtime/contracts/test_executor_birth_semantic_authority.py`
- `tests/runtime/engine/test_autopath_prune_aging.py`
- `tests/runtime/engine/test_autopath_raw_plan.py`
- `tests/runtime/engine/test_served_plan_reresolution.py`
- `tests/runtime/engine/test_terminator_operational_blame.py`
- `tests/runtime/infra/test_alignment_engine.py`
- `tests/runtime/infra/test_async_lifecycle.py`
- `tests/runtime/infra/test_log_lifecycle.py`
- `tests/runtime/infra/test_protected_undo.py`
- `tests/runtime/infra/test_sqlite_snapshot_completa.py`
- `tests/runtime/infra/test_turn_events_thread_safety.py`
- `tests/runtime/infra/test_turn_feedback.py`
- `tests/runtime/learning/test_change_applier.py`
- `tests/runtime/learning/test_change_intent_adapters.py`
- `tests/runtime/learning/test_change_intents.py`
- `tests/runtime/learning/test_change_observer.py`
- `tests/runtime/learning/test_change_rollback_publication.py`

Copertura esistente rilevante ma fuori dai quattro root richiesti:

- `tests/runtime/durable_workloads/test_artifacts.py`
- `tests/runtime/durable_workloads/test_durable_workload_storage.py`
- `tests/runtime/durable_workloads/test_image_indexing_boundaries.py`
- `tests/runtime/durable_workloads/test_image_indexing_e2e.py`
- `tests/runtime/durable_workloads/test_image_indexing_plan.py`
- `tests/runtime/durable_workloads/test_model_reservations.py`
- `tests/runtime/durable_workloads/test_source_authority.py`
- `tests/runtime/executors/test_delete_images_indices.py`
- `tests/runtime/executors/test_find_images_unified.py`
- `tests/runtime/executors/test_image_index_build_phases.py`

Nuovi path esatti da inserire nel work manifest:

- `tests/runtime/contracts/test_authenticated_origin.py`
- `tests/runtime/contracts/test_change_canonical.py`
- `tests/runtime/contracts/test_growth_privacy_migration.py`
- `tests/runtime/contracts/test_growth_registry.py`
- `tests/runtime/contracts/test_growth_store_registry.py`
- `tests/runtime/engine/test_gap_evidence.py`
- `tests/runtime/engine/test_implements_intent.py`
- `tests/runtime/engine/test_plan_templates.py`
- `tests/runtime/infra/test_growth_durable_lifecycle.py`
- `tests/runtime/infra/test_growth_image_lifecycle.py`
- `tests/runtime/infra/test_growth_snapshot.py`
- `tests/runtime/infra/test_user_data_lifecycle.py`
- `tests/runtime/learning/test_change_evaluations.py`
- `tests/runtime/learning/test_change_operations.py`
- `tests/runtime/learning/test_gap_adapter.py`
- `tests/runtime/learning/test_growth_adapter_transactions.py`
- `tests/runtime/learning/test_growth_metrics.py`
- `tests/runtime/learning/test_growth_policy.py`
- `tests/runtime/learning/test_one_shot_tokens.py`
- `tests/runtime/learning/test_rejection_rules.py`
- `tests/runtime/learning/test_usage_patterns.py`

I test immagine/durable esistenti caratterizzano il meccanismo, ma non provano
il lifecycle utente integrato. I due nuovi test infra devono quindi coprire
delete durante build/publish/lease, tutte le generazioni/cache/snapshot,
rilascio reservation, fencing e riavvio, usando esclusivamente fixture
temporanee e finti owner/pepper.

## Condizioni residue per G0.5

Il coordinatore deve trasformare questo inventario in un work manifest senza
glob che, per ciascuno dei 57 ID e degli 8 ingressi crescita:

1. assegni D-P2.9 oppure D-F6.6 con file/API concreti;
2. assegni mapping, purge, revoca, delayed/replay e prova restart;
3. congeli il numero di schema per ogni DB fisico condiviso;
4. assegni almeno un test esatto esistente da estendere o nuovo da creare;
5. trasformi ogni fonte globale esclusa in allowlist motivata e verificabile.

Fino ad allora l’inventario è riproducibile e consolidato sul commit RM-0008,
ma D-G0.5 resta aperto.
