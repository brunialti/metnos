# Referto del blocco del punto 4 — autorità dell'oracolo

Data: **12 agosto 2026**.

Stato: **le tre regole di giudizio sono approvate; serve una nuova scelta di Roberto sulla fonte delle risposte corrette**.

## Esito semplice

Le 120 richieste congelate esistono, ma le relative risposte corrette originali
non sono state congelate insieme al campione. I file correnti del vecchio
benchmark appartengono a un insieme diverso e coincidono testualmente soltanto
con **3** richieste su 120.

Gli overlay di adjudicazione aggiungono copertura puntuale, ma l'unione di
ogni risposta indipendente riutilizzabile copre soltanto **3** richieste.
Restano quindi **117** richieste senza una risposta d'oro indipendente.

I controlli disponibili sono **34**, mentre l'ordine ne richiede **38**:
ne mancano **4**. Nessuna delle fonti esistenti contiene già la nuova radice
`operation_graph | system_control | unrepresentable`.

Il controllo di questa diagnosi termina con **errori = 0**. Questo significa
che il buco è provato e riproducibile; non significa che l'oracolo sia completo.

## Perché non è corretto usare le vecchie uscite

I tre bracci c10, c11 e controllo sono risposte del modello. Se il modello
sbaglia nello stesso modo in tutti e tre, il loro accordo resta sbagliato.
Usare quell'accordo come risposta corretta renderebbe la valutazione circolare:
il sistema giudicherebbe se stesso.

Le tre decisioni appena approvate da Roberto regolano come assegnare i voti,
ma non inventano quale sia il significato corretto di ogni frase.

## Scelta necessaria

### A — oracolo indipendente rigoroso

Revisionare da zero le **117** richieste scoperte, conservare le 3
risposte già riutilizzabili e creare i **4** controlli mancanti. Ogni
risposta viene poi verificata contro contratto e registro congelati.

È la raccomandazione di Codex: richiede più lavoro, ma produce un vero metro
di giudizio.

### B — oracolo provvisorio per consenso

Usare come etichetta l'accordo dei vecchi bracci e revisionare soltanto i
disaccordi. È più rapido, ma è circolare e non può essere chiamato oracolo
indipendente né sostenere un verdetto finale di qualità.

## Revisione cerca-difetti

### Attacchi

1. Il confronto testuale potrebbe non riconoscere parafrasi equivalenti fra
   vecchio gold e campione congelato.
2. Gli overlay puntuali potrebbero sovrapporsi alle poche corrispondenze del
   benchmark corrente.
3. I 34 controlli esistenti possono essere corretti ma non coprono i quattro
   nuovi casi richiesti.
4. Un consenso unanime dei bracci può sembrare abbastanza affidabile da usare
   come scorciatoia.
5. “Errori = 0” dell'audit potrebbe essere scambiato per completezza
   dell'oracolo.

### Risposte

1. Senza una mappatura revisionata, una parafrasi non può trasferire
   automaticamente un'etichetta; farlo sarebbe una nuova adjudicazione.
2. La copertura usa l'unione per indice e non somma due volte la stessa
   richiesta.
3. Il deficit di quattro resta esplicito e non viene riempito con copie.
4. L'accordo è evidenza diagnostica, non verità indipendente.
5. Audit e oracolo hanno stati diversi: l'audit è coerente; l'oracolo resta
   bloccato.

### Esito

Non esiste un percorso onesto verso un oracolo completo senza scegliere
l'autorità delle nuove adjudicazioni. Per la regola di Roberto, Codex non
assume da solo questa scelta.

## Stato

- punto 4: **incompleto**;
- tre regole di giudizio: **approvate**;
- audit delle fonti: **errori 0**;
- risposte d'oro indipendenti mancanti: **117**;
- controlli mancanti: **4**;
- GPU: **0**;
- produzione e banco: **intatti**;
- commit: **nessuno**.
