# Self-review prompt style v0.1

La variabile sperimentale è soltanto la presentazione di sei regole già presenti
nel prompt v0.3. L'inventario conserva ogni riga CURRENT e la collega in modo
unico a una o più regole. S1 e S2 sono generati dallo stesso oggetto immutabile:
non possono perdere, duplicare o riordinare una regola senza rompere i test.

S0 è il prompt v0.3 byte per byte. Nei tre bracci restano identici: tag lingua,
tre template minimi delle radici, dati registry completi e ordinati, schema JSON,
query utente e tutti i parametri della richiesta. S1 elimina prosa ripetuta ed
esempi lunghi; S2 cambia soltanto le etichette in una sequenza numerata.

Rischio principale: una parafrasi può sembrare equivalente a una persona ma non
esserlo per il modello. Per ridurlo, S1 e S2 condividono letteralmente le stesse
sei frasi; la provenienza verso CURRENT è congelata e sottoposta a due revisioni
indipendenti prima di qualunque misura.

La proposta coverage candidate_v0_4 è archiviata e non importata. Nessuna GPU,
rete, autorizzazione, repair o critic è usata in questo pacchetto.
