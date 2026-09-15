# RM-0009 — Crescita allineata delle capacità

> **RM-0009**
> - **Stato:** `active`. Revisione 6, pronta per un nuovo giro adversarial;
>   con l'approvazione di Roberto passa a `ready`.
> - **Creazione e revisione:** creata il 2 settembre 2026; ultima revisione il
>   14 settembre 2026 (sera).
> - **Conservazione:** persistente.
> - **Implementazione:** nessuna fase F0-F6 iniziata. FS-A e FS-B sono
>   autorizzate da Roberto il 14/9 a partire subito.
> - **Elenco lavori di riferimento:** appendice C.
> - **Fonti:**
>   - revisioni 1-5, con i rilievi della revisione 4, la review indipendente
>     della revisione 5 e la sua verifica (commit `ad37442c`), nella storia Git;
>   - verifiche sul codice del 14/9, riportate al §2;
>   - esito dei 22 rilievi della revisione 5 nell'appendice B.
> - **Baseline di approvazione:** va registrata fuori da questo file, in
>   `internal/reports/rm0009-baseline/`, dall'unità P0.

## Decisioni di Roberto (vincolanti)

| Data | Decisione |
|---|---|
| 3/9 | «L'approccio e l'obiettivo non è mettere pezze ma creare un sistema che cresce in modo intelligente e gestito. Il modo di rappresentarlo è indifferente. L'obiettivo no.» |
| 4/9 | La libertà di Metnos deve essere **modulata** (fase F6). |
| 14/9 | **Ambito globale.** Le modifiche governate da RM-0009 sono globali e disponibili a tutti gli utenti. `owner_id` e il componente proponente sono solo provenienza: nessun gate, nessun rollout per owner. |
| 14/9 | **Piano completo:** fasi F0-F6 e istruzioni per gli agenti (appendice A). |
| 14/9 | **Sicurezza subito:** FS-A e FS-B partono ora, separate da RM-0009. |
| 14/9 | **Obiettivo:** un sistema che aumenta le proprie capacità, anticipa le necessità, ottimizza le proprie prestazioni e realizza i fini di TELOS. |
| 14/9 | **Autonomia:** «l'utente deve intervenire solo in poche e semplici occasioni, il meno possibile». |
| 14/9 | **Criteri di progettazione:** KISS, utilità per l'utente, soluzioni universali e non ad hoc, niente hardcoding, efficienza. |
| 14/9 | **Non bloccante:** un difetto cosmetico o di preparazione non ferma sviluppo e manutenzione; si ferma solo per rischi veri. |

## 1. Obiettivo e valore per l'utente

**Metnos è un sistema che:**

1. **aumenta le proprie capacità.** Si accorge di ciò che non sa fare, lo propone,
   fa nascere la capacità sotto la porta unica di RM-0008, la prova, poi la
   attiva o la ritira;
2. **anticipa le necessità.** Propone le capacità che serviranno prima che una
   richiesta fallisca, partendo dai fini di TELOS e dagli schemi d'uso;
3. **ottimizza le proprie prestazioni.** Rende più veloce, economico e affidabile
   ciò che fa già, a partire dai piani ripetuti;
4. **realizza i fini di TELOS.** Ogni proposta e ogni misura dichiarano il fine
   servito (`workspace/TELOS.md`): `t.tempo`, `t.ordine`, `t.puntualita`,
   `t.protezione`, `t.discrezione`, `t.parsimonia`.

**Autonomia.** Tutto avviene nel modo più autonomo possibile.
- **Decide Metnos** ciò che è reversibile in modo garantito, misurabile e dentro
  le capacità già concesse.
- **L'utente interviene** solo quando un cambiamento dà a Metnos autorità nuove,
  non è reversibile in modo garantito, costa o tocca dati sensibili. Riceve una
  domanda chiara, con risposta sì o no, raccolta nel riepilogo e mai come
  interruzione (`t.discrezione`).
- Il numero di domande ha un tetto settimanale; quelle in eccesso aspettano in
  una coda ordinata, senza perdersi.

**Ambito e privacy.**
- Una modifica attivata entra nello stesso catalogo e nello stesso routing per
  tutti gli utenti.
- Una modifica globale contiene soltanto **strutture canoniche**: tipo,
  verbo, oggetto, hash d'intento, piano senza valori. Mai testo, nomi,
  percorsi o valori di un utente.
- Dati, credenziali, task e singole esecuzioni restano sempre circoscritti
  all'utente che li possiede.

**Libertà modulata.** La libertà di un executor dipende da fatti firmati:
capacità e ambiti concessi, reversibilità provata, stato del ciclo di vita.
Non dipende dall'etichetta con cui la proposta è nata.

**Valore per l'utente**, espresso per fine TELOS:
- meno incombenze ripetitive (`t.tempo`);
- scadenze intercettate (`t.puntualita`);
- dati in ordine (`t.ordine`);
- risposte più rapide ed economiche (`t.parsimonia`);
- nessuna interruzione inutile (`t.discrezione`);
- nessuna esposizione di dati (`t.protezione`).

## 2. Stato verificato del codice (14 settembre 2026)

Il ciclo esiste già: va ricongiunto, non costruito. Categorie:
- `connected`: produttore, consumatore ed effetto presenti;
- `unconsumed`: scritto, ma nessuno lo legge per cambiare comportamento;
- `unreachable`: nessun chiamante in produzione;
- `gated`: bloccato per progetto da un cancello con un proprietario;
- `alive_starved`: gira, ma non riceve input utile;
- `partial`: funziona solo in parte.

| # | Anello | Dove | Stato | Nota |
|---|---|---|---|---|
| 1 | Il motore dichiara di non saper fare | `engine/dispatch.py` | `connected` | `capability_missing` significa «il piano ha perso un passo» |
| 2 | Il fallimento diventa lacuna | `engine/terminator.py` (`_record_lacuna`) | `partial` | aggrega per chiave con `n_seen`; non riceve turno, passo né owner |
| 3 | La lacuna diventa proposta | `learning_loop.py:34` | `alive_starved` | ammette solo `out_of_scope` e `wrong_tool` |
| 4 | La proposta riceve un punteggio | `change_intents.py:405` | difetto statico | una colonna, cinque formule, `max()` |
| 5 | Il giudizio sui fini TELOS | `alignment_engine.py`; `vaglio.judge` | `partial` / `unreachable` | `alignment_engine` giudica solo `telos` e su errore restituisce `[]` |
| 6 | Una persona decide | `change_applier.py`, `/admin/changes` | `connected` | tutto umano; `upsert_intent` e `_transition` non sono atomici |
| 7 | La capacità nasce | porta RM-0008 | `gated` | 0 nascite concluse su 59 (storico, 3/9) |
| 8 | Prova in ombra | `executor_birth_shadow.py` | `partial` | rapporto, non transizione |
| 9 | Il preesercizio decide | `executor_birth_preexercise.decide_preexercise` | `unreachable` + `gated` | cancello RM-0008/F5 |
| 10 | Promozione e riepilogo | `jobs/promoter*` | `alive_starved` | a vuoto dal 13/5; solo `promoted_grace` |
| 11 | Il rifiuto insegna | `change_applier.py:272` (`reject_pattern`) | `unconsumed` | scrive `rejected_patterns.jsonl`, che nessuno legge |

**Meccanismi vivi da riusare:**
- **Turni e contesto:** `agent_runtime.TurnLog` e `run_turn`, con `actor`,
  `channel` e `owner_user_id`.
- **Proposte e giudizio:**
  - `jobs/change_intent_materialize.task_change_intent_materialize`, ogni giorno
    alle 01:00, con `change_intent_adapters.iter_all` e `upsert_intent`;
  - il ciclo TELOS notturno `telos_introspect.py`.
- **Piani e cache:**
  - i piani autopath con il flag `shadow`
    (`engine/autopath._promote_autopath`);
  - la validità delle cache (`engine/cache_validity.tools_sig`, `ROUTING_EPOCH`).
- **Feedback personale:** il ✗ dell'utente, circoscritto all'owner
  (`turn_feedback.rejected_pipelines_for_query`), già letto dal planner.
- **Nascita:** la sintesi verso la porta Birth
  (`synth_request` → `executor_birth_synth.submit_synth_multistage`).
- **Invocazioni remote:** la coda firmata
  (`invocations.enqueue_invocation` → `invocations.next_invocation`, prelievo
  lato server).
- **Aging:** la deprecazione degli executor inutilizzati (`executor_aging`,
  anche per `dedupe_executors`).

**Difetti verificati di cui tenere conto.**
- **`change_applier`:**
  - `apply_create_executor` sintetizza solo dopo l'accettazione;
  - `apply_materialize_pipeline` esegue la richiesta una volta sola;
  - `apply_cache_pattern` scrive nello store `canonical_query_log`, ritirato il 2/7.
- **Coerenza dei dati:**
  - `idx_ci_fingerprint` non è univoco;
  - `tools_sig` non dipende né da politica né da rifiuti;
  - `change_rollback` restituisce errori come dizionari, senza eccezioni;
  - `final_kind` vale anche `loop_break`;
  - i ruoli HTTP (`anonymous`, `user`, `admin`) non coincidono con `users.ROLES`
    (`host`, `guest`).

**Sicurezza, verificata:**
- `synth_request.py:72` esegue `test_runner.py` sull'host;
- `sandbox.py:549` monta nell'executor il vault e `admin.key`;
- i percorsi vietati del Vaglio non coprono la radice di configurazione.

**Numeri storici (3/9)**, da rigenerare in P0:
- lacune 13/0/4;
- 55 intent `proposed`;
- nessuna proposta da bisogno reale dal 19/7;
- scostamento registro/turni di circa 5 volte, come ipotesi.

## 3. Cause

- **C1 — Il segnale è solo reattivo e staccato** (anelli 2-3); anticipazione e
  ottimizzazione vivono fuori dal ciclo.
- **C2 — La decisione non è calibrata, è tutta umana e non è atomica** (4-6):
  - il punteggio è monotono;
  - il giudice vede una sola famiglia;
  - i salvataggi possono duplicare.
- **C3 — Il ritorno è interrotto e il ciclo è cieco:**
  - il rifiuto non ha effetto (anello 11);
  - il riepilogo segue un solo stato (anello 10);
  - turni reali e di collaudo sono indistinguibili.

## 4. Principi di progettazione (norma)

1. **KISS.** Una sola pipeline. Nessuna struttura nuova se ne esiste una
   equivalente. Le poche strutture nuove sono condivise: un solo primitivo di
   token monouso, una sola epoca globale, un solo costruttore di fatti.
2. **Utilità.** Ogni fase dichiara il beneficio per l'utente e il fine TELOS.
3. **Universale, non ad hoc.** Si riusano i meccanismi vivi del §2. I tipi di
   cambiamento sono un insieme chiuso, e ciascuno ha un contratto completo (§5.7).
4. **Niente hardcoding.**
   - Soglie, finestre, budget e famiglie sensibili stanno nel registro di
     politica (§5.6).
   - Le enumerazioni si definiscono una volta, come `(str, Enum)`, e si
     persistono come valore.
   - Classi d'errore tipizzate; i testi per l'utente passano da i18n.
5. **Efficienza.**
   - Calcolo in memoria sul percorso del turno e dell'invocazione, con flush
     periodico e aggregato: nessuna scrittura per invocazione.
   - Conservazione di ogni tabella append-only.
   - Gli indicatori li calcola il riepilogo notturno.
6. **Autonomia.** Una sola regola deterministica stabilisce chi decide (§5.3);
   un fatto assente o non verificato non porta mai ad `auto`.
7. **Atomicità.** Ogni transizione è un compare-and-swap su stato e versione.
   - Ogni effetto ha un `operation_id` idempotente e una riconciliazione.
   - Nessun effetto globale senza ricevuta.
8. **Privacy strutturale.** Lo store globale ha schemi chiusi senza testo libero.
   - Provenienza e identificativi causali sono opachi e visibili solo
     all'amministrazione.
9. **«Chi lo chiama?»** Un registro chiuso di collegamenti
   `produttore → store/evento → consumatore → effetto osservabile`, verificato da
   prove di esecuzione.
10. **Onestà** (CLAUDE.md §2.8). Nessuna funzione mostrata all'utente dichiara un
    effetto che non produce.
11. **Non bloccante.**
    - Un difetto locale ferma solo il suo contratto.
    - Una politica non valida sospende la sola pipeline di crescita, mai Metnos.

## 5. Architettura: una pipeline, tre sorgenti, due momenti di decisione

```text
S1 lacuna ────┐
S2 anticipazione ─┼─> intent globale canonico ─> valutazioni ─> decisione
S3 ottimizzazione ┘   (impronta univoca)         (append-only)   │
                                                                 ├─ create/extend: D1 prova ─> RM-0008 Birth ─> ricevuta ─> D2 attivazione
                                                                 └─ promote_plan: decisione unica
esito: auto | ask_user (domanda monouso) | deny | blocked (veto tecnico, ritentabile)
effetto: applying (lease, operation_id) ─> applied + epoca globale ─> misura ─> ritorno verificato se peggiora
```

### 5.1 Sorgenti

- **S1, lacuna:** eventi `capability_absent` di F3, emessi solo in turni
  `origin=user` e ricontrollati sul catalogo al momento dell'aggregazione.
  Producono `create_executor` o `extend_executor`.
- **S2, anticipazione:** le lenti TELOS notturne producono `create_executor` o
  `extend_executor` in forma canonica: verbo, oggetto, fine. Le automazioni
  personali (task ricorrenti del singolo utente) sono fuori da RM-0009 (§9).
- **S3, ottimizzazione:** i piani autopath in ombra, con esecuzioni riuscite in
  turni reali, producono `promote_plan`.

Ogni intent porta:
- tipo e contratto;
- impronta canonica (§5.7);
- `source_kind`;
- `telos_goal`;
- `proposer_component_id` e versione;
- `origin_owner_id` opaco.

I contributi stanno in `change_intent_sources` (§5.2). Nessuno di questi campi
di provenienza entra in impronta, soglie, ranking, decisione o autorità.

### 5.2 Valutazioni e provenienza

Un registro append-only `change_intent_evaluations`:
- `evaluation_id`: intero autoincrementale, che dà l'ordine totale;
- `intent_id`: chiave esterna;
- `dimension`: `alignment`, `recurrence`, `rejections` o `benefit`;
- `status`: `ok`, `not_applicable` o `insufficient_evidence`;
- `value`, `metric_name`, `unit`, `window`, `sample_count`, `baseline`,
  `observed`;
- `evaluator_id` e `evaluator_version`;
- `source_event_id`: `NOT NULL`, con namespace (§5.7);
- `revision` e `supersedes_evaluation_id`;
- `created_at`;
- vincolo `UNIQUE(intent_id, dimension, evaluator_id, source_event_id, revision)`.

Regole:
- **Proiezione:** l'ultima valutazione non superata per (intent, dimensione),
  secondo `evaluation_id`. Una correzione è una nuova revisione dello stesso
  evento e conserva la storia.
- **`alignment`:** solo `alignment_engine`, per ogni famiglia, con risultato
  tipizzato `ok`, `not_applicable` o `failed`.
  - `failed` non scrive nulla e viene ritentato con backoff (§5.6).
  - L'idempotenza vale per (intent, versione TELOS, versione del valutatore).
- **`benefit`:** porta metrica, unità, finestra, campione, baseline e osservato.
  Sotto il campione minimo lo stato è `insufficient_evidence`.
- **`score`:** la colonna storica resta in sola lettura e non alimenta decisioni.
- **`change_intent_sources`:** provenienza di ogni contributo (`source_event_id`,
  componente e versione, owner opaco), con `UNIQUE(intent_id, source_event_id)`.

### 5.3 Decisione: la regola di autonomia

Due funzioni pure e distinte, che condividono soltanto fatti ed enumerazioni
tipizzate:
- `decide_change(facts, policy)` per i cambiamenti;
- `decide_invocation(envelope, facts, policy)` per le invocazioni (F6).

I fatti li costruisce **solo** `build_change_facts(intent_id)` nel core.
- Ogni fatto porta valore, fonte autorevole e versione.
- **Fonti autorevoli:**
  - ricevuta Birth e manifest firmato dallo store dei contratti;
  - riga autopath;
  - politica;
  - regole di rifiuto.
- Ciò che l'intent dichiara non è un'attestazione.
- **Reversibilità:**
  - `guaranteed`: executor con sole capability di lettura, oppure
    `reverse_pattern` del catalogo chiuso con prova `undo.round_trip` superata
    nella ricevuta Birth; piano autopath (si riporta in ombra);
  - `conditional`: `[undo] outcome="per_execution"`;
  - `none`: in tutti gli altri casi.

**Ordine di precedenza** (il primo che si applica decide):

| # | Condizione | Esito |
|---|---|---|
| 1 | Veto tecnico: ciclo di vita non attivo, ricevuta o attestazione mancante o discordante, legacy ignoto, politica non valida | `blocked` (ritentabile; **non** è un rifiuto) |
| 2 | Regola di rifiuto attiva sulla stessa impronta | `deny` (nessuna nuova domanda) |
| 3 | Un fatto obbligatorio del contratto è assente o non verificato | `ask_user` |
| 4 | `create`/`extend`, **D1 prova**: allineamento ≥ soglia e nessuna capability critica o di famiglia sensibile richiesta | `auto` (la prova non attiva nulla) |
| 5 | `create`/`extend`, **D1 prova**, altrimenti | `ask_user` |
| 6 | `create`/`extend`, **D2 attivazione**, prima della certificazione RM-0008/F5 | `ask_user` |
| 7 | `create`/`extend`, **D2 attivazione**, dopo RM-0008/F5: reversibilità `guaranteed`, nessuna rete in uscita, credenziali o famiglia sensibile, preesercizio superato | `auto` |
| 8 | `promote_plan`: reversibilità garantita, piano senza valori letterali, campione ≥ minimo, successo non inferiore alla baseline | `auto` |
| 9 | `promote_plan` con campione insufficiente | resta in ombra, nessuna domanda |
| 10 | Tutti gli altri casi | `ask_user` |

Tutte le modifiche sono globali per decisione (§ Decisioni): non esiste una riga
«regola globale» separata. `reject_rule` nasce solo da un «no» dell'utente
autorizzato.

### 5.4 Effetto

- **Protocollo, uguale per ogni tipo:**
  1. `accepted → applying`, con compare-and-swap, `operation_id` e lease;
  2. l'handler del tipo esegue l'effetto in modo idempotente per `operation_id`;
  3. `observe_effect(operation_id)` risponde `present`, `absent` o `unknown`;
  4. se `present`: `applying → applied`, con la ricevuta
     `{operation_id, before, after, scope: "global_all_users"}`;
  5. nella stessa transazione cresce `global_change_epoch`.
- **Recupero** (all'avvio e ogni notte): un `applying` con lease scaduta viene
  osservato.
  - `present` → `applied`;
  - `absent` → nuovo tentativo, fino al massimo di politica, poi `failed`;
  - `unknown` → resta `applying` e viene segnalato.
- **Executor:** la nascita, l'attivazione, il ritiro e il ritorno passano solo come
  richieste tipizzate alla porta RM-0008. Il puntatore firmato della
  generazione garantisce che nessun utente veda uno stato misto; RM-0009
  conserva richiesta e ricevuta e non modifica mai manifest o ciclo di vita.
- **Cache:** `tools_sig` include `global_change_epoch`. Rifiuti, revoche,
  rollback e cambi di politica rendono invalide le cache alla lettura successiva.
- **Rollback:** `RollbackResult(ok, verified, error_code)` tipizzato. Lo stato
  diventa `rolled_back` solo dopo che `observe_effect` conferma l'effetto
  inverso; altrimenti `rollback_failed`, con nuovi tentativi.
- **Ritorno automatico:** si attiva solo con `benefit` `ok` e campione
  sufficiente, quando peggiora oltre la soglia di politica. Una prova
  insufficiente non dichiara successo e non provoca rollback.

### 5.5 Consegna e domande

- **Il riepilogo esistente** (`jobs/promoter_digest.py` con
  `jobs/promoter_state.py`) viene esteso dal solo `promoted_grace` alle domande
  `ask_user` e al resoconto delle decisioni `auto`.
- **Ogni domanda è una richiesta di decisione monouso**, emessa dal primitivo
  `one_shot_tokens` e legata a:
  - `intent_id` e `row_version` attesa;
  - impronta;
  - digest dei fatti e dell'effetto mostrato;
  - versione della politica;
  - canale, destinatario e scadenza.
- **Chi può rispondere:** solo un principal autenticato con ruolo logico `host`
  (dal registro utenti) o la chiave amministrativa. Consumo e transizione
  avvengono con compare-and-swap. Si rifiutano:
  - ospiti e messaggi inoltrati;
  - risposte ripetute e doppi clic;
  - risposte scadute;
  - intent rivalutati dopo la domanda.
- **Budget:** al massimo il numero settimanale di politica. Le altre domande
  aspettano in coda ordinata (allineamento decrescente, poi data, poi id),
  senza perdersi né duplicarsi.

### 5.6 Registro di politica: valori iniziali

Modulo unico `runtime/growth_policy.py` (unità P1).
- Le variabili `METNOS_GROWTH_*` prevalgono sui due nomi storici
  (`METNOS_PROPOSE_SEEN`, `METNOS_TELOS_ACCEPT_HARD_GATE`).
- Un valore non valido **sospende la sola pipeline di crescita**: la readiness
  riporta `growth_policy_invalid` e Metnos continua a servire.

| Chiave | Valore iniziale |
|---|---|
| ricorrenza S1 | 3 eventi unici in 30 giorni; al massimo 1 evento contato per (principal, bisogno, giorno UTC) |
| allineamento minimo | 0,45 |
| famiglie di capability sensibili | credenziali, posta, persone, messaggi, sistema/amministrazione (lista modificabile) |
| campione minimo per `benefit` | 20 turni reali |
| ritorno automatico | calo della quota di successo oltre 5 punti, oppure latenza mediana peggiore del 20%, con campione ≥ minimo |
| inutilizzo di un piano promosso | 30 giorni → di nuovo in ombra |
| inutilizzo di un executor nato da RM-0009 | 60 giorni → richiesta di ritiro via RM-0008 |
| budget di domande | 3 alla settimana |
| scadenza di una richiesta di decisione | 72 ore |
| TTL dei token di invocazione (remoto, durevole) | 15 minuti |
| lease di `applying` | 10 minuti; al massimo 3 tentativi |
| giudizio TELOS | lotto di 20 intent a notte; al massimo 5 tentativi con backoff esponenziale |
| scadenza di una regola di rifiuto | 180 giorni, rinnovabile |
| soglia di riconciliazione F3 | al massimo 10% di eventi attesi mancanti |
| conservazione degli eventi append-only | 180 giorni di dettaglio, poi aggregati |
| flush dei contatori F6 | ogni 5 minuti e all'arresto |

### 5.7 Matrice dei tipi di cambiamento (insieme chiuso)

| Tipo | Sorgente | Impronta | Fatti autorevoli | Decisione | Effetto globale | Store e atomicità | Misura | Ritorno |
|---|---|---|---|---|---|---|---|---|
| `create_executor` | S1, S2 | `sha256("create_executor:v1:" + intent_hash)` | ricevuta Birth, manifest firmato, politica, regole di rifiuto | D1 prova, poi D2 attivazione (§5.3) | nuova generazione nel catalogo, via RM-0008 | store dei contratti RM-0008; `operation_id` | uso in turni reali, successo | ritiro via RM-0008 |
| `extend_executor` | S1, S2 | `sha256("extend_executor:v1:" + target + ":" + intent_hash)` | come sopra, più la generazione corrente del target | D1, poi D2 | nuova generazione del target | come sopra | come sopra | ritorno alla generazione precedente via RM-0008 |
| `promote_plan` | S3 | `sha256("promote_plan:v1:" + plan_hash + ":" + intent_hash)` | riga autopath (`shadow`, esecuzioni riuscite), piano senza valori letterali, politica | decisione unica (§5.3, righe 8-10) | il piano L1 serve tutti gli utenti | `autopath.sqlite`, un solo UPDATE idempotente; epoca | latenza, costo e successo dei turni serviti | ritorno in ombra |
| `reject_rule` | «no» dell'host | `sha256("reject_rule:v1:" + impronta_rifiutata)` | richiesta di decisione consumata, principal `host` | nasce solo dalla risposta | la stessa impronta non viene riproposta | `rejection_rules` append-only; epoca | rigenerazioni evitate | revoca append-only |

`intent_hash` è la parte hash di `_compute_intent_sig`: verbo, oggetto e azioni,
senza parole chiave dell'utente.

**Tipi ritirati.** Gli intent storici vanno in `superseded`, con motivo.
- `materialize_pipeline`: esecuzione una tantum, nessun artefatto.
- `cache_pattern`: store ritirato il 2/7.
- `dedupe_executors`: resta a `executor_aging`.
- `reject_pattern`: il ✗ personale resta in `turn_feedback`, circoscritto all'owner.

**Formule di `source_event_id`:**

| Evento | Formula |
|---|---|
| lacuna | `gap:<lacuna_event_id>` |
| lente TELOS | `lens:<telos_proposal_id>:<versione TELOS>` |
| piano | `plan:<plan_hash>:<data UTC di inizio finestra>` |
| giudizio | `align:<intent_id>:<versione TELOS>:<versione valutatore>` |
| beneficio | `benefit:<intent_id>:<metrica>:<inizio finestra>` |

### 5.8 Matrice dei percorsi di invocazione (F6)

L'`InvocationEnvelope` contiene:
- principal autenticato, attore e canale;
- `operational_owner_user_id`, ricavato dal principal;
- executor, `ContractId`, generazione e `admission_context_id`;
- capability e ambiti effettivi;
- destinazione;
- digest canonico degli argomenti;
- workload e tentativo, per il durevole;
- approvazioni e mandati;
- versione della politica.

L'autorità F6 è un'intersezione **aggiuntiva**, dopo autenticazione, ACL,
consenso e ambiti ordinari: non li sostituisce mai.

| Percorso | Ingresso | Principal operativo | Hook di ammissione | Ricevuta | Consumo | Prova di zero effetti |
|---|---|---|---|---|---|---|
| Locale | `agent_runtime.invoke_executor` → `_invoke_executor_impl` | principal del turno | dopo l'iniezione degli argomenti e la risoluzione dei percorsi, prima di undo e subprocess | — (sincrono) | — | diniego: nessun undo, nessun subprocess |
| Remoto | stesso punto, ramo remoto → `remote_exec.invoke_remote` | principal del turno | prima di `invocations.enqueue_invocation` | token monouso legato a invocazione, generazione, argomenti e politica | `invocations.next_invocation`, il prelievo lato server | diniego: nulla in coda; token riusato o scaduto: prelievo rifiutato |
| Builtin e verb-unique | `agent_runtime.invoke_tool_by_name` → `loader.invoke_verb_unique` | principal del turno | prima della chiamata al builtin | — | — | diniego: builtin non chiamato |
| Durevole con executor | `durable_workloads/execution.py` → `executor_scheduler.invoke_scheduled` | owner del workload | all'inizio di ogni tentativo | token monouso legato a workload e tentativo | inizio del tentativo | revoca fra accodamento e tentativo: nessun effetto |
| Durevole interno | worker durevole, passi senza executor | owner del workload | non applicabile | — | — | un test dichiara che il passo non ha autorità di executor |

Il primitivo `one_shot_tokens` è unico per richieste di decisione e
invocazioni, e tutto lato server, quindi senza differenze d'orologio:
- emissione;
- consumo con compare-and-swap;
- revoca per prefisso di legame;
- pulizia notturna.

## 6. Fasi

Ordine: `P0 → P1 → P2 → F0 → F1 → F2 → F3 → F4 → F5 → F6`.
- **FS-A e FS-B** partono subito, fuori sequenza, secondo la matrice dei file
  (appendice A.5).
  - FS-A precede qualunque esecuzione di codice candidato.
  - FS-B precede la dichiarazione di sicurezza di F6.
- **Enforcement di F6** solo dopo la certificazione di RM-0008/F5.
- **Ogni fase** produce un commit per unità, test mirati e almeno un turno reale
  su `/agent/turn` nel dominio toccato (CLAUDE.md §8.5). Il turno lo esegue il
  coordinatore dopo il rilascio (appendice A.3).

**P0 — Baseline riproducibile.**
- *Cambiamento:*
  - istantanea coerente degli archivi (API di backup di SQLite, che rispetta il
    WAL) e dei file dei turni fino a un watermark;
  - digest degli input;
  - calcolo solo sull'istantanea;
  - ogni numero del §2 marcato `reproduced`, `changed` o `not_reconstructable`.
- *Completata quando* due esecuzioni sulla stessa istantanea danno risultati
  identici.

**P1 — Registro di politica.**
- *Cambiamento:* `runtime/growth_policy.py`, con i valori del §5.6, validazione,
  precedenza delle variabili, versione stabile e controllo nella readiness.
- *Completata quando* nessuna soglia del ciclo è un letterale nel codice e una
  politica non valida sospende la sola crescita.

**P2 — Fondamenta condivise.**
- *Cambiamento:*
  - archivio degli intent robusto:
    - pulizia deterministica dei duplicati storici, poi `UNIQUE(fingerprint)`;
    - `row_version`;
    - upsert atomico `INSERT … ON CONFLICT … RETURNING`;
    - transizioni con compare-and-swap;
    - stati nuovi: `awaiting_activation`, `applying`, `blocked`, `superseded`,
      `rollback_failed`;
  - il primitivo `one_shot_tokens`;
  - `global_change_epoch`, incluso in `tools_sig`.
- *Completata quando:*
  - due connessioni che inseriscono la stessa impronta producono un solo intent;
  - una transizione con versione vecchia fallisce;
  - un token consumato due volte fallisce;
  - una cache colpita dopo un cambio d'epoca viene invalidata.

**F0 — Osservabilità.**
- *Cambiamento:*
  - `TurnLog` riceve `origin`, `outcome` e `intent_hash`:
    - `origin`: `user`, `test`, `system` o `unknown`;
    - `outcome`: `success`, `partial`, `error`, `needs_input` o `unknown`, per
      ogni `final_kind` raggiungibile, compreso `loop_break`;
    - `intent_hash`: la parte hash della firma d'intento, senza parole chiave.
  - `origin` si risolve dal ruolo logico dell'utente autenticato, preso dal
    registro utenti (`host`/`guest` → `user`; `test` → `test`). Lo scheduler dà
    `system`; ogni caso incerto, compreso l'anonimo LAN, dà `unknown`.
- *Completata quando:*
  - admin/host, ospite, collaudo, LAN sintetico e ripresa di un turno danno
    l'origine attesa;
  - ogni nuovo turno ha il suo esito;
  - il legacy resta `unknown`.

**F1 — Componenti e backlog.**
- *Cambiamento:*
  - registro chiuso dei collegamenti, verificato da prove di esecuzione;
  - ritiro di `vaglio.judge`, di `reject_pattern` e del suo adapter
    `user_feedback` nel ciclo globale, e dei tipi `materialize_pipeline`,
    `cache_pattern` e `dedupe_executors`;
  - documentazione vera;
  - pulizia del backlog per tipo, in `superseded`, con dry-run, regola e prova.
    Un `create_executor` con bersaglio assente **non** si pulisce.
- *Completata quando* ogni collegamento ha la sua prova, e togliere un
  collegamento fa fallire la prova giusta.

**F2 — Valutazioni e privacy.**
- *Cambiamento:*
  - il registro del §5.2;
  - `max()` tolto;
  - giudizio tipizzato per ogni famiglia;
  - schemi chiusi senza testo libero: sintesi e motivazioni si generano da
    campi canonici tramite i18n, e il testo delle lenti resta nello store
    amministrativo.
- *Completata quando:*
  - una correzione verso il basso resta, con la sua storia;
  - un errore del giudice non scrive nulla e viene ritentato;
  - due owner con valori privati diversi convergono nello stesso intent, senza
    che un valore dell'uno compaia all'altro.

**F3 — Lacune vere.**
- *Cambiamento:*
  - il contratto tipizzato `GapEvidence`, emesso dal dispatch: turno, ordinale
    del passo, principal, generazione del catalogo, classificatore;
  - `lacuna_events` append-only;
  - cinque tipi: `capability_absent`, `capability_unavailable`,
    `plan_step_missing`, `out_of_scope`, `unknown`;
  - riconciliazione solo sulle classi che devono emettere un evento.
- *Completata quando:*
  - un collaudo o un errore non pertinente non produce eventi utili a S1;
  - un retry non duplica;
  - lo scostamento è sotto soglia.

**F4 — Tre sorgenti.**
- *Cambiamento:*
  - adapter S1, S2 e S3 registrati in
    `runtime/change_intent_adapters/__init__.py` (`iter_all`);
  - un'unica transazione intent + sorgente nel job
    `task_change_intent_materialize`;
  - il giudizio subito dopo, idempotente;
  - le formule di `source_event_id` del §5.7;
  - S1 ricontrolla il catalogo prima di proporre.
- *Completata quando* il job registrato, senza interventi manuali, porta un caso
  per sorgente a un solo intent con la sua sorgente e la sua valutazione.

**F5 — Decisione, effetto e ritorno.**
- *Cambiamento:*
  - `build_change_facts`;
  - `decide_change` con due momenti;
  - richieste di decisione monouso nel riepilogo, con budget e coda;
  - protocollo `applying`, recupero ed epoca;
  - `reject_rule` append-only, con scadenza e revoca;
  - `benefit` e rollback tipizzato e verificato.
- *Completata quando:*
  - una richiesta Birth idempotente produce una ricevuta verificata e una sola
    attivazione, e senza ricevuta l'attivazione è impossibile;
  - ospite, inoltro, replay e scadenza vengono rifiutati;
  - un crash a ogni confine converge;
  - un veto tecnico riparato rende di nuovo valutabile il bisogno;
  - un rifiuto dell'host impedisce la rigenerazione fino a scadenza o revoca.

**F6 — Libertà modulata.**
- *Cambiamento:*
  - `InvocationEnvelope`;
  - `decide_invocation`;
  - un hook di ammissione in ciascun percorso del §5.8, prima in ombra;
  - contatori in memoria con flush aggregato;
  - poi l'enforcement con token consumati al punto indicato.
- *Completata quando:*
  - il diniego produce zero effetti in ogni percorso;
  - replay concorrente, riavvio, scadenza e revoca falliscono senza effetti;
  - due utenti vedono la stessa capacità ma non possono riusare argomenti,
    ricevute, cache, riprese o task dell'altro.

**FS-A — Un solo runner di nascita (subito).**
- *Cambiamento:*
  - inventario su tutto il repository e sulla release installata;
  - `run_birth_phase` diventa l'unica API;
  - `setup`, `teardown` ed `env` vengono **rifiutati** con un errore tipizzato;
  - i 24 manifest che li usano vengono ripubblicati senza quei campi, dal ciclo
    normale;
  - infine `test_runner.py` viene ritirato.
- *Completata quando* nessun riferimento resta e le prove di isolamento passano:
  rete, processi, utente e IPC, ambiente ammesso, cwd effimera, limiti di
  CPU/RAM/output, morte dell'intero albero.

**FS-B — Radici protette e broker (subito).**
- *Cambiamento:*
  - gli executor delle credenziali diventano builtin nel core e restituiscono
    solo metadati; l'owner si ricava dal principal;
  - via i mount di vault e `admin.key`;
  - radici protette = tutto il piano di controllo, con eccezioni tipizzate;
  - apertura con `openat2(RESOLVE_NO_SYMLINKS | RESOLVE_BENEATH)`, o cammino
    no-follow su ogni segmento.
- *Completata quando* nessuna chiave radice compare in mount, ambiente,
  argomenti o log, e alias, symlink e sostituzioni del percorso vengono negati.

## 7. Indicatori permanenti

Li produce il riepilogo notturno. Finestra mobile UTC; `unknown` sempre a parte.

| # | Indicatore | Fonte | Finestra | Denominatore | Fase |
|---|---|---|---|---|---|
| 1 | Proposte nate da un bisogno reale | intent S1 con causa, turni `origin=user` | 30 g | — | F4 |
| 2 | Capacità nate e attive | ricevute RM-0008 e ciclo di vita | sempre | tentativi | RM-0008 |
| 3 | Capacità nate e poi usate | turni `origin=user` che le invocano | 30 g | capacità nate | F5 |
| 4 | Scostamento registro/turni | `lacuna_events` contro turni F0 | 30 g | eventi attesi | F3 |
| 5 | Turni per origine ed esito | `TurnLog` | 30 g | turni chiusi | F0 |
| 6 | Proposte anticipate accettate e usate | intent S2, decisione, uso | 30 g | intent S2 | F4-F5 |
| 7 | Prestazioni del turno reale | latenza e costo mediani, quota `success` | 30 g | turni `origin=user` | F0, S3 |
| 8 | Valore per fine TELOS | intent ed effetti per `t.*` | 30 g | — | F4-F5 |
| 9 | Domande all'utente | domande emesse, coda in attesa, tempo mediano di risposta | 7 g | budget | F5 |
| 10 | Autorità ristretta | decisioni F6 aggregate | corrente | executor attivi | F6 |

**Regola di tensione.** Finché gli indicatori 1, 3 e 6 restano a zero, il tema è
aperto. L'indicatore 9 deve restare entro il budget.

## 8. Prove obbligatorie

Ogni rilievo della revisione 5 ha la sua prova di chiusura, nominata
nell'appendice B. Minimo trasversale:
- concorrenza su due connessioni e iniezione di crash a ogni confine del
  protocollo `applying`;
- anti-auto-attestazione per ogni fatto;
- tabella esaustiva delle precedenze della decisione;
- richieste di decisione con ospite, inoltro, replay, doppio clic, scadenza e
  rivalutazione;
- zero effetti al diniego in ogni percorso del §5.8;
- convergenza di due owner senza fuga di valori privati;
- cache invalidata dopo rifiuto, revoca, rollback e cambio di politica;
- job registrato → adapter → intent → sorgente → valutazione, senza interventi
  manuali.

## 9. Non-obiettivi

- **Il preesercizio produttivo:** appartiene a RM-0008/F5. RM-0009 ne consuma la
  ricevuta.
- **I quattro livelli nell'identità firmata.**
- **Le automazioni personali** (task ricorrenti del singolo utente): dati
  circoscritti all'owner, fuori dalla pipeline globale (area di RM-0001).
- **Il feedback ✗ personale:** resta in `turn_feedback`, circoscritto all'owner e
  già letto dal planner. Non diventa una regola globale.
- **Aging e deprecazione degli executor:** restano a `executor_aging`.
- **Il ritiro delle guardie:** resta la procedura umana in quattro passi, con
  `guard_stats.dormant()` nel riepilogo.
- **`wrong_args`:** dominio della GUARD_PIPELINE.
- **Rollout, visibilità o varianti per owner.**

## 10. Rischi e misure

| Rischio | Misura |
|---|---|
| Crescita della complessità | principio 4.1: tre strutture condivise, nient'altro di nuovo; revisione in un giro |
| Auto-attestazione e avvelenamento del segnale | `build_change_facts` da fonti autorevoli; al massimo un evento contato per principal, bisogno e giorno; solo turni `origin=user` |
| Doppie applicazioni e stati misti | UNIQUE sull'impronta, compare-and-swap, `applying` con lease, puntatore firmato RM-0008, epoca globale |
| Decisioni rubate o vecchie | richieste di decisione monouso legate ai fatti mostrati; solo `host` |
| Dati privati negli artefatti globali | schemi chiusi senza testo libero; `intent_hash` senza parole chiave; provenienza opaca |
| Guasti trattati come rifiuti | `blocked` ritentabile, distinto da `reject_rule` |
| Rollback falsi | rollback tipizzato, verificato e ritentato |
| Codice candidato non isolato, chiavi esposte | FS-A e FS-B subito |

## 11. Completamento e arresto

RM-0009 è completata quando:
1. per ciascuna sorgente un caso reale compie il giro evento → intent →
   valutazione → decisione → effetto → misura, con identificativi causali e fine
   TELOS;
2. esistono un rifiuto efficace, una restrizione applicata, un retry
   idempotente, un recupero dopo crash e un caso legacy che fallisce in modo
   sicuro;
3. gli indicatori 1, 3, 6 e 9 sono prodotti e il 9 resta entro il budget.

La nascita automatica e l'enforcement di F6 dipendono da RM-0008/F5 e da FS-A.
Fino ad allora RM-0009 può arrivare a `implemented` per F0-F5 e F6 in ombra, con
D2 sempre `ask_user`.

**Arresto.** F0 può correggere denominatori e gravità, ma non dichiarare
artefatti i difetti statici provati.

## 12. Che cosa resta a Roberto

- **Approvare** la revisione, dopo il nuovo giro adversarial.
- **A regime**, rispondere alle poche domande sì/no nel riepilogo.

**Condizione per il giro adversarial** (dalla review della revisione 5): due
agenti medium indipendenti, leggendo solo la roadmap e il repository, producono
lo stesso elenco di file, transizioni, vincoli e test. La verifica la esegue il
coordinatore prima di chiedere l'approvazione.

## Appendice A — Istruzioni operative per agenti di medio livello

> **Per chi implementa.** Ogni unità è autosufficiente: obiettivo, file da
> leggere, passi, divieti, test e consegna. I numeri di riga sono indicativi
> (verificati il 14/9): ritrova sempre il simbolo con `rg -n`.

### A.1 Ruoli

- **Coordinatore** (l'agente scelto da Roberto):
  - assegna le unità secondo l'A.5;
  - rivede ogni consegna in un solo giro;
  - raccoglie le unità in un ciclo di rilascio;
  - esegue i turni reali e gli script sui dati del servizio;
  - aggiorna lo stato in testa alla roadmap.
- **Esecutore** (agente di medio livello): una sola unità alla volta.

### A.2 Regole comuni

**Prima di iniziare**
1. Leggi `CLAUDE.md`, `CLAUDE.mutabile.md` e i §§1-6 di questa roadmap.
2. Registra `git rev-parse HEAD` e `git status --short`. Se un file dell'unità
   ha modifiche non tue, fermati con `BLOCKED_BASELINE`.
3. Ritrova ogni simbolo con `rg -n "<simbolo>" runtime tests`. Se non esiste più
   o fa altro, fermati con `BLOCKED_BASELINE` e descrivi la differenza.

**Durante il lavoro**
4. Prima il test che fallisce, poi il cambiamento minimo, poi la migrazione
   idempotente, versionata con `PRAGMA user_version`.
5. Crea soltanto i file nuovi elencati; riusa ciò che la scheda indica.
6. Nessun letterale di soglia: leggi da `runtime/growth_policy.py`.
7. Le enumerazioni sono `class X(str, Enum)`, definite una volta, e si
   persistono con `.value`.
8. Ogni transizione di un intent usa la funzione di compare-and-swap di P2: mai
   `UPDATE` diretti.
9. I testi per l'utente passano da chiavi i18n (seed IT ed EN); la diagnostica
   usa `LOG_*`. Commenti e docstring del codice in inglese.
10. Nei test, solo archivi temporanei (`tmp_path` e `METNOS_USER_*`).

**Divieti assoluti**
- modificare chiavi, firme, ricevute, store dei contratti o dati di produzione;
- riavviare servizi;
- eseguire `prepare`, `apply` o rilasci;
- `git stash` senza nome;
- pubblicare su GitHub;
- aggiungere dipendenze;
- indebolire test (skip, xfail, `-k` per nascondere, timeout più lunghi);
- scrivere testo libero dell'utente o del modello in un campo globale.

**Test**
11. Esegui `/opt/metnos/.venv/bin/python -m pytest -q <test dell'unità>`, poi le
    suite dei moduli toccati (`rg -l "<modulo>" tests`). Riporta raccolti,
    passati, saltati e falliti.

**Consegna**
12. Un commit per unità, con i soli file dell'unità: messaggio in inglese, senza
    Co-Authored-By.
13. Una nota al coordinatore: commit, file, test con i numeri, rischi, rollback.

**Stati di arresto**
- `BLOCKED_BASELINE`: codice o albero diversi dall'atteso.
- `BLOCKED_DECISION`: serve un valore che non sta né nel §5.6 né nella scheda.
- `BLOCKED_SAFETY`: servirebbe allargare permessi, esporre segreti o eseguire
  codice candidato fuori dal runner Birth.

### A.3 Turno reale e dati del servizio (solo il coordinatore)

```bash
curl -s -m 600 -X POST http://127.0.0.1:8770/agent/turn \
  -H "Authorization: Bearer $(cat ~/.config/metnos/admin.key)" \
  -H 'Content-Type: application/json' --data '{"query": "<richiesta>"}'
curl -s -H "Authorization: Bearer $(cat ~/.config/metnos/admin.key)" \
  'http://127.0.0.1:8770/admin/turns?limit=5'
```

- Gli script sui dati del servizio (account `metnos`) si eseguono con
  `sudo -n /usr/local/sbin/metnos-agent-admin <script> <sha256> <modo>`, dopo la
  revisione dello SHA.
- Per i turni di collaudo si usa il token del principal `test` (F0.2).

### A.4 Schede delle unità

#### P0 — Baseline riproducibile
- **Leggi prima:**
  - `engine/terminator.py` (`_db_path`, tabella `lacune`);
  - `change_intents.py` (`_conn`, `count_by_state`);
  - `engine/autopath.py`, per il suo archivio;
  - `jobs/promoter_state.py`;
  - `config.PATH_TURNS`.
- **Crea:** `internal/tools/rm0009_baseline.py` e `tests/internal/test_rm0009_evidence.py`.
- **Passi:**
  1. `--snapshot <dir>` copia ogni database con `sqlite3.Connection.backup`, che
     rispetta il WAL, e i file dei turni fino al watermark `--until <UTC>`;
     registra i digest degli input.
  2. `--report <dir>` calcola solo sull'istantanea e scrive `report.md` e
     `manifest.json`: query, watermark, digest, risultati.
  3. Ogni numero del §2 è marcato `reproduced`, `changed` o `not_reconstructable`.
- **Test:** due report sulla stessa istantanea sono identici; gli archivi
  d'origine restano immutati (digest prima e dopo).
- **Coordinatore:** esegue l'istantanea sui dati del servizio (A.3).

#### P1 — Registro di politica
- **Crea:** `runtime/growth_policy.py` e `tests/runtime/learning/test_growth_policy.py`.
- **Passi:**
  1. `@dataclass(frozen=True) class GrowthPolicy`: un campo per ogni riga del §5.6.
  2. `load_growth_policy()`:
     - default del §5.6;
     - prima `METNOS_GROWTH_<NOME>`; poi, solo se manca, i due nomi storici
       (`METNOS_PROPOSE_SEEN`, `METNOS_TELOS_ACCEPT_HARD_GATE`).
  3. Validazione di tipo e intervallo. Un valore non valido solleva
     `GrowthPolicyError` (codice tipizzato).
  4. `version`: hash stabile dei valori effettivi.
  5. Un controllo di readiness riporta `growth_policy_invalid`. Ogni punto
     d'ingresso della pipeline di crescita (job, riepilogo, decisioni) si
     sospende e lo registra; Metnos continua a servire.
  6. `learning_loop.py` smette di leggere `PROPOSE_SEEN` (~29).
- **Test:**
  - default;
  - precedenza fra nomi nuovi e storici;
  - valore non valido: pipeline sospesa, servizio vivo;
  - `version` cambia se cambia un valore.

#### P2 — Fondamenta condivise
- **Leggi prima:** `change_intents.py` (`ALL_STATES` ~111, `_TRANSITIONS` ~118,
  `init_db` ~304, `upsert_intent` ~356, `_transition` ~544, indici ~88-95),
  `engine/cache_validity.py` (`tools_sig` ~109).
- **Crea:** `runtime/one_shot_tokens.py`, `tests/runtime/learning/test_change_intents_atomicity.py`
  e `tests/runtime/safety/test_one_shot_tokens.py`.
- **Passi:**
  1. **Migrazione versionata:**
     - per ogni impronta duplicata tiene l'intent più recente e porta gli altri
       in `superseded`, con motivo;
     - poi `CREATE UNIQUE INDEX` sull'impronta;
     - aggiunge `row_version INTEGER NOT NULL DEFAULT 0`.
  2. **Stati nuovi** in `ALL_STATES`, con le transizioni:
     - `staged → awaiting_activation` (ricevuta verificata);
     - `awaiting_activation → accepted | rejected | blocked`;
     - `accepted → applying`;
     - `applying → applied | failed | applying`;
     - `applied → rolled_back | rollback_failed`;
     - `rollback_failed → rolled_back | rollback_failed`;
     - `blocked → proposed | awaiting_activation`;
     - `proposed | staged | awaiting_activation → superseded`;
     - `superseded → proposed` (ripristino).
  3. **`upsert_intent`** con `INSERT … ON CONFLICT(fingerprint) DO UPDATE …
     RETURNING id`, in una sola istruzione.
  4. **`transition(id, expected_state, expected_version, new_state, **fields)`**
     esegue `UPDATE … WHERE id=? AND state=? AND row_version=?` e controlla
     `rowcount`. Tutti i chiamanti di `_transition` passano da qui.
  5. **`one_shot_tokens.py`**, una tabella SQLite in `PATH_USER_STATE`:
     - colonne: `token_id` (128 bit casuali), `purpose` (enum), `binding_digest`,
       `expires_at`, `consumed_at`, `consumed_by`;
     - funzioni: `issue`, `consume(token_id, binding_digest, principal)` (UPDATE
       con condizione su non consumato e non scaduto), `revoke_by_binding`,
       `gc`.
  6. **Epoca:** tabella a riga unica `global_change_epoch` nel database degli
     intent, con la funzione `bump_epoch(conn)` da chiamare dentro la
     transizione. `tools_sig` include l'epoca, letta con una memoria
     invalidata da `PRAGMA data_version`.
- **Test:**
  - due connessioni e due thread che inseriscono la stessa impronta producono
    un solo id;
  - una versione vecchia fallisce;
  - un token consumato due volte, scaduto, o con legame diverso fallisce;
  - cambio d'epoca → `tools_sig` diverso.

#### F0.1 — Origine, esito e hash d'intento
- **Leggi prima:** `agent_runtime.py` (`class TurnLog` ~4109, `error_class` ~4185,
  `final_kind` accettati ~5188, `TurnLog.write` ~5173, `orchestrate_needs_inputs`
  ~6168), `engine/autopath.py` (`_compute_intent_sig` ~298).
- **Crea:** `runtime/turn_outcome.py` e `tests/runtime/infra/test_turn_provenance.py`.
- **Passi:**
  1. Due enumerazioni `(str, Enum)`:
     - `TurnOrigin`: `user`, `test`, `system`, `unknown`;
     - `TurnOutcome`: `success`, `partial`, `error`, `needs_input`, `unknown`.
  2. Inventario dei `final_kind`: `rg -n 'final_kind\s*=|final_kind in' runtime`.
     La tabella del test elenca **tutti** i valori trovati.
  3. `derive_outcome(final_kind, error_class, steps) -> TurnOutcome`:

     | Caso | Esito |
     |---|---|
     | `ask`, e ogni turno prodotto da `orchestrate_needs_inputs` | `needs_input` |
     | `error`; `answer` con `error_class` | `error` |
     | `loop_break` con almeno un passo riuscito | `partial` |
     | `loop_break` senza passi riusciti | `error` |
     | `answer` con un passo fallito | `partial` |
     | `answer` pulito | `success` |
     | valore sconosciuto | `unknown` (con test) |

  4. Aggiungi a `TurnLog` i campi `origin`, `outcome` e `intent_hash`, con i
     default `unknown`, `unknown` e `""`. `intent_hash` è **solo** il secondo
     elemento di `_compute_intent_sig`, mai la firma leggibile.
  5. In `write()`, `outcome` si calcola una sola volta e si persiste come `.value`.
- **Test:** la tabella esaustiva; il record storico letto come `unknown`;
  `intent_hash` non contiene parole della richiesta.

#### F0.2 — Origine dal confine autenticato
- **Leggi prima:**
  - `run_turn` (`agent_runtime.py` ~7502) e i chiamanti
    (`rg -n "run_turn\(" runtime`);
  - `http_auth.py` (ruoli `anonymous`, `user`, `admin` ~360-410,
    `authenticated_user_id`);
  - `users.py` (`ROLES` ~46).
- **Passi:**
  1. `run_turn` riceve il parametro obbligatorio `origin: TurnOrigin`.
  2. Aggiungi `"test"` a `users.ROLES`, con le autorizzazioni di `guest`
     (verifica `rg -n 'role ==|role !=' runtime`).
  3. `resolve_turn_origin(request_role, authenticated_user_id, channel)`:
     - chiave amministrativa → `user`, perché è l'host;
     - `authenticated_user_id` → ruolo nel registro utenti: `host`/`guest` →
       `user`, `test` → `test`;
     - anonimo, anche il bypass LAN, → `unknown`;
     - scheduler, task ricorrenti e `change_applier` → `system`.
  4. Il corpo della richiesta non può impostare `origin`.
  5. La ripresa di un turno conserva l'`origin` del turno originale.
  6. Aggiorna i finti `run_turn` nei test.
- **Test:** in `tests/runtime/http/test_http_turn_ownership.py`:
  - admin/host;
  - ospite;
  - collaudo;
  - LAN sintetico;
  - corpo con `origin`;
  - ripresa;
  - scheduler.
- **Coordinatore:** crea il principal `test` con l'abbinamento dispositivi esistente.

#### F0.3 — Vista e indicatori 5 e 7
- **Leggi prima:** `http_routes_admin.py` (`admin_turns` ~1040),
  `nightly_orchestrator.py` (`NIGHTLY_SEQUENCE` ~37), `config.PATH_TURNS`,
  `config.PATH_COST`.
- **Passi:**
  1. `/admin/turns` mostra `origin` e `outcome`.
  2. Una voce notturna calcola:
     - quota di turni per origine ed esito;
     - latenza mediana dei turni `origin=user`;
     - costo per turno se `PATH_COST` lo registra, altrimenti `not_observed`.
- **Test:** vista HTTP; indicatore su file di prova.
- **Turno reale:** una richiesta con la chiave → `origin=user`; con il token
  `test` → `origin=test`.

#### F1.1 — Registro dei collegamenti
- **Crea:** `tests/runtime/learning/test_growth_cycle_links.py`.
- **Passi:**
  1. Nel test, una tabella chiusa:
     `id | produttore | store/evento | consumatore | effetto osservabile | prova`.
  2. Per ogni riga, una prova di esecuzione con archivi temporanei: esegue il
     produttore, poi il consumatore, e verifica l'effetto.
  3. Un'eccezione porta proprietario, motivo e fase che la rimuove.
- **Test:** togliere un collegamento fa fallire la riga corrispondente
  (verificato una volta a mano, poi annotato nella consegna).

#### F1.2 — Ritiri e documentazione vera
- **Leggi prima:**
  - `vaglio.py` (`JUDGE_KIND` ~46), `prompts/{it,en}/vaglio.j2`;
  - `engine/executor.py` (~2050), `engine/dispatch.py` (~7086, ~7162),
    `testing/populate_cases.py` (~433);
  - `change_applier.py` (`_HANDLERS` ~310, `apply_reject_pattern` ~271),
    `change_rollback.py`;
  - `runtime/change_intent_adapters/__init__.py` e `user_feedback.py`;
  - `docs/{it,en}/architecture/lifecycle.html`.
- **Passi:**
  1. Rimuovi il ramo `llm-v1` di `vaglio.judge`, il parametro `vaglio_judge` e
     il prompt, se `rg` non gli trova altri usi.
  2. Togli dal ciclo globale i tipi `materialize_pipeline`, `cache_pattern`,
     `dedupe_executors` e `reject_pattern`: handler, rollback e adapter
     `user_feedback` in `iter_all`.
  3. Archivia `rejected_patterns.jsonl` in `PATH_AUDIT`. **Non** lo migri in
     regole globali: sono dati personali.
  4. Correggi docstring e `lifecycle.html` (IT ed EN): stato vero dei
     componenti, anello 8 come rapporto.
- **Test:** le suite toccate; F1.1 aggiornato.
- **Coordinatore:** pubblica la doc con `./deploy.sh`.

#### F1.3 — Pulizia del backlog per tipo
- **Crea:** `internal/tools/rm0009_backlog_cleanup.py`, con dry-run di default.
- **Passi:** regole per tipo, tutte verso `superseded` con regola e prova.
  - Tipi ritirati → `superseded`.
  - Duplicati per la nuova impronta del §5.7: resta il più recente.
  - `extend_executor` il cui bersaglio non esiste più → `superseded`.
  - `create_executor` con bersaglio assente: **nessuna azione**, perché è normale.
  - Il dry-run stampa, per ogni id, regola ed evidenza; `--restore <id>` usa
    `superseded → proposed`.
- **Test:** un `create_executor` valido con bersaglio assente resta; il ripristino
  funziona.
- **Coordinatore:** esegue `--apply` sui dati del servizio.

#### F2.1 — Schema delle valutazioni
- **Leggi prima:** `change_intents.py`, `runtime/change_intent_adapters/_base.py`.
- **Passi:**
  1. Crea le tabelle del §5.2 e `change_intent_sources`, con `NOT NULL`, chiavi
     esterne verso `change_intents(id)` e i vincoli di unicità indicati.
  2. Aggiungi a `change_intents`:
     - `proposer_component_id`, `proposer_component_version`;
     - `origin_owner_id`;
     - `source_kind` (enum, compreso `unknown`);
     - `telos_goal`;
     - `contract_version`.
  3. Le enumerazioni `EvaluationDimension`, `EvaluationStatus` e `SourceKind`
     si definiscono qui.
- **Test:** `tests/runtime/learning/test_change_intent_evaluations.py`:
  - `source_event_id` nullo rifiutato;
  - una revisione dello stesso evento accettata;
  - chiave esterna verificata.

#### F2.2 — Scrittori, proiezione e conservazione
- **Passi:**
  1. `append_evaluation(...)`: idempotente per la chiave unica; restituisce l'id.
  2. `correct_evaluation(evaluation_id, value, ...)`: scrive `revision+1` con
     `supersedes_evaluation_id`.
  3. `current_evaluations(intent_id)`: l'ultima non superata, per
     `evaluation_id`.
  4. In `upsert_intent` togli `max()` (~405). Ogni lettura decisionale di
     `score` passa a `current_evaluations`.
  5. Conservazione notturna secondo la politica: resta sempre l'ultima per
     (intent, dimensione, valutatore).
- **Test:** 0,8 corretto a 0,4 resta 0,4, con la storia; un'altra dimensione non
  lo cambia.

#### F2.3 — Giudizio tipizzato
- **Leggi prima:** `alignment_engine.py` (ritorni `[]` ~214-228, `except` ~301),
  `runtime/change_intent_adapters/telos.py`, `telos_loader.py`.
- **Passi:**
  1. `judge_intent(intent) -> JudgeResult(status: ok|not_applicable|failed, value,
     telos_goal, reason)`. Distingue un vero «non applicabile» da un errore,
     cioè un `[]` prodotto da un'eccezione o da un JSON non valido.
  2. `ok` e `not_applicable` scrivono con
     `source_event_id=align:<intent_id>:<versione TELOS>:<versione valutatore>`.
     `failed` non scrive nulla e ritenta secondo la politica.
- **Test:**
  - valutatore non disponibile;
  - risposta non valida;
  - non applicabile reale;
  - nuova versione TELOS che produce una nuova valutazione.

#### F2.4 — Privacy strutturale
- **Leggi prima:** `ChangeIntent` (`intent_summary`, `intent_rationale`,
  `intent_body`), gli adapter.
- **Passi:**
  1. Uno schema chiuso del corpo per ogni tipo del §5.7; un campo fuori schema
     o testuale viene rifiutato in `upsert_intent`.
  2. `intent_summary` si genera da tipo, verbo, oggetto e fine, tramite chiavi
     i18n. Il testo del modello o delle lenti resta nello store amministrativo
     esistente (`telos_proposals_store`).
  3. La provenienza si mostra solo in `/admin/changes`: mai in catalogo, Tutor
     o riepilogo ordinario.
- **Test:** due owner con valori privati diversi convergono nello stesso intent,
  e nessun valore compare nelle viste dell'altro.

#### F3.1 — `GapEvidence` e `lacuna_events`
- **Leggi prima:** `engine/dispatch.py` (chiamate a `terminator.explain` ~7508 e
  ~7862, le uscite con `final_kind`), `engine/terminator.py` (`_ensure_db` ~51,
  `_record_lacuna` ~76).
- **Passi:**
  1. `@dataclass(frozen=True) GapEvidence`:
     - `turn_id`, `step_ordinal`, `principal_user_id`;
     - `gap_kind` (`GapKind`);
     - `intent_hash`, `catalog_generation` (il `tools_sig` del turno);
     - `classifier_version`.
  2. Inventario delle uscite non soddisfatte del dispatch
     (`rg -n 'final_kind' runtime/engine/dispatch.py`). Ognuna passa la sua
     `GapEvidence` al terminator, oppure ha un motivo scritto per non farlo.
  3. `lacuna_events`: `event_id = sha256(turn_id, step_ordinal, gap_kind,
     intent_hash)` come chiave primaria, più i campi di `GapEvidence`.
     `lacune` resta come vista storica.
- **Test:** `tests/runtime/engine/test_lacuna_events.py`: retry → una riga; ogni
  uscita inventariata emette o motiva.

#### F3.2 — Classificazione
- **Leggi prima:** `_dropped_required_verbs` (~2129), `prefilter.implements_intent_verb`
  (~212), le classi `placement`, `permission_denied` e `capability_unavailable`.
- **Passi:**

  | Condizione | `gap_kind` |
  |---|---|
  | `_dropped_required_verbs` non vuoto | `plan_step_missing` |
  | nessun executor del catalogo del turno implementa verbo e oggetto | `capability_absent` |
  | le tre classi d'errore, in una tabella unica nel modulo di F3.1 | `capability_unavailable` |
  | la classe esistente `out_of_scope` | `out_of_scope` |
  | resto | `unknown` |

- **Test:** un caso per tipo; un executor esistente ma negato non diventa mai
  `capability_absent`.

#### F3.3 — Riconciliazione
- **Passi:**
  1. Eventi attesi = turni `origin=user` con esito `error` o `partial` il cui
     `error_class` rientra nell'insieme chiuso delle classi di capacità. Input
     non valido, servizio esterno e consenso restano esclusi.
  2. Join con `lacuna_events` per turno: mancanti, duplicati, orfani,
     scostamento.
- **Test:** un collaudo, un errore non pertinente, un mancante, un duplicato.

#### F4.1 — Sorgente S1
- **Leggi prima:** `learning_loop.py` (~34, ~37), `jobs/change_intent_materialize.py`
  (`task_change_intent_materialize`), `runtime/change_intent_adapters/__init__.py`
  (`iter_all`).
- **Passi:**
  1. L'adapter S1 legge `lacuna_events` con `gap_kind=capability_absent`, in join
     con i turni `origin=user`. Esclude collaudo, sistema, `unknown` e legacy.
  2. Conta al massimo un evento per (principal, `intent_hash`, giorno UTC). Sopra
     la soglia: ricontrolla il catalogo corrente con `implements_intent_verb`.
     Se ora esiste un executor, nessuna proposta.
  3. Produce un `create_executor` canonico, con
     `source_event_id=gap:<event_id>` per ogni evento contato.
  4. Registralo in `iter_all`.
- **Test:**
  - tre collaudi non producono nulla;
  - un executor aggiunto fra evento e aggregazione blocca la proposta;
  - tre turni reali in tre giorni producono un solo intent.

#### F4.2 — Sorgente S2
- **Leggi prima:** `telos_introspect.py` (`run_all_telos` ~425),
  `runtime/change_intent_adapters/telos.py`, `telos_proposals_store.py`.
- **Passi:**
  1. L'adapter `telos` produce solo `create_executor` o `extend_executor`
     canonici:
     - verbo e oggetto validati da `vocab.py`;
     - `telos_goal`;
     - `source_event_id=lens:<id>:<versione TELOS>`.
  2. Il testo della lente non entra nell'intent (F2.4).
- **Test:** una proposta di lente produce un intent canonico, senza testo libero.

#### F4.3 — Sorgente S3 e `promote_plan`
- **Leggi prima:** `engine/autopath.py` (`_promote_autopath` ~644, colonna
  `shadow`, `seed_from_run` ~699).
- **Crea:** `runtime/change_intent_adapters/optimization.py`.
- **Passi:**
  1. Candidati: piani con `shadow=1`, senza valori letterali nello scheletro,
     serviti in turni `origin=user` riusciti.
  2. `source_event_id=plan:<plan_hash>:<data>`.
  3. Handler `apply_promote_plan(ci, operation_id)`: porta `shadow=0` in modo
     idempotente.
  4. `observe_effect`: legge `shadow`.
  5. Rollback `demote_autopath(ihash, fhash)` (nuova funzione): riporta
     `shadow=1`.
- **Test:** promozione, osservazione e rollback; un piano con valori letterali
  viene escluso.

#### F4.4 — Job, transazione e raggiungibilità
- **Passi:**
  1. In `task_change_intent_materialize`, per ogni intent prodotto: upsert e
     sorgente in un'unica transazione, poi `judge_intent`.
  2. Test dal job registrato: adapter → intent → sorgente → valutazione.
- **Test:** `tests/runtime/learning/test_change_intent_materialize.py`.

#### F5.1 — `build_change_facts`
- **Crea:** `runtime/growth_facts.py` e `tests/runtime/learning/test_growth_facts.py`.
- **Passi:**
  1. `Fact(value, source, version)`.
  2. `build_change_facts(intent_id) -> ChangeFacts`, che rilegge:
     - lo store dei contratti e la ricevuta Birth (capability, ambiti,
       `critical`, reversibilità con `undo.round_trip`, generazione);
     - la riga autopath per `promote_plan`;
     - la politica e le regole di rifiuto.
  3. La reversibilità vale `guaranteed`, `conditional` o `none`, come al §5.3.
  4. Un campo del corpo dell'intent non è mai fonte di un fatto.
- **Test:** anti-auto-attestazione per ogni fatto; ricevuta assente → fatto
  `unverified`.

#### F5.2 — `decide_change`
- **Crea:** `runtime/growth_decision.py` e `tests/runtime/learning/test_growth_decision.py`.
- **Passi:**
  1. `ChangeDecision`: `auto`, `ask_user`, `deny`, `blocked`.
  2. `ReasonCode` chiuso.
  3. `DecisionMoment`: `trial` o `activation`.
  4. Implementa la tabella del §5.3 riga per riga, nell'ordine.
- **Test:** tabella esaustiva delle precedenze; fatti mancanti → mai `auto`.

#### F5.3 — Richieste di decisione e riepilogo
- **Leggi prima:** `jobs/promoter_digest.py`, `jobs/promoter_state.py`
  (`pending_notification`, `mark_notified`), `http_routes_admin.py`
  (`admin_change_action`).
- **Passi:**
  1. Per ogni `ask_user`, `one_shot_tokens.issue(purpose=change_decision,
     binding_digest=sha256(intent_id, row_version, impronta, digest dei fatti,
     digest dell'effetto, versione della politica, canale, destinatario))`.
  2. Il riepilogo mostra le domande entro il budget, in coda ordinata, e il
     resoconto `auto`.
  3. La risposta (callback del riepilogo o `/admin/changes`):
     - principal con ruolo logico `host` o chiave amministrativa;
     - `consume`;
     - `transition` con compare-and-swap.

     Un «no» crea `reject_rule` (F5.5).
- **Test:**
  - ospite;
  - inoltro (destinatario diverso);
  - replay;
  - doppio clic;
  - scadenza;
  - intent rivalutato;
  - risposta valida → una sola transizione.

#### F5.4 — Protocollo di applicazione ed epoca
- **Leggi prima:** `change_applier.py` (`_HANDLERS`), `change_intents.py`
  (`mark_applied` ~608), `synth_request.py` (`handle_synth_request`, verso Birth),
  RM-0008 (`BirthIntent`).
- **Passi:**
  1. Per `create_executor` ed `extend_executor`: D1 `auto` o approvata →
     `proposed → staged` con richiesta Birth idempotente (`operation_id`).
     Alla ricevuta verificata → `awaiting_activation`, poi D2.
  2. `accepted → applying`, con lease e `operation_id`; handler idempotente;
     `observe_effect`; `applied` + `bump_epoch`, nella stessa transazione.
  3. Un recupero, all'avvio e ogni notte, gestisce le lease scadute come al §5.4.
- **Test:**
  - iniezione di crash prima dell'effetto, dopo l'effetto e prima della
    ricevuta, dopo la ricevuta;
  - due worker sullo stesso intent;
  - senza ricevuta Birth l'attivazione è impossibile.

#### F5.5 — `reject_rule` append-only
- **Passi:**
  1. Tabelle `rejection_rules`, con la richiesta di decisione e il principal, e
     `rejection_revocations`.
  2. Regola attiva = non revocata e non scaduta.
  3. `upsert_intent` rifiuta di riaprire un'impronta con regola attiva e scrive
     una valutazione `rejections`.
  4. Alla scadenza, riapertura atomica `rejected → proposed`.
  5. Ogni cambio chiama `bump_epoch`.
- **Test:**
  - un `blocked` riparato torna valutabile;
  - un «no» dell'host impedisce la rigenerazione fino a scadenza o revoca;
  - la revoca è append-only.

#### F5.6 — Beneficio e rollback verificato
- **Leggi prima:** `change_rollback.py` (ritorni con `error` ~58-122).
- **Passi:**
  1. `benefit` con metrica, unità, finestra, campione, baseline e osservato,
     secondo la formula del §5.7.
  2. `RollbackResult(ok, verified, error_code)`: un avvolgitore tipizzato sulle
     funzioni esistenti.
  3. Stato `rolled_back` solo con `verified`; altrimenti `rollback_failed` e
     nuovi tentativi.
  4. Inutilizzo: TTL della politica (piano → in ombra; executor → richiesta di
     ritiro via RM-0008).
- **Test:**
  - campione insufficiente → `insufficient_evidence`, nessun rollback;
  - rollback fallito → `rollback_failed`;
  - peggioramento → rollback verificato.

#### F6.1 — `InvocationEnvelope` e `decide_invocation`
- **Crea:** `runtime/invocation_authority.py` e `tests/runtime/safety/test_invocation_authority.py`.
- **Passi:**
  1. Un envelope immutabile con i campi del §5.8.
  2. `decide_invocation(envelope, facts, policy)`: intersezione aggiuntiva, con
     veto fail-closed.
  3. Riusa `Fact` e le enumerazioni di `growth_facts.py`.
- **Test:**
  - ogni veto;
  - due politiche materialmente diverse;
  - una differenza di sola provenienza non cambia nulla.

#### F6.2 — Hook in ombra e contatori
- **Leggi prima:** i punti d'ingresso del §5.8.
- **Passi:**
  1. `admit_invocation(envelope)` in ciascun percorso del §5.8, in ombra.
  2. Contatori in memoria per processo, con flush aggregato secondo la politica
     in `invocation_authority_stats`, con watermark
     `(avvio del processo, sequenza)`; nessuna scrittura per invocazione.
  3. Un test sul grafo delle chiamate: con l'hook forzato a negare, **nessun**
     percorso produce effetti.
- **Test:**
  - locale;
  - remoto;
  - builtin;
  - verb-unique;
  - durevole;
  - durevole interno dichiarato;
  - riavvio e multiprocesso per i contatori.

#### F6.3 e F6.4 — Enforcement (dopo RM-0008/F5, FS-A, FS-B)
- **Passi:**
  1. Il veto nega prima dell'effetto.
  2. Remoto: token all'accodamento, consumato in `invocations.next_invocation`.
  3. Durevole: token per tentativo, consumato all'inizio del tentativo.
  4. La revoca per legame invalida code, retry e cache.
- **Test:**
  - replay concorrente;
  - riavvio;
  - scadenza;
  - revoca fra accodamento e consumo;
  - zero effetti.

#### FS-A — Runner unico
- **Leggi prima:** `synth_request.py` (~60, ~72), `test_runner.py`,
  `executor_birth_runner.py` (`run_birth_phase` ~427), `executor_birth_identity.py` (~275).
- **Passi:**
  1. Inventario: `rg -n "test_runner" .` sull'intero repository e sull'albero
     della release installata; i 24 manifest con `setup`, `teardown` o `env`.
  2. `_validate_birth_tests` usa `run_birth_phase`.
  3. `setup`, `teardown` ed `env` vengono rifiutati: errore
     `legacy_test_field_refused`.
  4. Il coordinatore ripubblica i 24 manifest senza quei campi, dal ciclo normale.
  5. Rimuovi `test_runner.py` e i campi dalla grammatica.
- **Test:** isolamento di rete, processi, utente e IPC, ambiente ammesso, cwd
  effimera, limiti di CPU, RAM e output, morte dell'intero albero; ogni campo
  legacy rifiutato.

#### FS-B — Radici protette e broker
- **Leggi prima:** `sandbox.py` (~549), `credentials.py`, gli executor
  `find_credentials`, `set_credentials` e `delete_credentials`, `vaglio.py`
  (~51), `config.PATH_USER_CONFIG` (~123), `BUILTIN_INPROC_SPECS` e
  `scripts/generate_builtin_executor_contracts.py`.
- **Passi:**
  1. Le tre funzioni diventano builtin nel core: owner dal principal,
     `assert_no_secrets_in_return`.
  2. Via i mount di vault e `admin.key`.
  3. `protected_roots()`: tutto il piano di controllo (configurazione, stato,
     store dei contratti, Birth, vault, chiavi), con eccezioni tipizzate.
  4. Apertura con `openat2(RESOLVE_NO_SYMLINKS | RESOLVE_BENEATH)`, oppure
     cammino no-follow su ogni segmento.
- **Test:**
  - nessun mount di chiavi in bwrap;
  - `~`, percorso assoluto, `XDG_CONFIG_HOME`, symlink e sostituzione durante
    l'apertura negati;
  - nessun segreto nei log.
- **Turno reale:** «quali credenziali ho salvato?» restituisce solo metadati.

### A.5 Ordine e matrice dei file

1. **Sequenza:**
   `P0 → P1 → P2 → F0.1 → F0.2 → F0.3 → F1.1 → F1.2 → F1.3 → F2.1 → F2.2 →
   F2.3 → F2.4 → F3.1 → F3.2 → F3.3 → F4.1 → F4.2 → F4.3 → F4.4 → F5.1 → F5.2
   → F5.3 → F5.4 → F5.5 → F5.6 → F6.1 → F6.2`. F6.3 e F6.4 vengono dopo
   RM-0008/F5.
2. **Parallelismo consentito** solo fra unità senza file in comune:

| Unità | File principali | Conflitto con |
|---|---|---|
| FS-A | `synth_request.py`, `test_runner.py`, `executor_birth_runner.py`, `executor_birth_identity.py` | nessuna unità F; si può fare subito in parallelo |
| FS-B | `sandbox.py`, `credentials.py`, `vaglio.py`, `config.py`, builtin delle credenziali, generatore dei builtin | F1.2 (`vaglio.py`): non contemporanee |
| F0.x | `agent_runtime.py` (`TurnLog`, `run_turn`), `http_auth.py`, `users.py`, route HTTP | F6.2 (`agent_runtime.py`): F6.2 dopo F0 |
| P2, F2.x, F5.x | `change_intents.py`, `change_applier.py`, `jobs/` | sequenziali fra loro |
| F3.x | `engine/dispatch.py`, `engine/terminator.py` | F1.2 (`dispatch.py`): F3 dopo F1.2 |

3. Il coordinatore raggruppa le unità concluse in un ciclo di rilascio ed
   esegue i turni reali delle schede.

## Appendice B — Esito dei 22 rilievi della revisione 5

Il testo integrale dei rilievi e la loro verifica nel codice sono nella storia
Git (appendice C della revisione 5, commit `ad37442c`). L'esito dei rilievi
della revisione 4 è nel commit `dbf3f1d5`.

| Rilievo | Esito | Chiusura nel testo | Prova di chiusura |
|---|---|---|---|
| R5-01 Ciclo circolare | accolto | §5.3 (D1 prova, D2 attivazione), §5.4, stati P2, F5.4 | F5.4: richiesta Birth idempotente, ricevuta verificata, una sola attivazione; senza ricevuta nessuna attivazione |
| R5-02 Costruttore dei fatti | accolto | §5.3, F5.1 (`build_change_facts`, reversibilità `guaranteed`/`conditional`/`none`) | F5.1: anti-auto-attestazione per ogni fatto; F5.2: tabella delle precedenze |
| R5-03 Contratto di approvazione | accolto | §5.5, P2 (`one_shot_tokens`), F5.3 | F5.3: ospite, inoltro, replay, doppio clic, scadenza, rivalutazione |
| R5-04 Atomicità | accolto | P2 (UNIQUE, `row_version`, compare-and-swap), §5.4, F5.4 | P2: due connessioni; F5.4: crash a ogni confine, due worker |
| R5-05 Tipi incoerenti | accolto; ritirati `materialize_pipeline`, `cache_pattern`, `dedupe_executors`, `reject_pattern` | §5.7, F1.2, F4.3 | F1.1: registro dei collegamenti; F4.3: promote, observe, rollback |
| R5-06 Owner operativo | accolto | §5.8 (`InvocationEnvelope`), F6.1 | F6: due utenti, riuso di argomenti, ricevute e riprese negato |
| R5-07 Veto e rifiuto | accolto | §5.3 (`blocked`), F5.5 | F5.5: veto riparato valutabile; rifiuto host che persiste; revoca append-only |
| R5-08 Punto unico e monouso | accolto | §5.8, F6.1-F6.4 | F6.2: grafo delle chiamate con zero effetti; F6.3-F6.4: replay, riavvio, scadenza, revoca |
| R5-09 Dati privati | accolto | §1, §5.7 (`intent_hash`), F2.4 | F2.4: due owner convergono senza fuga di valori |
| R5-10 Selezione dei turni | accolto | §5.1, F3.3, F4.1 | F4.1: collaudi esclusi, catalogo ricontrollato, tre turni reali |
| R5-11 Identità e append-only | accolto | §5.2, §5.7, F2.1-F2.2 | F2.1: `NOT NULL` e revisione; P2: concorrenza |
| R5-12 Pulizia errata | accolto | F1.3 | F1.3: `create_executor` con bersaglio assente conservato; ripristino |
| R5-13 Beneficio e rollback | accolto | §5.2, §5.4, §5.6, F5.6 | F5.6: evidenza insufficiente, rollback verificato |
| R5-14 FS e parallelismo | accolto; `setup`, `teardown` ed `env` **rifiutati** | FS-A, FS-B, A.5 | test di isolamento; alias e `openat2`; matrice dei file |
| R5-15 Test AST | accolto: registro chiuso con prove di esecuzione | principio 4.9, F1.1 | F1.1: togliere un collegamento fa fallire la riga |
| R5-16 Cache | accolto | §5.4 (`global_change_epoch` in `tools_sig`), P2 | P2: cambio d'epoca; F5.5: rifiuto e revoca |
| R5-17 Collegamenti F3 e F4 | accolto | F3.1 (`GapEvidence`, inventario delle uscite), F4.4 | F4.4: dal job registrato alla valutazione |
| R5-18 Contatori e rollback | accolto | §5.4, F5.6, F6.2 | F6.2: riavvio e multiprocesso; F5.6: `rollback_failed` |
| R5-19 Esiti e ruoli | accolto | F0, F0.1-F0.2 | F0.1: tabella di tutti i `final_kind`; F0.2: host, ospite, collaudo, LAN, ripresa |
| R5-20 Giudizio tipizzato | accolto | §5.2, F2.3 | F2.3: indisponibile, non valido, non applicabile, nuova versione |
| R5-21 Percorsi e identificativi | accolto | §5.7 (formule), F4.1-F4.4 (`runtime/change_intent_adapters/`, `iter_all`, job) | F4.4 |
| R5-22 Baseline e politica | accolto, con una modifica: una politica non valida sospende la **sola** crescita, non Metnos (decisione di Roberto sul non bloccante) | P0, P1, §5.6 | P0: due report identici; P1: pipeline sospesa, servizio vivo |
| C.4.5 Due agenti medium indipendenti | accolto come condizione del giro adversarial | §12 | eseguita dal coordinatore |

## Appendice C — Elenco lavori di riferimento

> **A che cosa serve.** È l'elenco atteso di file, simboli, schemi, transizioni,
> vincoli e test. Il coordinatore lo confronta con gli elenchi prodotti da due
> agenti medium indipendenti (§12). In caso di differenza con le schede
> dell'appendice A vale questo elenco, e la scheda va corretta. Verificato sul
> codice del 14/9: ogni file indicato come «modifica» esiste; ogni «nuovo» non
> esiste ancora. Legenda del tipo: N = nuovo, M = modifica, R = rimozione,
> G = migrazione, T = test.

### C.1 Lavori per unità

**P0 — Baseline**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| P0-1 | N | `internal/tools/rm0009_baseline.py` | `snapshot()`, `report()` | istantanea con `sqlite3.Connection.backup` e watermark `--until`; report da istantanea | `tests/internal/test_rm0009_evidence.py::test_two_reports_on_same_snapshot_are_identical` |
| P0-2 | T | `tests/internal/test_rm0009_evidence.py` | — | gli archivi d'origine restano immutati | `::test_sources_are_not_modified` |

**P1 — Politica**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| P1-1 | N | `runtime/growth_policy.py` | `GrowthPolicy`, `load_growth_policy()`, `GrowthPolicyError`, `version` | valori del §5.6; precedenza `METNOS_GROWTH_*` > nomi storici | `tests/runtime/learning/test_growth_policy.py::test_defaults`, `::test_new_names_win_over_legacy` |
| P1-2 | M | `runtime/learning_loop.py` | `PROPOSE_SEEN` (~29) | legge la politica | `::test_learning_loop_reads_policy` |
| P1-3 | M | readiness (`stack_reconcile.check`, voce nuova) | `growth_policy` | `growth_policy_invalid` sospende la sola crescita | `::test_invalid_policy_pauses_growth_only` |

**P2 — Fondamenta**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| P2-1 | G | `runtime/change_intents.py` | `init_db` (~304) | duplicati per impronta → `superseded`; `CREATE UNIQUE INDEX` sull'impronta; `row_version`; `PRAGMA user_version` | `tests/runtime/learning/test_change_intents_atomicity.py::test_migration_dedupes_then_unique` |
| P2-2 | M | `runtime/change_intents.py` | `ALL_STATES` (~111), `_TRANSITIONS` (~118) | stati e transizioni della tabella C.3 | `::test_transition_table_matches_roadmap` |
| P2-3 | M | `runtime/change_intents.py` | `upsert_intent` (~356) | `INSERT … ON CONFLICT(fingerprint) DO UPDATE … RETURNING id`; `max()` tolto | `::test_concurrent_upsert_single_intent` |
| P2-4 | M | `runtime/change_intents.py` | `_transition` (~544) → `transition(...)` | compare-and-swap su stato e `row_version` | `::test_stale_version_rejected` |
| P2-5 | N | `runtime/one_shot_tokens.py` | `issue`, `consume`, `revoke_by_binding`, `gc`, `TokenPurpose` | tabella in `PATH_USER_STATE`; consumo con UPDATE condizionato | `tests/runtime/safety/test_one_shot_tokens.py::test_double_consume_fails`, `::test_expired_fails`, `::test_wrong_binding_fails` |
| P2-6 | M | `runtime/change_intents.py` | `bump_epoch(conn)`, `current_epoch()` | tabella a riga unica `global_change_epoch` | `::test_epoch_bumps_in_same_transaction` |
| P2-7 | M | `runtime/engine/cache_validity.py` | `tools_sig` (~109) | include l'epoca (memoria invalidata da `PRAGMA data_version`) | `tests/runtime/engine/test_cache_validity.py::test_epoch_change_invalidates` |

**F0 — Osservabilità**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| F0-1 | N | `runtime/turn_outcome.py` | `TurnOrigin`, `TurnOutcome`, `derive_outcome()` | tabella della scheda F0.1; enumerazioni `(str, Enum)` | `tests/runtime/infra/test_turn_provenance.py::test_outcome_table_covers_all_final_kinds` |
| F0-2 | M | `runtime/agent_runtime.py` | `TurnLog` (~4109), `TurnLog.write` (~5173) | campi `origin`, `outcome`, `intent_hash`; esito calcolato una volta | `::test_intent_hash_has_no_user_words`, `::test_legacy_record_reads_unknown` |
| F0-3 | M | `runtime/agent_runtime.py` | `run_turn` (~7502) | parametro obbligatorio `origin` | `::test_run_turn_requires_origin` |
| F0-4 | N | `runtime/turn_outcome.py` | `resolve_turn_origin()` | ruolo HTTP + `users.get_user` (~287) → origine | `tests/runtime/http/test_http_turn_ownership.py::test_origin_host_guest_test_lan_resume` |
| F0-5 | M | `runtime/users.py` | `ROLES` (~46) | aggiunge `test`, con autorità di `guest` | `::test_test_role_has_guest_authority` |
| F0-6 | M | chiamanti di `run_turn` (`http_routes_agent.py`, `channels/daemon.py`, `recurring_tasks.py`, `change_applier.py`, `agent_server.py`, `orchestration.py`, `smoke.py`, `testing/`) | — | passano `origin`; il corpo HTTP non lo imposta | `::test_body_origin_ignored`, `::test_scheduler_turn_is_system` |
| F0-7 | M | `runtime/http_routes_admin.py` | `admin_turns` (~1040) | colonne `origin` ed `outcome` | `tests/runtime/http/test_admin_turns_view.py::test_origin_outcome_visible` |
| F0-8 | N | `runtime/growth_indicators.py` | `indicators_turns()` | indicatori 5 e 7 | `tests/runtime/learning/test_growth_indicators.py::test_turn_indicators` |
| F0-9 | M | `runtime/scheduler_v2/builtin_callbacks.py`, `runtime/nightly_orchestrator.py` | `NIGHTLY_SEQUENCE` (~37) | callback `growth_indicators`, dopo `promoter_digest` | `::test_nightly_registration` |

**F1 — Componenti e backlog**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| F1-1 | N | `tests/runtime/learning/test_growth_cycle_links.py` | tabella `LINKS` | registro chiuso con prove di esecuzione | `::test_every_link_has_observable_effect` |
| F1-2 | R | `runtime/vaglio.py`, `runtime/engine/executor.py`, `runtime/engine/dispatch.py`, `runtime/testing/populate_cases.py`, `runtime/prompts/{it,en}/vaglio.j2` | ramo `llm-v1`, `vaglio_judge` | ritiro | suite Vaglio e motore |
| F1-3 | R | `runtime/change_applier.py`, `runtime/change_rollback.py` | handler e rollback di `materialize_pipeline`, `cache_pattern`, `dedupe_executors`, `reject_pattern` | ritiro | `::test_retired_kinds_have_no_handler` |
| F1-4 | R | `runtime/change_intent_adapters/__init__.py` | `iter_all`: `iter_user_feedback` | tolto dal ciclo globale | `::test_user_feedback_not_global` |
| F1-5 | G | `rejected_patterns.jsonl` | — | archiviato in `PATH_AUDIT`; nessuna migrazione globale | `::test_jsonl_archived_not_migrated` |
| F1-6 | M | `runtime/change_applier.py` (~272), `docs/{it,en}/architecture/lifecycle.html` | docstring e pagine | stato vero dei componenti | revisione documentale |
| F1-7 | N | `internal/tools/rm0009_backlog_cleanup.py` | regole per tipo, `--apply`, `--restore` | pulizia verso `superseded` | `tests/internal/test_rm0009_backlog_cleanup.py::test_create_executor_with_absent_target_kept`, `::test_restore` |

**F2 — Valutazioni e privacy**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| F2-1 | G | `runtime/change_intents.py` | tabelle `change_intent_evaluations`, `change_intent_sources`; colonne nuove | schema C.2 | `tests/runtime/learning/test_change_intent_evaluations.py::test_null_source_event_rejected`, `::test_revision_of_same_event_accepted`, `::test_foreign_key` |
| F2-2 | M | `runtime/change_intents.py` | `append_evaluation`, `correct_evaluation`, `current_evaluations` | proiezione per `evaluation_id` | `::test_downward_correction_persists` |
| F2-3 | M | `runtime/change_intent_adapters/*.py`, `runtime/http_routes_admin.py`, `runtime/jobs/*.py` | letture di `score` | passano a `current_evaluations` | `::test_no_decision_reads_score` |
| F2-4 | N | `runtime/growth_judge.py` | `judge_intent()`, `JudgeResult` | `ok`, `not_applicable`, `failed`; ritenti | `tests/runtime/learning/test_growth_judge.py::test_failed_writes_nothing`, `::test_new_telos_version_new_evaluation` |
| F2-5 | M | `runtime/alignment_engine.py` (~214-301) | ritorni `[]` | distinguere errore da vuoto (eccezione tipizzata) | `::test_invalid_json_is_failure` |
| F2-6 | M | `runtime/change_intents.py` | `upsert_intent` | schema chiuso del corpo per tipo; testo libero rifiutato | `tests/runtime/learning/test_change_intent_privacy.py::test_two_owners_converge_without_leak` |
| F2-7 | M | `runtime/change_intent_adapters/telos.py`, `runtime/telos_proposals_store.py` | sintesi | testo della lente solo nello store amministrativo | `::test_lens_text_not_in_intent` |

**F3 — Lacune**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| F3-1 | N | `runtime/engine/gap_evidence.py` | `GapEvidence`, `GapKind`, tabella delle classi d'errore | contratto tipizzato | `tests/runtime/engine/test_lacuna_events.py::test_gap_kinds` |
| F3-2 | M | `runtime/engine/dispatch.py` | `terminator.explain` (~7508, ~7862), uscite con `final_kind` | inventario; ogni uscita emette o motiva | `::test_every_unfulfilled_exit_emits_or_documents` |
| F3-3 | M | `runtime/engine/terminator.py` | `_ensure_db` (~51), `_record_lacuna` (~76) | tabella `lacuna_events`; `lacune` storica | `::test_retry_single_row` |
| F3-4 | M | `runtime/engine/dispatch.py` | `_dropped_required_verbs` (~2129), `prefilter.implements_intent_verb` (~212) | classificazione della scheda F3.2 | `::test_denied_executor_not_absent` |
| F3-5 | M | `runtime/growth_indicators.py` | `reconcile_gaps()` | indicatore 4 | `tests/runtime/learning/test_growth_indicators.py::test_reconciliation_missing_duplicate_orphan` |

**F4 — Sorgenti**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| F4-1 | N | `runtime/change_intent_adapters/gaps.py` | `iter_gaps()` | S1 con join `origin=user`, conteggio per giorno, ricontrollo del catalogo | `tests/runtime/learning/test_learning_loop.py::test_test_turns_excluded`, `::test_catalog_recheck_blocks`, `::test_three_real_days_one_intent` |
| F4-2 | M | `runtime/learning_loop.py` | `_CAPABILITY_GAP_CLASSES` (~34), `propose_from_lacuna` (~37) | ritirati a favore di `iter_gaps` | `::test_legacy_trigger_removed` |
| F4-3 | M | `runtime/change_intent_adapters/telos.py` | `iter_telos` | solo `create`/`extend` canonici, validati su `vocab.ACTIONS` e `vocab.OBJECTS` | `::test_lens_produces_canonical_intent` |
| F4-4 | M | `runtime/proposal_actions.py` (`on_accept` ~97), `runtime/telos_synth_consumer.py` (`run_once` ~118) | marker `synt_pending` | l'accettazione dal cruscotto TELOS crea o aggiorna l'intent e passa da D1; il consumer non chiama più direttamente `handle_synth_request` | `::test_dashboard_accept_goes_through_d1` |
| F4-5 | N | `runtime/change_intent_adapters/optimization.py` | `iter_optimization()` | S3 dai piani `shadow=1` | `tests/runtime/learning/test_optimization_adapter.py::test_plan_with_literals_excluded` |
| F4-6 | M | `runtime/engine/autopath.py` | `_promote_autopath` (~644), `demote_autopath()` nuovo | promozione e ritorno idempotenti | `::test_promote_observe_demote` |
| F4-7 | M | `runtime/change_intent_adapters/__init__.py` | `iter_all` | registra `iter_gaps` e `iter_optimization` | `::test_iter_all_includes_new_adapters` |
| F4-8 | M | `runtime/jobs/change_intent_materialize.py` | `task_change_intent_materialize` (~26) | intent + sorgente in una transazione, poi `judge_intent` | `tests/runtime/learning/test_change_intent_materialize.py::test_job_to_evaluation` |

**F5 — Decisione ed effetto**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| F5-1 | N | `runtime/growth_facts.py` | `Fact`, `ChangeFacts`, `build_change_facts()`, `Reversibility` | fonti autorevoli; `undo.round_trip` (`executor_birth_properties.py` ~113) | `tests/runtime/learning/test_growth_facts.py::test_intent_body_never_a_fact`, `::test_missing_receipt_unverified` |
| F5-2 | N | `runtime/growth_decision.py` | `decide_change()`, `ChangeDecision`, `DecisionMoment`, `ReasonCode` | tabella del §5.3 | `tests/runtime/learning/test_growth_decision.py::test_precedence_table`, `::test_missing_fact_never_auto` |
| F5-3 | M | `runtime/jobs/promoter_digest.py`, `runtime/jobs/promoter_state.py` | selezione e resoconto | domande `ask_user` entro il budget, in coda; resoconto `auto` | `tests/runtime/learning/test_promoter_digest.py::test_budget_queue_no_loss`, `::test_auto_only_in_report` |
| F5-4 | M | `runtime/channels/daemon.py` | `_handle_promoter_callback` (~1526), dispatch dei prefissi (~1766) | nuovo formato `chg:<token_id>:y` o `:n`; `consume` + `transition` | `tests/runtime/channels/test_change_decision_callback.py::test_guest_forward_replay_expiry_rejected` |
| F5-5 | M | `runtime/http_routes_admin.py` | `admin_change_action` | stessa verifica (token o chiave amministrativa) | `::test_admin_action_uses_cas` |
| F5-6 | M | `runtime/change_applier.py` | `_HANDLERS` (~310), `apply_create_executor` (~60) | protocollo `applying`; D1 → `submit_synth_multistage_birth`, D2 dopo la ricevuta; extend → `submit_change_extend_birth`; rollback → `submit_change_rollback_birth` (`executor_birth_intent.py` ~83-91) | `tests/runtime/learning/test_change_applier.py::test_no_activation_without_receipt`, `::test_crash_at_each_boundary_converges` |
| F5-7 | N | `runtime/change_recovery.py` | `recover_applying()` | lease scadute: `present`, `absent`, `unknown` | `tests/runtime/learning/test_change_recovery.py::test_recovery_paths` |
| F5-8 | M | `runtime/change_intents.py` | tabelle `rejection_rules`, `rejection_revocations` | regola attiva; riapertura alla scadenza; epoca | `tests/runtime/learning/test_rejection_rules.py::test_blocked_repaired_is_reevaluated`, `::test_host_no_persists_until_expiry` |
| F5-9 | M | `runtime/change_rollback.py` (~58-122) | `RollbackResult` | avvolgitore tipizzato; `rolled_back` solo verificato | `::test_unverified_rollback_is_failed` |
| F5-10 | M | `runtime/growth_indicators.py` | `measure_benefit()` | metrica, campione, `insufficient_evidence`; TTL d'inutilizzo | `::test_insufficient_evidence_no_rollback` |

**F6 — Libertà modulata**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| F6-1 | N | `runtime/invocation_authority.py` | `InvocationEnvelope`, `decide_invocation()`, `admit_invocation()` | intersezione aggiuntiva, veto | `tests/runtime/safety/test_invocation_authority.py::test_each_veto`, `::test_provenance_does_not_change_authority` |
| F6-2 | M | `runtime/agent_runtime.py` | `_invoke_executor_impl` (~3456) | hook locale e remoto (prima di `invocations.enqueue_invocation`) | `::test_local_remote_zero_effect_on_deny` |
| F6-3 | M | `runtime/loader.py` | `invoke_verb_unique` (~520) | hook builtin e verb-unique | `::test_builtin_zero_effect_on_deny` |
| F6-4 | M | `runtime/durable_workloads/execution.py` | `_invoke_executor` (~190) | hook durevole | `::test_durable_zero_effect_on_deny` |
| F6-5 | M | `runtime/invocation_authority.py` | contatori, `flush()` | tabella `invocation_authority_stats` con watermark | `::test_counters_survive_restart_multiprocess` |
| F6-6 | M | `runtime/invocations.py` | `enqueue_invocation` (~681), `next_invocation` (~937) | token all'accodamento, consumo al prelievo (solo enforcement) | `tests/runtime/remote/test_invocation_scope.py::test_token_consumed_once_at_claim` |

**FS-A e FS-B**

| ID | Tipo | File | Simbolo | Lavoro | Test |
|---|---|---|---|---|---|
| FSA-1 | M | `runtime/synth_request.py` | `_validate_birth_tests` (~60, ~72) | usa `executor_birth_runner.run_birth_phase` (~427) | `tests/runtime/infra/test_executor_birth_runner.py::test_isolation_matrix` |
| FSA-2 | M | `runtime/executor_birth_identity.py` (~275) | grammatica dei test | `setup`, `teardown`, `env` → `legacy_test_field_refused` | `::test_legacy_fields_refused` |
| FSA-3 | G | 24 manifest in `executors/` | test `setup`/`teardown`/`env` | ripubblicazione dal ciclo normale (coordinatore) | inventario a zero |
| FSA-4 | R | `runtime/test_runner.py` | — | ritiro dopo l'inventario a zero | `rg -n "test_runner" .` a zero |
| FSB-1 | M | `runtime/user_preferences.py` (modello), nuovi builtin delle credenziali, `scripts/generate_builtin_executor_contracts.py` | `BUILTIN_INPROC_SPECS` | `find_credentials`, `set_credentials`, `delete_credentials` come builtin nel core | `tests/runtime/safety/test_credentials.py::test_builtin_returns_metadata_only` |
| FSB-2 | M | `runtime/sandbox.py` (~549) | ramo `metnos:credentials_metadata_only` | via i mount | `tests/runtime/safety/test_sandbox_runtime_bind.py::test_no_key_mounts` |
| FSB-3 | N | `runtime/protected_roots.py` | `protected_roots()`, `open_protected()` | radici del piano di controllo; `openat2` o cammino no-follow | `tests/runtime/safety/test_protected_roots.py::test_alias_symlink_race_denied` |
| FSB-4 | M | `runtime/vaglio.py` (~51) | `_FORBIDDEN_PATH_PATTERNS` | usa `protected_roots()` | `::test_vaglio_uses_protected_roots` |

### C.2 Schemi dei dati

| Database | Oggetto | Definizione |
|---|---|---|
| intent (`change_intents._conn`) | indice | `UNIQUE(fingerprint)` al posto di `idx_ci_fingerprint` |
| intent | colonne di `change_intents` | `row_version INTEGER NOT NULL DEFAULT 0`, `proposer_component_id`, `proposer_component_version`, `origin_owner_id`, `source_kind`, `telos_goal`, `contract_version`, `operation_id`, `lease_until` |
| intent | `change_intent_evaluations` | colonne del §5.2; `evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT`; `source_event_id NOT NULL`; FK `intent_id`; `UNIQUE(intent_id, dimension, evaluator_id, source_event_id, revision)` |
| intent | `change_intent_sources` | `intent_id` FK, `source_event_id NOT NULL`, `source_component_id`, `source_component_version`, `owner_id`, `created_at`; `UNIQUE(intent_id, source_event_id)` |
| intent | `rejection_rules`, `rejection_revocations` | `rule_id`, `fingerprint`, `token_id`, `principal`, `created_at`, `expires_at`; `rule_id`, `revoked_at`, `reason`, `principal` |
| intent | `global_change_epoch` | riga unica `epoch INTEGER NOT NULL` |
| stato (`PATH_USER_STATE`) | `one_shot_tokens` | `token_id PRIMARY KEY`, `purpose`, `binding_digest`, `expires_at`, `consumed_at`, `consumed_by` |
| lacune (`terminator._db_path`) | `lacuna_events` | `event_id PRIMARY KEY` più i campi di `GapEvidence` |
| stato | `invocation_authority_stats` | `process_start_id`, `sequence`, `executor`, `reason`, `count`, `window_start`; `UNIQUE(process_start_id, sequence, executor, reason)` |
| turni (`PATH_TURNS`) | record | campi `origin`, `outcome`, `intent_hash` (valori `.value`) |

### C.3 Transizioni degli intent (finale)

| Da | A |
|---|---|
| `proposed` | `staged`, `accepted`, `rejected`, `blocked`, `superseded` |
| `staged` | `awaiting_activation`, `failed`, `rejected`, `proposed`, `superseded` |
| `awaiting_activation` | `accepted`, `rejected`, `blocked`, `superseded` |
| `blocked` | `proposed`, `awaiting_activation` |
| `accepted` | `applying`, `rejected` |
| `applying` | `applied`, `failed`, `applying` (nuovo tentativo) |
| `applied` | `observed`, `rolled_back`, `rollback_failed` |
| `observed` | `finalized`, `rolled_back`, `rollback_failed` |
| `finalized` | `rolled_back`, `rollback_failed` |
| `rollback_failed` | `rolled_back`, `rollback_failed` |
| `failed` | `accepted`, `rejected` |
| `rejected` | `proposed` (alla scadenza o revoca della regola) |
| `superseded` | `proposed` (ripristino) |
| `rolled_back` | — (terminale) |

### C.4 Enumerazioni

- `TurnOrigin`, `TurnOutcome` (`turn_outcome.py`);
- `EvaluationDimension`, `EvaluationStatus`, `SourceKind` (`change_intents.py`);
- `GapKind` (`engine/gap_evidence.py`);
- `ChangeDecision`, `DecisionMoment`, `ReasonCode` (`growth_decision.py`);
- `Reversibility` (`growth_facts.py`);
- `TokenPurpose` (`one_shot_tokens.py`).

Tutte come `(str, Enum)`, persistite con `.value`.

### C.5 Dipendenze esterne aperte

| Dipendenza | Stato | Effetto su RM-0009 |
|---|---|---|
| API pubblica di ritiro di un executor in RM-0008 | oggi esiste solo l'interna `contract_store._install_retirement`; i submitter pubblici sono `submit_change_extend_birth`, `submit_change_rollback_birth`, `submit_synth_multistage_birth` | il ritiro per inutilizzo (F5-10) resta `ask_user`, con un'operazione manuale del coordinatore, finché RM-0008 non espone l'API |
| Certificazione RM-0008/F5 | aperta | D2 sempre `ask_user`; F6.3-F6.4 non partono |
| Principal di collaudo | da creare con `pairing.generate_code` / `consume_code` (~122, ~181) | operazione del coordinatore prima dei turni E2E di F0 |
