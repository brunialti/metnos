# Design TODO

Questo elenco raccoglie valutazioni architetturali non ancora approvate. Non e'
una fonte normativa e non autorizza modifiche a vocabolario, planner, executor o
routing.

## DLG-001 - Valutare il dominio logico `dialogues`

- Stato: aperto, solo analisi.
- Richiesta: 2026-07-11.
- Ambito: verificare se gli oggetti canonici `inputs` e `approval` debbano essere
  aggregati in una famiglia o in un dominio superiore `dialogues`.
- Vincolo: non implementare questa aggregazione finche' non viene presa e
  ratificata una decisione architetturale separata.

### Valutazione preliminare

L'aggregazione e' coerente come **famiglia logica**: entrambe le operazioni usano
un `dialog_id`, stato pendente isolato per mittente, scadenza, rendering per
canale, sospensione e ripresa del turno e la capability `dialog.user_input`.

Non e' invece dimostrato che debbano diventare lo stesso oggetto semantico:
`inputs` acquisisce valori tipizzati, mentre `approval` rappresenta un confine
di autorizzazione esplicito e determina il ramo successivo. Un input generico
non deve mai poter sostituire, implicare o auto-concedere un'approvazione.

### Criteri da verificare

1. L'aggregazione deve rappresentare un concetto autonomo, comprensibile e
   generale, non una somiglianza solo tecnica tra due executor.
2. Il contratto comune deve valere per tutti i canali e per tutti i domini che
   producono dialoghi, senza eccezioni legate a executor specifici.
3. Famiglia, sottotipi e capability devono provenire da una sola fonte
   deterministica; sono escluse mappe hardcoded per nome di executor.
4. La separazione di policy tra raccolta dati e concessione di autorita' deve
   restare verificabile dal planner, dai gate e dall'audit.
5. La soluzione deve definire migrazione o compatibilita' per `get_inputs`,
   `get_approval`, firme di cache, piani persistiti, routing, i18n e catalogo.
6. La nomenclatura deve rispettare la grammatica canonica e funzionare in modo
   naturale nelle lingue supportate, senza introdurre un linguaggio CLI rigido.

### Alternative da confrontare

1. Introdurre `dialogues` come oggetto canonico con sottotipi distinti e sicuri.
2. Conservare `inputs` e `approval` come oggetti pubblici e dichiarare
   `dialogues` soltanto come famiglia di catalogo/runtime nella fonte canonica.
3. Condividere esclusivamente infrastruttura e lifecycle, mantenendo separate
   anche le classificazioni pubbliche.

### Condizioni minime per una futura implementazione

- Una sola fonte di verita' genera in modo deterministico classificazione,
  catalogo e controlli, senza elenchi duplicati.
- I test dimostrano che planner ed executor non possono rimpiazzare
  `get_approval` con `get_inputs` nei punti che richiedono consenso.
- Il modello conserva isolamento per mittente, scadenza, idempotenza, audit e
  ripresa sicura del turno.
- L'impatto su compatibilita', cache e piani esistenti e' esplicito e testato.

## Programma prioritario

Questa sezione ricompone i punti di lavoro emersi dalla valutazione generale di
Metnos. L'ordine e' intenzionale: prima si stabilizza il contratto, poi si
ampliano integrazioni e catalogo. Ogni voce richiede metriche e un done-gate;
"codice scritto" non e' una misura di completamento.

### WIN-001 - Installazione software sui dispositivi Windows

- Priorita': alta (P1).
- Stato (17/8/2026): **progettazione chiusa, implementazione aperta**.
  Decisioni ratificate in ADR 0209 (dominio `packages`, verbo `install`,
  disinstallazione al posto dell'undo, regola dei permessi) e ADR 0210
  (l'aiutante elevato su Windows: canale, vocabolario chiuso, consenso,
  rimozione). Analisi di fattibilita' in
  `analysis_install_software_windows_16_8_2026.md`; istruzioni implementative
  in `spec_install_packages.md`, in quattro parti (vocabolario,
  `find_packages` esteso a Windows, `install_packages`, aiutante elevato).
  L'executor copre Linux **e** Windows, non e' un pezzo Windows con Linux
  aggiunto dopo.
- Permessi (ADR 0209 D4, emendato 17/8): nessuno installa su un dispositivo
  che non possiede, **l'amministratore compreso**; sul server, solo un
  amministratore. La prima regola e' gia' strutturale (filtro proprietario
  prima del placement, nessun ramo per l'amministratore); la seconda va
  costruita al choke-point di invocazione.
- Obiettivo: aggiungere un executor remoto tipizzato che installi o aggiorni un
  pacchetto su un dispositivo Windows posseduto, usando un gestore di pacchetti
  disponibile sul dispositivo e verificando identita', editore, fonte e
  versione prima di modificare il sistema.
- Confine di sicurezza: non introdurre una shell remota generica, comandi liberi
  o argomenti eseguibili. Dispositivo, pacchetto e versione devono essere
  espliciti; ambiguita', fonte non attendibile, richiesta di privilegi non
  autorizzata o gestore incompatibile devono fallire senza modifiche.
- Contratto: operazione idempotente, consenso esplicito prima dell'elevazione,
  stato e avanzamento osservabili, risultato verificato dall'inventario software
  del dispositivo e messaggi localizzati. Il rollback va dichiarato disponibile
  soltanto quando il gestore scelto lo supporta e la prova lo conferma.
- Generalita': rilevamento delle capacita' dal client e dai manifest firmati;
  nessun nome di pacchetto, lingua, percorso o host codificato nel runtime.
- Done-gate: test isolati per installazione, aggiornamento, gia' installato,
  pacchetto ambiguo o assente, firma/editore errati, privilegi negati,
  interruzione e postcondizione; quindi una prova reale controllata su Windows,
  senza perdita di configurazione e con audit completo.

### EXE-001 - Adozione dello standard executor

- Stato: fondazione approvata; migrazione incrementale aperta.
- Autorita': `EXECUTOR_STANDARD.md` e ADR 0193.
- Fatto: standard v1, validatore deterministico, enforcement delle dichiarazioni
  nel loader, template importer e test di non regressione legacy. Il registro
  capability canonico ora blocca i nomi sconosciuti per gli executor conformi
  e li segnala soltanto come debito nei legacy; censimento iniziale: 55 usi
  legacy non canonici su 83 manifest distribuiti.
- Fatto: autorita' provider per-invocazione chiusa su `provider:access`, sette
  executor provider-backed promossi, `get_processes` promosso con
  `system:read` e `compute_files_loc` promosso dopo il gate remoto.
- Fatto: chiuso il percorso IMAP locale di `read_messages` con `mail:read`, bind
  per-account in sola lettura e nessuna esposizione del vault web condiviso;
  promossi inoltre `list_dirs`, `find_dirs` e `read_files` con schema di output,
  input fail-closed e gate remoto.
- Fatto: promossi `find_images_indices` e `find_persons_indices`; chiusi gli
  hint semantici `index:read/image`, `persons_registry:local` e
  `arg:reference_images`; rimossi build e cache-write nascosti dal reader e
  verificata la non esposizione degli embedding biometrici. `get_images_indices`
  ora osserva lo storage unificato v4 e scopre i corpus senza path obbligatorio.
  Allineati inoltre i contratti dei gia' conformi `get_images_indices` e
  `get_persons`.
- Fatto: promosso `read_persons` nel lotto profilo separato con
  `metnos:read/identity_profile:local`, bind SQLite esatti in sola lettura,
  minimizzazione di canali/preferenze e nessun accesso a vault o home mail.
- Fatto: promossi `read_urls_html` e `get_urls`; `network:http` governa la rete,
  il cookie jar opzionale produce un solo bind dinamico RO e input/metodo/errori
  sono fail-closed e tipizzati. Il test bubblewrap reale prova che file
  adiacenti e segreti non vengono montati o restituiti.
- Fatto: promosso `compress_files` come mutazione server-only atomica e
  realmente reversibile. Non sovrascrive destinazioni, ripulisce i fallimenti,
  dichiara archivio e sole directory create per l'undo e preserva sempre i
  sorgenti; round-trip verificato anche in bubblewrap reale.
- Fatto: promosso `delete_files` come mutazione server-only con input e root
  fail-closed, backup content-addressed verificato, unlink successivo al commit,
  restore che non sovrascrive occupanti concorrenti e undo remoto sul device.
  Il gate ha incluso bubblewrap reale, remote/undo, un turno naturale che ha
  osservato l'assenza del file e la firma valida
  `sha256:4ecce01dce2db6aed5c0ed0d37af3624d57c1e1322d852f5833fd10389e9fa90`.
- Fatto: promossi in due lotti separati `get_places` e `move_files`.
  `get_places` dichiara `network:http`, placement server, output tipizzato e
  fallimento onesto del provider; `move_files` conserva placement remoto e
  reverse, espone `partial` e verifica postcondizione, ripetizione,
  interruzione e round-trip. I turni naturali verdi sono
  `749a5200d8b9424e` e `67bf37d94e244b9a`.
- Fatto nel lotto per dominio: promossi `create_dirs`/`delete_dirs`,
  `create_calendars`/`delete_calendars` e `get_approval`. I test ampi sono
  aggregati per dominio; i birth restano mirati. `dialog.user_input` e' ora
  nel registro canonico e concede soltanto lo storage del dialogo ristretto
  dal runtime. Gli E2E naturali di directory e calendari sono verdi; l'E2E
  `get_approval` resta da eseguire su una sessione con accesso HTTP locale.
- `create_events`/`delete_events` sono conformi: `fs:write`, reverse pattern e
  contratti tipizzati; suite calendario/window 28/28. Il cutover resta separato
  dalla promozione del manifest.
- Fatto nei successivi tre lotti di dominio: `get_inputs`, `find_packages` e
  `get_proposals`. `get_inputs` dichiara lo storage dialogo server-owned e
  fallimenti tipizzati; `find_packages` osserva soltanto gli eseguibili nel
  `PATH`, anche su device, senza `code:exec`; `get_proposals` legge audit e
  stato dormant tramite una risorsa Metnos chiusa e non crea piu' il DB durante
  una lettura. `lists` non e' stata forzata: l'oggetto non appartiene al
  vocabolario canonico e richiede una decisione separata.
- Stato corrente verificato il 2026-08-04: il report live rileva **119
  executor conformi, 0 legacy, 0 finding strutturali** su tutte le superfici
  (configured, loadable, admitted e planner). I conteggi storici riportati
  sopra non sono piu' validi.
- Da fare: riallineare la documentazione generata e verificare con un test
  dedicato che Synt e promoter mantengano l'ammissione fail-closed e rendano
  persistente la transizione `candidate -> conformant/active`.
- Vincolo promoter: non basta emettere `lifecycle=synthesized`. Il daemon oggi
  collega scrittura, `promoted_grace`, rollback e kill-switch; un artefatto
  invisibile al planner non puo' essere marcato promosso. Introdurre una sola
  transizione persistita `candidate -> conformant/active`, condivisa con Synt,
  e attivare grace e kill-switch soltanto dopo l'ammissione standard.
- Metriche: nuovi executor attivi non conformi = 0; dichiarazioni false accettate
  = 0; capability sconosciute ammesse come conformi = 0; regressioni dei flussi
  coperti = 0; debito legacy contato per categoria.

### EXE-DESC-001 - Analisi multidimensionale delle descrizioni legacy

- Stato: aperto, solo analisi; nessuna riscrittura massiva autorizzata.
- Baseline 2026-07-23: `manifest_lint --all` chiude con zero errori e 95
  avvisi, tutti relativi ai limiti editoriali delle descrizioni legacy. Il
  conformance audit strutturale resta verde su tutti i contratti.
- Obiettivo: stabilire quali testi eccedenti siano vero debito e quali
  conservino informazione necessaria al comportamento, quindi progettare una
  riduzione incrementale che non perda semantica, capacita' o disambiguazione.
- Dimensioni da analizzare: routing e selezione del tool; comprensione dei
  modelli locali; normalizzazione di argomenti e piping; confini `NON:` e
  autorita'; output e fallimento onesto; equivalenza IT/EN e altre lingue;
  costo token, latenza e composizione del pool; coerenza fra manifest, codice,
  template e tre percorsi di generazione/promozione degli executor.
- Metodo: classificare gli avvisi per famiglia, rischio e contenuto; misurare
  cosa vede realmente il proposer; confrontare prima/dopo su corpus naturale,
  query multidominio e near-miss avversariali; modificare una famiglia alla
  volta e conservare un rollback puntuale. Vietato accorciare automaticamente
  per solo conteggio di caratteri.
- Alternative da confrontare: testo piu' compatto a semantica invariata;
  spostamento della prosa implementativa fuori dalla testa macchina;
  descrizioni argomento strutturate o generate da metadati comuni; budget
  elastico invariato; revisione motivata dei cap soltanto se i benchmark
  dimostrano che il limite, e non il testo, e' errato.
- Done-gate: zero regressioni nei corpus routing IT/EN e nei flussi di riferimento;
  equivalenza di args/output/autorita'; nessuna perdita dei boundary critici;
  nessun nuovo falso positivo o falso negativo del linter; firme valide e due
  cicli di test consecutivi. Quantificare warning eliminati, token risparmiati,
  p50/p95 e ogni eccezione rimasta con motivazione esplicita.

### MCP-001 - Decisione sul confine MCP

- Stato: analisi completata, decisione e codice sospesi in attesa di confronto.
- Analisi: `internal/design/executor_contract_mcp_boundary.md`.
- Ipotesi preferita: MCP come trasporto/backend interno oppure proxy stretto,
  mai come secondo linguaggio libero visibile al planner.
- Da decidere: broker, modello di ammissione dei tool pubblici, mapping di
  identita'/schema/errori/autorita', invalidazione su `tools/list_changed`,
  isolamento credenziali, timeout, cancellazione e audit.
- Alternative da confrontare: backend di executor esistente, proxy executor
  conforme, quarantena. La riscrittura automatica in executor nativo resta una
  proposta da validare, non un comportamento ammesso.
- Done-gate dell'analisi: tre esempi reali mappati end-to-end senza riscrivere il
  catalogo, threat model, matrice semantica e decisione esplicita prima del
  primo cambiamento al runtime.

### QUA-001 - Flussi di riferimento e affidabilita' percepita

- Stato: completato il 23/8/2026 nella roadmap persistente
  `internal/roadmap/RM-0006-certificazione-logica-e2e.md`; C0-C6 certificate,
  cinque sonde reali verdi e due cicli finali 96/96.
- Decisione: la Fase 8 non e' piu' uno «stress logico» generico. Certifica 24
  flussi di riferimento in italiano e inglese, con oracoli congelati per piano ammesso,
  autorita', effetti, postcondizioni, risposta e recupero.
- Esecuzione: Metnos esegue la matrice in lotti tramite i normali confini; un
  coordinatore esterno e deterministico conserva l'indipendenza dell'oracolo.
  Il revisore apre tutte le anomalie e soltanto un campione dei successi.
- Confine: QUA-001 resta il puntatore breve. Matrice, fasi, costi, artefatti e
  criteri misurabili sono definiti esclusivamente in RM-0006.

### UND-001 - Completare gli annullamenti con ricevute esatte

- Stato: contratto generale e quattro riprogettazioni implementati il
  23/8/2026; resta aperta l'architettura locale di `delete_dirs` e la prova
  reale Windows dello stop tipizzato.
- Evidenza: `internal/reports/executor-undo-audit-20260823.md`.
- Implementato: `set_messages` usa il delta effettivo prima/dopo delle label;
  `open_sites`, `set_signatures`, `run_processes` e `login_urls` usano
  l'esito firmato per singola esecuzione di ADR 0217. Segreti undo cifrati
  fuori dal journal; stop Windows soltanto con PID+creation-time.
- Progettazione aperta: `delete_dirs`; specifica
  `internal/design/undo-redesign-spec-20260823.md`. La modalita' persistente di
  `run_processes` resta irreversibile finche' la registrazione di startup
  non ha un'identita' verificabile equivalente.
- Decisione di prodotto: `set_credentials` e `set_persons` restano non
  annullabili; cancellazione soltanto su richiesta utente esplicita.
- Vincolo: nessun inverso per nome, query o testo naturale; soltanto ricevute
  sigillate, stato precedente sufficiente e ripristino verificabile.

### RUN-001 - Avvio generale delle applicazioni Windows impacchettate

- Stato: da progettare; emerso dal turno reale `c77c005a1ce84cec` del
  23/8/2026. Il routing `run`, il device e il piping sono corretti, ma WinGet
  identifica Blocco note come `MSIX\\<PackageFullName>` e l'helper accetta oggi
  soltanto pacchetti portable registrati (ADR 0211).
- Vincolo: nessuna tabella di applicazioni, nessun path/comando dal chiamante e
  nessun avvio nella sessione SYSTEM invisibile all'utente. La soluzione deve
  risolvere in modo autorevole package/app identity, attivare nel desktop
  dell'utente proprietario e produrre una ricevuta di processo verificabile
  per l'undo di sessione. MSIX/AppX e future famiglie devono essere resolver
  tipizzati sotto lo stesso contratto, con ambiguita' fail-closed.
- Non confondere col bug chiuso da ADR 0218: `avvia/start/launch` instradano gia'
  a `run`, mai a `open_sites`; questo TODO riguarda solo una nuova famiglia di
  pacchetti Windows.

### RLS-001 - Installazione, aggiornamento e rollback ripetibili

- Stato: verificatore interno portabile implementato in
  `tests/tools/release_gate.py`, con specifica in
  `internal/design/release_lifecycle_verification.md`. Due cicli host completi
  consecutivi sono verdi: dipendenze isolate, fresh, upgrade, rollback, otto
  turni reali, vault sintetico, produzione invariata e pulizia.
- Da fare: estendere la matrice delle piattaforme supportate e progettare
  separatamente l'eventuale profilo amministrativo pubblico. Il gate duplica
  solo release, ambiente Python e stato sintetico: non copia credenziali reali
  e non replica LLM, motori di ricerca, geocoder o browser.
- Metriche: fresh install e upgrade/rollback verdi su matrice supportata,
  nessuna configurazione o credenziale persa, tempo operatore e passaggi manuali
  dichiarati.

### OPS-001 - Lifecycle integrato dello stack locale

- Stato: implementazione, gate e pilot live con rollback completati il
  2026-07-18; cutover dell'istanza legacy corrente intenzionalmente non
  eseguito.
- Evidenza: l'istanza corrente usa HTTP come servizio system e Playwright e
  side-display come servizi user. Lo split consente riavvii parziali e rende
  fragile l'allineamento del contratto di sorgente.
- Fatto: `metnos.target` user-level, readiness/quarantine/watchdog, health stack
  amministrativa, catalog parity, fingerprint Playwright, broker e turni
  quiescenti; reconcile serializzato con catalogo unit chiuso, firma solo di
  executor nominati e circuit breaker persistente. L'installer abilita il target
  sui nodi nuovi e non effettua un doppio bind sui nodi legacy.
- Migrazione: `prepare` conserva comando, working directory e ambiente
  allowlisted; `pilot` richiede almeno due cicli E2E e ripristina il baseline
  dopo ciascuno; `cutover` richiede evidenza/digest invariati e fa rollback
  automatico su errore.
- Certificazione finale senza modifiche intermedie: due cicli isolati da
  112/112 e due cicli host integrati da 132/132. Il pilot live ha completato
  2/2 cicli con readiness, turni naturali `c2801cabbd0041e4` e
  `1f50d0d6e4d148e3`, quiete e rollback verificato; Sites Chromium reale 5/5 e
  indice/recovery 139/139. Report in
  `internal/reports/stack_lifecycle.*` e `internal/reports/stack_live.*`.
- Stato live dopo il pilot: HTTP system attivo; HTTP user e `metnos.target`
  inattivi; Playwright allineato al contratto HTTP; catalogo 112, turni e code
  broker a zero. Firme executor 82/83, con la sola modifica concorrente
  `find_places` lasciata intatta e non firmata.
- Da fare operativo: il cutover resta separato e non autorizzato da questo
  lotto. Il servizio system rimane il baseline attivo; l'eventuale cutover
  futuro deve rivalidare evidence, host, contract e utente di servizio.
- Fonti correnti: implementazione e test del lifecycle, report
  `internal/reports/stack_lifecycle.*` e `internal/reports/stack_live.*`.

### SEC-001 - Audit di sicurezza indipendente

- Stato: aperto.
- Perimetro prioritario: vault e mandati, OTP email, browser/sites, prompt
  injection, sandbox, remote executor, MCP futuro, log e allegati.
- Done-gate: threat model aggiornato, revisione da soggetto diverso
  dall'implementatore, finding classificati e chiusura verificata dei bloccanti
  e alti.

### REL-001 - Affidabilita' osservata nel tempo

- Stato: aperto.
- Obiettivo: misurare mesi di uso reale, non soltanto suite sintetiche.
- Da fare: tassonomia unica degli incidenti, SLO per dominio, trend per versione,
  distinzione fra dipendenze esterne, planner, executor, UI e infrastruttura.
- Metriche: tasso di turni completi, falsi successi, parziali, timeout, recovery,
  retry, sessioni orfane e regressioni per release.

### CLN-001 - Pulizia generale controllata

- Stato: schedulata dopo la migrazione legacy dello standard executor.
- Obiettivo: ridurre duplicazioni, artefatti obsoleti e fonti di verita'
  concorrenti senza cambiare il comportamento pubblico verificato.
- Ordine KISS: inventario automatico; classificazione per proprietario e uso;
  copertura dei percorsi ancora attivi; rimozione o consolidamento in patch
  piccole; verifica completa; aggiornamento della documentazione generata.
- Perimetro: codice non raggiungibile, helper duplicati, generatori storici,
  template, fixture e report scaduti, documenti sostituiti, link e figure,
  dipendenze non usate e residui di installazione. Segreti, dati utente e stato
  operativo richiedono una procedura separata e non sono pulizia ordinaria.
- Vincoli: nessuna rimozione basata soltanto sull'eta' o sul nome; nessuna
  riscrittura cosmetica di componenti stabili; nessuna nuova astrazione senza
  almeno due usi reali; modifiche reversibili e indipendenti per famiglia.
- Metriche: fonti di verita' duplicate note = 0; riferimenti e asset orfani = 0;
  dipendenze dichiarate ma non usate = 0; suite ed E2E rilevanti senza
  regressioni per due cicli consecutivi; riduzione del debito quantificata nel
  report prima/dopo.

### PERF-002 - Pool e throttle trasversale di `read_urls_html`

- Stato: baseline controllata completata; modifica sospesa fino alla separazione
  fra ottimizzazione locale e policy trasversale. Report:
  `internal/reports/read_urls_html_worker_benchmark_2026-07-21.md`.
- Evidenza: same-host satura a 4 worker (p50 215,595 ms) e 8-32 non migliorano;
  multi-host continua a scalare fino ai 20 job (p50 57,390 ms). Tutti i 216
  output sono equivalenti e senza errori.
- Debito locale: il pool ignora il numero di host e crea fino a 20 thread anche
  quando il throttle consente soltanto 4 fetch. Verificare su HTML pesante la
  formula `min(job, cap_globale, host_distinti * cap_per_host)` prima di una
  patch mirata e firmata.
- Debito trasversale: due invocazioni hanno throttle indipendenti e portano la
  pressione dello stesso host da 4 a 8; su cinque host il picco osservato e' 40.
  Una classe worker centrale fissa non risolve il problema e classe 2 dimezza
  oltre misura il throughput multi-host.
- Done-gate locale: equivalenza su successi, parziali, timeout, ordine, iframe e
  HTML pesante; p50/p95 non peggiorate; thread inutili eliminati.
- Done-gate trasversale: decisione esplicita su budget per-host condiviso fra
  processi o serializzazione di insiemi di host sovrapposti. Non introdurre una
  falsa chiave singola per batch multi-host.

### CONV-001 - Query colloquiali, manuale RAG e tutor Metnos

- Stato: analisi preliminare completata; implementazione sospesa.
- Evidenza: i turni `fc3dfd282faf4f9a` ("cosa sai fare") e
  `a519e1554e00431e` ("com epuoi aiutarmi") hanno prodotto correttamente un
  intent privo di verbo e oggetto, ma il fallback sul catalogo completo ha
  selezionato rispettivamente `list_tasks` e `get_proposals`.
- Causa: l'assenza di un intent d'azione e' oggi trattata come insufficiente
  precisione di routing, non come possibile interazione senza azioni. La regola
  di non-rinuncia induce quindi il proposer a scegliere un executor non
  pertinente.
- Confine proposto: estendere l'output di `intent_extractor` con una union
  chiusa e language-neutral: `action`, `conversation`, `metnos_help`,
  `unknown`. Per `action` restano obbligatori verbo/oggetto canonici; se una
  frase contiene sia cortesia sia un'azione, vince sempre `action`.
- Linearita': nessun secondo planner e nessuna pipeline conversazionale
  parallela. `conversation` limita il pool al solo `final_answer`;
  `metnos_help` usa un executor documentale conforme e termina nel normale
  percorso di presentazione; `unknown` conserva il fallback prudente.
- Manuale: il corpus RAG deve unire documentazione pubblica versionata e stato
  live autorizzato (catalogo executor, servizi, configurazione e permessi),
  riportando fonti e confidenza. Una copia solo statica risponderebbe in modo
  obsoleto a "cosa puoi fare su questa installazione".
- Tutor: estensione successiva dello stesso percorso con stato minimo per
  argomento, livello dell'utente e progresso. Non e' necessario per il primo
  rilascio del manuale.
- Sicurezza e privacy: separare documentazione pubblica, interna e dati live;
  applicare lo stesso controllo accessi del dato sorgente; non indicizzare
  segreti, prompt riservati o log grezzi.
- i18n: classificatori, prompt, risposte e corpus devono seguire il repository
  multilingue; le classi restano identificatori neutrali.
- Done-gate: corpus intent esistente senza regressioni; parafrasi IT/EN per le
  quattro classi; nessun executor d'azione su query colloquiali; nessuna query
  d'azione assorbita da `conversation`; risposte RAG con fonte verificabile;
  comportamento onesto quando indice, LLM o stato live non sono disponibili.

### PKG-001 - La scheda di conferma non elenca le dipendenze

- Priorita': media. Trovata dal vivo il 17/8/2026 (turni e563a5ab, 234daad8).
- Sintomo: «installa LibreHardwareMonitor» mostra una scheda che dice
  «1 programma», ma winget ne installerebbe DUE — il pacchetto dichiara la
  dipendenza `namazso.PawnIO`. Chi approva sta autorizzando anche un secondo
  componente che non ha letto, e la scheda esiste proprio per evitarlo.
- Prova: `winget show --id LibreHardwareMonitor.LibreHardwareMonitor --exact`
  espone una sezione `Dipendenze: > Dipendenze dei pacchetti: > namazso.PawnIO`.
  Il dato c'e', non lo leggiamo.
- Perche' non e' stato chiuso subito: l'uscita di `winget show` e' LOCALIZZATA
  (le etichette sono tradotte) e non esiste un'uscita in JSON in
  winget 1.29.280. `--locale en-US` NON cambia la lingua della riga di comando
  e in piu' rende indisponibile la sezione del programma di installazione
  (verificato). Serve quindi una lettura per STRUTTURA, come quella gia' usata
  per le tabelle di `winget list`, e va validata su piu' lingue prima di
  fidarsene: nella stessa giornata due assunzioni sulla forma dell'uscita si
  sono rivelate sbagliate.
- Done-gate: la scheda elenca ogni pacchetto che verra' realmente installato,
  distinguendo quello chiesto dalle sue dipendenze; un test con l'uscita reale
  di winget in almeno due lingue.
- Nota collegata: la dipendenza di questo pacchetto e' un driver, che si
  installa per tutti gli utenti. E' il caso in cui «solo per me» non basta e
  serve l'aiutante amministrativo (ADR 0210, parte D).

### DEV-001 - «metnos» come nome di macchina: a volte il server, a volte il PC

- Priorita': da assegnare. Segnalata da Roberto il 17/8/2026.
- Sintomo: «temperatura cpu metnos» ha risposto per **PC-ROBERTO** (turno
  `1a07c3b8`), mentre «temperatura cpu server metnos» ha risposto per il
  server (`bb35aae2`). La stessa domanda, con e senza la parola «server»,
  finisce su due macchine diverse.
- Che cosa NON e': non e' il difetto della direzione install/uninstall
  (chiuso il 17/8 con `install_direction_resolver`), e non e' il caso
  «c'e' outlook su pc-roberto» (instradamento a `get_processes`, aperto
  sotto). Qui lo strumento e' quello giusto e sbaglia la MACCHINA.
- Ipotesi da verificare, non ancora misurate: (a) «metnos» e' insieme il
  nome del prodotto e il nome del server, e la risoluzione del device per
  nome puo' non distinguerli; (b) la destinazione appiccicosa di un turno
  precedente sopravvive alla domanda nuova; (c) senza un nome esplicito la
  scelta ricade su un device invece che sul server.
- Primo passo: misurare su una serie di richieste con e senza il nome della
  macchina, leggendo `target_device` dal registro dei turni. Il difetto e'
  riproducibile, quindi la misura si fa prima di toccare qualunque cosa.
- Done-gate: una richiesta che non nomina nessuna macchina risponde sempre
  per la stessa, e quale sia e' scritto nella risposta; una richiesta che ne
  nomina una risponde per quella.

### JOB-001 - Motore generico per lavori lunghi, persistenti e paralleli

- Priorita': da assegnare.
- Stato: F0-F2 completate il 2026-08-20. Il nucleo interno inattivo comprende
  contratti versionati, schema e migrazione SQLite, repository transazionale e
  macchine a stati; F3 (claim, lease e fencing) non è iniziata. La ricognizione,
  le fasi F0-F13 e le istruzioni per agenti esecutivi sono nella roadmap propria:
  **`internal/roadmap/RM-0004-motore-workload-durevoli.md`**. Il mandato
  originale resta in calce, integro, e va letto insieme all'analisi prima di
  qualunque modifica.
- Obiettivo: eseguire carichi lunghi o molto grandi **senza perdere il lavoro
  gia' svolto e senza duplicare gli effetti** dopo errori, interruzioni o
  riavvii. L'utente formula soltanto il risultato voluto; il sistema ne
  ricava un lavoro durevole, e resta osservabile (piano, limiti, modello,
  avanzamento, errori, artefatti).
- Confine: deve appartenere all'architettura esistente — richieste in
  linguaggio naturale, piani tipizzati, executor ammessi, autorita' minima,
  audit, postcondizioni osservabili, modelli per tier. **Non** un secondo
  agente separato, non un flusso cablato per un caso solo, non un accesso
  shell generale, non una via che aggiri scheduler, policy ed executor.
- Perche' e' una voce distinta e non un duplicato: ADR 0196 (politica
  centrale di esecuzione) e ADR 0204-0205 (ricorsione parallela
  deterministica) governano il parallelismo **dentro** una invocazione, che
  vive quanto il turno. Qui serve durevolezza **attraverso** i riavvii, che
  nessuno dei due copre. Verificato prima di aprire la voce.
- Prossimo passo: F3 della roadmap. Provare con processi concorrenti claim,
  lease, heartbeat, fencing del commit e ripresa, senza ancora collegare
  executor reali o superfici pubbliche. ADR 0213 mantiene rinviato il nome
  pubblico e vieta di attivare il worker prima dei gate successivi.
- Done-gate: un lavoro interrotto a meta' e ripreso non ripete un effetto
  gia' prodotto, e non dichiara completamento finche' ogni unita' d'ingresso
  non e' contabilizzata. Le prove complete e i gate intermedi sono in
  RM-0004 §18.

### I18N-DEDUP-001 - Controllo duplicati di chiavi e stringhe i18n

- Stato: check deterministico implementato in
  `tests/tools/i18n_duplicate_audit.py`; primo report in
  `internal/reports/i18n_duplicate_audit.json`. Nessuna traduzione e' stata
  modificata automaticamente.
- Baseline 2026-08-04: seed 2.292 righe, live 2.292, bundle device 1.786.
  I 48 gruppi di duplicati esatti sono stati classificati come alias
  contestuali in `internal/design/i18n_duplicate_allowlist.json`: nessun
  duplicato esatto resta non classificato e nessuna chiave pubblica e' stata
  cancellata. Restano 10 coppie quasi-identiche e 47 testi identici fra IT/EN
  da classificare.
- Finding oggettivi: 0 mismatch di placeholder e 0 drift seed/live/device.
- Prossimo passo: classificare le coppie quasi-identiche e quelle IT/EN,
  quindi riallineare seed/live/bundle prima di introdurre un gate CI bloccante.
  Le quasi-identiche osservate sono varianti legittime (singolare/plurale,
  messaggi generici/specializzati e hint contestuali). Le uguaglianze IT/EN
  sono per lo piu' termini tecnici o identificatori; restano da revisione
  editoriale i testi lifecycle come `proposals cleanup`, `deprecated` e
  `synth_proposals archived`, che potrebbero essere tradotti senza modificare
  il contratto runtime.
- Il controllo deterministico segnala collisioni/anomalie di chiave; testi
  identici o quasi-identici; coppie IT/EN identiche; divergenze di placeholder,
  punteggiatura strutturale o pluralizzazione fra duplicati candidati.
- Normalizzare soltanto per il confronto (spazi, Unicode, maiuscole e
  punteggiatura configurabile), conservando nel report testo originale,
  famiglia, lingua, call-site e placeholder. Distinguere duplicati esatti,
  alias intenzionali e somiglianze da revisione umana.
- Il check deve confrontare seed, DB live e `messages_i18n.json`, produrre un
  report stabile e fallire CI solo su collisioni o drift oggettivi. Nessuna
  chiave va rinominata, unificata o cancellata senza verifica dei call-site e
  approvazione esplicita.
- Done-gate: zero drift seed/live/device; falsi positivi documentati tramite
  allowlist motivata e minima; test IT/EN sui placeholder; nessuna variazione
  delle stringhe rese a runtime.

### RED-001 - Dominio Reddit e rassegna personale

- Priorita': da assegnare.
- Stato (22/8/2026): **sospeso in attesa della risposta di Reddit** alla
  richiesta individuale di accesso alla Data API. Nessuna implementazione e
  nessun accesso ai dati Reddit sono autorizzati prima della risposta.
- Obiettivo: elencare i subreddit seguiti dall'utente, rilevare nuove risposte
  e nuovi post, scartare duplicati e temi non scelti, produrre una rassegna
  privata con fonti e preparare eventuali bozze di post o commenti.
- Confine proposto: lettura periodica circoscritta al proprietario; interessi
  dichiarati dall'utente; elaborazione locale; conservazione minima; nessuno
  scraping, voto, addestramento sui dati Reddit, messaggio indesiderato o
  pubblicazione automatica. Ogni post o commento richiede una conferma manuale
  separata sul testo e sulla destinazione esatti.
- Condizione di sblocco: risposta scritta di Reddit che chiarisca piattaforma
  ammessa, permessi OAuth, classificazione e riassunto locali, conservazione e
  condizioni per un eventuale uso futuro non personale. Solo allora definire
  backend, executor, contratti, prove e decisione architetturale.
