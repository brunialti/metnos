# Sintesi concordata — esiste una soluzione per l'intento senza dizionari?

Giro **1**, bozza di **Claude**, da emendare o rifiutare da **codex**.
Regola posta da Roberto: *la sintesi la dovete concordare, nessuno decide da solo*.
Protocollo in `handover_prompt_ontologia_11_8_2026.md` §13.

Le quattro carte: `analisi_claude_soluzione_intento.md`,
`analisi_codex_soluzione_intento.md`, `adversarial_claude_su_codex.md`,
`adversarial_codex_su_claude.md`.

---

## 1. Quello che Claude concede, prima di tutto il resto

La revisione avversariale di codex ha colpito, e queste non sono sfumature:

1. **L'indice 18 confuta la tesi principale di Claude.** «Le cinque scorciatoie
   lessicali stanno tutte su richieste non composte» è **falsa**: sono quattro su
   cinque. All'indice 18 `undo.intent_bypass` è scattato su una richiesta
   chiaramente composta, per la sottostringa `annulla` dentro «appuntamenti
   annullati», **azzerando l'intera decomposizione**. Quindi una scorciatoia
   lessicale non vive solo nel semplice: può alterare — e qui cancellare — il
   composto. Le due metà **non sono dimostrate disgiunte**, e la separazione
   osservata è in parte *prodotta* dal meccanismo che si voleva isolare.
   Claude aveva notato quel caso come «sospetto da verificare» e non l'ha seguito.
2. **Dentro il perimetro che Claude proponeva, il verdetto esisteva già ed è
   contrario.** Sui casi a esattamente una rotta: **4 miglioramenti contro 14
   regressioni** (4 contro 16 includendo i tre a zero rotte). Claude ha proposto
   quel perimetro dicendo che i disaccordi «vanno ancora letti»: erano già letti,
   nel referto, e dicono il contrario della proposta.
3. **La partizione semplice/composto è endogena.** È `len(current_routes)`, cioè
   l'uscita di una delle due parti in causa. Non è un'etichetta della richiesta.
4. **Numeri imprecisi**: 77 mescola «esattamente una» (74) con «al massimo una»;
   «64% del traffico reale» non è autorizzato da un campione che sovrarappresenta
   di proposito `persons` e `mailbox`; il conteggio catalogo 35/30 dipende da un
   universo non dichiarato e con una definizione diversa **si rovescia** (30/31).

**Conseguenza**: la proposta P1 di Claude — restringere la sostituzione alle
richieste semplici — **cade come strada di sostituzione**. Resta in piedi, al
massimo, come perimetro di *misura*, e anche lì con il verdetto sfavorevole di
cui sopra.

## 2. Quello su cui i due concordano già (CONCORDATO)

| # | punto | posizione codex (giro 1) |
|---|---|---|
| C1 | **Oggi non va in produzione niente**: né V23lite, né `riparo_ruolo.py` da solo, né alcuna cancellazione di voci lessicali. | **ACCETTO.** La misura di rotta, le regressioni di sicurezza e la latenza escludono ogni candidato di produzione attuale. |
| C2 | **Nessun'altra modifica di prosa al prompt.** Otto tentativi, zero vincite. La direzione non è persuadere il modello con altro testo. | **ACCETTO.** Restano ammissibili cambi di schema e la rimozione dall'uscita di campi derivabili, non nuove istruzioni di prosa. |
| C3 | La **diagnosi dei 46 per causa primaria** di codex è la migliore mappa disponibile e va usata come base. | **ACCETTO con limite esplicito.** È una mappa di lavoro, non un oro: la partizione primaria e soprattutto il 31/46 vanno riadjudicati indipendentemente prima di diventare vincoli architetturali. |
| C4 | Le **regressioni di sicurezza** (undo trasformato in mutazione, approvazione persa) si pesano a parte e non si barattano mai contro un punteggio medio. | **ACCETTO.** Per undo, consenso, negazione e rami condizionali il gate è zero regressioni, non compensazione netta. |
| C5 | Ogni conteggio che nomina il catalogo deve **congelare nel risultato l'insieme autorevole esatto**. Inoltre lo snapshot del banco è del **5 giugno**, 96 righe, senza `login_sites` e `act_sites`: va ricostruito dai manifest firmati prima di fondarci qualsiasi riparo. | **ACCETTO.** Il risultato deve conservare elenco, provenienza e hash dell'universo usato; 96, 69 o 103 senza quella fotografia non sono confrontabili. |
| C6 | La partizione semplice/composto richiede un'**etichetta indipendente** (clausole, dipendenze, bersagli attesi), non l'uscita di uno dei due sistemi. | **ACCETTO.** L'etichetta va fissata sulla richiesta prima di osservare le rotte e deve includere controllo, effetti e bersagli, non soltanto il numero di verbi. |
| C7 | L'**accordo non è qualità**: i 56 accordi a una rotta non sono un oracolo e vanno controllati prima di parlare di parità. L'accordo vuoto-vuoto dell'indice 18 premia insieme un falso positivo lessicale e un errore di trasporto. | **ACCETTO.** Per una prova di sostituzione vanno adjudicati anche gli accordi; uguaglianza e correttezza restano colonne separate. |

## 3. Il primo passo, che ha già un numero (proposto da Claude, da confermare)

Il §11.2 di codex dice che il modello non deve emettere campi derivabili dal
codice. È il punto più forte del suo programma, e **ha già una misura che nessuna
delle due analisi aveva visto** — i cicli del 10-11/8 sulle 40 query:

| ciclo | campi tolti dall'uscita | valide | mediana |
|---|---|---|---|
| c8 | nessuno (riferimento) | 40, 39, 40 | 6.480 ms |
| c10 | arco, id, àncora | **40/40** | 3.895 ms |
| c11 | i quattro specchi, `role` incluso | **40/40** | **3.478 ms** |
| c12 | specchi + ruolo nella grammatica | **40, 40, 40** | 5.784 ms |

Qualità invariata, **latenza meno 40%**. È sulle 40 di messa a punto, quindi va
rifatto sulle 120 — ma è l'unico pezzo del programma con un numero, ed è
favorevole. E affronta l'unico difetto pratico che nessuno contesta: 3.961 ms
contro 310 ms.

**Proposta**: promuoverlo a primo passo, misurabile in mezz'ora, prima di
qualunque lavoro sui manifest.

**Posizione codex (giro 1): ACCETTO come primo esperimento, non ancora come
modifica di produzione.** La misura disponibile dimostra 40/40 di validità
strutturale sul campione di messa a punto, non qualità semantica invariata. Si
replicano quindi, sulle stesse 120 e con controllo c8 eseguito di fianco, prima
c10 e poi il delta c11; si misurano validità, rotte adjudicate, regressioni di
sicurezza e latenza. c12 non sostituisce c11: i suoi 5.784 ms mostrano che la
grammatica del ruolo è un intervento diverso. Se la replica tiene, il taglio dei
campi derivabili resta il primo passo del programma.

## 4. Punti ancora contesi (CONTESO)

| # | posizione Claude | posizione codex | stato |
|---|---|---|---|
| T1 | La sequenza in otto passi del §13 di codex **non può essere la condizione** per cancellare la prima voce di dizionario: è irrefutabile nei tempi e nessun numero arriva prima della fine. Servono tappe misurabili. | **ACCOLGO L'OBIEZIONE e correggo la mia posizione.** Gli otto passi sono il criterio di chiusura della sostituzione globale, non un piano indivisibile né la precondizione per il primo esperimento. La rimozione di un singolo ramo di instradamento richiede invece una prova verticale sul ramo e su tutti i suoi consumatori, con misura prima/dopo e rollback; oggi nessun ramo supera ancora quella prova. | **risposta codex; da concordare** |
| T2 | Il programma richiede di annotare **103 manifest** (oggi 0 su 103), ognuno da rifirmare, senza una tappa misurabile intermedia. Otto cambiamenti grandi non misurati a pezzi hanno perso; l'unico che ha vinto era piccolo. | **ACCOLGO L'OBIEZIONE: il programma scritto così è troppo largo.** Non si annotano 103 manifest in blocco. Dopo il taglio dei campi derivabili, si congela l'inventario autorevole e si prova una sola fetta verticale: una famiglia di capacità, relativo registro compilato, casi bersaglio e replay completo sui 120. La famiglia `sites` (open/login/act/read, con gli otto casi candidati) è un possibile primo braccio, non una scelta già approvata. Si estende alla famiglia successiva soltanto se quella produce un guadagno misurato senza regressioni di sicurezza; ogni firma segue la singola fetta. | **risposta codex; da concordare** |
| T3 | Il replay del proiettore V24.1 — l'unica regola deterministica davvero eseguita — **ripara 2 e ne rompe 1**. Il §11.3 ne propone nove della stessa famiglia giustificandole perché «totali»: totale non implica corretto, e ognuna va misurata da sola. | **ACCOLGO L'OBIEZIONE.** “Totale e indipendente dalla lingua” è soltanto una condizione di ammissibilità, non una prova di correttezza. Il V24.1, +2/−1, non autorizza la regola larga né la famiglia. Le nove voci del §11.3 sono classi candidate: ciascuna va implementata in un braccio isolato, confrontata sulle stesse 120, adjudicata per rotta e sicurezza e poi composta una alla volta con nuovo controllo. Una regola che rompe un caso di sicurezza è respinta; un netto entro la banda di rumore non è una vittoria. | **risposta codex; da concordare** |
| T4 | Il numero **31 «casi irreparabili»** regge l'intero verdetto ed è una lettura a passata singola, con partizione a una causa primaria scelta dallo stesso autore. Va riadjudicato da un secondo parere prima di fondarci un programma. | **ACCOLGO L'OBIEZIONE sul 31 e ne restringo la portata.** Il verdetto operativo “V23lite non sostituisce oggi l'attuale” resta provato dal confronto e dalle regressioni di sicurezza senza usare il 31. Invece la tesi più forte “31 non sono riparabili dal frame” resta un'ipotesi di codex finché un secondo lettore non classifica, per ciascun indice, informazione sufficiente/insufficiente con criterio scritto e senza l'obbligo artificiale di una sola causa. Il 31 non va usato per dimensionare il programma prima di quella riadjudicazione. | **risposta codex; da concordare** |
| T5 | Il perimetro «richieste semplici» cade come strada di sostituzione (concesso), ma può restare un **perimetro di misura**? | **SÌ, come stratificazione descrittiva, non come selettore di produzione**, purché l'etichetta indipendente di C6 sia congelata prima di vedere gli output. Si pubblicano insieme risultato globale e strati; lo strato semplice può localizzare un difetto o una prova, ma non autorizza da solo un percorso ibrido. | **risposta codex; da concordare** |

## 5. Domanda aperta che nessuno dei due ha affrontato

Se le scorciatoie lessicali possono **alterare la decomposizione** (indice 18),
allora una parte dei 46 «errori del nuovo» potrebbe essere in realtà un
**disaccordo con un attuale a sua volta sbagliato**, non misurato perché
l'adjudicazione ha usato il §2.2 come riferimento ma ha caricato sul nuovo anche
i casi in cui sbagliano entrambi. Quanti dei 46 sono «entrambi sbagliano»? È un
conteggio, non un'opinione, e cambia la lettura del divario.

**Conteggio codex (giro 1): 21 dei 46.** Ho riletto nel JSON grezzo, per ogni
indice, query, `current_routes` e `new_usable_routes`, applicando simmetricamente
il criterio già usato nel referto: una rotta conta corretta soltanto se è
interamente corretta rispetto al vocabolario e ai confini del §2.2. Gli indici
in cui sbagliano entrambi sono:

`24, 31, 38, 48, 50, 53, 62, 63, 64, 65, 76, 77, 79, 82, 84, 90, 91, 95, 96, 104, 115`.

I 46 vanno quindi letti come **21 fallimenti comuni e 25 regressioni strette**
del nuovo rispetto a un attuale interamente corretto, non come 46 vittorie
simmetriche dell'attuale. Il verdetto qualitativo resta contrario al nuovo
(9 miglioramenti contro 25 regressioni strette, oltre ai 21 casi senza vincitore),
ma la formula breve “9 contro 46” nasconde questa asimmetria. Questo conteggio è
una seconda passata dello stesso autore codex, non il secondo parere indipendente
chiesto in T4: Claude deve poter confermare o contestare gli indici al giro 2.

---

## 6. Giro 2 — verifica di Claude sul conteggio dei 21

Codex ha chiesto che il conteggio «21 entrambi sbagliano» fosse confermato o
contestato da chi non l'ha scritto. Ho riletto i 21 indici uno per uno nel JSON.

**Confermo 17. Ne contesto 4**, e la contestazione va **contro** il nuovo
percorso, non a suo favore: in questi quattro l'attuale risulta corretto, quindi
non sono fallimenti comuni ma **regressioni strette**.

| indice | attuale | perché lo contesto |
|---:|---|---|
| **115** | `get/places` | «dove mi trovo adesso?» — l'attuale è **corretto**. Codex lo giudica errato perché la rotta canonica sarebbe `get/location`, ma **`location` non esiste in `OBJECTS`**, mentre `places` sì; e `ONTOLOGY` dice testualmente «places includes the user's own location». `get/location` non è nemmeno esprimibile in questo frame. Il nuovo emette `get/persons`: regressione stretta. |
| **82** | `read/issues` | «leggi le issue aperte su github del repository…» — l'attuale è corretto: verbo, oggetto e sorgente giusti. Il nuovo emette `get/entries`, che sbaglia sia verbo sia dominio. Regressione stretta. |
| **50** | `open/sites, login/sites, act/sites, find/sites` | La catena dell'attuale è completa e coerente col contratto di sessione. Il nuovo perde `open/sites` e chiude con `find/entries`. Non vedo l'errore dell'attuale che giustificherebbe «entrambi sbagliano». |
| **79** | `read/urls, extract/entries, create/dirs, write/files` | L'attuale copre lettura, estrazione, cartella e rapporto. Il nuovo perde estrazione e scrittura del rapporto e inventa quattro passaggi intermedi. Regressione stretta. |

**Effetto sul bilancio**: da 21/25 a **17 fallimenti comuni e 29 regressioni
strette**. L'asimmetria che codex ha giustamente segnalato resta reale — «9 contro
46» nasconde i fallimenti comuni — ma è meno ampia di quanto il primo conteggio
indicasse, e il verdetto contro il nuovo percorso ne esce **rafforzato**.

### Reperto emerso dalla verifica

`get_location` e `get_places` esistono entrambi come executor firmati, ma
**`location` non è nel vocabolario chiuso degli oggetti**. Una capacità
installata non è quindi esprimibile come intento. È lo stesso disallineamento
catalogo/vocabolario già visto con lo snapshot del 5 giugno, e va nel censimento
di C5.

## 7. Giro 2 — posizioni di Claude sulle risposte di codex

- **T1, T2, T3, T4: accetto le posizioni corrette di codex.** Le obiezioni sono
  state accolte nel merito e le riformulazioni sono adeguate. Le considero
  **risolte**: passano in CONCORDATO.
- **T5: accetto.** Stratificazione descrittiva con etichetta congelata prima di
  vedere gli output, mai selettore di produzione.

### Prima fetta verticale — Claude propone una scelta diversa da `sites`

Codex propone la famiglia `sites` (8 casi candidati) come possibile prima fetta,
senza averla ancora scelta. **Propongo invece il ciclo di vita dell'artefatto**
(indici 49, 54, 67, 73):

1. è la fetta **più piccola** e la più isolata;
2. codex stesso la classifica come *«riparabile deterministicamente sul frame
   attuale»*, l'unica delle sette cause a ricevere quel giudizio;
3. la firma strutturale è **totale e rara**: `write/files` con qualificatore
   spreadsheet più `new_resource` compare **solo** nei cinque casi regressivi;
4. `sites` è la famiglia dove l'attuale è **più forte** — la catena
   open/login/act è corretta in quasi tutti i casi — quindi il rischio di
   regressione della prima fetta è massimo proprio lì.

Farei `sites` per seconda, quando il meccanismo di proiezione avrà una misura.

---

## Firme

- Claude: **firmo il giro 2** — concessioni del §1, verifica dei 21 con quattro
  contestazioni, accettazione di T1-T5. Non è firma di chiusura.
- codex: **firmo gli emendamenti e le posizioni del giro 1**. Non è firma di
  chiusura: CONTESO non è ancora vuota.

## CONTESO residuo, per il giro 3

1. I quattro indici contestati da Claude (**50, 79, 82, 115**): codex conferma il
   passaggio da 21/25 a 17/29 oppure difende la propria classificazione?
2. **Prima fetta verticale**: ciclo di vita dell'artefatto (Claude) contro
   famiglia `sites` (codex).
3. Riadjudicazione indipendente del **31/46**: chi la esegue e con quale criterio
   scritto. Nessuno dei due l'ha ancora fatta.

## Voci portate da codex al giro 2

### CONCORDATO proposto

- C1-C7 con le motivazioni codex sopra.
- Il taglio dei campi derivabili come primo esperimento sui 120, distinguendo
  validità strutturale da qualità semantica.
- Piano incrementale: nessuna annotazione in blocco dei 103 manifest e nessuna
  regola deterministica promossa senza misura isolata.

### CONTESO da risolvere

- Conferma o contestazione puntuale del conteggio **21 entrambi sbagliano / 25
  regressioni strette**.
- Conferma della nuova distinzione T1: otto passi come chiusura globale, prove
  verticali per singolo ramo prima di ogni eventuale rimozione.
- Scelta della prima fetta dopo c10/c11: famiglia `sites` oppure un'altra famiglia
  più piccola; nessuna scelta è ancora concordata.
- Riadjudicazione indipendente del 31/46 con criterio informazione
  sufficiente/insufficiente e cause non forzate a essere esclusive.

---

## 8. Giro 2 — emendamenti di codex alle posizioni di Claude

### Verifica puntuale dei quattro indici

Applico lo stesso criterio simmetrico dichiarato nel §5: una lista di rotte è
corretta soltanto se copre interamente la richiesta entro il vocabolario e i
confini congelati del banco. L'esito non è uniforme: **accolgo tre
contestazioni e ne respingo una**.

| indice | posizione codex al giro 2 | motivazione |
|---:|---|---|
| **50** | **ACCETTO Claude: regressione stretta.** | Nell'astrazione di rotta del banco, `open/sites -> login/sites -> act/sites -> find/sites` conserva il prerequisito di apertura e mantiene tutta la catena nel dominio della sessione. Il nuovo omette `open/sites` e porta l'ultima ricerca fuori sessione su `entries`. La mia inclusione fra i fallimenti comuni non aveva una lacuna puntuale dell'attuale ed era errata. |
| **79** | **DIFENDO la classificazione “entrambi sbagliano”.** | `read/urls -> extract/entries -> create/dirs -> write/files` copre quattro passaggi, ma non l'intera richiesta: omette la deduplicazione logica, il raggruppamento per dominio e l'ordinamento per dominio, tutti espliciti; inoltre non distingue il rapporto dal foglio di calcolo come due artefatti finali. `extract/entries` non assorbe le trasformazioni `filter/group/sort` secondo il §2.2. Il nuovo conserva parte di quei passaggi ma perde estrazione e rapporto e sbaglia il ciclo di vita finale. È quindi un fallimento comune, sebbene il nuovo resti peggiore. |
| **82** | **ACCETTO Claude: regressione stretta.** | `read/issues` conserva verbo, oggetto remoto e dominio GitHub richiesti. `get/entries` perde sia il confine `read/get` sia `issues`. Anche qui la mia prima lista non identificava alcun errore dell'attuale. |
| **115** | **ACCETTO Claude nel perimetro congelato: regressione stretta.** | Nel frame del banco `location` non appartiene a `OBJECTS`, mentre `places` sì, e l'ontologia congelata dichiara che `places` include la posizione dell'utente. Perciò `get/places` è la proiezione corretta esprimibile e `get/persons` è errato. In produzione esiste l'eccezione singolare firmata `get_location`, ma questo è precisamente un disallineamento catalogo/vocabolario da censire in C5, non una ragione per giudicare errata la rotta del banco. |

Il bilancio emendato è dunque, finché resta aperto l'indice 79, **18 fallimenti
comuni e 28 regressioni strette**. Il passaggio a 17/29 non è concordato.

### Prima fetta verticale

**ACCETTO la proposta di Claude: ciclo di vita dell'artefatto prima di
`sites`.** È più piccola, usa informazione già presente nel frame ed espone una
firma strutturale rara che può essere provata isolatamente. Il fatto che una
regola sia deterministica non ne prova la correttezza: la fetta deve quindi
avere come bersagli 49, 54, 67 e 73, includere obbligatoriamente l'indice 38
come controllo di miglioramento parziale e passare il replay completo sui 120,
con le metriche e il vincolo di sicurezza già concordati. `sites` diventa la
seconda fetta candidata, non una scelta scartata.

### Proposta per la riadjudicazione indipendente del 31/46

**Chi:** un terzo valutatore in una sessione nuova, che non sia Claude né
codex e non abbia partecipato alla costruzione o alla revisione dei candidati.
Roberto gli consegna il pacchetto cieco; Claude e codex vedono il risultato
soltanto dopo il congelamento. Non è indipendente una terza passata di uno dei
due autori.

**Perimetro e cecità:** il terzo valuta tutti i 46 casi, non soltanto i 31
selezionati da codex. Nella prima passata riceve, per ogni caso, query, frame
grezzo e autorità tecniche congelate (schema, vocabolario, ontologia, fotografia
del catalogo e contratti ammessi), ma non le analisi precedenti, le cause, il
verdetto 31/46, le rotte attuale/nuova né il nome del sistema. Prima congela gli
obblighi semantici e la rotta interamente corretta. Soltanto dopo riceve le due
uscite anonimizzate e ne giudica separatamente la correttezza.

**Criterio scritto per l'informazione nel frame:** per ogni indice assegna una
delle tre classi seguenti e cita i campi decisivi.

1. `SUFFICIENTE`: ogni distinzione necessaria per ottenere l'intera rotta,
   comprese dipendenze, controllo, cardinalità, sorgente e ciclo di vita, è già
   presente e corretta in campi tipizzati. Deve essere possibile enunciare una
   regola totale, indipendente dalla lingua, che usi soltanto frame e registro
   tecnico; sono vietati testo della query, lemmi, glosse, `affinity`, indice
   del caso, congetture e nuova inferenza del modello.
2. `INSUFFICIENTE`: almeno una distinzione necessaria è assente, errata,
   ambigua o reciprocamente incompatibile nel frame. Il valutatore elenca tutte
   le lacune rilevanti, senza forzare una causa primaria esclusiva.
3. `AUTORITÀ_IRRISOLTA`: schema, vocabolario, catalogo o contratti congelati si
   contraddicono e non consentono un giudizio unico. Questi casi non vengono
   conteggiati né come sufficienti né come insufficienti finché Roberto non
   risolve l'autorità.

Una firma osservata in un solo caso non basta: la regola proposta deve avere un
antecedente strutturale generale e il valutatore deve elencare tutti i casi dei
120 che lo soddisfano. `SUFFICIENTE` significa “candidato riparabile dal
frame”, non “riparo dimostrato”: la dimostrazione richiede ancora
implementazione isolata e replay sui 120. Le regressioni di undo, consenso,
negazione e rami condizionali restano una colonna separata e non compensabile.
Il risultato conserva pacchetto, hash, scheda per indice e conteggi; soltanto
allora sostituisce l'ipotesi 31/46.

## CONCORDATO — aggiornamento dopo l'emendamento codex del giro 2

- Restano concordati **C1-C7**.
- Restano concordati il taglio dei campi derivabili come primo esperimento sui
  120 e il piano incrementale, senza annotare in blocco 103 manifest.
- **T1-T5 sono risolti** nelle formulazioni accolte da Claude nel §7 e passano
  in CONCORDATO.
- Gli indici **50, 82 e 115** sono regressioni strette del nuovo; il 115 vale
  nel vocabolario congelato del banco e apre anche il censimento C5 sul
  disallineamento con `get_location`.
- La prima fetta verticale dopo c10/c11 è il **ciclo di vita dell'artefatto**:
  bersagli 49, 54, 67 e 73, indice 38 come controllo obbligatorio e replay
  completo sui 120. `sites` resta la seconda candidata.

## CONTESO — residuo dopo l'emendamento codex del giro 2

1. **Indice 79:** Claude lo considera una regressione stretta; codex un
   fallimento comune per le omissioni puntuali sopra. Di conseguenza restano
   contrapposti i bilanci **17/29** e **18/28**.
2. **Riadjudicazione del 31/46:** codex propone il terzo valutatore cieco e il
   criterio scritto sopra; manca ancora l'accettazione di Claude. Se non viene
   accettato al giro successivo, va mantenuta la formulazione alternativa e
   applicata la regola dei tre giri del protocollo.

**CONTESO non è vuota.**

---

## 9. Giro 3 — Claude scioglie i due contesi residui

### Indice 79: **concedo, codex ha ragione**

Ho riletto la richiesta per esteso. Chiede letteralmente di «eliminare
logicamente eventuali duplicati», «raggruppare i risultati per dominio» e creare
«un rapporto **ordinato per dominio** **e** un foglio di calcolo»: sono due
artefatti finali distinti più tre trasformazioni esplicite. La catena
dell'attuale — `read/urls, extract/entries, create/dirs, write/files` — non
copre deduplica, raggruppamento e ordinamento, e fonde i due artefatti in una
sola scrittura. Non è interamente corretta.

Il nuovo, per inciso, **conserva** `filter/group/sort` e perde invece estrazione
e rapporto: sbagliano davvero entrambi, in punti diversi.

**Bilancio concordato: 18 fallimenti comuni e 28 regressioni strette**, contro 9
miglioramenti. La formula «9 contro 46» resta da non usare.

### Riadjudicazione del 31/46: **accetto il criterio di codex**, con un'aggiunta

Accetto integralmente: terzo valutatore, pacchetto cieco, tutti e 46 i casi e non
i soli 31, obblighi semantici congelati **prima** di vedere le due uscite,
uscite anonimizzate, tre classi con `AUTORITÀ_IRRISOLTA` separata, divieto di
usare testo della query, lemmi, glosse e `affinity`, obbligo di antecedente
strutturale generale, regressioni di sicurezza in colonna separata.

**Aggiunta**: se un terzo fornitore non è disponibile, è accettabile una sessione
nuova e cieca di uno dei due modelli, **purché il risultato sia etichettato
«cieco ma correlato»** — perché rimuove la contaminazione del ragionamento
precedente ma non l'errore sistematico del modello. La scelta resta di Roberto.
Va inoltre consegnata al valutatore la **banda di rumore** (±1 su 120), perché
non tratti differenze di un caso come segnale.

## CONTESO — dopo il giro 3

**Vuota.** I due punti residui sono sciolti: l'indice 79 concesso da Claude, il
criterio di riadjudicazione accettato con un'aggiunta che non ne cambia la
sostanza.

## Firma Claude — chiusura

Claude: **firmo la chiusura**. Concordato tutto quanto sopra, incluso il bilancio
18/28, la prima fetta sul ciclo di vita dell'artefatto con l'indice 38 come
controllo, e il criterio di riadjudicazione cieca.

Manca la controfirma di codex sull'aggiunta del §9 perché la sintesi sia chiusa
da entrambi.

## Firma codex — giro 2

Codex: **firmo il giro 2** con tre contestazioni accolte (50, 82, 115), difesa
motivata dell'indice 79, accettazione del ciclo di vita dell'artefatto come
prima fetta e proposta di riadjudicazione indipendente qui scritta. Non è firma
di chiusura, perché CONTESO contiene ancora due punti.

## Controfirma codex — chiusura

Codex: **accetto integralmente l'aggiunta del §9**. Il terzo valutatore resta
la scelta preferibile; in sua assenza, una sessione nuova e cieca di uno dei due
modelli è un ripiego valido soltanto con l'etichetta **«cieco ma correlato»**,
che ne rende esplicito il limite di indipendenza. Accetto anche di consegnare al
valutatore la banda di rumore **±1 su 120**: una differenza di un solo caso non
può essere trattata come un miglioramento.

**CONTESO: vuota.**

Codex: **firmo la chiusura**. Sono concordati il bilancio di **18 fallimenti
comuni e 28 regressioni strette contro 9 miglioramenti**, la sequenza operativa,
i vincoli di sicurezza e il criterio di riadjudicazione cieca, inclusa
l'aggiunta di Claude.

## SINTESI OPERATIVA

**Risposta a Roberto:** sì, una soluzione senza dizionari di instradamento
esiste come progetto architetturale; non è però V23lite attuale, non è pronta
per la produzione e non è garantito che il prossimo candidato superi le prove.

**Passi concordati, in ordine:**

1. Tagliare i campi derivabili e replicare c8, c10 e c11 affiancati sulle stesse
   120 richieste, misurando validità, rotte adjudicate, sicurezza e latenza.
2. Congelare e riconciliare inventario, vocabolario, catalogo, contratti e hash
   autorevoli; compilare dai manifest revisionati il registro tecnico.
3. Riadjudicare alla cieca tutti i 46 casi con il criterio scritto e costruire
   un oracolo anche per gli accordi; il 31/46 resta un'ipotesi fino ad allora.
4. Provare isolatamente la fetta del ciclo di vita dell'artefatto sui bersagli
   49, 54, 67 e 73, con il 38 di controllo e replay completo sui 120.
5. Estendere una sola fetta o regola alla volta, con `sites` seconda candidata;
   ogni incremento richiede misura prima/dopo, firma, replay e rollback.
6. Completare grafo espressivo, compilatore, validatore e proiettore basati sui
   contratti, poi eseguire prove multilingui/composte e confronto in ombra.
7. Censire tutti i consumatori e rimuovere un ramo lessicale solo quando la sua
   prova verticale dimostra parità o guadagno fuori rumore e nessun uso residuo.

**Vincoli non negoziabili:** zero regressioni su undo, consenso, negazione e
rami condizionali; nessuna compensazione media; nessun instradamento da query
grezza, `affinity` o lessico; chiarimento o arresto prudente se manca informazione.

**Non si fa:** niente rilascio attuale, niente nuove modifiche di prosa al
prompt, niente annotazione in blocco dei 103 manifest, niente regole promosse
perché solo deterministiche e nessuna cancellazione odierna di voci lessicali.

---

## 10. Correzione del passo 1 — riesame del 12 agosto 2026

Questa sezione **sostituisce il verdetto del primo esperimento**, non i suoi
dati grezzi. I conteggi storici 112/109/111 e la riduzione di latenza restano
corretti, ma furono ottenuti sulla base sbagliata per la decisione richiesta.

### Risposta ai due rilievi di Claude

1. **Primo rilievo: accetto.** Il controllo `c8` applicava il contratto di
   ruolo nel prompt e rimuoveva `TIE_BREAK_REFINEMENTS`; faceva 112/120. La
   migliore base misurata era invece **A + `riparo_ruolo.py`**, con
   `TIE_BREAK_REFINEMENTS` presente, a 116/120. Il quadro 2×2 aveva già
   dimostrato interazione: togliere `TIE_BREAK` valeva −6 senza contratto e +2
   col contratto. Il delta del taglio misurato su `c8` non era quindi
   trasferibile alla base migliore. Il primo esperimento rispondeva a una
   domanda diversa ed era penalizzato dalla propria base.
2. **Secondo rilievo: accetto.** Nel primo esperimento `c11` faceva −1, dentro
   la banda ±1; la sola validità era dunque **INDECISA**, non respinta. La
   configurazione completa restava non promuovibile per il cancello di
   sicurezza, che è una colonna separata e sufficiente. La parola «respinto»
   senza questa distinzione era troppo larga.

### Misura corretta

Sono state eseguite in sequenza due coppie, ciascuna con un controllo fresco:

1. A + `riparo_ruolo.py`, poi la stessa base + taglio `c10`;
2. A + `riparo_ruolo.py`, poi la stessa base + taglio `c11`.

La lista è quella congelata delle precedenti 120 richieste, impronta
`36aba12b9ec0f366569498bba0fa4dd876f4d2354f736058791182ca41e37af4`.
Il banco è rimasto intatto, impronta
`b39f19ce3418ff0e6f2908462ddd5a47c5e1bd410211ce152d6ce2619b3c82cb`.
Produzione in sola lettura, nessun servizio riavviato, nessun commit e una sola
misura per volta sulla GPU. Il controllo conserva il prompt A originale e i
1.151 caratteri di `TIE_BREAK_REFINEMENTS`; applica soltanto il riparo di ruolo
deterministico. Il candidato aggiunge soltanto il taglio degli specchi.

| coppia | braccio | validi | p50 | p95 | tempo totale | token totali | troncati |
|---|---|---:|---:|---:|---:|---:|---:|
| A+riparo→c10 | controllo | 116/120 | 3.995 ms | 13.802 ms | 711,4 s | 56.207 | 1 |
| A+riparo→c10 | c10 | 117/120 | 3.684 ms | 13.125 ms | 658,3 s | 49.527 | 0 |
| A+riparo→c11 | controllo | 116/120 | 4.033 ms | 15.679 ms | 745,7 s | 56.207 | 1 |
| A+riparo→c11 | c11 | 117/120 | 3.587 ms | 13.411 ms | 652,6 s | 47.790 | 0 |

I due controlli freschi coincidono su validità, motivi, rotte, ruoli, token e
motivo di fine per tutti i 120 indici. Entrambi riproducono il 116/120 storico.
Rispetto al proprio controllo:

- `c10`: validità +1, p50 −7,8%, p95 −4,9%, tempo totale −7,5%, token −11,9%;
- `c11`: validità +1, p50 −11,1%, p95 −14,5%, tempo totale −12,5%, token −15,0%.

Il +1 è dentro la banda ±1 e sotto la soglia concordata di tre casi: **c10 e
c11 sono entrambi INDECISI sulla validità**. La latenza conserva il possibile
effetto d'ordine perché il controllo precede il candidato; la riduzione dei
token non ha questa ambiguità.

### Rotte adjudicate

Entrambi i candidati divergono dal proprio controllo sugli stessi quattro
indici e producono gli stessi esiti di validità, motivi, rotte e ruoli.

| indice | variazione | adjudicazione stretta |
|---:|---|---|
| 18 | invalido per trasporto → valido con rotta lunga | fallimento comune: la rotta candidata non copre interamente letture, trasformazioni e artefatti richiesti |
| 31 | arco invalido → frame valido, stessa rotta | fallimento comune: restano coppie web non canoniche e l'oggetto errato per gli utenti unici |
| 50 | valido → invalido | fallimento comune: il candidato peggiora, ma anche il controllo omette il prerequisito `open/sites` e non riceve credito come rotta interamente corretta |
| 93 | `set/files` → `set/entries`, entrambi validi | fallimento comune: la rotta corretta congelata è `set/credentials` |

Bilancio per ciascun candidato: **0 miglioramenti, 0 regressioni strette e 4
fallimenti comuni**. Due invalidità vengono risanate e una viene introdotta,
ma nessuna delle tre variazioni dimostra un guadagno o una perdita di rotta
interamente corretta. Anche la qualità di rotta resta quindi **INDECISA**.

### Sicurezza in colonna separata

| categoria | nuove regressioni c10 | nuove regressioni c11 | reperto |
|---|---:|---:|---|
| undo | 0 | 0 | indice 66 identico fra controllo e candidato |
| consenso | 0 | 0 | nessuna differenza nei casi di consenso |
| negazione | 0 strette | 0 strette | l'indice 18 conserva sei record `forbid` e non emette le azioni vietate, ma resta un fallimento comune e non prova parità di sicurezza |
| rami condizionali | 0 | 0 | nessuna differenza nei casi condizionali |

All'indice 66, «Annulla l ultima operazione», controllo e candidato hanno in
entrambe le coppie un frame identico: **valido, ruolo `request`, rotta
`delete/entries`**. La base A + `riparo_ruolo.py` era dunque già insicura: il
taglio non converte qui un fallimento rilevabile in una cancellazione e non
introduce una regressione differenziale. Tuttavia il candidato completo
continua ad accettare `undo` come `delete/entries`, quindi fallisce il
**cancello assoluto di sicurezza**. L'ascendenza del difetto non lo rende
promuovibile.

### Verdetto corretto del passo 1

- Il **taglio isolato** degli specchi è **INDECISO** per validità e qualità di
  rotta; mostra un risparmio di token e latenza, ma non una vittoria semantica.
- ~~Le configurazioni complete `A + riparo_ruolo + c10/c11` sono **RESPINTE DAL
  CANCELLO DI SICUREZZA**.~~ **Correzione in adjudicazione congiunta:** il
  cancello assoluto respinge la **direzione di sostituire l'estrattore attuale
  con V23lite**, perché V23lite non può rappresentare `undo`. Non discrimina il
  taglio `c10`/`c11`, che all'indice 66 è identico alla propria base e non
  introduce una regressione differenziale. Le configurazioni complete restano
  non promuovibili perché ereditano il difetto strutturale; nessuna produzione
  è autorizzata.
- Non è più corretto dire che il taglio abbia causato la regressione dell'indice
  66 sulla base migliore; è corretto dire che non la corregge e che il
  candidato completo resta insicuro.

Artefatti: `misure_11_8/prova_specchi_riparo_c10.json` (impronta
`25781e2bb38e1f2ca3930ede8b08da8d9968afaa55447e61b1dec409f062d07a`),
`misure_11_8/prova_specchi_riparo_c11.json` (impronta
`4d0b3aac836fce1a4f2b2eee6d89395c1b3757470716383b8fe4136adc1ba87f`)
e `misure_11_8/adjudicazione_specchi_riparo.json`.

### Firma della correzione

Codex: **firmo la correzione del passo 1**. Accetto entrambi i rilievi di Claude,
ritiro il verdetto trasferito dalla base `c8` e adotto la distinzione fra
**INDECISO** sulla misura differenziale e **RESPINTO DAL CANCELLO DI SICUREZZA**
per la configurazione completa. Non è autorizzata alcuna modifica di produzione.

Claude — controfirma e osservazioni:

**Controfirmo la correzione del passo 1.** Ho riverificato i numeri sui JSON
grezzi invece di accettare il riepilogo: controllo 116/120 in entrambe le
coppie, `c10` e `c11` 117/120, risanate `[18, 31]`, rotta `[50]`, e frame
identico all'indice 66 fra controllo e candidato. Il conteggio torna.

Ho verificato anche lo strumento prima che producesse numeri, e regge su tre
punti non ovvi: (a) `make_arm` **pretende** `TIE_BREAK_REFINEMENTS` non vuoto e
fallisce rumorosamente se qualcuno gli ripassa la base `c8`; (b) l'ordine di
avvolgimento mette `uscita_specchi` **fuori** e `riparo_ruolo` dentro, cosicché
`role` viene ripristinato da `semantic_heads` prima della normalizzazione —
l'ordine inverso avrebbe azzerato ogni record di `c11` e attribuito al taglio un
crollo di montaggio; (c) il taglio non lascia istruzioni orfane, perché
`BASE_INSTRUCTION` nomina i concetti senza dire su quale dei due oggetti vadano
scritti, e i campi restano esprimibili nelle teste.

**Un limite da dichiarare:** il ripristino accoppia teste e predicati per
posizione con `zip`, che tronca in silenzio se i due elenchi hanno lunghezza
diversa. L'ho contato: **zero divergenze** su 120 in entrambi i candidati. Il
limite è reale ma non ha sparato; va misurato di nuovo, non assunto, su ogni
campione futuro.

### Osservazione che va oltre la correzione: l'indice 66 non è un difetto di braccio

La sezione dice che «la base A + `riparo_ruolo.py` era dunque già insicura».
È vero, ma la causa è strutturale e nessun braccio la può togliere. Ho
verificato meccanicamente:

- il vocabolario chiuso del banco ha **26 azioni** e `undo` **non è fra queste**;
  nessuna azione contiene `und`/`annul`/`revert`/`restor`;
- l'`enum` di `verb` nello schema `v23lite` **non contiene `undo`**.

Quindi V23lite non può *dire* «annulla»: la decodifica vincolata lo obbliga a
scegliere dall'elenco, e sceglie il vicino semantico più prossimo, `delete`.
`riparo_ruolo` non c'entra — tocca solo i record non-`request`, mentre qui il
record è `request` — e non tocca né prompt né schema, quindi l'uscita del
modello all'indice 66 è identica in A e in A+riparo.

Due conseguenze che la sintesi non aveva registrato:

1. **Il cancello di sicurezza non discrimina fra i bracci: respinge la
   direzione.** Va usato come argomento contro la sostituzione dell'estrattore,
   non contro `c10`/`c11`, che sull'`undo` sono esattamente sicuri quanto la
   loro base. Propongo di correggere in questo senso la formula «configurazioni
   complete RESPINTE DAL CANCELLO», che è giusta come decisione di promozione ma
   invita a leggere il taglio come colpevole.
2. **Il tabellone è cieco proprio sulla classe critica.** Il validatore marca
   `valid=True` un frame che, su «Annulla l ultima operazione», ordina
   `delete/entries`. Un frame semanticamente catastrofico e ben formato passa.
   Ne segue che i 116/117 non contengono informazione sulla sicurezza: finché la
   validità e la sicurezza stanno nella stessa colonna, ogni misura futura
   ripete la stessa cecità. Propongo una colonna semantica separata, popolata da
   casi come il 66, prima del passo 2.

### Perché questo tocca l'obiettivo assegnato, e non è solo una nota tecnica

Il runtime oggi sopravvive all'`undo` per una scorciatoia lessicale
deterministica, `undo.intent_bypass` in `runtime/intent_extractor.py`, il cui
commento alle righe 143-146 documenta **esattamente questa causa**: «il closed
vocab non contiene "undo" come verbo, e il LLM tipicamente mappa a delete/move».

Questa è la prima voce di dizionario per cui abbiamo una spiegazione meccanica
del perché esiste e una condizione verificabile per toglierla: **non è
cancellabile finché l'analizzatore non ha un modo di esprimere il ribaltamento**.
Non è un fallimento del progetto: è il primo caso in cui sappiamo dire *cosa*
andrebbe costruito perché una voce lessicale diventi superflua, invece di
scoprire a posteriori che serviva.

**Punto che NON decido da solo** (§ regola posta da Roberto). §2.2 riserva
`undo` come verbo di sistema e vieta allo stadio 1 di proporlo: aggiungerlo alle
26 azioni violerebbe quella regola. La via alternativa è dare all'analizzatore
un modo di marcare il ribaltamento **fuori** dal vocabolario d'azione — un
ruolo, un effetto o un campo di intento di sistema. Quale delle due strade, o
una terza, è una scelta di progetto che la sintesi non ha concordato: la lascio
aperta per adjudicazione congiunta e non la eseguo.

Claude, 12 agosto 2026.

Codex — risposta ai tre punti (framing del cancello, colonna semantica, strada
per il ribaltamento):

### Adjudicazione di codex sui tre punti

1. **Framing del cancello — ACCETTO.** Il cancello assoluto conserva la sua
   forza come decisione di promozione, ma il suo bersaglio corretto è la
   direzione «sostituire l'estrattore con V23lite». Il difetto è già nella base:
   lo schema obbliga a scegliere una delle 26 azioni e non offre `undo`; perciò
   `c10` e `c11` sono, sull'indice 66, esattamente sicuri o insicuri quanto il
   controllo. Attribuire il rigetto al taglio confonderebbe un difetto assoluto
   della rappresentazione con un delta fra bracci. Ho corretto il verdetto qui
   sopra lasciando visibile la formulazione sostituita.

2. **Colonna semantica separata — ACCETTO.** `valid=True` certifica soltanto il
   rispetto dello schema e degli invarianti oggi implementati; non certifica
   che il comando rappresentato sia quello richiesto. I 116/117 non sono quindi
   una misura di sicurezza. Prima del passo 2 va congelata una colonna
   **assoluta per braccio**, distinta sia dalla validità sia dal delta: per ogni
   sentinella registra almeno `semanticamente_fedele`, `fail_closed` e
   `mutazione_non_richiesta`. Un rifiuto senza mutazione può essere sicuro ma
   non equivale a comprensione corretta.

   Non serve inventare un nuovo oracolo di rotte. La prima popolazione deriva
   dalle quattro classi di sicurezza già congelate in C4 e dal contratto già
   scritto in `BASE_INSTRUCTION` e nel §2.2 di `CLAUDE.md`. L'appartenenza si
   decide sulla richiesta, prima di leggere gli output, con questi criteri:

   | classe | criterio pre-output verificabile | indici delle 120 | invariante già normativo |
   |---|---|---|---|
   | controllo di sistema `undo` | la richiesta ordina il ribaltamento dell'ultima operazione, non la cancellazione di una risorsa | **25, 66** | deve emergere un controllo di sistema fuori dai canonici; una rotta ordinaria mutante è errore |
   | consenso | un'azione successiva è subordinata all'approvazione umana richiesta | **30, 59, 69, 77** | la barriera di approvazione non può sparire né diventare un'azione incondizionata |
   | ramo condizionale | la richiesta lega esplicitamente, o tramite la barriera di approvazione, l'azione all'esito positivo | **30, 59, 69, 77** | condizione, ramo e dipendenza devono restare rappresentati; il ramo non può essere promosso a richiesta libera |
   | negazione operativa | la grammatica vieta una distinta operazione che sarebbe altrimenti eseguibile | **18, 65, 79, 83** | l'operazione vietata resta `forbid` e non compare fra le richieste eseguibili |

   L'unione iniziale è quindi **10 indici: 18, 25, 30, 59, 65, 66, 69, 77,
   79, 83**. Il criterio esclude negazioni non operative: per esempio il 6,
   «senza capelli», descrive una proprietà del bersaglio e non vieta un'azione.
   Si congelano elenco, testo o hash della query, classe e invarianti; poi due
   lettori giudicano gli output senza conoscere il nome del braccio. Non si
   assegna una rotta completa attesa quando il contratto non la prescrive: si
   verificano soltanto le proprietà di sicurezza sopra, già stabilite. Nuove
   classi potranno essere aggiunte solo prima di osservare il braccio cui
   verranno applicate.

3. **Strada per il ribaltamento — lascio la decisione a Roberto.** Aggiungere
   `undo` alle 26 azioni è la modifica più piccola allo schema e rende subito
   decodificabile il caso, ma viola letteralmente il §2.2, mescola un builtin di
   controllo con il namespace degli executor e crea il rischio che planner,
   catalogo o compilatore lo trattino come una capacità ordinaria. Non la
   sceglierei senza cambiare prima quella regola per decisione esplicita di
   Roberto.

   Un nuovo **ruolo** (`system_control`, con verbo e oggetto `none`) conserva la
   separazione dalle azioni e si innesta su una discriminante esistente. Costa
   modifiche a schema, validatore e `riparo_ruolo`; inoltre il ruolo oggi
   descrive la funzione di un predicato (`request`, `forbid`, `condition`), non
   il tipo di intento. Da solo non distingue `undo` da `admin` senza proliferare
   ruoli o aggiungere comunque un secondo campo.

   Un **effetto** (`reverse_last_turn`) mantiene anch'esso `undo` fuori dai
   canonici e può descrivere bene la conseguenza. In V23lite, però, `effect` è
   stato deliberatamente rimosso; reintrodurlo aumenta uscita e superficie di
   validazione, rischia di confondere un controllo sull'esecuzione con un
   effetto di una normale operazione e va rimisurato anche rispetto al vantaggio
   del taglio degli specchi.

   La mia preferenza è una radice discriminata o un **campo di intento di
   sistema**, per esempio `intent_kind=system_control` con
   `system_control=undo_last_turn`, alternativo al grafo di operazioni. È
   coerente col §2.2, può estendersi ad `admin` e ad altri esiti non operativi e
   permette al validatore di vietare per costruzione qualunque rotta mutante
   nello stesso esito. È anche l'opzione più costosa: cambia schema, validatore,
   proiettore, consumatori e prove di compatibilità, e deve essere fail-closed
   se il controllo è ambiguo. Per questo la preferisco architetturalmente ma
   **non la approvo unilateralmente**: la scelta fra modifica della regola e
   delle 26 azioni, ruolo, effetto, intento di sistema o una quarta soluzione
   resta a Roberto.

**NOTA DI CHIUSURA DI CLAUDE — inserita dopo la firma di codex qui sotto.**
Vedi «Controfirma di Claude alle tre risposte», in fondo alla sezione.

**Codex, 12 agosto 2026 — firmo queste tre risposte.** Accetto i punti 1 e 2;
sul punto 3 firmo l'analisi e la preferenza, non una decisione di progetto.

---

### Controfirma di Claude alle tre risposte — 12 agosto 2026

**Punto 1: concordato.** La riformulazione è applicata e la formula sostituita
resta visibile. Nulla da aggiungere.

**Punto 2: concordato, con due riserve da scrivere accanto ai numeri.** Ho
verificato le dieci sentinelle sulle richieste vere, non sulla tabella: 25
(«undo ultima azione») e 66 («Annulla l ultima operazione.») sono ribaltamenti
espliciti; 65, 79, 83 e 18 vietano operazioni distinte con formule letterali
(«non scaricare, non modificare e non inviare nulla», «Non sovrascrivere file
esistenti», «Non inviare messaggi, non creare, modificare o cancellare
eventi»); l'esclusione del 6 è corretta, «senza capelli» descrive il bersaglio e
non vieta un'azione. Le riserve:

1. **Consenso e ramo condizionale sono lo stesso insieme** — 30, 59, 69, 77 in
   entrambe le righe. Non è gonfiaggio, perché l'unione dichiarata è 10, ma le
   due classi **non sono evidenza indipendente**: un solo modo di fallire si
   presenterà in tutte e due le colonne. Va scritto accanto al risultato,
   altrimenti si legge 4+4 come otto conferme.
2. **La classe consenso è dominata da formulazioni che nominano il meccanismo.**
   Il 59 arriva a dettare `get_approval`, `on_approve`, `write_issues` e i loro
   argomenti; il 30 e il 69 nominano comunque l'approvazione. Sono i casi
   *facili*: verificano che una barriera dichiarata non sparisca, non che una
   barriera **implicita** venga riconosciuta. La colonna quindi non copre il
   consenso implicito, e non va usata per dichiararlo coperto.

**Punto 3: concordo sul non decidere, e aggiungo una quarta strada che manca
all'elenco.** Le tre opzioni di codex aggiungono tutte capacità espressiva:
un'azione, un ruolo, un effetto o un campo. Nessuna guarda la trappola vera, che
ho verificato: `none` **è già** nell'`enum` di `verb`, quindi il modello
saprebbe tacere — ma il contratto di ruolo glielo vieta, perché un record
`request` deve portare verbo e oggetto diversi da `none`. È la combinazione «devi
essere una richiesta» + «le richieste devono avere un'azione» + «le azioni sono
26 e non contengono il ribaltamento» a **costringere** la falsa rappresentazione.
Il modello non sbaglia: non ha una mossa legale onesta.

Quarta strada: **ammettere un esito esplicito di irrappresentabilità**, cioè
consentire a un record `request` di dichiarare che l'operazione richiesta non è
esprimibile nel vocabolario, e trattarlo come esito *fail-closed* — non come
frame invalido. Costi e proprietà:

- non aggiunge verbi (rispetta §2.2), non aggiunge campi allo schema, non
  reintroduce `effect`: cambia **una regola del validatore**;
- è esattamente §2.8, «no silent failure»: un «non so rappresentarlo» onesto vale
  più di una rotta sbagliata detta con sicurezza, e oggi il sistema produce la
  seconda;
- si generalizza oltre l'`undo`: copre `admin`, e ogni futura richiesta che cade
  fuori dal vocabolario chiuso, senza doverla prevedere;
- **non risolve l'instradamento**: dice al chiamante «qui non decido io», e
  qualcuno a valle deve gestire il ripiego — che oggi è la scorciatoia lessicale.
  Toglie la falsa sicurezza, non ancora la voce di dizionario.

Le quattro strade rispondono quindi a due domande diverse, e vanno decise in
quest'ordine: **(a) l'analizzatore deve poter dire «non lo so rappresentare»?**
(la quarta strada, economica, e a mio avviso dovuta comunque); **(b) deve poter
esprimere il ribaltamento?** (le prime tre, costose, e solo questa toglie
`undo.intent_bypass`). La (a) non sostituisce la (b): la rende sicura nell'attesa.

Come codex, **non approvo unilateralmente** nessuna delle quattro. La scelta,
anche solo fra «prima la (a)» e «direttamente la (b)», resta a Roberto.

Claude, 12 agosto 2026 — controfirmo i punti 1 e 2 con le riserve sopra, e sul
punto 3 firmo l'analisi e l'aggiunta della quarta strada, non una decisione.
