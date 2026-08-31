# RM-0008 — transizione append-only del contesto di nascita

Data: 31 agosto 2026  
Unita': `F4-EPOCA-01`  
Stato: proposta A, seconda versione pronta alla revisione incrociata
Ancora fattuale B: `937ba594`

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
- diagnosi O15 e classificazione B: i 12 legami osservati sono sei atti
  storici sotto due rappresentazioni; tutte le generazioni coperte sono
  superate e nessuno dei 12 oggetti va ripuntato;
- misura B sul negozio: 123 pubblicazioni, 21 con ricevute storiche e zero
  ricevute per la generazione corrente; il censimento da riattestare deve
  quindi partire dalle generazioni correnti, non dai 12 legami storici;
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
4. il censimento completo delle generazioni correnti;
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
authority-sets/<set_id-nuovo>/...
context-transitions-v1/<request_id>.json
```

Non nasce un secondo selettore. Il solo selettore sostituibile resta
`required-head-v1.bin` della catena F4 gia' costruita. La testa firmata seleziona
il certificato di passaggio; il certificato firmato contiene `request_id`; quel
valore seleziona e autentica transitivamente il record canonico in
`context-transitions-v1`.

Il record di transizione contiene esattamente:

```text
schema_version
closed_build_id
previous_cutover_id
previous_set_id
previous_admission_context_id
previous_context_epoch
set_id
prepared_admission_context_id
prepared_context_epoch
context_material_sha256
set_json_sha256
current_inventory_hash
```

`request_id` e' il digest con dominio della build, del passaggio precedente e
dei byte canonici di questo record. Il certificato F4 gia' firma `request_id`,
`closed_build_id` e l'elenco completo delle ricevute correnti. Il lettore
verifica quindi la catena completa
`required-head -> certificato -> request_id -> record -> insieme`, senza una
nuova chiave e senza due puntatori che possano divergere.

Il record e l'insieme sono append-only: nome temporaneo esclusivo, rinomina
senza sostituzione, sincronizzazione e rilettura. Un nome gia' occupato e'
accettato soltanto se i byte coincidono. Predecessore, build, epoca, insieme,
inventario corrente e ricevute devono concordare fra record, certificato e
distribuzione installata.

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

Il rapporto dell'agente B dimostra che i 12 legami di O15 sono storici. Questo
protocollo distingue quei fatti dall'inventario che F4 deve rendere avviabile:

- i 12 legami storici e tutte le altre ricevute di generazioni superate restano
  byte per byte immutati;
- sotto la manutenzione F4 si acquisisce invece l'inventario autenticato delle
  generazioni correnti, con identita' composta `(contract_id, generation_id)`;
- ogni generazione corrente viene riattestata nel nuovo contesto, anche se non
  possiede una ricevuta dell'epoca precedente;
- la riattestazione aggiunge una ricevuta in
  `admission-receipts-v2/<generation_digest>/<context_digest>.json` e una
  nuova registrazione Producer; i due oggetti sono le due rappresentazioni
  dello stesso atto, non due dipendenze da migrare separatamente;
- il lettore della ricevuta corrente riceve una selezione di contesto sigillata
  dal caricatore; nessun chiamante puo' passargli un contesto o un percorso;
- una generazione aggiunta, rimossa, cambiata o non riattestabile fra le due
  letture impedisce il passaggio della testa;
- il certificato F4 copre identita' e impronta di ogni nuova ricevuta; il
  conteggio da solo non e' una prova.

Il percorso V1 delle ricevute resta storico. Da questa transizione in avanti
anche le nuove ammissioni ordinarie usano il percorso V2 legato al contesto,
cosi' una seconda epoca puo' aggiungere una nuova ricevuta per la stessa
generazione senza sostituire quella precedente. Prima della testa nuova, le
riletture produttive provano firma, identita' composta, contesto, predecessore
e postcondizione di ciascun fatto nuovo.

## 7. Ordine vincolante

Il coordinatore mantiene nell'ordine i blocchi F4 di distribuzione,
manutenzione e nascita. La sequenza è:

1. verificare build chiusa, catena F4 precedente e prova di manutenzione;
2. aprire il contesto selezionato dalla testa F4, oppure `prepared-v1.json`
   soltanto prima della prima testa, e ricostruirne il materiale;
3. riconoscere lo scostamento verso la nuova distribuzione;
4. riprendere o costruire il nuovo insieme riusando l'autore senza modificarlo;
5. rileggere e pubblicare l'insieme con rinomina senza sostituzione;
6. costruire dal nuovo insieme un nucleo di nascita sigillato e limitato alla
   riattestazione, senza installarlo come nucleo ordinario;
7. congelare sotto manutenzione l'inventario autenticato delle generazioni
   correnti e derivare il record di transizione e `request_id`;
8. riattestare ogni generazione corrente nel percorso V2 mediante una nuova
   ricevuta Producer, poi rileggere entrambe le rappresentazioni;
9. ripetere il censimento e pretendere identita' identiche al punto 7;
10. pubblicare il record di transizione append-only;
11. emettere e pubblicare il certificato F4 che lega `request_id`, build e
    impronte delle ricevute;
12. pubblicare build e testa F4 append-only;
13. sostituire atomicamente il solo `required-head-v1.bin`;
14. rileggere catena, record, insieme, distribuzione, inventario e ricevute;
15. chiudere il giornale senza cancellare oggetti finali.

Nel primo passaggio, `CERTIFICATE_PUBLISHED` conserva il punto di non ritorno
dal regime precedente gia' stabilito dalla roadmap: un recupero deve
completare. Negli aggiornamenti successivi il punto di non ritorno e' il
confronto-e-scambio del punto 13. La selezione del contesto cambia sempre e
soltanto col punto 13; non esiste un commutatore parallelo.

## 8. Stati e ripresa

Il giornale F4 append-only viene esteso, non affiancato da un secondo registro,
e usa gli stati:

```text
PREPARED
SET_PUBLISHED
RECEIPTS_COMPLETE
CONTEXT_BOUND
CERTIFICATE_PUBLISHED
BUILD_VERIFIED
HEAD_REQUIRED
PREFLIGHT_VERIFIED
```

| ultimo stato durevole | stato osservato | azione |
|---|---|---|
| nessuno | testa precedente valida | nuova transazione soltanto con autorizzazione completa |
| `PREPARED` | insieme non pubblicato | riprendere soltanto i byte inventariati |
| `SET_PUBLISHED` | insieme nuovo concordante, testa vecchia | completare le riattestazioni; non rimuovere l'insieme |
| `RECEIPTS_COMPLETE` | inventario e ricevute concordanti, testa vecchia | pubblicare il record legato a `request_id` |
| `CONTEXT_BOUND` | record presente, testa vecchia | emettere il certificato con lo stesso `request_id` |
| `CERTIFICATE_PUBLISHED` | certificato presente | completare build e testa; nel primo passaggio non e' ammesso il ritorno al regime precedente |
| `BUILD_VERIFIED` | oggetti F4 riletti, selettore vecchio | pubblicare la testa e confrontare il predecessore |
| `HEAD_REQUIRED` | selettore nuovo | completare tutte le riletture |
| `PREFLIGHT_VERIFIED` | tutto concordante | successo idempotente |
| qualunque | identità estranea o inventario incompleto | `birth_context_transition_recovery_required`, nessuna modifica |

Un'interruzione prima del punto di non ritorno può lasciare finali append-only
non selezionati; sono innocui e riutilizzabili soltanto dalla stessa richiesta.
Non vengono eliminati automaticamente. Un ritorno funzionale dopo il punto di
non ritorno è una nuova epoca con sequenza superiore, non il ripristino del
selettore precedente.

## 9. Ripetibilità e concorrenza

`request_id` copre almeno passaggio precedente, build nuova, record canonico,
insieme precedente e inventario completo delle generazioni correnti. Una
ripetizione identica restituisce il risultato gia' verificato. Qualunque
differenza produce un conflitto nominato.

Una sola transizione può avanzare. Due richieste uguali convergono sullo stesso
risultato; due richieste diverse non possono entrambe pubblicare la sequenza
successiva. I lettori vedono una testa intera e verificata e scartano una
lettura se il selettore cambia durante l'acquisizione.

## 10. Legame con F4

La distribuzione F4 usa una radice immutabile amministrativa, non `/opt/metnos`.
Il nuovo contesto viene costruito da quella radice dopo la verifica del suo
manifest. La build chiusa diventa avviabile soltanto se:

- il suo `closed_build_id` coincide con testa, certificato e record di
  transizione;
- `request_id` ricalcolato sui byte del record coincide col certificato;
- insieme e materiale ricostruito coincidono col record;
- la prova delle ricevute coincide con l'inventario corrente congelato;
- la normale catena F4 di build, passaggio e testa richiede la stessa release.

Una build precedente alla testa richiesta non è un percorso di ritorno. Il
ritorno richiede una nuova release e una nuova transizione, entrambe con
sequenza superiore.

## 11. Prove richieste

Prima di B2 servono almeno:

1. prima transizione dall'ancora V1;
2. seconda transizione dalla testa V1;
3. zero, una e molte generazioni correnti;
4. i 12 legami storici osservati da O15 restano byte per byte immutati;
5. zero ricevute per le generazioni correnti produce una riattestazione per
   ciascuna generazione, non un successo vuoto;
6. interruzione dopo ogni frontiera durevole;
7. ripetizione identica dopo ogni interruzione;
8. due richieste concorrenti uguali e due diverse;
9. collisione di nome con byte uguali e diversi;
10. generazione scomparsa, aggiunta o cambiata dopo il censimento;
11. distribuzione diversa da quella legata alla testa;
12. vecchia epoca e vecchie ricevute ancora leggibili e immutate;
13. un solo selettore: nessuna combinazione fra testa F4 e contesto diverso;
14. diniego di una build precedente dopo il punto di non ritorno;
15. ritorno mediante nuova release con sequenza superiore;
16. server completo e turni reali in copia dopo la transizione.

Le prove di interruzione osservano file e ricevute reali. Non è sufficiente
avanzare una macchina di stati fittizia. La suite completa resta riservata a
B3.

## 12. Perimetri di sviluppo dopo B1

Agente A:

- codec del record di transizione e legame con la testa F4 esistente;
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
- schema del record, legame con la testa F4 e identita' della richiesta;
- classificazione completa delle 12 dipendenze;
- punto di non ritorno e matrice di ripresa;
- interfacce fra nucleo e adattamento delle dipendenze;
- insieme minimo di prove non vacue.

Fino a B1 questo documento non autorizza modifiche al codice di prodotto.
