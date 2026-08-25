# RM-0008 — Porta unica di nascita e ciclo controllato degli executor sintetizzati

> `RM-0008` · stato `active` · definita `2026-08-25` · conclusioni di
> principio approvate dall'utente · specifica candidata alla revisione
> adversarial · nessuna implementazione autorizzata

## 1. Esigenza

Metnos dispone già di molti controlli sugli executor e, con RM-0007, di un
publisher immutabile, autenticato e atomico. Questi controlli non formano però
ancora una sola porta d'uscita. Un generatore, un importatore o uno strumento di
manutenzione può preparare e pubblicare un executor seguendo sequenze
parzialmente diverse. La qualità finale dipende quindi anche dal percorso che
ha prodotto il candidato.

Il problema è più grave per gli executor sintetizzati: lo stesso modello può
proporre scopo, codice, test e capacità. Se il runtime tratta queste
dichiarazioni come prova, il generatore finisce per certificare il proprio
lavoro. Inoltre, un executor di sola lettura può sembrare corretto nei test e
fallire soltanto su una richiesta reale dell'utente.

RM-0008 deve realizzare due componenti coordinate ma distinte:

1. una **porta deterministica comune** a ogni executor nuovo o modificato;
2. un **ciclo di revisione e preesercizio riservato agli executor
   sintetizzati**.

La seconda componente non si applica agli executor builtin, core, importati o
scritti direttamente da una persona. Questi attraversano la stessa porta
deterministica e conservano i propri confini di provenienza, revisione e firma,
ma non entrano nel ciclo Synt descritto nei §§8-12.

## 2. Stato del codice verificato

Al 25 agosto 2026 risultano già disponibili:

- l'inventario strutturale e neutro dei manifest di RM-0002;
- l'Executor Standard, il linter, i controlli di capacità, collocazione, undo,
  piattaforma, code root, digest e firma;
- il deposito a generazioni immutabili e il publisher unico di basso livello
  di RM-0007;
- candidati Synt con lifecycle non attivo e un promoter separato;
- test di nascita dichiarati nei manifest;
- workload logici e routing centralizzato dei modelli;
- audit e stati di rifiuto, quarantena e ritiro già presenti in più componenti.

Restano invece difetti architetturali misurati:

1. più produttori operativi possono raggiungere direttamente il publisher;
2. l'evidenza di ammissione non è sempre legata all'impronta esatta del
   candidato;
3. alcuni controlli Synt indisponibili o in errore degradano a warning;
4. il promoter può fidarsi dello stato generico `synthesized` senza verificare
   una ricevuta completa e non obsoleta;
5. test e implementazione proposti dallo stesso modello non sono indipendenti;
6. i test sintetizzati possono dichiarare setup e teardown shell che il runner
   storico esegue fuori dalla sandbox reale;
7. il vecchio percorso `Synt.approve_proposal()` non rappresenta in modo
   coerente una nascita operativa verificata;
8. non esiste uno stato di preesercizio con regole e uscita chiaramente
   definite;
9. il feedback negativo dell'utente non è legato in modo obbligatorio alla
   generazione e alla richiesta che hanno fallito;
10. correzione, quarantena, ritiro e cancellazione dei sintetizzati non
    condividono ancora una politica di conservazione chiusa.

Questi difetti non riaprono RM-0002 o RM-0007. RM-0002 controlla le invarianti
linguistiche; RM-0007 pubblica byte già ammessi. RM-0008 possiede la decisione
che precede il publisher.

## 3. Conclusioni già approvate

Le seguenti conclusioni sono vincolanti per la progettazione successiva.

### 3.1 Il modello non implementa la porta

> Il modello locale produce soltanto un candidato nel formato richiesto. Se il
> candidato viene respinto, può tentare di correggere gli errori strutturati.
> Non può ampliare i propri poteri, dichiararsi conforme, firmare, approvare o
> pubblicare.

La porta è codice runtime versionato. I prompt possono spiegare il formato del
candidato, ma non contengono la politica autorevole.

### 3.2 Generazione prima, certificazione dopo

Per gli executor sintetizzati:

1. la generazione preferisce template e primitive già ammesse;
2. i test di certificazione non sono scritti esclusivamente dallo stesso
   modello che ha prodotto il codice;
3. codice nuovo o nuovi poteri richiedono approvazione umana esplicita.

### 3.3 Il solo giudizio non deterministico è l'allineamento

Le regole strutturali, i digest, le capacità, la firma, il lifecycle, la
pubblicazione e l'esecuzione dei test sono deterministici. Per un executor
sintetizzato resta un solo giudizio non deterministico: verificare che il
codice realizzi davvero lo scopo assegnato senza effetti non dichiarati.

Questo giudizio è affidato a un workload di livello alto, non inferiore al tier
`wise`; un modello frontier resta ammesso. Il revisore legge obiettivo,
contratto e codice e prova, quando possibile, a costruire almeno un caso
input→output indipendente e simulabile. Se l'allineamento non è dimostrabile,
chiede una decisione umana; non sostituisce l'incertezza con un'approvazione
automatica.

### 3.4 Il riesame riguarda soltanto i sintetizzati

Revisione semantica, test indipendenti generati dal revisore, preesercizio,
riesame frontier dopo un errore e correzione automatica appartengono solo agli
executor sintetizzati. Non vanno applicati a builtin, core, importati o
executor scritti direttamente da una persona.

## 4. Non obiettivi

RM-0008 non deve:

- sostituire il publisher RM-0007;
- duplicare il linter o il registro i18n;
- creare un package manager universale;
- rivalutare semanticamente tutti gli executor esistenti;
- riscrivere i test storici di builtin e core come conseguenza indiretta;
- concedere a un LLM credenziali, firma o accesso al publisher;
- introdurre liste di nomi executor, eccezioni di dominio o regole per una
  lingua specifica;
- dichiarare sicuro codice arbitrario con una sola risposta di un modello;
- usare account, rete o dati personali reali per certificare un sintetizzato;
- accumulare indefinitamente revisioni sintetizzate rifiutate.

Il legame fra byte verificati e byte eseguiti fino all'invocazione resta di
`EXEC-BIND-001`. La porta deve consumare la garanzia disponibile senza fingere
di estenderla.

## 5. Matrice di applicazione

| Provenienza attestata | Porta deterministica | Revisione LLM | Preesercizio | Riesame frontier su feedback | Revisione automatica |
|---|---:|---:|---:|---:|---:|
| core | sì | no | no | no | no |
| builtin | sì | no | no | no | no |
| importato | sì | no | no | no | no |
| scritto da una persona | sì | no | no | no | no |
| sintetizzato da Metnos | sì | sì | solo se idoneo | sì | possibile, come nuovo candidato |

La provenienza non può essere una stringa scelta nel manifest. Deve derivare
da una ricevuta emessa dal produttore autorevole o dall'inventario strutturale.
Il candidato può dichiarare metadati descrittivi, ma non può trasformarsi da
`synthesized` a `human_authored` né da `imported` a `builtin`.

La revisione di un executor sintetizzato conserva la provenienza
`synthesized`. Non diventa “scritto da una persona” perché una persona ne ha
approvato una versione.

## 6. Architettura KISS candidata

```text
produttore attestato
        |
        v
candidato non operativo e immutabile
        |
        v
porta deterministica comune
        |
        +---- se sintetizzato ----> revisione semantica indipendente
        |                              | test isolati
        |                              | approvazione se richiesta
        |                              v
        |                         ammesso / preesercizio / quarantena
        |
        v
publisher RM-0007
        |
        v
generazione autenticata selezionata dal loader
```

La soluzione introduce un solo modulo pubblico, indicativamente
`runtime/executor_birth.py`, con una sola operazione produttiva:

```python
birth_executor(request: BirthRequest) -> BirthResult
```

Il modulo orchestra controlli già esistenti e infine chiama RM-0007. Non
ricopia l'implementazione di firma, linter, inventario, standard o store.

I publisher di basso livello restano accessibili soltanto a:

- `executor_birth`, per una nascita o revisione;
- RM-0007, per pubblicazione linguistica deterministica;
- ritiro e rollback verso generazioni già ammesse;
- cutover legacy una tantum;
- strumenti offline che non modificano lo stato vivo.

Un controllo statico del confine deve fallire se compare un nuovo chiamante
operativo non censito. La Birth Gate non si considera implementata finché
esiste un bypass vivo.

## 7. Candidato e identità

La porta fotografa sotto il lock del catalogo:

- `manifest.toml`;
- `manifest.lang_state.json`;
- ogni file dichiarato in `[code].files`;
- provenienza attestata e obiettivo originale;
- generazione attiva precedente, se esiste;
- versioni delle regole;
- prove e approvazioni applicabili.

L'identità del contenuto è uno SHA-256 con framing length-delimited di percorso
relativo e byte. Timestamp, path assoluti, nome del modello e testo dell'audit
non entrano nell'impronta.

Ogni prova, parere o approvazione riporta questa impronta. Prima del commit la
porta la ricalcola: se cambia anche un solo byte, l'esito è
`candidate_changed` e tutte le evidenze precedenti diventano obsolete.

La classificazione minima della modifica è:

| Classe | Condizione | Controlli comuni | Ramo Synt se provenienza sintetizzata |
|---|---|---:|---:|
| `first_birth` | nessun predecessore | sì | sì |
| `code_revision` | cambia codice o elenco file | sì | sì |
| `authority_revision` | cambia autorità o politica operativa | sì | sì |
| `contract_revision` | cambia schema o comportamento dichiarato | sì | sì |
| `localization_revision` | cambiano solo risorse i18n ammesse | RM-0002 + RM-0007 | no |
| `equivalent_republish` | contenuto operativo identico | sì, idempotente | no nuovo parere |

Ritiro e rollback non sono nascite: possono soltanto rimuovere autorità o
selezionare una generazione già ammessa e autenticata.

## 8. Controlli deterministici comuni

Ogni provenienza deve superare, nell'ordine:

1. identità strutturale e unicità del `ContractId` e del nome;
2. formato TOML UTF-8 e Executor Standard;
3. transizione lifecycle ammessa;
4. schema completo di argomenti e risultato;
5. nome nel vocabolario canonico;
6. elenco chiuso dei file, containment nei code root e digest;
7. AST/import e punto di ingresso dichiarato;
8. capacità note e nessun ampliamento implicito;
9. collocazione, piattaforme, sandbox e politica di esecuzione;
10. effetto e contratto undo coerenti;
11. invarianti e copertura linguistica di RM-0002;
12. test dichiarati non vuoti e matcher noti;
13. prove di non interferenza sul corpus di routing applicabile;
14. impronta ancora identica;
15. pubblicazione atomica RM-0007;
16. rilettura autenticata della generazione appena selezionata.

`failed` e `unavailable` bloccano entrambi. Il chiamante riceve errori
strutturati e può correggere il candidato; non può trasformare un controllo
indisponibile in un warning favorevole.

La certificazione è binaria: passa soltanto quando tutti i controlli e i test
obbligatori applicabili sono verdi. Provenienza, componente o fase che ha
prodotto un errore servono alla diagnosi, ma non possono ridurne la gravità né
consentire una certificazione parziale.

Le regole dipendono da schema, registri e proprietà dichiarate, non da nomi di
executor, directory storiche o cataloghi mantenuti a mano.

## 9. Generazione Synt

Synt deve preferire, nell'ordine:

1. template runtime versionati;
2. primitive e helper già ammessi;
3. composizione di contratti esistenti;
4. codice nuovo soltanto quando i primi tre mezzi non bastano.

Il modello può proporre nome, descrizione, schema, implementazione, capacità e
test. Sono tutti dati non fidati. Lifecycle, origine, autorità effettiva,
approvazione, firma e pubblicazione sono proprietà del runtime.

Una capacità proposta non viene concessa dal prompt né dal manifesto
candidato. La porta la convalida contro il registro e, quando è nuova o
aumenta i poteri del sintetizzato, richiede l'approvazione umana descritta nel
§11.

## 10. Revisione semantica dei soli sintetizzati

Il workload logico è `executor.birth.semantic_review`, con tier minimo `wise`.
Il router può usare un modello frontier e deve farlo quando rischio,
complessità o incertezza superano le soglie registrate. Non è ammesso un
fallback silenzioso a tier inferiori.

Il revisore riceve:

- obiettivo originale;
- contratto normalizzato;
- codice;
- eventuale predecessore;
- template e primitive dichiarati;
- risultati dei controlli deterministici.

Non riceve chiavi, credenziali, publisher o dati reali. Produce un risultato
tipizzato:

```text
verdict: aligned | misaligned | uncertain
observed_effects: lista chiusa
undeclared_effects: lista chiusa
reason: testo breve
tests: casi input -> output tipizzati
confidence: 0..1, informativa
```

- `misaligned` porta in quarantena;
- `uncertain` blocca e chiede una decisione umana;
- `aligned` consente di continuare, ma non concede autorità e non sostituisce
  l'approvazione richiesta.

Il revisore deve essere logicamente distinto dalla generazione. Se il router
non può offrire un binding indipendente, registra `review_not_independent` e
impedisce l'attivazione automatica.

## 11. Test indipendenti e approvazione dei sintetizzati

I test prodotti dal generatore sono suggerimenti. Il revisore aggiunge almeno
un caso non copiato quando il comportamento è simulabile. Il runtime valida il
caso e confronta deterministicamente risultato effettivo e atteso.

Il candidato sintetizzato viene eseguito soltanto:

- in una sandbox realmente attiva;
- senza rete, credenziali o dati personali reali;
- in una directory temporanea privata;
- con limiti di tempo, memoria e output;
- con fixture dichiarative ammesse dal runtime;
- senza setup o teardown shell proposti dal modello.

L'impossibilità di costruire un ambiente simulato è
`test_environment_unavailable`, non un successo.

Per un sintetizzato l'approvazione umana è obbligatoria quando:

- nasce codice nuovo;
- cambia almeno un byte di codice;
- cambia il comportamento operativo;
- nasce o aumenta un potere;
- il revisore restituisce `uncertain`.

L'approvazione mostra obiettivo, diff, autorità, test, parere e impronta. È
legata all'impronta, ha durata limitata e viene invalidata da qualsiasi
modifica del candidato.

## 12. Preesercizio dei soli sintetizzati

Un executor sintetizzato può entrare in preesercizio soltanto se:

1. dichiara effetto non mutante;
2. tutte le capacità effettive sono classificate dal registro come idonee al
   preesercizio;
3. non usa rete reale, credenziali, comandi generici o dati per cui una risposta
   errata possa causare un danno operativo;
4. ha superato porta deterministica, revisione semantica e test isolati;
5. la sua generazione esatta è riconoscibile in ogni esito e feedback.

L'idoneità non è ricavata dal nome della capacità. Il registro delle capacità
deve esporre una proprietà generale e conservativa; per capacità nuove il
valore predefinito è `false`.

Il preesercizio non equivale ad attivazione piena:

- è visibile soltanto nel perimetro autorizzato;
- non può essere riusato in piani durevoli o cache che sopravvivono alla sua
  generazione;
- ogni risposta dichiara internamente executor e generazione;
- un feedback negativo lo rimuove immediatamente dal percorso utilizzabile.

La revisione adversarial deve scegliere fra due rappresentazioni e respingere
quella che introduce più autorità o più cache incoerenti:

1. lifecycle firmato `preexercise` nel contratto;
2. stato di ammissione separato, autenticato e legato alla generazione.

La prima è oggi preferita perché rende lo stato visibile nello stesso contratto
che il loader autentica; la seconda è ammissibile soltanto se dimostra di non
creare un secondo catalogo autorevole. La scelta deve precedere lo sviluppo.

## 13. Feedback, riesame frontier e fine vita

Quando l'utente marca come errata una risposta prodotta da un executor
sintetizzato in preesercizio:

1. il runtime lega il feedback a query, argomenti, risultato, executor e
   generazione esatti;
2. quella generazione viene nascosta e posta in quarantena prima di qualsiasi
   riesame;
3. parte il workload obbligatorio `executor.birth.failure_review`, con tier
   `frontier` e senza fallback inferiore;
4. il revisore confronta obiettivo, codice, contratto e caso fallito;
5. l'esito è `false_feedback`, `repairable`, `misaligned` oppure `uncertain`;
6. se il frontier non è disponibile, la generazione resta in quarantena.

`repairable` può produrre una proposta di correzione, ma la correzione è un
executor nuovo: nuova impronta, nuovi controlli, nuova revisione, nuovi test e
nuova approvazione quando richiesta. Nessuna patch modifica la generazione che
ha fallito.

Le quarantene hanno una politica di conservazione per stato e tempo. Una
generazione con audit o feedback aperto non viene cancellata; una generazione
chiusa e superata viene rimossa dopo una finestra configurata. La pulizia è
idempotente, conserva una ricevuta minima e non usa liste di nomi.

## 14. Rapporto di nascita

Ogni esito produce un rapporto append-only bounded con:

- versione della porta;
- provenienza attestata;
- impronta candidata;
- `ContractId`, nome e classe della modifica;
- generazione precedente e nuova;
- versione di ogni regola;
- controlli `passed|failed|unavailable`;
- riferimenti di generatore e revisore senza contenuti segreti;
- hash e risultati dei test;
- approvazione, se richiesta;
- firma, generazione pubblicata e risultato del reload;
- eventuale stato di preesercizio o quarantena.

Il rapporto è evidenza, non una nuova autorità crittografica. Loader e runtime
continuano a fidarsi del contratto firmato, della generazione RM-0007 e delle
autorità operative già censite.

## 15. Rischi principali

| Rischio | Gravità | Contromisura richiesta |
|---|---:|---|
| il modello si autocertifica | bloccante | politica e publisher non accessibili al generatore |
| provenienza falsificata | bloccante | ricevuta strutturale non controllata dal candidato |
| bypass del publisher | bloccante | guardia statica con unico owner operativo |
| evidenza su byte diversi | bloccante | impronta ricalcolata prima del commit |
| controllo indisponibile trattato come pass | bloccante | `unavailable` fail-closed |
| test e codice condividono lo stesso errore | alta | caso indipendente e revisore distinto |
| test sintetizzato esce dalla sandbox | bloccante | fixture dichiarative e sandbox reale |
| “sola lettura” espone dati o rete | alta | idoneità data-driven, default deny |
| preesercizio contamina cache o piani | alta | identità di generazione e divieto di riuso durevole |
| feedback analizza una revisione diversa | alta | binding a query, risultato e generazione |
| frontier assente provoca promozione | alta | quarantena persistente, nessun fallback |
| quarantene si accumulano | media | retention state/time based e ricevuta minima |
| porta troppo grande e lenta | alta | orchestrazione di controlli esistenti, publisher atomico separato |
| builtin/core entrano per errore nel ciclo Synt | alta | matrice di provenienza e test negativi dedicati |

## 16. Fasi proposte dopo la convergenza adversarial

### B0 — Censimento e caratterizzazione

- enumerare ogni produttore, modificatore, firma, publisher, rollback e ritiro;
- dimostrare i bypass correnti con test che inizialmente falliscono;
- classificare la provenienza senza euristiche di percorso;
- congelare la matrice di compatibilità.

### B1 — Candidato immutabile e provenienza

- introdurre `BirthRequest`, `CandidateSnapshot` e impronta canonica;
- emettere ricevute di origine dai produttori autorizzati;
- provare cambio di byte, cambio di origine e collisioni.

### B2 — Porta deterministica comune

- comporre standard, linter, capacità, undo, code root, test e RM-0002;
- rendere fail-closed ogni errore e indisponibilità;
- chiamare RM-0007 una sola volta dopo l'ammissione.

### B3 — Chiusura dei bypass

- migrare i produttori uno alla volta;
- restringere il publisher di basso livello;
- aggiornare il censimento statico e `CLAUDE.md` nello stesso cutover.

### B4 — Ramo Synt

- template/primitive;
- workload di revisione semanticamente indipendente;
- test isolati e approvazioni legate all'impronta;
- eliminazione dei fail-open e del vecchio percorso incoerente.

### B5 — Preesercizio e feedback

- adottare la rappresentazione scelta al §12;
- applicare l'idoneità data-driven;
- quarantena immediata, review frontier e nuova revisione;
- retention e cancellazione controllata.

### B6 — Migrazione e certificazione

- nessuna attivazione implicita dei candidati esistenti;
- migrazione solo tramite nuova ammissione;
- suite completa, concorrenza, crash, Windows/Linux dove applicabile;
- due cicli di routing e prova live controllata;
- documentazione italiana/inglese e distribuzione pubblica.

Ogni fase richiede un commit autonomo e un gate verde. Lo sviluppo non parte
finché la revisione adversarial non converge e l'utente non approva la
specifica risultante.

## 17. Criteri di completamento

RM-0008 passa a `implemented` soltanto quando:

1. ogni nascita o revisione operativa attraversa la porta unica;
2. un accesso diretto non censito al publisher fallisce il test statico;
3. ogni evidenza è legata all'impronta esatta;
4. `failed` e `unavailable` bloccano;
5. nessun modello può concedere autorità, firmare o pubblicare;
6. builtin, core, importati e user-authored non entrano nel ciclo Synt;
7. ogni sintetizzato attivabile ha revisione indipendente e test isolati;
8. shell, rete, credenziali e dati reali non sono disponibili ai test Synt;
9. codice o poteri nuovi di un sintetizzato richiedono approvazione esatta;
10. solo sintetizzati idonei e non mutanti possono entrare in preesercizio;
11. un feedback negativo li nasconde prima del riesame frontier;
12. una correzione produce sempre un candidato nuovo;
13. quarantena e retention non perdono l'audit necessario;
14. la generazione pubblicata viene riletta e autenticata;
15. retry identici sono idempotenti;
16. cutover, rollback e riavvio non riaprono bypass legacy;
17. suite completa e due cicli di routing sono verdi;
18. roadmap, ADR, `CLAUDE.md` e documentazione pubblica sono allineati.

Passa a `closed` dopo distribuzione, prova sull'installazione di riferimento e
assenza di attività residua.

## 18. Domande per la revisione adversarial

1. La provenienza attestata può essere ridotta a una sola struttura senza
   creare una nuova autorità persistente?
2. Esiste ancora un percorso vivo che pubblica senza la porta?
3. Quale controllo elencato duplica inutilmente RM-0002 o RM-0007?
4. Il lifecycle firmato `preexercise` è davvero più semplice dello stato
   separato?
5. Come si impedisce a un sintetizzato “read-only” di esfiltrare informazioni?
6. La proprietà di idoneità appartiene al registro capacità o a una policy più
   generale?
7. Il reviewer `wise` può essere indipendente se condivide provider o modello
   fisico con il generatore?
8. Quale evidenza minima consente una revisione frontier riproducibile senza
   conservare dati personali non necessari?
9. La quarantena immediata può interrompere piani già in volo e con quale esito
   tipizzato?
10. La retention proposta è sufficiente per evitare sia accumulo sia perdita
    di prova?
11. Il controllo statico copre chiamate indirette, alias e import dinamici?
12. Quale requisito può essere eliminato mantenendo tutte le invarianti?

## 19. Registro

| Data | Stato | Evento |
|---|---|---|
| 2026-08-25 | `active` | raccolte le conclusioni approvate sulla Birth Gate |
| 2026-08-25 | `active` | separato il ramo Synt: revisione e preesercizio non si applicano a builtin, core, importati o user-authored |
| 2026-08-25 | `active` | specifica candidata pronta per il primo giro adversarial; sviluppo non autorizzato |
