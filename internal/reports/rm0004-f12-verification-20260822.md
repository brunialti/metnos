# RM-0004 — verifica privata F12 (2026-08-22)

Stato: **gate automatico e prova operativa esterna superati**.
Questo rapporto è interno: non va pubblicato, incluso in Tutor o usato per
presentare LRE come funzione distribuita. L'unità systemd è rimasta assente e
inattiva; l'interruttore è stato attivato soltanto nel processo circoscritto
della prova esterna e ripristinato subito dopo.

## Esito sintetico

Il nucleo generico, il preset privato immagini e i confini HTTP/Telegram hanno
superato la suite integrata e la suite completa. Le prove avversariali non
hanno osservato duplicazioni, sorgenti fuori dal denominatore o commit con un
fence obsoleto. Il corpus da 980 file usa il contenitore su spool: né il
sigillo né la validazione costruiscono una seconda copia integrale in memoria.

F12 ha inoltre corretto cinque lacune emerse durante la certificazione:

1. il factory di produzione ora collega esplicitamente l'attestatore delle
   sorgenti remote;
2. `provider_unavailable` appartiene alla tassonomia transitoria, mentre una
   credenziale revocata resta terminale e richiede intervento;
3. transazioni e operazioni filesystem critiche espongono punti di arresto
   nominati e condivisi;
4. il preset immagini è composto nel registro di distribuzione, non importato
   dal nucleo LRE. Il nucleo non contiene nomi OCR, immagini, `98` o `980`;
5. la conferma dell'outbox conserva ora il solo identificativo del messaggio
   restituito da Telegram, sufficiente per la ricevuta operativa, senza
   registrare chat, destinatario o risposta completa del fornitore.

Il 22 agosto 2026 Roberto ha autorizzato esplicitamente la chat Telegram
dell'amministratore. La prova ha attraversato il metodo del daemon, il gate,
l'autorità di associazione, la localizzazione, l'outbox e la vera Bot API. Un
solo messaggio è stato confermato dal fornitore e dal destinatario; il ciclo
successivo non ha prodotto duplicati. Il test HTTP usa invece la route
`aiohttp` reale ma sostituisce ancora il turno semantico con un risultato
deterministico. Questa sostituzione è dichiarata e non equivale a un progetto
pilota o alla distribuzione della funzione.

## Modifiche verificate

### Confini transazionali e filesystem

- `transactions.py` è l'unico helper per `BEGIN IMMEDIATE`, commit, rollback e
  relativi punti `before_*`/`after_*`.
- Store dei lavori, deposito artefatti e autorità delle sorgenti usano lo
  stesso protocollo; una callback non valida fallisce subito.
- Inventario locale: punti nominati su scoperta, `lstat`, scansione, apertura,
  lettura, rilettura dei metadati e commit dello spool.
- Artefatti: punti nominati su temporaneo, scrittura, `fsync`, installazione
  atomica, verifica finale, registrazione e pubblicazione.
- Worker: punti nominati dopo il claim, prima/dopo `running`, prima
  dell'invocazione, dopo il risultato e prima/dopo il commit.
- Un guasto dopo il commit è trattato come esito ambiguo recuperabile tramite
  replay idempotente, non come prova che la transazione sia stata annullata.

### Scala senza accumulo

- Soglia della rappresentazione in memoria: 512 sorgenti. È una scelta di
  rappresentazione, non un limite di cardinalità.
- Lotto dello spool: 1.024 record.
- Corpus F12: 980 file creati uno alla volta; limite ammesso 980 sorgenti,
  16 MiB complessivi e profondità 2.
- `SealedInventory` conserva i metadati nello spool SQLite; `validate_inventory`
  non produce l'envelope JSON monolitico per una `Sequence` su disco.
- Soglie di arresto del lavoro sorgente: 588 commit (30% di 1.960 unità
  sorgente) e 1.176 commit (60%).

### Universalità del motore

`runtime/durable_workloads/` non importa il preset immagini e non contiene
rami per OCR o per uno specifico corpus. `runtime/durable_runtime_registry.py`
è il confine di composizione che traduce ogni pacchetto approvato nello stesso
`RuntimeRegistration`. Scheduler, storage, fencing, retry, artefatti e worker
restano indipendenti dal dominio.

## Esecuzioni riproducibili

| Prova | Comando | Esito osservato |
|---|---|---|
| Gate integrato F8-F12 | `timeout --signal=TERM --kill-after=15s 420s pytest -q tests/runtime/durable_workloads tests/runtime/executors/test_read_files_ocr_agentic.py tests/runtime/executors/test_executor_scheduler_durable.py tests/runtime/http/test_durable_workloads_api.py tests/runtime/http/test_http_server.py tests/runtime/channels/test_telegram_offset_ack.py tests/runtime/engine/test_finalize_framework_universal.py tests/runtime/infra/test_provenance_equivalence.py tests/runtime/infra/test_guard_corpus_equivalence.py` | 363 passati in 47,19 s |
| Corpus 980 + processi reali | `timeout --signal=TERM --kill-after=15s 420s pytest -q tests/runtime/durable_workloads/test_image_preset_e2e.py::test_full_image_preset_survives_real_sigkill_at_30_60_and_artifact_commit` | 1 passato in 26,89 s |
| Suite completa | `timeout --signal=TERM --kill-after=15s 900s pytest -q` | 6.762 passati, 102 esclusi e 1.074 subtest passati in 498,20 s |
| Golden dei piani reali | `pytest -q tests/runtime/infra/test_guard_corpus_equivalence.py` | 804 piani equivalenti; 7 impronte aggiornate e spiegate per i selettori termici |
| Controlli di sintassi | `git diff --check` e `python3 -m compileall -q ...` | nessun errore |
| Telegram esterno | processo monouso `PYTHONPATH=runtime python3`, database temporaneo e `ChannelDaemon._push_durable_workload_notices()` | gate spento: coda immutata; gate acceso: un invio confermato con ricevuta redatta; secondo ciclo: zero invii; conferma del destinatario acquisita |

La suite completa emette quattro avvisi di deprecazione da
`test_audit_jsonl_bounded.py`: il test combina thread e `fork`. Non sono errori
LRE, ma l'avviso va eliminato prima di rendere quel test portabile su sistemi
nei quali `fork` dopo l'avvio di thread non è sicuro.

Una ripetizione precedente della suite ha inoltre intercettato un difetto
intermittente estraneo a LRE: una traduzione completata al cambio di secondo
poteva apparire più recente della propria sorgente e diventare falsamente
autorevole. `align_messages()` distingue ora le traduzioni derivate tramite la
provenienza persistita e usa il tempo soltanto fra modifiche indipendenti. La
prova deterministica copre sia la traduzione più recente della sorgente sia una
vera modifica umana nella lingua di destinazione.

## Iniezioni e condizioni avverse

| Condizione | Evidenza |
|---|---|
| Crash al 30% e 60% | processi `spawn`, arresto con `SIGKILL`, lease da 300 ms e processo nuovo dopo ogni arresto |
| Crash durante artefatto | `SIGKILL` dopo la verifica del blob e prima della sua registrazione; il processo pulito completa una sola riga logica |
| Crash durante pubblicazione | processo reale arrestato dopo `publication_after_fsync`; riconciliazione per digest in un processo nuovo |
| Commit ambiguo | guasto dopo commit su lavoro, sorgente, artefatto e pubblicazione; replay con una sola riga osservabile |
| Due worker e fence | 100 corse fra processi e 100 corse del vecchio fence; un solo risultato committato |
| Provider e limite richieste | `provider_unavailable`, poi `rate_limited`, poi successo: tre tentativi, un risultato |
| Credenziale revocata | `permission_denied`: `needs_attention`, nessun retry automatico |
| Binding rimosso/cambiato | identità congelata verificata prima dell'invocazione; executor o modello non viene chiamato |
| Orologio | rollback ampio, correzione piccola e ripresa della lease senza estendere la scadenza autorevole |
| Disco pieno | `OSError(ENOSPC)` reale sul confine `fsync`; nessuna riga o temporaneo parziale, poi commit riuscito |
| Database occupato | writer lock reale, `busy_timeout=100 ms`, errore entro 1 s, stato intatto e commit successivo |
| File modificato | hash e metadati riletti; modifica o sostituzione produce errore chiuso, mai una sorgente diversa |
| Raccolta concorrente | 16 corse, seme `0xF12`, latenze 0/1/3 ms; il blob committato sopravvive e gli errori ammessi sono ristretti alla corsa documentata |
| Saturazione remota | 12 corse, seme `0xF12`, latenze 0/1/3 ms, due proprietari e capacità device=1; turno interattivo entro 200 ms |

I retry descritti nella raccolta concorrente sono retry **logici verificati**
dell'operazione, non rilanci di pytest: un errore diverso dalle due condizioni
di corsa ammesse fa fallire immediatamente il test.

## Matrice delle venti prove del mandato

| N. | Proprietà | Evidenza principale |
|---:|---|---|
| 1 | creazione e lettura owner-scoped | `test_submit_is_idempotent_and_owner_ids_can_overlap`; `test_read_dtos_are_closed_owner_scoped_and_cursor_paged` |
| 2 | chiavi stabili e deduplica | `test_same_owner_deduplicates_content_and_replays_logical_commit`; `test_image_workload_invoker_derives_stable_question_identities` |
| 3 | due worker, un commit | `test_two_real_processes_produce_one_commit_in_100_races` |
| 4 | crash dopo esecuzione, prima del commit | `test_real_process_crash_at_controlled_boundaries[attempt_after_result]` |
| 5 | crash dopo commit, prima dell'ack | `test_real_process_crash_at_controlled_boundaries[attempt_after_commit]` |
| 6 | arresti 30%, 60% e pubblicazione | test del corpus 980 più `test_real_process_reconciles_crash_during_publication` |
| 7 | lease scaduta e fencing | `test_expired_lease_rejects_heartbeat_commit_and_then_recovers`; `test_old_real_worker_cannot_heartbeat_or_commit_after_new_fence` |
| 8 | retry transitorio selettivo | `test_provider_outage_and_request_limit_retry_then_commit_once` |
| 9 | nessun retry cieco di effetto ambiguo | `test_telegram_adapter_never_retries_an_uncertain_send`; `test_send_does_not_repeat_an_ambiguous_transport_call` |
| 10 | pausa, ripresa e cancellazione | `test_pause_settles_when_the_last_active_attempt_finishes`; `test_cancel_drains_active_attempt_and_cancels_waiting_units_once` |
| 11 | invalidazione minima | `test_executor_prompt_and_binding_mutations_invalidate_only_descendants` |
| 12 | ordine stabile fuori ordine | `test_reduction_order_is_identical_after_reversed_parallel_completion` |
| 13 | riduzione oltre un prompt | `test_hierarchy_resumes_between_groups_and_exposes_one_root`; vincoli `max_input_bytes` |
| 14 | budget scheduler, nessun pool privato | suite `test_executor_scheduler_durable.py` e scansione statica del pacchetto |
| 15 | cap e troncamento fino al terminale | `test_completion_rejects_caps_and_unmaterialized_usage`; `test_invoke_caps_files_and_reports_truncation` |
| 16 | isolamento completo | test IDOR di store, controllo, HTTP, artefatti e outbox; blob fisicamente separati per proprietario |
| 17 | download dopo nuova sessione/device | route HTTP reale con una seconda `ClientSession`, autorizzazione owner-bound, scadenza e revoca |
| 18 | Telegram senza raffica | outbox deduplicata, coalescenza e `test_durable_telegram_cycle_is_inert_off_and_delivers_once_on` |
| 19 | corpus 98 | test parametrico: 98 OCR, 98 estrazioni, occorrenze contabilizzate, due soluzioni e tre artefatti |
| 20 | corpus 980 e riavvii | test con tre `SIGKILL`, controllo indipendente e nessuna duplicazione |

Una sorgente illeggibile ha inoltre una prova dedicata: resta nella tabella
`sources` con `accounted=1`, l'unità fallisce esplicitamente e il lavoro non
produce artefatti né viene presentato come completato.

## Prove aggiuntive F12

- IDOR: dettaglio, comandi, SSE, artefatti, download, outbox e cancellazione.
- Cancellazione durante un tentativo: convergenza dopo il drenaggio delle
  unità attive, nessun nuovo claim.
- Errore outbox: distinzione fra errore precedente all'invio, esito ambiguo,
  rifiuto permanente e limite rigido dei tentativi.
- Aggiornamento schema: database vuoto, doppia migrazione, rollback dopo
  eccezione, due connessioni concorrenti e fixture v1/v2.
- Raccolta concorrente: cursore persistente, batch limitato, symlink rifiutati
  e corsa con la registrazione.
- Nessuna attesa indefinita: timeout su DB, scheduler, heartbeat, arresto del
  servizio, processi figli e turno interattivo.
- Controllo indipendente del corpus: `PRAGMA integrity_check`,
  `foreign_key_check`, conteggi e identità uniche di unità/risultati, fence,
  digest e size dei file, assenza di temporanei e revoca finale dei grant.

## Interruttore di funzionalità e trasporti

- Flag disattivato: il servizio non compone factory, non reclama lavoro e non
  invia notifiche.
- Flag attivato nel test: stessa route HTTP interattiva e stesso testo; daemon
  Telegram reclama una sola riga outbox, formatta con il canale reale e la
  marca `sent` una volta.
- Replay: nessun secondo invio.
- Prova esterna autorizzata: una notifica localizzata è arrivata alla chat
  verificata dell'amministratore. L'outbox ha registrato `sent`, un solo
  tentativo e l'identificativo redatto del messaggio; il destinatario ne ha
  confermato la ricezione.
- Stato macchina al termine della prova: nessuna unità
  `metnos-durable-worker.service` installata o attiva; il template conserva
  `METNOS_DURABLE_WORKLOADS_ENABLED=0`.

## Limiti residui e confine F13

1. OCR, LLM e device del corpus sono sostituti deterministici. La prova misura
   il control plane, non durata, qualità, costo o affidabilità dei provider
   reali.
2. L'ambiente `.venv` locale non contiene `jsonschema==4.10.3`, pur dichiarato
   in `requirements.txt`; i comandi sopra hanno usato il Python di sistema,
   che contiene la versione richiesta. Questa anomalia dell'ambiente non è
   stata mascherata né attribuita al runtime.
3. F13, installazione dell'unità, attivazione, documentazione pubblica e
   progetto pilota restano fuori da F12.

Conclusione: F12 ha superato sia il gate automatico sia quello operativo. Ciò
non abilita implicitamente F13: il worker resta spento e LRE non viene ancora
presentato come funzione distribuita.
