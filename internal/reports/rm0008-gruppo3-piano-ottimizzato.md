# RM-0008 gruppo 3 — piano ottimizzato

Il gruppo 2 ha lasciato un insieme di autorità **predisposto e inerte**. Il
gruppo 3 lo rende **attivo**: è il gruppo che accende il controllo di nascita.

Questo piano nasce da una richiesta esplicita di Roberto (27/8/2026):
ottimizzare il lavoro rimanente e provare soltanto ciò che serve allo scopo del
gruppo. Le stime di costo qui sotto sono misurate sul gruppo 2, non ipotizzate.

## 1. Che cosa il gruppo 3 rivendica

Una sola frase: **l'insieme predisposto diventa l'autorità che governa la
nascita di un executor, e l'identità del contesto cambia quando cambia una
politica.**

Tutto ciò che non serve a sostenere quella frase non è lavoro del gruppo 3.

## 2. Obblighi ereditati (fonte: §9.3 e sparsi nel rapporto del gruppo 2)

1. invocare davvero il linter sul manifest congelato;
2. controllo AST e risoluzione degli import sulla mappa chiusa dei file e sugli
   involucri dei 21 executor;
3. risoluzione chiusa di modelli e primitive delle proprietà;
4. su Linux sostituire `shutil.which("bwrap")` e `sys.executable` non
   autenticati col registro predisposto; completare il legame Windows;
5. installare i registri di autorità nel bundle privato e **dimostrarne il
   consumo**;
6. portare a `productive` ogni `enforcement_state` interessato, ricostruire
   identificativo ed epoca, e provare che cambiare ciascuna politica cambia
   **sia comportamento sia identità**;
7. analizzare import statici e caricamenti dinamici noti, e fallire se un file
   locale eseguito non appartiene allo snapshot o a una dipendenza chiusa;
8. migrare il bootstrap al pubblicatore sigillato e installare la vista pubblica
   come istantanea del bundle;
9. fornire la tabella chiusa per `ContractId` e attivare le fabbriche Producer;
10. creare le basi dati di ricevute e approvazioni sotto `PATH_USER_STATE/birth`;
11. sostituire il decodificatore libero del contesto **nello stesso incremento
    atomico** che installa il bootstrap sigillato;
12. riaprire e riconvalidare autore, insieme, registri e materiale sotto la
    propria barriera prima di qualunque attivazione: `prepared_not_active` non
    si traduce in `active`.

## 3. Le quattro ottimizzazioni

### 3.1 Congelare una volta sola, alla fine

Ogni modifica alla base congelata costa un ciclo completo: prodotto indietro,
pubblicazione, fotografia, ripristino, pubblicazione, verifica. Nel gruppo 2 ne
sono serviti **dieci**, quasi tutti perché la base veniva emendata appena serviva.

**Regola:** implementare tutto, tenere le modifiche alla base in coda, emendarla
una volta e scattare **una** fotografia alla fine dell'incremento. Costo atteso:
1 ciclo invece di 6.

### 3.2 Provare il proprio contratto, non quello altrui

La primitiva a handle, il giornale, i documenti canonici e la disposizione sono
già certificati dal gruppo 2 e non vanno riesercitati. Le celle del gruppo 3
partono dall'insieme già predisposto — che l'entrata del gruppo 2 sa creare in
una radice isolata — e verificano soltanto attivazione, consumo e identità.

### 3.3 Le due prove che pagano davvero

Misurato sul gruppo 2, i difetti veri li hanno trovati due sole cose:

- **la cella del grafo produttivo (R1)**: ha respinto due volte una porta nel
  posto sbagliato, e aveva ragione entrambe;
- **attraversare la cosa vera invece di simularla**: ha scoperto che su Windows
  una radice storica non era leggibile affatto.

Equivalenti per il gruppo 3: (a) estendere R1 perché dichiari **esattamente
quali chiamanti** possono raggiungere il pubblicatore e le fabbriche, e (b) una
nascita reale di un executor attraverso il bundle attivato, non uno stub.

Il resto delle celle serve a non regredire, non a scoprire.

### 3.4 Nessuno strumento diagnostico senza una decisione che dipende da esso

Nel gruppo 2 tre strumenti su tre hanno risposto per conto proprio prima di dire
la verità: una sonda che si fermava su un'altra cosa, una lettura del token
troncata a 32 bit, un passo diagnostico che non veniva nemmeno eseguito. Uno
strumento si costruisce solo quando la sua risposta cambia una decisione, e
deve dichiarare **come** ha ottenuto la risposta.

## 4. Ordine che minimizza il rilavoro

L'ordine non è libero: il punto 6 (portare le politiche a `productive`) cambia
identificativo ed epoca del contesto, quindi **ogni vettore golden va rifatto**.
Farlo presto significa rifarlo a ogni passo.

1. barriera di riconvalida (12) e migrazione del bootstrap (8, 11) — nessun
   cambio di identità;
2. tabella chiusa `ContractId` e fabbriche Producer (9), basi dati (10);
3. registri consumati davvero (5), linter e controlli chiusi (1, 2, 3, 7),
   registro sandbox (4) — il comportamento diventa reale, l'identità non ancora;
4. **ultimo**: portare a `productive` gli `enforcement_state` corrispondenti (6),
   ricostruire identificativo ed epoca, rifare i vettori golden una volta sola,
   e provare che toccare una politica muove entrambe le cose.

## 5. Fuori dal gruppo 3, dichiarato

Autenticità della distribuzione, protezione fra utenti sullo stesso host,
servizio Windows definitivo, archivio a freddo: gruppi 4-6. Anche i tre
requisiti che il gruppo 2 ha dichiarato non provati restano dichiarati finché
qualcuno non li assegna.

## 6. Criterio di uscita

Come per il gruppo 2: una tabella riempita cella per cella con simbolo
produttivo, prova di modulo, prova di integrazione, prova installata ed esito.
Una cella rinviata dice `N/A`, il gruppo che la possiede e il motivo. Il verde
di una prova che non attraversa il simbolo produttivo non riempie la colonna
della prova installata.
