# F4-EPOCA-01 — revisione A del censimento dei legami

Data: 31 agosto 2026  
Commit esaminato: `937ba594ec0ac02e0a379a7879466e38930383e0`  
Verdetto: `MODIFICHE_RICHIESTE`

## Risultato confermato

Le otto prove mirate sono verdi e la misura reale e' stata riprodotta. Il fatto
centrale e' accettato: i 12 legami di O15 sono sei atti conclusi sotto due
rappresentazioni, tutte e sei le generazioni sono superate e nessuno dei 12
oggetti deve essere riscritto o ripuntato.

E' confermato anche il fatto trasversale che corregge il perimetro A: le
ricevute esistenti non coprono alcuna generazione corrente. La transizione deve
quindi censire le generazioni correnti indipendentemente dai 12 oggetti storici;
non puo' trattare quei 12 come l'elenco del lavoro da riattestare.

## Rilievo 1 — identita' del gemello incompleta

`per_generazione` indicizza le ricevute con la sola generazione, mentre il
contratto F4 usa gia' l'identita' composta `(contract_id, generation_id)`. Una
ricevuta Producer viene poi accoppiata tramite il solo digest. Il classificatore
deve usare la coppia completa e deve rifiutare ogni divergenza fra contratto e
generazione presenti nel percorso, nel documento e nella busta.

Prova minima richiesta: due contratti con la stessa generazione di prova non
devono poter condividere il gemello; contratto o generazione discordanti devono
produrre `non_classificato`.

## Rilievo 2 — autorita' scelta dal chiamante

`--ritirati` permette al chiamante di far classificare una generazione corrente
come `cessa_di_essere_corrente`. Questa e' una decisione autorevole, non un dato
diagnostico. Deve provenire dallo stato autenticato del contratto oppure la
classe va tolta dallo strumento finche' quella fonte non esiste. Un argomento
libero non puo' ridurre il lavoro necessario per F4.

## Rilievo 3 — i fatti decisivi non sono autenticati

Il classificatore legge direttamente `binding.json`, `current` e i JSON delle
ricevute. Non autentica la generazione corrente con le primitive del negozio e
non verifica firma e legami della ricevuta di ammissione. Cosi' la conclusione
e' riproducibile sui byte osservati, ma non ancora probante contro una
discordanza fra locatori e fatti firmati.

La correzione deve riusare le primitive produttive di inventario, lettura della
generazione corrente e verifica delle ricevute, senza crearne una seconda
implementazione. Le prove devono includere puntatore, contratto, generazione o
firma discordanti e mostrare un rifiuto.

## Rilievo 4 — due buste rifiutate sono chiamate illeggibili; un caso invalido
puo' passare

Le due buste reali senza `admission_receipt` appartengono a righe `rejected` e
sono JSON validi: non sono dipendenze illeggibili. Vanno escluse esplicitamente
per stato e forma terminale verificata. Al contrario, una busta `committed`
invalida che non ripeta il contesto in chiaro oggi finisce nell'elenco
`non_interpretabili`, ma non genera un legame e non cambia l'uscita verde.

La regola deve essere chiusa:

- rifiuto terminale valido senza ricevuta di ammissione: non e' un legame;
- conclusione valida con ricevuta: viene accoppiata e classificata;
- stato, autenticazione o busta incoerenti: `non_classificato`, quindi blocco.

Servono due prove distinte per il rifiuto valido e per la conclusione invalida
senza testo del contesto.

## Condizione del giro successivo

Il disegno dei tre futuri e la conclusione sui 12 oggetti non devono cambiare.
Il giro successivo deve rendere lo strumento coerente con quelle affermazioni:
identita' composta, nessuna autorita' dal chiamante, fatti autenticati e nessun
caso terminale ignorato. Restano prove mirate; la suite completa non va
eseguita in questa barriera.
