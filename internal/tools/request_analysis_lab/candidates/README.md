# Frozen candidate archive

> **Candidato corrente**: V26.5.6.5 in `v26565/`, **STATIC BLOCK**
> indipendente. V26.5.6.4 in `v26564/` era STATIC PASS ma il suo live K1/34 è
> **fallito semanticamente, 0/34 validi**, e il suo gate è esaurito. Nessuno dei
> due autorizza rete, trasporto o cutover: sono Phase-1 K1/34 con registro a 10
> relazioni, non un sostituto del full 109.

Questi file sono snapshot di laboratorio per rendere verificabile l'analisi
anche se `/tmp` viene ripulito. Non sono importati dal runtime.

La ricostruzione completa dei path storici è descritta in
[`REPLAY_CHAIN.md`](REPLAY_CHAIN.md). La catena è
V26.3 → V26.2 → V25.3 → V25 → V24.1/V24, con bench V22 e auditor congelati in
`replay_deps/`. V26.4.1 dipende inoltre dal freeze semantico V26.4.

## `v24_v241/`

Contiene base V24 e delta V24.1: prompt-independent projector, contratti
tecnici frozen, schema, validator, mutation, stabilità e risultati. V24.1
dipende dalla base V24 nella stessa directory; i path `/tmp` nel codice sono
quelli dell'esperimento originale e vanno adattati soltanto in una copia di
lavoro, senza alterare il freeze.

Tutti i file coincidono byte-per-byte con gli originali indicati nel handover,
tranne `metnos_v24_on_v23lite_results.json`: l'originale `/tmp` non aveva LF
finale e l'archivio testuale ne aggiunge uno. SHA originale:
`2757d52eb324f89e6946a95792df0604b8436c098d91bfd8d6f58a00ff07f669`.
Rimuovere soltanto quell'ultimo byte LF ricostruisce l'originale.

## `v25/`

Contiene runner, schema, prompt, fixture, lock e risultato targeted V25. Tutti
i sei file coincidono byte-per-byte con `/tmp`. V25 ha dimostrato route
corrette 16/16, incluso current-location 7/7 multilingue, ma i due claim
opzionali sono rimasti `none`; è un risultato diagnostico, non il candidato
finale.

## `v252/`

Contiene il primo contratto compositivo `desired_observation`, i 34 controlli,
11 mutation, freeze, evaluator corretto per gli outage, audit statico e run
live completo. Risultato live: 30/34 sul binding, 8/9 positivi, 3/25 falsi
positivi, 25/34 frame proiettati, 22 retry. Non passa il gate.

La causa più importante non è una parola mancante: il validator ha trattato il
claim semantico come autorità e il retry ha cambiato route/patient per adeguarli
a un claim errato. Il candidato successivo deve isolare e validare prima
soggetto, tipo di domanda e tempo; non deve mai usare il claim per forzare la
route. Il caso cinese mostra inoltre che la segmentazione whitespace non è
i18n-completa.

## `v251/`

Contiene l'unione `requested_result` con i due ordini di branch e la verifica
indipendente. None-first e active-first hanno prodotto frame identici: 14/16
strict, 15/16 validi e 15/16 route, quindi l'ordine del decoder non era la
causa. Restano un falso `question_slot` su un luogo nominato e un attachment
fail-closed. La mutation indipendente mostra inoltre due vie di fuga: un luogo
può essere emesso come entity esplicita senza question slot e un file held può
evitare support_argument. V25.1 è diagnostico, non by-construction.

## `v262/`

Contiene il freeze finale pre-inferenza di V26.2 minimal Phase 1: una sola
chiamata per caso, zero retry, tupla semantica minima, tre prove tipizzate
obbligatorie, segmentazione Unicode UAX #29 e nessun termine della lingua
sorgente nel contratto. La review indipendente autorizza un solo run nativo
K1 sui 34 controlli; il run è stato eseguito una sola volta e non passa il
gate: 26/34 valutabili, 5/26 tuple esatte, 20/26 binding esatti, 17/26 evidence
complete, 3/7 positivi valutabili e leakage 2/19. Route integration e cutover
runtime restano vietati. Il dettaglio redatto è in
`metnos_v262_minimal_phase1_k1_failure_report.md`.

Tutti i file coincidono byte per byte con gli originali `/tmp`:

| File | SHA-256 |
|---|---|
| `metnos_v262_minimal_phase1_runner.py` | `0b6ab465fc9cfd19ddd2a59c49d2ce0d5ff4e31867ebc8d0f1c2aa8e558b5cb8` |
| `metnos_v262_minimal_phase1.schema.json` | `3a8963414f56c9d239a59ef010e76a330dea53f4838c7bb4fed6060698f4caf0` |
| `metnos_v262_minimal_phase1.prompt.txt` | `dad168f1975a3ab96b3e70303efce939aaab464ba71f567ff9c691d11c862a60` |
| `metnos_v262_minimal_phase1.freeze.json` | `757f461e26cb39083c039d4a4f3871e56e94a3bc30e5c838ff8a1401d5e8ff9d` |
| `metnos_v262_minimal_phase1_controls_frozen.json` | `4e887a1e3cf6f38b20f18e2b39842674ed4ca2a78ede4d93693690e0e62cd427` |
| `metnos_v262_minimal_phase1_mutations_frozen.json` | `e5a8a45f44808e253f963aac4bbcf66ad1b3444b9392a31dc444bd5f4d89846c` |
| `metnos_prompt_contamination_audit.json` | `7340b0fb3fdcb13ee5c3285aa7d4659a91b2ba7028a7ede7b4ad793b594cc49f` |
| `metnos_v262_minimal_phase1_static_audit.md` | `7f443d5f0f50dd8e2650732ff88736a78b5d88885bc7304c57191368dba75aff` |
| `metnos_v262_independent_review.json` | `4cb7134b780ecc2f7eea5b35ec397730fcab6199e94d01733df20a41cd9ce6d7` |
| `metnos_v262_minimal_phase1_preinference_gate.json` | `dfdfafb715e357a773893f62b6576fe27b712f032cb47640cd2f26a652d3d48d` |
| `metnos_v262_minimal_phase1_gate.lock.json` | `c433e6da5f69f83fe49e46596e978767bd6976808f6f010159f7b3a2bb92f29a` |
| `metnos_v262_gate_verifier.py` | `7956f9120cff9e80b98edde3dffd2a3bc7eed70bb2517ffee969c5c9f9aa5cd1` |
| `metnos_v262_minimal_phase1_controls_k1.json` | `ae8020a29ade3de48922d64cc61fe5f5bf9349fa3defda6643d0c4716757b834` |
| `metnos_v262_failure_audit.json` | `184faec929b1ee2260efd37cb1caca27b1487d5390ffe1387f888327b007a73e` |
| `metnos_v262_failure_audit.md` | `69041c89453bca54cd595d819f66d14b21668afcd047a01b9eb954ef73e2c530` |
| `metnos_v262_k1_multidimensional_review.md` | `df21820aa0c55c098b3c6ae4fe17666b362dd5abfda1fea79a3fe4af354cb5e8` |

## `v253/`

Conserva byte per byte runner, prompt, schema, freeze, fixture, mutation e run
K1 V25.3, più il rescore diagnostico del primo tentativo. Il rescore produce
34/34 sul binding current-location, ma è post-hoc e non riceve credito; la
tupla completa a sette campi era 24/34. V25.3 è una dipendenza della catena,
non il candidato da rilanciare. Hash e limiti sono in `v253/README.md`.

## `v263/`

Contiene il freeze autore pre-run V26.3: stessi campi semantici minimi V26.2,
registro tecnico completo e simmetrico, prove tagged con anchor locali
impliciti, UAX #29, una call e zero retry. L'audit offline è 37/37 e non trova
righe surface, query copiate o overlap di almeno tre token sui dataset
109 + 34 + 70.

**Nessun run V26.3 è autorizzato o presente.** Leggere prima
`v263/README.md`: il candidato attende audit indipendente dell'oracolo, review
esterna e un gate lock hash-chained separato. Il campo storico
`inference_allowed=true` nell'`author_pre_gate` non è permesso operativo.

## `v264/`

Contiene il freeze autore pre-inferenza V26.4, costruito dopo il blocco di
V26.3 e l'oracolo tipizzato indipendente. Usa un grafo normalizzato con atomi
projection/dependency, edge soltanto verso atomi precedenti, firme e adapter
derivati da un unico registro tecnico, prove claim-compatible, schema generico
compatto e validator fail-closed. Supporta inoltre più azioni/domìni e conserva
le clausole supportate quando un'altra clausola è fuori registro.

I controlli offline sono 69/69 nel core e 125/125 nel probe indipendente;
l'audit prompt trova zero righe surface/mixed e zero overlap di almeno tre
token sui dataset 109 + 34 + 70. Il successivo K1 autorizzato non ha raggiunto
il server: la sandbox ha prodotto `PermissionError(errno=1)`, quindi il modello
resta non valutato. Il gate V26.4 è consumato; diagnosi e output grezzo sono
archiviati in `v264/` e non autorizzano un rerun.

## `v2641/`

Contiene il freeze runner-only V26.4.1. Registry, schema, prompt e fixture sono
byte-identici a V26.4; restano identici anche i bundle sorgente di segmentatore,
validator, classifier, evaluator e request body. Il delta conserva la diagnosi
completa degli errori, usa contatori per fase, interrompe solo su
trasporto/protocollo, continua sui veri output modello invalidi e pubblica
output atomici/no-clobber.

Il live gate deve legare un preflight standalone PASS recente e il runner
esegue comunque un GET inline nello stesso processo prima del primo POST.
Core 69/69, graph 125/125, infra 72/72, oracle 34/34 e contamination zero.
Freeze SHA
`6f98647d6a9ea3b46ec521dca50e9f5bb00352d9b9360047219bd81976cd6eff`.
Review indipendente runner-only PASS, SHA
`598dac69fe53e5b1dd1884b400a6d48de35f586db1b4a6394b49a9392c195bfa`.
Il preflight e l'unico K1/34 sono poi stati eseguiti correttamente: trasporto e
34 inferenze riusciti, ma solo 1/34 frame valido. In 20 casi l'unico errore è
`clause_ids`, invariante non dichiarata al modello. Non rilanciare V26.4.1.
I probe post-run nella directory provano offline che `clause_id`, `atom_id` e
`output_index` sono derivabili; il solo puntatore alla sorgente resta semantico.

## `v265/`

Contiene esclusivamente il design/probe offline della normal form compatta:
nessun `clause_id`, `atom_id` o `output_index` nell'output LLM; resta un solo
`source_ordinal` sugli edge. L'adattatore ricostruisce il formato V26.4.1 e usa
il validator congelato senza modificarlo. Risultato: 19/19 round-trip sui
controlli positivi del probe indipendente, 5/5 nuovi controlli fail-closed e
riduzione JSON 5,64%. **Non è frozen e non autorizza prompt, rete o run.**
Lo schema decoder di design è ora auditato 30/30: accetta i 19 grafi positivi,
rifiuta i campi rimossi e non aggiunge lessico sorgente. Vedere `v265/README.md`.
Anche il prompt è derivato e auditato: 56/59 righe invariate, 7/7 controlli,
zero overlap sui dataset e 1.140 token contro 1.147 del parent. Non esiste
ancora freeze e nessun run è autorizzato. Il runner offline-only ora passa
34/34 e non espone endpoint o inferenza; dettagli in `v265/README.md`.

## `v2651/`

Successore creato dopo il BLOCK indipendente V26.5. Il manifest contiene ora
34 artefatti durevoli e passa 38/38 controlli pre-import. Il validator tecnico
self-contained è stato estratto meccanicamente (23 funzioni), usa il registry
hash-pinned, non contiene rete/path temporanei e passa 11/11 test offline.
La suite compact-native passa 106/106: 68 mutazioni storiche classificate
56 retained, 3 obsolete e 9 replacement, più 16 negativi e 6 positivi. Le
sorgenti storiche restano quarantinate e gli import sono source-only. L'audit durevole 109+34+70 passa
15/15, pinna le sequenze e trova zero overlap; il corpus di 213 query è
audit-only e non contiene gold/output. Il core candidato source-only passa
96/96, verifica i propri artefatti, conserva l'input e non contiene trasporto;
il futuro freeze/gate deve pin-nare il manifest come radice esterna. Review
pre-hardening PASS; la review del delta è **STATIC BLOCK** sulla policy di
esecuzione. Vedere i due report `metnos_v2651_independent_delta_review.*`.
Nessun freeze/gate/run da questa versione.

## `v2652/`

Successore offline che applica la policy per consumer prima di ogni
`compile/exec`. Core 114/114 e verifier 43/43: i 96 replay precedenti restano
verdi, 14 mutazioni manifest sono respinte da core+verifier e 4 probe diretti
dell'accessor falliscono chiusi. Prompt/schema/adapter/validator/corpus sono
invariati. La review indipendente lo pone in **STATIC BLOCK**: mappe/path
forgiati raggiungono ancora l'esecuzione e `evaluate_segments` accetta input
id-only. Nessun freeze/gate/run.

## `v2653/`

Successore offline query-bound: espone soltanto
`evaluate(original_request, frame)`, deriva internamente segmenti/testo/offset
e non accetta path, mappe o moduli dal caller. Un manifest runtime a cinque
identità è pin-nato dal core; solo adapter e validator verificati raggiungono
un unico `compile/exec`. Core 115/115 e verifier 49/49 su 43 artefatti.
La review indipendente lo pone in **STATIC BLOCK**: l'helper di esecuzione
accetta ancora source/injection dal caller e l'allowlist non chiude l'accesso
indiretto ai builtins. Replay 106/106 e audit 15/15 restano verdi; nessun
wrapper/freeze/rete/run.

## `v2654/`

Successore offline con facciata a sola API `evaluate` e worker Python
one-shot non importabile. Il worker riceve soltanto query+frame JSON, viene
eseguito da byte verificati in `memfd` sigillato con interprete isolato e
mantiene localmente l'unico sink adapter/validator. Author self-test 24/24,
fixture 6/6 positiva e 16/16 negativa; overhead p50 circa 88 ms. La review
indipendente riproduce 24/24, 106/106 e 15/15 ma pone V26.5.4 in **STATIC
BLOCK**: la facade verifica il limite cumulativo da 1,5 MB soltanto dopo aver
materializzato JSON e UTF-8. Nessun freeze/gate/rete/run.

## `v2655/`

Successore facade-only: worker, manifest e semantica V26.5.4 restano
byte-identici; lo snapshot conta esattamente e cumulativamente JSON/UTF-8
prima della serializzazione. Author PASS: 24/24, 106/106, 15/15, 6/6 positivi
e 16/16 negativi; 1.500.000 byte accettati e 1.500.001 respinti prima del
dump con memoria aggiuntiva limitata. La review indipendente del delta è
**STATIC PASS**, con 1.004 differenziali canonici e 10.011 float senza mismatch.
Nessun freeze/gate/rete/run è creato o autorizzato.

## `v2656/`

Author checkpoint del runner compatto K1/34, ora in **STATIC BLOCK** dopo
review indipendente. Una call per
caso, zero retry, preflight GET inline, raw frame passato alla facade V26.5.5,
contatori di trasporto/modello separati e output atomico. Il runner non apre
mai source controls, fixture, oracle o evaluator: salva prima un model-batch
query-free con `accuracy_claimed=false`. L'evaluator CLI offline legge quel
batch una volta, valida closure/hash/counters prima del gold e scrive un
artifact `*_evaluation.json` distinto legato al batch SHA.

Author self-test 21/21 con trasporto finto; rete/modello reali zero. Freeze
`d17cddcb1f4aae770f9288294c17a2b49cb7321916dfa61bd33ba954cfe813f5`,
runner `ffb55a27da2167dc1ecb3cf878bf4f949380b81ffff1890dbe7a5f7ee4adf225`.
La review riproduce 21/21 e il freeze, ma blocca il candidato: errori CLI
attesi non contenuti, nessun producer `--preflight`, proxy/redirect impliciti
che invalidano one-call e contatori, frame non validato interamente pre-gold e
writer runner senza parent dirfd/no-follow. Il batch corrente ha comunque un
bound statico conservativo di 7.609.728 byte serializzati. External gate
assente; nessun live è autorizzato. Il registro congelato ha soltanto 10
relazioni: nessun risultato K1/34 può certificare la suite generale 109 o
autorizzare il cutover legacy.

## `v26561/`

Successore infrastrutturale separato di V26.5.6, ora **STATIC BLOCK**
indipendente. Prompt, schema, registro, adapter, facade, worker e
manifest restano byte-identici. Aggiunge boundary CLI sanitizzato per runner
ed evaluator; producer `--preflight` atomico subordinato a un gate preliminare
indipendente; shape esatte e validazione full-frame pre-gold; opener nativo
senza proxy/redirect, endpoint loopback letterale e conteggio delle aperture;
output parent aperto no-follow prima del trasporto e mantenuto via dirfd fino
al link/fsync. Il cap batch è 7.609.728 byte prima della codifica.

Self-test: 21/21 ereditati più 13/13 nuovi, inclusi frame vuoto con zero gold
read, redirect/proxy/doppia apertura, swap del parent output e boundary del
cap; rete e modello reali zero. Freeze
`f39140cc94e328e465e60e4c297ea8a77f4af57d810ce65c54b43693599ae316`.
Preflight gate ed external live gate sono entrambi assenti: il verdetto
indipendente non autorizza alcun GET, POST, run o inference.
La review riproduce 34/34 ma trova tre blocker: checkpoint che impone assenti i
gate operativi da validare, mismatch contatori/summary che raggiungono gold e
contesto interprete/dependency non imposto prima del validator exec. Il limite
di scope del registro a 10 relazioni resta invariato.

## `v26562/`

Successore byte-distinct author **PRE-INFRA-REVIEW** che chiude esattamente i
tre blocker V26.5.6.1: verifica dei byte author separata dalla policy di assenza
gate; closure/riconciliazione completa pre-gold di diagnostica, contatori,
preflight, tentativi totali e latenze derivate; contesto evaluator e membership
completa dei tree verificati prima di compile/exec. I byte semantici restano
immutati. Self-test 34 ereditati + 16 nuovi = 50 PASS, rete/modello zero.
Freeze `da2ccdf5f04f22916fec8bd066cad5f01ba2fccb005cb21437767b209b2a5e88`.
Preflight gate ed external gate assenti; nessun trasporto o live autorizzato
prima di una nuova review indipendente sui byte esatti.

La review successiva ha bloccato V26.5.6.2: otto mutazioni di tipi JSON,
counter replicati, prove request-body/latenza ed endpoint userinfo raggiungono
il gold. Non creare gate per questa versione.

## `v26563/`

Successore author **PRE-INFRA-REVIEW**. Impone tipi esatti, ricostruisce
byte/SHA della request dai pin, riconcilia counter e latenze e usa un parser
canonico IP-loopback. Rimuove dal batch byte/digest del response body, perché
senza il payload sarebbero osservazioni non verificabili. Gli otto bypass più
le varianti boundary falliscono con zero gold reads. Self-test 50 ereditati +
16 nuovi = 66 PASS; freeze
`f3a655d1786b651da509c91a4cf0f9679f6e192885d744ffb100486c6bd224cc`.
Rete/modello zero; gate assenti; serve review indipendente sui byte esatti.

La review successiva ha bloccato V26.5.6.3: quattro booleani annidati accettano
0/1, timestamp negativi coerenti superano la closure e la porta 0 è ammessa;
due digest non ricostruibili restano presentati come osservazioni. Non creare
gate per questa versione.

## `v26564/`

Successore author **PRE-INFRA-REVIEW**. Un census ricorsivo fail-closed assegna
un tipo esatto a ogni foglia scalare prima del gold: 148 path normalizzati
mutati, 2.028/1.994 foglie nei batch valid/invalid. Booleani e timestamp sono
esatti, le porte canonicali sono `1..65535`; `raw_frame_sha256` e persisted
`model_content` sono rimossi. Self-test 66 ereditati + 19 nuovi = 85 PASS;
freeze `d41a440551af5bfc430000e12bcdda2cbc89ca8c601b137d74b6e57f47f6cd56`.
Rete/modello zero; gate assenti; serve review indipendente sui byte esatti.

## `v26565/`

Successore clause-owned, **STATIC BLOCK** indipendente. L'identità di clausola
passa nella struttura: `clauses[]` posizionale, una sola `projection` per
clausola, `dependencies` annidate, span retrocesso a pura prova,
`source_ordinal` sull'appiattimento derivato. Schema e prompt sono generati dal
registro tipizzato congelato, quindi non possono divergere; registro V26.4.1 e
validator V26.5.3 sono riusati invariati. Self-test 42/42, `inference=false`,
freeze `b7b02367bba88c244dd772b0722f4925292e4e318eca80b5df0b0556f3638f90`.

La review indipendente riproduce 42/42, il risultato d'autore byte-identico,
9/9 artifact, 5/5 dipendenze, 10/10 righe di hash e zero `.pyc`, e **conferma la
correzione**: su 4.000 frame schema-validi casuali `primary_cardinality`,
`orphan_dependency`, `clause_span_consistency` e `projection_missing` non si
accendono mai. Confermati anche archi fra clausole, Unicode su 9 scritture ×
NFC/NFD/NFKC/NFKD, contaminazione 0 su prompt **e** schema (0 3-gram e 0
4-gram), 53 letterali di schema tutti registro-derivati o strutturali, e
`DERIVED_REFERENCE_BRIDGE` confinato al self-test.

Blocca su quattro difetti offline: (1) l'espansore **non è totale** — cinque
input scalari al posto di un contenitore lo fanno sollevare, e lo stadio 2 della
condotta non ha `try/except` mentre lo stadio 3 ce l'ha, quindi il caso si perde
su tutti e tre gli stadi, cioè il guasto V26.5.6.4; (2) l'impronta S4 copia
verbatim le stringhe del modello sui frame **schema-invalidi**, che sono
esattamente quelli per cui S4 esiste; (3) `typed_ambiguity` con clausole fuori
registro proietta solo quelle della prima alternativa e riapre `clause_ids`, il
codice che uccise V26.4.1, più `clause_limit`; (4) `max_atoms_per_analysis` non
è imposto dallo schema di risposta (tetto reale 128 contro 16) e il validator
ritorna subito dopo il proprio schema interno, azzerando la misura semantica
mentre `stages_measured` dichiara 3. Report, sonda deterministica e risultato:
`metnos_v26565_independent_static_review.{md,json}` e
`metnos_v26565_independent_static_review_probe.py`. Nessun gate, nessun live.

## Regola

Un nuovo candidato crea una nuova directory/versione. Non modificare un freeze
esistente dopo aver osservato risultati; non promuovere i metadata
`prototype_manual_review_required` a produzione.
