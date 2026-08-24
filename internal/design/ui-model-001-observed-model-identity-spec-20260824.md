# UI-MODEL-001 — Identita' osservata dei modelli

## Problema confermato

La pagina amministrativa mostra correttamente il binding configurato di ogni
ruolo, ma il campo `model` puo' essere soltanto un selettore logico (per esempio
`local`). Non e' quindi prova del modello caricato dal servizio che risponde.
La configurazione e l'identita' osservata sono due fatti diversi e devono
restare visibili separatamente.

## Contratto

1. La configurazione resta l'unica autorita' per provider, endpoint, selettore e
   policy del ruolo.
2. L'identita' effettiva e' un'osservazione del provider, con stato, sorgente e
   istante. Non modifica la configurazione e non diventa autorita' di routing.
3. I ruoli che condividono lo stesso binding fisico condividono anche una sola
   osservazione: nessuna enumerazione per tier o per nome di modello.
4. Il rendering della pagina non apre connessioni. Un task periodico, isolato e
   best-effort aggiorna una cache in memoria; un errore non rallenta ne' rende
   indisponibile l'interfaccia.
5. I protocolli di scoperta sono adapter registrati per provider. Aggiungere un
   provider richiede un adapter di protocollo, non condizioni sui tier o sui
   modelli. La prima versione copre OpenAI-compatible/llama.cpp e Ollama.
6. La UI distingue almeno: osservato, piu' modelli osservati (ambiguo), servizio
   non raggiungibile, risposta valida ma priva di modelli, provider senza
   protocollo di scoperta, osservazione non ancora eseguita e dato scaduto.
7. Identificatori restituiti come path sono ridotti al nome finale; endpoint,
   query string, userinfo, credenziali, header e dettagli d'errore non entrano
   nella cache o nella risposta HTTP.
8. Limiti chiusi: soli endpoint HTTP(S) validi, timeout breve, risposta limitata,
   JSON richiesto, numero e lunghezza degli identificatori limitati.

## Modello dei dati

La chiave di scoperta interna e' il digest della coppia canonica
`provider + endpoint`; il selettore resta nella proiezione del singolo ruolo.
In questo modo un endpoint viene interrogato una volta anche quando serve piu'
ruoli. L'osservazione pubblica contiene soltanto:

- `status`: `observed`, `ambiguous`, `empty`, `unreachable`, `unsupported`,
  `not_observed` o `stale`;
- `identities`: lista limitata di identificatori sanificati;
- `source`: protocollo di scoperta usato;
- `observed_at`: timestamp UTC quando disponibile.

## Ciclo di vita

Il task effettua una passata subito dopo l'avvio e poi a intervalli
configurabili. Risolve i binding dalla stessa configurazione effettiva usata
dalle factory, li deduplica e prova soltanto gli adapter registrati. La cache e'
process-local per evitare un nuovo archivio con semantica di invalidazione
ambigua; dopo il riavvio la UI mostra `not_observed` finche' termina la prima
passata.

## Done gate

- Un server OpenAI-compatible con un modello rende il nome effettivo, anche se
  il selettore configurato e' `local`.
- Path POSIX e Windows non espongono directory.
- Piu' modelli non vengono presentati come una scelta certa.
- Endpoint non raggiungibile e provider non supportato sono distinti.
- Binding condivisi producono una sola richiesta per passata.
- Snapshot e template non fanno rete e non espongono segreti.
- Test i18n italiano/inglese e suite della configurazione amministrativa verdi.
