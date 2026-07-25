# RM-0003 — Tutor integrato: guida operativa intelligente

**Stato:** `in_progress`  
**Creazione:** 2026-07-23  
**Ultima revisione:** 2026-07-24 (notte)  
**Implementazione reale:** F2 implementato, verificato in test e in replay live
mirati sul boundary HTTP; correttore di bozze deterministico post-composizione
attivo (§5.7). Certificazione per-corpus: gate a radice flessiva e skip
documentati (§5.6); f1_equivalence 30/38 non-fail, boundary 12/12; la
ri-certificazione completa col correttore e un turno live Telegram restano da
completare prima del ritiro delle schede. F3 e F4 sono progettati, non
implementati.  
**Conservazione:** persistente fino a implementazione dimostrata o cancellazione
esplicita di Roberto.  
**Decisione di prodotto:** il Tutor deve rispondere a domande imprevedibili per
forma e contenuto usando esclusivamente conoscenza locale ammessa, senza
diventare un secondo planner e senza richiedere schede precompilate per ogni
executor.  
**ADR:** 0197 (fondazione semantica), 0198 (compilatore F2 e superamento F1).  
**Fonti verificate:** `runtime/tutor/`, `runtime/published_docs.py`, `docs/`,
manifest ammessi dal loader, `tutor/sources.toml`,
`runtime/services_registry.py`, `runtime/devices.py`,
`runtime/recurring_tasks.py`, `runtime/dialog_pending.py`,
`runtime/change_intents.py`, `runtime/turn_feedback.py`, scheduler centrale.

## 1. Esito della revisione

F1 ha dimostrato tre proprietà utili: boundary pre-planner, isolamento di
autorità e procedure critiche letterali. Non è però un Tutor sufficiente:
dipende da sei schede curate, richiede manutenzione quando cambia il catalogo e
copre soltanto domande anticipate.

F2 deve quindi **sussumere F1**, non aggiungersi come ripiego subordinato. Il
motore corrente costruisce un unico catalogo conoscitivo da:

1. descrizioni multilingue generali e dei singoli argomenti dei manifest
   effettivamente ammessi dal loader;
2. ogni HTML indicizzabile nell'esatta radice locale pubblicata `docs/`;
3. guide curate, considerate semplici fonti ad alta autorità;
4. vettori BGE-M3 locali, conservati con le fonti nello stesso artefatto
   SQLite firmato.

Le schede F1 rimangono temporaneamente per procedure ad alta criticità e come
corpus di equivalenza. Non sono più il prerequisito per rispondere e possono
essere ritirate quando F2 supera il loro insieme di accettazione.

La direzione è confermata con quattro vincoli:

- nessun hardcoding di frasi, sinonimi o nomi executor nel routing Tutor;
- nessuna scansione arbitraria del filesystem: il solo confine automatico è
  la radice `docs/` effettivamente pubblicata; nessun accesso ai dati utente;
- il modello locale formula, ma non amplia, il contesto recuperato;
- una richiesta operativa dubbia ricade nel motore normale: il Tutor non la
  sottrae.

## 2. Valore per l'utente

Il Tutor deve rendere Metnos comprensibile dalla chat:

- spiegare capacità installate e limiti reali;
- fornire procedure UI esatte quando la fonte è ammessa;
- adattare forma e lingua della risposta alla domanda;
- distinguere conoscenza pubblica e amministrativa;
- dichiarare una lacuna invece di completarla con conoscenza plausibile;
- in futuro osservare stato live e consegnare un'azione al normale motore,
  soltanto su conferma.

La metrica principale non è il numero di risposte. È la combinazione di
copertura utile, fedeltà alle fonti e **zero richieste operative sottratte**.

## 3. Stato del codice verificato

### 3.1 F2 già implementato

Il codice corrente comprende:

- `runtime/published_docs.py`: inventario bounded condiviso della radice
  pubblicata, con validazione di lingua, canonical, hreflang e confini;
- `runtime/tutor/sources.py`: compilazione bounded di unità conoscitive;
- `tutor/sources.toml`: registro chiuso delle sole fonti supplementari non
  pubbliche;
- `runtime/tutor/catalog.py`: catalogo schema 3, firma Ed25519, swap atomico,
  last-known-good e riuso incrementale dei vettori invariati;
- `runtime/tutor/semantic.py`: ranking unico di schede, documentazione e
  manifest con segnale denso più sovrapposizione lessicale derivata dai testi;
- `runtime/tutor/mode.py`: classificatore locale chiuso
  `EXPLAIN|ACT|MIXED|UNKNOWN` per forme non previste;
- `runtime/tutor/conversation.py`: un solo scambio effimero, isolato per
  principal e conversazione, ammesso soltanto con guadagno semantico;
- `runtime/tutor/service.py`: filtro audience, precedenza delle fonti,
  composizione locale grounded e procedure critiche letterali;
- `runtime/ui_surfaces.py`: registro canonico delle pagine Settings consumato
  dalla sidebar e proiettato come fonte `ui_surface` nel Tutor;
- boundary HTTP e Telegram prima del planner;
- telemetria senza query in chiaro.

Il corpus isolato compilato il 24 luglio dopo l'ammissione automatica dei
documenti pubblicati e della reference dei domini contiene 3.734 unità:

| Dimensione | Conteggio |
|---|---:|
| pagine HTML pubbliche indicizzabili | 77 |
| redirect storici `noindex`, esclusi | 2 |
| descrizioni generali da manifest ammessi | 230 |
| descrizioni di argomenti da manifest ammessi | 1.332 |
| segmenti della reference domini IT/EN | 72 |
| unità totali | 3.734 |

Una prova fredda F2-only completa di compilazione, classificazione e risposta
ha richiesto circa 203 secondi sull'host corrente; non è una misura della sola
compilazione né della latenza a catalogo caldo. Il timbro degli input viene
controllato all'avvio e prima di ogni accesso al catalogo: se è invariato non si
ricompila, se cambia il compilatore riusa per hash semantico i vettori rimasti
identici.

### 3.2 Certificazioni live HTTP del 23-24 luglio

Tre regressioni segnalate in chat sono state riprodotte e chiuse:

| Turn live | Domanda | Esito verificato |
|---|---|---|
| `c91e0a239e4a4931` | panoramica generale | include `urls`, `places`, `location`, Google Workspace, Google Photos e GitHub, oltre alle altre aree del catalogo vivo |
| `8ec991fc12094155` | tre rifiuti Google Workspace | identifica `gmail reply`, `gmail labels`, `drive download`, spiega le cause e segnala il dato obsoleto su `sheets append`; stato corrente 24/24 |
| `0dc1129c795a4404` | mailbox aggiuntive e credenziali | conferma mailbox ulteriori e recupera dal manifest il file account e i campi IMAP |

Il fallimento `1a76ca0e5c4547d9` ha mostrato due difetti distinti: il composer
restituiva contesto insufficiente, ma il boundary lo presentava come
indisponibilità tecnica; inoltre il turno seguente non poteva risolvere un
riferimento ellittico. Gli esiti sono ora tipizzati (`answer`, `insufficient`,
`unavailable`) e un solo scambio effimero entra nel contesto soltanto con
guadagno semantico. Una parte documentata viene risposta anche se manca un
dettaglio.

Il 24 luglio due richieste Tutor sono inizialmente cadute nel planner per uno
skew di moduli: il processo HTTP era precedente alle modifiche coordinate di
`tutor/service.py` e `tutor/mode.py`. Dopo il riallineamento, una seconda
regressione ha mostrato che «Cosa sai fare?» recuperava correttamente i segmenti
di capability, ma il miglior punteggio restava appena sotto una soglia assoluta
troppo alta. La soglia globale è stata calibrata da 0,75 a 0,70 sulla
distribuzione osservata, senza eccezioni per frase, dominio o executor.

Replay successivi:

| Turn live | Domanda | Esito verificato |
|---|---|---|
| `9374cf268d954530` | «Cosa sai fare?» | Tutor F2, nessun executor; prima panoramica utile ma ancora incompleta sulle credenziali |
| `8b8bf20f81ed4e2e` | «Come si 8nseriscono le credenziali di una mailbox?» | esempio naturale, percorso e campi IMAP recuperati semanticamente; nessun executor |
| `6bdfca5e24404dbb` | «Cosa sai fare?» | panoramica completa delle 44 aree/provider e 114 operazioni, incluse capacità OS, web, geografia, Google, credenziali, amministrazione e sicurezza; nessun marker interno e nessun executor |

Il boundary ora distingue anche l'errore tecnico: se il Tutor fallisce, una
richiesta riclassificata semanticamente `EXPLAIN` o `MIXED` riceve un messaggio
esplicito di indisponibilità e non viene trasformata in un'operazione; `ACT`
continua invece a ricadere nel motore normale.

### 3.3 Lingue

La documentazione di prodotto pubblicata corrente è bilingue
italiano/inglese. L'inventario deriva la lingua da `html lang`, quindi una
nuova traduzione valida entra automaticamente nel corpus senza modificare un
registro. Per una lingua diversa o non ancora pubblicata:

1. il retriever preferisce, **per ogni concetto**, la lingua richiesta;
2. se assente usa inglese;
3. solo se manca anche inglese usa deterministicamente un'altra traduzione;
4. BGE-M3 confronta la domanda nella lingua corrente con la fonte inglese;
5. il compositore risponde nella lingua corrente.

Le descrizioni manifest sono mappe i18n. Italiano e inglese sono obbligatori
per un executor attivo; altre lingue sono ammesse. Una lingua opzionale nel
manifest viene indicizzata automaticamente.

Prove reali query→fonte inglese:

| Lingua query | Coseno fonte email corretta | Alternative non pertinenti |
|---|---:|---:|
| francese | 0,899 | 0,673–0,674 |
| tedesco | 0,878 | 0,636–0,650 |
| spagnolo | 0,863 | 0,652–0,656 |

Una composizione reale in francese da contesto esclusivamente inglese ha
prodotto una risposta francese grounded in 1,59 secondi.

### 3.4 Evidenze di riuso per F3

Non serve un nuovo framework di osservazione generico:

- `services_registry.snapshots()` possiede già probe bounded concorrenti;
- `devices.list_by_owner()` e `get_device()` applicano identità e ownership;
- `recurring_tasks.list_user_tasks()` e gli handler di cronologia sono
  actor-scoped;
- il loader espone membership, lifecycle, dormancy e policy;
- `dialog_pending` salva stato 0600, con TTL, scrittura atomica e owner del
  canale;
- `TutorRequest.probes` esiste ma non è ancora consumato;
- `executor_scheduler` è il punto unico per limiti e backpressure.

### 3.5 Evidenze di riuso per F4

- `turn_feedback` registra feedback collegato a un turn ID;
- `change_intents` offre lifecycle proposta→applicazione→osservazione→rollback,
  ma il suo `ALL_KINDS` è chiuso e oggi non contempla conoscenza Tutor;
- il catalogo Tutor è già compilabile, firmabile e sostituibile atomicamente;
- la telemetria Tutor non conserva la query in chiaro e oggi non è sufficiente
  per apprendere nuove formulazioni: ogni estensione deve quindi avere una
  politica privacy esplicita.

## 4. Invarianti

1. Il Tutor non possiede handler, credenziali, browser, rete o strumenti.
2. Audience deriva soltanto dal principal autenticato del canale.
3. Le fonti non visibili vengono escluse prima del contesto LLM.
4. Testo della query e delle fonti è evidenza non fidata, mai istruzione.
5. Manifest prevale su capacità, disponibilità ed effetti installati.
6. Guida curata prevale su passi operativi e nomi UI.
7. Documentazione pubblica fornisce spiegazione, non prova stato live.
8. Procedure amministrative e di sicurezza ad alta criticità restano
   letterali finché un test di equivalenza non giustifica diversamente.
9. Nessuna risposta negativa forte deriva dalla sola assenza di retrieval.
10. Nessuna azione è costruita dal testo generato dal modello.
11. Pending esistenti restano intatti durante una domanda Tutor.
12. Errore di catalogo, embedding o composizione è fail-soft per il motore e
    onesto per la domanda d'aiuto.

## 5. F2 — conoscenza dinamica che sussume F1

### 5.1 Pipeline

```text
loader verificato ─────────────┐
radice pubblicata docs/ ───────┤
fonti supplementari selezionate├─> unità tipizzate ─> segmenti semantici
registri runtime proiettati ───┤                              │
guide curate ──────────────────┘                   embedding locale
                                                               │
                                      catalogo SQLite firmato <─┘
                                                   │
query ─> mode semantico ─> ranking/audience ─> contesto rilevante
                                                   │
                               procedura letterale | composer locale
```

Ogni unità contiene `unit_id`, `concept_id`, lingua, audience, tipo fonte,
autorità, priorità, titolo, testo, riferimento e hash. `concept_id` consente
il fallback linguistico per singolo concetto senza restituire due traduzioni
della stessa informazione.

### 5.2 Fonti ammesse

Il compilatore non esegue crawling. Ammette soltanto:

- descrizioni generali e descrizioni degli argomenti dei manifest passati
  attraverso il loader verificato;
- ogni pagina HTML indicizzabile sotto l'esatta radice `docs/` che viene
  pubblicata da `deploy.sh`;
- fonti supplementari non pubbliche dichiarate in `tutor/sources.toml`;
- registri runtime proiettati esplicitamente e guide curate.

Sono esclusi per costruzione:

- dati utente, email, calendari, file e conversazioni;
- ADR, audit, roadmap, handoff e report interni;
- log, turni e telemetria;
- pagine `noindex`, canonical o gruppi `hreflang` invalidi, symlink e path che
  escono dalla radice pubblicata.

### 5.3 Retrieval

Il ranking usa un solo embedding della query. Il segnale lessicale è calcolato
dinamicamente dai token presenti in query e candidati con peso IDF locale: non
esistono tabelle di sinonimi. La priorità della fonte rompe soltanto pareggi
stretti e non può compensare una bassa rilevanza.

I documenti lunghi vengono segmentati per titoli e sezioni in unità di massimo
920 caratteri. Il ranking seleziona i segmenti pertinenti e può espandere solo
un numero chiuso di segmenti adiacenti dello stesso documento. La soglia
globale corrente è 0,70, calibrata anche su domande panoramiche brevi e query
cross-lingua; non ha
override per topic, parola o executor. La banda di selezione relativa è
parametrizzata (`METNOS_TUTOR_KNOWLEDGE_BAND`, default 0,06); il budget per
fonte è per coppia documento+sezione, così due sezioni forti non affamano una
terza sezione pertinente della stessa pagina.

L'inventario delle capacità è proiettato come una testa più parti: la testa è
il riassunto proprio dell'inventario (prosa generale e aree naturalizzate) e fa
da ancora di retrieval per le domande panoramiche; ogni parte contiene soltanto
le proprie righe, con le finalità in linguaggio naturale estratte dai manifest
ammessi. Una fonte logica divisa solo per dimensione viene ricongiunta in
selezione: quando una parte qualsiasi di un inventario è selezionata, le parti
sorelle si uniscono entro il budget di gruppo. Nessun vettore deve così
rappresentare insieme il generale e lo specifico.

**Registro superfici: analisi di rigidità e guardia anti-deriva (24/7/2026).**
Verificata l'ipotesi «registro generato dagli artefatti UI a manutenzione 0»:
oggi NON è equipotente. Le pagine Settings coesistono in tre implementazioni
(template Jinja con letterali solo-IT spesso sciatti — `id`, `owner`, `OS` in
pagina italiana —; `changes.html` già su chiavi i18n `msg('UI_CHANGE_COL_*')`;
`/admin/timers` costruita in Python senza template). L'estrazione automatica
produrrebbe contenuto sotto la qualità richiesta dalla certificazione e non
coprirebbe l'inglese, che esiste solo nel registro. Il registro resta quindi lo
STRATO EDITORIALE curato (contenuto, come le procedure), ma il vincolo
manutentivo stretto è sostituito da una **guardia anti-deriva**:
`validate_surfaces` ricalcola l'impronta strutturale della riga di intestazione
letterale di ogni template (`structure_sha`, incluse le chiamate `msg()`) e
fallisce al mismatch; `python3 runtime/ui_surfaces.py` stampa impronte e
colonne estratte, così l'aggiornamento è una revisione di diff suggerita, non
un atto di memoria. La deriva silenziosa è impossibile; il costo di
manutenzione scende a un comando dopo la modifica UI. Fine-stato a
manutenzione 0 (vincolato al risanamento del debito i18n, alla terza lingua):
generalizzare il pattern di `changes.html` — chiavi i18n per intestazioni e
controlli + anchor `data-tutor-*` — e GENERARE la parte strutturale del
registro da template+DB i18n, lasciando dichiarati solo sommari, procedure e
audience (residuo editoriale irriducibile). Gli anchor `data-tutor-id` di
`ui_map.toml` restano validati nei template.

Il filtro audience precede la resa del corpo. Se il miglior risultato è
riservato e non esiste una fonte pubblica sostanzialmente equivalente, il
servizio restituisce un esito `restricted` senza passare il testo riservato al
modello. L'audience di ACCESSO a una pagina e l'audience della sua CONOSCENZA
sono dichiarazioni distinte del registro delle superfici: ogni route
amministrativa resta riservata alla navigazione, mentre `knowledge_audience`
dichiara chi può apprenderne contenuto e procedura, replicando il contratto
`audience_minima` ratificato dalle schede F1 (dispositivi = `user`; console
delle proposte = `instance_admin`).

### 5.4 Distinzione spiegazione/azione

Non esiste un detector di frasi d'aiuto:

- i controlli deterministici coprono soltanto segreti, comandi di controllo e
  il bypass di un verbo operativo canonico iniziale;
- per le altre forme un piccolo classificatore locale restituisce soltanto
  uno dei quattro stati chiusi prima di aprire il catalogo;
- `ACT` e `UNKNOWN` ricadono nel motore normale;
- `MIXED` chiede di separare spiegazione e operazione;
- solo `EXPLAIN` entra nel composer.

Il retrieval e la composizione sono quindi sostenuti soltanto per `EXPLAIN`.

### 5.5 Virtualizzazione e composizione LLM

Il Tutor dichiara soltanto il tier logico e il contratto d'uscita:

- il classificatore usa `fast`;
- il composer usa `wise` e l'output policy `public`;
- provider, modello, endpoint, temperatura e modalità di ragionamento sono
  risolti dal gateway centrale;
- il prompt del composer identifica esplicitamente il modello come Tutor di
  Metnos e gli chiede di cercare in tutto il contesto la risposta migliore e
  completa, integrando fonti compatibili;
- il gateway normalizza l'uscita e rifiuta marker strutturali interni.

Sul backend locale corrente `wise` usa lo stesso Qwen degli altri tier con
ragionamento nascosto disattivato: la prova live ha mostrato che quel deployment
può riversare il reasoning nel contenuto e consumare il budget prima della
risposta. È una policy del binding centrale, sostituibile quando un provider
espone un canale separato affidabile, non un workaround del Tutor.

### 5.6 Uscita di F1

F1 può essere dichiarato sussunto quando:

1. tutte le domande canoniche F1 sono servite da F2 con qualità almeno
   equivalente;
2. l'aggiunta/rimozione di un executor ammesso si riflette senza editare
   schede o codice Tutor;
3. un corpus di forme nuove raggiunge contenuti corretti senza liste di frasi;
4. il corpus operativo registra zero sottrazioni al planner;
5. procedure critiche conservano riferimenti UI e stop condition;
6. IT, EN e almeno tre query cross-lingua superano i gate di retrieval;
7. HTTP e Telegram producono turni Tutor auditabili.

Dopo questo gate, le schede informative diventano eliminabili. Restano solo
eventuali procedure curate per le quali il valore è l'autorità editoriale, non
il routing. **Decisione c1 (24/7 sera, ratificata):** `fotografie-dominio`
resta procedura curata — il caso previsto qui sopra — e il certificatore
`--f2-only` salta il suo set con nota esplicita (`curated_card` nel corpus);
la card pubblicata continua a servire quelle query.

**Fedeltà del corpus di certificazione (findings 24/7 sera).** La tassonomia
dei 40 scarti della certificazione 94/134 ha separato quattro nature diverse:
~14 attese-fotocopia F1 (flessioni: «crearlo», «directories», «posizioni»,
«amministrative»), ~18 sbadataggini del composer (voce presente in contesto ma
persa nella risposta, con varianza fra run), ~4 classificazioni mode su
imperativi, 1 pari-fallimento F1 documentato (panoramica EN «in practice»,
flag `known_equal_fail` + skip motivato). Correzioni GENERALI adottate:

- le attese flesse del corpus usano voci `lex:tutor_gate.<concetto>` risolte
  da `detection_lexicon.match` (8 concept `regex` a radice, seed it+en, unione
  it∪en al match: un solo gate copre entrambe le lingue e le lingue future
  arrivano dal daemon di traduzione) — niente liste di sinonimi nel codice né
  frasi-specchio nei contenuti;
- gli skip sono ESPLICITI nel certificatore (`skipped` distinto da
  passed/failed, nota per riga) — mai un fail nascosto come pass;
- un replay deterministico dei report congelati contro i nuovi gate distingue
  flessione (riparata dal lexicon) da omissione totale (bersaglio del
  correttore §5.7) senza consumare run LLM.

Esito misurato (cert6, catalogo isolato): f1_equivalence da 22/38 a 23 pass +
7 skip documentati (30/38 non-fail); boundary invariato 12/12; admin_ui 16-17
con varianza fra run concentrata su route e campi singoli — la famiglia
bersaglio del correttore.

### 5.7 Correttore di bozze deterministico (post-composizione)

Il ledger di copertura è già una checklist derivata dalle fonti strutturate
selezionate; dopo la composizione il servizio la RILEGGE meccanicamente
(§7.9, zero LLM) contro la risposta: percorso esatto di ogni superficie con i
suoi campi e controlli, aree e provider dell'inventario, finalità dei tool,
condizioni di arresto attestate («Fermati se»/“Stop if”), divieto dei marker
interni (`from_step`, `object=`, …). Voci mancanti → UNA sola ricomposizione
con l'elenco esplicito dei buchi appeso al contesto, poi si consegna comunque
(cap onesto, niente loop). Un guasto della rilettura non degrada mai una
composizione riuscita (cintura fail-soft).

**Finding sul matching (generale, niente liste).** La granularità della
rilettura dipende dalla lunghezza dell'etichetta, e sbagliarla la rende cieca
in entrambe le direzioni:

- una voce LUNGA (finalità di un tool, elenco di contenuti visibili) non è
  provata da una parola qualunque — «file» compare in mezza checklist e
  maschererebbe «impronta»: valgono le parole DISTINTIVE per frequenza
  documentale interna alla checklist, cioè con radice presente in una sola
  voce;
- una voce CORTA (fino a tre parole) è il nome esatto di un campo o di un
  controllo — «esegui ora», «nome visualizzato» — e vale solo per intero:
  spezzarla la dichiarava coperta da un «eseguire» qualsiasi. Ogni parola è
  richiesta a livello di radice, così «riprova» resta coperto da «riprovare»
  senza che «esegui ora» lo sia da «eseguire».

La misura che ha imposto la regola: sui casi in cui il correttore era scattato
e il gate falliva comunque, ZERO dei gate falliti figurava fra i punti
richiesti dalla ricomposizione — il correttore non li aveva visti, non li
aveva chiesti invano.

**Confine delle procedure esteso alle superfici (finding, notte 24/7).** Una
procedura porta passi numerati e condizioni di arresto che il composer deve
riportare per intero: lasciata nel contesto di un'ALTRA domanda, cattura la
risposta. Il servizio escludeva dal contesto generativo le sole procedure
delle schede curate; le unit `ui_procedure` restavano, e il dettaglio utente
(`/admin/users`) veniva risposto con la procedura delle proposte
(`/admin/changes`), che aveva portato dodici voci di ledger estranee. La
regola già ratificata vale ora per ogni procedura tipizzata, schede e unit
insieme; se restano soltanto procedure, sopravvive la primaria.

Telemetria per misurare il guadagno reale: `tutor_repair_pass` (0|1) e
`tutor_repair_missing` (voci che hanno chiesto la ricomposizione) nel record
turno JSONL e nei report del certificatore. Costo: +1 chiamata `wise` solo
sui turni che falliscono la rilettura (+8-15 s su quelli).

## 6. F3 — stato live tipizzato e handoff sicuro

F3 non deve dare al Tutor accesso agli executor. Introduce **capsule di
osservazione tipizzate**, prodotte da probe registrati in codice.

### 6.1 Contratto `ProbeSpec`

Ogni probe dichiara:

- ID chiuso e audience minima;
- binding ammessi e loro origine;
- timeout, TTL e dimensione massima;
- schema di output e sanitizzatore;
- classe dello scheduler centrale;
- sorgente/versione che ne giustifica l'uso.

Il modello non sceglie né nomina probe. Le unità recuperate contengono
`probe_refs` firmati. Il binder accetta soltanto:

- valori fissi nella fonte; oppure
- ID esatti presenti nella query e risolti con ownership.

Ambiguità, owner errato o binding libero producono chiarimento, non una prova
più ampia.

### 6.2 Envelope di osservazione

```json
{
  "probe_id": "owned_device_state",
  "observed_at": "...",
  "fresh_until": "...",
  "status": "ok|partial|unavailable|stale",
  "facts": {},
  "redactions": [],
  "source_version": "..."
}
```

Prime capsule proposte:

- stato degli executor ammessi;
- salute servizi, solo amministratore;
- dispositivi posseduti;
- task e cronologia actor-scoped.

Probe indipendenti possono correre in parallelo tramite lo scheduler centrale,
con budget complessivo. Un fallimento parziale resta visibile nella risposta;
non viene trasformato in uno stato sano.

### 6.3 Handoff all'azione

Il Tutor non esegue. Può offrire una **action reference canonica** proveniente
da una fonte firmata. Dopo conferma:

1. salva un pending server-side legato a principal, conversazione, hash del
   catalogo, TTL e nonce monouso;
2. preserva la query canonica letterale, mai il testo generato;
3. passa la richiesta al normale motore Metnos;
4. planner, vaglio, autonomia, consenso ed executor restano invariati.

`dialog_pending` è riusabile estendendo in modo chiuso `on_complete` con
`tutor_handoff`; non va creato un secondo store di conferme.

### 6.4 Gate F3

- isolamento cross-user e cross-conversation;
- scadenza e replay del token;
- ownership dei device e dei task;
- osservazione stale/partial/unavailable;
- pressione scheduler e timeout;
- immutabilità della query canonica;
- preservazione di pending preesistenti;
- nessuna invocazione generica da argomenti LLM.

F3 non parte prima della stabilità live di F2.

## 7. F4 — manutenzione intelligente, non addestramento online

F4 serve a piegare il costo di manutenzione. Non modifica pesi del modello e
non pubblica autonomamente testo generato.

### 7.1 Eventi minimi

Il sistema distingue:

- nessuna fonte recuperata;
- fonte obsoleta o contraddittoria;
- composer insufficiente;
- ambiguità spiegazione/azione;
- feedback negativo;
- copertura presente ma lingua debole.

Per default le query riuscite restano soltanto hash e contatori aggregati. Le
lacune che richiedono clustering possono conservare localmente:

- embedding della query;
- estratto redatto e cifrato/0600;
- lingua, audience, source ID e motivo;
- TTL breve, limite di dimensione e cancellazione verificabile.

Il testo grezzo non entra nel catalogo e non viene conservato indefinitamente.

### 7.2 Pipeline notturna proposta

1. raccogli gap e feedback negativi;
2. redigi e normalizza localmente;
3. raggruppa con BGE e deduplica;
4. richiedi ricorrenza o convergenza minima;
5. confronta il cluster con hash di fonti e catalogo correnti;
6. classifica il debito: fonte mancante, stale, conflitto, composizione o modo;
7. genera un change-intent di nuovo tipo
   `update_tutor_knowledge`, aggiunto esplicitamente ad `ALL_KINDS`;
8. applica automaticamente solo rigenerazioni meccaniche di fonti già
   ammesse; guide, admin e sicurezza richiedono review umana;
9. compila un candidato firmato;
10. esegue replay contro esempi trattenuti e corpus operativo;
11. osserva metriche e finalizza o usa il rollback del change-intent.

Non si deve abusare di un kind esistente: il nuovo intent richiede adapter e
applier propri.

### 7.3 Meccanismi proposti

**Mappa del debito conoscitivo.** I gap vengono aggregati per vicinato
semantico e causa, non come semplice lista di domande. Permette di correggere
una fonte invece di aggiungere dieci schede equivalenti.

**Replay controfattuale.** Prima di pubblicare, il candidato viene confrontato
con esempi storici redatti e con query operative: deve aumentare copertura
senza rubare azioni.

**Ritiro automatico assistito delle schede.** Una scheda F1 è proposta per il
ritiro soltanto quando il catalogo dinamico supera il suo acceptance set nelle
lingue richieste. La cancellazione resta osservabile e reversibile.

**Contratti di freschezza.** Manifest: invalidazione immediata su identità del
catalogo; documenti: hash; procedure curate: review e versione esplicita;
osservazioni F3: TTL breve.

### 7.4 Metriche F4

- copertura per tipo fonte, lingua e audience;
- lacune oneste;
- false sottrazioni al planner, target zero;
- conflitti tra fonti;
- insufficienza del composer;
- p50/p95 di retrieval, mode e composizione;
- feedback positivo/negativo per vicinato semantico;
- crescita del corpus e quota di unità stale.

Le soglie vanno derivate da distribuzioni misurate e versionate, non inventate
a priori. Il gate F4 richiede almeno due settimane di osservazione con rollback
provato e nessuna pubblicazione autonoma di contenuti amministrativi o di
sicurezza.

## 8. Rischi e contromisure

| Rischio | Contromisura |
|---|---|
| Tutor ruba un comando | bypass canonico, classificatore chiuso, corpus operativo |
| Hallucination | contesto solo ammesso, nessun tool, sentinel insufficiente |
| Leak admin | audience prima del corpo e principal autenticato |
| Prompt injection | fonti/query trattate come evidenza non fidata |
| Catalogo corrotto | firma, integrity check, swap atomico, last-known-good |
| Latenza startup | embedding incrementale per hash e lavoro fuori richiesta |
| Documento pubblico inatteso | radice esatta, canonical/hreflang validi, `noindex`, confine e symlink fail-closed |
| Moduli runtime disallineati | deploy/restart coordinato e risposta Tutor tecnicamente onesta al fallimento |
| Reasoning esposto all'utente | policy d'inferenza centrale sicura e contratto d'uscita pubblico |
| Nuova lingua debole | fallback per concetto verso EN e gate cross-lingua |
| Schede diventano debito | F2 dinamico e gate di ritiro basato su equivalenza |
| F3 diventa tool gateway | soli ProbeSpec chiusi, binding ownership-aware |
| F4 diventa auto-edit | change-intent, review per contenuti sensibili, rollback |
| Telemetria invade privacy | query riuscite non conservate, gap redatti con TTL/cap |

## 9. Piano e criteri di completamento

### F2

- [x] catalogo unificato e firmato;
- [x] manifest live, inclusi argomenti i18n, + tutti i documenti pubblici validi + fonti supplementari + guide;
- [x] ranking senza frasi hardcoded;
- [x] fallback linguistico per concetto;
- [x] mode gate locale e safe fallthrough;
- [x] test unitari, i18n e cross-lingua;
- [x] certificazione live HTTP;
- [x] gate del corpus a radice flessiva via `detection_lexicon` + skip
      documentati (§5.6);
- [x] correttore di bozze deterministico post-composizione con telemetria
      (§5.7);
- [x] ri-certificazione completa sequenziale col correttore attivo
      (cert7, 24/7 notte: 101 pass + 7 skip documentati = 108/134 non-fail,
      da 94; boundary 12/12 con zero composizioni; repair su 64/134 casi,
      83% dei riparati passa);
- [x] certificazione con un turno live Telegram (`35bba21a0a774f31`, 24/7
      23:36: `channel=telegram, mode=tutor`, esito fondata; lead naturale,
      confine chat web rispettato, route e controlli devices completi);
- [ ] osservazione zero false-steal su corpus operativo;
- [ ] decisione di ritiro delle schede informative F1.

### F3

- [ ] specifica eseguibile `ProbeSpec`;
- [ ] quattro probe iniziali;
- [ ] capsule e budget scheduler;
- [ ] handoff monouso tramite pending esistente;
- [ ] test avversariali e prova live.

### F4

- [ ] schema eventi privacy-bounded;
- [ ] nuovo change-intent e adapter;
- [ ] clustering/debt map;
- [ ] replay controfattuale;
- [ ] osservazione di due settimane e rollback.

## 10. Registro di avanzamento

| Data | Evento | Evidenza |
|---|---|---|
| 2026-07-23 | F1 semantico iniziale | ADR 0197, sei schede IT/EN |
| 2026-07-23 | Rimossi `affinity` ed `exact` Tutor | selezione BGE-M3 locale |
| 2026-07-23 | F2 sussume F1 | corpus dinamico schema 3, ADR 0198 |
| 2026-07-23 | Verifica cross-lingua | FR 0,899; DE 0,878; ES 0,863 |
| 2026-07-23 | Gate automatici | 55 Tutor; 340 i18n + 360 subtest |
| 2026-07-23 | Manifest argomenti nel corpus | 2.047 unità; mailbox e credenziali recuperabili |
| 2026-07-23 | Google Workspace convergente | parser 24, plan 24, rifiuti 0; manifest generati e nomi validati |
| 2026-07-23 | Certificazione live HTTP | turn `c91e0a2`, `8ec991f`, `0dc1129` |
| 2026-07-23 | Gate consolidato finale | 352 pass, 22 skip ambientali dichiarati; catalogo schema 3 integro e firmato |
| 2026-07-23 | Ingresso Tutor senza frasi | mode semantico prima del catalogo; rimossi tutti i concetti `help.*` |
| 2026-07-23 | Seguiti ed esiti tipizzati | contesto RAM con guadagno semantico; lacuna distinta da errore tecnico |
| 2026-07-23 | Fonti Settings e posta standard | registro servizi canonico + guida IMAP/SMTP; corpus compilato insieme alle superfici UI |
| 2026-07-23 | Esempio naturale come ingresso | le risposte «come si fa» iniziano con una richiesta di esempio adattabile nella lingua corrente, poi espongono contratto e dettagli; decisione semantica, nessuna frase di routing |
| 2026-07-23 | Presentazione UI semantica | le fonti `ui_surface` rispondono con percorso e contenuti visibili; da Telegram rinviano esplicitamente alla chat web, senza regole per pagina o formulazione |
| 2026-07-24 | Inventario automatico della pubblicazione | 77 HTML indicizzabili, 2 redirect `noindex`, 3.734 unità firmate; add/modify/delete invalidano il catalogo |
| 2026-07-24 | Reference domini IT/EN | 26 domini + `_system`, 98 operazioni e 54 esempi; pubblicata su `/it/domains` e `/en/domains` |
| 2026-07-24 | Retrieval panoramico calibrato | soglia globale 0,70; nessun branch per «fare», capability, dominio o executor |
| 2026-07-24 | Fallimento Tutor onesto | recupero semantico `EXPLAIN|MIXED` restituisce indisponibilità esplicita e non avvia operazioni |
| 2026-07-24 | Gateway LLM centrale | mode `fast`, composer `wise`, policy e post-processing fuori dal Tutor; output pubblico fail-closed sui marker interni |
| 2026-07-24 | Replay live panoramica completa | turn `6bdfca5e24404dbb`, nessun executor e copertura delle aree prima omesse |
| 2026-07-24 | Certificazione sequenziale 94/134 con boundary 12/12 | tassonomia dei 40 scarti in 4 nature (§5.6); ratifiche corpus (a)/(b) |
| 2026-07-24 | Gate a radice flessiva + skip documentati | 8 concept `tutor_gate.*` seed it+en; c1 photos card curata; f1_equivalence 30/38 non-fail (cert6) |
| 2026-07-24 | Correttore di bozze deterministico | §5.7: rilettura meccanica del ledger, una ricomposizione, parole distintive per frequenza; 89 test tutor verdi; telemetria `tutor_repair_pass` |
| 2026-07-24 | Fragilità operativa documentata | rimbalzo llama-server 23:02 ha invalidato una certificazione intera e un turno live: certificare SOLO a servizio LLM stabile e macchina scarica |
| 2026-07-24 | E2E post-restart | tutor `7135ad998f3645cc` (procedura proposte, esito consolidata); form credenziali `67f1ebe30fc847c2` dopo fix iniezione server-side (ADR 0199 rev.) |
| 2026-07-24 | Gate 7: turno live Telegram | `35bba21a0a774f31` («Dove vedo i dispositivi collegati?»): mode=tutor, fondata, confine chat web + route + controlli completi |
| 2026-07-24 | Certificazione completa col correttore | cert7: 108/134 non-fail (da 94), boundary 12/12; repair 64 casi (48%), 83% dei riparati passa; 26 residui: 3 mode (Passo 3), ~7 retrieval-selezione, ~12 conformità composer (Passo 4), ~4 concept fuori fonte |
| 2026-07-25 | Passo 3: mode v2 (imperativi di visualizzazione = EXPLAIN) | `tutor_mode.j2` v2 it+en; boundary ri-verificato 12/12 (cert8); 3/3 bersagli PASS (admin-services-content, ops-calendar-events, ops-github-issues) → proiezione 111/134; E2E prod `f3150fb974b54044` |
| 2026-07-25 | Misura del tetto a parità di contesto | Un modello forte, con lo STESSO contesto e lo stesso contratto, recupera 4 casi su 6: il tetto è il modello dove l'evidenza c'era, il retrieval dove non c'era (`admin-settings-errors` senza superficie, `admin-restricted-user` senza fonte che attesti il ruolo). Harness `ceiling_dump`/`ceiling_eval` |
| 2026-07-25 | Confine procedure + rilettura a due granularità | `ui_procedure` non primaria fuori dal contesto generativo; etichette corte richieste per intero a radice. `admin-timers-controls` e `admin-user-detail` recuperati, `admin-changes-content` senza regressione |
| 2026-07-25 | Baseline cert11 e smentita della regola larga | 99+7/134 non-fail, boundary 12/12. Il diff contro cert7 attribuisce: 0 recuperi all'esclusione `ui_procedure`, 3 perdite causate da essa (la procedura della STESSA pagina era l'unica fonte di `/admin/changes`); i recuperi veri venivano dal budget composer 2048 |
| 2026-07-25 | Confine procedure ristretto alla co-tematicità | si scarta una procedura non primaria SOLO se la primaria è un'ALTRA pagina (`_surface_key`); cert13 103+7/134, i 3 persi rientrano, 2 regressioni a fonti identiche (rumore); 95 test tutor verdi |
| 2026-07-25 | Prompt a soli segnaposto (mode v3, composer v9) | §6 completo, zero frasi del corpus, `<SEGNAPOSTO>` al posto dei letterali non-contratto; fix linter L2 a confine di parola («entry to» conteneva «try to»); cert14 106+7/134, admin_ui 17→21 — la regola UI da 126 parole spezzata in tre regole ha sbloccato route e percorsi |
| 2026-07-25 | Guida pubblica all'interfaccia | `scripts/generate_ui_reference.py` → `docs/{it,en}/interface.html`: prosa di orientamento + mappa SVG derivata dal registro superfici; dettaglio pagine NON ripubblicato (evidenza ad autorità unica, niente concorrenza di retrieval); freschezza imposta da test + preflight `deploy.sh`; catalogo isolato +20 unità; impatto misurato in cert15 |
| 2026-07-25 | cert15: doc interfaccia nel corpus + E2E prod | 104+7/134, dentro la banda di rumore di cert14 (106; 2 delle 4 regressioni a fonti identiche, le altre 2 su casi già oscillanti con 1 unità doc in contesto). 22 casi col doc in contesto, 18 pass; recuperato `admin-user-detail`. Verdetto: guadagno per il lettore e route ora attestate anche da fonte pubblica, certificazione invariata — il collo resta la SELEZIONE delle superfici users/services/admin. E2E prod post-restart `dfb4cbbe20d84a4c` (interfaccia, mode tutor, fondata) |
