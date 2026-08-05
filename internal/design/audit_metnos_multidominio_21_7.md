# Audit multidominio Metnos — reperti + piano di fix

> **Prodotto il 21/7/2026** da sei revisioni adversariali parallele (una per dominio), ciascuna
> con lettura integrale del codice e, dove indicato, esecuzione di funzioni pure (read-only,
> nessun turno, nessuna scrittura su store reali). Questo è un documento di CONSEGNA a un agente
> per fix e implementazione. Non è documentazione pubblica: non deployare, non pushare.
>
> **Come usarlo.** Ogni reperto ha un ID stabile ([SAFE-P0], [NL-R3], …), livello di confidenza
> (CONFERMATO = verificato nel codice / PLAUSIBILE = meccanismo confermato, impatto dipendente
> dai dati), file:line, scenario di fallimento, direzione di fix e passo di verifica. **Prima leggi
> i §Temi trasversali**: la maggior parte dei P0/P1 discende da 5 cause condivise; chiuderle sana
> più reperti insieme. Rispetta §7.10 (re-sign dopo edit executor/manifest), §8.5 (≥1 turno reale
> per dominio toccato), il feedback «no gaming test» e «mai purge su store reali» (persons/
> credenziali/devices sono REALI: usare DB isolati).

---

## Sommario esecutivo

- **1 P0** (bypass del consenso umano), **~15 P1**, **~30 P2**, più ~1.5k LOC di codice morto e
  3 accoppiamenti fragili strutturali.
- **Il dato più diagnostico**: `dispatch.py` è oggi **5914 LOC con 26 guard**; l'ADR 0177 di due
  settimane fa ne censiva 1916 con 11 guard — triplicato. Il costo non è solo dimensionale: la
  disciplina di rimappatura degli step è artigianale e ripetuta a mano in ogni guard, ed è la
  causa meccanica della metà delle regressioni [→ T3].
- **Le aree di sicurezza «progettate come nucleo» reggono** (anti-replay result remoto, revoca
  device al boundary HTTP, capability `when` fail-closed, provider-authority 0193, approval
  registry monouso). I buchi P0/P1 sono ai **confini** fra sottosistemi, non nei nuclei — coerente
  con la tesi «l'insieme rompe ciò che le parti accettano».
- **Provenienza (per l'osservazione di Roberto sul tapis roulant)**: la maggioranza dei reperti
  gravi è da **accoppiamento** (coerce che whitelista i `_*`, gate provider pool-globale,
  rimappatura per-guard), non da riscrittura ratificata. Questi NON si chiudono da soli alla fine
  dei ripensamenti. I reperti da riscrittura recente (scheduler 0196, contratti generati) sono per
  lo più latenti oggi. → §Provenienza in coda.

---

## Temi trasversali (root cause condivise — chiudere questi per primi)

### T1 · `coerce_args` whitelista ogni chiave `_`-prefissata → bypass di consenso e autorità  **[P0/P1]**
`runtime/engine/coerce_args.py:66-69` (Guard #0, dichiarato UNICO backstop deterministico sul
confine LLM→pipeline) fa:
```python
for key, val in args.items():
    if key in _UNIVERSAL_KEYS or key.startswith("_") or key in guard_owned:
        out[key] = val          # ogni "_confirmed"/"_pre_approved"/"_actor" del MODELLO passa
        continue
```
Assunzione: i `_*` sono solo runtime-injected. Falso: `_confirmed`, `_pre_approved`, `_actor` sono
segnali di consenso/isolamento **letti a valle** dagli executor. Da qui discendono [SAFE-P0] (bypass
conferma), [SAFE-1] (spoofing `_actor`), e il ramo `guard_owned` globale di [SYNT-2].
**Ordine verificato**: il coerce è un Guard sul framework in planning (`dispatch.py:4702`);
l'iniezione legittima dei `_*` avviene DOPO, all'invocazione (`agent_runtime.py:3680` per `_actor`,
`:6148` per `_credential_mode`). Quindi il fix è sicuro.
**FIX (radice)**: sostituire `startswith("_")` con una **allowlist esplicita** di chiavi
runtime-owned (`_actor`, `_actor_email`, `_lang`, `_channel`, `_turn_id`, `_credential_mode`,
`_stealth`, `_undo`, …) e **droppare ogni altro `_*`** dall'output grezzo del proposer. Restringere
`guard_owned` alla coppia (tool, arg) del guard che dichiara il write, non a un set globale di nomi.

### T2 · Gate provider POOL-GLOBALE invece che per-clausola → misroute su compound e query locali  **[P1]**
Il docstring promette «un solo provider **per clausola**», l'implementazione agisce sul pool
dell'intero turno. Radice comune di [NL-R2] (marker su parole comuni), [NL-R3] (`provider_gate_names`
senza parametro clausola), [NL-R4] (guard 0179 mixed-compound non implementato), [NL-R5]
(`_align_provider_client` framework-globale). **FIX (radice)**: rendere il gate clause-scoped quando
`intent.actions` ha ≥2 clausole — escludere il canonico solo se NESSUNA clausola è provider-free per
quell'object (segnale: path filesystem esplicito o object locale nello span della clausola). Vale sia
per `tool_grammar.provider_gate_names` sia per il loop di `_align_provider_client`.

### T3 · Rimappatura step artigianale per-guard → classe-bug `${stepN}`/`from_step`  **[P1] (motore delle regressioni)**
Chi inserisce/sposta step deve ricordarsi TRE rimappe distinte: `from_step` int, `${stepN.field}`
negli args, `${stepN...}` in `final_message`. Le fanno complete `_conform_to_intent_order`,
`_align_foreign_producers_v3`, `_ensure_extracted_period_scope`. Le fanno **parziali o niente** i due
gate (mass/consent), `_ensure_extract_clause`, `_scope_dirs_clause_to_contents`,
`_enrich_move_source_dir`. Ogni guard nuovo ripete la scelta → regressione garantita. Radice di
[ENGINE-1] e di uno dei 3 accoppiamenti. **FIX (radice)**: un helper unico
`insert_steps(framework, at, new_steps)` che esegue TUTTE le rimappe (coda + final_message), vietare
`steps.insert`/slicing diretto nei guard con un test di lint. È il fix a più alto ritorno
anti-regressione dell'intero audit.

### T4 · Undo dei creatori Google Workspace rotto end-to-end  **[P1]**
Convergenza di [SYNT-1] e del dominio safety. La catena è rotta in 3 punti: (a) il `reverse_pattern`
di manifest (`delete_created_paths`) è **no-op** sui result gw (che non hanno `created`/`path`);
(b) l'hint `results._undo.reverse_pattern` che gli executor gw **già emettono** non viene mai
consumato da `undo_last_turn` (usa solo `ex.reverse_pattern` dal manifest); (c) anche il pattern
by-id corretto chiamerebbe `delete_files`, che è **local-only** (`enum=["local"]`, nessun handler gw,
nessun arg `file_ids`). Executor coinvolti: `create_dirs`, `write_files`,
`create_files_spreadsheet`, `create_files_doc` sul ramo gw. **FIX**: (1) `undo_last_turn` deve onorare
`results._undo.reverse_pattern` PRIMA del pattern manifest; (2) `build_undo_calls` deve propagare
`_undo.scope` negli args; (3) **DECISIONE (§10.2, fermarsi da Roberto)**: serve un executor delete
che accetti ids gw — oggi non esiste.

### T5 · Scarti silenziosi: osservabilità mancante sul piano operativo  **[P1/P2]**
Tre punti dove qualcosa sparisce senza segnale (spirito §2.8 sul piano operativo, non solo runtime):
il **loader muto** [SYNT-4] (scarto per firma assente/invalida: zero log, invisibile in /admin), la
**degradazione BoW muta** [NL-R6/R8] (fallback intent senza notice), l'**aging** (trappola già nota).
**FIX**: log `warning` + superficie /admin per ogni scarto; `parse_degraded`/notice quando il fallback
NL entra in gioco.

---

## P0 — bloccante

### [SAFE-P0] Consenso umano bypassabile via `_confirmed` / `_pre_approved` iniettabili dal modello  · CONFERMATO
**File**: `runtime/engine/coerce_args.py:67` · `executors/delete_calendars/delete_calendars.py:107` ·
`runtime/backends/files/google_workspace.py:296,553` · `executors/get_approval/get_approval.py` ·
`executors/create_calendars/create_calendars.py:66`
**Cosa**: il coerce (barriera unica) conserva i `_*` del modello [→ T1]; gli executor leggono
`_confirmed`/`_pre_approved` per saltare la conferma umana:
```python
# delete_calendars.py:107 — cancellazione IRREVERSIBILE calendari+eventi
if not a.get("_confirmed"): return {... "decision": "needs_inputs" ...}
# google_workspace.py:296 — bulk delete Drive
if len(ids) > 1 and not args.get("_confirmed"): ...
```
**Scenario**: un piano indotto (prompt-injection da web/mail nel turno) emette
`delete_calendars(ids=[...], _confirmed=true)` o `get_approval(..., _pre_approved=true)`. Esposizione
massima sul path frontier (Opus opt-in, NESSUN GBNF → chiavi arbitrarie); sul path locale i tool a
schema complesso cadono sul fallback JSON generico e restano iniettabili.
**Fix**: allowlist `_*` runtime-owned in `coerce_step_args` [→ T1]; il fix chiude in un colpo delete
calendari, bulk Drive, e il gate `get_approval`. In subordine, ogni executor che legge `_confirmed`
deve verificare che provenga dal registro consenso, non dagli args.
**Verifica**: unit `coerce_step_args({"ids":[1,2],"_confirmed":True}, schema_delete_calendars)` deve
droppare `_confirmed`; poi turno reale (DB calendari isolato) con piano che tenta l'iniezione.

---

## P1 — gravi

### Sicurezza / autorità

**[SAFE-1] Spoofing `_actor` → undo cross-utente** · CONFERMATO ·
`agent_runtime.py:3680-3681` + `executors/undo_last_turn/undo_last_turn.py:147`
Il runtime inietta `_actor` solo `if "_actor" not in args` → il valore del modello vince; `_actor` è
l'autorità di isolamento multi-utente dell'undo. Un guest con piano `undo_last_turn(_actor="host")`
ribalta il turno dell'host. **Fix**: al choke-point `invoke_executor` SOVRASCRIVERE sempre `_actor`
con l'attore autenticato (mai «if not in args»); più il drop dei `_*` a monte [→ T1].
**Verifica**: undo con `_actor` fittizio ≠ attore reale → deve filtrare sull'attore reale.

**[SAFE-2] Guardia binaria: forbidden-path senza canonicalizzazione (`..` bypassa i pattern ancorati)**
· CONFERMATO · `runtime/vaglio.py:139-140,157,160-163`
`_expand_user` NON fa `normpath`; i pattern ancorati (`^/etc/(passwd|shadow|sudoers)`, `^/root`,
`^/boot`, `^/sys`, `^/proc`) non matchano `/tmp/../etc/shadow`. `is_protected_path` (che fa normpath)
è consultata solo per prefissi mutanti, e `MUTATING_TOOL_PREFIXES` esclude `extract_`/`get_`.
Mitigazione reale: bwrap sulle scritture, permessi OS sui read (metnos non-root). Ma il nucleo
«non negoziabile» è aggirato da un `..`. **Fix**: `normpath`+`realpath` (anti-symlink) prima del match;
consultare `is_protected_path` per tutti gli executor con effetto di scrittura.
**Verifica**: `guard_check("read_files", {"paths":["/tmp/../etc/shadow"]})` → `False`.

### Motore / dispatch

**[ENGINE-1] Gate inseriti a runtime rompono i ref `${stepN}` → «0 file» dopo delete reali** ·
CONFERMATO · `dispatch.py:5029-5037` (mass gate), `:4876` (consent gate)
I gate rinumerano solo `from_step` int, non i ref stringa negli args di coda né in `final_message`
[→ T3]. Piano `[find, delete(from_step=1), final "eliminati ${step2.@count} file"]`: inserito il gate,
il delete slitta, `${step2.@count}` risolve il result di `get_approval` (nessuna lista → `@count`=0) →
utente legge «eliminati 0 file» dopo N delete reali. Viola §2.8. **Fix**: usare l'helper unico [→ T3]
sui due gate. **Verifica**: unit con final `${step2.@count}` + mass gate sotto-soglia.

**[ENGINE-2] Doppia convenzione `step_idx` vs posizione-history → il seed renderizzato al posto del
risultato** · CONFERMATO · `executor.py:2547` vs `:2281`; `:1059-1069`; `agent_runtime.py:6156`
`StepRun.step_idx` = indice framework; la risoluzione (`_resolve_from_step`, `_render_final_message`)
è posizionale su history. Con `seed_steps` le due numerazioni divergono di `len(seed)`. Path
upload-volto: il final auto-derivato `${step1.summary}` rende il **seed @uploaded** (le foto caricate)
invece dei match. Anche `_referenced_producer` e `_resolve_from_step` disaccordano sullo stesso ref.
**Fix**: una sola convenzione `step_idx = len(result.steps)+1` ovunque, risoluzione by-`step_idx` con
fallback posizionale. **Verifica**: run 1 seed + 1 step reale, template vuoto → final = ultimo step.

**[ENGINE-3] §4.4 (loop_break / DUPLICATE_CALL) non implementato nel path v3** · CONFERMATO
(grep+AST) · `executor.py:2046-2049`
L'unico cap è le 12 posizioni; nessun controllo «stesso executor 3×» né «step identico». I writer
legacy (`_cap_same_for_executor`, `_args_jaccard`, `_loop_break_hint`, `loop_detect.py`) non hanno
call-site; nessun `final_kind="loop_break"` viene mai prodotto. 3 `send_messages` identici partono
(bounded solo da 12); su turno schedulato il consent gate copre solo il primo. **Fix**: guard
deterministico in `Executor.run` — step con (tool, args-normalizzati) identici a uno già eseguito ok →
`duplicate_call`; allineare §4.4 (oggi è lettera morta). **Verifica**: framework con 2 send identici →
1 sola invoke; sweep dello store L0 per piani con step duplicati.

### Confine NL→vocabolario  (radice comune [→ T2])

**[NL-R2] Marker github su parole comuni («merge», «branch», «issue») dirottano query LOCALI** ·
CONFERMATO (funzioni pure) · `detection_lexicon_seed.py:39-40`
`«fai il merge dei due file csv»` → `active_provider_suffixes=['_github']` → i produttori locali
`find_files`/`read_files` sono ESCLUSI e rimpiazzati dai `_github`. **Fix**: nei marker solo termini
brand-univoci (github/repo/gist); i polisemici (merge/branch/issue/commit/pr/fork) attivi solo con
co-occorrenza di un termine brand — funzione deterministica nel matcher, non lista nel prompt.
**ATTENZIONE**: `detection_lexicon.register` è insert-only → correggere il seed NON aggiorna i DB
installati; serve un passo di riseed del concept. **Verifica**: rieseguire le 2 query su
`active_provider_suffixes` + bench routing.

**[NL-R3] `provider_gate_names` è pool-globale, non per-clausola** · CONFERMATO (funzioni pure) ·
`tool_grammar.py:1001-1008` (applicato anche in `routing_pool.py:292` e `proposer_v3.py:58-63`)
`«confronta il readme del repo su github con quello in /opt/metnos»` → kept `[read_files_github,
find_files_github]`, excluded i locali → la metà locale del confronto è irrappresentabile.
Generalizzazione del known-open `project_routing_provider_blind.md`. **Fix**: gate per-clausola [→ T2].
**Verifica**: unit su `provider_gate_names` con compound misto + turno reale confronto locale/github.

**[NL-R4] Guard ADR 0179 (anti web-steal): il caso «mixed-compound genuino» del commento NON è
implementato** · CONFERMATO (funzioni pure) · `engine/routing_pool.py:274-287`
Si affida a `detect_canonical_object` (winner-takes-all) invece del conteggio clausole promesso.
`«trova i file readme nel repo su github e cerca sul web le novità su rust»` → find_urls/get_urls/
read_urls_html tutti strippati → la clausola web muore in silenzio, e il coverage-guard
`_dropped_required_verbs` non la vede perché `dispatch.py:1332-1334` tratta `find_files_github` come
soddisfacimento di qualsiasi `find`. Viola §2.8. **Fix**: esenzione mixed-compound reale — se
`intent.actions` contiene una clausola object=urls distinta, il web-steal non scatta. **Verifica**:
riesecuzione snippet + bench compound `METNOS_ENGINE=v3`.

**[NL-R5] `_align_provider_client` inietta `client="google_workspace"` su TUTTI i file-producer,
sovrascrivendo anche un client esplicito** · CONFERMATO (codice) · `dispatch.py:2972-2976`
Il loop è framework-globale; solo il termine di ricerca è clause-scoped, il client no. `«confronta il
budget su Drive con ~/docs/budget.csv»` → entrambi i read forzati a Drive → il file locale non è mai
letto. Viola §11 «l'esplicito non è clampato». **Fix**: scoping per-clausola (riusare
`_clause_scoped_drive_term`) + mai sovrascrivere un `client` esplicito ≠ default [→ T2].
**Verifica**: turno reale drive+locale sullo stesso object.

### Scheduler esecuzione (ADR 0196)

**[SCHED-R1] Retropressione bloccante senza timeout + inversione head-of-line — ATTIVA IN PROD col
pool spento** · CONFERMATO (struttura) · `executor_scheduler.py:333-343`
`invoke()` acquisisce SEMPRE (anche con `METNOS_EXECUTOR_PARALLEL=0`) prima il semaforo globale (32)
poi quello di risorsa (`network_io`=16, `llm`=1), in attesa bloccante non interrompibile senza notify.
16 op network in esecuzione + 16 in attesa trattengono 32/32 permessi globali → qualunque altro
executor (`get_now`, `undo`, `final_answer`) si blocca in silenzio a tempo indefinito. Viola §2.8/2.11
e l'invariante «nessun executor cambia semantica per adozione». **Fix**: acquisire la risorsa PRIMA
del globale (o rilasciare il globale in attesa); `acquire(timeout=N)` → `ERR_SCHEDULER_BUSY` onesto con
campi §2.7; metrica `waiting`. **Verifica**: `max_in_flight=2`, `resource_limits={"slow":1}`, 2 lente
+ 1 default → il default oggi si blocca; atteso: passa o fallisce onesto entro timeout.

**[SCHED-R2] Chiave d'isolamento legata al NOME e non canonicalizzata** · CONFERMATO (latente) ·
`executor_scheduler.py:300-301,439-451`
`key=(name, key_kind, identity)`: la serializzazione vale solo per lo stesso executor, e l'identità è
stringa grezza (`/tmp/x` ≠ `/tmp/../tmp/x` ≠ symlink). Due mutanti sulla stessa risorsa non
collidono. Latente oggi (nessun manifest non-read-only in classe>0) ma è il primitivo di ogni futura
ammissione mutante. **Fix**: `key=(key_kind, identità_canonica)` senza nome; `os.path.realpath`+
`normcase` per `path`; documentare la forma canonica di `account`/`browser_session`/`device`.
**Verifica**: due executor A≠B, stessa path in due spelling → mutua esclusione.

**[SCHED-R3] Wave-prep: mismatch di preparazione ABORTA il turno invece di degradare a seriale** ·
CONFERMATO (percorso) · `engine/executor.py:2466-2476,2585-2588`
La prep wave è un gemello manutenuto a mano del loop principale; `remember_scope_args` muta i default a
metà turno → gli args ricalcolati divergono → lo step (read-only, riuscito) è marcato failed e il turno
aborta, dove il seriale passerebbe. Richiede entrambi i flag ON. **Fix**: su mismatch scartare la
future e ri-invocare sincrono con gli args freschi; il fail resta solo come ultima difesa loggata.
**Verifica**: wave di 2 step stesso tool dove il secondo omette uno scope-arg ricordato dal primo.

**[SCHED-R4] `map_ordered`: item extra eseguiti dopo un errore + risultati persi non
deterministicamente** · CONFERMATO · `executor_workers.py:73-79,91-95`
Con `workers=N`, se `fn` solleva, fino a N-1 item oltre quello fallito sono già sottomessi e
`shutdown(wait=True)` li porta a termine; quali entrino nei risultati dipende dall'ordine del set
`done`. Un executor generato `create_only` (pattern benedetto dall'ADR) che scrive per item e solleva
sull'item 2 scrive anche 3-4 in parallelo → output non equivalente al seriale, e le prove di nascita
non lo catturano. **Fix**: `map_ordered` non deve propagare l'eccezione a metà — catturarla per-indice
o restituire parziale+skipped come per la deadline. **Verifica**: `fn` che registra gli indici
avviati e solleva su index 1, `workers=4` vs 1 → stesso insieme di indici avviati.

### Synt / contratti

**[SYNT-1] Undo gw rotto** → vedi [→ T4]. CONFERMATO (eseguito).
`backends/files/google_workspace.py:706-728` · `reverse_patterns.py:234-249` ·
`executors/undo_last_turn/undo_last_turn.py:175-182` · `reverse_patterns_patch.py:131-137`.

**[SYNT-2] Guard #0: `guard_owned` è un set GLOBALE di nomi-arg** → vedi [→ T1]. CONFERMATO
(eseguito) · `coerce_args.py:67` + `dispatch.py:3044-3049,4561-4683`
Per ~10 nomi (`client`, `path`, `base_path`, `pattern`, `dst_folder`, `queries`, …) il backstop salta
tutte e 3 le regole su OGNI tool. Un leak `client="google_workspace"` su arg marcato `runtime_resolved`
è conservato; un `pattern` fuori-schema è conservato. **Fix**: `guard_owned` per coppia (tool, arg);
in subordine applicare comunque regole 1 (fuori-schema) e 3 (enum) ai guard-owned, esentando solo il
drop `runtime_resolved`.

**[SYNT-3] Test di nascita tautologici: `expect = {}` passa i 3 livelli di validazione** · CONFERMATO
(eseguito) · `synt_multistage.py:158-171` · `executor_standard.py:475` · `test_runner.py:73-80`
`check_expect(qualsiasi_result, {})` → `[]` (PASS): passa perfino un executor che fallisce sempre. Un
sintetizzato entra firmato nel pool con «N test verdi» che non asseriscono nulla. **Fix**:
`validate_stage3` richieda ≥1 matcher per test, ≥1 test con `expect.ok=true` e ≥1 negativo;
`check_expect` con expect vuoto = failure esplicita. **Verifica**: synth con expect vuoti → `abandoned:
stage3`.

**[SYNT-4] Loader: scarto per firma invalida/assente COMPLETAMENTE muto** → vedi [→ T5]. CONFERMATO ·
`loader.py:1165-1169`
Zero log (a differenza di affinity-overlap che logga a `:1046`); `catalog.rejected` non è esposto in
/admin. Edit di manifest senza re-sign → l'executor sparisce, il planner misroute al fratello, nessun
segnale. **Fix**: `log.warning` per ogni rejected a cache-miss; esporre in /admin/executors; cleanup
della dir in `_install_synthesized` su eccezione di firma (`synth_request.py:685-689` oggi non pulisce).

---

## P2 — da correggere

### Sites (broker Playwright)

- **[SITES-1] TOCTOU fra ultimo check d'origine e submit** · CONFERMATO(codice)/PLAUSIBILE(exploit) ·
  `credential_injection.py:1731-1754` (+ act path `session_broker.py:3682-3709`). Fra
  `_CURRENT_FORM_ACTION_JS`+`_origin_ok` e il click girano `_arm_email_factor` (I/O IMAP, secondi) e
  `_human_pause` (fino a 3s in stealth); JS in-pagina può riscrivere `form.action` nel mezzo. Il gate
  d'origine è difesa-in-profondità, non barriera unica → non P0 pulito. **Fix**: il check d'origine
  DEVE essere l'ultima istruzione prima del click/press; spostare `_arm_email_factor`/`_human_pause`
  PRIMA del check finale (o ripeterlo dopo).
- **[SITES-2] Goal-guard brand/home non copre le home con locale a PREFISSO (`/it/`, `/en/`)** ·
  CONFERMATO · `action_resolver.py:884-913`. `_is_home_path("/it/")` → False → riapre la classe-bug
  Booking su un'altra forma di URL: `page_satisfies_goal=True` sulla home → observe invece di navigare.
  **Fix**: riconoscere il segmento path che è un solo tag lingua/regione, deterministico, senza
  allowlist host.
- **[SITES-3] TOTP auto-inviato senza gate di scope** (asimmetria col fattore email, ADR 0190) ·
  CONFERMATO · `credential_injection.py:1777-1787` vs `:534-546`. Sotto un mandato che non concede
  `sites.read`, l'email-2FA è bloccata ma il TOTP procede. **Fix**: gate del ramo TOTP con lo stesso
  criterio di scope, o dichiarare esplicitamente che il TOTP presente è sempre ammesso.
- **[SITES-4] `storage_domain` con prefisso legacy `web_<domain>` in `same_site_origin`** ·
  PLAUSIBILE · `credential_injection.py:1179-1183,1340` + `sites_origin.py:154-173`. Record legacy
  senza `credential_origins` → `origin_unverified` su ogni login. **Fix**: spogliare il prefisso `web_`
  prima di `same_site_origin`.
- **[SITES-5] `op_read` restituisce body-text NON redatto** (email/identità eco) al planner/LLM, mentre
  gli screenshot mascherano · PLAUSIBILE/bassa · `session_broker.py:1500,1519-1521` vs
  `redaction.py:45-61`. **Fix**: stessa maschera email al `text` quando authenticated.
- **[SITES-6] `scrub_url` preserva la userinfo del netloc** (`user:pass@host`) in audit/result ·
  CONFERMATO/raro · `sites_url_scrub.py:70-79`. **Fix**: ricomporre il netloc senza userinfo.

### Confine NL→vocabolario

- **[NL-R6] Doppia SoT divergente per l'oggetto** (`prefilter._OBJECT_HINTS` vs `vocab.canonical_object`)
  · CONFERMATO · `prefilter.py:329-333` + `vocab.py:1068`. `«task»`→processes negli hints, →tasks in
  vocab; persons/tasks/credentials/issues/pulls/sites ASSENTI dagli hints. Col fallback BoW attivo,
  `«elenca i task ricorrenti»` → object=processes → get_processes iniettato primary. **Fix**: derivare
  `_OBJECT_HINTS` dalla stessa SoT dei sinonimi (una funzione, un dato); property-test di coerenza.
- **[NL-R7] Typo nel vocabolario SoT: `"publica"` (send.it)** · CONFERMATO · `vocab.py:700`. «pubblica»
  corretto non è riconosciuto → niente verb-boost `send`, coverage-guard cieco, typo esposto nel prompt
  synt stage 1. **Fix**: correggere in `ACTION_MAPPING` (tenendo il typo come alias).
- **[NL-R8] `_parse_json` collassa un compound malformato alla SOLA prima clausola, in silenzio** ·
  CONFERMATO(collasso)/PLAUSIBILE(frequenza) · `intent_extractor.py:339-343`. Le clausole 2..N si
  perdono senza log né flag; la catena a valle (`_is_compound`, verb-filter mono, coverage-guard) può
  non accorgersene → clausola utente evaporata end-to-end. **Fix**: nel ramo-recupero estrarre TUTTI i
  `{...}` se il testo conteneva `[`, o marcare `parse_degraded` e forzare il path lessicale multi-verbo.
- **[NL-R9] Ordine dei suffissi provider congelato dall'iterazione di `frozenset` (hash-randomizzato)** ·
  CONFERMATO(meccanismo)/latente · `detection_lexicon_seed.py:525` + `compound_decomposer.py:395-400`.
  Innocuo finché nessun `verb_object` ha due varianti provider installate; diventa non-riproducibile
  cross-install alla prima coppia. **Fix**: `sorted(PROVIDER_SUFFIXES)` + scelta del suffisso della
  CLAUSOLA in `derive_tool_name`.

### Scheduler

- **[SCHED-R5] Doppio `assigned_workers` con clamp divergente** · CONFERMATO ·
  `executor_workers.py:44-47` vs `executor_helpers.py:38-46`. La variante usata dai backend NON clampa
  alle CPU visibili → un device remoto a 2 core può ricevere 8 worker. **Fix**: passare `cpu_count`
  anche in `executor_workers.assigned_workers`; valutare l'unificazione delle due omonime.
- **[SCHED-R6] Fail-open da ereditarietà env** · CONFERMATO · `agent_runtime.py:3643-3648` +
  `executor_scheduler.py:506-511`. `assigned_worker_environment` ritorna `{}` per i non-dichiarati e
  non rimuove un `METNOS_EXECUTOR_ASSIGNED_WORKERS` presente nell'env del daemon → legacy si
  parallelizzano senza gate. **Fix**: emettere sempre `"1"` esplicito per i non-dichiarati.
- **[SCHED-R7] Leak di permessi: acquire e `metrics.started` FUORI dal try/finally** · PLAUSIBILE ·
  `executor_scheduler.py:333-347`. Un'eccezione asincrona fra le acquire e il `try` perde i permessi
  (semaforo mai rilasciato) → degradazione cumulativa fino allo stallo (aggrava R1). **Fix**:
  try/finally annidati o flag `acquired_*`.
- **[SCHED-R8] Interruzione turno: peer wave running orfani con slot occupati** · CONFERMATO(slot)/
  PLAUSIBILE(dialog) · `engine/executor.py:2590-2598`. `cancel()` è no-op sulle future in esecuzione →
  il peer trattiene slot oltre il turno; se incontra il ramo auth può lasciare un dialog orfano.
  **Fix**: registrare le future del turno e attenderle con timeout breve; TTL/idempotenza sui dialog.
- **[SCHED-R9] Fan-out gmail per-id: race di refresh sul token OAuth condiviso** · PLAUSIBILE ·
  `backends/messages/gmail_google_workspace.py:208-220`. Fino a 8 CLI concorrenti condividono
  `google_token.json`; a token scaduto ciascuna riscrive senza lock. **Fix**: refresh preventivo
  singolo prima del fan-out, o file-lock.
- **[SCHED-R10] `_policy_slots` chiave `(name, limit)`: cambio del limite a caldo somma le concorrenze**
  · CONFERMATO(logica) · `executor_scheduler.py:283-288`. **Fix**: chiave = solo `name`, clamp del
  limite alla acquire, o congelare i cap env alla costruzione.

### Synt / contratti

- **[SYNT-5] `validate_stage2`: catalogo reverse_pattern INCOMPLETO** · CONFERMATO ·
  `synt_multistage.py:58-59,148-155`. Rifiuta la 5ª famiglia `delete_<obj>_by_id` E i pattern
  multistage a lista che il runtime supporta → synth forzato a `revertible=false` o a mis-dichiarare
  `delete_created_paths` (undo no-op, [SYNT-1]). **Fix**: accettare `str|list[str]` con catalogo esteso.
- **[SYNT-6] Bypass «legacy per omissione» del contratto generato** · CONFERMATO(codice morto) ·
  `sign.py:224-231` + `synt.py:1774-1797`. Chi non dichiara `executor_standard` firma senza gate; il
  generatore `specialize` (ritirato ma callable) è esattamente in questo stato + `AttributeError` sui
  manifest `[description]` a tabella. **Fix**: eliminare `Synt.specialize`/`_build_specialize_manifest`
  (§7.1) o portarli sul contratto; `require_declaration=True` di default nei path di GENERAZIONE.
- **[SYNT-7] `approve_proposal` lascia lifecycle `proposed` → invisibile al composer per sempre** ·
  CONFERMATO · `synt.py:1062,1554-1563` + `loader.py:1420-1421`. `synt approve` firma e installa ma non
  riscrive il lifecycle → approvazione = no-op silenzioso (§2.8). **Fix**: riscrivere
  `lifecycle="active"` PRIMA di `sign_executor`.
- **[SYNT-8] Stage 1 NAMING: qualifier non validato contro il vocab (fail tardivo) + descriptor
  kebab-case rifiutato dalla regex** · CONFERMATO · `synt_multistage.py:61,114-138`.
  `find_files_nightly` passa stage 1 e fallisce alla firma dopo ~150s; `write_files_csv_dry-run`
  (nome ADR 0156 valido) è rifiutato dalla regex. **Fix**: `validate_stage1` chiami
  `naming_grammar.validate_name` (SoT unica, §7.3).
- **[SYNT-9] Politica `runtime_resolved` cieca fuori da `executors/` del repo** · PLAUSIBILE ·
  `synth_request.py:184-188` + `tests/test_config_args_marking_policy.py:31`. Stage 2 può marcare
  `runtime_resolved` arbitrariamente e la marcatura finisce verbatim nel manifest synth → proposer
  nasconde + coerce droppa un arg intent-bearing. **Fix**: whitelist delle chiavi arg-spec in
  `_install_synthesized` e STRIP di `runtime_resolved` (mai deciso dal modello); check nel `manifest_lint`.
- **[SYNT-10] Birth test eseguiti FUORI sandbox sul path multistage** · CONFERMATO(lettura) ·
  `synth_request.py:66-70` + `test_runner.py:73-80` (contrasto: whitelist stdlib solo nel path legacy
  `synt.py:78-86,704-715`). Il codice di stage 5 gira come subprocess coi privilegi del daemon senza
  vetting import, PRIMA di ogni gate umano. **Fix**: applicare il vetting AST/import del path legacy al
  multistage, o eseguire il runner dentro il profilo sandbox del dispatcher. (Interagisce con
  [SAFE-P0]: un intento injected può orientare stage 5.)

### Motore / dispatch (P2)

- **[ENGINE-4] Validator seed-blind: ogni continuazione paga un re-propose spurio** · CONFERMATO ·
  `validator.py:80-84`. `from_step=1` verso un seed → `from_step_invalid` → seconda call wise sprecata
  a ogni ripresa dialogo. **Fix**: `Validator` accetta `seed_count`, bound `1 ≤ fs < i+seed_count`.
- **[ENGINE-5] `if_prev_entries_nonempty` guarda `history[-1]`, non il producer `from_step`** ·
  CONFERMATO(meccanismo) · `executor.py:1016-1022`. Uno step interposto senza entries (describe
  skip-record) fa saltare il mutante anche con dati presenti → «fatto» senza aver fatto. **Fix**: se lo
  step ha `from_step`, testare il payload di QUEL producer (`_mutating_input_is_empty`).
- **[ENGINE-6] Dormancy flip invisibile a `tools_sig`/`pool_sig`** · CONFERMATO(lettura) ·
  `cache_validity.py:46-63,126-143`. Un provider che passa da dormant ad attivo (creds aggiunte) non
  cambia nome/digest → i piani L0/L1 locali restano validi per sempre, contro §11 «capacità nuova
  invalida per costruzione». **Fix**: includere il bit dormancy nelle firme.
- **[ENGINE-7] Cap 12 conta `final_answer` e gli step inseriti dai guard** · PLAUSIBILE ·
  `executor.py:2046-2049`. Un piano legittimo di 11 exec + final, con anche 1 solo step inserito dai
  guard, aborta al finale con tutto il lavoro fatto. **Fix**: non contare `final_answer` nel cap, o far
  bumpare `runtime_step_cap` ai guard che inseriscono.
- **[ENGINE-8] Ramo retry `remediate_args_cb`: codice morto + bypass latente del vaglio** · CONFERMATO ·
  `executor.py:2521-2533`. Mai passato in prod; se ricablato, il retry invoca args rimediati senza
  ri-passare dal `vaglio_guard`. **Fix**: rimuovere (§7.1) o fattorizzare guard+invoke in un helper unico.

### Sicurezza / undo / remoto / i18n (P2)

- **[SAFE-3] i18n §7.13: stringhe user-facing hardcoded** · CONFERMATO · `backends/files/local.py:1239-
  1275` (inglese crudo, surfacato in `failed[].error` a `:1448`) · `delete_calendars.py:101-120`
  (italiano hardcoded, builtin). **Fix**: convertire in `_msg("ERR_...")` con chiavi seed IT+EN + re-sign.
- **[SAFE-4] Move con overwrite: undo dichiarato reversibile ma perdita del dst clobberato** ·
  CONFERMATO · `backends/files/local.py:1242-1251`. `swap_src_dst` ripristina il src ma il dst
  originale (unlink/rmtree) non ha blob → irrecuperabile con `overwrite=true`. Viola §2.8/2.9. **Fix**:
  blob content-addressed del dst prima di sovrascrivere + `restore_blob_backup` nello stage reverse; o
  dichiarare non-reversibile l'overwrite.
- **[SAFE-5] Doppio effetto remoto su crash fra side-effect e journal (at-least-once)** · PLAUSIBILE ·
  `client-rs/src/runner.rs:342-343` + `invocations.py:418-462`. Il client registra l'esecuzione DOPO
  l'effetto; crash nel mezzo → la redelivery riesegue l'effetto sul device (l'idempotenza server
  protegge solo il result). **Fix**: journal write-ahead dell'`invocation_id` PRIMA dell'effetto
  (at-most-once per i mutanti), o effetto idempotente per costruzione.
- **[SAFE-6] Integrità consenso del gate model-authored: testo mostrato non vincolato all'azione** ·
  CONFERMATO · `orchestration.py:765-798` + `get_approval.py`. Quando è il planner ad autorizzare un
  `get_approval`, il `prompt` è testo libero slegato da `on_approve{tool,args}` → un piano injected può
  mostrare «Approvo l'invio mail?» ed eseguire `delete_files`. **Fix**: derivare deterministicamente il
  testo del gate da `on_approve`, o vietare `get_approval` model-authored.
- **[SAFE-7 · nota latente] `dialog_pending._dialog_path` interpola `dialog_id` non sanitizzato** ·
  `dialog_pending.py:99-100`. Oggi innocuo (`dialog_id` è uuid server-generato) ma traversal latente se
  un `dialog_id` influenzabile raggiungesse `save_pending`. **Fix**: sanitizzare come `sender_id`.

---

## Codice morto e debito (sweep §7.1)

- **~1.4k LOC morte in `agent_runtime.py`** (AST + grep, esclusi i test): `resolve_from_step` (170,
  replicata in engine), `validate_args` (130), `_maybe_remediate_obs` (109), `_check_top_k_affinity_
  jaccard` (70), `_expand_nested_from_step` (65), `_try_synt_compose` (55), `_resolve_auto_final_from_
  steps` (50), l'apparato loop legacy (`_cap_same_for_executor`, `_args_jaccard`, `_loop_break_hint`,
  `DEFAULT_CAP_SAME_EXECUTOR`) e i rami TurnLog su `final_kind="loop_break"` (mai prodotto).
- **`runtime/loop_detect.py`** (94 LOC): importato da nessuno.
- **`Synt.specialize` / `_build_specialize_manifest`** ([SYNT-6]): ritirato (ADR 0180), callable, marcio.
- **Commenti-contratto stantii che mentono al lettore**: `dispatch.py:5778` «Gate METNOS_ERROR_FORM
  (default ON)» ma il codice default `"0"`; `validator.py:15` «default OFF» ma il gate è ON
  (`engine/__init__.py:61`).
- **Memoria stale**: la voce «🐞 BUG engine scarta delete_persons» in MEMORY.md è RISOLTA in prod
  (aging esenta gli handcrafted, `executor_aging.py:357`; strato-3 non escalation, `agent_runtime.py:
  6620`; verificato su `executor_stats.db`: `delete_persons deprecated_at=None`). Declassare a chiusa
  dopo 1 turno reale «cancella l'enrollment di <nome-test>» con backup persons.sqlite.

---

## Aree verificate SANE (anti-rumore — non spenderci tempo)

- **Sicurezza remota**: anti-replay result idempotente (`invocations.complete_invocation:528`), revoca
  device respinta 403 al boundary HTTP PRIMA della chiamata (`agent_server.py:182`), firma device sui
  bytes esatti. Il buco remoto è solo l'effetto at-least-once su crash [SAFE-5].
- **Capability `when={arg,values}`**: fail-closed (`capabilities.py` — clausola malformata/valore
  fuori-enum → capability NON concessa).
- **Provider-authority 0193**: executor conformi derivano le skill solo da `provider:access`; nome/
  client/suffisso ignorati (`sandbox.py:559-563`). `_credential_mode`/`_stealth` iniettati con override
  runtime.
- **Approval registry**: `resolve` atomico monouso, TTL applicato, decisore==richiedente per id.
- **Scheduler invarianti**: default classe 0 su ogni ramo (doppia difesa loader + scheduler), pool
  default-OFF a due flag indipendenti, identità mancante = fail-closed, ordine risultati deterministico,
  `generated_executor_contract` impone la policy seriale (il modello non può dichiarare concorrenza).
- **Origine sites**: normalizzazione robusta (punycode/IDNA, trailing-dot, IPv6, userinfo via
  `hostname`, porta nel regime esplicito, deny-all su `credential_origins:[]`); screenshot fail-closed,
  `full_page=False`, `secret_pending` blocca il capture fra fill e submit; cicli goal bounded.
- **Motore**: `from_step` verso `results` gestito; doppia esecuzione post-recovery bloccata da
  `_leg_committed_mutations`; i guard deterministici girano DAVVERO sugli hit L0/L1 (ADR 0174); la LRU
  alternative include `catalog_epoch` (il buco è solo la dormancy [ENGINE-6]).

---

## Piano d'attacco consigliato (ordine)

1. **[SAFE-P0] subito**, con la sua unit di regressione. Chiudere T1 (allowlist `_*`) sana in un colpo
   P0 + [SAFE-1] + il ramo globale di [SYNT-2]. Poi [SAFE-2] (normpath guardia binaria). Sono i tre
   fix di autorità; nessuno tocca il planning, rischio di regressione basso.
2. **T3 (helper `insert_steps` unico)** — il fix a più alto ritorno anti-regressione. Chiude [ENGINE-1]
   e rimuove la causa meccanica di future regressioni della stessa classe. Convertire i guard che oggi
   fanno `steps.insert` diretto + test di lint.
3. **T2 (gate provider per-clausola)** — chiude [NL-R2..R5] insieme. Attenzione al riseed del
   `detection_lexicon` (insert-only) per [NL-R2].
4. **T4 (undo gw)** — [SYNT-1]; il punto (c) richiede DECISIONE di Roberto (§10.2: executor delete per
   ids gw). Nel frattempo [SYNT-5] (catalogo reverse esteso) e [SAFE-4] (blob su overwrite).
5. **Scheduler**: [SCHED-R1] (timeout+notify, attivo in prod) ha priorità sugli altri, che sono per lo
   più latenti; poi R2/R3/R4 prima di qualsiasi ampliamento delle classi mutanti.
6. **[ENGINE-2], [ENGINE-3]** e i P2 del motore; **[SYNT-3], [SYNT-4], [SYNT-10]** (integrità della
   filiera synt); **T5** (osservabilità).
7. **Sweep codice morto §7.1** (~1.5k LOC) come ultimo passo, a superficie ferma, con suite piena +
   turno reale per dominio (§8.5). Correggere i due commenti-gate stantii insieme.

**Regole operative per l'agente che fixa**: dopo ogni edit di executor/manifest → `python3
runtime/sign.py sign executors/<name>` da repo root + committare manifest+sig insieme (§7.10). Testo
model-facing (description/affinity/prompt .j2) è dominio di Fable: se un fix lo tocca, fermarsi e
chiedere. Ogni fix di codice di prodotto → ≥1 turno reale `/agent/turn` sul dominio (§8.5), mai una
query modificata per farla passare. Bench compound SEMPRE con `METNOS_ENGINE=v3`.

---

## Provenienza delle regressioni (per la classificazione richiesta)

Etichetta ogni fix con la sua provenienza, così in un mese si vede quale curva domina:

- **Da accoppiamento (non si chiude alla fine dei ripensamenti)**: T1, T2, T3, [NL-R6/R7], [ENGINE-2/5/
  6/7], [SAFE-2/6]. Sono i più gravi e i più numerosi. Il tapis roulant qui è strutturale: nasce dal
  fatto che confini fra sottosistemi (coerce↔executor, routing↔clausole, guard↔rimappatura) non hanno
  un contratto unico applicato.
- **Da riscrittura ratificata (converge quando i ripensamenti finiscono)**: tutti gli [SCHED-*] (ADR
  0196), [SYNT-6] (contratti generati), la maggior parte è LATENTE oggi — il che conferma che quella
  fase sta maturando come previsto dal §7.1, purché la superficie resti ferma mentre si assesta.

Il segnale da sorvegliare: se i prossimi fix restano concentrati nella prima categoria, il problema è
di **contratti di confine mancanti**, non di ripensamenti — e la cura è consolidare quei confini (un
helper `insert_steps`, un coerce con allowlist, un gate clause-scoped), non aspettare la fine delle
riscritture.
