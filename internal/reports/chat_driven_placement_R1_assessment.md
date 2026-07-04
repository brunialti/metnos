> **STATO 2026-07-04 — STORICO/AGGIORNATO (D6 review)**: le 5 osservazioni
> dell'assessment (sticky-offline, owner-filter, fallback silenzioso, threading
> upload, nomi duplicati) sono **FIXATE** (commit `5e6e828`). Una review esterna
> successiva (`composer_engine_remote_review_2026-07-04.md`) le ha confermate
> chiuse e ha aggiunto F1-F10: F1/F2/F3/F5 fixati (`f16e0ef`), il resto
> tracciato. Questo documento resta come assessment R1 originale.

# Chat-driven placement — R1: report per assessment esterno

**Data:** 2026-07-04 · **Branch:** `session/detection-lexicon-i18n` (non pushato) ·
**Autore:** agente (sessione Opus) · **Scopo:** sottoporre R1 a un agente esterno
per un **assessment critico + controllo di correttezza**.

## 0. Obiettivo
Metnos esegue gli executor sul server `.33`; da poco anche su un **PC appaiato**
(client Rust). Obiettivo («è lo scopo di tutto», Roberto): **parlare alla chat**
e far eseguire l'operazione sul PC giusto — dalla conversazione naturale, non da
un endpoint di test. Questo report copre **R1** (il ponte chat→placement). NON
copre R2/R3 (ampliamento executor sul device).

## 1. Cosa esisteva già (verificato)
- `placement.choose_placement` (funzione PURA, testata): **L1.c** instrada per
  nome device (`intent["device"]`) anche executor `scope="any"`; **L1.d** gate
  connessione `is_available` (battito < 60s, non revocato) → `ERR_DEVICE_UNREACHABLE`;
  gate piattaforma. Routing e controllo connessione c'erano già.
- `remote_exec.invoke_remote`: consegna firmata alla coda del device.
- **MA** l'hook in `agent_runtime.invoke_executor` era morto (gated `scope=="device"`,
  `intent=None`).
- **C7** (fatto in sessione): shim del device spedisce `path_alias` → oggi sono
  impacchettabili/eseguibili sul device: `get_files`, `compute_files_loc`,
  `list_dirs`.

## 2. Architettura di R1 (come costruito)
Risoluzione **una volta sola** a livello `agent_runtime.run_turn`, PRIMA di
fast_path e engine, così ENTRAMBI i path instradano al device.

1. **`runtime/target_device.py`** — resolver puro `resolve_target(query, devices,
   last_target, is_available)` → SERVER | device_id | `unreachable` | `ambiguous`
   + `cleaned_query` (adjunct di destinazione rimosso). Segnali (deterministici,
   §7.9, no LLM/sinonimi):
   - **nome device** abbinato ai nomi REALI, SOLO se preceduto da preposizione
     locativa (`su|sul|…|on`) — il nome nudo NON instrada («foto di casa» col
     device «casa» → no). Match più lungo vince.
   - **marcatore locale** («su questo pc / sul mio pc / localmente / on this pc /
     locally») → device dell'utente (uno → quello; più → `ambiguous`).
   - **marcatore server** → `.33`.
   - nessun segnale → `last_target` appiccicoso (se online), poi server.
   - controllo connessione SEMPRE sul target risolto (anche appiccicoso):
     offline → `unreachable`.
   - Inoltre `DEVICE_ELIGIBLE = {get_files, compute_files_loc, list_dirs}` (chiusura
     shim C7): un target si applica SOLO a questi.
2. **`runtime/chat_target_store.py`** — destinazione appiccicosa per `sender_id`
   (sqlite `chat_target.db`, co-locato con `devices.db`).
3. **`runtime/agent_runtime.py`**:
   - `run_turn`: risolve il target (unreachable/ambiguous → esito onesto SUBITO,
     nessuna esecuzione); passa `_query_for_planning` (ripulita) + `_placement_target`
     (nome) sia a `try_fast_path` sia a `_try_engine_v2`; aggiorna l'appiccicoso
     solo su riferimento ESPLICITO.
   - `invoke_executor(..., target_device=None)`: instrada al device SOLO se
     `scope=="device"` OPPURE (`target_device` presente **E** executor in
     `DEVICE_ELIGIBLE`). Un target su executor NON impacchettabile (es. get_now
     con appiccicoso a un PC) → **gira in locale, non fallisce**. Sulla consegna
     remota marca il result con `_ran_on_device=<nome>`.
   - **Tag NON ottimistico**: `_apply_device_tag` (engine, in `_finalize_engine_result`)
     e il ramo fast_path impostano `log.target_device` + antepongono `📍<nome>`
     SOLO se uno step è girato DAVVERO sul device (marker `_ran_on_device`). Il
     campo è esposto nella risposta HTTP `_turn_json`.

## 3. Decisioni di design (con l'utente)
- Identificazione PC = **NOME device** (dato) + marcatore locale; **non** l'IP di
  connessione (sul tunnel = `127.0.0.1`, su Telegram nullo); **non** LLM/sinonimi.
- Default = **ultima destinazione appiccicosa**; primo turno = server; reset «sul server».
- Controllo connessione SEMPRE → «device non connesso», mai esecuzione silenziosa
  altrove (§2.8).
- Un target si applica SOLO a executor impacchettabili al device (§eleggibilità).
- Prod invariata quando nessun PC è nominato/appiccicato.

## 4. Validazione (turni REALI su prod, device Windows fisico `PC-ROBERTO`)
- **Unit** `test_target_device.py` (21): resolver (nome/locale/server/appiccicoso/
  offline/ambiguo/nome-più-lungo/falsi-positivi) + `references_device` + store.
  **21/21.**
- **Device file turn**: «quante righe di codice in C:\…\Lib\json **sul PC-ROBERTO**»
  → `target_device="PC-ROBERTO"`, tag `📍 PC-ROBERTO`, risposta «1317» (dir Windows,
  inesistente su .33 → girato SUL PC), `final_kind=answer`, **nessun legacy**.
- **Regressione critica risolta**: «che ore sono» con appiccicoso=PC → gira in
  LOCALE («Sono le …»), `target_device=None`, nessun tag (get_now non
  impacchettabile → non instradato, non fallisce).
- **Suite completa**: **3311 passed, 27 skipped**. Le uniche 2 falle
  (`test_change_intent_adapters::TestAdaptersSmoke`) passano **10/10 in
  isolamento** → flakiness pre-esistente di isolamento fra moduli, non R1 (più il
  test scheduler wall-clock 23:59, deselezionato). Nessuna regressione dal
  refactor.

## 5. 🚩 LIMITE PRINCIPALE — fallthrough al PLANNER LEGACY
Osservato dal vivo: alcuni turni-device NON raggiungono l'engine (`_try_engine_v2`
ritorna None) e cadono nel **PLANNER LEGACY** (~3300 LOC, in via di rimozione —
vedi `project_legacy_planner_removal_deadline`). Il legacy NON conosce il
placement → produce un piano degenere (es. `compute_files_loc(machine_name="PC-ROBERTO")`,
arg inventato) e ignora il device. Il fallthrough è **intermittente** (stessa
query a volte engine a volte legacy; ~4s senza attività LLM prima del legacy →
sospetto intent-extraction/timing, spesso subito dopo un restart). **È
pre-esistente e ortogonale a R1**, ma ne mina la reliability: finché il legacy
esiste, un turno-device che ci finisce bypassa tutto. **Priorità assessor**:
capire perché `_try_engine_v2` declina per queste query e se va accelerata la
rimozione del legacy. La mitigazione di R1 (risoluzione anche sul path fast_path)
copre solo le query che matchano un pattern fast_path.

## 6. Altri follow-up (da valutare)
1. **Owner-filter multi-utente**: oggi si usano tutti i device non-revocati
   (`owner='host'`, mono-utente). In multi-utente FILTRARE per `owner_user_id ==
   actor` — altrimenti un utente potrebbe nominare il device di un altro.
   **Sicurezza: fare prima di aprire a più utenti.**
2. **`DEVICE_ELIGIBLE` hardcoded** (set di 3): renderlo manifest-driven
   (`[placement] device_ok`) o derivato dalla chiusura shim, quando C7 R2/R3
   amplia.
3. **Ambiguo → risposta testuale**, non form get_inputs a bottoni (§2.11).
4. **SSE path** (`_turn_sse`): il campo `target_device` è aggiunto a `_turn_json`;
   verificare/aggiungere anche all'evento finale SSE e agli adapter Telegram.
5. **Adjunct stripping** best-effort: casi con la destinazione in mezzo alla frase
   / punteggiatura anomala.
6. **UX appiccicoso**: un turno-file senza target su un device appiccicoso offline
   dà «non connesso»; valutare decadimento al server dopo N minuti.
7. **Performance**: `resolve_target` gira ad ogni turno (`list_devices` +
   `get_last_target` aprono sqlite); memoizzare. Trascurabile a 1 device.
8. **`references_device`** (in target_device.py) è ora inutilizzato dopo il
   refactor (il guard skip-fast_path è stato sostituito dalla risoluzione unica):
   rimuovere o riusare.

## 7. Come riprodurre / verificare
- Unit: `cd runtime && python3 -m pytest tests/test_target_device.py -q`.
- Turno reale: `POST /agent/turn` (Bearer admin.key) con una query che nomina un
  device appaiato+online e un'operazione FILE (get_files/compute_files_loc/
  list_dirs). Atteso: `target_device=<nome>`, `final_message` con `📍<nome>`.
- Prod-safety: qualunque query SENZA riferimento a un PC → esito e path IDENTICI a
  prima. «che ore sono» con appiccicoso=PC → LOCALE (get_now non eleggibile).

## 8. Cosa chiedo all'assessor
- **Correttezza resolver**: falsi positivi/negativi nome+ancora; robustezza dello
  stripping; casi IT/EN.
- **Prod-safety**: confermare path locale invariato (nessun effetto del resolver
  sul turno normale); overhead per-turno accettabile.
- **Sicurezza**: instradamento a un device NON proprio (manca il filtro owner);
  `choose_placement` + gate connessione non aggirabili dalla query.
- **§2.8**: unreachable/ambiguous onesti, nessuna esecuzione nascosta; tag mai
  ottimistico (solo su `_ran_on_device` reale).
- **Legacy fallthrough (§5)**: causa del declino di `_try_engine_v2` e impatto.
- **Priorità dei follow-up** §6.
