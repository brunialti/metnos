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
- Quando il goal e' raggiunto, controlli contestuali di continuazione
  (`mostra/carica altro`, pagina successiva e forme tradotte) possono essere
  eseguiti fino a sei volte. Ogni passo richiede contenuto nuovo; controllo
  esaurito, contenuto ripetuto o budget chiudono il ciclo. Le pagine navigate
  vengono aggregate nella lettura finale senza duplicati.
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
  immediatamente prima del fill.
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

## Conseguenze

Il planner non vede segreti, DOM grezzo o selettori e non puo' autorizzare da
solo un effetto outbound. CAPTCHA e 2FA non risolvibili vengono ceduti
all'utente; TOTP opt-in e fattore email strettamente correlato restano nel
broker. Applicazioni
multi-host richiedono un'approvazione esplicita. Per metriche/API stabili resta
preferibile un backend first-party rispetto all'automazione della dashboard.

## Verifica

- `runtime/tests/test_sites_security.py`: owner, origine, redazione, audit,
  resolver, consenso privacy, off-viewport, username-first/continue, TOTP,
  2FA-push, goal post-login, taint, gate/token e allowlist.
- `runtime/tests/test_factor_resolvers.py`: binding mailbox esatto, cursore UID
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
