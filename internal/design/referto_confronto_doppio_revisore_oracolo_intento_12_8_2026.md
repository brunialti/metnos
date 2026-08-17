# Referto — confronto a doppio revisore dell'oracolo intento

Data: 12 agosto 2026

## Esito del confronto — stato storico

Al momento di questo confronto il punto 4 dell'ordine autorizzato era
**incompleto** e non era ancora stato prodotto né congelato un oracolo finale.
Questo stato è superato dall'«Esito finale successivo» in fondo al referto.

Roberto ha autorizzato due revisori AI separati. Ogni revisore ha classificato
in modo cieco gli stessi 120 casi e ha proposto 4 controlli aggiuntivi. I due
lavori sono stati confrontati soltanto dopo la loro chiusura indipendente.

Questa è una doppia revisione AI, non una revisione umana indipendente. Il
limite va mantenuto visibile in ogni futura attestazione dell'oracolo.

## Risultati verificati

- entrambi i lavori contengono 120 casi unici e 4 controlli unici;
- entrambi superano i propri controlli di forma con errore 0;
- 102 casi su 120 hanno lo stesso `expected`;
- 18 casi divergono;
- in 13 dei 18 cambia la radice principale;
- 16 divergenze sembrano applicazioni differenti di regole già approvate;
- il confronto prudente porta a Roberto tre possibili scelte nuove, descritte
  sotto;
- le due serie di controlli non possono essere unite automaticamente: vi sono
  duplicazioni semantiche e sovrapposizioni tematiche.

## Decisione intervenuta e approvata

Per una richiesta composta che contiene una clausola indispensabile fuori dal
registro, l'intera richiesta è `unrepresentable/outside_registry`. Non si
conserva come verità eseguibile il solo sottografo parziale.

## Scelte emerse nel confronto — ora chiuse

### Caso 38 — ambiguità e lacuna del registro insieme

Richiesta: «cerca tutte fatture anthropic e metti data e importo su
spreadsheet».

Nel confronto restava da stabilire quale motivo principale registrare quando la sorgente è ambigua ma
tutte le letture plausibili richiedono comunque un'operazione indispensabile
fuori registro: `ambiguous_intent` oppure `outside_registry`.

Decisione di Roberto: per la versione congelata attuale prevale
`outside_registry`. La decisione è esplicitamente **temporanea**: descrive una
lacuna del registro v0.1, non un limite definitivo del prototipo.

Direzione futura indicata da Roberto, non ancora autorizzata come modifica:
valutare una pipeline di estrazione testuale sul modello di `/opt/giorgio2` e
il concetto di template per lettori di file PDF. Questo richiederà una scelta e
una versione futura separata del contratto/registro; non dovrà cambiare in modo
silenzioso l'oracolo congelato v0.1.

### Caso 84 — conteggio su più corpus

Richiesta: «Elenca i corpus che hanno un indice immagini unificato e mostra
quante foto contiene ciascuno».

Nel confronto restava da stabilire se `get/images` possedesse anche lo scope plurale e l'enumerazione
dei corpus, se serve una discovery esplicita, oppure se il significato esatto
non è rappresentabile dal registro congelato.

Chiarimento di Roberto e verifica successiva: Metnos indicizza le immagini con
scansione ricorsiva; `get_images_indices`, senza `base_path`, enumera tutti gli
indici `unified` materializzati e restituisce separatamente `base_path` e
`n_entries` per ciascuno. Entrambi i revisori hanno confermato la stessa
semantica nell'implementazione e nel manifest correnti.

Il caso è quindi rappresentabile con il solo nodo `get/images`. `find/dirs` è
superfluo e meno preciso, perché trova directory e non specificamente corpus
dotati di indice. La precedente proposta `outside_registry` era un errore di
analisi, non una nuova scelta di Roberto.

Nota avversariale: il `catalog_snapshot` del banco è arretrato rispetto al
manifest corrente. Resta congelato e non va corretto retroattivamente; lo
scarto va mantenuto visibile nella provenienza dell'oracolo.

Chiarimento architetturale successivo di Roberto: l'elenco corrente degli
indici materializzati non esaurisce il concetto futuro di registro dei corpus.
Il registro dovrà essere la fonte percorsa dal task notturno di
reindicizzazione e il task dovrà anche riconciliarlo quando directory o indici
vengono cancellati senza passare da Metnos. Se il corpus esiste ma manca
l'indice, il caso richiede ricostruzione/riallineamento dell'indice. Se manca o
non è raggiungibile la directory, il registro deve essere riconciliato senza
cancellare automaticamente la voce.

Decisione successiva di Roberto: se la directory non è raggiungibile, il task
notturno non elimina la voce dal registro; la marca come `indisponibile`. La
regola evita di confondere una cancellazione effettuata fuori da Metnos con un
supporto temporaneamente scollegato. Non è autorizzata alcuna rimozione
automatica della voce.

### Caso 113 — selezione di campi già strutturati

Richiesta: «leggi gli eventi di questa settimana, estrai titolo e orario e
crea un foglio di calcolo».

Nel confronto restava da stabilire se selezionare campi già restituiti da `read/events` fosse una
semplice proiezione dati accettata da `create/files`, oppure un'operazione
esplicita che richiede una rotta propria.

Decisione di Roberto: la selezione di campi già strutturati è una proiezione
dati e non richiede una nuova rotta. Il caso è quindi rappresentabile come
`read/events` seguito da `create/files`. Il confine è stretto: selezionare o
rinominare campi esistenti è ammesso; interpretare testo libero, calcolare o
derivare nuovi valori resta una trasformazione distinta e deve essere coperta
esplicitamente dal registro.

## Revisione avversariale del confronto — stato storico

I rilievi seguenti descrivono il momento precedente all'adjudicazione e sono
superati, per lo stato finale, dalla sezione conclusiva:

- Errore 0 nei due verificatori significa solo completezza e forma corrette;
  non dimostra correttezza semantica.
- I 18 disaccordi impediscono di promuovere uno dei due lavori a verità.
- Per prudenza sono presentati tutti e tre i casi indicati almeno da un
  revisore come possibile nuova policy, anziché scegliere quale revisore abbia
  ragione.
- I 4 controlli finali dovranno essere scelti dopo l'adjudicazione, evitando le
  duplicazioni tra le due proposte.

## Integrità e vincoli

- banco congelato, campione canonico, registro e impronte salvate: integri;
- nessuna nuova misura GPU;
- nessun riavvio di servizio;
- nessun commit;
- nessuna modifica attribuibile a questo lavoro in produzione;
- il worktree di produzione era già sporco per modifiche precedenti e resta
  pertanto non attestabile come globalmente pulito.

## Prossimo passo allora previsto — completato

Il passo allora previsto era adjudicare i 18 disaccordi, selezionare 4
controlli non duplicati, costruire l'oracolo con freeze e verificatore,
introdurre test di mutazione e ciclare fino a errore 0. Tutte queste attività
sono state completate.

## Esito finale successivo — punto 4 completato

L'oracolo canonico contiene 120/120 casi unici: 102 accordi esatti fra i due
revisori e 18 adjudicazioni autorizzate. Le radici sono 84
`operation_graph`, 2 `system_control` e 34 `unrepresentable`. I quattro
controlli finali coprono composto fail-closed, vista multi-corpus
`get/images`, proiezione strutturata e similarità da foto con `find/persons`;
si aggiungono ai 34 esistenti per il totale autorizzato di 38. Il freeze
sigilla 23 fonti.

Il verificatore termina con `error_count=0`. La suite ufficiale respinge
107/107 mutazioni negative e accetta 6/6 riordini positivi; il replay
indipendente mirato termina 30/30+6/6 e il piano avversariale 32/32. Sono
chiuse D-01 (chiavi JSON duplicate), D-02 (numeri non finiti), D-03 (tipi
esatti e metadati chiusi) e D-04 (sei liste di autorità e lista base esatte,
uniche e indipendenti dall'ordine). L'oracolo è rimasto byte-identico durante
questo rafforzamento.

La provenienza resta doppia revisione AI indipendente e cieca fino alle
consegne, non revisione umana. Il risultato non è prova di accuratezza
universale o validità oltre i 120 casi, 38 controlli e registro 0.1; il freeze
è un sigillo deterministico, non una firma esterna. L'accordo dei vecchi
bracci non è stato usato come gold.

Le decisioni finali sono quelle già descritte sopra, più la regola fail-closed
per l'intero composto. Il caso 38 resta temporaneamente
`unrepresentable/outside_registry`; pipeline testuale per fatture e possibili
template PDF sono una direzione futura non retroattiva. Il caso 84 resta il
solo `get/images`, vista degli indici materializzati e non registro persistente.
Il caso 113 resta `read/events -> create/files`, con selezione e rinomina di
campi strutturati come proiezione.

Il futuro registro dei corpus sarà persistente e indipendente dagli indici e
guiderà la reindicizzazione; una voce non raggiungibile sarà marcata
`indisponibile`, non eliminata automaticamente. Registro futuro e stato di
indisponibilità non fanno parte degli `expected` v0.1. Il
`catalog_snapshot`, autorità congelata con 96 executor, resta dichiaratamente
arretrato rispetto agli 83 manifest correnti.

Banco, campione, registro e produzione sono rimasti integri; nessuna misura
GPU, nessun riavvio, nessun commit. I punti 1–4 sono completati. Il primo punto
incompleto è il punto 5 dell'ordine autorizzato; non è iniziato in questa
chiusura.
