# F4-EPOCA-01 — revisione B del protocollo di transizione

Commit esaminato: `d1c25395` (`internal/design/rm0008_transizione_epoca_31_8_2026.md`)
Verdetto: `MODIFICHE_RICHIESTE`

## Quello che regge

Il disegno è solido dove conta. Gli stati restano i sette esistenti senza
aggiunte, la matrice di ripresa copre ogni stato durevole con l'azione
corrispondente e chiude con una riga che rifiuta invece di indovinare. Il punto
di non ritorno è dichiarato e il ritorno funzionale dopo di esso è una **nuova
epoca con sequenza superiore**, non il ripristino del selettore precedente:
questo evita esattamente la sostituzione che §8.1 del gruppo 2 vieta.

`transition_id` copre richiesta, passaggio precedente, build, insieme
precedente, nuovo insieme e inventario, quindi una ripetizione identica
converge e una differenza produce un conflitto nominato invece di una seconda
pubblicazione.

E il §6 recepisce correttamente il fatto trasversale che avevo consegnato: ogni
generazione corrente viene riattestata **anche se non possiede una ricevuta
dell'epoca precedente**. Era il punto in cui il perimetro A rischiava di trattare
i 12 oggetti storici come l'elenco del lavoro, e non lo fa.

## Rilievo B1 — «inventario incompleto» non è definito, e il negozio oggi non è pulito

Il §6 acquisisce «l'inventario autenticato delle generazioni correnti» e la
matrice del §8 blocca su «inventario incompleto». Nessuno dei due dice che
l'inventario produttivo debba riportare **zero problemi**.

La distinzione non è formale. `inventory_store_manifests` restituisce le
directory illeggibili in `problems` e **le omette da `manifests`**: un inventario
può quindi essere completo rispetto a ciò che è riuscito a leggere, mentre il
negozio porta un oggetto che nessuno possiede. Con la formulazione attuale la
transizione può avanzare in quello stato.

**E non è ipotetico.** Sul negozio reale:

```
$ python3 internal/tools/classifica_legami_epoca.py
anomalie del negozio che bloccano: 2
    problema d'inventario: InventoryProblem(code='binding_invalid',
      path='.../contract-publications/v1/4e2feabf613fef06a63bddbf37396733f427cbeae5366aa75df4816d7f27611d')
    directory inattesa nel negozio: 4e2feabf613fef06a63bddbf37396733f427cbeae5366aa75df4816d7f27611d
ESITO: il negozio contiene oggetti che l'inventario non possiede.
        F4 non puo' essere dichiarata.
```

Quella directory è una **pubblicazione interrotta** del 30 agosto alle 12:48:
nessun `binding.json`, nessun `current`, `generations` vuota, un `writer.lock`
rimasto. È esattamente la proprietà che il rilievo 7 ha preteso da me — «una
directory inattesa blocca l'intero censimento, anche se non contiene ricevute
del contesto cercato» — e che il protocollo non pretende ancora da sé stesso.

**Disposizione**: il §6 deve dire che l'acquisizione dell'inventario esige zero
problemi, e il §8 deve definire «inventario incompleto» in modo che comprenda
sia una generazione mancante sia un oggetto del negozio che l'inventario non
possiede. Altrimenti i due perimetri intendono due cose diverse con la stessa
parola, e la più permissiva vince nel punto peggiore.

## Rilievo B2 — il percorso V2 rende cieco il censimento, e nessun perimetro lo possiede

Il §6 introduce `admission-receipts-v2/<generation_digest>/<context_digest>.json`
e stabilisce che «da questa transizione in avanti anche le nuove ammissioni
ordinarie usano il percorso V2».

Il censimento del perimetro B legge oggi soltanto il percorso V1
(`admission-receipts/*.json`). Dopo la transizione, **un legame creato sotto V2
sarebbe invisibile** allo strumento che certifica «nessuna dipendenza richiede
un'azione prima di F4». Il censimento continuerebbe a uscire verde perché non
guarda dove ora si scrive.

Non è un difetto del disegno di A né una svista mia: è un **confine condiviso**
che nessuno dei due documenti assegna. Il §12 ripartisce i perimetri e il §13
elenca fra le condizioni B1 «interfacce fra nucleo e adattamento delle
dipendenze»: questa è una di quelle interfacce, e va nominata **prima** di B1,
non scoperta dopo.

**Disposizione**: il §12 assegni esplicitamente l'estensione del censimento al
percorso V2 — la prendo io, è il mio perimetro — e il §13 includa fra le
condizioni B1 il congelamento del formato del percorso V2, perché il censimento
non può leggerlo prima che sia fermo. Chiedo inoltre che il disegno dichiari se
un contratto possa avere contemporaneamente una ricevuta V1 storica e una V2
corrente per la **stessa** generazione: la risposta cambia la chiave di
identità che il censimento deve usare.

## Nota, non un rilievo

Il §6 dice che il rapporto B «deve dimostrare che i 12 legami sono storici». È
fatto, sul commit `dd86dc05`, con l'autenticazione completa chiesta dai rilievi
5-7: 12 su 12 `epoca_storica`, zero non classificati, 32 ricevute di altre
epoche fuori ambito. Con una divergenza dichiarata dai numeri attesi: i rifiuti
terminali autenticati sono **zero**, non due, perché le due righe `rejected` non
portano alcuna ricevuta e precedono di due ore la creazione di questo insieme.
