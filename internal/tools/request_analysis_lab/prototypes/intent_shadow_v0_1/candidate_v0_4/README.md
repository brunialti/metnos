# Candidate v0.4 — coverage-before-root — ARCHIVIATO, NON ESEGUITO

Stato: proposta non selezionata e sostituita prima di freeze, protocollo,
autorizzazione o misura. Non appartiene al RUN3 style-only e non deve essere
importata dai suoi percorsi live. I sorgenti restano soltanto come traccia del
lavoro interrotto; gli artefatti prompt/schema/freeze non sono materializzati.

Questo candidato offline è un overlay immutabile di v0.3. Cambia soltanto la
proiezione del prompt: prima di scegliere la radice, il modello deve verificare
che tutte le capacità indispensabili dell'intera richiesta siano coperte dal
registro. Un composto non interamente coperto diventa interamente
`unrepresentable/outside_registry`; non sono ammessi sottografi parziali.

Il controllo di sistema esatto è esclusivo. Gli argomenti necessari soltanto
all'esecuzione non causano astensione quando radice e route sono decidibili.
Restano invariate le descrizioni e le autorità delle altre ragioni.

Schema, registry, validator, compiler, adapter, modello, limiti ed evaluator non
sono modificati. Il percorso è query-free, registry-derived e uguale per ogni
tag BCP47 valido. Non esistono repair, critic o regole speciali del banco.

Questo è codice di laboratorio. I dizionari rappresentano soltanto JSON del
laboratorio; un porting in produzione richiede oggetti tipizzati e immutabili.
