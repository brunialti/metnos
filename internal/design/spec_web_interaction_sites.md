# SPEC — Interazione web sicura con siti (dominio `sites`)

> **Stato**: IMPLEMENTATA E VALIDATA (11/7/2026, ADR 0188). Fasi F1/F2 e decisioni §11 D-A..D-E RATIFICATE da Roberto (10/7 sera, sessione Fable). Documento vivo. La stesura iniziale è stata sottoposta a **red-team adversarial**: 20 problemi (3 CRITICI di sicurezza) → integrati come design corretto qui sotto e sintetizzati nella **Review Fable §12**.
> **Origine**: Roberto — «funzioni generali per interagire con siti: login con credenziali gestite in sicurezza, esplorare, compiere azioni; delicato, sicuro, facile da usare». Benchmark: Hercules, Browser-Use, Stagehand, Skyvern.
> **Implementazione**: completata su ordine esplicito di Roberto; broker, executor, i18n, test ed E2E sono riepilogati in ADR 0188.
> **Verdetto Fable (§12)**: la Fase 1 (login+lettura) è realizzabile in sicurezza SE si adottano gli irrigidimenti §3-§4 qui integrati. La superficie di rischio REALE non è «il modello vede il segreto» ma **la destinazione della credenziale e la cattura del segreto negli screenshot** — riprogettati sotto.

---

## 0. Contesto minaccia (domina il design)

Il **prompt injection indiretto** è la minaccia dominante: testo ostile nascosto in una pagina (bianco-su-bianco, commenti HTML, iframe, alt-text) dirotta l'agente e gli fa usare le **credenziali dell'utente**. Attack success rate ~84% sui modelli base; OpenAI: «potrebbe non essere mai risolto del tutto». Opus 4.5 scende a ~1% via RL — ma **Qwen 3.6 locale NON ha quella robustezza** → i presidi sono DETERMINISTICI, mai «ci fidiamo del modello».
Fonti: OpenAI hardening-Atlas, Brave (Comet injection), 1Password (credential risk gap), cyberyozh 2026, arXiv plan-then-execute 2605.14290.

**Correzione dopo red-team — la superficie vera:** l'iniezione-nel-broker protegge il segreto dall'LLM, **non dalla sua DESTINAZIONE**. Un attaccante non deve leggere la password: gli basta far sì che il broker la digiti in un campo che lui controlla su un dominio già-allowlisted, e un listener same-origin la esfiltra (nessun cross-domain). Quindi il presidio #1 da solo NON basta: serve **verifica deterministica dell'origine del form** + **niente segreto negli screenshot**.

## 1. Panorama e scelta di fondo

| Scuola | Esempi | «Vede» | Trade-off |
|--------|--------|--------|-----------|
| DOM-driven | Browser-Use (89%) | DOM/accessibilità → LLM | preciso; fragile UI custom |
| Deterministico-first | Stagehand | Playwright per i noti, LLM sui fuzzy | affidabile; serve «ricette» |
| Vision | Skyvern (86%) | screenshot → VLM | robusto; più caro; **rischio: il VLM vede tutto** |

**Scelta Metnos: IBRIDO deterministico + vision, MAI DOM-nudo-LLM.** Playwright come motore (già in casa), VLM SOLO come fallback di risoluzione elemento e SOLO su pagine non-autenticate/pre-fill (§3.3). Le azioni sono un **vocabolario chiuso e verificabile**: l'LLM sceglie fra azioni tipizzate, non «clicca dove pare».

## 2. Cosa Metnos ha GIÀ (riuso) — con path

- **Vault cifrato** — `runtime/credentials.py`: Fernet per-dominio (`~/.config/metnos/credentials/<d>.json.age` 0600), chiave HKDF da `admin.key`; `store/load(domain)/list_domains/remove/fingerprint`. Regola `*_credentials` **metadata-only** + `assert_no_secrets_in_return` (`credentials.FORBIDDEN_KEYS`) + Vaglio. Mail/NAS leggono già i segreti via `credentials.load`. → Presidio #1 infrastrutturato.
- **Motore** — `runtime/playwright_sidecar/server.py`: UN Chromium headless persistente (~200MB), oggi `POST /render {url}`→testo/html, contesti usa-e-getta. Client `client.py`. Install `install/sidecar.py`.
- **Cookie-session** — `executors/login_session/`: jar Netscape in `~/.config/metnos/cookies/<d>.txt` (0600, ADR 0082), re-iniettato via `auth_cookies_file`.
- **VLM** — `runtime/vlm_client.py::describe_image` con `qwen3vl-2b` locale (config `~/.config/metnos/vlm_tiers.toml` via `virt.get_vlm()`).
- **HITL** — `executors/get_approval/` (2 bottoni + `on_approve`), `orchestration._process_gate_dispatch`/`_process_resume_engine_gate`, `approval_registry.py` (sqlite).
- **Sandbox** — `runtime/sandbox.py` bwrap: rete gated da capability. Stato fra invocazioni = file, executor stateless.
- **Web READ** — `get_urls`, `read_urls_html`/`read_urls_pdf` (con `auth_cookies_file`, `js_render`), `find_urls`.

## 3. Il DELTA (indurito post-review)

### 3.1 Session-broker (estensione del sidecar)
- **Contesti nominati e persistenti**: `POST /session/open {session_id, allowlist:[domini]}` → `browser.new_context()` isolato, registry in-memory `{session_id: {context, allowlist, created, last_used, gate_pending: bool, factor_pending: bool}}` con **TTL idle** (default 15 min) e cleanup.
  - **[FIX A — restart/crash]** `session_id` VALIDATO a ogni operazione; se assente/scaduto → risposta `{ok:false, error_class:"session_lost"}` esplicita (§2.8). L'engine può riaprire+ri-loggare. MAI proseguire su un context morto in silenzio.
  - **[FIX B — TTL vs HITL]** il TTL si METTE IN PAUSA finché `gate_pending=true` oppure `factor_pending=true` su quella sessione (attesa OTP/approvazione può superare 15 min). I due stati non sono sovraccaricati: un codice OTP può riprendere `factor_pending` senza essere rifiutato come approvazione mancante. Reap-safe e limite assoluto di un'ora.
  - **[FIX C — concorrenza]** timeout per-operazione (default 20s), cap context concorrenti (default 4), quota per-utente (default 2), handling asincrono così un'operazione appesa NON stalla le altre; fairness in coda.
- **Operazioni** (`{session_id,...}`→JSON): `goto`, `screenshot`, `read`, `click`, `fill`, `submit`, `wait`, `close`.
- **Confine di rete PER-SESSIONE**: `context.route()` ABORTA fuori-allowlist. **[FIX D]** oltre a route(): disabilitare **WebRTC** (flag Chromium — canale STUN/TURN esfiltra fuori banda), BLOCCARE navigazione top-level `data:`/`blob:`, allowlist per hostname con nota che **DNS-rebinding e same-origin exfil restano possibili** → per questo il vero presidio sull'esfiltrazione è il gate tainted-turn su `submit` (§4.2/§4.5), non route().
- **Risoluzione elemento**: (a) accessibilità (role+name/label) deterministica; (b) fallback VLM su screenshot — con i vincoli §3.3. Il DOM grezzo NON va al planner.
- **Executor intelligente drop-in**: il planner continua a conoscere soltanto
  input e output pubblici dell'executor. Quando una singola azione richiede
  adattamento, l'executor puo' iterare internamente
  `observe -> propose -> verify -> gate -> execute -> observe` entro un budget
  chiuso. `login_sites`, senza aggiungere step al piano, attraversa landing,
  consenso privacy opzionale, ingresso login anche fuori viewport, username
  opzionale, continuazione, password, 2FA e verifica esito. Le SPA lente sono
  riosservate deterministicamente prima dell'unico fallback a modello. Il prompt
  contiene `goal`, `state`, `observed`, `history`, `constraints`; la primitiva
  resta fissata dal codice e il modello ritorna soltanto un `candidate_id`
  broker-owned. I valori in `observed` sono dati di pagina non fidati.
- **UI custom e popup**: accessibilita' prima; poi geometria/topmost e nodi
  foglia visibili `cursor:pointer`. Un reveal richiede nuova osservazione e
  firma stabile. Popup multipli falliscono `popup_ambiguous`; un popup unico
  fuori allowlist viene chiuso e il solo host document osservato passa dal gate.
  Dopo replay il popup consentito diventa la pagina attiva.

### 3.2 Iniezione credenziali — RIPROGETTATA (i 3 CRITICI del red-team)
- **[CRITICO-1 — anti-phishing]** il broker inietta una credenziale SOLO se: (1) l'origine dell'`action` del form di login coincide ESATTAMENTE con il dominio del vault oppure con una origine delegata approvata tramite token one-shot sulla coppia esatta; (2) il campo è nel **frame top-level** (MAI iframe); (3) origine e DOM sono verificati PRIMA di digitare. Mismatch → RIFIUTO (`error_class:"origin_mismatch"`), niente digitazione. Deterministico, non affidato al VLM.
- **[CRITICO-2 — la destinazione non la sceglie l'LLM]** per i `value_ref` di tipo `cred:<domain>:<field>` il broker **IGNORA** ogni selettore proposto dall'LLM/planner e risolve AUTONOMAMENTE il campo credenziale legittimo dell'origine attesa. L'LLM non può mai dirigere dove va una credenziale. Un `value_ref:cred:` è ammesso SOLO sul campo che il broker stesso ha identificato come credenziale di quell'origine.
- **[CRITICO-3 — niente segreto negli screenshot]**: (a) MAI `screenshot` fra un `fill` credenziale e il `submit`; (b) **redazione deterministica** dei campi input `type=password` E dei campi appena riempiti dal broker con credenziali/OTP, PRIMA di ogni capture (overlay nero via CSS injection pre-shot); (c) la risoluzione VLM avviene SOLO su screenshot **PRE-fill**; (d) **VLM FRONTIER (Opus) VIETATO** su qualsiasi pagina in contesto autenticato o di credenziale (solo VLM locale, che comunque non deve mai vedere un campo segreto in chiaro).
- **URL-scrub [FIX E]**: da ogni `url`/`final_url` restituito/loggato/persistito, scrub deterministico dei parametri sensibili (`token,code,access_token,id_token,ticket,sig,saml,otp,session,auth`) in query E fragment. Un token nell'URL è un segreto.
- **2FA a canali** — il broker classifica il canale dalla pagina e invoca un
  resolver ristretto; input/output pubblici di `login_sites` non cambiano.
  Primo canale: `email`. L'automazione e' consentita solo se il binding web ha
  `sites.read` e l'identita' coincide esattamente con una singola mailbox.
  Prima del submit viene catturato un cursore UID di Inbox e cartelle marcate
  `\\Junk`; dopo il submit sono ammessi solo UID nuovi, pertinenti al dominio
  emittente e con semantica di fattore non ambigua. Ogni operazione IMAP ha un
  timeout socket, il polling ha deadline propria e il budget login e' assoluto.
  Il generico `read_messages` non viene riusato. Fallimento, ambiguita' o scope
  assente → `needs_inputs`, mai codice scelto per somiglianza. Il TOTP resta
  opt-in con `totp_secret`; CAPTCHA e push restano handoff umani. Nessun valore
  di fattore entra in planner, result, audit o screenshot.

### 3.3 Screenshot — ciclo di vita [FIX F]
- Dir temp `~/.local/share/metnos/sites-shots/<owner>/` **0700**; file 0600; **cleanup a TTL** (default 30 min); ACL per-owner (un utente non vede gli shot di un altro — riuso del signed-URL per-owner di `photo_endpoint`).
- **NIENTE doppia persistenza**: il result porta il `screenshot_path` (+ signed URL), NON il base64 nel JSON del turn-record.
- Contenuto autenticato negli screenshot → marcato `sensitive:true` → describe resta LOCALE, mai frontier (§3.5).

### 3.4 Executor `sites` — VETTORIALI (contratto §2.1 corretto)
Tutti: manifest §2.5 IT+EN, i18n §7.13 (3 posti), firma §7.10, onestà §2.8. **[FIX G — vettorialità]** operano su `session_ids: array[str]` (o `from_step`), con fan-out: una entry per sessione. `open_sites` produce N sessioni → i successivi le consumano tutte (o l'utente ne cita una).

- **`open_sites`** (F1): args `urls: array[str]`, `session_label` opz., `allowlist: array[str]` opz. (default = domini esatti degli urls — **D-D: esatto**). OUT `entries=[{session_id,url,title,ok}]`.
- **`login_sites`** (F1, intelligente drop-in): args `session_ids`|`from_step`; `domain` opz. (handle vault; default = origine login della pagina, verificata §3.2); `form_hint` opz. Attraversa autonomamente consenso privacy, ingresso login, flussi username-first/continue, password e fattori entro un deadline condiviso. Ogni submit separa dispatch (`no_wait_after`) e osservazione; i checkpoint sono `discovering|username_submit|primary_submit|factor_pending|factor_resolving|factor_submit|complete|failed`. OTP email puo' essere risolto come sopra; gli altri OTP/CAPTCHA/push producono handoff redatto; TOTP e' opt-in nel vault. OUT `entries=[{session_id,logged_in:bool,reason_code?}]`, invariato. `critical=true`. **Zero segreti nel result** (`reason_code` = codice i18n, MAI username/password).
- **`read_sites`** (F1): args `session_ids`|`from_step`; `include_screenshot` def true; `include_forms` def false. OUT `entries=[{session_id,url,title,text,screenshot_path,sensitive}]`.
- **`act_sites`** (F2): args `session_ids`|`from_step`; `action: str` (NL); `value_ref` opz. Oltre alle primitive singole, `search` mantiene un goal post-login, attraversa menu con riosservazione bounded e applica filtri finali. Dopo il goal puo' seguire controlli contestuali di continuazione/load-more/next fino a 6 volte, soltanto finche' il contenuto cambia; pagine distinte sono aggregate per la lettura finale. Un modello locale puo' scegliere soltanto un ID da nomi/ruoli broker-owned. Gate §4.2. OUT `results`. `critical=true`, `revertible=false`.

### 3.5 Contenuto autenticato — no-frontier [MEDIO red-team]
Le entries `sites` con `sensitive:true` (contenuto post-login: saldi, dati personali) NON passano mai al VLM/LLM frontier: describe/sintesi restano LOCALI. Taint `no_frontier` propagato dal `read_sites` autenticato al describe a valle.

## 4. I 5 PRESIDI (adozione IMPOSTA da Roberto) — con gli irrigidimenti

1. **L'LLM non vede il segreto** — §3.2 (iniezione broker + origine verificata + destinazione non-LLM) + §3.3 (no segreto negli shot, no frontier) + regola metadata-only + Vaglio. **Verifica automatica**: test che grep-a payload-executor, result, turn-record e prompt-planner per le chiavi `credentials.FORBIDDEN_KEYS` e i valori del vault di test → 0 hit; test che il VLM non riceve mai uno shot con campo credenziale non-redatto.
2. **HITL su azioni sensibili** — **[FIX H — classificazione sul TARGET, non sul testo]** `act_sites` classifica SENSIBILE sull'elemento RISOLTO (role/testo del bottone, `form.action`, metodo POST), deterministico sul DOM — NON sulla frase NL (aggirabile con «tocca in basso a destra»). Default-SENSIBILE ogni azione che innesca navigazione/submit/POST/download a prescindere dal fraseggio. Gate = `get_approval` con **screenshot redatto** + descrizione. **[FIX usabilità]** approvazione **BATCH** per un'azione multi-passo descritta («compila e invia il form» = UN gate con l'intento intero), non un gate per primitiva.
3. **Allowlist domini** — doppio confine: `route()` (§3.1) + capability `network:sites` (hint=allowlist). **D-D: default = dominio ESATTO** (no sottodomini, riduce l'esposizione della credenziale). Estensione = solo `get_approval`. + no-WebRTC, no data:/blob: top-level (§3.1 FIX D).
4. **Sessioni effimere, credenziali con mandato** — TTL idle + cleanup; una credenziale è caricabile soltanto dal binding vault richiesto. La destinazione deve coincidere con quel dominio o con una origine delegata approvata e ricontrollata nella sessione (§3.2); nessun alias viene creato implicitamente. Per i binding web il form di inserimento sceglie `interactive` oppure `sites.read`; lo scope e' cifrato con i campi, vale come default per ogni query interattiva e puo' essere aggiornato senza reinserire il segreto. `sites.read` autorizza anche il solo recupero del fattore email strettamente necessario al login, ma esclusivamente dalla mailbox con identita' esatta; non concede ricerca o lettura mail generale. La query puo' restringerlo; un ampliamento richiede consenso one-shot e non persiste. Nei task vale inoltre l'intersezione con l'envelope ADR 0190 (actor, query hash, host esatti e operazioni): fuori scope il fire fallisce senza aprire dialoghi.
5. **Tainted-turn** — flag `web_content_ingested` al primo `read/goto` di contenuto esterno; da lì OGNI `act_sites` exfil-capace richiede approvazione. **[FIX I — cache]** il taint DEVE essere ricostruito DETERMINISTICAMENTE a ogni esecuzione, MAI dedotto dal cache-path; `sites` **ESCLUSO** dall'L0 arg-caching; **`session_id` MAI cachato**; lo scheletro L1 riapre SEMPRE sessione fresca e ri-fa login (§7 cache).

## 5. Vocab (`sites` — RATIFICATO, escalation §2.2)
- Nuovo oggetto `sites` in `vocab.py::OBJECTS`. Confine manifest: `NON: pagina senza login = read_urls_html; sites = sessione con stato/credenziali`.
- **Verbi — D-A RATIFICATA ✓ (10/7)**: token NUOVI in `vocab.py::ACTIONS` = `login`, `open`, `act` (`read` esiste già; precedente: `login_session` è già builtin fuori-grammatica). Mappatura forzata sui canonici RESPINTA (ambigua). Enforce in `naming_grammar` (legge §10.4).
- Lessici (phrases, §7.13): `sites.reference` («sul sito/portale», «accedi a»), `sites.sensitive_action` (§4.2, ma la classificazione VERA è sul DOM §FIX-H, il lessico è solo un hint aggiuntivo).

## 6. UI
Screenshot (redatti) di `read_sites`/gate → `attachments`/gallery esistenti (`photo_endpoint` signed URL per-owner). L'utente **vede** la pagina e, sul gate, cosa l'agente sta per fare, prima di approvare.

## 7. Fasi + interazione con la cache
- **FASE 1 — login+lettura** (RATIFICATA): broker (open/goto/read/screenshot/login/close), iniezione §3.2, presidi 1/3/4/5, executor `open/login/read_sites`. NIENTE mutazioni.
- **FASE 2 — azioni** (RATIFICATA, dopo F1): `act_sites`, presidio #2 (gate+screenshot+batch), classificazione DOM, tainted-turn pieno.
- **Cache (critico)**: `sites` fuori dall'arg-caching L0; L1 = solo scheletro (riapre+rilogga); `session_id`/`domain`/`value_ref` MAI persistiti in un piano cachato (dangling + riuso cross-utente di sessione autenticata = bug di correttezza E sicurezza).

## 8. MVP di validazione
Un sito reale di Roberto, F1: `open_sites`→`login_sites(domain=…)`→`read_sites`→screenshot in chat + testo. Valida i due pezzi rischiosi: persistenza context nel broker (FIX A/B/C) e iniezione-credenziale senza leak (FIX §3.2/§3.3). **Criteri**: (1) segreto assente da log/turn-record/prompt/shot (grep + ispezione VLM-input); (2) rifiuto su origine-mismatch (test con un form finto cross-origin); (3) login riesce, screenshot redatto arriva in chat.

## 9. Lacune COLMATE (dal red-team) + non-goals
- **Audit-log** [ALTO]: log append-only dedicato `sites` (`~/.local/state/metnos/sites_audit.jsonl` 0600): sessione aperta, dominio, tentativo login (esito, MAI credenziale), ogni `act`, ogni uso credenziale (fingerprint, non valore), ogni modifica allowlist. Per incident response — il turn-record non basta.
- **Revoca / kill-switch** [ALTO]: `close_sites{all}` + comando «revoca tutte le mie sessioni web» + abort d'emergenza su sospetta injection (il gate rifiutato dall'utente → kill della sessione).
- **Tassonomia fallimento** [MEDIO]: codici canonici inglesi (`password_wrong|account_locked|two_factor_required|captcha_required|origin_unverified|selector_missing|session_lost`) → messaggio utente localizzato, MAI eco di credenziali.
- **CAPTCHA / 2FA-push / fattore non risolto** [MEDIO]: rilevati → messaggio onesto «serve il tuo intervento / conferma sul dispositivo», screenshot redatto e sessione aperta → cede il controllo, MAI appeso. Un TOTP viene compilato solo se il dominio ha un `totp_secret` opt-in nel vault; email usa il resolver bounded §3.2; codice, campo e origine restano nel broker.
- **Selector drift** [MEDIO→sicurezza]: catena accessibilità→VLM→fallimento onesto; su pagina sensibile MAI click a indovinare; bassa confidenza sull'elemento → gate, non azione.
- **Non-goals F1**: azioni mutanti; download; CAPTCHA-solving automatico;
  sessioni oltre TTL (D-B). Il popup singolo necessario alla navigazione F2 e'
  gestito dal broker; non e' navigazione multi-tab libera.

## 10. LEGGI VINCOLANTI (violarle = PR respinta) — CLAUDE.md
1. Re-sign §7.10 dopo ogni edit executor/manifest (da root), commit manifest+sig insieme.
2. E2E reale §8.5: ≥1 turno reale per dominio toccato; MAI adattare la query.
3. i18n §7.13 in 3 posti; placeholder `{key}` VIETATO.
4. Vocab chiuso §2.2: oggetto `sites` + verbi `login`/`open`/`act` RATIFICATI (§11 D-A); enforce in `naming_grammar`; NESSUN altro token senza nuova escalation a Roberto.
5. Onestà §2.8: nessun `logged_in:true` non verificato; nessun segreto nel result; screenshot reali e redatti.
6. **Segreti**: MAI nel payload/result/turn-record/prompt/log/screenshot. Solo `credentials.load` DENTRO il broker, con origine verificata.
7. Lessici solo phrases (traducibili), no regex, no sinonimi hardcoded nel routing.
8. `sites` gira solo server (no `device_ok`); rete ristretta all'allowlist esatta.
9. **Verifiche di sicurezza automatiche** obbligatorie nei test (§4.1 verifica + §8 criteri): sono parte del contratto, non opzionali.

## 11. DECISIONI — RATIFICATE ✓ (Roberto, 10/7/2026 — opzione = proposta Fable per tutte e 5)
- **D-A ✓** — verbi NUOVI nel vocab chiuso: `login`, `open`, `act` (+ `read` esistente) e oggetto `sites` (§5). Mappatura forzata sui canonici RESPINTA (ambigua). L'escalation §2.2 è assolta da questa ratifica; NESSUN altro token senza nuova escalation.
- **D-B ✓** — ri-login a ogni sessione = DEFAULT; riuso cookie-jar = opt-in esplicito per-dominio (jar 0600 ADR 0082; in F1 il default basta — vedi non-goals §9).
- **D-C ✓** — VLM locale-only in F1 (frontier resta VIETATO in contesto autenticato/credenziale, §3.2); un eventuale frontier su pagine PUBBLICHE pre-login si rivaluta in F2, non prima.
- **D-D ✓** — allowlist default = dominio ESATTO (come §4.3); estensione SOLO via `get_approval`.
- **D-E ✓, estesa 13/7/2026** — 2FA: `needs_inputs` resta il fallback universale. `totp_secret` e' opt-in esplicito per-dominio. Il canale email puo' essere risolto automaticamente soltanto sotto `sites.read`, binding mailbox esatto, cursore UID pre-submit e correlazione issuer; ogni incertezza torna al fallback umano. MAI screenshot del campo OTP.

## 12-bis. USABILITÀ — vincolo di pari rango (Roberto: «facile da usare»)

La sicurezza NON deve trasformare ogni compito in una fila di conferme. La usabilità è un requisito, non un residuo. Principi PRESCRITTIVI (misurabili):

- **Budget-conferme**: un `login+lettura` (Fase 1) = **ZERO gate** nel caso normale (il login non è un'azione mutante/outbound; la lettura non esfiltra). Le conferme esistono solo in Fase 2 e solo su azioni sensibili. Target: un flusso «accedi al portale X e dimmi il saldo» = **un turno, nessun click di approvazione**.
- **Gate BATCH, mai per-primitiva** (§4.2): «compila e invia il form» = UN gate con screenshot + intento intero, non uno per campo. Un compito multi-passo = al più UNA conferma.
- **Ricorda-nella-sessione**: entro lo stesso task/sessione, un'approvazione su un dominio non si richiede a ogni azione ripetuta identica (approvo «invia» una volta per quel form, non a ogni retry). Il taint alza il gate su azioni NUOVE exfil-capaci, non ripete quelle già approvate.
- **Ri-login trasparente**: TTL scaduto a metà task → il broker ri-usa il cookie-jar (D-B opt-in) o ri-fa login SILENZIOSAMENTE con la credenziale del vault (nessun re-prompt), tranne se serve 2FA. L'utente non deve accorgersi della scadenza.
- **Linguaggio naturale, non selettori**: l'utente dice «accedi a Spaggiari e leggi i voti», non parla di sessioni/id. Il `session_id` è interno; il planner lo cabla via `from_step`. «il sito X» → `sites.reference` risolve il dominio.
- **Progresso visibile**: lo screenshot in chat (§6) è UX, non solo audit — l'utente vede cosa sta facendo l'agente, riduce l'ansia da «cosa sta combinando».
- **Default comodi e sicuri**: credenziale salvata una volta (dominio → vault via `set_credentials`), poi «accedi a X» non richiede più nulla. Il fattore email esatto puo' essere risolto internamente sotto mandato; 2FA non risolvibile, CAPTCHA e push restano l'attrito ricorrente esplicito.
- **Fallimenti chiari, mai muti** (§9 tassonomia): «password errata su X» / «serve la conferma 2FA sul telefono» — l'utente sa sempre cosa fare, non riceve un errore criptico.

**Anti-obiettivo dichiarato**: se in Fase 1 un login+lettura richiede più di zero conferme nel caso normale, il design ha SBAGLIATO il bilanciamento — la sicurezza della sola-lettura non giustifica attrito. Le conferme sono un budget scarso da spendere solo dove c'è effetto irreversibile/outbound reale (Fase 2).

## 12. REVIEW FABLE — sintesi
La stesura iniziale è stata red-teamata (20 reperti, transcript non allegato). Giudizio:
- **I 3 CRITICI erano reali e strutturali**, non nit: (1) il broker digitava la password in un campo «che crede» sia password → phishing/injection cattura la credenziale su un dominio già-allowlisted (esfiltrazione same-origin, non serve cross-domain); (2) era l'LLM a scegliere il selettore-destinazione del `value_ref` → injection dirige dove va il segreto; (3) screenshot + VLM (soprattutto frontier) catturavano OTP/campi in chiaro → il presidio #1 falliva per una via laterale. **Tutti e tre riprogettati in §3.2/§3.3** (origine verificata, destinazione risolta dal broker, no-segreto-negli-shot, no-frontier-autenticato, URL-scrub).
- **Due buchi architetturali insidiosi**, ora chiusi: il tainted-turn bypassabile da un hit L0/L1 e il `session_id` cachato → §4.5/§7 escludono `sites` dall'arg-caching e ricostruiscono il taint deterministicamente.
- **Lacune di prodotto** (audit-log, revoca, tassonomia fallimento, CAPTCHA/2FA-push, selector-drift) colmate in §9 — erano assenti e per un agente-con-credenziali sono obbligatorie.
- **Usabilità vs sicurezza**: il gate-per-primitiva era brutale → approvazione BATCH (§4.2). Il TTL vs HITL era una trappola (login scade durante l'approvazione) → TTL in pausa sui gate (§3.1 FIX B).
- **Residuo onesto**: con Qwen locale il same-origin exfil dopo injection resta possibile; il presidio finale è il **gate umano su ogni submit in turno contaminato** — non la fiducia nel modello. La Fase 1 (solo lettura) è a rischio molto più basso della Fase 2 ed è il punto di partenza corretto.
- **Usabilità (§12-bis, richiesta Roberto)**: l'indurimento di sicurezza NON deve costare attrito. Vincolo di pari rango: Fase 1 login+lettura = ZERO conferme nel caso normale; gate solo in Fase 2 e solo BATCH su azioni sensibili; ri-login trasparente; linguaggio naturale. Se un login+lettura chiede una conferma, il bilanciamento è sbagliato.
- **Verdetto**: procedere con **MVP Fase 1** (§8) su un sito reale, adottando gli irrigidimenti §3-§4-§9 come vincolanti (§10) E il budget-conferme §12-bis. Rimandare la Fase 2 finché l'MVP non ha validato broker+iniezione dal vivo.
