# FLAKY, TRICKY, RISKY: quando il meglio è nemico del bene — la velocità (MTP, cache) vale l'incertezza che introduce?

Da alcuni mesi sto sviluppando **Metnos**, un assistente personale self-hosted (gira tutto in casa mia, su un mini-PC, con un modello LLM locale — niente cloud). Come tutti i sistemi simili, uno dei componenti principali è il **motore** che estrae le intenzioni dell'utente e costruisce la catena di esecutori/agenti che realizzano la risposta.

Poiché gli esecutori sono tanti (anche centinaia) e variabili, servono strategie sofisticate per **disambiguare** la richiesta, **ridurre** i candidati e fare il **wiring** tra i componenti fino al risultato. E una richiesta può contenere più azioni che toccano **domini diversi** (file, mail, database, calendario…): non è banale. Quindi si usano tecniche miste — **deterministiche** e **statistiche (LLM)**.

Se si potesse fare tutto in modo deterministico sarebbe l'ideale: più veloce e con risultati certi. Purtroppo non è possibile, se non rendendo il sistema rigido: l'utente dovrebbe parlare con un sottoinsieme "innaturale" della lingua. Quello che segue è il racconto della **lotta tra la parte deterministica e quella statistica** del motore, e del mio tentativo di capire a quali **limiti dimensionali** — in termini di azioni e domini — il motore si "rompe".

## L'architettura, in 3 righe

L'utente scrive in linguaggio naturale. Un **estrattore di intenzioni** (LLM) scompone la frase in clausole *(verbo, oggetto)*. Un **proponitore** (LLM, con vincoli) sceglie gli esecutori e li mette in sequenza. Infine una serie di **guardie deterministiche** (codice puro, niente LLM) correggono struttura, ordine e argomenti, prima di eseguire.

## Come ho cercato i limiti

Ho costruito una griglia: sull'asse verticale il **numero di azioni** (2→8), su quello orizzontale il **numero di domini** (1→7). Ho generato ~117 richieste reali che fanno crescere entrambe le dimensioni, e ho confrontato ogni piano prodotto con uno corretto noto. Una cella è "verde" solo se l'engine sceglie **gli esecutori giusti, nell'ordine giusto**.

*[IMMAGINE 1: la heatmap — carica `struct_iter5_final.png`]*

*Ogni cella è una difficoltà (N azioni × M domini), 4 richieste a caso. Verde = tutto corretto. Il riquadro fino a **6 azioni × 5 domini è al 100%**; oltre, inizia a degradare.*

## Quanto è complessa, davvero, una frase

Per dare l'idea: una sola frase può contenere 6 domini e 8 azioni. Eccola scomposta negli esecutori, colorati per dominio di appartenenza:

*[IMMAGINE 2: la scomposizione — carica `example_breakdown_it.png`]*

Notate una cosa: lo **stesso verbo "trova"** compare su 4 oggetti diversi (file, spese, foto, web). È esattamente lì che nascono i guai.

## Da una sicurezza iniziale all'apparente casualità

All'inizio i numeri erano ottimi: il riquadro 6×5 al 100%. Ma misurando **più volte la stessa richiesta**, lo stesso input produceva a volte un piano corretto, a volte uno rotto. La sensazione era di **pura casualità** delle prestazioni. Per un sistema che vuole essere affidabile, è il peggior tipo di problema: non sai se hai un bug o solo sfortuna.

## La caccia alle cause

Ho spinto Claude Code, in modo iterativo, a fare un'analisi multidimensionale, con una regola: **mai una pezza, sempre la causa**. Ho isolato un componente alla volta:

- l'estrattore di intenzioni, chiamato 5 volte sullo stesso input → **deterministico**;
- il proponitore, idem → **deterministico**;
- ma la **pipeline completa** → instabile.

Il paradosso si è sciolto con un test mirato: la stessa identica chiamata al modello, eseguita dopo richieste diverse, produceva **output diversi** (in un caso, addirittura vuoto).

## I findings

Sono il più preciso possibile su cosa ho **misurato** e cosa resta **ipotesi**.

**Misurato (riproducibile).** Ho isolato i componenti del motore uno a uno:
- l'estrattore di intenzioni, chiamato 5 volte sullo stesso input → identico (deterministico);
- il proponitore, idem → deterministico;
- ma la stessa identica chiamata HTTP al modello (stesso testo, stesso *seed*, stesso slot), eseguita **dopo richieste diverse**, produce **output diversi** su frasi lunghe — in un caso perfino vuoto. Quindi: **l'output dipende dallo stato interno lasciato dalle chiamate precedenti**, non solo dall'input. Su frasi corte non si manifesta.

**E qui ho sbagliato due ipotesi di fila — gli esperimenti me le hanno smentite entrambe (è la parte più utile del racconto).**

*Ipotesi 1: lo speculative decoding (MTP).* È una tecnica che accelera la generazione decodificando più token "in anticipo" e poi verificandoli: sospettato perfetto. Controprova decisiva: ho lanciato **lo stesso identico modello, ma senza MTP**, e ho ripetuto il test su output lunghi. Risultato: **flaky lo stesso.** MTP scagionato.

*Ipotesi 2: la cache interna del server (KV-cache) riusata tra le richieste.* Ho provato a **disabilitarla**, aspettandomi che stabilizzasse. Successo l'opposto: con la cache **attiva** l'output era stabile (3/3 identici), **disattivandola** diventava instabile (3/3 diversi). Anche questa ipotesi sbagliata.

*La causa vera (coerente con tutti i dati): il calcolo in virgola mobile su GPU non è deterministico sulle generazioni lunghe.* Quando il modello elabora più richieste **in parallelo** (per andare veloce), l'ordine delle somme interne cambia di volta in volta; su poche decine di token non si nota, ma su migliaia di token queste micro-differenze si accumulano e l'output finale diverge. Quadra con tutto: l'estrattore di intenzioni (output corto) è stabile; il proponitore (output lungo) balla; e la cache, quando la richiesta è identica, *fissa* il percorso di calcolo e lo rende ripetibile.

Morale del sotto-capitolo: il sospettato ovvio quasi mai è il colpevole, e l'unico modo per saperlo è l'esperimento. La domanda del titolo resta aperta: **questa velocità vale l'incertezza che porta?**

**La scoperta che ribalta il problema (misurata).** Le **guardie deterministiche a valle assorbono ~14 instabilità su 15**: l'LLM oscilla, ma il codice deterministico **normalizza** l'output e il piano finale torna corretto. La "casualità" quasi mai raggiunge l'utente. Conseguenza pratica importante: **un test che misura il piano grezzo del modello misura soprattutto il rumore**, non la qualità del sistema. Due esecuzioni dello stesso identico codice danno numeri diversi, e molti "errori" semplicemente *ballano* tra una misura e l'altra.

**Bug veri residui (pochi, stabili).** A 7-8 clausole l'LLM a volte **contamina** una clausola con l'oggetto di una vicina ("trova le **foto**", dopo "trova i **file**", diventa erroneamente *file*). Non è ignoranza (isolata, la mappa è giusta), è un bias d'attenzione sulle frasi lunghe. L'ho corretto con una guardia deterministica che ri-deriva l'oggetto dal testo della singola clausola.

## Morale

Per giorni ho iterato alla ricerca della **pietra filosofale**: la certezza assoluta in una tecnologia incerta per definizione. A un certo punto serve il coraggio di **fermarsi e accettare un compromesso**.

Il mio compromesso attuale: nella grande maggioranza dei casi, anche richieste molto complesse (fino a ~6 azioni e ~5 domini) vengono risolte correttamente e in modo ripetibile. Le rimanenti vengono gestite in modo che **non ci sia mai un errore silenzioso**: l'utente è sempre informato del problema e, se serve, invitato a riformulare.

Nel mio caso c'è una difficoltà in più: Metnos è **multilingua by design**. Non posso ricorrere a "trucchetti" mono-lingua — sinonimi cablati, dizionari, hardcoding — che risolverebbero un problema in italiano ma lo lascerebbero aperto in ogni altra lingua. Ogni soluzione deve essere **generale e deterministica**, o passare dal modello.

C'è ancora molto da fare, ma sono molto contento di quello che ho imparato e raggiunto. (A monte e a valle del motore ci sono anche meccanismi per mitigare gli errori e per imparare i casi particolari, ma è un'altra storia.)
