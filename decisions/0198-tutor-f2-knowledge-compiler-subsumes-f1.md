---
id: 0198
title: Tutor F2 con catalogo dinamico; F1 diventa compatibilità transitoria
date: 2026-07-23
status: accepted
area: runtime | tutor | documentation | i18n
related: [0092, 0173, 0193, 0195, 0196, 0197]
---

# 0198 - Il catalogo conoscitivo F2 sussume le schede F1

## Contesto

ADR 0197 ha eliminato il routing Tutor per frasi e introdotto recupero BGE-M3
locale e composizione fondata. Il primo corpus era però composto da sei schede
curate. Cambiare il set degli executor richiedeva ancora manutenzione manuale e
domande non anticipate potevano restare senza una fonte pur in presenza di
manifest o documentazione sufficienti.

Le schede non sono un livello equipotente a un Tutor: sono utili per procedure
editoriali ad alta criticità, ma troppo rigide come rappresentazione generale
delle capacità.

## Decisione

F2 costruisce un solo catalogo firmato schema 3 da quattro classi di fonte:

1. descrizioni multilingue generali e dei singoli argomenti di tutti gli
   executor ammessi dal loader verificato;
2. ogni pagina HTML indicizzabile sotto l'esatta radice di pubblicazione
   `docs/`, inventariata da `runtime/published_docs.py`;
3. guide curate F1, trattate come fonti ad alta autorità nello stesso ranking.
4. registri runtime esplicitamente proiettati, quando sono la fonte canonica
   della struttura mostrata dalla UI; non contengono stato live materializzato.

Non viene eseguita una scansione arbitraria del filesystem: l'inventario è
bounded e limitato alla sola radice che `deploy.sh` pubblica. Accetta file
regolari UTF-8, non symlink, con `html lang`, `canonical` HTTPS univoco sotto
`metnos.com` e gruppi `hreflang` reciproci; una pagina `noindex` è esclusa.
`tutor/sources.toml` resta il registro delle sole fonti supplementari non
pubbliche e non può duplicare un documento pubblicato. Dati utente, turni,
log, ADR, roadmap, report e documenti interni restano fuori dal corpus. Un
blocco HTML pubblico marcato `tutor-exclude` resta visibile al lettore ma non
viene indicizzato: serve in particolare a separare capacità correnti e
roadmap.

Ogni unità possiede un `concept_id` stabile. Il retriever sceglie la lingua per
singolo concetto: lingua richiesta, inglese, poi un'altra traduzione
deterministica. La lingua dei documenti deriva da `html lang`: il corpus
pubblicato corrente è IT/EN, ma una nuova traduzione valida viene ammessa senza
modificare un registro. BGE-M3 può recuperare una fonte inglese da query in
altre lingue e il compositore risponde nella lingua corrente. I manifest
restano mappe i18n: IT/EN obbligatorie per executor attivi, lingue ulteriori
ammesse e indicizzate.

La graduatoria unifica schede e unità dinamiche. Usa un embedding della query,
similarità densa, un piccolo segnale lessicale IDF derivato dai candidati e una
priorità limitata al tie-break. Non usa sinonimi, `affinity`, esempi `exact` o
nomi executor cablati nel codice Tutor.

I documenti lunghi sono segmentati in fase di compilazione rispettando titoli
e sezioni, con unità di massimo 920 caratteri. Il retrieval opera direttamente
sui segmenti: al compositore arrivano i soli segmenti pertinenti e, entro un
limite chiuso, quelli adiacenti dello stesso documento. Una pagina intera non
viene quindi inserita nel prompt soltanto perché uno dei suoi argomenti è
pertinente.

Il confine di modo opera così:

- i soli controlli deterministici riconoscono segreti, comandi di controllo e
  un verbo canonico operativo iniziale; non esiste un lessico di frasi d'aiuto;
- un classificatore locale chiuso restituisce soltanto `EXPLAIN`, `ACT`,
  `MIXED` o `UNKNOWN` prima di aprire il catalogo;
- solo `EXPLAIN` viene composto; `ACT` e `UNKNOWN` ricadono nel motore,
  `MIXED` richiede separazione.

Il classificatore e il composer usano lo scheduler centrale come risorse LLM
classe 0. Nessuno riceve strumenti. Il filtro audience precede la costruzione
del contesto; una fonte riservata non viene passata al modello.

I due componenti dichiarano soltanto il tier logico e il contratto d'uscita:
mode usa `fast`, composer usa `wise` con policy `public`. Provider, modello,
endpoint e parametri di inferenza sono risolti da `llm_router`/`llm_helpers`,
non dal Tutor. Il prompt assegna esplicitamente il ruolo di Tutor di Metnos e
richiede di cercare nell'intero contesto la risposta migliore e completa,
integrando tutte le fonti pertinenti. Il gateway è il solo punto di
normalizzazione e rifiuta marker strutturali interni nell'output pubblico.

Le procedure amministrative o di sicurezza selezionate come fonte primaria
restano letterali. Le schede informative F1 sono transitorie: possono essere
ritirate quando F2 supera il loro corpus di equivalenza e un corpus operativo
dimostra zero sottrazioni al planner.

## Integrità e prestazioni

Testi, vettori, hash, fingerprint dell'embedder e metadati vivono nello stesso
SQLite firmato. Il compilatore:

- costruisce un candidato separato;
- verifica shape, finitezza e norma dei vettori;
- esegue `PRAGMA integrity_check`;
- firma il candidato e lo sostituisce atomicamente;
- mantiene un last-known-good della generazione ammessa;
- riusa i vettori invariati per hash semantico.

Un'aggiunta, modifica o cancellazione nella radice pubblicata cambia il timbro
dell'inventario e invalida il catalogo, come un cambio a fonti supplementari,
manifest ammessi, compilatore o modello. Il timbro viene controllato prima di
ogni accesso al catalogo e all'avvio HTTP; se è invariato il controllo è rapido,
se cambia parte la ricompilazione con riuso dei vettori invariati. Un errore
lascia intatto il candidato precedente e non rende indisponibile il motore
operativo. L'installazione indicizza i documenti locali ricevuti con
l'aggiornamento: non effettua crawling remoto di `metnos.com`.

## Conseguenze

- L'aggiunta o rimozione di un executor ammesso si propaga al Tutor senza
  editare schede.
- Valori ammessi, selettori di account, default e collegamenti di credenziali
  documentati negli argomenti manifest diventano fonti recuperabili, senza
  duplicarli in schede Tutor.
- Una nuova forma linguistica non richiede una regola di routing.
- La documentazione pubblica può presentare la roadmap senza farla apparire
  come capacità corrente.
- F1 conserva soltanto il valore editoriale e di equivalenza, non è più il
  limite della copertura.
- La prima compilazione ha un costo misurabile; le successive sono
  incrementali e il lavoro non è sostenuto per ogni domanda.
- Una lacuna documentale e un guasto tecnico sono esiti distinti. Il composer
  risponde alle parti sostenute e dichiara il solo dettaglio mancante.
- Un seguito ellittico usa un unico scambio in RAM soltanto quando il contesto
  precedente migliora materialmente il retrieval; nessuna query entra nella
  telemetria in chiaro.
- Una domanda su come usare o configurare una capacità riceve prima una
  richiesta naturale di esempio da adattare nella lingua corrente e poi i
  dettagli tecnici. È una
  regola del compositore grounded, non un nuovo instradamento per frasi o topic.
- Le superfici Settings sono descritte da un registro tipizzato condiviso con
  la sidebar e compilate come fonti `ui_surface`. La presentazione dipende dal
  tipo di fonte e dal canale: percorso e contenuti visibili vengono prima;
  Telegram rinvia esplicitamente alla chat web. Non esistono branch per una
  singola pagina o formulazione.

## Evidenza al momento dell'adozione

- 713 unità: 358 IT, 355 EN; 230 da manifest ammessi; 520 utente e 193
  amministrative;
- query francese, tedesca e spagnola recuperano la fonte email inglese con
  coseni rispettivamente 0,899, 0,878 e 0,863;
- composizione reale francese da solo contesto inglese riuscita in 1,59 s;
- 55 test Tutor verdi;
- 340 test i18n più 360 subtest verdi;
- 82 test su manifest, loader e standard verdi.

## Guard

- `tests/runtime/tutor/test_tutor_f1.py` — nome storico, copre F1 e F2;
- `tests/runtime/i18n/`;
- `tests/runtime/skills/test_manifest_description_schema.py`;
- `tests/runtime/skills/test_manifest_lang_state.py`;
- `tests/runtime/executors/test_executor_standard.py`.

## Addendum di certificazione — 23/7/2026

Il compilatore indicizza ora anche `[args.properties.*.description]`, comprese
le proprietà annidate, in unità bounded con `concept_id` distinto per argomento
e parte. Il file del compilatore entra nell'identità di invalidazione: cambiare
la proiezione dei manifest forza una nuova generazione anche se nessun documento
è stato modificato.

Il corpus risultante contiene 2.047 unità (1.025 IT, 1.022 EN): 230 descrizioni
generali manifest, 1.332 descrizioni di argomenti, 1.844 fonti utente e 203
amministrative. Integrità SQLite e firma Ed25519 sono verificate.

La panoramica generale è derivata da tutte le aree e dai provider del catalogo
vivo, non dai titoli delle guide. Il budget bounded del composer è 640 token per
evitare il taglio di aree tardive. Evidenze HTTP: `c91e0a239e4a4931`
(web/luoghi/geolocalizzazione/Google inclusi), `8ec991fc12094155` (Google
Workspace 24/24 e cause storiche), `0dc1129c795a4404` (mailbox aggiuntive e
credenziali dal manifest). Il boundary Telegram ha caricato lo stesso codice,
ma un turno live Telegram resta fuori da questa certificazione.

Gate consolidato successivo: 352 test verdi e 22 skip ambientali dichiarati;
catalogo finale schema 3, 2.047 unità, `PRAGMA integrity_check=ok`, firma valida.

Addendum successivo: fonti Settings, guida bilingue IMAP/SMTP e superfici UI
portano il corpus da certificare a 2.091 unità (1.047 IT, 1.044 EN; 1.862
utente, 229 amministrative), comprese 26 unità `ui_surface`. La presentazione
è consapevole del canale e l'esempio naturale resta nella lingua corrente.

## Addendum — inventario di pubblicazione e reference dei domini, 24/7/2026

L'inventario condiviso comprende 77 pagine HTML indicizzabili; due redirect
storici `noindex` restano pubblicabili ma non sono fonti Tutor. La nuova
reference bilingue documenta i 26 domini canonici e l'utilità `_system`, con
98 operazioni incluse derivate da manifest first-party e contratti builtin e
54 esempi di richieste naturali curati. Le due pagine producono 72 segmenti
conoscitivi bounded.

Il catalogo isolato risultante contiene 3.734 unità, supera
`PRAGMA integrity_check` e verifica la firma. Nella prova reale «Qual è la
differenza tra eventi, calendari e attività programmate? Fammi esempi.», i
primi risultati pubblici pertinenti includono le tre sezioni distinte della
reference, senza passare l'intero documento al compositore. La certificazione
F2-only della panoramica è `PASS` senza schede F1. La reference è stata inoltre
verificata a 1.440 e 390 pixel senza overflow ed è pubblicata su
`/it/domains` e `/en/domains`.

Queste evidenze non chiudono ancora il gate di ritiro F1: restano necessari il
corpus completo di equivalenza, zero sottrazioni operative e il turno live
Telegram.

## Addendum — robustezza live e virtualizzazione LLM, 24/7/2026

Due richieste reali finite impropriamente nel planner hanno rivelato uno skew
tra moduli caricati dal processo HTTP e sorgenti su disco. Il boundary è stato
reso fail-soft ma sincero: dopo un errore del sottosistema Tutor esegue una
seconda classificazione semantica; `EXPLAIN` e `MIXED` restituiscono
indisponibilità tecnica esplicita senza operazioni, mentre `ACT` continua a
ricadere nel motore. Non sono state introdotte liste di frasi o topic.

La query breve «Cosa sai fare?» recuperava già i segmenti di capability ai
primi posti, ma restava sotto la soglia assoluta 0,75. La calibrazione globale
a 0,70 la ammette senza branch per query, dominio o executor. Il replay
`6bdfca5e24404dbb` produce una panoramica completa delle 44 aree/provider e 114
operazioni, incluse capacità OS, web, geografia, Google e amministrazione,
senza invocare executor.

Una prova con `wise` e reasoning attivo sul deployment llama.cpp/Qwen corrente
ha inoltre mostrato che il backend può restituire il ragionamento nel normale
contenuto, consumando il budget prima della risposta. La contromisura non è un
filtro lessicale nel Tutor: la policy centrale del tier locale disabilita il
reasoning finché non esiste un canale separato affidabile, mentre il binding
`wise` resta virtuale e può essere spostato a un provider più capace. Il
contratto pubblico rifiuta in modo provider-neutrale marker strutturali interni.

Questi replay chiudono le regressioni mirate, non il gate di ritiro F1: la
certificazione estesa di equivalenza e lo zero-false-steal restano necessari.

## Addendum — rappresentazione dell'inventario e selezione coerente, 24/7/2026 (sera)

L'analisi multidominio (109 esempi pubblicati, harness
`scripts/analyze_tutor_multidomain.py` con hook `explain` in
`semantic.retrieve_sources`) e la certificazione hanno isolato quattro difetti
strutturali, tutti risolti con regole generali e misurate:

1. **Audience della conoscenza distinta dall'audience di accesso.** Le unit
   delle superfici UI ereditavano l'audience di ACCESSO (`instance_admin` per
   ogni route `/admin`), negando agli utenti conoscenza che le schede F1
   ratificavano come `audience_minima = "user"` (accoppiamento dispositivi):
   esito `restricted` errato in IT e concetti impossibili da coprire in EN.
   `UiSurfaceSpec.knowledge_audience` è la dichiarazione distinta nel registro
   (dispositivi = `user`, console proposte = `instance_admin`), consumata da
   `sources.py`; il verifier la valida.

2. **Inventario capacità = testa + parti, ricongiunto in selezione.** Le parti
   dell'inventario embeddavano tutte lo stesso testo (lista globale di slug):
   cloni irrilevanti per il contenuto proprio e rumorosi per le panoramiche.
   Un solo vettore non può rappresentare insieme il generale e lo specifico:
   ora una unit «testa» porta il riassunto proprio dell'inventario (prosa
   generale + aree naturalizzate) e ogni parte porta solo le finalità in
   linguaggio naturale estratte dai manifest ammessi. Una fonte logica divisa
   solo per dimensione viene ricongiunta: quando una parte è selezionata, le
   sorelle si uniscono entro il budget di gruppo (8) e i membri già ricongiunti
   sono protetti dall'eviction di gruppi successivi. Il bug «ping-pong» (un
   anchor già espulso ri-espandeva il proprio gruppo espellendo la coda) è
   stato trovato con l'instrumentazione `explain` e chiuso: un anchor espulso
   non espande.

3. **Fusione titolo/corpo (B2) misurata e SCARTATA.** Il prototipo
   (max(coseno titolo, coseno corpo) su candidati reali, aritmetica esatta a
   parità degli altri segnali) dà +1 problema in banda su 15 ma −1 controllo su
   10 e peggiora undo-EN: guadagno netto ≈ 0 contro il costo di uno schema v4
   con doppio vettore. Resta documentato qui come strada esplorata.

4. **Prompt compose v7: l'esempio naturale apre, il percorso UI segue.** La
   clausola di precedenza UI contraddiceva il contratto del corpus e la forma
   stessa delle unit `ui_procedure`; il trigger del lead ora copre anche le
   domande procedurali («come/con quale procedura»).

Misure sul catalogo finale: multidominio 105/109 stabile (4 residui noti con
vincitore semanticamente corretto: coppia get_inputs, coppia undo a 0,060-0,061
dalla banda); 5/6 query panoramica del corpus selezionano l'inventario completo
(6 unit). La sesta («What can you do in practice?») è un pari-fallimento
ereditato: nemmeno la scheda F1 `metnos-capabilities` entra in selezione per
quella formulazione (misurato col medesimo ranking, carte attive) — F2 ≥ F1 su
ogni query panoramica. Correzioni minori: banda parametrizzata
(`METNOS_TUTOR_KNOWLEDGE_BAND`, default 0,06), budget per coppia
documento+sezione, marker provider da `vocab.PROVIDER_SUFFIXES` (SoT), ledger
di copertura multiriga con provider dal registro. La semantic delle unit
`ui_procedure` è ora derivata dai controlli dichiarati della superficie, non da
prosa specifica di una pagina.

Estensioni successive nella stessa giornata: il ledger di copertura deriva la
checklist da TUTTE le fonti strutturate selezionate (righe inventario, finalità
dei manifest via `SCOPO:`, contratti delle superfici dal registro) con regola
uniforme; prompt compose v8 (lead naturale = prima riga sulle domande
procedurali; passi e condizioni di arresto riportati per intero; prerequisito
credenziale+vagli per i provider elencati); registro superfici con
`structure_sha` (guardia anti-deriva, analisi in RM-0003 §5.3). Certificazione
sequenziale completa `--f2-only` sul catalogo finale: 89/134, con `boundary`
12/12 (zero sottrazioni al planner, gate 4 verde) e cross-lingua FR/DE/ES/PT
4/4 ai gate di retrieval (gate 6). I 45 scarti si ripartiscono in: ~13 voci di
corpus da ratificare (coppie `restricted` servite con sole fonti pubbliche
senza leak; gate «Telegram» che vieta il nome del daemon omonimo; panoramica EN
pari-fallimento F1; attese in stile bundle della scheda fotografica), ~15 di
conformità marginale del composer locale su ledger lunghi, ~4 di
classificazione mode su imperativi. Validazione live post-restart: turni HTTP
auditabili `5d8061e866394cff` (credenziali mailbox IT: lead + binding cifrato,
risolve la classe del turno insoddisfacente `6b84d98b`), `d57eb2bb8a784d57`
(panoramica GitHub con prerequisito credenziale e vagli). Nota di canale: in
produzione la lingua di risposta è quella dell'istanza (`current_lang()`), non
della query; una query EN su istanza IT riceve risposta IT con contenuti
completi (personalizzazione per-utente = W2).

## Addendum — gate flessivi, correttore di bozze e certificazione 108/134, 24/7/2026 (notte)

Passo 1 (fedeltà del corpus): le attese fallite per flessione usano voci
`lex:tutor_gate.<x>` risolte da `detection_lexicon.match` (8 concept `regex`
a radice, seed it+en, unione it∪en al match); gli skip sono espliciti e
motivati nel certificatore (`known_equal_fail` per la panoramica EN
pari-fallimento F1; `curated_card` per f1-photos, decisione c1: la scheda
`fotografie-dominio` resta procedura curata). Misura intermedia (cert6):
f1_equivalence da 22/38 a 30/38 non-fail, boundary invariato 12/12.

Passo 2 (correttore di bozze deterministico, RM-0003 §5.7): dopo la
composizione il servizio rilegge meccanicamente la risposta contro le voci
strutturate del ledger (route e campi delle superfici, aree/provider,
finalità dei tool per parole DISTINTIVE per frequenza documentale interna,
condizioni di arresto, marker interni vietati); voci mancanti = UNA
ricomposizione con l'elenco esplicito dei buchi, poi si consegna comunque;
telemetria `tutor_repair_pass`/`tutor_repair_missing` nel turno e nei report.

Certificazione completa sequenziale col correttore (cert7, 34 min, catalogo
isolato): **101 pass + 7 skip = 108/134 non-fail** (baseline 94), `boundary`
12/12 con correttore mai attivato lì (nessuna composizione = nessuna
sottrazione). Repair su 64/134 casi (48%); l'83% dei casi riparati passa.
I 26 scarti residui: 3 di classificazione mode su imperativi (bersaglio del
ritocco al prompt `tutor_mode`), ~7 di mancata selezione della superficie in
retrieval (il correttore non può imporre ciò che il ledger non contiene),
~12 di conformità marginale del composer locale su ledger lunghi (la cura è
il binding `wise` più capace, ADR 0146), ~4 di concept assenti dalle fonti.
Gate 7 chiuso: turno live Telegram `35bba21a0a774f31` (mode=tutor, fondata,
confine chat web rispettato, route e controlli completi).
