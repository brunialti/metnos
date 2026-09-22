# RM-0009 — revisione avversaria dell'analisi preparatoria

Data: 15 settembre 2026. Oggetto: `preparation-analysis.md`, questioni A–H.
Stato: **revisione documentale circoscritta; non G0.8 e non certificazione**.

## Esito

Le otto questioni hanno una base documentale o tecnica riconoscibile. Le
direzioni sono proposte da completare in G0.2–G0.6, non nuove prescrizioni.
Non emerge una violazione già deliberata della globalità né una dipendenza
esplicita FS/X0 introdotta nella consegna preliminare I1.1. Non si richiede
l'implementazione dei moduli futuri per congelare il documento, né una prova
reale FS-A/FS-B/F5 per eseguire test isolati.

Nessun rilievo P0/P1 contro il rapporto preparatorio nella sua forma attuale.
Le precisazioni P2 sotto evitano interpretazioni divergenti quando le proposte
diventeranno contratto. Le decisioni tecniche residue restano al coordinatore;
non sono autorizzazioni di prodotto già acquisite né compiti da lasciare
implicitamente agli esecutori.

La conclusione e il commit finale RM-0008 restano il prerequisito di
coordinamento imposto dall'utente per integrazione/runtime/congelamento.
Questo rapporto non verifica che siano avvenuti e non certifica quel futuro
commit.

## Rilievi prioritizzati e correzioni minime

### R1 — P2 — B: il risultato `blocked` deve avere anche l'arco per promote

**Riferimento:** analisi, righe 68–74; roadmap §5.3, §5.4.1, A.4/F5.2,
D-P2.3 e D.8.

La proposta risolve correttamente il quinto esito implicito della riga 9 e
la precedenza della domanda a campione insufficiente. Tuttavia l'arco
`proposed → blocked` è oggi descritto soltanto come veto tecnico D1, mentre
`promote_plan` ha una decisione unica. Indicare soltanto un nuovo reason e
«ripresa in proposed» lascia un esecutore davanti a due testi incompatibili.
Non serve un quinto risultato o un nuovo stato: serve riallineare
esplicitamente condizione dell'arco, momento e fatti obbligatori.

**Inserimento minimo dopo la direzione B:**

> G0.6 riallinea anche §5.4.1, `DecisionMoment` e D.8: il veto per campione
> insufficiente di promote ammette `proposed → blocked` con
> `resume_state=proposed`. Si congela come rappresentare la sua decisione
> unica, con i soli fatti tecnici applicabili. Il veto tecnico e il rifiuto
> attivo conservano la precedenza; l'attesa del campione precede ogni domanda.

Questo è un completamento della proposta, non l'accusa che esista già una
transizione illegale nel runtime RM-0009.

### R2 — P2 — F: separare il momento del budget dal consumo della risposta

**Riferimento:** analisi, righe 150–160; roadmap §5.5,
`question_budget_weekly` in §5.6, D-P2.6 e D-F5.3.

«Outbox e consumo/budget atomici» può confondere due transazioni diverse.
Il limite riguarda le nuove domande emesse in settimana ISO; il token viene
consumato quando arriva una risposta autorizzata. Se il budget si aggiornasse
soltanto alla risposta, le domande senza risposta non occuperebbero posti e
due emittenti potrebbero superare il limite pur consumando correttamente i
token. Il testo non prescrive questo errore, ma non esclude la lettura.

**Sostituzione minima della prima frase della direzione F:**

> Identificatore logico stabile della domanda; creazione della domanda/token,
> prenotazione del posto nel budget e outbox nella stessa transazione di
> emissione. Il consumo del token avviene nella distinta transazione della
> risposta, insieme a stato, eventuale regola di rifiuto ed epoca. G0.6 fissa
> anche la sorte della prenotazione su errore certo, consegna ignota, scadenza
> e cambio di settimana; un retry della stessa domanda non prenota di nuovo.

La scelta sulla liberazione del posto resta da congelare; non è risolta
dalla sola presenza di un'outbox.

### R3 — P2 — E/G: distinguere revoca esistente, futuri grant e prove di uscita

**Riferimento:** analisi, righe 126–139 e 176–179; roadmap §5.8,
D-P2.9, D-F6.2.barrier, D-F6.4a-e, D-F6.6, D.1-ter e D-I1.1.

Revocare prima del purge è la direzione corretta. Un controllo «prima della
scrittura/claim», preso da solo, non chiude però l'intervallo tra controllo
ed effetto: un writer può verificare, essere sospeso, poi scrivere dopo il
purge. Occorre congelare il punto di serializzazione comune alla revoca e
al commit oppure un protocollo equivalente di arresto e riconciliazione dei
writer. Inoltre un claim remoto non è l'avvio irrevocabile: nel futuro v2
quest'ultimo è il CAS `authorize_start`. Dopo il CAS non si può attestare
l'assenza di effetto solo perché l'identità è stata cancellata.

La lista di prove E/G comprende sia P2.9 sia enforcement futuro, senza
attribuzione puntuale. Non dimostra una dipendenza errata, ma tale
attribuzione va resa esplicita per evitare di richiedere F6.6 a I1.1.

**Inserimento minimo dopo le prove E:**

> G0.6 fissa la serializzazione revoca/commit per ciascun writer e il
> trattamento delle operazioni già avviate. P2.9 copre gli store e i percorsi
> operativi esistenti con prove isolate; grant e completion v2 appartengono
> alle relative unità F6 e a F6.6. Dopo lo start irrevocabile l'esito resta
> ignoto finché non è osservato: il purge non costituisce prova di zero effetti
> e non deve eliminare l'unica evidenza necessaria alla riconciliazione.

**Inserimento minimo dopo le prove G:**

> Le prove di diniego con zero effetti sono criteri della tranche di
> enforcement. I1.1 richiede il conteggio singolo e l'invarianza degli esiti
> in ombra, senza antenati FS/S0/X0/F6.3-F6.6.

La revoca riguarda il principal operativo cancellato. Non revoca l'artefatto
globale né concede accesso ai dati, alle credenziali o ai task di altri
utenti; auth, ACL e ambiti ordinari restano applicati.

### R4 — P2 — Ordine finale: preservare l'eccezione già autorizzata per FS

**Riferimento:** analisi, righe 209–213; roadmap D.1 e D-G0.9.

«Solo allora si assegnano le unità di prodotto» dopo G0.10 comprende
letteralmente anche FS-A/FS-B. La roadmap consente invece queste unità dopo
G0.6, con lease e prerequisiti delle rispettive righe. Il rapporto ha già
usato correttamente il vincolo generale per il codice non-FS; serve la stessa
precisione nell'ordine conclusivo. L'attesa RM-0008 richiesta dall'utente
resta comunque in vigore.

**Sostituzione minima dell'ultima frase del punto 5:**

> Solo allora si assegnano le unità di prodotto non-FS secondo la roadmap.
> FS-A/FS-B conservano l'avvio già autorizzato dopo G0.6, con lease e
> prerequisiti espliciti, una volta terminata l'attesa RM-0008 richiesta.

### R5 — P3 — F: la garanzia della coda non prova la consegna fisica unica

**Riferimento:** analisi, righe 143–148; roadmap §5.5.

§5.5 prescrive che le domande in coda non si perdano né si duplichino; non
enuncia un protocollo di consegna fisica exactly-once su ogni canale. Il
problema della consegna ignota è reale come requisito da specificare, ma non
va presentato come confutazione di una garanzia già esplicitata. La direzione
F, che distingue consegna e decisione, è corretta.

**Sostituzione minima dell'evidenza F:**

> §5.5 richiede unicità e conservazione delle domande logiche in coda.
> La consegna al canale aggiunge un effetto esterno il cui contratto di
> recupero va specificato separatamente.

## Verifica delle otto direzioni e decisioni ancora da congelare

| Questione | Esito della contestazione | Decisione tecnica residua |
|---|---|---|
| A | Fondata: `rolled_back` terminale e fingerprint stabile convivono con unicità del canonico; l'upsert corrente preserva lo stato. Non c'è motivo di introdurre evento o owner nell'impronta. | Identità del ciclo e archivio; evento nuovo rispetto a già consumato/arretrato; unicità transazionale dell'istanza corrente; receipt, valutazioni e operation storiche legate al ciclo originale, senza riassegnarle automaticamente al nuovo tramite alias. |
| B | Fondata: riga 9 senza esito tipizzato e riga 3 antecedente possono divergere. Il campione insufficiente non autorizza una domanda né una promozione. | Matrice completa per promote e riallineamento dell'arco indicato in R1. |
| C | Fondata: backup coerenti per singolo DB e filtro timestamp non garantiscono un taglio causale fra store. A.4 è già dichiarata subordinata a D: il rapporto individua una specifica insufficiente, non due autorità equiparate. | Scegliere un protocollo concreto, compresa un'eventuale sospensione coordinata dei writer; inventario dei partecipanti, ripresa, definizione di cutoff e identità logica delle esportazioni. Non è necessario inventare un servizio permanente di barriera. |
| D | Fondata: con soglia zero e successo invariato, la formula letterale `delta >= 0` è vera; il denominatore zero dei confronti relativi non è risolto. | Accogliere e normalizzare esplicitamente le formule proposte; segno, unità, uguaglianza, numeri non finiti e campione minimo devono essere condivisi da politica, valutazione e test. Il rapporto non cambia già la formula normativa. |
| E | Fondata sul censimento degli store e dei purger. HMAC operativo e tombstone di provenienza sono correttamente distinti. | R3; ordine di rimozione delle correlazioni, store privati senza owner ricostruibile, copie persistenti/RAM e ripresa dopo errore. Conservare i contributi canonici e aggregati globali lecitamente privi di dati personali, scollegandone la provenienza. |
| F | Rischio reale di crash fra invio e receipt, senza prova che §5.5 prometta consegna fisica unica. | R2/R5; emissione, invio, risposta e scadenza distinti; idempotenza o osservazione disponibile per ogni canale; `delivery_unknown` separato dallo stato dell'intent. |
| G | Confermati builtin fuori dal loader, undo remoto prima dell'accodamento e ingresso durable nel percorso executor comune. L'assegnazione di soli file nominali non copre il grafo reale. | Matrice dei chiamanti diretti/riprese e deleghe admin; envelope autenticato, identità del tentativo e unico responsabile dei file condivisi; separazione prove in ombra/enforcement di R3. |
| H | Coerente con la differenza tra risultato di pubblicazione e receipt Admission e con `unknown ≠ absent`. La façade resta proposta, non contratto RM-0008 acquisito. | Mapping durevole autorevole, identità e conflitto dei retry, CAS sul predecessore/generazione, lettore autenticato e controlli al confine effettivo. La sola lease RM-0009 non impedisce a un worker scaduto di completare un effetto: il target deve applicare idempotenza e precondizioni congelate. |

Per A, il fatto che l'upsert storico non riapra un terminale non è un difetto
rispetto al codice oggi contrattualizzato: rende concreta la decisione da
prendere per il nuovo requisito. Per H, l'assenza della futura API nel codice
osservato non è motivo per attendere X0.1 prima di definirla o collaudarla con
adattatori inerti. L'emendamento deve fissare separatamente le dipendenze di
sviluppo e le condizioni reali D1/D2, anche quando riprende diagrammi o elenchi
da `birth-contract.md`.

## Evidenza e limiti

Letti `CLAUDE.md`, `CLAUDE.mutabile.md`, `internal/AGENTS.md`; confrontati
integralmente l'analisi e i §§5.3–5.7, D.1–D.8 della roadmap, oltre a §5.8
e alle schede A.4 pertinenti. Consultate soltanto le parti necessarie di
`inventory-data.md`, `inventory-security.md` e `birth-contract.md`.

Ricontrolli sul solo codice sorgente del checkout principale:
`runtime/change_intents.py::upsert_intent`;
`runtime/agent_runtime.py::{_invoke_builtin_handler,invoke_tool_by_name}`
e ramo remoto prima di `_undo_pending`;
`runtime/durable_workloads/execution.py::{_invoke_executor,_invoke}`.
Confermano A/G per quello snapshot, non per il futuro commit RM-0008.

Nessuna lettura di database, dati utente o segreti; nessuna chiamata di rete,
servizio, test di prodotto o modifica RM-0008. Il solo file scritto per questa
revisione è questo rapporto. La revisione dei preparatori non sostituisce
G0.7/G0.8, G0.9 o le prove reali I0.

## Chiusura mirata delle correzioni — 15 settembre 2026

Verificata la versione di `preparation-analysis.md` con SHA-256:

```text
572afedf47667ea9edb7d63aeb03dd610ec607d6562a4c3febde2c4c7c37a697
```

R1–R5 sono **recepiti sul piano documentale**: B esplicita il riallineamento
dell'arco/momento di promote; F separa prenotazione all'emissione e consumo
alla risposta, oltre a unicità logica e consegna fisica; E/G distinguono
serializzazione della revoca, esito dopo lo start e tranche di enforcement;
§3 conserva l'eccezione FS dopo G0.6. A protegge inoltre receipt, valutazioni
e operation del ciclo storico da riassegnazioni automatiche tramite alias.

Nessun ulteriore rilievo sulle sole correzioni verificate. I riferimenti di
riga nelle sezioni precedenti identificano la versione antecedente alle
correzioni e restano come traccia della revisione. Le scelte residue A–H
restano proposte da definire e normalizzare in G0: questa chiusura non congela
il progetto, non modifica la roadmap, non equivale a G0.8 e non certifica
runtime, attestazioni o conclusione RM-0008. In questa verifica è stata
aggiunta soltanto la presente sezione al rapporto.
