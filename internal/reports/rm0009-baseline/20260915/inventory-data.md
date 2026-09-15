# RM-0009 D-G0.5 — inventario dati, TurnLog e crescita (fotografia non congelata)

Data della ricognizione: 2026-09-15 (Europe/Rome).

## 1. Esito e perimetro

Questo documento e una ricognizione dal **codice**, non una certificazione di
completezza e non chiude D-G0.5. La baseline RM-0008 e ancora mobile: il
worktree `/opt/metnos/.claude/worktrees/rm0008-reboot` era a
`de76f375d2704062a9248ce4b718d75cc634eb34` (discendente di un commit da
`1a9f42fb`) e aveva modifiche non committate a
`internal/reports/rm0008-release-20260915.md`, oltre a `BACHECA` e
`internal/coordination/` non tracciati. Il `main` osservato era
`037840f455c005899dcaf089cc2e52dd614f07de` e non e una base valida da
congelare al posto del rilascio RM-0008.

Sono stati letti integralmente `CLAUDE.md`, `CLAUDE.mutabile.md` e
`internal/AGENTS.md`; della roadmap
`internal/roadmap/RM-0009-crescita-allineata-delle-capacita.md` sono state
applicate le parti normative §§1-6, A, D ed E.2. Non sono stati letti DB
personali, file dei turni, credenziali, secret o payload utente. Non sono stati
avviati servizi, test mutanti, migrazioni o writer.

L'inventario include gli store trovati con riferimenti diretti o indiretti a
owner/principal/identita e gli store non owner-scoped che conservano contenuto
personale o derivato dai turni. Non dimostra l'assenza di store costruiti
dinamicamente, fuori da `runtime`/`client-rs`, o introdotti dal commit RM-0008
finale. Le righe sotto sono quindi candidati obbligatori del registry D-P2.9,
non la sua lista congelata.

## 2. Metodo riproducibile e stabilita delle scansioni

Ogni scansione deterministica usata come evidenza e stata eseguita due volte
nella stessa fotografia; le due uscite hanno prodotto lo stesso SHA-256.
L'ordinamento e forzato con `LC_ALL=C`. I comandi non aprono file dati.

| Scansione | Comando riproducibile (corpo eseguito per `pass in 1 2`) | SHA-256 identico |
|---|---|---|
| riferimenti owner | `rg -n --glob '*.py' --glob '*.rs' --glob '*.toml' --glob '!*.retired-v1' '(owner_user_id|owner_id|principal_user_id|operational_owner|authenticated_user_id|user_id)' runtime client-rs executors 2>&1 \| LC_ALL=C sort \| sha256sum` | `ed8693d78493c1a04c452b45622a105517ed7185b5d902f0ad929464759feced` |
| store e writer | `rg -n --glob '*.py' --glob '*.rs' --glob '!*.retired-v1' 'sqlite3\.connect|aiosqlite|\.sqlite|\.db\b|jsonl|write_text|open\(' runtime client-rs 2>&1 \| LC_ALL=C sort \| sha256sum` | `5feca241ddd4fac28884372f4c87240b074fdb9870540887159566101ef535f0` |
| TurnLog generale | `rg -n --glob '*.py' --glob '*.rs' --glob '!*.retired-v1' 'TurnLog\(|\.write\(\)|final_kind\s*=|final_kind\s+in|final_kind\s*==' runtime 2>&1 \| LC_ALL=C sort \| sha256sum` | `eb74d2dbbc616a0a6e2eba8f7fa818aa5ca7ed5e3f657e9f414ed3f0ffe26ff9` |
| costruttori/write focalizzati | `rg -n 'log\.write\(\)|TurnLog\(' runtime/agent_runtime.py runtime/http_routes_agent.py runtime/tutor/telemetry.py 2>&1 \| LC_ALL=C sort \| sha256sum` | `1581ec383ea7b32acb5dce9d3ac2d7dd04b1670a5f844193f89ea18f9c562` |
| uscite terminali | `rg -n 'return log|return _finalize_engine_result|return recorded_answer|_persist\(' runtime/agent_runtime.py runtime/http_routes_agent.py runtime/tutor/telemetry.py 2>&1 \| LC_ALL=C sort \| sha256sum` | `c6b9385f2dd5545495a64f699b15ba1a1c3a7d4faa0de031d4e537b83326943e` |
| `user_version` | `rg -n --glob '*.py' --glob '*.rs' 'PRAGMA user_version|user_version[[:space:]]*=' runtime client-rs 2>&1 \| LC_ALL=C sort \| sha256sum` | `4d5b3ee483c63a049e9d4eb234704c2e50cf9170d2df6a6c2cbd6e369197fd4f` |
| migrazioni estese | `rg -n --glob '*.py' --glob '*.rs' --glob '!*.retired-v1' 'PRAGMA table_info|ALTER TABLE|CREATE TABLE.*schema|schema_version|CURRENT_SCHEMA_VERSION|_SCHEMA_VERSION|EPOCH_STORE_SCHEMA_VERSION|def _?migrat' runtime client-rs 2>&1 \| LC_ALL=C sort \| sha256sum` | `d23cd7bb4acf9d335fad2fe82ebc096191749337e5880b250e2b21881e9c7336` |
| registri crescita | `rg -n --glob '*.py' --glob '!*.retired-v1' 'iter_all|_HANDLERS|CHANGE_KIND|JUDGE_KIND|ADAPTER|source_kind|synt_pending|handle_synth_request|submit_.*birth|submit_synth' runtime 2>&1 \| LC_ALL=C sort \| sha256sum` | `ce8efa12a9823e5ac201ae6363994391532d4a5d04e45557776d9f88eae9dd42` |
| candidati path configurabili | `rg -n --glob '*.py' --glob '!tests/**' --glob '!runtime/tests/**' 'PATH_USER_(DATA|STATE|CONFIG).*(\.jsonl|\.json|\.sqlite|\.db|credentials|turns|pending|workloads|audit|examples|uploads)|DEFAULT_(DB|LOG|BLOBS)|AUDIT_PATH|FEEDBACK_PATH|TURNS_DIR' runtime 2>&1 \| LC_ALL=C sort \| sha256sum` | `2478c3d7e7051c2d1814d9e46e149e7873a3ba4385e913cdacf0f3cc41c08dc4` |
| topologia suite candidate | `rg --files tests/runtime/learning tests/runtime/infra tests/runtime/contracts tests/runtime/engine 2>&1 \| LC_ALL=C sort \| sha256sum` | `8c2e4688056afd4281edb9c9e5bcbd684dde350fd830bddc7400c5f60819215d` |
| suite affini gia presenti | `rg --files tests/runtime \| rg 'auth|origin|user|delete|lifecycle|privacy|snapshot|turn.*log|http' \| LC_ALL=C sort \| sha256sum` | `dbf2ea4fde4ac67058f6ef75fe366fd21e1923e037f1f429fb5e9bff1e0aece9` |
| confronto strutturale main/RM-0008 | per ciascuna radice `/opt/metnos` e `/opt/metnos/.claude/worktrees/rm0008-reboot`: `rg -n --glob '*.py' 'class TurnLog|TurnLog\(|\.write\(\)|final_kind[[:space:]]*=|def purge_owner|PRAGMA user_version|sqlite3\.connect|\.jsonl' <radice>/runtime 2>&1 \| LC_ALL=C sort`; concatenazione poi `sha256sum` | `dc74f74e91b06a43b4a92bbf485c4036797a0c14d26b01b329ce41a207e4e3df` |
| stato HEAD dei due alberi | concatenazione di `git -C /opt/metnos rev-parse HEAD`, `git -C /opt/metnos/.claude/worktrees/rm0008-reboot rev-parse HEAD`, `git -C /opt/metnos/.claude/worktrees/rm0008-reboot status --short --branch`, poi `sha256sum` | `7b908360008a3dd9727050e22cc34e970b247c17bd5292e51bb8009e54e45503` |

Conteggi derivati dalla fotografia: 2 costruttori reali `TurnLog`, 15 chiamate
`log.write()`, 3 siti `append_private_bytes` nei tre writer focalizzati
(incluso quello dentro `TurnLog.write`), 17 assegnazioni letterali di
`final_kind`, 4 adapter attivi, 6 kind registrati, 6 handler apply, 6 observer e
6 rollback. Questi conteggi sono riproducibili dalle scansioni sopra, ma vanno
ricalcolati sul commit RM-0008 finale.

Esclusioni intenzionali: `*.retired-v1`, test nelle scansioni che cercano store
di esercizio, dati sotto `PATH_USER_DATA`/`PATH_USER_STATE`, artefatti runtime,
log e secret. `git status` non ha potuto ispezionare
`install/data/i18n_seed.sqlite`/`install/data/` (`Permission denied`): il limite
e registrato e nessun tentativo di aggiramento e stato fatto. Due tentativi di
comando incompleti con errore di sintassi shell sono stati scartati e non sono
usati come prova.

## 3. Radici configurabili

`runtime/config.py:119-128` risolve `PATH_USER_DATA`, `PATH_USER_STATE`,
`PATH_USER_CONFIG` e cache, inclusi `METNOS_USER_DATA` e
`METNOS_USER_STATE`. I path principali sono dichiarati in
`runtime/config.py:539,547,549,551,556,568,570,572,574,576,578,580,582,584,586,588,592-594,597,602,604`;
il log generale e a `runtime/config.py:695-696`. Dire “configurabile” nelle
tabelle significa variabile dedicata oppure derivazione da una di queste
radici; “no” significa path fisso relativo alla radice o passato dal chiamante,
non configurazione per-store.

## 4. Store owner-bearing, personali o derivati da turni

### 4.1 Identita, autenticazione, sessioni e invocazioni

| Store/path | Configurazione e ownership | Writer / reader esatti | Purge attuale o lacuna |
|---|---|---|---|
| utenti e canali SQLite, `runtime/users.py:44` (`PATH_USER_DATA/users.db`) | `METNOS_USERS_DB`, `runtime/users.py:85-86`; `users`, `user_channels`, tombstone a `runtime/users.py:50-76` | `_open_db` `:89`; `create_user` `:211`; getter `:287,:304`; `delete_user` `:346` | `delete_user` marca/revoca e cancella righe `:358-461`, ma scrive il **raw user id** nel tombstone `:457-461`; manca HMAC delete tag + tombstone casuale D-P2.9 e il call graph non copre tutti gli store sotto |
| sessioni/conversazioni nello stesso `users.db` | owner diretto; schema `runtime/active_sessions.py:51-78`; cache RAM `_PENDING_TAKEOVERS` `:48` | `_open_db` `:87-96`; migration additiva `conversation_id` `:99-114`; lettori/writer nello stesso modulo | righe DB cancellate da `users.delete_user:432-446`; RAM con `purge_user_memory` `active_sessions.py:126`, chiamato `users.py:481-486` |
| pairing legacy nello stesso DB configurabile, `runtime/pairing.py:37,102-107` | `pairings`/`consumed_codes` `:44-64`; identita per sender/channel, non owner immutabile | `consume_code` `:181-219`; lettori `:226-276`; migrazione `actor/display_name` `:108-117` | `users.delete_user:396-404` revoca pairing dei canali noti, non cancella fisicamente pairings/codici consumati; owner non sempre ricostruibile |
| approval registry SQLite, `runtime/approval_registry.py:31,87-93` | dedicato env; chiave sender, schema `:36-54`, non owner | create `:102-141`; read `:144` | nessun `purge_owner`; correlazione owner dipende da mapping esterno |
| policy grants SQLite, `runtime/policy.py:29,321-327` | env dedicato; schema sender-based `:286-302` | `record_grant` `:330-357`; read `:360-409` | nessun `purge_owner`; stesso difetto di correlazione |
| device/token DB, `runtime/devices.py:40,140-146` | env dedicato; owner nelle tabelle `:55-93` | migrazione `:150-180`; lettori `:253,:268,:712`; writer join/token nello stesso modulo | `purge_owner` `:297-356` elimina device/token/join e anche invocations co-locate; chiamato da `users.delete_user:399-406` |
| invocation queue nello stesso DB, `runtime/invocations.py:58-82` | owner diretto; `_open_db` `:559`; migrazioni additive `:89-127` | enqueue `:681`, insert `:778-790`; lettori `:937,:1170,:1197` | retention per eta `purge` `:803-827`; owner purge soltanto indirettamente via `devices.purge_owner`, quindi accoppiamento non dichiarato e fragile |
| session broker Playwright RAM, `runtime/playwright_sidecar/session_broker.py:208` | entry con owner/owner_user_id, processo-locale | creazione entry `:1433`; lookup/filtri owner `:1068`; close individuale intorno a `:1998` | reaper TTL `:1012-1036` e shutdown `:1045`; nessun `purge_owner` richiamato da cancellazione utente |
| upload temporanei, `runtime/channels/daemon.py:64`, `runtime/channels/telegram.py:31,489` | cartelle per sender sotto radice dati; non owner immutabile | writer daemon/Telegram e route HTTP intorno a `runtime/http_routes_agent.py:1700` | solo cleanup TTL `runtime/upload_cleanup.py:22,72`; nessun purge owner e mapping sender necessario |

### 4.2 Stato dialogico e notifiche

| Store/path | Configurazione e ownership | Writer / reader esatti | Purge attuale o lacuna |
|---|---|---|---|
| recurring SQLite, `runtime/recurring_tasks.py:42` | `config.DB_RECURRING`; owner diretto | `_open_and_migrate` `:163`; rebuild/migration owner `:174-264`; `register_user_task` `:507`; read `:584,:607,:632` | `purge_owner` `:715-745`, inclusi job scheduler; chiamato da `users.delete_user:414` |
| scheduler v2 SQLite, `runtime/scheduler_v2/storage.py:23` | scelta env in `runtime/scheduler_v2/client.py:29-35`; owner nel payload/prefisso, non colonna vincolata | upsert `storage.py:135`; read da `:250`; migrazione additiva `:108-124` | `purge_owner` `:187-222` cerca payload/prefisso; fragile rispetto a payload non canonico |
| chat target SQLite, `runtime/chat_target_store.py:20-37` | env/fallback; owner incorporato in scope hash `:49-70`, tabella conserva solo `sender_id` | read `:73-101`; write `:104-117` | nessun purge; owner non ricavabile dal solo hash senza mappa esterna |
| deferred turns JSONL, `runtime/deferred_turns.py:25-26` | path sotto root configurata; owner diretto | `_append` `:58`; `_load` `:70`; `add` `:101`; `mark` `:121`; `pending` `:134` | `purge_owner` `:225`; `purge_unscoped` `:232`; chiamato da `users.delete_user:415` |
| dialog pending JSON, `runtime/dialog_pending.py:73` | root configurata; owner diretto | `save` `:183`, create `:224`, consume `:403`; read `:278,:291,:322` | purge owner `:588`, unscoped `:645`, expiry `:681`; chiamato da `users.delete_user:408` |
| capability pending JSON, `runtime/channels/daemon.py:70` | root configurata; owner nel record | write `:81-115`; read `:122-150` | purge `:159-180`; chiamato da `users.delete_user:409` |
| location pending JSON, `runtime/location_request.py:46-47` | root configurata; owner diretto | request `:72-103`; read/resolve `:114,:181` | purge `:228-256`; chiamato da `users.delete_user:418` |
| locations JSONL, `runtime/location_store.py:18-19` | path sotto root; owner diretto | append `:72`; read `:105` | purge owner `:153`, unscoped `:160`; chiamato da `users.delete_user:419` |
| notices JSONL per owner hash, `runtime/user_notices.py:23,38` | directory configurata; nome file derivato dall'owner | append `:42`; drain `:67` | purge owner `:106`, unscoped `:123`; chiamato da `users.delete_user:422` |
| OAuth pending RAM, `runtime/oauth_pending.py:23-24` | owner diretto, processo-locale | write `:35-57`; read `:60,:69` | purge `:83-97`; chiamato da `users.delete_user:420` |
| TurnEventLog RAM, `runtime/turn_events.py:22-23,55-69,87-90` | owner diretto in `_TurnState`; singleton process-local | create `:106-124`; read `:126`; append `:149`; snapshot `:252` | solo GC TTL `:280`; nessun purge owner/call da `delete_user` |
| tutor conversation RAM, `runtime/tutor/conversation.py:31` | owner diretto nella chiave/cache | reader `:62,:84`; writer `:110` | purge `:152`, chiamato da `users.delete_user:428` |
| tutor probes RAM, `runtime/tutor/probes.py:397` | owner nella cache | read `:420`; write `:438` | purge `:448`, chiamato da `users.delete_user:429` |

### 4.3 Turni, feedback, undo e cache derivate

| Store/path | Configurazione e ownership | Writer / reader esatti | Purge attuale o lacuna |
|---|---|---|---|
| turn JSONL giornalieri, `runtime/config.py:549`, `runtime/agent_runtime.py:200` | `PATH_USER_DATA/turns`; record con `owner_user_id` | `TurnLog.write` `agent_runtime.py:5173`, append `:5711-5735`; writer HTTP `http_routes_agent.py:524,547-568`; tutor `tutor/telemetry.py:37,45-78,89,101-105` | nessun purge owner; e una lacuna bloccante D-P2.9 |
| feedback JSONL, `runtime/turn_feedback.py:34-36` | path root; il record scritto `:136-160` omette owner e dipende dal lookup del turno `:41-65` | `apply_feedback` `:113`; append `:257-259`; read `:272,:310,:350` | nessun purge; dopo cancellazione dei turni la provenienza owner non e ricostruibile |
| undo JSONL + blobs, `runtime/undo.py:26-27` | root configurata; record `actor/channel`, non owner immutabile | `UndoLog` `:55`; `_append` `:62`; `append_pending` `:73`; completion `:93-151`; read `:153-261` | purge solo per eta `:263-293`; nessun owner purge |
| scratchpad SQLite, `runtime/scratchpad.py:38,45-60` | `config.DB_SCRATCHPAD`; chiave `turn_id`, nessun owner | `Scratchpad.put` `:139-178`; read `:242,:248,:310` | GC TTL `:133`; nessun owner purge/catena autorevole turn→owner |
| mnestoma SQLite, `runtime/mnestoma.py:50,222-226` | `MNESTOMA_DB_PATH`; eventi/query canoniche con turn id ma senza owner | schema `:86-104,:122`; write `record_canonical_query` `:271`, `record_passing` `:368`; read `:600,:702` | delete per query `:501`, proto purge `:770`; nessun purge owner |
| fastpath SQLite, `runtime/engine/fastpath.py:136-174` | `PATH_USER_DATA/fastpaths.sqlite`; testo canonico/query-derived, nessun owner | `record_success` `:295-372`; read `:394` | delete selettivo `:415,:428`, retention `:651`; nessun purge owner |
| autopath SQLite, `runtime/engine/autopath.py:79-175` | `PATH_USER_DATA/autopath.sqlite`; turn id, intent/framework/embedding, nessun owner | `record_observation` `:454`; feedback `:535`; list `:805` | prune da `:209`; nessun owner purge o provenienza normalizzata |
| argument defaults SQLite, `runtime/args_defaults.py:31-46` | chiave `actor`, conserva `last_value` | `_conn` `:49-59`; read `:108`; write `:121` | age sweep `:140`; nessun owner immutabile/purge |
| terminator lacune SQLite, `runtime/engine/terminator.py:46-73` | `PATH_USER_DATA/terminator_log.sqlite`; query raw, nessun owner | `_record_lacuna` `:76-105`; richiama `learning_loop.propose_from_lacuna` `:105` | nessun owner purge; e insieme fonte globale e contenuto privato non canonicalizzato |
| tutor gap/associazioni SQLite, `runtime/tutor/gaps.py:21`, `runtime/tutor/associations.py:22-24` | owner hash; path configurato | gaps schema `gaps.py:49-98`, write `:209`, read `:445`; associazioni migration `associations.py:126`, write `:318,:371`, read `:475` | `gaps.purge_owner` `:524-537` chiama `associations.purge_owner` `:500`; chiamato da `users.delete_user:410` |
| persons/face data, `runtime/persons_registry.py:35-36` | root dati; tabelle `:68-90` non hanno owner | enroll `:212-359`; get/list `:509,:539` | delete per person `:362` e file examples `:389`; nessun lifecycle owner; natura biometrica richiede decisione esplicita se globale o owner-scoped |

### 4.4 Durable, artefatti e credenziali

| Store/path | Configurazione e ownership | Writer / reader esatti | Purge attuale o lacuna |
|---|---|---|---|
| durable workload SQLite | `runtime/config.py:592-593`; default `runtime/durable_workloads/migrations.py:37-42`; owner diretto | `open_db` `migrations.py:80`; `DurableWorkloadStore.open` `storage.py:672`; create draft `:747`; read `:839,:868` | `DurableWorkloadStore.purge_owner` `:8288-8316`, con verifica residui su 18 tabelle; **non chiamato** da `users.delete_user` |
| durable artifact metadata + filesystem | root `runtime/config.py:594`; repository DB co-locato e `ArtifactStore` root esplicita | `ArtifactRepository.open` `runtime/durable_workloads/artifacts.py:457`; register `:493`; read `:671,:682`; `ArtifactStore` `:1054-1076`, read `:1435,:1468` | `delete_owner_rows` `:1023`; `ArtifactStore.delete_owner` `:2073-2077`; non registrati in `delete_user` |
| source authority SQLite + snapshots | default `runtime/durable_workloads/source_authority.py:55-58`; snapshot root derivata dal parent DB `:240-247` | open `:230`; inventory/register `:520-600`; resolve `:610` | revoke singolo workload `:694`, prune `:778`; nessun purge owner-wide e nessun hook delete-user |
| Sites audit JSONL | `runtime/sites_audit.py:30-34`; path configurato | `record` `:69-94` riceve owner e campi sanitizzati; reader `_read_sites_audit` `runtime/credential_mandates.py:107`, `verified_site_topology` `:124` | sola rotazione; nessun owner purge |
| credential files cifrati | `runtime/credentials.py:45-46`; root configurata | store `:100-125`; load `:128`; list `:147`; remove per dominio `:160` | file keyed per dominio, non owner; nessun owner purge. Il contenuto non e stato letto |

### 4.5 Store globali della crescita con provenienza privata possibile

| Store/path | Configurazione e ownership | Writer / reader esatti | Purge attuale o lacuna |
|---|---|---|---|
| change intents SQLite, `runtime/config.py:602` | schema `runtime/change_intents.py:49-96`; nessuna tabella fonti append-only, FK o versione; campi top-level origin possono includere testo/provenienza | `_conn` `:294`; `upsert_intent` `:356-423`; read `:426-535`; transizioni `:544-654` | nessun purge/source tombstone; stato aggiornabile fuori da CAS (`:628-643`); globale lecito solo dopo canonicalizzazione/redazione F2.4 |
| TELOS proposals/decisioni | `runtime/telos_introspect.py:41`; store in `runtime/telos_proposals_store.py` | producer `_persist` `telos_introspect.py:78-114,391-420`; read/decision `telos_proposals_store.py:295-378` | nessun contratto fonti/owner comune; on-accept produce marker legacy |
| proposals state SQLite | env/path `runtime/proposals_state.py:25-30`; schema `:36-54` | lookup `:98`; `touch_or_insert` `:115-215`; `mark_action` `:218` | nessun owner/source ledger; retention maintenance non equivale a purge |
| marker synth/change/pipeline pending | path `runtime/proposal_actions.py:41-44` | `_write_marker` `:85-94`; `on_accept` `:97-189` | lifecycle file ad hoc; nessun owner registry comune; consumer puo chiamare sintesi direttamente |

Gli store aggregati senza evidente dato personale (per esempio statistiche di
guardia) non sono stati promossi automaticamente a owner-bearing. Prima del
freeze D-G0.6 serve una decisione documentata per ogni esclusione; la sola
assenza della stringa `owner_user_id` non prova che un payload sia anonimo.

## 5. TurnLog: costruttori, writer e terminali

La struttura canonica e `runtime/agent_runtime.py:4109` (`class TurnLog`), con
`final_kind` a `:4118`, `owner_user_id` a `:4169` ed `error_class` a `:4185`.
Non contiene ancora i contratti RM-0009 chiusi per origine/esito/intent hash.
`TurnLog.write` e a `:5173`; accetta come terminali
`answer`, `ask`, `error`, `loop_break` a `:5187-5189` e appende il JSONL a
`:5711-5735`.

Costruttori e scritture:

| Percorso | Costruttore/writer | Uscita |
|---|---|---|
| `runtime/agent_runtime.py:7549-7552` | unico costruttore del flusso `run_turn` | `log.write()` a `:7165,:7189,:7226,:7589,:7634,:7669,:7791,:7799,:7815,:7836,:7879,:7927,:7936,:8000,:8046` |
| `runtime/http_routes_agent.py:524,547-561` | `_persist_pending_http_turn` costruisce davvero `TurnLog(...)` e lo converte con `dataclasses.asdict`, senza chiamare `TurnLog.write` | `final_kind="answer"`; append diretto `:562-568`, failure best-effort |
| `runtime/tutor/telemetry.py:37,45-78` | `_prepare` costruisce un dict compatibile, non `TurnLog` | `_persist` `:89`, append diretto `:101-105`, `final_kind="answer"` |

Assegnazioni letterali osservate in `runtime/agent_runtime.py`: ask
`:7151,:7180,:7631,:7787`; answer/default answer
`:7191,:7587,:7796,:7832,:7866,:7924`; error
`:7668,:7813,:7933,:7994,:8044`. `loop_break` e prodotto dal contratto di
`runtime/loop_detect.py:19` e puo arrivare dai risultati engine. Le chiusure che
passano da `_finalize_engine_result` (`runtime/agent_runtime.py:7123`) scrivono
internamente; i ritorni engine rilevanti sono a `:7714,:7985,:8032`.

L'elenco non puo ancora essere congelato: rispetto a `1a9f42fb`, il main
modifica `runtime/agent_runtime.py` e la struttura corrente ha rimosso il campo
`durable_admission`; il worktree RM-0008 modifica a sua volta
`runtime/agent_runtime.py` nel commit `de76f375`. Ogni costruttore, append
diretto, terminale e linea va rieseguito sul commit finale.

## 6. Versioni schema e migrazioni esistenti

La ricerca esatta di `PRAGMA user_version` trova soltanto tre moduli attivi:

| Store | Versione e simboli | Migrazione/comportamento |
|---|---|---|
| Birth epoch | `runtime/executor_birth_epoch_store.py:27-28` `EPOCH_STORE_SCHEMA_VERSION=2`; PRAGMA `:275,:292` | schema vuoto v0 creato direttamente a v2; DB non vuoto non versionato o versione diversa fallisce chiuso |
| Birth producer | `runtime/executor_birth_producer_store.py:15` `_VERSION=5`; PRAGMA `:89,:130` | `_migrate` `:84`; passaggi v1→v5 a `:94,:109-110,:117-129` |
| Birth retention | `runtime/executor_birth_retention.py:265` `_SCHEMA_VERSION=2`; PRAGMA `:365,:375,:383` | v0 vuoto→2; v1→2 con migrazione additiva |

`runtime/executor_birth_epoch_store.py:222` contiene un riferimento solo in
commento. Gli altri store usano DDL idempotente, `PRAGMA table_info`,
`ALTER TABLE`, rebuild locali o ledger custom, quindi non condividono un
numero `user_version`:

- durable usa `durable_schema`, versione 7:
  `runtime/durable_workloads/migrations.py:19,138,1589-1692`;
- source authority usa schema custom versione 3:
  `runtime/durable_workloads/source_authority.py:35,250-320`;
- active sessions migra `conversation_id` con introspezione:
  `runtime/active_sessions.py:99-114`;
- pairing migra `actor/display_name`: `runtime/pairing.py:108-117`;
- devices migra colonne/tabelle: `runtime/devices.py:150-180`;
- invocations migra additivamente: `runtime/invocations.py:89-127`;
- recurring ricostruisce/migra ownership: `runtime/recurring_tasks.py:174-264`;
- scheduler v2 migra additivamente: `runtime/scheduler_v2/storage.py:108-124`;
- tutor associations migra il legacy: `runtime/tutor/associations.py:126`.

Gap del runtime da trasformare in contratto a D-G0.6: non esiste oggi un
ledger unico per il nucleo change intent, ne un contratto condiviso di FK-on,
upgrade/downgrade, rollback simulato e schema unknown fail-closed. Il freeze
deve fissare numeri, ownership, ordine e prove; non richiede che il ledger sia
gia implementato. I numeri futuri non devono essere inventati finche la base
RM-0008 e il work manifest non sono congelati.

## 7. Registry crescita: producer → consumer → effetto

Il registry adapter attivo e chiuso nel codice in
`runtime/change_intent_adapters/__init__.py:34-41`: `synt`, `telos`,
`introvertiva`, `user_feedback`. `multi_tool` e `canonical` sono ritirati a
`:14-20`. I sei kind in `runtime/change_intents.py:134-144` sono
`create_executor`, `extend_executor`, `dedupe_executors`,
`materialize_pipeline`, `cache_pattern`, `reject_pattern`.
Questi sono i **sei kind legacy osservati**, non il set futuro approvato. Il
codec e gli effect RM-0009 nuovi riguardano soltanto
`create_executor`, `extend_executor` e `promote_plan`; i sei kind correnti
(il set legacy complessivo, con create/extend sovrapposti al futuro)
vanno caratterizzati per sola lettura, migrazione o ritiro, senza trasformarli
in sei effect nuovi.

| Producer/sorgente | Consumer | Kind/effect osservato e lacuna |
|---|---|---|
| `runtime/change_intent_adapters/synt.py:33-103`, legge `synt_proposals` | `runtime/jobs/change_intent_materialize.py:26-75` → `change_intents.upsert_intent` | `create_executor`; stato derivato dai marker, fonte non append-only |
| `runtime/change_intent_adapters/telos.py:42-113`, legge TELOS | stesso materializzatore | `existing_parametric`→`extend_executor` `:71-79`; `existing_pipeline`→`materialize_pipeline` `:80-90`; `new_valid`→`create_executor` `:91-99` |
| `runtime/change_intent_adapters/introvertiva.py:51-146`, legge proposals_state | stesso materializzatore | dedupe→`dedupe_executors` `:85-95`; generalize/specialize→`extend_executor` `:96-128` |
| `runtime/change_intent_adapters/user_feedback.py:29-100`, legge turn feedback | stesso materializzatore | duplicati negativi→`reject_pattern`; dipende da feedback privo di owner persistito |
| `runtime/engine/terminator.py:76-105` | `runtime/learning_loop.py:37-90` → `change_intents.upsert_intent` | `create_executor` diretto da lacuna ricorrente; aggira registry adapter/source authority e include query sample |
| `runtime/telos_introspect.py:78-114,391-420` | `runtime/telos_proposals_store.py:295-378` → `runtime/proposal_actions.py:97-189` | scrive marker `synt_pending`/change/pipeline; percorso legacy parallelo |
| `runtime/proposal_actions.py:85-189` | `runtime/telos_synth_consumer.py:66-115,118,195` | consumer marker chiama direttamente `handle_synth_request` |
| `runtime/engine/fastpath_promote.py:276-340` | proposals_state; approvazione `:379-430`; auto `:436-505` | scrive marker synth o chiama direttamente `handle_synth_request`, senza operation journal |
| `runtime/synth_request.py:382,704` | Birth submit imports/call `:23,:278,:442` | proposal JSON e invio Birth diretto |

Il materializzatore registra in audit a
`runtime/jobs/change_intent_materialize.py:78-86`. Apply ha sei handler, uno
per kind, in `runtime/change_applier.py:310-317`; il job consumer e a
`:320-374`. Effetti: create→`handle_synth_request` `:60-101`;
extend→`change_applier_extend` `:106-120`; dedupe→alias JSON/statistiche
`:125-190`; materialize→`run_turn` `:195-234`; cache→mnestoma
`:239-266`; reject→append rejected patterns `:271-305`.
Observer: sei handler in `runtime/change_observer.py:307-313`, task da `:316`.
Rollback: dispatch `change_observer.py:381-406`, sei handler in
`runtime/change_rollback.py:270-277`.

Non c'e un producer attivo per `cache_pattern` dopo il ritiro di `canonical`.
Viceversa esistono ingressi diretti alla sintesi/Birth che non transitano dal
registry e dall'operation journal. Il registry corrente non e quindi il
registro chiuso richiesto da D-F1.1 e non puo essere usato come prova di
unicita della porta di crescita.

## 8. Gap osservati e contratti da congelare in D-G0.6

I punti seguenti descrivono due cose diverse: (a) condizioni documentali
necessarie per poter congelare D-G0.6; (b) lacune del runtime attuale che
D-G0.6 deve trasformare in nomi, schema, DAG, ownership e prove di uscita.
**Non** richiedono che i moduli P/F futuri siano gia implementati prima del
freeze documentale.

1. **Condizione documentale: baseline finale e contratto esterno.** Manca il
   commit finale RM-0008 su cui rifare l'inventario. Per `EXT-RM0008-F5` vanno
   congelati formato, lettore e verifica machine-readable, ma la disponibilita
   di una prova F5 reale **non** e prerequisito di G0.5, G0.6 o I1.1: e gate di
   esercizio per D2.
2. **Gap runtime da coprire nel contratto lifecycle.** `users.delete_user` chiama soltanto i purger
   elencati a `runtime/users.py:408-430` piu device/sessioni; non copre turni,
   feedback, undo, scratchpad/cache, TurnEventLog, auth legacy, durable,
   artifact/source authority, Sites audit, credenziali, browser session e
   upload. Alcuni store sono irreversibilmente keyed per sender/hash/turn id.
3. **Gap runtime da modellare nel contratto privacy.** Il tombstone conserva
   l'ID raw. D-G0.6 deve congelare proprietario/file/API e prove per delete
   pepper protetto, HMAC di revoca, tombstone casuale scollegato e negazione
   fail-closed di richieste ritardate/replay; l'implementazione e D-P2.9.
4. **Contratto schema da congelare.** Il runtime attuale non ha ledger
   versionato, FK-on, fonti append-only, alias canonici, CAS esaustiva, epoch
   unico, operations/outbox, one-shot token e rejection rules separate.
   D-G0.6 deve fissarne schema, numeri, dipendenze e ownership; non crearli.
5. **Gap runtime da modellare nel codec/privacy.** Query, summary, rationale/body,
   defaults e cache possono contenere testo/valori personali; non esiste il
   codec chiuso dei tre kind approvati ne quarantena/redazione legacy.
6. **Gap runtime TurnLog.** Due costruttori reali (`run_turn` e HTTP
   `asdict(TurnLog(...))`) piu il writer dict tutor, append best-effort e
   quattro terminali non tipizzati; D-G0.6 deve censire e assegnare i contratti
   mancanti per origine, esito, action ordinal/event ID e short-circuit.
7. **Gap runtime registry.** Un kind legacy senza producer, percorsi marker legacy e
   chiamate dirette a sintesi/Birth; nessun test producer→consumer→effect che
   fallisca esattamente quando si rimuove un arco. I sei handler correnti sono
   evidenza da migrare/ritirare, non sei nuovi effect approvati.
8. **Gap runtime autorita fonti.** Gli adapter possono fornire provenienza;
   il materializzatore non inietta component ID/version/digest autorevoli e
   `upsert_intent` conserva una provenienza top-level primaria.
9. **Condizione documentale: work manifest incompleto.** Senza file concreti per metriche, snapshot,
   pattern, gap evidence, operations e test non sono congelabili DAG, nomi,
   numeri migrazione e dipendenze P0/P1/P2/F0-F4.

## 9. Percorsi e test concreti da proporre, senza implementazione

Questi sono candidati di work manifest. Non costituiscono prove gia eseguite.
I path rispettano la raccolta esistente sotto `tests/runtime/learning`,
`tests/runtime/infra`, `tests/runtime/contracts` e `tests/runtime/engine`.
Quando una suite affine esiste gia, la proposta e estenderla; un file nuovo e
indicato soltanto per un contratto realmente distinto.

| Unita | File di produzione proposto/confermato | Test puro o fixture anticipabile |
|---|---|---|
| P0.1 | nuovo `runtime/growth_snapshot.py`; lettori adattati per `runtime/agent_runtime.py`, `runtime/turn_feedback.py`, SQLite censiti | estendere `tests/runtime/infra/test_sqlite_snapshot_completa.py` con fixture SQLite/JSONL solo sotto `tmp_path`, cutoff/watermark canonico, digest uguale su due letture e fake writer/barriera; nuovo `tests/runtime/infra/test_growth_snapshot.py` solo se il contratto multi-store non puo restare leggibile nella suite esistente |
| P1.1-P1.3 | nuovo `runtime/growth_policy.py` | nuovo `tests/runtime/learning/test_growth_policy.py`: tabella dataclass/env sintetica, JSON canonico golden, alias legacy, bounds, policy version order-independent, owner/principal mutation invariant, retry dedupe |
| P2.1-P2.3 | `runtime/change_intents.py` | estendere `tests/runtime/learning/test_change_intents.py` con fixture SQL v0/storica anonimizzata, due connessioni temp, dedup/alias e matrice completa archi/CAS; evita tre suite concorrenti sullo stesso store |
| P2.4 | nuovo `runtime/change_canonical.py` | nuovo `tests/runtime/contracts/test_change_canonical.py`: vettori golden del nuovo codec **solo** per i tre kind approvati `create_executor`, `extend_executor`, `promote_plan`, Unicode/delimitatori/qualifier e digest domain-separated; per tutti i sei kind correnti aggiungere fixture legacy di sola lettura, migrazione o ritiro, senza derivarne sei nuovi effect |
| P2.5, P2.7 | nuovo `runtime/change_operations.py` | nuovo `tests/runtime/learning/test_change_operations.py`: clock/fault injector/fake effect in-memory, due worker, lease/crash a ogni confine, `waiting_dependency`, observe/commit/reconcile con stesso operation ID |
| P2.6 | nuovo `runtime/one_shot_tokens.py` | nuovo `tests/runtime/learning/test_one_shot_tokens.py`: clock deterministico, due connessioni temp, replay/forward/expiry/revoca/crash; nessun secret reale |
| P2.8 | API epoch in `runtime/change_intents.py` | estendere `tests/runtime/learning/test_change_intents.py`: event/operation ID duplicato incrementa una volta, policy-version change invalida firma |
| P2.9 | nuovo `runtime/user_data_lifecycle.py`; integrazione `runtime/users.py` | nuovo `tests/runtime/infra/test_user_data_lifecycle.py`: registry esaustivo e fixture isolate SQLite/JSONL/RAM/filesystem; riusare le fixture lifecycle delle suite infra esistenti, fake delete-pepper, HMAC vector, tombstone distinto, delayed/replay denied; nessun path reale |
| P2.10 | nuovo `runtime/rejection_rules.py` | nuovo `tests/runtime/learning/test_rejection_rules.py`: fingerprint exact binding, expiry/revoca, unknown schema fail-closed, assert nessun effect handler |
| F0.1 | `runtime/agent_runtime.py`, `runtime/http_routes_agent.py`, `runtime/tutor/telemetry.py` | estendere `tests/runtime/infra/test_log_lifecycle.py`: quattro terminali correnti come caratterizzazione, HTTP `asdict(TurnLog(...))`, tutor dict writer, error/resume e sink in memoria; il contratto futuro verifica origine/esito mai unknown |
| F0.2 | `runtime/http_auth.py`, `runtime/users.py`, ingressi dal work manifest | nuovo `tests/runtime/contracts/test_authenticated_origin.py`: host/guest/LAN/test/forwarded e payload spoof; principal deriva soltanto da fixture auth di confine |
| F0.3 | nuovo `runtime/growth_metrics.py` e viste confermate dopo freeze | nuovo `tests/runtime/learning/test_growth_metrics.py`: fixture event canonici real/test, denominatori nulli, errori, indicatori 5/7 e zero divisione |
| F1.1-F1.3 | `runtime/change_intent_adapters/__init__.py`, registry/fixture, `runtime/change_intents.py` | estendere `tests/runtime/learning/test_change_intent_adapters.py`, `test_change_applier.py`, `test_change_observer.py` e `test_change_rollback_publication.py`: caratterizzare i sei archi legacy per migrazione/ritiro; provare soltanto gli effect approvati create/extend/promote; rimozione di un arco fallisce un caso; cleanup in `test_change_intents.py` |
| F2.1-F2.2 | nuovo `runtime/change_evaluations.py` | nuovo `tests/runtime/learning/test_change_evaluations.py`: append-only/revisioni concorrenti/latest projection, metriche multiple, retention 180 giorni e watermark con clock finto |
| F2.3 | `runtime/alignment_engine.py` | estendere `tests/runtime/infra/test_alignment_engine.py`: fake judge per ok/not_applicable/timeout/JSON invalido, `JudgeFailureCode.INVALID`, retry/backoff senza rete |
| F2.4 | codec P2.4 + migrazione privacy in file da congelare | nuovo `tests/runtime/contracts/test_growth_privacy_migration.py`: corpus sintetico PII/secret/two-owner, scansione di DB/temp log/digest, quarantine vs redact golden |
| F3.1-F3.3 | nuovo `runtime/gap_evidence.py`; `runtime/engine/dispatch.py`, `runtime/engine/terminator.py`, `runtime/prefilter.py`, nuovo reconcile job | nuovo `tests/runtime/engine/test_gap_evidence.py` per event ID/turn+ordinal/revisioni/reconcile; estendere `tests/runtime/engine/test_terminator_operational_blame.py` per il producer; nuovo `tests/runtime/engine/test_implements_intent.py` per la matrice verbo-oggetto-generazione |
| F4.0 | `runtime/change_intent_adapters/__init__.py`, schema fonti in `runtime/change_intents.py` | estendere `tests/runtime/learning/test_change_intent_adapters.py`: adapter spoof component/owner ignorato o respinto, materializzatore inietta manifest fixture, due fonti concorrenti conservate |
| F4.1 | nuovo `runtime/change_intent_adapters/gap.py` | nuovo `tests/runtime/learning/test_gap_adapter.py`: tre eventi canonici→un intent, retry/test/rejected/catalog-covered non contano |
| F4.2a-F4.2c | nuovo `runtime/usage_patterns.py`, nuovo `runtime/change_intent_adapters/usage_patterns.py`, modifica `runtime/change_intent_adapters/telos.py` | nuovo `tests/runtime/learning/test_usage_patterns.py`: projector watermark/replay, PII corpus, delete provenance invariance, aggregazione multiprocess simulata e mapping catalog deterministic create/extend |
| F4.3a-F4.3d | nuovo `runtime/plan_templates.py`, `runtime/engine/autopath.py`, nuovo `runtime/change_intent_adapters/optimization.py` | nuovo `tests/runtime/engine/test_plan_templates.py`; estendere `tests/runtime/engine/test_autopath_raw_plan.py`, `test_autopath_lifecycle.py` e `test_served_plan_reresolution.py` per shadow/legacy_committed/replay read-only/apply-observe-rollback; nessun executor/undo/rete nel probe |
| F4.4 | materializer/job da congelare dopo registry | nuovo `tests/runtime/learning/test_growth_adapter_transactions.py`: commit intent+source prima di fake judge, timeout/crash/rerun, nessun lock attraversa la chiamata, S1/S2/S3 non duplicati |

Le fixture P1, P2.3-P2.8, F0.3, F1.1, F2, F3.2 e gran parte di F4 possono
essere definite subito come dati puri/in-memory, ma i loro import e nomi finali
devono attendere D-G0.6. P0.1 e P2.9 possono anticipare harness `tmp_path` e
adapter finti; l'elenco dei backend e i path reali devono essere rigenerati sul
commit RM-0008 finale.

## 10. Ricontrolli obbligatori sul commit RM-0008 finale

Prima di considerare D-G0.5 completabile:

1. registrare il commit finale RM-0008 e un worktree pulito per i file censiti,
   congelare il contratto machine-readable, il lettore e il verifier di
   `EXT-RM0008-F5`, quindi ripetere scansioni/hash della §2. Non attendere una
   prova F5 reale per G0.5/G0.6/I1.1: la sua disponibilita e gate di esercizio
   D2;
2. rieseguire diff strutturale per `runtime/agent_runtime.py`,
   `runtime/config.py`, `runtime/users.py`,
   `runtime/durable_workloads/migrations.py`,
   `runtime/durable_workloads/storage.py`, `runtime/recurring_tasks.py`,
   `runtime/dialog_pending.py`, `runtime/user_notices.py` e
   `runtime/change_intent_adapters/telos.py`;
3. ricalcolare costruttori/writer/append diretti e terminali TurnLog;
4. ricostruire il call graph `users.delete_user`→purger e confrontarlo con ogni
   riga delle §§4.1-4.5, includendo store nuovi e RAM;
5. ricontrollare versioni durable/source-authority/Birth e ogni nuovo
   `PRAGMA user_version`/ledger;
6. ricostruire registry producer→consumer→effect, includendo marker e chiamate
   dirette a `handle_synth_request`/Birth;
7. trasformare ogni esclusione in allowlist motivata e provata, non in assunto;
8. congelare solo allora nomi, DAG, file, numeri migrazione e work manifest in
   D-G0.6.

Conclusione: la fotografia individua lacune bloccanti concrete e offre comandi
ripetibili, ma la mobilita della baseline e i lifecycle owner mancanti vietano
di dichiarare D-G0.5 completo o esaustivo.
