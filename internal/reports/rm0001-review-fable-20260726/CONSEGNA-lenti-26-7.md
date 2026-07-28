# Review adversariale di RM-0001 — consegna delle sette lenti

**Destinatario**: l'agente che ha prodotto e mantiene
`internal/roadmap/RM-0001-conoscenza-utente-locale.md`.
**Data**: 26 luglio 2026. **Autore**: agente di review su modello Fable,
sette agenti in parallelo, uno per lente, mandato in
`~/.metnos/rm0001_review/`.

## Che cosa è, e che cosa NON è ancora

Questo file raccoglie le sette lenti **come sono state prodotte**: sono
rilievi di prima mano, non ancora passati al vaglio. Il resto della
catena è in esecuzione e produrrà, nella stessa cartella:

- `refutazione.md` — ogni rilievo affidato a un attaccante col mandato di
  UCCIDERLO; in caso di dubbio il rilievo CADE. Serve a buttare via ciò che
  non regge prima che venga letto come vero;
- `REVIEW.md` — la sintesi sui soli sopravvissuti, con l'impianto minimo
  proposto, la prova non giocabile e le decisioni che spettano a Roberto.

**Fino a quel momento tratta questi rilievi come da verificare.** Un
rilievo qui presente può essere caduto in refutazione: `REVIEW.md` è il
documento che fa fede.

## Come leggere i marcatori

Ogni affermazione porta la sua natura, per mandato:

- `[PROVATO]` — riscontro rieseguibile: percorso e riga, esito di un
  comando, un test, una fonte citata;
- `[IPOTESI]` — ragionamento plausibile senza riscontro;
- `[OPINIONE]` — giudizio di progetto.

Un'affermazione senza marcatore è un difetto del lavoro di review, non una
verità implicita.

## Scopo dichiarato da Roberto, che è il criterio di giudizio

Dare a Metnos un'intelligenza **forte, semplice e automatica**, come
vincolo congiunto: *forte* = sposta in modo misurabile ciò che Metnos
capisce e fa; *semplice* = poche parti, deterministico dove può esserlo;
*automatica* = impara e si applica da sola, e il controllo è una domanda
(«cosa sai di me?»), non un flusso di approvazioni.

Vincolo esplicito del mandato: NON ripetere ciò che RM-0001 ha già in
§7.1, §7.2 e §27, e non riaprire una decisione di §22 senza un riscontro
nuovo.

## I sette verdetti in breve

### ambizione — L'impianto promette intelligenza FORTE?

RM-0001 è un impianto eccellente di *governo* della memoria (isolamento, provenienza,
oblio) ma non promette intelligenza forte: a §9 completo le famiglie di richieste nuove
comprese sono circa cinque, tutte a valle del piano; il tetto è scelto, non subìto —
la roadmap chiude essa stessa (§14.2) l'unica porta verso la comprensione forte
compatibile con le cache, e ignora un precedente interno già in produzione
(`backend_resolver`) per le preferenze che cambiano le azioni.

### semplicita — Costo d'impianto e impianto minimo

Il nucleo (F0-F3 più FTS5) è proporzionato e ben difeso; il costo eccedente sta in tre punti:
il dominio esperienza cucito nella stessa roadmap pur condividendo col dominio utente «soltanto
tipi primitivi e l'adapter dell'embedder»; il derivatore LLM messo in sequenza prima di una misura
del bisogno; una specifica già 2,6 volte più grande di ogni altra roadmap, con 18 ADR aperte.
Con una scissione e due riordini, circa metà delle 2832 righe esce dal cammino critico senza
perdere alcun caso d'uso core (UC-01/02/03/09/10/11).

### automatismo — Quanto lavoro resta all'utente

Il lato controllo («cosa sai di me?», correzione, oblio) rispetta la direzione
di Roberto; il lato apprendimento no: ho censito 21 punti in cui l'utente deve
agire, e nessuna fase F0-F11 rende mai l'acquisizione implicita il
comportamento predefinito del proprietario — le preferenze, l'unico tipo di
memoria che cambia le risposte, non hanno alcun percorso silenzioso in tutta la
roadmap, e l'apprendimento comportamentale termina sempre in una proposta da
approvare. Ogni gate preso da solo è motivato; è l'aggregato che nessuna delle
tre review precedenti ha contato.

### fondamento — I punti d'innesto dichiarati esistono nel codice?

Il fondamento regge: nessun modulo, funzione o tabella che RM-0001 dichiara
esistente è inventata — gli 11 punti d'innesto di §25.1 esistono tutti e §5 è
fedele al codice fin nei dettagli. I difetti stanno nella distanza fra «patch
minime» (r. 2503) e realtà: la catena d'identità odierna fabbrica il
proprietario («host») in almeno quattro punti che F0.3 non elenca, e tre
contratti chiave (InvocationContext, clause_span, authz_revision) non hanno
alcun produttore nei file che la tabella autorizza a toccare; tre meccanismi
esistenti e pertinenti (project_paths.json, `_RUNTIME_ARG_SOURCES`,
TutorPrincipal) non sono mai nominati — rischio di duplicazione, non di
impossibilità.

### danno — Come fa male: memoria sbagliata, conflitti, oblio, ospiti

RM-0001 ha già assorbito bene gli attacchi frontali (origine esterna, planner, cache); i danni residui stanno nei confini fra i suoi stessi compartimenti: la coda di compilazione che riscrive un oblio, il registro di cancellazione che vive nel database che dovrebbe sopravvivergli, la regola lettura/mutazione applicata al passo invece che al piano, i due archivi di preferenze che non si vedono a vicenda. [OPINIONE] Le contromisure di §10 e §16 sono in maggioranza meccanismi con punto d'innesto; quattro (impronta degli elementi dimenticati, «verifica dell'oggetto», profilo minore, isolamento ospite-da-proprietario) sono prosa senza substrato. F0 può partire, ma questi buchi vanno chiusi in F0-F1, non scoperti in F4.

### misura — §18/§19 sono falsificabili o giocabili?

§18 è in gran parte falsificabile (guardie fail-closed, equivalenza §18.4,
avversariali §18.3); §19 lo è solo a metà: l'apparato causale serio (replay
accoppiati, holdout, effetto minimo preregistrato) esiste SOLO per gli hint
d'esperienza, mentre il beneficio della memoria UTENTE — il cuore del progetto —
si promuove su corpus offline costruito dagli stessi autori, con almeno quattro
metriche di §19.1 gonfiabili senza dare valore all'utente. [OPINIONE fondata
sui rilievi 1-4 sotto, ciascuno con riscontro]

### leiden — Il grafo tipizzato con Leiden serve QUI? (domanda di Roberto)

**Non serve.** La partizione Leiden alimenta una sola cosa — proposte revisionabili
(report/ChangeIntent) — e quel canale esiste già in produzione con raggruppamento
deterministico (ADR 0180/0185, `telos_proposals_store`); la scala provata dell'istanza
(17 archi nel grafo di co-attivazione dopo 2 mesi; tetto di ~200 memorie/utente) è di
2-4 ordini di grandezza sotto quella in cui Leiden si distingue da componenti connesse;
l'alternativa più semplice a parità di scopo è: componenti connesse sulla proiezione
positiva + grouping per chiavi e sequence mining che il documento stesso impone come
baseline (r. 1399). Che cosa si perde togliendolo: detto in fondo, ed è poco.


---

# Lente ambizione

Review adversariale di RM-0001, lente «ambizione» — 26/7/2026.
Domanda guida: con l'impianto di §9 completato, quali richieste dell'utente
Metnos capirebbe che oggi non capisce? Riferimenti a riga = 
`internal/roadmap/RM-0001-conoscenza-utente-locale.md` salvo diversa indicazione.

## Verdetto in tre righe

RM-0001 è un impianto eccellente di *governo* della memoria (isolamento, provenienza,
oblio) ma non promette intelligenza forte: a §9 completo le famiglie di richieste nuove
comprese sono circa cinque, tutte a valle del piano; il tetto è scelto, non subìto —
la roadmap chiude essa stessa (§14.2) l'unica porta verso la comprensione forte
compatibile con le cache, e ignora un precedente interno già in produzione
(`backend_resolver`) per le preferenze che cambiano le azioni.

## Rilievi

### 1. Il tetto è architetturale e la roadmap non risponde alla propria domanda di forza
**[PROVATO]** Gli invarianti 13, 15, 16 (righe 543-552) e §18.4 (righe 2045-2068)
impongono piani, executor, argomenti e cache identici con memoria accesa o spenta;
il core si chiude con F0-F5 (§23, righe 2378-2380). Elenco esaustivo delle richieste
nuove comprese a quel punto: (a) «cosa sai di me?» / «cerca nei miei ricordi»;
(b) «ricorda X» / «dimentica X»; (c) UN tipo di riferimento personale, in sola lettura
(F3, righe 1791-1797); (d) scelta silenziosa fra ≥2 candidati già prodotti dal motore;
(e) adattamento narrativo di tono/lunghezza/unità. **Conseguenza:** la risposta alla
domanda guida è «poche», e nessuna metrica di §19 conta le famiglie di richieste nuove
comprese: «forte» non è dimostrato né misurabile con l'impianto attuale.

### 2. «Capire meglio richieste implicite» (§1, riga 200) non ha un meccanismo
**[PROVATO]** I tre trigger di §9.5 (righe 802-808) sono: comando esplicito, fallimento
di una risoluzione esatta di riferimento entro un tipo ammesso, oppure ≥2 candidati
concreti *già delimitati dal motore*. Una richiesta implicita che il motore non sa
piazzare («prepara le solite cose») fallisce PRIMA che esistano candidati: la memoria
non si accende mai. **Conseguenza:** la promessa di comprensione implicita di §1 muore
al trigger; va riformulata o va aggiunto un trigger che parta dal fallimento
dell'intent, non solo dei riferimenti.

### 3. La porta della comprensione forte è indicata e subito richiusa
**[PROVATO + OPINIONE]** §14.2 (righe 1538-1542) nomina l'unica via forte compatibile
con L0/L1 condivise: riscrittura deterministica della richiesta *prima* del lookup
cache, con l'identificatore risolto parte dell'input canonico indicizzato — e la
liquida con «richiederebbe un design separato… Non è autorizzata da questa roadmap».
Risoluzione deterministica pre-hash non viola l'invariante 15: le cache restano
condivise, cambia solo l'input canonico. [PROVATO: chiave L0 = `normalize_hash(query)`,
`runtime/engine/fastpath.py:228`; L1 = `verb|object|actions`,
`runtime/engine/autopath.py:293-298`, coerenti con §5.2 righe 307-315.]
**Conseguenza:** l'unico moltiplicatore di comprensione è rinviato senza data né fase;
senza di esso «Atlas» resta per sempre un cerotto post-piano.

### 4. Caso d'uso assente: default operativi personali — e il precedente interno esiste già
**[PROVATO + OPINIONE]** §9.2 chiude il vocabolario su lang/reply_length/tone/units
(righe 733-737) e §14.1 limita l'applicazione alla presentazione narrativa. «Le foto
vanno sempre sul NAS», «gli impegni di lavoro sul calendario di lavoro» — preferenze
che cambiano ARGOMENTI — non sono consegnabili in nessuna fase F0-F11. Eppure Metnos
inietta già oggi default per-object negli argomenti, post-piano e senza toccare le
cache: `runtime/backend_resolver.py:37` (`OBJECT_BACKENDS`, ADR 0165/0136, «selezione
provider = config, non intento», CLAUDE.mutabile.md §11). **Conseguenza:** la via
deterministica, cache-compatibile e già collaudata per preferenze operative esiste nel
codice; la roadmap non la cita e lascia fuori la classe di richieste che più distingue
un assistente che ti conosce.

### 5. Caso d'uso assente: memoria episodica del lavoro svolto insieme
**[PROVATO]** Le classi origine `assistant` e `automation` hanno scrittura vietata
(§10, righe 923-924): nulla di ciò che Metnos FA può diventare memoria; il sistema
ricorda solo ciò che l'utente DICE. Nessuno dei dodici UC copre «cosa abbiamo fatto
ieri?», «rifallo come l'altra volta», «riprendi da dove eravamo». LongMemEval, citato
in §24, misura proprio il richiamo su dialoghi multi-sessione (arXiv 2410.10813).
**Conseguenza:** il divieto è giusto per la *prosa* dell'assistente (auto-rinforzo,
§16), ma butta via anche gli *esiti fattuali attestati dal runtime* («spostati 12 file
in X»), che non sono prosa LLM: manca una classe origine per l'episodico attestato.

### 6. «Automatica» vale solo in lettura, per sempre
**[PROVATO]** §14.2 (righe 1544-1547): per operazioni mutanti o outbound il bersaglio
viene mostrato o confermato «anche se la memoria ha una corrispondenza forte», e
nessuna fase successiva prevede una promozione a fiducia consolidata. **Conseguenza:**
per tutto ciò che produce effetto — la parte per cui un assistente serve — l'utente
paga la conferma a ogni turno, indefinitamente: il costo permanente che il criterio
«automatica» del mandato di prodotto dichiara inaccettabile. Serve almeno un percorso
dichiarato (episodi confermati N volte → silenzioso) anche se rinviato.

### 7. L'esperienza executor non apprende: sceglie fra opzioni scritte a mano
**[PROVATO + OPINIONE]** L'hint non può uscire da `allowed_strategy_codes` forniti dal
chiamante (righe 1238-1239; firma `consult(...)` riga 2576). Il sistema non potrà mai
acquisire un comportamento che un programmatore non abbia già enumerato: è una cache
di selezione fra k rami esistenti, non esperienza. **Conseguenza:** UC-04/05/12 vanno
riletti così; scelta difendibile per sicurezza, ma il contributo di F7-F8
all'«intelligenza forte» è la scelta di un ramo, cioè log2(k) bit — e il documento
non lo dice.

### 8. F6 duplica il learning-loop W1 già in produzione, mai nominato
**[PROVATO]** ADR 0185 (CLAUDE.mutabile.md §11) consegna già: turno costoso ripetuto →
autopath ombra con conferma umana; lacuna ricorrente → change_intent PROPOSED con
triage. F6/UC-07 (righe 114-127, 1828-1837) promettono lo stesso artefatto
(proposta/ChangeIntent revisionabile da ripetizioni riuscite); `grep -n "0185\|W1"`
su RM-0001 non dà esiti. **Conseguenza:** parte della proattività promessa esiste già;
il delta reale di F6 non è stimato e rischia un secondo canale di proposte parallelo
a quello di ADR 0180/0185.

### 9. Domande di profilo aggregate non coperte
**[PROVATO]** UC-09 è un elenco di record («nessun riassunto LLM necessario»,
riga 152); il recupero restituisce al massimo 3-5 elementi (riga 1424) terminali e non
concatenabili. «Che progetti ho attivi?», «quali orari preferisco per le riunioni?»
richiedono sintesi su più memorie — la classe che LongMemEval chiama ragionamento
multi-sessione. Nessun UC e nessuna fase la prevede. **Conseguenza:** «cosa sai di me»
risponde da schedario, non da assistente.

### 10. Stato dell'arte: il divario è nella superficie d'uso, non nel modello dati
**[PROVATO fonti + OPINIONE]** §24 non cita i due sistemi di riferimento del 2025 per
memoria d'agente: Zep/Graphiti, grafo di conoscenza bi-temporale (arXiv 2501.13956;
gli autori dichiarano fino a +18,5% su LongMemEval e -90% latenza — misura auto-condotta
dal produttore) e Mem0 (arXiv 2504.19413, ECAI 2025; ~67% LLM-judge su LoCoMo,
auto-misurato). Il modello dati RM-0001 regge il confronto (bi-temporalità
`observed_at`/`effective_*`, compilatore notturno allineato a sleep-time compute,
arXiv 2504.13171); ciò che quei sistemi fanno e RM-0001 vieta è iniettare il
recuperato nel contesto di generazione. Il divieto è motivato (C1, §7.1), ma il costo
in capacità della scelta difensiva non è mai quantificato. **Conseguenza:** manca in
§19 un confronto onesto «cosa perdiamo rispetto all'iniezione» su un corpus Metnos,
lo stesso rigore che §19.5 pretende per promuovere gli edge.

### 11. Dimensionamento da rubrica telefonica
**[PROVATO + IPOTESI]** 200 memorie attive per utente (righe 1075-1077), massimo una
consultazione per turno (righe 800-801, 2167), 3-5 elementi restituiti (riga 1424):
un turno compound con due ambiguità ne risolve una sola. Ipotesi: basta per i dodici
UC, non basta per «un assistente che ti conosce» dopo un anno d'uso quotidiano; i cap
sono legittimi come partenza ma non è previsto alcun criterio di crescita.

## Che cosa toglierei / che cosa aggiungerei

**Toglierei.**
- F9/Leiden come fase numerata del percorso: contribuisce zero comprensione utente e
  il documento stesso lo tiene a bagnomaria (righe 1495-1497, 1919-1920); meglio
  appendice di ricerca, la numerazione delle fasi deve raccontare l'ambizione. [OPINIONE]
- F6 come fase autonoma: fondere con il canale proposte esistente di ADR 0185/0180
  (un solo luogo dove nascono proposte da ripetizioni), dopo il confronto che il
  rilievo 8 chiede. [OPINIONE]

**Aggiungerei.**
1. Commissionare il «design separato» di §14.2 (canonicalizzazione deterministica
   pre-lookup, identificatore risolto nell'input canonico) come condizione per
   chiamare la roadmap «forte»: è l'unico punto in cui la memoria può cambiare ciò
   che Metnos *capisce* senza violare gli invarianti 15-16. [OPINIONE, riscontro al rilievo 3]
2. UC-13 — default operativi personali: nuove chiavi W2 con ambito-argomento
   (destinazione foto, calendario per dominio), iniettate al punto già esistente
   `backend_resolver`-style, enum-aware, mai sopra un valore esplicito. Deterministico,
   §7.9-conforme, cache-compatibile. [OPINIONE, riscontro al rilievo 4]
3. UC-14 — episodico attestato: classe origine `runtime_attested` distinta dalla prosa
   `assistant`, limitata a esiti fattuali del choke point (stesso principio
   dell'attestazione di §12.5), per rispondere a «cosa abbiamo fatto ieri». [OPINIONE]
4. Metrica di ambizione in §19.1: quota di un corpus congelato di richieste personali
   *oggi fallite* che diventano comprese, misurata a ogni fase. Un numero solo, e
   risponde alla domanda guida per sempre. [OPINIONE]

## Ciò che ho cercato e NON ho trovato

- Menzioni di Zep, Graphiti, Mem0, MemGPT/Letta o sleep-time compute in RM-0001:
  `grep -in "zep\|graphiti\|mem0\|memgpt\|letta\|sleep"` → nessun esito reale
  (solo il falso positivo «riletta», riga 166). [PROVATO]
- Menzione di ADR 0185 / learning-loop W1: `grep -n "0185\|learning-loop\|W1"` →
  zero esiti. [PROVATO]
- Una metrica, in §19 o §23, che conti le richieste nuove comprese. [PROVATO: assente]
- Un percorso, in qualunque fase F0-F11, in cui una memoria modifichi un argomento di
  executor o la scelta di un piano. [PROVATO: vietato da invarianti 13/16 e §14.3,
  senza fase di superamento]
- Un consumo odierno delle preferenze *di risposta* nel percorso del turno:
  `grep -rn "list_prefs\|get_pref("` su runtime/ → solo `sites_*` e `lang`
  (`runtime/agent_runtime.py:6221-6231`) e un segnaposto (`runtime/engine/executor.py:295`);
  la base W2 «riutilizzabile» di §5.1 è oggi inerte sul lato risposta, quindi anche
  UC-01 parte da zero cablaggio, non da un riuso. [PROVATO]
- Un UC in cui l'assistente ricordi le proprie azioni per l'utente. [PROVATO: assente]

## Fonti esterne usate

- Zep: A Temporal Knowledge Graph Architecture for Agent Memory —
  <https://arxiv.org/abs/2501.13956> (numeri auto-dichiarati dal produttore)
- Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory —
  <https://arxiv.org/abs/2504.19413> (ECAI 2025; misure condotte dagli autori)
- Sleep-time Compute: Beyond Inference Scaling at Test-time —
  <https://arxiv.org/abs/2504.13171> e <https://www.letta.com/blog/sleep-time-compute/>
- LongMemEval — <https://arxiv.org/abs/2410.10813> (già in §24; usato per la tassonomia
  delle domande multi-sessione)


---

# Lente semplicita

## Verdetto in tre righe

Il nucleo (F0-F3 più FTS5) è proporzionato e ben difeso; il costo eccedente sta in tre punti:
il dominio esperienza cucito nella stessa roadmap pur condividendo col dominio utente «soltanto
tipi primitivi e l'adapter dell'embedder»; il derivatore LLM messo in sequenza prima di una misura
del bisogno; una specifica già 2,6 volte più grande di ogni altra roadmap, con 18 ADR aperte.
Con una scissione e due riordini, circa metà delle 2832 righe esce dal cammino critico senza
perdere alcun caso d'uso core (UC-01/02/03/09/10/11).

## Rilievi

### R1 — Due prodotti in una roadmap: l'esperienza executor va scissa
[PROVATO] RM-0001:2538-2541 «`experience_*` può condividere con il dominio utente soltanto tipi
primitivi e l'adapter dell'embedder, mai policy, tabelle o claim»; idem 633-634. Store, compilatore,
facciata, purpose, metriche, suite e fasi sono tutti separati per decisione fissata (§22, righe
2326-2328).
[OPINIONE] Se i due domini non condividono quasi nulla per costruzione, tenerli in un documento
non è integrazione: è somma. Il prezzo lo paga il core: F0 deve preregistrare anche la suite
d'esperienza (R2), §11.6/§12.5/§14.5/§18.6/F7-F8 gonfiano la specifica e ogni revisione futura
rilegge 2832 righe. La scissione in una RM propria RAFFORZA la decisione fissata di separazione,
non la riapre. Conseguenza: RM-0001 perde ~1/3 delle righe e F0 si dimezza.

### R2 — F0 contraddice la regola «ogni riga nasce soltanto nella fase indicata»
[PROVATO] §25 riga 2475-2476 fissa la regola; ma F0 (righe 1758-1759) impone «costruire suite
separate di acquisizione, consultazione, temporalità/conflitti/oblio ed esperienza» e (1755-1757)
preregistra effetto minimo e harm metrics del pilot F8, mentre lo store d'esperienza «non viene
creato prima di F7» (riga 1140) e §27.3 (2779-2783) ha respinto la creazione anticipata proprio
perché speculativa.
[OPINIONE] Lo stesso argomento anti-speculazione vale per la suite: corpus, doppia annotazione e
metodo accoppiato per un modulo dichiarato opzionale (§23, riga 2417) e NO-GO live (§27.4) sono
lavoro a fondo perduto se F7 non parte. Conseguenza: F0 congela solo le tre suite utente; la
suite d'esperienza si congela a un «F7.0» d'ingresso.

### R3 — Il grosso di §11-§12 serve la classe di dati meno autorizzata: F4 dietro misura, F5 prima
[PROVATO] Derivatore (§12.2), riconciliatore (§12.3), segnali comportamentali (§12.4) e il modello
evidenze multiple (§11.3) servono i claim `derived`/`behavioral`, che §13.1 colloca al 6° e 7°
posto su 7 nella precedenza (righe 1465-1466) e che §21 rende «candidato invisibile al
comportamento» (riga 2311). I casi core UC-01/02/03/09/10/11 (§23) sono coperti da W2+scope,
snapshot, CRUD esplicito, un ReferenceSlot e FTS5 — nessuno richiede il derivatore.
[OPINIONE] È la parte più costosa dell'impianto (coda, lease, ombra, riconciliazione, opt-in) per
la classe di conoscenza cui il disegno stesso concede meno effetto: valore per unità di complessità
minimo. F5 non dipende da F4 (il retrieval lavora sulle memorie esplicite di F2). Conseguenza:
ordine F0→F1→F2→F3→F5→(F4 solo se un contatore di lacune — turni in cui esplicito+riferimenti non
bastavano e un claim derivato avrebbe risolto — supera una soglia fissata in F0).

### R4 — §7.9: prima un registro deterministico di regole statiche, poi lo store d'esperienza
[PROVATO] La roadmap stessa dichiara la baseline «regola statica nell'executor» (riga 2228) e il
criterio «Se una regola deterministica risolve il gotcha, la memoria d'esperienza non ha dimostrato
utilità» (righe 2232-2233). Il caso guida UC-04 (righe 71-85, pannello da chiudere) è esprimibile
come regola statica in act_sites. Eppure F7 costruisce store, compilatore, attestazione, scrubber
e fingerprint HMAC prima di qualunque conteggio di gotcha non riducibili a regola.
[OPINIONE] Per §7.9 l'ordine va invertito: un registro versionato di regole statiche dentro
l'executor (costo quasi nullo, stessa autorità, stessa postcondizione) e F7 si apre solo quando N
gotcha contati non sono esprimibili come regola. Le review precedenti hanno stretto sicurezza e
causalità degli hint, non questo cancello d'ingresso. Conseguenza: F7-F8 diventano condizionali a
un numero, non a una posizione in sequenza.

### R5 — F3 non ha un primo caso nominato: il pilastro «riferimenti» è senza deliverable
[PROVATO] Il caso guida UC-02 (Atlas) è differito (righe 51-52) e l'uscita di F3 lo ribadisce
(righe 1800-1801); la scelta di slot e provider è ADR aperta (§22 punto 6, riga 2352). Si
progettano quindi `references.py`, ReferenceSlot e provider (righe 2491, 1795-1797) senza sapere
per quale oggetto.
[OPINIONE] §9.3 elenca già tipi con ID deterministici esistenti (persona/contatto, calendario,
righe 754-760): nominare ORA il primo slot (contatti è il candidato naturale: la disambiguazione
di destinatari è frequente e il registro esiste) costa una riga e dà a F3 un criterio di
completamento concreto. Conseguenza: senza questa scelta F3 è impianto per completezza e scivola
di fatto dietro F5.

### R6 — §7.9/§7.3: il rilevatore bilingue non aggancia `detection_lexicon` (rischio secondo riconoscitore)
[PROVATO] `grep -c detection_lexicon RM-0001` = 0. §12.1 (riga 1254) introduce «un rilevatore
bilingue limitato» e §25.1 (riga 2484) un `commands.py` con «riconoscimento bilingue ristretto»;
il meccanismo SoT esistente `runtime/detection_lexicon.py` (+ seed IT+EN) non è mai nominato. La
regola di progetto (memoria `feedback_no_hardcoded_synonym_lists`) vieta liste bilingue proprie e
impone il lessico; il Tutor lo usa già (`lex:tutor_gate.*`, CLAUDE.mutabile §11).
[OPINIONE] Senza il vincolo scritto, F0.4 produrrà quasi certamente un secondo riconoscitore
parallelo, aggravando il debito i18n a 2 locali già censito. Conseguenza: una riga in §25.1 —
«commands.py = concept nel detection_lexicon» — elimina un modulo di fatto.

### R7 — Il costo della specifica è esso stesso impianto: 2,6×, 18 ADR, 23 file di test, 7 artefatti per fase
[PROVATO] `wc -l internal/roadmap/*.md`: RM-0001 2832 righe contro 1074 (RM-0002) e 1345
(RM-0003). §22 elenca 18 ADR da chiudere (righe 2344-2369) — ~9% dell'intero registro storico
(0001-0199, 4 saltate, CLAUDE.mutabile §S). §25.5 nomina 23 file di test candidati; ogni fase
deve salvare 7 artefatti (righe 1973-1981); §25.4 elenca 24 micro-attività.
[OPINIONE] Nessuna riga è indifendibile da sola; è la somma che ha un costo di manutenzione e di
rilettura che concorre col budget del valore. La mitigazione non è tagliare invarianti ma R1+R3:
scissione e condizionamento riducono il documento vivo a ~1500 righe senza perdere una decisione.

### R8 — Undici tabelle e quote a sei dimensioni per un tetto di ~200 memorie
[PROVATO] §11.4 fissa «massimo iniziale di circa 200 memorie attive per utente» (riga 1075) e
quote «su righe e byte di evidenze, relazioni, coda, applicazioni, FTS ed embedding» (1078-1079);
§11.5 elenca 11 «tabelle minime» (1109-1121), fra cui `memory_relations` e `memory_embeddings`
che appartengono a F4/F5/F9, non al giorno 1.
[OPINIONE] State/memories/evidence/tombstones/ledger/queue/applications sono valore (oblio,
provenienza, audit dei gate a tolleranza zero). Relazioni ed embedding nelle «minime» sono
completezza: violano lo spirito di §25 (riga 2476). Le quote a 6 dimensioni si riducono a due
(righe totali, byte totali) per un corpus di 200 record. Conseguenza: schema iniziale a 8 tabelle
e una pagina di §11 in meno da testare in F1.

### R9 — §7.9: nel riconciliatore l'LLM deve essere irraggiungibile per i claim con slot
[PROVATO] §12.3 (righe 1315-1317) ammette «un modello per proporre la relazione» in generale,
mentre per claim con `slot_key`/`normalized_key` la relazione è già calcolabile senza modello:
stesso slot+valore diverso ⇒ `contradicts`; correzione esplicita ⇒ `supersedes` (§12.3 punto 5).
[OPINIONE] Manca una frase che renda il modello raggiungibile SOLO per claim privi di chiave.
Costa una riga e toglie dal perimetro di test avversariale l'intera classe «LLM propone relazione
sbagliata su dati slottati». Conseguenza: riconciliatore deterministico-prima esplicito, coerente
con §7.9.

### R10 — Tre meccanismi d'oblio dove ne bastano due
[IPOTESI] `deletion_epoch` (riga 982, «barriera minima per rebuild e restore»), tombstone per
evidenza (1046-1050) e `memory_deletion_ledger` (1119, 1098-1101) si sovrappongono: l'epoch è
derivabile come massimo del ledger, e il ledger più le tombstone coprono rebuild e restore. Non ho
trovato nel documento un caso che richieda l'epoch come contatore autonomo.
[OPINIONE] Un intero da mantenere è poco; un terzo concetto da specificare, testare (§18.1, §25.3)
e spiegare è meno poco. Conseguenza: se l'ADR di schema (§22 punto 1-2) non trova il caso, l'epoch
si definisce come vista sul ledger, non come campo.

## Che cosa toglierei / che cosa aggiungerei

**Impianto minimo che dà la maggior parte del valore** (parti di §9/§11 dentro):
§9.1 istantanea senza cache; §9.2 preferenze W2 con ambito; §9.3 con UN primo slot nominato
(contatti); §9.4-§9.5 consultazione tipizzata coi tre trigger; §9.6 quattro builtin dopo ADR;
§9.7; §11.1-§11.2; §11.3 ridotta (memories, evidence, tombstones); §11.4 con quote a due
dimensioni; §11.5 senza relations/embeddings al giorno 1; §12.1; §13 gradini 1-2 più FTS5;
§15; §16. Fasi: F0 (solo suite utente) → F1 → F2 → F3 → F5-lessicale.

**Fuori dal cammino critico** (restano nel progetto, dietro condizione contata):
tutto il ramo esperienza (§9.0 ramo destro, §11.6, §12.5, blocco Leiden di §12.6, §14.5, F7-F8)
→ roadmap propria con cancello «N gotcha non riducibili a regola statica» (R1, R4); derivatore e
riconciliatore (§12.2-§12.4, F4) → dietro contatore di lacune (R3); embedding/RRF/grafo (§13.2)
e F6/F9/F10/F11 → già opzionali per §23, nulla da cambiare.

**Aggiungerei** (righe singole, non moduli): il vincolo `detection_lexicon` in §25.1 (R6); il
nome del primo ReferenceSlot in F3 (R5); il confine deterministico-prima nel riconciliatore (R9);
la definizione del contatore di lacune in F0 (R3).

## Ciò che ho cercato e NON ho trovato

- [PROVATO] Un aggancio a `detection_lexicon`: 0 occorrenze in RM-0001 (grep), mentre il
  meccanismo esiste (`/opt/metnos/runtime/detection_lexicon.py`) ed è regola di progetto.
- [PROVATO] Il documento di design del 22/7 (`internal/design/design_user_data_subsystem_22_7.md`):
  rimosso come dichiarato in §2 (righe 234-236); ne sopravvive solo il sommario in MEMORY.md:21
  («2 tier … 4 fasi P0-P3»). La crescita 2 livelli/4 fasi → 3 archivi/12 fasi non è quindi più
  verificabile contro l'originale; RM-0001 non motiva da nessuna parte questo salto di scala.
- [PROVATO] Un criterio numerico per APRIRE F4: esistono soglie per promuovere i claim (F0,
  §19.5) ma nessuna condizione d'ingresso del derivatore; la sequenza lo attiva per posizione.
- [PROVATO] Il primo ReferenceSlot: §22 punto 6 lo lascia aperto e UC-02 è differito.
- [PROVATO] `runtime/memory/` non esiste (ls fallisce): coerente con lo stato «non implementato»
  dichiarato in testa; nessun costo sommerso da proteggere, la scissione R1 è ancora gratis.
- Una stima di sforzo per fase (giorni, righe, GPU): assente; il costo d'impianto è definito solo
  per enumerazione di attività, mai dimensionato. [OPINIONE] Per una roadmap di questa taglia è
  l'unico numero che manca davvero.


---

# Lente automatismo

Tutte le righe citate si riferiscono a
`/opt/metnos/internal/roadmap/RM-0001-conoscenza-utente-locale.md` (2832 righe,
lette per intero), salvo dove indicato altrimenti.

## Verdetto in tre righe

Il lato controllo («cosa sai di me?», correzione, oblio) rispetta la direzione
di Roberto; il lato apprendimento no: ho censito 21 punti in cui l'utente deve
agire, e nessuna fase F0-F11 rende mai l'acquisizione implicita il
comportamento predefinito del proprietario — le preferenze, l'unico tipo di
memoria che cambia le risposte, non hanno alcun percorso silenzioso in tutta la
roadmap, e l'apprendimento comportamentale termina sempre in una proposta da
approvare. Ogni gate preso da solo è motivato; è l'aggregato che nessuna delle
tre review precedenti ha contato.

## Rilievi

### R1 — Censimento: 21 azioni dell'utente perché la memoria funzioni

[PROVATO] — ogni voce con sezione e riga del documento:

| # | Azione richiesta all'utente | Sezione (riga) |
|---|---|---|
| 1 | confermare il primo riferimento (Atlas) e rispondere quando Metnos chiede | UC-02 (44-48) |
| 2 | ricordare i comandi «ricorda X» / «dimentica X» e il confine memories/tasks/texts | UC-10 (157), §9.6 (818-871) |
| 3 | porre «cosa sai di me?» / «cerca nei miei ricordi» | UC-09 (143-153), §9.6 (823-826) |
| 4 | rispondere alla domanda quando la scelta cambia materialmente il risultato | §6.1 (364) |
| 5 | rispondere alla domanda funzionale alla prima ambiguità; ri-risposte quando i candidati cambiano | §9.3 (762-766) |
| 6 | una conferma quando il comando esplicito è ambiguo | §9.5 (813-814) |
| 7 | approvare il delete «se richiesta dalla futura ADR» | §9.6 (887) |
| 8 | formulare le preferenze con marcatori persistenti riconoscibili («d'ora in poi», «sempre») | §10.1 punto 4 (934-936), §12.1 (1252-1264) |
| 9 | opt-in individuale dell'ospite per conservare preferenze | §10.2 (955-957) |
| 10 | rispondere alla disambiguazione gestita dal runtime | §12.1 (1261) |
| 11 | opt-in del proprietario per eventi impliciti reali | §12.2 (1290-1291), F4 (1806), §25.4 F4.3 (2638), §27.4 (2792) |
| 12 | richiedere il backfill (operazione amministrativa con preview e opt-in) oppure ripetere il già detto | §12.2 (1285-1287) |
| 13 | confermare il bersaglio con get_inputs per OGNI operazione mutante/outbound, anche con corrispondenza forte | §14.2 (1544-1546) |
| 14 | accettare o rifiutare le proposte proattive | §14.4 (1568-1577), F6 (1828-1837) |
| 15 | visitare la superficie di esplorazione; chiedere il dettaglio per vedere i candidati | §15.1 (1620-1630) |
| 16 | correggere il dato errato | §15.2 (1639-1647), §21 (2314) |
| 17 | dare il comando di oblio e rispondere «quale?» se il bersaglio non è esatto | §15.3 (1652-1653) |
| 18 | accendere gli interruttori per fase, utente e capacità (default tutti spenti) | §17 (1741-1742), §25.1 (2508) |
| 19 | etichettare il campione di claim derivati (confermati/corretti/rifiutati); valutazione manuale F4 | §19.1 (2144-2145), F4 (1809) |
| 20 | rispondere alla domanda sul conflitto materiale («domanda una volta») | §21 (2312), §20 (2290) |
| 21 | revisionare report/ChangeIntent e ratificare le ADR di attivazione | UC-07 (118-127), F10 (1924-1934), §22 punti 4/8/18 (2349, 2355, 2369) |

Conseguenza: il vincolo «automatica» del mandato va giudicato su questo
aggregato, non sul singolo gate; i rilievi seguenti lo fanno.

### R2 — Le preferenze tipizzate non hanno alcun percorso silenzioso, in nessuna fase

[PROVATO] — §11.2 (1004-1005): «Le preferenze tipizzate non entrano in
user_memories, non hanno embedding e non sono dedotte dal compilatore nelle
prime fasi»; nessuna fase F0-F11 (§17, 1745-1949) sblocca poi la deduzione. Il
rilevatore immediato copre solo «formulazioni ad alta precisione» con marcatori
(§12.1, 1252-1258; §10.1 punto 4, 934-936). F6 produce solo «proposte
deterministiche limitate» (1833). Quindi l'unico tipo di memoria che cambia le
risposte nasce SOLO da dichiarazione esplicita ben formulata o da proposta da
approvare — mai in silenzio. [PROVATO] La tensione è interna al documento: §21
(2306-2311) promette «per preferenze non sensibili e autoregolanti non sono
previste conferme continue», ma nessun modulo produce quelle preferenze
autoregolanti. Conseguenza: la promessa di §21 è priva di produttore; o si
aggiunge un percorso di derivazione a bassa sensibilità, o §21 va riscritto.

### R3 — Lo stato finale «apprende in silenzio» non è mai programmato

[PROVATO] — default tutti spenti (§25.1, 2508), interruttore per utente e
capacità (§17, 1741-1742), opt-in per dati reali impliciti (§12.2, 1290-1291),
ADR separata per «il primo cambiamento osservabile in produzione» (§22 punto
18, 2369). §23 (2377-2380) consente lo stato `implemented` con F0-F5: una
roadmap completata in cui il proprietario non ha mai fatto opt-in non impara
nulla implicitamente, e il documento non dichiara mai quando — a fasi promosse
— il silenzioso diventi il default. [OPINIONE] Una roadmap la cui condizione di
completamento è compatibile con «memoria che non impara mai da sola» non
soddisfa il vincolo «automatica» dichiarato dal mandato. Conseguenza: serve una
riga di stato finale (vedi sotto), non un'altra fase.

### R4 — L'opt-in del proprietario può collassare nell'installazione

[PROVATO il vincolo attuale] — §12.2 (1290-1291), §27.1 (2739) e §27.4 (2792)
esigono opt-in prima di dati reali impliciti. [PROVATO il riscontro nuovo] — la
direzione di prodotto «l'apprendimento avviene in silenzio e il controllo passa
da una domanda» è documentata fuori da RM-0001: contesto comune
`/home/roberto/.metnos/rm0001_review/comune.md` righe 25-29 e indice memorie
`~/.claude/projects/-opt-metnos/memory/MEMORY.md` («Silent-learn (NO conferme,
Roberto)», voce DESIGN dati-utente 22/7); il documento preparatorio che la
conteneva è stato rimosso (§2, 234-236) e la direzione NON è transitata in
RM-0001 come requisito. [OPINIONE] Poiché inventario e oblio esistono già da F1
(1769-1777) e l'opt-in non è fra le decisioni fissate di §22 (2320-2340), per
l'istanza mono-proprietario l'opt-in può diventare un consenso unico all'
installazione/prima attivazione, senza violare §2.8 (nulla viene dichiarato
diverso dal reale) né l'ispezionabilità (UC-09 resta il controllo). Gli ospiti
(§10.2) restano opt-in individuale. Conseguenza: un'azione una-tantum invece di
un gate che il documento oggi lascia perpetuamente aperto.

### R5 — L'apprendimento comportamentale termina sempre in una proposta

[PROVATO] — §14.4 (1568-1577): «Può proporre, non eseguire»; §21 (2313):
«inferenza debole: candidato invisibile al comportamento»; F6 misura «proposte
accettate» e «tasso di fastidio» (1835-1837). Non esiste il gradino intermedio:
applicazione silenziosa, reversibile e visibile in inventario per abitudini di
sola presentazione/lettura. [OPINIONE] Per questo sottoinsieme (nessuna
mutazione, nessun outbound, correzione a costo di una frase) la proposta
obbligatoria è più conservativa della direzione dichiarata e crea proprio il
«flusso di approvazioni» che Roberto ha escluso. Conseguenza: F6 dovrebbe
distinguere «proporre azioni» (giusto) da «applicare presentazione» (può essere
silenzioso con voce in inventario).

### R6 — La ri-domanda sui riferimenti non ha isteresi

[PROVATO] — §9.3 (764-766): «Se i candidati cambiano o il riferimento diventa
ambiguo, il sistema torna a chiedere», senza alcun limite di frequenza; i
budget di §9.5 (813-815) riguardano le consultazioni, non le domande poste
all'utente. [IPOTESI] Su domini volatili (cartelle create/rimosse, contatti
sincronizzati da provider) «i candidati cambiano» a ogni sincronizzazione: lo
stesso riferimento già confermato può rigenerare domande ripetute.
Conseguenza: serve una regola tipo «si richiede solo se il candidato confermato
è sparito o il nuovo insieme non lo contiene», altrimenti la prima domanda —
legittima — degrada in flusso.

### R7 — Niente importazione del già detto: costo di ripetizione non contato

[PROVATO] — §12.2 (1285-1287): «I turni precedenti all'attivazione non vengono
importati»; il backfill è operazione amministrativa con preview e opt-in. La
motivazione tecnica esiste (§5.4, 333-341: i TurnLog non hanno il principal
canonico). [IPOTESI] Per il proprietario sui canali già associati (Telegram
accoppiato, device binding) l'attribuzione è in pratica nota: il vincolo è più
largo del necessario e il costo — ripetere preferenze e riferimenti già
espressi in mesi d'uso — non compare in nessuna metrica di §19. Conseguenza:
almeno dichiarare il costo e offrire il backfill del proprietario come singola
azione guidata alla prima attivazione.

### R8 — «Cosa sai di me?» in chat non è garantito dalle prime fasi

[PROVATO l'ambiguità] — F2 (1783): «UI/runtime diretto prima; executor builtin
server-only soltanto dopo ADR»; l'oggetto `memories` richiede ratifica (§9.6,
828-830; §22 punto 4, 2349). [IPOTESI] Se «UI diretto» diventa il percorso
reale e l'ADR dell'executor slitta, il controllo passa dal porre una domanda in
chat al visitare una pagina di amministrazione — l'esatto rovescio della
direzione «il controllo è una domanda». Conseguenza: F2 deve garantire
esplicitamente che UC-09 e UC-10 funzionino dal canale chat, con o senza
executor ratificato.

### R9 — Che cosa NON può diventare silenzioso, e perché

[OPINIONE, su riscontri citati] Questi punti del censimento devono restare
azioni dell'utente:
- **#13** conferma su mutanti/outbound (§14.2, 1544-1546): la memoria non può
  ridurre un consenso (invariante 4, §8; minaccia «autorità implicita», §16) —
  renderla silenziosa trasformerebbe un claim in autorità;
- **#5** prima domanda su ambiguità materiale (§9.3): scegliere in silenzio
  senza fatto confermato rischia il falso successo (§2.8); una domanda una
  volta è il prezzo minimo dell'onestà;
- **#9** opt-in ospiti (§10.2): riguarda dati di terzi, non del proprietario;
- il rifiuto dei sensibili anche su richiesta esplicita (§16.1, 1697-1699;
  §10.1, 944-946): un rifiuto dichiarato è §2.8-conforme, un silenzioso
  non-salvataggio di un «ricorda» esplicito sarebbe uno scarto muto;
- **#17** il comando di oblio (§15.3): è il controllo stesso, non un attrito;
- **#21** revisione di report/ChangeIntent (F10): scritture verso autorità
  esterne (Tutor, autopath) esigono revisione umana per costruzione.

## Che cosa toglierei / che cosa aggiungerei

**Aggiungerei** (in ordine di valore):
1. una dichiarazione di stato finale in §17 o §22: «a fasi promosse, per il
   proprietario, acquisizione implicita e applicazione in lettura sono il
   default; il controllo è UC-09/UC-10» — oggi la roadmap è compatibile con un
   sistema che non impara mai da solo (R3);
2. un percorso di derivazione per preferenze a bassa sensibilità con retention
   `behavioral` e correzione a posteriori, che dia un produttore alla promessa
   di §21 (R2);
3. il collasso dell'opt-in del proprietario in un consenso unico alla prima
   attivazione, ospiti esclusi (R4);
4. in F6, il gradino «applicazione silenziosa reversibile» per abitudini di
   sola presentazione, distinto dalle proposte di azione (R5);
5. una regola di isteresi sulla ri-domanda dei riferimenti (R6) e una metrica
   aggregata del carico d'interazione in §19 (domande e conferme per settimana,
   non solo il «tasso di fastidio» delle proposte F6);
6. in F2, la garanzia che UC-09/UC-10 funzionino dal canale chat (R8).

**Toglierei**: l'«approvazione se richiesta dalla futura ADR» sul delete
esplicito (§9.6, 887). Un delete della propria memoria, con selezione esatta,
expected_version e postcondizione riletta (§25.3), ha già tutte le garanzie: il
comando esplicito È il consenso. Lasciare aperta la porta a un secondo giro di
approvazione contraddice la direzione senza aggiungere sicurezza. Nient'altro:
i gate di R9 restano.

## Ciò che ho cercato e NON ho trovato

- Una fase, gate o ADR che renda l'acquisizione implicita il default del
  proprietario: assente in §17, §22, §23 (verificato leggendo integralmente
  F0-F11 e i criteri di completamento).
- Il produttore delle preferenze «autoregolanti» promesse da §21: nessun modulo
  di §25.1 le genera; §11.2 esclude la deduzione «nelle prime fasi» senza mai
  indicare la fase che la sblocca.
- La direzione «silent-learn, il controllo è una domanda» dentro RM-0001: il
  documento preparatorio che la conteneva è stato rimosso (§2) e la frase non
  compare nella roadmap; il punto più vicino è §6.1 «effetto silenzioso quando
  il dato è non sensibile, affidabile e pertinente» (362), che però governa
  l'applicazione, non l'acquisizione.
- Una metrica del carico d'interazione complessivo in §19: esistono «ambiguità
  risolte correttamente senza domanda» (2140) e il fastidio delle sole proposte
  F6 (1835-1837), ma nessun conteggio di domande/conferme richieste all'utente
  per turno o per settimana.
- Un'osservazione equivalente nelle review precedenti: §7.1, §7.2 e §27 spingono
  tutte verso PIÙ gate (opt-in, terminalità, attestazioni); nessuna valuta il
  costo aggregato di interazione né la sua coerenza con la direzione dichiarata.


---

# Lente fondamento

Review adversariale di RM-0001, lente «fondamento» — 26/7/2026. Righe senza
percorso = `internal/roadmap/RM-0001-conoscenza-utente-locale.md`; percorsi di
codice relativi a `/opt/metnos`. Tutte le letture e i comandi citati sono stati
eseguiti da me in questa sessione.

## Verdetto in tre righe

Il fondamento regge: nessun modulo, funzione o tabella che RM-0001 dichiara
esistente è inventata — gli 11 punti d'innesto di §25.1 esistono tutti e §5 è
fedele al codice fin nei dettagli. I difetti stanno nella distanza fra «patch
minime» (r. 2503) e realtà: la catena d'identità odierna fabbrica il
proprietario («host») in almeno quattro punti che F0.3 non elenca, e tre
contratti chiave (InvocationContext, clause_span, authz_revision) non hanno
alcun produttore nei file che la tabella autorizza a toccare; tre meccanismi
esistenti e pertinenti (project_paths.json, `_RUNTIME_ARG_SOURCES`,
TutorPrincipal) non sono mai nominati — rischio di duplicazione, non di
impossibilità.

## Rilievi

### 1. Gli innesti dichiarati esistono e §5 è fedele al codice: il fondamento regge
[PROVATO] Verifica sistematica: gli 11 file/directory di §25.1 esistono tutti
(`ls`: users.py, config.py, vocab.py, agent_runtime.py, http_routes_agent.py,
channels/telegram.py, scheduler_v2/builtin_callbacks.py,
jobs/maintenance_tasks.py, playwright_sidecar/session_broker.py;
`runtime/memory/` assente, coerente con lo stato dichiarato).
`PATH_USER_DATA`/`PATH_USER_STATE`: config.py:111-113. Il pattern F2 esiste
già identico: `runtime/builtin_executor_contracts/` con 17 builtin, ciascuno
manifest.toml + manifest.toml.sig, registro `_BUILTIN_TOOL_HANDLERS` e
catalogo virtuale `_engine_v2_catalog_with_builtins`
(agent_runtime.py:5743-5773). Schema `user_prefs` con user_id/key/value/
source/updated_at (users.py:657-664) e `list_prefs` che perde source e data
(705-712), come §5.1. Turno persistito con soli `actor`/`channel` e nessun
principal (ispezione campo-per-campo dell'ultimo record di
`~/.local/share/metnos/turns/2026-07-26.jsonl`), come §5.4. FTS5
`unicode61 remove_diacritics 2` funziona sul sqlite in uso (3.45.1; comando
eseguito, match «perche» su «perché» = 1). `embed_texts`/`embed_query`:
virt/interfaces.py:34-35. Catalogo Tutor davvero firmato Ed25519
(tutor/catalog.py:4, 40). `mnestoma.py` + `DB_MNESTOMA` (config.py:148).
Registro ArgTransform con scope `exec-only` (engine/executor.py:1306 e
pipeline 1329-1371), come §9.3/F3 assume. session_broker.py = 4189 righe con
`sites_observed` (r. 43) e postcondizioni verificabili (r. 2015, 2258), come
§12.5/§14.5. vocab: azioni set/find/list/delete presenti, oggetto `memories`
assente (vocab.py:41-50). Firme cache come §5.2: `normalize_hash`
fastpath.py:228; intent sig compound-aware `verb|object` per clausola
(autopath.py:288+); «niente prefilter/affinity nella firma»
cache_validity.py:17. Conseguenza: la parte «stato corrente verificato» del
documento è affidabile; i rilievi seguenti riguardano il carico degli innesti,
non la loro esistenza.

### 2. L'innesto F2 (dispatch builtin) oggi fabbrica l'identità del proprietario, e il contratto che RM gli assegna non esiste
[PROVATO] `InvocationContext`: 0 occorrenze in runtime/ (grep), mentre §9.6
(r. 833) e §25.1 F2 (r. 2489) lo danno per contratto dei quattro handler. Il
dispatch reale è `_invoke_builtin_handler` (agent_runtime.py:5775-5807):
contesto come kwarg stringa scelto per introspezione della firma, e al
r. 5803 `kwargs["actor"] = actor or "host"` — l'assenza d'identità diventa il
PROPRIETARIO; il fallback su TypeError riprova senza kwargs (5833). I
call-site del dispatcher uniforme (`invoke_tool_by_name` +
`_invoke_builtin_handler`) fra agent_runtime e orchestration sono ≥6 (grep).
[IPOTESI] Conseguenza: se set/delete_memories entra da questo dispatch senza
ridisegnarlo, un chiamante privo di actor muta la memoria del proprietario —
l'invariante 1 è violato dal punto d'innesto stesso. Il ridisegno fail-closed
del dispatch è più di una «patch minima» e va elencato come attività F0/F2
con test cross-principal sul dispatch, non solo sui confini di canale.

### 3. Tutte le fabbriche d'identità esistenti falliscono APERTE verso host — e il precedente TutorPrincipal non è citato
[PROVATO] actor_resolver.py: «non pairato: trattalo come host» e fallback
host ai r. 19, 48, 50, 53; http_routes_agent.py:309-314 `_resolve_actor` →
`request.get("device_id") or "host"`; tutor_boundary.py:47-59
`http_principal` → `user_id=str(device_id or actor or "http-user")`,
`actor=str(actor or "host")`. F0.3 (r. 2618-2619) implementa PrincipalContext
«nei due confini di canale», ma la fabbricazione avviene anche dentro il
runtime (rilievo 2) e nella factory Tutor. `TutorPrincipal`/`tutor_boundary`:
0 occorrenze in RM-0001 (grep) — eppure è l'unico principal oggi costruito ai
confini di canale in produzione, con la regola giusta già scritta («Map only
middleware-authenticated HTTP fields; body cannot elevate»,
tutor_boundary.py:49). [OPINIONE] Conseguenza: senza una riga che dica se
PrincipalContext sussume TutorPrincipal, F0 crea il secondo tipo di principal
della stessa casa; e l'inversione fail-open→fail-closed è un cambiamento di
semantica trasversale, da censire come attività con i suoi punti.

### 4. UC-02 è differito in attesa di un provider che esiste già: runtime/project_paths.json
[PROVATO] Righe 51-52: «Il caso Atlas/path resta differito finché un provider
deterministico di progetti o percorsi non produce gli ID candidati». Il
provider c'è: `runtime/project_paths.json` (nome progetto → code_root/
data_root + descrizione), reso da `_render_project_paths_block`
(agent_runtime.py:999-1031, ADR 0079) nel prompt PLANNER legacy
(prompts/it/planner/_footer.j2:27). Non è cablato nel proposer v3 (grep
`project_paths` in runtime/engine/ e in engine_proposer.j2 = 0) e RM-0001 non
lo nomina (grep = 0). Conseguenza: F3 e l'ADR di §22 punto 6 rischiano di
reinventare un registro esistente; il caso guida del documento aspetta una
cosa che il codebase possiede, da promuovere da blocco-prompt legacy a
provider di ID per il ReferenceSlot.

### 5. clause_span non ha sorgente: l'intent layer manca dalla tabella dei file da toccare
[PROVATO] `ExplicitMemoryCommand` porta `clause_span` (r. 2550) e la prova
della clausola top-level (invariante 19, r. 558-560). Nel motore le clausole
esistono solo come coppie (verb, object) senza posizione nel testo:
cache_validity.py:131-141; intent extractor → `actions=[{verb,object}]`.
`clause_span`: 0 occorrenze in runtime/ (grep). La tabella §25.1
(r. 2503-2515) non elenca né l'intent extractor né engine/dispatch.
[IPOTESI] Conseguenza: F0.4 (`commands.py`) o rifà una segmentazione propria
delle clausole — un secondo segmentatore, complementare al rilievo R6 della
lente semplicità che riguarda il lessico, qui si tratta degli span — oppure
l'estensione dell'intent layer va aggiunta alla tabella; oggi il contratto
non è producibile dai punti d'innesto elencati.

### 6. «Non ha capability filesystem sullo store» vale solo per subprocess: per un builtin in-process il meccanismo non esiste
[PROVATO] Le capability sono applicate dal sandbox dei subprocess:
`effective_capabilities` è consumata da runtime/sandbox.py (r. 193, 606, 722)
ed executor_standard.py; i builtin in-process condividono soltanto «signed
execution policy, central scheduler and assigned worker budget»
(agent_runtime.py:5813-5814) e girano nel processo server con la sua piena
autorità. [IPOTESI] Conseguenza: la garanzia di §9.6 (r. 833-834) per
l'adapter memories non è una proprietà del meccanismo capability ma disciplina
d'import; va riscritta come tale e il test architetturale di §27.5 va promosso
a gate di F2 (i quattro handler non importano sqlite3/user_store).

### 7. Il carico assegnato a users.py eccede la patch ammessa; authz_revision non ha alcun substrato
[PROVATO] §11.1 (r. 972-973) esige `prefs_revision` «nella stessa transazione
di set/delete preference», ma `_open_db` (users.py:81-87) apre in autocommit
(`isolation_level=None`), senza WAL né busy_timeout, ed esegue
`executescript(SCHEMA)` a ogni apertura; le API prefs sono keyed su stringa
`user_id_or_name` (set_pref, r. 668) — coerente con §5.1, che lo dichiara. La
patch ammessa per users.py è però solo «binding fra principal verificato e
account, senza API memoria» (r. 2507). `authz` in qualunque forma: 0
occorrenze in runtime/ (grep) — il campo `authz_revision` di PrincipalContext
(r. 725, 1210, 2546) non ha produttore; la revoca oggi è `verified_at=NULL`
(users.py:435-438), senza contatore. Conseguenza: transazione+revisione,
disciplina concorrente di users.db e contatore di revoca sono attività reali
di F0/F1 da aggiungere alla tabella, o il contratto §9.1/§11.8 nasce con campi
vuoti.

### 8. Il veicolo runtime-owned esiste già (`_RUNTIME_ARG_SOURCES`) ma ha sorgenti senza contesto, e RM non lo nomina
[PROVATO] Il meccanismo generale per arg runtime-owned che «il planner non
può valorizzare» e che «sovrascrive sempre, anche su resume» esiste:
`_RUNTIME_ARG_SOURCES` + `_fill_runtime_sourced_args`
(agent_runtime.py:3602-3647, ADR 0199). Le sorgenti però sono lambda SENZA
argomenti (es. r. 3612): non possono trasportare principal, turn_id o comando
del turno corrente. 0 occorrenze in RM-0001 (grep). [OPINIONE] Conseguenza: è
il precedente naturale per «usano il principale fornito dal runtime, mai un
user_id prodotto dal modello» (§9.6): F2 dovrebbe dichiarare se estende
questo registro con sorgenti a contesto di turno o se introduce un canale
parallelo — la seconda via duplicherebbe un meccanismo firmato appena
consolidato.

## Che cosa toglierei / che cosa aggiungerei

**Toglierei.** La frase «non ha capability filesystem sullo store»
(r. 833-834) nella forma attuale: sostituirla con il vincolo verificabile —
nessun import dello store nei quattro handler, test architetturale come gate
F2 (rilievo 6). [OPINIONE]

**Aggiungerei.** [OPINIONE]
1. Alla tabella §25.1: l'intent layer (produzione di `clause_span`), il
   dispatch builtin (`_invoke_builtin_handler` fail-closed), le fabbriche
   actor (actor_resolver, `_resolve_actor`, tutor_boundary) e la transazione
   prefs_revision+WAL in users.py (rilievi 2, 3, 5, 7).
2. In §9.3/F3: il censimento dei provider deterministici già esistenti —
   `project_paths.json` e i resolver ArgTransform già in pipeline
   (mail_account, calendar, self_recipient; engine/executor.py:1329-1371) —
   come candidati concreti del primo ReferenceSlot (rilievo 4; complementare
   alla scelta del primo slot chiesta dalla lente semplicità R5).
3. Una riga su TutorPrincipal: sussunzione in PrincipalContext o coesistenza
   dichiarata (rilievo 3).
4. Una riga su `_RUNTIME_ARG_SOURCES` come veicolo del principal/command
   verso i builtin (rilievo 8).

## Ciò che ho cercato e NON ho trovato

- [PROVATO] `InvocationContext`, `clause_span`, `authz` (qualunque forma):
  0 occorrenze in runtime/ (grep, 26/7).
- [PROVATO] `project_paths`, `TutorPrincipal`/`tutor_boundary`,
  `_RUNTIME_ARG_SOURCES`: 0 occorrenze in RM-0001 (grep).
- [PROVATO] WAL o busy_timeout in users.py: assenti (grep).
- [PROVATO] Un meccanismo di contenimento filesystem per codice in-process:
  assente — sandbox.py governa i soli subprocess.
- [PROVATO] Un innesto citato da RM-0001 come esistente e inesistente nel
  codice: NON trovato — dopo verifica sistematica di §5, §9.3, §9.6, §11.8,
  §12.5, §14.5-14.6 e §25.1 nessun percorso, funzione o meccanismo dichiarato
  «esistente» è risultato inventato. Il difetto che questa lente doveva
  soprattutto cercare non c'è.
- Non ho ripetuto le verifiche già a verbale nella lente danno (ArgTransform
  a executor.py:1306, sites_observed a session_broker.py:43, bypass LAN in
  http_auth.py, 117 `_msg(`): dove mi servivano le ho rieseguite in proprio
  (117 occorrenze di `_msg(` in orchestration.py riconfermate con grep).


---

# Lente danno

## Verdetto in tre righe

RM-0001 ha già assorbito bene gli attacchi frontali (origine esterna, planner, cache); i danni residui stanno nei confini fra i suoi stessi compartimenti: la coda di compilazione che riscrive un oblio, il registro di cancellazione che vive nel database che dovrebbe sopravvivergli, la regola lettura/mutazione applicata al passo invece che al piano, i due archivi di preferenze che non si vedono a vicenda. [OPINIONE] Le contromisure di §10 e §16 sono in maggioranza meccanismi con punto d'innesto; quattro (impronta degli elementi dimenticati, «verifica dell'oggetto», profilo minore, isolamento ospite-da-proprietario) sono prosa senza substrato. F0 può partire, ma questi buchi vanno chiusi in F0-F1, non scoperti in F4.

## Rilievi

### 1. L'oblio è riscritto dalla coda di compilazione, e l'«impronta» che dovrebbe impedirlo contraddice una decisione fissata

[PROVATO] La tombstone protegge solo localizzatori già noti come evidenza: `memory_evidence_tombstones` conserva `source_kind, source_id, span_id` (RM-0001 r. 1045-1047) e il compilatore «controlla le tombstone delle evidenze» (r. 1386). Ma un MemorySourceEvent già accodato in `memory_compile_queue` (r. 1120) prima del «dimentica X», con uno span diverso che riafferma lo stesso contenuto, passa il controllo: né §15.3 (r. 1649-1661) né il flusso delete di §25.3 (r. 2592-2597) toccano la coda. §10.1 punto 8 promette di controllare «l'impronta degli elementi dimenticati prima della promozione» (r. 940), ma §11.3 vieta content_hash come tombstone (r. 1038) e §27.3 ha respinto la barriera a hash del claim (r. 2775-2780): l'«impronta» non ha alcun meccanismo compatibile con le decisioni fissate.
[IPOTESI] Conseguenza: l'utente dimentica «vivo a Roma» la sera; il derivatore notturno compila un evento accodato il pomeriggio e il claim riappare. La prova 18.3 §9 (r. 2028) esiste come intento, ma il design corrente la fa fallire per costruzione. Violazione diretta di UC-10 («nessuna ricomparsa dopo riavvio», r. 166-167).

### 2. Il deletion ledger vive dentro il database a cui deve sopravvivere

[PROVATO] `memory_deletion_ledger` è una tabella di `user_memory.sqlite` (r. 1119). Il contratto di restore richiede che si «conserva o riapplica il deletion ledger più recente del backup» (r. 1098-1099), e la prova 18.3 §23 testa «restore di backup precedente alla deletion_epoch» (r. 2042). Ma se il database viene ripristinato perché perso o corrotto — lo scenario tipico di un restore — il ledger recente è perso insieme a esso: il backup contiene il ledger vecchio, e nessuna riga di RM-0001 (§11.4, §11.5, §15.3, §25.3, §22) prevede una copia del ledger fuori dal DB.
[IPOTESI] Conseguenza: ogni memoria dimenticata dopo l'ultimo backup risorge a un restore da disco guasto, con dichiarazione impossibile da rispettare. Il test 18.3 §23 è superabile solo aggiungendo un meccanismo (ledger esportato altrove, o append-log separato) che il documento non progetta: oggi è buona intenzione.

### 3. «Risoluzione silenziosa solo per letture» è definita sul passo, non sul piano: una lettura risolta dalla memoria alimenta l'azione confermata

[PROVATO] §14.2: «Nella prima versione la risoluzione silenziosa è ammessa solo per letture. Per operazioni mutanti o outbound il bersaglio viene mostrato o confermato» (r. 1544-1546). L'unità («operazione») non è definita. Nel motore reale i passi si concatenano: `from_step` e placeholder sono il contratto dei piani (CLAUDE.mutabile.md §4.1) e i verbi d'azione seguono read/find (§4.3). §18.3 §20 copre solo `find_memories` concatenato a un mutante (r. 2039), non il ReferenceSlot; §27.1 idem (terminalità dell'executor, non della risoluzione).
[IPOTESI] Conseguenza: «comprimi i file di Atlas e mandali a Marco» — la risoluzione di «Atlas» avviene in un passo find (lettura, quindi silenziosa), il consenso mostra il destinatario ma non la sorgente scelta dalla memoria. Un riferimento stantio o mal confermato fa uscire i file sbagliati attraverso un consenso formalmente rispettato: la memoria ha spostato la decisione senza mai toccare un'operazione «mutante».

### 4. L'origine esterna non deve scrivere il claim: le basta fabbricare i candidati fra cui la memoria sceglie

[PROVATO] §10 vieta la scrittura da `external` (r. 922) e §16 la contromisura è «origine external esclusa prima dell'LLM» (r. 1671). Ma la classe `interaction` («scelta in un dialogo di disambiguazione», r. 919) è «attiva nel medesimo ambito», e i candidati della disambiguazione provengono dal motore, cioè da dati che un terzo può creare (cartelle su share, contatti sincronizzati, nomi di calendario). §18.3 §7 («due riferimenti con lo stesso nome», r. 2026) tratta l'omonimia accidentale, non la fabbricazione ostile; §14.2 rassicura che la memoria «non può inventare path» (r. 1535) — non serve: il path lo pianta l'attaccante.
[IPOTESI] Conseguenza: chi può creare una cartella `atlas` sul NAS montato fa comparire il proprio path fra i candidati; una conferma distratta crea un riferimento di classe interaction con precedenza 5 (r. 1465), e da lì ogni lettura «di Atlas» — inclusa quella del rilievo 3 — è silenziosamente instradata su contenuto controllato dal terzo. Manca una prova avversariale «candidato fabbricato da origine esterna» in §18.3.

### 5. §12.4 esige un esito che MemorySourceEvent non trasporta e che il compilatore non può ricostruire: la ripetizione-per-fallimento diventa abitudine

[PROVATO] §12.4 ammette un candidato comportamentale solo se «l'operazione ha avuto esito semanticamente riuscito» e «non è una ripetizione causata da errore o ripresa tecnica» (r. 1325-1327). Ma i campi di `MemorySourceEvent` sono «ID, principal, turno, span, classe fonte, excerpt, observed_at, digest e versione dello scrubber» (r. 2556-2558) — nessun esito — e «il compilatore non ricostruisce questi dati dai log» (r. 2559); l'invariante 22 vieta la scansione dei TurnLog (r. 565-567). Le tre regole insieme rendono §12.4 inapplicabile così com'è scritta.
[IPOTESI] Conseguenza §2.8 al secondo ordine: l'utente ripete «manda il rapporto a X» tre giorni di fila perché la consegna fallisce in silenzio; il compilatore, cieco sull'esito, vede tre episodi indipendenti riusciti e produce il candidato «manda sempre il rapporto a X», che F6 proporrà. Un fallimento del sistema viene ricompilato come preferenza dell'utente. Serve il campo esito nell'evento (dal CallbackOutcome/esito semantico del turno) fissato in F0.

### 6. I set espliciti scavalcano il reconciler: finestra di co-attivazione contraddittoria che nessuno richiude

[PROVATO] La riconciliazione obbligatoria vale per i claim `derived`: «Nessun claim derived passa da candidato ad attivo con la sola estrazione» (r. 1298-1299). Un set esplicito attiva subito: la validazione conflitti avviene al passo 4 del flusso (r. 879) quando l'embedding non esiste ancora (passo 8, r. 882; «il successo del set non promette l'embedding», r. 895-896), quindi con soli normalized_key e FTS. §12.3.7 riesamina «i conflitti» esistenti (r. 1312-1313), non le coppie attive mai marcate; nessuna riga prevede una ripassata anti-contraddizione quando l'indice diventa ready.
[IPOTESI] Conseguenza: «ricorda che odio i riassunti prolissi» convive attivo con «preferisco risposte dettagliate» (parafrasi: chiavi e lessico diversi); §13 passo 4 esclude solo i conflitti *marcati* (r. 1424), quindi il recupero applica l'uno o l'altro secondo la query. La contromisura di §16 («stato conflict, relazione esplicita, astensione», r. 1678) presuppone una rilevazione che il design in questo punto non esegue. §7.1-M4 aveva visto il buco solo sul lato derived.

### 7. W2 e memoria libera sono reciprocamente cieche: il conflitto fra i due archivi non ha giurisdizione

[PROVATO] Le preferenze tipizzate «non entrano in user_memories, non hanno embedding e non sono dedotte dal compilatore» (r. 1004-1005); `set_memories` rifiuta `kind` preference (r. 855-856); il reconciler confronta solo «nello stesso ambito e per lo stesso principal» dentro lo store memoria (r. 1302-1303). Nessuna sezione — §12.3, §13, §16 — definisce la rilevazione di una contraddizione W2↔memoria libera.
[IPOTESI] Conseguenza: W2 ha `lang=en`; l'utente detta «ricorda che scrivo le email sempre in italiano» (fact/habit legittimo, non una preference). Due verità attive in archivi che non si vedono: la presentazione segue W2, la disambiguazione segue la memoria, e una correzione dell'utente su un archivio lascia intatto l'altro — la «correzione» di §15.2 incrementa una sola revisione (r. 1647). Il mandato dei due store è fissato in §22 e non lo contesto; manca però un passo di riconciliazione cross-archivio o almeno un esito `conflict` visibile in UC-09.

### 8. «Profilo indicato come minore» non ha substrato: contromisura di prosa

[PROVATO] §10.2: «Per un profilo indicato come minore la derivazione implicita resta vietata» (r. 957-958). Ma `runtime/users.py:43` definisce `ROLES = ("host", "guest")` e lo schema utenti (r. 52 e seguenti) non ha alcun campo per marcare un minore; nessuna delle 18 ADR aperte in §22 (r. 2342-2369) prevede l'introduzione del marcatore — il punto 8 copre la UX degli «ospiti verificati», non i minori.
[IPOTESI] Conseguenza: in una casa il divieto è inapplicabile perché indecidibile: un minore con dispositivo associato è un principal verificato indistinguibile da un adulto, e l'opt-in individuale degli ospiti (r. 955-956) non ha alcun controllo di età. O si aggiunge il campo e chi può impostarlo (F0, schema utenti), o la frase va riscritta come limite dichiarato, non come protezione.

### 9. L'isolamento dell'ospite «dal proprietario» è una promessa d'interfaccia, non un meccanismo a riposo

[PROVATO] L'invariante 18 promette che il profilo ospite «resta isolato dal proprietario» (r. 555-557) e §10.2 che «il proprietario non ottiene automaticamente lettura del profilo di un ospite» (r. 960-961). La protezione a riposo è «permessi 0600 sul database» (r. 1082); la cifratura è esplicitamente una ADR aperta (§22 punto 1, r. 2344-2345). Il proprietario amministra la macchina e il servizio: 0600 lo protegge dagli altri utenti Unix, non da lui.
[OPINIONE] Il documento è onesto altrove («locale non significa automaticamente privato», r. 405-406) ma qui promette all'ospite, al momento dell'opt-in, un isolamento che l'architettura non può garantire contro il proprio amministratore. La UX di opt-in (§22 punto 8) deve dichiarare il limite, o l'opt-in raccoglie un consenso male informato.

### 10. «Verifica dell'oggetto» controlla l'esistenza, non la continuità d'identità: il riferimento a identificatore riciclato risolve in silenzio l'oggetto sbagliato

[PROVATO] F3 invalida «quando l'oggetto non esiste o i candidati cambiano» (r. 1797); la riga di §16 «memoria obsoleta» offre «scadenza, recenza, verifica dell'oggetto» (r. 1680) senza mai definire come si verifica che l'oggetto sia *lo stesso* e non un omonimo subentrato al medesimo identificatore.
[IPOTESI] Conseguenza: `/opt/atlas` viene cancellato e il path riusato per un progetto diverso; il provider deterministico produce lo stesso path, il riferimento confermato combacia, la risoluzione silenziosa (lettura) porta dati del progetto sbagliato in una risposta su cui l'utente decide. Servirebbe un'ancora di continuità (per file: inode/ctime; per contatti: id stabile del registro) memorizzata con il riferimento e confrontata dal provider — è un meccanismo a costo basso che F3 può fissare subito.

### 11. La «famiglia dichiarata dall'executor» è un canale di trasferimento dell'esperienza fra siti che il threat model non elenca

[PROVATO] §11.6: «Una famiglia dichiarata dall'executor oppure un fingerprint HMAC locale può partecipare al matching» (r. 1170-1171); la granularità della famiglia la decide l'executor. Il ciclo act_sites espone il modello al contenuto di pagina (`session_broker.py:43` importa `sites_observed`; codici osservativi derivati dalla navigazione). §16 «esperienza avvelenata» copre solo l'auto-certificazione (r. 1689) e 18.3 §22 idem (r. 2041): nessuna prova copre un sito ostile che, con postcondizioni genuinamente riuscite, addestra una strategia per una famiglia dichiarata larga.
[IPOTESI] Conseguenza: il sito A (ostile) accumula successi «chiudi il pannello e prosegui» per la famiglia «pannello-modale»; su un sito B benigno della stessa famiglia l'hint orienta la scelta — dentro le strategie ammesse, quindi bounded, ma è esattamente «una memoria che sposta una decisione» con provenienza avvelenata legittimamente. Mitigazione: la famiglia non basta mai da sola al matching live; richiedere congiunzione con fingerprint HMAC dell'ambiente.

### 12. get_inputs con il candidato della memoria preselezionato: la conferma diventa nominale

[OPINIONE] §14.2 chiede che per mutanti/outbound il bersaglio sia «mostrato o confermato con get_inputs» (r. 1545-1546), ma non vieta che la scelta della memoria arrivi come default preselezionato. Con l'assuefazione alla conferma, un default sbagliato passa: il valore protettivo del dialogo dipende da un dettaglio di UX che il documento non fissa. Basta una riga in F3: nel dialogo di conferma il candidato suggerito dalla memoria è evidenziato ma mai preselezionato.

## Che cosa toglierei / che cosa aggiungerei

**Toglierei.** (a) §10.1 punto 8 («impronta degli elementi dimenticati») nella forma attuale: o riceve un meccanismo compatibile con §27.3 o è una promessa falsa (rilievo 1). (b) La frase «profilo indicato come minore» finché non esiste il campo che la rende decidibile (rilievo 8). (c) L'ambiguità «operazioni» in §14.2: sostituire con «piani che contengono almeno un passo mutante od outbound» (rilievo 3).

**Aggiungerei.** (1) Purge della `memory_compile_queue` per gli eventi il cui contenuto sostiene il claim dimenticato, o quantomeno ri-esame anti-ricreazione dei candidati compilati dopo un delete, dentro §15.3 (rilievo 1). (2) Copia append-only del deletion ledger fuori da `user_memory.sqlite`, applicata dal percorso di restore (rilievo 2). (3) Campo esito-turno in `MemorySourceEvent`, alimentato al confine autenticato (rilievo 5). (4) Ripassata del reconciler sulle coppie attive quando `index_state` passa a ready, anche per claim espliciti (rilievo 6). (5) Prova avversariale 18.3: «candidato di disambiguazione fabbricato da contenuto esterno» (rilievo 4). (6) Ancora di continuità d'identità nel contratto del ReferenceSlot (rilievo 10). (7) Dichiarazione del limite di riservatezza verso l'amministratore nella UX di opt-in ospite (rilievo 9).

## Ciò che ho cercato e NON ho trovato

- [PROVATO] Ho verificato i richiami del documento al codice esistente: `normalize_hash` in `runtime/engine/fastpath.py:228`; la classe `ArgTransform` e il suo registro in `runtime/engine/executor.py:1306` (il «registro ArgTransform esistente» di §9.3 esiste davvero); `sites_observed` in `runtime/playwright_sidecar/session_broker.py:43`; il bypass LAN→user in `runtime/http_auth.py:9`; 117 occorrenze di `_msg(` in `runtime/orchestration.py` (`grep -c "_msg(" `), esattamente il numero dichiarato a §5.3. Nessun richiamo inventato.
- [PROVATO] Ho cercato un canale vocale (whisper) nel runtime per l'attribuzione di parlato ambientale al principal: non esiste (`grep -rln whisper runtime/` vuoto); lo scenario resta futuro, non un buco attuale.
- [PROVATO] Ho cercato un buco «chat di gruppo Telegram = più persone, un principal»: il pairing è per `channel+sender_id` (`runtime/channels/daemon.py:16`), quindi i mittenti restano distinti. Non trovato.
- [OPINIONE] Ho cercato un falso successo §2.8 nel percorso di recupero a metà: non l'ho trovato — `committed_postcondition_unknown` (r. 2596-2597), esiti parziali senza finta atomicità (r. 988-989), `index_state` visibile e fallimento onesto dei comandi espliciti (§6.6) sono fra le parti più solide del documento.
- [OPINIONE] Non ho trovato ripetizioni dei rilievi già chiusi in §7.1, §7.2 e §27: i dodici rilievi sopra attaccano confini che quelle review non hanno toccato (coda vs oblio, ledger vs restore, passo vs piano, cross-store, substrato minori).


---

# Lente misura

Percorsi citati: `internal/roadmap/RM-0001-conoscenza-utente-locale.md` (di
seguito «RM», righe della revisione del 26/7/2026) e
`/home/roberto/.metnos/rm0001_review/comune.md`.

## Verdetto in tre righe

§18 è in gran parte falsificabile (guardie fail-closed, equivalenza §18.4,
avversariali §18.3); §19 lo è solo a metà: l'apparato causale serio (replay
accoppiati, holdout, effetto minimo preregistrato) esiste SOLO per gli hint
d'esperienza, mentre il beneficio della memoria UTENTE — il cuore del progetto —
si promuove su corpus offline costruito dagli stessi autori, con almeno quattro
metriche di §19.1 gonfiabili senza dare valore all'utente. [OPINIONE fondata
sui rilievi 1-4 sotto, ciascuno con riscontro]

## Rilievi

### 1. La prova causale esiste solo per gli hint; F5 si promuove senza braccio di confronto
[PROVATO] La «Prova causale degli hint» (RM r. 2254-2262: replay accoppiati
hint_off/hint_on, holdout live, limite inferiore dell'intervallo sopra
l'effetto minimo) è dichiarata soltanto per l'esperienza executor. L'uscita di
F5 per la memoria utente è «beneficio netto sul corpus personale» (r. 1825-1826):
nessun replay on/off, nessuna statistica, nessuna definizione di «netto». F0
preregistra «metodo accoppiato» (r. 1756-1757) senza dire a quali fasi si
applica.
Conseguenza: si può promuovere F5 con Recall@3 alto su un corpus che gli stessi
autori hanno etichettato, senza aver mai mostrato UN turno reale migliorato.
Il numero sale perché il corpus definisce insieme che cosa ricordare e che cosa
conta come successo: un circolo, non una misura. Questo è dentro il recepimento
di §27.1 («il benchmark offline non prova il beneficio operativo») ma il
recepimento è stato cablato solo su F7/F8.

### 2. «Riferimenti riusati con esito verificato» è cieco al referente sbagliato
[PROVATO] Metrica a r. 2143. F3 è read-only (r. 1799-1801) e la risoluzione
silenziosa è ammessa solo per letture (r. 1544-1547). L'unica verifica
disponibile in lettura è la postcondizione dell'executor, che riesce anche sul
referente SBAGLIATO: memoria risolve «Atlas» su /opt/atlas-vecchio, find_files
riesce, la metrica conta +1 «riuso con esito verificato», l'utente riceve in
silenzio i risultati del progetto sbagliato.
Conseguenza: il numero sale proprio nel modo di guasto principale di F3, che
per costruzione è invisibile (niente conferma sulle letture). Manca un oracolo
del referente atteso indipendente dalla postcondizione.

### 3. «Riduzione delle correzioni dell'utente» è accoppiata al rilevamento del danno
[PROVATO il testo; IPOTESI il meccanismo] Metrica a r. 2141; il «tasso di
applicazione fuori contesto» (r. 2150) in esercizio si osserva quasi solo
attraverso le correzioni dell'utente. Meccanismo di gonfiaggio: se gli errori
diventano meno visibili (rilievo 2) o l'utente si adatta/si arrende, le
correzioni calano E il danno rilevato cala — beneficio e danno «migliorano»
insieme mentre il valore scende. Su N=1 utente è una serie temporale non
controllata: cala anche se cambia solo il mix di lavoro della settimana.
Conseguenza: inutilizzabile come metrica di promozione; al più telemetria.

### 4. La partizione «profilo irrilevante» di §18.4 è prodotta dal sistema sotto test
[PROVATO] §18.4 esige identità piano/argomenti «per richieste nelle quali il
profilo è irrilevante» (r. 2047-2048), ma l'idoneità di un turno la decide il
trigger tipizzato del sistema stesso (§9.5, r. 800-802). Un turno classificato
non idoneo non consulta la memoria: l'uguaglianza vale per costruzione. Un
turno erroneamente ritenuto idoneo esce dall'insieme a uguaglianza stretta per
autogiudizio del sistema.
Conseguenza: allargare il trigger riduce l'insieme sottoposto al test più
severo. La partizione idoneo/non-idoneo va congelata NEL corpus (etichetta per
turno), non derivata a runtime dal sistema misurato.

### 5. L'esposizione N dei gate a zero non è definita per la scrittura da fonte esterna
[PROVATO] Il rapporto con zero eventi dichiara il limite 3/N (r. 2277-2279),
ma per «scrittura da contenuto esterno» N non è definito: il filtro §10.1
scarta prima di ogni estrazione e «in caso di dubbio non scrive» (r. 949) senza
obbligo di registrare l'evento scartato; `memory_applications` (r. 1123-1125)
copre le consultazioni, non i drop pre-coda.
Conseguenza: con N = turni totali il 3/N è lusinghiero di ordini di grandezza
rispetto a N = tentativi reali di scrittura esterna. La scelta del denominatore
cambia il claim di sicurezza e oggi è libera.

### 6. «Claim derivati confermati dall'utente» si gonfia coi claim-pappagallo
[PROVATO la metrica (r. 2144-2145); IPOTESI il meccanismo] Un derivatore tarato
su riformulazioni quasi letterali («l'utente ha un progetto chiamato Atlas» da
«il progetto Atlas») ottiene conferme vicine al 100% con valore nullo: il claim
non risolve mai nulla che il testo del turno non risolvesse già. La metrica
misura fedeltà di estrazione, non utilità; manca il legame claim→uso a valle
(quante volte un claim confermato ha poi risolto un'ambiguità). Nota anche il
costo: per misurare bisogna interrogare Roberto, in tensione con «automatica»
(comune.md, scopo dichiarato).

### 7. F8: la disgiunzione «oppure riduzione passi» è una scappatoia
[PROVATO] Uscita F8: delta di successo sopra l'effetto minimo «oppure riduzione
passi senza regressioni» (r. 1894-1896). Chi scrive gli strategy_code controlla
direttamente il conteggio passi: una strategia che salta una riosservazione
riduce i passi per costruzione, senza beneficio visibile all'utente. Se il
delta di successo fallisce, si promuove sul surrogato. §27.4 (r. 2796) non ha
chiuso questo ramo.
Conseguenza: rendere la riduzione passi criterio secondario, mai sufficiente.

### 8. Nel simulatore F8 gotcha e rimedio sono scritti dalla stessa mano
[IPOTESI ancorata a r. 1870-1873 e 2255-2257] Se lo scenario simulato pianta
l'ostacolo (pannello da chiudere) e la allowlist contiene esattamente il suo
rimedio, il replay accoppiato vince per costruzione: prova il collaudo
dell'impianto, non che i gotcha reali siano apprendibili. Il gate live c'è
(§27.4), ma il rapporto di F8 dovrebbe dichiarare quali scenari derivano dagli
strategy_code stessi ed escluderli dall'evidenza di valore.

### 9. Metriche vere-per-costruzione elencate come metriche di qualità
[PROVATO] «numero di consultazioni per turno idoneo, massimo uno» (r. 2167) è
imposto da §9.5 (r. 800-802); «chiamate LLM aggiunte al turno non idoneo,
obiettivo zero» (r. 2166) è vero per definizione di idoneità (rilievo 4). Sono
guardie: non possono fallire se il codice è conforme.
Conseguenza: gonfiano il quadro dei «numeri verdi» in §19.3; spostarle fra i
property test di §18.7, dove appartengono.

### 10. «Metrica primaria» al singolare contro ~30 metriche senza mappa per fase
[PROVATO] F0 preregistra UNA metrica primaria e due harm metrics (r. 1756-1757);
§19 elenca circa trenta misure e le soglie si fissano «in F0 sulla baseline
reale» (r. 2173-2175), cioè oggi §19 non contiene alcun numero falsificabile.
Senza una mappa fase→metrica primaria dichiarata prima dei dati, la promozione
può scegliere a posteriori quale delle trenta chiamare «beneficio netto»
(sentieri che si biforcano).

### 11. «Doppia annotazione» senza requisito di indipendenza
[PROVATO il testo (r. 2214); IPOTESI il rischio] In un progetto a un utente i
due annotatori saranno con ogni probabilità due agenti LLM correlati o Roberto
due volte. La doppia annotazione senza indipendenza dichiarata misura la
coerenza dell'annotatore, non la verità dell'etichetta. Prescrivere: tasso di
disaccordo riportato e dichiarazione della natura dei due annotatori.

## Che cosa toglierei / che cosa aggiungerei

**Toglierei:**
- da §19.3 le due metriche vere-per-costruzione (rilievo 9) → §18.7;
- da §19.1 «riduzione delle correzioni dell'utente» come criterio di
  promozione (resta telemetria) (rilievo 3);
- da F8 il ramo «oppure riduzione passi» come condizione sufficiente
  (rilievo 7).

**Aggiungerei — la prova minima «la memoria serve» vs «la memoria è presente»**
[OPINIONE di progetto, coerente con le baseline già in RM r. 2219-2223]:
- **Turni:** ~30 coppie (turno-sorgente, turno-beneficio) separate da almeno
  una sessione, IT+EN, su tre tipi (preferenza persistente, riferimento,
  decisione); più ~30 turni-esca a profilo irrilevante etichettati NEL corpus
  (rilievo 4); più ~10 turni-trappola in cui il referente è cambiato fra
  sorgente e beneficio (misurano il danno da memoria stantia).
- **Tre bracci sullo stesso replay a seed fisso:** (a) memoria spenta;
  (b) memoria accesa; (c) baseline lineare = dizionario ultimo-valore-per-slot
  riempito deterministicamente dal turno-sorgente, senza compiler né retrieval
  (è la «baseline lineare e onesta» richiesta; RM la elenca a r. 2221 ma non
  la lega ad alcuna regola di promozione).
- **Misura primaria per coppia:** interazioni utente necessarie all'esito
  corretto, dove «corretto» è giudicato contro il referente/valore atteso
  scritto nel corpus, MAI contro la postcondizione dell'executor (rilievo 2).
  Delta accoppiato (b)−(a) e (b)−(c) con intervallo e effetto minimo
  preregistrati, come già fatto per gli hint.
- **Regola di promozione:** (b) deve battere (c), non solo (a). Battere
  «nessuna memoria» è facile; il Memory Compiler si giustifica solo oltre il
  dizionario. Sulle esche: identità §18.4 stretta. Sulle trappole: risoluzione
  silenziosa errata pesata più del beneficio (coerente con la matrice di costo
  di §19.5).
- Inoltre: definizione del denominatore N per ogni gate a zero (rilievo 5) e
  log obbligatorio, con reason code, di ogni scarto del filtro §10.1.

**Riproducibilità dichiarata:** onesta e già qualificata (snapshot congelato,
niente identità byte, corpus con digest e seed — r. 1448-1454, 2680-2683);
nessun overclaim residuo trovato dopo le correzioni C2/M2-M3 di §7.1. [PROVATO
per assenza nei punti citati] La lacuna non è la ripetibilità del percorso ma
l'assenza del confronto che renda il percorso significativo (rilievo 1).

**Coerenza con §18.3:** i 24 casi sono coerenti con i gate di §19.5 ma quasi
tutti falliscono solo in presenza di un bug del filtro (fail-closed per
costruzione): sono regressioni, non misure. Il solo caso «di misura» (12, testo
che massimizza la similarità BGE) non dichiara chi costruisce l'attacco né il
criterio di successo (ingresso in top-k? applicazione?). [PROVATO r. 2020-2043]

## Ciò che ho cercato e NON ho trovato

- Un replay accoppiato on/off per la memoria UTENTE (F3/F5), analogo a quello
  degli hint: assente (rilievo 1).
- Un oracolo di correttezza del referente indipendente dalla postcondizione
  dell'executor nei casi read-only di F3: assente (rilievo 2).
- La definizione del denominatore N per i gate a zero lato scrittura, e
  l'obbligo di registrare gli scarti del filtro §10.1: assenti (rilievo 5).
- Una mappa fase→metrica primaria preregistrata: assente (rilievo 10).
- Il congelamento nel corpus della partizione idoneo/non-idoneo di §18.4:
  assente (rilievo 4).
- Un requisito di indipendenza per i due annotatori di §19.5: assente
  (rilievo 11).
- Una regola che leghi la baseline «ultimo valore esplicito della stessa slot»
  a una condizione di promozione (batterla, non solo elencarla): assente.
- Un legame claim→uso a valle per la metrica dei claim confermati: assente
  (rilievo 6).


---

# Lente leiden

Riferimenti a riga = `internal/roadmap/RM-0001-conoscenza-utente-locale.md`
(revisione 26/7/2026, 2832 righe, letta per intero) salvo diversa indicazione.
Tutti i comandi citati sono stati eseguiti in sola lettura il 26/7/2026.

## Verdetto in tre righe

**Non serve.** La partizione Leiden alimenta una sola cosa — proposte revisionabili
(report/ChangeIntent) — e quel canale esiste già in produzione con raggruppamento
deterministico (ADR 0180/0185, `telos_proposals_store`); la scala provata dell'istanza
(17 archi nel grafo di co-attivazione dopo 2 mesi; tetto di ~200 memorie/utente) è di
2-4 ordini di grandezza sotto quella in cui Leiden si distingue da componenti connesse;
l'alternativa più semplice a parità di scopo è: componenti connesse sulla proiezione
positiva + grouping per chiavi e sequence mining che il documento stesso impone come
baseline (r. 1399). Che cosa si perde togliendolo: detto in fondo, ed è poco.

## Rilievi

### 1. La partizione non alimenta nessuna decisione che non abbia già un produttore
[PROVATO] Tutto ciò che il documento fa con le comunità: UC-07 «Leiden può raggruppare
comunità offline» → «report o ChangeIntent revisionabile» (r. 118-121); «Leiden può
produrre soltanto report o ChangeIntent revisionabili» (r. 1605); F9 «comunità come sole
proposte; adapter verso candidati workflow» (r. 1907-1908); «risultato soltanto candidate»
(r. 1407); «Una comunità Leiden non crea di per sé una relazione epistemica» (r. 1200).
Nessuna query online (r. 1495), nessuna promozione, nessun campo dati consuma la comunità.
Il canale «ripetizioni → proposta revisionabile» esiste già: ADR 0185 (turno costoso
ripetuto → autopath ombra; lacuna ricorrente → change_intent PROPOSED) e ADR 0180
(adapter telos cluster-head), con raggruppamento deterministico per firma in produzione:
`runtime/telos_proposals_store.py:172` (`annotate_clusters`), `:233` (`cluster_score`),
`:246` (`recompose_clusters`).
[OPINIONE] Conseguenza: per il criterio stesso del mandato — un algoritmo di comunità che
non alimenta nessuna decisione nuova è ornamento — Leiden qui è ornamento; in più è una
seconda strada verso lo stesso artefatto (proposta revisionabile), rilievo §7.2 e di
sorgente unica di verità che nessuna delle review precedenti ha sollevato.

### 2. La scala provata dell'istanza rende l'esito di F9-Leiden già scritto — dal documento stesso
[PROVATO] Dati reali (comandi read-only): 7.023 turni in 61 giorni (jsonl in
`PATH_TURNS`, 2026-05-27→2026-07-26), 16.441 step, 105 tool distinti, 230 step
`act_sites`/`login_sites`; 84 executor firmati su disco; 2 utenti (`users.db`); 4 persone;
5 autopath; 76 fastpath; 188 righe `executor_stats`. Il documento fissa ~200 memorie
attive per utente (r. 1075-1077) e deduplica l'esperienza per tupla indipendente
(r. 1172-1174).
[IPOTESI] Proiezione a un anno al tasso corrente (che include traffico di sviluppo e
bench, quindi per eccesso): ~42.000 turni; grafo memoria utente nell'ordine delle
centinaia di nodi (tetto 200/utente × 2 utenti, più candidati); esperienza del pilot
`act_sites` nell'ordine di 10^3 episodi pre-dedup; grafo workflow con ~10^2 nodi
executor. Nessun grafo supera 10^3-10^4 nodi.
[PROVATO] Il documento stesso: «Leiden non viene promosso su grafi piccoli» (r. 1412).
Conseguenza: F9-Leiden è una fase numerata il cui esito negativo è deducibile oggi dai
dati e dalla regola di promozione del documento; mantenerla come fase con harness
obbligatorio è spesa di specifica a risultato noto.

### 3. Il precedente interno: il grafo di co-attivazione reale ha 17 archi dopo due mesi
[PROVATO] Metnos possiede già un grafo di co-occorrenza fra executor: mnestoma
(`runtime/mnestoma.py`, tabelle `mnests`/`events`, traversal `walk` r. 624, `top_k_*`
r. 606-615). Sull'istanza di produzione (`/opt/metnos/workspace/.mnestoma/mnest.sqlite`):
17 `mnests` (archi), 1.034 `events`, 84 `canonical_query_log` — dopo ~2 mesi di uso a
~115 turni/giorno.
[OPINIONE] Conseguenza: il grafo relazionale che questa istanza produce davvero non
matura nemmeno i dati per porre il problema delle comunità; 17 archi si ispezionano a
occhio. È il riscontro empirico che RM-0001 non porta: nessuna stima di nodi/archi in
tutto il documento (vedi «non trovato»).

### 4. Il problema che Leiden risolve è un fenomeno da grafi grandi
[PROVATO fonte] Traag, Waltman, van Eck 2019 (fonte già in §24, r. 2460-2461; abstract
arXiv:1810.08473): Louvain produce «fino al 25% di comunità mal connesse e fino al 16%
disconnesse», Leiden «garantisce comunità connesse» ed è più veloce. Sono percentuali
misurate su reti empiriche grandi, ed è la garanzia di connessione il valore distintivo
di Leiden su Louvain.
[OPINIONE] Su un grafo di centinaia di nodi la connessione di ogni gruppo si verifica
direttamente in microsecondi, e le componenti connesse — deterministiche, 20 righe di
BFS o `networkx` già installato — danno gruppi connessi per costruzione. A questa scala
la proprietà per cui Leiden esiste non è il fattore che decide nulla.

### 5. Contraddizione interna: Leiden è «livello di retrieval» e insieme «mai nella query»
[PROVATO] §9.0 lo pone come livello 5 della scala e afferma «Ogni livello implementa la
stessa interfaccia di retrieval» (r. 638-647); §19.5 lo mette nel confronto «Exact, FTS5,
dense, RRF, grafo e Leiden ricevono stessi input, stesso k, stesso budget di output e
stesso reader» (r. 2233-2234). Ma §13.2: «Leiden non fa parte della query online»
(r. 1495-1497). Una partizione offline non risponde a una query con k risultati: il
documento non definisce mai come Leiden implementerebbe l'interfaccia di retrieval che
gli attribuisce due volte.
Conseguenza: l'ablation di §22 punto 16 (r. 2367) è inapplicabile così com'è scritta per
il ramo Leiden; o si definisce un uso retrieval (espansione per cluster — mai descritta)
o Leiden va tolto dalla scala e dal confronto a stesso k.

### 6. La parte portante del «grafo tipizzato» non può aspettare F9
[PROVATO] Le relazioni sono «la fonte canonica di supports, contradicts e supersedes» e
`status` è una loro materializzazione nella stessa transazione (r. 1052-1054, tabella
`memory_relations` fra le «minime» a r. 1116); il reconciler di F4 «classifica
EvidenceRelation e MemoryRelation» (r. 1301-1304). Eppure §17.1 colloca «relations e
Leiden in F9» (r. 1969-1970) e §25.1 assegna `relations.py` a F9 (r. 2499).
Conseguenza: il contenuto davvero portante di F9 (gli archi tipizzati) è dovuto a F1/F4
per il funzionamento della macchina a stati; ciò che resta esclusivo di F9 è l'espansione
a un salto e Leiden. Chiarito questo, «F9 = grafo tipizzato» è un'etichetta che fa
sembrare strutturale una fase il cui contenuto proprio è quasi solo l'ornamento del
rilievo 1.

### 7. Costo di dipendenza: Leiden non c'è nell'ambiente, e non arriva gratis
[PROVATO] `python3 -c "import leidenalg"` → ModuleNotFoundError; `import igraph` →
ModuleNotFoundError; `networkx` 3.6.1 presente con `louvain_communities` nativo, ma
`leiden_communities` è solo API di dispatch: eseguirlo dà «NotImplementedError:
'leiden_communities' is not implemented by 'networkx' backend». Quindi Leiden vero
richiede `leidenalg`+`python-igraph` (estensioni C) o un backend esterno: una dipendenza
nuova per un modulo a esito pre-scritto (rilievo 2). `sklearn` 1.8.0 e `scipy` 1.17.1
sono già presenti per ogni alternativa agglomerativa/a soglia.
[OPINIONE] Il tempo di calcolo, viceversa, non è un argomento in nessuna direzione: a
10^2-10^3 nodi qualunque algoritmo termina in millisecondi. Il costo reale è dipendenza,
harness (rilievo 8) e superficie di specifica, non la CPU.

### 8. Il modulo più caro di F9 è l'harness anti-stocasticità, che i metodi semplici non richiedono
[PROVATO] Leiden è stocastico e il documento lo sa e lo presidia onestamente: seed e
ordine input deterministici, più seed e perturbazioni degli archi, stabilità VI/ARI,
confronto obbligatorio senza Leiden (r. 1403-1409), «Seed fisso dimostra ripetibilità,
non stabilità» (r. 1411), harness in F9 (r. 1906) e verifica «cluster instabili»
(r. 1915-1916).
[OPINIONE] Quindi il §7.9 è salvo, ma al prezzo massimo: l'apparato multi-seed/VI/ARI
esiste SOLO per sorvegliare una proprietà (l'instabilità della partizione) che componenti
connesse, grouping per chiavi e sequence mining non hanno affatto, essendo deterministici
per costruzione. Si costruisce il pezzo più costoso della fase per governare un difetto
che le alternative non introducono.

### 9. I workflow sono sequenze ordinate; la proiezione positiva butta via l'ordine
[PROVATO] La proiezione dichiarata esclude «contradicts, supersedes e archi diretti»
dagli archi di comunità (r. 1400-1402), ma l'arco d'esperienza che definisce un workflow
è `followed_by` (r. 1193), cioè direzionale e ordinato; e il bersaglio di UC-07 sono
«sequenze verificate di executor» (r. 116). La baseline dichiarata dal documento —
«grouping per chiavi e frequent sequence mining» (r. 1399, 1905) — è lo strumento
canonico e deterministico proprio per le sequenze.
[OPINIONE] Conseguenza: per lo scopo workflow, Leiden non è solo sovradimensionato, è lo
strumento della categoria sbagliata: una comunità di co-occorrenza non conserva l'ordine
che rende un workflow proponibile. La baseline non è un termine di paragone da battere:
è la soluzione.

## Che cosa toglierei / che cosa aggiungerei

**Toglierei** [OPINIONE, sui riscontri 1-9]:
- Leiden da F9 e dai punti che lo citano come stadio: livello 5 di §9.0 (r. 642), blocco
  Leiden di §12.6 (r. 1397-1413), voce nel confronto a stesso k di §19.5 (r. 2233-2234),
  harness e verifiche Leiden di F9 (r. 1906, 1912-1916); §22 punto 16 ridotto a
  «embedding e grafo». F9 si rinomina «relazioni tipizzate e un salto», con gli archi
  canonici anticipati alla fase che già li richiede (rilievo 6).
- La menzione di Leiden nella risposta Reddit di §28 (r. 2827): promette in vetrina il
  modulo meno difendibile del documento.

**Aggiungerei** [OPINIONE]:
- come livello massimo del raggruppamento offline: componenti connesse deterministiche
  sulla proiezione positiva (zero dipendenze nuove, deterministiche, comunità connesse
  per costruzione); per l'eventuale raggruppamento su embedding, soglia di similarità o
  agglomerativo con `sklearn` già installato;
- UNA riga di condizione di riapertura misurabile, al posto dell'intera macchina F9-Leiden:
  «si riapre la valutazione di un algoritmo di comunità se la proiezione positiva supera
  ~10^4 nodi CON una componente gigante (le componenti connesse non discriminano più),
  oppure se esistono N casi contati in cui grouping per chiavi e sequence mining
  producono raggruppamenti sbagliati documentati». Oggi entrambe le condizioni sono
  lontane ordini di grandezza (rilievi 2-3).

**Che cosa si perde togliendo F9-Leiden** (dovere di onestà del verdetto «non serve»):
(a) la capacità di scoprire, su grafi grandi e densi, comunità ben connesse che non
condividono chiavi né contiguità sequenziale — un caso che questa istanza, ai tassi
misurati, non produrrà negli anni coperti dalla roadmap; (b) una voce di vetrina in §28;
(c) nulla dei casi core: §23 dichiara già la roadmap completabile senza grafo e Leiden
(r. 2417-2421) e UC-07/08 non sono gate (r. 2423-2426). Le relazioni tipizzate e il
salto singolo NON si perdono: restano, anticipate dove servono.

## Ciò che ho cercato e NON ho trovato

- [PROVATO] Una decisione, un campo dato o un consumatore che usi la comunità oltre
  report/ChangeIntent: assente — le 27 occorrenze di «Leiden» in RM-0001 (grep con
  righe) portano tutte a proposta revisionabile, harness o rinuncia; nessuna tabella di
  §11.5/§11.6 ha un campo comunità.
- [PROVATO] `leiden`/`louvain` nel codice: 0 occorrenze in `runtime/` (grep).
- [PROVATO] Una stima di nodi o archi attesi, o una soglia numerica di scala, in tutto
  RM-0001: assente — esiste «budget di nodi e tempo» (r. 1408) senza alcun numero.
- [PROVATO] Una metrica di qualità dei raggruppamenti oltre la stabilità VI/ARI:
  «utilità end-to-end» è nominata (r. 1916) ma mai definita; il documento ammette che
  «modularità alta non dimostra utilità» (r. 1411-1412) senza dire che cosa la dimostri.
- [PROVATO] Preferenze W2 registrate sull'istanza: `users.db` reale contiene 2 utenti e
  nessuna tabella `user_prefs` ancora creata — il corpus del profilo parte oggi da zero,
  coerente con la stima di scala del rilievo 2.
- [PROVATO] Nelle lenti già scritte, la catena scala+alternative+costo su Leiden:
  `lente_ambizione.md` propone di degradare F9 ad appendice ma come [OPINIONE] di
  ambizione («contribuisce zero comprensione utente»), senza dati d'istanza, senza
  alternative e senza costi di dipendenza; §7.2 del documento (r. 489) e §27.2 (r. 2754)
  tengono Leiden come edge dietro ablation ma nessuna review ha verificato che l'esito
  dell'ablation sia già deducibile dalla scala reale.

