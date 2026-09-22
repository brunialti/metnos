# RM-0009 — inventario sicurezza consolidato

Fonte immutabile: `1c308922839f7659a3cf54d988f995bba0f215d6`. L’inventario è stato calcolato due volte sugli stessi 2407 blob tracciati selezionati; path, hash e strutture parse sono identici (`e89377f4977c046b4f93df04b9257371c17e7c7fe1620f8295912bbef6c2d412`). Il working tree, le copie installate e ogni dato runtime reale sono esclusi.

## Esito

- FS-A: 109 manifest TOML validi e 400 casi totali. 33 manifest contengono campi legacy in 113 casi: setup 91, teardown 90, env 35. I 24 fixture strutturati esterni candidati hanno zero occorrenze legacy e zero errori di parse.
- FS-B: 13 famiglie consumer disgiunte e 6 famiglie di radici protette/autorità adiacenti. L’elenco localizza il lavoro; non prova ancora least privilege, revoca o assenza di bypass.
- F6: 10 percorsi di chiamata. Non esiste un’autorità RM9 al commit; built-in, code diretti alla coda remota, riprese e workload durable richiedono una decisione esplicita per evitare bypass o doppio conteggio.
- Stato: inventario soltanto. FS-A, FS-B e G0.5 non sono certificati.

## FS-A — gruppi D-FS-A.3

| ID | Manifest | Casi legacy | Campi | Semantica da preservare |
|---|---|---:|---|---|
| D-FS-A.3.001 | `executors/compress_files/manifest.toml` | 1, 5, 6, 7, 8 | setup:5, teardown:5 | Alberi sintetici, selezioni multiple, collisioni e archivi creati/rimossi attorno al caso. |
| D-FS-A.3.002 | `executors/compute_files_loc/manifest.toml` | 1, 2, 3, 4, 5, 6 | env:1, setup:6, teardown:6 | Albero sorgente e artefatti binari sintetici; selezione worker controllata da METNOS_EXECUTOR_ASSIGNED_WORKERS. |
| D-FS-A.3.003 | `executors/consult_frontier/manifest.toml` | 4 | setup:1 | Caso isolato con setup no-op esplicito; non richiede dati persistenti. |
| D-FS-A.3.004 | `executors/create_dirs/manifest.toml` | 1, 2, 4, 5 | setup:4, teardown:4 | Alberi temporanei, directory preesistenti e collisioni di nome. |
| D-FS-A.3.005 | `executors/create_files_spreadsheet/manifest.toml` | 1, 2, 3, 5, 6 | setup:5, teardown:5 | Ciclo di vita di workbook e directory sintetiche, incluse collisioni. |
| D-FS-A.3.006 | `executors/create_images_indices/manifest.toml` | 2 | env:1, setup:1, teardown:1 | Indice immagini temporaneo e worker assegnato tramite METNOS_EXECUTOR_ASSIGNED_WORKERS. |
| D-FS-A.3.007 | `executors/delete_dirs/manifest.toml` | 1, 3, 4, 5, 7 | setup:5, teardown:5 | Alberi temporanei, directory annidate e condizioni di link/collisione. |
| D-FS-A.3.008 | `executors/delete_files/manifest.toml` | 1, 3, 6 | setup:3, teardown:3 | File sintetici, directory contenitrici e condizioni di symlink. |
| D-FS-A.3.009 | `executors/find_contacts/manifest.toml` | 1, 2, 3, 4 | env:4 | Provider contatti fittizio selezionato con METNOS_SUBPROCESS_FAKE. |
| D-FS-A.3.010 | `executors/find_dirs/manifest.toml` | 1, 2, 3, 4 | env:1, setup:4, teardown:4 | Alberi temporanei con visibilità/case controllati; worker assegnato nel primo caso. |
| D-FS-A.3.011 | `executors/find_files/manifest.toml` | 1, 2, 3, 4, 5, 6, 7, 10 | env:1, setup:8, teardown:8 | Alberi temporanei, file nascosti, maiuscole/minuscole e limiti; worker assegnato nel primo caso. |
| D-FS-A.3.012 | `executors/find_files_hash/manifest.toml` | 1, 2, 3, 4 | env:1, setup:4, teardown:4 | File duplicati e cache hash temporanea; worker/cache controllati da ambiente. |
| D-FS-A.3.013 | `executors/find_images_google_photos/manifest.toml` | 1, 2 | env:2 | Skill home sintetica senza credenziali Google reali. |
| D-FS-A.3.014 | `executors/get_approval/manifest.toml` | 4 | env:1, setup:1, teardown:1 | Owner e archivio dati temporanei per la richiesta di approvazione. |
| D-FS-A.3.015 | `executors/get_files/manifest.toml` | 1 | setup:1, teardown:1 | File sintetico con metadati e timestamp controllati. |
| D-FS-A.3.016 | `executors/get_images_google_photos/manifest.toml` | 2 | env:1 | Skill home sintetica senza credenziali Google reali. |
| D-FS-A.3.017 | `executors/get_inputs/manifest.toml` | 1, 2, 3, 4, 5, 6 | env:6, setup:2, teardown:2 | Owner, archivio dati e stato dialogo temporanei. |
| D-FS-A.3.018 | `executors/get_location/manifest.toml` | 1, 3, 4 | env:3, setup:3, teardown:3 | Radici config/dati temporanee, owner e rete/geolocalizzazione disabilitate. |
| D-FS-A.3.019 | `executors/get_places/manifest.toml` | 7 | env:1 | Provider geografici disabilitati tramite METNOS_GEO_PROVIDERS. |
| D-FS-A.3.020 | `executors/get_proposals/manifest.toml` | 1 | env:1, setup:1, teardown:1 | Radici dati e stato temporanee. |
| D-FS-A.3.021 | `executors/list_dirs/manifest.toml` | 1, 2, 4 | env:1, setup:3, teardown:3 | Alberi temporanei e worker assegnato nel primo caso. |
| D-FS-A.3.022 | `executors/move_files/manifest.toml` | 1, 3, 4, 5, 6, 8, 11, 12, 13 | setup:9, teardown:9 | File e directory sintetici, collisioni, timestamp e spostamenti multipli. |
| D-FS-A.3.023 | `executors/read_contacts/manifest.toml` | 1, 2, 3, 4 | env:4 | Provider contatti fittizio selezionato con METNOS_SUBPROCESS_FAKE. |
| D-FS-A.3.024 | `executors/read_files/manifest.toml` | 1, 3, 4, 6, 7, 8, 9 | setup:7, teardown:7 | File sintetici testuali/binari, codifiche e troncamento. |
| D-FS-A.3.025 | `executors/read_files_csv/manifest.toml` | 1, 2, 3 | setup:3, teardown:3 | CSV sintetici e directory temporanee. |
| D-FS-A.3.026 | `executors/read_files_doc/manifest.toml` | 4 | env:1 | Skill home sintetica senza credenziali documentali reali. |
| D-FS-A.3.027 | `executors/read_files_ocr/manifest.toml` | 4, 5 | setup:2, teardown:2 | Immagini/PDF sintetici e input non valido. |
| D-FS-A.3.028 | `executors/read_files_xlsx/manifest.toml` | 1, 2 | setup:2, teardown:2 | Workbook sintetici e directory temporanee. |
| D-FS-A.3.029 | `executors/read_messages/manifest.toml` | 1, 2, 3 | env:3 | HOME e config temporanee senza casella o segreti reali. |
| D-FS-A.3.030 | `executors/undo_last_turn/manifest.toml` | 1, 2, 3 | setup:3, teardown:3 | Journal e file temporanei usati per verificare ripristino e compensazione. |
| D-FS-A.3.031 | `executors/write_files/manifest.toml` | 1, 3, 4, 5, 6, 7, 8, 9 | setup:8, teardown:8 | File e directory sintetici, sovrascrittura, append e collisioni. |
| D-FS-A.3.032 | `executors/write_files_doc/manifest.toml` | 5 | env:1 | Skill home sintetica senza credenziali documentali reali. |
| D-FS-A.3.033 | `executors/write_images_google_photos/manifest.toml` | 3 | env:1 | Skill home sintetica e percorso senza credenziali reali. |

Ogni gruppo possiede un solo manifest e i gruppi sono disgiunti. Il target è una proposta da validare, non un contratto congelato: nessun caso o asserzione viene rimosso e la prova resta nel runner Birth isolato. Un controller Python trusted può soltanto orchestrare; le operazioni di fixture devono essere dichiarative, ammesse da allowlist ed eseguite dentro il sandbox. I casi Birth continuano a esporre soltanto `name`, `input`, `expect`. I test proposti e il loro stato `existing`/`new` sono nel JSON canonico.

Punti di compatibilità da non confondere: `runtime/test_runner.py` resta un runner host eseguibile; `runtime/testing/runner.py` conserva adattatori e setup trusted di sviluppo; `runtime/smoke.py` è un harness curato separato. Invece `runtime/synth_request.py` usa già la validazione Birth isolata; `runtime/executor_birth_functional.py` conserva da `test_runner` solo il matcher `check_expect`.

## FS-B — gruppi D-FS-B.3

| ID | Famiglia concreta | File | Contratto target |
|---|---|---:|---|
| D-FS-B.3.001 | CRUD credenziali/loader | 10 | Proposta da validare, non contratto congelato: intake e CRUD terminano nel core protetto; executor e loader ricevono soltanto handle opachi scoped per invocation, mai valori o autorità più ampia. |
| D-FS-B.3.002 | mail e bollette | 6 | Proposta da validare, non contratto congelato: il core protetto esegue le operazioni mail per account e azione; executor e skill bollette passano solo handle scoped e ricevono risultati redatti. |
| D-FS-B.3.003 | login siti/browser | 9 | Proposta da validare, non contratto congelato: il core protetto effettua l’iniezione host-bound in una sessione autorizzata e revocabile; executor, wrapper e browser child vedono solo handle, mai credenziali. |
| D-FS-B.3.004 | Google/OAuth/bridge | 15 | Proposta da validare, non contratto congelato: token e refresh restano nel core protetto e sono vincolati a provider, account, scope e invocation; bridge, skill e subprocess ricevono soltanto handle e risultati. |
| D-FS-B.3.005 | skill generiche | 2 | Proposta da validare, non contratto congelato: wrapper e skill ricevono soltanto handle scoped; ogni uso del segreto avviene nel core protetto e gli errori sono redatti. |
| D-FS-B.3.006 | geografia | 1 | Proposta da validare, non contratto congelato: la chiave resta nel core protetto che compie la richiesta provider-scoped; il consumer riceve solo handle e risultato. |
| D-FS-B.3.007 | Frontier/LLM | 2 | Proposta da validare, non contratto congelato: il core protetto compie la chiamata LLM con capability provider/model scoped; executor e client non ricevono il segreto e l’audit è redatto. |
| D-FS-B.3.008 | Telegram | 3 | Proposta da validare, non contratto congelato: il token bot resta nel core protetto che esegue azioni chat/account scoped; canale, daemon e subprocess transitano solo handle e risultati. |
| D-FS-B.3.009 | CIFS/admin | 2 | Proposta da validare, non contratto congelato: il core protetto separa grant CIFS, autorità admin e comando privilegiato; helper e figli ricevono solo handle mount/host scoped. |
| D-FS-B.3.010 | raccolta e ripresa core | 4 | Proposta da validare, non contratto congelato: un’unica autorità core per invocation collega intake, consenso, ripresa e uso; stati ordinari, executor, skill e figli conservano soltanto handle opachi. |
| D-FS-B.3.011 | undo protetto | 1 | Proposta da validare, non contratto congelato: journal e compensazioni conservano soltanto handle opachi; il replay richiede ri-autorizzazione core scoped e non materializza segreti nel consumer. |
| D-FS-B.3.012 | migrazione/bootstrap | 2 | Proposta da validare, non contratto congelato: il confine core d’installazione importa una volta, verifica proprietà/permessi e rimuove copie in chiaro; runtime, executor e figli vedono solo handle. |
| D-FS-B.3.013 | target di rete | 1 | Proposta da validare, non contratto congelato: la risoluzione produce identità/endpoint e un handle separato host-bound; nessun target o consumer incorpora materiale segreto. |

Le radici protette correlate sono inventariate separatamente nel JSON: confinement client/runtime, autenticazione e controllo, signing/contract store, autorità e rilascio Birth, cataloghi firmati/cleanup, installazione e riconciliazione stack. I target sono proposte da validare, non contratti congelati: il segreto resta nel core protetto; executor, skill, wrapper, browser child e subprocess ricevono soltanto handle opachi scoped, mai materiale segreto. L’eventuale sovrapposizione con un gruppo consumer è intenzionale; i gruppi di espansione FS-B restano disgiunti tra loro.

## F6 — grafo di chiamata

| ID | Ingresso | Percorso | Rischio da chiudere |
|---|---|---|---|
| D-F6-PATH-001 | runtime.agent_runtime.invoke_executor | optional scheduler wrapper -> _invoke_executor_impl -> local or remote branch | No RM9 invocation-authority hook exists at the source commit. |
| D-F6-PATH-002 | runtime.agent_runtime.invoke_tool_by_name | built-in handler branch bypasses loader; verb-unique branch loads implementation; normal executor branch delegates to invoke_executor | Placement only in loader would miss built-ins; placement in both APIs risks duplicate accounting. |
| D-F6-PATH-003 | HTTP resume paths | approval/OAuth resume -> invoke_executor; verb-unique resume -> invoke_verb_unique directly | Must reuse original invocation authority and remain idempotent. |
| D-F6-PATH-004 | channel daemon | channel action -> invoke_executor or invoke_verb_unique directly | Requires the same hook as ordinary turns, not a channel-specific duplicate. |
| D-F6-PATH-005 | durable workloads | RunnerKind.EXECUTOR -> configured executor invoker -> invoke_executor; RunnerKind.WORKLOAD -> scheduler/internal handler | EXECUTOR already reaches the common choke point; WORKLOAD needs an explicit applicability decision. |
| D-F6-PATH-006 | remote execution transport | invoke_remote -> enqueue -> agent poll/started -> client sandbox -> completion | Authority must be established before enqueue and verified through lease/result; transport is not the only queue producer. |
| D-F6-PATH-007 | direct invocation queue producers | undo compensation or admin device test -> enqueue_invocation directly | These bypass remote_exec and need hook coverage or explicit, proved non-applicability. |
| D-F6-PATH-008 | engine and fast-path dispatch | main engine, managed starter, auto-remediation and fast path converge on built-in/tool/executor dispatch with multiple re-entry points | Exactly-once shadow accounting needs one stable invocation id across retries/remediation. |
| D-F6-PATH-009 | orchestration resume and post-gate tail | credential resume -> invoke_verb_unique directly; async resume/post-gate tail -> invoke_tool_by_name | Direct verb-unique resume bypasses invoke_tool_by_name; all resumes must reuse one authority id. |
| D-F6-PATH-010 | admin to sudoer | approved admin decision -> invoke_verb_unique("sudoer") directly | Privileged direct loader dispatch needs the common hook or an explicit, proved special authority contract. |

Choke point candidato: `invoke_executor` per executor locali/remoti. Non basta però un hook nel loader, perché i built-in lo evitano; non basta `invoke_tool_by_name`, perché HTTP, canale, orchestration e admin hanno chiamate verb-unique dirette; non basta `remote_exec`, perché undo e admin accodano direttamente. Il percorso durable executor converge già nel choke point, quindi un secondo hook durable produrrebbe doppio conteggio. Questa è una mappa statica, non prova shadow/enforcement.

## Differenze dal precedente inventario

- L’evidenza è ora ancorata esclusivamente ai blob del commit, con hash relativi al repository; non mescola checkout corrente, release installata o copie di prodotto.
- Il percorso Synth è distinto dal runner legacy: al commit usa la validazione Birth isolata; resta soltanto la dipendenza matcher `check_expect` in `executor_birth_functional.py`.
- `executors/create_images_indices/manifest.toml` usa i campi legacy nel caso 2, non nel caso 3.
- Sono esplicitati il parse dei fixture strutturati esterni, i confini smoke/trusted-development e i produttori diretti della coda che bypassano `remote_exec`.
- FS-B include ora, tra gli altri, bollette, target di rete, migrazione/bootstrap e radici Birth/signing come superfici distinte.

## Limiti

Nessun runner legacy o Birth è stato eseguito; nessun servizio, vault, chiave, database/log runtime o provider è stato letto. La copia installata citata dal report precedente non è stata verificata. Gli hash coprono soltanto lo scope dichiarato nel JSON. Il completamento di FS-A/FS-B richiede migrazioni e prove; G0.5 richiede inoltre work manifest e copertura per tutti gli ID, più verifica release/installed dove prevista.
