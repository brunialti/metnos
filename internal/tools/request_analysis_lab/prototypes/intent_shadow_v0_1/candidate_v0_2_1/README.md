# Candidate v0.2.1 — requisito BCP47

Questa base intermedia del laboratorio corregge un solo difetto contrattuale:
il normalizzatore v0.2 gestiva i tag BCP47 moderni ma non tutti i 26 tag
`grandfathered` registrati da IANA. La tabella è completa e data-driven; applica
il `Preferred-Value` quando presente, altrimenti conserva la grafia canonica
registrata. Tutti gli altri tag sono delegati byte per byte al parser v0.2.
Un cancello ASCII stretto precede il lookup case-insensitive, così caratteri
Unicode confondibili non possono trasformarsi in tag registrati.

Non cambia prompt, schema, registry, validator, compiler, adapter, modello,
limiti o evaluator. È un prerequisito separato dall'ipotesi sperimentale RUN2.
È codice di laboratorio, non runtime Metnos.
