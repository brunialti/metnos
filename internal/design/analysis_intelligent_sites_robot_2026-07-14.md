# Analisi implementativa v4 — Robot sites intelligente (ADR 0191)

> **v4 AUTONOMA, deterministica.** Sostituisce integralmente v1/v2/v3 (testo vecchio
> rimosso). Incorpora **tre giri adversarial + un giro @3-bis** (14/7) e due
> **commitment non trattabili** (C1 estensione companion; C2 stealth da UI senza
> restart + estensibile). **Obiettivo di questa revisione**: rendere l'handoff
> VINCOLANTE — nessuna decisione autonoma residua in punti security-critical. Ogni
> valore, contratto, precedenza e algoritmo e' fissato qui.
> Destinatari: reviewer adversarial + Opus (implementazione). Stato codice:
> worktree di sessione, non committato.

## 0. Invarianti

- **§2.8 onesta'**: mai un esito falso; `logged_in:true` solo verificato; una causa
  si dichiara solo con **evidenza**, mai dedotta da un sintomo ambiguo o da un solo
  status HTTP.
- **Dismissione overlay (ADR 0188)**: solo controllo esatto tradotto o X in angolo di
  un contenitore fixed/sticky topmost; **VIETATI** i submitter di form e i link
  naviganti/`javascript:` (§8.2). Un controllo navigante → piano firmato + gate.
- **Origine credenziale (§4)**: confronto exact su `(scheme, host, port)` normalizzato,
  in campo dedicato `origins`, MAI `allowed_hosts` (rete ≠ destinazione credenziale).
- **Vincolo modello**: niente VLM/screenshot **dopo** il fill credenziale; resolver
  testuale locale su metadati sanitizzati broker-owned (ID/ruoli/nomi, mai valori).
- **Verbi (§5)**: goal deciso dal runtime, un solo DTO tipizzato (`goal_query`), una
  sola regola OPEN/READ/RETRIEVE. Niente lessici hardcoded; niente doppia SoT.
- **Superfici (§1)**: logica unica; cambia solo la superficie (headless / stealth /
  extension), scelta da un resolver (config/pref, non intento).
- **Localizzazione**: `locale`/`timezone` da config utente/sistema, non costanti.
- **reason_code vs i18n (§6.1)**: `reason_code` = slug STABILE interno (audit/logica),
  MAI tradotto; il testo utente e' una chiave `MSG_SITES_RC_*` separata via mapping.

## 1. Tre superfici dietro gli STESSI executor

Logica identica (trova email → riempi → continua → password → goal → leggi →
estrai); cambia solo **dove** girano le primitive. Astrazione `BrowserSurface`.

| Modalita' | Superficie | Onesta' | Browser | Credenziali | Default |
|-----------|-----------|---------|---------|-------------|---------|
| `headless` | `PlaywrightSurface` | onesto | Chromium server (honest) | vault | **SI** |
| `side` | `PlaywrightSurface` | pilotato, grafico | Chromium completo server | vault | no |
| `extension` | `ExtensionSurface` §3 | **onesto**, browser reale | device utente | reali | no |

Gli executor `open/login/read/act/delete_sites` NON cambiano (drop-in). Il resolver
sceglie la superficie per-sessione all'`op_open`. Le tecniche stealth §2 sono
ortogonali e applicabili singolarmente a `headless` e `side`.

### 1.1 `BrowserSurface` — astrazione della FASE EXTENSION, NON prerequisito headless (chiude #1, H3)

**Decisione (H3)**: l'astrazione `BrowserSurface` **NON e' un prerequisito** del
lavoro Playwright e **non** precede i fix di sicurezza. Le modalita' `headless`/
`side` condividono il broker odierno cambiando la variante browser all'open
(§2.2). L'estrazione di una superficie unica attraverso cui far passare
`op_open/login/read/act` + navigazione goal + `action_resolver` e' un refactor
grande (quei punti toccano `page` in profondita': `op_open:925`, `op_read:1184`,
`op_login:1273`, `op_act:3425`, goto `2673/3042`) — NON un «diff strutturale a
suite invariata». Si affronta **quando** si costruisce `ExtensionSurface`
(§3, fase separata), non prima.

**Contratto (per la fase extension)**. Direzione dipendenze unica:
`session_broker.op_*` → `BrowserSurface` → Playwright/estensione (mai il contrario).
Primitive: `open`, `enumerate_controls`, `fill`, `click`, `read_dom`, `screenshot`,
`close`; DTO sanitizzati broker-owned (`Origin`, `NavOutcome`, `Control` con
`action_id`/role/name mai valori, `DomSnapshot`, `FillOutcome`); condizioni attese
negli outcome (`reason_code`), guasti come `SurfaceError`/`SurfaceTimeout`; timeout
per-op espliciti; backpressure = lock per-sessione (F1 FIX C). Dettaglio completo
si fissa all'apertura della fase extension.

## 2. Website browsing — superficie, stealth per-entry, propagazione end-to-end

### 2.1 Interruttore, sotto-opzioni e ceiling (non piu' env globale)
- **Pannello Website browsing**: pref `sites_browser_mode=headless|side` (default
  `headless`), master `sites_stealth` (`on|off`, default `off`) e una pref `on|off`
  per ogni entry del registro. La UI `/admin/users` rende il master e checkbox
  indipendenti dal catalogo restituito da `stealth.preference_specs()`; assenza =
  `off`. Il master limita le tecniche ma non cancella le selezioni memorizzate.
- **Param runtime**: `open_sites` riceve `_stealth` e
  `_stealth_techniques: array[string]`, entrambi `runtime_resolved=true` e nascosti
  al proposer. La lista e' chiusa ai nomi del registro, normalizzata in registry
  order e conservata nei replay di approvazione.
- **Ceiling** `METNOS_SITES_STEALTH_ALLOWED` (default `"1"`): se `"0"` e pref `on` →
  stealth **forzato off** (default onesto), audit `stealth_denied_by_ceiling`, NON un
  errore (degrado onesto).

### 2.2 Varianti browser + effetto immediato senza restart (C2)
- Browser headless honest sempre pronto; varianti headless+LAUNCH, side honest e
  side+LAUNCH **lazy**. Side usa `headless=False` e richiede DISPLAY/Wayland su Linux.
- **Lock lazy-launch** unico con double-check sulla variante richiesta.
- `op_open` risolve `browser_mode` e **effective_techniques = selected** solo se
  master e ceiling sono attivi; la chiave della variante e' `(mode, has_LAUNCH)`.
  La surface e la sessione fissano la tupla effettiva per tutta la loro vita.
- **Health/shutdown**: stato distinto per le quattro varianti possibili; shutdown
  chiude tutte quelle lanciate. `side_browser_available=false` se manca il display.
- **Replay approvazioni**: al resume di un gate la sessione ESISTE → si riusa la sua
  surface (stealth fissato all'open). Un cambio pref a meta'-turno NON tocca la
  sessione aperta; una sessione NUOVA post-resume ri-risolve.
- **Salvataggio UI → la sessione successiva usa subito la nuova selezione.** Il
  costo del secondo browser esiste solo se `webdriver_launch_arg` e' selezionata.

### 2.3 Registro tecniche (C2-estensibilita')
`runtime/playwright_sidecar/stealth.py`:
```
StealthTechnique(name, preference_key, label_key, help_key, layer, apply)
#   layer ∈ {LAUNCH, CONTEXT, BEHAVIOR}
#   apply per layer:
#     LAUNCH   : apply(launch_args:list[str]) -> None     # append, dedupe
#     CONTEXT  : apply(context_kwargs:dict, init_scripts:list[str]) -> None
#     BEHAVIOR : apply(flow_config:dict) -> None           # abilita human_pause
```
- Entry iniziali: `webdriver_launch_arg` (LAUNCH), `ua_override` (CONTEXT),
  `mobile_emulation` (CONTEXT), `chrome_permissions_js` (CONTEXT),
  `human_delays` (BEHAVIOR). Sono tutte selezionabili singolarmente e off di default.
- **Ordine**: registry-order per layer; LAUNCH al lancio browser, CONTEXT a
  `new_context`+`add_init_script`, BEHAVIOR nel flusso login.
- **Idempotenza**: init-script aggiunto una volta per contesto; launch-args dedupati.
- **Failure policy**: una tecnica che solleva → `LOG_*` + **skip** (best-effort:
  opt-in sperimentale NON deve rompere la sessione).
- Aggiungere una tecnica futura = **una entry**. Mai attive di default.

### 2.4 Default onesto + correzioni
- DEFAULT: UA nativo, `navigator.webdriver` nativo, nessun ritardo (attese su
  postcondizioni). Misure funzionali: viewport, WebRTC off, service-worker block.
- `locale` deriva da `_lang`/`METNOS_LANG`; `timezone` dal sistema/TZ. Non
  esistono `pref_locale`/`pref_timezone` e non vanno introdotte.
- **Rimuovere** da `_STEALTH_JS:187-188` il ramo `defineProperty(navigator,'webdriver')`
  (inefficace, verificato). `webdriver` si nasconde SOLO col launch-arg.
- Implementazione verificata da `test_sites_stealth.py`: mode, layer isolati,
  binding replay, UI e fallimento side senza display.

## 3. Estensione companion — superficie remota onesta (C1)

Terza superficie (commitment). Onesta: browser reale utente, sessione vera,
credenziali reali, trasmette i dati **tranne privacy**.

- **NON un executor dedicato**: e' la terza implementazione di `BrowserSurface`
  (`ExtensionSurface`), scelta dal resolver (`extension`). Gira sul device utente,
  innestata nel canale Fase 7 (device→users.id).
- **Piloting**: `perform_login` emette le stesse primitive §1.1; in modalita'
  extension vengono inviate via RPC all'estensione, che le esegue nella pagina reale
  e torna gli **stessi DTO sanitizzati** (mai valori). Le credenziali viaggiano verso
  il device (owner-bound); i campi privacy sono redatti **lato estensione**.
- **Contratto surface = §1.1 (gia' completo)**. Resta aperto SOLO il **transport**
  (fase separata): riuso canale Fase 7 vs WS dedicato; heartbeat; degrado se il
  browser utente e' chiuso; credenziali sul client (invio one-shot vs vault-lato-
  estensione); sicurezza estensione (consenso, owner-binding, host_permissions
  minimi, firma pacchetto). **Impianto dopo il consolidamento headless.**

## 4. Origine credenziale — `origins` nel vault (chiude #4, #6-origine)

- **Rappresentazione persistita**: nuova chiave `origins` nel payload per-dominio
  (`~/.config/metnos/credentials/<domain>.json.age`, `runtime/credentials.py`), valore
  = lista di stringhe canoniche `"scheme://host[:port]"` (porta omessa sse default),
  memorizzate GIA' normalizzate.
- **Normalizzazione** (deterministica): host IDNA/punycode→ASCII lowercase; trailing
  dot rimosso; IPv6 compresso in `[...]`; effective port (443 https / 80 http)
  confrontato sempre esplicito. Dominio CHIUSO → **match esatto** della tupla.
- **Scheme**: `https` obbligatorio. **Eccezione http** (DECISIONE UNICA, non piu' un
  fork): ammesso per loopback (`127.0.0.0/8`,`::1`,`localhost`) **e** range privati/
  link-local (`10/8`,`172.16/12`,`192.168/16`,`169.254/16`,`.local`). Estende la
  richiesta #6 «solo loopback» per il caso reale FASTGate (router LAN http); il
  reviewer puo' restringere in un giro futuro, ma il doc **commit a un solo path**.
- **Campo assente = default STESSO SITO (rev. 14/7, regressione turn 025c53fa)**:
  se `credential_origins` manca, l'autorita' del fill e' il predicato
  `sites_origin.origin_authorized`: host uguale o sottodominio first-party
  dot-anchored del domain handle (`account.booking.com` per `booking.com`),
  `https` obbligatorio (`http` solo locali), handle `www.<root>` ancorato al
  root; IP/locali/label-singola = host esatto. La prima stesura (derive-on-read
  apex[+www]) era PIU' stretta del contratto storico → gate di consenso sui
  login first-party e loop di ri-approvazione. NESSUN fail-closed su campo
  assente; NESSUNA persistenza derivata.
- **Form creazione/modifica**: `set_credentials` guadagna un arg opzionale
  `credential_origins` (lista di stringhe); se omesso la chiave resta ASSENTE
  (regime stesso-sito); ogni voce fornita validata contro normalizzazione+scheme
  (lista vuota rifiutata). Label i18n.
- **IdP delegato**: usa il meccanismo one-shot F1 (ADR 0188) — l'origine IdP delegata
  e' login-origin ammessa **solo per quel flusso** (token one-shot, tupla esatta
  `scheme://host:port` propagata al resume), NON persistita in `origins`.
- **Matching**: fill consentito SSE `origin_authorized(origine corrente)`
  (esplicite = match esatto fail-closed, anche vuote = deny-all; assente =
  stesso-sito); altrimenti **rifiuto fail-closed**
  (`reason_code=origin_unverified`), mai fill. Sostituisce `_login_origin`
  (`credential_injection.py:465`, fold www hostname-only).

## 5. Goal tipizzato — un DTO, una regola (chiude #6)

- **Un solo carrier**: `goal_query: str|None`. **`goal_query is not None` ⟺ recupero**
  (retrieve-mode). Invariante GIA' garantito in codice: `act_sites.py:66` passa
  `goal_query=(action if goal_mode else None)`. Il broker rinomina internamente
  `goal_query`→`goal_target` (dettaglio impl.); la **condizione unica** a ogni layer
  e' `goal_query is not None`. `_goal_mode` bool = ridondante (`== goal_query is not None`):
  resta come flag runtime, MAI una seconda condizione divergente.
- **Regola canonica OPEN/READ/RETRIEVE** (SoT = `vocab.py`):
  - **OPEN** = apre sessione / naviga al sito (`open`→`open_sites`). NON recupera.
  - **READ** = legge la pagina gia' caricata della sessione (`read`→`read_sites`).
  - **RETRIEVE** = naviga post-login a un goal ed estrae record (`act_sites` con
    `goal_query`).
- **Doppia SoT (decisione unica: DERIVARE, non «allineare»)**: sostituire la tabella
  `prefilter._VERB_TO_CANONICAL` (`:38`, con «apri»→`read` errato) con una
  **derivazione da `vocab.ACTION_MAPPING`** (canonico primario per sinonimo),
  preservandone l'uso context-free del prefilter. Effetto: «apri»→`open`; «apri
  booking» → OPEN (non recupero).

## 6. Esiti e codici — precedenza deterministica (chiude #8, #4, #5, #3)

### 6.1 `reason_code` STABILE (interno) → `MSG_SITES_RC_*` (i18n)
`reason_code` NON e' una chiave i18n. Mapping esplicito codice→MSG. Precedenza
**deterministica, primo match vince** (status server-autoritativo prima del
contenuto):

| Ordine | Evidenza (soglia esatta) | reason_code | MSG i18n |
|--------|--------------------------|-------------|----------|
| 1 | status 429 OR header `Retry-After` presente | `rate_limited` | MSG_SITES_RC_RATE_LIMITED |
| 2 | status 403 | `http_forbidden` | MSG_SITES_RC_FORBIDDEN |
| 3 | status ∈ {500,502,503,504} OR response None OR `net::ERR_*` | `page_unavailable` | MSG_SITES_RC_UNAVAILABLE |
| 4 | marker captcha/2FA presente (detection_lexicon) | `challenge_observed` | MSG_SITES_RC_CHALLENGE |
| 5 | testo body visibile < 32 char **E** 0 controlli interattivi (dopo settle) | `empty_surface` | MSG_SITES_RC_EMPTY_SURFACE |
| — | altrimenti | (nessuno — pagina usabile) | — |

- **Redirect**: atterraggio su origine attesa con status 2xx = usabile (nessun
  codice). Redirect fuori origine = gestione origine (§4), non un reason_code.
- **response None** = `page_unavailable` (ordine 3).
- **Segnali multipli post-submit**: stessa precedenza (429 batte challenge batte
  empty). **Prerequisito**: recuperare la risposta di `page.goto` (oggi scartata).
- **MAI** dedurre `automation_blocked` dal solo status.

### 6.2 Esiti post-submit (login) — solo due alimentano il cooldown

| Esito | Definizione strutturale (deterministica) | Cooldown |
|-------|------------------------------------------|----------|
| `login_verified` | postcondizione positiva (area account/sessione) | **reset** |
| `credentials_rejected` | regione errore ESPLICITA (role=alert / aria-live=assertive / container noto) + testo rifiuto via detection_lexicon **E** password ancora presente stessa origine | **+1** |
| `rate_limited` | §6.1 ordine 1 | **+1** |
| `challenge_observed` | §6.1 ordine 4 (2FA/CAPTCHA corretta) | **NO** |
| `login_inconclusive` | nessuna delle sopra deterministicamente vera (remount/timeout) | **NO** |

**Incremento cooldown = ESCLUSIVAMENTE `credentials_rejected` e `rate_limited`.**
`login_inconclusive` NO. `challenge_observed` NO. `empty_surface` NO. (chiude #3/#4/#5)

## 7. Cooldown anti-lockout — specifica ESEGUIBILE (chiude #5, #9)

- **Dove**: broker. **Chiave**: `(owner, binding_id, credential_fp)`.
  - `credential_fp` = **`credentials.fingerprint(domain)`** (ESISTE: sha256[:16] della
    pwd, gia' usato in audit — NESSUN nuovo HMAC/server_key/rotazione).
  - `binding_id` = origine di login normalizzata `"scheme://host:port"` (§4).
- **Conta SOLO** `credentials_rejected` / `rate_limited` (§6.2).
- **«Consecutivi»**: `fail_count` accumula ogni fallimento eleggibile **dall'ultimo
  `login_verified`**; ragioni miste sommano entrambe; `last_reason` = piu' recente;
  `login_verified` → DELETE riga (→0).
- **Formula esatta**: `cooldown_s = 0 if fail_count < N else min(CAP, BASE * FACTOR**(fail_count-N))`.
  Default `N=2, BASE=900, FACTOR=3, CAP=43200` → 2:900 · 3:2700 · 4:8100 · 5:24300 ·
  ≥6:43200. Env bounded: `METNOS_SITES_COOLDOWN_{N(1-5),BASE_S(60-3600),FACTOR(2-5),CAP_S(3600-86400)}`.
- **Schema** (`sites_cooldown.sqlite` sotto state dir da `runtime/config.py`, WAL,
  `PRAGMA busy_timeout=5000`):
  ```sql
  CREATE TABLE sites_cooldown(
    owner TEXT NOT NULL, binding_id TEXT NOT NULL, credential_fp TEXT NOT NULL,
    fail_count INTEGER NOT NULL DEFAULT 0, last_reason TEXT NOT NULL,
    cooldown_until INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
    PRIMARY KEY (owner, binding_id, credential_fp));
  ```
- **Upsert transazionale** (atomico, `now` = `time.time()` passato):
  ```
  with conn:                                   # BEGIN IMMEDIATE (isolation_level=None + explicit, o context-manager)
    row = SELECT fail_count WHERE key
    fc  = (row.fail_count if row else 0) + 1
    cd  = 0 if fc < N else min(CAP, BASE*FACTOR**(fc-N))
    UPSERT(key, fail_count=fc, last_reason=reason, cooldown_until=now+cd, updated_at=now)
  ```
  **Reset**: `with conn: DELETE WHERE key`. **Check**:
  `row=SELECT cooldown_until WHERE key; in_cooldown = bool(row) and row.cooldown_until > now`.
  **TTL**: righe con `updated_at < now-86400` purgate opportunisticamente all'accesso.
- **Esposizione**: interattivo → `MSG_SITES_COOLDOWN_ACTIVE` (i18n, `retry_after_s`,
  binding redatto); task/schedulato → **fail-closed** (ADR 0190), `sites_cooldown_active`
  in `failed[]`.

## 8. Gestore ostruzioni — due procedure, locator e budget deterministici (chiude #8-round1, #9, #2)

### 8.1 Due procedure (autorita' diversa)
- **A. Privacy/cookie** (proattiva, stretta, **solo non-navigante**): classe nota
  sicura; target di rifiuto esatto tradotto. Se l'unico controllo naviga/fa submit →
  §8.3 (piano firmato), NON dismissione silenziosa.
- **B. Generica** (reattiva): chiusura SOLO su target NOTO **geometricamente occluso**,
  con le 5 condizioni §8.2. Mai su nudo `selector_missing`.

### 8.2 Locator sicuro + occlusione (deterministici — chiude #9)
**Divieti locator dismissione** (nessuna ambiguita'): candidato NON deve essere un
submitter di form. **Vietati**: `input[type∈{submit,image}]`; `button[type=submit]`;
`button` **senza attributo `type`** dentro un `<form>` (submit implicito); `<a>` con
href navigante o `javascript:`. **«Link navigante»** = `<a>` con `href` non vuoto e
non puro fragment same-page (`#...`). **Ammessi**: `button[type=button]`,
`[role=button]` non-submitter, X/label di chiusura senza navigazione.

**Occlusione target noto — 5 condizioni (tutte vere prima del click)**:
1. target **renderizzato** (box non nullo, non display:none/visibility:hidden/opacity<0.05);
2. target **non topmost**: `document.elementFromPoint(cx,cy)` al centro ritorna un
   nodo ≠ target e non-discendente;
3. controllo di chiusura nello **stesso modal**: condivide l'antenato piu' vicino
   `[role=dialog]`/`[aria-modal=true]`/**fixed-topmost** col nodo occludente.
   **«fixed-topmost»** = element con `position ∈ {fixed,sticky}` che e' topmost al
   proprio centro (`elementFromPoint` ritorna esso o un discendente);
4. **firma DOM stabile**: `sha1(tag|role|round(x),round(y),round(w),round(h)|childElementCount|firstText64)`
   (firstText64 = primi 64 char normalizzati di aria-label o textContent) IDENTICA su
   due osservazioni a `_MODAL_SETTLE_MS = 250` di distanza (non a meta' animazione);
5. il controllo di chiusura passa i divieti locator sopra.
Dopo: ri-osserva; target topmost → prosegui; altrimenti consuma budget e fermati.

### 8.3 Controllo navigante → piano firmato (non «ignorato» — chiude #9)
Quando l'unico controllo di dismissione e' un submitter/navigante, il gestore NON
dismette e NON ignora: ritorna l'osservazione
`{obstruction:"navigating_control", control:Control(action_id,role,name,form_origin)}`.
Il runtime la trasforma in un passo di **piano firmato** via il gate F2
`get_approval` (batch), presentando screenshot redatto + descrizione controllo.

### 8.4 Budget (valori, consumo, reset — chiude #2)
- `_MAX_PRIVACY_DISMISSALS = 2` (ESISTE, `:145`) — solo privacy proattiva.
- `_MAX_GENERIC_DISMISSALS = 3` — solo target occluso §8.2.
- `_MAX_TOTAL_DISMISSALS = 4` — tetto **assoluto** di sessione (privacy+generica).
- Env bounded: `METNOS_SITES_DISMISS_{PRIVACY,GENERIC,TOTAL}`.
- **Consumo**: ogni TENTATIVO (riuscito o no) decrementa il contatore di classe **e**
  il totale. Esaurito un tetto applicabile → niente altre dismissioni di quella
  classe → osserva o stop onesto.
- **Reset**: per-sessione, **nessun reset entro la sessione** (un loop su modali si
  ferma, non si azzera).
- **Interazione**: le dismissioni NON consumano `_MAX_GOAL_STEPS = 4` (che conta
  navigazioni/azioni goal). Wall-clock gia' bounded da timeout per-op (§1.1) + TTL
  sessione 15m (F1).

## 9. Persistenza adattiva (entro il mandato) — bounded

- Su `selector_missing` del goal, DOPO §8: reveal alternativo area account
  (`sites.account_reveal_control`, esiste) se goal personale; poi **una sola** passata
  modello su metadati broker-owned. Dedup `goal_candidate_key` (esiste,
  `action_resolver.py:417`); bounded `_MAX_GOAL_STEPS`. Coerente col cooldown (§7):
  niente ri-esposizione credenziali a raffica.

## 10. Piano test (chiude #3, #10)

### 10.1 Due livelli E2E — ordine coerente (chiude #10)
- **(a) E2E SIMULATORE** (`test_sites_login_simulator.py`, `METNOS_SITES_SIM=1`): HTTP
  locale + Chromium reale + broker + traversata planner/riduttore. **E' un turno
  reale** (browser+broker veri) e **soddisfa §8.5** senza toccare account esterni →
  e' il **Done gate per-prerequisito**.
- **(b) E2E REALE ESTERNO** (Booking): SOLO **dopo** il prerequisito 6 (cooldown), per
  non rischiare lockout. Non e' in conflitto con (a): «E2E per ogni cambio» = livello
  (a); «niente reale esterno prima di §7» = livello (b).
- I test sono **per-prerequisito** (Done gate) **e** consolidati a fine (§10.2/10.3).

### 10.2 Funzionali + sicurezza (simulatore)
1. Privacy proattivo **non-navigante** che riappare → login, dismissioni ≤ tetto privacy.
2. Consenso **navigante** → §8.3 (gate), non dismesso in silenzio.
3. Generica su target occluso (5 condizioni §8.2) → dismesso, record raggiunti.
4. Nudo `selector_missing` senza target noto → **nessun** `overlay_dismiss`.
5. `empty_surface` (soglia §6.1) → codice neutro, nessun ritento.
6. **Cooldown (senza contraddizioni)**: assert espliciti — `credentials_rejected`→+1;
   `rate_limited`→+1; `login_inconclusive`→**invariato**; `challenge_observed`→**invariato**;
   `login_verified`→reset. Concorrenza: due submit sulla stessa chiave → un solo
   incremento coerente (`BEGIN IMMEDIATE`).
7. Catena completa con `goal_query` che **ATTESTA** la pagina-obiettivo.
8. Sicurezza: origine schema/porta (`http://h`≠`https://h`, porte≠, IDNA/dot/IPv6);
   `http` pubblico negato, privato/loopback ammesso; con origini ESPLICITE ogni
   alias fuori lista negato (match esatto); con chiave assente stesso-sito
   first-party ammesso, altro sito registrabile negato;
   locator: submitter impliciti + js-link **rifiutati**; stealth su browser
   **riavviato** (routing per variante, non env); cooldown persistente dopo restart.
9. Igiene: ripristinare le global monkeypatchate a fine test.

### 10.3 Reale (post-§7): Booking «login e mostra le prenotazioni» → catena completa;
attesta la pagina; record o vuoto onesto con evidenza. Non martellare account reali.

## 11. Non-goals

- Default = nessun occultamento; stealth (§2) opt-in sperimentale, **non «lecito»**.
- Fingerprint headless = **NON SUPPORTATO in headless**; superficie `extension` (§3)
  = via onesta opt-in (fase separata).
- Fuori scope: canali **API** ed **email**. Estensione companion = **IN** (C1).

## 12. Prerequisiti bloccanti (ordine) + Done gate (simulatore)

0. **Seam `BrowserSurface`** (§1.1) — estrazione pura; suite verde invariata.
1. **Website browsing** superficie+registro stealth+propagazione end-to-end (§2); rimuovi il
   `webdriver` defineProperty. Done: test §10.2-8 (stealth su restart).
2. **`origins` credenziale** (§4) + migrazione derive-on-read. Done: §10.2-8 origine.
3. **Goal DTO unico + derivare `_VERB_TO_CANONICAL` da vocab** (§5). Done: «apri
   booking»=OPEN, «mostra prenotazioni»=RETRIEVE (simulatore).
4. **Status `page.goto` + tabella precedenza §6.1**. Done: §10.2-5 (`empty_surface`).
5. **Locator vieta submitter/js-link (§8.2) + contratto navigante→piano (§8.3)**.
   Done: §10.2-2, §10.2-8 locator.
6. **Cooldown eseguibile (§7)**. Done: §10.2-6. **Sblocca l'E2E reale esterno.**

Solo DOPO: gestore ostruzioni due-procedure+budget (§8), persistenza (§9), suite
completa (§10.2/10.3), giro adversarial finale. Superficie `extension` (§3) = fase
dedicata successiva.
