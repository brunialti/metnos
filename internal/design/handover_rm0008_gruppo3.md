# RM-0008 — prompt di subentro per il gruppo 3

Da incollare tale e quale a un agente che subentra. Aggiornare solo se cambia
lo stato descritto nella prima sezione.

```
Continua RM-0008 nel worktree /tmp/metnos-rm0008-a-only (ramo main).
NON toccare /opt/metnos, non creare rami.

STATO: il gruppo 2 e' chiuso. L'installatore prepara un insieme di autorita'
completo ma INERTE: il runtime Birth non e' attivo e nessun chiamante e'
migrato. Il ciclo pubblico e' verde su tutti e nove i lavori. Tutto e'
committato; non esiste lavoro in file temporanei.

LEGGI PRIMA, IN QUEST'ORDINE:
0. internal/reports/rm0008-regole-di-lavoro-fra-gruppi.md
   — cinque regole valide per TUTTI i gruppi dal 3 al 6, misurate sul gruppo 2.
     Si ereditano cosi' come sono. Contengono anche la regola che i piani di
     dettaglio di un gruppo si scrivono QUANDO quel gruppo inizia: i gruppi 4-6
     non hanno un piano e non devono averlo in anticipo.
1. internal/reports/rm0008-gruppo3-piano-ottimizzato.md
   — cosa fare ora, le quattro ottimizzazioni, l'ordine che evita di rifare i
     vettori golden, e §7 la PROCEDURA DI RIFOTOGRAFIA (otto passi, trappole
     gia' pagate: eseguila cosi', non improvvisarla).
2. internal/reports/rm0008-gruppo2-analisi-implementazione.md §13
   — criterio di uscita compilato del gruppo 2, con i tre requisiti dichiarati
     NON provati. Non spacciarli per provati e non toccarli senza mandato.
3. lo stesso rapporto §17.70-§17.81
   — diario: cosa e' stato costruito, i difetti trovati, e soprattutto le
     deduzioni sbagliate corrette dalla misura. Leggile: impediscono di rifare
     lo stesso giro.

COMPITO: il gruppo 3, seguendo il piano ottimizzato. Rende attivo cio' che il
gruppo 2 ha predisposto. L'ordine del §4 del piano non e' negoziabile: portare
gli `enforcement_state` a `productive` va fatto PER ULTIMO, perche' cambia
identificativo ed epoca e obbliga a rifare tutti i vettori golden.

MODO DI LAVORARE:
- commit piccoli e tematici solo su main, con il marcatore
  `RM-0008-Status: candidate-not-certified` in coda al messaggio;
- pubblicazione incrementale:
  `METNOS_VENV=/opt/metnos/.venv bash scripts/publish-public.sh --incremental -m "<inglese>"`;
- dopo OGNI pubblicazione verifica il workflow pubblico e non proseguire se e'
  rosso;
- riporta una sola riga di avanzamento per volta, in parole semplici.

QUATTRO VINCOLI CHE COSTANO CARI SE IGNORATI:
1. Ogni modifica alla base congelata costa un ciclo intero di rifotografia: nel
   gruppo 2 ne sono serviti dieci. ACCUMULA le modifiche alla base e congela
   UNA VOLTA SOLA alla fine dell'incremento.
2. La cella R1 del grafo produttivo respinge ogni nuova "porta" verso la
   capacita' che scrive su disco. Ha colto due errori veri di collocazione:
   estendila dichiarando esattamente chi puo' passare, non aggirarla.
3. Non riesercitare cio' che il gruppo 2 ha gia' certificato (primitiva a
   handle, giornale, documenti canonici, disposizione). Prova il contratto del
   TUO gruppo.
4. Non costruire strumenti diagnostici per curiosita': nel gruppo 2 tre su tre
   hanno risposto per conto proprio prima di dire la verita'. Costruiscine uno
   solo se la sua risposta cambia una decisione, e fagli dichiarare come l'ha
   ottenuta.

REGOLA DI ONESTA': "solo i test necessari" non significa "solo i test che
passano". Cio' che non provi va scritto come non provato, con il motivo, nel
criterio di uscita del gruppo 3.

DECISO (Roberto, 27/8, delegata): caricare codice da un percorso passa da UNA
PORTA CHE AUTENTICA — `runtime/admitted_module_v1.py`. Non si vieta e non si
concede per fiducia: si concede per autenticazione. Vedi §10 del piano. Unica
eccezione dichiarata: `undo_last_turn`, la cui modifica e' gia' scritta in
`internal/design/patch_undo_last_turn_porta_autenticata.diff` e va presentata
come PRIMA intenzione quando Birth e' attivo (dopo il cutover un executor
cambia solo cosi': `sign.py publish` risponde «unavailable in STORE_ONLY»).

APERTO E DA NON RISOLVERE A OCCHI CHIUSI: su Windows il predispositore arriva
fino alla rinomina che pubblica il primo finale e riceve accesso negato. Il
privilegio NON c'entra (misurato). L'ipotesi "manca DELETE nella maschera" e'
in tensione con due celle verdi: non toccare la maschera prima di una misura.

GIA' FATTO DEL GRUPPO 3, tutto committato, pubblicato e VERDE su tutti e nove
i lavori (sedicesima fotografia; ultimo pubblico verde `3a4840e`):
- `runtime/executor_birth_prepared_set.py` rilegge l'insieme sotto la propria
  barriera e rifiuta se marcatore, insieme, archivi e materiale non concordano;
- `runtime/executor_birth_prepared_root.py` e' la porta del runtime, in SOLA
  LETTURA, e all'avvio **ricostruisce il materiale dalla distribuzione
  installata** confrontando identificativo, epoca e digest (§9.4): la
  descrizione registrata non basta;
- `runtime/executor_birth_context_v1.py` tiene catalogo e fabbrica del
  contesto, importati sia dal predispositore sia dal runtime — una sola
  implementazione, mai due;
- `runtime/executor_birth_policy_v1.py` tiene i due fatti autorevoli che hanno
  lasciato il file di configurazione (versione della politica, durata delle
  ricevute), decisi con Roberto;
- la cella R1 ammette due sedi nominate e la porta del runtime solo finche' non
  tocca una mutazione, con due mutanti a dimostrarlo;
- prove: `tests/portable/rm0008_2b/test_group3_*.py`.

**Regola 1 applicata e misurata**: dodici cicli in tutto, ma gli ultimi due
incrementi hanno accumulato sei e poi due modifiche alla base con UNA sola
fotografia ciascuno, invece di una per modifica.

- `runtime/executor_birth_producer_table_v1.py` chiude la provenienza: autore
  fisso per produttore (undici righe), tipo derivato da dove vive il manifest e
  da nient'altro. Deciso con Roberto; il §6.quater del piano spiega perche' la
  tabella fissa per il tipo sarebbe stata falsa per sei righe su undici.

- lo SCAMBIO ATOMICO e' fatto (§6.quinquies, un solo commit): l'avvio monta
  ogni autorita' dall'insieme letto sotto barriera, il nucleo riceve il
  pubblicatore sigillato e gli consegna soltanto fatti, e nello STESSO
  passaggio spariscono `_build`, `_load_authorities`, `_context_builder` e la
  lettura di `bootstrap.json`. Un decodificatore libero accanto a uno sigillato
  sarebbe la doppia verita' che questo gruppo toglie (§9.1).
  Due difetti corretti passando: un rifiuto di contesto si presentava come
  indisponibilita' generica, e il pubblicatore non confrontava l'epoca
  osservata con quella predisposta.
  La cella R1 tratta ora la porta di sola lettura come TERMINALE della
  raggiungibilita': chi la chiama eredita una sessione con cui puo' solo
  leggere. Senza, ogni chiamante dell'avvio sembrava un mutante.

- le due basi dati durevoli (ricevute e approvazioni) stanno sotto la cartella
  di stato con la protezione rimessa: lo scambio l'aveva lasciata cadere. Una
  sola entrata tratta i due archivi allo stesso modo (obbligo 10 del §2).
- il cancello dei lavoratori NON deduce piu' lo stato inattivo da un rifiuto:
  una radice assente e una lettura fallita hanno lo stesso codice. Ora cerca il
  marcatore e chiede la domanda vera. Sparito anche il parametro che nominava
  un file di configurazione inesistente.
- corretto un fallimento non nostro: `tests/e2e/driver/test_birth_bootstrap.py`
  importava un modulo che da quel livello non esiste e rompeva la raccolta
  dell'INTERA suite e2e da quando e' nata. Rimossa insieme al predispositore
  e2e che scriveva un documento che nessuno legge; la suite raccoglie pulita.
- su Linux il fondo della sandbox lo dichiara un REGISTRO, non l'ambiente
  (obbligo 4, parte Linux): `LinuxSandboxRegistry` nomina `bwrap` e
  l'interprete e li confronta col digest un istante prima dell'uso, con un
  manico che rifiuta i collegamenti finali. Senza registro il fondo Linux e'
  indisponibile, come lo era Windows senza il suo. Tre rifiuti nominati:
  registro assente, programma sparito, programma che non corrisponde.
  NB: nessuno predispone ancora quel registro — il provisioner non scrive nulla
  sulla sandbox. Il prossimo passo dell'obbligo 4 e' registrarlo nell'insieme.
- i due contenitori di dipendenze presi in prestito DERIVANO ora da quello
  ombra (`replace`) invece di rielencarne i campi: aggiungerne uno li aveva
  rotti entrambi in silenzio, mascherato da `birth_unavailable`.
- il LINTER dei manifest smette di essere solo un'identita' e decide (obbligo
  1): una verifica del ciclo di nascita applica `lint_manifest` sul manifest
  congelato, in OGNI lingua che il candidato dichiara, e un rilievo "errore"
  rifiuta la nascita. Le lingue le dichiara il candidato, mai la macchina.
  Il catalogo delle verifiche cresce di un membro; l'identita' del contesto NON
  si muove, perche' il catalogo non ne fa parte (verificato: i vettori golden
  del contesto restano validi).
- il candidato deve REGGERSI DA SOLO (obbligo 2 + meta' del 7): una verifica
  legge ogni suo file e decide tre cose — che si analizzi, che un import
  relativo resti dentro il candidato, che non ci sia codice montato mentre gira
  (`exec`/`eval`/`compile` builtin). Costo MISURATO a zero sui 93 file degli
  executor pubblicati; una cella rifa' quella misura a ogni giro.
- le PRIMITIVE delle proprieta' si risolvono da una tabella chiusa (obbligo 3,
  meta'): `runtime/executor_birth_primitive_table_v1.py` possiede l'insieme una
  volta sola; il corridore confronta i quattro registri con la tabella nei due
  sensi all'importazione. Il digest della tabella e' pronto per il componente
  del contesto, che lo prendera' all'ULTIMO passo.
- caricare codice da un percorso passa da `runtime/admitted_module_v1.py`, che
  ricalcola il digest dei file dichiarati, lo confronta con quello firmato ed
  esegue i byte GIA' IN MEMORIA. La verifica del candidato rifiuta i sei nomi
  che caricano da percorso. 🚨 La porta sta FUORI dallo spazio dei nomi
  `executor_birth_*` di proposito: dentro il cancello la valutazione dinamica e'
  vietata (R1 l'ha colto), e una cella prova che nessun modulo del cancello la
  importa.
- 🚨 LEZIONE: la cella R1 legge l'albero TRACCIATO. Un file non ancora aggiunto
  all'indice non viene esaminato: in locale si verifica DOPO `git add`, o il
  ciclo pubblico trova cio' che tu non hai trovato.
- i due MODELLI interni del cancello escono da una tabella chiusa (obbligo 3,
  completo): `runtime/executor_birth_template_table_v1.py` possiede il
  programma che il corridore Linux lancia e l'istruzione del revisore
  semantico; un nome non elencato e' un rifiuto, non una stringa vuota. Digest
  ricavato dal testo, pronto per il componente del contesto all'ULTIMO passo.
- corretto un altro fallimento non nostro della famiglia gia' vista:
  `import conftest` prende quello della suite raccolta per PRIMA. Ora il
  conftest del runtime si carica per percorso con un nome suo
  (`tests/runtime/infra/_runtime_conftest.py`).
- i registri di autorita' installati sono DAVVERO consumati (obbligo 5):
  `tests/portable/rm0008_2b/test_group3_authority_consumption.py`. Due
  installazioni con attori diversi producono autorita' diverse nel nucleo. E
  l'esito e' piu' forte del previsto: modificare un registro DOPO la
  predisposizione non cambia l'autorita', la RIFIUTA — il documento
  dell'insieme ne porta il digest, quindi i byte sono autenticati, non solo
  letti.
- il FONDO della sandbox si misura una volta e si congela nell'insieme
  (obbligo 4, parte Linux): `runtime/executor_birth_sandbox_registry_v1.py`.
  Non e' un'opinione dell'amministratore, e' una misura della macchina. La
  grammatica chiusa della disposizione ha due voci nuove (`sandbox_container`,
  `sandbox_registry`) e la base 2A e' emendata di conseguenza. Windows resta
  DICHIARATO NON PROVATO: il predispositore non completa su Windows.
- OGNI componente del contesto e' ora `productive` (obbligo 6, l'ultimo).
  Identificativo ed epoca sono cambiati. La cella che lega politica e identita'
  ora lo prova con una RETROCESSIONE, perche' non resta altra mossa.
- 🚨 LEZIONE (tredicesima fotografia, pagata due volte): torna indietro col
  prodotto anche una prova PUBBLICATA che vive FUORI da
  `tests/portable/rm0008_2b/`. `test_executor_birth_runner_linux_real.py`
  nominava il registro della sandbox e ha fatto fallire il lavoro Ubuntu
  ordinario nello stato precedente la correzione.

PROSSIMO PASSO: i dodici obblighi del §2 del piano sono CHIUSI, tranne il
legame Windows del registro sandbox, dichiarato non provato. Restano: (a)
compilare il criterio di uscita del gruppo 3 con cio' che e' provato e cio' che
non lo e'; (b) la modifica di `undo_last_turn` come prima intenzione, quando il
cancello e' attivo; (c) decidere con Roberto se ritirare `find_persons_indices`
(misura: 0 invocazioni su 8716 passi reali) — da cui dipendono le due prove in
sandbox ancora rosse.
```

## Stato operativo del blocco Windows — 28/8/2026, misura discriminante

Questa sezione e' il punto di ripresa corrente. Non sostituisce lo storico del
gruppo 2: registra soltanto cio' che la nuova misura ha deciso.

- Commit sorgente della sonda temporanea: `582032df`.
- Commit esportato su `main` pubblico: `32602fa`.
- Ciclo GitHub Actions: `33152572168`; solo il lavoro Windows portatile e il
  riepilogo dipendente sono rossi. Lavoro Windows: `98787762928`.
- Esito: i primi **66** spostamenti di file completano apertura, controllo del
  profilo, chiamata nativa e riconciliazione. Lo spostamento **67**, il primo
  spostamento di una directory finale, arriva a `NtSetInformationFile` e viene
  rifiutato con `ERROR_ACCESS_DENIED` (5). La sorgente era stata aperta con
  successo usando `mutating_open` e il suo profilo era valido.
- L'ipotesi del §14 del piano e' quindi **falsificata**: togliere
  `WRITE_DAC|WRITE_OWNER` dalla maschera era corretto per minimo privilegio,
  ma non risolve il blocco. Il rifiuto non avviene durante l'apertura.
- Il caso produttivo che manca alla base e' ora delimitato: il predispositore
  sposta un **albero non vuoto** e la sessione conserva maniglie autenticate
  sui suoi discendenti; `cached-source-renames` misura soltanto una directory
  vuota. La specifica Windows `MS-FSA 2.1.5.15.12` stabilisce che
  `FileRenameInformation` rifiuta con accesso negato una directory che contiene
  file aperti. Questo coincide con il punto e con il codice osservati.
- Non e' autorizzato un secondo giro diagnostico. Il prossimo cambiamento deve
  prima chiudere la decisione sulle capacita' discendenti: non si possono
  semplicemente chiudere maniglie in cache se esistono
  `_SecureDirectoryHandle` consegnati al chiamante. Occorre una soluzione che
  conservi il confine autenticato e renda esplicita la validita' delle
  capacita' dopo lo spostamento.
- La sonda temporanea e' stata rimossa dal cambiamento candidato. Al suo posto
  c'e' una cella Windows di certificazione che pretende installazione completa,
  documento sandbox autenticato e valore esplicito
  `windows_backend_not_measured`; non stampa diagnostica e non trasforma un
  rifiuto in verde.

**Correzione candidata, gia' verificata localmente.** Il checkpoint
`verified` e' gia' il confine durevole dal quale il predispositore sa ripartire
senza sorgente precedente e senza rileggere gli ingressi dell'operatore. La
prima sessione si ferma li', rilascia in modo ordinato lucchetto e maniglie, e
una seconda sessione riprende la stessa transazione e pubblica. Non vengono
chiuse di nascosto capacita' consegnate al chiamante; non si allargano DACL o
maschere; non esiste una politica di tentativi. Il passaggio e' limitato a due
sessioni: una seconda richiesta di riapertura e' un'ambiguita' di recupero.

Verifica locale del candidato:

- gruppo 3: **227 passate, 1 salto Windows atteso**;
- cella nuova sul confine: due sessioni esatte, distinte e chiuse, con la prima
  gia' chiusa quando nasce la seconda;
- manifesto e grafo produttivo 2A: **7 su 7**;
- resto portatile 2A: **85 passate** e le solite cinque celle che richiedono
  `sudo` rosse soltanto nell'ambiente locale;
- inventario Python rigenerato meccanicamente per sostituire il file della
  sonda con la cella di certificazione; nessuno schema o classificazione e'
  cambiato.

PROSSIMO PASSO: commit del candidato e una sola misura pubblica Windows. Se la
cella Windows passa, aggiornare il criterio di uscita del gruppo 3 e riportare
`main` pubblico interamente verde. Se fallisce, non applicare altri tentativi:
registrare il fatto nuovo e lasciare il legame Windows dichiarato non provato.

## Fallimenti locali che NON sono del gruppo 3

In una copia di lavoro nuova la suite `tests/runtime` non e' tutta verde. Stato
accertato, cosi' nessuno li scambia per regressioni:

- **riferimento dei domini** (2 celle): il file e' generato da
  `scripts/generate_domain_reference.py` e `.gitignore` lo tiene fuori dal
  repository di proposito. Ora le celle **saltano dichiarando il comando** da
  eseguire, invece di rompersi con un errore muto. Sistemato.
- **lettura URL in sandbox** (`test_executor_standard_url_reader`):
  **RISOLTO 28/8** (`0e6156ad`). Causa: `runtime/backends/urls/httpx_default.py`
  importava tutti e quattro i moduli executor all'istante del caricamento.
  Fuori dalla sandbox andava perche' ogni cartella era raggiungibile; dentro e'
  legata solo quella di chi chiama. Ogni entrata ha bisogno di UN fratello e
  ora chiede quello. Non e' lo stesso difetto dell'altra cella: qui a sbagliare
  era il RUNTIME, non un executor.
- **lettura indici in sandbox** (`test_executor_standard_index_readers`):
  **diagnosticato, difetto di prodotto vero, non risolto perche' e' una scelta
  di progetto.**
  `executors/find_persons_indices` delega importando il modulo del fratello
  (`import find_images_indices`), ma il suo manifest dichiara
  `files = ["find_persons_indices.py"]` e basta. La sandbox lega quello che il
  manifest dichiara, quindi dentro l'isolamento l'import non trova il fratello.
  Funziona solo dove la sandbox espone per caso anche gli altri executor.

  Due riparazioni possibili, e non sono equivalenti:
  (a) l'executor smette di delegare per import e passa dal runtime — piu'
      pulito, perche' un executor e' un'unita' firmata e importare il modulo di
      un'altra unita' ne attraversa il confine;
  (b) il manifest dichiara anche il file del fratello — piu' rapido, ma mette
      il codice di un'unita' nella firma di un'altra.

  **Misurato sullo storico reale dei turni** (57 file giornalieri, 8.716 passi
  osservati): `find_persons_indices` e' stato scelto **0 volte**, mentre
  `find_images_indices` 106 e `get_images_indices` 4. L'alias compare soltanto
  negli elenchi di candidati e nel testo dei prompt (4 righe). Non e' mai stato
  invocato, quindi il difetto non ha mai fatto danno — e ritirarlo non toglie
  un comportamento a nessuno.

  **DECISO 28/8 (delega di Roberto): NON si ritira.** La misura «0 invocazioni»
  era vera e la conclusione sbagliata: il nome e' un riferimento di
  instradamento che il pianificatore legge in quattro `.j2` e in un manifest
  FIRMATO (`read_persons`). Vale per il fatto di essere NOMINATO, non di essere
  invocato. Motivazione completa nel §13 del piano ottimizzato. Il difetto vero
  — l'attraversamento del confine di un'altra unita' firmata — passa dal
  cancello: e' il SECONDO cliente, accanto a `undo_last_turn`. La cella resta
  rossa e dichiarata: la base 2A vieta `xfail`, quindi qui un caso e' verde
  oppure e' un rilievo.

  (Storico) Se un giorno si scegliesse (b), servirebbe una rifirma (§7.10)
  — che oggi passa da un'intenzione di Executor Birth, perche' `sign.py publish`
  risponde «unavailable in STORE_ONLY». Non toccato.

  Nota di scala: `find_images_indices.py` sono 1778 righe di implementazione
  vera dentro l'executor, `find_persons_indices.py` 70 righe di sola
  inoltrazione. Portare la parte condivisa nel runtime (la strada §7.3, quella
  usata per `pdf_extract` fra i due lettori URL) e' un lavoro grosso, non una
  correzione.
- **cinque celle POSIX della base 2A** (`g2` e `g8`): pretendono un secondo
  utente o i privilegi di root. Verdi nel ciclo pubblico, rosse in locale.
  Attese.

## Perche' i gruppi 4-6 non hanno un piano

Deciso con Roberto il 27/8/2026. La forma dei gruppi 4-6 dipende da cosa il
gruppo 3 consegna davvero; un piano scritto in anticipo va riscritto, e
riscriverlo costa piu' che non averlo. Cio' che invece non invecchia — le
regole di lavoro fra gruppi — e' stato sollevato in un documento separato che
quei gruppi ereditano.

Quindi: quando il gruppo 3 chiude, il gruppo 4 comincia scrivendo il **proprio**
piano ottimizzato sullo stesso modello, non prendendone uno gia' pronto.

## Avvertenze per chi consegna

- L'agente parte da freddo: il primo giro serve a leggere, non a produrre.
- Il prompt fa continuare il piano. Se invece si vuole **rimettere in
  discussione** una scelta, va detto esplicitamente cosa riaprire, altrimenti
  l'agente la trattera' come acquisita.
