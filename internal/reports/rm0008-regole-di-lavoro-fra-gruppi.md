# RM-0008 — regole di lavoro valide per tutti i gruppi

Queste regole non appartengono a un gruppo: valgono dal 3 al 6 e vanno ereditate
così come sono. Sono **misurate sul gruppo 2**, non ipotizzate.

Il piano di dettaglio di un gruppo si scrive **quando quel gruppo inizia**, con i
fatti del gruppo precedente in mano. Un piano scritto in anticipo va riscritto, e
riscriverlo costa più che non averlo.

## 1. Congelare una volta sola, alla fine dell'incremento

Ogni modifica alla base congelata costa un ciclo completo di rifotografia:
prodotto indietro, pubblicazione, fotografia, ripristino, pubblicazione,
verifica. Nel gruppo 2 ne sono serviti **dieci**, quasi tutti perché la base
veniva emendata appena serviva.

Accumulare le modifiche alla base, emendarla una volta, scattare **una**
fotografia. Procedura in `rm0008-gruppo3-piano-ottimizzato.md` §7.

Corollario di progettazione: molte modifiche alla base si evitano scrivendo il
codice in modo che **riceva** un'autorità invece di prendersene una nuova. Il
lettore del gruppo 3 riceve una sessione già aperta ed è costato zero cicli.

## 2. Provare il proprio contratto, non quello dei gruppi precedenti

Ciò che un gruppo precedente ha certificato non si riesercita. Le celle di un
gruppo partono dallo stato che il gruppo prima sa produrre e verificano soltanto
ciò che il gruppo nuovo rivendica.

## 3. Le due prove che pagano davvero

Misurato sul gruppo 2, i difetti veri li hanno trovati due sole cose:

- **la cella del grafo produttivo (R1)**: respinge una «porta» messa nel posto
  sbagliato. Ha avuto ragione due volte su due. Si estende dichiarando chi può
  passare, non si aggira;
- **attraversare la cosa vera invece di simularla**: ha scoperto che su Windows
  una radice storica non era leggibile affatto.

Il resto delle celle serve a non regredire, non a scoprire. Un gruppo nuovo si
chiede: qual è il mio equivalente di queste due?

## 4. Nessuno strumento diagnostico senza una decisione che dipenda da esso

Nel gruppo 2 tre strumenti su tre hanno risposto per conto proprio prima di dire
la verità: una sonda che si fermava su un'altra cosa, una lettura del token
troncata a 32 bit, un passo diagnostico che non veniva nemmeno eseguito. Uno
strumento si costruisce solo quando la sua risposta cambia una decisione, e deve
dichiarare **come** ha ottenuto la risposta.

## 5. Regola di onestà, sopra tutte le altre

«Solo i test necessari» non significa «solo i test che passano». Ciò che non si
prova va scritto come non provato, con il motivo, nel criterio di uscita del
gruppo. Una cella rinviata dice `N/A`, il gruppo che la possiede e la ragione
normativa; non resta vuota e non diventa verde.

## 6. Riesame dopo il gruppo 3: come accelerare i gruppi 4-6

Il gruppo 3 ha confermato le cinque regole e aggiunge queste regole operative.
Servono a ridurre i cicli senza ridurre le prove.

1. **Prima la causa, poi il codice.** Prima di una correzione devono essere
   scritti: punto esatto del fallimento, fatto osservato, ipotesi causale e
   risultato previsto. Se una correzione progettata non risolve, non si applica
   una seconda modifica finché una nuova prova non ha ristretto la causa.
2. **Un solo esperimento discriminante.** Una sonda è ammessa soltanto se due
   soluzioni diverse dipendono dalla sua risposta. Va rimossa nello stesso
   incremento che introduce la cella di certificazione permanente.
3. **Due cancelli, non una suite continua.** Durante lo sviluppo si eseguono le
   prove possedute dal gruppo e R1. La matrice pubblica completa si esegue quando
   l'incremento causale è completo o quando serve un sistema operativo non
   disponibile localmente. Un rosso pubblico ferma il lavoro successivo.
4. **Una fetta verticale per commit.** Ogni commit contiene simbolo produttivo,
   prova diretta, prova del percorso reale e aggiornamento del criterio di
   uscita. Non si aprono in parallelo più difetti causali dello stesso gruppo.
5. **Subentro sempre aggiornato.** Dopo ogni nuova evidenza, correzione e
   risultato pubblico si aggiornano stato, commit, ciclo, ciò che resta non
   provato e prossimo passo. Un altro agente deve poter ripartire senza
   ricostruire la cronologia.

Per il gruppo 4 questo significa: prima si scrive il suo piano sullo stato
chiuso del gruppo 3; poi si esegue la prima fetta verticale completa. Non si
iniziano F5 o F6 e non si costruisce in anticipo il loro dettaglio.

## 7. Riesame di semplificazione durante il gruppo 4

Il riesame richiesto dopo la chiusura del gruppo 3 ha prodotto una
semplificazione concreta e un limite prudenziale.

- G4-A resta separato, perche' introduce una nuova porta di esecuzione e deve
  dimostrarla su Linux e Windows prima di toccare le autorita' di firma.
- G4-B e G4-C diventano un solo incremento pubblico. L'inventario e' una prova
  derivata dai fatti di G4-B, non un secondo cambiamento produttivo. Viene
  scritto una volta sola, dopo il codice e prima del commit. Si risparmia una
  matrice completa senza perdere alcuna prova.
- I gruppi successivi non vengono uniti in anticipo. Coordinatore,
  distribuzione, passaggio reale, F5 e cancellazione F6 hanno punti di non
  ritorno diversi. Saranno riesaminati uno alla volta usando lo stato pubblico
  verde del gruppo precedente.

In ogni gruppo restano tre livelli di verifica: prove possedute durante lo
sviluppo, un attraversamento reale del percorso rivendicato e la matrice
pubblica alla fine della fetta verticale. Le suite gia' certificate non vengono
ripetute localmente se il nuovo codice non attraversa il loro confine.
