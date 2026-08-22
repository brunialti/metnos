# RM-0006 — Certificazione logica da capo a fondo

| Campo | Valore |
|---|---|
| Stato | `ready`; confini e criteri definiti, attuazione non iniziata |
| Creazione | 2026-08-22 |
| Ultima revisione | 2026-08-22 |
| Implementazione reale | Esistono suite di modulo, simulatore E2E, corpus di turni, equivalenza su piani reali e prove avversariali specialistiche; non esiste ancora una certificazione unica di 20-30 esperienze utente complete |
| Decisione di prodotto | La Fase 8 misura se Metnos raggiunge davvero il risultato richiesto, con autorita', collocazione, effetti e risposta corretti; non usa il numero di executor o di test come sostituto della qualita' |
| Nome storico | Fase 8 — «stress logico» |
| Origine | `internal/design/TODO.md::QUA-001` e §12 di `CLAUDE.mutabile.md` |
| Conservazione | Roadmap persistente fino a implementazione dimostrata o cancellazione esplicita |
| Riservatezza | Documento interno. Non va copiato in `docs/`, incluso nel Tutor o pubblicato |

## 1. Sintesi

La Fase 8 certifica Metnos come lo incontra una persona: dalla richiesta in
linguaggio naturale fino alla postcondizione osservabile. Non e' una prova di
carico e non ripete le verifiche specialistiche gia' presenti. Attraversa
insieme interpretazione, instradamento, piano, autorita', approvazione,
collocazione, executor, risposta, annullamento e recupero.

La certificazione usa **24 flussi d'oro**, ciascuno con formulazione italiana e
inglese. Un flusso e' un contratto logico, non una frase: puo' ammettere piu'
piani equivalenti, ma congela prima dell'esecuzione risultato, effetti,
divieti, postcondizioni e limiti. Due cicli completi sulla stessa revisione
devono dare lo stesso verdetto.

Il lavoro lungo viene eseguito in lotti da Metnos e dai suoi normali confini
HTTP, locali, remoti e durevoli. Un coordinatore deterministico prepara le
fixture, invia le richieste e raccoglie le prove. L'agente di revisione legge
il riepilogo di tutti i casi, ma apre le tracce estese soltanto per fallimenti,
divergenze e un campione di controllo. In questo modo il costo di ragionamento
del revisore dipende dai difetti, non dal numero totale di turni.

### Istruzione vincolante per chi attua la roadmap

Non ridefinire il successo osservando ciò che la versione sotto prova ha
prodotto. Per ogni caso l'oracolo deve essere congelato prima dei cicli. Metnos
puo' eseguire e misurare il lotto; non puo' essere l'unica autorita' che
stabilisce se la propria risposta e' corretta.

## 2. Perche' e' ancora necessaria

### 2.1 Copertura forte gia' disponibile

Al 22 agosto 2026 sono gia' dimostrati:

- 6.762 test della suite completa e 1.074 sottoprove;
- equivalenza su 804 piani reali del motore;
- prove LRE su 980 sorgenti, arresti reali, ripresa, concorrenza, recinzioni
  dei tentativi, disco pieno, database occupato e consegna remota satura;
- corpus deterministici separati per scelta degli executor, intenti, Tutor,
  Synt, sicurezza e crescita del pianificatore;
- simulatore E2E isolato che usa HTTP e storage realistico.

Queste prove restano autorevoli nei loro confini e vanno riusate. La Fase 8
non le ricopia in una seconda suite.

### 2.2 Vuoto verificato

La fotografia corrente del simulatore E2E mostra:

- 1.113 record nel corpus, dei quali 942 capostipiti dopo la deduplicazione;
- soltanto 7 casi raccolti dal controllo semantico della chat;
- i successi campionati dalla chat sono esplicitamente in sola lettura;
- i casi negativi controllano forma della risposta e forma della pipeline, ma
  non una postcondizione che dimostri la correzione dell'errore storico;
- il file dei criteri richiede almeno 280 test, mentre la suite ne raccoglie
  163; nel repository non e' conservato un esito corrente di due cicli
  qualificati.

Il conteggio di 280 non e' un obiettivo della nuova fase. Centinaia di prove
di pagina o di modulo non sostituiscono 24 risultati utente verificati. Il
nuovo criterio conta flussi logici e proprieta' coperte, non funzioni di test.

### 2.3 Cosa e' cambiato dopo il nome storico

«Stress logico» fu lasciato come sola etichetta dopo la chiusura del client
remoto. Da allora sono entrati multiutente, Tutor integrato, standard executor,
gestione dei servizi e LRE. La certificazione deve quindi comprendere anche:

- identita' e proprietario;
- scelta server/dispositivo e assenza di ripiego implicito;
- attesa di input o consenso e ripresa della stessa richiesta;
- lavori durevoli, ricevute immediate, arresto e ripartenza;
- distinzione visibile fra successo, parziale, attesa e fallimento;
- italiano e inglese come istanze distinte.

## 3. Valore per l'utente

La promessa verificata non e' «Metnos possiede molti strumenti». E':

1. capisce la richiesta abbastanza da scegliere il percorso giusto;
2. usa soltanto l'autorita' concessa e il dispositivo corretto;
3. porta a termine tutti gli effetti dichiarati, oppure dice esattamente cosa
   non e' avvenuto;
4. non presenta un risultato parziale come completo;
5. dopo consenso, input, disconnessione o riavvio riprende senza duplicare
   effetti ambigui;
6. permette di osservare e, dove dichiarato, annullare il risultato.

## 4. Confine della certificazione

### 4.1 Compreso

- Tutor, percorso diretto L0, piano riusato L1 e pianificatore ordinario;
- richieste singole e composte, con dati collegati fra passaggi;
- letture, creazioni, modifiche, cancellazioni controllate e annullamento;
- executor sul server e su un dispositivo posseduto;
- approvazioni, raccolta di input e ripresa della sessione;
- un lavoro LRE, stato, arresto, ripresa e artefatto finale;
- errori di provider, credenziale revocata, limite dichiarato, timeout e
  risultato parziale;
- italiano e inglese su istanze separate ma semanticamente equivalenti;
- tempi percepiti, numero di richieste di consenso e chiarezza della risposta.

### 4.2 Escluso

- aumentare il catalogo degli executor per far passare il corpus;
- rifare i confronti di scelta executor, Synt o intenti gia' coperti da
  laboratori propri;
- usare un modello linguistico come unico giudice di autorita', effetti,
  conteggi o postcondizioni;
- misurare mesi di esercizio reale: appartiene a `REL-001`;
- eseguire un audit di sicurezza indipendente: appartiene a `SEC-001`;
- inviare posta reale, cancellare dati reali o modificare calendari personali
  durante la matrice ordinaria;
- dichiarare affidabilita' universale per provider esterni sulla base di
  sostituti deterministici.

## 5. Matrice dei 24 flussi d'oro

Ogni riga sotto rappresenta una famiglia. I casi concreti vengono scelti da
turni reali anonimizzati, incidenti documentati e contratti correnti. Nessuna
frase privata entra nel repository.

| Famiglia | Casi | Proprieta' obbligatorie |
|---|---:|---|
| spiegazione e Tutor | 3 | confine EXPLAIN/ACT, fonte ammessa, nessuna azione sottratta al motore |
| lettura e selezione | 4 | executor, argomenti, completezza, limiti visibili |
| composizione e passaggio dati | 4 | ordine, riferimenti fra passi, nessun passo inventato o perso |
| mutazione, consenso e annullamento | 4 | autorizzazione, postcondizione, ricevuta, compensazione reale |
| dispositivo e proprietario | 3 | collocazione esatta, isolamento, nessun ripiego locale o fra utenti |
| dialogo e ripresa | 2 | attesa esplicita, input legato al mittente, una sola continuazione |
| lavoro durevole | 2 | presa in carico, stato, arresto, ripresa, un solo risultato committato |
| fallimento e risultato parziale | 2 | errore localizzato, conteggi onesti, nessun falso successo |
| **Totale** | **24** | tutte le superfici critiche almeno una volta |

Ogni flusso possiede due formulazioni naturali, `it` ed `en`, con la stessa
semantica. La matrice completa produce quindi 48 casi per ciclo e 96 casi nei
due cicli finali. Parafrasi aggiuntive sono diagnostiche e non possono
compensare un caso obbligatorio fallito.

## 6. Contratto dell'oracolo

Ogni caso vive come dato versionato e contiene almeno:

```yaml
case_id: mutazione-file-annullabile
logical_flow_id: mutation.undo.file
locale: it
request: "..."
fixture: isolated-files-v1
expected_route: engine
allowed_plans:
  - [find_files, delete_files, final_answer]
forbidden_tools: [delete_dirs]
expected_placement: owned_device
required_approval: true
expected_terminal: completed
required_effects: [target_absent]
forbidden_effects: [unrelated_path_changed]
postcondition_probes: [filesystem_digest, undo_round_trip]
response_requirements: [honest_count, device_visible]
budgets:
  max_approvals: 1
  deadline_s: 180
```

Lo schema reale sara' JSON con validazione deterministica. Lo YAML sopra e'
soltanto leggibilita'. `allowed_plans` ammette alternative dichiarate prima
della prova; non costringe il pianificatore a una sola sequenza quando due
sequenze hanno effetti equivalenti.

### 6.1 Ordine delle autorita'

1. Postcondizione ed effetti osservati.
2. Autorita', proprietario, collocazione e consenso.
3. Stato terminale e completezza contabilizzata.
4. Piano e collegamenti dei dati.
5. Risposta visibile.
6. Giudizio linguistico residuale.

Un testo convincente non puo' compensare una postcondizione falsa. Un piano
diverso ma ammesso puo' passare se autorita', effetti e risposta sono corretti.

### 6.2 Uso ammesso dei modelli

- Il normale modello locale partecipa alla richiesta perche' e' parte del
  prodotto sotto prova.
- Un secondo giudizio linguistico puo' valutare chiarezza o coerenza della
  prosa, usando una rubrica congelata.
- Il giudizio linguistico non decide mai effetti, consenso, proprietario,
  collocazione, conteggi, annullamento o recupero.
- Un caso bloccante non puo' dipendere soltanto dal giudizio dello stesso
  carico linguistico che ha prodotto la risposta.

## 7. Esecuzione in lotti e consumo di risorse

### 7.1 Divisione del lavoro

**Metnos e il coordinatore automatico eseguono:**

- preparazione e ripristino delle fixture isolate;
- invio dei 96 turni finali attraverso HTTP;
- raccolta di piani, eventi, approvazioni, effetti, tempi e postcondizioni;
- arresti e ripartenze controllati nei soli casi che li richiedono;
- confronto deterministico con l'oracolo;
- produzione di riepilogo, differenze e pacchetti dei fallimenti.

**Il revisore esegue:**

- congelamento iniziale degli oracoli e delle alternative ammesse;
- controllo del riepilogo completo;
- apertura di ogni fallimento e divergenza fra i due cicli;
- controllo a campione di almeno un successo per famiglia;
- classificazione della causa e verifica della correzione generale.

### 7.2 Perche' LRE non giudica LRE

LRE e' una superficie sotto prova. Puo' eseguire il caso durevole e conservare
la sua evidenza, ma il coordinatore della certificazione resta fuori dal
processo Metnos verificato. Se il coordinatore fosse un lavoro LRE, un guasto
del motore potrebbe interrompere o falsare anche il giudice del proprio guasto.

Il coordinatore puo' essere supervisionato dal sistema operativo e riprendere
da un registro append-only. Metnos fa il lavoro applicativo in lotti; il
coordinatore conserva l'indipendenza dell'oracolo.

### 7.3 Limiti di costo

- controllo rapido: 8 flussi in italiano, senza provider reali;
- controllo completo: 24 flussi in due lingue;
- certificazione: due controlli completi sulla stessa matrice;
- un solo caso attivo per slot LLM; niente parallelismo sulla GPU;
- scadenza per caso e scadenza globale obbligatorie;
- nessuna chiamata frontier predefinita;
- traccia estesa letta dal revisore soltanto per anomalie e campioni.

Con il modello locale corrente, 96 turni richiedono indicativamente da una a
tre ore di macchina; arresti, ripartenze e sonde reali possono portare il lotto
completo a circa quattro ore. E' tempo macchina, non tempo continuo del
revisore. Le misure reali sostituiranno questa previsione dalla prima
esecuzione.

## 8. Artefatti della prova

Ogni esecuzione crea una directory immutabile con:

```text
certification/<certification_id>/
  manifest.json
  cases.jsonl
  results.jsonl
  events.redacted.jsonl
  summary.json
  junit.xml
  failures/<case_id>/
```

`manifest.json` congela commit Git, impronta del catalogo, configurazione dei
carichi LLM, lingua, piattaforma, fixture e versione dell'oracolo. Il secondo
ciclo deve usare gli stessi valori. `summary.json` contiene soltanto misure e
puntatori; non copia richieste private, segreti o risultati sensibili.

Il pacchetto di un fallimento contiene il minimo necessario: richiesta
anonimizzata, piano, eventi rilevanti, differenza delle postcondizioni e
classificazione iniziale. I successi conservano la traccia redatta ma non la
duplicano nel riepilogo.

## 9. Metriche e criterio di uscita

RM-0006 passa a `implemented` soltanto con tutte queste condizioni:

1. 24 flussi congelati, ciascuno in italiano e inglese.
2. Due cicli consecutivi sulla stessa matrice e revisione.
3. Tutti i 96 casi obbligatori eseguiti; nessuna esclusione obbligatoria.
4. Zero falsi successi e zero successi senza postcondizione provata.
5. Zero violazioni di proprietario, autorita', consenso o collocazione.
6. Zero effetti vietati e zero ripetizioni automatiche di effetti ambigui.
7. Zero sessioni, dialoghi, invocazioni o lavori rimasti orfani oltre la
   scadenza dichiarata.
8. Ogni fallimento atteso e' localizzato, comprensibile e contabilizzato.
9. Annullamento e recupero superano le relative postcondizioni.
10. Tempi `p50` e `p95`, numero di approvazioni, riprese e chiamate modello
    rispettano i limiti preregistrati per famiglia.
11. Almeno quattro sonde sicure su servizi o dispositivi reali confermano che
    i sostituti non nascondono una differenza di contratto.
12. Il rapporto finale contiene impronte, limiti e riproduzione, e viene
    verificato da un soggetto diverso dal processo che ha eseguito i casi.

Un errore del prodotto non si risolve cambiando la richiesta o l'oracolo. Una
correzione modifica la causa generale, aggiunge una regressione mirata e poi
riesegue l'intera famiglia. I due cicli finali ricominciano soltanto dopo che
il codice e la matrice sono di nuovo congelati.

## 10. Fasi di attuazione

Per evitare ambiguita' con la Fase 8 generale e con F8 di altre roadmap, le
fasi interne usano il prefisso `C` di certificazione.

### C0 — Fotografia e contratto degli artefatti

- fotografare suite, corpus, configurazione e superfici correnti;
- definire schema `CaseSpec`, `CaseResult` e `CertificationManifest`;
- rendere impossibile un verdetto senza postcondizione per i casi di successo;
- produrre un lotto fittizio riprendibile senza chiamare Metnos.

**Uscita:** schema validato, registro append-only e rapporto deterministico su
fixture sintetica.

### C1 — Selezione e congelamento dei 24 flussi

- estrarre candidati dal corpus anonimizzato e dagli incidenti documentati;
- coprire tutte le famiglie della matrice senza sovrappeso del dominio file;
- scrivere oracoli, alternative, effetti vietati e limiti prima dei risultati;
- revisione manuale delle richieste per naturalezza e assenza di dati privati.

**Uscita:** 24 contratti approvati, 48 formulazioni e rapporto di copertura.

### C2 — Coordinatore isolato

- estendere `tests/e2e/` con esecuzione riprendibile e scadenze finite;
- usare soltanto HTTP e superfici pubbliche per comandare il Metnos sotto
  prova; le sonde leggono direttamente solo fixture e registri di test;
- ripristinare ogni fixture e provare che lo stato reale non cambia;
- produrre gli artefatti del §8 e un riepilogo compatto.

**Uscita:** gli 8 casi rapidi completano due cicli identici senza intervento.

### C3 — Percorsi logici e interattivi

- abilitare Tutor, L0, L1, planner, composizione, approvazioni e dialoghi;
- verificare piano, collegamenti, risposta e postcondizione;
- introdurre italiano e inglese come istanze separate;
- vietare che indisponibilita' del modello o della rete diventino esclusioni
  silenziose nei casi obbligatori.

**Uscita:** 19 flussi non durevoli verdi in un ciclo completo diagnostico.

### C4 — Dispositivo, LRE e condizioni avverse

- collegare un dispositivo simulato e una sonda controllata su dispositivo
  posseduto;
- attraversare presa in carico LRE, arresto, ripresa e artefatto;
- iniettare timeout, revoca, limite, errore di provider e risultato parziale;
- verificare assenza di ripiego, duplicazioni ed elementi orfani.

**Uscita:** tutti i 24 flussi verdi in un ciclo diagnostico.

### C5 — Sonde reali e correzioni generali

- eseguire almeno quattro sonde non distruttive su confini reali;
- classificare ogni difetto per causa, non per frase;
- correggere il punto comune e aggiungere una regressione mirata;
- ricongelare revisione e matrice dopo l'ultima correzione.

**Uscita:** zero difetti aperti bloccanti o alti e nessuna differenza di
contratto fra sostituto e sonda reale.

### C6 — Certificazione finale

- eseguire i 96 casi in due cicli consecutivi;
- verificare automaticamente i criteri del §9;
- far controllare riepilogo, fallimenti e campione di successi;
- pubblicare soltanto il comportamento realmente implementato; il corpus e il
  rapporto dettagliato restano interni;
- aggiornare §12 di `CLAUDE.mutabile.md` e lo stato di questa roadmap.

**Uscita:** rapporto finale riproducibile e stato `implemented`.

## 11. Rischi e contromisure

| Rischio | Contromisura |
|---|---|
| Oracolo copiato dall'uscita corrente | congelamento prima dei cicli e revisione separata |
| Stesso LLM produce e approva | postcondizioni deterministiche; giudizio linguistico solo residuale |
| Suite grande ma poco rappresentativa | 24 flussi per matrice di proprieta', non soglia di conteggio test |
| Dati personali nel corpus | anonimizzazione, fixture sintetiche e controllo manuale prima del commit |
| Provider reale rende la suite instabile | sostituti per la matrice, quattro sonde separate e fallimento distinto |
| Correzioni sulla frase | causa generale, test mirato e riesecuzione dell'intera famiglia |
| Prova che modifica l'esercizio | storage isolato, impronte pre/post e nessun effetto reale ordinario |
| Esecuzione interrotta | registro append-only, ripresa per caso e nessun rilancio cieco |
| Costo GPU eccessivo | un caso per slot, controllo rapido, nessun frontier e tracce aperte su richiesta |
| Confusione con affidabilita' nel tempo | REL-001 resta separata e usa mesi di dati reali |

## 12. Riuso dopo la Fase 8

La certificazione non e' una campagna monouso:

- gli 8 casi rapidi diventano controllo ordinario prima di una versione;
- i 24 flussi completi vengono eseguiti dopo cambi a pianificatore, autorita',
  collocazione, dialoghi, LRE o modelli;
- i dati aggregati alimentano REL-001 senza trasferire richieste private;
- un nuovo incidente entra prima come regressione mirata e soltanto se copre
  una proprieta' nuova puo' sostituire, non semplicemente aggiungersi, a un
  flusso della matrice.

## 13. Riferimenti

- `CLAUDE.md` §2.8, §8.2 e §8.5
- `CLAUDE.mutabile.md` §12
- `internal/design/TODO.md::QUA-001`
- `internal/design/TODO.md::REL-001`
- `internal/design/TODO.md::SEC-001`
- `tests/e2e/README.md`
- `tests/e2e/quality_targets.json`
- `tests/e2e/scenarios/test_chat_quality.py`
- `tests/e2e/corpus/corpus.sqlite`
- `tests/runtime/infra/test_guard_corpus_equivalence.py`
- `internal/reports/rm0004-f12-verification-20260822.md`
- `internal/reports/rm0004-f14-verification-20260822.md`
