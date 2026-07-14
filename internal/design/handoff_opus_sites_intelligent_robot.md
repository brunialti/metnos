# Handoff Opus — Robot sites intelligente (ADR 0191 / analisi v4)

> **Ripresa a freddo.** Branch `session/detection-lexicon-i18n` (NON pushare).
> Prima di toccare codice leggi, in quest'ordine:
> 1. `CLAUDE.md` + `CLAUDE.mutabile.md` (regole invarianti + stato).
> 2. `decisions/0191-intelligent-mandate-sites-robot.md` (decisione canonica, `proposed`).
> 3. `internal/design/analysis_intelligent_sites_robot_2026-07-14.md` (**v4** — il COSA/PERCHE'; questo handoff e' il COME).
> 4. `decisions/0188-*` (sites F1/F2), `0190-*` (mandati credenziale), `0189-*` (executor intelligenti), `0187-*` (user_prefs).
>
> **Regola di metodo**: questo documento e' ancorato al codice al 14/7. Prima di
> ogni prerequisito, RILEGGI le righe citate: alcune cose sono gia' fatte (sotto,
> marcate ✅ ESISTE) e vanno ESTESE, non ricreate. Non dichiarare fatto cio' che
> non hai verificato con un turno reale (§2.8, §8.5).
>
> **Precedenza limitata al COME**: ADR 0191 resta canonico per decisioni e
> invarianti; la v4 resta canonica per COSA/PERCHE'. Quando una formulazione
> astratta della v4 collide con un fatto verificato del codice, i contratti esatti
> di questo handoff governano il COME. In particolare: launch solo in `server.py`,
> mapping dei segnali post-submit esistenti (nessun detector parallelo),
> `credentials.fingerprint(storage_domain)` e migrazione `www` simmetrica con
> origini persistite. Non riaprire questi quattro punti durante l'implementazione.

---

## Contesto in 6 righe

Il dominio `sites` (ADR 0188) fa interazione web autenticata via un **broker
sidecar Playwright** (`runtime/playwright_sidecar/`, unit USER
`metnos-playwright.service`, porta 8771); gli executor `open/login/read/act/
delete_sites` sono **client thin**. ADR 0191 lo rende un **robot intelligente su
mandato** (ADR 0190) su **tre superfici**: `headless` (default, onesto),
`headless_stealth` (opt-in), `extension` (browser reale utente, fase futura). La
v4 recepisce tre giri adversarial + due commitment non trattabili (estensione
companion; stealth da UI senza restart). Amazon-class (fingerprint headless) =
NON SUPPORTATA in headless per progetto.

## Mappa file → cosa toccare (anchor al 14/7)

| Area | File:riga | Stato |
|------|-----------|-------|
| launch args + stealth env-globale + `_playwright`/`_browser` | `runtime/playwright_sidecar/server.py:53,446-511` | il launch resta ownership ESCLUSIVA del server |
| `_context_kwargs` (UA/locale/timezone) | `runtime/playwright_sidecar/session_broker.py:95` | locale/tz da de-hardcodare |
| `_STEALTH_JS` (defineProperty webdriver inefficace) | `runtime/playwright_sidecar/session_broker.py:185-202` | rimuovere il ramo webdriver |
| `op_open` (crea contesto, applica init-script) | `runtime/playwright_sidecar/session_broker.py:925,1026,1033` | instradare su browser scelto |
| `_login_origin` (fold www, hostname-only) | `runtime/playwright_sidecar/credential_injection.py:465` | → `(scheme,host,port)` + `credential_origins` |
| `_observe_post_submit` + `_post_submit_authenticated` | `runtime/playwright_sidecar/credential_injection.py:652-745`; call-site `:906,:1552` | mappare i segnali esistenti nei 5 esiti |
| `_human_pause` / `_stealth_on` | `runtime/playwright_sidecar/credential_injection.py:438,443` | layer BEHAVIOR del registro |
| `action_resolver.parse_action` + `search_action_verb` | `runtime/playwright_sidecar/action_resolver.py:109,127,140` | **NON toccare** (parser naturale, #7) |
| guard precursor + goal routing | `runtime/engine/dispatch.py:2816,2978,2987,3017` | decide `_goal_mode` (runtime, #7) |
| doppia SoT verbi («apri»→read) | `runtime/prefilter.py:38,59` vs `runtime/vocab.py:40` | riconciliare + open-vs-read |
| user_prefs infra | `runtime/users.py:619-624`, `runtime/http_routes_admin.py:935` | aggiungere `sites_stealth=(on,off)` al vocab, NON allo schema SQL |
| goal tipizzato end-to-end | `executors/act_sites/act_sites.py:53`, `session_client.py:112`, `server.py:417`, `session_broker.py:2702-2704` | ✅ ESISTE (vedi sotto) |
| fingerprint credenziale | `runtime/credentials.py:168`; usi `credential_injection.py:825,958,1069` | ✅ ESISTE; chiamare con `storage_domain`, puo' essere `None` |
| `page.goto` broker | `session_broker.py:1051,2673,3042` | catturare la `Response` in tutti e tre i percorsi |

## Cose GIA' FATTE — non ricreare, ESTENDI

- ✅ **Goal tipizzato broker-side**: **un solo carrier `goal_query: str|None`; la
  condizione unica e' `goal_query is not None`** (v4 §5). Invariante gia' garantito:
  `act_sites.py:66` passa `goal_query` SSE `_goal_mode` → non sono due condizioni.
  Il broker rinomina internamente `goal_query`→`goal_target` (`session_broker.py:2703-2704`
  bypassa `parse_action` quando presente): stesso valore, un solo test a ogni layer.
  → Il finding #7 e' **risolto lato broker**. RESIDUO **solo nel runtime** (prereq 3):
  decidere `_goal_mode` correttamente + sanare open-vs-read. NON rimuovere
  `search_action_verb` dal broker.
- ✅ **user_prefs**: tabella `user_prefs`, vocab CHIUSO (`users.py:619-624`), getter/
  setter, endpoint `/admin/users/{id}/prefs` (`http_routes_admin.py:935`), test
  `runtime/tests/test_user_prefs.py`. La chiave storage da aggiungere e'
  `sites_stealth`, valori `on|off`; `${RUNTIME:pref_sites_stealth}` e' il nome
  dell'eventuale placeholder, NON il nome della chiave DB.
- ✅ **Stealth env attuale** (`server.py:471`, `credential_injection.py:438`,
  `session_broker.py:_context_kwargs`): esiste come `METNOS_SITES_STEALTH`
  process-globale. → da **migrare** a pref per-turno + due browser (prerequisito 1),
  NON da inventare.
- ✅ **Segnali post-submit**: `_observe_post_submit` produce gia'
  `stable_positive`, `password_rejected`, `otp`, `captcha`, `push`. Sono la SoT
  della classificazione: NON creare un secondo detector/lessico parallelo.
- ✅ **Fingerprint**: `credentials.fingerprint(storage_domain)` produce lo
  `sha256[:16]` della password o `None`. Cambiare password cambia fingerprint e
  azzera naturalmente la chiave cooldown: comportamento VOLUTO.

---

## PREREQUISITO 0 — confine di launch + seam `BrowserSurface` INCREMENTALE

**Non fare un refactor big-bang.** Il due-browser richiede un confine di launch,
non l'estrazione immediata di ogni accesso a `page`. Il seam architetturale viene
introdotto ora, ma la migrazione delle primitive avviene solo nei punti gia'
toccati dai prerequisiti successivi; il completamento e' il gate della fase
`extension`.

- Creare in `runtime/playwright_sidecar/browser_surface.py` due contratti distinti:
  type alias `BrowserProvider = Callable[[bool], Awaitable[Browser]]` e
  `PlaywrightSurface(context, page)`. Il provider possiede i browser; la surface
  possiede ESATTAMENTE un `context` e una `page` di sessione.
- **Direzione unica**: `session_broker.op_open` → `BrowserProvider` (configurato dal
  server) → browser; poi `op_open` crea `PlaywrightSurface` e la salva in
  `session[sid]["surface"]`. La surface non richiama mai il broker.
- **Compatibilita' incrementale**: il broker puo' leggere temporaneamente
  `surface.page`/`surface.context`; ogni funzione modificata da 1-6 deve passare alla
  primitiva surface equivalente. NON spostare in una volta `op_open`, `op_read`,
  `op_login`, `op_act`, goal navigation e `action_resolver`.
- **Gate prima della fase extension**: nessun accesso diretto Playwright deve
  restare fuori da `PlaywrightSurface`; solo allora implementare
  `ExtensionSurface`. Questo gate NON blocca i prerequisiti headless 1-6.
- **Done P0**: provider configurabile + surface memorizzata per sessione +
  open/close invariati; test lifecycle verde. Nessuna riscrittura della macchina
  intelligente e nessun cambiamento osservabile.

## PREREQUISITO 1 — stealth: pref per-turno + due browser + registro (§2 v4)

**Risolve #1, #2, #10 + commitment C2.**

1. **Registro tecniche** `runtime/playwright_sidecar/stealth.py`: namedtuple
   `StealthTechnique(name, layer, apply, enabled_when)`, `layer ∈
   {LAUNCH,CONTEXT,BEHAVIOR}`. Entry iniziali: `webdriver_launch_arg` (LAUNCH),
   `ua_override` (CONTEXT), `mobile_emulation` (CONTEXT, opt), `chrome_permissions_js`
   (CONTEXT), `human_delays` (BEHAVIOR). Struttura pronta a un profilo futuro
   (`off|basic|aggressive`) via `enabled_when(profile)` — **mai** attiva di default.
2. **Ownership launch (B1, vincolante)**: `_playwright`, `_browser_honest`,
   `_browser_stealth` e `_stealth_launch_lock = asyncio.Lock()` vivono SOLO in
   `server.py`. `_browser_honest` parte allo startup; `_browser_stealth` viene
   lanciato lazy da `server._get_browser(*, stealth: bool)`, sotto lock con doppio
   controllo `is_connected`. `session_broker` NON importa Playwright e NON lancia
   browser.
3. **Configure cross-confine**: sostituire `session_broker.configure(browser)` con
   `configure(browser_provider)` e passare la callback `server._get_browser`.
   `op_open(stealth: bool=False)` chiama
   `await browser_provider(effective_stealth)` e crea il context su quel browser.
   Launch stealth fallito → `browser_unavailable`, senza fallback silenzioso sul
   browser honest. Disconnect callback, health e shutdown di ENTRAMBE le istanze
   restano responsabilita' di `server.py`; un disconnect inatteso di qualunque
   istanza percorre il recovery/restart esistente, mentre `_stopping=True` lo ignora.
   L'health espone separatamente `browser_honest_connected` e
   `browser_stealth_state=not_started|connected|disconnected`.
4. **Pref e writer autorizzato**: aggiungere
   `"sites_stealth": ("on", "off")` a `PREF_KEYS`+`PREF_ALLOWED` in
   `runtime/users.py:619-624`; default applicativo `off`. NON modificare
   `_PREFS_SCHEMA`. Il toggle `/admin/users/{id}/prefs` riusa l'endpoint esistente.
5. **Propagazione completa (M5, nessun salto)**:
   `dispatch` risolve owner e inietta `_stealth="on"|"off"` da
   `users.get_pref(owner_id,"sites_stealth","off")` → `open_sites.py` valida e
   converte in bool → ogni `on_approve.args` conserva `_stealth` →
   `session_client.session_open(stealth: bool)` → `server.handle_session_open`
   accetta SOLO un JSON bool → `session_broker.op_open(stealth: bool=False)`.
   Dichiarare `_stealth` stringa `on|off`, `runtime_resolved=true`, nel manifest di
   `open_sites`; NON affidarsi alla sola UI e NON chiedere al planner di produrlo.
6. **Scelta per-sessione**: `effective_stealth = requested_stealth AND
   METNOS_SITES_STEALTH_ALLOWED`. Salvarlo in `session[sid]["stealth"]` e nella
   surface; approval replay/pending-open include il valore nella propria binding e
   non lo ricalcola. Ceiling `0` → honest + audit `stealth_denied_by_ceiling`, non
   errore. Toggle UI influenza solo la sessione successiva.
7. **Rimuovere** da `_STEALTH_JS` (`session_broker.py:187-188`) il ramo
   `Object.defineProperty(navigator,'webdriver',...)` — inefficace (verificato).
8. **Locale/timezone (H1)**: `pref_locale` e `pref_timezone` NON esistono e NON
   vanno aggiunte. `locale` usa `_lang`, gia' runtime-owned, propagato da
   `open_sites` lungo la stessa catena fino a `op_open`; fallback `METNOS_LANG` e,
   se assente, override omesso per usare Chromium nativo. `timezone_id` deriva dal
   timezone di sistema/`TZ` (se non determinabile, omettere l'override). Nessun enum
   di locale/timezone in `user_prefs`.
- **Done**: toggle UI → sessione successiva usa l'altro browser **senza restart**;
  default = UA nativo + webdriver nativo + nessun ritardo; test dedicati a
  plumbing, lazy launch concorrente, routing per-sessione, restart, health e
  shutdown verdi.

## PREREQUISITO 2 — origine credenziale + binding storage (§4 v4)

**Risolve #4, #6 e B3.** Confronto origine e lookup del record sono due operazioni
distinte, ma usano lo stesso binding esplicito; nessun fold `www` resta implicito
a runtime.

- **Persistenza**: nuova chiave **`credential_origins`** nel payload per-dominio
  (`runtime/credentials.py`, `~/.config/metnos/credentials/<domain>.json.age`), lista
  di stringhe canoniche `"scheme://host:effective_port"` GIA' normalizzate (IDNA
  lowercase, no trailing-dot, IPv6 `[addr]`, porta SEMPRE esplicita). Match esatto
  della tupla `(scheme,host,port)`; `allowed_hosts` resta separato.
- **http = DECISIONE UNICA (niente fork)**: `https` salvo loopback+range privati/
  link-local (`127/8`,`::1`,`localhost`,`10/8`,`172.16/12`,`192.168/16`,`169.254/16`,
  `.local`). E' l'estensione deliberata per FASTGate; il doc committa a **un solo
  path** (il reviewer puo' restringere in futuro, ma NON implementare due varianti).
- **Identita' record**: `op_open` ottiene dal mandato un
  `storage_domain_candidate`; `_load_site_credentials(candidate)` prova solo
  `candidate` e il prefisso legacy `web_<candidate>`, e ritorna la chiave realmente
  caricata come `storage_domain`. Salvarla nella sessione come
  `credential_binding_id` e passarla a login/fill/fingerprint. NON derivare
  `www.host→host`.
- **Rimozione simmetrica fold**: eliminare sia il fold di `_login_origin`
  (`credential_injection.py:465-475`) sia il fallback `www.host→host` di
  `_load_site_credentials` (`:996-1005`). L'autorizzazione `www` esiste solo come
  entry persistita in `credential_origins`, mai come equivalenza calcolata al fill.
- **Migrazione legacy deterministica**: se il payload non contiene
  `credential_origins`, derivare in memoria e validare due entry HTTPS esplicite:
  `https://<D>:443` e la controparte stretta `www` (`D↔www.D`, una sola label,
  solo hostname DNS con almeno due label; mai IP/localhost/`.local`). Persistere le
  entry al successivo `set_credentials`, senza riscrittura durante `load`.
- **Risoluzione legacy iniziale**: `op_open` usa il `root_host` del
  `credential_mandate` gia' risolto come `storage_domain_candidate`; questo consente alla
  prima sessione su `www.D` di caricare il record legacy `D` senza reintrodurre il
  fold dentro `_load_site_credentials`. Il resolver puo' usare il vecchio alias
  stretto SOLO per trovare il candidate legacy; non autorizza il fill, che resta
  vincolato alle entry migrate. Se manca un binding → fail-closed
  `credentials_missing`, non scansione indiscriminata del vault.
- **Form**: `set_credentials` guadagna `credential_origins` opzionale; il default
  usa le stesse due entry della migrazione, mostrate e modificabili nel form. Ogni
  voce viene normalizzata/validata prima del salvataggio. IdP delegato resta il
  one-shot F1 di ADR 0188 e non viene persistito automaticamente.
- **SoT mandato**: `credential_mandates.resolve_sites_binding` legge
  `credential_origins` dal payload del record risolto e lo espone al broker; non
  ricostruisce questa autorita' da `allowed_hosts` ne' da soli eventi audit
  hostname-only. Le origini one-shot approvate restano overlay di sessione e non
  mutano il payload.
- **Enforcement**: prima di OGNI fill, origine corrente e action del form devono
  appartenere a `credential_origins`. Fuori lista → `origin_unverified` fail-closed,
  mai fill. Il caricamento del record non costituisce autorizzazione al fill.
- **Done**: test dedicati a schema/porta/IDNA/IPv6/http-privato, migrazione
  apex↔www, alias non registrato, email-first su `www`, binding assente e IdP
  one-shot; F1 `test_sites_security.py` verde o aggiornato coerentemente.

## PREREQUISITO 3 — goal tipizzato dal runtime + doppia SoT (§5 v4)

**Risolve #7 (residuo runtime).** Il broker e' gia' a posto (vedi «GIA' FATTE»).

- In `dispatch.py` (`_ensure_site_session_precursor`, `:2816` + uso
  `search_action_verb` a `:2978,2987`): la decisione «questa clausola e' un
  recupero → `_goal_mode`» deve basarsi sull'**intent canonico del runtime**, non
  su un rilevatore-verbo indipendente. Passare `_goal_mode`+goal tipizzato (gia'
  costruito a `:3017`).
- **Doppia SoT — DECISIONE UNICA: DERIVARE** (non «allineare», niente due varianti):
  sostituire la tabella `prefilter._VERB_TO_CANONICAL` (`:38,59`, «apri»→`read`) con
  una **derivazione da `vocab.ACTION_MAPPING`** (canonico primario per sinonimo),
  preservando l'uso context-free del prefilter. Effetto: «apri»→`open`; distinguere
  OPEN (naviga) da READ/RETRIEVE (recupera). Regola canonica in v4 §5.
- Vincolo §7.3/§7.9 + memorie: **niente liste sinonimi hardcoded**; usa
  `detection_lexicon` (NL→canonico, IT+EN) e `vocab.py` come SoT. Il testo
  model-facing e' dominio Fable — se serve un concetto lessicale nuovo, **fermati e
  chiedi a Fable** (memoria `feedback_fable_authors_manifests_prompts`).
- **Done**: turno «apri booking» → OPEN (no goal); «mostra le prenotazioni su
  booking» → goal tipizzato; entrambi su turno reale.

## PREREQUISITO 4 — status `page.goto` + codici osservativi (§6.1 v4)

**Risolve #4/#5 (parte osservativa).**

- Il broker oggi **scarta** la risposta di `page.goto` → manca lo status HTTP.
  Catturare e propagare la `Response` in TUTTI i tre percorsi broker:
  apertura iniziale (`op_open`, circa `:1051`), landing recovery goal
  (`_retry_goal_from_entry`, circa `:2673`) e `primitive=goto` approvata
  (`_execute_approved_plan`, circa `:3042`). Catturare anche il `goto(login_url)`
  in `credential_injection.py:1228`, perche' puo' produrre il 429 del login.
- Popolare i reason_code con questa precedenza deterministica: 429/`Retry-After`→
  `rate_limited`; 403→`http_forbidden`; 5xx/None/net-err→`page_unavailable`;
  marker→`challenge_observed`; infine `empty_surface` se body<32 caratteri e zero
  controlli. Primo match vince; status prima del contenuto. **MAI** dedurre
  `automation_blocked` dal solo status.
- **DISTINZIONE (chiude #8)**: `reason_code` = **slug STABILE interno** (audit/logica),
  MAI tradotto; il testo utente e' una chiave **separata** `MSG_SITES_RC_*` via
  mapping codice→MSG (IT+EN nel DB, non in git). Non confondere i due.
- **Chiavi i18n da seminare, nomi inglesi esatti (M3)**:
  `MSG_SITES_RC_RATE_LIMITED`, `MSG_SITES_RC_FORBIDDEN`,
  `MSG_SITES_RC_UNAVAILABLE`, `MSG_SITES_RC_CHALLENGE`,
  `MSG_SITES_RC_EMPTY_SURFACE`; il cooldown usa separatamente
  `MSG_SITES_COOLDOWN_ACTIVE`. Tutte IT+EN, nessuna stringa user-facing hardcoded.
- **Done**: test separati per i quattro goto, `Response=None`, redirect finale,
  403, 429, 5xx/network e `empty_surface` neutro; nessun ritento su esito terminale.

## PREREQUISITO 5 — locator dismissione vieta submit/js-link (§0/§8 v4)

**Risolve #3 (parte locator).**

- Nel locator (`session_broker.py:335` `_LOCATE_SAFE_OVERLAY_DISMISS_JS`, usato da
  `_dismiss_obstructing_overlay:2055`) applicare questi divieti deterministici:
  vietare `input[type∈{submit,image}]`, `button[type=submit]`, **`button` senza
  `type` dentro un `<form>`** (submit implicito — chiude #9), e `<a>` navigante
  («link navigante» = href non vuoto e non puro `#fragment`) o `javascript:`.
  Ammessi: `button[type=button]`, `[role=button]` non-submitter, X/label di chiusura.
- **Controllo navigante → piano firmato (chiude #9)**: NON ignorare — ritorna
  `{obstruction:"navigating_control", control:Control(...)}`; il runtime lo trasforma
  in passo di piano via gate F2 `get_approval`.
- **Done**: test locator distinti per input submit/image, button submit, submit
  implicito, link http(s), `javascript:`, fragment e button non navigante; test
  consenso navigante→gate separato dal dismiss privacy.

## PREREQUISITO 6 — cooldown anti-lockout completo (§7 v4)  ⚠️ blocca l'E2E reale

**Risolve #8-round1/#9.** Specifica interamente in v4 §7 (non reinventare).

- **Dove**: broker. **Chiave** `(owner, binding_id, credential_fp)` — mai il valore.
  `binding_id = storage_domain` esatto del record vault; `credential_fp =
  credentials.fingerprint(storage_domain)` (ESISTE: sha256[:16] password). NON
  passare `domain`/`vault_domain` ambiguo e NON creare un secondo HMAC/server key.
  Se il payload e' deliberatamente passwordless e il fp e' `None`, usare la
  sentinella stabile `passwordless` (la chiave contiene gia' owner+binding); se una
  password esiste ma il fp e' `None`, fail-closed `credential_identity_unavailable`.
  Il cambio password produce un nuovo fp e azzera naturalmente il cooldown:
  comportamento voluto.
- **Conta SOLO** gli esiti classificati `credentials_rejected` e
  `rate_limited`. NON `selector_missing`/`empty_surface`/timeout/`challenge`/`inconclusive`.
- **«Consecutivi»**: accumula dall'ultimo `login_verified` (reset=DELETE); ragioni
  miste sommano. **Formula esatta** `cd = 0 if fc<N else min(CAP, BASE*FACTOR**(fc-N))`;
  N=2, BASE=900, FACTOR=3, CAP=43200; env bounded
  `METNOS_SITES_COOLDOWN_{N,BASE_S,FACTOR,CAP_S}`; TTL idle 24h. **Upsert
  transazionale** (algoritmo completo v4 §7).
- **Storage**: `sites_cooldown.sqlite` sotto lo state dir utente
  (`~/.local/state/metnos`, override `METNOS_USER_STATE`; **derivalo da
  `runtime/config.py`**, NON path hardcoded §7.11), WAL, schema in v4 §7,
  read-modify-write in `BEGIN IMMEDIATE` (atomico, non il JSONL audit).
- **Esposizione**: interattivo → `MSG_SITES_COOLDOWN_ACTIVE` (i18n, con
  `retry_after_s`, binding redatto); task/schedulato → **fail-closed** (ADR 0190).
- **Classificazione post-submit (B2, una sola SoT)**: cambiare
  `_post_submit_authenticated(observed, session_cookie_names)` da `bool` a esito
  strutturato e aggiornare ENTRAMBI i call-site `credential_injection.py:906,1552`.
  Mappatura, in questo ordine: `stable_positive` verificato → `login_verified`;
  status/marker 429 → `rate_limited`; `otp|captcha|push` → `challenge_observed`;
  `password_rejected` → `credentials_rejected`; nessuno → `login_inconclusive`.
- `password_rejected` resta prodotto da `_PASSWORD_REJECTED_JS` dentro
  `_observe_post_submit`; se va irrobustito, modificare QUEL detector e i suoi test.
  Per questo handoff `password_rejected` E' l'implementazione esistente della
  «regione di errore esplicita» richiesta dalla v4 §6.2, non un segnale piu' debole.
  NON aggiungere una seconda ricerca di regioni errore, un nuovo concetto
  `detection_lexicon` o un percorso Fable parallelo. `rate_limited` e' l'unico
  segnale nuovo, alimentato dal prerequisito 4.
- Per il submit che naviga senza `page.goto`, il wrapper di submit cattura in modo
  bounded la `Response` del documento top-level e passa `http_status` a
  `_observe_post_submit`; solo uno status 429 osservato alimenta `rate_limited`.
  Nessuna inferenza dal testo generico della pagina.
- **Effetti esatti**: `login_verified` DELETE/reset; `credentials_rejected` e
  `rate_limited` incrementano; `challenge_observed` e `login_inconclusive` non
  toccano il cooldown. Audit e reason_code derivano dall'esito, non riclassificano.
- **Done**: cinque test di mapping + due call-site aggiornati; test persistenza
  restart e concorrenza `BEGIN IMMEDIATE`; assert esplicito che inconclusive e
  challenge non modificano il record.

---

## DOPO i prerequisiti (in quest'ordine)

- **Gestore ostruzioni: DUE procedure + budget distinti** (§8 v4): privacy
  proattiva **solo non-navigante**; generica SOLO su target NOTO occluso con le **5
  condizioni** verificabili (renderizzato + non-topmost via `elementFromPoint` +
  controllo chiusura nello stesso modal + **firma DOM sha1 stabile a `_MODAL_SETTLE_MS=250`**
  + locator sicuro). **Budget vincolanti**: `_MAX_PRIVACY_DISMISSALS=2`
  (esiste), `_MAX_GENERIC_DISMISSALS=3`, `_MAX_TOTAL_DISMISSALS=4` (assoluto);
  ogni tentativo decrementa classe+totale; **nessun reset entro la sessione**; NON
  consumano `_MAX_GOAL_STEPS`. NON fondere le due procedure.
- **Persistenza adattiva bounded** (§9 v4): `account_reveal_control` + una sola
  passata modello su metadati broker-owned; dedup `goal_candidate_key`; bounded
  `_MAX_GOAL_STEPS`.
- **Piano test** (§10 v4): riscrivi `test_sites_login_simulator.py` — fixture
  privacy **non-navigante**; consenso navigante→gate; occlusione con 5 condizioni;
  nudo `selector_missing` senza chiusura; esiti post-submit; catena completa con
  goal tipizzato che **ATTESTA** la pagina-obiettivo; ripristino global
  monkeypatchate; test sicurezza §10.2.

## Matrice Done-gate (M4 — nomi, non numeri ambigui)

Ogni riga e' un gate distinto e deve fallire separatamente. Non usare riferimenti
come «§10.2-8» per accorpare comportamenti indipendenti.

| Gate | Test minimo vincolante |
|------|------------------------|
| P0 lifecycle | provider configurato; surface owner-bound; open/close senza regressione |
| P1 stealth plumbing | pref off/on; ceiling; approval replay; due sessioni su browser diversi; lazy launch concorrente; restart/health/shutdown |
| P2 origins | schema+porta; alias negato; migrazione apex/www; email-first www; HTTP privato; IDNA/IPv6; binding assente |
| P3 goal | «apri booking»=OPEN; «mostra prenotazioni»=goal; `goal_query` end-to-end |
| P4 status | i quattro goto; 403; 429; 5xx/None/network; redirect; empty surface |
| P5 locator | submit esplicito/implicito; link attivo/js; controllo non-navigante; navigante→gate |
| P6 outcome/cooldown | cinque mapping; entrambi i call-site; solo rejected/rate incrementano; reset; restart; concorrenza |
| Ostruzioni | privacy e generica consumano budget distinti; tetto totale; target occluso; nudo selector missing non chiude |
| Catena | login→act(goal)→read→extract→describe attesta la pagina-obiettivo |

## Fase separata (non ora) — superficie `extension` (§3 v4, commitment C1)

`ExtensionSurface` = terza implementazione di `BrowserSurface` (NON un executor
dedicato). Gira sul device utente via canale Fase 7. Le stesse primitive vengono
inviate via RPC all'estensione, che le esegue nel browser reale e torna metadati
sanitizzati broker-owned (mai valori). Credenziali reali verso il device
(owner-bound, ADR 0190); redazione privacy lato estensione. Nodi aperti in v4 §3.3
(transport, credenziali sul client, sicurezza estensione). **Impianto = fase
dedicata dopo il consolidamento headless.**

---

## Regole operative (vincolanti)

- **§7.10 re-sign** dopo ogni edit executor/manifest: `python3 runtime/sign.py sign
  executors/<name>` (da repo root; `python -m runtime.sign` NON funziona) + restart;
  committa manifest+`.sig` INSIEME. Senza firma il loader scarta in silenzio.
- **Restart**: `sudo -n systemctl restart metnos-http.service` (system,
  passwordless); sidecar `systemctl --user restart metnos-playwright.service`.
  NON riavviare durante un turno attivo (§8.6).
- **§7.13 i18n**: ogni messaggio user-facing via `_msg`/DB, IT+EN. Il `reason_code`
  e' uno slug STABILE (NON i18n); il testo utente e' la chiave `MSG_SITES_RC_*`
  separata. Vietate stringhe hardcoded. Testo model-facing (manifest
  `[description]`/affinity, `.j2`) = **dominio Fable** — fermati e chiedi.
- **§8.5 E2E — due livelli (v4 §10.1, chiude #10 apparente conflitto)**:
  - **(a) SIMULATORE** (`METNOS_SITES_SIM=1`, browser+broker+planner veri, target
    locale) = **turno reale che soddisfa §8.5** → e' il **Done gate per-prerequisito**,
    senza toccare account esterni. I test NON sono «dopo tutto»: ogni prereq ha il suo.
  - **(b) REALE ESTERNO** (Booking) = SOLO **dopo il prereq 6** (cooldown), per non
    rischiare lockout. «E2E per ogni cambio» = (a); «niente reale prima di §7» = (b).
- **Sicurezza**: i pezzi security-critical (origine, cooldown, dismissione, esiti
  post-submit) passano **review adversarial separata PRIMA del merge**.
- **NON martellare account reali** (Amazon/router = rischio lockout): E2E reale
  esterno solo post-prereq-6. Booking = dominio leniente per la catena.
- **NON pushare** il branch dev. **MAI committare** `ANALISI MEDICHE E CERTIFICATI/`.
- **Niente Co-Authored-By** nei commit; commit locali in italiano, modulari.

## Ordine di lavoro consigliato

0 (provider + seam minimo) → relativo Done-gate → 1 (stealth) → gate → 2
(origine/binding) → gate → 3 (goal runtime) → gate → 4 (status goto) → gate → 5
(locator) → gate → 6 (outcome+cooldown) → gate → [E2E reale esterno abilitato] →
ostruzioni due-procedure → gate → persistenza → catena completa → giro adversarial
→ merge. La migrazione completa delle primitive `BrowserSurface` e
`ExtensionSurface` appartiene alla fase extension successiva, non al critical path
dei fix headless.
