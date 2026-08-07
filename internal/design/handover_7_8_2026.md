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

1. [ ] **Il fine come STRUTTURA** — analisi in
   `analysis_goal_come_struttura_7_8.md`. **NON e' piu' bloccato: Roberto ha
   deciso l'8/8**, tutte e quattro come raccomandato:
   1. **campi piatti, uno per volta** (stampo di `done_when`), non un oggetto
      `goal` unico;
   2. **`ambito` = enum chiusa `personale` | `pubblico`** (la grammatica GBNF
      vincola le enum alla produzione, ed e' estensibile a un terzo ambito);
   3. i **nomi di argomento sono liberi**: §2.2 governa i nomi di executor, non
      gli argomenti — nessuna escalation di vocabolario;
   4. **solo `act_sites`**, non `login_sites` (il cui fine implicito non ha
      varianti: sarebbe cerimonia senza informazione).

   **Stato: il primo campo `ambito` E' FATTO** (`2eacf43a`, pubblicato
   `7f189a1`, suite 5871 verdi). Un solo predicato
   `action_resolver.goal_is_personal(target, scope)` risponde alla domanda, e i
   tre punti che consultavano il marcatore di possesso ci passano attraverso;
   lo scope viaggia come `done_when` (executor → client → server → `op_act` →
   `entry["goal_scope"]`); nel manifest la regola «di' mie/miei» e' diventata
   il suo contrario. 6 test in `tests/runtime/sites/test_ambito_dichiarato.py`.
   **Resta da fare la MISURA dal vivo** che lo promuove o lo boccia (sotto):
   finora e' verificato solo a unita'.

   **Ricetta seguita** (utile se si aggiunge un secondo campo):
   - `executors/act_sites/manifest.toml`: nuovo arg enum + descrizione a
     capitoli §2.5 (it/en), poi **re-sign** `executors/act_sites` (§7.10);
   - `act_sites.py` lo legge e lo inoltra; `session_client.session_act` e
     l'handler in `playwright_sidecar/server.py` lo trasportano; `op_act` lo
     posa su `entry["goal_scope"]` (stesso stampo di `goal_done_when`);
   - `action_resolver`: un solo predicato `_goal_is_personal(target, scope)`
     — **lo scope dichiarato vince, il marcatore di possesso resta il
     ripiego** — e da li' i tre punti che oggi chiamano `_is_personal_goal`:
     `goal_candidate_is_admissible`, `choose_goal_candidate` (ripiego
     strutturale sull'area personale) e `page_satisfies_goal` (guardia home);
   - 🚨 moduli di confine toccati → **riavviare il sidecar**.
   - ✅ **Verificato che il campo e' PRODUCIBILE**: la grammatica lo vincola
     alla produzione, `propActSitesAmbito ::= "ambito" colon ("personale" |
     "pubblico")` — il modello non puo' scrivere altro. Era l'assunto della
     decisione 2 e regge.
   - ⏳ **MISURA DAL VIVO ANCORA DA FARE** (analisi §8), l'unica cosa che
     promuove o boccia il campo: un fine su area personale che **non contiene
     marcatori di possesso**. Comando:
     `./.venv/bin/python internal/tools/e2e_sites_goal.py "vai su booking.com e mostrami le prenotazioni"`
     (senza «mie»). Che cosa guardare, in ordine: (1) il piano dichiara
     `ambito="personale"` fra gli args di `act_sites`? (2) l'audit mostra il
     passo sul menu dell'account? (3) arriva alla pagina e consegna i dati?
     Se (1) e' no, il problema e' la descrizione nel manifest, non il codice.
     **Blocco pratico**: su sessione nuova Booking chiede la verifica in due
     passaggi, quindi serve il codice del proprietario. Alternativa senza
     attesa: un sito con area personale ma senza 2FA.

     **Tentativo offline dell'8/8, da NON ripetere cosi'**: chiamando
     direttamente `proposer.propose(...)` con un `Intent` costruito a mano, il
     piano che torna e' `open_sites → login_sites → read_sites →
     describe_entries`, **senza `act_sites`** — e quindi senza nessun posto
     dove `ambito` possa comparire. Non e' una bocciatura del campo: e' un
     banco infedele, perche' in produzione l'intento arriva dall'estrattore e
     il motore aggiunge i precursori del dominio, e li' `act_sites` c'e'
     (turni veri con 6-8 passi). Se pero' anche il turno VERO senza possessivo
     salta `act_sites`, allora il difetto e' a monte del campo: si sta leggendo
     la home invece di navigare, ed e' quello da guardare per primo.
     Lo script sta in `.../scratchpad/misura_ambito.py`; per renderlo fedele
     servirebbe passare dall'estrattore d'intento vero, non da un `Intent`
     scritto a mano.
   - Se `ambito` non rende, ci si ferma: gli altri tre campi (`filtro`,
     `portata`, `cosa`) non si fanno per simmetria.

   Nota: **non lo chiude la sonda dell'intent** — sono due livelli diversi (la
   sonda decide DA DOVE leggere, i campi decidono com'e' fatto il fine dentro
   `act_sites`).
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

## Rilettura avversariale del proprio lavoro — resa misurata

Rileggendo i commit della notte con una domanda sola («quale proprieta' rompe
questo codice senza accorgersene?») sono usciti **tre difetti veri**, tutti
della stessa famiglia — un segnale debole che scavalca uno forte, o un
accoppiamento dimenticato:

1. `ambito="pubblico"` cancellava il possessivo dell'utente e spegneva tre
   guardie insieme (`834352b9`). Il campo ora puo' solo AGGIUNGERE il senso
   personale.
2. Il ramo del MODELLO applicava la guardia dei candidati senza lo scope
   dichiarato, quello deterministico si': stessa domanda, due risposte
   (`475cb028`).
3. La sonda dell'intento riscriveva l'intento primario senza motivo, dove la
   normalizzazione delle clausole puo' differire dal livello superiore
   (`475cb028`).

Vale la pena rifarlo dopo ogni tranche: e' costato venti minuti e ha trovato
piu' difetti dell'intera suite, che era verde su tutti e tre.

Sospetto NON confermato, lasciato scritto perche' non si ricerchi due volte:
`goal_scope` e `goal_done_when` restano posati sull'`entry` di una sessione
riusabile fra turni, ma `op_act` li riscrive a OGNI invocazione (anche a
vuoto), quindi non c'e' valore stantio. Se un giorno un percorso li impostasse
solo dentro un ramo condizionale, quella garanzia salta.

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
