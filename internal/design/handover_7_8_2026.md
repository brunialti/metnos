# Consegna operativa — notte 7→8/8/2026

> File VIVO: chi subentra lo legge per primo e lo aggiorna smarcando i punti.
> Narrazione e contesto: memoria `project_session_7_8_2026_sites_goal.md`.
> Stato dell'albero: tutto committato tranne dove scritto qui sotto.

## Come si verifica che il sistema fa il suo mestiere

```bash
./.venv/bin/python internal/tools/e2e_sites_goal.py \
  "accedi a booking.com e mostrami le mie prenotazioni"
```
Atteso: 6 passi, 0 gate, ~45 s, tabella strutturata (destinazione, date, stato).
**Nota**: su sessione NUOVA Booking chiede la verifica in due passaggi, quindi
serve il codice del proprietario; su sessione riusata no.

Banchi di misura riusabili (nello scratchpad di sessione, copiarli se servono):
- `misura_intent.py --dump|--confronta` — 13 query di controllo sull'intent.
- `misura_date_vuote.py` — quante date osservate vengono buttate via.
- `scripts/bench_prefilter_corpus.py` — 233 query, routing bag-of-words.

## Fatto e chiuso (committato + pubblicato)

- [x] **Il fine si giudica per cio' che distingue** — la home non attesta un
      fine fatto solo di nome-del-sito + faccetta; il conteggio dei token non
      e' il criterio. `3a1cd57a`
- [x] **Il verbo d'azione e' rumore per tutti i consumatori** (`_goal_noise`).
- [x] **Le faccette di stato**: fermano il pilota solo se un controllo le porta
      o se la pagina ne dichiara una diversa (`offered_facet_tokens`).
- [x] **Identita' del posto + budget sul progresso** (`goal_place_key`,
      `_MAX_GOAL_STERILE`).
- [x] **`open_sites` non chiude una sessione che ha solo riusato** `cab933ae`
- [x] **Le date visibili non spariscono**: anno corrente + `*` quando il sito
      omette l'anno; vuoto solo se non si deriva nulla. `109153bb`
- [x] **Una richiesta, una risposta**: nomi dei campi nella lingua
      dell'istanza, e la granularita' («data») vince sul ruolo («inizio»)
      nella politica delle date. `92298e0b`
- [x] **La clausola senza fonte propria legge dalla fonte aperta** — sonda
      binaria all'LLM, 1 caso cambiato su 13. `83b2b2f5`
- [x] Doc IT+EN aggiornate e deployate; GitHub pubblico allineato
      (`facb8f4`, `6741326`, `25bc7a6`).
- [x] Suite **5853 verdi** (`--ignore=tests/simulator/web`).

- [x] **La quota piena dice come uscirne** — «chiudi le sessioni web», e il
      test pin-a entrambe le meta' (che il messaggio la dica, e che la frase
      instradi su `delete_sites`). `a3341ea8`, pubblicato `0b4972b`.

## In corso adesso

- (niente in volo: albero pulito)

## Da fare, in ordine di resa

1. [ ] **Il fine come STRUTTURA** — analisi pronta in
   `analysis_goal_come_struttura_7_8.md`; **bloccato sulle 4 decisioni del §7**
   che spettano a Roberto. Raccomandazione: campi piatti incrementali sullo
   stampo di `done_when`, primo campo `ambito` (enum chiusa). Non lo chiude la
   sonda dell'intent: sono due livelli diversi (la sonda decide DA DOVE
   leggere, i campi decidono com'e' fatto il fine dentro `act_sites`).
2. [x] ~~**Ripresa del piano dopo un gate**~~ — **era un fantasma, verificato
   l'8/8 sul codice.** Il meccanismo esiste in due forme, scelte dal contratto
   e non dal nome: `dispatch.py::_inject_gate_resume_if_paused` (riga ~6286)
   riscrive l'`on_complete` del dialogo o come `resume_executor_gate_tail`
   — che PORTA i passi residui con i riferimenti rimappati, quando il ramo
   approvato rilancia lo stesso executor che si e' fermato — o come
   `resume_engine_gate`, che riesegue il turno con il gate pre-approvato.
   Coperto da 9 riscontri in `tests/runtime/engine/test_orchestration.py`
   («replays branch then only tail», «carries tail across repeated gates») e
   da due asserzioni in `tests/runtime/sites/test_sites_security.py`.
   Se un turno vero mostra il contrario, la cosa da consegnare e' **l'id del
   turno**, non il sintomo: senza quello si insegue un fantasma.
3. [ ] **Quota, il resto** — vedi `sites_todo_quota.md`, aggiornato stanotte:
   due dei quattro punti si ridimensionano (sei delle otto condizioni di riuso
   sono AUTORITA', non configurazione; la fragilita' del confine di rete e'
   gia' chiusa). Resta la tensione: chiudere a fine turno ucciderebbe il riuso
   fra turni, e su un sito con 2FA significa richiedere il codice ogni volta.

## Trappole che costano ore se non le sai

- 🚨 **Moduli di confine del sidecar**: toccarne uno — anche solo un commento —
  cambia l'impronta del contratto. Va riavviato
  (`systemctl --user restart metnos-playwright.service`), e non si toccano
  mentre un turno e' in volo, altrimenti ogni turno `sites` muore con
  `sidecar_contract_mismatch`.
- 🚨 **`runtime/extract_entries.py` e' un BUILTIN FIRMATO**: il contratto sta in
  `runtime/builtin_executor_contracts/extract_entries/`. Dopo ogni modifica:
  `./.venv/bin/python runtime/sign.py sign runtime/builtin_executor_contracts/extract_entries`
  + restart. Senza firma il loader lo scarta IN SILENZIO e i turni perdono un
  passo: successo stanotte, letto come regressione.
- 🚨 Una prova dal vivo che lascia un **gate 2FA pendente** cambia il turno
  successivo dell'utente (viene classificato `semantic_mixed` e il Tutor chiede
  «spiegazione o esecuzione?»). Controllare con
  `dialog_pending.list_pending(<attore>, owner_user_id=<id>)`.
- 🚨 Non martellare Booking: lo stealth aggira il CAPTCHA, ma tentativi
  ripetuti di login su sessione nuova fanno scattare la 2FA.
- 🚨 I 12 rossi di `tests/simulator/web` sono **ambientali** (Playwright non
  trova il binario nella HOME isolata dei test), non regressioni.

## Regole di lavoro apprese stanotte

- Commenti e docstring nel codice **in inglese** (documenti di analisi in
  `internal/design/` restano in italiano).
- Un bug individuato si risolve **anche se fuori scope**.
- Prima il dato che prova la causa, poi la correzione: prove differenziali su
  `git worktree` (mai `git stash` in questo albero).
