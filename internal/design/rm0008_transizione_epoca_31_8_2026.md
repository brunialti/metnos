# RM-0008 — transizione append-only del contesto di nascita

Data: 31 agosto 2026  
Unita': `F4-EPOCA-01`  
Stato: proposta A, sesta versione pronta alla revisione incrociata
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
successive aggiungono oggetti in due radici gia' possedute, senza confonderne
i proprietari:

```text
<PATH_USER_CONFIG>/birth/authority-sets/<set_id-nuovo>/...
/var/lib/metnos/executor-birth/chain-v1/
    context-transitions-v1/<transition_digest>.json
```

`transition_digest` e' `transition_id` senza il prefisso `sha256:`. L'insieme
resta nella radice Birth e viene aperto soltanto mediante la sessione sicura
gia' esistente. Il record appartiene invece al negozio root-owned della catena
F4: il suo inizializzatore crea anche `context-transitions-v1` con le stesse
proprieta' delle altre directory del negozio; il lettore ordinario non la crea
e non la ripara. Il record non porta una firma separata: nome, digest del
contenuto e `context_transition_id` nel certificato firmato sono una sola
catena di autenticazione.

Non nasce un secondo selettore. Il solo selettore sostituibile resta
`required-head-v1.bin` della catena F4 gia' costruita. La testa firmata seleziona
il certificato di passaggio; il certificato firmato contiene
`context_transition_id`; quel valore seleziona e autentica transitivamente il
record canonico in `context-transitions-v1`.

Il record di transizione contiene esattamente:

```text
schema_version
transition_id
request_id
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

`transition_id` e' il digest con dominio dei byte canonici del record senza il
campo omonimo. Il certificato V1 e la sua ancora esistente restano byte per byte
immutati. Il primo passaggio reale emette invece un certificato V2, con dominio
di firma e dominio dell'identificativo V2 distinti, che contiene esattamente
tutti i campi V1 piu' `context_transition_id`,
`dominant_startup_receipt` e `schema_version=2`. Usa la
stessa autorita' gia' circoscritta allo scopo ownership-cutover: non nasce una
chiave e non si amplia lo scopo. Il verificatore sceglie il codec dal formato
canonico e non prova un formato come ripiego dell'altro.

La testa firmata, il suo codec V1 e `required-head-v1.bin` restano invariati:
`cutover_id` puo' nominare un certificato V1 storico oppure un V2. La catena
verifica V1 per l'ancora e V2 per ogni passaggio che seleziona un contesto; un
V1 nuovo dopo l'ancora non puo' rendere avviabile una distribuzione che richiede
la transizione.

Il lettore verifica la catena completa
`required-head -> certificato -> context_transition_id -> record -> insieme`.
`request_id` conserva il proprio significato di identita' della richiesta del
coordinatore e deve coincidere fra giornale, certificato e record; non viene
usato come impronta indiretta di un altro documento.

Il record e l'insieme sono append-only: nome temporaneo esclusivo, rinomina
senza sostituzione, sincronizzazione e rilettura. Un nome gia' occupato e'
accettato soltanto se i byte coincidono. Predecessore, build, epoca, insieme,
inventario corrente e ricevute devono concordare fra record, certificato e
distribuzione installata.

`current_inventory_hash` e' il digest con dominio della sequenza canonica,
ordinata e senza duplicati di coppie `(contract_id, generation_id)`. Le coppie
devono essere identiche a quelle di `current_receipts` nel certificato; il
conteggio non partecipa come autorita' separata.

`dominant_startup_receipt` e' il digest restituito dall'involucro del gruppo 7
dopo la seconda lettura concordante sotto deployment lock, startup esclusivo e
manutenzione. I suoi binding V2 aggiungono `context_transition_id` ai fatti V1
gia' osservati. Il coordinatore accetta quel digest soltanto nella chiamata
interna dell'involucro, lo rende durevole in `CERTIFICATE_READY` e lo include
nel certificato: una ripresa deve ripresentare il valore identico.

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

Il percorso V2 e' relativo alla directory del contratto. `generation_digest`
e' `generation_id` senza `sha256:` e `context_digest` e'
`admission_context_id` senza `sha256:`; entrambi devono essere esattamente 64
cifre esadecimali minuscole. Il writer deriva il secondo valore
dall'autorizzazione Birth sigillata e il lettore lo deriva dal contesto
selezionato dalla testa: nessuna API pubblica accetta uno dei due come percorso
o selettore libero.

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
   soltanto prima della prima testa, e ricostruirne il materiale dalla
   `installation_root` della distribuzione verificata corrispondente;
3. riconoscere lo scostamento verso la nuova distribuzione;
4. riprendere o aprire la transazione di provisioning V2 riusando l'autore
   senza modificarlo;
5. costruire e rileggere nello staging il nuovo insieme, senza pubblicarlo e
   senza toccare `prepared-v1.json`;
6. acquisire la manutenzione, congelare l'inventario autenticato delle
   generazioni correnti e derivare record e `transition_id`;
7. registrare `PREPARED` legando richiesta, transazione di provisioning,
   predecessore, distribuzione, insieme target, byte attesi, inventario e
   `transition_id`, poi pubblicare l'insieme con rinomina senza sostituzione;
8. costruire dal nuovo insieme e dalla distribuzione verificata un nucleo di
   nascita sigillato e limitato alla riattestazione, senza installarlo come
   nucleo ordinario;
9. riattestare ogni generazione corrente nel percorso V2 mediante una nuova
   ricevuta Producer, poi rileggere entrambe le rappresentazioni;
10. ripetere il censimento e pretendere identita' identiche al punto 6;
11. registrare `RECEIPTS_COMPLETE` e pubblicare il record di transizione
    append-only;
12. dentro l'involucro dominante del gruppo 7, rileggere i suoi binding,
    consumare la capacita', costruire payload e firma e registrare
    `CERTIFICATE_READY` legando la sua ricevuta, il record, il payload e la
    firma;
13. pubblicare il certificato F4 V2 che lega `transition_id`,
    `request_id`, build e impronte delle ricevute;
14. pubblicare build e testa F4 append-only;
15. sostituire atomicamente il solo `required-head-v1.bin`;
16. rileggere catena, record, insieme, distribuzione, inventario e ricevute;
17. chiudere il giornale senza cancellare oggetti finali.

Nel primo passaggio, `CERTIFICATE_PUBLISHED` conserva il punto di non ritorno
dal regime precedente gia' stabilito dalla roadmap: un recupero deve
completare. Negli aggiornamenti successivi il punto di non ritorno e' il
confronto-e-scambio del punto 15. La selezione del contesto cambia sempre e
soltanto col punto 15; non esiste un commutatore parallelo.

## 8. Stati e ripresa

Il giornale F4 append-only mantiene senza aggiunte o rimozioni i sette stati
esistenti:

```text
PREPARED
RECEIPTS_COMPLETE
CERTIFICATE_READY
CERTIFICATE_PUBLISHED
BUILD_VERIFIED
HEAD_REQUIRED
PREFLIGHT_VERIFIED
```

I record V1 e V2 storici restano esatti. Un record coordinatore V3 aggiunge ai
campi V2 le identita' `provisioning_transaction_id`, `previous_set_id`,
`previous_admission_context_id`, `previous_context_epoch`, `target_set_id`,
`target_admission_context_id`, `target_context_epoch`,
`target_context_material_sha256`, `target_set_json_sha256`,
`context_transition_id`, `current_inventory_hash` e
`dominant_startup_receipt`.

La transazione di provisioning V2 e' interna e recuperabile: il suo header lega
`request_id`, build verificata, insieme precedente e inventario delle sorgenti;
non accetta questi valori dal chiamante e non pubblica `prepared-v1.json`.
Prima di `PREPARED` mantiene lo staging e il proprio inventario durevole. Il
coordinatore scrive `PREPARED` soltanto quando set, materiale, record di
transizione e inventario corrente hanno identita' complete; da quel momento
ogni ripresa accetta soltanto la stessa transazione e gli stessi byte.

| ultimo stato durevole | stato osservato | azione |
|---|---|---|
| nessuno | testa precedente valida | nuova transazione soltanto con autorizzazione completa |
| `PREPARED` | staging o insieme target concordante, testa vecchia | pubblicare/rileggere l'insieme e completare le riattestazioni vincolate |
| `RECEIPTS_COMPLETE` | inventario e ricevute concordanti, testa vecchia | pubblicare il record, preparare certificato e registrare le impronte |
| `CERTIFICATE_READY` | ricevuta dominante, record, payload e firma concordanti, testa vecchia | pubblicare il certificato esatto |
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

`request_id` conserva la derivazione gia' posseduta dal coordinatore F4.
`transition_id` copre richiesta, passaggio precedente, build nuova, insieme
precedente, nuovo insieme e inventario completo delle generazioni correnti.
Una ripetizione identica restituisce il risultato gia' verificato. Qualunque
differenza produce un conflitto nominato.

Una sola transizione può avanzare. Due richieste uguali convergono sullo stesso
risultato; due richieste diverse non possono entrambe pubblicare la sequenza
successiva. I lettori vedono una testa intera e verificata e scartano una
lettura se il selettore cambia durante l'acquisizione.

Per ogni riattestazione, `objective` e `request_id` Producer usano domini V2 e
includono almeno `(contract_id, generation_id, transition_id,
admission_context_id, context_epoch, source_id)`. La registrazione terminale,
la richiesta sigillata e la ricevuta devono concordare su questi valori. Una
richiesta V1 non viene riutilizzata per una ricevuta V2 e una seconda epoca non
puo' consumare la richiesta della prima.

## 10. Legame con F4

La distribuzione F4 usa una radice immutabile amministrativa, non `/opt/metnos`.
Il nuovo contesto viene costruito da quella radice dopo la verifica del suo
manifest. La build chiusa diventa avviabile soltanto se:

- il suo `closed_build_id` coincide con testa, certificato e record di
  transizione;
- `transition_id` ricalcolato sui byte del record coincide col certificato e
  `request_id` coincide fra record, certificato e giornale;
- insieme e materiale ricostruito coincidono col record;
- la prova delle ricevute coincide con l'inventario corrente congelato;
- la normale catena F4 di build, passaggio e testa richiede la stessa release.

Una build precedente alla testa richiesta non è un percorso di ritorno. Il
ritorno richiede una nuova release e una nuova transizione, entrambe con
sequenza superiore.

Il caricatore produttivo autentica prima la catena richiesta e ottiene da essa
la `VerifiedDistribution` sigillata. Solo dopo apre internamente una sessione
in sola lettura sulla sua `installation_root`, verifica nuovamente i file
richiesti e ricostruisce il materiale dell'insieme esatto nominato dal record.
Non usa `config.PATH_RUNTIME`, non riceve una radice dal chiamante e ripete la
lettura della testa dopo l'acquisizione: un cambio durante la lettura fa
scartare l'intera acquisizione.

`ContextSelectionV1` e' un risultato nominale non costruibile dal chiamante.
Ha due soli produttori privati: il lettore della catena richiesta crea la
selezione ordinaria; il coordinatore F4 crea una selezione staged, vincolata a
`transition_id` e capace soltanto di riattestare. Entrambi verificano record,
insieme e distribuzione prima di consegnare chiavi o materiale al bootstrap.
Il bootstrap non accetta dizionari, percorsi o identita' equivalenti.

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
16. ancora e certificato V1 storici seguiti da certificato V2, senza ripiego
    fra codec o domini;
17. interruzione della transazione di provisioning prima e dopo `PREPARED`,
    con ripresa soltanto dell'inventario esatto;
18. richiesta Producer V1 o di un'altra epoca non riutilizzabile in V2;
19. ricevuta dominante assente, diversa o proveniente da binding senza
    `context_transition_id` rifiutata anche in ripresa;
20. server completo e turni reali in copia dopo la transizione.

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

Le interfacce congelate sono minime:

- A consegna a runtime `ContextSelectionV1`, nominale e sigillato, contenente
  `transition_id`, `set_id`, `admission_context_id`, `context_epoch` e la
  distribuzione verificata; B non ricostruisce la selezione;
- B persiste e rilegge una ricevuta V2 soltanto per la coppia corrente e per
  l'identita' di contesto contenuta nell'autorizzazione sigillata;
- B deriva `objective` e `request_id` Producer V2 includendo la selezione di
  contesto e ne verifica la registrazione terminale autenticata;
- B restituisce ad A il `CurrentReceiptProof` ordinato di identita' e impronte;
  A verifica che le identita' producano `current_inventory_hash` e le lega nel
  certificato;
- A estende l'involucro dominante in V2 con `context_transition_id` e lega la
  ricevuta consumata nel record coordinatore e nel certificato V2;
- il lettore storico V1 resta distinto dal lettore corrente V2: nessun
  ripiego automatico da V2 a V1 e nessuna riscrittura delle ricevute V1.

## 13. Condizione B1

B1 è raggiunta soltanto quando entrambi gli agenti concordano, sugli stessi
commit, su:

- proprietario e fonte dell'autorizzazione;
- schema del record, legame con la testa F4 e identita' della richiesta;
- classificazione completa delle 12 dipendenze;
- punto di non ritorno e matrice di ripresa;
- interfacce fra nucleo e adattamento delle dipendenze;
- insieme minimo di prove non vacue.

Poiche' la roadmap §7.3 congela oggi il certificato V1 esatto, B1 comprende
anche un addendum alla roadmap che autorizzi il certificato V2, il record
coordinatore V3 e la transazione di provisioning V2 appena descritti. L'addendum
viene applicato soltanto dopo l'accettazione incrociata della stessa versione
di questa specifica.

Fino a B1 questo documento non autorizza modifiche al codice di prodotto.
