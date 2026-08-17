# Revisione avversariale dell'analisi Claude sulla soluzione d'intento

Data: **11 agosto 2026**.

Fonti: `misure_11_8/confronto_intento.json` (SHA-256
`bd29821e8da22b8f76351786fd5a092700611cb22936207a3509c682a825481f`) e
`referto_confronto_intento.md`. I conteggi sotto sono stati ricalcolati dal
JSON; per il catalogo sono dichiarati separatamente gli universi possibili,
perché il JSON non incorpora il catalogo.

## 1. Affermazioni numeriche verificate una per una

| affermazione | esito | ricalcolo e contestazione |
|---|---|---|
| Accordo **76%** sulle richieste a una operazione | **VERA solo per la classe “esattamente una rotta emessa dall'attuale”** | Sono **56/74 = 75,68%**, arrotondato al 76%. Non è una misura indipendente delle richieste semplici: è una classe costruita dall'uscita dell'attuale. Se “semplice” significa al massimo una rotta, sono invece **57/77 = 74,03%**. |
| Accordo **12%** sulle richieste a due operazioni | **CONTEGGIO VERO, PERCENTUALE AMBIGUA** | Sono **3/24 = 12,50%**. Il 12% è riproducibile con arrotondamento ai pari; con l'arrotondamento scolastico diventa 13%. Andava data la frazione esatta. |
| Accordo **26%** sulle richieste a tre o più operazioni | **VERA entro lo stesso indicatore viziato** | Sono **5/19 = 26,32%**, quindi 26% arrotondato. La classe comprende 11 casi a tre rotte, 4 a quattro, 2 a cinque e 2 a sei. |
| **77/120** richieste a una sola operazione | **FALSA** | Le richieste con **esattamente una rotta attuale sono 74/120**. Il numero 77 è “al massimo una”: 74 con una rotta più **3 con zero rotte**. I tre zeri sono tutti bypass `undo`; uno è la lunga richiesta composta dell'indice 18. Non possono essere ribattezzati “una operazione”. |
| Tutte e cinque le scorciatoie lessicali sono su richieste non composte | **FALSA** | Le attivazioni sono 5, ma solo **4/5** sono non composte: indici 25, 51, 66 e 86. L'indice **18** è inequivocabilmente composto e la sottostringa `annulla` dentro “appuntamenti annullati” azzera l'intera analisi. È una confutazione diretta, non un caso dubbio. |
| L'attuale emette 35 coppie fuori catalogo contro 30 del nuovo, sui 55 disaccordi | **NON RIPRODUCIBILE COME SCRITTA; 35/30 sono casi, non coppie, e richiedono un catalogo ibrido non dichiarato** | Con le 69 coppie canoniche ricavate dai soli manifest firmati nel repository risultano **37 casi attuali contro 31 nuovi** e **57 contro 52 occorrenze** fuori catalogo. Aggiungendo tre capacità universali in processo senza manifest (`classify/entries`, `describe/entries`, `extract/entries`) si ottengono proprio **35 contro 30 casi**, ma le occorrenze sono **53 contro 45**. Includendo anche le directory firmate della skill GitHub installata, la frase “fra i manifest firmati sul disco” dà **30 contro 31 casi**. Il numero pubblicato dipende quindi da un universo non nominato e non conta ciò che dice di contare. |
| Il nuovo emette in media **1,98** rotte contro **2,11** | **VERA solo con denominatore e stato d'uscita taciuti** | Sui soli 55 disaccordi e sulle rotte nuove grezze: nuovo **109/55 = 1,9818**, attuale **116/55 = 2,1091**. Sul totale dei 120 sono invece **1,55 contro 1,6083**. Considerando soltanto le rotte nuove utilizzabili, il nuovo scende a **1,50** sul totale e **1,8727** sui disaccordi. La direzione resta la stessa, ma il numero citato non è una media generale. |

La tabella centrale dell'analisi contiene inoltre una contraddizione aritmetica:
propone di misurare sulle “77” richieste e di giudicare “18” disaccordi. Le 74
con esattamente una rotta hanno davvero 18 disaccordi; le 77 con al massimo una
rotta ne hanno **20**. L'autore cambia popolazione fra una frase e l'altra.

## 2. Errori trovati

1. **Confusione fra zero, una e al massimo una operazione.** Il 77 incorpora
   tre uscite vuote e trasforma un fallimento del percorso in una misura di
   semplicità. L'indice 18 mostra quanto sia grave: attuale vuoto per falso
   positivo lessicale, nuovo vuoto per errore di trasporto, e il JSON li conta
   perfino come accordo.

2. **La frase sulle cinque scorciatoie è smentita dai dati che l'analisi stessa
   cita.** L'indice 18 non è “sospetto da verificare”: query, scorciatoia e
   forma che ha colpito sono già nel JSON. È una richiesta composta con molte
   letture, trasformazioni e tre artefatti finali.

3. **“64% del traffico reale” non è autorizzato dal campione.**
   `prova_cieca.sample()` dichiara di sovrarappresentare deliberatamente due
   punti ciechi: inserisce prima tutti i casi `persons` e `mailbox`, poi estrae
   casualmente il resto. È un campione di frasi reali, non un campione
   rappresentativo della loro frequenza. Da 77/120 non si può stimare la quota
   di traffico di produzione.

4. **Il referto aveva già giudicato i disaccordi semplici.** Non è vero che i
   18 casi debbano ancora essere letti prima di valutare P1. Incrociando gli
   indici del referto con il JSON, fra i 74 casi a una rotta ci sono **56
   accordi, 4 “nuovo migliore” e 14 “nuovo peggiore”**. Sul perimetro 77 sono
   **57 accordi, 4 migliori e 16 peggiori**. L'analisi omette il risultato più
   contrario alla propria proposta: nel suo perimetro prescelto le regressioni
   superano i miglioramenti di 10 o 12 casi. I quattro miglioramenti sono 10,
   23, 33 e 47; i quattordici peggioramenti a una rotta sono 0, 36, 45, 48,
   53, 60, 62, 72, 82, 90, 93, 98, 110 e 115. Gli zeri aggiungono i due
   peggioramenti 25 e 66.

5. **Il conteggio di catalogo mescola categorie.** Il 35/30 compare soltanto
   contando casi con almeno un'anomalia rispetto ai manifest del repository
   più tre capacità virtuali. Non è né il numero di coppie emesse né il
   confronto con tutti i manifest firmati installati. Inoltre usa le rotte
   grezze del nuovo: sulle rotte utilizzabili i valori cambiano ancora.

6. **Il 12% nasconde 12,50%.** È un errore minore rispetto agli altri, ma in
   una tesi fondata su una discontinuità percentuale va riportata la frazione
   esatta oppure una regola di arrotondamento coerente.

## 3. Obiezioni al ragionamento, in ordine di gravità

### 1. La tesi delle due parti disgiunte è costruita con la risposta del sistema attuale

Il numero di operazioni non è un'etichetta indipendente della query: è
`length(current_routes)`. L'attuale è uno dei due sistemi giudicati e può
omettere operazioni, fonderle o azzerarle con una scorciatoia. Condizionare il
confronto sulla sua uscita introduce una classificazione endogena: proprio gli
errori dell'attuale decidono se un caso entra nel gruppo “semplice” o
“composto”.

L'indice 18 è la prova concreta del vizio. Una richiesta chiaramente composta
finisce nella classe zero perché il dizionario sbaglia. Non è quindi vero che
le scorciatoie “vivono” soltanto nel semplice; una scorciatoia può alterare la
decomposizione e perfino cancellarla. La separazione osservata non dimostra due
cause disgiunte: in parte è prodotta dal meccanismo che si vorrebbe isolare.

L'invalidazione è sostanziale, non una semplice cautela metodologica. Senza un
oracolo indipendente per clausole e operazioni non si può concludere né che la
complessità causi il crollo di accordo né che il lavoro sui dizionari sia
ortogonale al composto. Le percentuali restano descrizioni dell'uscita
dell'attuale, non proprietà delle richieste.

### 2. P1 presuppone un selettore che in produzione non esiste

“Usare il nuovo solo sulle richieste semplici” richiede sapere, prima di
scegliere il percorso, che la richiesta è semplice. Le possibilità sono tutte
problematiche:

- usare l'attuale come selettore mantiene in servizio proprio estrattore e
  dizionari che si vorrebbero eliminare, e conserva i suoi errori di
  sottodecomposizione;
- usare il nuovo come selettore affida la decisione al sistema che si sta
  ancora validando;
- introdurre un terzo classificatore sposta lo stesso problema a monte e
  richiede una nuova misura, compresi falsi “semplice” sulle richieste con
  effetti e approvazioni.

P1 non è dunque una soluzione di sostituzione. È un perimetro valutativo che
presuppone un oracolo indisponibile. Finché non esiste e non è certificato un
selettore, il prodotto non può applicare quel 76% a un sottoinsieme noto.

### 3. L'accordo viene usato al posto della qualità, benché il referto dia già il contrario

Accordo significa soltanto uguaglianza delle liste di rotte. Può essere accordo
su un errore, su un'omissione o su due fallimenti: l'indice 18 è contato come
accordo vuoto-vuoto. I 56 accordi della classe a una rotta non sono stati
giudicati semanticamente uno per uno, quindi non provano parità.

Sui casi non concordi, invece, il giudizio esiste già e demolisce la lettura
ottimistica: **4 miglioramenti contro 14 regressioni** nella classe esattamente
una rotta; **4 contro 16** includendo gli zeri. Due regressioni sono gli undo
semplici trasformati in `change/files` e `delete/entries`, una delle quali è
pericolosa. Il valore medio “76%” nasconde sia la direzione del residuo sia la
gravità diseguale degli errori.

### 4. Cinque attivazioni non dimostrano la cancellabilità dei dizionari

Il referto censisce 1.030 caselle/921 forme uniche nel perimetro principale e
1.183/1.044 nel perimetro allargato. Il confronto osserva soltanto cinque
scorciatoie e zero casi `system.status_query`. L'assenza di attivazione in un
campione non è prova di irrilevanza, tanto meno prova che un'intera superficie
lessicale sia cancellabile.

L'analisi oscilla fra tre oggetti diversi: cinque bypass osservati, rami di
instradamento e dizionari condivisi. Le due richieste hardware mostrano solo
che il bypass di scelta rotta è ridondante in due casi. Non autorizzano a
cancellare le forme: `health.section_focus` seleziona anche la presentazione e
`machine.reference` alimenta `_ensure_health_arg`. Per il referto, le voci
globali cancellabili oggi restano **zero**.

### 5. “Fuori catalogo” non misura comprensione semantica e il catalogo non è definito

Una coppia può essere ammessa e semanticamente sbagliata (`get/persons` per
“dove mi trovo”); una coppia astratta può non corrispondere a un manifest
distribuito e tuttavia essere una capacità virtuale valida. Il conteggio di
legalità non falsifica l'ipotesi che il nuovo non usi bene il catalogo: misura
soltanto l'appartenenza di stringhe a un insieme scelto dopo il fatto.

Peggio, il 35/30 non nasce dai soli manifest firmati dichiarati. Le capacità
firmate della skill GitHub installata cambiano il confronto, e il catalogo di
prodotto contiene anche capacità in processo per task, preferenze, store e
descrizione. Senza congelare nel risultato l'esatto insieme autorevole, il
numero non è riproducibile e non può sostenere una falsificazione.

### 6. Il confronto del numero medio di rotte non confuta la difficoltà di decomposizione

Emettere meno rotte non significa decomporre meglio. Può significare aver perso
un bersaglio, un'approvazione, una sorgente, un artefatto o un intero ramo; il
referto documenta tutti questi casi. La media più bassa è quindi compatibile
con la regressione che l'ipotesi voleva escludere.

Inoltre la rotta attuale omette per contratto le sette `implicit_actions`, e le
due architetture non rappresentano necessariamente lo stesso livello di
dettaglio. Contare elementi di due rappresentazioni diverse non misura da solo
la capacità di decomporre una richiesta.

### 7. La proposta non soddisfa neppure il proprio criterio operativo

L'obiettivo era sostituire il percorso senza perdere qualità e rimuovere la
dipendenza lessicale. Nel sottoinsieme scelto dall'analisi, il nuovo ha molte
più regressioni che miglioramenti; per riconoscere quel sottoinsieme servirebbe
ancora l'attuale o un nuovo analizzatore; tre bypass undo non sono cancellabili;
le forme hardware hanno altri consumatori; la latenza mediana resta 3.961 ms
contro 310 ms. Non rimane una sostituzione dimostrata: rimane soltanto la
possibilità di togliere in futuro un piccolo ramo di instradamento hardware.

## 4. Ciò che l'analisi non dice e avrebbe dovuto dire

- Il campione è deliberatamente sbilanciato e non permette di stimare quote di
  traffico.
- I 18 disaccordi a una rotta sono già giudicati: 4 miglioramenti e 14
  regressioni; non sono lavoro futuro ignoto.
- I 56 accordi a una rotta non sono un oracolo di correttezza e vanno
  controllati prima di parlare di parità.
- L'accordo vuoto-vuoto dell'indice 18 premia simultaneamente un falso positivo
  lessicale e un fallimento di trasporto.
- Serve un'etichetta indipendente del numero di operazioni, con clausole,
  dipendenze e bersagli attesi; senza di essa la partizione semplice/composto è
  circolare.
- Serve un selettore di produzione misurato prima di proporre il percorso
  ibrido. Non basta nominare il sottoinsieme a posteriori.
- Il criterio deve pesare separatamente regressioni di sicurezza, in
  particolare undo e approvazioni, invece di trattarle come normali divergenze.
- Il catalogo esatto usato per ogni conteggio deve essere congelato nel
  risultato; manifest distribuiti, skill installate e capacità virtuali non
  sono intercambiabili.
- Cinque attivazioni non coprono il dizionario: mancano prove per forme non
  osservate, lingue, `system.status_query`, altri consumatori e richieste
  composte.
- La conclusione operativa coerente con la misura è quella del referto:
  nessuna voce globale è cancellabile oggi; due soli casi hardware dimostrano
  ridondanza del ramo di instradamento, non del dato lessicale condiviso.

## Verdetto

- **La tesi delle due parti disgiunte non è dimostrata: nasce da una classificazione endogena, contiene un controesempio composto e ignora un giudizio sui casi semplici nettamente sfavorevole al nuovo.**
- **P1 sposta il problema dietro un selettore inesistente; oggi non giustifica né la sostituzione del percorso né la cancellazione di alcuna voce di dizionario.**
