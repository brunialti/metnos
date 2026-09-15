# RM-0009 — Crescita allineata delle capacità

> **RM-0009**
> - **Stato:** `active`. Revisione 8, risultante dall'unione analizzata fra la
>   revisione 7 e la proposta dell'agente esterno. Non e ancora `ready`: prima
>   servono il dry-run medium e la review indipendente conclusiva di G0.7-G0.8.
> - **Creazione e revisione:** creata il 2 settembre 2026; ultima revisione il
>   14 settembre 2026 (sera).
> - **Conservazione:** persistente.
> - **Implementazione:** nessuna fase F0-F6 iniziata. FS-A e FS-B sono
>   autorizzate da Roberto il 14/9, ma ogni modifica di file parte soltanto
>   dopo il preflight, l'assegnazione esclusiva e le correzioni di progetto
>   applicabili indicate nell'appendice D.
> - **Fonti:**
>   - revisioni 1-5, con i rilievi della revisione 4, la review indipendente
>     della revisione 5 e la sua verifica (commit `ad37442c`), nella storia Git;
>   - revisione 7 precedente all'unione, conservata nella storia Git (commit `0b28ba07`) in
>     `internal/roadmap/.codex-review/RM-0009-rev7-codex-review.md`, SHA-256
>     `9700e6962691b2743732c7d88519a28e09d6f51152fa6b270a842a17ed1bd360`;
>   - proposta dell'agente esterno precedente all'unione, conservata nella storia Git (commit `0b28ba07`) in
>     `internal/roadmap/.merge-sources/RM-0009-proposta-agente-esterno-pre-merge.md`,
>     SHA-256 `fe7cf3de76559075ca04b6ce2d54fd69f899ce3b542c1a98a8617f604fedaa59`;
>   - verifiche sul codice del 14/9, riportate al §2;
>   - esito storico dei 22 rilievi della revisione 5 nell'appendice B;
>   - nuova review adversarial e multidisciplinare nell'appendice C;
>   - piano esecutivo atomico per lo sviluppo nell'appendice D;
>   - decisioni puntuali dell'unione nell'appendice E.
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
| 15/9 | **Revisione 8 principale:** la revisione 7 dell'agente esterno, unita con la proposta precedente, è il documento di riferimento; osservazioni di verifica in C.4. |
| 15/9 | **Attivazione:** nessuna nuova capacità si attiva prima della certificazione RM-0008/F5, nemmeno con il «sì» dell'host (D2 resta `blocked`). |
| 15/9 | **Separazione:** RM-0009 si chiude sul ciclo di crescita con F6 in ombra; la sicurezza completa (FS-A, FS-B multipiattaforma, helper amministrativo, protocollo remoto v2, enforcement F6) è un lavoro a parte, già autorizzato (D.1-bis). |
| 15/9 | **Schemi d'uso:** S2 usa anche gli schemi d'uso ripetuti di tutti gli utenti, aggregati e senza testo né valori personali. |
| 15/9 | **Documento unico:** le due sorgenti dell'unione restano soltanto nella storia Git. |

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
- **S2, anticipazione:** due adapter producono `create_executor` o
  `extend_executor`: lenti TELOS notturne e pattern d'uso aggregati, canonici e
  privi di dimensioni owner. Le automazioni personali (task ricorrenti del
  singolo utente) restano fuori da RM-0009 (§9).
- **S3, ottimizzazione:** un piano canonico ripetuto in turni reali viene
  materializzato come `PlanTemplateV1` in ombra. La riga shadow non e servita
  dal lookup ordinario; replay e probe senza effetti raccolgono evidenza e
  producono `promote_plan`. Il primo uso come hit globale avviene solo dopo il
  commit di promozione.

Ogni intent porta soltanto tipo, corpo/contratto canonico, impronta (§5.7) e
stato. Ogni contributo sta in `change_intent_sources` e porta `source_kind`,
`telos_goal`, `proposer_component_id/version/digest` e `origin_owner_id` opaco.
Non esiste una provenienza primaria top-level: tutte le fonti restano
append-only. Nessun campo di provenienza entra in impronta, soglie, ranking,
decisione o autorita.

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
- `evaluation_key` non nulla, digest canonico versionato di dimensione,
  valutatore, metrica, unita, finestra e `source_event_id`; vincolo
  `UNIQUE(intent_id, evaluation_key, revision)`.

Regole:
- **Proiezione:** l'ultima valutazione non superata per `(intent, dimensione,
  evaluator, metrica, unita, finestra/source_event_id)`, secondo
  `evaluation_id`. Una correzione e una nuova revisione dello stesso evento e
  conserva la storia. La decisione legge un vettore tipizzato, quindi successo,
  latenza e costo non si sovrascrivono.
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
- I fatti obbligatori dipendono dal `DecisionMoment`:
  - **D1/trial:** corpo canonico costruito dal core, snapshot del catalogo,
    politica, tassonomia di rischio e regole di rifiuto; ricevuta e manifest
    Birth valgono `not_required`;
  - **D2/activation:** ricevuta Birth, manifest firmato, preesercizio e
    attestazioni di sicurezza applicabili sono obbligatori.
- **Fonti autorevoli:**
  - ricevuta Birth e manifest firmato dallo store dei contratti;
  - riga autopath;
  - politica e registro budget/costi del core;
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
| 1 | Veto tecnico richiesto dal momento corrente: dipendenza/ciclo di vita non pronto, ricevuta, firma o attestazione obbligatoria mancante o discordante, legacy ignoto, politica non valida | `blocked` (ritentabile; **non** è un rifiuto e non è superabile dall'utente) |
| 2 | Regola di rifiuto attiva sulla stessa impronta | `deny` (nessuna nuova domanda) |
| 3 | Serve una scelta umana su consenso, costo, dati sensibili o nuova autorità; tutti i fatti tecnici obbligatori sono verificati | `ask_user` |
| 4 | `create`/`extend`, **D1 prova**: allineamento ≥ soglia e nessuna capability critica o di famiglia sensibile richiesta | `auto` (la prova non attiva nulla) |
| 5 | `create`/`extend`, **D1 prova**, altrimenti | `ask_user` |
| 6 | `create`/`extend`, **D2 attivazione**, prima della certificazione RM-0008/F5 o delle attestazioni FS applicabili | `blocked` con `ReasonCode.dependency_unready` |
| 7 | `create`/`extend`, **D2 attivazione**, dopo RM-0008/F5 e FS applicabili: reversibilità `guaranteed`, nessuna rete in uscita, credenziali o famiglia sensibile, preesercizio superato | `auto` |
| 8 | `promote_plan`: reversibilità garantita, piano senza valori letterali, campione ≥ minimo, successo non inferiore alla baseline | `auto` |
| 9 | `promote_plan` con campione insufficiente | resta in ombra, nessuna domanda |
| 10 | Tutti gli altri casi | `ask_user` |

Tutte le modifiche sono globali per decisione (§ Decisioni): non esiste una riga
«regola globale» separata. Una `rejection_rule` non e un tipo di change intent
ne un effetto del protocollo `applying`: e un record di governance che nasce
atomicamente solo da un «no» autorizzato.

### 5.4 Effetto

- **Protocollo degli effetti, uguale per i tre tipi di change intent:**
  1. `accepted → applying`, con compare-and-swap, `operation_id` e lease;
  2. l'handler prepara nel target un effetto idempotente per `operation_id`,
     ancora invisibile quando il target lo consente;
  3. `observe_effect(operation_id)` risponde `prepared`, `present`, `absent` o
     `unknown`;
  4. se pronto: commit dell'autorita di visibilita e `applying → applied`, con
     la ricevuta
     `{operation_id, before, after, scope: "global_all_users"}`;
  5. per gli effetti governati dallo store RM-0009, nella stessa transazione
     cresce `global_change_epoch`; per create/extend la generazione firmata
     RM-0008 entra direttamente in `tools_sig` e RM-0009 registra la receipt
     con riconciliazione idempotente, senza fingere un commit tra due DB.
- **Autorita per tipo:**
  - per create/extend resta autorevole il puntatore firmato di generazione
    RM-0008; `tools_sig` include anche quella generazione;
  - per autopath e autorevole l'operation committed nello store di governance;
    il lookup rifiuta righe prepared/applying;
  - una regola di rifiuto non entra in questo protocollo: l'unica autorita e il
    commit della transazione locale che consuma il token e scrive insieme stato
    `rejected`, `rejection_rule` ed epoca globale;
  - l'outbox riconcilia i due store: il piano non presume una transazione
    atomica fra database distinti.
- **Recupero** (all'avvio e ogni notte): un `applying` con lease scaduta viene
  osservato.
  - `prepared` → completa il commit autorevole una sola volta;
  - `present` → `applied` se la receipt/autorita corrisponde;
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

#### 5.4.1 Stati e transizioni

La proposta esterna ha correttamente ricordato gli stati storici `observed` e
`finalized`, ma ammetteva anche salti incompatibili con D1/D2. La tabella
seguente e l'unica macchina a stati normativa; ogni arco usa il compare-and-swap
e gli archi non elencati sono vietati.

| Da | A | Condizione normativa |
|---|---|---|
| `proposed` | `trial_applying` | solo create/extend dopo D1 `auto` o «si» valido; non attiva la capacita |
| `proposed` | `accepted` | solo `promote_plan`, dopo la sua decisione unica `auto` o «si» valido |
| `proposed` | `rejected` | «no» autorizzato, atomico con token, regola di rifiuto ed epoca |
| `proposed` | `blocked` | veto tecnico D1 con `ReasonCode` e `resume_state=proposed` |
| `trial_applying` | `staged` | la richiesta Birth idempotente e stata accettata dalla porta unica |
| `trial_applying` | `failed` o `trial_applying` | errore tipizzato oppure recupero della stessa operation |
| `staged` | `awaiting_activation` | ricevuta Birth/preesercizio verificata e associata alla stessa operation |
| `staged` | `rejected` | annullamento autorizzato prima di D2 |
| `awaiting_activation` | `accepted` | solo dopo D2 `auto` o «si» valido e con tutti i prerequisiti tecnici |
| `awaiting_activation` | `rejected` | «no» D2 autorizzato e transazione completa della regola |
| `awaiting_activation` | `blocked` | veto tecnico D2 con `resume_state=awaiting_activation` |
| `blocked` | `proposed` o `awaiting_activation` | soltanto verso il `resume_state` registrato, dopo rimozione verificata del veto |
| `accepted` | `applying` | claim dell'effetto con lease e `operation_id` |
| `accepted` | `rejected` | revoca autorizzata prima che l'operation sia stata reclamata |
| `applying` | `applied`, `failed` o `applying` | commit dell'autorita di visibilita, errore, oppure recupero della stessa operation |
| `failed` | `trial_applying`, `accepted` o `rejected` | il `failure_phase` tipizzato sceglie il solo retry coerente, oppure abbandono autorizzato |
| `applied` | `observed` | prima finestra di beneficio valida e attribuita all'effetto |
| `observed` | `finalized` | criteri di stabilizzazione soddisfatti; il monitoraggio di sicurezza continua |
| `applied`, `observed`, `finalized` | `rolled_back` o `rollback_failed` | inverso verificato oppure fallito; un segnale di sicurezza apre prima la quarantine operation senza inventare `applied -> blocked` |
| `rollback_failed` | `rolled_back` o `rollback_failed` | nuovo tentativo della stessa operazione inversa |
| `rejected` | `proposed` | soltanto dopo scadenza o revoca atomica della regola di rifiuto |
| `proposed`, `staged`, `awaiting_activation` | `superseded` | sola migrazione/bonifica con alias canonico; non e una decisione operativa |
| `superseded` | — | terminale; il ripristino consolida/sostituisce transazionalmente il canonico |
| `rolled_back` | — | terminale; una nuova proposta usa un nuovo evento causale |

Gli stati storici terminali non compatibili vengono conservati in audit e
mappati dalla migrazione, mai fatti rientrare con un arco inventato.

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

| Campo / variabile `METNOS_GROWTH_*` | Tipo e intervallo | Default | Semantica |
|---|---|---|---|
| `s1_recurrence_count` / `S1_RECURRENCE_COUNT` | int, 1..1000 | 3 | Eventi causali unici richiesti; owner/principal/provenienza esclusi. |
| `s1_window_days` / `S1_WINDOW_DAYS` | int, 1..365 | 30 | Finestra UTC della ricorrenza S1. |
| `alignment_min` / `ALIGNMENT_MIN` | float, 0..1 | 0,45 | Allineamento minimo D1. |
| `auto_incremental_cost_max_microunits` / `AUTO_INCREMENTAL_COST_MAX_MICROUNITS` | int, 0..2^63-1 | 0 | Sopra soglia `ask_user`; costo ignoto `blocked/cost_unverified`. |
| `sensitive_capability_families` / `SENSITIVE_CAPABILITY_FAMILIES` | CSV di enum chiuso, almeno 1 | `credentials,mail,people,messages,system_admin` | Famiglie che richiedono consenso e attestazioni applicabili. |
| `benefit_min_samples` / `BENEFIT_MIN_SAMPLES` | int, 1..100000 | 20 | Turni reali causali minimi per decidere. |
| `benefit_window_days` / `BENEFIT_WINDOW_DAYS` | int, 1..3650 | 30 | Finestra UTC, di uguale durata, usata per congelare baseline e osservato. |
| `success_drop_pp` / `SUCCESS_DROP_PP` | float, 0..100 | 5 | Punti percentuali: rollback se `baseline_rate - observed_rate >= success_drop_pp/100`. |
| `latency_regression_pct` / `LATENCY_REGRESSION_PCT` | float, 0..1000 | 20 | Peggioramento della mediana di latenza. |
| `cost_regression_pct` / `COST_REGRESSION_PCT` | float, 0..1000 | 20 | Peggioramento del costo mediano autorevole. |
| `new_executor_min_success` / `NEW_EXECUTOR_MIN_SUCCESS` | float, 0..1 | 0,80 | Successo assoluto minimo di create/extend senza baseline equivalente. |
| `promoted_plan_idle_days` / `PROMOTED_PLAN_IDLE_DAYS` | int, 1..3650 | 30 | Poi ritorno in ombra. |
| `grown_executor_idle_days` / `GROWN_EXECUTOR_IDLE_DAYS` | int, 1..3650 | 60 | Poi richiesta di ritiro via RM-0008. |
| `question_budget_weekly` / `QUESTION_BUDGET_WEEKLY` | int, 0..100 | 3 | Numero massimo di nuove domande in settimana ISO; zero accoda tutto. |
| `decision_ttl_hours` / `DECISION_TTL_HOURS` | int, 1..720 | 72 | Scadenza server-side della domanda. |
| `invocation_auth_ttl_seconds` / `INVOCATION_AUTH_TTL_SECONDS` | int, 1..86400 | 900 | TTL del grant remoto/durevole. |
| `applying_lease_seconds` / `APPLYING_LEASE_SECONDS` | int, 1..86400 | 600 | Lease operation. |
| `applying_max_attempts` / `APPLYING_MAX_ATTEMPTS` | int, 1..100 | 3 | Tentativi prima di `failed`; `unknown` non viene contato come effetto assente. |
| `judge_batch_size` / `JUDGE_BATCH_SIZE` | int, 1..1000 | 20 | Intent per ciclo notturno. |
| `judge_max_attempts` / `JUDGE_MAX_ATTEMPTS` | int, 1..100 | 5 | Retry; backoff `min(60*2^(attempt-1),3600)` secondi. |
| `rejection_ttl_days` / `REJECTION_TTL_DAYS` | int, 1..3650 | 180 | Scadenza rinnovabile di una regola. |
| `reconcile_max_missing_ratio` / `RECONCILE_MAX_MISSING_RATIO` | float, 0..1 | 0,10 | Massimo rapporto di evidenze attese mancanti. |
| `detail_retention_days` / `DETAIL_RETENTION_DAYS` | int, 1..3650 | 180 | Poi aggregati tipizzati con watermark. |
| `counter_flush_seconds` / `COUNTER_FLUSH_SECONDS` | int, 1..3600 | 300 | Flush F6 e sempre all'arresto. |

`new_authority` non e una soglia configurabile: e la differenza tipizzata fra
capability/ambiti richiesti e quelli gia concessi al sistema. Se non vuota,
richiede `ask_user` dopo tutti i gate tecnici.

#### 5.6.1 Formule di beneficio e ritorno

- **Unita di osservazione:** un `turn_id` terminale `origin=user`, attribuito
  mediante receipt alla specifica operation/generazione/piano. Un retry remoto
  dello stesso `invocation_id` e lo stesso campione. Turni test/system, replay e
  fallimenti non causati dall'artefatto sono esclusi con `ReasonCode` chiuso.
- **Nessuna identita nel calcolo:** owner, principal e provenienza non entrano
  in inclusione, peso, soglia o decisione. La deduplica usa soltanto ID causali
  e digest canonici.
- **Finestre:** baseline congelata sui `benefit_window_days` precedenti il
  commit; osservato dal commit fino a `benefit_window_days`. Prima del campione
  minimo ogni metrica e
  `insufficient_evidence`. Finestra, numeratore, denominatore e versione
  dell'artefatto vengono persistiti.
- **Metriche:**
  - `success_rate = success / (success + partial + error)`; `needs_input` e
    misurato separatamente e non entra nel denominatore;
  - `latency_median_ms` sui turni terminali attribuiti;
  - `cost_median_microunits` dal registro costi autorevole; assenza del dato e
    `cost_unverified`, non zero;
  - `need_completion_rate` per create/extend confronta i bisogni canonici
    conclusi dopo l'attivazione con le lacune equivalenti della baseline.
- **Per kind:** create/extend richiedono
  `success_rate >= new_executor_min_success` dopo
  `benefit_min_samples`; dove esiste una route precedente applicano anche i
  confronti relativi della policy. Promote confronta successo, latenza e costo
  con la baseline degli stessi intent canonici. La regola di rifiuto misura le
  rigenerazioni evitate e termina solo per scadenza/revoca esplicita. I valori
  0,80 e 20 sono esclusivamente i default della tabella, non letterali nel
  codice.
- **Precedenza:** regressione di sicurezza o autorita provoca ritorno immediato;
  con campione sufficiente, il superamento di una qualsiasi soglia di
  peggioramento prevale su miglioramenti delle altre metriche. `no traffic` non
  e successo e non provoca rollback automatico.
- **Segnale di sicurezza:** una regressione di sicurezza/autorita esiste solo
  come `AuthorityViolationV1` autenticata, emessa dal verificatore RM-0008 o dal
  punto F6 core-owned, con operation/generazione, rule ID, fonte, digest e
  timestamp server. Non usa testo del modello. Alla ricezione si apre una
  operation idempotente di quarantine/rollback che impedisce subito nuovo uso:
  create/extend chiedono il ritiro via RM-0008, promote torna shadow. Un intent
  gia `applied` non compie la transizione illegale `applied -> blocked`: resta
  tale finche `observe_effect` o la receipt verificano l'inverso, quindi passa a
  `rolled_back`; un inverso non verificato porta a `rollback_failed`. Per gli
  stati pre-apply resta applicabile `blocked` secondo la state machine. Un
  evento non autenticato viene auditato ma non cambia lo stato.

### 5.7 Matrice dei tipi di cambiamento (insieme chiuso)

| Tipo | Sorgente | Impronta | Fatti autorevoli | Decisione | Effetto globale | Store e atomicità | Misura | Ritorno |
|---|---|---|---|---|---|---|---|---|
| `create_executor` | S1, S2 | `sha256("create_executor:v1:" + intent_hash)` | ricevuta Birth, manifest firmato, politica, regole di rifiuto | D1 prova, poi D2 attivazione (§5.3) | nuova generazione nel catalogo, via RM-0008 | store dei contratti RM-0008; `operation_id` | uso in turni reali, successo | ritiro via RM-0008 |
| `extend_executor` | S1, S2 | `sha256("extend_executor:v1:" + target + ":" + intent_hash)` | come sopra, più la generazione corrente del target | D1, poi D2 | nuova generazione del target | come sopra | come sopra | ritorno alla generazione precedente via RM-0008 |
| `promote_plan` | S3 | `sha256("promote_plan:v1:" + plan_hash + ":" + intent_hash)` | `PlanTemplateV1` shadow non servito, evidenza replay/probe senza effetti, politica | decisione unica (§5.3, righe 8-10) | il piano L1 serve tutti gli utenti solo dopo commit | riga prepared in `autopath.sqlite` + operation committed/epoca nello store di governance | latenza, costo e successo dopo promozione, confrontati con baseline | ritorno in ombra |

`intent_hash` nel nuovo schema e un SHA-256 completo, con separazione di dominio,
del JSON canonico versionato `IntentKeyV1` (verbo, oggetto, azioni e
qualificatori chiusi), senza parole chiave o valori dell'utente. La parte hash
storica di 16 caratteri prodotta da `_compute_intent_sig` resta soltanto come
alias di migrazione e non alimenta unicita o decisioni.

**Tipi ritirati.** Gli intent storici vanno in `superseded`, con motivo.
- `materialize_pipeline`: esecuzione una tantum, nessun artefatto.
- `cache_pattern`: store ritirato il 2/7.
- `dedupe_executors`: resta a `executor_aging`.
- `reject_pattern`: il ✗ personale resta in `turn_feedback`, circoscritto all'owner.

**Regola di rifiuto, fuori dall'insieme dei tipi.** Il record
`rejection_rules` usa `sha256("rejection-rule:v1:" + impronta_rifiutata)` ed e
creato nella stessa transazione che consuma il token, porta l'intent a
`rejected` e incrementa l'epoca. Scadenza e revoca sono record append-only. Non
ha handler, `observe_effect` o rollback del change lifecycle.

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
| Remoto | stesso punto, ramo remoto → `remote_exec.invoke_remote` → `agent_server` → client device | principal del turno | prima dell'accodamento e di nuovo con `authorize_start` subito prima dell'effetto sul device | autorizzazione strutturata legata a invocazione, device, principal/owner operativo, generazione, digest argomenti, epoca e scadenza | CAS server-side di `authorize_start`; il poll e soltanto un claim ridistribuibile | diniego: nulla in coda; revoca prima del CAS, payload diverso, token scaduto o replay diverso: nessun effetto; redelivery identica resta valida |
| Builtin e verb-unique | `agent_runtime.invoke_tool_by_name` → `loader.invoke_verb_unique` | principal del turno | prima della chiamata al builtin | — | — | diniego: builtin non chiamato |
| Durevole con executor | `durable_workloads/execution.py` → `executor_scheduler.invoke_scheduled` | owner del workload | all'inizio di ogni tentativo | token monouso legato a workload e tentativo | inizio del tentativo | revoca fra accodamento e tentativo: nessun effetto |
| Durevole interno | worker durevole, passi senza executor | owner del workload | non applicabile | — | — | un test dichiara che il passo non ha autorità di executor |

Il modulo di binding/revoca e condiviso da richieste di decisione e
autorizzazioni d'invocazione, ma i protocolli sono tipizzati: la decisione e
consumata in una transazione; il remoto usa claim e `authorize_start`, con
redelivery dello stesso `invocation_id`. Tutto il tempo autorevole e lato
server, quindi senza differenze d'orologio:
- emissione;
- consumo con compare-and-swap;
- revoca per prefisso di legame;
- pulizia notturna.

Per il remoto, il CAS `authorize_start` e il punto irrevocabile: emette un solo
grant e porta l'invocazione a `start_authorized`. Senza completion firmata del
device entro la lease, lo stato diventa `execution_unknown`; non viene emesso
un secondo grant e non esiste retry automatico, salvo che la ricevuta del
contratto attesti idempotenza per lo stesso `operation_id/idempotency_key`.
Recovery distingue crash dopo CAS/prima effetto da crash dopo effetto/prima
receipt solo mediante receipt/observe autorevoli, mai per supposizione.

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
    - stati nuovi: `trial_applying`, `awaiting_activation`, `applying`,
      `blocked`, `superseded`, `rollback_failed`, con `resume_state` e
      `failure_phase` tipizzati; gli storici `observed` e `finalized` restano;
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
    - `intent_hash`: SHA-256 completo di `IntentKeyV1`, senza parole chiave o
      valori dell'utente; il digest corto storico resta solo alias di lettura.
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
  - `rejection_rule` di governance append-only, atomica con il «no», con
    scadenza e revoca;
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
  - ogni uso di `setup`, `teardown` ed `env` viene inventariato; la sua funzione
    viene prima convertita in una fixture ermetica o in un'operazione chiusa
    equivalente, poi il campo legacy viene **rifiutato** con errore tipizzato;
  - tutti i manifest trovati dall'inventario della baseline vengono
    ripubblicati senza quei campi, dal ciclo normale; nessun conteggio e
    cablato nel piano;
  - infine `test_runner.py` viene ritirato.
- *Completata quando* nessun riferimento resta e le prove di isolamento passano:
  rete, processi, utente e IPC, ambiente ammesso, cwd effimera, limiti di
  CPU/RAM/output, morte dell'intero albero.

**FS-B — Radici protette e broker (subito).**
- *Cambiamento:*
  - un broker core copre tutti i consumer censiti di vault e chiavi, comprese
    credenziali, posta e provider; il processo executor non riceve il segreto;
  - gli executor CRUD delle credenziali diventano builtin e restituiscono solo
    metadati; l'owner si ricava dal principal;
  - dopo la migrazione di ogni consumer, via i mount di vault e `admin.key`;
  - radici protette = tutto il piano di controllo server e device, con eccezioni
    tipizzate e nessun fallback remoto che allarghi i permessi;
  - apertura no-follow su ogni segmento e protezione esplicita dagli hardlink
    mediante separazione filesystem o denyset autorevole `(device,inode)`.
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
| Auto-attestazione e avvelenamento del segnale | `build_change_facts` da fonti autorevoli; deduplica per ID causale/retry canonico, mai per owner o principal; solo turni `origin=user` |
| Doppie applicazioni e stati misti | UNIQUE sull'impronta, compare-and-swap, `applying` con lease, puntatore firmato RM-0008, epoca globale |
| Decisioni rubate o vecchie | richieste di decisione monouso legate ai fatti mostrati; solo `host` |
| Dati privati negli artefatti globali | schemi chiusi senza testo libero; `intent_hash` senza parole chiave; provenienza opaca |
| Guasti trattati come rifiuti | `blocked` ritentabile, distinto da `rejection_rule` |
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
3. gli indicatori 1-10 sono prodotti oppure marcati `not_applicable` con
   motivo tipizzato; 1, 3 e 6 sono non nulli, 7 non mostra regressioni oltre le
   soglie, 8 collega ogni effetto a un fine TELOS, 9 resta nel budget e 10 non
   mostra bypass di autorita;
4. tutti gli artefatti attivati sono visibili nello stesso catalogo/routing a
   tutti gli utenti, mentre dati, argomenti, credenziali, invocazioni e task
   restano owner-scoped;
5. il release manifest, le prove fault-injection e la review finale indipendente
   non contengono rilievi bloccanti o alti.

La nascita automatica e l'enforcement di F6 dipendono da RM-0008/F5, FS-A e
dalle attestazioni FS-B applicabili. Fino ad allora RM-0009 puo arrivare al
milestone `implemented_pending_rm0008` per F0-F5 infrastrutturali e F6 in
ombra, con D2 sempre `blocked`/`dependency_unready`.

**Arresto.** F0 può correggere denominatori e gravità, ma non dichiarare
artefatti i difetti statici provati.

## 12. Che cosa resta a Roberto

- **Adesso:** nessuna approvazione e richiesta. Il coordinatore deve prima
  chiudere tutti i gate dell'appendice C e far ripetere la review indipendente.
- **Poi:** approvare la revisione soltanto se la review conclusiva non contiene
  rilievi bloccanti o alti senza decisione.
- **A regime**, rispondere alle poche domande sì/no nel riepilogo.

**Normalizzazione dopo l'approvazione.** Nel primo commit documentale successivo
all'approvazione il coordinatore elimina da questo file lo stato di lavorazione,
la cronaca delle revisioni e le appendici storiche B, C ed E. Restano soltanto il
testo normativo approvato, le istruzioni operative e il piano esecutivo
approvato. La storia Git non viene riscritta; una sua eventuale riscrittura
richiede un'autorizzazione separata ed esplicita.

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
printf '%s' '{"query":"<richiesta di collaudo priva di segreti>"}' | \
  /opt/metnos/.venv/bin/python internal/tools/metnos_admin_request.py \
    POST /agent/turn --json-stdin --timeout 600
/opt/metnos/.venv/bin/python internal/tools/metnos_admin_request.py \
  GET '/admin/turns?limit=5'
```

L'helper, specificato da D-G0.4 e consegnato da D-S0.1, non legge ne trasmette la
chiave amministrativa master. Ottiene un token breve e limitato a
metodo+path tramite socket Unix protetto e peer verificato. Non inserisce token
in argv, log o ambiente. Prima che l'helper esista questi comandi non si
eseguono.

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
     - costruisce prima la fingerprint SHA-256 canonica di D-P2.4;
     - per ogni impronta duplicata trasferisce fonti e valutazioni all'intent
       canonico e sposta i duplicati in un archivio/alias con `canonical_id`;
       non lascia mai due fingerprint uguali nella tabella soggetta a UNIQUE;
     - poi crea l'indice univoco sulla sola tabella canonica;
     - aggiunge `row_version INTEGER NOT NULL DEFAULT 0`.
  2. **Macchina a stati:** implementa senza aggiunte la tabella esaustiva del
     §5.4.1, compresi gli stati storici `observed` e `finalized`. Persisti
     `decision_moment`, `resume_state` e `failure_phase` come enum chiusi;
     verifica la precondizione specifica dell'arco nello stesso CAS. Il
     ripristino di un alias consolida/sostituisce transazionalmente il canonico:
     non esiste `superseded → proposed` diretto.
  3. **`upsert_intent`** con `INSERT … ON CONFLICT(fingerprint) DO UPDATE …
     RETURNING id`, in una sola istruzione.
  4. **`transition(id, expected_state, expected_version, new_state, **fields)`**
     esegue `UPDATE … WHERE id=? AND state=? AND row_version=?` e controlla
     `rowcount`. Tutti i chiamanti di `_transition` passano da qui.
  5. **`one_shot_tokens.py`**, primitive tipizzate lato server:
     - per le decisioni la tabella e nello stesso DB dell'intent;
     - colonne: `token_id` (128 bit casuali), `purpose` (enum),
       `binding_kind`, `binding_id`, `binding_generation`, `binding_digest`,
       destinatario/canale, `expires_at`, `consumed_at`, `consumed_by`,
       `revoked_at`, `revocation_reason`;
     - funzioni: `issue`, `consume(token_id, binding_digest, principal)` (UPDATE
       con condizione su non consumato/revocato/scaduto), revoca per chiave
       strutturata esatta, `gc`;
     - consumo, transizione, eventuale regola di rifiuto ed epoca stanno nello
       stesso `BEGIN IMMEDIATE`.
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
     default `unknown`, `unknown` e `""`. Per i nuovi record `intent_hash` e il
     digest SHA-256 di `IntentKeyV1`; il secondo elemento storico di
     `_compute_intent_sig` viene letto soltanto come alias legacy.
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
- **Passi:** regole per tipo verso archivio/alias canonico, con regola e prova.
  - Tipi ritirati non terminali → archivio; terminali conservati come audit
    legacy senza handler.
  - Duplicati per la nuova impronta del §5.7: resta il più recente.
  - `extend_executor` il cui bersaglio non esiste più → archivio.
  - `create_executor` con bersaglio assente: **nessuna azione**, perché è normale.
  - Il dry-run stampa, per ogni id, regola ed evidenza; `--restore <id>` esegue
    consolidamento/sostituzione transazionale del canonico e non una transizione diretta.
- **Test:** un `create_executor` valido con bersaglio assente resta; il ripristino
  funziona.
- **Coordinatore:** esegue `--apply` sui dati del servizio.

#### F2.1 — Schema delle valutazioni
- **Leggi prima:** `change_intents.py`, `runtime/change_intent_adapters/_base.py`.
- **Passi:**
  1. Crea le tabelle del §5.2 e `change_intent_sources`, con `NOT NULL`, chiavi
     esterne verso `change_intents(id)` e i vincoli di unicità indicati.
  2. `change_intents` riceve soltanto `contract_version` e campi canonici.
     Aggiungi a ogni riga di `change_intent_sources`:
     - `proposer_component_id`, `proposer_component_version` e digest del
       componente, iniettati dal registry;
     - `origin_owner_id`, derivato dalla fonte autenticata;
     - `source_kind` (enum, compreso `unknown`) e `telos_goal`.
     Nessuno di questi campi viene duplicato come provenienza primaria
     nell'intent.
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
     `(dimensione, valutatore, metrica, unita, finestra/source_event_id)`,
     ordinata per `evaluation_id`.
  4. In `upsert_intent` togli `max()` (~405). Ogni lettura decisionale di
     `score` passa a `current_evaluations`.
  5. Conservazione notturna secondo la stessa chiave della proiezione; dopo 180
     giorni sposta il dettaglio in aggregati con schema e watermark espliciti.
- **Test:** 0,8 corretto a 0,4 resta 0,4, con la storia; un'altra dimensione non
  lo cambia.

#### F2.3 — Giudizio tipizzato
- **Leggi prima:** `alignment_engine.py` (ritorni `[]` ~214-228, `except` ~301),
  `runtime/change_intent_adapters/telos.py`, `telos_loader.py`.
- **Passi:**
  1. `judge_intent(intent) -> JudgeResult(status: ok|not_applicable|failed,
     value, telos_goal, reason_code)`. `invalid` e un `ReasonCode` di `failed`,
     non un quarto stato. Distingue un vero «non applicabile» da un errore,
     cioe un `[]` prodotto da un'eccezione o da un JSON non valido.
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
  4. Una migrazione scansiona e mette in quarantena/redige i campi legacy
     `intent_summary`, `intent_rationale`, `intent_body` e i piani autopath con
     argomenti, filler, messaggi, path o valori. Un piano globale usa soltanto
     `PlanTemplateV1`; gli argomenti sono rilegati dal principal corrente.
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
     - verbo, oggetto, azioni e qualificatori canonici sicuri;
     - fingerprint SHA-256 canonica, `catalog_generation` (il `tools_sig` del
       turno);
     - `classifier_version`.
  2. Inventario delle uscite non soddisfatte del dispatch
     (`rg -n 'final_kind' runtime/engine/dispatch.py`). Ognuna passa la sua
     `GapEvidence` al terminator, oppure ha un motivo scritto per non farlo.
  3. `lacuna_events`: `event_id = sha256("gap-event:v1", turn_id,
     action_ordinal)` come chiave primaria, piu i campi di `GapEvidence`.
     `gap_kind` e una classificazione revisionabile, non parte dell'identita.
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
- **Crea:** `runtime/change_intent_adapters/gap.py`.
- **Passi:**
  1. L'adapter S1 legge `lacuna_events` con `gap_kind=capability_absent`, in join
     con i turni `origin=user`. Esclude collaudo, sistema, `unknown` e legacy.
  2. Deduplica soltanto per identita causale canonica: replay e retry dello
     stesso `source_event_id` contano una volta. Owner, principal e provenienza
     non entrano nella soglia. Sopra la soglia ricontrolla il catalogo corrente
     con il matcher composito di F3.2.
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
  1. Candidati: `PlanTemplateV1` con `shadow=1`, derivati da piani canonici
     ripetuti in turni `origin=user` riusciti, senza valori letterali. La riga
     shadow non viene servita dal lookup ordinario.
  2. `source_event_id=plan:<plan_hash>:<data>`.
  3. Replay/probe senza effetti validano candidato e baseline senza cambiare il
     risultato del turno.
  4. Handler `apply_promote_plan(ci, operation_id)`: prepara la riga e la rende
     servibile soltanto quando l'operation risulta committed.
  5. `observe_effect`: verifica insieme `shadow` e `operation_id` committed.
  6. Rollback `demote_autopath(ihash, fhash)` (nuova funzione): riporta
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
  2. `build_change_facts(intent_id, moment) -> ChangeFactsV1`, che rilegge:
     - a D1: richiesta canonica core-owned, catalogo, policy, tassonomia di
       rischio e rifiuti; ricevuta/manifest sono `not_required`;
     - a D2: store dei contratti, ricevuta Birth (capability, ambiti,
       `critical`, reversibilità con `undo.round_trip`, generazione), manifest,
       preesercizio e attestazioni FS applicabili;
     - la riga autopath per `promote_plan`;
     - la politica e le regole di rifiuto.
  3. La reversibilità vale `guaranteed`, `conditional` o `none`, come al §5.3.
  4. Un campo del corpo dell'intent non è mai fonte di un fatto.
- **Test:** anti-auto-attestazione per ogni fatto; ricevuta assente non blocca
  D1 ma produce `unverified` e blocco tecnico a D2.

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

     Un «no» crea `rejection_rule` nella stessa transazione (F5.6).
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
  1. Per `create_executor` ed `extend_executor`: D1 `auto` o approvata porta a
     `proposed → trial_applying`; la richiesta Birth e idempotente per
     `operation_id`. L'accettazione della richiesta porta a `staged`; la
     ricevuta verificata porta ad `awaiting_activation`, poi D2.
  2. `accepted → applying`, con lease e `operation_id`; handler idempotente;
     `observe_effect`; `applied` + `bump_epoch`, nella stessa transazione.
  3. Un recupero, all'avvio e ogni notte, gestisce le lease scadute come al §5.4.
- **Test:**
  - iniezione di crash prima dell'effetto, dopo l'effetto e prima della
    ricevuta, dopo la ricevuta;
  - due worker sullo stesso intent;
  - senza ricevuta Birth l'attivazione è impossibile.

#### F5.6 — `rejection_rule` di governance append-only
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

#### F5.7 — Beneficio e rollback verificato
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
  1. `build_invocation_envelope()` costruisce nel core un envelope immutabile
     con i campi del §5.8; il chiamante non fornisce campi gia risolti.
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
  3. In ombra una decisione forzata a negare viene registrata ma non cambia
     esito o effetti. La prova di zero effetti appartiene all'enforcement.
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
  2. Remoto: autorizzazione strutturata all'accodamento; il poll effettua un
     claim ridistribuibile. Il client chiede `authorize_start` immediatamente
     prima dell'effetto e consuma con CAS il grant firmato dal server.
  3. Durevole: token per tentativo, consumato all'inizio del tentativo.
  4. La revoca per legame invalida code, retry e cache.
- **Test:**
  - replay concorrente;
  - riavvio;
  - scadenza;
  - poll perso e redelivery identica;
  - revoca fra accodamento e `authorize_start`;
  - zero effetti.

#### FS-A — Runner unico
- **Leggi prima:** `synth_request.py` (~60, ~72), `test_runner.py`,
  `executor_birth_runner.py` (`run_birth_phase` ~427), `executor_birth_identity.py` (~275).
- **Passi:**
  1. Inventario generato alla baseline: `rg -n "test_runner" .` sull'intero
     repository e sull'albero della release installata; elenco, non numero
     cablato, di tutti i manifest con `setup`, `teardown` o `env`.
  2. Introduci nel runner Birth una compatibilita chiusa per le sole semantiche
     censite; `_validate_birth_tests` usa `run_birth_phase`. Non eseguire mai il
     vecchio `test_runner.py` per ottenere il risultato “prima”.
  3. Per ogni campo legacy registra la preparazione e la converte in fixture
     ermetica o operazione chiusa; la stessa asserzione sostanziale deve passare
     nel runner Birth prima e dopo la conversione.
  4. Solo dopo tutte le prove di equivalenza, `setup`, `teardown` ed `env` vengono
     rifiutati con `legacy_test_field_refused`.
  5. Il coordinatore ripubblica tutti i manifest dell'inventario senza quei
     campi, dal ciclo normale.
  6. Rimuovi `test_runner.py` e i campi dalla grammatica soltanto quando
     inventario e ricerca dei riferimenti sono vuoti.
- **Test:** isolamento di rete, processi, utente e IPC, ambiente ammesso, cwd
  effimera, limiti di CPU, RAM e output, morte dell'intero albero; ogni campo
  legacy rifiutato.

#### FS-B — Radici protette e broker
- **Leggi prima:** `sandbox.py` (~549), `credentials.py`, gli executor
  `find_credentials`, `set_credentials` e `delete_credentials`, `vaglio.py`
  (~51), `config.PATH_USER_CONFIG` (~123), `BUILTIN_INPROC_SPECS` e
  `scripts/generate_builtin_executor_contracts.py`; inoltre
  `client-rs/src/sandbox_common.rs`, `sandbox_linux.rs`, `sandbox_windows.rs` e
  `appcontainer.rs`, e tutti i consumer di vault/chiavi prodotti da G0.5.
- **Passi:**
  1. `protected_roots()`: tutto il piano di controllo server e device, con
     matrice read/write/execute ed eccezioni tipizzate.
  2. Applicalo al confine finale locale e nei sandbox Rust; nessun percorso
     assoluto da argomenti/hint diventa automaticamente bind/ACL e nessun
     fallback Windows allarga l'accesso.
  3. Usa no-follow/openat2 per symlink e una separazione filesystem o denyset
     `(device,inode)` per hardlink.
  4. Introduci il broker core per credenziali, posta e provider; migra tutti i
     consumer censiti. Le tre funzioni CRUD diventano builtin: owner dal
     principal e `assert_no_secrets_in_return`.
  5. Solo quando nessun consumer usa il mount, rimuovi vault e `admin.key` da
     ogni sandbox e produci l'attestazione FS-B.
- **Test:**
  - nessun mount di chiavi in bwrap o sul device;
  - `~`, assoluto, env, symlink, hardlink, magiclink, mount e race negati;
  - Linux, Windows e AppContainer senza degradazione permissiva;
  - posta/provider continuano a funzionare tramite broker;
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
| FS-A | `synth_request.py`, `test_runner.py`, `executor_birth_runner.py`, `executor_birth_identity.py`, manifest e fixture censiti | F5.4 usa la stessa API Birth/sintesi: non contemporanee |
| FS-B | file server precedenti, broker mail/provider, `client-rs/src/sandbox_common.rs`, `sandbox_linux.rs`, `sandbox_windows.rs`, `appcontainer.rs` | F0.x e F6.x (`agent_runtime.py`/`loader.py`), F1.2 (`vaglio.py`), modifiche client remote: non contemporanee |
| F0.x | `agent_runtime.py` (`TurnLog`, `run_turn`), `http_auth.py`, `users.py`, route HTTP | F6.2 (`agent_runtime.py`): F6.2 dopo F0 |
| P2, F2.x, F5.x | `change_intents.py`, `change_applier.py`, `jobs/` | sequenziali fra loro |
| F3.x | `engine/dispatch.py`, `engine/terminator.py` | F1.2 (`dispatch.py`): F3 dopo F1.2 |

3. Il coordinatore raggruppa le unità concluse in un ciclo di rilascio ed
   esegue i turni reali delle schede.

## Appendice B — Esito storico dichiarato per i 22 rilievi della revisione 5

Il testo integrale dei rilievi e la loro verifica nel codice sono nella storia
Git (appendice C della revisione 5, commit `ad37442c`). L'esito dei rilievi
della revisione 4 è nel commit `dbf3f1d5`.

> Questa tabella registra cio che la revisione 6 dichiarava accolto; non prova
> la chiusura corrente. La verifica successiva nell'appendice C la sostituisce
> ai fini dell'approvazione.

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

## Appendice C — Review adversarial e multidisciplinare della revisione 6

### C.1 Metodo e verdetto

La revisione e stata svolta in sola lettura da tre agenti indipendenti, con
lenti diverse — architettura, sicurezza multiutente e attuabilita da parte di
agenti medium-tier — e verificata dal coordinatore sul codice corrente.

**Verdetto unanime: revisione 6 non approvabile.** Le decisioni su globalita,
provenienza e `owner_id` sono espresse correttamente, ma il testo operativo non
le realizza ancora in modo coerente. L'appendice B non puo essere usata come
prova di chiusura.

La tabella seguente usa il criterio prudenziale. Per approvare il **progetto**,
un rilievo e chiuso quando testo normativo e WBS concordano e nominano, ove
applicabili, schema, migrazione, rollback e prova. Il codice corrente e un
vincolo verificato, non deve essere gia modificato. Per dichiarare
**completata** la roadmap devono poi concordare anche codice e risultati delle
prove. `B` indica che il rilievo blocca lo sviluppo dipendente; `P` che la
soluzione e parziale.

| Rilievo | Stato | Evidenza e correzione necessaria |
|---|---|---|
| R5-01 | **B** | D1 richiede implicitamente la ricevuta Birth che deve ancora produrre. Separare fatti D1 pre-Birth e fatti D2 post-Birth; prima di RM-0008/F5 D2 e `blocked`/`dependency_unready`, mai `ask_user`. |
| R5-02 | **P** | Definire `ChangeFactsV1` campo per campo: fonte, versione, digest, autorita corrente/richiesta, sensibilita e obbligatorieta distinta per D1/D2. |
| R5-03 | **P** | Il binding della domanda e buono, ma consumo token, transizione, eventuale rifiuto ed epoca devono stare nella stessa transazione o in un protocollo recuperabile esplicito. |
| R5-04 | **B** | `superseded` non rimuove la fingerprint duplicata: il successivo indice `UNIQUE(fingerprint)` fallisce. Mancano inoltre schema persistente di operation, lease, tentativi e ricevute. |
| R5-05 | **P** | L'insieme dei kind e chiuso, non lo sono i body e il registry esaustivo `handler/observe/rollback`; `promote_plan` oggi non produce l'effetto dichiarato. |
| R5-06 | **B** | Il cap S1 per principal fa entrare l'identita in una soglia, in contrasto con la decisione vincolante: owner e provenienza sono solo metadati e mai gate. |
| R5-07 | **P** | Rifiuto e veto sono distinti, ma servono binding normativo, revoca e consumo prima sia del generatore sia del planner. |
| R5-08 | **B** | Il consumo remoto a `next_invocation` confligge con la redelivery at-least-once e lascia una finestra di revoca; il digest opaco non supporta revoca per prefisso. |
| R5-09 | **B** | Manca `PlanTemplateV1`, la separazione piano globale/argomenti per-owner, la bonifica legacy e l'integrazione dei nuovi ID nella cancellazione utente. |
| R5-10 | **B** | `GapEvidence` non conserva il contratto canonico necessario al ricontrollo; il matcher corrente verifica soltanto il verbo. Resta inoltre il cap per principal. |
| R5-11 | **B** | Migrazione univoca incoerente e identita globale basata sul digest storico di 64 bit. Serve SHA-256 del JSON canonico, con separazione di dominio e controllo collisioni. |
| R5-12 | **P** | Il restore diretto di un record `superseded` puo collidere con il superstite; gli stati storici terminali non hanno una migrazione definita. |
| R5-13 | **P** | Mancano formule per kind, denominatori, attribuzione causale, costo, precedenza tra metriche e protezione da campioni ripetuti. |
| R5-14 | **B** | Il numero di manifest e gia falso; la rimozione di `setup/teardown/env` senza sostituzione elimina prove sostanziali. La matrice dei file omette conflitti reali. |
| R5-15 | **P** | Il metodo di prova e corretto, ma il registro chiuso dei collegamenti obbligatori non e materializzato nel documento o in un fixture versionato. |
| R5-16 | **P** | L'epoca e letta dalle cache, ma non cambia sicuramente al cambio policy o al rollback. Includere `GrowthPolicy.version` nelle firme e prescrivere i bump. |
| R5-17 | **P** | Il flusso e descritto, ma file dell'adapter S1, body canonico e registrazioni per S3 non sono tutti assegnati. |
| R5-18 | **P** | Watermark e risultato tipizzato sono presenti; servono commit point e recovery esatti per contatori, operation e rollback. |
| R5-19 | **P** | L'inventario deve includere ogni costruttore/scrittore diretto di `TurnLog`; la route HTTP corrente puo persistere `outcome=unknown`. |
| R5-20 | **P** | Il contratto tipizzato e corretto, ma l'API corrente di alignment assorbe eccezioni/JSON invalido come lista vuota: va modificata alla fonte. |
| R5-21 | **P** | Le formule devono usare un codec canonico; vanno nominati tutti i file nuovi e la provenienza del componente deve derivare dal registro, non dall'adapter. |
| R5-22 | **P** | La policy non ha ancora nomi macchina/tipi/range per ogni scalare; la baseline tra piu store richiede un cutoff globale coerente. |

### C.2 Nuovi rilievi della revisione 6

| ID | Gravita | Rilievo verificato | Condizione di chiusura |
|---|---|---|---|
| R6-01 | bloccante | `promote_plan` presume che `shadow=1` non sia servito, ma `autopath.lookup()` non filtra `shadow` e il codice dichiara che lo shadow e un hit normale. La promozione e quindi un no-op e la modifica precede la governance. | Lo shadow non e mai servito nel routing ordinario; una via di probe separata raccoglie evidenza; il commit di attivazione e il primo istante di visibilita globale. |
| R6-02 | bloccante | Il protocollo universale `applying` non copre la richiesta Birth e non ha DDL per operation, lease, tentativi, `before/after` e receipt. | Stato `trial_applying` o operation equivalente, journal persistente, CAS, pseudo-SQL di claim/commit/reconcile e fault test a ogni confine. |
| R6-03 | bloccante | Una approvazione umana puo oggi sostituire una prova tecnica mancante o la certificazione RM-0008/F5. | Integrita, firma, receipt, preesercizio e dipendenze non pronte producono sempre `blocked`; `ask_user` vale solo per consenso, costo, sensibilita o nuova autorita. |
| R6-04 | bloccante | Consumo del token remoto al poll, redelivery e revoca non hanno una semantica comune. | Protocollo `claim -> authorize_start` server-side legato a `invocation_id`; redelivery identica permessa, payload diverso/revoca prima dello start negati. |
| R6-05 | alta | Non esiste un costruttore core-owned dell'`InvocationEnvelope`. | `build_invocation_envelope()` ricava principal, owner operativo, contratto, generazione, capability, ambiti e versione; i chiamanti non possono auto-attestare campi. |
| R6-06 | alta | `proposer_component_id/version` puo essere auto-dichiarato dall'adapter e la provenienza primaria in un upsert concorrente non e deterministica. | Identita componente assegnata dal registry chiuso; tutte le fonti append-only; regola transazionale deterministica per la provenienza primaria o nessuna primaria privilegiata. |
| R6-07 | alta | Lo store autopath serializza anche argomenti, filler e messaggi; “senza valori” non definisce una trasformazione eseguibile. | Schema chiuso `PlanTemplateV1`, placeholder allowlist, scanner PII/segreti e rebinding esclusivo da dati del principal corrente. |
| R6-08 | alta | `GapEvidence` conserva un hash non reversibile; `implements_intent_verb()` non verifica l'oggetto. | Evidenza canonica tipizzata con verbo, oggetto, azioni/qualificatori sicuri e ordinale; matcher composito con generazione catalogo e prove negative. |
| R6-09 | alta | Lo stesso tipo `benefit` proietta successo, latenza e costo su una sola chiave, mentre la retention usa una chiave diversa. | Chiave di proiezione con metrica/evaluator/finestra e vettore di decisione tipizzato; schema aggregati e watermark espliciti. |
| R6-10 | alta | Le metriche globali possono essere dominate da retry o richieste ripetute e non esiste una soglia costo. | Deduplica causale indipendente da owner/provenienza, attribuzione dell'errore all'effetto, formule per kind e soglie di successo, latenza e costo. |
| R6-11 | alta | Cambi di policy e alcuni rollback possono lasciare valida una cache vecchia. | `GrowthPolicy.version` nel `tools_sig`; bump persistente dell'epoca per policy, rifiuto, revoca, apply e rollback, con test dopo restart. |
| R6-12 | alta | `users.delete_user` non conosce `principal_user_id`, `origin_owner_id`, token e receipt nuovi. | Revoca token e anonimizzazione/tombstone degli identificativi, mantenendo l'artefatto globale e cancellando dati/argomenti owner-scoped. |
| R6-13 | bloccante | FS-A assumeva 24 manifest; l'inventario corrente ne trova 33. Rimuovere i campi senza sostituzione altera i test. | Inventario generato alla baseline; conversione uno-a-uno a fixture ermetiche/operazioni chiuse; prova di equivalenza; solo dopo rifiuto e rimozione. |
| R6-14 | bloccante | FS-B e prerequisito solo di F6, mentre una capacita nuova potrebbe ancora ricevere vault o `admin.key`. | FS-B e veto D2 per ogni capacita che raggiunge credenziali o piano di controllo; prova di assenza mount e di accesso no-follow. |
| R6-15 | alta | Gli esempi A.3 esponevano `admin.key` negli argomenti di `curl`. | Helper amministrativo che legge la chiave internamente; test `/proc`, log ed environment senza segreti. |
| R6-16 | alta | RM-0008 ordina di usare/irrobustire `test_runner.py`, mentre FS-A di RM-0009 lo ritira. | Emendamento esplicito a RM-0008 e un solo owner dell'API Birth prima di modificare i file condivisi. |
| R6-17 | alta | A.5 dichiarava FS-A indipendente da F5 e ometteva `agent_runtime.py`/`loader.py` da FS-B. | Matrice file generata/verificata sulla baseline, lease esclusivo dei file e ordine aggiornato. |
| R6-18 | alta | L'obiettivo include anticipazione dagli schemi d'uso, ma S2 usa soltanto TELOS e le automazioni personali sono escluse. | Definire una seconda sorgente canonica, aggregata e priva di dati personali per pattern d'uso, oppure restringere esplicitamente l'obiettivo con decisione di Roberto. |
| R6-19 | alta | Il grafo degli stati non consente tutte le transizioni richieste e non distingue D1 da D2; `yes` puo portare direttamente ad `accepted`. | Tabella esaustiva evento/stato/momento/precondizione/stato successivo; test parametrico che impedisce ogni salto di fase. |
| R6-20 | alta | Per effetti in database diversi, `applied + epoch` non puo essere atomicamente vero senza un'autorita di visibilita comune. | Puntatore/stato di attivazione autorevole nello store di governance; lookup serve soltanto generazioni committed; recovery non espone mai uno stato misto. |

### C.3 Gate per una nuova richiesta di approvazione

Una revisione successiva puo essere sottoposta a Roberto solo quando:

1. R6-01, R6-02, R6-03, R6-04, R6-13 e R6-14 hanno una soluzione normativa
   completa e test nominati;
2. ogni riga R5/R6 ha, ove applicabili, schema, file owner, migrazione,
   rollback e prova di uscita;
3. RM-0008 e RM-0009 non ordinano piu modifiche opposte agli stessi file;
4. due agenti medium, senza contesto orale, producono lo stesso ordine, gli
   stessi file, le stesse transizioni e gli stessi test;
5. una nuova review architetturale e di sicurezza non trova rilievi bloccanti o
   alti irrisolti;
6. `git diff --check`, link documentali e riferimenti ai simboli risultano
   validi.

### C.4 Verifica e osservazioni di Claude sulla revisione 8 (14/9, notte)

**Adozione.** Il file canonico è la revisione 8, cioè la revisione 7
dell'agente esterno unita, caso per caso, con la mia proposta (revisione 6 più
elenco lavori; appendice E). La adotto come principale. Non la sostituisco con
la revisione 7 grezza, perché si perderebbero le decisioni dell'unione.

**Fatti verificati nel codice** (sola lettura, checkout principale):

| Rilievo | Esito | Evidenza |
|---|---|---|
| R6-01 | confermato | `engine/autopath.lookup` (~362) filtra `status='active'` ma non `shadow`: un piano in ombra è servito |
| R6-04, M-11 | confermato | `invocations.next_invocation` (~937) riconsegna le invocazioni `delivered` orfane oltre scadenza + grazia: consumare il token al poll romperebbe la riconsegna |
| R6-08 | confermato | `prefilter.implements_intent_verb(candidate_verb, intent_verb)` confronta solo il verbo |
| R6-13 | confermato | 33 manifest hanno `setup`/`teardown`/`env` nei `[[tests]]`; il 24 della mia revisione 6 era sbagliato |
| R6-15 | confermato | `-H "Authorization: Bearer $(cat …)"` mette la chiave negli argomenti di `curl`, visibili in `/proc` |
| R6-16 | confermato | RM-0008 (~797) descrive `runtime/test_runner.py` come runner delle prove |
| R5-11 | confermato | l'hash di `_compute_intent_sig` ha 16 caratteri esadecimali, cioè 64 bit |
| E.2, D.6 | confermato | esistono tutti i file `client-rs/src/*.rs` citati, `runtime/change_applier_extend.py`, `runtime/agent_server.py`, `runtime/engine/fastpath_promote.py` |

**Osservazioni**, nell'ottica dei criteri di Roberto (KISS, autonomia, non bloccante):

- **O-01 — Il completamento dipende da lavori estranei al ciclo di crescita.**
  - `D-I0.1` (chiusura) richiede il protocollo remoto v2 con modifiche al client
    Rust e rollout sulla flotta (F6.4a-e), FS-B multipiattaforma con
    attestazione di release (FS-B.4c) e l'helper con socket (S0.1).
  - Proposta: una tranche o roadmap separata «sicurezza ed enforcement F6».
    RM-0009 chiude sul ciclo di crescita, con F6 in ombra.
  - `D-I1.1` non dipende da FS-B.4c finché D2 resta `blocked`: FS-B serve come
    veto di D2, che prima di RM-0008/F5 è comunque bloccata.
- **O-02 — D2 bloccata fino a RM-0008/F5 congela la nascita di capacità.**
  - La regola è sicura (R6-03, M-05), ma l'anello 7 resta a zero finché RM-0008/F5
    non è certificata, e oggi quel cancello è a zero ammissioni.
  - È una **decisione di prodotto** di Roberto:
    - (a) attendere F5;
    - (b) prima di F5, attivazione con il «sì» dell'host solo per capacità in
      sola lettura, senza rete né credenziali, con quarantena e ritiro rapido.
- **O-03 — `D-S0.1` è sproporzionato rispetto a R6-15.** Per non esporre la
  chiave basta leggere l'intestazione da file o da stdin: `curl -H @file`, che
  `curl` 8.5 supporta. Un nuovo socket con token non serve a questo scopo.
- **O-04 — `D-P2.9`: la revoca HMAC con «delete-pepper» è superflua.** Gli id
  utente sono casuali (`uuid.uuid4().hex[:16]`) e non si riusano, quindi basta
  un registro di cancellazione con tombstone. Il resto di P2.9 (inventario degli
  store con dati dell'owner) resta necessario.
- **O-05 — `D-G0.2` porta a Roberto decisioni tecniche.** Secondo la regola
  «Roberto interviene il meno possibile», gli restano solo O-02, l'ambito dei
  pattern d'uso di S2 (aggregati di più utenti) e il budget di costo. Il resto
  lo fissano gli agenti, con valori prudenti.
- **O-06 — Documento unico.** La revisione 8 cita per SHA due file non tracciati
  (`.codex-review/`, `.merge-sources/`), e la mia revisione 6 non è mai stata
  committata. Proposta:
  1. un commit che traccia la revisione 8 e le due sorgenti;
  2. un secondo commit che rimuove le sorgenti dall'albero.

  Restano in Git, con gli SHA verificabili, e vive un solo documento.
- **O-07 — Coerenza interna.** Finché G0.6 non riallinea, le schede A.4 e l'elenco
  candidato E.2 possono contraddire l'appendice D. D.1 dichiara la precedenza di
  D; conviene ripeterlo in testa ad A.4 e a E.2.

## Appendice D — Piano esecutivo dettagliato dello sviluppo

### D.1 Regola di esecuzione

Questo e l'elenco master delle attivita. Ogni ID e una consegna separata e un
solo commit. L'appendice D e autorevole e, in caso di differenza, sostituisce
le schede A.4 e l'ordine A.5; queste restano materiale di contesto finche
D-G0.6 non le riallinea. Nessuna unita di codice **non-FS** parte prima di
D-G0.10. Per la decisione gia vincolante del 14/9, FS-A/FS-B possono partire
dopo G0.6, lease e prerequisiti espliciti delle rispettive righe. Un agente
medium riceve una sola riga per volta. `Dip.` elenca singoli
prerequisiti obbligatori, non intervalli o suggerimenti.

D-G0.5 produce per ogni ID un work manifest versionato con tipo di unita,
owner, percorsi esatti (compresi test, fixture e documenti), simboli, DB e
numero di migrazione, comandi di prova e rollback. Il coordinatore non assegna
una riga se il relativo manifest manca o non coincide con la baseline.
Se un prerequisito non e chiuso, l'agente restituisce `BLOCKED_DEPENDENCY`.

Per le unita `implementation` e `migration` l'agente consegna: test rosso
iniziale, diff minimo, migrazione idempotente se necessaria, suite mirata e dei
moduli toccati, conteggi pytest, nota di rollback e commit contenente solo i
file assegnati. Le unita `inventory` richiedono due output riproducibili; le
unita `decision/document/review/approval` richiedono rispettivamente verbale,
diff documentale, rapporto indipendente o registrazione dell'approvazione, non
un test rosso artificiale. Il coordinatore assegna un lease esclusivo sui file.

### D.1-bis Separazione della sicurezza (decisione di Roberto, 15/9)

Le unità seguenti formano la **tranche di sicurezza**. È un lavoro a parte, già
autorizzato, che non condiziona la chiusura di RM-0009:
- `D-S0.1`;
- tutte le `D-FS-A.*` e le `D-FS-B.*`;
- `D-F6.3`, `D-F6.4a`-`D-F6.4e`, `D-F6.5`, `D-F6.6`.

RM-0009 conserva F6 **in ombra**: `D-F6.1`, `D-F6.2a`-`D-F6.2e` e
`D-F6.2.barrier`.

Restano due vincoli di dipendenza, non di chiusura:
- l'attestazione FS-B (`D-FS-B.4c`) è un veto di D2 per ogni capacità che
  raggiunge credenziali o piano di controllo (R6-14);
- l'esecuzione di codice candidato richiede `D-FS-A.4`.

Poiché D2 resta comunque `blocked` fino a RM-0008/F5 (decisione del 15/9),
nessuno dei due vincoli ritarda i milestone di RM-0009.

### D.2 Tranche G — Correzione del progetto e baseline

| ID | Dip. | File di proprieta | Attivita atomica | Prova e criterio di uscita |
|---|---|---|---|---|
| D-G0.1 | — | report in `internal/reports/rm0009-baseline/` | Registrare commit, worktree, file gia modificati, versioni DB e servizi, senza ancora copiare gli store; assegnare l'owner di ogni modifica presente. | Manifest di preflight digestato; ogni file sporco ha un owner oppure blocca l'unita. Lo snapshot coerente appartiene solo a D-P0.1. |
| D-G0.2 | G0.1 | Roberto per le decisioni di prodotto; coordinatore per la specifica | Fissare le decisioni normative: fatti D1/D2, `blocked` tecnico, semantica shadow, deduplica, protocollo remoto, costo/autorita e pattern d'uso S2. Nessuna scelta di prodotto viene delegata a un agente medium. | Tabella decisionale esaustiva senza `TBD`; simulazione su create, extend, promote e reject; decisioni materiali registrate tra quelle vincolanti. |
| D-G0.3 | G0.2 | `internal/roadmap/RM-0008-porta-unica-nascita-executor.md`, questo file | Emettere l'emendamento che assegna una sola porta Birth, risolve il destino di `test_runner.py` e rende RM-0008/F5 un prerequisito non aggirabile di D2. | Ricerca repository: nessuna istruzione attiva contraddittoria; approvazione dei maintainer dei due piani. |
| D-G0.4 | G0.1 | specifica helper, socket locale e test | Definire senza implementare: socket Unix con owner/mode/inode e peer credential verificati; token breve legato a metodo+path, mai chiave master; HTTP solo loopback/path relativo; proxy/redirect/URL assoluti vietati; payload stdin e redazione. | Modello di minaccia e prove argv/env/log, processo impostore loopback/socket, redirect/proxy e replay; implementazione D-S0.1. |
| D-G0.5 | G0.1 | report baseline, A.5, work manifest, elenco candidato E.2 | Generare inventari di manifest legacy, consumer di chiavi/vault, writer `TurnLog`, call graph F6, registrazioni per kind, produttori che scrivono marker di crescita o chiamano direttamente Birth/sintesi, file condivisi e **tutti** gli store owner-bearing esistenti (DB, JSONL, cache, auth, invocation, durable); verificare i candidati E.2 e generare il work manifest per ogni ID. | Inventari riproducibili; conteggi derivati dai file; nessun ingresso di crescita diretto, path generico o store owner-bearing senza lifecycle owner. |
| D-G0.6 | G0.2, G0.3, G0.4, G0.5 | questa roadmap, §§5.4.1 e D.8, A.4, A.5, work manifest | Integrare gli inventari; allineare §§5-6 e A.4-A.5 a D; congelare nomi moduli, DAG, schemi, file e numeri migrazione; generare test di aciclicita, macchina a stati e dominanza dei milestone. | Nessuna prescrizione concorrente; ogni unita obbligatoria e antenata del milestone pertinente; work manifest completo. |
| D-G0.7 | G0.6 | due agenti medium indipendenti | Eseguire il dry-run documentale senza contesto orale. | Stesso ordine, file, transizioni, commit point e test; ogni divergenza riapre G0.6. |
| D-G0.8 | G0.7 | reviewer architettura e sicurezza indipendenti | Ripetere la review del progetto e verificare tutti i gate C.3. | Nessun rilievo bloccante o alto irrisolto. |
| D-G0.9 | G0.8 | Roberto, poi coordinatore | Generare in anteprima il contenuto normalizzato senza storia; ottenere l'approvazione su quel payload e impostare lo stato `ready`. | Digest registrato; nessun codice non-FS iniziato. Eventuali commit FS gia autorizzati sono elencati con stato e prove. |
| D-G0.10 | G0.9 | questa roadmap | Nel commit documentale immediatamente successivo sostituire il file con l'esatto payload approvato, eliminando cronaca e appendici B/C. | Digest del file uguale a quello approvato; baseline esterna conservata; nessuna riscrittura della storia Git. |
| D-X0.1 | G0.3 | RM-0008/F5, responsabile RM-0008 | Produrre il gate esterno machine-readable `EXT-RM0008-F5` con commit, receipt di certificazione e suite richiesta. | Il verifier RM-0009 valida firma/digest, stato F5 e compatibilita del contratto; fino ad allora D2 resta bloccata. |

### D.3 Tranche P — Primitive condivise

| ID | Dip. | File di proprieta | Attivita atomica | Prova e criterio di uscita |
|---|---|---|---|---|
| D-S0.1 | G0.10 | nuovo `runtime/admin_local_auth.py`, startup service, `internal/tools/metnos_admin_request.py` e test manifest | Implementare socket peer-verified e token one-shot/TTL legato a metodo+path; l'helper lo usa su loopback senza mai leggere la chiave master. | Processo impostore su porta/socket, replay, URL/redirect/proxy e peer/mode errati negati; token assente da argv/env/output/log. |
| D-P0.1 | G0.10 | tool/report P0 dal work manifest | Implementare cutoff globale e snapshot coerente di SQLite/JSONL con watermark e digest. | Due snapshot dello stesso cutoff sono identici; un writer concorrente non attraversa il confine senza retry. |
| D-P1.1 | P0.1 | nuovo `runtime/growth_policy.py`; test policy | Definire un campo per parametro con nome Python/env, tipo, unita, default, min/max e tassonomia sensibile. | Test default, override, alias legacy, limiti e valore invalido; solo la crescita entra in stato degradato. |
| D-P1.2 | P1.1 | `growth_policy.py`, firma cache | Calcolare `GrowthPolicy.version` da JSON canonico e inserirla direttamente nelle firme di decisione e `tools_sig`. | Cambio env + restart invalida la cache; ordine delle chiavi non cambia la versione. |
| D-P1.3 | P1.2 | `growth_policy.py`, test decisioni | Aggiungere budget/costo, soglie benefit per kind e regole anti-ripetizione indipendenti da owner/provenienza. | Nessun campo owner/principal influenza soglia, ranking o decisione; retry duplicati non aumentano il campione. |
| D-P2.1 | P1.2 | `runtime/change_intents.py`; test migrazione | Definire migration ledger e DDL del solo nucleo intent+fonti+alias, con FK attive su ogni connessione. Valutazioni, rifiuti, token, operation ed epoca appartengono alle unita dedicate. | Migrazione vuota e da schema storico, rollback simulato, `foreign_key_check` vuoto. |
| D-P2.4 | P2.1 | nuovo `runtime/change_canonical.py`; test codec | Definire body chiuso per ogni kind, qualificatori sicuri e SHA-256 domain-separated su JSON canonico. | Vettori golden, delimitatori/Unicode, collision check e compatibilita di sola lettura col digest corto legacy. |
| D-P2.2 | P2.1, P2.4 | `change_intents.py`; test concorrenza | Migrare duplicati trasferendo fonti e sole tabelle figlie legacy gia esistenti al canonico e archiviando alias; creare unicita sul canonico. Le nuove valutazioni F2 risolvono sempre l'alias canonico alla scrittura. | Due connessioni convergono a un intent; indice creato con duplicati reali; il ripristino consolida senza collisione. |
| D-P2.3 | P2.2 | `change_intents.py`; test state machine | Implementare enum e tabella esaustiva D1/D2: incluso `trial_applying`, `blocked` con reason tipizzato, recovery e terminali legacy. | Test parametrico di ogni arco ammesso e di ogni salto negato; nessun `UPDATE` stato fuori dal CAS. |
| D-P2.8 | P1.2, P2.3 | `change_intents.py`; test epoch | Creare l'unica API epoch e il ledger idempotente dopo schema, deduplica e stati; ogni consumer successivo deve usarla per policy, apply, rifiuto, revoca e rollback. | Stesso event/operation ID incrementa una volta; policy version diversa dopo restart invalida la firma. |
| D-P2.5 | P2.3, P2.8 | nuovo `runtime/change_operations.py`; test recovery | Implementare journal/outbox con `operation_id`, phase, lease owner/expiry, attempt, before/after, receipt e CAS. | Due worker, lease scaduta e crash in ogni confine convergono a un solo effetto osservato. |
| D-P2.10 | P2.3, P2.8 | nuovo `runtime/rejection_rules.py`; test dedicato | Creare schema/API di lettura e scrittura per regole/revoche di governance, con binding esatto alla fingerprint e controllo comune prima dell'upsert. Non registrare alcun effect handler. | Regola attiva blocca ogni producer senza owner; expiry/revoca la rende valutabile; unknown schema fallisce chiuso. |
| D-P2.6 | P2.3, P2.8, P2.10 | nuovo `runtime/one_shot_tokens.py`; DB change intent | Implementare token di decisione co-locato: binding strutturato, destinatario/canale, TTL, revoca e consumo atomico con scelta, stato rejected, eventuale `rejection_rule` ed epoca. | Replay, doppio clic, inoltro, scadenza e crash non perdono ne duplicano la decisione/regola. |
| D-P2.7 | P2.5, P2.8 | `change_operations.py`, `change_intents.py` | Definire il protocollo degli effetti: claim; target `prepared` invisibile; observe; commit governance+epoch; visibilita autorizzata solo dall'operation committed; reconcile a ogni crash. Per gli executor resta autorevole il puntatore firmato RM-0008, per autopath il commit governance. Le regole di rifiuto restano fuori da operation/apply. | Fault test di ogni confine; mai due autorita concorrenti o una generazione globale intermedia. |
| D-P2.9 | P2.6, P2.7, F0.2 | nuovo `runtime/user_data_lifecycle.py`, `runtime/users.py`; test cancellazione | Creare registry obbligatorio di purge dall'inventario completo G0.5, inclusi turni JSONL, auth, invocation e durable. Alla cancellazione: revocation tag `HMAC(delete-pepper, owner_id)` per negare richieste future, tombstone casuale distinto per scollegare la provenienza. Ogni store futuro deve registrarsi. | Test esaustivita su ogni store esistente; ID non recuperabile dalle viste, stessa identita cancellata negata fail-closed tramite HMAC protetto. |

### D.4 Tranche F0-F3 — Osservazione, valutazione ed evidenza

| ID | Dip. | File di proprieta | Attivita atomica | Prova e criterio di uscita |
|---|---|---|---|---|
| D-F0.1 | P2.1, P2.4 | `runtime/agent_runtime.py`, route e writer turni dal work manifest | Introdurre origine/esito tipizzati e inventariare ogni `TurnLog(`, `write()` e scrittura diretta; eliminare `unknown` dai terminali. | Test parametrico di tutti i `final_kind`, incluso short-circuit HTTP, errore e ripresa. |
| D-F0.2 | F0.1 | `runtime/http_auth.py`, `runtime/users.py`, ingressi | Assegnare origin e principal soltanto al confine autenticato; ignorare valori dichiarati nel payload. | Host, guest, LAN, test e forwarded; spoofing non cambia origin/ruolo. |
| D-F0.3 | F0.1, F0.2 | file vista promoter/metriche dal work manifest | Costruire infrastruttura dei denominatori e produrre i soli indicatori 5 e 7 disponibili a F0, separando reale e collaudo; gli indicatori 1-10 completi appartengono a D-I0.5. | Fixture golden con denominatori nulli, errori e origini miste; nessun divide-by-zero. |
| D-F1.1 | G0.5, G0.10 | fixture registry e test collegamenti | Materializzare il registro chiuso producer→consumer→effect e automatizzare una prova di esecuzione per ogni riga. | Rimuovere ciascun collegamento fa fallire esattamente il relativo caso. |
| D-F1.2 | F1.1, G0.3 | file censiti da registry | Ritirare soltanto simboli davvero senza consumer; aggiornare documenti e contratti nello stesso commit. | Ricerca simboli vuota salvo allowlist; suite dei consumer verdi. |
| D-F1.3 | P2.2, P2.3, P2.4 | `change_intents.py`; test cleanup | Migrare kind ritirati per stato: non terminali in archivio/alias, terminali conservati come audit, create con target assente conservato. | Dry-run/apply idempotenti; restore transazionale senza collisioni. |
| D-F2.1 | P2.1, P2.4 | nuovo `runtime/change_evaluations.py` | Implementare schema append-only, revisioni, source ID e proiezione per dimension/evaluator/metrica/finestra. | Correzione concorrente e piu metriche benefit restano tutte interrogabili. |
| D-F2.2 | F2.1 | `change_evaluations.py`; job retention | Implementare writer tipizzati, latest projection, aggregati 180 giorni e watermark. | Retention non cambia il risultato delle query decisionali e puo riprendere dopo crash. |
| D-F2.3 | F2.1 | `runtime/alignment_engine.py`, adapter valutazione | Restituire `ok/not_applicable/failed` senza trasformare errori in lista vuota; definire qui `JudgeFailureCode.INVALID` per JSON invalido e persistere retry/backoff. | Servizio assente, JSON invalido, non applicabile e successo distinti; una nuova versione rivaluta. |
| D-F2.4 | P2.4, F2.1 | costruttori canonici e migrazione privacy | Rifiutare testo libero nei global store; scansionare e quarantinare/redigere summary/rationale/body legacy. | Corpus PII/segreti e due owner: nessun valore appare in DB, viste, log o digest reversibili. |
| D-F3.1 | F0.1, P2.4, P2.9 | `engine/dispatch.py`, `engine/terminator.py`, nuovo modulo evidenza | Definire `GapEvidenceV1` con event ID immutabile, turn/action ordinal, verbo, oggetto, azioni e qualificatori canonici; registrare purge/tombstone dello store. | Due azioni nello stesso turno restano distinte; riclassificazione crea revisione, non nuovo conteggio; delete-user non lascia ID riferibili. |
| D-F3.2 | F3.1 | `runtime/prefilter.py` e catalogo | Implementare `implements_intent(candidate, intent)` su verbo, oggetto, generazione e capability richieste. | Matrice positive/negative con stesso verbo e oggetti diversi; nessun falso “gia coperto”. |
| D-F3.3 | F3.1, F3.2 | job riconciliazione dal work manifest | Confrontare attesi/osservati per turn+action ordinal, con classe chiusa delle esclusioni. | Sotto/sopra soglia, multipli per turno e restart; rapporto causale riproducibile. |

### D.5 Tranche F4-F5 — Sorgenti, decisione ed effetti globali

| ID | Dip. | File di proprieta | Attivita atomica | Prova e criterio di uscita |
|---|---|---|---|---|
| D-F4.0 | P2.2, P2.4 | modifica `runtime/change_intent_adapters/__init__.py`; schema fonti | Rendere il registry l'unica autorita di component ID/version/digest; l'output adapter non contiene provenienza. Il materializzatore inietta il componente e deriva `origin_owner_id` dalla fonte autenticata. Conservare tutte le fonti append-only, senza primaria privilegiata. | Spoof di componente/owner respinto; due producer concorrenti conservano entrambe le fonti senza influire sulla decisione. |
| D-F4.1 | F3.3, F4.0, P1.3, P2.10 | modifica registry e nuovo `runtime/change_intent_adapters/gap.py` | Registrare S1; ricontrollare catalogo e rejection API; deduplicare eventi canonici globalmente senza owner/principal nelle soglie. | Tre eventi validi aprono un solo intent; collaudi/retry/rifiuto/catalogo gia capace non contano. |
| D-F4.2a | F0.3, P1.3, P2.4, P2.9 | nuovo store/proiettore pattern dal work manifest | Proiettare dai turni reali soli eventi canonici privi di testo/valori; owner resta audit e non entra nella decisione; associare source ID/watermark e registrare il purger. | Retry/replay non duplicano; corpus PII non entra; delete-user tombstona la provenienza senza cambiare l'aggregato globale. |
| D-F4.2b | F4.2a | aggregatore pattern dal work manifest | Aggregare pattern d'uso per bisogno/finestra con soglie globali e output tipizzato, senza dimensioni owner. | Restart e multiprocesso convergono; stesso corpus produce lo stesso aggregato. |
| D-F4.2c | F4.0, F4.2b, F2.3, P2.10 | modifica `runtime/change_intent_adapters/telos.py`; nuovo `runtime/change_intent_adapters/usage_patterns.py` | Produrre S2 da TELOS e pattern aggregati; controllare rifiuti; scegliere create/extend con regola deterministica sul catalogo. | Stesso input canonico produce stesso intent; nessun owner influenza tipo, ranking o soglia. |
| D-F4.3a | F4.0, P2.4, P2.9, F2.4 | nuovo modulo template indicato dal work manifest; `runtime/engine/autopath.py` | Definire/migrare `PlanTemplateV1`, placeholder, scanner, rebinding e purger. Per ogni piano legacy sicuro gia servito creare una operation `legacy_committed` che preserva il comportamento; quarantinare come shadow ogni piano non canonico/privato. | Ogni riga ha governing operation; golden schema/migrazione; nessun valore privato globale; il backfill non disattiva silenziosamente piani sicuri. |
| D-F4.3b | F4.3a, P2.7 | `runtime/engine/autopath.py` | Escludere `shadow=1` dal lookup ordinario e ammettere soltanto operation committed o `legacy_committed`; non raccogliere ancora probe. | Prima del commit nessun utente usa il nuovo piano; dopo il commit lo usano tutti; legacy sicuro mantiene l'esito. |
| D-F4.3c | F4.3b, F0.3, P1.3 | autopath e modulo metriche dal work manifest | Creare solo replay su trace sanificate e simulazione read-only: non invocare executor, undo o API esterne; per piani mutanti validare struttura e binding, non eseguire l'effetto. | Probe non cambia output, store o mondo esterno e produce campione causale deduplicato. |
| D-F4.3d | F4.3b, F4.3c, P2.7 | nuovo `runtime/change_intent_adapters/optimization.py`; funzioni autopath | Produrre intent S3 e funzioni idempotenti apply/observe/rollback, non ancora registrate nel registry degli effetti. | Apply committed abilita globalmente; observe distingue operation; rollback verificato rimette in shadow. |
| D-F4.4 | F4.1, F4.2c, F4.3d, F2.1 | file job e registry dal work manifest | Per ogni adapter, committare upsert intent+source nella sola transazione locale; dopo il commit invocare il giudizio esterno idempotente, che persiste la valutazione in una seconda transazione. | Test end-to-end per S1/S2/S3; timeout del judge, crash e rerun non tengono lock e non duplicano. |
| D-F5.1 | F4.4, P1.3, FS-B.4c | nuovo `runtime/growth_facts.py` | Implementare `ChangeFactsV1` core-owned con tabella delle fonti: D1 non usa receipt; D2 richiede receipt/manifest/preexercise/attestazione FS-B quando applicabile e verifica autorita/sensibilita. | Anti-auto-attestazione campo per campo; receipt assente blocca D2 ma non D1; attestazione FS-B assente non e superabile. |
| D-F5.2 | F5.1 | nuovo `runtime/growth_decision.py` | Implementare funzione pura e tabella di precedenza: veto tecnico prima, rifiuto, D1, D2 e consenso/costo. | Test parametrico di ogni riga e combinazioni; pre-RM8/F5 sempre `blocked`/`dependency_unready`. |
| D-F5.3 | F5.2, P2.6, P2.9, F0.3 | file promoter digest/state e i18n dal work manifest | Implementare coda persistente, ranking, budget, scadenza, callback namespaced e purger; D1 yes va a trial, D2 yes ad accepted. | Host/admin soli; guest/forward/replay/double click/rivalutazione/delete-user falliscono sicuri; nessun salto D1→accepted. |
| D-F5.4 | F5.2, P2.5, G0.3, FS-A.4 | `runtime/synth_request.py`, adapter RM-0008 | Applicare D1 come operation idempotente `trial_applying`; inviare BirthIntent con dati candidati definiti e riconciliare receipt. | Crash prima/dopo invio e due worker producono una richiesta e una receipt associata. |
| D-F5.5a | F5.3, F5.4, P2.7 | registry effetti e file RM-0008 dal work manifest | Registrare/applicare/observare/ritirare `create_executor` esclusivamente via RM-0008. | Crash a ogni confine, operation idempotente e receipt firmata; nessuna modifica diretta a manifest/lifecycle. |
| D-F5.5b | F5.5a | registry effetti e `change_applier_extend.py` | Registrare/applicare/observare/ritornare `extend_executor` alla generazione precedente via RM-0008. | Stesse prove di create, incluse target/generazione concorrenti. |
| D-F5.5c | F5.5a, F4.3d | registry effetti, `change_applier.py`, autopath | Registrare le funzioni `promote_plan` prodotte da F4.3d. | Apply/observe/rollback operation-specific; visibilita globale solo dopo commit. |
| D-F5.5e | F5.5b, F5.5c | registry e test parametrico | Rendere esaustivo kind→schema→handler→observe→rollback per i tre change kind e rifiutare unknown kind prima della decisione. `rejection_rule` e esplicitamente assente. | Test di copertura registry e crash matrix per create/extend/promote; tentare di registrare rejection come effect fallisce. |
| D-F5.6 | F5.3, P2.6, P2.8, P2.10 | `runtime/rejection_rules.py`, `growth_decision.py` | Verificare la transazione atomica del «no» gia introdotta da P2.6; implementare scadenza/revoca append-only ed epoch. I producer usano l'API P2.10. | Nessuna rigenerazione fino a expiry/revoca; crash sul «no» non separa token, stato, regola o epoca. |
| D-F5.7 | F5.5e, F5.6, F0.3, F2.2, P1.3 | valutazioni benefit e rollback dal work manifest | Calcolare vettore success/latency/cost per kind, baseline causale e sample deduplicato; rollback solo dopo `observe_effect` inverso. | No traffic = insufficient; peggioramento attribuito provoca un solo rollback; failure non attribuita non lo provoca. |
| D-F5.8 | F5.5e, P2.5, P2.8 | nuovo `runtime/growth_safety.py`, adapter RM-0008/F6 e test | Implementare `AuthorityViolationV1` autenticata: quarantine immediata; create/extend richiedono ritiro RM-0008, promote torna shadow; stato finale solo dopo observe/receipt. | Evento falsificato ignorato; evento valido blocca uso globale senza attendere campione; crash/retry convergono a una quarantine. |
| D-F5.9 | F5.2, F5.4, F4.2c, G0.5 | `runtime/proposal_actions.py`, `runtime/telos_synth_consumer.py`, `runtime/engine/fastpath_promote.py` e ogni altro ingresso trovato dal work manifest | Chiudere i percorsi che scrivono `synt_pending` o chiamano direttamente `handle_synth_request`: ogni proposta di crescita diventa prima intent canonico con fonte registrata e attraversa D1; restano fuori soltanto gli ingressi espliciti non classificati come crescita dal registro chiuso. | Per ciascun produttore censito: nessuna chiamata Birth prima di D1, retry idempotente, rifiuto efficace e «si» che arriva a `trial_applying`, mai direttamente ad `accepted` o attivo. |

### D.6 Tranche FS e F6 — Confini di sicurezza e invocazione

| ID | Dip. | File di proprieta | Attivita atomica | Prova e criterio di uscita |
|---|---|---|---|---|
| D-FS-A.1 | G0.5 | report inventario FS-A | Censire in sola lettura runner e ogni manifest/fixture legacy nel repository e nella release installata; non eseguire mai il runner legacy. | Elenco con path, campo, semantica e owner; ricerca ripetuta produce lo stesso inventario. |
| D-FS-A.2 | FS-A.1, G0.3, G0.6 | `runtime/executor_birth_runner.py`, `runtime/synth_request.py` e test dal work manifest | Introdurre nel runner Birth una compatibilita chiusa per le sole semantiche legacy censite e instradare li ogni prova, senza ancora rifiutare i campi. | Tutte le prove avvengono nel sandbox Birth con limiti rete/processi/utente/IPC/env/cwd/CPU/RAM/output; nessuna chiamata a `test_runner.py`. |
| D-FS-A.3.N | FS-A.2 | template sostituito da G0.5 con ID e file esatti, uno per manifest/fixture o gruppo disgiunto | Convertire ciascun `setup/teardown/env` in fixture ermetica o operazione chiusa; un commit per gruppo di file disgiunto. | La stessa asserzione sostanziale passa nel runner Birth prima e dopo la conversione; nessuna esecuzione sull'host. |
| D-FS-A.3.barrier | FS-A.2 | coordinatore, manifest di chiusura | Leggere l'elenco concreto generato da G0.5 e bloccare finche ogni D-FS-A.3.N espansa non e integrata; verificare che nessun file sia rimasto senza conversione. | Barrier firmata con tutti gli ID/commit/test concreti; nessun glob o `N` resta nel release manifest. |
| D-FS-A.4 | FS-A.3.barrier | manifest censiti, `runtime/test_runner.py`, grammatica | Ripubblicare i manifest convertiti; rifiutare i campi con errore tipizzato; rimuovere riferimenti, grammatica e infine runner legacy. | Inventario legacy/riferimenti vuoto; equivalenza e isolamento verdi sull'intero censimento. |
| D-FS-B.1a | G0.5, G0.6 | nuovo `runtime/protected_roots.py`, fixture multipiattaforma | Definire enum delle radici e matrice read/write/execute, denyset hardlink `(device,inode)`, regole mount e vettori golden condivisi. | Contratto chiuso e test golden; nessuna integrazione runtime in questa unita. |
| D-FS-B.1b | FS-B.1a | `runtime/sandbox.py` e test server | Applicare il contratto al confine finale Python con no-follow/openat2 e denyset. | Assoluti/env/symlink/hardlink/magiclink/mount/race negati nel sandbox server. |
| D-FS-B.1p | FS-B.1a | `client-rs/src/config.rs`, `client-rs/src/runner.rs` e test Rust | Derivare da `Paths` e passare alle sandbox le radici effettive anche in installazioni non standard. | Nessun default implicito; Linux/Windows ricevono lo stesso contratto serializzato. |
| D-FS-B.1c | FS-B.1p | `client-rs/src/sandbox_common.rs`, `sandbox_linux.rs` e test Rust | Applicare il contratto prima che argomenti/hint diventino bind. | Vettori golden identici al server; nessun fallback permissivo Linux. |
| D-FS-B.1d | FS-B.1p | `client-rs/src/sandbox_windows.rs`, `appcontainer.rs` e test Rust/Windows | Applicare ACL/AppContainer; Job Object non puo allargare il filesystem. | Vettori golden Windows; senza AppContainer fail-closed sulle radici protette. |
| D-FS-B.1e | FS-B.1b, FS-B.1c, FS-B.1d | coordinatore sicurezza | Eseguire la matrice multipiattaforma e confrontare i risultati. | Barrier firmata: stesso allow/deny su server, Linux e Windows. |
| D-FS-B.2 | FS-B.1e | nuovo broker core e test dal work manifest | Implementare il solo broker tipizzato. Il segreto resta nel core; l'handle e one-shot e legato a invocation/principal/operazione/target/digest/epoca/TTL. | Replay/cross-owner/cross-target/scadenza negati; nessun segreto nel figlio. |
| D-FS-B.3.N | FS-B.2, F0.2, F1.2 | template espanso da G0.5, una famiglia consumer per unita | Migrare separatamente CRUD credenziali, mail e ciascun provider censito al broker; owner dal principal, output minimo. | Test di famiglia e due utenti; nessun mount/handle generico. |
| D-FS-B.3.barrier | FS-B.2 | coordinatore, censimento consumer G0.5 | Bloccare finche ogni D-FS-B.3.N concreta e integrata. | Tutti gli ID/commit/test nel manifest; ricerca consumer diretti vuota. |
| D-FS-B.4a | FS-B.3.barrier, FS-B.1b | `runtime/sandbox.py`, config e test | Rimuovere mount vault/admin key dal server. | Nessuna chiave raggiungibile in argv/env/mount/processo locale. |
| D-FS-B.4b | FS-B.3.barrier, FS-B.1e | sandbox client Rust e test remote | Rimuovere accessi/mount equivalenti dai device e negare bind automatici di radici protette. | Nessuna chiave/config/identita device raggiungibile da executor remoto. |
| D-FS-B.4c | FS-B.4a, FS-B.4b | autorita di release separata dall'implementatore; attestazione FS-B | Firmare receipt legata a commit server, digest binari client per piattaforma, policy protected-roots, configurazione effettiva, censimento consumer e suite. Ogni drift la invalida. | Auto-attestazione respinta; receipt assente, firma/digest/config/policy divergenti bloccano D2. |
| D-F6.1 | F5.2, FS-B.4c | nuovo `runtime/invocation_authority.py` e test dal work manifest | Implementare `build_invocation_envelope()` core-owned e `decide_invocation()` come intersezione aggiuntiva di auth/ACL/consenso/scope. | Ogni campo auto-dichiarato dal caller viene ignorato; due utenti non riusano autorita o argomenti. |
| D-F6.2a | F6.1 | `runtime/invocation_authority.py`, nuovo modulo stats e test | Implementare receipt shadow e contatori/flush con watermark, senza hook. | Multiprocesso/restart e nessuna scrittura per invocation nel percorso caldo. |
| D-F6.2b | F6.2a | `runtime/agent_runtime.py` e test locali | Installare un solo hook shadow locale prima di undo/subprocess. | Esito invariato; una decisione per invocazione; nessun bypass locale. |
| D-F6.2c | F6.2a | `runtime/loader.py` e test builtin/verb-unique | Installare un solo hook shadow prima del builtin. | Esito invariato; builtin e verb-unique contati una volta. |
| D-F6.2d | F6.2a | `runtime/remote_exec.py`, `runtime/invocations.py`, `runtime/agent_server.py` e test | Installare shadow all'accodamento remoto senza cambiare wire/esecuzione. | Esito invariato; enqueue e redelivery non duplicano il conteggio. |
| D-F6.2e | F6.2a | `runtime/durable_workloads/execution.py`, `runtime/executor_scheduler.py` e test | Installare shadow una volta per tentativo executor; marcare i passi interni non applicabili. | Retry conta tentativi distinti; nessun doppio gate nel ponte scheduler. |
| D-F6.2.barrier | F6.2b, F6.2c, F6.2d, F6.2e | test call graph generato da G0.5 | Provare globalmente esattamente un hook autorevole per percorso. | Nessun bypass/doppio gate; shadow non cambia alcun esito o effetto. |
| D-F6.3 | F6.2.barrier, X0.1, FS-A.4, FS-B.4c, F5.8 | `runtime/agent_runtime.py`, `runtime/loader.py` e test manifest | Attivare enforcement locale e builtin immediatamente prima dell'effetto; quando rileva una regressione di autorita emettere verso F5.8 un `AuthorityViolationV1` autenticato e idempotente. | Diniego: nessun undo/subprocess/builtin; cache invalida a policy/epoch nuova; segnale valido apre una sola quarantine operation. |
| D-F6.4a | F6.2.barrier, X0.1, FS-A.4, FS-B.4c | `client-rs/src/wire.rs`, fixture/protocollo e test compatibilita | Specificare `remote_auth_v2`, feature poll, grant, completion e stati `claimed/start_authorized/completed/execution_unknown`; nessun secondo grant dopo CAS, retry solo con idempotenza attestata. | Golden wire, state table e matrice old/new/downgrade; nessun enforcement in questa unita. |
| D-F6.4b | F6.4a, F5.8 | `runtime/remote_exec.py`, `runtime/invocations.py`, `runtime/agent_server.py` e test | Implementare server compatibile con v1 gia iniziato ma fail-closed per nuovo→client senza v2; endpoint/CAS start, completion firmata, lease e reconcile `execution_unknown`. Violazioni attestate dal confine remoto emettono verso F5.8 un solo `AuthorityViolationV1` autenticato. | Old in-flight converge; nuovo→old negato; revoca pre-CAS negata; post-CAS senza completion non riceve secondo grant; replay del segnale non duplica la quarantine. |
| D-F6.4c | F6.4a, F6.4b | `client-rs/src/runner.rs`, `client-rs/src/wire.rs` e test Rust | Implementare claim, richiesta firmata, verifica grant prima dell'effetto e completion firmata dopo; persistere localmente lo start prima di eseguire. | Payload/expiry/device/generazione/epoca alterati negati; crash dopo CAS/prima effetto e dopo effetto/prima receipt producono stati distinti/conservativi. |
| D-F6.4d | F6.4c | `runtime/agent_server.py`, `runtime/devices.py`, `client-rs/src/selfupdate.rs`, descrittori component update e test manifest | Distribuire il client v2 tramite il gestore centrale, anche dopo riavvio, e verificare che ogni device attivo attesti la feature; nessun rollout per owner. | Receipt fleet/versione e prova restart/update fallito/rollback; device vecchio non riceve payload operativo governato. |
| D-F6.4e | F6.4d | configurazione server globale e test E2E | Attivare globalmente v2 senza fallback per tutte le invocazioni governate. | Poll perso→redelivery; replay/payload diverso/expiry/revoca/crash non eseguono; tutti gli utenti hanno la stessa regola. |
| D-F6.5 | F6.2.barrier, X0.1, FS-A.4, FS-B.4c, F5.8 | `runtime/durable_workloads/execution.py`, `runtime/executor_scheduler.py` e test manifest | Applicare una sola ammissione per tentativo executor e dichiarare i passi interni senza autorita executor; una regressione verificata emette verso F5.8 un `AuthorityViolationV1` autenticato e idempotente. | Enqueue/restart/retry/revoca; nessun bypass o doppia contabilizzazione; replay del segnale non duplica il rollback. |
| D-F6.6 | F6.3, F6.4e, F6.5, P2.9 | `runtime/user_data_lifecycle.py`, `runtime/users.py` e purger del manifest | Registrare autorizzazioni remote, queue, receipt owner-scoped e workload; applicare revocation HMAC e tombstone casuale distinto. | Delete in ogni stato/restart; nessuna esecuzione residua, ID non esposto e reingresso negato. |

### D.7 Tranche I — Integrazione, rilascio globale e chiusura

| ID | Dip. | Responsabile | Attivita atomica | Prova e criterio di uscita |
|---|---|---|---|---|
| D-I1.1 | F1.1, F1.2, F1.3, F5.6, F5.7, F5.8, F5.9, F6.2.barrier, P2.9 | coordinatore | Integrare il milestone pre-certificazione: registro/ritiri/migrazioni legacy, F0-F5 infrastrutturali, chiusura di ogni ingresso diretto alla sintesi, F6 shadow e D2 `blocked`/`dependency_unready`. La tranche di sicurezza (D.1-bis) procede a parte. | Test di dominanza verde; stato `implemented_pending_rm0008`; prova che nessuna approvazione o marker attiva D2 o Birth fuori dal ciclo. |
| D-I0.1 | I1.1, X0.1 | coordinatore | Integrare in ordine topologico i commit elencati nel release manifest, risolvere conflitti dai rispettivi owner e rieseguire suite dei moduli toccati. | Worktree pulito, `git diff --check`, nessun test indebolito, release manifest con conteggi completi. |
| D-I0.2 | I0.1 | coordinatore sicurezza | Eseguire fault injection su transazioni, lease, token, Birth, apply, rollback, redelivery e restart. | Ogni crash converge; nessun effetto senza receipt/commit; nessun replay produce un secondo effetto. |
| D-I0.3 | I0.1 | coordinatore privacy | Eseguire matrice con almeno due utenti: stesso catalogo/routing globale; dati, argomenti, credenziali, task, ricevute d'invocazione e riprese isolati. Le ricevute del cambiamento globale restano canoniche e prive di dati owner. | Tutti vedono la modifica attiva; nessun artefatto owner-scoped attraversa il confine. |
| D-I0.4 | I0.2, I0.3 | coordinatore | Eseguire un ciclo reale S1, S2-TELOS, S2-pattern e S3 con ID causali; includere rifiuto, revoca, retry e rollback. | Evento→intent→valutazione→decisione→effetto→misura completo per ogni sorgente. |
| D-I0.5 | I0.4 | coordinatore | Produrre tutti gli indicatori 1-10 e verificare successo, latenza, costo, valore TELOS, domande e autorita; nessuna attivazione per-owner. | Soglie di §5.6 rispettate e indicatori non vuoti dove il ciclo reale li rende applicabili. |
| D-I0.6 | I0.5 | due reviewer indipendenti | Ripetere review architettura/sicurezza e dry-run medium del codice e del release manifest approvato. | Nessun rilievo bloccante/alto; file, transizioni, test e risultati concordano con il digest approvato a G0.9. |
| D-I0.7 | I0.6 | coordinatore | Impostare lo stato `implemented`, poi `complete` solo quando le condizioni del §11 sono tutte provate. | Report finale persistente; baseline e digest di approvazione conservati; nessuna riscrittura della storia Git. |

### D.8 Schemi minimi da congelare in G0.6

Questa tabella integra il catalogo dati della proposta esterna, correggendone i
conflitti con globalita, atomicita e ciclo D1/D2. I nomi fisici e il numero di
migrazione vengono confermati dal work manifest; campi e invarianti minimi non
possono essere ridotti da un agente esecutore.

| Unita proprietaria | Oggetto | Campi/invarianti minimi |
|---|---|---|
| P2.1/P2.3 | `change_intents` | ID canonico, kind, body canonico e versione, fingerprint SHA-256, stato, `row_version`, `decision_moment`, `resume_state`, `failure_phase`, contract/policy version e timestamp. Nessuna provenienza o identita owner top-level. |
| P2.2 | alias/archivio legacy | ID e fingerprint legacy, ID canonico, motivo, digest pre/post e timestamp; unicita sul canonico attivo; ripristino soltanto come consolidamento transazionale. |
| F4.0 | `change_intent_sources` | FK intent, `source_event_id`, kind/fine TELOS, component ID/version/digest iniettati dal registro, `origin_owner_id` opaco e timestamp; tutte le fonti append-only, nessuna primaria. |
| F2.1 | `change_intent_evaluations` | ID totale, FK intent, `evaluation_key`, dimensione, stato, valore, metrica, unita, finestra, campione, baseline/osservato, evaluator/versione, source event, revisione/supersedes e timestamp; `UNIQUE(intent_id,evaluation_key,revision)`. |
| P2.5 | `change_operations` e outbox | `operation_id`, intent/kind, fase, lease owner/scadenza, tentativo, autorita target/versione, digest before/after/receipt, errore tipizzato e timestamp; claim e commit con CAS. |
| P2.10/F5.6 | `rejection_rules` e revoche | rule ID, fingerprint canonica, token/decisione, principal autenticato o tombstone, creazione/scadenza; revoche append-only. Non e un change kind e non possiede handler. |
| P2.6 | token di decisione | nello stesso DB dell'intent: token casuale, purpose, binding kind/ID/generazione/digest, destinatario/canale, scadenza, consumo/principal e revoca/motivo; «no», stato, regola ed epoca nella stessa transazione. |
| P2.8 | epoca globale | riga epoch e ledger degli event/operation ID gia applicati; stesso evento incrementa una volta. `GrowthPolicy.version` entra direttamente nelle firme anche senza incremento. |
| F3.1 | `lacuna_events` | event ID immutabile da turno+ordinale azione, contratto canonico, classificazione revisionabile, generazione catalogo, classificatore e provenienza owner soggetta a cancellazione. |
| F4.3a | `PlanTemplateV1` | scheletro e placeholder allowlist senza valori, fingerprint, operation autorevole, stato shadow/legacy committed e versione; argomenti concreti rilegati solo dal principal corrente. |
| F6.2a | statistiche F6 | process start ID, sequenza/watermark, percorso, executor, decisione/reason e conteggio; unicita idempotente del flush, nessun record per singola invocazione. |
| F6.4a-e | autorizzazione remota v2 | invocation/device/principal operativo, contratto/generazione, digest payload, epoca, scadenza, claim, CAS start, completion firmata e stato `execution_unknown`; nessun secondo grant dopo start. |
| F0.1/P2.9 | record turno e lifecycle owner | origin/outcome/intent hash canonico; registro di ogni store owner-bearing, purger, revocation HMAC e tombstone distinto. |

## Appendice E — Registro dell'unione delle due revisioni

Questa appendice documenta l'unione caso per caso richiesta da Roberto. E
storica e viene rimossa dopo l'approvazione secondo il §12. Le due sorgenti
integrali, identificate dagli SHA-256 in testa al documento, restano separate e
non normative.

### E.1 Decisioni puntuali

| Caso | Apporto confrontato | Decisione | Motivo |
|---|---|---|---|
| M-01 | Stato del documento | mantenuta la prudenza della revisione 7 | Un elenco lavori non equivale a review conclusiva; G0.7-G0.8 restano necessari. |
| M-02 | S2 limitata a TELOS nella proposta esterna | mantenute TELOS e pattern d'uso aggregati | L'obiettivo vincolante include anticipazione dagli schemi d'uso. |
| M-03 | Provenienza duplicata nell'intent e nelle fonti | mantenuta solo in `change_intent_sources` | Evita una fonte primaria arbitraria; componente e `owner_id` restano audit, mai cancello. |
| M-04 | Proiezione delle valutazioni per sola dimensione | mantenuta la chiave per valutatore/metrica/unita/finestra/evento | Successo, latenza e costo devono coesistere; introdotta `evaluation_key` univoca. |
| M-05 | Fatto mancante trasformato in domanda | mantenuta la separazione D1/D2 e `blocked` tecnico | Una persona non puo sostituire receipt, firma, preesercizio o dipendenza. |
| M-06 | Tabella stati esterna con `observed/finalized` e salti diretti | integrazione selettiva nel §5.4.1 | Gli stati storici utili restano; `proposed→accepted` vale solo per promote, create/extend passano da trial; `superseded` e terminale. |
| M-07 | `reject_rule` come quarto change kind | escluso | La regola e governance atomica del «no», senza apply/observe/rollback. |
| M-08 | Effetto diretto e falsa transazione fra DB | mantenuti prepare/commit, autorita per tipo e outbox | Solo il puntatore RM-0008 o il commit governance rende globale una modifica. |
| M-09 | Politica in tabella descrittiva senza tipi/range | mantenuto il registro macchina della revisione 7 | Un agente medio deve poter validare ogni campo e non introdurre letterali. |
| M-10 | Digest storico corto come identita globale | escluso | Il nuovo ID usa SHA-256 del codec canonico; il corto resta alias di migrazione. |
| M-11 | Token remoto consumato al poll | escluso | Rompe redelivery e revoca; resta claim seguito da `authorize_start` con CAS immediatamente prima dell'effetto. |
| M-12 | Numero fisso di manifest FS-A e rimozione immediata dei campi | escluso | Il conteggio e gia variabile e le prove legacy vanno convertite con equivalenza prima del rifiuto. |
| M-13 | FS-B limitata al server e al CRUD credenziali | escluso | Il confine comprende server, device, hardlink, mail e provider censiti, con attestazione di release. |
| M-14 | Completamento con soli quattro indicatori | escluso | Restano indicatori 1-10, visibilita globale e isolamento dei dati per owner. |
| M-15 | Comandi amministrativi con chiave in argv | esclusi | Resta l'helper con socket protetto e token breve, implementato da D-S0.1. |
| M-16 | Catalogo esterno di file, simboli e prove | accolto come elenco candidato E.2 | E utile per gli agenti medi, ma G0.5 deve riconfermarlo sul commit di partenza. |
| M-17 | Percorso `proposal_actions → synt_pending → telos_synth_consumer → handle_synth_request` | accolto e generalizzato in D-F5.9 | E un vero ingresso che puo aggirare D1; il censimento include anche `fastpath_promote` e ogni altro chiamante. |
| M-18 | Schema dati consolidato della proposta esterna | accolto con correzioni in D.8 | Conservati i dettagli utili; rimossi provenienza top-level, token in DB separato e rejection come kind. |
| M-19 | Nomi di test puntuali della proposta esterna | accolti come candidati, non come prova esistente | Molti file di test sono ancora da creare; il work manifest fissa nome finale e comando. |
| M-20 | Cronaca da cancellare dopo approvazione | mantenuta ed estesa a questa appendice | Dopo l'approvazione restano soltanto specifica e piano approvati, senza riscrivere Git. |

### E.2 Elenco candidato di file e simboli

I percorsi esistenti sotto sono stati ricontrollati il 14/9; quelli indicati
come «nuovo» non esistono ancora. D-G0.5 deve risolvere nuovamente ogni simbolo,
aggiungere i chiamanti mancanti e produrre percorsi/test esatti prima
dell'assegnazione.

| Area | Esistenti da verificare | Nuovi candidati |
|---|---|---|
| P0-P1 | `runtime/learning_loop.py`, `runtime/stack_reconcile.py` | `internal/tools/rm0009_baseline.py`, `runtime/growth_policy.py`, relativi test |
| P2 | `runtime/change_intents.py`, `runtime/engine/cache_validity.py` | `runtime/change_canonical.py`, `runtime/change_operations.py`, `runtime/rejection_rules.py`, `runtime/one_shot_tokens.py`, test migrazione/concorrenza |
| F0 | `runtime/agent_runtime.py`, `runtime/http_auth.py`, `runtime/users.py`, route e chiamanti di `run_turn` | `runtime/turn_outcome.py`, `runtime/growth_indicators.py`, test origine/esito |
| F1 | `runtime/vaglio.py`, `runtime/engine/{executor,dispatch}.py`, `runtime/change_{applier,rollback}.py`, adapter e documenti lifecycle | registro test dei collegamenti e `internal/tools/rm0009_backlog_cleanup.py` |
| F2 | `runtime/alignment_engine.py`, adapter, route amministrative e job | `runtime/change_evaluations.py`, test valutazioni/privacy |
| F3 | `runtime/engine/{dispatch,terminator}.py`, `runtime/prefilter.py` | `runtime/engine/gap_evidence.py`, test eventi e riconciliazione |
| F4 | adapter `telos`, registro adapter, autopath e job `change_intent_materialize` | adapter `gap.py`, `usage_patterns.py`, `optimization.py`, store pattern e test |
| Ingressi D1 | `runtime/proposal_actions.py::on_accept`, `runtime/telos_synth_consumer.py::run_once`, `runtime/engine/fastpath_promote.py`, chiamanti di `handle_synth_request` e `submit_*_birth` | test parametrico che ogni produttore di crescita attraversi D1 |
| F5 | promoter digest/state, canale, route amministrativa, change applier/rollback, `executor_birth_{intent,properties}.py` | `runtime/growth_{facts,decision,safety}.py`, recovery e test fault-injection |
| F6 | `runtime/agent_runtime.py`, `runtime/loader.py`, durable execution/scheduler, `runtime/{invocations,remote_exec,agent_server,devices}.py` | `runtime/invocation_authority.py`, protocollo/test v2 |
| Device | `client-rs/src/{config,runner,wire,selfupdate,sandbox_common,sandbox_linux,sandbox_windows,appcontainer}.rs` | fixture comuni e prove Linux/Windows |
| FS-A/FS-B | `runtime/{synth_request,executor_birth_runner,executor_birth_identity,test_runner,sandbox,credentials,vaglio}.py`, generatori builtin e consumer vault/chiavi | `runtime/protected_roots.py`, broker core, inventari e attestazioni |
