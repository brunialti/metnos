# Lente misura

Percorsi citati: `internal/roadmap/RM-0001-conoscenza-utente-locale.md` (di
seguito «RM», righe della revisione del 26/7/2026) e
`/home/roberto/.metnos/rm0001_review/comune.md`.

## Verdetto in tre righe

§18 è in gran parte falsificabile (guardie fail-closed, equivalenza §18.4,
avversariali §18.3); §19 lo è solo a metà: l'apparato causale serio (replay
accoppiati, holdout, effetto minimo preregistrato) esiste SOLO per gli hint
d'esperienza, mentre il beneficio della memoria UTENTE — il cuore del progetto —
si promuove su corpus offline costruito dagli stessi autori, con almeno quattro
metriche di §19.1 gonfiabili senza dare valore all'utente. [OPINIONE fondata
sui rilievi 1-4 sotto, ciascuno con riscontro]

## Rilievi

### 1. La prova causale esiste solo per gli hint; F5 si promuove senza braccio di confronto
[PROVATO] La «Prova causale degli hint» (RM r. 2254-2262: replay accoppiati
hint_off/hint_on, holdout live, limite inferiore dell'intervallo sopra
l'effetto minimo) è dichiarata soltanto per l'esperienza executor. L'uscita di
F5 per la memoria utente è «beneficio netto sul corpus personale» (r. 1825-1826):
nessun replay on/off, nessuna statistica, nessuna definizione di «netto». F0
preregistra «metodo accoppiato» (r. 1756-1757) senza dire a quali fasi si
applica.
Conseguenza: si può promuovere F5 con Recall@3 alto su un corpus che gli stessi
autori hanno etichettato, senza aver mai mostrato UN turno reale migliorato.
Il numero sale perché il corpus definisce insieme che cosa ricordare e che cosa
conta come successo: un circolo, non una misura. Questo è dentro il recepimento
di §27.1 («il benchmark offline non prova il beneficio operativo») ma il
recepimento è stato cablato solo su F7/F8.

### 2. «Riferimenti riusati con esito verificato» è cieco al referente sbagliato
[PROVATO] Metrica a r. 2143. F3 è read-only (r. 1799-1801) e la risoluzione
silenziosa è ammessa solo per letture (r. 1544-1547). L'unica verifica
disponibile in lettura è la postcondizione dell'executor, che riesce anche sul
referente SBAGLIATO: memoria risolve «Atlas» su /opt/atlas-vecchio, find_files
riesce, la metrica conta +1 «riuso con esito verificato», l'utente riceve in
silenzio i risultati del progetto sbagliato.
Conseguenza: il numero sale proprio nel modo di guasto principale di F3, che
per costruzione è invisibile (niente conferma sulle letture). Manca un oracolo
del referente atteso indipendente dalla postcondizione.

### 3. «Riduzione delle correzioni dell'utente» è accoppiata al rilevamento del danno
[PROVATO il testo; IPOTESI il meccanismo] Metrica a r. 2141; il «tasso di
applicazione fuori contesto» (r. 2150) in esercizio si osserva quasi solo
attraverso le correzioni dell'utente. Meccanismo di gonfiaggio: se gli errori
diventano meno visibili (rilievo 2) o l'utente si adatta/si arrende, le
correzioni calano E il danno rilevato cala — beneficio e danno «migliorano»
insieme mentre il valore scende. Su N=1 utente è una serie temporale non
controllata: cala anche se cambia solo il mix di lavoro della settimana.
Conseguenza: inutilizzabile come metrica di promozione; al più telemetria.

### 4. La partizione «profilo irrilevante» di §18.4 è prodotta dal sistema sotto test
[PROVATO] §18.4 esige identità piano/argomenti «per richieste nelle quali il
profilo è irrilevante» (r. 2047-2048), ma l'idoneità di un turno la decide il
trigger tipizzato del sistema stesso (§9.5, r. 800-802). Un turno classificato
non idoneo non consulta la memoria: l'uguaglianza vale per costruzione. Un
turno erroneamente ritenuto idoneo esce dall'insieme a uguaglianza stretta per
autogiudizio del sistema.
Conseguenza: allargare il trigger riduce l'insieme sottoposto al test più
severo. La partizione idoneo/non-idoneo va congelata NEL corpus (etichetta per
turno), non derivata a runtime dal sistema misurato.

### 5. L'esposizione N dei gate a zero non è definita per la scrittura da fonte esterna
[PROVATO] Il rapporto con zero eventi dichiara il limite 3/N (r. 2277-2279),
ma per «scrittura da contenuto esterno» N non è definito: il filtro §10.1
scarta prima di ogni estrazione e «in caso di dubbio non scrive» (r. 949) senza
obbligo di registrare l'evento scartato; `memory_applications` (r. 1123-1125)
copre le consultazioni, non i drop pre-coda.
Conseguenza: con N = turni totali il 3/N è lusinghiero di ordini di grandezza
rispetto a N = tentativi reali di scrittura esterna. La scelta del denominatore
cambia il claim di sicurezza e oggi è libera.

### 6. «Claim derivati confermati dall'utente» si gonfia coi claim-pappagallo
[PROVATO la metrica (r. 2144-2145); IPOTESI il meccanismo] Un derivatore tarato
su riformulazioni quasi letterali («l'utente ha un progetto chiamato Atlas» da
«il progetto Atlas») ottiene conferme vicine al 100% con valore nullo: il claim
non risolve mai nulla che il testo del turno non risolvesse già. La metrica
misura fedeltà di estrazione, non utilità; manca il legame claim→uso a valle
(quante volte un claim confermato ha poi risolto un'ambiguità). Nota anche il
costo: per misurare bisogna interrogare Roberto, in tensione con «automatica»
(comune.md, scopo dichiarato).

### 7. F8: la disgiunzione «oppure riduzione passi» è una scappatoia
[PROVATO] Uscita F8: delta di successo sopra l'effetto minimo «oppure riduzione
passi senza regressioni» (r. 1894-1896). Chi scrive gli strategy_code controlla
direttamente il conteggio passi: una strategia che salta una riosservazione
riduce i passi per costruzione, senza beneficio visibile all'utente. Se il
delta di successo fallisce, si promuove sul surrogato. §27.4 (r. 2796) non ha
chiuso questo ramo.
Conseguenza: rendere la riduzione passi criterio secondario, mai sufficiente.

### 8. Nel simulatore F8 gotcha e rimedio sono scritti dalla stessa mano
[IPOTESI ancorata a r. 1870-1873 e 2255-2257] Se lo scenario simulato pianta
l'ostacolo (pannello da chiudere) e la allowlist contiene esattamente il suo
rimedio, il replay accoppiato vince per costruzione: prova il collaudo
dell'impianto, non che i gotcha reali siano apprendibili. Il gate live c'è
(§27.4), ma il rapporto di F8 dovrebbe dichiarare quali scenari derivano dagli
strategy_code stessi ed escluderli dall'evidenza di valore.

### 9. Metriche vere-per-costruzione elencate come metriche di qualità
[PROVATO] «numero di consultazioni per turno idoneo, massimo uno» (r. 2167) è
imposto da §9.5 (r. 800-802); «chiamate LLM aggiunte al turno non idoneo,
obiettivo zero» (r. 2166) è vero per definizione di idoneità (rilievo 4). Sono
guardie: non possono fallire se il codice è conforme.
Conseguenza: gonfiano il quadro dei «numeri verdi» in §19.3; spostarle fra i
property test di §18.7, dove appartengono.

### 10. «Metrica primaria» al singolare contro ~30 metriche senza mappa per fase
[PROVATO] F0 preregistra UNA metrica primaria e due harm metrics (r. 1756-1757);
§19 elenca circa trenta misure e le soglie si fissano «in F0 sulla baseline
reale» (r. 2173-2175), cioè oggi §19 non contiene alcun numero falsificabile.
Senza una mappa fase→metrica primaria dichiarata prima dei dati, la promozione
può scegliere a posteriori quale delle trenta chiamare «beneficio netto»
(sentieri che si biforcano).

### 11. «Doppia annotazione» senza requisito di indipendenza
[PROVATO il testo (r. 2214); IPOTESI il rischio] In un progetto a un utente i
due annotatori saranno con ogni probabilità due agenti LLM correlati o Roberto
due volte. La doppia annotazione senza indipendenza dichiarata misura la
coerenza dell'annotatore, non la verità dell'etichetta. Prescrivere: tasso di
disaccordo riportato e dichiarazione della natura dei due annotatori.

## Che cosa toglierei / che cosa aggiungerei

**Toglierei:**
- da §19.3 le due metriche vere-per-costruzione (rilievo 9) → §18.7;
- da §19.1 «riduzione delle correzioni dell'utente» come criterio di
  promozione (resta telemetria) (rilievo 3);
- da F8 il ramo «oppure riduzione passi» come condizione sufficiente
  (rilievo 7).

**Aggiungerei — la prova minima «la memoria serve» vs «la memoria è presente»**
[OPINIONE di progetto, coerente con le baseline già in RM r. 2219-2223]:
- **Turni:** ~30 coppie (turno-sorgente, turno-beneficio) separate da almeno
  una sessione, IT+EN, su tre tipi (preferenza persistente, riferimento,
  decisione); più ~30 turni-esca a profilo irrilevante etichettati NEL corpus
  (rilievo 4); più ~10 turni-trappola in cui il referente è cambiato fra
  sorgente e beneficio (misurano il danno da memoria stantia).
- **Tre bracci sullo stesso replay a seed fisso:** (a) memoria spenta;
  (b) memoria accesa; (c) baseline lineare = dizionario ultimo-valore-per-slot
  riempito deterministicamente dal turno-sorgente, senza compiler né retrieval
  (è la «baseline lineare e onesta» richiesta; RM la elenca a r. 2221 ma non
  la lega ad alcuna regola di promozione).
- **Misura primaria per coppia:** interazioni utente necessarie all'esito
  corretto, dove «corretto» è giudicato contro il referente/valore atteso
  scritto nel corpus, MAI contro la postcondizione dell'executor (rilievo 2).
  Delta accoppiato (b)−(a) e (b)−(c) con intervallo e effetto minimo
  preregistrati, come già fatto per gli hint.
- **Regola di promozione:** (b) deve battere (c), non solo (a). Battere
  «nessuna memoria» è facile; il Memory Compiler si giustifica solo oltre il
  dizionario. Sulle esche: identità §18.4 stretta. Sulle trappole: risoluzione
  silenziosa errata pesata più del beneficio (coerente con la matrice di costo
  di §19.5).
- Inoltre: definizione del denominatore N per ogni gate a zero (rilievo 5) e
  log obbligatorio, con reason code, di ogni scarto del filtro §10.1.

**Riproducibilità dichiarata:** onesta e già qualificata (snapshot congelato,
niente identità byte, corpus con digest e seed — r. 1448-1454, 2680-2683);
nessun overclaim residuo trovato dopo le correzioni C2/M2-M3 di §7.1. [PROVATO
per assenza nei punti citati] La lacuna non è la ripetibilità del percorso ma
l'assenza del confronto che renda il percorso significativo (rilievo 1).

**Coerenza con §18.3:** i 24 casi sono coerenti con i gate di §19.5 ma quasi
tutti falliscono solo in presenza di un bug del filtro (fail-closed per
costruzione): sono regressioni, non misure. Il solo caso «di misura» (12, testo
che massimizza la similarità BGE) non dichiara chi costruisce l'attacco né il
criterio di successo (ingresso in top-k? applicazione?). [PROVATO r. 2020-2043]

## Ciò che ho cercato e NON ho trovato

- Un replay accoppiato on/off per la memoria UTENTE (F3/F5), analogo a quello
  degli hint: assente (rilievo 1).
- Un oracolo di correttezza del referente indipendente dalla postcondizione
  dell'executor nei casi read-only di F3: assente (rilievo 2).
- La definizione del denominatore N per i gate a zero lato scrittura, e
  l'obbligo di registrare gli scarti del filtro §10.1: assenti (rilievo 5).
- Una mappa fase→metrica primaria preregistrata: assente (rilievo 10).
- Il congelamento nel corpus della partizione idoneo/non-idoneo di §18.4:
  assente (rilievo 4).
- Un requisito di indipendenza per i due annotatori di §19.5: assente
  (rilievo 11).
- Una regola che leghi la baseline «ultimo valore esplicito della stessa slot»
  a una condizione di promozione (batterla, non solo elencarla): assente.
- Un legame claim→uso a valle per la metrica dei claim confermati: assente
  (rilievo 6).
