# RM-0003 — Tutor integrato: guida operativa intelligente

**Stato:** `closed`

**Creazione:** 2026-07-23

**Chiusura:** 2026-07-30

**Ultima revisione:** 2026-07-30

**Implementazione reale:** F2, F3 e F4 implementati. F2 dispone del catalogo
firmato, del confine semantico e del correttore deterministico; F3 aggiunge
quattro osservazioni correnti tipizzate e la consegna monouso di una clausola
d'azione letterale; F4 aggiunge ledger delle lacune, associazioni per utente,
riscontro positivo/negativo, cancellazione e replay controfattuale. Le prove
isolate F3/F4 e il caso di instradamento per identità documentale sono verdi.
La chiusura del 30 luglio 2026 è sostenuta dal catalogo firmato di 3.396 unità,
dalle suite Tutor e i18n verdi, dalle certificazioni F3/F4, dal campione UI
finale e dalla verifica del servizio e della pubblicazione in esercizio.

**Conservazione:** persistente fino a implementazione dimostrata o cancellazione
esplicita di Roberto.

**Decisione di prodotto:** il Tutor deve rispondere a domande imprevedibili per
forma e contenuto usando esclusivamente conoscenza locale ammessa, senza
diventare un secondo planner e senza richiedere schede precompilate per ogni
executor.

**ADR:** 0197 (fondazione semantica), 0198 (compilatore F2 e superamento F1),
0202 (osservazioni, consegna e apprendimento privato), 0203 (identità delle
fonti pubblicate).

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
- osservare lo stato corrente tramite fonti tipizzate e consegnare una clausola
  d'azione al normale motore soltanto su conferma.

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

### 3.4 F3 implementato

`runtime/tutor/probes.py` contiene un registro chiuso di quattro `ProbeSpec`.
Le fonti `ui_surface` riportano i relativi `probe_refs` nel catalogo firmato;
né la domanda né il modello possono scegliere una sonda o costruirne gli
argomenti. I dati provengono dai registri già autorevoli:

- `loader.load_catalog()` per executor ammessi, provenienza e ciclo di vita;
- `services_registry.snapshots()` per lo stato dei servizi;
- `devices.list_by_owner()` più `placement.is_available()` per i soli
  dispositivi dell'utente;
- `recurring_tasks.list_user_tasks(actor=...)` e scheduler v2 per i soli task
  dell'attore e tre esecuzioni recenti.

Audience, schema delle chiavi, dimensione, timeout, TTL e durata massima del
dato scaduto appartengono al `ProbeSpec`. Le cache sono separate per sonda,
utente, attore, audience e lingua. Il risultato è una `ObservationCapsule` con
stato chiuso `ok|partial|unavailable|stale`; ogni errore conserva uno stato
esplicito e non viene presentato come salute.

`runtime/tutor/handoff.py` separa una richiesta `MIXED` soltanto quando il
decompositore canonico conserva esattamente due segmenti letterali e il mode
gate trova una parte `EXPLAIN` e una `ACT`. `dialog_pending` conserva la parte
d'azione con owner, conversazione, hash della clausola, hash del catalogo,
nonce e TTL. `runtime/orchestration.py` reclama la conferma una volta sola,
ricontrolla identità, scadenza, catalogo e autonomia, poi chiama il normale
`run_turn`; un pending precedente non viene sostituito.

### 3.5 F4 implementato

`runtime/tutor/gaps.py` registra in SQLite 0600 soltanto hash normalizzato,
vettore quando disponibile, lingua, audience, causa chiusa, fonti, versioni e
scadenza. Evidenze di turno, lacune e contatori hanno quote e TTL distinti; ogni
riga è separata mediante un hash dell'identità utente.

Il riscontro della chat è collegato tramite `turn_feedback`: un ✓ può promuovere
in `runtime/tutor/associations.py` l'associazione fra il vettore della domanda
e la fonte primaria realmente servita; un ✗ elimina la stessa associazione e
registra `feedback_negative`. L'associazione è utilizzabile soltanto dallo
stesso utente e con lo stesso spazio di embedding, audience e hash della fonte
firmata. Il recupero la consulta dopo il mode gate; non modifica il planner,
le cache L0/L1 o le richieste `ACT`.

La mappa del debito raggruppa le lacune per causa e vicinanza coseno senza testo
in chiaro. `runtime/tutor/counterfactual.py` rilegge le associazioni contro la
matrice firmata corrente e rifiuta fingerprint cambiati, fonti orfane, contenuti
mutati, vettori invalidi o regressioni di rango. `purge_owner()` cancella in
modo verificabile ledger e associazioni del solo utente. F4 non crea
change-intent e non pubblica documentazione: quel passaggio resta escluso finché
non esiste un contenuto reale e revisionabile da proporre.

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
`probe_refs` firmati. Le quattro sonde correnti hanno `bindings=()`: derivano
lo scope esclusivamente dal principal autenticato e non accettano argomenti
della domanda. Il contratto conserva il campo `bindings` per una futura
estensione chiusa; un valore libero o un ID non risolto con ownership non può
entrare nel runner.

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

Il Tutor non esegue. Quando una richiesta contiene una spiegazione e
un'operazione separabili, conserva la **clausola d'azione letterale** scritta
dall'utente. Dopo conferma:

1. salva un pending server-side legato a principal, conversazione, hash del
   catalogo, hash della clausola, TTL e nonce monouso;
2. preserva la clausola letterale, mai una riscrittura o il testo generato;
3. passa la richiesta al normale motore Metnos;
4. planner, vaglio, autonomia, consenso ed executor restano invariati.

`dialog_pending` è riusabile estendendo in modo chiuso `on_complete` con
`tutor_handoff`; non esiste un secondo store di conferme. La scelta della sola
clausola d'azione è ratificata: passare l'intera richiesta riporterebbe la parte
interrogativa nel planner, mentre usare un esempio della fonte consegnerebbe
testo non scritto dall'utente. Se la segmentazione non è esatta, il sistema
chiede di separare le due richieste e non crea il pending.

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

F4 riduce il costo di manutenzione senza modificare i pesi del modello, senza
aggiungere frasi di instradamento e senza pubblicare testo generato.

### 7.1 Ledger privacy-bounded

Il database `tutor_learning.sqlite`, con permessi 0600, contiene tre tabelle:

- `turn_evidence`: prova breve necessaria a collegare un successivo riscontro
  alla fonte realmente servita;
- `gap_events`: lacune con causa chiusa e scadenza;
- `query_counters`: ricorrenze aggregate per esito.

Il sistema distingue `no_source`, `restricted_source`, `stale_source`,
`source_conflict`, `composer_insufficient`, `composer_incomplete`,
`composer_unavailable`, `mode_ambiguity`, `feedback_negative`,
`weak_language`, `live_observation_incomplete` e `source_unavailable`.

La domanda non viene conservata in chiaro. Ogni riga usa l'hash normalizzato
della domanda e l'hash derivato dell'utente; il vettore entra solo quando serve
alla prova o al raggruppamento. Evidenze, gap e contatori hanno TTL e quote per
utente distinti. Il pruning avviene su ogni scrittura e lettura rilevante.

### 7.2 Associazioni promosse dal riscontro

Un ✓ su un turno Tutor può creare o confermare una riga in
`tutor_associations.sqlite` soltanto se la telemetria dispone di:

- vettore normalizzato e fingerprint dell'embedder;
- versione del catalogo;
- ID e hash della fonte primaria realmente servita;
- audience e identità dell'utente autenticato.

Il match richiede lo stesso utente e lo stesso fingerprint. Una fonte assente o
con hash diverso invalida e rimuove la riga. Un'associazione vicina può entrare
nella banda di pertinenza; soltanto una corrispondenza forte, confermata
dall'utente, può diventare primaria. Un ✗ rimuove l'associazione della domanda
e registra un unico gap negativo, con semantica last-write-wins.

Questa leva vive dentro `retrieve_sources`, ma dopo il mode gate. Non può
trasformare `ACT` in `EXPLAIN`, non entra nel planner e non modifica le chiavi
delle cache dei piani condivisi.

### 7.3 Mappa del debito, replay e oblio

`debt_map()` raggruppa gli eventi attivi per causa, fingerprint, dimensione e
vicinanza coseno. Senza vettore, aggrega soltanto la ricorrenza dello stesso
hash. Il risultato espone conteggi, lingue, audience, fonti e intervallo
temporale, mai il testo della domanda.

`replay_associations()` confronta ogni associazione con la matrice e le unità
del catalogo firmato corrente. Verifica fingerprint, presenza e hash della
fonte, forma e finitezza del vettore, normalizzazione e rango prima/dopo. Il
corpus operativo resta un gate separato perché la prova fondamentale è che il
mode gate preceda sempre questa leva.

`purge_owner()` cancella ledger e associazioni del solo utente e restituisce i
conteggi eliminati. F4 non genera un `change_intent`, non modifica fonti e non
ritira schede. Un'eventuale proposta editoriale futura richiederà un contenuto
reale, una decisione separata e revisione umana.

### 7.4 Gate F4

- separazione fra due utenti, incluso lo stesso testo della domanda;
- scadenza dell'evidenza come confine di autorizzazione al riscontro;
- quote e pruning senza contaminazione fra owner;
- ✓ che promuove soltanto la fonte servita e ✗ che rimuove;
- invalidazione su hash fonte o fingerprint embedder;
- mappa del debito per causa e vicinato;
- cancellazione verificabile per utente;
- replay controfattuale con almeno un'associazione reale del catalogo;
- zero sottrazioni sul corpus delle richieste operative.

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
- [x] zero false-steal sui nove casi operativi del certificatore F4
      (`internal/reports/tutor_f34_cert_2026-07-28/f4.json`);
- [x] decisione di ritiro delle schede informative F1 (25/7: due
      tranche; tranche 1 eseguita — vedi §9-bis e registro).

### F3

- [x] specifica eseguibile `ProbeSpec`;
- [x] quattro sonde iniziali;
- [x] capsule, timeout, TTL, cache per principal e scheduler centrale;
- [x] consegna monouso tramite il pending esistente;
- [x] test avversariali e certificazione con il modello locale reale;
- [ ] turno post-distribuzione nell'istanza in esercizio.

### F4

- [x] schema eventi privacy-bounded, quote, TTL e cancellazione per utente;
- [x] riscontro positivo/negativo collegato alla fonte realmente servita;
- [x] associazioni per utente invalidate da fonte o embedder;
- [x] raggruppamento semantico della mappa del debito;
- [x] replay controfattuale con associazione temporanea reale;
- [x] corpus anti-sottrazione al planner;
- [x] nessun change-intent o auto-pubblicazione senza un contenuto reale.

## 9-bis. Revisione 25/7 — F3 ed F4 alla prova di un F2 maturo

Rilettura integrale a valle di: certificazione stabile a ~110/134 non-fail,
diagnosi della coda (salto semantico dell'embedder, §10 registro), ritiro
tranche 1 delle schede, A/B embedder (BGE-M3 4/8 → Qwen3-Embedding-0.6B 7/8
sui casi di coda). Questa sezione conserva l'analisi che aveva proposto un
rinvio; l'ordine esplicito del 28/7 di implementare F3 e F4 lo ha superato. Le
sezioni 6 e 7 descrivono ora il contratto effettivamente consegnato.

### F3 — ipotesi «F3-lite», poi superata

Che cosa è cambiato rispetto al disegno: (1) la proiezione dei servizi entra
già nel catalogo come fonte statica che dichiara esplicitamente «lo stato
corrente è live e non è nel catalogo» e indirizza alla pagina giusta; (2) le
domande di stato reale sono ACT per costruzione e il motore le serve con la
propria autorità; (3) il corpus certificato non contiene un solo caso che
richieda un'osservazione live nel Tutor. Il framework completo (registro
`ProbeSpec`, envelope, binder ownership-aware) oggi non ha domanda misurata
che lo giustifichi: è costo pronto per un bisogno ipotetico.

L'analisi considerava allora giustificato soltanto il nucleo: la
**consegna monouso** (§6.3) — spiegazione che
termina offrendo l'azione al motore via `dialog_pending` esteso con
`tutor_handoff`, query canonica letterale, nonce e TTL. È piccolo, riusa uno
store esistente e chiude l'unico attrito reale osservato (MIXED serviti solo
a metà). Il rinvio delle sonde è stato superato dall'ordine di implementazione
del 28/7; i vincoli individuati qui sono però rimasti e sono diventati il
registro chiuso, i limiti e l'isolamento descritti in §6.

### F4 — riformulato: il debito non è più delle schede, è del retrieval

Il disegno §7 nasce quando la conoscenza era card-heavy. Oggi: le fonti
meccaniche si rigenerano da sole (`input_stamp`), le schede superstiti sono 3
e la tranche 2 è condizionata solo alla chiusura della coda; il ritiro
assistito (§7.3) è quasi esaurito prima di nascere. Ciò che resta vivo di F4:

1. **Ledger dei gap privacy-bounded** (§7.1) — utile e leggero: eventi
   tipizzati (nessuna fonte, stale, insufficiente, ambiguità, feedback
   negativo) con hash/TTL; niente pipeline notturna finché i volumi non la
   giustificano.
2. **Associazioni apprese query→fonte** — il sostituto moderno della debt
   map: la coda residua di retrieval si chiude col pattern già benedetto
   altrove (L0 semantico + ✓ umano, ADR 0185): embedding della query fallita
   → boost firmato verso l'unità corretta, promosso dal ✓, versionato con
   l'identità del catalogo, TTL. Zero liste di frasi, zero prompt.
3. **Replay controfattuale** (§7.3) — resta valido come gate di ogni cambio
   di conoscenza, ed è di fatto già praticato dal certificatore per-corpus.

DECISIONE PROPOSTA: F4 non è più una fase — è due meccanismi (1 e 2) da
attivare su evidenza, con 3 come gate permanente. Il change-intent
`update_tutor_knowledge` si introduce solo quando esiste il primo contenuto
da proporre, non prima.

### Nuovi approcci (ordinati per rapporto valore/rischio)

1. **Embedder Qwen3-Embedding-0.6B** al posto di BGE-M3 nel Tutor: misurato
   sulla coda (7/8 vs 4/8 in top-5), stessa dimensione 1024, build
   comparabile, ONNX già in cache; cablaggio dietro la config `virt`
   esistente (`model_dir` del provider), default invariato fino a cert
   completa verde. È il singolo intervento che rende superflui i rattoppi.
2. **Query instruction-aware**: col cablaggio Qwen, il lato query riceve il
   prefisso di istruzione (asimmetria query/documento) — gratis e misurato
   nell'A/B.
3. **Associazioni apprese** (sopra, F4.2) per il residuo post-Qwen.
4. **Peso navigazionale deterministico**: se dopo Qwen restasse una coda
   «dove…?», valutare un peso maggiore di titolo/percorso nella componente
   lessicale del ranker — regola generale, non frasi. Solo su misura, non ora.
5. **Correttore: ledger del contesto giusto** — osservato che su contesto
   sbagliato il correttore insegue voci irrilevanti (chiede provider su una
   domanda di pagina): a retrieval sistemato il sintomo sparisce da sé;
   nessun intervento ora.

## 9-ter. Audit dei 134 casi (25/7) — perché non si chiude, e cosa fa

Tre analisi indipendenti su perimetri disgiunti (selezione admin/conversation;
corpus, lessico e skip; determinismo). Report integrali:
`scratchpad/audit_134/esperto{1,2,3}_*.md` della sessione. Ogni causa è
MISURATA, non ipotizzata; ogni chiusura proposta è universale (§7.3).

### 9-ter.1 La causa dominante: la banda è inerte

Con banda 0,06 i candidati dentro banda sono **21-271**, mentre `top_k=16`
tronca la camminata di selezione. Il selettore reale non è la banda ma un cap
piatto su sedici slot in mezzo al rumore: l'unità corretta è spesso DENTRO la
banda e viene affamata. Ranghi misurati: `/admin` 36 (3ª di 20 unità di
registro sopra soglia), `/admin/changes` 28 (1ª di 9), `/admin/safety` 25
(1ª di 11), `/admin/users` 18 (7ª di 23) e 187 su un'altra query (fuori
banda, con la banda ancorata a un frammento-argomento di executor).
**Otto dei dieci** casi admin/conversation sono selezione; **zero** sono gate
lessicali stretti; **zero** sono fatti non attestati (ogni atomo è nel
registro: «forbidden» in 15 unità IT, «graylist» in 4).

Corollario che spiega cert17/cert18: Qwen «risolveva» la coda perché la sua
distribuzione è più larga (rapporto IQR 1,56) e la banda si affolla meno —
curava il sintomo di un difetto di selezione, pagandolo altrove.

### 9-ter.2 Difetti chiusi (universali, con controprova)

| difetto | evidenza | commit |
|---|---|---|
| contratto d'uscita non applicato sul path deterministico | `public` filtrava solo sul ramo HTTP: latente, ma esattamente sulla strada proposta per il composer | `3c27c170` |
| sonda contestuale autoavverante | concatenava la RISPOSTA precedente → vettore quasi-duplicato delle sue stesse fonti, guadagno sempre vinto (0,9317 vs 0,8465) | `94dfe3db` |
| ledger del correttore catturato da un'altra pagina | 15 voci richieste, tutte estranee alla domanda | `94dfe3db` |
| la ricomposizione poteva peggiorare la risposta | integra i buchi elencati e ne perde un altro, consegnata senza rilettura | `94dfe3db` |
| scarto per audience silenzioso | pagina esistente ma non autorizzata scartata in silenzio, risposta che dichiara ASSENZA (§2.8) | `94dfe3db`, poi **ristretto al primato** in §9-quater |
| rilettura cieca agli identificatori | nessun confine di parola dopo un underscore: voce presente alla lettera dichiarata mancante | `94dfe3db` |

### 9-ter.3 Interventi proposti, per rischio crescente

1. **Igiene della segmentazione** — 13 unità il cui testo estratto è il solo
   banner di pagina (8 sono sezioni di sola navigazione). Sono attrattori per
   ogni domanda breve simile a un titolo: due di esse vincono il contesto di
   `f1-overview#4` con affinità di titolo +0,064 contro banda 0,06. Test
   deterministico, indipendente da qualunque query.
2. **Finalità nelle righe provider** — `sources.py` emette
   `- {object}: {actions}` per i provider mentre le righe overview aggiungono
   gli scopi: la risposta può solo riecheggiare lo slug (f1-github#4). Prova
   incrociata: il gemello IT PASSA con la stessa fonte che NON contiene le
   parole attese → quel verde è glossa non attestata, non evidenza.
3. **Ledger delle clausole della fonte mantenuta primaria** — le clausole di
   una scheda curata o di un segmento di documentazione non entrano in
   checklist, quindi il correttore non vede omissioni di prosa
   (f1-scheduled#1/#2/#4).
4. **Autorità prima del micro-punteggio entro la banda** — l'ordine dentro la
   banda è dato da differenze di 0,02-0,04 non significative, e
   `priority/10000` vale al massimo 0,01, **sei volte meno della banda**: la
   priorità dichiarata di una scheda è oggi ininfluente. È la leva grossa
   (photos#2/#3, e i quattro casi di registro) ed è anche la più rischiosa:
   va misurata DA SOLA, con la ri-corsa completa dei 134 come gate.
5. **Budget del composer proporzionale alla checklist** — 44 aree contro 2048
   token fissi: la compressione estrema è la vera radice della varianza di
   `f1-overview#5` (verde in 5 run su 7).

### 9-ter.4 Il tetto reale, e i tre casi che richiedono una ratifica

Rumore A/A misurato: **3,1%** (3 casi discordi su 98 a contesto identico);
9,4% su quattro run. **Quattro dei 21 fallimenti di cert14 sono fantasmi**
(passano altrove con le stesse fonti). Il tier `wise` è già idoneo al path
deterministico (provider llamacpp, temp 0, think off): costo misurato **+23%**
(17,0s contro 13,8s) con SHA identico verificato. Attivarlo rende «134/134»
un bersaglio misurabile invece che mobile.

Non chiudibili senza toccare il corpus — DECISIONE DI ROBERTO:
- **ops-google-workspace**: il gate chiede `task|attività` per Google
  Workspace, ma Google Tasks non è attestato da nessuna delle 3.762 unità e
  non è una capacità dell'istanza (fallisce in 6 run su 6, zero varianza).
  Documentarlo sarebbe documentare il falso: la voce va rimossa o riformulata
  su un'area realmente proiettata.
- **ops-mail-credentials-typo**: il concetto atteso vive SOLO nel segmento
  «Compatibilità con configurazioni precedenti», a rango 24 e fuori banda. Una
  risposta corretta e moderna fallisce per costruzione; i verdi di cert18
  premiano la risposta pedagogicamente peggiore. Proposta: attendere il
  concetto della via preferita (vault cifrato), non il percorso legacy.
- **f1-overview#4**: chiudibile con l'igiene della segmentazione, ma va
  rimosso il marcatore `known_equal_fail`, che oggi documenta una causa
  sbagliata (non è un pari-fallimento F1).
- Edit MECCANICI letterale→`lex:`, già sanzionati da ADR 0198, non gaming:
  `ops-undo` («ultima» → concetto flessivo `tutor_gate.last`), `f1-devices`
  IT/EN («un solo|una volta», «one pairing|one-time» → `tutor_gate.one_off`
  esteso con «monouso», che è il termine della SoT `ui_surfaces`).

Estensioni di lessico RESPINTE perché sarebbero gaming: `verific\w*` e
`conferma\w*` in un gate «vagli» (falsi positivi misurati: «per verificare lo
stato di un task» non esprime alcun vaglio) e gli slug `\bpulls?\b`/`\bdirs?\b`
per f1-github#4 (premierebbero una risposta di soli slug, cioè proprio ciò che
il ledger vieta).

## 9-quater. Post-mortem di cert19 (25/7 sera): un fascio non si misura

I quattro fix di §9-ter.2 sono stati applicati IN BLOCCO: **94+7 contro 106+7**.
La tabella delle transizioni chiude la diagnosi senza congetture — 14 risposte
`fondata` diventate `restricted`, più 2 fondate che hanno perso la rotta con
`repair_pass 1→0`.

### 9-quater.1 Il segnale «restricted» ristretto al primato

Il fix §2.8 faceva scattare il rifiuto per autorizzazione appena UNA fonte
scartata per audience stava sopra la soglia e dentro la banda della migliore
visibile. Poiché le unità di registro stanno di norma sopra 0,70 — la soglia è
sotto il pavimento di rumore del corpus, con 155 candidati sopra soglia su una
query misurata — la condizione è quasi sempre vera: **14 casi su 134**.

Condizione onesta: il rifiuto è dovuto solo quando la risposta SAREBBE STATA la
fonte scartata, cioè quando nessuna fonte visibile la eguaglia — il **primato**,
valutato sullo stesso punteggio che ordina la classifica. Con una fonte visibile
in testa la risposta nasce da quella e non dichiara alcuna assenza.

### 9-quater.2 Il confronto fra due classifiche era fra scale diverse

I 2 casi restanti sono follow-up ellittici; la pagina non era in contesto,
quindi il correttore non poteva nemmeno vedere l'omissione. Rango della
superficie attesa, per formulazione della sonda:

| caso | sola domanda corrente | + domanda precedente |
|---|---|---|
| `changes-actions#2` → `/admin/changes` | rango 155, adj 0,7548, fuori banda | **rango 4**, adj 0,8162, selezionata |
| `device-token#2` → `/admin/devices` | rango 97, adj 0,7042, fuori banda | **rango 8**, adj 0,7562, selezionata |

La sonda arricchita recuperava la fonte giusta e il test di guadagno la buttava
via: 0,8578 contro 0,8421 + 0,02 richiesti, **mancati per 0,0043**. Il difetto
non è la soglia ma il confronto: i punteggi di TESTA di due sonde appartengono a
testi di lunghezza diversa (il normalizzatore dell'affinità di titolo è
`len(query_tokens)`, la densità si diluisce su un testo più lungo), quindi non
sono commensurabili. Un confronto fra scale diverse non misura nulla.

Regole a confronto sui 9 follow-up con rotta attesa — solo retrieval, nessun
modello, quante volte una fonte SELEZIONATA attesta la rotta:

| regola | attestate |
|---|---|
| scelta in blocco per punteggio di testa (cert19) | 5/9 |
| sempre la sonda arricchita | 7/9 |
| sonda con la RISPOSTA precedente (cert14) | 7/9, ma perde `pronoun-independence#2` |
| **massimo per fonte fra le due formulazioni** | 7/9 |

Scelta: massimo per fonte. Le due formulazioni entrano nella stessa classifica e
ogni fonte prende il proprio punteggio migliore; una domanda indipendente
conserva la fonte che risponde a sé stessa (controprova nel test), un follow-up
ellittico ottiene quella che risponde alla domanda risolta. Non c'è più una
scelta in blocco, quindi non c'è più un confronto fra scale.

### 9-quater.3 Rappresentanza per autorità dentro la banda

§9-ter.3 punto 4 implementato come **copertura, non riordino**: per ogni classe
di autorità presente in banda e assente dalla selezione entra la sua migliore,
sfrattando la più debole di una classe sovrarappresentata; il primario non cambia
mai e le espansioni strutturali (inventario di capacità, sezioni adiacenti) sono
protette dallo sfratto. Un riordino per autorità seppellirebbe invece la prosa
esplicativa sulle domande concettuali, mentre il difetto misurato è
l'AFFAMAMENTO: l'unità corretta è dentro la banda e il tetto piatto la taglia.

### 9-quater.4 Nota di accesso al composer: intervento SCARTATO con misura

Sembrava la chiusura onesta di §2.8: invece di rifiutare, dichiarare al composer
che esiste una fonte pertinente riservata, così la risposta nomina il confine di
autorizzazione (`admin-restricted-user` chiede esattamente questo: `["executor"]`
+ `lex:tutor_gate.admin_role`). Misurato sui 44 casi ad audience `user`:

| condizione della dichiarazione | scatta su |
|---|---|
| fonte riservata DENTRO la banda | 9/44 (20%) |
| fonte riservata almeno PARI al primario | **0/44** |

Le 9 sono le stesse che cert19 trasformava in rifiuti, e le fonti riservate in
banda sono estranee alla domanda: `executor-admin` per la LOC dei file,
`runtime-ui-turns` per una ricerca web, la procedura delle proposte per una
casella di posta. Dichiararle produrrebbe una nota di autorizzazione FALSA su un
quinto delle risposte. E su `admin-restricted-user` non scatta affatto: nessuna
fonte riservata entra in banda, perché la superficie `executors` non è nemmeno
CONOSCIBILE da un utente (`knowledge_audience` di default `instance_admin`; una
sola superficie, `devices`, è aperta).

Quindi quel caso non è una leva di retrieval: è una **ratifica** — vedi
§9-quater.5. L'intervento è chiuso come scartato, con la misura che lo scarta.

### 9-quater.5 Quarta decisione per Roberto: conoscibilità delle pagine admin

Un utente semplice può SAPERE che una pagina amministrativa esiste e che serve il
ruolo amministratore per aprirla? Oggi no, per tutte tranne `devices`. Se sì:
(1) `knowledge_audience="user"` sulle superfici che si vogliono conoscibili, e
(2) il testo dell'unità di superficie dichiara il ruolo d'ACCESSO, derivato dal
registro (`UiSurfaceSpec.audience`), una riga per ogni superficie. Se no,
`admin-restricted-user` va riformulato: chiede una risposta che il corpus
autorizzato non può fondare.

### 9-quater.6 Triage dei 22 fallimenti di cert20, per leva che li chiude

Non congetture: per ogni caso si è cercata nella RISPOSTA la forma flessa che il
gate letterale non vede. Il risultato ribalta due priorità.

| cluster | casi | leva che lo chiude | verificato |
|---|---|---|---|
| rotta e voci di pagina assenti | `admin-settings-errors`, `admin-services-telegram`, `admin-users-list-create`, `conversation-safety-classes#1/#2`, `conversation-device-token#2` | quota di autorità in banda | la superficie giusta è la **prima** unità di registro della classifica in 3 casi su 4 misurati (`registro-che-la-precede=0`) |
| clausola di prosa omessa | `f1-scheduled#1/#2/#4/#5`, `f1-devices#3` (metà) | ledger delle clausole della fonte primaria (§9-ter.3 punto 3) | nelle risposte **nessuna** forma di «monouso/one-off» è presente in #1/#2/#4: non è un problema di lessico, la clausola manca davvero |
| gate letterale contro forma flessa | `ops-undo` | edit meccanico `lex:` sanzionato da ADR 0198 | la risposta contiene «ultime» e «operazioni», il gate chiede «ultima»/«operazione» |
| compressione del composer | `f1-overview#5` (8 concetti mancanti) | budget proporzionale alla checklist (§9-ter.3 punto 5) | 44 aree contro 2048 token |
| ratifica del corpus | `admin-restricted-user`, `ops-google-workspace`, `ops-mail-credentials-typo`, `f1-github#4` | decisione di Roberto (§9-quater.5 e §9-ter.4) | — |
| rumore | `conversation-capabilities-local#2`, `f1-scheduled#5` (in parte) | nessuna: A/A 3,1% | passavano in cert14 con le stesse fonti |
| banda ancorata a un frammento-argomento | `admin-user-preferences` (rango 187, FUORI banda) | igiene della segmentazione, non la quota | la banda è ancorata a `open_sites._lang` |

Correzione di rotta rispetto a §9-ter.3: gli edit di lessico sanzionati valgono
**un caso**, non quattro; la leva del ledger ne vale quattro. La stima del tetto
raggiungibile senza ratifiche resta ~117 su 127 non-skip.

### 9-quater.7 Regola di metodo

**Una leva per certificazione, attribuita per gruppo.** cert19 è costato
un'attribuzione sbagliata: avevo imputato 2 dei 16 regressi al ledger del
correttore, mentre erano la sonda contestuale. Il fascio è comodo da scrivere e
impossibile da leggere.

## 9-quinquies. Il quadrato 2×2 della selezione (25/7 notte)

Due leve indipendenti, misurate separatamente e nella loro interazione. Quattro
run complete, stessa suite, stesso catalogo, nient'altro cambiato:

| | quota di autorità SPENTA | quota di autorità ACCESA |
|---|---|---|
| **massimo per fonte** fra le formulazioni | cert20 = 105 | **cert21 = 110** |
| **ammissione in coda** (3 posti) | cert22 = 104 | cert23 = 108 |

Base cert14 = 106. Letture:

1. **La quota di autorità vale +5, indipendentemente dall'altra leva** (105→110 e
   104→108). È il rimedio all'affamamento di §9-ter.1 e chiude 4 casi admin che
   nessuna calibrazione della banda aveva mosso.
2. **Il massimo per fonte batte l'ammissione in coda di 1-2 casi.** Ero partito
   dall'ipotesi opposta (§9-quater.2, leva «2b»): l'ammissione in coda era stata
   scritta proprio per proteggere il primario dai 3 follow-up che cambiano
   argomento. Misurata, **non li recupera** — regrediscono in entrambe le
   varianti — e in più perde `conversation-mailbox-credentials#2`. Tre posti in
   coda diluiscono senza risolvere: il difetto non era l'ORDINE, era la
   FORMULAZIONE della seconda sonda (§9-quinquies.1).
3. Le due leve non interferiscono: `+5` additivo e insiemi di casi disgiunti
   (la quota lavora su `admin_ui`, la sonda su `conversation`).

Configurazione congelata per il commit: primato onesto del rifiuto + massimo per
fonte + **quota accesa per default** (`METNOS_TUTOR_AUTHORITY_QUOTA`, spegnibile
come le altre calibrazioni). cert24 la riverifica col default, senza env.

### 9-quinquies.1 La seconda sonda non è la domanda precedente: è la congiunzione

Il difetto residuo, letto sui 4 casi di conversazione che non chiudono in
cert21: il massimo per fonte fra «domanda corrente» e «domanda precedente» è una
**disgiunzione** di due argomenti, mentre la domanda che l'utente ha fatto
davvero è la loro **congiunzione**. «E dove li configuro?» dopo «Dove vedo le
esecuzioni dei task pianificati?» non vale né come «configurare» né come
«esecuzioni»: vale come «dove configuro i task pianificati». La domanda
precedente da sola, poi, **non è una domanda che l'utente ha fatto** — ed è
proprio quella che si prende il primario e riporta la risposta al turno prima.

Sonda: `probe_conjunction.py`, tutti i 12 scambi del corpus, solo retrieval,
nessun modello. Rango della superficie attesa e sua presenza in selezione:

| scambio | precedente nuda | **congiunzione** | senza contesto |
|---|---|---|---|
| `runs-to-timers#2` | 30, fuori | 11, fuori | 88, fuori |
| `device-token#2` | 109, fuori | **16, selezionata** | 97, fuori |
| `pronoun-independence#2` | 24, fuori | **6, selezionata** | 3, fuori |
| `changes-actions#2` | 19, sel. | **5**, sel. | 38, fuori |
| `rejected-reason#2` | 46, sel. | **25**, sel. | 975, fuori |
| `google-followup#2` | 3, sel. | **1**, sel. | 2, sel. |
| `safety-classes#2` | 26, sel. | 31, sel. | 556, fuori |
| `capabilities-local#2` | 2, sel. | 3, sel. | 3, sel. |
| `mailbox-credentials#2`, `services-controls#2`, `telegram-ui-location#2`, `build-details#2` | 1, sel. | 1, sel. | 5-1432 |

Il rango migliora in 11 casi su 12 e nessun caso verde perde la selezione
(`safety-classes#2` scende di 5 posizioni restando dentro). Due dei quattro rotti
portano in contesto la superficie che oggi manca. La colonna «senza contesto»
misura quanto il contesto serva: 8 scambi su 12 non trovano affatto la fonte.

Leva C, una sola riga in `tutor/service.py`: la seconda formulazione diventa
`f"{precedente} {corrente}"`. `retrieve_sources` resta agnostica — riceve «una
seconda formulazione della stessa domanda», e la congiunzione lo è più della
precedente nuda. cert25 la isola contro cert24.

### 9-quinquies.2 Leva D già istruita: il collegamento fra superfici del registro

`runs-to-timers#2` migliora (30→11) ma resta fuori banda, perché la congiunzione
rafforza anche la pagina del turno prima. Il registro però **dichiara già** la
relazione che serve: la superficie `runs` elenca fra i controlli «collegamento a
Timer, dove si configura il task». Terza espansione strutturale, accanto alle
sezioni adiacenti e ai fratelli d'inventario: se il primario è una superficie che
dichiara un collegamento a un'altra superficie, quella entra in contesto.

Vincolo di progetto: il collegamento oggi vive nella PROSA di `controls_it/en`.
Inferirlo dal testo sarebbe indovinare; va dichiarato come dato — un campo
`links` di chiavi di superficie in `UiSurfaceSpec`, compilato dove il controllo
già lo dice (3 superfici). Deterministico, dal registro, senza liste di frasi
(§7.3, §7.9).

## 9-sexies. Leva E istruita con la misura: il ledger ignora la PROSA

I 4 casi `f1-scheduled` sono il gruppo più grande che resta, e la loro diagnosi
in §9-quater.6 («la clausola manca davvero») ora ha un colpevole preciso.

Il concetto richiesto è `lex:tutor_gate.one_off` — la distinzione fra
un'esecuzione sola e una ricorrente. Prima ipotesi, misurata e SCARTATA: la
clausola sta nell'argomento `times` del manifest `create_tasks` («1 = ONE-SHOT,
esegue una volta sola, poi auto-cancella»), unica fonte strutturata che la
attesti. Rango di quell'unità sulle sei formulazioni del set:

| formulazione | rango di `create_tasks.times` | selezionata |
|---|---|---|
| #1 IT ricorrente/email | 50 (adj 0,7456) | no |
| #2 IT promemoria periodico | 50 (adj 0,7537) | no |
| #4 EN recurring/email | 18 (adj 0,7683) | no |
| #5 EN reminder periodic | 75 (adj 0,7386) | no |

Sopra soglia, sempre fuori banda. Ma la fonte PRIMARIA di #1, #2, #4 e #5 è la
scheda curata `attivita-programmate` (adj 0,84-0,90), e il suo corpo dichiara
**entrambi** i concetti mancanti, in entrambe le lingue:

- «Metnos ti mostrerà la schedulazione risultante e applicherà i normali
  **vagli**» → il gate `vagli | controlli` / `gates | checks`;
- «Per una **sola esecuzione**, ometti la ricorrenza» / «For a **one-off** run,
  omit the recurrence» → il gate `one_off`.

Quindi non è un problema di selezione: **la fonte giusta è la prima della
classifica e il composer ne butta via due frasi su quattro**. Il correttore non
lo vede perché `_coverage_items` costruisce la checklist SOLO da fonti
strutturate (inventari, manifest, superfici): una scheda curata, come una
sezione di prosa, contribuisce **zero** voci.

Leva E, ambito minimo e difendibile: quando la primaria è una **scheda curata**,
le clausole del suo corpo entrano nel ledger (parola distintiva per clausola,
stesso meccanismo di `_label_covered`, cap esplicito). Una scheda è una risposta
BREVE e scritta a mano: perderne una clausola è un difetto per costruzione. Una
sezione di documento pubblico è invece un estratto di un testo più lungo e non
ha lo stesso contratto — l'estensione alla prosa `manual` resta separata, da
misurare dopo.

## 9-septies. Il quadrato misurato, e l'audit dei 14 residui (26/7)

### 9-septies.1 Le quattro leve della notte, con l'attribuzione

| cert | configurazione | pass | contro il precedente |
|---|---|---|---|
| 24 | default congelato | 110 | riferimento |
| **25** | **+ leva C (congiunzione)** | **113** | +3 / -0, tutti in `conversation` |
| 26 | + leva E (clausole della scheda) | 108 | +0 / -5 |
| 27 | + leva H (inventario) sopra E | 109 | +2 / -1 |
| 28 | + leva H senza E | 112 | -1 contro cert25 |

**Leva C validata**: +3 casi, tutti nel gruppo che la leva mira, coerente con
la sonda offline sui dodici scambi (§9-quinquies.1). È la configurazione tenuta.

**Leva H ritirata**: senza la leva E non recupera `f1-overview#5` e perde
`ops-calendar-events`. Il recupero visto in cert27 era interazione con E, non
effetto della leva: rimossa E, il caso torna a fallire. Il codice è tornato allo
stato certificato in cert25, quindi il ritiro non richiede una misura nuova.

**Leva E ritirata, e per la ragione SBAGLIATA rispetto a quella scritta**: il
suo −5 non veniva dalle clausole. In `--f2-only` `card_ids` è **vuoto in tutti i
run** (cert24, 25, 26, 27, 28 verificati): nessuna scheda entra mai in
selezione, quindi `_guide_clauses`, che si attivava solo su `primary.card`, non
ha mai prodotto una clausola. Ciò che ha cambiato il comportamento è
l'**istruzione del ledger**, riscritta per OGNI risposta in modo da chiedere
«and guide clause» — una famiglia di voci mai fornita. Cinque casi persi per una
perturbazione del prompt a carico utile nullo. Lezione riusabile: una modifica
al testo del ledger è essa stessa una leva, e va condizionata alla presenza del
carico.

### 9-septies.2 I 14 residui: rieseguiti, e classificati per punto di rottura

Due passate indipendenti sugli stessi casi (piloti 17 e 18): **12 falliscono in
modo riproducibile, 2 cambiano fra passate identiche** — `ops-undo` e
`f1-scheduled#4`. Il rumore non è nullo, ed è la ragione per cui i verdetti da
1-2 casi di questa sessione restano deboli finché la banda non è misurata su
tutto il corpus (cert29a/b, due passate a configurazione invariata).

L'audit incrocia, per ogni concetto mancante, tre fatti: chi lo attesta nel
catalogo, se quella fonte era in selezione, e se la risposta ne dice una
flessione. **Nessuna delle 14 attese è impossibile**: tutte sono attestate da
fonti F2 visibili all'audience del caso. La rottura si divide così:

| natura | voci | casi |
|---|---|---|
| **C composer** — la fonte è in selezione, la risposta omette | 10 | `f1-scheduled#1/2/4/5`, `ops-mail-credentials-typo`, `ops-undo` |
| **B retrieval** — attestato, nessuna fonte selezionata | 17 | `admin-services-telegram`, `admin-users-list-create`, `admin-user-preferences`, `admin-restricted-user`, `conversation-runs-to-timers#2`, `f1-github#4`, `f1-overview#5`, `ops-google-workspace` |
| **D formulazione** — letterale dove serve un gate | 1 | `ops-undo` |

Dato strutturale sui B: `f1-github#4` seleziona **una sola fonte** e
`ops-google-workspace` due. Quando la primaria è un'unità di registro, la banda
0,06 taglia tutto il resto e i concetti attestati dalla pagina dei domini non
entrano mai. È la causa di 2 residui su 8, e non è la coda dell'embedder: è la
larghezza della banda attorno a una classe di fonti particolare.

### 9-septies.3 Tre difetti del TEST, corretti con l'evidenza

1. **`ops-undo`** chiedeva il letterale «ultima». La risposta dice «ultimo», ed
   è la stessa cosa: il caso passava o falliva secondo la flessione scelta dal
   composer (misurato instabile fra due passate). Il meccanismo sancito per
   questo è il gate a radice flessiva: nuovo `lex:tutor_gate.last` (it+en) nel
   seed, e `must_cover` che lo usa. Non è un test addolcito — è il test scritto
   col meccanismo che il progetto ha già deciso di usare.
2. **`ops-google-workspace`** pretendeva l'area `tasks`. Il catalogo firmato
   espone il provider in **10 aree** e `tasks` non è fra queste: soddisfare
   l'attesa avrebbe richiesto di dichiarare una capacità inesistente (§2.8). La
   risposta era corretta e completa. Attesa rimossa, con la nota di evidenza
   dentro il caso.
3. **`ops-mail-credentials-typo`** pretende il percorso del file di
   configurazione, oppure la parola «file». La risposta descrive il **modulo
   iniettato** (ADR 0199), che è la via corrente per un utente. Qui non decido:
   quale delle due sia la risposta giusta per un utente è una scelta di
   prodotto, e resta in attesa di ratifica.

### 9-septies.4 Leva E′: le clausole della primaria quando è PROSA

La diagnosi di §9-sexies era giusta nel meccanismo e sbagliata nell'ambito. Le
fonti che attestano `one_off` e `vagli | controlli` non sono le schede curate,
ma le sezioni pubbliche `doc-public-tutor-…-0014/0015` — e in `f1-scheduled#2`,
`#4` e `#5` la sezione è **la primaria**. `_coverage_items` costruisce voci solo
da fonti strutturate, quindi una sezione di manuale contribuisce zero e la sua
clausola omessa è invisibile alla rilettura.

Leva E′: `_prose_clauses` legge il corpo della primaria quando è prosa — scheda
curata **o** unità `manual` — con cap esplicito, minimo di parole, e due
esclusioni. Fuori le frasi che portano un esempio fra virgolette (chiederne la
ripetizione letterale genera imitazione semantica, §6) e i segnaposto del
renderer. L'istruzione del ledger cambia SOLO quando esiste almeno una clausola:
è la correzione diretta dell'errore misurato in cert26.

Due difetti dell'estrattore trovati dai test prima della misura, entrambi
sull'esempio che precede la clausola bersaglio:

- il terminatore seguito da una chiusura di citazione (`.»`) non spezzava la
  frase, e l'esempio si portava dietro la clausola successiva — proprio quella
  che i casi richiedono;
- in inglese l'esempio usa virgolette curve (`“ ”`), non caporali, e sfuggiva al
  filtro. Corretto come **classe tipografica**, non per lingua.

Verificato sulle fonti reali: in IT e in EN entrambi i concetti mancanti sono
ora clausole distinte del ledger.

### 9-septies.5 Il ledger è un budget SATURO: leva E′ ritirata, e la ragione vale in generale

La leva è stata isolata senza spendere una certificazione, riscorando le
risposte già registrate di cert25 col corpus corretto: stesse risposte, matcher
nuovo. Differenza attribuibile alla sola E′: **+3 / −5**.

- **Guadagni**: `f1-scheduled#2`, `#4`, `#5` — esattamente i bersagli. `#1` no,
  e coerentemente: la sua primaria è un'unità strutturata, quindi la leva non si
  attiva.
- **Perdite**: `f1-changes#3`, `#6`, `f1-devices#5`, `ops-sites-login` (più
  `admin-services-content`, che è rumore del classificatore di modo: risposta
  `fallthrough` in 417 ms). Tutte e quattro hanno primaria `manual`, cioè sono
  casi in cui la leva si attiva — e la leva si attiva su **63 casi su 127**,
  metà corpus.

Il meccanismo, letto nella telemetria: sui tre guadagni la riparazione **non
scatta mai** (`repair_pass=0` in cert25 e in cert29a), e sulle quattro perdite
`repair_missing` non contiene nessun buco di clausola. Quindi E′ non agisce come
correttore: agisce arricchendo il PRIMO prompt. Il ledger elencato al composer
non è una checklist gratuita — è una lista di richieste che compete con le altre
per l'attenzione del modello, e il carico che si aggiunge sposta contenuto
obbligatorio già coperto.

**Regola che ne esce, e che vale oltre questa leva**: il ledger è un budget
saturo. Aggiungere una famiglia di voci al prompt del composer costa più di
quanto renda, perché il guadagno si concentra sulla famiglia nuova mentre il
danno si distribuisce su tutte le altre. Le due leve tentate su questa strada —
E (che non aveva nemmeno carico utile) ed E′ (che l'aveva, e mirato) — perdono
entrambe. Prima di riproporre una variante servono due cose: una misura del
livello di rumore, e un meccanismo che NON passi dal prompt del composer.

**Difetto separato, trovato per strada e non ancora affrontato**: il ledger di
`ops-sites-login` chiede 46 aree di capacità, tre provider e l'inventario
completo della pagina Dispositivi — per una domanda su come si fa il login a un
sito. Quando l'inventario di capacità entra in una domanda che non è una
panoramica, la rilettura spende l'unica ricomposizione su decine di voci
estranee. È indipendente da E′ (presente identico in cert25) e vale una leva a
sé, sul PERIMETRO del ledger e non sulle sue famiglie.

### 9-septies.6 Il livello di rumore, misurato — e quali verdetti resta lecito emettere

Due coppie di passate a **codice e corpus invariati**:

| coppia | carico della macchina | casi che cambiano |
|---|---|---|
| cert29a / cert29b | certificazione + turni E2E in parallelo | 2 (`ops-calendar-events`, `ops-places-nearby`) |
| **cert30 / cert31** | **macchina scarica, nulla in parallelo** | **1** (`conversation-capabilities-local#2`) |

La prima coppia è un **limite superiore**, non la banda: girava in contesa di
GPU con i turni live, e la contesa allunga la certificazione da ~20 a ~32 s per
caso. La seconda è la misura buona: **la banda di rumore è 1 caso**, e cresce con
il carico della macchina — cioè il rumore non è una proprietà del Tutor, è una
proprietà delle condizioni in cui si misura.

Conseguenza retroattiva sui verdetti della sessione, da applicare senza sconti:

- **Attribuibili**: leva A (+14), quota di autorità (+5 additivi in due coppie),
  leva C (+3), leva E (−5). Tutte fuori banda.
- **Dentro la banda, quindi NON attribuibili come effetto**: il −1 della leva H e
  l'1-2 casi con cui il massimo per fonte batteva l'ammissione in coda. Entrambe
  le leve restano ritirate, ma per l'argomento strutturale (H non recupera il
  caso per cui era scritta; 2b non recupera i tre follow-up per cui era scritta),
  non per il numero.
- **Immune al rumore per costruzione**: il +3/−5 della leva E′, ottenuto
  riscorando risposte già registrate. Quando una leva vive nel matcher e non nel
  modello, il ri-punteggio è la misura giusta e costa zero certificazioni.

Regola di metodo che ne segue, da usare d'ora in avanti: **certificare a
macchina scarica, e non emettere verdetti da 1-2 casi**. Se una leva promette
meno di 3 casi, o si misura per ri-punteggio, o non si misura.

### 9-septies.7 Configurazione consegnata

Codice di prodotto: **la sola leva C** (sonda di compagnia = congiunzione della
domanda precedente con quella corrente). Corpus: le due correzioni di
§9-septies.3. Nient'altro delle sette leve tentate sopravvive.

| cert | configurazione | pass | contro cert25 |
|---|---|---|---|
| 30 | C + corpus corretto | 114 | +2 / −1 |
| **31** | **identica a cert30** | **115** | +2 / −0 |

**115 + 7 skip / 134**, `boundary` 12/12, 12 residui: 4 `admin_ui`
(`admin-restricted-user`, `admin-services-telegram`, `admin-user-preferences`,
`admin-users-list-create`), 1 `conversation` (`conversation-runs-to-timers#2`),
6 `f1_equivalence` (`f1-github#4`, `f1-overview#5`, `f1-scheduled#1/2/4/5`), 1
`typical_operations` (`ops-mail-credentials-typo`, in attesa di ratifica).

I due guadagni sul corpus sono guadagni di **verità del test**, non di capacità:
`ops-undo` ora misura il concetto invece di una flessione, e
`ops-google-workspace` non pretende più un'area che il catalogo firmato non
espone. La differenza fra 114 e 115 è la banda.

Le tre strade che restano aperte, in ordine di rapporto valore/rischio: la
larghezza della banda attorno alle unità di registro (2 residui su 8 dei B,
§9-septies.2), il PERIMETRO del ledger (46 aree su una domanda di login,
§9-septies.5), la leva D già istruita (§9-quinquies.2). Nessuna passa dal prompt
del composer, ed è il punto.

## 9-octies. Le due ratifiche del 26/7 alla prova (cert32): una passa, una è bocciata dalla misura

Roberto ha ratificato tre casi in sospeso. Il secondo (`f1-github#4`) resta
rosso per scelta: l'attesa di otto famiglie a «quali operazioni GitHub sono
disponibili» è legittima, e il difetto è che quella domanda seleziona UNA fonte
sola — si chiude allargando la banda attorno alle unità di registro, non
abbassando l'asta. Gli altri due sono stati implementati e misurati insieme in
cert32: **110 pass**, cioè **+2 / −7 contro cert31** (115). Fuori banda, quindi
attribuibile.

### 9-octies.1 Ratifica del corpus mail: validata

`ops-mail-credentials-typo` pretendeva il percorso `~/.config/metnos/mail/<nome>.env`
e il campo `HOST_IMAP`. Le fonti pubbliche lo attestano — ma come **percorso di
compatibilità e migrazione**, e dichiarano che «il vault cifrato ha precedenza»
(`doc-public-mail-accounts-766a246aa389-it-0005`). Il test misurava quindi la via
deprecata. Riscritta sul meccanismo corrente (modulo credenziali iniettato, ADR
0199: Metnos chiede i campi in un flusso protetto, i segreti sono cifrati e
sostituiti da un marcatore redatto prima del planner): **il caso passa**, e
`typical_operations` chiude a 32/0.

### 9-octies.2 Conoscenza delle pagine admin: la politica regge, l'implementazione no

La politica ratificata — la conoscenza delle superfici è aperta all'utente,
l'ACCESSO resta admin — è coerente con un fatto già vero: lo stesso registro
genera la guida pubblica `docs/{it,en}/interface.html`, che pubblica ogni route e
ogni sommario. Tenere il Tutor più muto della documentazione pubblicata non
proteggeva niente.

L'implementazione misurata era: `knowledge_audience` con default `user` (28 unità
di superficie visibili all'audience `user`) più una clausola d'accesso derivata da
`audience` aggiunta al **testo** di ognuna. Due esiti distinti, entrambi provati:

1. **Non risolve il proprio bersaglio.** `admin-restricted-user` continua a
   fallire, ed è il record a dirlo: esito `fondata`, otto fonti in selezione, e
   **nessuna è la superficie** — vincono documenti pubblici (catalogo executor,
   QuickTour, Tutor) e argomenti di executor. La risposta dice, onestamente, «il
   contesto non specifica una pagina dedicata». Aprire l'audience non porta la
   pagina in selezione: la porta solo nel campo dei candidati. Il difetto era, e
   resta, di RETRIEVAL.
2. **Costa 6 casi.** Perde `admin-devices-pairing`, `admin-timers-controls`,
   `admin-turns-channel`, `admin-user-detail` (audience amministratore: per loro
   la visibilità non è cambiata, solo il testo) e i due
   `conversation-telegram-ui-location`. I concetti che spariscono sono campi
   dell'elenco visibile della pagina: `admin-turns-channel` perde «Telegram»,
   `admin-user-detail` perde «nome visualizzato» ed «email».

**Generalizzazione, ed è più larga di quella di §9-septies.5**: il budget saturo
non è solo il ledger, è anche il TESTO DELLA FONTE. Una frase aggiunta a ogni
unità è una leva a tutti gli effetti, e paga il suo posto togliendolo a un campo
obbligatorio. Il corollario operativo: un'informazione che serve in un caso su
venti non va scritta nelle venti fonti, va aggiunta a valle nel solo caso che la
richiede — qui, il richiamo al ruolo appartiene al momento della risposta e al
solo principal non amministratore, non al catalogo.

La politica resta ratificata e **da implementare**; la reimplementazione va
misurata da sola, perché la sola visibilità estesa cambia la competizione nei
casi utente e non è stata isolata da questa passata. Consegnato di cert32 resta
il solo corpus mail (`typical_operations` 32/0).

## 9-nonies. Un nome di documento non è una collocazione file (28/7)

Il turno `2922f38388974a83` ha chiesto che cosa contenesse
`metnos_prospettive_estese_v1.html`. Il motore lo ha trattato come un file
utente, ha ereditato la collocazione appiccicosa `PC-ROBERTO` e ha costruito un
percorso Windows inesistente. Due errori distinti: destinazione presunta e
dominio presunto.

La correzione non usa la parola «file», un nome speciale o una precedenza
server. `published_docs.resolve_reference()` confronta nome completo, percorso
relativo e URL canonico con lo stesso inventario validato che governa
pubblicazione e compilazione Tutor. La corrispondenza esatta produce
un'attestazione del runtime per il mode gate; non riconoscere o non distinguere
la fonte produce `None`.

Per una lettura o un riassunto, `answer_request()` carica le unità firmate e
passa a `retrieve_sources()` il solo `source_ref` del documento. Le schede e
le altre unità sono escluse; la similarità ordina le sezioni della fonte ma non
può cambiarne l'identità. Una modifica, uno spostamento o una cancellazione
restano `ACT` e ricadono nel motore normale. La collocazione dell'ultimo turno
non entra in questo percorso.

L'inventario delle identità viene riutilizzato finché non cambia l'identità
filesystem delle pagine; sull'host corrente il controllo caldo costa circa
5-6 ms invece dei circa 125 ms necessari a riparsare tutti gli HTML a ogni
turno. Test di modulo,
test di servizio e certificazione reale coprono anche suffissi più lunghi,
ambiguità fra lingue, nome sconosciuto e cancellazione. La prova completa ha
restituito `detection=published_document_reference`, sole fonti del documento,
collegamento canonico e nessuna azione. ADR 0203.

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
| 2026-07-25 | Diagnosi della coda di selezione | Replay retrieval con le query REALI del corpus: registro superfici completo (users attesta preferenze e navigazione web; services attesta componenti e riavvio), proiezione con percorso+route+scopo. La coda (~6 casi) è un salto semantico dell'embedder: «persone che possono usare» ≠ «utenti» (vince QuickTour), «lingua/stile/browser per utente» vince `open_sites`, «componenti attivi» vince timers. Conseguenza a valle: il correttore insegue il ledger del contesto sbagliato (chiede provider su una domanda di pagina). Nessun fix ammesso per liste di frasi (§7.3, memoria anti-sinonimi): opzioni = embedder più forte, associazioni apprese (F3), o coda accettata e documentata |
| 2026-07-25 | Ritiro F1 tranche 1 eseguito | `console-proposte` + `dispositivi-accoppiamento` in `retired/`; vincolo 6-schede rimosso dal compilatore; `ui_map` demolito (zero consumatori verificati); test F1 migrati al mondo a 4 schede (procedura = ramo tipizzato `ui_procedure`). cert16 con schede ATTIVE: f1-changes 6/6, f1-devices 6/6, boundary 12/12 — criterio di coincidenza col run f2-only rispettato. Scoperta collaterale: la scheda photos non vince il retrieval su 3/6 query del suo set (condizione preesistente, in carico alla tranche 2) |
| 2026-07-25 | Embedder Qwen cablato dietro `virt` | provider `qwen` (`runtime/qwen_embedding.py`): instruction-aware sul solo lato query via `embed_query` (i fake di test espongono i due lati), fingerprint del catalogo provider-aware; default di prodotto BGE INTATTO, override via `METNOS_EMBEDDING_TIERS_CONFIG`. A/B sulla coda: BGE 4/8 → Qwen 7/8 in top-5. cert17 completa in corso |
| 2026-07-25 | cert17: Qwen end-to-end BOCCIATO senza ricalibrazione | f2-only, catalogo dedicato: 85+7/134 (BGE in cert14: 106+7). 27 regressioni, ZERO a fonti identiche = selezione spostata ovunque; 20 esiti «lacuna» (cert14: 0) = la soglia assoluta 0,70 e la banda 0,06 sono CALIBRATE SULLA DISTRIBUZIONE BGE e tagliano fuori i coseni Qwen, che vivono su un'altra scala. L'A/B puro-denso (ordinamento) resta valido: 7/8 vs 4/8 sulla coda. VERDETTO: niente flip di prod; riesame SOLO con ricalibrazione di soglia e banda derivate dalla distribuzione misurata (per percentili, come §7.4 già prescrive) e cert18. Tranche 2 resta in attesa: le 3 schede informative continuano a guadagnarsi il posto. E2E prod post-ritiro verdi: changes `c731ceb2836e4c52`, devices `6768ff36053047e6` |
| 2026-07-25 | cert18: Qwen ricalibrato per percentili | knowledge_min=0.643 (percentile 0.0 della distribuzione BGE), banda=0.094 (rapporto IQR 1.563). Esito: 100 pass + 7 skip / 134, 27 fail; lacune 1 (cert17: 20). Riferimento BGE (cert14): 106+7/21. Decisione flip = Roberto |
| 2026-07-25 | cert19: il fascio dei quattro fix BOCCIATO | 94+7/134 contro 106+7. Transizioni: `fondata`→`restricted` ×14 (il fix §2.8 scattava su ogni scarto in banda, e le unità di registro stanno di norma sopra soglia) + 2 follow-up ellittici senza la pagina in contesto. Post-mortem e correzioni: §9-quater. Lezione di metodo: UNA leva per certificazione |
| 2026-07-25 | cert20: «restricted» al primato + massimo per fonte | 105+7/134. Leva A validata: **+14** contro cert19, `restricted: 0` sul corpus, e la sonda misurata 0/44 sui casi utente conferma che il primato non scatta a vuoto. Leva B (massimo per fonte) SCARTATA: fixa `changes-actions#2` ma rompe 3 casi in cui il follow-up cambia argomento, perché il compagno si prende il primario. Sostituita dall'ammissione in coda (leva 2b, `_COMPANION_SLOTS=3`), con controprova esplicita sull'ordine |
| 2026-07-25 | cert21-cert23: il quadrato 2×2 della selezione | **cert21 = 110+7/134**, il migliore misurato (base cert14: 106). Quota di autorità **+5 additivi** in entrambe le coppie (105→110, 104→108); massimo per fonte batte l'ammissione in coda di 1-2 casi, che non recupera i 3 follow-up per cui era stata scritta e perde `conversation-mailbox-credentials#2`. Leva 2b ritirata con la sua misura. Configurazione congelata = primato + massimo per fonte + quota ON di default; §9-quinquies |
| 2026-07-25 | Leva C isolata: la sonda è la congiunzione | La seconda formulazione non è la domanda precedente (che l'utente non ha fatto e che si prende il primario) ma la sua congiunzione con quella corrente = la domanda risolta. Sonda su tutti i 12 scambi, solo retrieval: rango della superficie attesa migliore in 11 casi su 12, due dei quattro rotti entrano in selezione, nessun verde perde la fonte. cert25 la isola contro cert24; §9-quinquies.1 |
| 2026-07-25 | cert24: il default congelato riproduce la misura | 110+7/134 e **zero casi di differenza** contro cert21, che aveva la quota accesa da variabile d'ambiente. Il default di prodotto è quindi la configurazione misurata, non una sua approssimazione; i 17 residui sono 4 admin_ui, 4 conversation, 6 f1_equivalence, 3 typical_operations |
| 2026-07-25 | cert25: leva C validata in isolamento | **113+7/134**, il migliore misurato. +3 / -0 contro cert24, tutti e tre nel gruppo `conversation` che la leva mira: la sonda di compagnia deve essere la CONGIUNZIONE della domanda precedente con quella corrente |
| 2026-07-26 | cert26-cert28: leve E e H ritirate con la misura | E = 108 (-5) e H = 112 (-1 contro cert25). Scoperta che riqualifica il -5 di E: in `--f2-only` nessuna scheda entra MAI in selezione, quindi le clausole non sono mai state prodotte — a cambiare il comportamento era l'istruzione del ledger, riscritta per ogni risposta a chiedere una famiglia mai fornita. Una modifica al testo del ledger è essa stessa una leva (§9-septies.1) |
| 2026-07-26 | Audit dei 14 residui: nessuna attesa impossibile | Due passate indipendenti: 12 fallimenti riproducibili, 2 instabili. Ogni concetto mancante è attestato da fonti F2 visibili all'audience. Rottura: 10 voci al composer (fonte in selezione, risposta che omette), 17 al retrieval, 1 alla formulazione del test. `f1-github#4` seleziona 1 sola fonte e `ops-google-workspace` 2: attorno a un'unità di registro la banda 0,06 taglia tutto (§9-septies.2) |
| 2026-07-26 | Tre difetti del corpus corretti con l'evidenza | `ops-undo` letterale «ultima» → gate `lex:tutor_gate.last` (instabilità misurata come prova); `ops-google-workspace` pretendeva l'area `tasks` che il catalogo firmato NON espone (10 aree) e avrebbe imposto di dichiarare una capacità inesistente; `ops-mail-credentials-typo` (file di configurazione contro modulo iniettato ADR 0199) resta in attesa di ratifica |
| 2026-07-26 | Leva E′: clausole della primaria quando è prosa | Ambito corretto di §9-sexies: le fonti che attestano `one_off` e `vagli` sono sezioni `manual` pubbliche, primarie in 3 dei 4 casi, non le schede. Istruzione del ledger condizionata alla presenza di clausole. Due difetti dell'estrattore trovati dai test: terminatore seguito da chiusura di citazione, e virgolette curve in EN (corretto come classe tipografica). 100 test tutor verdi; cert29a/b misura insieme la leva e il LIVELLO DI RUMORE |
| 2026-07-26 | Leva E′ ritirata: il ledger è un budget saturo | Isolata a **+3 / −5** riscorando le risposte di cert25 col corpus corretto (stesse risposte, matcher nuovo). Sui guadagni la riparazione NON scatta e sulle perdite non c'è nessun buco di clausola: la leva agisce arricchendo il PRIMO prompt, e il carico aggiunto sposta contenuto obbligatorio già coperto. Si attivava su 63 casi su 127. Regola generale: aggiungere una famiglia di voci al ledger costa più di quanto renda — una variante richiede un meccanismo che non passi dal prompt del composer (§9-septies.5). Difetto separato aperto: 46 aree di capacità chieste a una domanda di login |
| 2026-07-26 | cert32: ratifica corpus mail validata, conoscenza admin bocciata | 110+7/134 (+2/−7 contro cert31). `ops-mail-credentials-typo` passa col meccanismo corrente (ADR 0199) e `typical_operations` chiude 32/0: il file `.env` che il test pretendeva è documentato come percorso di COMPATIBILITÀ. L'apertura di `knowledge_audience` non porta la superficie in selezione (8 fonti, nessuna è la pagina: difetto di retrieval) e la clausola d'accesso aggiunta al testo di 28 unità costa 6 casi. Il budget saturo vale anche per il TESTO DELLA FONTE, non solo per il ledger (§9-octies) |
| 2026-07-28 | F3 implementato | Quattro sonde chiuse con capsule, audience, timeout/TTL/cache per principal e scheduler; consegna letterale monouso su HTTP e Telegram tramite `dialog_pending`; ADR 0202, 18 test F3/F4 mirati. |
| 2026-07-28 | F4 implementato e certificato | Ledger 0600 senza query in chiaro, associazioni per utente, ✓/✗, mappa del debito, oblio e replay. Certificazione isolata: una fonte reale da rango 3184 a 1, 1/1 replay, 9/9 richieste operative fuori dal Tutor, associazione di prova eliminata. |
| 2026-07-28 | Identità delle fonti pubblicate | ADR 0203; nome/percorso/URL esatto prima del mode gate, retrieval vincolato al documento. Caso reale `metnos_prospettive_estese_v1.html`: risposta fondata con collegamento canonico, sole fonti della pagina e nessuna azione; 296 test mirati complessivi verdi. |
| 2026-07-30 | RM-0003 chiusa | Catalogo firmato: 4 schede e 3.396 unità. F3: consegna mista, osservazione live e documento esatto certificati. F4: associazione per utente da rango 2 a 1 e confine operativo 9/9. Suite finale: 139 test Tutor, 236 test prompt/i18n, campione UI critico verde; servizio caricato dal virtualenv di Metnos e distribuzione pubblica verificata. |
