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
