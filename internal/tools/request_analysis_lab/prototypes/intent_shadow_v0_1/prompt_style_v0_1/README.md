# Prompt style v0.1

Esperimento offline a tre bracci sul solo stile del prompt candidate v0.3:

- `S0_CURRENT`: byte correnti v0.3, controllo interno della stessa misura;
- `S1_METNOS_SHORT`: una riga imperativa per comportamento, forma breve ADR 0027;
- `S2_PROCEDURAL`: le stesse righe, nello stesso ordine, come procedura numerata.

Le sei regole autoritative sono estratte e mappate riga per riga dal prompt
CURRENT. S1 e S2 riusano esattamente la stessa frase per ogni regola, una sola
volta. Le tre forme di radice e l'intera sezione dati del registry sono identiche
fra i bracci. Gli esempi lunghi e il riepilogo duplicato del CURRENT non vengono
replicati: il loro contenuto è già mappato alle medesime regole e allo schema.

Non viene aggiunta alcuna regola coverage-before-root. Schema, registry,
validator, compiler, adapter, modello, limiti ed evaluator restano invariati.
Il percorso è query-free e identico per ogni tag BCP47 valido.

È solo laboratorio: i dizionari sono artefatti JSON. Un eventuale porting in
produzione richiede oggetti tipizzati e immutabili.
