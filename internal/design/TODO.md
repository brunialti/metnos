# Design TODO

Questo elenco raccoglie valutazioni architetturali non ancora approvate. Non e'
una fonte normativa e non autorizza modifiche a vocabolario, planner, executor o
routing.

## DLG-001 - Valutare il dominio logico `dialogues`

- Stato: aperto, solo analisi.
- Richiesta: 2026-07-11.
- Ambito: verificare se gli oggetti canonici `inputs` e `approval` debbano essere
  aggregati in una famiglia o in un dominio superiore `dialogues`.
- Vincolo: non implementare questa aggregazione finche' non viene presa e
  ratificata una decisione architetturale separata.

### Valutazione preliminare

L'aggregazione e' coerente come **famiglia logica**: entrambe le operazioni usano
un `dialog_id`, stato pendente isolato per mittente, scadenza, rendering per
canale, sospensione e ripresa del turno e la capability `dialog.user_input`.

Non e' invece dimostrato che debbano diventare lo stesso oggetto semantico:
`inputs` acquisisce valori tipizzati, mentre `approval` rappresenta un confine
di autorizzazione esplicito e determina il ramo successivo. Un input generico
non deve mai poter sostituire, implicare o auto-concedere un'approvazione.

### Criteri da verificare

1. L'aggregazione deve rappresentare un concetto autonomo, comprensibile e
   generale, non una somiglianza solo tecnica tra due executor.
2. Il contratto comune deve valere per tutti i canali e per tutti i domini che
   producono dialoghi, senza eccezioni legate a executor specifici.
3. Famiglia, sottotipi e capability devono provenire da una sola fonte
   deterministica; sono escluse mappe hardcoded per nome di executor.
4. La separazione di policy tra raccolta dati e concessione di autorita' deve
   restare verificabile dal planner, dai gate e dall'audit.
5. La soluzione deve definire migrazione o compatibilita' per `get_inputs`,
   `get_approval`, firme di cache, piani persistiti, routing, i18n e catalogo.
6. La nomenclatura deve rispettare la grammatica canonica e funzionare in modo
   naturale nelle lingue supportate, senza introdurre un linguaggio CLI rigido.

### Alternative da confrontare

1. Introdurre `dialogues` come oggetto canonico con sottotipi distinti e sicuri.
2. Conservare `inputs` e `approval` come oggetti pubblici e dichiarare
   `dialogues` soltanto come famiglia di catalogo/runtime nella fonte canonica.
3. Condividere esclusivamente infrastruttura e lifecycle, mantenendo separate
   anche le classificazioni pubbliche.

### Condizioni minime per una futura implementazione

- Una sola fonte di verita' genera in modo deterministico classificazione,
  catalogo e controlli, senza elenchi duplicati.
- I test dimostrano che planner ed executor non possono rimpiazzare
  `get_approval` con `get_inputs` nei punti che richiedono consenso.
- Il modello conserva isolamento per mittente, scadenza, idempotenza, audit e
  ripresa sicura del turno.
- L'impatto su compatibilita', cache e piani esistenti e' esplicito e testato.

