# Metnos — CLAUDE.mutabile.md · PARTE MUTABILE

> Compagno di `CLAUDE.md` (parte invariante, governance in testa a quel file). **Questo file lo manutiene l'agente.**
>
> **QUANDO AGGIORNARLO** (ex §13): nuova decisione di runtime (tier LLM, helper universale, vincolo dominio, gate/env); chiusura fase o nuovo macro-topic; sezione contraddetta dal codice (aggiornare PRIMA della PR). **NON aggiornarlo per**: bug fix puntuali (commit message); decisioni temporanee/sperimentali e stato di sessione (→ memorie); dettagli di un singolo ADR (→ l'ADR). Stile: una decisione = poche righe operative + puntatore ADR/spec/test.

## S. Stato corrente (16/9/2026)

- **Candidate-local Birth — in development** (16/9): one common admission
  procedure; property/semantic checks remain outside catalog publication.
  Name reservation authenticates other contracts' signed identities without
  reopening their code. A broken unrelated payload no longer prevents repair
  of another contract, but still cannot execute or release its reserved name.
  Isolated real-path concurrency and refusal tests passed; this is not a live
  Synt latency claim or a completed deployment. G8 work plan §5.18.
- **Bounded Birth reads and release-code retention — in development** (16/9,
  ADR 0224 addendum): ordinary runtime selection checks the signed current
  head and its immediate edge, not the whole archive. The candidate is tested,
  not deployed. The standalone administrative verifier also reads only current,
  previous and optional pending state; its installed read-only proof passed.
  The release tool now retains current + one recovery tree, preserving live,
  mounted, service and future references. Installed cleanup removed code
  trees 1–51, kept 52/53 and freed 1.70 GB; signed evidence and data are intact.
  Exact proof and remaining work: G8 work plan §§5.16–5.17. The release
  coordinator's journal inventory remains a separate follow-up. This is not
  F5/F6 closure.
- **Shared temporal resolution — installed** (15/9,
  ADR 0176 addendum): schema-declared dates, instants and windows use one
  timezone-aware arithmetic. Versioned language resources handle common forms;
  one bounded local `temporal.interpret` call may interpret unfamiliar wording,
  but only deterministic code computes dates. Ambiguity uses the ordinary
  selection form, not a new permission. Mail filters receipt timestamps exactly.
- **Resumable photo indexing — installed** (15/9,
  ADR 0093 addendum): missing-index prerequisites enter the registered LRE plan;
  discovery, bounded analysis groups, merge and complete-generation publication
  are resumable units. Resource reservations bound concurrency. Native VLM
  startup paths may come from the root-owned `/etc/metnos/vlm-startup.toml`
  prerequisite, shared by HTTP and the worker; ordinary model settings cannot
  select executables. Real cold-start E2E as `metnos` passed; large-archive
  performance remains unmeasured. Incremental and full maintenance use the
  same LRE admission, including the nightly refresh. Current release, real
  acceptance tests and open limits: `internal/reports/rm0008-release-20260915.md`.
- **Permesso di avvio distinto dall'avvio automatico** (14/9, ADR 0218):
  una volta, fino al riavvio del PC o sempre fino a revoca. Riutilizzo per
  utente/app registrata/UUID del PC nel registro grants esistente; verifica
  dell'avvio Windows sul device, non della sessione Metnos. Vecchi consensi
  non promossi; installazione e chiusura restano separate.
- **Aggiornare Metnos e' diventato un ciclo a due comandi** (10/9): Release 3,
  4 e 5 attraversate in giornata. `internal/tools/rm0008_release_cycle.py
  prepare` (senza privilegi) riallinea radici riviste, elenco firmato,
  esportazione sigillata e messa in scena; `apply --cross` (radice) copia il
  candidato in una cartella di root, lo **rimisura li'** e solo da quella copia
  costruisce, esamina e attraversa. `internal/tools/install_release_authority.sh`
  installa una volta un lanciatore di root e una regola sudo ristretta ai due
  soli comandi: nessuna password memorizzata, nessun segreto, revoca con un
  file. Prova generale del ritiro: `rm0008_rehearse_withdrawal.py`, sei
  scenari sulla copia della catena viva. **Non e' ancora la capacita' di
  prodotto di §9.4**: resta uno strumento da albero di lavoro.
- **Due difetti vivi trovati provando, non dalle suite** (10/9): il caricamento
  del catalogo prendeva il lucchetto di ammissione **in modo esclusivo**, cosi'
  due lettori si escludevano e il guardiano dello stack affamava i turni (un
  `open_sites` su tre rifiutato); ora la lettura ha la propria meta' condivisa
  (`catalog_admission_lock(exclusive=False)`) e la riconciliazione libera il
  catalogo prima di passare a systemd. E `service-telegram-daemon` dichiarava
  `RestrictNamespaces=yes`, vietando gli spazi dei nomi che la sandbox degli
  executor crea: Telegram non poteva eseguire nulla. Entrambi **preesistenti**,
  entrambi nascosti da un servizio fermo. Una prova vieta ora la direttiva a
  qualunque servizio del catalogo.
- **Il consenso dentro uno shadow DOM ora si vede** (10/9, ADR 0191 addendum):
  l'osservazione della pagina attraversa gli shadow root aperti (tetto 24) e
  segue l'albero composto per punto di contatto, contenimento e testo. Un
  banner disegnato in uno shadow root era invisibile: la pagina risultava
  libera mentre era coperta e la scoperta del login non trovava il modulo.
  Iframe erano gia' gestiti; restano fuori i CAPTCHA.
- **Interprete amministrativo fisso e uscita in avanti** (9/9, ADR 0226):
  il costruttore ricava l'interprete amministrativo dal collegamento fisso del
  sistema operativo, mai da quello che esegue il build; la transizione rifiuta
  un descrittore incoerente PRIMA di fermare qualsiasi servizio. Una crociera
  che ha pubblicato la head e che la macchina dimostra non attestabile viene
  registrata da un documento immutabile accanto alla transazione, che conserva
  il suo ultimo record: la release successiva si apre sopra, senza riscrivere
  nulla di firmato. Motivo da vocabolario CHIUSO, dimostrato dal verificatore
  dentro il lock, con impronte del prerequisito coincidenti (percorsi diversi
  + impronte diverse = macchina cambiata, non abbandono). I quattro lettori
  della regola restano indipendenti e il loro accordo è asserito da una prova.
  **Abbandono della Release 2 eseguito il 9/9 alle 17:00**: documento scritto e
  riletto, transazione ferma a sequenza 5 HEAD_REQUIRED, servizi intatti. La
  catena ora ammette la Release 3; costruzione e crociera restano da fare.
  Referto: `internal/reports/rm0008-administrative-python-20260909.md`.
- **Cookie come precondizione semantica** (9/9, ADR 0191 addendum): il candidato
  usa `sites.cookie_resolution` sul modello locale, senza liste linguistiche.
  Osservazione Unicode limitata, due clic/quattro decisioni per flusso e
  ricontrollo dei nodi prima dell'effetto; nessuna chiamata LLM senza pannelli.
  Credenziali e autorità restano nel login. Test sintetici Chromium/modello
  superati; non ancora distribuito. Iframe, shadow DOM e CAPTCHA restano fuori.
- **Avvio distinto dalla certificazione** (9/9, ADR 0225): il verificatore
  amministrativo installato autentica il servizio esistente senza confrontare
  impronte storiche degli strumenti del sistema operativo o ricertificare
  unità estranee. Conserva firme, identità, file immutabili e protezioni del
  servizio. L'interprete gestito viene realmente eseguito dopo la rinuncia ai
  privilegi. HTTP, Telegram, browser e LRE hanno superato la ripartenza e il
  controllo completo; non è una prova di reboot né chiusura RM-0008.
  Modalità HTTP di manutenzione, configurazione SMTP per invocazione e comandi
  sullo stesso catalogo selezionato sono testati ma non ancora distribuiti.
  Checkpoint privato: `/opt/metnos/internal/reports/rm0008-maintenance-analysis-20260909.md`.
- **RM-0008: transizione F4 verificata in esercizio** (8/9, ADR 0224):
  release chiusa, catena e sette record durevoli riletti; HTTP, browser e LRE
  attivi, turni reali dell'ora e Tutor riusciti. LRE resta abilitato.
  Non ripetere la transizione. F5-F6 restano distinti: nessuna apertura implicita
  del preesercizio. Prove: `internal/design/handover_rm0008_verifica_8_9_2026.md`.
- **RM-0009 nella raccolta comune** (8/9): conservata la revisione 4 del 4/9
  in `internal/roadmap/RM-0009-crescita-allineata-delle-capacita.md`.
  Progettazione futura, implementazione non iniziata; non modifica i gate RM-0008.
- **Analisi richiesta/intent in corso (8/8)**: prima di riprendere prove di
  normalizzazione o intent extraction leggere
  `internal/design/handover_request_analysis_8_8_2026.md` e
  `internal/tools/request_analysis_lab/README.md`; contengono il checkpoint
  corrente, i blocker e le varianti da non rieseguire. È lavoro shadow, non
  ancora runtime.
- **Host**: `.33` (Strix Halo 96GB unified). Dopo la transizione RM-0008,
  HTTP (porta 8770), Telegram e worker sono unità SYSTEM del catalogo selezionato;
  llama-server risponde su `:8080`. Non usare i precedenti comandi USER.
- **LLM**: `fast` (`micro|procedural|fidelity`), `middle`, `wise`, `creative` e `frontier` sono contratti logici risolti centralmente da `runtime/llm_router.py`; ogni consumer sceglie un workload registrato in `runtime/llm_workloads.py`. Provider, modello, endpoint, temperatura, thinking e reasoning budget appartengono al router; al consumer restano tetto di output, deadline, grammatica e schema dei tool. I tre livelli fast, `middle` e `wise` condividono oggi Qwen 3.6 35B-A3B Q4_K_M/MTP `:8080` e la stessa policy deterministica, ma i livelli fast hanno default e override indipendenti in `[fast.level.<nome>]`; `creative` eredita il binding di `wise` finché non materializzato e usa `temperature=0.35`, mentre `middle` e `wise` restano a `0`. Frontier = Anthropic Opus opt-in. SoT: `runtime/llm_router.py::{DEFAULT_TIERS,DEFAULT_FAST_LEVELS}`, registro workload e ADR 0207; MAI nomi modello o override di policy nei consumer.
- **Slot LLM condivisi** (14/8, ADR 0120): Giorgio2 usa sempre lo slot 0
  (voce, observer e servizi); Metnos usa lo slot configurato da
  `METNOS_LLM_SLOT_ID`, default 1. L'affinità è applicata nei due client
  centrali, non nei singoli observer. Separa la cache KV, non il calcolo GPU.
- **Prod = engine v3**: drop-in systemd `proposer-hardening.conf` (`METNOS_ENGINE=v3`, grammar+verb_filter ON). I guard compound sono v3-gated → **bench compound SEMPRE con `METNOS_ENGINE=v3`**.
- **ADR registry**: `0001-0220` (skipped: `0055`/`0115`/`0116`/`0121`).
- **Avvio software ≠ apertura web** (23/8, ADR 0218): `run` e' il verbo
  canonico bilingue per avviare software gia' installato; `run_processes`
  risolve prima nomi umani Unicode con `find_packages` e accetta soltanto una
  identita' esatta/univoca. `open` resta solo sessione browser `sites`.
  Composizione e launcher gestito derivano da manifest/capability firmati,
  senza nomi di executor o applicazioni nel runtime.
- **RM-0005 F6 riaperto — localizzazione versionata** (29/8,
  ADR 0219-0220): il closeout del 23/8 non aveva censito tutti i lessici
  eseguibili del prefilter. Identità canoniche e seed editoriali restano dati; un
  registro SQLite versiona prompt, manifest, messaggi/UI, lessico, documenti,
  device e Tutor per qualunque tag BCP-47 strutturalmente valido. La pipeline
  materializza prima di tradurre, valida struttura e equivalenza, promuove
  atomicamente, rifirma i contratti e attiva la lingua solo dopo gate completo.
  Il job notturno è bounded, idempotente e non attiva autonomamente; device e
  Tutor ricevono solo risorse pubbliche ammesse. Fixture di terza lingua e
  suite i18n certificano fallback bootstrap e ripresa. Il percorso privilegiato
  richiede sintassi nativa pronta e revisionata; il censimento lessicale residuo
  e la prova reale devono concludersi prima di richiudere RM-0005.
- **RM-0008 gruppo 2, checkpoint storico superato dalla transizione F4** (27/8,
  ADR 0224): l'installatore prepara identita' autore, Admission e un archivio
  per capacita' Producer dentro una transazione durevole; nessun nome
  autorevole nasce definitivo e i tre finali arrivano con rinomine senza
  sostituzione. Il nucleo puo' consegnare soltanto fatti al pubblicatore
  sigillato. Al checkpoint del 27/8 il runtime Birth non era ancora attivo;
  per lo stato produttivo corrente vale l'aggiornamento F4 dell'8/9 in testa.
  Tre requisiti sono dichiarati non provati (confine Windows oltre la
  pubblicazione, uccisione reale a ogni passo di scrittura, due predispositori
  concorrenti). Dettaglio e criterio di uscita:
  `internal/reports/rm0008-gruppo2-analisi-implementazione.md` §13.
- **Lingua unica firmata** (23/8, ADR 0219):
  `runtime.config` definisce `INSTANCE_LANG`, `REQUESTED_LANG` e
  `LOCALIZATION_STATE` da una richiesta BCP-47 firmata Ed25519 e scritta
  atomicamente in fase 3; `METNOS_LANG` e' soltanto l'avvio per installazioni
  prive del documento. Un contesto di richiesta propaga la lingua d'istanza ma
  non puo' sostituirla; il lint F1 vieta il ritorno di lookup o override
  operativi per utente, canale e turno.
- **Undo condizionale** (23/8, ADR 0217): il manifest firmato puo' dichiarare
  `[undo] outcome="per_execution"`; il runtime accetta soltanto
  `reversible|no_effect|irreversible`, chiude sempre il turno e non contiene
  rami per executor. `open_sites`, `set_signatures`, `run_processes`
  (sola sessione con PID+creation-time) e `login_urls` usano ricevute esatte;
  i segreti precedenti stanno cifrati in `protected_undo`, non nel JSONL.
  Persistenza Windows e `delete_dirs` locale restano non annullabili.
- **Arresto AppX verificato** (23/8, ADR 0221-0222): client Windows 0.2.62.
  La ricevuta di `run_processes` lega confine di attivazione e processi
  preesistenti; l'inverso segue la coorte del pacchetto anche quando il processo di
  attivazione passa l'esecuzione, esclude identita' precedenti e dichiara
  successo soltanto con attestazione positiva di ripristino.
- **Destinazione conversazionale osservata** (24/8, ADR 0181 DEV-001): la
  memoria server/device e' circoscritta a proprietario, canale, attore e
  conversazione, scade dopo una finestra breve e viene aggiornata soltanto da
  un'esecuzione osservata. Gli alias configurabili dell'istanza risolvono al
  server nelle domande sulla macchina; un dispositivo esplicito prevale.
- **LRE F0-F14 implementato** (22/8, ADR 0213-0214, RM-0004):
  `runtime/durable_workloads/` contiene contratti, schema
  SQLite, archivio circoscritto al proprietario, acquisizione, concessioni a
  tempo, segnali di attività, delimitazione dei tentativi, ritentativo e
  ripresa; il worker supervisionato resta separato da HTTP e
  compone soltanto collegamenti di produzione registrati. Il preset immagini
  privato usa contratti OCR firmati, carichi logici su `wise`, diramazione
  tipizzata per elemento e tre artefatti idempotenti nel deposito. Una sorgente
  locale non viene mai ricostruita dal suo riferimento oscurato: prima
  dell'attivazione serve un'autorità esplicita del dispositivo. Il deposito
  privato degli artefatti è circoscritto al proprietario, indirizzato per
  contenuto e dotato di pubblicazione interna riconciliabile, raccolta prudente
  degli orfani e cancellazione selettiva. La facciata e la console circoscritte
  al proprietario espongono solo dati oscurati, eventi SSE persistenti e
  scaricamenti con autorizzazione temporanea revocabile; la coda Telegram
  conserva la consegna fino alla conferma. Il DB resta autorevole: una
  ripetizione identica è idempotente, una delimitazione obsoleta del tentativo
  non modifica lo stato e una pubblicazione diventa definitiva soltanto dopo
  la rilettura dell'impronta. F14 applica l'ammissione in un solo confine prima
  di ogni esecuzione del piano: una singola invocazione deterministica con
  durata dichiarata di almeno 600 secondi viene compilata automaticamente se
  argomenti, effetto e collocazione sono congelabili. Nessun profilo è
  richiesto; i piani registrati restano ottimizzazioni per grafi noti. Un
  carico lungo riconosciuto e non ammissibile fallisce in modo esplicito, senza
  ripiego in linea. L'installazione crea un solo interruttore privato,
  persistente e disattivato per impostazione predefinita; worker e HTTP lo
  interpretano con lo stesso lettore restrittivo. Guida bilingue e catalogo
  Tutor firmato descrivono il comportamento implementato.

Destinazioni di rete: `runtime/network_targets.py` risolve soltanto l'operando
dichiarato dalla grammatica centrale (inizialmente ping/ping6). IP letterali
invariati; nomi da dispositivi del proprietario, interfaccia della rotta server
e metadati nome/IP delle credenziali host. Il client firma indirizzo e tempo
dell'osservazione; dati scaduti o discordanti non diventano IP indovinati.
Il binding runtime giustifica solo quella sostituzione: numero di ping,
vaglio e consenso sull'argv effettivo restano invariati. Test:
`tests/runtime/remote/test_network_targets.py`.

## 3. Synth pipeline (6 stadi)

| Stage | Tipo | Workload → tier (SoT: `llm_workloads.py`) | Output |
|-------|------|------|--------|
| 1 NAMING | procedurale | `synt.procedural` → middle | `name`, `revertible`, `critical`, `target_kind` |
| 2 SIGNATURE | procedurale | `synt.procedural` → middle | `args_schema`, `capabilities`, `reverse_pattern` |
| 3 TESTS | procedurale | `synt.procedural` → middle | 4-6 test (felice, lista vuota, args invalidi, edge) |
| 4 DESCRIPTION | creativo | `synt.description` → creative | description §2.5 + affinity |
| 5 CODE | creativo+proc. | `synt.multistage` → wise | `<name>.py` con `def invoke()` |
| 6 VERIFY | fedeltà semantica | `synt.semantic_verify` → wise | allineamento description ↔ code |

Vincoli: vocabolario chiuso SOLO in stage 1; ogni stage vede la fetta minima di contesto; quality floor (no degradare a fast); sintesi locale only.

**SoT del tier di un workload = `runtime/llm_workloads.py`** (chiusa 5/8). Il censimento `internal/design/llm-invocation-census.md` proponeva di sciogliere `middle` in `fast.procedural` e di spostare le trasformazioni fedeli su `fast.fidelity`; la Decision di ADR 0207 **non** ha accolto quella parte e tiene `middle` e `wise` come ruoli separati coi loro carichi. Il registro implementa l'ADR; il censimento porta l'emendamento, e i commenti nel runtime sono stati riallineati. Conseguenza dichiarata: `fast.procedural` e `fast.fidelity` restano livelli configurabili **senza consumatori** — configurarli non sposta alcun carico finché un workload non ci viene spostato. La pagina Modelli lo scrive sulla scheda.

Il nucleo dei manifest generati e' centralizzato in `generated_executor_contract.py`: i tre generatori condividono intestazione standard e politica `[execution]` seriale, validate prima della scrittura. Il modello locale puo' variare l'implementazione, non identita', ciclo di vita, autorita', I/O o classe iniziale; worker interni soltanto via `executor_helpers.assigned_workers()` (ADR 0196).

## 4. Planner/Engine — contratti dei piani

- **4.1 Da-piping fra step**: liste fra step → SEMPRE `from_step: N` (int); valori singoli → placeholder `{{stepN.field}}`. NIENTE `entries: "{{step1.entries}}"`.
- **4.2 Caso degenere N=1 con literal**: letterali (path, url) come typed list arg inline. OK `delete_files(paths=["/tmp/x.txt"])`; ERRORE `delete_files(from_step=1)` con history vuota.
- **4.3 Action verbs portano a termine**: verbo d'azione esplicito → read/find/get → classify/filter (se serve) → VERBO_AZIONE → final_answer. Niente `describe_entries` PRIMA del verbo d'azione.
- **4.4 Cap**: 12 step max per turno; stesso executor 3× di seguito = loop_break; `DUPLICATE_CALL` → `final_answer`.
- **4.5 Undo**: `undo_last_turn` ok con `undone_count>=1` → step successivo DEVE essere `final_answer`. Mai due undo nello stesso turno.

## 5. Vincoli di dominio (nel PLANNER prompt)

- **EMAIL/IMAP** → `*_messages`; mai `move_files` su mail; cancellazione = `move_messages(dst_folder="Trash")` (`delete_messages` non esiste).
- **FOTO/EXIF/GPS** → `get_files`.
- **IDENTITÀ/PROFILO** → `read_persons(name="${RUNTIME:actor}")` per "chi sono io"; `read_persons(role="guest")` per lista paired; distinto da `get_persons` (registro biometrico). ADR 0163.
- **ENROLLMENT** → dominio `*_persons` (elenco=`get_persons()`; «cancella l'enrollment di X»=`delete_persons(names=["X"])`). MAI `*_credentials`.
- **POSIZIONE** → `get_location`. **TEMPO/DATA** → `get_now`. Una ricerca di luoghi riferita a chi chiede («la più vicina», «qui vicino») NON porta il centro nella query: lo aggiunge la guardia `ensure_proximity_center` incatenando `get_location`. Il server nominato come centro risolve `subject=server`, separato dalla posizione dell'utente e dalla collocazione dell'esecuzione. Un luogo nominato nella richiesta resta il centro e non viene toccato. Photon usa l'indirizzo del registro servizi; errori di trasporto non fanno ripetere l'espansione del raggio.
- **DESTINAZIONE spam/cestino/archivio** → nome utente come `dst_folder`; l'executor risolve via `M.list`. Non hardcodare `INBOX.Junk`.

## 11. Decisioni di runtime

- **Ciclo di vita dei servizi** (4/8): il catalogo centrale comprende soltanto
  componenti del prodotto. Il timer `metnos-i18n-translator.timer` è nucleo
  obbligatorio: `metnos.target` lo richiede, la prontezza lo controlla e la UI
  non ne consente l'arresto. Unità private di sviluppo o accesso personale
  (per esempio cruscotti interni e tunnel) restano attive e governate fuori dal
  catalogo, dal Tutor e dall'installer pubblici.
- **Glossario livelli** (12/6): **fastpath=L0** cache della stessa query (hash+coseno; può tenere args concreti); **autopath=L1** piano generalizzato per cluster (scheletro senza args, promosso dal ✓ umano; store `autopath.sqlite`); **executor**=singolo tool firmato; **skill**=INSIEME di executor (bundle, ADR 0170). VIETATO «skill» per il piano L1.
- **Tool-use protocol**: nativo (tool_calls strutturati). NIENTE parser JSON fragile.
- **Routing deterministico** (8/6): seed fisso `METNOS_LLM_SEED` (default 42; `-1`=random); affinity-match boost nel prefilter rompe i pareggi fra fratelli stesso-object.
- **Describe deterministico** (12/6): testo byte-riproducibile via processo `llama-completion` monouso (temp=0+seed); gate `METNOS_DESCRIBE_DETERMINISTIC` (ON); fallback HTTP onesto `meta.deterministic=false`. **Cap anti-runaway map-reduce** (6/7): `METNOS_DESCRIBE_MR_MAX_ENTRIES` (default 100, 0=illimitato) — oltre: prime N + nota utente NEL summary + campi §2.7.
- **Compound: path di planning UNICO = engine** (ADR 0177 D1): decomposer eliminato; in `compound_decomposer.py` restano solo helper condivisi. Guard deterministici align/enforce anche sugli HIT cache L0/L1; `_compute_intent_sig` compound-aware; clausole STORE normalizzate a `entries` pre-cache (ADR 0174).
- **Provenienza args** (ADR 0177 S4 + `internal/design/spec_args_provenance_architecture.md`, 6-7/7): mappa runtime/clause/semantic in `runtime/arg_provenance.py`; config-args marcati `runtime_resolved` (il proposer li nasconde; politica-invariante `tests/runtime/infra/test_config_args_marking_policy.py`, esenzioni intent-bearing in `is_intent_bearing_config`); backstop `coerce_args_to_schema` = Guard #0 (drop fuori-schema/leak marcati, enum case-normalize o drop; esenzione arg guard-owned per idempotenza); registro `Guard` tipizzato + oracolo di equivalenza golden (PROV.1-3). Cat. C (`overwrite_phantom_install_args`): rimovibile con journal `[phantom_install]` 0-fire ≥14gg (verifica ≥21/7).
- **Backend multi-provider** (ADR 0165/0136): selezione provider = config, non intento; `backend_resolver.OBJECT_BACKENDS` = events, files, contacts, **dirs** (7/7: cartelle Drive via NL; `delete_dirs` risolve i nomi name-first via `find_dirs`). Injection enum-aware: il default per-object non scavalca l'enum del tool; MAI iniettare un arg non dichiarato; l'esplicito non è clampato (errore onesto a valle). Provider via CLIENT-ARG, non executor-suffisso.
- **Autorita' provider per invocazione** (16/7, ADR 0193): `provider:access` con binding da `vocab.PROVIDER_SKILLS`; `runtime/capabilities.py` applica la clausola chiusa `when={arg,values}` sul valore finale/default. Per executor conformi il binding effettivo governa insieme rete, home credenziali RW e pin al server; nome/`client` non concedono autorita'. Solo i legacy conservano i 5 segnali storici come ripiego.
- **Provider locali privilegiati in sola lettura** (20/8, ADR 0212): un manifest firmato può dichiarare una dipendenza `mode="provider"` attivata da un booleano. Il server emette un mandato monouso legato a manifest e invocazione; client e aiutante lo riverificano e l'aiutante espone soltanto interfacce tipizzate chiuse. Il profilo firmato può indicare l'assembly figlio diretto e il tipo d'ingresso; metodi e proprietà restano fissi nell'interfaccia. Nessun percorso, comando, script o argomento libero attraversa il canale. Un nuovo programma compatibile richiede solo profilo dati e installazione dalla UI, non codice Metnos; installazione, avvio processo e lettura restano contratti distinti.
- **Autorita' dei dialoghi runtime** (19/7): `dialog.user_input` e' una capability canonica non critica e senza approvazione ricorsiva; `sandbox.dialog_extras` restringe la scrittura alla sola directory pendente del mittente. `get_approval` e' server-only e non esegue il ramo prima della decisione umana.
- **Politica centrale di esecuzione executor** (20/7, ADR 0196): ogni invocazione attraversa `executor_scheduler`; metriche, retropressione e limiti del pool sono comuni, ma default e fallback sono classe 0 seriale e il pool trasversale resta spento. Classi 1-3 = solo budget hardware-bounded, non autorita': richiedono equivalenza verificata; un effetto non read-only richiede anche chiave+identita' d'isolamento. Nessun executor esistente cambia semantica per adozione implicita.
- **Ricorsione parallela deterministica** (30/7, ADR 0204-0205): `runtime/parallel_walk.py` divide gli alberi per directory, bilancia una frontiera dinamica e ricompone per percorso; `accept`/`transform`/`descend` mantengono il dominio nei callback. `find_files`, `find_dirs`, `list_dirs`, `compute_files_loc`, `create_images_indices` e `find_files_hash` usano il visitor; `find_urls` conserva la BFS rate-aware ma usa lo stesso budget centrale. Il runtime deriva da CPU visibili e `max_workers` un solo tetto per istanza; classe firmata, task disponibili e profilo I/O possono soltanto ridurlo. Un futuro carico multiutente userà un pool centralizzato distinto. `find_files_hash` esegue dimensione -> campione negativo -> SHA-256 completo, limita solo la presentazione per default e usa una cache `stat`-validata, opaca e separata per utente.
- **Tutor F2 locale** (23-24/7, ADR 0197-0198): un catalogo firmato unifica manifest ammessi, ogni HTML indicizzabile nell'esatta radice pubblicata `docs/` inventariata da `runtime/published_docs.py`, fonti supplementari selezionate in `tutor/sources.toml`, registri runtime esplicitamente proiettati e guide curate; le schede F1 sono compatibilità transitoria, non il limite della copertura. Rifiniture 24/7 sera: audience di CONOSCENZA distinta dall'audience di ACCESSO (`UiSurfaceSpec.knowledge_audience`, contratto `audience_minima` F1; devices=`user`); inventario capacità = unit testa + parti con finalità dai manifest, ricongiunto in selezione (espansione fratelli con membri protetti); ledger di copertura da TUTTE le fonti strutturate selezionate; banda `METNOS_TUTOR_KNOWLEDGE_BAND` (default 0,06); registro superfici con guardia anti-deriva `structure_sha` (refresh: `python3 runtime/ui_surfaces.py`; analisi e fine-stato manutenzione 0 in RM-0003 §5.3). L'inventario valida `canonical` HTTPS `metnos.com`, `html lang`, gruppi `hreflang`, confini e assenza di symlink; `noindex` e dati non pubblici restano esclusi. I documenti sono segmentati per struttura e dimensione; al compositore arrivano solo segmenti semanticamente pertinenti e, in modo bounded, quelli adiacenti. Ranking BGE-M3 e ingresso `EXPLAIN|ACT|MIXED|UNKNOWN` senza frasi/affinity, fallback lingua per-concetto verso EN, audience prima del modello; i workload registrati sono `tutor.mode` → `fast.micro` e `tutor.compose` → `wise`, entrambi attraverso il gateway LLM centrale e lo slot `llm` classe 0. `precise` non è una chiave di tier. Il composer usa il contratto d'uscita `public`, che rifiuta marker strutturali interni. Un solo scambio Tutor, isolato per principal+conversazione, resta in RAM per 15 minuti ed entra nel contesto soltanto con guadagno semantico. Lacuna documentale e indisponibilità tecnica sono esiti distinti; se il sottosistema fallisce, una richiesta semanticamente Tutor riceve un messaggio tecnico esplicito senza eseguire operazioni, mentre un'azione ricade nel motore. Il Tutor non possiede strumenti; azioni o casi dubbi ricadono nel motore. I blocchi pubblici `tutor-exclude` mostrano roadmap senza indicizzarla come capacità corrente. Notte 24/7: **correttore di bozze deterministico** post-composizione (rilettura meccanica del ledger, UNA ricomposizione con i buchi elencati, consegna comunque, fail-soft; parole distintive per frequenza documentale interna; telemetria `tutor_repair_pass`/`tutor_repair_missing`; RM-0003 §5.7) + gate del corpus a radice flessiva (`lex:tutor_gate.*` in `detection_lexicon`, 8 concept regex it+en) con skip DOCUMENTATI nel certificatore (photos=card curata c1, overview EN=pari-fallimento F1). 25/7: prompt del Tutor in §6 a SOLI segnaposto (mode v3, composer v9; un esempio letterale produce imitazioni semantiche — misurato 103→106/134, admin_ui 17→21), confine procedure co-tematico (si scarta solo se la primaria è un'altra pagina), budget composer 2048 per-call; guida pubblica all'interfaccia `docs/{it,en}/interface.html` generata da `ui_surfaces` (mappa SVG; dettaglio pagine NON duplicato, autorità unica al registro; freschezza da test + preflight `deploy.sh`). 26/7: la sonda di compagnia è la **congiunzione** della domanda precedente con quella corrente in UNA sola classifica (non due), unica leva sopravvissuta a sette tentate; il ledger del composer è un **budget saturo** — aggiungere una famiglia di voci al prompt costa più di quanto renda, una variante deve agire FUORI dal prompt; si certifica a macchina scarica e non si emettono verdetti sotto i 3 casi (banda di rumore misurata = 1 caso a vuoto, 2 sotto contesa di GPU). cert31 = 115+7/134, `boundary` 12/12; RM-0003 §9-septies. **16/8: zero fonti non e' una lacuna.** Il Tutor e' un'uscita anticipata prima del motore: con `gap_reason=no_source` (nessuna fonte ammissibile) restituisce `None` e il turno torna al motore, invece di chiudersi con «non ho una guida». La lacuna resta un esito dichiarato dove e' PROVATA — il compositore ha le fonti e non riesce a rispondere. Misura sullo storico: 13 turni su 167 chiusi a vuoto, «dov'e' il Duomo di Milano» fra questi. Il turno Tutor registra ora `user_query` come un turno del motore (l'impronta resta chiave del registro F4): senza, l'ammissione non e' verificabile a posteriori. Decisione accolta da Roberto e messa a verbale in ADR 0208; il ledger F4 resta senza domanda in chiaro (ADR 0202), che e' un'altra cosa dal registro dei turni.
- **Tutor F3/F4** (28/7, ADR 0202): fonti firmate richiamano soltanto quattro sonde chiuse e in sola lettura (executor ammessi, servizi, dispositivi posseduti, task dell'attore); capsule tipizzate con audience, limiti, TTL e stato esplicito entrano solo nella composizione finale. Le richieste `MIXED` vengono separate in una clausola `EXPLAIN` e una `ACT` letterali; la seconda passa al normale motore soltanto tramite pending monouso owner-bound, dopo conferma. Il riscontro promuove o rimuove associazioni query-vettore→fonte per il solo utente; ledger delle lacune, quote, TTL, invalidazione di fonte/embedder, cancellazione e replay controfattuale restano fuori da planner e cache L0/L1.
- **Tutor: identità delle fonti pubblicate** (28/7, ADR 0203): nome completo, percorso relativo o URL canonico di una pagina ammessa vengono risolti deterministicamente prima del mode gate. Lettura/descrizione resta `EXPLAIN`, modifica/uso concreto resta `ACT`; il retrieval informativo è vincolato allo `source_ref` esatto e la similarità ordina solo le sezioni di quella fonte. Nomi assenti o ambigui ricadono nel normale dominio file, senza ereditare un dispositivo remoto per una domanda documentale riconosciuta.
- **Il confine del catalogo ha due meta'** (10/9): una lettura prende
  `catalog_admission_lock(exclusive=False)` — esclude ogni pubblicazione, non
  esclude un altro lettore; scrittura, firma, pubblicazione, riconciliazione e
  ritiro restano esclusivi. E' sicuro perche' il caricamento era gia' protetto
  da se': impronta dello store prima e dopo, rifiuto esplicito
  (`store_snapshot_unstable`) se si muove. Il lucchetto esclusivo non
  aggiungeva correttezza a un lettore, toglieva disponibilita'. Un lettore che
  rientra chiedendo la scrittura e' una promozione di lucchetto e viene
  rifiutata; il contrario resta lecito. Chi riconcilia rilascia il catalogo
  **prima** di passare a systemd: tenerlo durante riavvio e attesa di prontezza
  affamava il server che stava avviando.
- **Cache-validity** (ADR 0182): ogni piano cachato (L0/L1/alternative-LRU) porta `tools_sig`+`pool_sig` VERIFICATE A LETTURA → mismatch=MISS; re-sign o capacità nuova invalidano per costruzione. Firmare SEMPRE col catalogo del chiamante.
- **Undo** (ADR 0183): scrittore al choke-point `invoke_executor` (`_undo_pending`/`_undo_done`, campo `device`); reverse device-aware accodato allo STESSO device; `restore_blob_backup` non remotabile. **Self-update client** firmato+idempotente (ADR 0184).
- **Learning-loop W1** (ADR 0185): turno costoso ripetuto → autopath **shadow** (il ✓ umano conferma); lacuna ricorrente → change_intent PROPOSED (triage umano su /admin/changes); review notturna TTL 21gg. Soglie via env; SEED_STEPS=4 (confermato 7/7).
- **Crescita della GUARD_PIPELINE: si governa con la misura, non con la migrazione** (6/8, decisione di Roberto delegata; analisi `internal/design/analysis_planner_growth_6_8.md`, diario `planner_growth_worklog.md`). Chiuso il cricchetto dell'esenzione di schema (`strip_unknown_args` in uscita), unificato l'inserimento di step (`insert_steps`), persistiti gli spari (`engine/guard_stats.py`) e portato l'oracolo di equivalenza sui piani reali nel repo (804 casi). La migrazione delle famiglie B/C/D/F a superfici dichiarative **non si fa**: misurate sul journal reale, sono quelle che portano il traffico (440 spari su 451 in 21 giorni), mentre la famiglia quasi muta era la E — già trattata col primo ritiro della storia della pipeline. Riapertura a criterio esplicito (quarto report-pipeline; quinta guardia che inserisce un produttore). Il ritiro e' ora un evento ordinario: `guard_stats.dormant()` propone le candidate nel riepilogo notturno, il verdetto resta umano in quattro passi. Aggiunta 16/8 `ensure_proximity_center` (centro geografico da `get_location`): e' una guardia che inserisce un produttore e va contata verso il criterio di riapertura.
- **Un esito gia' spiegato dal motore non si riscrive** (16/8): `TurnLog.error_class` porta la classe del `DispatchResult` fino a `write()`; per le classi in `_AUTHORITATIVE_UNFULFILLED_CLASSES` (oggi `capability_missing`) la sostituzione §4.3 «azione non completata, riformula» non si applica. Una richiesta che nessuno strumento sa eseguire riceve la ragione, non un invito a riprovare.
- **Manutenzione domini esterni = comandi NL schedulati** (ADR 0186): mai job bespoke; organi interni = builtin in `NIGHTLY_SEQUENCE`; osservatori esterni = timer di sistema. Aging: esenzioni alla fonte.
- **Esito task schedulati** (ADR 0186, 11/7): `CallbackOutcome` separa stato semantico da consegna canale; `run_user_query` deriva `success|partial|error` da fallimenti/effetti reali. Un push Telegram riuscito non maschera una pipeline fallita; `failed[]` alimenta `last_error` e circuit-breaker.
- **User prefs** (ADR 0187 W2-v1): tabella `user_prefs` vocabolario CHIUSO; UI /admin/users. Website browsing = `sites_browser_mode=headless|side` + master `sites_stealth` + tecniche `on|off` generate dal registro, tutte default off (ADR 0191).
- **Conoscenza utente locale — solo design, non implementata** (RM-0001 `ready`, ADR 0200 proposta, 26/7): W2 resta autorevole per preferenze e default tipizzati; il profilo libero non entra nel planner o nelle cache condivise. Valori personali soltanto dopo il piano tramite dichiarazioni firmate dei domini; routine pre-L0 solo come forma canonica di verbi/oggetti/slot senza valori personali. Apprendimento implicito non sensibile automatico per il proprietario dopo certificazione, ospiti spenti; controllo dalla chat; oblio con invalidazione coda e restore in quarantena. Esperienza executor, dense, Leiden e MCP fuori da RM-0001.
- **Conversazione chat trasferibile fra device HTTP** (27/7, ADR 0201): principale, `conversation_id` e lease `device_token` sono separati. Al conflitto la UI offre annulla / attiva questa conversazione / continua la precedente; il takeover è atomico, owner-bound e protetto da prompt stantii. History locale scoped per conversazione, recent server owner-bound, vecchio writer read-only e submit validato. Tutta la superficie chat, iframe `get_inputs` incluso, usa `msg()` e `ui_lang`; guard statica + seed IT/EN vietano nuove scritte hardcoded.
- **Sites F1+F2 CHIUSO** (ADR 0188, 11/7): session broker Playwright owner-bound; `login_sites` intelligente drop-in (stesso I/O pubblico) attraversa consenso, target off-viewport, landing, login, username/continue/password e handoff 2FA con ciclo bounded; TOTP solo opt-in nel vault. `act_sites(search)` naviga goal post-login con riosservazione e un gate batch. Campi credenziale deterministici; autorita' fill = `sites_origin.origin_authorized` (origini esplicite = match esatto fail-closed; chiave assente = stesso-sito first-party, rev. 14/7 turn 025c53fa; delega = token one-shot a tupla esatta), screenshot mask; modello solo su ID broker-owned e mai dopo fill; allowlist extra approvata; `sites` fuori cache. Chiavi `MSG_SITES_RC_*` con nomi inglesi. **Discovery risorse con provenienza** (13/7): fallback implicito post-`selector_missing` propone SOLO host first-party/origine-credenziale; host terzi solo con evidenza causale esatta (`required_hosts`); passi goal auditati con target risolto+confidence+URL. E2E Booking `727f9ba37b3d4732` verde end-to-end.
- **Executor intelligenti** (ADR 0189, 11/7): agenti a mandato ristretto, drop-in per il planner; stesso I/O e stessa autorita' dell'executor normale, ciclo bounded deterministic-first, modello solo entro azioni enumerate, postcondizione verificabile e handoff/fallimento esplicito. Pattern ortogonale ai domini, non nuovo linguaggio. Catalogo first-party per dominio generato dai manifest firmati con `scripts/generate_executor_catalog.py`.
- **Mandati credenziale** (ADR 0190, 12/7): il form web sceglie `interactive` o `sites.read`; lo scope cifrato e' il default per ogni query interattiva e schedulata. La query puo' restringerlo; un ampliamento interattivo resta one-shot. Il task aggiunge soltanto un envelope subordinato esatto e fail-closed; revoca del binding immediata.
- **Form credenziali iniettati dal dominio** (24/7, ADR 0199 + addendum notte): sezione `[credential_form]` nel manifest FIRMATO di un executor del dominio consumatore (mail=`read_messages`, site=`login_sites`, provider=nella skill, generico `api`=`set_credentials`); la collezione dal catalogo ammesso gira NEL SERVER (la sandbox non ha chiavi trusted) e viene iniettata nell'arg runtime-owned `credential_forms` al choke-point (meccanismo generale `runtime_source` in `_RUNTIME_ARG_SOURCES`, vale anche su resume, sovrascrive sempre); round di dialogo ADR 0090; ritorno valori con `merge_into: "fields"` = re-invocazione diretta, segreti MAI dal planner. Nuovo tipo = sezione manifest + chiavi i18n (seed rigenerato), zero codice.
- **Igiene filiera proposte** (ADR 0180): generatori specialize/generalize RITIRATI (introvertiva=solo dedupe); adapter attivi telos (cluster-head)/introvertiva/synt/user_feedback; **accept di una pipeline = eseguirla una volta** in scheduled-scope; killer `layer_overlap` nell'auto-evaluator.
- **Data piping**: `from_step: int` + `{{stepN.field}}` per scalari.
- **Output terminale dei flussi composti**: se l'ultimo producer impacchetta artefatti (`compress`/`extract`), governa la presentazione finale anche quando l'intento primario era una ricerca. La chat mostra una ricevuta con cartella e artefatti, non la tabella tecnica dell'executor; le tabelle legittime restano contenute nel bubble e scorrono orizzontalmente sui valori non spezzabili.
- **Intent extractor**: LLM `fast`, fallback bag-of-words, bypass deterministico per undo; compound → `actions=[{verb,object}]` per clausola (routing pool per-clausola).
- **Universal helpers**: `classify_entries`, `filter_entries`, `extract_entries`, `undo_last_turn` sempre; `describe_entries` SOLO se intent.verb non è d'azione; `extract_entries` = testo non strutturato→record tipizzati (date ISO 8601), confine §2.2.
- **Reverse patterns**: `runtime/reverse_patterns.py` — 5 entry deterministiche (§2.3). Gap noto: il ramo gw dei creatori multi-provider non è pattern-undoable (`delete_created_paths` non copre ids).
- **Platform policy**: `runtime/platform_policy.py` — system files cross-mount-safe + protected paths host-aware.
- **Messaggi**: `runtime/messages.py` — dizionario unico code→template `ERR_*/WARN_*/MSG_*/LOG_*`; mai stringhe duplicate negli executor. Norma i18n: §7.13 (parte invariante).

## 12. Fasi di sviluppo

- **Fasi 1-5 chiuse** (POC / test framework / synt 5 stadi / reality check+Telegram / vaglio+sandbox+dispatcher).
- **Fase 6** voce — STANDBY. **Fase 7** (client Rust executor remoti) — **CHIUSA** (8-9/7): topic 1 MVP + C7 mutanti (write/move/delete remoti, undo device-aware ADR 0183); topic 2 multi-user (device→users.id, owner-filter), multi-OS = Windows+Linux (macOS ESCLUSO da Roberto); robustezza (blob-TTL, co-location consumer↔producer, device-i18n bundleato). **W4 AppContainer pienamente in prod**: `appcontainer::gate_on()` default ON su Windows (opt-OUT `METNOS_SANDBOX_APPCONTAINER=0`), + skip AppContainer per `code:exec` (get_processes/tasklist con CPU nativa Win32); **self-update robusto/automatico** (launcher-loop, respawn `Stdio::null()`) validato live (auto-update 0.2.21→0.2.22 in ~13s zero-manuale). PC su **0.2.62**. Handoff `project_w4_prod_enable_followup` = OBSOLETO. **Fase 8 — CHIUSA** (23/8, ADR 0215, RM-0006): 24 flussi di riferimento IT/EN, cinque sonde reali e due cicli finali 96/96; non e' una prova di carico. Metnos esegue i casi, mentre oracolo, postcondizioni e verifica restano esterni al processo sotto prova.

## 14. HTTP API

Server `runtime.metnos_http_server` porta **8770** (8765=pairing). aiohttp bare: ROUTES tuple list, `_error()`, `auth_middleware`; ruoli anonymous/user/admin (admin key `~/.config/metnos/admin.key`, 0600). Endpoint `/agent/{health,turn,devices/me,session/*}` + `/.well-known/metnos.json` + `/admin/{,changes,executors,executors/stats,runs,safety,turns,caches/*/flush}`. Negotiation HTML (htmx+Jinja2+uPlot) vs JSON; ETag su collezioni admin; SSE sui turni e sugli eventi di revoca sessione. Sessione, conversazione e storage browser (`conv`, token, command buffer, history v3) sono owner-scoped e indipendenti per utente; solo l'host importa le chiavi legacy single-user. ADR 0078+0201.

## Preferenze di comunicazione di Roberto (12 agosto 2026)

- Ogni comunicazione diretta a Roberto deve usare parole semplici, spiegate
  “per dummies”, anche quando il lavoro interno è tecnico.
- Non mostrare stdout, log grezzi o dettagli operativi dei comandi: riferire
  soltanto risultato, rischio e prossima decisione in forma breve.
- Quando serve una scelta, presentarla con alternative concrete e comprensibili;
  non trattare un assenso generico come approvazione di opzioni non capite.
