# Affidabilità del Tutor: diagnosi e percorso di correzione

Data: 16 settembre 2026. Stato: **analisi proposta, non specifica approvata**.
Destinazione: interna; non pubblicare né indicizzare nel catalogo Tutor.

## 1. Conclusione e perimetro

Il problema non è una sola frase sbagliata del modello. Il Tutor confonde
quattro passaggi distinti: trovare una fonte pertinente, capire quanto della
fonte serve, comporre una risposta fedele e verificarne il significato.
La modifica sperimentale che riduceva la copertura alle singole voci recuperate
ha eliminato riparazioni e contenuto obbligatorio superfluo, ma ha peggiorato
materialmente una domanda panoramica sulla pagina Utenti. **Non va recuperata
come correzione pronta da pubblicare.**

Raccomandazione: prima rendere osservabile e verificabile il rapporto fra
domanda, fatti necessari e fonti; poi progettare un contratto esplicito
dell'ambito della risposta. La forma della fonte arrivata prima nella ricerca
non può decidere da sola se l'utente vuole un fatto, un inventario o una
procedura. La composizione deve conservare soggetto, condizioni e negazioni,
senza trasformare una fonte mancante nella negazione di una capacità.

Questo mandato produce soltanto il presente documento. Non modifica codice,
prompt, modelli, criteri dei test o produzione; non esegue nuove prove LLM.
La correzione del pallino LRE è un intervento distinto del coordinatore.
Le attività descritte sotto richiedono approvazione prima dell'implementazione.

## 2. Evidenze e loro limiti

### 2.1 Esperimento disponibile

Il resoconto cronologico è nel ramo sperimentale:
`/opt/metnos/.claude/worktrees/lre-resilience/internal/reports/lre-tutor-correction-20260916.md`.
La candidata respinta è identificata dal commit `b9e3e0f3` e dal prompt v18;
il riferimento A/B è `034d6ba9d4c6b8b42eea3c407f9db83e06712ca4`, prompt v17.
Il riferimento non è sinonimo di versione produttiva certificata.

Nell'A/B sono rimasti uguali query, corpus, criteri, modelli, configurazione
centrale, catalogo firmato e fonti. Sono state sostituite nel solo processo
di prova le funzioni `_ledger_scope`, `_coverage_items` e i prompt IT/EN.
Le fonti effettivamente consegnate hanno gli stessi identificatori in tutti
gli **11 confronti**. La prova quindi isola il pacchetto copertura+prompt,
non separa l'effetto individuale di ogni cambiamento. Non è stato eseguito
un confronto fattoriale con codice e prompt cambiati uno alla volta.

Catalogo di prova: 3028 unità, 4 schede, 162 unità manifest ammesse;
digest `sha256:88a363fc1738ae74edd7abc1d5c90d37205275aa7ad77fbe492b333db52a985f`.
L'impronta degli input è rimasta invariata. Era un ambiente iniziale isolato,
con identità privata creata solo per la prova e chiavi pubbliche di verifica.
I contratti non ammessi nell'albero di sviluppo sono rimasti esclusi: non è
una replica certificata del catalogo installato. Nessuna equivalenza fra
questa prova e l'esercizio va presunta.

| Casi | Riferimento v17 | Candidata v18 | Significato della differenza |
|---|---|---|---|
| Domanda incidentale LRE IT, tre ripetizioni | 3 superati | 3 falliti | La risposta candidata usa «fine dell'intero job» anziché i sinonimi attesi. Ma entrambe le versioni hanno anche imprecisioni reali su disabilitazione/conferma/configurazione. |
| Equivalente LRE EN | superato | superato | La candidata associa impropriamente Needs attention all'approvazione: il controllo lessicale non lo rileva. |
| Panoramica LRE | superato | fallito | Ripresa/riprendi spiega il diverso esito lessicale; ordinamento/filtri inventati e conferma non richiesta compaiono in entrambe. |
| Panoramica Servizi | superato | superato | Nessuna regressione materiale osservata su questo caso, non su tutto il dominio. |
| Gestione Utenti | fallito | fallito | Regressione sostanziale nascosta dall'esito binario: la candidata perde email, creazione, token/canali ed eliminazione, presenti nel riferimento. |
| Modelli, configurazione | superato | superato | Informazioni pertinenti; entrambe aggiungono una cornice procedurale/conferma non richiesta. |
| Safety, colonne | fallito | fallito | Percorso della console mancante; guida pubblica presentata come accesso all'interfaccia. |
| Decisioni sulle proposte | fallito | fallito | Fonti generali al posto del contratto della console; azioni concrete assenti. |
| Modelli, provenienza/errori | fallito | fallito | Entrambe negano erroneamente la mappatura esplicita del file sorgente. |

Totali: **7/11 contro 3/11** secondo l'oracolo invariato. Sono undici
esecuzioni, non undici richieste indipendenti: la domanda IT è ripetuta tre
volte. Quattro peggioramenti binari dipendono da formulazioni lessicali;
la regressione materiale Utenti invece non cambia il risultato binario.
Il numero aggregato non descrive da solo la qualità.

Le quattro interpretazioni LRE passano da una riparazione a zero. È una
riduzione del lavoro svolto, **non una prova di fedeltà**. I 140 test
deterministici pertinenti erano tutti superati: verificavano il contratto
implementato, non che quel contratto rispondesse bene alle domande reali.
La prova con fonti scelte manualmente aveva dato un esito migliore della
ricerca completa: non sostituisce la verifica del recupero delle fonti.

Il supplemento su `admin-devices-pairing` e `admin-safety-filters` non ha
prodotto risposta Tutor nella selezione iniziale del percorso; non dimostra
che le procedure funzionino. Non è stato forzato il classificatore.

### 2.2 Fatti, inferenze, punti ancora non provati

- **Accertato:** stessi identificatori di fonte nell'A/B; maggiore copertura
  Utenti nel riferimento; false associazioni LRE; fonti non pertinenti per
  Proposte; differenza fra esiti lessicali e giudizio sul significato.
- **Inferenza sostenuta dal codice e dalle risposte:** l'espansione a tutta
  la superficie compensava alcuni recuperi incompleti; eliminarla senza
  rappresentare l'ambito della domanda lascia scoperti fatti necessari.
- **Non isolato sperimentalmente:** quanto della variazione dipenda dal
  prompt v18, dalla nuova copertura o dalla loro interazione; nessuna causalità
  esclusiva del solo prompt o della sola funzione è certificata.
- **Non provato:** frequenza del difetto nell'uso generale, prestazioni di un
  nuovo classificatore d'ambito, beneficio di un modello diverso o di un
  verificatore semantico, affidabilità di una nuova soluzione in produzione.
- **Confine operativo:** il confronto descrive il candidato storico, non
  lo stato attuale dei servizi. Questo mandato non verifica la produzione.

## 3. Cause distinte e punti del codice

Riferimenti relativi alla radice del repository; il ramo backend esaminato
mantiene il Tutor precedente all'esperimento respinto.

### 3.1 Fonti: contenuto, selezione e provenienza

`runtime/tutor/sources.py::KnowledgeUnit` descrive identità, lingua, autorità,
contenuto, riferimento e impronta. `_ui_surface_units` produce sia una
superficie intera sia proiezioni delle sue singole voci. I documenti pubblici
sono segmentati; `runtime/tutor/semantic.py::retrieve_sources` seleziona i
risultati. Firma e audience dimostrano ammissibilità, non pertinenza alla
domanda né correttezza di ogni interpretazione generata.

All'origine, registro e guida LRE non spiegavano abbastanza le nuove
etichette. Le fonti raffinate dell'esperimento, però, contenevano già le
distinzioni corrette in un segmento unico: non basta aggiungere ancora testo
per risolvere gli errori successivi.

Evidenze puntuali:

- LRE: `doc-public-lre-…-0030` definisce motore, attesa e risultati; altre
  sezioni recuperate descrivono ammissione e conferme. Sono vere nello stesso
  documento, ma non descrivono tutte il medesimo stato o soggetto.
- Utenti: la primaria `runtime-ui-users-facet-04-it` riguarda preferenze,
  la voce 05 i dispositivi. La domanda chiede dove gestire persone e quali
  operazioni siano possibili: quei due fatti non sono un inventario completo.
- Proposte: sono selezionate sezioni di `Metnos_Prospettive_Estese_v1`, non
  la superficie amministrativa pertinente. Un testo vero sulle proposte
  generiche non autorizza a negare i pulsanti della console.
- Modelli/provenienza: arrivano navigazione, rilettura, modelli locali,
  autorità e la voce sugli errori TOML, non quella specifica sulla provenienza.

Occorre distinguere «fonte assente dal catalogo», «presente ma non recuperata»
e «recuperata ma non rispettata». Richiedono interventi diversi.

### 3.2 Ambito: pertinenza non significa completezza richiesta

`runtime/tutor/mode.py::ModeDecision` separa spiegazione, osservazione,
azione, richiesta mista e dubbia. Non rappresenta fatto singolo, confronto,
panoramica o procedura. `runtime/tutor/obligations.py::QuestionObligations`
contiene query delle singole parti, un indicatore di scomposizione e la
ragione, non un contratto di completezza per ciascuna parte.

Nel comportamento precedente `_coverage_items` ricostruisceva l'intera
superficie anche quando il risultato era una singola voce. Il correttore
imponeva così controlli e campi non richiesti. L'esperimento opposto ha
assunto che una voce primaria provasse l'intento focalizzato: Utenti smentisce
questa assunzione. **Né «tutto sempre» né «solo le voci recuperate sempre»
risolvono la scelta dell'ambito.**

Inoltre `_ledger_scope` limita alcune famiglie, ma gli inventari di capacità
secondari rimangono obbligatori nel caso Utenti. Separare contesto utile da
obblighi di risposta deve valere per tutte le famiglie, conservando gli
obblighi realmente richiesti da ciascuna parte di una domanda composta.

### 3.3 Composizione: relazioni false fra fatti veri

`runtime/tutor/compose.py::compose_answer` compone da fonti e lista di
copertura. Anche con definizioni corrette il modello può:

- trasferire una causa da un lavoro al motore dell'istanza;
- associare approvazione a uno stato che la fonte dichiara distinto;
- trasformare un risultato durevole in prova di completamento o di file
  scaricabile;
- dedurre «non esiste/non è visibile» da «non compare nelle fonti recuperate»;
- aggiungere procedura, azione da confermare o invito a ripetere la domanda
  quando era richiesta solo una spiegazione.

Sono errori di soggetto, relazione, condizioni o intento, non semplici parole
mancanti. La correzione v18 del prompt ha ridotto un obbligo contraddittorio
sui passi delle procedure secondarie, ma non ha reso affidabile il risultato.
La presenza di una chiusura sulla conferma non prova da sola un difetto del
compositore: va registrata anche la decisione di modalità e l'eventuale
separazione della richiesta mista. Le tracce A/B conservate non isolano ogni
contributo di quel percorso.

### 3.4 Valutazione: presenza lessicale non equivale a verità

`runtime/tutor/service.py::_find_gaps` controlla percorsi, parole distintive,
campi, controlli e arresti. Dopo una sola ricomposizione il servizio consegna
comunque una risposta; non è un validatore completo delle sue affermazioni.
`scripts/certify_tutor_f2.py::_evaluate` usa inoltre concetti lessicali e
divieti del corpus. Questo protegge requisiti utili, ma può:

- respingere una parafrasi fedele;
- accettare una frase che contiene i vocaboli ma ne inverte il rapporto;
- nascondere un peggioramento reale quando entrambe le risposte già fallivano.

Vanno conservati gli esiti originali e affiancati giudizi sul significato.
Non si corregge il modello ampliando sinonimi solo nei casi appena falliti.
Un'eventuale manutenzione generale dell'oracolo richiede una revisione
separata, esempi positivi e negativi indipendenti e un nuovo confronto di
entrambe le versioni, senza cancellare la serie storica.

## 4. Alternative generali

| Alternativa | Vantaggio | Rischio/costo | Giudizio |
|---|---|---|---|
| A. Solo fonti più chiare e prompt più esplicito | Intervento piccolo, conserva struttura | Le definizioni corrette erano già disponibili; accumulare divieti non corregge la scelta dell'ambito | Utile per vere lacune editoriali, insufficiente come soluzione completa |
| B. Recupero gerarchico con legami canonici voce–superficie–sezione–procedura | Riduce confusione fra documento generale e pagina; separa pertinenza e autorità | Una superficie può restare troppo ampia; non decide da sola quanto richiesto | Base strutturale raccomandata, da misurare isolatamente |
| C. Contratto esplicito d'ambito per ogni parte della domanda | Distingue fatti, inventario e procedura; permette copertura mirata senza perdere panoramiche | L'interpretazione della domanda è fallibile; nuovo schema, prove di dubbio e costi da misurare | Da progettare dopo B, senza assumere che un nuovo LLM risolva il problema |
| D. Composizione vincolata a fatti con soggetto/condizioni e riferimenti | Facilita conservazione delle distinzioni; alcune risposte si rendono deterministicamente | Strutturare tutto il sapere sarebbe oneroso; una citazione valida non prova una deduzione vera | Usare inizialmente i registri già tipizzati; prosa libera rimane un caso distinto |
| E. Verificatore semantico dopo la bozza | Può rilevare contraddizioni e negazioni che il lessico non vede | Seconda valutazione fallibile, errori correlati, latenza; non recupera automaticamente fonti mancanti | Eventuale controllo aggiuntivo, non arbitro unico né autorizzazione all'azione |
| F. Modello più capace o diversa configurazione centrale | Potrebbe migliorare comprensione e fedeltà | Beneficio non misurato; risorse, latenza e rischio di mascherare errori strutturali | Alternativa sperimentale separata, solo con autorizzazione; mai impostazioni locali nascoste |

Non raccomandati: nomi di pagine o frasi incidentali nel selettore; passaggio
automatico a un modello esterno; eliminazione dei passi di sicurezza per
accorciare le risposte; attribuire a una fonte adiacente gli stessi obblighi
della primaria; ridurre il corpus ai casi che passano.

## 5. Proposta incrementale da approvare

### 5.1 Contratto minimo, prima del codice

Proposta da formalizzare nell'ADR Tutor, non schema già implementato:

- **Parte della domanda:** conserva il testo originale e il rapporto con la
  richiesta; distingue fatto/confronto, inventario, procedura, ambito non
  determinato. È un'intenzione informativa, non un nuovo permesso.
- **Evidenza:** identità stabile del fatto, autorità, lingua, soggetto,
  riferimento alla superficie o sezione e legame alla parte della domanda.
  Si deriva dai registri quando disponibile; un modello non crea identità
  canoniche o nuovi collegamenti autorevoli.
- **Ruolo della fonte:** necessaria per rispondere, supporto contestuale,
  oppure non pertinente. Un inventario secondario non diventa automaticamente
  una lista obbligatoria; può essere necessario per una diversa parte.
- **Stato di sufficienza:** fatti necessari disponibili, mancanti o
  contraddittori. Assenza non equivale a falsità. Il significato operativo
  dell'esito insufficiente deve essere approvato, non improvvisato nel prompt.

Il codice può validare identità, legami, schema e completezza di un insieme
già scelto. Non può dedurre correttamente l'intento generale da un suffisso
`facet` o da una soglia di similarità non calibrata. Per la selezione
semantica d'ambito confrontare l'estensione dei contratti esistenti con una
classificazione separata; preferire il determinismo quando è equivalente,
senza moltiplicare chiamate o introdurre elenchi di parole del dominio.

### 5.2 Attività eseguibili e condizioni di uscita

Ogni attività è separabile e revisionabile. Nessun passaggio autorizza quello
successivo se manca la sua condizione di uscita.

| Fase | Attività e punti di intervento futuri | Risultato verificabile |
|---|---|---|
| T0 — Congelare il riferimento | Conservare in archivio interno revisionato risposte, hash, query, catalogo, modello e configurazione della prova; escludere chiavi e dati personali. Mantenere distinta la prova storica dalla base scelta per il nuovo lavoro. | Tutti gli 11 esiti ricostruibili dai dati salvati; nessuna dipendenza esclusiva da `/tmp`; nessuna nuova chiamata necessaria per leggere il confronto. |
| T1 — Rendere diagnosticabile il percorso | Progettare traccia per modalità, parti della domanda, fonti candidate/scelte/escluse, obblighi, bozza, riparazione e motivo dell'esito. Riutilizzare tracce esistenti; oscurare e limitare conservazione/accesso. | Per ogni difetto si distingue fonte assente, selezione errata, copertura errata o composizione; nessun testo privato finisce nel catalogo pubblico. |
| T2 — Specificare i casi semantici | Affiancare al corpus una scheda di requisiti e affermazioni vietate per significato, con estratti autorevoli e gravità. Conservare query/oracoli esistenti. Revisione indipendente prima di implementare. | Le schede distinguono la negazione corretta da quella inversa e la parafrasi dalla perdita di significato; Utenti non è giudicato equivalente solo perché entrambe le versioni falliscono. |
| T3 — Collegare le fonti | In `sources.py`, registro UI e formato del catalogo, progettare legami canonici fra superficie, voce e documentazione. Identità di fatto stabili, distinte dagli indici delle chiavi i18n; migrazione con ricompilazione/firma, non lettura permissiva di vecchi cataloghi. | Test su tutte le superfici IT/EN: identità univoche, legami coerenti, traduzione/fallback e audience invariati; nessuna fonte interna aggiunta. |
| T4 — Rappresentare l'ambito richiesto | Dopo ADR, adattare il contratto di domanda/obbligazioni e la selezione. La domanda composta conserva l'ambito di ogni parte; il risultato della ricerca non lo riscrive. | Prove distinte per fatto, confronto, panoramica, procedura e dubbio, anche quando la fonte più simile è una voce; nessun controllo di autorità spostato al modello. |
| T5 — Derivare copertura e contesto | In `service.py`, costruire gli obblighi per parte della domanda con unione ordinata e senza duplicati. Trattare coerentemente UI, procedure, manifest e inventari di capacità. Conservare contesto utile senza promuoverlo implicitamente. | Una spiegazione non richiede passi estranei; una panoramica non perde campi; una procedura conserva tutti i passi/arresti; una parte focalizzata non restringe un'altra parte completa. |
| T6 — Preservare le relazioni nella risposta | Prima usare proiezioni/rendering dei fatti già tipizzati. Per la prosa mantenere riferimenti a estratti e qualificazioni; valutare composizione e verifica separatamente. Non convertire assenza in negazione. | Mutanti di soggetto, condizione, quantificatore e polarità vengono respinti dalla valutazione; nessun «risultato confermato → lavoro finito» può passare per presenza di parole. |
| T7 — Confronto controllato | Prima prove deterministiche, poi, solo se autorizzate, A/B a fonti/modelli fissi. Separare codice, prompt e ranking in varianti diverse; prove con fonti fissate diagnosticano la composizione, ricerca completa diagnostica l'intero Tutor. | Miglioramento sui casi incidentali senza regressioni semantiche di dominio; tutti i fallimenti e i limiti pubblicati nel rapporto interno. |
| T8 — Chiusura del candidato | Compilazione firmata dell'esatto pacchetto, prove di confine e turno reale HTTP con query invariata; istruzioni di ritiro. Il rilascio è una decisione separata. | Accettazione sul pacchetto effettivo, non su fonti manuali; nessuna promessa di correttezza globale né riattivazione implicita di LRE. |

T0–T2 sono il primo incremento raccomandato. T3–T6 richiedono la decisione
architetturale: non è opportuno farli eseguire a un agente di implementazione
lasciandogli inventare come gestire ambiguità, insufficienza o autorizzazioni.
La successiva attività può essere affidata per modulo con contratto e test
già approvati; la revisione finale resta trasversale.

## 6. Criteri di accettazione semantici IT/EN

### 6.1 Domanda incidentale immutabile

IT: «Spiegami come leggere lo stato delle attività nella console LRE di
Metnos: che differenza c’è tra motore disabilitato, lavoro in attesa e
risultati confermati? È solo una domanda sulla guida; non eseguire operazioni.»

EN: «Explain how to read activity status in the Metnos LRE console: what is
the difference between a disabled engine, pending work and confirmed results?
This is only a question about the guide; do not execute operations.»

Per entrambe le lingue il revisore deve verificare, non cercare semplicemente
parole:

1. Motore disabilitato riguarda l'abilitazione globale; non accetta nuovi
   lavori né avvia nuove unità, lasciando consultabili storia e risultati.
   Non equivale automaticamente a processo spento, pausa di un lavoro,
   attesa di consenso o qualsiasi errore di configurazione.
2. Distinzione fra lavoro in coda e unità in attesa; risorse o dipendenze
   possono spiegare l'attesa. Non è un sinonimo di approvazione e non
   autorizza ad associare genericamente Needs attention a consenso umano.
3. Risultato confermato riguarda una unità intera salvata definitivamente;
   non prova il completamento dell'intero lavoro. Se si parla di artefatti,
   non ogni unità ne produce e il download richiede un artefatto convalidato.
4. Se citati, ultimo risultato riuscito e ultimo aggiornamento restano
   distinti; un aggiornamento non prova avanzamento. La crescita del totale
   durante la scoperta non è automaticamente crescita dei risultati riusciti.
5. Risposta diretta, nessun invito a ripetere la domanda, nessuna operazione
   eseguita, nessuna conferma presentata come conseguenza della spiegazione.
   Un'indicazione di navigazione utile non è vietata; una procedura estesa
   non richiesta non deve sostituire le differenze domandate.

Le condizioni 1–3 e 5 sono necessarie. Le precisazioni condizionali del punto
4 e sugli artefatti non devono diventare un altro inventario da recitare:
se il modello introduce quei temi, deve trattarli correttamente. Nessuna
risposta può compensare una contraddizione centrale con dettagli corretti.

### 6.2 Protezione trasversale, senza eccezioni per LRE

| Famiglia | Prova semantica richiesta in entrambe le lingue |
|---|---|
| Panoramiche LRE/Servizi/Utenti/Modelli | Tutti gli aspetti richiesti dalla domanda, secondo il registro; nessun ordinamento, filtro, pulsante o permesso inventato; dati descrittivi distinti dallo stato live. |
| Utenti | Non perdere elenco/dettaglio e azioni di gestione attestate a favore di preferenze, dispositivi o capacità generali non richieste. |
| Modelli/provenienza | Indicare correttamente fonte dei valori, sostituzioni d'ambiente, errori TOML, protezione dei segreti e limiti di modifica quando richiesti; non negare una funzione per fonte non recuperata. |
| Safety e proposte | Console e guida pubblica distinte; azioni ammesse e vincoli attestati conservati; nessuna scorciatoia suggerita per aggirare una decisione di sicurezza. |
| Procedure richieste | Passi, prerequisiti e arresti completi; nessun passo obbligatorio tolto per ottenere una risposta breve. |
| Domande composte | Tutte le parti soddisfatte autonomamente; procedura richiesta in una parte non imposta alle altre né soppressa da un fatto focalizzato. |
| Informazione/azione/osservazione | Confini esistenti rispettati; nessuna nuova sonda, azione o autorizzazione perché il selettore sceglie un tipo di risposta. |
| Lingua, pubblico e ambiguità | IT/EN semanticamente equivalenti, ripiego linguistico e audience invariati; fonti non ammesse mai usate; dubbio o lacuna non trasformati in capacità inventata. |

Metodo proposto per T7: tre ripetizioni per lingua della domanda incidentale
e dei casi toccati; casi di controllo estratti dal corpus prima dell'ultima
modifica e varianti indipendenti non usate per tarare il candidato. Non
escludere una risposta fallita scegliendo quella migliore. Le tre ripetizioni
sono una soglia minima di stabilità, non una stima statistica dell'affidabilità.

Giudizi separati: copertura dei fatti, fedeltà delle relazioni, pertinenza,
conservazione delle condizioni di sicurezza e assenza di azioni non richieste.
Un revisore indipendente annota affermazione contestata e fonte; disaccordi
restano aperti fino a risoluzione. Un giudice LLM, se approvato, può assistere
ma non essere l'unico decisore né valutare da solo la propria risposta.

Condizione di promozione proposta: nessun errore materiale nelle ripetizioni
incidentali, nessuna regressione materiale nei controlli, nessuna violazione
del confine informazione/azione. I fallimenti preesistenti restano nominati;
se non risolti, non si dichiara certificato quel dominio. Prove, latenza,
numero di chiamate, dimensione del contesto e risposte non fornite vanno
riportati insieme, senza ottimizzare soltanto il numero di riparazioni.

## 7. Decisioni necessarie e arresti

Prima di sviluppare occorre approvare:

1. **Ampiezza:** correzione circoscritta ai significati della console con
   controllo trasversale, oppure lavoro sul contratto generale di ambito.
   L'evidenza sostiene il secondo percorso incrementale, non un'altra frase
   speciale per LRE.
2. **Insufficienza:** quando fornire una risposta limitata, chiedere quale
   aspetto interessa o dichiarare una lacuna. Non alterare implicitamente il
   ripiego `no_source` verso il motore stabilito in ADR0208; ogni variazione
   del confine richiede una decisione esplicita e prove di sicurezza.
3. **Costo:** budget accettabile di latenza e chiamate; nessun nuovo modello
   o servizio esterno senza scelta esplicita e confronto a condizioni uguali.
4. **Valutazione:** responsabile della revisione semantica indipendente e
   soglia di accettazione, fissati prima del nuovo esperimento.
5. **Conservazione:** collocazione e durata dell'archivio interno delle prove;
   dati oscurati, accessi limitati, niente chiavi né cataloghi personali.

Arrestare il candidato se introduce una regressione come Utenti, se richiede
modifica della query per passare, se fonti/modelli cambiano durante l'A/B o se
l'unica giustificazione è il totale dei test verdi. Conservare l'esperimento
respinto; non riportarlo in produzione insieme a una correzione estranea.

## 8. Riferimenti e riproducibilità delle evidenze già esistenti

Artefatti letti, non rigenerati, nella directory
`/tmp/metnos-lre-tutor-candidate.9AG6DW/full/`:

- `result-scoped-coverage.json`, SHA-256
  `d8b17d857e7aeb6ecb3fc469a4c51c7c0756e396e32b4d656589508a5e4ea94b`;
- `result-baseline-034d6ba9.json`, SHA-256
  `1c9e6ae8d2ea56c041ddd4ef0b462899301ea3a27a61c164a99388482bd6aea1`;
- `baseline-provenance.json`, SHA-256
  `796fe08afc73231f55553c3f8fe89b828f816bb915c0de9222634155840d88c0`.

Il rapporto cronologico citato al §2 è l'evidenza durevole già nel ramo
sperimentale. I file temporanei contengono anche dettagli non necessari alla
documentazione pubblica: non copiarli automaticamente in `docs/`. La loro
archiviazione interna controllata è T0, non un'operazione svolta da questo
mandato a documento unico.

Fonti di progetto da consultare nell'implementazione futura: ADR0198
(compilatore e correttore), ADR0202 (osservazioni e domande miste), ADR0203
(identità delle fonti), ADR0208 (assenza di fonti); `runtime/tutor/{sources,
catalog,semantic,obligations,mode,service,compose}.py`, `runtime/ui_surfaces.py`,
`scripts/certify_tutor_f2.py` e corpus F2. Non sono state usate fonti web.
