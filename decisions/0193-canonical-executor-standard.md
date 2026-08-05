---
id: 0193
title: Standard canonico e versionato degli executor
date: 2026-07-16
status: accepted
area: executor
related: [0039, 0040, 0041, 0045, 0152, 0189, 0192]
---

# 0193 - Standard canonico degli executor

## Contesto

Il contratto degli executor era distribuito fra decisioni su naming,
vettorialita', robustezza, output, i18n, undo, sandbox, placement e intelligenza
interna. Il codice applicava molte regole, ma mancava un documento fondativo che
definisse in un solo punto cosa significa "executor conforme". Questo rendeva
ambigua sia la creazione di nuove capacita' sia la bonifica incrementale del
catalogo esistente.

MCP ha reso il problema piu' visibile: un protocollo di trasporto non puo'
diventare implicitamente il contratto semantico di Metnos. Prima di ammettere un
nuovo trasporto serve un'autorita' stabile sopra di esso.

## Decisione

`EXECUTOR_STANDARD.md` e' lo standard normativo degli executor Metnos. Si
applica a ogni capacita' visibile al planner, indipendentemente da origine,
modalita' diretta o intelligente e trasporto locale, remoto o MCP.

Lo standard ha identificatore versionato `metnos.executor/1.0`. Un executor
nuovo deve dichiararlo e superare i gate applicabili prima di diventare attivo.
Un executor esistente privo della dichiarazione resta temporaneamente
`legacy`: continua a funzionare, ma l'assenza non e' una variante valida dello
standard.

Il catalogo non viene riscritto in massa. Il refactor di un executor legacy e'
un atto esplicito: conservazione o versionamento del contratto pubblico, riuso
degli helper comuni, superamento dei gate, aggiunta della dichiarazione,
ri-firma e regressione. Le regole nuove vengono applicate in modalita'
osservativa ai legacy e bloccante ai nuovi o dichiarati conformi.

Non esiste conformita' parziale: la dichiarazione nel manifest attiva tutti i
gate applicabili. Durante la transizione il loader blocca una dichiarazione
falsa, mentre i percorsi di generazione e promozione vengono migrati uno alla
volta al profilo candidate/conformant. Un percorso non ancora migrato resta
esplicitamente legacy e non puo' usare la dichiarazione come etichetta
ornamentale.

Il documento consolida le decisioni specialistiche esistenti senza duplicarne
le fonti meccaniche: vocabolario, capability, placement, reverse pattern e
regole di lint continuano a provenire dai moduli runtime canonici. Le specifiche
di dominio possono restringere lo standard, mai indebolirlo.

In particolare, `runtime/policy.py::CAPABILITY_REGISTRY` e' l'autorita' chiusa
dei nomi di capability. I nomi sconosciuti restano diagnostici per i legacy e
bloccano firma e ammissione di ogni executor dichiarato conforme.

L'autorita' dei backend remoti usa una sola capability canonica,
`provider:access`. Il suo `hint` e' un binding della mappa chiusa
`runtime/vocab.py::PROVIDER_SKILLS`; la clausola chiusa `when` puo' limitarlo al
valore finale di un argomento tipizzato. `runtime/capabilities.py` risolve le
capability effettive in modo fail-closed. Per gli executor conformi lo stesso
binding effettivo governa rete, home credenziali RW e pin al server: nome,
suffisso provider e valore di `client` non concedono autorita'. I cinque
segnali storici restano soltanto come ripiego temporaneo degli executor legacy.

## Conseguenze

- Planner e utente vedono una capacita' stabile, non provider o protocolli.
- Executor diretti e intelligenti conservano lo stesso confine I/O e la stessa
  autorita'.
- Remote execution e MCP restano adattatori di trasporto.
- Un backend locale non eredita l'autorita' di un provider non selezionato; un
  backend remoto riceve rete, credenziali e collocazione dalla stessa
  dichiarazione condizionale.
- La conformita' non puo' essere soltanto dichiarata: le parti verificabili
  sono controllate deterministicamente, quelle semantiche richiedono test e
  review.
- Template, generatori, importer e admission devono convergere sullo standard
  v1. Le deviazioni legacy diventano un backlog misurabile, non eccezioni
  indefinite.

## Non decisioni

Questa decisione non approva l'adozione MCP, non richiede una migrazione
simultanea del catalogo e non rende ogni executor intelligente. Questi restano
assi indipendenti sottoposti ai rispettivi criteri.
