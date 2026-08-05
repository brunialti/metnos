---
id: 0188
title: Interazione web autenticata sicura tramite session broker e gate sul target DOM
date: 2026-07-11
status: accepted
area: runtime
related: [0071, 0082, 0090, 0125, 0159, 0177]
---

# 0188 — Interazione web autenticata sicura

## Contesto

Leggere una pagina pubblica non richiede stato; accedere a un portale e agire
richiede cookie, credenziali, navigazione e protezione dal prompt injection
indiretto. Consegnare segreti o selettori al planner renderebbe il modello
autorita' sia sul valore sia sulla sua destinazione.

## Decisione

Il dominio canonico `sites` usa quattro executor vettoriali host-only:
`open_sites`, `login_sites`, `read_sites`, `act_sites`; `delete_sites` e' il
kill-switch. Gli executor sono client sottili del sidecar Playwright su `:8771`.

- Il broker mantiene contesti nominati per owner, TTL, quota e lock; ogni
  operazione verifica owner e sessione. Approvazioni e fattori in attesa sono
  checkpoint distinti e sospendono entrambi il TTL entro un limite assoluto.
- Solo il broker legge il vault. Il form action deve avere origine esatta e
  viene ricontrollato prima del fill e del submit. Username e password sono
  redatti; gli screenshot usano overlay e maschera nativa Playwright.
- Le credenziali possono essere espresse naturalmente in chat: il runtime le
  estrae prima del planner tramite lessico traducibile e accetta sia lo schema
  vault canonico sia il formato CLI storico. La CLI interattiva resta fallback.
- La rete e' exact-host: HTTP(S) soltanto, service worker bloccati, WebRTC
  disabilitato e WebSocket guardato. Host aggiuntivi richiedono `get_approval`.
- `act_sites` accetta linguaggio naturale ma non selettori. Il broker riduce la
  frase a `goto|click|fill|submit|wait|search`, enumera target accessibili e usa
  un modello locale solo per scegliere fra candidati broker-owned.
- Un executor puo' diventare intelligente in modo drop-in senza modificare il
  planner: conserva input e output pubblici e risolve internamente gli stati
  intermedi entro un budget chiuso. `login_sites` applica
  `observe -> propose -> verify -> gate -> execute -> observe` per passare da
  landing a consenso privacy opzionale, ingresso login, username opzionale,
  continuazione, password, 2FA e verifica esito. Prima attende le SPA lente e
  prova resolver deterministici; puo' portare in viewport un target renderizzato
  ma non visibile. Il modello e' un solo fallback pre-fill.
  Il prompt espone `goal`, `state`, `observed`, `history`, `constraints`; il
  modello puo' riferire solo un `candidate_id` enumerato dal broker.
  `observed` e' dato non fidato. LLM/VLM non emettono selettori, credenziali,
  URL o autorizzazioni e non vengono invocati dopo il primo fill credenziale.
- UI custom vengono risolte anche tramite geometria/topmost e nodi foglia
  `cursor:pointer`. Un reveal e' seguito da nuova osservazione; target instabili
  sono ripianificati al massimo due volte, poi falliscono chiusi.
- Dopo il login, `act_sites(search)` conserva l'obiettivo semantico e puo'
  attraversare fino a quattro rami di menu. I vincoli numerici (per esempio
  l'anno) restano filtri finali e non penalizzano una voce generale come
  "Fatture". Ogni click e' riosservato; un resolver testuale locale vede solo
  ID, ruoli e nomi accessibili. La prima azione sensibile apre un gate batch;
  i passi deterministici successivi, sullo stesso host, riusano quel consenso.
- Quando il planner non emette una navigazione necessaria a una richiesta di
  record, il runtime recluta `act_sites` in modalita' goal interna. Un solo
  reducer locale bounded puo' restituire esclusivamente una frase breve ed
  estrattiva composta da parole della query; il broker la tratta come target
  tipizzato della normale primitiva `search`. URL, selettori, azioni e termini
  inventati vengono rifiutati. I resolver procedurali usano `think=false`: il
  budget piccolo serve al JSON validato, non a ragionamento non osservabile.
  L'input/output pubblico dell'executor non cambia.
- Il login non puo' usare la sola navigazione URL come postcondizione. Il broker
  ricontrolla strutturalmente il frame top-level e richiede che l'assenza di una
  superficie di autenticazione resti stabile per osservazioni consecutive;
  errori di osservazione e remount SPA transitori non producono successo. Il
  form ancora presente nel primo frame post-submit non termina l'osservazione:
  azzera la sequenza positiva e resta sotto controllo fino al budget.
- La diagnostica di ogni login non completato usa screenshot fail-closed. Oltre
  a password, OTP e campi identita' standard, la redazione copre indirizzi email
  riecheggiati nei nodi testuali tramite il loro formato strutturale. L'overlay
  copre il solo intervallo corrispondente, non l'intero nodo testuale, senza
  leggere valori dal vault o usare lessico linguistico.
- Se il primo goal dopo un login verificato non osserva alcun candidato, il
  broker puo' tornare una sola volta all'URL di ingresso same-host conservato in
  memoria e rieseguire il resolver normale. Il reset e' ammesso soltanto prima
  di qualsiasi passo del goal e con un mandato credenziale gia' valido per la
  navigazione; il tentativo viene marcato prima dell'I/O. Non dipende da testo
  d'errore, vendor, selettori o URL indovinati e non puo' diventare un retry
  loop.
- Quando il goal e' raggiunto, controlli contestuali di continuazione
  (`mostra/carica altro`, pagina successiva e forme tradotte) possono essere
  eseguiti fino a sei volte. Ogni passo richiede contenuto nuovo; controllo
  esaurito, contenuto ripetuto o budget chiudono il ciclo. Le pagine navigate
  vengono aggregate nella lettura finale senza duplicati.
- Una richiesta di record dalla pagina viene normalizzata deterministicamente
  in `read_sites -> extract_entries -> describe_entries`. `extract_entries`
  usa i campi espliciti quando presenti; altrimenti inferisce una sola volta un
  piccolo schema bounded dallo scopo e dal testo tramite modello locale. Il
  router non contiene campi, tipi di record o nomi di sito. Un risultato vuoto
  resta `entries=[]` e viene presentato onestamente, senza ripescare un blob
  precedente.
- Redirect, documenti bloccati e popup estendono la rete solo con gate esatto e
  token one-shot. Widget osservati non ampliano l'allowlist se la pagina e' gia'
  interagibile; un popup unico e consentito diventa la pagina attiva.
- Gli host negati vengono osservati con provenienza strutturata bounded (tipi,
  main-frame, navigazione, top/parent host — mai URL con token). Il fallback
  risorse IMPLICITO dopo `selector_missing` propone soltanto host rilevanti per
  il mandato: root configurato o suo sottodominio, host top-level corrente,
  origine credenziale delegata nel binding. Un document di subframe terzo
  (adv/telemetria, hostname generato) non diventa mai un gate — e quindi mai un
  mandato persistente — per sola discovery; per host terzi serve evidenza
  causale esatta (target DOM risolto, popup unico, redirect top-level), che
  passa da `required_hosts` e mantiene il gate one-shot. Nessuna denylist di
  vendor. Ogni passo goal/login eseguito viene auditato con metadati sicuri:
  kind, ruolo e nome accessibile bounded del target risolto, confidence,
  `model_selected`, URL scrubbed prima/dopo, blocco pre-dispatch (13/7,
  handoff Booking; E2E `727f9ba37b3d4732`).
- La sensibilita' deriva dal target risolto: navigazione, submit, POST,
  download e credenziali richiedono gate; dopo contenuto web ingerito si
  applica il tainted-turn. Il gate e' batch e porta screenshot redatto.
- L'approvazione ripresenta un token opaco in-memory; DOM o pagina cambiati
  invalidano il piano. Il rifiuto chiude la sessione. `sites` resta fuori L0/L1.
- Una transizione deterministica verso il login, gia' confinata allo stesso
  host, usa l'autorizzazione implicita nell'intento e non apre un dialogo.
  Host nuovi, selezione VLM o origine credenziale delegata mantengono il gate.
  Se l'origine del form differisce dal binding vault, il broker non copia il
  binding: richiede un token one-shot sulla coppia esatta e ricontrolla il form
  immediatamente prima del fill. L'ALIAS `www` (una sola label) del root del
  vault e' pero' la stessa origine di login e viene accettato senza gate: il
  form email-first servito su `www.<root>` mentre il vault e' `<root>` (pattern
  comune) non apre un dialogo. Nessun ALTRO sottodominio (`login.<root>`,
  host terzi) e' ripiegato: restano origini distinte e mantengono il token
  one-shot. Speculare al fallback record-name `www.host -> host` (14/7,
  turn 04e74199 Amazon).
- I codici e le chiavi `MSG_SITES_RC_*` hanno nomi canonici inglesi; i testi
  sono IT+EN nel DB i18n e nel seed d'installazione.
- I secondi fattori hanno un resolver interno a canali, invisibile al planner.
  `email` e' il primo canale: viene usato soltanto con mandato `sites.read`,
  identita' esattamente uguale a una singola mailbox configurata e messaggio
  nuovo rispetto al cursore UID catturato prima del submit. Mittente/contenuto
  devono essere pertinenti al dominio emittente; ambiguita', timeout o I/O
  fallita cedono il controllo senza scegliere un codice. Nessun brand e'
  hardcoded. CAPTCHA, OTP non risolto e conferma push producono un handoff
  esplicito con screenshot redatto e sessione ancora aperta. Il TOTP puo'
  essere calcolato nel broker solo come opt-in per-dominio se `totp_secret` e'
  nel vault; origine e campo sono ricontrollati e il codice non entra in
  screenshot, log o result.
- Per ogni modalita' si applica ADR 0190: `sites.read` vive cifrato nel binding
  credenziale ed e' il default anche per le query interattive. La query puo'
  restringerlo; un ampliamento richiede consenso one-shot e non persiste. Nei
  task viene inoltre intersecato con l'envelope esatto; un fire fuori mandato
  fallisce con `mandate_scope_exceeded` e non crea un dialogo che nessun utente
  potrebbe risolvere in tempo reale.
- Il resolver del mandato condivide il solo alias di binding gia' ammesso
  dall'iniettore: un profilo audit rooted su `www.host` puo' usare la credenziale
  scoped di `host`. Una credenziale esatta ha precedenza e nessun altro
  sottodominio, fratello o suffisso eredita autorita'. La regola vale allo stesso
  modo per query interattive e task.

## Conseguenze

Il planner non vede segreti, DOM grezzo o selettori e non puo' autorizzare da
solo un effetto outbound. CAPTCHA e 2FA non risolvibili vengono ceduti
all'utente; TOTP opt-in e fattore email strettamente correlato restano nel
broker. Applicazioni
multi-host richiedono un'approvazione esplicita. Per metriche/API stabili resta
preferibile un backend first-party rispetto all'automazione della dashboard.

## Verifica

- Il Capability Registry canonico distingue `network:sites`,
  `auth.password_storage` e `drive:permissions`; i cinque executor `*_sites`
  dichiarano collocazione `server`. Verifica 19/7/2026: cluster dominio
  360 passati, 2 skip dichiarati, ripetuto in due cicli; simulatore Chromium
  5/5 e sidecar live 3/3.

- `tests/runtime/sites/test_sites_security.py`: owner, origine, redazione, audit,
  resolver, consenso privacy, off-viewport, username-first/continue, TOTP,
  2FA-push, goal post-login, recupero landing una-tantum, taint, gate/token e
  allowlist.
- `tests/runtime/sites/test_sites_structured_extraction.py`: inserzione idempotente
  della catena tipizzata, inferenza schema bounded, piping, zero record e
  assenza di costanti per sito/campo.
- `tests/runtime/sites/test_sites_open_resource_relevance.py`: i documenti di
  subframe terzi restano bloccati senza promuoversi a gate; le navigazioni
  top-level conservano il gate esatto.
- `tests/runtime/engine/test_factor_resolvers.py`: binding mailbox esatto, cursore UID
  pre-submit, Junk standard, issuer generico, budget/cancellazione, OTP
  segmentato, timeout riclassificato, resume e TTL del factor checkpoint.
- Runner executor: 313/313.
- E2E broker: reveal menu custom, target React, gate esatto
  `login.telepass.com`, replay, adozione popup e URL login finale verificato.
- E2E Chromium login drop-in: consenso rifiutato, login fuori viewport, landing
  e form su host distinti, due gate one-shot, username-first, continuazione,
  password, cookie verificato, navigazione dashboard a fatture e zero segreti.
- Turno reale `3c86f35151104ff0`: callback verificata
  `open_sites -> login_sites -> act_sites(search) -> read_sites -> final_answer`.
