# Dati utente: analisi di Hermes (Hercules) e strategia KISS per Metnos

> **Prodotto il 22/7/2026.** «Hercules» = **Hermes Agent** (NousResearch) — l'app agentica open-source con
> memoria persistente; nomi foneticamente vicini, e Metnos ne traccia già USER.md come riferimento
> ([[project-user-md-hermes]]). Parte A basata su **documentazione ufficiale verificata** (non memoria).
> Parte B/C/D basate sul **codice Metnos verificato** (file:line). Solo report — nulla modificato.
> Fonti: `github.com/NousResearch/hermes-agent` (docs memory.md, honcho.md), `hermes-agent.nousresearch.com`,
> `mindstudio.ai/blog/hermes-agent-five-pillars`.

---

## Parte A — Come Hermes gestisce e memorizza i dati utente (fatti verificati)

Hermes usa **due file di testo curati dall'agente**, piccoli e con limite fisso, più un servizio esterno
opzionale di modellazione utente (Honcho). I «five pillars» sono Memory, Skills, Soul, Crons, Self-Improvement;
qui interessa Memory.

### A.1 I due file di memoria
Entrambi in `~/.hermes/memories/`, sopravvivono a restart/update/reinstall.

| File | Limite | Contenuto |
|---|---|---|
| **`MEMORY.md`** | **2.200 char (~800 token)** | Note dell'agente su di sé: fatti d'ambiente (OS, tool, struttura progetto), convenzioni, workaround di tool scoperti, diario dei task completati, tecniche che hanno funzionato. |
| **`USER.md`** | **1.375 char (~500 token)** | Profilo utente: nome, ruolo, fuso; **preferenze di comunicazione** (conciso vs dettagliato, formato); pet-peeve e cose da evitare; abitudini di workflow; livello tecnico. |

Il **limite di caratteri è deliberato**: forza l'agente a tenere solo l'essenziale (curation, non accumulo).

### A.2 Comportamento di scrittura
- **Automatico** — «saves automatically, you don't need to ask». Scrive quando apprende una preferenza,
  scopre un fatto d'ambiente, riceve una richiesta esplicita, o dopo una correzione.
- **Immediato su disco, differito nel contesto**: la scrittura va subito su file, ma **compare nel system
  prompt solo alla sessione successiva** (per non rompere il prefix-caching dell'LLM).
- **Gate opzionale**: `write_approval: true` mette le scritture in stage per revisione prima dell'accettazione.

### A.3 Caricamento nel contesto
- **Snapshot congelato all'avvio sessione**: i due file entrano nel system prompt come blocco che **non cambia
  mai a metà sessione** (preserva il prefix-cache).
- Formato a **delimitatori `§`** fra le voci (multi-riga ammesse); l'header mostra store, % di utilizzo e conteggio caratteri.

### A.4 Honcho — modellazione utente automatica (servizio ESTERNO, opzionale)
Il pezzo «intelligente» che va oltre i file curati a mano.
- **Cosa tiene**: «a running model of who the user is — preferences, communication style, goals, patterns»;
  peer profile con user-representation, peer-card, observations accumulate.
- **Come lo deriva**: **ragionamento dialettico** dopo ogni turno (cadenza `dialecticCadence`); multi-pass se
  `dialecticDepth>1` (pass 0 = valutazione, pass 1 = self-audit/lacune, pass 2 = riconciliazione/contraddizioni).
- **Dove vive**: **servizio esterno** (`honcho.dev`, `HONCHO_API_KEY`), dati **server-side**.
- **Iniezione**: due strati (contesto base: summary+user-representation+peer-card, rinfrescato a `contextCadence`;
  + supplemento dialettico sintetizzato dall'LLM), concatenati e troncati al budget `contextTokens`, auto-iniettati nel prompt.

### A.5 I benefici che l'utente ottiene (ciò che dobbiamo replicare)
1. **Ti ricorda** fra sessioni (chi sei, cosa fai) senza ripeterti.
2. **Impara il tuo stile** (conciso/dettagliato, formato) e le cose da evitare, e **le applica** senza chiedere.
3. **Si approfondisce nel tempo** (modello di te che matura), automaticamente.
4. **Personalizza** le risposte di conseguenza.

---

## Parte B — Cosa Metnos GIÀ ha (verificato nel codice)

Metnos ha **già la maggior parte dell'ossatura**, in forma *governata* invece che free-form.

| Pezzo Hermes | Equivalente Metnos | Dove (verificato) |
|---|---|---|
| `USER.md` (profilo+prefs) | **tabella `user_prefs`** `(user_id,key,value,source,updated_at)` PK(user_id,key), **vocabolario CHIUSO** | `runtime/users.py:632` (`PREF_KEYS`), `:637` (`PREF_ALLOWED`), `:661` (`source DEFAULT 'explicit'`); ADR 0187 |
| Chiavi profilo | `lang{it,en}`, `tone{neutro,informale,formale}`, `reply_length{breve,normale,dettagliata}`, `units{metric,imperial}`, + stealth | `decisions/0187-user-prefs-w2.md:8` |
| API scrittura/lettura | `users.set_pref/get_pref/list_prefs/delete_pref` | `users.py:669` |
| Iniezione profilo nel contesto | `read_persons` legge `user_prefs` e compone «chi sono io» | `executors/read_persons/read_persons.py:95`; `prompts/*/planner/sections/persons.j2` |
| UI di gestione | `/admin/users` (validata live) | `http_routes_admin.py` |
| **Gate propose-then-approve** (≈ Honcho + write_approval) | **`change_intents` + adapter `user_feedback`** → `STATE_PROPOSED`, conferma umana su /admin/changes | `change_intent_adapters/user_feedback.py:1,9,29` |
| Iniezione deterministica/riproducibile | describe deterministico (temp=0+seed, byte-riproducibile) | parte mutabile §11 |
| Isolamento multi-utente | `user_id` PK + device→users.id | ADR 0187, multi-user |
| «Note su di sé» (≈ MEMORY.md agent-notes) | **`mnestoma`** (co-attivazioni tool riuscite/mancate) | `runtime/mnestoma.py` |

### B.1 I due GAP reali vs Hermes (onesto)
1. **Nessun apprendimento automatico delle preferenze** (il beneficio-chiave di Honcho). Il campo `source`
   esiste ma oggi vale sempre `'explicit'`: nessun percorso scrive `source='learned'`. L'utente deve *dire*
   ogni preferenza; Metnos non la deduce da «rispondi più corto», «sempre in metrico», «niente emoji».
2. **Le prefs entrano nel contesto solo su richiesta di profilo** (`read_persons`, query «chi sono io»),
   non come **blocco congelato in OGNI turno**. Quindi `tone`/`reply_length` rischiano di NON modellare la
   risposta a ogni turno come fa lo snapshot di Hermes. *(Da verificare a codice: se `describe`/`final_answer`
   applicano già tone/reply_length per-turno, questo gap è chiuso; il grep non l'ha mostrato.)*
3. **Aspirazionale**: la «memoria fatti su di te» del Quick Tour cap. 6 («il compleanno della mamma è il 23/4»)
   **non esiste** come store dedicato — c'è solo `user_prefs` (chiuso). Non è un bug, ma la doc promette più del codice.

### B.2 Dove Metnos è già MEGLIO di Hermes (per i suoi principi)
- **Governato e verificabile**: ogni dato utente è una riga a vocabolario chiuso con `(source, updated_at)` →
  si sa *cosa*, *da dove*, *quando*. Hermes tiene prosa free-form in `MEMORY.md`/`USER.md` che il modello scrive
  di te — opaca e non auditabile allo stesso modo.
- **Nessun cloud esterno**: Honcho è un servizio server-side; Metnos è §10.3 self-hosted per principio.
- **Consenso**: SOUL.md + Quick Tour cap. 6 — «mai scritte di nascosto: propone, voi approvate». Hermes scrive
  di default (`write_approval` è opt-in); Metnos propone di default.

---

## Parte C — Mappatura Hermes → Metnos

| Beneficio Hermes | Meccanismo Hermes | Porta KISS in Metnos | Nuovo codice? |
|---|---|---|---|
| Ti ricorda chi sei | USER.md snapshot | `user_prefs` + iniezione profilo (esiste) | no |
| Applica il tuo stile | USER.md nel system prompt | `tone`/`reply_length` applicati **a ogni turno** (wiring) | minimo (wiring) |
| Impara lo stile da solo | Honcho dialectic post-turno, esterno | **pref-proposer deterministico post-turno → change_intent PROPOSED** (`source='learned'`), conferma umana | **1 modulo piccolo** |
| Si approfondisce nel tempo | Honcho observations | prefs accumulate + proposte ricorrenti (ha già W1 learning-loop) | no |
| Personalizza | iniezione a 2 strati | blocco-profilo deterministico nel contesto | wiring |
| Note libere su di te | MEMORY.md free-form | **NON portare free-form** (viola vocab chiuso); se serve → «fatti» a tipo chiuso, proposti | opzionale |

---

## Parte D — Strategia integrabile (KISS, verificabile, allineata a Metnos)

**Principio guida (rev. 22/7 su indicazione di Roberto).** Per il modello che Metnos costruisce sul **proprietario**
(su di TE): **apprendimento SILENZIOSO, nessuna conferma-per-scrittura** (le conferme continue sono attrito
inutile su dati che riguardano te stesso), sostituita da **piena esplorabilità + correzione/oblio istantanei**.
Il controllo non è *prima* (consenso per ogni nota) ma *dopo* (vedi tutto, correggi qualsiasi cosa in un tap).
**Perché è sicuro** — tre motivi che rendono la conferma superflua qui:
1. È il **tuo** self-model, non un'azione sul mondo né un fatto su terzi.
2. Il modello appreso influenza solo **COME** Metnos risponde/disambigua, **mai** autorizza un'azione: il cancello
   di consenso sulle azioni (vaglio §2.11) resta ORTOGONALE e invariato — un delete/send passa comunque dalla carta.
3. È **closed-vocab + provenienza** (`source`, `turn_id`, `updated_at`, confidenza) → interamente auditabile, molto
   più della prosa opaca di Hermes.
**Relaxation SCOPED**: il propose-then-approve RESTA per azioni sul mondo, fatti su ALTRE persone, e il catalogo
executor. Solo il self-model del proprietario diventa silent-learn. (Aggiorna il Quick Tour cap. 6 / SOUL.md per
questo caso specifico.) Restano fuori i due tratti di Hermes che confliggono davvero: la prosa free-form model-authored
(contro vocab chiuso §2.2/§2.4) e il servizio esterno Honcho (contro self-hosted §10.3). ~90% è wiring.

### D.1 — `user_prefs` resta la Single Source of Truth (0 codice)
È il «USER.md governato». Eventuale estensione del vocabolario chiuso con poche chiavi utili (allineate a
Hermes): `format{prosa,elenco,tabella}`, `emoji{si,no}`, `verbosity_code{minimale,commentato}`, `avoid` (lista
chiusa di N marcatori) — **solo via ADR** (governance §2.2), non a runtime.

### D.2 — Iniezione a ogni turno (wiring, ~poche righe)
Comporre un **blocco-profilo deterministico** dalle prefs dell'attore e iniettarlo nel contesto di
`planner`/`describe`/`final_answer` a OGNI turno, byte-riproducibile (temp=0). Congelato per-turno (come lo
snapshot Hermes). Verifica: confermare che `tone=informale`+`reply_length=breve` cambino *davvero* l'output su
un turno qualsiasi, non solo su «chi sono io».

### D.3 — Pref-LEARNER silenzioso (1 modulo piccolo, KISS)
Il solo pezzo nuovo. Dopo il turno, un **rilevatore DETERMINISTICO** (§7.9: `detection_lexicon` NL→canonico +
regex, IT+EN — NON un LLM) intercetta preferenze *dichiarate dall'utente* e le **scrive direttamente**, senza chiedere:
- «rispondi più corto/breve» → `set_pref(reply_length, breve, source='learned', turn_id)`; «più dettaglio» → `dettagliata`
- «in metrico/imperiale» → `units`; «niente emoji» → `emoji=no`; «dammi sempre una tabella» → `format=tabella`; «parla informale» → `tone`
Nessun `change_intent`, nessuna carta: `users.set_pref(..., source='learned')` immediato. È il «impara il tuo stile
e lo applica» di Honcho — silenzioso, closed-vocab, locale. La rete di sicurezza NON è la conferma-prima ma
l'esplorazione-dopo (D.4) + il fatto che una pref sbagliata cambia solo il *tono/formato*, mai un'azione.

### D.4 — «Cosa sai di me?» — l'esplorazione È il controllo (il pezzo che sostituisce le conferme)
Poiché non si conferma nulla in scrittura, il controllo diventa la **trasparenza a richiesta**. Superficie unica,
per-utente, raggiungibile in chat («cosa sai di me?», «dimenticami questa cosa») e su `/admin/users`:
- **elenca tutto** ciò che Metnos detiene su di te — ogni riga con `key/value`, **`source`** (explicit vs learned),
  **`turn_id`** che l'ha generata, `updated_at`, e (per le learned) una **confidenza**;
- **correggi/dimentica in un tap**: `set_pref` override o `delete_pref`; un override umano diventa `source='explicit'`
  e il learner non lo tocca più (l'esplicito vince, §11);
- **niente sorprese**: `list_prefs(user)` = l'intero self-model in una query → auditabile, testabile, esportabile.
Questo è **più verificabile** del `MEMORY.md`/Honcho di Hermes (prosa opaca / modello server-side che indovina):
qui ogni cosa è una riga a vocabolario chiuso con provenienza, e la puoi vedere e cancellare.

### D.5 — «Fatti su di te» a tipo chiuso (silent-learn anch'essi)
Per «ricorda il compleanno di mamma» / il gap Quick Tour cap. 6: tabella `user_facts` gemella, **tipi di fatto
chiusi** (`ricorrenza`, `contatto_abituale`, `soglia`, `luogo_abituale`…), appresi **in silenzio** con la stessa
provenienza ed esposti nella stessa superficie D.4. Rinviabile (YAGNI finché non richiesto), ma senza gate quando arriva.

---

## Parte E — Memoria delle INTERAZIONI: disambiguare query e proporre azioni

Questa è la capacità più profonda di Hermes, oltre alle preferenze statiche. **Verificato** (Honcho docs):
il sistema «uses observations from conversation history and user behavior to **disambiguate user intent** and
**suggest proactive actions**», con **osservazione bidirezionale** (peer utente *e* peer agente, 4 toggle default
on) e **durable writeback** dei fatti stabili appresi in conversazione. Il ragionamento dialettico post-turno
(`dialecticCadence`) *accumula* observations su abitudini/obiettivi → un modello che si approfondisce da solo.

### E.1 — Cosa Metnos ha già per questo (i mattoni ci sono)
- **`mnestoma`** (`record_canonical_query`, `record_passing`, proto→active, decay): memoria delle co-attivazioni
  di tool riuscite/mancate. È «interaction memory», ma orientata al **catalogo** (quali tool collaborano), non
  all'utente.
- **`autopath`** (`intent_hash`+`cluster_id` BGE-M3, auto-promote dopo `MIN_OBS_PROMOTE` ✓): impara da query
  *ripetute* a rigiocare un piano generalizzato per un cluster semantico. È «apprendi dall'interazione», ma
  per-cluster-di-query, non per-utente.
- **`learning-loop W1`** (`task_learning_loop_review`, ADR 0185): turno costoso ricorrente → autopath **shadow**;
  lacuna ricorrente → `change_intent` PROPOSED. È **proposta proattiva da interazioni ricorrenti**, ma a livello
  di catalogo/piano, non «proponi a QUESTO utente questa azione».
- **Storico `turns` per-`actor`** (su disco, `turns_recent` by conversation_id/actor) + `get_inputs` (chiede
  quando ambiguo). Il materiale grezzo per-utente c'è.

### E.2 — Il GAP preciso
Nessun **modello-interazione per-utente** che (a) **disambigui** un riferimento ambiguo *di quell'utente* dalla
sua storia («il report» → quale), (b) **proponga** a quell'utente un'azione dalle *sue* abitudini («fai X ogni
mattina — lo schedulo?»). Metnos apprende sui tool/piani, non sul singolo utente.

### E.3 — Strategia KISS (riuso, non nuovo motore)
Differenza-chiave da Honcho: Honcho costruisce un modello **nascosto** e **indovina** senza che tu possa vederlo;
Metnos apprende **anch'esso in silenzio** (nessuna conferma), ma ciò che apprende è **esplorabile e cancellabile**
(D.4). L'unica «domanda» che resta è quella **funzionale** — quando una query è *genuinamente* ambigua e Metnos
non può inventare la risposta — non una conferma di apprendimento.

**E.3.a — Disambiguazione dalla storia (per-utente).** Tabella `user_resolutions(user_id, ref_norm, object_type,
resolved_value, source, turn_id, updated_at)` — gemella di `user_prefs`, dominio *semi-chiuso* (`ref_norm` via
`detection_lexicon`; `object_type` chiuso: persons/files/calendars/…). Flusso con pezzi esistenti: la prima volta
che un riferimento è *davvero* ambiguo, Metnos **chiede** (`get_inputs`, esiste — è una domanda funzionale, non un
«confermi che ho imparato?»); la risposta si salva `source='learned'`+`turn_id`; le volte dopo, un **blocco
«riferimenti noti di questo utente»** (deterministico, congelato per-turno) entra nel contesto del planner e il
riferimento **risolve al default in silenzio** (mostrato in chiaro nella risposta, sempre scavalcabile e
cancellabile via D.4). **Zero motore nuovo**: `get_inputs` + `user_resolutions` + iniezione profilo.

**E.3.b — Proposta proattiva dalle abitudini (per-utente).** Un **nuovo adapter** in `change_intent_adapters/`
(gemello di `user_feedback`) che scandisce lo storico `turns` di *un actor* per pattern deterministici — stessa
query-azione ripetuta a cadenza (es. ≥3 mattine) → emette `change_intent` PROPOSED «vuoi schedularla?» (Metnos ha
già `recurring_tasks` + il pattern scena 8 del Quick Tour). Riusa: storico turni per-actor (c'è), la pipeline
change_intents→/admin/changes o tap in chat (c'è), il learning-loop W1 (estende, non duplica).

### E.4 — Osservazione bidirezionale, versione Metnos
Il «peer agente» di Honcho (l'agente osserva le *proprie* risposte) ha già l'equivalente onesto: `mnestoma`
(cosa ha funzionato) + i badge feedback ✓/✗ della chat (ADR: alimentano la cura del catalogo). Non serve
replicare l'auto-osservazione LLM; il segnale ✓/✗ esplicito è più verificabile.

## Parte F — Esplorazione: cosa Metnos POTREBBE detenere su di te

Poiché non si chiede conferma ma si offre esplorazione, ha senso mappare la **tavolozza** del self-model: cosa
Metnos può plausibilmente sapere di te, **tutto derivato da dati che già tocca** (turni, mail, calendario, file,
foto, persone, task, GitHub) — nessuna nuova raccolta, solo derivazione + un piccolo store per-utente + la
superficie D.4. Ordinato da «gratis/deterministico oggi» a «più ricco/costoso».

| # | Cosa può detenere | Da dove (dato già presente) | Come si deriva | Store |
|---|---|---|---|---|
| 1 | **Stile & formato**: lunghezza, tono, tabelle, emoji, lingua | turni (query + tue correzioni) | pref-learner deterministico (D.3) | `user_prefs` |
| 2 | **Riferimenti risolti**: «il report»→X, «mia figlia»→ospite Y, «il progetto»→path | storico `get_inputs` | E.3.a, salva la risoluzione | `user_resolutions` |
| 3 | **Contatti frequenti**: chi scrivi/senti di più (avvocato, banca, sorella) | mail/messaggi + `persons` | conteggio deterministico per-actor | `user_facts` (contatto_abituale) |
| 4 | **Cartelle & formati abituali**: dove lavori (`~/Documenti/Progetto Atlas`), CSV vs XLSX | operazioni file nei turni | frequenza path/estensione | `user_facts` (luogo_abituale) |
| 5 | **Ritmi & orari**: quando sei attivo (mattina), cadenze | timestamp dei turni | istogramma orario deterministico | `user_facts` (soglia/ritmo) |
| 6 | **Compiti ricorrenti**: «registro scuola ogni mattina» | turni ripetuti a cadenza | E.3.b → propone di schedulare | `change_intent` (azione, non self-model) |
| 7 | **Argomenti d'interesse**: temi che chiedi (rust, foto montagna) | query + ricerche web | top-term per-actor (BoW/embedding) | `user_facts` (interesse) |
| 8 | **Abitudini di workflow**: catene tipiche (find→compress→salva), locale vs Drive | forme di piano (`mnestoma` per-actor) | co-attivazioni per-utente | `mnestoma` esteso con actor |
| 9 | **Luoghi**: casa, posti frequenti | `get_location`/EXIF/geo | frequenza deterministica | `user_facts` (luogo_abituale) |
| 10 | **Provider/canale preferito**: Telegram vs browser, locale vs Google | metadati d'uso | conteggio | `user_prefs` |
| 11 | **Vincoli & cose da evitare**: «mai toccare X», tetti di budget | correzioni/feedback ✗ | detection deterministica | `user_prefs` (avoid, chiuso) |

**Righe 1-2-3-4-5-11 sono quasi-gratis oggi** (deterministiche, dati presenti). 7-8 richiedono un piccolo indice
per-actor. 6 è azione (resta proattiva-proposta, §E.3.b — quella NON è «apprendimento su di te», è offrire di fare
qualcosa). **Tutto self-hosted, mai fuori dalla tua macchina, tutto in `list_prefs`/`list_facts`** = esplorabile e
cancellabile. La *ampiezza* qui è un beneficio (Metnos ti capisce di più), non un rischio, proprio perché è
locale + ispezionabile + a vocabolario chiuso — l'opposto di un servizio cloud che accumula in silenzio su di te.

**Ordine di implementazione consigliato (KISS, valore-prima)**: 1 (stile, il più sentito) → 2 (riferimenti) →
3-4-5 (contatti/cartelle/ritmi, tutti conteggi deterministici) → la superficie D.4 «cosa sai di me?» (il controllo)
→ poi 7-8 se servono. Riga 6 (proattività) è indipendente e già a portata (learning-loop W1 + recurring_tasks).

## Parte G — USO dei dati: confronto con Honcho (best-practice) e proposta rivista

Focus sull'**uso**, non sulla memorizzazione. Honcho (verificato) ha **due tier d'uso**:
- **`honcho_profile`** — recupero peer-card **veloce, senza LLM**: fatti-chiave curati sull'utente.
- **`honcho_context` — Dialectic Q&A** — l'agente pone una **domanda NL arbitraria** sull'utente e ottiene una
  **risposta ragionata** sintetizzata dalla memoria (`dialecticReasoningLevel` minimal→max, `dialecticDynamic`
  auto-escalate). + `honcho_search` (trova qualsiasi cosa nota) + auto-iniezione dei due peer nel system prompt.

**Ammissione onesta.** La mia prima proposta (solo closed-vocab) copre **solo il tier veloce**. NON può fare il
Dialectic: rispondere a una domanda su di te che nessuno aveva enumerato. Per l'USO (disambiguare, decidere se
proporre) questo è insufficiente. Serve un secondo tier.

### G.1 — Proposta rivista: DUE tier d'uso, come Honcho, ma locali e verificabili
1. **Tier veloce (= `honcho_profile`)**: prefs closed-vocab + riferimenti risolti → applicati a OGNI turno,
   **deterministico, no LLM** (tono/formato/lingua, «il report»→X già noto). Sempre-on, gratis, auditabile.
2. **Tier dialettico (= `honcho_context`)**: un helper locale — chiamalo `consult_profile(domanda)` — dove il
   **middle LLM ragiona sul CORPUS-utente** (i turni per-actor già su disco + i fatti derivati) e risponde a una
   **domanda bounded** su di te, restituendo **risposta + EVIDENZA** (quali turni/fatti ha usato) + **riproducibile**
   (temp=0+seed, come describe deterministico). Invocato **solo quando serve**: riferimento ambiguo non nella tabella,
   o decisione «vale la pena proporre X?». Usa il tier LLM locale (`llm_router`) — **nessun honcho.dev**.

Questo è §7.9-conforme: deterministico dove si può (tier 1), LLM **solo** dove il deterministico è impossibile
(«cosa intende/vuole l'utente» non è enumerabile → LLM giustificato, tier 2).

### G.2 — Scorecard d'USO (onesto)
| Capacità d'uso (Honcho) | Honcho | Mia 1ª proposta | Proposta rivista (2-tier) |
|---|---|---|---|
| Applica prefs note alla risposta (tono/formato) | ✅ | ✅ | ✅ tier 1 |
| Peer-card veloce senza LLM | ✅ | ✅ | ✅ tier 1 |
| **Rispondere a una domanda NL arbitraria su di te** | ✅ dialectic | ❌ | ✅ tier 2 (locale) |
| Disambiguare un riferimento aperto dalla storia | ✅ | ⚠️ solo se già risolto | ✅ tier 2 ragiona sul corpus |
| Decidere «vorrebbe questo?» prima di proporre | ✅ | ❌ | ✅ tier 2 |
| Profondità di ragionamento scalabile | ✅ minimal→max | ❌ | ✅ tier LLM (fast/middle/wise/frontier) |

### G.3 — Dove Metnos può essere MEGLIO della best-practice (non solo pari)
- **Evidenza, non oracolo**: il Dialectic di Honcho sintetizza server-side, opaco; il `consult_profile` di Metnos
  **cita i turni/fatti** su cui ha ragionato → sai *perché* ha concluso X, e lo puoi contestare.
- **Riproducibile**: temp=0+seed → stessa domanda, stessa risposta (Honcho no).
- **Locale**: nessun dato esce dalla macchina, nessun API key esterno (Honcho è cloud).
- **Bounded**: l'LLM risponde a UNA domanda mirata; non costruisce un modello nascosto che gira da solo — il
  corpus resta osservazioni reali con provenienza, l'LLM le LEGGE, non le inventa.

### G.4 — Costo onesto
Il tier 2 è una chiamata LLM (latenza/costo) e nel CONTENUTO non è deterministico come il tier 1 (è però
riproducibile per-chiamata). Mitigazione: invocato **solo on-demand** (non a ogni turno), sul tier locale
`middle`, con floor di confidenza — se l'evidenza è debole, **dichiara incertezza** e chiede (§2.11) invece di
indovinare. È il prezzo per eguagliare la capacità-uso aperta di Honcho; §7.9 lo ammette perché la domanda non è enumerabile.

## Perché è KISS e verificabile

- **~90% wiring di parti esistenti**: `user_prefs` (SoT), `read_persons` (iniezione), describe deterministico
  (riproducibilità), storico turni per-actor + `get_inputs` (esistono), multi-user (isolamento). **Un solo modulo
  nuovo**: il pref-learner deterministico (~un `detection_lexicon` + N regole, testabile a tavolino). Niente gate,
  niente carte per il self-model.
- **Equipotente ai benefici di Hermes** (ti ricorda, impara lo stile in silenzio, si approfondisce, personalizza,
  disambigua, propone) **senza** i due tratti che rompono Metnos (prosa model-authored, cloud esterno).
- **Il controllo è l'ESPLORAZIONE, non la conferma**: nessuna interruzione mentre apprende; «cosa sai di me?»
  mostra tutto con provenienza e lo cancelli in un tap. Più trasparente di Honcho (modello server-side che indovina).
- **Verificabile meccanicamente**: ogni datum è closed-vocab con `source/turn_id/updated_at` → `list_prefs`/`list_facts`
  è il self-model completo in una query; un unit test asserisce «query X → `set_pref` Y `source=learned`».
- **Sicuro senza conferma**: il self-model cambia solo tono/formato/disambiguazione, mai autorizza un'azione — il
  vaglio §2.11 sulle azioni resta invariato e ortogonale.
- **Il trade onesto** (già framato nel Quick Tour cap. 2): il vocabolario chiuso NON cattura la sfumatura arbitraria
  che Hermes mette in prosa. Rinuncia deliberata in cambio di esplorabilità e audit.

## Piano di verifica (test §8)
1. `test_user_prefs` esteso: nuove chiavi vocab (D.1) → CRUD + rifiuto valori fuori-enum.
2. Iniezione (D.2): turno reale con `tone=informale,reply_length=breve` → output cambia; byte-riproducibile a temp=0.
3. Pref-learner (D.3): unit `detect_pref("rispondi più corto") == (reply_length, breve)`; IT+EN; turno reale
   «d'ora in poi più breve» → `set_pref(reply_length,breve,source=learned)` **immediato, senza conferma** → il turno
   successivo risponde breve.
4. Esplorazione (D.4): turno «cosa sai di me?» → elenca ogni riga con source+turn_id+updated_at; «dimentica che…»
   → `delete_pref`; override umano → `source=explicit` e il learner non lo riscrive.
5. Disambiguazione (E.3.a): 1ª volta ambiguo → `get_inputs`; 2ª volta → risolve al default in silenzio (mostrato, scavalcabile).

**Fonti**: [Hermes memory.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md) ·
[Hermes honcho.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/honcho.md) ·
[hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) ·
[Five Pillars](https://www.mindstudio.ai/blog/hermes-agent-five-pillars-memory-skills-soul-crons).
