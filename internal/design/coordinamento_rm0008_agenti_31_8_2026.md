# RM-0008 — coordinamento autonomo fra due agenti

Data: 31 agosto 2026  
Stato: proposta operativa pronta all'uso  
Ancoraggio iniziale: `2dc42db1` su `rm0008/diagnosi-avvio`

## 1. Scopo

Due agenti devono avanzare in parallelo su RM-0008 senza usare Roberto come
centralino, senza modificare contemporaneamente gli stessi file e senza
duplicare lo stato del progetto.

Il coordinamento usa Git, che e' gia' il registro durevole del lavoro. Non
introduce un servizio, un processo residente o un secondo sistema di stato.
Ogni passaggio e' legato a un commit esatto e resta ricostruibile dopo una
interruzione.

Il punto di partenza e' gia' misurato:

- G6 e' chiuso;
- i rilievi C18 e C19 sono chiusi;
- C1 e' chiusa da O17: il server completo parte e serve turni reali in copia;
- resta da progettare e attuare la transizione append-only a nuova epoca prima
  del passaggio produttivo F4;
- produzione, servizi, permessi e radice di nascita non vengono toccati da
  questa proposta.

## 2. Una sola fonte per ogni informazione

| Informazione | Fonte unica |
|---|---|
| ordine e criteri di RM-0008 | `internal/roadmap/RM-0008-porta-unica-nascita-executor.md` |
| fatti della diagnosi e O15/O17 | `internal/design/diagnosi_avvio_nascita_31_8_2026.md` |
| protocollo fra agenti | questo documento |
| contenuto di una consegna | commit indicato dalla consegna |
| stato della fase | roadmap, aggiornata dall'integratore di turno dopo una barriera accettata da entrambi |

I messaggi di coordinamento non ricopiano analisi, risultati o elenchi. Portano
solo un riferimento al commit che li contiene. In questo modo non esistono due
versioni concorrenti dello stesso fatto.

## 3. Ruoli e separazione fisica

I due agenti sono paritetici nelle decisioni. Entrambi progettano, sviluppano
codice, scrivono prove e revisionano il lavoro dell'altro. Nessuno e' il
revisore permanente dell'altro.

Il carico non deve pero' essere simmetrico. L'agente con piu' risorse prende
scansioni ampie, sintesi trasversali, integrazione e verifiche costose. L'altro
riceve porzioni di codice circoscritte, contesto gia' selezionato e prove
mirate. Questa differenza riduce il tempo senza ridurre il peso del suo giudizio
tecnico.

Ogni unita' assegna tre funzioni:

- **agente A**: possiede una parte del disegno, del codice e delle prove;
- **agente B**: possiede una parte diversa del disegno, del codice e delle
  prove;
- **integratore di turno**: uno dei due unisce meccanicamente soltanto commit
  accettati da entrambi e aggiorna la roadmap. Il ruolo alterna fra le unita' e
  non concede autorita' tecnica superiore.

Ogni agente usa un ramo e un worktree distinti. Per la prossima unita':

| agente | ramo | worktree | perimetro iniziale |
|---|---|---|---|
| A, piu' risorse | `codex/rm0008-f4-transizione` | `/tmp/metnos-rm0008-f4-transizione` | nucleo della transizione, analisi trasversale, prove di modulo e revisione del perimetro B |
| B, risorse minori | `rm0008/f4-verifica-epoca` | `/tmp/metnos-rm0008-f4-verifica` | adeguamento circoscritto dei 12 legami, prove di accettazione e revisione del perimetro A |

Il ramo `rm0008/diagnosi-avvio` resta il riferimento chiuso della diagnosi e
non viene usato come tavolo di lavoro concorrente.

Prima di scrivere, il primo commit di ogni unita' dichiara i percorsi posseduti.
Un percorso puo' appartenere a un solo ruolo. Un trasferimento richiede un
commit esplicito del proprietario corrente; fino a quel commit l'altro ruolo lo
tratta in sola lettura.

## 4. Messaggi di sincronizzazione

Lo stato viaggia nei trailer dei commit. Non serve un registro condiviso da
modificare, quindi i due agenti non possono creare un conflitto nel coordinarsi.

Ogni checkpoint usa questi campi:

```text
RM0008-Unita: F4-EPOCA-01
RM0008-Ruolo: agente-a | agente-b | integratore-di-turno
RM0008-Stato: OFFERTA | PRESA | PRONTA | MODIFICHE_RICHIESTE | ACCETTATA | INTEGRATA | BLOCCATA
RM0008-Ancora: <commit esatto esaminato>
RM0008-Percorsi: <elenco compatto dei percorsi posseduti>
RM0008-Prova: <percorso del rapporto o comando mirato, senza ricopiare l'esito>
RM0008-Ambito: disegno | codice | prove | roadmap | passaggio
```

Regole:

1. `OFFERTA` apre l'unita', assegna i percorsi e indica l'ancora comune.
2. `PRESA` conferma che il secondo agente ha letto la stessa ancora e accetta
   il proprio perimetro.
3. `PRONTA` rende revisionabile un commit immutabile. L'autore non lo modifica
   mentre viene verificato: eventuali correzioni nascono in commit
   successivi.
4. `MODIFICHE_RICHIESTE` deve indicare una proprieta' violata e una prova
   riproducibile. Osservazioni solo stilistiche non fermano l'unita'.
5. `ACCETTATA` nomina il commit preciso verificato. Se la testa cambia,
   l'accettazione non si trasferisce automaticamente.
6. `INTEGRATA` viene emesso solo dall'integratore di turno dopo l'accettazione
   incrociata e le prove mirate sulla composizione dei due rami.

Ogni stato `PRONTA`, `MODIFICHE_RICHIESTE`, `ACCETTATA` o `INTEGRATA` viene
spinto nel deposito locale di recupero. I rami di sviluppo non vengono mai
spinti nel deposito GitHub pubblico: quel deposito riceve soltanto
l'esportazione curata, dopo le relative verifiche di pubblicabilita'. I
checkpoint intermedi restano piccoli e incrementali; non si riscrive la storia
e non si comprime la sequenza prima della convergenza.

## 5. Sincronizzazione senza intervento di Roberto

L'avvio iniziale dei due processi appartiene all'orchestrazione della sessione,
non al protocollo Git e non concede autorita' tecnica. Dopo l'avvio, nessun
agente deve disporre di un canale diretto verso l'altro: ciascuno pubblica il
proprio checkpoint nel deposito locale di recupero e osserva il ramo dell'altro.
Il testo di avvio e' fisso:

```text
Leggi integralmente internal/design/coordinamento_rm0008_agenti_31_8_2026.md.
Lavora sull'unita' indicata dal piu' recente trailer RM0008-Unita.
Verifica l'esatto RM0008-Ancora, opera solo nei tuoi RM0008-Percorsi e pubblica
il prossimo stato nel deposito locale di recupero con i trailer prescritti.
Osserva il ramo dell'altro agente prima di ogni incremento e mentre attendi una
barriera. Non chiedere a Roberto di inoltrare messaggi ordinari. Fermati prima
di qualunque modifica al sistema in funzione.
```

Quando entrambi gli agenti sono attivi, ciascuno controlla il ramo dell'altro
alla fine di un proprio incremento e prima di iniziarne un altro. Non usa una
testa letta in precedenza: rilegge sempre il commit nominato dal trailer. Se ha
esaurito il lavoro indipendente e attende una revisione, osserva il riferimento
locale a intervalli brevi e limitati; un cambiamento avvia subito il giro
successivo, l'assenza di cambiamenti non produce nuovi commit.

Se un agente non pubblica un nuovo checkpoint:

1. l'altro continua soltanto sulle attivita' indipendenti gia' assegnate;
2. controlla se esiste un nuovo commit o un lavoro ancora in corso;
3. se non esiste, l'orchestrazione della sessione puo' riavviarlo una volta con
   la stessa ancora, senza creare un secondo processo concorrente;
4. non prende possesso dei file dell'altro e non crea una seconda
   implementazione;
5. segnala a Roberto il blocco solo se esaurisce tutto il lavoro indipendente e
   il mancato riscontro impedisce la barriera successiva.

## 6. Aggiornamento a Roberto

Durante il lavoro attivo, l'agente A invia ogni 30 minuti un aggiornamento
breve costruito leggendo le teste correnti di entrambi i rami:

```text
Fatto: <checkpoint conclusi da entrambi dall'ultimo aggiornamento>.
Manca: <prossima barriera e lavoro necessario per raggiungerla>.
```

Si aggiunge `Blocco:` soltanto se esiste un impedimento reale. Nessun log,
comando, richiesta di attivazione o scelta tecnica viene trasferito a Roberto.
Se nei 30 minuti non esiste un nuovo checkpoint, l'aggiornamento indica quale
lavoro e' ancora in corso senza dichiarare avanzamenti non provati. Gli
aggiornamenti terminano quando l'unita' e' chiusa o sospesa.

## 7. Barriere

| barriera | condizione | effetto |
|---|---|---|
| B0 — avvio | `OFFERTA` e `PRESA` sulla stessa ancora, percorsi disgiunti | i due ruoli lavorano in parallelo |
| B1 — disegno | specifica della nuova epoca e censimento dei 12 legami entrambi `ACCETTATA` | puo' iniziare il codice di prodotto |
| B2 — composizione | codice, prove di modulo e prova indipendente verdi sul commit integrato | puo' iniziare la preparazione F4 |
| B3 — chiusura fase | suite completa eseguita una sola volta, confronto regressioni e documentazione allineata | la fase puo' essere dichiarata chiusa |
| B4 — sistema in funzione | checklist F4 firmata, stato salvabile e autorita' esplicita gia' registrata | esecuzione seriale, un solo agente operativo |

La suite completa non gira durante gli incrementi. Come richiesto, viene usata
solo a B3, prima della chiusura della fase. Prima si usano prove mirate veloci e
non vacue.

## 8. Revisioni incrociate

Le revisioni incrociate avvengono quando cambia un artefatto significativo, non
a intervalli di tempo. Non duplicano le prove e non fermano attivita' che
restano indipendenti.

Metodo e cadenza sono responsabilita' dei due agenti. Roberto non deve
programmare giri, scegliere chi parte o riattivare un agente. L'integratore di
turno decide quando chiedere un controllo ulteriore usando tre segnali concreti:

- cambia un contratto o un confine condiviso dai due rami;
- una prova smentisce un presupposto usato dall'altro ramo;
- una modifica sposta ordine, criterio o stato nella roadmap.

In assenza di questi segnali si usano soltanto i quattro momenti minimi sotto,
accorpando nello stesso giro gli artefatti gia' stabili. Se uno dei segnali
compare, la revisione parte al checkpoint incrementale successivo, senza
attendere una richiesta esterna.

| momento | cosa rivede l'agente A | cosa rivede l'agente B | uscita |
|---|---|---|---|
| R1 — prima di B1 | classificazione e disegno di adeguamento dei 12 legami | protocollo di epoca, stati e recupero | due `ACCETTATA` sulla stessa coppia di commit |
| R2 — prima di B2 | codice di adeguamento e prove di accettazione del perimetro B | nucleo della transizione e prove di modulo del perimetro A | rilievi riproducibili oppure due `ACCETTATA` |
| R3 — prima di B3 | delta integrato, piano della suite totale e documentazione | stesso delta partendo dai criteri RM-0008 | autorizzazione congiunta alla verifica di chiusura |
| R4 — prima e dopo B4 | checklist operativa e ricevute prodotte | precondizioni prima, postcondizioni dopo | doppio riscontro sul commit e sull'esito reale |

Durante una revisione ciascun ruolo legge il lavoro dell'altro ma non lo
modifica. I rilievi passano con `MODIFICHE_RICHIESTE`; l'autore corregge nel
proprio ramo. Questo conserva proprieta' chiare e rende ogni correzione
attribuibile a un solo incremento.

### Variazioni della roadmap

Una variazione della roadmap non viene mescolata a codice o prove. Usa una
unita' dedicata, `RM-VARIAZIONE-<numero>`, con `RM0008-Ambito: roadmap` e un
commit che modifica solo la roadmap e gli eventuali riferimenti strettamente
necessari.

Prima dell'integrazione entrambi gli agenti controllano:

1. che la variazione distingua fatti osservati, lavoro residuo e nuova regola;
2. che non dichiari chiusa una barriera senza i commit e le prove richiesti;
3. che non riduca un criterio gia' accettato per adattarlo al codice corrente;
4. che non replichi dettagli gia' autorevoli in diagnosi, rapporto o codice;
5. che l'ordine delle fasi resti coerente con le dipendenze reali.

Una correzione puramente fattuale dello stato, sostenuta da una barriera gia'
accettata, richiede il riscontro del secondo agente ma non Roberto. Una
variazione che cambia contratto, autorita', criteri di chiusura o ordine
normativo richiede prima due `ACCETTATA` sul commit proposto e poi la decisione
di Roberto. Fino a quella decisione la roadmap vigente resta autorevole e il
codice non anticipa la proposta.

## 9. Ripartizione della prossima unita'

### Tratto parallelo 1 — disegno ed evidenze

L'agente A prepara il nucleo del protocollo append-only a nuova epoca: stati,
proprietario normativo, ingresso, ripresa dopo interruzione, ripetibilita',
conservazione dell'epoca precedente e ritorno controllato.

L'agente B classifica le 12 dipendenze vive di O15 e disegna il loro adeguamento.
Per ognuna indica se deve puntare alla nuova epoca, restare legata a quella
storica o cessare di essere corrente, e prova che nessun ripuntamento meccanico
riduca le guardie esistenti.

I due prodotti sono in file distinti. B1 richiede che la specifica citi il
rapporto dei legami e che entrambi gli agenti accettino il comportamento per
tutte e 12 le dipendenze. B1 congela anche le interfacce comuni necessarie ai
due perimetri di codice.

### Tratto parallelo 2 — costruzione e accettazione

Dopo B1 entrambi sviluppano codice in parallelo. L'agente A implementa il punto
comune e le relative prove di modulo. L'agente B implementa l'adeguamento dei
legami nel proprio perimetro e prepara, in file distinti, i casi di accettazione:

- zero, una e molte dipendenze correnti;
- ripetizione dopo una interruzione in ogni passo durevole;
- epoca precedente leggibile ma non modificata;
- collisione con contenuto diverso negata senza sostituzione;
- seconda esecuzione idempotente;
- impossibilita' di dichiarare F4 con una dipendenza non classificata.

Le prove dell'agente B non copiano il nucleo dell'agente A nei propri attesi:
osservano postcondizioni e ricevute. Le prove dell'agente A verificano il
contratto del nucleo senza incorporare le regole specifiche dei singoli legami.
Entrambi revisionano il codice dell'altro, ma ogni correzione viene applicata
dal proprietario del relativo percorso.

### Tratto seriale — integrazione e passaggio F4

L'integratore di turno unisce soltanto commit `ACCETTATA`, esegue le prove
mirate sulla composizione e pubblica `INTEGRATA`. Dopo B3, installazione,
commutazione dei servizi, cambio dell'imposizione, riavvio e cicli reali sono
seriali. L'altro agente osserva e verifica; non opera contemporaneamente sul
sistema.

## 10. Risoluzione dei disaccordi

Un disaccordo viene risolto nell'ordine seguente:

1. norme invarianti del repository;
2. contratto RM-0008 e disposizioni del gruppo 2;
3. comportamento del codice corrente;
4. prova riproducibile che distingue le due ipotesi;
5. soluzione piu' semplice che conserva tutte le proprieta' precedenti.

Il ciclo e' `MODIFICHE_RICHIESTE` → nuovo `PRONTA` → nuova verifica, sempre su
commit esatti. Non esiste un numero artificiale di giri: si chiude quando
entrambi i ruoli nominano lo stesso commit `ACCETTATA`.

Roberto viene coinvolto soltanto quando il disaccordo richiede una nuova
autorita', cambia il comportamento richiesto oppure autorizza un'operazione sul
sistema in funzione non gia' coperta. Non viene coinvolto per scegliere una
soluzione tecnica fra alternative che codice, contratto e prove possono
distinguere.

## 11. Criterio di riuscita del coordinamento

Il meccanismo funziona se:

- Roberto non deve copiare prompt, commit o osservazioni fra gli agenti;
- entrambi gli agenti producono codice e prove su perimetri distinti;
- nessun file viene modificato in parallelo da entrambi;
- ogni verdetto e' legato al commit realmente esaminato;
- una interruzione riparte dall'ultimo commit senza ricostruzioni manuali;
- nessuna attivita' sul sistema in funzione nasce dal solo protocollo di
  coordinamento;
- la copertura totale viene pagata una sola volta, alla chiusura della fase.
