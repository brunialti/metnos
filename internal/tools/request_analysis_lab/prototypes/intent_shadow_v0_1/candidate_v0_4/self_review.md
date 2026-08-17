# Candidate v0.4 self-review

La sola ipotesi sperimentale è una procedura lineare nel prompt:

1. controllo di sistema esatto ed esclusivo;
2. censimento delle capacità e clausole indispensabili dell'intera richiesta;
3. grafo solo con copertura completa ed esatta;
4. composto intero `outside_registry` se manca una capacità indispensabile;
5. sole operazioni indispensabili e dipendenze `from` reali e precedenti.

La procedura applica decisioni già congelate e non aggiunge precedenze fra le
altre ragioni di astensione. Non interpreta query, ID, indici o hash del banco.
Il catalogo resta derivato dal registry e il medesimo percorso Unicode/BCP47 è
usato per ogni lingua.

Rischio avversariale principale: il modello potrebbe confondere una lacuna
dell'esecutore con una capacità assente oppure scartare una clausola difficile.
Il prompt vieta entrambe le scorciatoie senza introdurre repair. Un test del
contratto non prova l'accuratezza semantica; serve una nuova misura affiancata.

Schema, validator, compiler, adapter, registry, modello, limiti ed evaluator
restano fuori dall'overlay e sono legati ai byte congelati di v0.3 e del core.
Nessun armamento, rete, GPU o inferenza appartiene a questo candidato.
