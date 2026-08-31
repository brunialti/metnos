# RM-0008 — secondo giro A sul recupero della prima pubblicazione

Data: 1 settembre 2026  
Commit esaminato: `5050b31eb8b3fc8ec49b4442fcef38553c66d9f2`  
Verdetto: `MODIFICHE_RICHIESTE`

Il checkpoint chiude realmente i cinque rilievi del primo giro nella forma
provata da B, e le sue quindici prove sono verdi. La verifica sincronizzata
indipendente trova però sette proprietà ancora mancanti. Sono concentrate nel
punto d'impegno e nell'autorità, senza richiedere un ampliamento di perimetro.

## R6 — la rinomina dichiarata senza sostituzione sostituisce

Il codice controlla l'assenza di `ritirato` e poi usa `os.rename`. Su POSIX un
nome occupato da una directory vuota fra i due passi viene sostituito. La prova
registra le identità prima e dopo e osserva la sostituzione. Il controllo
anticipato non rende `os.rename` una primitiva no-replace.

Va usata la primitiva no-replace già presente nel repository, relativa allo
stesso descrittore padre, oppure un equivalente con la stessa semantica. Una
collisione deve lasciare entrambi gli oggetti invariati.

## R7 — il punto d'impegno non è durevole e non viene ripreso

Dopo la rinomina la radice non viene sincronizzata prima della pulizia. Un
arresto immediato può quindi perdere il solo punto che il disegno chiama
durevole. Inoltre un nuovo tentativo cerca sempre il nome originario: quando
esiste soltanto `.recupero-<storage_key>`, termina con
`contenitore_assente` invece di riprendere.

Servono una registrazione durevole prima o insieme alla rinomina, `fsync` della
radice subito dopo il punto d'impegno e una matrice che distingua almeno:
originale soltanto, ritirato soltanto, entrambi, nessuno e collisione. Il
ritirato può essere riusato soltanto se identità, autorizzazione e forma
concordano; un nome deterministico da solo non prova la provenienza.

## R8 — una voce tardiva viene eliminata senza essere stata autorizzata

La forma è verificata prima della rinomina, ma la pulizia successiva itera ogni
nome e cancella tutto ciò che non si chiama `generations`. La prova aggiunge un
file subito dopo la verifica: il file viene eliminato e la funzione riporta
successo.

Il contenitore ritirato deve essere verificato di nuovo attraverso lo stesso
descrittore e la pulizia deve nominare soltanto `generations` e l'eventuale
`writer.lock`. Qualunque altra voce blocca e resta intatta.

## R9 — l'autorizzazione è costruibile e non è legata alla radice

Il test B costruisce l'autorizzazione con
`AutorizzazioneRecupero(..., R._TOKEN)`: qualunque importatore del modulo può
fare lo stesso. Inoltre la stessa autorizzazione seleziona la medesima chiave
in due `store_root` scelti dal chiamante. La provenienza dall'inventario e la
frase «il chiamante non nomina il bersaglio» non sono quindi proprietà del
confine produttivo.

Il costruttore e il sigillo devono restare dentro una chiusura privata o un
confine nominale equivalente. L'autorizzazione deve includere l'identità della
radice produttiva osservata; l'ingresso produttivo non riceve un percorso
libero. Un ingresso di prova distinto può accettare una radice temporanea senza
entrare nel grafo produttivo.

## R10 — la seconda esecuzione completa non è idempotente

Dopo una rimozione riuscita non resta una ricevuta durevole che distingua
«recupero già completato» da «oggetto mai esistito». Il secondo tentativo
termina con `contenitore_assente`. Il caso 23 della specifica richiede una
seconda esecuzione innocua; accettare genericamente l'assenza sarebbe un falso
successo, quindi serve un esito durevole legato alla stessa autorizzazione.

## Criterio del prossimo giro

Il prossimo commit è accettabile quando le quindici prove B e le sette prove
indipendenti passano insieme, senza attenuare gli attesi. In particolare:

1. nessuna collisione sostituisce un nome;
2. ogni frontiera durevole è sincronizzata e ripetibile;
3. un ritirato viene ripreso soltanto con provenienza completa;
4. una voce inattesa non viene modificata;
5. l'autorità produttiva non è costruibile da token o percorsi del chiamante;
6. una ripetizione dopo successo restituisce lo stesso esito autenticato.

La suite completa resta esclusa; `git diff --check` e queste prove mirate sono
sufficienti per il giro.
