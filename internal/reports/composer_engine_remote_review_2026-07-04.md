# Review complessiva: composer, motore a quattro strati, executor remoti

Data: 2026-07-04  
Scopo: fornire a un agente un report operativo su codice, algoritmi e documentazione, con priorita' di intervento.

## Executive summary

Il codice attuale e' coerente per un uso domestico mono-utente e per la fase W3.3 dei remote executor: il client Windows 0.2.7 ha Job Object, lock single-instance, spool dei result, heartbeat separato, verifica firme e cache executor firmata. Il motore a quattro strati e' realmente operativo in `runtime/engine/dispatch.py`, anche se i nomi nel codice sono ancora "engine v2/v3" e non "engine 4".

Le aree piu' critiche per il prossimo agente sono:

1. Allineare `list_dirs` a Windows: oggi e' in `DEVICE_ELIGIBLE`, ma non dichiara `platforms = ["linux", "windows"]`, quindi su un PC Windows puo' essere rifiutato dal placement.
2. Rendere il remote placement manifest-driven: oggi la chat usa una whitelist hardcoded (`DEVICE_ELIGIBLE`) e i manifest non dichiarano `[placement]`.
3. Chiudere owner-filter anche nel fallback generale di `invoke_executor`, non solo nella risoluzione chat.
4. Aggiornare la documentazione interna/pubblica che parla ancora di mTLS, client 0.2.3/0.2.6, `sandbox_unavailable` pre-W3.1 e vecchio stato W1-2.
5. Introdurre GC server-side per `invocations`: lo spool client ha retention, le join session pure, ma la tabella invocazioni sembra append-only.
6. Prima di abilitare executor remoti mutanti, formalizzare l'idempotenza applicativa: il protocollo evita molti doppi run, ma non puo' garantire exactly-once se il client crasha dopo il side-effect e prima dello spool.

Le verifiche locali eseguite sono positive:

```text
python3 -m pytest runtime/tests/test_target_device.py runtime/tests/test_placement.py runtime/tests/test_agent_server_remote.py runtime/tests/test_engine_v2.py runtime/tests/test_planner_routing_composition.py -q
99 passed in 2.11s

cargo test --manifest-path client-rs/Cargo.toml
5 passed
```

## Mappa reale del sistema

### Motore a quattro strati

Il documento pubblico `praxis_engine.html` descrive quattro strati: L0 fastpath, L1 autopath, L2 validator, L3 engine. Nel codice l'entry point reale e':

- `runtime/agent_runtime.py::run_turn`
- `runtime/agent_runtime.py::_try_engine_v2`
- `runtime/engine/dispatch.py::run_turn`
- `runtime/engine/executor.py::Executor.run`

La cascata effettiva e':

1. Shortcut pre-engine in `agent_runtime`: admin commands, escalation UI, fast path deterministici, scheduling deterministico.
2. Bridge `_try_engine_v2`: intent extraction, disambiguazione routing, seed per upload/resume, passaggio `placement_target`.
3. `engine.dispatch.run_turn`: L0 fastpath, L1 autopath, L3 proposer/executor/recovery/terminator; L2 validator e output policy sono feature-gated.
4. `engine.executor.Executor.run`: risoluzione `from_step`, steprefs, fillers, runtime placeholders, resolver di backend/scope/account/calendar, guardia Vaglio pre-invoke, invocazione.

Nota terminologica: "Engine 4" nel linguaggio di progetto corrisponde al motore a quattro strati; il codice lo chiama ancora "engine v2", con vari gate/estensioni v3.

### Composer

Ci sono tre significati diversi di "composer":

1. `prompt_loader.compose`: compone prompt planner da `_core` + sezioni + footer. E' stabile, testato, e non e' il composer semantico.
2. `runtime/synt.py::Composer`: BFS sul mnestoma per trovare catene di executor esistenti. E' il composer "vero" del sottosistema synt.
3. Composer UI: area input chat in `runtime/templates/chat.html`, non rilevante per gli algoritmi.

Il `Composer` di synt e' semplice e sano: fa `mnestoma.walk(start_executor, max_depth=5, state_filter=("active",))` e ritorna il primo path il cui `dst` soddisfa `target_pred`. Non usa LLM. Il limite e' che oggi synt/compose non e' remote-aware: non ragiona su placement, piattaforma, disponibilita' device o sandbox remoto. La documentazione `synt.html` segnala la domanda come aperta e il codice conferma il default conservativo server-side.

### Executor remoti

Pipeline reale:

1. Chat target: `runtime/target_device.py::resolve_target`
2. Sticky target per sender: `runtime/chat_target_store.py`
3. Bridge: `agent_runtime.run_turn` risolve `_placement_target`
4. Invoke: `agent_runtime.invoke_executor(..., target_device=...)`
5. Placement: `runtime/placement.py::choose_placement`
6. Queue firmata: `runtime/remote_exec.py::invoke_remote`
7. DB invocazioni: `runtime/invocations.py`
8. HTTP agent server: `runtime/agent_server.py`
9. Client Rust: `client-rs/src/runner.rs`, `sandbox_linux.rs`, `sandbox_windows.rs`

Il risultato remoto viene fuso come result locale; il marker `_ran_on_device` viene usato solo dopo esecuzione reale per taggare `log.target_device`.

## Findings codice

### F1 - `list_dirs` e' device-eligible ma non Windows-platform

Severita': alta per W3 Windows.

`runtime/target_device.py` abilita `list_dirs` in `DEVICE_ELIGIBLE`, ma `executors/list_dirs/manifest.toml` non dichiara `platforms`. Il loader assegna default `["linux"]`; `placement.choose_placement` rifiuta un device Windows.

File:

- `runtime/target_device.py`: `DEVICE_ELIGIBLE = {"get_files", "compute_files_loc", "list_dirs"}`
- `runtime/loader.py`: default piattaforme `["linux"]`
- `runtime/placement.py`: `_check_platform`
- `executors/list_dirs/manifest.toml`: manca `platforms`

Impatto: un utente puo' chiedere "elenca questa cartella sul PC Windows" e ottenere errore di piattaforma, nonostante il resolver lo consideri eleggibile.

Fix consigliato:

- verificare che `list_dirs.py` sia davvero stdlib/cross-platform;
- aggiungere `platforms = ["linux", "windows"]`;
- aggiungere test placement o test target remoto dedicato a `list_dirs` su Windows mock.

### F2 - Remote eligibility hardcoded, non manifest-driven

Severita': alta architetturale.

La documentazione dice che il manifest decide se un executor puo' uscire dal server (`[placement]`, `platforms`, capabilities). Il codice chat-driven usa invece `target_device.DEVICE_ELIGIBLE`, lista hardcoded. Inoltre i tre executor ammessi non dichiarano `[placement]`.

Impatto:

- ogni nuovo executor remoto richiede modifica codice centrale;
- il catalogo non e' single source of truth;
- synt/importer/admission non possono ragionare correttamente sul placement;
- i documenti danno un modello piu' pulito di quello implementato.

Fix consigliato:

- introdurre nel manifest un campo esplicito, ad esempio:

```toml
[placement]
scope = "any"
device_ok = true
targets = ["filesystem"]
class = "io_fs"
```

- oppure usare `scope = "device"` solo per device-only e `device_ok = true` per "puo' girare sul device se la chat lo chiede";
- sostituire `DEVICE_ELIGIBLE` con predicato derivato da loader/catalog;
- mantenere una compat shim temporanea per gli attuali tre executor.

### F3 - Owner filtering incompleto nel fallback generale di `invoke_executor`

Severita': media ora, alta appena arrivano guest o `scope="device"`.

`run_turn` filtra i device per `owner_user_id == actor` prima di risolvere il target chat. Pero' `invoke_executor`, quando entra nel placement, chiama di nuovo `devices.list_devices()` non filtrata.

Oggi l'impatto e' limitato perche':

- il target chat passa un nome gia' filtrato;
- non risultano manifest attivi con `[placement] scope="device"`;
- l'uso reale e' mono-utente.

Ma appena un executor `scope=device` viene eseguito senza target esplicito, il placement vede tutti i device.

Fix consigliato:

- passare `owner_user_id`/`actor` a `choose_placement`;
- oppure filtrare in `invoke_executor`:

```python
_who = actor or "host"
_devices_for_actor = [
    d for d in _devices.list_devices()
    if (getattr(d, "owner_user_id", "host") or "host") == _who
]
```

- aggiornare i test con due owner e due device.

### F4 - Token/join session admin non propagano owner

Severita': media.

`devices.generate_token` e `devices.create_join_session` supportano `owner_user_id`, ma gli endpoint admin usati dalla UI chiamano entrambi senza owner esplicito. Il default e' `host`.

File:

- `runtime/http_routes_admin.py::admin_devices_token`
- endpoint join session vicino a `create_join_session(...)`
- `runtime/devices.py::generate_token`
- `runtime/devices.py::create_join_session`

Impatto: la UI attuale e' host-centric. Va bene in mono-utente, ma non basta per guest.

Fix consigliato:

- se la UI e' autenticata come host e crea device per host, documentarlo;
- se si vuole creare device per guest, aggiungere owner nel body e validare permessi;
- mostrare owner nella console dispositivi e impedire collisioni di nome per owner.

### F5 - `invocations` non ha purge server-side

Severita': media resource-safety.

Il client ha GC dello spool (`METNOS_SPOOL_RETENTION_DAYS`). Le join session hanno purge nel `state_reaper`. La tabella `invocations` non sembra avere un purge equivalente.

File:

- `runtime/invocations.py`
- `runtime/jobs/maintenance_tasks.py::task_state_reaper`

Impatto: crescita append-only di payload/result JSON. In casa non esplode subito, ma e' incoerente con il resto del design.

Fix consigliato:

- aggiungere `purge_invocations(older_than_days=...)`;
- default 30 o 90 giorni;
- conservare almeno metadati aggregati se servono audit/statistiche;
- agganciarlo a `state_reaper`.

### F6 - Exactly-once non garantito per futuri executor mutanti remoti

Severita': alta prima di abilitare mutazioni remote.

Il protocollo e' robusto ma non puo' garantire exactly-once in ogni crash window:

- il server evita doppio claim normale;
- il client ha lock single-instance;
- il client mantiene `executed` in memoria;
- il client scrive result nello spool prima di consegnarlo;
- il server accetta il primo result in modo idempotente.

Finestra residua: executor mutante completa il side-effect, poi il client crasha prima di scrivere lo spool. Il server puo' redeliverare dopo deadline+grace e il side-effect puo' ripetersi.

Oggi il rischio e' basso perche' la whitelist remota contiene read-only/file-inspection. Prima di `write_files`, `move_files`, `delete_files`, `send_*` remoti serve una strategia.

Fix consigliato:

- per mutanti remoti, richiedere idempotency key `invocation_id` dentro executor;
- oppure marcare `started` durable prima dell'invoke e bloccare redelivery mutante senza result;
- oppure abilitare mutanti solo con reverse pattern + audit + conferma utente + idempotenza applicativa.

### F7 - Linux remote sandbox puo' degradare a `sandbox:"none"`

Severita': media.

`client-rs/src/sandbox_linux.rs` usa bwrap se disponibile; se manca o `METNOS_SANDBOX` e' off, esegue diretto e logga. Questa e' coerente con la doc locale storica, ma meno forte del claim "executor remoti sempre nel sandbox piu' forte".

Impatto: un device Linux senza bwrap esegue executor remoti nudi. Il result dichiara `sandbox:"none"`, quindi non e' silenzioso.

Fix consigliato:

- decidere policy: per remote executor accettare `none` solo read-only, o fail-closed per tutti;
- documentare in remote_executors e sandbox che Linux remoto ha fallback;
- valutare enforcement server-side: rifiutare executor mutanti se ultimo sandbox label del device e' `none`.

### F8 - `target_device` e fast path: esplicito device su executor non impacchettabile resta locale

Severita': bassa/media UX.

Il commento in `invoke_executor` dice che un target device non impacchettabile, ad esempio `get_now`, gira locale e non fallisce. Va bene per sticky implicito. E' meno chiaro per una richiesta esplicita "sul PC X".

Impatto: l'utente puo' credere che una domanda esplicitamente destinata al PC sia stata eseguita li', ma il sistema non tagga il device. Non e' falso nel log, ma e' potenzialmente sorprendente.

Fix consigliato:

- distinguere target esplicito vs sticky implicito fino a `invoke_executor`;
- per target esplicito + executor non remoto: messaggio onesto o nota "questa operazione gira sul server";
- per sticky implicito: mantenere fallback locale.

### F9 - Engine/dispatch molto efficace ma fragile per accumulo di guard

Severita': media manutentiva.

`runtime/engine/dispatch.py` contiene molte correzioni deterministiche accumulate: decontaminazione clausole, fix verbi, enforce missing objects, ordering, consent gate, guard get_inputs, cache safety, recovery. Questa e' una forza del sistema, ma il file e' diventato una sequenza lunga di trasformazioni con ordine semantico implicito.

Rischio:

- un nuovo agente puo' inserire una guard nel punto sbagliato;
- l'interazione fra cache L0/L1 e nuovi guard puo' creare regressioni silenziose;
- il nome "engine v2" non comunica lo stato reale.

Fix consigliato:

- estrarre una pipeline esplicita di normalizzatori:
  - `pre_pool_intent_guards`
  - `post_propose_structure_guards`
  - `post_cache_guards`
  - `pre_execute_policy_guards`
- rendere l'ordine testabile con snapshot di framework;
- aggiornare nomenclatura doc: "motore a quattro strati" vs "engine v2/v3".

### F10 - Synt Composer non considera placement/remoto

Severita': bassa ora, media se synt genera catene operative su device.

`runtime/synt.py::Composer` cerca catene nel mnestoma solo per nomi/capability. Non valuta:

- piattaforma device;
- `[placement]`;
- disponibilita' heartbeat;
- sandbox label;
- owner/user.

La documentazione `synt.html` dice che il tema remoto e' aperto e il default e' conservativo server-side. Il codice conferma.

Fix consigliato:

- per ora documentare che synt compose e' server-side;
- quando si abilita remote synt, passare un `placement_context` e filtrare catene incompatibili.

## Conferme positive

### Remote executor

- Verifica server_sig lato client prima dell'esecuzione.
- Verifica device_sig lato server sui bytes grezzi del body.
- Cache executor content-addressed: manifest sha + code sha.
- Shim firmato.
- Job Object Windows con `CREATE_SUSPENDED`, assign prima di resume, kill-on-close.
- Timeout Windows con `TerminateJobObject`.
- Timeout Linux con process group + `kill_on_drop`.
- Lock single-instance cross-platform con `fs2`.
- Heartbeat separato dal loop di poll/execution.
- Spool result prima della consegna.
- GC spool client.
- Join flow UI ha controllo "UI sul server: niente installazione qui" salvo `for_other_pc=true`.

### Engine

- L0/L1 non sono replay cieco: ci sono guard per mutanti, query-specific args, tool mancanti, ordering clause, stale cache.
- Seed upload/resume salta L0/L1, scelta corretta per contesto specifico.
- Legacy planner sembra rimosso dal path vivo: il fondo di `run_turn` termina con errore onesto.
- `_apply_device_tag` tagga solo se `_ran_on_device` e' presente nel result reale.

### Target device

Rispetto a una review precedente risultano corretti:

- sticky offline decade al server, non blocca query normali;
- nomi device duplicati diventano `ambiguous`;
- owner-filter esiste nella risoluzione chat;
- target passato anche al ramo upload;
- fallback resolver fail-closed se c'e' riferimento esplicito.

## Incongruenze documentali

### D1 - `client-rs/README.md` e' obsoleto

Dice:

- mTLS verso `.33`;
- layout con `src/transport.rs`, che non esiste;
- "W1-2 MVP Linux in corso";
- "Windows cross arriva alla W3".

Codice reale:

- client 0.2.7;
- HTTP firmato con Ed25519 device/server;
- moduli reali: `runner.rs`, `wire.rs`, `sandbox_windows.rs`, `proclock.rs`, `pyenv.rs`;
- Windows W3.3 validato.

Fix: aggiornare README prima di darlo a un agente esterno.

### D2 - `internal/design/remote-executors.html` contiene strati storici non separati abbastanza

Il documento include note aggiornate, ma anche sezioni che parlano ancora di:

- mTLS come gia' parte del flusso;
- token per-invocazione via mTLS;
- gate `sandbox_unavailable` pre-W3.1;
- client 0.2.2/0.2.3/0.2.6 come stato corrente in punti diversi;
- W3.0 client-half come DoD, poi superseded;
- W3.3 chiuso con client 0.2.6, mentre il codice e le docs pubbliche indicano 0.2.7.

Fix: aggiungere una sezione iniziale "Stato corrente normativo" e marcare il resto come storico, oppure spostare §16.1 storico in appendice.

### D3 - Pubblico `remote_executors.html` e `sandbox.html` sono piu' aggiornati dell'interno su alcuni punti

Le docs pubbliche IT/EN indicano correttamente:

- W3.3 validata il 2026-07-03;
- client 0.2.7;
- Job Object Windows;
- AppContainer futuro;
- heartbeat separato dal client 0.2.7.

Il design interno invece mescola piu' stati. Per un agente conviene dichiarare come fonte normativa: codice + docs pubbliche aggiornate + appendice storica interna.

### D4 - Documentazione manifest placement piu' avanzata del codice

Docs remote/executor dicono che il manifest decide il remote placement. Il codice usa:

- `platforms` da manifest;
- `[placement]` solo se presente;
- whitelist chat hardcoded per device-ok.

Manca quindi il campo manifest che sostituisce `DEVICE_ELIGIBLE`.

### D5 - Documentazione mTLS/TLS da chiarire

Stato reale:

- transport MVP: HTTP sulla LAN/overlay;
- autenticazione messaggi: Ed25519 device/server;
- pin server pubkey nel client;
- TLS/mTLS: futuro W6 o overlay.

Ogni documento che dice "dopo register l'auth e' mTLS" e' oggi fuorviante.

### D6 - `chat_driven_placement_R1_assessment.md` e' storico

Il report R1 segnala problemi che ora risultano chiusi:

- sticky offline che blocca query normali;
- owner filter mancante nella risoluzione chat;
- target non propagato agli upload;
- duplicati nome device.

Va marcato come assessment storico o aggiornato con stato "fixed".

## Task consigliati per agente

### P0 - Fix immediati

1. Aggiungere `platforms = ["linux", "windows"]` a `executors/list_dirs/manifest.toml` se il codice e' cross-platform.
2. Aggiungere test per `list_dirs` remoto su device Windows mock.
3. Aggiornare `client-rs/README.md`.
4. Aggiungere owner filter in `invoke_executor` prima di `choose_placement`.

### P1 - Pulizia architetturale remote

1. Sostituire `DEVICE_ELIGIBLE` con manifest-driven `device_ok`.
2. Aggiungere `[placement]` agli executor remoti ammessi.
3. Aggiungere test: "executor non device_ok + target esplicito" deve avere comportamento deciso e documentato.
4. Aggiungere purge `invocations` nel `state_reaper`.

### P2 - Documentazione

1. Rendere `internal/design/remote-executors.html` normativo in testa e storico in appendice.
2. Allineare tutte le versioni client a 0.2.7 dove si parla dello stato corrente.
3. Sostituire claim mTLS corrente con "HTTP firmato; TLS/mTLS W6".
4. Aggiornare R1 assessment come storico.

### P3 - Prima dei mutanti remoti

1. Definire policy exactly-once/idempotency per executor mutanti.
2. Usare `invocation_id` dentro executor mutanti remoti.
3. Bloccare mutanti su Linux remoto `sandbox:"none"` e Windows `job-object` se la policy richiede isolamento FS.
4. Aggiungere audit UI: ultimo sandbox label, ultimo errore, versione client, last result.

### P4 - Motore/composer

1. Rinominare o documentare "engine v2/v3" rispetto al motore a quattro strati.
2. Estrarre le guard di `dispatch.py` in moduli/pipeline ordinata.
3. Aggiungere snapshot test per ordine delle trasformazioni framework.
4. Documentare che synt Composer e' server-side e non remote-aware.

## Test da rieseguire dopo i fix

Minimo:

```text
python3 -m pytest runtime/tests/test_target_device.py runtime/tests/test_placement.py runtime/tests/test_agent_server_remote.py runtime/tests/test_invocations.py -q
cargo test --manifest-path client-rs/Cargo.toml
```

Se si tocca engine/dispatch:

```text
python3 -m pytest runtime/tests/test_engine_v2.py runtime/tests/test_planner_routing_composition.py runtime/tests/test_resume_planner.py runtime/tests/test_engine_seed_uploads.py -q
```

Se si tocca documentazione pubblica:

```text
rg -n "mTLS|0\\.2\\.[0-6]|sandbox_unavailable|W1-2 MVP|transport\\.rs" docs internal client-rs
```

## Note finali per agente

Non partire da un refactor largo. Il sistema funziona; i rischi principali sono di coerenza tra whitelist/manifest, multiutente futuro, lifecycle DB e documentazione. Il primo intervento utile e a basso rischio e' correggere `list_dirs` + README + owner filter in `invoke_executor`, poi rendere `device_ok` manifest-driven.
