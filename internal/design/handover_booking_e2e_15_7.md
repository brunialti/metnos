# Handover — regressione Booking end-to-end (15/7/2026)

## Obiettivo
Ripristinare «accedi a booking e trova le mie prenotazioni» end-to-end:
login → **navigare alla sezione prenotazioni** → leggere/riportare. Funzionava il
13/7 (turn `727f9ba37b3d4732`, raggiungeva `secure.booking.com/mytrips.html`) e la
mattina del 14/7. Ora si ferma prima. **Vincolo utente: NON martellare Booking**
(anti-bot suscettibile; 1 turno per verifica).

## Stato branch `session/detection-lexicon-i18n` (NON pushato)
Commit di questa sessione, in ordine:
- `dd8552e` — origini P2: loop consenso su login first-party (default same-site).
- `84f1de2` — `quota_exceeded`: recovery Metis + describe_entries su turni sites.
- `f051914` — `page_satisfies_goal`: home LOCALIZZATA `/index.<lang>.html`.
- `792ea84` — `page_satisfies_goal`: guard **brand-token** (ULTIMO; vedi deploy).

## Stato deploy (IMPORTANTE)
- `metnos-http.service`: running, allineato a tutti i commit.
- `metnos-playwright.service` (sidecar): running con `f051914` **MA NON `792ea84`**.
  `action_resolver.py`/`session_broker.py` girano nel SIDECAR → **serve
  `systemctl --user restart metnos-playwright.service`** per caricare `792ea84`.
- Sessione live `5e4a5dab8c0e83fa24de6af9c6037b65` (owner `host`, stealth) aperta e
  loggata, su codice `f051914`. Il restart la chiude (poi serve re-login = 1 login).
- **Stealth ATTIVO**: pref `sites_stealth=on` per owner `cc885f5e66a64874` (Roberto).
  Aggira il CAPTCHA Booking. Toggle UI: `/admin/users` → Roberto → Preferenze →
  `sites_stealth`. Letta a ogni turno (no restart).

## Diagnosi (CONFERMATA, non ipotesi)
Confronto 13/7 (verde) vs stasera:
- Codice di decisione goal (`page_satisfies_goal`, `choose_goal_candidate`,
  `goal_candidate_is_exact`, `_enumerate_candidates`, `_GOAL_EVIDENCE_JS`,
  `_page_satisfies_goal`) **byte-identico** dal 13/7 (`git log -L`/`-S` a vuoto).
- `secure.booking.com` **È** nell'allowlist del mandato (non è un blocco di rete).
  L'allowlist è **appresa dall'audit** (`credential_mandates.verified_site_topology`),
  non hardcodata: accumula host da `session_open`+estensioni approvate (fail-first-
  learn-after; sedimentati ~30 ad-server — scope-creep di RETE, NO leak credenziali
  = same-site). Roberto: «non è carino» → ripulire (item separato).

## Root cause (PINNED)
1. `_reduce_site_goal` (`session_broker.py:~1913`, prompt LLM **`sites_goal_reducer`**)
   riduce «trova le **mie** prenotazioni» → «**prenotazioni**», spogliando «mie».
2. `_is_personal_goal("prenotazioni")` = **False** → il guard home (che scattava solo
   per goal personali) non scattava.
3. `goal_tokens("prenotazioni")` = `('booking',)` — brand **onnipresente** su
   booking.com (cluster IT+EN voluto: serve a concludere su trips-page EN).
4. `page_satisfies_goal("prenotazioni", home)` = **True** → `op_act` conclude
   `goal_complete`/`observe` sulla home → **non naviga mai** alle prenotazioni.
5. Il 13/7: target era «mie prenotazioni» (personale) E landing su path `/` → il
   guard scattava → navigava con **6 click** (traccia audit sessione
   `f17e224170661d66e4d903e46a0f9f3a`, incl. un `overlay_dismiss`) → mytrips.

Verifica secca (riproducibile):
```
cd /opt/metnos/runtime && python3 -c "import playwright_sidecar.action_resolver as ar
print(ar.page_satisfies_goal('prenotazioni', ['le mie prenotazioni'], scope_text='https://www.booking.com/index.it.html'))"
# pre-792ea84: True (bug)  ·  post-792ea84: False (naviga)
```

## Fix applicato (`792ea84`, verificato UNIT, NON live)
`page_satisfies_goal`: la home non soddisfa un goal a token singolo quando il goal è
personale **OPPURE** quando il token è il **brand del sito** (`wanted <=
goal_tokens(host)`). Deterministico (§7.9), non dipende dal marker «mie».
Alternativa/complemento NON fatto: correggere il prompt `sites_goal_reducer` perché
non spogli «mie» (meno robusto dell'approccio deterministico).

## BLOCKER RESIDUO (verificato dallo screenshot live)
Screenshot home post-login (loggato «roberto brunialti · Livello 3 Genius»):
`/home/roberto/.local/share/metnos/sites-shots/host/5e4a5dab8c0e83fa24de6af9c6037b65_1784067607808.png`
- **Due overlay** coprono la pagina: modale promo **«Extra esclusivi solo per te!»**
  (X + «OK») + banner cookie («Rifiuto»/«Accetto»).
- Nella nav **NESSUN link diretto «Prenotazioni»**: sono dietro il **menu account**
  (avatar «roberto brunialti» in alto a destra).
- Per l'end-to-end serve: (a) dismissare i 2 overlay, (b) aprire il menu account,
  (c) cliccare «Prenotazioni» → `secure.booking.com/mytrips.html`.
- **IPOTESI da verificare** (non confermata): il fix #11 (`580f26f`,
  «navigating→gate» in `_dismiss_obstructing_overlay`, `session_broker.py:~2155`)
  potrebbe RIFIUTARE di chiudere il modale promo se il controllo è classificato
  navigante/submitter → overlay resta → blocca l'enumerazione candidati → il primo
  goal-step fallisce `selector_missing` (già osservato nel turno `0850c8e3` post-fix
  `f051914`: `act_sites` ok=False `selector_missing`, nessun `site_action` emesso).

## Prossimi passi (proposti)
1. `systemctl --user restart metnos-playwright.service` (carica `792ea84`; chiude 5e4a5dab).
2. UN turno Booking controllato (stealth on) via `/agent/turn` porta 8770:
   `{"query":"accedi al sito booking e trova le mie prenotazioni"}`.
   Attesa: `page_satisfies_goal` ora False sulla home → tenta di navigare.
3. Se `selector_missing`/observe persiste: instrumentare (env-gated) o loggare
   `_enumerate_candidates` + l'esito di `_dismiss_obstructing_overlay` per capire se
   (a) gli overlay restano, (b) il menu account non è enumerato. Verificare l'ipotesi #11.
4. Distinguere page-change Booking vs effetto-stealth: 1 turno con `sites_stealth=off`
   (confronto layout). Il 13/7 era senza stealth.
5. Eventuale capacità nuova: traversata menu a tendina (apri avatar → click voce),
   se il goal-nav non la copre (oggi enumera i candidati VISIBILI).

## Vincoli operativi
- NON `git add .` (path espliciti). MAI committare `ANALISI MEDICHE E CERTIFICATI/`.
  No trailer `Co-Authored-By`. Branch dev non pushato. Commit in italiano.
- Sidecar restart = deploy per `action_resolver`/`session_broker` (builtin, non firmati).
  Executor firmati (`executors/<name>`): re-sign + restart (§7.10).
- NON stampare/persistere credenziali o contenuto autenticato. Screenshot sono
  redatti (mask campi segreti) ma mostrano la pagina.
- Vault `booking.com`: `credential_origins` ASSENTE (regime same-site); login OK.

## File chiave
- `runtime/playwright_sidecar/action_resolver.py` — `page_satisfies_goal` (:~700),
  `_is_home_path` (:~683), `goal_tokens`, `_is_personal_goal` (:393), `goal_candidate_is_exact` (:524).
- `runtime/playwright_sidecar/session_broker.py` — `_page_satisfies_goal` (:1811),
  `_reduce_site_goal` (:~1913), goal flow / branch `search` (:2247), decisione
  observe/click (:2311-2320), `_dismiss_obstructing_overlay` (:~2155), `op_act` (:3562).
- `runtime/prompts/{it,en}/…/sites_goal_reducer*` — il prompt che spoglia «mie».
- `runtime/credential_mandates.py` — `verified_site_topology` (allowlist appresa),
  `resolve_sites_binding` (:240).
- Turni: 13/7 VERDE `727f9ba37b3d4732` (sess. `f17e224170661d66e4d903e46a0f9f3a`);
  stasera falliti `2e39b8a6…`, `0850c8e3…`. Audit `~/.local/state/metnos/sites_audit.jsonl`.
- Memoria sessione: `~/.claude/projects/-opt-metnos/memory/project_session_14_7_impl.md`.
