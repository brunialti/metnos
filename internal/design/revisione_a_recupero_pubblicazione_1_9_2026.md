# RM-0008 — revisione A della primitiva di recupero pubblicazione

Data: 1 settembre 2026  
Commit esaminato: `82f8e4e896166ccdcf544b1c390205ec741e463a`  
Verdetto: `MODIFICHE_RICHIESTE`

L'incremento è separato dalla transizione di epoca e la forma rifiutata è
ragionevole, ma il commit non può ancora rimuovere un contenitore. Le dieci
prove dichiarate non esercitano cinque proprietà necessarie; la prova
indipendente in `internal/tools/prova_robustezza_recupero_pubblicazione_a.py`
le rende riproducibili senza toccare il negozio configurato.

## R1 — il lock globale appartiene al negozio sbagliato

`recupera_contenitore_incompleto()` passa `store_root` a `_writer_lock` ma non a
`catalog_admission_lock`. Le due prove positive B, lanciate su una copia con la
configurazione reale in sola lettura, tentano infatti il lock globale sotto la
radice configurata e sono rosse. Il lock globale e quello del contratto non
serializzano lo stesso negozio.

La correzione minima è passare la stessa radice canonica a entrambi e provare
che il lock osservato sia proprio quello della copia.

## R2 — osservare modifica lo stato

La forma ammessa consente l'assenza di `writer.lock`; anche con
`applica=False`, `_writer_lock` lo crea. L'esito dichiara di non aver rimosso,
ma l'operazione non è in sola lettura. O la forma positiva richiede un lock già
esistente, oppure ispezione e applicazione devono avere API e postcondizioni
distinte. Una modalità dichiarata osservativa non può creare oggetti.

## R3 — `ContractId` non prova la provenienza dall'inventario

Il costruttore di `ContractId` è pubblico. Un chiamante può costruire una
identità valida non presente nell'inventario, predisporre la forma ammessa e
farla rimuovere. Il tipo prova la sintassi, non la proprietà. Serve una
autorizzazione nominale e sigillata prodotta dal censimento autenticato, oppure
una verifica produttiva equivalente nello stesso confine bloccato.

## R4 — un errore tardivo lascia uno stato peggiore e non ripetibile

La funzione rimuove prima `generations/`, poi `writer.lock`, poi il contenitore.
Se l'ultimo passo fallisce, i primi due sono già durevoli e il secondo tentativo
rifiuta la forma. La prova inietta il solo errore finale e osserva esattamente
questo stato parziale.

La correzione deve dare un unico punto di impegno recuperabile: per esempio una
rinomina senza sostituzione, sullo stesso filesystem, verso un nome durevole
legato all'autorizzazione, seguita dalla pulizia separata. Qualunque soluzione
scelta deve convergere dopo un arresto in ciascun passo senza perdere la prova
di provenienza.

## R5 — `lstat` seguito da operazioni per nome non chiude la sostituzione

La seconda verifica avviene sotto i lock cooperativi, ma risolve nuovamente i
nomi. Una sostituzione sincronizzata fra `lstat` e `iterdir` fa seguire il nuovo
componente: la prova sostituisce il contenitore con un collegamento al secondo
passaggio e osserva la rimozione di `generations/` e `writer.lock` nel bersaglio
estraneo. Il rifiuto finale non annulla quella modifica.

La verifica e la modifica devono usare gli stessi handle o descrittori aperti
senza seguire collegamenti, identità stabili prima e dopo, e operazioni relative
al padre. Su Windows `os.O_DIRECTORY` non è disponibile e il lock aperto non è
rimuovibile con questa sequenza: il percorso deve usare la primitiva portabile
esistente oppure rifiutare esplicitamente la piattaforma prima di ogni effetto.

## Criterio del prossimo giro

Non serve ampliare l'obiettivo. Il prossimo commit è accettabile quando:

1. le dieci prove B restano verdi su una copia senza raggiungere il negozio
   configurato;
2. le cinque prove indipendenti passano senza indebolirne gli attesi;
3. un arresto o errore dopo ogni effetto converge a una forma riconoscibile;
4. nessuna identità priva di autorizzazione inventariale può selezionare un
   contenitore;
5. `git diff --check` è verde e commenti e docstring del codice restano in
   inglese.

La suite completa resta esclusa: sono sufficienti queste prove mirate.
