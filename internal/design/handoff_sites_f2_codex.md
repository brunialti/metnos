# Handoff storico — dominio `sites` (web interaction)

> **CHIUSO 11/7/2026.** F1 e F2 sono implementate e validate; la decisione
> canonica e' ADR 0188. Il contenuto seguente resta come fotografia del punto
> di passaggio precedente, non come lista di lavoro aperta.
>
> Brief originario di ripresa a freddo. Branch `session/detection-lexicon-i18n` (NON pushato).
> Prima di toccare codice: leggi `CLAUDE.md` + `CLAUDE.mutabile.md` (regole invarianti)
> e la spec **`internal/design/spec_web_interaction_sites.md`** (F1/F2 §7, presidi §4,
> iniezione credenziali §3.2, screenshot §3.3, decisioni ratificate §11 D-A..D-E,
> usabilità §12-bis, leggi vincolanti §10).

---

## COSA È GIÀ FATTO — FASE F1 (login + lettura). Commit `c9e6916` + `ce2f8bc`.

Architettura: gli executor sono **client thin**; TUTTO il segreto vive solo nel
**broker** = processo sidecar Playwright (user-unit `metnos-playwright.service`,
porta **8771**). Gli executor girano SOLO server (§10.8).

### Broker (`runtime/playwright_sidecar/`)
- `session_broker.py` — registry sessioni nominate; TTL idle 15m (in PAUSA su
  `gate_pending`, FIX B); cap 4 contesti / quota 2-utente / timeout 20s-op / lock
  per-sessione (FIX C); route-guard per-sessione: aborta fuori-allowlist, WebRTC off,
  no `data:`/`blob:` top-level (FIX D); screenshot per-owner
  (`~/.local/share/metnos/sites-shots/<owner>/` 0700, file 0600, TTL 30m, SEMPRE
  redatti). Ops: `open/read/screenshot/login/close`. `session_lost` su id
  assente/scaduto (FIX A).
- `credential_injection.py` — §3.2: CRITICO-1 (origine `form.action` == dominio vault
  ESATTA, solo main-frame, verificata PRIMA di digitare); CRITICO-2 (il broker
  risolve/tagga i campi `data-metnos-*`, mai selettori LLM); CRITICO-3 (redact prima
  del capture, nessuno screenshot fra fill e submit, no VLM). `credentials.load()`
  SOLO qui.
- `redaction.py` — overlay nero opaco su `input[type=password]` + `[data-metnos-redact]`.
- `session_client.py` — client sync urllib usato dagli executor.
- `server.py` — endpoint HTTP `/session/{open,read,screenshot,login,close}`.

### Shared (`runtime/`)
- `sites_url_scrub.py` — scrub token in query E fragment → `REDACTED` (FIX E).
- `sites_audit.py` — audit append-only `~/.local/state/metnos/sites_audit.jsonl`
  0600 (login attempt, credential_use per FINGERPRINT mai il valore, origin_mismatch,
  session open/close). §9.

### Executor (`executors/`)
`open_sites`, `login_sites`, `read_sites`, `delete_sites` (kill-switch/revoca:
`close` NON è verbo vocab → si usa `delete`). Ognuno `.py` + `manifest.toml` + `.sig`.
Vettoriali §2.1, cap `network:sites`, server-only. `login_sites` `critical=true`.
Zero segreti nei result (`reason_code` = slug i18n).

### Vocab / cache / routing
- `runtime/vocab.py`: oggetto `sites`; verbi `open`/`login`/`act` (D-A ratificata —
  **`act` è già valido, NIENTE escalation per F2**).
- `runtime/engine/fastpath.py`: `open/login/read/delete/act_sites` in
  `NON_CACHEABLE_TOOLS` (§4.5: `sites` fuori L0/L1, session_id mai cachato).
- `runtime/engine/dispatch.py`: guard `ensure_site_session_precursor` in
  `GUARD_PIPELINE` (ricostruisce open→[login]→[read] quando il planner non incatena).
- `runtime/engine/recovery_metis.py`: `_fix_needs_site_session` (rete di sicurezza gemella).

### Test / i18n
- `runtime/tests/test_sites_security.py` (§4.1/§8): url-scrub, no-segreti, origine,
  redazione, no-cache, no-selettore.
- Reason codes `MSG_SITES_RC_*` nel DB `~/.local/share/metnos/i18n.sqlite` (IT+EN;
  NON in git — stesso meccanismo di `MSG_GPHOTOS_DOWNLOADED`).

### Validazione MVP §8 (dal vivo)
open→login→read incatenati su sito demo, pagina post-login letta (`sensitive=true`),
screenshot redatto in chat (signed URL per-owner + gallery), rifiuto origine-mismatch
su form phishing, segreto ASSENTE da turn-record/response/audit/screenshot.

### ⚠️ Review di sicurezza F1 = INCOMPLETA
Rivista SOLO `credential_injection.py`. 4 note preliminari (da confermare/scartare)
in `~/.claude/projects/-opt-metnos/memory/project_sites_f1_10_7_2026.md`:
1. **TOCTOU su `form.action`**: origine letta al tag, submit usa l'action al
   submit-time → pagina ostile può cambiare action fra check e submit. `route()`
   copre altri host (allowlist esatta), ma il same-origin exfil resta (residuo §12);
   al login F1 NON c'è gate umano. Valutare re-verifica origine subito-prima-del-submit.
2. **`still_pw` falso-positivo**: senza `session_cookie_names`,
   `logged_in=(not still_pw) and (not otp)` → una pagina d'errore senza form dà falso
   `logged_in:true`. Preferire segnale positivo.
3. **username non redatto** (solo il pw field è taggato redact) — scelta di design da
   ratificare (può essere PII).
4. **redazione dipende da `full_page=False`** (`getBoundingClientRect` + `position:fixed`
   = coordinate viewport). Blindare/commentare: `full_page=True` disallineerebbe.
**Completa la review su `session_broker.py`, `redaction.py`, `sites_url_scrub.py`,
`sites_audit.py`, executor e guard/recovery prima del merge.**

---

## COSA BISOGNA FARE — FASE F2 (azioni). Spec §7/§3.4/§4.2/§4.5/§9/§12-bis.

1. **Broker ops mancanti** (`session_broker.py`, spec §3.1): `goto`, `click`, `fill`,
   `submit`, `wait` + endpoint in `server.py` + metodi in `session_client.py`.
2. **Executor `act_sites`** (§3.4): args `session_ids|from_step`, `action: str` (NL),
   `value_ref` opz. `critical=true`, `revertible=false`, OUT `results`. Manifest §2.5
   IT+EN, firma §7.10.
3. **Presidio #2 — HITL (§4.2, FIX H)**: classificazione SENSIBILE sull'elemento
   **RISOLTO** (role/testo bottone, `form.action`, metodo POST) — deterministica sul
   DOM, **mai sulla frase NL**. Default-SENSIBILE ogni azione che innesca
   navigazione/submit/POST/download. Gate = `get_approval` con **screenshot redatto +
   descrizione**; approvazione **BATCH** (un gate per l'intento multi-passo, non per
   primitiva). Riusa `executors/get_approval/` + `orchestration._process_gate_dispatch`.
4. **Risoluzione elemento** (§3.1/§9): accessibilità (role+name) deterministica →
   fallback **VLM locale** SOLO su screenshot PRE-fill (D-C, frontier VIETATO in
   contesto autenticato). Bassa confidenza su pagina sensibile → **gate, mai click a
   indovinare** (selector drift).
5. **`value_ref: cred:<domain>:<field>`** (§3.2 CRITICO-2): il broker risolve
   autonomamente il campo credenziale dell'origine attesa e IGNORA ogni selettore LLM.
6. **Tainted-turn pieno (§4.5, presidio #5)**: flag `web_content_ingested` al primo
   `read/goto`; da lì ogni `act_sites` exfil-capace richiede approvazione. Taint
   ricostruito deterministicamente a ogni run. Gate RIFIUTATO → **kill della sessione** (§9).
7. **Usabilità §12-bis**: ricorda-nella-sessione (approva una volta l'azione identica);
   ri-login trasparente su TTL scaduto; linguaggio naturale (l'utente non nomina session_id).

---

## REGOLE OPERATIVE (vincolanti)
- §2.1 vettoriale; §2.5 manifest a capitoli SCOPO/PATTERN/NON/OUT.
- §7.10 re-sign dopo ogni edit executor/manifest: `python3 runtime/sign.py sign
  executors/<name>` (da repo root) + restart. Committa manifest+sig INSIEME.
- Restart: `sudo -n systemctl restart metnos-http.service` (system, passwordless);
  sidecar `systemctl --user restart metnos-playwright.service` (USER unit).
- §7.13 i18n: messaggi user-facing via `_msg`/DB, IT+EN. Vietate stringhe hardcoded.
- §8.5 E2E: ≥1 turno reale `/agent/turn` (porta 8770) sul dominio toccato, senza
  adattare la query. §2.8 onestà (nessun `logged_in`/esito non verificato).
- I pezzi security-critical (§3.2/§3.3/§4) passano review separata PRIMA del merge.
- **NON pushare.**

## Limiti noti F1 che F2 deve affrontare
- Allowlist default = host ESATTO (D-D): dashboard cross-host (`dash.*` + CDN) vanno
  gestiti con allowlist esplicita (estensione = decisione utente / `get_approval`).
- 2FA/CAPTCHA: rilevati e CEDUTI all'utente (D-E), non risolti.
- Rendering finale di `read_sites`: tabella markdown grezza → migliorabile via
  terminale `output_policy` dedicato per `sites`.
- Testo model-facing (manifest `[description]`/affinity, sezioni planner `.j2`) =
  dominio Fable; la struttura (args/caps/tests/codice) resta all'implementatore.
