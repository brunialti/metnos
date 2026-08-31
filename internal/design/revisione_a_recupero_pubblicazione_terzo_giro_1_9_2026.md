# RM-0008 · revisione A del recupero pubblicazione · terzo giro

Data: 1 settembre 2026  
Agente: A  
Ancora revisionata: `80b26409`  
Stato: **MODIFICHE_RICHIESTE**

## Esito breve

Le 23 prove di B sono verdi e chiudono correttamente i sette rilievi del giro
precedente. Restano quattro difetti riprodotti da una sonda indipendente. Non
richiedono un nuovo disegno: si chiudono con una ricevuta a due stati, legata
all'identità del contenitore, una ripresa monotona della pulizia e una sola porta
produttiva di emissione dell'autorizzazione.

Sonda:

```text
internal/tools/prova_robustezza_recupero_pubblicazione_a.py \
  /tmp/metnos-rm0008-b-review-80b26409
```

Esito osservato:

```text
FAIL authorization is inventory-only the module-level seal emitter minted an authorization
FAIL partial cleanup is resumable retry did not resume after partial cleanup: FileNotFoundError
FAIL receipt binds the exact container a receipt prepared for the original container removed a different retired directory (...)
FAIL receipt distinguishes preparation from commit a pre-rename receipt reported completion after the rename had failed
```

## R11 — l'emettitore del sigillo è ancora raggiungibile

`_emetti_sigillo` è un attributo del modulo e restituisce proprio l'oggetto
accettato da `AutorizzazioneRecupero`. Un importatore può quindi costruire una
autorizzazione senza passare dall'inventario. Anche
`_autorizzazione_di_prova`, nello stesso modulo runtime, è una seconda porta che
accetta un'identità libera.

Correzione minima: la funzione produttiva che interroga l'inventario deve
chiudere sul sigillo ed essere l'unica capace di emettere l'oggetto. Il modulo
non deve esportare né l'emettitore né una porta fixture. Le prove possono
sostituire l'inventario con una fixture e attraversare la stessa porta
produttiva.

## R12 — una sospensione durante la pulizia non è riprendibile

Se l'esecuzione si ferma dopo `rmdir(generations)` e prima di rimuovere il
contenitore ritirato, il giro successivo entra in `_verifica_forma`, pretende
che `generations` esista e termina con `FileNotFoundError`. Questo contraddice la
proprietà dichiarata «un'interruzione in ogni passo lascia una forma
riconoscibile».

Correzione minima: dopo il punto d'impegno, ammettere soltanto gli stati
monotoni realmente raggiungibili della pulizia e soltanto per il medesimo inode
registrato: forma completa, forma senza `writer.lock`, contenitore vuoto. Ogni
voce ulteriore o stato non raggiungibile continua a bloccare.

## R13 — la ricevuta non identifica il contenitore

La ricevuta contiene contratto, chiave e identità della radice, ma non
`(st_dev, st_ino)` del contenitore verificato. La sonda fa fallire la rinomina
perché il nome di ritiro è già occupato, poi rimuove l'originale. Al tentativo
successivo la ricevuta preparata per l'originale autorizza la rimozione del
contenitore diverso che occupava il nome di ritiro.

Correzione minima: registrare durevolmente l'identità del contenitore prima
della rinomina e confrontarla in ogni ripresa, prima di qualunque rimozione.

## R14 — “preparata” e “impegnata” sono lo stesso documento

La ricevuta viene resa durevole prima della rinomina, ma la presenza della
stessa ricevuta con entrambi i nomi assenti viene interpretata come successo.
La sonda induce una collisione sulla rinomina, elimina poi entrambe le fixture e
il secondo giro dichiara completata una rinomina che era fallita.

Correzione minima: distinguere almeno `prepared` e `committed`. Scrivere
`prepared` con l'identità esatta prima della rinomina; dopo una rinomina riuscita
e il `fsync` della radice, rendere durevole `committed`. Un solo ritirato con
identità concorde può far avanzare `prepared` a `committed`; entrambi assenti
sono idempotenti soltanto da `committed`.

## Criterio di accettazione

- Le 23 prove di B restano verdi.
- Le quattro prove indipendenti sopra diventano verdi senza indebolirle.
- `git diff --check` resta pulito.
- Nessuna applicazione al negozio reale in questo giro.

Il perimetro F4 di prodotto resta fermo finché la variazione normativa non è
decisa; questa primitiva separata può invece convergere e restare non applicata.

