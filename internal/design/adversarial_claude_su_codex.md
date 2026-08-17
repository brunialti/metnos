# Avversariale — Claude contro l'analisi di codex (11/8/2026)

Bersaglio: `analisi_codex_soluzione_intento.md`. Non è un riassunto: è un attacco.
Documento gemello: `adversarial_codex_su_claude.md`.

Premessa di onestà: quell'analisi è più fondata della mia su un punto decisivo.
Ha ripartito tutti e 46 i casi per causa primaria sommando esattamente a 46, e
soprattutto ha **eseguito** il proiettore congelato V24.1 sui frame invece di
ragionarci sopra. Le obiezioni che seguono non toccano quel lavoro.

## O1 — Il programma ottimizza la metà del problema dove i dizionari non ci sono

**È l'obiezione grave.** L'obiettivo dichiarato è cancellare dizionari legati alla
lingua senza perdere qualità. L'analisi di codex non chiede mai **dove stanno i
dizionari**: parte dai 46 errori di instradamento e costruisce un programma per
azzerarli tutti.

Ma le cinque scorciatoie lessicali misurate stanno **tutte** su richieste a zero o
una operazione. **Nessuna** scatta su una richiesta composta. E l'accordo fra i due
percorsi è 76% sulle richieste a una operazione contro 12-26% sulle composte: i 46
casi vivono in massima parte proprio dove i dizionari non intervengono.

Conseguenza: il §13 pretende parità **ovunque** prima di cancellare qualsiasi cosa,
quindi lega la cancellazione dei dizionari alla risoluzione di un problema —
la decomposizione del composto — che con i dizionari non ha rapporto. Non sto
dicendo che il composto non vada risolto. Sto dicendo che **metterlo come
precondizione rende l'obiettivo dichiarato irraggiungibile per costruzione**.

## O2 — La precondizione è riannotare 103 manifest, senza una sola tappa misurabile prima della fine

Il §11.1 chiede che ogni capacità dichiari relazione, ruoli, tipi di paziente,
sorgente, destinazione e destinatario, cardinalità, titolarità, durata,
prerequisiti, compatibilità produttore-consumatore, ciclo di vita, consenso,
criticità, regola di proiezione e stato di revisione. Oggi, per ammissione della
stessa analisi, sono **0 su 103**. Ognuno va poi rifirmato (§7.10).

È il cambiamento più grande mai proposto in questo filone, e il suo primo numero
arriva **soltanto alla fine**. Il registro di questo lavoro su cambiamenti grandi
e non misurati per pezzi: riscrittura compressa del prompt, riorganizzazione per
dimensioni, contratto di ruolo in tre versioni, rimozione di `TIE_BREAK`,
riscrittura di `ONTOLOGY` in due forme. Otto tentativi, **zero vincite**. L'unica
cosa che ha guadagnato era piccola, isolata e misurabile in ventiquattro minuti.

Una proposta che non produce un numero prima di mesi di lavoro non è confutabile,
e su questo tavolo la confutabilità è l'unica cosa che ha funzionato.

## O3 — L'unica regola deterministica che l'analisi ha davvero eseguito spara di traverso

Il replay del proiettore V24.1 è la parte migliore del documento, e dice questo:
**ripara 2 casi (39, 100) e ne rompe 1** (24: `find/issues` diventa `find/urls`),
più due spostamenti che non arrivano alla rotta giusta (76, 82). Rapporto 2 a 1
su una regola progettata come «autorità del carrier», cioè esattamente il tipo di
regola totale e senza parole che il §11.3 elenca come ammessa.

Il §11.3 propone **nove** ripari di quella famiglia e li giustifica perché
«totali e indipendenti dalla lingua». La prova interna al documento mostra che
totale non implica corretto: una regola totale può essere totalmente troppo
larga. Ognuno dei nove va misurato da solo, e il documento non lo dice.

## O4 — Il numero 31 regge l'intero verdetto ed è un giudizio a passata singola

«In almeno 31 dei 46 casi l'informazione necessaria è assente o è già
semanticamente sbagliata» è la frase da cui discende tutto: niente prompt, niente
V23lite in produzione, nessuna cancellazione. Ma 31 nasce da una lettura dei
frame fatta una volta sola, da un solo agente, con una partizione a **una causa
primaria per caso** scelta dallo stesso agente. Non è dichiarato il criterio che
distingue «informazione assente» da «informazione presente e usata male» — ed è
proprio la distinzione su cui poggia la conclusione di irreparabilità.

Chiedo la stessa cosa che è stata chiesta a me due volte oggi: una verifica
avversariale su quei 31, prima che diventino la base di un programma.

## O5 — L'asimmetria della regola di giudizio non è dichiarata dove conta

La regola «se sbagliano entrambi, o il nuovo è solo meno sbagliato, conta contro
il nuovo» è dichiarata nel referto ma poi il risultato circola come **«9 contro
46»**, che si legge come un confronto simmetrico e non lo è. Nell'analisi il
numero 46 viene usato come denominatore in tutte le tabelle senza mai ricordare
quanti di quei casi sono «sbagliano entrambi». Almeno tre lo sono per ammissione
esplicita (24, 84, 91). Il verdetto non cambia; la sua forza retorica sì.

## O6 — Ha proposto la cosa giusta ignorando che è già stata misurata, e a favore

Il §11.2 dice che il modello non deve emettere campi derivabili. È il punto più
forte del documento, e ha già una misura che nessuno dei due aveva guardato — i
cicli del 10-11/8 sulle 40 query:

| ciclo | campi tolti dall'uscita | valide | mediana |
|---|---|---|---|
| c8 | nessuno (riferimento) | 40, 39, 40 | 6.480 ms |
| c10 | arco, id, àncora | **40/40** | 3.895 ms |
| c11 | i quattro specchi, `role` incluso | **40/40** | **3.478 ms** |
| c12 | specchi + ruolo nella grammatica | **40, 40, 40** | 5.784 ms |

Togliere dall'uscita i campi che il codice può derivare ha tenuto la qualità e
**tagliato la latenza del 40%**. È misurato sulle 40 di messa a punto, quindi va
rifatto sulle 120 — ma è l'unica parte del programma di codex che ha già un
numero, ed è favorevole. Va promossa a **primo passo**, non lasciata dentro un
piano che comincia da 103 manifest.

Nota che la distinzione che il documento fa — tagliare **campi d'uscita** sì,
tagliare **prosa del prompt** no — è esattamente quella giusta e ha dati da
entrambi i lati: i campi tolti tengono 40/40, la prosa tagliata del 41% fece
33/40.

## Verdetto

L'analisi è tecnicamente solida e la sua diagnosi dei 46 è la migliore cosa
prodotta oggi; ma come **programma** è irrefutabile nei tempi e mette come
precondizione un problema — il composto — che non ha rapporto con l'obiettivo
dichiarato.

Ne salvo tre pezzi e ne rifiuto uno: salvo la diagnosi per causa, salvo il taglio
dei campi derivabili (che ha già un numero), salvo il divieto di regressione su
undo e approvazione; rifiuto la sequenza in otto passi del §13 come condizione
per cancellare la prima voce di dizionario.
