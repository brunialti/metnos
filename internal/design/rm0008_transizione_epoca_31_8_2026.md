# RM-0008 — transizione append-only del contesto di nascita

Data: 31 agosto 2026  
Unita': `F4-EPOCA-01`  
Stato: proposta A pronta alla revisione incrociata  
Ancora: `80b5037c`

## 1. Risultato richiesto

Una distribuzione nuova deve poter produrre nuovo materiale di contesto e una
nuova epoca senza modificare o rimuovere l'insieme precedente. Il passaggio
deve essere esplicito, ripetibile dopo un'interruzione e legato alla
distribuzione firmata che lo richiede.

Il server non adotta mai da solo i byte che trova. Se la distribuzione e
l'epoca selezionata non concordano, l'avvio restituisce un esito nominato e non
modifica lo stato.

Questa unita' appartiene al passaggio F4: prepara il contesto richiesto dalla
build chiusa prima che quella build diventi avviabile. Non anticipa le epoche
di ciclo degli executor previste in F5.

## 2. Vincoli già autorevoli

- rapporto gruppo 2 §7.6: non si rimuove una radice finale esistente;
- rapporto gruppo 2 §8.1: le destinazioni finali non vengono sostituite;
- rapporto gruppo 2 §8.2: una differenza è un conflitto, non un invito a
  rigenerare byte;
- rapporto gruppo 2 §9.4: una distribuzione nuova produce materiale ed epoca
  nuovi;
- roadmap §7.3 e §23.6: F4 lega build, censimento, riattestazione, passaggio e
  avvio chiuso in un ordine non permutabile;
- diagnosi O15: esistono 12 dipendenze vive, sei ricevute di ammissione e sei
  registrazioni Producer;
- diagnosi O17: il server completo parte e serve turni reali in una copia
  isolata quando il contesto concorda.

Ne consegue che non sono ammesse tre scorciatoie: rimuovere la radice
precedente, riscrivere `prepared-v1.json` oppure cambiare l'epoca dentro
ricevute esistenti.

## 3. Proprietario e autorizzazione

La transizione è un atto del coordinatore F4 posseduto dall'amministratore. Il
coordinatore accetta soltanto:

1. una distribuzione già verificata dal verificatore della build chiusa;
2. la prova di manutenzione già richiesta da F4;
3. la testa F4 precedente, oppure l'ancora V1 per il primo passaggio;
4. il censimento completo delle dipendenze dell'epoca corrente;
5. un'identità di richiesta derivata deterministicamente da questi fatti.

Il chiamante non fornisce percorsi, impronte, sequenze, chiavi o nomi di file.
Non nasce una nuova chiave: l'autorizzazione deriva dalla distribuzione
verificata, dall'entrata amministrativa del coordinatore e dalle autorità F4
già separate. L'esecuzione ordinaria del server possiede soltanto il lettore.

L'avvio può diagnosticare `birth_context_transition_required`, indicando la
prima identità di materiale non concordante. Non può iniziare la transizione.

## 4. Forma durevole

Restano immutati:

```text
author-root-v1/
authority-sets/<set_id-precedente>/
prepared-v1.json
```

La prima epoca interpreta `prepared-v1.json` come ancora storica. Le epoche
successive aggiungono:

```text
context-epochs-v1/<epoch_id>.json
context-heads-v1/<sequence>-<epoch_id>.json
required-context-head-v1.bin
context-transactions-v1/<request_id>/...
authority-sets/<set_id-nuovo>/...
```

`context-epochs-v1` e `context-heads-v1` sono append-only: pubblicazione con
nome temporaneo esclusivo, rinomina senza sostituzione, sincronizzazione e
rilettura. Un nome già occupato è accettato soltanto se i byte coincidono.

`required-context-head-v1.bin` è l'unico selettore sostituibile. Contiene il
record completo della testa e la sua autenticazione, con incorniciatura a
lunghezza esatta. La sostituzione atomica avviene per ultima. Il vecchio
selettore completo oppure il nuovo completo restano sempre osservabili.

Il record di epoca contiene esattamente:

```text
schema_version
epoch_id
previous_epoch_id
sequence
request_id
closed_build_id
set_id
prepared_admission_context_id
prepared_context_epoch
context_material_sha256
dependency_inventory_hash
dependency_result_hash
```

Il record di testa contiene esattamente:

```text
schema_version
head_id
previous_head_id
sequence
epoch_id
closed_build_id
set_id
```

Gli identificativi sono digest con dominio e serializzazione canonica. La
sequenza cresce esattamente di uno; predecessore, build, epoca e insieme devono
coincidere fra record, ricevute e distribuzione installata.

## 5. Insieme nuovo e identità conservate

`author-root-v1` resta quello esistente e viene riaperto con il caricatore
produttivo. La sua identità attiva e l'inventario pubblico devono coincidere con
l'epoca precedente.

Il nuovo `authority-sets/<set_id>` viene costruito in una transazione nuova,
con nuovo materiale, nuova epoca e le autorità per insieme previste dal gruppo
2. I byte provengono esclusivamente dalla radice immutabile della distribuzione
F4 verificata, non dall'albero di sviluppo e non da un percorso indicato dal
chiamante.

L'insieme precedente resta leggibile in sola lettura per verificare le
ricevute storiche. Non viene rinominato, potato o reinterpretato come corrente.

## 6. Dipendenze correnti

Il rapporto dell'agente B è la fonte dell'elenco e della classificazione
puntuale. Questo protocollo impone già le regole comuni:

- l'inventario viene acquisito sotto gli stessi blocchi che impediscono nuove
  pubblicazioni;
- ogni dipendenza è identificata dal contenuto autenticato, non dal solo
  percorso;
- una ricevuta esistente non viene modificata;
- una generazione ancora corrente viene riattestata nel nuovo contesto e
  riceve una nuova ricevuta append-only;
- una registrazione Producer riceve un nuovo fatto append-only legato alla
  nuova epoca; la riga precedente resta storica;
- una dipendenza che non può essere classificata o riattestata impedisce il
  passaggio della testa;
- `dependency_result_hash` copre l'esito completo e ordinato di tutte le
  dipendenze censite; il conteggio da solo non è una prova.

Nessun ripuntamento è sufficiente: prima della testa nuova, le riletture
produttive devono provare firma, generazione, contesto, predecessore e
postcondizione di ciascun fatto nuovo.

## 7. Ordine vincolante

Il coordinatore mantiene nell'ordine i blocchi F4 di distribuzione,
manutenzione e nascita. La sequenza è:

1. verificare build chiusa, testa precedente e prova di manutenzione;
2. aprire l'epoca selezionata e ricostruirne materiale e dipendenze;
3. riconoscere lo scostamento verso la nuova distribuzione;
4. congelare l'inventario delle dipendenze e derivare `request_id`;
5. creare o riprendere la sola transazione concordante;
6. riaprire l'autore esistente senza modificarlo;
7. costruire nuovo materiale e nuovo insieme nella transazione;
8. rileggere tutto e pubblicare l'insieme con rinomina senza sostituzione;
9. riattestare tutte le dipendenze e rileggere i fatti nuovi;
10. pubblicare record di epoca e testa append-only;
11. sostituire atomicamente `required-context-head-v1.bin`;
12. rileggere testa, epoca, insieme, dipendenze e distribuzione;
13. chiudere la transazione senza cancellare oggetti finali.

Il punto 11 è il punto di non ritorno. Prima, l'epoca precedente resta
corrente. Dopo, il recupero deve completare la nuova epoca: non può ripiegare
su quella precedente.

## 8. Stati e ripresa

Il journal append-only usa gli stati:

```text
PREPARED
SET_PUBLISHED
DEPENDENCIES_COMPLETE
EPOCH_PUBLISHED
HEAD_PUBLISHED
HEAD_REQUIRED
VERIFIED
```

| ultimo stato durevole | stato osservato | azione |
|---|---|---|
| nessuno | testa precedente valida | nuova transazione soltanto con autorizzazione completa |
| `PREPARED` | insieme non pubblicato | riprendere soltanto i byte inventariati |
| `SET_PUBLISHED` | insieme nuovo concordante, testa vecchia | completare le dipendenze; non rimuovere l'insieme |
| `DEPENDENCIES_COMPLETE` | fatti nuovi concordanti, testa vecchia | pubblicare epoca e testa |
| `EPOCH_PUBLISHED` | epoca presente, testa vecchia | pubblicare la testa append-only |
| `HEAD_PUBLISHED` | selettore ancora vecchio | sostituire il selettore |
| `HEAD_REQUIRED` | selettore nuovo | completare tutte le riletture |
| `VERIFIED` | tutto concordante | successo idempotente |
| qualunque | identità estranea o inventario incompleto | `birth_context_transition_recovery_required`, nessuna modifica |

Un'interruzione prima del punto di non ritorno può lasciare finali append-only
non selezionati; sono innocui e riutilizzabili soltanto dalla stessa richiesta.
Non vengono eliminati automaticamente. Un ritorno funzionale dopo il punto di
non ritorno è una nuova epoca con sequenza superiore, non il ripristino del
selettore precedente.

## 9. Ripetibilità e concorrenza

`request_id` copre almeno testa precedente, build nuova, materiale nuovo,
insieme precedente e inventario completo delle dipendenze. Una ripetizione
identica restituisce il risultato già verificato. Qualunque differenza produce
un conflitto nominato.

Una sola transizione può avanzare. Due richieste uguali convergono sullo stesso
risultato; due richieste diverse non possono entrambe pubblicare la sequenza
successiva. I lettori vedono una testa intera e verificata e scartano una
lettura se il selettore cambia durante l'acquisizione.

## 10. Legame con F4

La distribuzione F4 usa una radice immutabile amministrativa, non `/opt/metnos`.
Il nuovo contesto viene costruito da quella radice dopo la verifica del suo
manifest. La build chiusa diventa avviabile soltanto se:

- il suo `closed_build_id` coincide con la testa di contesto richiesta;
- la testa di contesto e l'insieme sono completamente verificati;
- la prova delle dipendenze coincide con il censimento congelato;
- la normale catena F4 di build, cutover e testa richiede la stessa release.

Una build precedente alla testa richiesta non è un percorso di ritorno. Il
ritorno richiede una nuova release e una nuova transizione, entrambe con
sequenza superiore.

## 11. Prove richieste

Prima di B2 servono almeno:

1. prima transizione dall'ancora V1;
2. seconda transizione dalla testa V1;
3. zero, una e molte dipendenze;
4. sei ricevute e sei fatti Producer nella forma osservata da O15;
5. interruzione dopo ogni frontiera durevole;
6. ripetizione identica dopo ogni interruzione;
7. due richieste concorrenti uguali e due diverse;
8. collisione di nome con byte uguali e diversi;
9. dipendenza scomparsa, aggiunta o cambiata dopo il censimento;
10. distribuzione diversa da quella legata alla testa;
11. vecchia epoca ancora leggibile e byte per byte invariata;
12. diniego di una build precedente dopo il punto di non ritorno;
13. ritorno mediante nuova epoca con sequenza superiore;
14. server completo e turni reali in copia dopo la transizione.

Le prove di interruzione osservano file e ricevute reali. Non è sufficiente
avanzare una macchina di stati fittizia. La suite completa resta riservata a
B3.

## 12. Perimetri di sviluppo dopo B1

Agente A:

- codec di epoca e testa;
- coordinatore e recupero;
- integrazione con distribuzione F4 e caricatore;
- prove di modulo, concorrenza e interruzione del nucleo.

Agente B:

- rapporto e classificazione delle 12 dipendenze;
- adattamento append-only di ricevute e registrazioni Producer;
- prova di accettazione che compone nucleo e dipendenze;
- revisione del codice dell'agente A.

Le interfacce comuni vengono congelate a B1. Ogni variazione successiva richiede
una revisione incrociata prima che uno dei due rami la usi.

## 13. Condizione B1

B1 è raggiunta soltanto quando entrambi gli agenti concordano, sugli stessi
commit, su:

- proprietario e fonte dell'autorizzazione;
- schema di epoca, testa e richiesta;
- classificazione completa delle 12 dipendenze;
- punto di non ritorno e matrice di ripresa;
- interfacce fra nucleo e adattamento delle dipendenze;
- insieme minimo di prove non vacue.

Fino a B1 questo documento non autorizza modifiche al codice di prodotto.
