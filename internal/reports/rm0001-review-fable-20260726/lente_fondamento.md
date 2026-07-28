# Lente fondamento

Review adversariale di RM-0001, lente «fondamento» — 26/7/2026. Righe senza
percorso = `internal/roadmap/RM-0001-conoscenza-utente-locale.md`; percorsi di
codice relativi a `/opt/metnos`. Tutte le letture e i comandi citati sono stati
eseguiti da me in questa sessione.

## Verdetto in tre righe

Il fondamento regge: nessun modulo, funzione o tabella che RM-0001 dichiara
esistente è inventata — gli 11 punti d'innesto di §25.1 esistono tutti e §5 è
fedele al codice fin nei dettagli. I difetti stanno nella distanza fra «patch
minime» (r. 2503) e realtà: la catena d'identità odierna fabbrica il
proprietario («host») in almeno quattro punti che F0.3 non elenca, e tre
contratti chiave (InvocationContext, clause_span, authz_revision) non hanno
alcun produttore nei file che la tabella autorizza a toccare; tre meccanismi
esistenti e pertinenti (project_paths.json, `_RUNTIME_ARG_SOURCES`,
TutorPrincipal) non sono mai nominati — rischio di duplicazione, non di
impossibilità.

## Rilievi

### 1. Gli innesti dichiarati esistono e §5 è fedele al codice: il fondamento regge
[PROVATO] Verifica sistematica: gli 11 file/directory di §25.1 esistono tutti
(`ls`: users.py, config.py, vocab.py, agent_runtime.py, http_routes_agent.py,
channels/telegram.py, scheduler_v2/builtin_callbacks.py,
jobs/maintenance_tasks.py, playwright_sidecar/session_broker.py;
`runtime/memory/` assente, coerente con lo stato dichiarato).
`PATH_USER_DATA`/`PATH_USER_STATE`: config.py:111-113. Il pattern F2 esiste
già identico: `runtime/builtin_executor_contracts/` con 17 builtin, ciascuno
manifest.toml + manifest.toml.sig, registro `_BUILTIN_TOOL_HANDLERS` e
catalogo virtuale `_engine_v2_catalog_with_builtins`
(agent_runtime.py:5743-5773). Schema `user_prefs` con user_id/key/value/
source/updated_at (users.py:657-664) e `list_prefs` che perde source e data
(705-712), come §5.1. Turno persistito con soli `actor`/`channel` e nessun
principal (ispezione campo-per-campo dell'ultimo record di
`~/.local/share/metnos/turns/2026-07-26.jsonl`), come §5.4. FTS5
`unicode61 remove_diacritics 2` funziona sul sqlite in uso (3.45.1; comando
eseguito, match «perche» su «perché» = 1). `embed_texts`/`embed_query`:
virt/interfaces.py:34-35. Catalogo Tutor davvero firmato Ed25519
(tutor/catalog.py:4, 40). `mnestoma.py` + `DB_MNESTOMA` (config.py:148).
Registro ArgTransform con scope `exec-only` (engine/executor.py:1306 e
pipeline 1329-1371), come §9.3/F3 assume. session_broker.py = 4189 righe con
`sites_observed` (r. 43) e postcondizioni verificabili (r. 2015, 2258), come
§12.5/§14.5. vocab: azioni set/find/list/delete presenti, oggetto `memories`
assente (vocab.py:41-50). Firme cache come §5.2: `normalize_hash`
fastpath.py:228; intent sig compound-aware `verb|object` per clausola
(autopath.py:288+); «niente prefilter/affinity nella firma»
cache_validity.py:17. Conseguenza: la parte «stato corrente verificato» del
documento è affidabile; i rilievi seguenti riguardano il carico degli innesti,
non la loro esistenza.

### 2. L'innesto F2 (dispatch builtin) oggi fabbrica l'identità del proprietario, e il contratto che RM gli assegna non esiste
[PROVATO] `InvocationContext`: 0 occorrenze in runtime/ (grep), mentre §9.6
(r. 833) e §25.1 F2 (r. 2489) lo danno per contratto dei quattro handler. Il
dispatch reale è `_invoke_builtin_handler` (agent_runtime.py:5775-5807):
contesto come kwarg stringa scelto per introspezione della firma, e al
r. 5803 `kwargs["actor"] = actor or "host"` — l'assenza d'identità diventa il
PROPRIETARIO; il fallback su TypeError riprova senza kwargs (5833). I
call-site del dispatcher uniforme (`invoke_tool_by_name` +
`_invoke_builtin_handler`) fra agent_runtime e orchestration sono ≥6 (grep).
[IPOTESI] Conseguenza: se set/delete_memories entra da questo dispatch senza
ridisegnarlo, un chiamante privo di actor muta la memoria del proprietario —
l'invariante 1 è violato dal punto d'innesto stesso. Il ridisegno fail-closed
del dispatch è più di una «patch minima» e va elencato come attività F0/F2
con test cross-principal sul dispatch, non solo sui confini di canale.

### 3. Tutte le fabbriche d'identità esistenti falliscono APERTE verso host — e il precedente TutorPrincipal non è citato
[PROVATO] actor_resolver.py: «non pairato: trattalo come host» e fallback
host ai r. 19, 48, 50, 53; http_routes_agent.py:309-314 `_resolve_actor` →
`request.get("device_id") or "host"`; tutor_boundary.py:47-59
`http_principal` → `user_id=str(device_id or actor or "http-user")`,
`actor=str(actor or "host")`. F0.3 (r. 2618-2619) implementa PrincipalContext
«nei due confini di canale», ma la fabbricazione avviene anche dentro il
runtime (rilievo 2) e nella factory Tutor. `TutorPrincipal`/`tutor_boundary`:
0 occorrenze in RM-0001 (grep) — eppure è l'unico principal oggi costruito ai
confini di canale in produzione, con la regola giusta già scritta («Map only
middleware-authenticated HTTP fields; body cannot elevate»,
tutor_boundary.py:49). [OPINIONE] Conseguenza: senza una riga che dica se
PrincipalContext sussume TutorPrincipal, F0 crea il secondo tipo di principal
della stessa casa; e l'inversione fail-open→fail-closed è un cambiamento di
semantica trasversale, da censire come attività con i suoi punti.

### 4. UC-02 è differito in attesa di un provider che esiste già: runtime/project_paths.json
[PROVATO] Righe 51-52: «Il caso Atlas/path resta differito finché un provider
deterministico di progetti o percorsi non produce gli ID candidati». Il
provider c'è: `runtime/project_paths.json` (nome progetto → code_root/
data_root + descrizione), reso da `_render_project_paths_block`
(agent_runtime.py:999-1031, ADR 0079) nel prompt PLANNER legacy
(prompts/it/planner/_footer.j2:27). Non è cablato nel proposer v3 (grep
`project_paths` in runtime/engine/ e in engine_proposer.j2 = 0) e RM-0001 non
lo nomina (grep = 0). Conseguenza: F3 e l'ADR di §22 punto 6 rischiano di
reinventare un registro esistente; il caso guida del documento aspetta una
cosa che il codebase possiede, da promuovere da blocco-prompt legacy a
provider di ID per il ReferenceSlot.

### 5. clause_span non ha sorgente: l'intent layer manca dalla tabella dei file da toccare
[PROVATO] `ExplicitMemoryCommand` porta `clause_span` (r. 2550) e la prova
della clausola top-level (invariante 19, r. 558-560). Nel motore le clausole
esistono solo come coppie (verb, object) senza posizione nel testo:
cache_validity.py:131-141; intent extractor → `actions=[{verb,object}]`.
`clause_span`: 0 occorrenze in runtime/ (grep). La tabella §25.1
(r. 2503-2515) non elenca né l'intent extractor né engine/dispatch.
[IPOTESI] Conseguenza: F0.4 (`commands.py`) o rifà una segmentazione propria
delle clausole — un secondo segmentatore, complementare al rilievo R6 della
lente semplicità che riguarda il lessico, qui si tratta degli span — oppure
l'estensione dell'intent layer va aggiunta alla tabella; oggi il contratto
non è producibile dai punti d'innesto elencati.

### 6. «Non ha capability filesystem sullo store» vale solo per subprocess: per un builtin in-process il meccanismo non esiste
[PROVATO] Le capability sono applicate dal sandbox dei subprocess:
`effective_capabilities` è consumata da runtime/sandbox.py (r. 193, 606, 722)
ed executor_standard.py; i builtin in-process condividono soltanto «signed
execution policy, central scheduler and assigned worker budget»
(agent_runtime.py:5813-5814) e girano nel processo server con la sua piena
autorità. [IPOTESI] Conseguenza: la garanzia di §9.6 (r. 833-834) per
l'adapter memories non è una proprietà del meccanismo capability ma disciplina
d'import; va riscritta come tale e il test architetturale di §27.5 va promosso
a gate di F2 (i quattro handler non importano sqlite3/user_store).

### 7. Il carico assegnato a users.py eccede la patch ammessa; authz_revision non ha alcun substrato
[PROVATO] §11.1 (r. 972-973) esige `prefs_revision` «nella stessa transazione
di set/delete preference», ma `_open_db` (users.py:81-87) apre in autocommit
(`isolation_level=None`), senza WAL né busy_timeout, ed esegue
`executescript(SCHEMA)` a ogni apertura; le API prefs sono keyed su stringa
`user_id_or_name` (set_pref, r. 668) — coerente con §5.1, che lo dichiara. La
patch ammessa per users.py è però solo «binding fra principal verificato e
account, senza API memoria» (r. 2507). `authz` in qualunque forma: 0
occorrenze in runtime/ (grep) — il campo `authz_revision` di PrincipalContext
(r. 725, 1210, 2546) non ha produttore; la revoca oggi è `verified_at=NULL`
(users.py:435-438), senza contatore. Conseguenza: transazione+revisione,
disciplina concorrente di users.db e contatore di revoca sono attività reali
di F0/F1 da aggiungere alla tabella, o il contratto §9.1/§11.8 nasce con campi
vuoti.

### 8. Il veicolo runtime-owned esiste già (`_RUNTIME_ARG_SOURCES`) ma ha sorgenti senza contesto, e RM non lo nomina
[PROVATO] Il meccanismo generale per arg runtime-owned che «il planner non
può valorizzare» e che «sovrascrive sempre, anche su resume» esiste:
`_RUNTIME_ARG_SOURCES` + `_fill_runtime_sourced_args`
(agent_runtime.py:3602-3647, ADR 0199). Le sorgenti però sono lambda SENZA
argomenti (es. r. 3612): non possono trasportare principal, turn_id o comando
del turno corrente. 0 occorrenze in RM-0001 (grep). [OPINIONE] Conseguenza: è
il precedente naturale per «usano il principale fornito dal runtime, mai un
user_id prodotto dal modello» (§9.6): F2 dovrebbe dichiarare se estende
questo registro con sorgenti a contesto di turno o se introduce un canale
parallelo — la seconda via duplicherebbe un meccanismo firmato appena
consolidato.

## Che cosa toglierei / che cosa aggiungerei

**Toglierei.** La frase «non ha capability filesystem sullo store»
(r. 833-834) nella forma attuale: sostituirla con il vincolo verificabile —
nessun import dello store nei quattro handler, test architetturale come gate
F2 (rilievo 6). [OPINIONE]

**Aggiungerei.** [OPINIONE]
1. Alla tabella §25.1: l'intent layer (produzione di `clause_span`), il
   dispatch builtin (`_invoke_builtin_handler` fail-closed), le fabbriche
   actor (actor_resolver, `_resolve_actor`, tutor_boundary) e la transazione
   prefs_revision+WAL in users.py (rilievi 2, 3, 5, 7).
2. In §9.3/F3: il censimento dei provider deterministici già esistenti —
   `project_paths.json` e i resolver ArgTransform già in pipeline
   (mail_account, calendar, self_recipient; engine/executor.py:1329-1371) —
   come candidati concreti del primo ReferenceSlot (rilievo 4; complementare
   alla scelta del primo slot chiesta dalla lente semplicità R5).
3. Una riga su TutorPrincipal: sussunzione in PrincipalContext o coesistenza
   dichiarata (rilievo 3).
4. Una riga su `_RUNTIME_ARG_SOURCES` come veicolo del principal/command
   verso i builtin (rilievo 8).

## Ciò che ho cercato e NON ho trovato

- [PROVATO] `InvocationContext`, `clause_span`, `authz` (qualunque forma):
  0 occorrenze in runtime/ (grep, 26/7).
- [PROVATO] `project_paths`, `TutorPrincipal`/`tutor_boundary`,
  `_RUNTIME_ARG_SOURCES`: 0 occorrenze in RM-0001 (grep).
- [PROVATO] WAL o busy_timeout in users.py: assenti (grep).
- [PROVATO] Un meccanismo di contenimento filesystem per codice in-process:
  assente — sandbox.py governa i soli subprocess.
- [PROVATO] Un innesto citato da RM-0001 come esistente e inesistente nel
  codice: NON trovato — dopo verifica sistematica di §5, §9.3, §9.6, §11.8,
  §12.5, §14.5-14.6 e §25.1 nessun percorso, funzione o meccanismo dichiarato
  «esistente» è risultato inventato. Il difetto che questa lente doveva
  soprattutto cercare non c'è.
- Non ho ripetuto le verifiche già a verbale nella lente danno (ArgTransform
  a executor.py:1306, sites_observed a session_broker.py:43, bypass LAN in
  http_auth.py, 117 `_msg(`): dove mi servivano le ho rieseguite in proprio
  (117 occorrenze di `_msg(` in orchestration.py riconfermate con grep).
