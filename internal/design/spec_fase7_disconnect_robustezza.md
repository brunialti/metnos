# SPEC IMPLEMENTATIVA — Fase 7 / topic 2+: disconnect-proof, robustezza, multi-OS

> **Destinatario**: LLM esecutore (Opus). File:riga verificati il 7/7/2026. Tre assi INDIPENDENTI: (A) esecuzione differita su device offline, (B) robustezza del canale, (C) client macOS. Ogni asse spedibile da solo; dentro ogni asse, incrementi ordinati.
> **Autore analisi**: Fable, 7/7/2026.
> **VINCOLO SUPREMO**: la coda firmata/idempotente (`runtime/invocations.py`) e il flusso undo device (ADR 0183) NON si ridisegnano. §2.8 ovunque: mai un «fatto» non vero, mai un timeout che nasconde un'op avvenuta.

---

## 0. Stato attuale (fatti verificati)

- **Coda** `runtime/invocations.py`: stati `queued → delivered → done|failed` (`:51`); ri-consegna di `delivered` stantii (`:285`, epoch wall-clock fix 2/7); dedup per `invocation_id` (idempotente); reaper `done/failed` vecchi (`:255-263`). Firma Ed25519 bidirezionale.
- **Attesa SINCRONA**: `remote_exec.invoke_remote` (`:68-103`) → `invocations.wait_result` = polling 0.25s (`:426-441`) fino a `timeout_s + WAIT_MARGIN_S`; scaduto → `ERR_DEVICE_TIMEOUT`, `error_class="remote_timeout"`. **Il thread del turno resta bloccato per tutta l'attesa.**
- **Device offline**: `placement.is_available` = heartbeat fresco < `HEARTBEAT_FRESH_S` (`placement.py:50-58`); `target_device.resolve_target` → status `unreachable` → il turno MUORE SUBITO con errore onesto (`agent_runtime.py:6046-6058`). L'intento dell'utente è perso.
- **Client crash-safe** (2/7): result persistito nello spool PRIMA della consegna, `flush_pending` ri-consegna senza ri-eseguire; `executed` set ricaricato allo startup.
- **Noti-non-fixati dichiarati** (2/7): `executed` set cresce illimitato per-processo; ceiling teorico del pool-thread se molti `wait_result` concorrenti.
- **Undo device**: log al choke-point con campo `device` (ADR 0183); `_reverse_on_device` attesa bounded `METNOS_UNDO_DEVICE_TIMEOUT_S` (25s); actor-isolation dal 7/7 (`02c48df`).

## A. Disconnect-proof: esecuzione DIFFERITA (l'asse di valore)

### A.0 Il buco più grave è un altro: il RISULTATO TARDIVO (fix PRIMA del differito)
Scenario misurabile oggi: device lento/offline-transitorio → `invoke_remote` scade → il turno riporta «timeout» → **ma l'invocazione resta `queued/delivered` e il device la esegue APPENA torna** → result salvato in `done` nel DB e MAI mostrato; per un MUTANTE l'utente crede «non fatto» mentre È fatto; il log undo resta `pending` senza `done` (perché `_undo_done` scatta solo su obs ok — `agent_runtime.py:3283`) → l'op non è nemmeno annullabile. Violazione §2.8 concreta.
**Fix A.0 (prerequisito, piccolo)**:
1. Su timeout, `invoke_remote` marca l'invocazione `abandoned_by_turn=1` (colonna additiva) e il messaggio d'errore dice la verità: «non ho ricevuto conferma entro Xs; se il PC la completa ti avviso».
2. Al `submit_result` (`invocations.py:365-398`) di un'invocazione `abandoned_by_turn`: (a) chiudere il record undo (riusare `_undo_done` con l'obs reale — import da agent_runtime o spostare l'helper in `undo.py`); (b) **notificare l'utente** sul canale d'origine (vedi A.2). Test: unit su submit tardivo + undo log chiuso; e2e con device che consegna dopo il timeout.
**Done A.0**: nessun result tardivo silenzioso; undo funziona anche per op confermate in ritardo.

### A.1 Differito esplicito («quando il PC torna online»)
- **Consenso, non magia** (§2.11): device offline + turno che lo bersaglia → invece dell'errore secco, il turno risponde con la DOMANDA: «PC-X non è raggiungibile: vuoi che esegua appena torna online?» (riusare il flusso `needs_inputs`/`get_inputs` esistente; canale web = form, telegram = bottoni). Sì → enqueue con `deferred=1` + TTL (`deferred_expires_at`, default 24h, env `METNOS_DEFER_TTL_H`); il turno CHIUDE subito con «accodato» (final_kind answer, niente thread bloccato).
- **Esecuzione al ritorno**: NESSUN meccanismo nuovo — la coda già consegna al primo poll del device. Serve solo: (a) il gate `is_available` NON blocca l'enqueue dei deferred; (b) scadenza TTL → stato `expired` + notifica onesta.
- **Undo del differito**: il pending undo si scrive all'ENQUEUE col turno d'origine (actor incluso). NB «annulla» = ULTIMO turno dell'actor: se nel frattempo ha fatto altro, il differito non è più «l'ultimo» — comportamento corretto, documentarlo nel messaggio di notifica («per annullarla: …»).
- **Vincolo di senso**: differibili SOLO azioni idempotenti-nel-tempo dichiarate — primo taglio: whitelist per verbo (write/create/delete/move su path espliciti sì; get/read/list NO — un read differito di ore è spazzatura §2.8, meglio l'errore onesto attuale). Campo manifest additivo `[placement] deferrable=true` sui soli mutanti fs.

### A.2 Notifica di completamento (condivisa con A.0)
Piccolo notificatore server-side: alla transizione `done|failed|expired` di un'invocazione `deferred|abandoned_by_turn`, componi messaggio i18n (`_msg`) e consegna sul canale d'origine del turno (registrato nell'invocazione: `origin_actor`, `origin_channel`): telegram → daemon esistente; web → riusare il meccanismo eventi/SSE della chat se presente, altrimenti prossima-visita (messaggio in coda conversazione). NIENTE infrastrutture push nuove.
**Done A**: e2e Linux con client spento → turno defer → consenso → client riacceso → op eseguita → notifica ricevuta → undo funzionante; TTL scaduto → notifica `expired`.

## B. Robustezza (interventi puntuali, ciascuno autonomo)

1. **`executed` set illimitato** (client, `runner.rs`): LRU bounded (es. 4096 id) — il dedup server resta la difesa primaria (idempotenza per `invocation_id`), il set client è solo risparmio.
2. **Attesa sincrona → ceiling pool**: in `agent_server` esiste già `await_result` async — `remote_exec` gira nel contesto sync dell'engine; opzione minima: ridurre `poll_interval_s` adattivo (0.25→1s dopo i primi 5s) e documentare il ceiling; opzione piena: quando A.1 esiste, i turni lunghi diventano deferred-by-choice e il ceiling perde rilevanza. NON introdurre async nell'engine per questo.
3. **Backoff client su 5xx/rete**: verificare `main.rs` poll loop (oggi: intervallo fisso?); aggiungere backoff esponenziale cap 60s + jitter. Log onesto lato client.
4. **Formalizzare `expired`**: oggi lo stato compare solo nelle stages dell'undo remoto; con A.1 diventa stato di coda a pieno titolo (transizione da reaper: `queued` oltre TTL → `expired`).
5. **Heartbeat come telemetria di livello sandbox** (aggancio a W4): il client riporta `sandbox_level` nel profilo heartbeat → `devices.profile_json` (additivo, utile al gate `min_sandbox`).

## C. Multi-OS: client macOS (parità col livello Linux)

- **Build**: target `aarch64-apple-darwin` (+ `x86_64` se serve); il client è HTTP puro (niente TLS/ring §6 doc) → cross-compile pulita o build su mac. Firma: `codesign --sign -` ad-hoc; NIENTE notarizzazione (self-hosted §10.4: l'utente fa right-click→Apri la prima volta; documentarlo nell'installer).
- **Sandbox**: primo taglio = PARITÀ Linux-senza-bwrap: process-group + timeout kill + `sandbox="none"` DICHIARATO (il campo esiste già). `sandbox-exec`/Seatbelt = incremento successivo separato (profilo SBPL con gli stessi hint→root; API deprecata ma funzionante — decisione a valle).
- **pyenv**: python-build-standalone pubblica `aarch64-apple-darwin` → `pyenv.rs` deve solo mappare `os_family="macos"`→asset giusto (verificare la detection in `main.rs`/`state.rs` e il campo `platforms` dei manifest: aggiungere `"macos"` ai 13 device-abili è un edit manifest+re-sign §7.10).
- **Installer**: `install.sh` è POSIX — verificare le assunzioni Linux (systemd unit! su mac serve LaunchAgent plist). Deliverable: `install-macos.sh` con LaunchAgent + `metnos-client` in `~/Library/Application Support/Metnos/`.
- **e2e**: riusare `tests/e2e/tools/e2e-remote-executor.sh` su un mac reale (o CI se disponibile); pairing+find_packages+write+undo.
**Done C**: pairing da mac reale, 13 executor girano, undo round-trip verde, self-update funziona (verificare che `selfupdate.rs` gestisca il path .app-less: è un binario nudo, dovrebbe essere identico).

## Ordine consigliato
**A.0** (fix onestà, piccolo, valore immediato) → **B.3+B.1** (spiccioli) → **A.1+A.2** (il differito, cuore del topic) → **C** (macOS, indipendente — parallelizzabile) → B.2/B.4/B.5 dove non già assorbiti. W4 (spec gemella) è ortogonale e parallelizzabile.

## NON fare
- Niente redesign coda/protocollo wire (firmato+idempotente, validato).
- Niente TLS/rustls in questo giro (decisione MVP documentata §6 del doc padre).
- Niente push-notification infra nuova: canali esistenti.
- Niente differito per read-only (spazzatura temporale §2.8).
- Mai marcare `undone`/`done` senza evento reale (principio 29/4, ADR 0183).
