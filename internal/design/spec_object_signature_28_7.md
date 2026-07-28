# SPEC — Firma degli oggetti osservati e giudizi dell'utente

> **28/7/2026 — direzione di progetto, non implementata.** Origine: conversazione
> di progettazione con Roberto del 28 luglio, nata dal caso «questa mail è spam, le
> simili pure» e generalizzata da lui a qualunque oggetto osservabile.
>
> **Perché sta qui e non in RM-0001.** La firma descrive **gli oggetti**, non
> l'utente: è una capacità dei domini che quegli oggetti li producono, e vale oltre
> la conoscenza utente. RM-0001 ne consuma soltanto l'interfaccia — vi conserva i
> **giudizi** dell'utente su una firma, con provenienza, evidenza e cancellazione —
> e resta costruibile anche senza, con la sola via per mittente e lista (§6.9).
>
> **Stato**: nessuna implementazione, nessuna ADR. Si apre soltanto se la via
> deterministica per identità di sorgente lascia una lacuna documentata su un
> corpus reale. La sede della ratifica è una ADR del dominio interessato, non
> questa spec e non RM-0001.

---

## 0. Il caso che la origina

Un utente segnala un messaggio come indesiderato e si aspetta che i simili lo
diventino. La via corta — stesso mittente, stessa lista — copre la maggior parte
dei casi in modo deterministico ed è descritta in RM-0001 §6.9. Resta scoperto il
caso in cui la sorgente cambia a ogni invio e la somiglianza sta in **come
l'oggetto è fatto**. Questa spec descrive la direzione con cui si affronterebbe,
generalizzata a qualunque genere di oggetto osservabile invece che risolta per la
posta.

## 0-bis. Che cosa esiste già, verificato

Non è terreno vergine: un embrione esiste, copre **un** tratto di **un** genere di
oggetti, ed è esattamente nella forma che questa spec propone di generalizzare.

| Pezzo | Stato | Prova |
|---|---|---|
| Tratti di **struttura** per la posta, calcolati da intestazioni standard: `list`, `bulk`, `auto`, `noreply`, `esp` | ✅ esiste, deterministico, vocabolario chiuso di cinque valori | `runtime/mail_client.py:398` `_category_hints` |
| Sono dichiarati **segnali neutri e non un giudizio** | ✅ esiste, ed è la stessa separazione di §2 fra «insolito» e «indesiderato» | docstring della stessa funzione |
| Emessi su ogni messaggio letto | ✅ esiste | `category_hints` nel contratto d'uscita di `read_messages` |
| Classificatore generico: lista + dimensione + **insieme chiuso** di classi → etichetta ogni elemento | ✅ esiste | `runtime/classify_entries.py` |
| Pre-filtro **deterministico** che etichetta i casi inequivocabili senza modello | ⚠️ esiste ma **spento per impostazione predefinita** | `runtime/classify_entries.py:74` regole, `pre_filter` default off |
| Il modello mappa in un vocabolario chiuso, non inventa classi | ✅ esiste | stesso file, lotti a tier `fast` |
| Non partiziona: arricchisce e lascia partizionare a valle | ✅ esiste | stesso file |

**Che cosa manca**, ed è la maggior parte: gli altri tratti (sorgente scomposta,
richiesta, tono, argomento), la firma come oggetto **persistito e versionato**, le
proiezioni, i **giudizi dell'utente** agganciati a una proiezione, la frequenza
relativa al contesto, e qualunque forma di derivazione delle classi dal corpus.

Due osservazioni che orientano il lavoro. La prima: il pattern non è estraneo al
sistema — tratti strutturali calcolati più una mappatura del modello in un insieme
chiuso è già ciò che il codice fa, e questa spec lo estende invece di sostituirlo.
La seconda, meno comoda: la via deterministica **c'è già ed è spenta**, mentre
quella con il modello è la predefinita. È lo stesso ordine di priorità che §7.9
inverte, e il primo passo utile non è costruire: è misurare quanto coprirebbero le
regole esistenti se venissero accese.

## 0-ter. Che cosa è davvero universale, e che cosa no

Un classificatore universale deterministico — una funzione che, dato un oggetto
qualunque, ne dica il genere — **non è realizzabile**, e non va tentato. Ciò che è
universale non è il calcolo: è il **contratto**. Lo schema è comune, il calcolo è
locale al dominio, e nessun dominio è obbligato a riempirlo tutto.

Le dimensioni non sono sullo stesso piano, e mescolarle è l'errore che rende la
cosa «troppo difficile».

**Universali e deterministiche — quattro.** Non richiedono di capire l'oggetto:
sono note per il solo fatto che l'oggetto è arrivato.

1. **Tipologia.** Non si classifica: la si sa. Il dominio che ha prodotto l'oggetto
   sa che cos'è, perché è lui ad averlo letto. È il campo che seleziona come si
   calcolano gli altri.
2. **Sorgente**, nei suoi quattro tratti — canale, identità entro il canale, grado
   di autenticazione, relazione (primo contatto, ricorrente, noto). Tutti esistono
   per qualunque oggetto: un messaggio ha un mittente, una segnalazione un autore,
   una pagina un dominio, un file un proprietario. I primi tre si leggono, il
   quarto si conta.
3. **Tempo.** Momento d'osservazione, momento di creazione quando dichiarato,
   cadenza rispetto agli altri oggetti della stessa sorgente. Sempre disponibile,
   sempre calcolabile.
4. **Modo d'arrivo.** L'hai chiesto tu, è arrivato senza che tu lo chiedessi, o
   l'ha prodotto il sistema. È deterministico — lo sa il runtime — ed è più
   discriminante di quanto sembri: la differenza fra ciò che cerchi e ciò che ti
   cerca è la radice della questione «indesiderato».

**Universale come casella, locale nel contenuto — una.**

5. **Struttura.** Il concetto vale per ogni genere di oggetto, i tratti no: le
   intestazioni di lista valgono per la posta, non per un documento. È una casella
   comune riempita da chi conosce il formato. Qui sta il lavoro vero, ed è per
   dominio.

**Non universali — tre.** Valgono solo per oggetti che comunicano, e richiedono il
modello entro vocabolari chiusi.

6. **Argomento**, 7. **richiesta**, 8. **tono.** Una fotografia non chiede nulla e
   non ha tono; un evento di calendario nemmeno. Pretenderli ovunque è precisamente
   ciò che renderebbe l'impianto impossibile.

**La firma è parziale per costruzione.** Un dominio riempie i campi che sa
riempire e lascia vuoti gli altri; una proiezione può usare soltanto campi
presenti. Ne segue che l'impianto è **incrementale e non richiede un piano
d'insieme**: la posta ha già la struttura, una piattaforma di codice ha
autenticazione e relazione quasi gratis, i documenti forse non avranno mai nulla
oltre alle quattro universali — e vanno bene lo stesso, perché con tipologia,
sorgente, tempo e modo d'arrivo si distingue già moltissimo senza leggere una
parola.

## 1. Il principio: firma composita, non vettore di testo

Se la via per mittente lascia una lacuna documentata, la risposta **non** è
l'immersione del testo in un vettore. È una **firma composita tipizzata**: un
insieme dichiarato di tratti, in larga maggioranza calcolati e non inferiti, di cui
solo uno o due vengono da un modello e comunque su vocabolario chiuso.

E non va costruita per la posta. Va costruita **una volta, per qualunque oggetto
osservabile** — messaggio, documento, articolo, pagina, contenuto pubblicato — con
lo stesso schema a quattro campi:

- **Tipologia**: che genere di oggetto è. È il campo che seleziona come si calcolano
  gli altri tre.
- **Struttura**: la forma, non il contenuto. È l'intuizione centrale, e vale ben
  oltre la posta: gli oggetti di una stessa categoria si somigliano nella forma
  molto più che nelle parole. Una truffa e una promozione hanno strutture
  riconoscibili qualunque cosa dicano; così un comunicato, una fattura, un
  articolo di cronaca. Per la posta significa intestazioni di lista e di
  disiscrizione, rapporto fra testo e marcatura, collegamenti rispetto al testo,
  disallineamento fra nome mostrato e mittente e indirizzo di risposta, esito delle
  verifiche di autenticità, tipi di allegato, destinatario visibile o in copia
  nascosta. Per un documento o una pagina significa altri tratti, calcolati allo
  stesso modo: la definizione dei tratti appartiene alla tipologia, lo schema no.
- **Sorgente**: e non è un'etichetta piatta, perché è il tratto che fa più lavoro
  di tutti. Una segnalazione aperta sul tuo repository e un messaggio di posta che
  chiede aiuto possono avere **identica** tipologia di richiesta, identico
  argomento e identico tono, e non sono la stessa cosa. La sorgente si scompone in
  quattro tratti, tre dei quali deterministici:
  - **canale**: posta, piattaforma di codice, sito, messaggistica, archivio locale.
    Porta con sé il contratto implicito dell'interazione: una segnalazione sul tuo
    repository è un'interazione attesa, un messaggio non richiesto no.
  - **identità entro il canale**: l'indirizzo esatto, l'account. È il tratto forte
    quando c'è (§6.9).
  - **grado di autenticazione della sorgente**: chi garantisce quell'identità. Una
    piattaforma che autentica il suo utente garantisce molto; un protocollo di
    posta con verifiche di dominio superate garantisce qualcosa; un mittente non
    verificato non garantisce nulla. È calcolabile, non inferito, ed è il tratto
    che distingue l'avviso autentico della banca dalla sua imitazione — cioè
    esattamente ciò che il tono non sa fare.
  - **relazione**: primo contatto, sorgente ricorrente, corrispondente noto. Si
    ricava contando nel corpus, senza modello e senza registro nuovo.

  **Il canale entra obbligatoriamente in ogni proiezione che agisce**, e per una
  ragione diversa da quella del tono: non perché sia imitabile, ma perché il
  significato è relativo alla sorgente. Un giudizio imparato sulla posta non deve
  poter transitare su una piattaforma di codice: «chiede aiuto» segnalato come
  molesto in una casella non può rendere molesta una segnalazione aperta su un
  repository. Le due regole insieme dicono che una proiezione che agisce contiene
  sempre il canale, e non è mai fatta di solo tono.
- **Argomento e richiesta**: di che cosa parla e che cosa vuole da te. La
  **richiesta** è il tratto più discriminante e il più stabile: chi truffa cambia
  parole a ogni invio, ma quello che vuole — segui un collegamento, paga,
  rispondi, conferma un'identità, scarica — resta quasi sempre lo stesso.
- **Tono**: neutro, urgente, minatorio, promozionale, adulatorio, insistente.
  Vocabolario chiuso e piccolo, con marcatori deterministici prima del modello —
  imperativi, scadenze, conti alla rovescia, densità di esclamativi, maiuscole —
  e il modello che mappa soltanto ciò che il codice non riconosce. È un tratto
  utile perché l'urgenza artificiale è una delle firme più costanti dell'inganno,
  e perché serve anche altrove: un contenuto allarmistico si può declassare
  nell'ordinamento delle novità (UC-13) senza toccare l'argomento.

  **Ma il tono non basta mai da solo, ed è una regola, non una cautela.** Un avviso
  autentico della tua banca e la sua imitazione hanno lo *stesso* tono: l'urgenza è
  proprio ciò che l'inganno copia meglio. Una proiezione che agisce non può quindi
  essere fatta di solo tono: deve contenere almeno un tratto di **struttura** o di
  **sorgente**, che sono i due che l'imitatore non controlla. È anche il tratto
  epistemicamente più debole — due persone in buona fede discordano fra «urgente» e
  «insistente» — quindi vale in combinazione e non in isolamento.

I campi non sono cinque per sempre: lo schema è un **insieme dichiarato** di tratti,
e un dominio può aggiungerne se il suo genere di oggetti lo richiede. Ciò che non
cambia sono le regole di ammissione — vocabolario chiuso, deterministico prima del
modello, ispezionabile — e il divieto di un elenco centrale.

**L'insieme dei tratti firma l'oggetto, e firma un genere non un esemplare.** È la
proprietà che rende il meccanismo praticabile. Due messaggi di truffa da mittenti
diversi non si somigliano: hanno la **stessa firma**, perché la firma non descrive
il singolo oggetto ma la categoria a cui appartiene. Ne segue la semplificazione
più importante di tutta questa direzione: con tratti tipizzati e vocabolari chiusi,
**«simile» non è una distanza ma un'uguaglianza** su un sottoinsieme dei campi.
Niente vettori, niente soglia da tarare, niente indice denso, nessuna delle cose
che §8.1 tiene fuori dal nucleo — e la ricerca è un raggruppamento esatto.

Il giudizio dell'utente si applica quindi a una **proiezione dichiarata** della
firma, ed è la proiezione a governare quanto si generalizza: pochi campi
significano generalizzazione larga, molti campi generalizzazione stretta. La scelta
è esplicita, non un parametro nascosto, ed è mostrabile in una riga: «trattata così
perché ha la stessa tipologia, la stessa struttura e la stessa richiesta, quale che
sia il mittente». Se generalizza troppo, si aggiunge un campo alla proiezione; se
troppo poco, se ne toglie uno. Un comportamento che si corregge guardandolo.

*Nota terminologica*: qui «firma» descrive e non autentica. Non ha nulla a che
vedere con la firma crittografica dei manifest, che attesta l'origine di un
executor; questa dice a quale genere appartiene un oggetto osservato.

**Lo schema è universale, i vocabolari no.** È il punto che separa questa idea da
un progetto di ontologia, che invecchierebbe e che nessuno manterrebbe. I quattro
campi sono comuni; i **valori ammessi di ciascuno li dichiara il dominio** che
produce quegli oggetti, con lo stesso meccanismo delle dichiarazioni di
personalizzazione (§5.4) e delle forme credenziale (ADR 0199). Nessun elenco
centrale di argomenti, di strutture o di tipologie: chi conosce gli oggetti ne
dichiara i tratti, e il resto del sistema li consuma senza saperne il contenuto.

**Il guadagno vero è che due capacità diventano una.** Con una firma comune,
«questa mail è spam» e «mi interessa la biologia» (UC-13) sono lo **stesso**
meccanismo: un giudizio dell'utente su un campo della firma, negativo nel primo
caso e positivo nel secondo, che il dominio usa per marcare o per ordinare. Non
servono un classificatore di posta e un ordinatore di notizie: serve una firma e
due segni. Ed è la stessa ragione per cui vale la pena generalizzare invece di
risolvere il caso della posta.

**Il confine che non va superato.** La firma descrive **gli oggetti**, non
l'utente. Ciò che appartiene a questa roadmap resta soltanto il **giudizio** —
il tuo segno su una firma — con provenienza, evidenza e cancellazione. Se un
giorno la firma diventasse un modo per descrivere *te* invece che le cose che
guardi, saremmo tornati al profilo narrativo di §6.7.1 per un'altra strada.

**Sede della decisione.** Questa è una capacità dei domini degli oggetti e vale
oltre la conoscenza utente: non è RM-0001 a doverla ratificare. RM-0001 ne dichiara
soltanto l'interfaccia che le serve — una firma stabile, versionata, ispezionabile
e cancellabile — e resta costruibile anche senza, con la sola via per mittente e
lista.

## 1-bis. La firma semantica: dove il vettore è lo strumento giusto

Fin qui la firma dice **di che genere** è un oggetto. C'è una seconda domanda che
i vocabolari chiusi non possono soddisfare: **di che cosa parla**. Un documento di
testo, la descrizione di una fotografia, una segnalazione su una piattaforma di
codice, la nota di un appuntamento e un messaggio possono riguardare la stessa
cosa senza condividere una parola. Nessuna tassonomia chiusa lo coglie, e provare
a chiuderla è il progetto di ontologia che §1 rifiuta.

È esattamente la condizione di riapertura già scritta in §2: *un campo
intrinsecamente aperto, di cui non si può chiudere il vocabolario*. Il campo è
`argomento`, e per esso il vettore non è una scorciatoia: è lo strumento adatto.

**Due firme, due lavori distinti, e non vanno confusi.**

| | Firma tipizzata | Firma semantica |
|---|---|---|
| risponde a | di che **genere** è | di che **cosa** parla |
| forma | valori tipizzati, vocabolari chiusi | vettore |
| operazione | uguaglianza su una proiezione | vicinanza |
| serve a | **decidere** una classe | **trovare** i correlati |
| ispezionabile | sì, riga leggibile | no |
| governa un'azione | sì, entro le regole di §1 | **mai** |

La riga che conta è l'ultima. La firma tipizzata **decide**; quella semantica
**suggerisce vicinanza**. Un oggetto non diventa indesiderato perché il suo
vettore è vicino a un altro: diventerebbe un classificatore opaco, che è
precisamente ciò che questa spec evita. La vicinanza serve a *recuperare*, e il
recupero si presenta con gli oggetti trovati, mai con una distanza.

**Che cosa abilita, e non è poco.** Il richiamo che attraversa i domini: mettere
in relazione un messaggio, un documento, un appuntamento e una fotografia che
parlano della stessa cosa. Oggi il recupero esatto e testuale lo manca per
costruzione: una parafrasi lessicalmente distante non trova nulla. È il limite già
dichiarato in RM-0001 §8.1, e questa è la via per superarlo — fuori dallo store
delle memorie, sugli oggetti.

**Sei discipline, senza le quali torna il difetto di RM-0001 §6.7.1.**

1. **È proprietà dell'oggetto, non conoscenza dell'utente.** La calcola il dominio,
   vive accanto all'oggetto, e RM-0001 non ne conserva nemmeno una. Ciò che RM-0001
   conserva resta il giudizio.
2. **Versionata.** Cambiare modello d'immersione invalida tutti i vettori
   precedenti invece di mescolarli, come per la versione dell'estrattore.
3. **Cancellabile.** Un vettore derivato da contenuto personale muore con l'oggetto
   e con il protocollo di oblio; non sopravvive in un indice separato.
4. **Non entra nelle chiavi condivise.** Le cache dei piani restano indipendenti
   dall'utente e dagli oggetti: la vicinanza si calcola dopo, mai prima.
5. **Sempre accompagnata dalla firma tipizzata.** È il rimedio al suo difetto
   strutturale: un vettore non si guarda e non si corregge, quindi ogni risposta
   che lo usa mostra accanto i tratti leggibili che l'hanno accompagnato.
6. **Calcolata con parsimonia.** Un vettore per oggetto non è gratis: si calcola a
   richiesta o in lotti notturni, mai dentro un turno interattivo. Il precedente è
   l'indice immagini costruito pigramente.

**Che cosa esiste già.** L'infrastruttura c'è e non va costruita:
`runtime/qwen_embedding.py`, `runtime/bge_embedding.py`, `runtime/clip_embedding.py`
e `runtime/face_embedding.py` sono presenti, gli indici immagine sono in
produzione, e la cache di primo livello usa già la vicinanza coseno sulle query.
Manca soltanto un'immersione **per oggetto osservato**, con le sei discipline
sopra. Il costo del lavoro non è il modello: è la disciplina.

## 2. Derivare le classi dal corpus: un reticolo dato, non un albero appreso

L'idea successiva è naturale: su un corpus di messaggi si possono **derivare** le
classi, guardando come le firme si raggruppano. È giusta nella sostanza — il
corpus contiene le classi e non serve inventarle — ma il meccanismo per estrarle è
molto più semplice di un albero appreso con distanze fra dimensioni, e per una
ragione che appartiene alla firma stessa.

**Con campi tipizzati e vocabolari chiusi, le classi non si cercano: si contano.**
Una classe è una **proiezione frequente**: un sottoinsieme di campi con un
sottoinsieme di valori che ricorre nel corpus. Trovarla è un raggruppamento con un
conteggio — l'operazione più economica che una base dati sappia fare — non un
addestramento. Il risultato è esatto, riproducibile, e si spiega da sé perché la
classe *è* la sua descrizione: «tipologia messaggio, struttura promozionale, nessuna
disiscrizione, richiesta segui-collegamento — 143 casi».

**L'albero c'è già, ed è il reticolo delle proiezioni.** Le proiezioni ordinate per
inclusione formano una gerarchia gratuita: meno campi significa classe più
generale, più campi classe più specifica. È esattamente il controllo della
generalizzazione descritto sopra, visto dall'altro lato. Non c'è una struttura da
apprendere: c'è una struttura da percorrere.

**La distanza fra dimensioni non è definita, e definirla costerebbe tutto.** Fra
due valori di un vocabolario chiuso non esiste una distanza naturale: quanto dista
«biologia» da «tecnologia»? Per rispondere servirebbe immergerli in uno spazio
vettoriale, cioè reintrodurre esattamente ciò che questa direzione ha eliminato —
opacità, soglie da tarare, irriproducibilità, un indice denso. Su campi chiusi la
somiglianza è uguaglianza, e va tenuta tale.

**Da classe candidata a classe utile.** Un raggruppamento frequente non è ancora
una classe che serve: lo diventa quando i tuoi giudizi su quel raggruppamento sono
concordi. Support e concordanza sono due conteggi, entrambi deterministici, ed
entrambi mostrabili. Una classe candidata si **propone** — «questi 143 messaggi
hanno la stessa firma e ne hai segnalati sette su sette: li tratto tutti così?» —
e non si applica da sola, per la stessa ragione di §6.9.

**La normalità è locale, e questo cambia il verdetto.** Una classe non è buona o
cattiva in assoluto: lo è rispetto al **contesto** in cui compare. Su una casella
di assistenza le richieste d'aiuto sono la norma e costituiscono la maggior parte
del traffico; la stessa firma su una casella personale, da una sorgente al primo
contatto e non autenticata, con richiesta di denaro, è un caso su migliaia. Non è
la firma a essere cambiata: è cambiata la sua frequenza attesa.

Ne segue che la misura utile non è la frequenza assoluta ma la **frequenza
relativa al contesto**, e resta un conteggio: quante volte quella proiezione
compare in *questa* casella contro quante ne compare altrove. Anche la spiegazione
resta a portata di riga: «insolito per questa casella — tre casi su quattromila,
mentre sulla casella di assistenza è il sessanta per cento».

Il contesto non va inventato: è l'ambito che l'utente ha già dichiarato nominando
le proprie caselle (§5.6), ed è il campo `scope` che ogni memoria porta già (§6.2).
Un giudizio nasce quindi legato al proprio contesto, e non si applica altrove per
inerzia — è la stessa regola del canale, un livello più in basso.

**Raro non significa cattivo.** L'anomalia rispetto a una base attesa richiama
attenzione, non condanna: può marcare o ordinare, mai agire. Il conteggio sa dire
che cosa è **insolito**; solo il tuo giudizio sa dire che cosa è **indesiderato**.
Tenere separate le due cose è ciò che impedisce a una statistica di diventare una
sentenza — e vale anche al contrario, come partenza a freddo: la rarità si misura
dal primo giorno, senza che tu abbia segnalato nulla, ma non decide nulla finché
non lo fai.

**Il parallelo con Leiden, ed è deliberato.** Questa roadmap ha già tolto una
comunità appresa su grafo, non perché fosse cattiva in sé ma perché il suo
consumatore era servito meglio da un raggruppamento deterministico (§3.2). Qui
vale lo stesso ragionamento, applicato prima invece che dopo: un clustering sopra
una firma tipizzata risolverebbe con un metodo approssimato un problema che un
conteggio risolve in modo esatto. La condizione che riaprirebbe la questione è
unica e precisa: **se un campo dovesse essere intrinsecamente aperto o continuo**,
e non fosse possibile chiuderne il vocabolario. Fino ad allora, contare.

**Perché è meglio di un vettore, in questo documento.** *Spiegabile*: la
somiglianza si stampa in una riga — «stessa struttura, nessuna disiscrizione, otto
collegamenti, mittente non allineato, stessa richiesta» — mentre un coseno non dice
nulla che si possa contestare. *Deterministica quasi ovunque*: il modello serve
solo dove il codice non arriva, che è §7.9 applicato. *Chiusa e riproducibile*: a
vocabolari chiusi lo stesso messaggio produce sempre la stessa firma, che è il
vantaggio dichiarato di Metnos sulle alternative opache (§17.5). *Ispezionabile e
cancellabile*: una firma è una riga di valori tipizzati, compare nell'inventario e
si revoca; un vettore no.

**Il confine con l'esclusione di §8.1, che non è contraddetta.** Ciò che resta
fuori dal nucleo è il **recupero denso sul corpus delle memorie**: cercare
semanticamente fra ciò che il sistema sa di te. Qui invece un dominio classifica i
**propri oggetti** con tratti che dichiara. Sono due cose diverse e non c'è
eccezione da concedere: RM-0001 non ne è nemmeno la sede: la sede è una ADR del
dominio posta.

**Il rischio proprio di questa direzione.** I due tratti che vengono dal modello
sono il punto debole: se l'estrattore cambia, cambiano in silenzio tutte le firme.
Contromisura, già usata altrove nel documento: la firma porta la versione
dell'estrattore, e un cambio di versione invalida le firme precedenti invece di
mescolarle. E una nota di onestà che vale più di una contromisura: chi manda
truffe si adatta, quindi un classificatore appreso è sempre in ritardo. Si promette
copertura, mai protezione.
