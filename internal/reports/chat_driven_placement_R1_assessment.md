# Chat-driven placement — R1: report per assessment esterno

**Data:** 2026-07-03 · **Branch:** `session/detection-lexicon-i18n` (non pushato) ·
**Autore:** agente (sessione Opus) · **Scopo del documento:** sottoporre R1 a un
agente esterno per un **assessment critico + controllo di correttezza**.

## 0. Contesto e obiettivo
Metnos esegue gli executor sul server `.33`. Da poco può eseguirli anche su un
**PC appaiato** (client Rust remoto). Obiettivo del feature («è lo scopo di
tutto», Roberto): **parlare alla chat** e far eseguire l'operazione sul PC giusto
— non via endpoint di test, ma dalla conversazione naturale.

Questo report copre **R1** = il ponte chat→placement. NON copre R2/R3 (copertura
executor mutanti, fasi successive).

## 1. Intuizione dell'architettura (cosa esisteva già)
- `runtime/placement.py::choose_placement` (funzione PURA, testata) decide dove
  gira un executor. Livello **L1.c**: se l'intento nomina un device
  (`intent["device"]`), instrada lì — anche per executor `scope="any"`. Livello
  **L1.d**: gate di connessione `is_available` (battito < 60s + non revocato) →
  `ERR_DEVICE_UNREACHABLE`. Gate piattaforma. **Il routing e il controllo di
  connessione c'erano già.**
- `runtime/remote_exec.py::invoke_remote` consegna l'invocazione firmata al device.
- **MA** l'hook in `agent_runtime.invoke_executor` era MORTO: gated su
  `scope=="device"` (nessun executor in prod ce l'ha) e passava `intent=None`.

## 2. Cosa ho costruito (R1)
Tre pezzi + tag. Tutti in `runtime/`.

1. **`runtime/target_device.py`** (nuovo) — resolver deterministico
   `resolve_target(query, devices, last_target, is_available)`:
   - **Nome device** abbinato ai nomi REALI dei device, SOLO se preceduto da
     preposizione locativa (`su|sul|…|on`): «sul portatile-ufficio» instrada,
     «foto di casa» col device «casa» NON instrada (guardia falsi-positivi).
     Match più lungo vince (nomi con prefisso comune).
   - **Marcatore locale** («su questo pc / sul mio pc / localmente / on this pc /
     locally») → device dell'utente (uno → quello; più → `ambiguous`).
   - **Marcatore server** («sul server / qui sul server») → riporta a `.33`.
   - **Nessun segnale** → `last_target` appiccicoso (se ancora presente+online),
     altrimenti server.
   - Controllo connessione applicato SEMPRE al target risolto (anche
     appiccicoso): offline → `status="unreachable"` (mai fallback silenzioso).
   - Ritorna anche `cleaned_query` (l'adjunct di destinazione rimosso).
2. **`runtime/chat_target_store.py`** (nuovo) — destinazione appiccicosa per
   `sender_id` (`<channel>:<actor>`), sqlite `chat_target.db` co-locato con
   `devices.db` (segue l'isolamento test via env).
3. **`runtime/agent_runtime.py`** (edit):
   - `invoke_executor(..., target_device=None)`: hook SGANCIATO — parte se
     `scope=="device"` **oppure** se `target_device` (nome) è presente; passa
     `intent={"device": target_device}` a `choose_placement`. **Senza
     target_device e senza scope=device → path locale IDENTICO a prima
     (prod-safe).**
   - `_try_engine_v2`: dopo l'intent, risolve il target (se ci sono device),
     gestisce `unreachable`/`ambiguous` con ritorno onesto (messaggi i18n
     `ERR_DEVICE_UNREACHABLE`/`ERR_DEVICE_AMBIGUOUS` già seed), passa il nome al
     `_invoke`, aggiorna l'appiccicoso SOLO su riferimento esplicito, usa la
     `cleaned_query` per il dispatch.
   - **Tag**: campo `target_device` nel dict risultato + marcatore `📍 <nome>`
     anteposto al `final_text` quando remoto.

## 3. Decisioni di design (fissate con l'utente)
- Identificazione PC = **NOME device** (dato) + marcatore locale; **non** l'IP di
  connessione (fragile su tunnel Cloudflare = `127.0.0.1`, e nullo su Telegram);
  **non** LLM/liste-sinonimi (§7.9 deterministico, regola anti-contaminazione).
- Default = **ultima destinazione appiccicosa**; primo turno = server; reset con
  «sul server».
- Controllo connessione SEMPRE sul target (esplicito o appiccicoso) → «device non
  connesso», mai esecuzione silenziosa altrove (§2.8).
- Prod invariata quando nessun PC è nominato/appiccicato.

## 4. Validazione fatta
- **Unit** `runtime/tests/test_target_device.py` (16 test): routing per nome,
  guardia falsi-positivi (nome nudo), offline→unreachable, marcatore locale
  single/multi(ambiguous), server-reset, appiccicoso riuso/offline/decaduto,
  nome-più-lungo-vince; store roundtrip/upsert/empty. **16/16 verdi.**
- **Turno REALE end-to-end** su prod contro il **device Windows fisico**
  (`PC-ROBERTO`, online): query *«quante righe di codice ci sono in C:\…\Lib\json
  sul PC-ROBERTO»* → risposta taggata **📍 PC-ROBERTO**, 1317 righe fisiche
  (= 1144 codice + 173 vuote della stessa dir, coerente col conteggio device del
  passo W3.3; quel path Windows NON esiste su `.33` Linux → **conferma che è
  girato sul PC**). Routing chat→device DIMOSTRATO.
- **Suite completa**: **3306 passed, 27 skipped**. Tre "falle" tutte
  pre-esistenti ed estranee a R1: (a) `scheduler_v2/…::test_run_now_advances…`
  fallisce SOLO vicino alle 23:59 (asserzione wall-clock-dipendente), (b/c) due
  `test_change_intent_adapters.py::TestAdaptersSmoke` falliscono nella suite piena
  ma **passano 10/10 in isolamento** → flakiness di isolamento fra moduli (state
  leak), non R1. Check mirato R1: `test_target_device + test_agent_server_remote +
  test_placement` = **45/45**. Nessuna regressione da R1.

## 5. Limiti noti / follow-up (da valutare)
1. **`target_device` strutturato non propagato** alla risposta HTTP/UI: il tag
   `📍 <nome>` compare nel testo, ma il campo top-level resta None
   (`_finalize_engine_result` non lo inoltra al turn log). Serve per il chip
   persistente «destinazione corrente» in UI 8770 + Telegram.
2. **Adjunct stripping**: `cleaned_query` rimuove «su <nome>» best-effort; casi
   con la destinazione in mezzo alla frase o punteggiatura anomala vanno
   verificati (rischio: residui che confondono l'args extractor).
3. **Owner-filter multi-utente**: oggi uso TUTTI i device non-revocati
   (`owner='host'`, mono-utente). Con più utenti va filtrato per `owner_user_id`
   == actor. Il mapping actor↔owner è da definire.
4. **Ambiguo → risposta, non form**: R1 risponde con testo che chiede il nome;
   sarebbe meglio un form get_inputs (§2.11) con i candidati come bottoni.
5. **Executor non bundlabili con target**: se il planner sceglie un executor non
   bundlabile (es. read_messages) con un target, l'esecuzione remota fallisce
   onesto (ModuleNotFoundError→placement/errore). Nessuna whitelist di
   eleggibilità: valutare se filtrare a monte.
6. **Falsi positivi nome**: nomi device molto corti/comuni potrebbero abbinarsi
   anche con l'ancora-preposizione (es. device «casa» + «vai sulla casa»). Min
   len 3 + ancora mitiga; valutare un vincolo più forte.
7. **UX appiccicoso-offline**: per regola, un turno senza target su un device
   appiccicoso offline dà «non connesso» anche se l'utente voleva altro. Voluto
   (Roberto), ma valutare se decadere al server dopo N minuti.
8. **i18n del tag** `📍 <nome>`: il nome è dato, ma il marcatore è prosa minima;
   valutare una chiave messaggio.
9. **Performance**: il resolver gira ad OGNI turno (list_devices + get_last_target
   apre sqlite). Trascurabile a 1 device, ma `chat_target_store` apre/crea la
   tabella ad ogni get: memoizzare.
10. **Copertura executor (C7)**: oggi bundlabili solo get_files/compute_files_loc/
    list_dirs. find/read (R2) e mutanti write/move/delete (R3) = fasi successive.

## 6. Come riprodurre / verificare
- Unit: `cd runtime && python3 -m pytest tests/test_target_device.py -q`.
- Turno reale: `POST /agent/turn` (Bearer admin.key) con una query che nomina un
  device appaiato+online e un'operazione file (get_files/compute_files_loc/
  list_dirs). Atteso: `final_text` con `📍 <nome>`, esecuzione sul device.
- Prod-safety: qualunque query SENZA riferimento a un PC deve dare esito e path
  IDENTICI a prima (nessun routing).

## 7. Cosa chiedo all'assessor
- **Correttezza del resolver**: falsi positivi/negativi del match nome+ancora;
  robustezza dello stripping; casi limite IT/EN.
- **Prod-safety**: confermare che il path locale è davvero invariato (nessun
  effetto collaterale del resolver sul turno normale).
- **Sicurezza**: un utente può instradare a un device NON suo? (oggi manca il
  filtro owner → potenziale in multi-utente). Verificare che `choose_placement`
  e il gate connessione non siano aggirabili dalla query.
- **Coerenza §2.8**: gli esiti unreachable/ambiguous sono onesti e non lasciano
  eseguire nulla di nascosto?
- **Priorità dei follow-up** §5.
