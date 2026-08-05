# Audit codebase Metnos — architettura + codice (giro 2, aree complementari)

> **Prodotto il 22/7/2026** da 8 revisioni adversariali per dominio (lettura del codice, sola lettura)
> + un check di duplicazione deterministico (AST/grep). Copre le aree che l'audit del 21/7
> (`audit_metnos_multidominio_21_7.md`) NON aveva toccato. **Solo report — nulla modificato.**
>
> **Copertura.** Questo giro: layer HTTP, canali/Telegram/dialoghi, scheduler_v2 + recurring_tasks,
> backend concreti + provider/router LLM, builtin universali (describe/extract/classify), learning/
> growth (mnestoma/telos/proposte/aging), loader + ciclo di vita skill, client Rust. **Già coperto il
> 21/7** (non ripetuto): engine/dispatch, confine NL→vocab, executor_scheduler (ADR 0196, ≠ scheduler_v2),
> synt/contratti, sites, safety/vaglio/undo/remoto. Insieme, i due audit coprono il grosso di runtime/ +
> executors/ + client-rs/.
>
> **Come leggerlo.** Ogni reperto: `[SEV]` `[categoria]` · file:line · scenario · confidenza · fix.
> **Prima i §Temi trasversali** — la maggioranza dei reperti gravi ricade in 6 pattern; alcuni sono la
> stessa radice vista da domini diversi. Poi la lista per-dominio (completa) e le aree verificate SANE.

---

## Sommario

- **2 P0** (client Rust), **~16 P1**, il resto P2/P3. ~60 reperti totali su 8 domini.
- **Il pattern dominante è §2.8 (falso successo)**: 7 domini su 8 hanno almeno un caso in cui un
  fallimento viene presentato come successo o come «0 risultati». È il difetto sistemico più diffuso.
- **Due buchi di sicurezza P0 nel client Rust** (journal write-ahead assente; interprete non firmato) e
  un **cluster di autorizzazione mancante** su callback/dialoghi (HTTP + Telegram).
- **Convergenze con gli audit precedenti**: [T1] estende il tema onestà; [T5] conferma [SAFE-5] del 21/7;
  il modello frontier hardcoded [BE-4] ricalca il reperto `consult_frontier` dell'audit manifest (22/7);
  lo scarto silenzioso del loader [LO-1] estende [SYNT-4].
- **Anti-rumore**: ogni agente ha verificato e SCARTATO ipotesi (protocollo backend polimorfico ≠
  duplicazione; «3 invii Telegram» infondata; describe deterministico onesto; esenzione aging integra).
  Le aree sane sono elencate in §Verificato SANO — non spenderci tempo.

---

## Temi trasversali (T1-T6)

### T1 · Falso successo / §2.8 — il pattern più diffuso (7 domini)
Un fallimento parziale o totale viene reso come `ok:True` con lista vuota, o come stato «riuscito».
- **[BE-1]** `geo_provider` scarta lo status del provider → 429/no-api-key diventano `entries:[]` «nessun luogo»; i rami `rate_limited`/`error` di find_places sono **codice morto irraggiungibile** (`geo_provider.py:51-59`).
- **[EN-2]** `classify_entries` su batch fallito assegna il **catch-all** = per le dimensioni chiuse `classes[-1]` (la più severa) e lo conta come classificato → «tieni le mail importanti» durante un hiccup llama → tutte `high` → filtro le tiene tutte (`classify_entries.py:456-462,529-532`).
- **[EN-1]** `extract_entries` proiezione: le sorgenti oltre `max_sources` sono scartate senza `truncated`/notice (`used=50, available_total=50`) — è il caso d'uso di testa (`extract_entries.py:1140-1142,1457-1476`).
- **[SC-1]** `partial` conta come fallimento del circuit-breaker → un task che consegna 19/20 ogni mattina è auto-disabilitato dopo 3 giorni (`daemon.py:302`, `storage.py:275`).
- **[SC-5]** `nightly_maintenance` ritorna dict (non `CallbackOutcome`) → /admin verde anche se i sub-task falliscono ogni notte (`nightly_orchestrator.py:82`).
- **[LG-2]** `change_observer` marca `rolled_back`/«intent malformed» il **100%** delle pipeline eseguite con successo (semantica observer non aggiornata al rewrite 2/7) (`change_observer.py:172`).
- **[LO-1]** loader: 11 punti di reject **senza log né superficie admin**; un `.py` non ri-firmato sparisce muto (`loader.py:1166`).
**Radice comune**: manca un contratto d'onestà applicato al confine. **Fix**: ogni ramo di
degradazione/troncamento/errore deve emettere i campi §2.7 e/o `CallbackOutcome(partial|error)`, e i
notice runtime NON devono saltare i PROCESSOR_VERBS quando il fallimento è reale.

### T2 · Autorizzazione mancante su callback/dialoghi (HTTP + Telegram)
La superficie dialoghi/consenso non lega l'azione all'identità del richiedente.
- **[HT-3]** route dialog HTTP (`dialog_form/submit/cancel`) richiedono solo ruolo `user` (ogni device LAN è auto-`user`) e risolvono per `dialog_id` a 64-bit senza owner binding → completare il dialogo (incl. raccolta credenziali/OAuth) di un altro se l'id trapela (`http_routes_agent.py:1058-1078`).
- **[CH-1]** callback Telegram `dlg:` risolve lo stato con fallback **globale cross-mittente**, zero verifica del proprietario — mentre `approve:`/`reject:`/`cap:` la fanno (`daemon.py:828-998`).
- **[CH-2]** callback `promoter:`/`sched:` gestiti **prima del pairing gate** e senza authz → un mittente non accoppiato cancella qualsiasi job o fa rollback di una promozione (`daemon.py:1055-1191,1265`).
**Fix**: bindare ogni dialogo/callback all'owner e verificare `resolve_actor(channel,sender)` (o firmare
l'URL come `photo_endpoint`); spostare promoter/sched DOPO il pairing.

### T3 · Gate fail-OPEN su errore (5 casi)
Diversi controlli, su eccezione, ammettono invece di rifiutare.
- **[HT-2]** `verify_user_cookie` accetta un cookie firmato (device anche revocato) se `is_device_bound` lancia (DB locked) — `except Exception: pass` (`http_auth.py:174`).
- **[LO-3]** verificatore semantico L6: il fallback su ImportError è `aligned=True` (il modulo è invece fail-safe) → il cancello descrizione-vs-codice diventa no-op (`skill_admission.py:416`).
- **[LO-4]** verb-boundary importer (ADR 0128): pass-through per ogni `domain:action` non in `contextual` (`importer_verb_verify.py:144`).
- **[LO-5]** skill enable-gate: su errore del registry la skill disabilitata si carica comunque (`loader.py:1110`).
- **[LO-6]** `METNOS_LOADER_VERIFY=0` disabilita tutta la firma senza guardia di produzione (`loader.py:698`).
**Fix**: fail-closed su errore per ogni gate di sicurezza/autorità; loggare quando un gate degrada.

### T4 · Autonomia / governance contraddittoria
- **[LG-1]** il promoter ETA auto-promuove i synth executor nel catalogo live (`dry_run` default **false**, grace 72h automatica, veto Telegram post-hoc) **mentre** lo stesso artefatto è proiettato come `change_intent` PROPOSED «in attesa di triage umano». Gate umano illusorio per i synth (`promoter.py:233`, `synt.py` adapter).
- **[LG-3]** `apply_dedupe_executors` deprecata l'executor **senza** il check `PROTECTED_NAMES`/`_is_synth` che i tre ager applicano → un dedupe «unifica web_fetch con get_urls» accettato deprecerebbe il core `get_urls` (classe delete_persons) (`change_applier.py:152-167`).
- **[LG-6]** synt adapter mappa `final_state=in_progress|missing` → `STATE_ACCEPTED` → auto-create senza triage (latente, non raggiungibile oggi) (`change_intent_adapters/synt.py:93`).
**Fix**: una sola governance (promoter dry-run di default → PROPOSED, oppure smettere di proiettare i
synth nella coda di triage); portare l'esenzione aging nel dedupe applier; mappare in_progress→PROPOSED.

### T5 · Sicurezza client Rust (2 P0 + 3 P1)
- **[RS-1] P0** journal write-ahead assente: effetto PRIMA dello spool → crash/watchdog → riesecuzione del mutante ([SAFE-5] confermato) (`runner.rs:320-347`).
- **[RS-2] P0** interprete Python scaricato senza firma, sha256 opzionale off-by-default — la TCB che esegue ogni executor non è verificata mentre executor/shim/self-update lo sono (`pyenv.rs:57-156`, `install.ps1.in:96`).
- **[RS-3] P1** ACL Windows cumulativi su SID container condiviso, mai revocati fra invocazioni → ambito FS effettivo = unione di tutte le dir mai concesse (`appcontainer.rs:244-255,512,777`).
- **[RS-4] P1** ramo job-object non isola il FS → un executor `code:exec` legge `data_dir/key` (chiave di firma del device) → impersonazione; su Linux bwrap non espone `data_dir` (asimmetria) (`sandbox_windows.rs:334`, `config.rs:24`, `identity.rs:47`).
- **[RS-5] P1** anti-replay assente: nessun nonce/scadenza assoluta nel payload firmato → MITM rigioca un'invocazione mutante dopo restart (`executed` in-RAM è l'unica freshness) (`wire.rs:43`, `runner.rs:186`).
**Fix**: journal STARTED write-ahead; descrittore runtime firmato per l'interprete; grant per-invocazione
revocati; chiave fuori reach del processo job-object (DPAPI/ACL); `expires_at`/nonce nel payload firmato.

### T6 · Duplicazione, codice morto, vestigiale
Vedi §Duplicazione per l'elenco completo. Sintesi: `ProcessLock` duplicato-divergente (HTTP),
`_auth_needs_inputs` in 3 copie con drift (backend), `_format_dialog_completion` duplicato (Telegram),
`migrate_v1` vestigiale ~315 LOC (scheduler), `proposals_unified.py` morto 570 LOC (learning),
`CapabilitySpec`/`Executor` omonimi (loader), file `*_v2` prefilter, provider Nominatim fantasma (geo).

---

## Reperti per dominio (completo)

### Layer HTTP
- **[HT-1][P1][auth]** SSRF via DNS-rebinding TOCTOU su `photo_web_proxy` (anonimo, tunnel-exposed): l'URL è validato risolvendo l'host una volta, `urllib` lo ri-risolve al connect → rebinding a 127.0.0.1/169.254.169.254 (`http_routes_agent.py:1597-1687`). Fix: risolvere una volta, pinnare l'IP vettato, connettere a quello.
- **[HT-2][P2→P1][auth]** `verify_user_cookie` fail-open (vedi T3).
- **[HT-3][P2][authz]** dialog routes senza owner binding (vedi T2).
- **[HT-4][P2][race]** `turn_status` TOCTOU su dict privato → 500 su endpoint di polling (`http_routes_agent.py:2228`). Fix: `.get()` + re-check.
- **[HT-5][P2][perf]** `admin_home` fa sqlite+FS I/O sincrono in handler async → stalla l'event loop per tutti (`http_routes_admin.py:217`). Fix: `asyncio.to_thread`.
- **[HT-6][P2][dup]** `ProcessLock` duplicato-divergente agent_server vs metnos_http_server (`:867` / `:254`).
- **[HT-7][P2][resource]** SSE non cancella `_run_blocking` su disconnessione client → worker bloccati accumulati sul pool condiviso (`http_routes_agent.py:1022`). Fix: executor dedicato bounded + semaforo.
- **[HT-8][P2][dead]** `/agent/register` in whitelist e pubblicizzato in `.well-known` su 8770 ma la route esiste solo su 8765 → 404 (`http_auth.py:38`, `http_routes_agent.py:176`).
- **[HT-9][P2][bug]** `agent_server` sovrascrive il logger strutturato con uno stdlib bare (`agent_server.py:54`).
- **[HT-10][P2][auth]** `.well-known` espone `sha256(admin_key)[:16]` all'anonimo (oracolo offline; P2-low perché chiave 256-bit) (`http_routes_agent.py:159`).

### Canali / Telegram / dialoghi
- **[CH-1][P1]** callback `dlg:` cross-mittente senza owner check (vedi T2).
- **[CH-2][P1]** callback `promoter:`/`sched:` prima del pairing, senza authz (vedi T2).
- **[CH-3][P2][bug]** `_cap_pending_save` scrive il token di consenso + comando admin senza `chmod 0600` (umask→0644, leggibile localmente); `dialog_pending` invece lo fa (`daemon.py:73`).
- **[CH-4][P2][bug]** `_safe_sender`/`_dialog_path` non neutralizzano `..` e non sanitizzano `dialog_id` (traversal latente, oggi protetto solo dalla regola aiohttp del segmento) (`dialog_pending.py:85`).
- **[CH-5][P2][race]** `consume_pairing_token` SELECT-poi-UPDATE in autocommit senza `BEGIN IMMEDIATE` → doppio consumo TOCTOU (`users.py:448`).
- **[CH-6][P2][dup]** `_format_dialog_completion` (mascheratura credenziali) duplicato inline (`daemon.py:181` vs `:951`) → rischio di stampare un segreto in chiaro se si aggiorna un solo ramo.
- **[CH-7][P2][bug]** `poll()` persiste l'offset PRIMA che il daemon gestisca i messaggi → crash in `handle_message` = perdita silenziosa del messaggio (`telegram.py:611` vs `daemon.py:1822`).

### Scheduler (v2 + recurring_tasks)
- **[SC-1][P1]** `partial` = fallimento circuit-breaker (vedi T1).
- **[SC-2][P2][bug]** one-shot rifira all'infinito se `cancel_job` lancia (contabilità doppia, ordine deletion) (`recurring_tasks.py:671`).
- **[SC-3][P2][dup]** doppio meccanismo countdown one-shot: `add_job` hardcoda `recurring=True`, la semantica `times=N` è bolted-on via `recurring_tasks.db` (`client.py:101`).
- **[SC-4][P2][dead]** `_normalize_task_name` allowlist stantia (`apply_ager`/`synt_suggest` non più vivi) → i builtin non indirizzabili per nome (`recurring_tasks.py:1172`).
- **[SC-5][P2]** `nightly_maintenance` verde su fallimento (vedi T1).
- **[SC-6][P2][race]** firing senza claim DB-level; safe solo perché esiste un solo daemon (`storage.py:190`, `daemon.py:230`).
- *Vestigiale*: `migrate_v1` gira a ogni boot su un DB v1 che nessuno scrive (~315 LOC).

### Backend + provider LLM
- **[BE-1][P1]** `geo_provider` scarta lo status → falso «0 risultati» (vedi T1).
- **[BE-2][P2][dup/bug]** MemoryBackend vs SqliteBackend divergono su NULL ordering: posizione opposta + `TypeError` su Memory con ≥2 NULL + json.loads asimmetrico (`memory.py:38` vs `sqldatabase/__init__.py:101`).
- **[BE-3][P2][dup]** 3 copie a mano di `_auth_needs_inputs` con drift: contacts omette campi, events hardcoda un errore italiano (§7.13); il canonico `_google_auth_common` esiste ma non è riusato (`contacts/gw:43`, `events/gw:96`, `gmail/gw:55`).
- **[BE-4][P2][ADR-0146]** frontier col solo `provider` istanzia **Sonnet** invece dell'Opus della SoT; `describe()` riporta `model=None` (`llm_provider.py:677,1093`). Converge col reperto `consult_frontier` (audit manifest).
- **[BE-5][P2][bug]** `run_with_retry` ritenta i transient/429 senza backoff → martella (`_google_api_runner.py:133`).
- **[BE-6][P2][dead]** catena geo = google/photon ma manifest+docstring parlano di Nominatim; `nominatim_client.py` **non esiste** (§9.1) (`geo_provider.py:19`, `photon_client.py:11`).
- **[BE-7][P2][bug]** IMAP `EXPUNGE` folder-wide dopo `STORE \Deleted` → cancella `\Deleted` preesistenti (`email_metnos.py:1018,848`). Fix: `UID EXPUNGE` (UIDPLUS).
- **[BE-8][P3][dead]** `email_metnos.delete` EXPUNGE irreversibile senza `_undo` mentre §5 dice che `delete_messages` non esiste (probabile morto) (`email_metnos.py:811`).

### Builtin universali (describe/extract/classify)
- **[EN-1][P1]** `extract_entries` proiezione perde le sorgenti troncate (vedi T1).
- **[EN-2][P1]** `classify_entries` catch-all = classe più severa contata come classificata (vedi T1).
- **[EN-3][P2]** proiezione omette anche `truncated_intentional` di `max_total` (`extract_entries.py:1457`).
- **[EN-4][P2]** ramo LLM di extract non normalizza le date a ISO 8601 nonostante il contratto (`extract_entries.py:1325`).
- **[EN-5][P2]** describe map-reduce: il segnale §2.7 del reduce viene poppato ai livelli «not capped» (`describe_entries.py:361`).
- **[EN-6][P2][dead]** classify: due rami irraggiungibili + parametro `fields` inutilizzato (`classify_entries.py:497,207`).

### Learning / growth
- **[LG-1][P1][autonomy]** promoter auto-promuove vs triage umano (vedi T4).
- **[LG-2][P1]** change_observer marca rolled_back ogni pipeline riuscita (vedi T1).
- **[LG-3][P2]** dedupe applier bypassa l'esenzione aging (vedi T4).
- **[LG-4][P2][bug]** proto-mnest crescita illimitata: il purge (`weight<0.05`) è irraggiungibile perché il bootstrap è 0.30 e il peso non scende mai sotto (`mnestoma.py:721-774`).
- **[LG-5][P2][dead]** `proposals_unified.py` (570 LOC) senza importer vivo (route rimosse 13/6).
- **[LG-6][P2][autonomy latent]** synt adapter `in_progress`→ACCEPTED (vedi T4).

### Loader / ciclo di vita skill
- **[LO-1][P1]** scarto silenzioso al load (11 reject muti, non in /admin) (vedi T1). Estende [SYNT-4].
- **[LO-2][P2][firma]** `lifecycle="proposed"` bypassa firma/digest/main-entry mentre lega un `code_path` (contenuto oggi, ma buco del gate) (`loader.py:1163`).
- **[LO-3][P2]** L6 verifier fail-open su import error (vedi T3).
- **[LO-4][P2]** importer verb-boundary pass-through per celle non in `contextual` (vedi T3).
- **[LO-5][P2]** skill enable-gate fail-open (vedi T3).
- **[LO-6][P2]** `METNOS_LOADER_VERIFY=0` spegne tutta la firma senza guard (vedi T3).
- **[LO-7][P2][dup]** `CapabilitySpec`×2, `Executor`×2 (collisione di nome, non divergenza).
- **[LO-8][P3][dead]** `verb_by_target_side` lookup scartato (`importer_verb_verify.py:130`).

### Client Rust
- **[RS-1][P0]**, **[RS-2][P0]**, **[RS-3..5][P1]** — vedi T5.
- **[RS-6][P2][idempotenza]** `flush_pending` scarta il result su QUALSIASI 4xx (incl. 408/423/425/429) → perdita silenziosa su rate-limit (`runner.rs:507`).
- **[RS-7][P2][sicurezza]** cache-hit ri-verifica solo lo sha del **manifest**, non del **codice** né la firma (`executors.rs:69-85`).
- **[RS-8][P2][dup]** sandbox scope divergente Linux/Windows: Windows concede i path-target degli args, Linux solo gli hint → stessa invocazione firmata, autorità FS diversa (`sandbox_linux.rs:226` vs `sandbox_windows.rs:285`).
- **[RS-9][P2][fragile]** `unique_suffix()` = indirizzo stack, non garantisce unicità (`sandbox_windows.rs:565`).

---

## Duplicazione / codice morto / vestigiale (check deterministico + agenti)

**Duplicazione confermata (da consolidare)**:
- `ProcessLock` — agent_server.py:867 vs metnos_http_server.py:254 (divergono: owner, testi). → `runtime/process_lock.py`.
- `_auth_needs_inputs` — 3 copie (contacts/events/gmail gw) con drift → riusare `_google_auth_common`.
- `_format_dialog_completion` — daemon.py:181 vs inline :951 → una fonte.
- `_env_int` — replicato in config/fastpath_promote/proposer/manifest_rules → importare da config.

**Codice morto / vestigiale (rimuovere §7.1)**:
- `proposals_unified.py` (570 LOC) — nessun importer vivo, route rimosse.
- `migrate_v1` (~315 LOC) — gira a ogni boot su DB v1 mai scritto.
- rami morti: geo `rate_limited`/`error` in find_places (irraggiungibili per [BE-1]), classify (497/207), `verb_by_target_side` (importer:130), `/agent/register` whitelist su 8770.
- `email_metnos.delete` — probabile morto ([BE-8]).

**Da verificare (candidati morti)**:
- `prefilter_strategies/*_v2` (token_flat_v2, trie_v2, selective_semantic_v2) — registrati ma 1 solo riferimento esterno; vivi solo se selezionati dal default `METNOS_PREFILTER_*`. Verificare il default.
- `index_schema_upgrade.py` + `_v4.py` — entrambi vivi: migrazione a doppio stadio o residuo?

**Collisioni di nome (rinominare, non urgente)**: `Executor`, `RunResult`, `ValidationResult`, `Verdict`,
`CapabilitySpec` definiti in 2 file ciascuno (responsabilità diverse — nessuna divergenza run-time).

---

## Verificato SANO (anti-rumore — non toccare)

- **HTTP**: cookie HMAC `compare_digest` + scadenza firmata; Bearer-prima-di-cookie; XFF/CF-Connecting-IP onorati solo da peer proxy fidato; pairing token 128-bit monouso atomico; URL foto firmati HMAC+TTL; ETag ricalcolati da payload fresco; autoescape Jinja on; upload/dialog-preview traversal-safe.
- **Telegram/pairing**: firma Ed25519 + `exp` + versione; `consume_code` atomico (`BEGIN IMMEDIATE` + PK); `cap:` self-scoped; niente guest→host; bootstrap Full solo se sender==default_chat_id.
- **Scheduler**: un solo daemon (no doppio-fire); DST/spring-forward corretto; `CallbackOutcome` sound sul path di consegna user-task (un push riuscito NON maschera pipeline fallita).
- **Backend**: protocollo `find/read/write/delete` polimorfico (dispatch table esplicite, non copie); stub Telegram onesti (`ERR_NOT_IMPLEMENTED`); move su Drive vietato onestamente; nessuna race OAuth (no fan-out); `ensure_fresh_token` atomico.
- **Builtin**: `run_bounded`/`AgenticProposal` è un helper CONDIVISO (non copia); map-reduce non replicato; describe deterministico onesto (`deterministic:True/False` corretto, outage→`ok:False`).
- **Learning**: esenzione aging integra su tutti e 3 gli ager (`_is_synth`+PROTECTED_NAMES); generatori ritirati solo lettura-legacy; killer `layer_overlap` presente; 11 telos-lenses condividono `_base` (no drift); TTL 21gg corretto sugli shadow.
- **Loader**: reconcile NON cancella un executor vivo; la firma copre il CODICE (non solo il manifest); audit append-mode.
- **Rust**: fallback AppContainer→job-object pre-effetto; self-update firmato+sha, no-downgrade, bounded (`MAX_PROBATION_BOOTS=2`); `shim_rel_path` blocca il traversal; `server_sig` fail-closed su OGNI invocazione prima di eseguire; handle FFI con RAII.

---

## Piano d'attacco consigliato

1. **P0 Rust subito**: [RS-1] journal write-ahead (STARTED prima dell'effetto) e [RS-2] descrittore
   interprete firmato. Sono gli unici P0 e toccano la TCB remota.
2. **T2 (autorizzazione callback/dialoghi)**: owner-binding su dialog HTTP + `dlg:` Telegram; spostare
   promoter/sched dopo il pairing. Un fix di pattern che chiude [HT-3],[CH-1],[CH-2] insieme.
3. **T3 (fail-open → fail-closed)**: un passaggio su [HT-2],[LO-3..6] — cambiare la policy d'errore dei
   gate. Basso rischio, alto valore.
4. **T1 (onestà §2.8)**: il più diffuso — [BE-1],[EN-1],[EN-2],[SC-1],[SC-5],[LG-2],[LO-1]. Ognuno è un
   ramo di degradazione che non emette i campi §2.7/`CallbackOutcome`. [LO-1] (loader muto) va fatto con
   la superficie /admin.
5. **T4 (governance)**: decidere opt-in vs opt-out del promoter; portare l'esenzione aging nel dedupe
   applier ([LG-3] è il più pericoloso — deprecazione di un core).
6. **[HT-1] SSRF**, poi i P2 di robustezza (backoff [BE-5], IMAP UID EXPUNGE [BE-7], SSE pool [HT-7]).
7. **T6 sweep**: consolidare i 4 duplicati, rimuovere il morto (`proposals_unified`, `migrate_v1`,
   rami irraggiungibili), verificare i `*_v2` prefilter.

**Regole operative**: sola lettura finora — ogni fix di codice di prodotto richiede ≥1 turno reale
`/agent/turn` sul dominio (§8.5); re-sign dopo edit executor/manifest (§7.10); i fix Rust richiedono
rebuild+test del client sul device (non solo `cargo check`). Bench compound con `METNOS_ENGINE=v3`.
