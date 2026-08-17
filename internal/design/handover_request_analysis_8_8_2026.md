# Handover operativo — RequestAnalysis strutturata

Aggiornato: **9 agosto 2026**.

Questo è il punto di ingresso breve per Codex, Claude o un altro agente. La
cronologia completa non è più duplicata qui: resta nei README e nei report
versionati sotto `internal/tools/request_analysis_lab/`.

## 0. Stato in una frase

V26.5.6–V26.5.6.3 sono in **STATIC BLOCK** indipendente. V26.5.6.4 è
**STATIC PASS** offline (85/85; review indipendente: 594 mutazioni scalari e
45 end-to-end respinte), ma il suo unico live K1/34 è **fallito
semanticamente**: trasporto 34/34, retry 0, p50 6,109 s e p95 9,447 s, però
frame validi 0/34. Cause registrate: 32 volte `two semantic clauses claim the
same source span`, una dipendenza senza proiezione sullo stesso span, un caso
`proof_family` + `reference_type`. L'evaluator congelato conferma 0/34
utilizzabili e non autorizza cutover.

**Analisi causale CHIUSA** (Claude `/effort max`, sessione `47552e7f`, 9/8).
I due output sono archiviati byte-identici in
`internal/tools/request_analysis_lab/candidates/v26564/`; gli originali in
`/tmp` sono intatti e i sei artifact congelati, i gate e i verifier non sono
stati toccati. Hash confermati: batch
`9c15cc295e9465d987c2fd6bcb86ed5d66ed7b28a00a160065b3f7e9de90fc04`, evaluation
`c2602051b1b62daf1b93ec9a0693158c7a341cfe175131c09c43845d8b184522`; 20/20
dipendenze fissate intatte, 14/14 hash incrociati coerenti, gate rispettato
(34 casi, 34 POST, 0 retry, 35 tentativi = 1 GET + 34 POST).

**Causa provata.** La condotta è schema → adapter → validator e si ferma al
primo errore: **34/34 hanno superato lo schema JSON**, 33 sono morti
nell'adapter, **uno solo ha raggiunto il validator**. Quindi
`proof_family`+`reference_type` non dice "un solo caso ha problemi di prova":
dice che uno solo è arrivato a farsi controllare, e la semantica di 33 casi
resta **non misurata**. La forma compatta ha tolto `clause_id` e promosso lo
**span sorgente a chiave primaria dell'identità di clausola**: la decodifica
pretende span iniettivo su proiezioni + clausole non supportate e uguaglianza
*esatta* di span fra dipendenza e proiezione, due invarianti **non esprimibili
nello schema JSON** — quindi la decodifica vincolata non può imporli — che
vivono solo nella prosa del prompt e abortiscono prima di ogni validazione.
Un solo campo porta due semantiche incompatibili: identità di clausola e
ancoraggio probatorio. Il gold è rappresentabile (max 1 proiezione per grafo,
31 `supported` + 3 `typed_ambiguity`): non è un limite dell'encoding sul gold,
è il modello che emette ancore semantiche in eccesso.

**Non diagnosticabile**: `expanded_frame` è persistito solo sui casi validi e
`raw_frame_sha256`/`model_content` erano stati rimossi per progetto, quindi
nessun frame grezzo esiste. Restano indeterminati quale coppia di ancore si sia
sovrapposta, se proiezione+proiezione o proiezione+clausola non supportata, se
lo status fosse `supported` o `typed_ambiguity` (escluso solo `unsupported`), e
quale slot/prova abbia prodotto i codici del caso 32.

**È la seconda volta.** Nel live V26.4.1 (§2) venti casi su 34 fallivano solo
su `clause_ids` perché schema e prompt non dichiaravano un invariante preteso
dal validator. V26.5 (§3) ha risposto **togliendo** `clause_id` e derivandolo
dall'ordine degli span: l'errore da locale e parziale (20/34) è diventato
globale e totale (34/34). Derivare un identificatore non elimina il vincolo, lo
sposta dove lo schema non arriva.

**Successore minimo** (nessuna mappatura di superficie, nessuna lista
linguistica, gold invariato): portare l'identità di clausola nella **struttura**
— `clauses[]`, ciascuna con esattamente una `projection` e le proprie
`dependencies[]`, identità = posizione nel vettore, span retrocesso a pura prova.
Regola generalizzabile: **ogni invariante fatale del decodificatore dev'essere o
esprimibile nello schema o retrocesso a non fatale**. In più: diagnosi non
censurante (tutti gli stadi, non solo il primo) e impronta strutturale sui casi
invalidi.

Report e metriche: `metnos_v26564_live_postmortem.md` e `.json`. Sonda offline
`metnos_v26564_postmortem_encoding_probe.py`, tutte le asserzioni verdi, zero
rete e zero modello: dimostra che tre frame schema-validi sono respinti
dall'adapter, e che il successore rende la collisione **schema-invalida**.

**Non eseguire un rerun** — il gate esigeva `output_must_be_absent` e l'output
ora esiste, quindi un nuovo live richiede un gate nuovo.

**V26.5.6.5 — successore clause-owned, ora in STATIC BLOCK indipendente.**
Author checkpoint offline, self-test 42/42, `inference=false`.
Directory `internal/tools/request_analysis_lab/candidates/v26565/`. L'identità
di clausola è passata **nella struttura**: `clauses[]` posizionale, una sola
`projection` per clausola, `dependencies` annidate, span **solo come prova**,
`source_ordinal` sull'appiattimento derivato (per clausola: dipendenze, poi
proiezione). Nessun `clause_id`, `atom_id`, `alternative_id` o `output_index`
viene emesso. Schema e prompt sono **generati dal registro tipizzato congelato**,
quindi non possono divergere; registro V26.4.1 e validator V26.5.3 sono
**riusati invariati**.

S2 rispettato: l'espansore è **totale** (non solleva mai) e l'insieme fatale
coincide con lo schema, quindi `primary_cardinality`, `orphan_dependency`,
`clause_span_consistency` e `projection_missing` non possono più scattare.
S3: la condotta misura **tutti e tre gli stadi** anche a schema fallito.
S4: ogni record porta un'impronta strutturale **query-free** con il solo bit
`distinct_clause_spans`.

Misure offline: rappresentabilità **34/34** con esattamente la semantica gold;
i due killer di V26.5.6.4 (span collisi, dipendenza con span stretto) ora
passano; doppia proiezione, dipendenza orfana e clausola senza proiezione sono
**schema-invalide**; 12/12 mutazioni semantiche respinte e tutte misurate su 3
stadi; 9 scritture Unicode danno **una sola impronta**; contaminazione 0 query
e 0 4-gram; riservatezza dell'impronta e fail-closed verdi.

Limiti dichiarati: registro **Phase-1 a 10 relazioni**, **non certifica i 109**;
42/42 offline non è accuratezza semantica del modello. Nessun live, nessuna
rete, nessun gate creato o consumato, V26.5.6.4 e gold intatti.

**Review indipendente offline: STATIC BLOCK** (9/8, Claude, sessione di
background). Riprodotti 42/42, risultato d'autore byte-identico, 9/9 artifact,
5/5 dipendenze, 10/10 righe di hash, zero `.pyc`; il residuo `__pycache__`
segnalato dal README d'autore non esiste più. **La correzione S1 regge**: su
4.000 frame schema-validi generati a caso `primary_cardinality`,
`orphan_dependency`, `clause_span_consistency` e `projection_missing` non si
accendono mai. Confermati archi fra clausole, Unicode su 9 scritture ×
NFC/NFD/NFKC/NFKD, contaminazione 0 su prompt **e** schema (0 3-gram, 0 4-gram),
53 letterali di schema tutti registro-derivati o strutturali, e
`DERIVED_REFERENCE_BRIDGE` presente solo nel self-test.

Quattro blocker, tutti chiudibili offline senza toccare prompt, schema,
registro, validator o gold:

1. **l'espansore non è totale.** Cinque input scalari al posto di un
   contenitore (`clauses`, `dependencies`, `alternatives`, un'alternativa) fanno
   sollevare `expand_frame` e `structural_fingerprint`; `evaluate` protegge lo
   stadio 3 con `try/except` ma **non** lo stadio 2, quindi l'eccezione esce e
   il caso non viene registrato su nessuno dei tre stadi: è il guasto
   V26.5.6.4, nel ramo che esiste apposta per i frame che lo schema respinge.
2. **l'impronta S4 è query-free solo sui frame schema-validi.** Su un frame
   schema-invalido copia verbatim `status`, `relations`, `clause_roles`,
   `speech_acts`, `binding_kinds` e `proof_kinds`; il test di riservatezza non
   lo vede perché costruisce ogni frame sondato con `build_frame_from_template`.
3. **`typed_ambiguity` con clausole fuori registro.** L'espansore proietta solo
   quelle della prima alternativa e il validator le somma al footprint di ogni
   alternativa: frame schema-validi finiscono su `clause_ids` — il codice che
   uccise V26.4.1 — più `alternative_coverage` e `clause_limit`, e le clausole
   fuori registro delle alternative successive spariscono in silenzio. Nessuno
   dei 42 test copre questa forma.
4. **`max_atoms_per_analysis` non è imposto** dallo schema di risposta (tetto
   reale 8 × 16 = 128 contro il limite 16) e `validate_frame` ritorna subito
   dopo il proprio schema interno: 24 atomi danno `validator: ["schema"]`, zero
   codici semantici, mentre `stages_measured` continua a dire 3.

Rilievi non bloccanti: `source_ordinal` senza `maximum` (i due codici più
frequenti in assoluto sul campione casuale); il prompt dice «across the whole
analysis» mentre l'appiattimento riparte da 1 in ogni alternativa; l'insieme
«fatale» dichiarato è più stretto del reale (`clause_source_order` 1.478 e
`projection_source_order` 1.057 sono prosa); la validità strutturale non è un
oracolo semantico (1.934 mutazioni indipendenti: 254 restano valide, 53
cambiano la semantica gold); due check di `materialisation` sono condizionati
all'esistenza del file; un disgiunto morto in `no_emitted_identity`.

Report, sonda deterministica e risultato:
`internal/tools/request_analysis_lab/candidates/v26565/metnos_v26565_independent_static_review.{md,json}`
e `metnos_v26565_independent_static_review_probe.py`.

**V26.5.6.6 — chiude B1–B4 e N1–N6, author checkpoint offline (9/8, Claude
`/effort max`).** Self-test **71/71**, `inference=false`, freeze
`a2d88bc18eddf4e766b42a2c5cba41e1fe2e3d37a5038ae89f4eb3eb4c1f1f3e`, directory
`internal/tools/request_analysis_lab/candidates/v26566/`. I quattro blocker
sono stati **riprodotti in proprio prima di correggerli**, e la chiusura è
verificata anche fuori dal self-test con le riproduzioni della review.
V26.5.6.5, V26.5.6.4, registro, validator, fixture, overlay e gold verificati
**intatti** per hash prima e dopo; zero rete, zero modello, nessun gate.

- **B1**: ogni lettura di contenitore passa da `_as_list`/`_as_dict`, e tutti e
  tre gli stadi più l'impronta sono protetti in modo simmetrico. **2.343
  sostituzioni di tipo** in ogni posizione di cinque frame base, più i nove
  input scalari della review: **0 eccezioni**, 9/9 misurati su tre stadi.
- **B2**: l'impronta filtra ogni stringa contro il vocabolario tecnico chiuso
  derivato dal registro e **conta** ciò che scarta. Marcatore in sei campi di
  un frame schema-invalido: **0 occorrenze** (erano 7); marcatore piantato in
  ogni posizione del corpus di fuzz: mai in un'impronta.
- **B3**, strutturale: `typed_ambiguity` non ripete più un array di clausole
  per alternativa. Porta **un solo scheletro**, e ogni clausola proiettata
  porta una `readings` — una lettura per alternativa; il ruolo di clausola vive
  nella lettura, perché il gold ha un caso in cui le due alternative
  differiscono proprio nel ruolo e nell'atto linguistico. Due alternative non
  possono più divergere su quante clausole esistono, su quali span hanno o su
  quali sono fuori registro: `clause_ids`, `alternative_coverage`,
  `clause_limit` e `alternative_order` diventano **irraggiungibili** da un
  frame schema-valido. Le tre forme che la review usava sono ora
  schema-invalide; lo scheletro condiviso con una clausola fuori registro a
  ogni indice è valido con zero codici.
- **B4**, in due metà. Il tetto agli atomi **non** è esprimibile nello schema:
  è una somma su tutte le clausole, e l'unico tetto per-clausola che la
  garantirebbe (8 × (1+D) ≤ 16, cioè D ≤ 1) renderebbe irrappresentabile una
  clausola con due produttori che il validator accetta — sarebbe di nuovo il
  guasto V26.5.6.4. Si chiude quindi la **censura**: l'espansore nomina il
  budget (`derived_atom_count_over_frozen_bound`) e la condotta rimisura la
  semantica rieseguendo le funzioni congelate del validator, a sezioni
  indipendenti. Sul frame da 24 atomi la semantica è misurata comunque
  (`output_unconsumed`, `proof_limit`); sui 781 mutanti del fuzz le valutazioni
  non misurate sono **0**; e la replica è **fedele**, non più debole
  dell'originale: su **1.500** frame casuali schema-validi coincide
  esattamente con `validate_frame`.
- **N1–N6** chiusi: `maximum` derivato sull'ordinale; prompt esplicito sul
  flattening per alternativa; claim del fatal set ristretto e **misurato**;
  oracolo semantico nella mutation suite (**2.064** mutazioni sistematiche, 100
  sopravvivono strutturalmente, **nessuna** con la semantica gold);
  materialisation incondizionata; disgiunto morto rimosso.

Residuo dichiarato: `clause_source_order` (507 accensioni su 1.500 frame
casuali schema-validi) resta una regola di sola prosa del validator congelato.
Il fatal set coincide con lo schema per gli **undici** codici elencati in
`STRUCTURALLY_CLOSED_CODES`, non in generale. Il registro resta Phase-1 a 10
relazioni e 71/71 offline non è accuratezza semantica.

**Decisione dell'utente, 9/8 ore 23:40: la review indipendente di V26.5.6.6 è
SALTATA.** Va detto per quello che è: nessun terzo ha provato a falsificare le
affermazioni del bundle, quindi V26.5.6.6 **non ha** uno STATIC PASS
indipendente e non va scritto da nessuna parte che ce l'abbia. Le due volte
precedenti la review indipendente ha trovato difetti veri che l'autore non
vedeva (V26.5.6.5: quattro; V26.5.6.4: nessuno, e infatti il live è comunque
fallito per una causa che nessuno dei due aveva cercato). Il rischio resta
dichiarato e a carico di chi decide il live.

**V26.5.6.7 — runner K1/34 e valutatore offline sul contratto clause-owned,
author checkpoint offline (10/8).** Self-test **92/92** (66 ereditati + 26
nuovi), `inference_allowed=false`, freeze
`a378742ff0ec7efa77dea0ca274c3b49e4ab3175459e8e79ffe1d0e9f9f67b23`, directory
`internal/tools/request_analysis_lab/candidates/v26567/`. Zero rete, zero
modello, **nessun gate creato o consumato**; i due gate operativi non esistono,
quindi il live è impossibile per costruzione. V26566, V26565, V26564, registro,
validator, fixture, overlay e gold verificati **intatti** per hash.

Non è una riscrittura: i byte di trasporto, macchina dei gate, ancoraggio
dell'output, budget di codifica e contratto d'errore della CLI sono quelli
STATIC PASS di V26.5.6.4, toccati solo dove il contratto di richiesta li tocca
(predecessore `6fb5785d…b146a`, questo runner `f840184f…5d93`).

- **Contratto ripinnato** su V26.5.6.6: prompt, schema, espansore e condotta per
  SHA. Lo schema **derivato dal registro deve essere uguale al materializzato**,
  e la verifica avviene PRIMA di qualunque trasporto: un contratto rotto costa
  zero chiamate al modello, non 34.
- **Impronta su ogni record** (S4), inclusi il caso non-JSON e l'eventuale caso
  di trasporto fallito, che ricevono l'impronta «senza frame».
- **Tre stadi su ogni record** (S3), con `validator_schema_censored`,
  `validator_semantic_measured` e i codici semantici.
- **Espansione persistita per ogni frame schema-valido**, non solo per i validi:
  è il punto cieco che rese indiagnosticabile il live V26.5.6.4. Resta
  query-free per costruzione — in un frame schema-valido ogni posizione di
  stringa è un enum o una const — e il self-test lo verifica contro un
  vocabolario **derivato**, non scritto a mano.
- **Percorsi d'errore dello schema sanificati**: le chiavi le sceglie il
  modello, quindi solo indici e nomi dichiarati dal contratto sopravvivono; il
  resto diventa `?` ed è contato. Era un secondo canale di perdita, chiuso.
- Misure del batch finto: 34 record, **1 GET + 34 POST = 35 aperture**, zero
  retry, **0 letture** gold-side (audit hook), marcatore ostile piantato in
  valori, chiavi inventate e prove: **0 occorrenze** nel batch.

Due cose dette com'è giusto dirle. Primo: il **worker sigillato V26.5.5 non è
usato** — esegue l'adapter compatto V26.5, che non parla questo contratto, e
pinna il manifest nei propri byte congelati; ricostruirlo avrebbe creato nuova
superficie di sicurezza non revisionata. La proprietà è conservata altrimenti:
il runner **non esegue mai dati del modello**, i soli `compile/exec` caricano
byte pinnati per SHA. Secondo: il self-test eredita da **V26.5.6.3**, non da
V26.5.6.4, perché la suite di quest'ultimo asserisce l'assenza dei gate
all'author-time e il suo stesso live li ha creati e consumati: non può più
passare. I controlli runner-side di V26.5.6.4 sono reimplementati qui.

**Il valutatore offline è nello stesso bundle.** Comando separato: valida il
batch **per intero prima** di aprire qualunque artefatto gold, poi delega il
punteggio allo scorer Phase-1 congelato, riusato invariato. Oltre ai controlli
ereditati verifica, prima del gold: l'impronta di ogni record e che ogni sua
stringa sia una denotazione chiusa (vocabolario **derivato**); che un record
schema-valido porti la sua espansione e che **i codici del validator siano
riproducibili** — li ricalcola e li confronta, quindi un batch non può mentire
sui propri esiti; che nessuna espansione sia persistita per un frame
schema-invalido. Misura: **9 batch falsificati** (stato forgiato, impronta
rimossa o alterata, testo libero nell'impronta, codici forgiati, espansione dove
non deve stare, stadi e contatori forgiati, query piantata in un codice) sono
**tutti respinti con 0 letture gold**.

**Review d'autore, NON indipendente (10/8).** Roberto: «al momento non
disponibile agente indipendente, fai tu review». Fatta, e dichiarata per quello
che è in ogni file: `metnos_v26567_author_static_review.{md,json}` + sonda
deterministica. **Non vale come STATIC PASS indipendente e non va citata come
tale in nessun gate.** Ha trovato **due difetti veri**, entrambi chiusi:
i codici semantici dietro un validator censurato non erano ricalcolati;
l'impronta non era mai confrontata con l'espansione — la stessa categoria del
digest del corpo di risposta che V26.5.6.3 aveva tolto.

Sotto il primo c'era il difetto vero: per un frame **schema-invalido**
l'espansione non si salva (query-freedom), quindi quei codici **nessuno** può
ri-derivarli. Ora ogni record dichiara `semantic_codes_reproducible` e il
valutatore impone la dichiarazione (vera → ricalcola; falsa → niente espansione
e conteggio esposto; incoerente → respinta prima del gold). Confine di fiducia:
**chi rivendica un successo porta prove ri-derivabili, chi dichiara un
fallimento porta osservazioni e lo scrive.** Sonda dopo le correzioni: 10 prove,
0 difetti residui. Self-test del bundle: 92/92 invariato.

**Gate preflight: creato e verificato, NON consumato (10/8).** Il nodo
dell'autorità è stato sciolto da Roberto: «autorizzo, considera STATIC_PASS come
passato». Quindi l'autorità del gate è **umana e scritta come tale** —
`verdict_authority: "user"`, `independent_review_performed: false`, stringa di
autorità `user_authorized_v26567_preflight_gate_verifier`, mai «independent».

Il gate autorizza **una** cosa: `GET /v1/models` su `http://127.0.0.1:8080`,
corpo 0 byte, 1 tentativo, 0 inferenze, output
`/tmp/metnos_v26567_transport_preflight.json` che deve essere assente e non si
sovrascrive. Il verificatore offline deriva da sé i byte esatti dei tre
artefatti, li confronta e poi prova a romperli: **75 mutazioni** (25 sul gate,
respinte anche dal runner; 27 sull'autorizzazione; 23 sulla verifica),
**0 fallite**, rete 0, chiamate al server 0. Gate
`053f97743856440f301efb5a7d8235cff38c0dc8a96430feb6270b44e93081c9`; verificatore
`e39ae6cc4e89466b134a74cab5589e3fdf21316f9cf9d82b4216e5c4ca47efd2`.

Conseguenza dichiarata, identica a V26.5.6.4: da quando il gate esiste il
self-test del bundle **non passa più in loco** (asserisce l'assenza dei gate).
Il risultato archiviato 92/92 è l'ultimo stato in cui quell'asserzione era vera.

**Preflight ESEGUITO il 10/8, esito PASS.** Un solo socket, HTTP 200, 1
documento JSON decodificato, **0 chiamate di inferenza**, corpo 0 byte, latenza
10.4 ms. Output `/tmp/metnos_v26567_transport_preflight.json`,
1632 byte, sha256 `67f7c284615967f38ed39e9920c961da9636061a8206ce17f3588c936dd6cf72`. Il gate preflight è ora **esaurito**
(esigeva `output_must_be_absent`). È stata la prima e finora unica azione di
rete della catena.

**LIVE K1/34 ESEGUITO il 10/8. Trasporto perfetto, 0 validi — ma per la prima
volta si sa perche'.** 34/34 richieste, **zero retry**, p50 7,94 s, p95 15,21 s,
max 16,44 s. Batch
`9fc453afafc6bef26b40daf01b5ea4c7dec28d4f9fbc276d1980d02985f33055`,
valutazione `5d5eb819f7dc695daa1516b5e44491b50d4bf9743fe0fcea425d8cab2b337b74`,
entrambi archiviati in `candidates/v26567/`.

**Il confronto che conta, contro il live V26.5.6.4:**

| | V26.5.6.4 | V26.5.6.7 |
|---|---:|---:|
| superano lo schema | 34/34 | 34/34 |
| sopravvivono all'espansione | **1/34** | **34/34** |
| raggiungono il validator | **1/34** | **34/34** |
| semantica misurata | **1 caso** | **34 casi** |
| validi | 0/34 | 0/34 |

La codifica **non e' piu' il collo di bottiglia**: l'identita' di clausola nella
struttura regge sul vivo, l'espansore non ha prodotto un solo codice, e ogni
caso e' arrivato a farsi controllare. Il fallimento e' ora **semantico e
completamente misurato**, che era esattamente lo scopo di V26.5.6.5-6-7.

**Cosa sbaglia il modello** (record per codice, su 34):

| codice | record | dove |
|---|---:|---|
| `proof_family` | 34 | 72 errori su 80 sono su `clause_role_proof`: il modello usa `explicit_segment` dove il contratto ammette solo `discourse_structure`/`clause_boundary` |
| `reference_type` | 13 | riferimento incompatibile con lo slot |
| `relation_arity` | 13 | numero di argomenti diverso dalla firma congelata |
| `proof_span` | 3 | prova esplicita fuori dalla clausola |
| `queryable_unknown` | 2, `role_speech` 1, `action_unknown` 1 | |

Dieci casi falliscono **solo** su `proof_family`. Ma un controfattuale
diagnostico — sostituendo la sola prova di `clause_role` con
`discourse_structure`, **nessun credito, non e' accuratezza** — porta a
**1/34 validi**: restano `reference_type` e `relation_arity`. Quindi la lezione
e': non basta ritoccare una riga del contratto di prova; il modello sbaglia
**tipi di riferimento e arieta'**, cioe' contenuto, non forma.

**V26.5.6.8 — contratto tipizzato per relazione (10/8, scelto e fatto).**
Roberto: «scegli la strada piu' sicura, definitiva e robusta». L'ha scelta il
dato, non il gusto: il live e' stato un esperimento controllato involontario, e
dice che cio' che sta nello schema il modello lo azzecca e cio' che sta nella
prosa lo sbaglia. Quindi si sposta nello schema tutto cio' che il registro
congelato gia' determina. Directory `candidates/v26568/`, self-test **22/22**
piu' la suite V26.5.6.6 eseguita e verde, freeze
`4fb54c3ecc89bafdc8748d1d056f9ee45f64ca0f7b2d8310f46367ec500d3ac3`.

Numero di argomenti fissato per relazione con `prefixItems`; ogni posizione
tipizzata da sola coi soli riferimenti che lo slot accetta; ogni prova con la
famiglia chiusa che il registro ammette per quella affermazione; output di
dipendenza nello slot dichiarato e in nessun altro; ruolo e atto linguistico
concordi per costruzione. Nessun vocabolario nuovo: ogni enum viene dal
registro. La **forma** del documento e' identica a V26.5.6.6, quindi espansore,
validator e condotta sono riusati invariati.

| | prima | ora |
|---|---:|---:|
| codici irraggiungibili per costruzione | 11 | **20** |
| schema di risposta | 7.650 B | 76.469 B, 97 definizioni |
| gold rappresentabili con semantica esatta | 34/34 | **34/34** |
| famiglie di errore del live ancora esprimibili | 6 | **0** |

Su 1.200 frame casuali schema-validi nessuno dei 20 codici chiusi si accende.
Restano possibili solo quelli che una grammatica non puo' esprimere: conteggio
degli unknown, ordine di sorgente, contenimento delle prove, budget atomi,
direzione degli archi.

**Rischio residuo dichiarato**: lo schema e' dieci volte piu' grande e usa
`prefixItems`; la conversione schema->grammatica avviene nel server e la si
esercita solo al live. Se non la regge, fallisce rumorosamente sul primo caso.

**V26.5.6.9 — runner sul contratto tipizzato, LIVE ESEGUITO il 10/8. Il rischio
grammatica NON si e' avverato, ma e' emerso un artefatto di serializzazione
mio.** Self-test 92/92, gate preflight ed esterno nuovi verificati (75 e 27
mutazioni, 0 fallite), preflight PASS, live 34/34 trasportati, zero retry, p50
11,7 s, p95 18,2 s.

- **34/34 schema-validi**: la decodifica vincolata regge uno schema di 76 KB con
  `prefixItems`. Il rischio dichiarato in V26.5.6.8 e' chiuso.
- Ma **0/34 validi**, e per ragioni nuove: `edge_source_ordinal_not_backward`
  34/34, `action_unknown` 33, `action_speech` 30.
- **Il dato che conta**: la relazione scelta. Con lo schema largo (V26.5.6.7) il
  modello azzeccava la relazione gold **34/34** (insieme esatto 24/34): capiva
  la richiesta e sbagliava solo la contabilita'. Con lo schema tipizzato scende
  a **1/34**, scegliendo `acl.share` in 31 casi su 34.

**Causa individuata, ed e' un difetto mio, non del modello ne' del disegno.**
Nello schema materializzato le proprieta' sono serializzate con `sort_keys`,
quindi `arguments` precede `relation`. Il generatore di grammatica emette le
proprieta' nell'ordine in cui compaiono, quindi il modello deve scrivere la
lista di argomenti PRIMA di scegliere la relazione: con i rami per-relazione si
impegna alla cieca e collassa sul ramo piu' corto. Con lo schema largo il
problema non esisteva perche' il binding era unico per tutte le relazioni.

**Correzione, non ancora provata**: materializzare lo schema senza riordinare le
chiavi, mettendo `relation` (e in generale il discriminante) come prima
proprieta' di ogni ramo. E' una modifica di minuti; richiede pero' un nuovo giro
di gate e un nuovo live per essere confermata.

**V26.5.7.0/V26.5.7.1 — correzione dell'ordine delle chiavi, live del 10/8:
l'ipotesi e' CONFERMATA e il conto e' la latenza.** Schema materializzato in
ordine di generazione (discriminante per primo) e corpo di richiesta non piu'
riordinato. Self-test 24/24 e 92/92, gate nuovi verificati, preflight PASS.

Live: **interrotto al caso 16 su 34** per timeout di trasporto (120 s). Sui 16
casi eseguiti:

| | V26.5.6.7 piatto | V26.5.6.9 tipizzato, chiavi ordinate | V26.5.7.1 tipizzato, chiavi corrette |
|---|---|---|---|
| relazione scelta | giusta 34/34 | `acl.share` 31/34 | `spatial.located_at` 10, `identity.same_as` 4 |
| edge non all'indietro | 0 | 34/34 | **2** |
| frame validi | 0/34 | 0/34 | **1/16** |
| famiglie di errore | 7 | 8 | **3** |
| p50 | 7,9 s | 11,7 s | **47 s** |

La correzione ha rimesso a posto la scelta semantica e ha ridotto le famiglie di
errore da sette a tre: restano `dependency_type` 8, `queryable_unknown` 6,
`output_unconsumed` 6 — cioe' esattamente le regole di conteggio e di
compatibilita' fra atomi che **una grammatica non puo' esprimere**. E' comparso
il primo frame valido della linea.

**Il conto e' la latenza**: p50 da 7,9 s a **47 s**, massimo 120 s. Vincolare un
oggetto grande con un albero di rami per-relazione costa. Il compromesso ora e'
misurato: **precisione del contratto contro costo della decodifica**.

**Prossima leva, misurabile**: ridurre il costo della grammatica tenendo il
guadagno. Le candidate sono, in ordine di rapporto valore/costo: (a) tenere
arieta' e famiglie di prova per relazione ma togliere l'enum di riferimenti
per-posizione, che e' il moltiplicatore di rami; (b) appiattire i rami di
`clause_role` su un enum unico con le prove separate; (c) misurare quanto
ciascuna leva sposta p50 su un solo caso prima di spendere un altro K1/34.

**Copertura, misurata sui log reali (10/8)**: 5.403 turni, **770 query uniche**
in 60 giorni; il runtime invoca realmente **99 tool, 25 verbi, 49 oggetti**, con
2,4 passi per turno e punte di 18. Il registro Phase-1 ne modella **10
relazioni**. La distanza dal sostituire il codice attuale non e' di rifinitura.

**Prossimo passo: nuovo K1/34 con questo contratto** — servono preflight nuovo
e gate esterno nuovo (entrambi i precedenti sono esauriti), piu' un runner
ripinnato su V26.5.6.8. La domanda a cui risponde e' binaria: **la decodifica
vincolata produce frame validi?** Se si', per la prima volta si misurera'
accuratezza vera. Se il modello sbaglia comunque dentro i vincoli, il limite e'
il modello e questa linea si chiude.

**Shadow V23lite+V24.1 e analisi del prompt (10/8, ore 14:30).** Dettaglio in
`internal/design/analysis_v23lite_prompt_10_8_2026.md`.

- **Catena completa 36/40 = 90%** su 40 query vere (corpus 754 uniche, seme
  20260810, budget 1.200). Clausola singola **32/32**, multi-clausola **4/8**.
  p50 3.966 ms, p95 13.783 ms. Token: ingresso p50 5.502, uscita p50 283.
- **I quattro fallimenti sono tutti troncamenti** a 1.200 token, non errori di
  analisi: il taglio del budget compra il 59% della coda e paga il 50% delle
  multi-clausola. Serve un budget per lunghezza di query, non uno fisso.
- **Prompt: ridondanza totale.** 11 concetti su 11 ripetuti in piu' blocchi,
  cinque in cinque blocchi. L'ontologia dei verbi e' il 31% del prompt e
  contiene sei parole italiane in un prompt language-independent.
- **La riduzione l'ho scritta e provata: −70% sui raffinamenti, −41% sul prompt,
  −36% di token in ingresso. Risultato: 33/40 (era 36/40) e p50 5.117 ms
  (era 3.966).** Su questo modello la ripetizione non e' ridondanza, e'
  rinforzo: toglierla costa ~7 punti e non guadagna un millisecondo.
- **Perche'**: a parita' di ingresso, 258 token di uscita in piu' costano
  3.466 ms, cioe' **13,4 ms per token generato**. L'ingresso e' quasi gratis
  (prefisso riusato dal server), la generazione e' tutto il costo. **La leva e'
  l'uscita, non il prompt.**
- **Tagli proposti sull'uscita**, a rischio basso o medio: `predicate_id`
  derivato dalla posizione, ancora emessa una volta sola invece che due, nomi
  di campo brevi, default omessi. Somma ~150 token = **~2,0 s**: V23lite
  passerebbe da 3,97 s a ~2,0 s. Il taglio del prompt va **scartato**.

**Da tenere presente, e non e' un dettaglio**: l'obiettivo del refactor —
soglia 108/109 adjudicato — **e' gia' raggiunto da V24.1** (107/109 strict,
108/109 adjudicato), che non e' integrata nel runtime. La strada piu' corta per
chiudere il refactor non passa dal V26: passa dall'integrare V24.1. Il V26 e'
una scommessa su un contratto migliore, non il percorso critico.

Strade alternative valutate e non scelte:
(a) lavorare sul prompt del contratto di prova e sulle firme di relazione, poi
un nuovo K1/34 (serve un nuovo giro di gate); (b) portare il contratto a un
registro catalog-derived generale prima di insistere sulla Phase-1; (c)
accettare che il modello locale non regga questo contratto e valutare un tier
piu' alto. La (a) da sola non basta: lo dice il controfattuale.

Storia precedente, ora chiusa:

1. ~~runner V26.5.6.6~~ **fatto** (V26.5.6.7);
2. ~~valutatore offline separato~~ **fatto** (V26.5.6.7);
3. ~~gate preflight nuovo + verifier~~ **fatto**, non consumato;
4. ~~eseguire il preflight autorizzato~~ **fatto**, PASS, gate esaurito;
5. gate esterno K1/34 nuovo + verifier;
6. solo allora il live (34 POST reali al modello), e la valutazione offline
   separata.

Il passo 6 è l'unico che chiama il modello: 34 richieste, una per caso, zero
retry. Il gate esterno che lo autorizza va costruito e verificato prima, e si
esaurisce con l'esecuzione.

**Checkpoint operativo 9/8.** La sessione Claude `191d9928` era stata fermata e
il timer delle 23:00 **cancellato**; unità, wrapper e prompt temporanei non
esistono più. Il gate «successore che chiude B1–B4» è **chiuso** da V26.5.6.6 e
i gate «runner» e «valutatore» sono **chiusi** da V26.5.6.7 (10/8). La review
indipendente di V26.5.6.6 è saltata per decisione di Roberto (sopra). Il
prossimo agente fa **solo** il punto 3 — il gate preflight nuovo — e si ferma
prima di qualunque trasporto. Niente rete, niente modello, nessun freeze o
output storico toccato.

Frase di ripresa per una nuova sessione Claude:
«Leggi integralmente `internal/design/handover_request_analysis_8_8_2026.md`
dal punto 0, il README V26566 e la review indipendente V26565 da cui nasce;
prosegui un solo gate alla volta,
autonomamente e fail-closed, aggiornando questo handover dopo ogni gate; non
avviare live o refactor runtime senza STATIC PASS indipendente e senza il
checkpoint esplicito richiesto prima del cutover.»

Il registro resta Phase-1 (10 relazioni); per la suite generale servono i tre
layer descritti in `internal/tools/request_analysis_lab/catalog_registry_v1/`.

## 1. Obiettivo e vincoli utente

Obiettivo minimo:

- centralizzare filtri, riscritture e trasformazioni query oggi disperse;
- una sola funzione prima o dentro l'intent extraction;
- nessuna lista hardcoded di stopword, suffissi, clitici, sinonimi o verbi
  della lingua sorgente;
- i18n by default.

Obiettivo massimo:

- sostituire la normalizzazione linguistica corrente con una singola analisi
  LLM strutturata, non con riscrittura libera;
- rappresentare lemma/relazioni, ruoli, argomenti, scope, dipendenze e prove;
- supportare query multi-clausola, multi-azione e multi-dominio;
- il codice conserva autorità su catalogo, tipi, safety, binding e validazione.

Target:

- soglia di sostituzione decisa dall'utente il 2026-08-09: **108/109
  adjudicato**, purché non emergano indicatori negativi strutturali, di safety,
  stabilità o regressioni rispetto al legacy;
- **109/109 adjudicato** resta un obiettivo di rifinitura, non un blocco al
  cutover; lo strict legacy va comunque pubblicato separatamente;
- zero invalidità strutturali e stabilità K>=5;
- dopo il 100%: holdout storico nuovo, redatto e indipendente, con multi-azione,
  multi-dominio, anafore, condizioni, negazioni e quote.

Decisione operativa dell'utente: appena terminati con esito positivo i test e
in assenza degli indicatori negativi sopra, procedere senza nuova conferma a
cutover e pulizia del legacy, conservando un rollback. Congelare e pubblicare
prima/dopo almeno: exact e clausole, validità/coverage/leakage, K, retry e
chiamate LLM, p50/p95, test passati, righe/helper/liste linguistiche rimossi.
Dopo il refactor, quando Claude è disponibile, richiedere inoltre una code
review indipendente con `/effort max` prima della rimozione definitiva del
legacy; verificare localmente ogni rilievo e rieseguire le suite interessate.
Prima di iniziare il refactor runtime, e soltanto dopo aver chiuso tutti i test
e gate precedenti, fermarsi per un checkpoint con l'utente: stato numerico,
rischi residui, diff previsto, rollback e risorse/sessione ancora disponibili.
Non iniziare il refactor prima di questo checkpoint.

Non dichiarare successo sulla sola suite 34 o su un rescore post-hoc.

## 2. Numeri che contano

| Sistema | Exact | Note |
|---|---:|---|
| baseline legacy | 96/109 | 141/159 clausole |
| V23lite raw | 98/109 | 109/109 validi |
| V24.1 su V23lite | 107/109 strict | 108/109 con overlay spreadsheet; zero regressioni |
| V26.4.1 live, suite mirata | 1/34 validi | trasporto riuscito; 33 invalidi |
| V26.5 | solo offline | nessun credito di accuracy live |

V24.1 resta il miglior risultato full-suite. Non è integrato nel runtime.

Nel live V26.4.1:

- 34/34 POST hanno raggiunto il modello; zero retry;
- p50 5,927 s, p95 13,863 s;
- 20 casi avevano come unico errore `clause_ids`;
- prompt/schema non dichiaravano la consecutività richiesta dal validator;
- i frame live non sono stati persistiti, quindi non è lecito correggerli
  post-hoc o attribuire nuova accuracy.

**Non rilanciare V26.4.1.**

## 3. Decisione architetturale corrente

L'LLM non deve riscrivere la frase. Deve emettere un grafo relazionale
compatto e ancorato agli span Unicode.

V26.5 elimina dall'output LLM:

- `clause_id`: derivato dall'ordine degli span;
- `atom_id`: derivato dalla posizione nell'array topologico;
- `output_index`: derivato dalla firma tecnica della relazione sorgente.

Resta `source_ordinal`, perché è informazione semantica: indica quale
risultato precedente alimenta una successiva azione. Due grafi validi possono
essere identici salvo questo collegamento.

Flusso previsto:

```text
query intatta + segmenti Unicode
  -> LLM: grafo relazionale compatto
  -> schema compatto
  -> worker isolato: adapter -> normal form -> validator tecnico/catalogo
  -> projector legacy temporaneo
```

Nessuna seconda chiamata LLM è prevista.

## 4. Core e runner correnti: V26.5.5 / V26.5.6.4

Directory: `internal/tools/request_analysis_lab/candidates/v2655/`, `v2656/`,
`v26561/`, `v26562/`, `v26563/` e `v26564/`.

V26.5.3/V26.5.4 restano STATIC BLOCK e immutati. V26.5.5 conserva worker,
manifest e byte semantici V26.5.4, modificando soltanto la facciata:

- facciata con sola API `evaluate(original_request, frame)` e snapshot JSON;
- worker non importabile, protocollo stdin/stdout chiuso, una richiesta/exit;
- worker verificato no-follow/hash/TOCTOU, eseguito da `memfd` sigillato con
  `/usr/bin/python3 -I -B` e ambiente minimo;
- nessun source/path/entry/injection attraversa il protocollo;
- unico `compile/exec` lessicalmente locale per adapter/validator pin-nati;
- schema prima del compile, registry assegnato da byte verificati;
- AST e audit hook chiudono reflection, rete, subprocess, shell, ctypes,
  `os.exec` e fork nel worker.
- snapshot conta esattamente i byte JSON/UTF-8 prima del dump, incluse chiavi,
  escape, separatori, envelope e query originale.

Author self-test: V26.5.4 **24/24**, mutation **106/106**, contamination
**15/15**, candidato **6/6** positivo + **16/16** negativo. Multi-azione,
multi-dominio, ambiguità e mixed coverage inclusi. Accetta esattamente
1.500.000 byte e rifiuta 1.500.001 prima del dump; picco aggiuntivo 16.740 B.
Overhead p50 archiviato 88,832 ms; replay root 85,305 ms.

Review indipendente **STATIC PASS**: 1.004 envelope differenziali e 10.011
float finiti coincidono con l'encoder; boundary 1.500.000/1.500.001 e chiavi
con escape falliscono prima del dump con memoria aggiuntiva limitata. Gli
alias sono contati per occorrenza; surrogati, non-finiti, cicli e subclass
sono fail-closed. Report MD/JSON
in `candidates/v2655/`; nessuna autorizzazione live.

Hash: facade `fc34ffef...8d63`, selftest `91bb542d...8426`, result
`8bfcb165...ab34`; worker `c57c8ece...0e22`, manifest `88d7b009...2391`.
Ambiente Python/interprete/package tree restano trust root da pin-nare nel
futuro freeze. Nessuna rete, modello, freeze o gate.

V26.5.6 collega questi byte a un runner compatto K1/34 e pinna interprete e
dipendenze osservate. Il live runner non legge mai il gold e salva prima un
batch query-free; la valutazione è un comando offline separato. Self-test
autore 21/21 e replay indipendente 21/21; freeze `d17cddcb...13f5`. La review
infrastrutturale è **STATIC BLOCK**: manca il boundary CLI e un producer
preflight, `urlopen` conserva proxy/redirect con contatori non fedeli, il frame
non è validato integralmente prima del gold e il writer runner non ancora il
parent a un dirfd no-follow. Il batch corrente è staticamente limitato sotto
7.609.728 byte serializzati. External gate assente, nessuna accuracy live.

V26.5.6.1 corregge offline quei cinque blocker senza cambiare prompt,
schema, registro, adapter, facade, worker o manifest: catch CLI sanitizzato;
producer `--preflight` atomico dietro gate preliminare indipendente; shape
esatte e `validate_frame` su ogni expanded frame prima del primo gold read;
opener senza proxy/redirect con loopback IP letterale e aperture contate;
parent output ancorato con openat/O_NOFOLLOW prima del trasporto e dirfd tenuto
fino a link/fsync. Cap batch 7.609.728 byte prima della codifica. Self-test 21
ereditati + 13 nuovi = 34 PASS, inclusi valid/model-JSON-invalid/schema-invalid
e mutation `expanded_frame={}` con zero gold reads. Freeze
`f39140cc94e328e465e60e4c297ea8a77f4af57d810ce65c54b43693599ae316`;
preflight gate ed external gate assenti, rete/modello zero. La review
indipendente è **STATIC BLOCK**: il checkpoint impone assenti i gate che i
verifier operativi devono leggere; mismatch diagnostica/preflight/summary
raggiungono gold; l'evaluator non impone interpreter/dependency context prima
del validator exec.

V26.5.6.2 separa correttamente la verifica immutabile dei byte dalla policy
author-time di assenza gate e impone all'evaluator 3.12.3/-I/-B/Unicode 15 con
membership completa dei tree prima di compile/exec. La review indipendente è
però **STATIC BLOCK**: la closure pre-gold non tipizza esattamente ogni replica
e non deriva tutte le prove; otto mutazioni JSON aprono `source_controls34` e
terminano `PHASE1_EVALUATED`. Self-test 34 ereditati + 16 nuovi = 50 PASS. Freeze
`da2ccdf5f04f22916fec8bd066cad5f01ba2fccb005cb21437767b209b2a5e88`;
gate assenti, rete/modello zero.

V26.5.6.3 chiude offline gli otto bypass: tipi scalari diretti, copie dei counter
riconciliate, request-body ricalcolata dai pin, relazioni di latenza coerenti e
endpoint canonicale IP-loopback senza userinfo. Byte/digest del response body
non sono più persistiti né accettati come prova. Self-test 50 ereditati + 16
nuovi = 66 PASS; freeze
`f3a655d1786b651da509c91a4cf0f9679f6e192885d744ffb100486c6bd224cc`.
La review indipendente è **STATIC BLOCK**: quattro booleani annidati mutati in
0/1 aprono gold; porta 0 e osservazioni hash non autorevoli restano da chiudere.
Gate assenti, rete/modello zero.

V26.5.6.4 chiude il report con census ricorsivo exact-type prima del gold,
timestamp nonnegativi, porte `1..65535` e rimozione di
`raw_frame_sha256`/persisted `model_content`. Il self-test muta 148 path
scalari normalizzati; 66 test ereditati + 19 nuovi = 85 PASS. Freeze
`d41a440551af5bfc430000e12bcdda2cbc89ca8c601b137d74b6e57f47f6cd56`.
La review indipendente è **STATIC PASS**: 8/8 byte semantici, 594 sostituzioni
exact-type e 45 mutazioni branch/container respinte prima del GOLD, request
proof 34/34. Al momento della review i gate erano assenti; rete/modello zero e
nessun live autorizzato.

Il successivo gate preflight è distinto dai byte author ed è stato verificato
offline 75/75 senza rete: autorizza solo `/usr/bin/python3 -I -B`, un GET
`http://127.0.0.1:8080/v1/models`, body 0, tentativo 1, POST/inference 0 e
output no-clobber `/tmp/metnos_v26564_transport_preflight.json`. Gate SHA
`dd27373e...a5ad`; verifier `de70f161...bbaa`. Il preflight è PASS (SHA
`7e85e2da...735a`, 1.552 B). External gate K1/34 SHA `f23972a2...ea7d`,
verificato 27/27; live non eseguito e output assente.

## 6. Prossime attività, in ordine

1. Preservare i byte frozen V26.5.6.4, i report STATIC PASS e il gate preflight
   verificato ma non consumato.
2. L'unica operazione di trasporto autorizzata è il comando preflight esatto
   nel README V26.5.6.4; verificare prima che l'output sia ancora assente.
3. Valutare separatamente l'eventuale output preflight. Un external gate
   monouso richiede un nuovo artifact e una nuova autorizzazione.
4. K1 sui 34 con valutazione offline separata; poi K5 solo con validità,
   binding, dependency, safety, evidence
   e leakage tutti conformi.
5. Espandere le 10 relazioni in un registro tecnico catalog-derived generale.
6. Full 109 e 70 adversarial: totale esteso 213; pubblicare strict e adjudicato.
7. Holdout storico nuovo con multi-azione/multi-dominio.
8. Solo dopo equivalenza e rollback: shadow runtime, cutover e rimozione legacy.

## 7. Oracle e gold

Suite mirata:

- 34 controlli, 9 positivi e 25 negativi, 8 lingue;
- il vecchio exact a sette campi non è oggettivo;
- oracle tipizzato: 31 proiezioni esatte + 3 ambiguità ammesse;
- metriche separate: direct binding, dependency, coverage, safety, evidence.

Punto di ingresso:

- `internal/tools/request_analysis_lab/oracles/phase1_v1/README.md`.

Gold full:

- non modificare l'originale in place;
- l'unico overlay spreadsheet è shadow/evaluation-only;
- pubblicare sempre strict legacy e adjudicato separatamente;
- gold, overlay e risultati non devono entrare in prompt, runtime o projector.

## 8. Confine runtime e codice impattato

Il runtime di produzione usa ancora il percorso legacy:

- `runtime/agent_runtime.py` chiama `extract_intent`;
- `runtime/intent_extractor.py` produce il contratto legacy;
- altri consumer sono in `runtime/prefilter.py` e
  `runtime/prefilter_strategies/`.

Nessun file V26.x/V25.x è collegato al runtime.

Da rimuovere **solo dopo equivalenza, shadow e rollback**:

- liste linguistiche verb/clitic/object/stop in `runtime/prefilter.py`;
- surface mapping in `runtime/vocab.py`;
- prompt intent IT/EN duplicati;
- split linguistico in `runtime/compound_decomposer.py`;
- repair lessicale in `runtime/engine/dispatch.py`;
- regex/list query-side nei resolver e in `agent_runtime.py`;
- `runtime/nlu.py` nella forma inert corrente.

Da mantenere:

- secret/credential redaction;
- verifica letterale di path, URL, email e host;
- catalog schema, arg types, ownership e connectivity;
- safety, approval, taint e policy recipient/channel;
- dataflow e prerequisite tecnici;
- evidence DOM, selector, visibility e ambiguity gates;
- validazione post-esecuzione.

## 9. Blocker catalogo separato

Il catalogo non è ancora production-ready:

- 96 route;
- 51 manifest allineati, 30 con drift, 15 assenti;
- 11 builtin fuori snapshot;
- 83 campi manual-required;
- 0/96 contratti fail-closed revisionati.

Non usare suggestion automatiche come autorità. Servono metadata tecnici
revisionati per action/object, patient/carrier, output domain, ownership,
intent role e projection rule.

## 10. File da leggere

Ordine minimo:

1. questo handover;
2. `internal/tools/request_analysis_lab/candidates/v26566/README.md` (candidato
   corrente) e, per capire da cosa nasce,
   `internal/tools/request_analysis_lab/candidates/v26565/README.md` con la
   review STATIC BLOCK MD/JSON nella stessa directory;
3. `internal/tools/request_analysis_lab/candidates/v2655/README.md`;
4. review STATIC PASS V26.5.5 MD/JSON in `candidates/v2655/`;
5. review STATIC BLOCK V26.5.4 MD/JSON in `candidates/v2654/`;
6. review STATIC BLOCK V26.5.3 MD/JSON in `candidates/v2653/`;
7. review STATIC BLOCK V26.5.2 MD/JSON in `candidates/v2652/`;
8. `internal/tools/request_analysis_lab/candidates/v265/README.md`;
9. `internal/tools/request_analysis_lab/candidates/README.md`;
10. `internal/tools/request_analysis_lab/oracles/phase1_v1/README.md`;
11. `internal/design/analysis_request_analysis_strutturata_8_8.md`.

Storia/replay dei candidati precedenti:

- `internal/tools/request_analysis_lab/candidates/REPLAY_CHAIN.md`;
- directory `v24_v241/`, `v25*/`, `v262/`, `v263/`, `v264/`,
  `v2641/`.

Usare la storia solo per evidence o replay: non ripartire da un candidato
vecchio e non riusare gate consumati.

## 11. Regole operative

- Fare poche attività per volta e aggiornare questo file dopo ogni checkpoint.
- È autorizzato usare Claude con `/effort max` per analisi, implementazioni
  delimitate o review indipendenti; l'agente principale deve comunque
  verificare personalmente diff, test, hash e conclusioni prima di accettarle.
- Usare `apply_patch` per ogni modifica.
- Preservare il worktree sporco: ogni modifica estranea è dell'utente.
- Non modificare freeze o output storici dopo aver visto i risultati.
- Nessuna lista o trigger della lingua sorgente in codice, prompt o projector.
- Enum/ID/contratti tecnici derivati dal catalogo sono ammessi.
- Nessun wrapper, freeze, gate o run prima di una review indipendente PASS di
  un successore di V26.5.2.
- Nessun rerun di V26.2, V26.3, V26.4 o V26.4.1.
- Non attribuire credito a output non valutabili o correzioni post-hoc.
- Riportare sempre validità, coverage, leakage, retry, grounding, stabilità,
  latenza e regressioni.

## 12. Checkpoint corrente

Data: 2026-08-09.

- miglior full-suite: V24.1, 107/109 strict e 108/109 adjudicato;
- V26.5.1: STATIC BLOCK documentato, byte invariati;
- V26.5.2: STATIC BLOCK documentato, byte invariati;
- V26.5.3: **STATIC BLOCK** nonostante core 115/115 e verifier 49/49; helper
  source/injection ancora caller-controlled e builtins recuperabili;
- V26.5.4: **STATIC BLOCK** nonostante 24/24 gruppi offline, 106/106 e 15/15;
  manca un budget cumulativo JSON/UTF-8 prima di `json.dumps`;
- V26.5.5: **STATIC PASS** sul delta facade; 24/24, 106/106, 15/15 e
  6/6+16/16, boundary esatto e differenziali indipendenti verdi;
- V26.5.6: **STATIC BLOCK** indipendente, self-test 21/21; runner a una
  chiamata/caso, zero retry, batch query-free/accuracy=false e zero aperture
  gold; evaluator offline distinto lega il batch SHA, ma non valida il full
  frame prima di aprire gold. Mancano inoltre catch CLI, producer preflight,
  opener senza proxy/redirect e writer parent dirfd/no-follow. Freeze
  `d17cddcb1f4aae770f9288294c17a2b49cb7321916dfa61bd33ba954cfe813f5`;
  runner `ffb55a27da2167dc1ecb3cf878bf4f949380b81ffff1890dbe7a5f7ee4adf225`;
- V26.5.6.1: **STATIC BLOCK** indipendente nonostante 21 test ereditati + 13
  nuovi = 34 PASS. Mantiene byte-identica la semantica e aggiunge cap batch
  7.609.728 byte, ma i gate operativi sono logicamente irraggiungibili,
  mismatch contatori raggiungono gold e il context exec non è imposto. Freeze
  `f39140cc94e328e465e60e4c297ea8a77f4af57d810ce65c54b43693599ae316`;
  runner `443fd60f41fd664401c30733b9677b9310eded6f02d8a6039ce767eb9bf0cd16`;
- V26.5.6.2: **STATIC BLOCK** indipendente nonostante 34 test ereditati + 16
  nuovi = 50 PASS. Gate split e context/tree sono corretti, ma otto mutazioni
  di tipi, endpoint e prove diagnostiche raggiungono gold; freeze
  `da2ccdf5f04f22916fec8bd066cad5f01ba2fccb005cb21437767b209b2a5e88`,
  runner `fe33701bcb5483b8881fef2bd56428434e626142e6128e3a39d3bdfc6a71832b`;
- V26.5.6.3: **STATIC BLOCK** indipendente nonostante 50 test ereditati + 16
  nuovi = 66 PASS. Gli otto bypass precedenti sono chiusi, ma booleani annidati
  come interi aprono gold; porta 0 e observation-only restano da esplicitare.
  Freeze `f3a655d1786b651da509c91a4cf0f9679f6e192885d744ffb100486c6bd224cc`,
  runner `8571df498a58a9de9cc6703845a156e4d7a0dd679107f753a090f2cb9e509465`;
- V26.5.6.5: successore clause-owned, **STATIC BLOCK** indipendente nonostante
  self-test 42/42 riprodotto, `inference=false`, rappresentabilità 34/34 e
  contaminazione 0/0. La correzione S1 è **confermata** su 4.000 frame
  schema-validi casuali; bloccano espansore non totale con stadio 2 non
  protetto, impronta S4 non query-free sui frame schema-invalidi,
  `typed_ambiguity` con clausole fuori registro che riapre `clause_ids`, e
  `max_atoms_per_analysis` non imposto con censura semantica dello stadio 3.
  Freeze `b7b02367bba88c244dd772b0722f4925292e4e318eca80b5df0b0556f3638f90`
  intatto; nessun live, nessun gate;
- V26.5.6.6: **author checkpoint offline**, self-test 71/71, `inference=false`,
  freeze `a2d88bc18eddf4e766b42a2c5cba41e1fe2e3d37a5038ae89f4eb3eb4c1f1f3e`.
  Chiude B1–B4 e N1–N6 della review V26.5.6.5: espansore totale su 2.343
  sostituzioni di tipo, impronta filtrata dal vocabolario chiuso e con
  conteggio del fuori-vocabolario, `typed_ambiguity` a scheletro condiviso con
  `readings` per alternativa (quattro codici in più diventano irraggiungibili),
  budget atomi nominato e terza fase mai censurante con replica **fedele** del
  validator congelato (1.500/1.500). Nessun live, nessun gate, predecessori e
  gold verificati intatti. **Manca la review indipendente**, saltata per
  decisione dell'utente;
- V26.5.6.7: **author checkpoint offline** di runner K1/34 e valutatore
  offline sul contratto clause-owned, self-test 92/92 (66 ereditati da
  V26.5.6.3 + 26 nuovi), `inference_allowed=false`, freeze
  `a378742ff0ec7efa77dea0ca274c3b49e4ab3175459e8e79ffe1d0e9f9f67b23`, runner
  `83bf70bda35a4c8dd9065f3d516089a3829df1bc080e612b5a04a99bad22a657`,
  valutatore
  `6c02fccb98521b3fa17ee8e8c89a889ebdf5d8861e0164d8ba6c67c96d18a8d7`.
  Il valutatore ricalcola i codici del validator e respinge 9 batch falsificati
  con 0 letture gold. Freeze aggiornato dopo la review d'autore:
  `003b46c54db3d9996788d8ebb8ae803f6cdd490d4b5e8d3e57ad89c87319e606`, runner
  `fd14965510ea13bc7cc8a1000d5e50dabae877c50f11e2325e1e08072ce90553`, valutatore
  `d46918898ac9ba1aecaf75e18f280f0984e9b06ad986e2548347b56ce5cb31ab`. **Review indipendente assente**
  per entrambi i bundle (V26566 saltata dall'utente, V26567 fatta dall'autore
  su sua richiesta). Trasporto
  e gate ereditati byte a byte da V26.5.6.4; contratto ripinnato su V26.5.6.6
  con schema derivato == materializzato verificato prima del trasporto;
  impronta e tre stadi su ogni record; espansione persistita per ogni frame
  schema-valido e query-free per costruzione; percorsi d'errore sanificati.
  Worker sigillato V26.5.5 **non usato**, motivo dichiarato. Nessun gate,
  nessun live, nessuna review indipendente;
- V26.5.6.4: live K1/34 **eseguito e fallito semanticamente, 0/34 validi**;
  34/34 superano lo schema, 33 muoiono nell'adapter, 1 raggiunge il validator;
  causa = span sorgente usato come chiave dell'identità di clausola, invariante
  non esprimibile nello schema; post mortem e sonda archiviati in `v26564/`;
  prima del live: **STATIC PASS** indipendente, 66 ereditati + 19 nuovi = 85 PASS;
  148 path scalari, 594 sostituzioni exact-type e 45 mutazioni end-to-end
  fail-closed prima del GOLD; request proof 34/34. Freeze
  `d41a440551af5bfc430000e12bcdda2cbc89ca8c601b137d74b6e57f47f6cd56`,
  runner `6fb5785d32b73f9d7dad4e917b211fdcdc12d9ca05088cc6d8ed9c4b167b146a`;
- anche un futuro 34/34 proverebbe solo questo Phase-1: prima del full 109 serve
  un successore con registro tecnico catalog-derived a copertura generale;
- zero `.pyc` nel candidato; author freeze invariato, preflight PASS ed
  external gate presente/verificato, live output assente;
- non riutilizzare Claude prima delle 17:20 CEST del 9/8;
- review V26.5.5 MD/JSON archiviata; facade/selftest/result e dipendenze
  V26.5.4 invariati;
- review, preflight PASS ed external gate/verifier V26.5.6.4 archiviati; live
  **eseguito**, output archiviati byte-identici e gate ormai esaurito;
- il gate di accuratezza per il rimpiazzo è già raggiunto da V24.1
  (108/109 adjudicato contro 96/109 legacy); restano da completare il candidato
  eseguibile, la verifica indipendente, la stabilità e l'assenza degli altri
  indicatori negativi prima del cutover.
