---
id: 0191
title: Robot sites intelligente su mandato — tre superfici, due procedure ostruzioni, persistenza adattiva
date: 2026-07-14
status: proposed
area: runtime
related: [0125, 0187, 0188, 0189, 0190]
---

> **Stato: PROPOSED (post-review adversarial @1+@2+@3+@3-bis, 14/7).** L'analisi v4
> e' ora **deterministica**: budget ostruzioni (valori §8.4), cooldown eseguibile
> (formula+schema+upsert §7), tabella precedenza codici (§6.1) e contratto
> `BrowserSurface` (§1.1) hanno valori/algoritmi espliciti — nessuna decisione
> autonoma residua. L'ADR passa ad `accepted` solo dopo un giro di conferma.
> **Due commitment non trattabili (Roberto, 14/7)**: (C1) **estensione companion**
> = terza superficie onesta (browser reale utente), **non piu' esclusa**; (C2)
> **stealth attivabile da UI, effetto immediato senza restart/admin**, e
> **costruito per essere esteso** (registro di tecniche).
> **Estensione Roberto, 15/7**: (C3) pannello Website browsing con superficie
> `headless|side`; `side` = Chromium completo grafico pilotato, componibile con
> ciascuna tecnica stealth senza profili predefiniti.
> Confine sull'occultamento: **fuori dal DEFAULT** (il default e' onesto, nativo);
> disponibile SOLO come **modalita' opt-in ESPLICITAMENTE sperimentale/anti-
> rilevamento** (non «lecita»), a rischio del **proprietario** (pref per-turno, non
> globale). La classe di siti che blocca via fingerprint headless e' **NON
> SUPPORTATA in modalita' headless** (la superficie `extension` e' la risposta
> onesta, opt-in).
> Analisi implementabile: `internal/design/analysis_intelligent_sites_robot_2026-07-14.md` (v4 autonoma).

# 0191 — Robot sites intelligente su mandato

## Contesto

Leggere contenuto autenticato da un sito (carrello, prenotazioni, dispositivi
di un router) richiede login, navigazione a un obiettivo e estrazione di record.
I portali moderni oppongono resistenza su piu' livelli: overlay (cookie, promo
«solo per te»), form di login variabili (username-first, alias email/telefono,
SPA lente), e anti-bot.

Evidenza empirica (14/7, questa sessione, simulatore + turni reali):

- Il broker headless (ADR 0125/0188) **supera i siti lenienti**: FASTGate (router
  LAN) e Booking arrivano al login e oltre.
- Viene invece **bloccato dall'anti-bot multi-segnale determinato** (Amazon:
  `navigator.webdriver` + fingerprint headless → pagina vuota servita a monte;
  dopo pochi tentativi ravvicinati il punteggio di rischio sale e Amazon serve
  vuoto a prescindere, con rischio di lock dell'account).
- Emergono **due classi di ostacolo distinte**:
  1. **OSTACOLI di interazione** (overlay, login variabili, navigazione goal):
     risolvibili con intelligenza di navigazione e persistenza.
  2. **FINGERPRINT headless** (rilevamento a monte del rendering): la navigazione
     non tocca palla — il robot non riceve nemmeno una pagina. Due leve oneste,
     entrambe opt-in (§Decisione): l'occultamento (stealth, headless mascherato,
     con limiti tecnici reali) e la superficie `extension` (browser reale
     dell'utente, che il fingerprinting non distingue perche' **e'** reale).

Alternative valutate e **SCARTATE** per il dominio `sites`:
- **API ufficiali** (Product Advertising, Business PunchOut, LWA): ad-hoc e
  per-provider, non generalizzano.
- **Email / dati derivati**: workaround extra-dominio (dominio `messages`), fuori
  scope qui.

Alternativa **RIAMMESSA come terza superficie** (commitment C1, 14/7):
- **Superficie browser-utente** (estensione companion): browser **reale**
  dell'utente, onesto, per la classe FINGERPRINT che l'headless non copre. Non un
  canale extra ne' un executor dedicato: e' la terza implementazione di
  `BrowserSurface` (§Decisione), dietro gli **stessi** executor.

## Decisione

- **Logica unica, TRE superfici** (§1 analisi v4, rev. side browser 15/7): la macchina intelligente e'
  **una sola** (osserva→decide→agisci); cambia solo la **superficie browser** su
  cui gira, scelta da un **resolver** (config/pref, non intento — pattern
  `backend_resolver`), dietro gli **stessi** executor `open/login/read/act_sites`
  (drop-in, planner-invisibile):
  1. **`headless`** (DEFAULT) — robot server onesto, non mascherato;
  2. **`side`** (opt-in) — Chromium completo grafico sul server, pilotato da
     Playwright; richiede un display disponibile e non ripiega su headless;
  3. **`extension`** (opt-in, commitment C1) — browser **reale** dell'utente via
     estensione companion, onesto, su device (canale Fase 7). Astrazione comune
     `BrowserSurface` (open/enumerate/fill/click/read/screenshot/close): il seam
     va introdotto SUBITO, l'impianto `extension` e' fase dedicata successiva.
  Le tecniche stealth (§Occultamento) sono un asse ortogonale selezionabile per
  entry sulle due superfici Playwright (`headless` e `side`), non una superficie
  o un profilo. Nessun canale **API/email** per `sites`. Per la classe FINGERPRINT
  `side` evita il solo headless-shell ma resta automazione; `extension` e' la via
  onesta sul browser dell'utente.
- **Mandato del proprietario**: l'executor agisce sul mandato del proprietario
  delle credenziali (ADR 0190). **«Onesto non vuol dire scemo»**: finche' e'
  **legalmente possibile**, PERSISTE e si adatta per fare cio' che gli e' chiesto;
  non si arrende al primo ostacolo. L'onesta' (§2.8) vieta di DICHIARARE un esito
  falso, non di INSISTERE su un mandato legittimo.
- **Gestore ostruzioni: DUE procedure distinte + budget distinti** (§8 analisi
  v4, post-review @3 #12 — NON un gestore unico a budget condiviso):
  1. **Privacy/cookie** (proattiva, stretta, **solo non-navigante**): classe nota
     sicura, target di rifiuto esatto tradotto; se l'unico controllo naviga/fa
     submit → passa dal piano firmato + gate, non dalla dismissione.
  2. **Ostruzione generica** (reattiva): chiusura di un modal ammessa **SOLO** su
     un target NOTO **geometricamente occluso**, con postcondizione implementabile
     (renderizzato + non-topmost via hit-test + controllo di chiusura nello stesso
     modal + firma DOM stabile) — mai su nudo `selector_missing`.
  Budget **per-procedura** + **tetto assoluto di sessione**, applicati
  uniformemente nelle fasi login/goal/azione senza fondere le due procedure. La
  dismissione resta quella sicura di ADR 0188 (VIETATI `input[type=submit]` e link
  `javascript:`/attivi nel locator).
- **Persistenza adattiva bounded**: prova reveal/percorsi ALTERNATIVI, non ritenta
  identico; budget di passi/dismissioni/tempo; modello solo entro azioni enumerate
  broker-owned.
- **Occultamento: OPT-IN, DEFAULT OFF, per-turno, esteso da UI senza restart**
  (post-adversarial @2 #1-#4 + @3 #1/#2 + commitment C2). Il DEFAULT e' **onesto e
  nativo**: UA nativo (nessun override — spoofing ≠ igiene), `navigator.webdriver`
  nativo (NIENTE launch-arg `AutomationControlled`), attese su **postcondizioni**
  (niente ritardi «umani»).
  - **Interruttore master = user-pref `sites_stealth`** (`on|off`, vocab CHIUSO,
    ADR 0187), letto per-turno e valido SOLO per le sessioni owner-bound. Sotto
    il master, ogni entry del registro ha una pref `on|off` indipendente generata
    dalla stessa entry (`sites_stealth_webdriver`, `sites_stealth_user_agent`,
    `sites_stealth_mobile`, `sites_stealth_browser_apis`,
    `sites_stealth_human_delays`). Assenza = `off`; nessun profilo/bundle
    predefinito. Kill-switch `METNOS_SITES_STEALTH_ALLOWED` = ceiling deployment.
  - **Effetto immediato senza restart** (C2): il sidecar tiene il browser headless
    honest sempre pronto e lancia lazy la variante esatta richiesta fra
    headless+LAUNCH, side honest e side+LAUNCH. `op_open` sceglie prima la
    superficie (`sites_browser_mode=headless|side`), poi applica solo le tecniche
    selezionate. Salvataggio UI → sessione successiva aggiornata subito.
  - **Registro estensibile, selezione per-entry** (C2): ogni tecnica dichiara
    pref, chiavi i18n, layer (`LAUNCH`/`CONTEXT`/`BEHAVIOR`) e applicazione;
    aggiungerla = una entry, senza enumerare combinazioni o modificare il cuore.
  - **Esplicitamente anti-rilevamento**, a rischio del proprietario, NON «lecito».
    Nota tecnica: `webdriver` si nasconde solo col launch-arg (init-JS inefficace —
    il tentativo `defineProperty(navigator,'webdriver')` va **rimosso** da `_STEALTH_JS`).
- **Fallimento onesto e NON sovra-interpretato** (§2.8, post-review @2 #7).
  Reason_code **osservativi e separati**, mai una causa dedotta: `empty_surface`
  (DOM vuoto/near-empty), `page_unavailable`, `http_forbidden` (403 osservato),
  `rate_limited` (429 osservato), `challenge_observed` (marker captcha/2FA). MAI
  dedurre `automation_blocked` dal solo status (403=rifiuto, 429=limite, non la
  CAUSA). Il broker oggi scarta la risposta di `page.goto` (non ha lo status):
  recuperarla e' **prerequisito** per popolare i codici osservativi.
  Esiti post-submit **strutturalmente distinti** (@3 #4/#5): solo
  `credentials_rejected` (regione di errore ESPLICITA, non «ancora sul login») e
  `rate_limited` alimentano il cooldown; `challenge_observed` (2FA/CAPTCHA
  **corretta**) e `login_inconclusive` (remount/timeout) **NO**.
- **Protezione del mandato (anti-lockout)**: NON ritentare a raffica un login che
  ESCALA il rischio dell'account. Il cooldown vive nel BROKER (choke-point che
  vede ogni esposizione credenziale), **transazionale** e persistente per
  `(owner, binding, credential_fp=HMAC)`, contando SOLO `credentials_rejected` e
  `rate_limited` (§6.2 v4), non `selector_missing`/`challenge`/timeout. Specifica
  completa (N=2, base 15m ×3 cap 12h, env bounded, schema SQLite, TTL, reason code
  `sites_cooldown_active`) in §7 analisi v4. Fermarsi li' **serve** il mandato
  (protegge l'asset del proprietario), non lo abbandona.
- **Goal tipizzato dal runtime — migrazione RISTRETTA** (post-review @2 #8 + @3 #7):
  il **runtime** decide se una clausola e' un recupero e passa ad `act_sites` il
  **goal tipizzato**; il sidecar **bypassa** la reinterpretazione dei verbi **solo**
  quando `_goal_mode` e' presente (il parser naturale di `act_sites` resta
  invariato negli altri casi — NON si rimuove `sites.search_action_verb`).
  Prerequisito runtime: sanare la **doppia SoT** (`prefilter._VERB_TO_CANONICAL`
  indipendente da `vocab.ACTION_MAPPING`, con «apri»→`read` errato) e distinguere
  OPEN (naviga) da READ/RETRIEVE (recupera).

## Conseguenze

Un motore unico, robusto e lineare, su tre superfici. **Classe di siti NON
SUPPORTATA IN HEADLESS, dichiarata esplicitamente**: portali che servono una
superficie inutile ai client automatizzati sulla base di fingerprint/segnali
headless (Amazon retail e simili). In headless l'intelligenza locale non
ricostruisce osservazioni che il server non riceve → degrada onesto con
reason_code neutro; la superficie **`extension`** (browser reale, opt-in) e' la
via onesta per quella classe. `side` puo' evitare segnali specifici del
headless-shell, ma resta pilotato e non garantisce l'accesso. Nessun canale
**API/email**. Il contratto drop-in
degli executor `open/login/read/act_sites` non cambia su nessuna superficie
(planner-invisibile).

**Nota su gestore ostruzioni**: due procedure separate a **budget distinti** (non
un gestore unico). `privacy/cookie` (classe nota sicura, **solo non-navigante**)
dismessa proattivamente con la procedura stretta; la chiusura **generica** di un
modal ammessa SOLO quando un target NOTO e' geometricamente occluso, con
postcondizione implementabile (§8 v4) — mai su un nudo `selector_missing`. Il
locator di dismissione deve **vietare** `input[type=submit]` e link
`javascript:`/attivi: un controllo navigante passa dal piano firmato e dal gate,
non dalla dismissione «sicura».

## Verifica

- **Funzionali** (`tests/simulator/sites/test_login_browser.py`, opt-in
  `METNOS_SITES_SIM=1`): privacy non-navigante che riappare; consenso navigante →
  piano firmato; ostruzione generica su target occluso; nudo `selector_missing`
  senza chiusura; `empty_surface` neutro; esiti post-submit con cooldown incrementato
  **ESCLUSIVAMENTE** da `credentials_rejected` e `rate_limited` — `login_inconclusive`
  e `challenge_observed` lo lasciano **INVARIATO** (§6.2 v4, assert espliciti);
  catena completa con **goal tipizzato dal runtime** che ATTESTA la pagina-obiettivo.
- **Sicurezza** (§10.2 v4): origine schema/porta e alias non autorizzato; locator
  submit/js-link rifiutato; stealth per-sessione su browser **riavviato** (routing
  routing per variante, non env-globale); cooldown persistente dopo restart; concorrenza
  atomica.
- **Reale** (dominio leniente, Booking): `open→login→act_sites(goal)→read→extract→
  describe`; record o vuoto **onesto** con evidenza della pagina target. Nessun E2E
  reale prima del cooldown completo (non martellare account reali).
- **`accepted` solo quando** testo ADR e analisi v4 coincidono su: tre superfici,
  due procedure a budget distinti, superficie+stealth per-entry, cooldown §7,
  `credential_origins` §4. Fino ad allora resta `proposed` (@3 #12).
- Analisi implementativa (destinata a Opus + prossimo giro adversarial):
  `internal/design/analysis_intelligent_sites_robot_2026-07-14.md` (v4 autonoma).
