# RM-0009 — Crescita allineata delle capacità

> **RM-0009**
> - **Stato:** `active`. La review multidisciplinare della revisione 5 ha trovato
>   rilievi bloccanti: non passa a `ready` finché non sono chiusi nell'architettura
>   normativa e nelle schede operative (appendice C).
> - **Creazione e revisione:** creata il 2 settembre 2026; ultima revisione il 14 settembre 2026.
> - **Conservazione:** persistente.
> - **Implementazione:** nessuna fase F0-F6 iniziata. FS-A e FS-B sono autorizzate da Roberto
>   il 14/9 a partire subito.
> - **Fonti:**
>   - revisioni 1-4, con i rilievi avversariali del 10/9 e la review del 14/9, nella storia
>     Git fino al commit `7cc36aa9`;
>   - verifiche sul codice del 14/9, riportate al §2;
>   - esito dei rilievi della revisione 4 nell'appendice B;
>   - review adversarial e multidisciplinare della revisione 5 nell'appendice C.
> - **Baseline di approvazione:** va registrata fuori da questo file, in
>   `internal/reports/rm0009-baseline/`, dall'unità P0.

## Decisioni di Roberto (vincolanti)

| Data | Decisione |
|---|---|
| 3/9 | «L'approccio e l'obiettivo non è mettere pezze ma creare un sistema che cresce in modo intelligente e gestito. Il modo di rappresentarlo è indifferente. L'obiettivo no.» |
| 4/9 | La libertà di Metnos deve essere **modulata** (fase F6). |
| 14/9 | **Ambito globale.** Le modifiche governate da RM-0009 sono globali e disponibili a tutti gli utenti. `owner_id` e il componente proponente sono solo provenienza: nessun gate, nessun rollout per owner. |
| 14/9 | **Piano completo:** fasi F0-F6 e piano di attuazione (appendice A). |
| 14/9 | **Sicurezza subito:** FS-A e FS-B partono ora, separate da RM-0009. |
| 14/9 | **Obiettivo:** un sistema che aumenta le proprie capacità, anticipa le necessità, ottimizza le proprie prestazioni e realizza i fini di TELOS. |
| 14/9 | **Autonomia:** «l'utente deve intervenire solo in poche e semplici occasioni, il meno possibile». |
| 14/9 | **Criteri di progettazione:** KISS, utilità per l'utente, soluzioni universali e non ad hoc, niente hardcoding, efficienza. |

## 1. Obiettivo e valore per l'utente

**Metnos è un sistema che:**

1. **aumenta le proprie capacità.** Si accorge di ciò che non sa fare, lo propone
   e fa nascere la capacità sotto la porta unica di RM-0008; la prova, poi la
   promuove o la ritira;
2. **anticipa le necessità.** Propone ciò che servirà prima che una richiesta
   fallisca, partendo dagli schemi ricorrenti d'uso e dai fini di TELOS;
3. **ottimizza le proprie prestazioni.** Rende più veloce, economico e affidabile
   ciò che fa già;
4. **realizza i fini di TELOS.** Ogni proposta e ogni misura dichiarano il fine
   servito (`workspace/TELOS.md`): `t.tempo`, `t.ordine`, `t.puntualita`,
   `t.protezione`, `t.discrezione`, `t.parsimonia`.

**Autonomia.** Tutto avviene nel modo più autonomo possibile.
- **Decide Metnos** tutto ciò che è reversibile, misurabile e dentro le
  capacità già concesse.
- **L'utente interviene** solo quando un cambiamento allarga l'autorità, non è
  reversibile, costa o tocca dati sensibili. Riceve una domanda chiara, con
  risposta sì o no, raccolta nel riepilogo e mai come interruzione
  (`t.discrezione`).

**Ambito.** Ogni modifica attivata entra nello stesso catalogo e nello stesso
routing per tutti gli utenti.
- Una modifica non ancora sicura per tutti resta in prova e non viene attivata
  per il solo proponente.
- I controlli ordinari su dati, effetti e capability restano validi per ogni
  richiesta.

**Libertà modulata.** La libertà di un executor dipende da fatti firmati:
capacità e ambiti concessi, reversibilità, stato del ciclo di vita. Non dipende
dall'etichetta con cui la proposta è nata.

**Valore per l'utente**, espresso per fine TELOS:
- meno incombenze ripetitive (`t.tempo`);
- scadenze intercettate (`t.puntualita`);
- dati in ordine (`t.ordine`);
- risposte più rapide ed economiche (`t.parsimonia`);
- nessuna interruzione inutile (`t.discrezione`);
- nessuna esposizione di dati (`t.protezione`).

Oggi l'obiettivo non è misurato: la prima cosa da fare è renderlo osservabile (F0, §7).

## 2. Stato verificato del codice (14 settembre 2026)

Il ciclo esiste già: va ricongiunto, non costruito. Ogni anello è classificato
con una categoria sola:
- `connected`: produttore, consumatore ed effetto presenti;
- `unconsumed`: scritto, ma nessuno lo legge per cambiare comportamento;
- `unreachable`: nessun chiamante in produzione;
- `gated`: bloccato per progetto da un cancello con un proprietario;
- `alive_starved`: gira, ma non riceve input utile;
- `partial`: funziona solo in parte, come descritto nella nota.

| # | Anello | Dove | Stato | Nota |
|---|---|---|---|---|
| 1 | Il motore dichiara di non saper fare | `engine/dispatch.py` | `connected` | `capability_missing` significa «il piano ha perso un passo», non «manca lo strumento» |
| 2 | Il fallimento diventa lacuna | `engine/terminator.py` | `partial` | aggrega per chiave con `n_seen`; una classe sola per due cause |
| 3 | La lacuna diventa proposta | `learning_loop.py:34` | `alive_starved` | ammette solo `out_of_scope` e `wrong_tool`; `wrong_tool` non compare nei dati, `capability_missing` è escluso |
| 4 | La proposta riceve un punteggio | `change_intents.py:405` | difetto statico | una colonna, cinque formule, `max(vecchio, nuovo)`: può solo salire |
| 5 | Il giudizio sui fini TELOS | `alignment_engine.py`; `vaglio.judge` | `partial` / `unreachable` | `alignment_engine` giudica solo la famiglia `telos`; `vaglio.judge` non ha chiamanti e il suo prompt è scaduto |
| 6 | Una persona decide | `change_applier.py`, `/admin/changes` | `connected` | tutte le decisioni sono umane |
| 7 | La capacità nasce | porta RM-0008 | `gated` | 0 nascite concluse su 59 tentativi (storico, 3/9) |
| 8 | Prova in ombra | `executor_birth_shadow.py` | `partial` | produce un rapporto; non ha publisher, firma né writer del ciclo di vita |
| 9 | Il preesercizio decide | `executor_birth_preexercise.decide_preexercise` | `unreachable` + `gated` | nessun chiamante; cancello RM-0008/F5 |
| 10 | Promozione, scadenza, riepilogo | catena `promoter` | `alive_starved` | a vuoto dal 13/5; considera solo `promoted_grace` |
| 11 | Il rifiuto insegna | `change_applier.py:272` (`reject_pattern`) | `unconsumed` | nessuno legge `rejected_patterns.jsonl`; docstring e doc pubblica `architecture/lifecycle.html` affermano il contrario |

**Sorgenti di anticipazione e ottimizzazione già presenti, fuori dal ciclo:**
- ciclo introspettivo TELOS notturno (`telos_introspect.py`, dieci lenti: produce la famiglia `telos`);
- autopath in ombra dai turni ripetuti (`engine.autopath.seed_from_run`, conferma umana);
- ritiro delle guardie dormienti (`engine/guard_stats.dormant()`, verdetto umano);
- aging degli executor inutilizzati.

**Sicurezza, verificata:**
- `synth_request.py:72` esegue `test_runner.py` sull'host, senza isolamento;
- `sandbox.py:549` monta nell'executor, per la capability
  `metnos:credentials_metadata_only`, il vault **e `admin.key`**;
- i percorsi vietati del Vaglio non coprono la radice di configurazione Metnos.

**Numeri storici (3/9)**: vanno rigenerati da P0 prima di usarli.
- Lacune `out_of_scope`/`wrong_tool`/`capability_missing`: 13/0/4.
- 55 intent `proposed`: 43 `telos`, 4 `observation`, 3 `user`, 4 `introvertiva`,
  1 `synt`. Massimo `telos` 0,724.
- Nessuna proposta nata da un bisogno reale dal 19/7.
- Scostamento registro/turni: circa 5 volte. È un'ipotesi, non una misura.

## 3. Cause

- **C1 — Il segnale è solo reattivo, ed è staccato.** Le lacune non arrivano al
  generatore (anelli 2-3). Anticipazione e ottimizzazione vivono fuori dal ciclo.
- **C2 — La decisione non è calibrata ed è tutta umana.**
  - Il punteggio è monotono e mescola scale diverse (4).
  - Il giudice vede una sola famiglia (5).
  - Ogni proposta chiede una persona (6).
- **C3 — Il ritorno è interrotto e il ciclo è cieco.**
  - Il rifiuto non ha effetto (11) e il riepilogo segue un solo stato (10).
  - Turni reali e di collaudo sono indistinguibili; l'esito del turno esiste solo
    dal 16/8.

## 4. Principi di progettazione (norma)

1. **KISS.** Una sola pipeline. Nessuna struttura nuova se ne esiste una
   equivalente. Ogni fase fa il cambiamento minimo che produce il suo effetto.
2. **Utilità.** Ogni fase dichiara il beneficio per l'utente e il fine TELOS
   servito; il valore reale si misura (§7).
3. **Universale, non ad hoc.** Si riusano:
   - la firma d'intento del motore (`engine/autopath.py:298`,
     `_compute_intent_sig`) come identità del bisogno;
   - `alignment_engine` come unico giudice TELOS;
   - prefiltro e catalogo del motore per la ricerca negativa;
   - il riepilogo esistente (`jobs/promoter_digest.py`, `jobs/promoter_state.py`)
     per domande, resoconti e marcature di notifica;
   - il riepilogo notturno (`nightly_orchestrator.NIGHTLY_SEQUENCE`);
   - il punto unico di invocazione `agent_runtime.invoke_executor`, locale e
     remoto, e `executor_scheduler.invoke_scheduled` per il durevole;
   - la validità delle cache (ADR 0182);
   - il runner `executor_birth_runner`.
4. **Niente hardcoding.**
   - Soglie, finestre, scale e obiettivi stanno in un solo **registro di
     politica** versionato (§5.6).
   - Le enumerazioni chiuse (esito, origine, tipo di lacuna, esito di decisione)
     sono definite una volta come fonte tipizzata.
   - Classi d'errore tipizzate, mai tabelle di stringhe o prefissi.
   - Nessuna lista di nomi di attori, applicazioni o executor.
   - I testi per l'utente passano da i18n.
5. **Efficienza.**
   - Sul percorso di ogni turno e invocazione si calcola in memoria, senza
     scritture sincrone per invocazione.
   - Ogni tabella append-only ha una politica di conservazione e un'aggregazione
     notturna.
   - Gli indicatori li calcola il riepilogo notturno esistente.
6. **Autonomia.** Una sola regola deterministica stabilisce chi decide (§5.3).
7. **«Chi lo chiama?»** Ogni componente del ciclo ha produttore, consumatore e
   prova di effetto. Un test generico fallisce se un writer del ciclo non ha
   lettori o se una sua funzione pubblica non ha chiamanti.
8. **Onestà** (CLAUDE.md §2.8). Nessuna funzione mostrata all'utente dichiara
   un effetto che non produce.

## 5. Architettura: una pipeline, tre sorgenti

```text
S1 lacuna ─┐
S2 anticipazione ─┼─> intent globale ─> valutazione ─> decisione (regola di autonomia)
S3 ottimizzazione ┘        │                                   │
                           └──── provenienza, fine TELOS        ├─ auto ──> effetto + resoconto
                                                                ├─ ask_user ─> domanda sì/no nel riepilogo
                                                                └─ deny ──> esito motivato
effetto ─> catalogo globale + invalidazione cache ─> misura (indicatori §7) ─> ritorno automatico se peggiora
```

### 5.1 Sorgenti

Le sorgenti sono adattatori che producono lo stesso intent.
- **S1, lacuna:** `capability_absent` provata da F3, con ricorrenza di eventi
  unici pari almeno alla soglia di politica.
- **S2, anticipazione:**
  - schemi ricorrenti di successo: turni reali con la stessa firma d'intento
    ripetuti oltre la soglia → proposta di automazione (task ricorrente o piano
    autopath);
  - lenti TELOS notturne (`telos_introspect.py`).
- **S3, ottimizzazione:**
  - turno ripetuto costoso → piano autopath;
  - guardia dormiente → ritiro;
  - executor inutilizzato → aging;
  - cache con resa bassa → revisione.

Ogni intent porta:
- impronta globale (firma d'intento e tipo di cambiamento);
- sorgente;
- fine TELOS servito;
- identificativi degli eventi causali;
- provenienza: `proposer_component_id` e versione, `owner_id` dell'evento
  originario; i contributi successivi stanno in `change_intent_sources`.

La provenienza non entra mai in impronta, ranking, gate o autorità.

### 5.2 Valutazione

Un registro append-only `change_intent_evaluations`, un record per valutazione:
- intent;
- dimensione: `alignment`, `recurrence`, `rejections` o `benefit`;
- valore;
- valutatore, con identità e versione;
- evento sorgente, con vincolo di idempotenza;
- istante.

Regole:
- `alignment` lo produce solo `alignment_engine`, per **ogni** famiglia.
  `not_applicable` è un esito tipizzato con motivo; l'assenza della ricevuta
  non vale come `not_applicable`.
- `benefit` è il guadagno misurato (latenza, costo, successo) per S3.
- La proiezione corrente è l'ultima valutazione valida per (intent, dimensione).
- Una correzione scrive una nuova valutazione e non cancella la storia.
- La vecchia colonna `score` resta solo in lettura, come
  `legacy_score_max_v0`, e non alimenta decisioni.

### 5.3 Decisione: la regola di autonomia

Una funzione pura e deterministica, `decide_change(fatti) → auto | ask_user | deny`.
- **Fatti in ingresso:**
  - fatti firmati del manifest o del cambiamento: capability, ambiti,
    reversibilità, rete in uscita, credenziali, costo;
  - allineamento TELOS e sorgente;
  - stato del ciclo di vita.
- **Stessa funzione di F6.** La stessa logica, sugli stessi fatti, dà
  l'autorità d'invocazione.

| Cambiamento | Esito | Ritorno |
|---|---|---|
| Ottimizzazione dentro le capacità esistenti (autopath, cache, ritiro di guardia dormiente, aging) | `auto` | ritorno automatico se gli indicatori peggiorano oltre la soglia |
| Capacità nuova **reversibile**, senza rete in uscita né credenziali, con allineamento sopra la soglia, dopo il preesercizio riuscito | `auto` | undo per esecuzione; ritiro automatico se non viene usata |
| Allarga l'autorità (scrittura irreversibile, rete in uscita, credenziali, costo) oppure allineamento sotto la soglia | `ask_user` | il rifiuto diventa una regola globale |
| Regola globale che cambia il comportamento per tutti | `ask_user`, una volta | rollback della regola |
| Veto: ciclo di vita non attivo, attestazione mancante, identità o generazione discordante, record legacy ignoto | `deny` | — |

`ask_user` produce **una** domanda sì/no nel riepilogo. La domanda contiene il
fine TELOS e l'effetto in una riga, nella lingua dell'istanza. Le decisioni
`auto` compaiono nel riepilogo come resoconto, senza domanda.

### 5.4 Effetto

- L'applicazione aggiorna in modo atomico il catalogo globale e invalida le
  cache di routing di tutti gli utenti (ADR 0182).
- Produce una ricevuta dell'effetto, con prima e dopo.
- Un fallimento parziale lascia la modifica non attiva per tutti.
- **Il rifiuto** (`reject_pattern` ricollegato) è una regola globale strutturata
  sull'impronta dell'intent, con scadenza di politica e rollback.
  - Il consumatore la applica prima del generatore e del planner; il testo del
    rifiuto non entra nel prompt.
  - L'owner d'origine può proporre la regola; la attiva solo la decisione
    globale.
- **Ritorno automatico:** una modifica `auto` il cui indicatore di beneficio
  peggiora oltre la soglia torna indietro da sola e lo registra.

### 5.5 Consegna

- Si usa il riepilogo esistente (`jobs/promoter_digest.py` con
  `jobs/promoter_state.py`), esteso dal solo `promoted_grace` all'insieme
  chiuso. L'outbox di `durable_workloads` non si riusa: è legato ai workload
  durevoli.
- Richiedono l'utente solo gli intent `proposed` con decisione `ask_user`.
- La finestra di consegna è il riepilogo successivo.
- Mancato invio e decisione assente si misurano separatamente.

### 5.6 Registro di politica: valori iniziali

Gli agenti fissano i valori con prudenza; si cambiano senza toccare il codice.
Il registro è `runtime/growth_policy.py` (unità P1).

| Chiave | Valore iniziale |
|---|---|
| ricorrenza S1 | 3 eventi unici in 30 giorni (oggi `METNOS_PROPOSE_SEEN=3`) |
| ricorrenza S2 | 3 turni reali con la stessa firma d'intento in 14 giorni |
| allineamento minimo per `auto` | 0,45 (oggi `METNOS_TELOS_ACCEPT_HARD_GATE`) |
| soglia di riconciliazione F3 | al massimo 10% di eventi attesi mancanti |
| finestra di consegna | riepilogo successivo (24 h) |
| scadenza della regola di rifiuto | 180 giorni, rinnovabile |
| ritorno automatico | peggioramento oltre il 20% su 7 giorni |
| TTL delle ricevute remote e durevoli (F6) | 15 minuti |
| obiettivo di interventi dell'utente | al massimo 3 domande a settimana |
| conservazione degli eventi append-only | 180 giorni di dettaglio, poi aggregati |

## 6. Fasi

Ordine: `P0 → P1 → F0 → F1 → F2 → F3 → F4 → F5 → F6`.
- **FS-A e FS-B** partono subito, fuori sequenza.
  - FS-A precede qualunque esecuzione di codice candidato.
  - FS-B precede la dichiarazione di sicurezza di F6.
- **Enforcement di F6** solo dopo la certificazione di RM-0008/F5.
- **Ogni fase** produce un commit autonomo, test mirati e almeno un turno reale
  su `/agent/turn` nel dominio toccato (CLAUDE.md §8.5), eseguito dal
  coordinatore dopo il rilascio (appendice A.3).

**P0 — Baseline.**
- *Cambiamento:*
  - congelare commit, digest della release e istante;
  - rigenerare i numeri del §2 con query ripetibili, marcando ciascuno
    `reproduced`, `changed` o `not_reconstructable`;
  - salvare il rapporto e il manifest di evidenze in
    `internal/reports/rm0009-baseline/`.
- *Completata quando* un secondo agente riesegue e ottiene gli stessi risultati,
  timestamp esclusi.

**P1 — Registro di politica.**
- *Cambiamento:* un solo modulo, `runtime/growth_policy.py`, con i valori del
  §5.6, validazione, override da variabili d'ambiente e una versione stabile.
- *Completata quando* nessuna soglia del ciclo è scritta come letterale nel
  codice.

**F0 — Osservabilità** (per l'utente: sapere se Metnos serve davvero).
- *Cambiamento:*
  - `TurnLog` riceve tre campi:
    - `origin`: `user`, `test`, `system` o `unknown`;
    - `outcome`: `success`, `partial`, `error`, `needs_input` o `unknown`;
    - `intent_sig`: la firma d'intento del motore, usata da S2.
  - `origin` deriva dal tipo del principal autenticato al confine. Un utente di
    collaudo è registrato come tale. Mai dal nome dell'attore, mai dal payload
    del client.
  - `outcome` si calcola una volta sola in `TurnLog.write`.
  - I record storici restano `unknown`.
  - Un cambio di proprietario a metà turno viene rifiutato: serve un turno nuovo.
- *Riuso:* confini autenticati esistenti (HTTP, canali, scheduler, apply,
  resume); proiezione `/admin/turns`.
- *Completata quando:*
  - un E2E su `/agent/turn` con utente di collaudo risulta `test`, e un turno
    umano risulta `user`;
  - ogni nuovo turno chiuso ha il suo esito;
  - nessun client può auto-attestarsi;
  - gli indicatori 5 e 7 del §7 sono producibili a comando.

**F1 — Componenti del ciclo** (per l'utente: nessuna funzione finta).
- *Destino di ciascun componente:*
  - `vaglio.judge` → **ritirato**, insieme al ramo `llm-v1` e al prompt scaduto;
  - `reject_pattern` → **ricollegato** in F5; nel frattempo UI, docstring e
    doc pubblica dicono il vero;
  - `decide_preexercise` → `gated` (RM-0008/F5); fornisce fatti alla regola di
    autonomia;
  - `promoter` → alimentato da F4 e F5 tramite il riepilogo esistente;
  - `executor_birth_shadow` → rapporto, non transizione: correggere l'anello 8
    nella doc.
- *Test generico:* il test «writer senza lettore / funzione pubblica senza
  chiamanti» sui moduli del ciclo.
- *Backlog:* pulizia meccanica e reversibile dei 55 intent `proposed`
  (duplicati, bersagli che non esistono più), con motivo registrato. La scelta
  per politica avviene in F5.
- *Completata quando:*
  - nessun componente è in stato ambiguo;
  - la documentazione corrisponde al codice;
  - togliere un collegamento fa fallire la prova corretta;
  - il backlog è trattato con ricevute.

**F2 — Valutazioni** (per l'utente: proposte ordinate in modo corretto).
- *Cambiamento:*
  - il registro del §5.2;
  - `max()` tolto da ogni percorso decisionale;
  - la migrazione legacy come `legacy_unknown`, senza attribuire il vecchio
    massimo a una famiglia;
  - i campi di provenienza;
  - la conservazione.
- *Completata quando:*
  - una valutazione 0,8 corretta a 0,4 resta 0,4 e un'altra famiglia non la fa
    risalire;
  - due esecuzioni dello stesso evento non duplicano;
  - ogni famiglia ha una ricevuta `alignment` oppure `not_applicable` tipizzato.

**F3 — Lacune vere** (per l'utente: Metnos capisce che cosa gli manca).
- *Cambiamento:* un registro append-only `lacuna_events` con:
  - `event_id` idempotente, turno e passo;
  - `gap_kind`: `capability_absent`, `capability_unavailable` (esiste ma è
    vietata, non collocabile o temporaneamente indisponibile),
    `plan_step_missing`, `out_of_scope` o `unknown`;
  - bisogno = firma d'intento;
  - provenienza e versione del classificatore.
- *Regole:*
  - l'evento lo emette il punto che vede piano, catalogo e selezione;
  - `capability_absent` richiede la ricerca negativa con il prefiltro sul
    catalogo globale verificato;
  - l'aggregato globale per (bisogno, `gap_kind`) è una proiezione;
  - i record legacy restano `unknown` e non entrano in F4;
  - si riconcilia con i turni di F0.
- *Completata quando:*
  - lo scostamento è sotto la soglia;
  - i cinque tipi sono distinti;
  - un retry non aumenta la ricorrenza;
  - due owner con lo stesso bisogno convergono in un solo aggregato.

**F4 — Proposte da tre sorgenti** (per l'utente: capacità nuove, necessità
anticipate, prestazioni migliori).
- *Cambiamento:* adattatori S1, S2 e S3 verso lo stesso upsert; ogni intent
  porta il fine TELOS.
  - S1 accetta solo `capability_absent` sopra la soglia.
  - S2 usa gli schemi ricorrenti e `telos_introspect`.
  - S3 usa l'autopath in ombra, `guard_stats.dormant()` e l'aging.
- *Completata quando:*
  - un caso reale per ciascuna sorgente produce una e una sola proposta
    globale, legata alla sua causa;
  - un retry non duplica;
  - i tipi esclusi non producono proposte.

**F5 — Decisione, effetto e ritorno** (per l'utente: poche domande e un rifiuto
che resta).
- *Cambiamento:*
  - la regola di autonomia del §5.3 in produzione;
  - il riepilogo esistente (`promoter_digest`), con le sole domande `ask_user` e il resoconto
    delle decisioni `auto`;
  - `reject_pattern` come regola globale;
  - la ricevuta dell'effetto;
  - l'attivazione atomica per tutti;
  - il ritorno automatico.
- *Completata quando:*
  - su casi reali, una decisione `auto` e una `ask_user` hanno effetto
    osservabile;
  - un rifiuto impedisce la rigenerazione per tutti;
  - una modifica attivata è visibile e instradabile per due utenti diversi dal
    proponente, e l'isolamento dei dati resta intatto;
  - un peggioramento simulato provoca il ritorno automatico;
  - due riepiloghi consecutivi non duplicano;
  - l'indicatore 9 è misurato.

**F6 — Libertà modulata** (per l'utente: ciò che nasce da solo non può fare
tutto).
- *Cambiamento:* la stessa funzione del §5.3 diventa l'autorità d'invocazione.
  - Autorità effettiva = intersezione tipizzata di capability e ambiti concessi
    dal manifest firmato, ristretta dai fatti. Può solo restringere, mai
    concedere.
  - Si applica in un solo punto: `invoke_executor` (locale e remoto) e
    `invoke_scheduled` (durevole), dopo le trasformazioni della destinazione e
    subito prima dell'effetto.
  - Si calcola in memoria. Le ricevute monouso con TTL servono solo alle code
    remote e durevoli, e vengono revocate a ogni cambio di ciclo di vita,
    generazione o politica.
  - Prima si osserva in ombra (F6.1-F6.2); l'enforcement (F6.3-F6.4) arriva solo
    dopo RM-0008/F5, FS-A e FS-B.
- *Completata quando:*
  - due executor con fatti firmati diversi hanno autorità materialmente diverse
    su un caso reale;
  - una politica più restrittiva blocca l'azione prima di qualunque undo, coda o
    subprocess;
  - una revoca fra accodamento ed esecuzione è verificata.

**FS-A — Un solo runner di nascita** (subito).
- *Cambiamento:*
  - `executor_birth_runner.run_birth_phase` diventa l'unica API che esegue
    codice candidato;
  - i campi legacy (`setup`, `teardown`, `env`, pytest diretto) vengono tradotti
    in operazioni chiuse;
  - si toglie la chiamata di `synth_request.py` a `test_runner.py`, poi si
    ritira `test_runner.py`.
- *Completata quando* nessun chiamante produttivo resta e le prove negative
  passano: shell, rete, lettura della configurazione, fork sopravvissuto al
  timeout, symlink, isolamento assente.

**FS-B — Radici protette e broker dei segreti** (subito).
- *Cambiamento:*
  - un solo fornitore delle radici protette, derivate dalla configurazione
    effettiva: configurazione, vault, `admin.key`, autorità Birth, alias XDG;
  - via i mount di vault e `admin.key` dagli executor delle credenziali;
  - un broker nel core esegue le operazioni e restituisce solo metadati;
  - apertura no-follow, `openat` o equivalente;
  - il Vaglio resta difesa in profondità.
- *Completata quando* nessuna chiave radice compare in comando, mount, ambiente,
  argomenti o log di un executor, e le prove sulle forme di alias e sulla
  sostituzione del percorso passano.

## 7. Indicatori permanenti

Li produce il riepilogo notturno. Finestra mobile in UTC; `unknown` è sempre
conteggiato a parte e mai trattato come zero. Soglie e obiettivi stanno nel
registro di politica.

| # | Indicatore | Fonte | Finestra | Denominatore | Fase | Oggi |
|---|---|---|---|---|---|---|
| 1 | Proposte nate da un bisogno reale | intent S1 con causa | 30 g | — | F4 | 0 (storico) |
| 2 | Capacità nate e attive | ricevute di nascita e ciclo di vita | sempre | tentativi | RM-0008 | 0 su 59 (storico) |
| 3 | Capacità nate e poi usate | turni `origin=user` che le invocano | 30 g | capacità nate | F5 | non misurabile |
| 4 | Scostamento registro/turni | `lacuna_events` contro turni F0 | 30 g | eventi attesi | F3 | ipotesi di circa 5 volte |
| 5 | Turni per origine ed esito | `TurnLog` | 30 g | turni chiusi | F0 | non misurabile |
| 6 | Proposte anticipate accettate e usate | intent S2, decisione, uso | 30 g | intent S2 | F4-F5 | 0 |
| 7 | Prestazioni del turno reale | latenza e costo mediani, quota `success` | 30 g | turni `origin=user` | F0, S3 | da P0 |
| 8 | Valore per fine TELOS | intent ed effetti per `t.*` | 30 g | — | F4-F5 | non misurabile |
| 9 | Interventi richiesti all'utente | domande `ask_user`, tempo mediano di risposta | 7 g | — | F5 | da P0 |
| 10 | Executor con autorità ristretta | decisioni F6 | corrente | executor attivi | F6 | 0 |

**Regola di tensione.** Finché gli indicatori 1, 3 e 6 restano a zero, il tema è
aperto per definizione, qualunque cosa dicano le percentuali. L'indicatore 9
deve restare entro l'obiettivo di politica.

## 8. Prove obbligatorie

- **F0:**
  - collaudo contro uso reale sullo stesso percorso produttivo;
  - un client che prova ad auto-attestarsi;
  - record legacy letto come `unknown`;
  - tabella esaustiva degli esiti, compreso `partial`.
- **F2:** correzione verso il basso non risalita da un'altra famiglia; evento
  idempotente.
- **F3:** due owner con lo stesso bisogno; retry; i cinque tipi distinti; legacy
  non ammesso.
- **F4:** un caso reale per sorgente; retry; tipi esclusi rifiutati.
- **F5:**
  - una decisione `auto` e una `ask_user` con effetto;
  - rifiuto globale;
  - disponibilità per due utenti diversi dal proponente, con isolamento dei dati;
  - ritorno automatico;
  - riepilogo idempotente.
- **F6:**
  - ogni veto scatta prima di qualunque effetto;
  - revoca fra accodamento ed esecuzione;
  - una differenza di sola provenienza non cambia l'autorità.
- **FS-A e FS-B:** le prove negative del §6.
- **Ciclo:** il test generico «writer senza lettore» fallisce se un componente
  torna inerte.

## 9. Non-obiettivi

- **Il preesercizio produttivo**, cioè l'anello 9: appartiene a RM-0008/F5.
  RM-0009 ne consuma la ricevuta e non crea una seconda ammissione.
- **I quattro livelli scritti nell'identità firmata.** Quella via è chiusa:
  cambierebbe l'identità di ogni executor già nato. La libertà si modula sui
  fatti firmati del manifest.
- **La riprogettazione della catena `promoter`.** Si alimenta a monte.
- **`wrong_args`**, che è dominio della GUARD_PIPELINE.
- **`n_seen` come cancello** prima di F3.
- **Rollout, visibilità o varianti per owner.**
- **Pipeline separate per sorgente.**

## 10. Rischi e misure

| Rischio | Misura |
|---|---|
| La complessità cresce, come in RM-0008 il 14/9 | principio 4.1; revisione in un giro; nessuna struttura parallela |
| Avvelenamento del segnale con richieste ripetute | eventi unici e idempotenti; decisione globale; provenienza fuori dai gate |
| Crescita illimitata dei registri | conservazione e aggregazione notturna |
| Errori dell'autonomia | `auto` solo per il reversibile; ritorno automatico; resoconto nel riepilogo |
| Componenti che tornano inerti | test generico «chi lo chiama?» |
| Codice candidato non isolato, chiavi esposte | FS-A e FS-B prima di tutto |
| Latenza sul turno | calcolo in memoria; nessuna scrittura sincrona per invocazione |

## 11. Completamento e arresto

RM-0009 è completata quando:
1. per ciascuna sorgente S1, S2 e S3 un caso reale compie il giro evento →
   intent → valutazione → decisione → effetto → misura, con gli identificativi
   causali e il fine TELOS servito;
2. esistono almeno un rifiuto efficace, una restrizione applicata, un retry
   idempotente e un caso legacy che fallisce in modo sicuro;
3. gli indicatori 1, 3, 6 e 9 sono prodotti e l'indicatore 9 è entro l'obiettivo.

La nascita automatica e l'enforcement F6 dipendono da RM-0008/F5 e da FS-A.
Finché mancano, RM-0009 può arrivare a `implemented` per le fasi F0-F5 e per
F6 in ombra, dichiarando aperta questa dipendenza.

**Arresto.** F0 può correggere denominatori e gravità, ma non può dichiarare
artefatti i difetti statici già provati: il `max()` del punteggio e il writer
senza lettore.

## 12. Che cosa resta a Roberto

- **Ora:** nessuna decisione. Gli agenti devono chiudere i rilievi bloccanti
  dell'appendice C e produrre una revisione coerente.
- **Dopo la nuova review:** approvare con un solo sì/no la parte normativa, che
  porta la roadmap allo stato `ready`.
- **A regime**, rispondere alle poche domande sì/no nel riepilogo.

Tutto il resto lo fissano gli agenti con valori prudenti nel registro di
politica (§5.6): dettagli, soglie, destino dei componenti.

Con l'approvazione, prima del commit che marca `ready`, si eliminano da questo
file le appendici B e C, i riferimenti alle revisioni precedenti e ogni altra
storia editoriale. Resta soltanto la parte normativa approvata; la tracciabilità
tecnica resta nella storia Git.

## Appendice A — Istruzioni operative per agenti di medio livello

> **Per chi implementa.** Ogni unità è autosufficiente: obiettivo, file da
> leggere, passi, divieti, test e consegna. I numeri di riga sono indicativi
> (verificati il 14/9): ritrova sempre il simbolo con `rg -n`.

### A.1 Ruoli

- **Coordinatore** (l'agente scelto da Roberto):
  - assegna le unità nell'ordine dell'A.5;
  - rivede ogni consegna in un solo giro;
  - raccoglie più unità in un ciclo di rilascio (i rilasci sono pre-autorizzati);
  - esegue i turni reali dopo il riavvio governato;
  - esegue gli script sui dati del servizio;
  - aggiorna lo stato in testa alla roadmap.
- **Esecutore** (agente di medio livello): una sola unità alla volta, senza
  iniziare la successiva.

### A.2 Regole comuni a ogni unità

**Prima di iniziare**
1. Leggi `CLAUDE.md`, `CLAUDE.mutabile.md` e i §§1-6 di questa roadmap.
2. Registra `git rev-parse HEAD` e `git status --short`. Se un file dell'unità
   ha già modifiche non tue, fermati con `BLOCKED_BASELINE`.
3. Ritrova ogni simbolo citato con `rg -n "<simbolo>" runtime tests`. Se non
   esiste più o fa altro da quanto scritto, fermati con `BLOCKED_BASELINE` e
   descrivi la differenza.

**Durante il lavoro**
4. Scrivi prima il test che fallisce, poi il cambiamento minimo. Se tocchi un
   database, scrivi poi la migrazione idempotente: rieseguirla non cambia nulla.
5. Riusa ciò che l'unità indica. Crea soltanto i file nuovi elencati nell'unità.
6. Nessun letterale di soglia, finestra o obiettivo nel codice: si leggono da
   `runtime/growth_policy.py` (unità P1).
7. Ogni enumerazione nuova si definisce una volta, nel modulo indicato, e si
   importa altrove. Niente confronti con stringhe sparse.
8. I testi per l'utente passano solo da chiavi i18n (`messages.get`/`_msg`, seed
   IT ed EN); la diagnostica interna usa `LOG_*`.
9. Commenti e docstring del codice in inglese.
10. Nei test solo archivi temporanei (`tmp_path` e variabili `METNOS_USER_*`
    verso una cartella temporanea): mai leggere o cancellare gli archivi reali.

**Divieti assoluti**
- modificare chiavi, firme, ricevute, store dei contratti o dati di produzione;
- riavviare servizi;
- eseguire `prepare`, `apply` o altri rilasci;
- usare `git stash` senza nome;
- pubblicare su GitHub;
- aggiungere dipendenze;
- indebolire un test esistente: skip, xfail, `-k` usato per nascondere,
  timeout più lunghi.

**Test**
11. Esegui `/opt/metnos/.venv/bin/python -m pytest -q <test dell'unità>`, poi le
    suite dei moduli toccati (`rg -l "<modulo>" tests`). Riporta raccolti,
    passati, saltati e falliti.

**Consegna**
12. Un commit per unità, solo con i file dell'unità: messaggio in inglese,
    senza righe Co-Authored-By.
13. Una nota al coordinatore, nel messaggio e non in un file nuovo: commit, file,
    test con i numeri, rischi residui, rollback (`git revert <sha>` più il
    comando inverso della migrazione, se c'è).

**Stati di arresto.** In arresto non si improvvisa: si consegna il motivo.
- `BLOCKED_BASELINE`: codice o albero diversi dall'atteso.
- `BLOCKED_DECISION`: serve un valore che non sta né nel §5.6 né nell'unità.
- `BLOCKED_SAFETY`: servirebbe allargare permessi, esporre un segreto o
  eseguire codice candidato fuori dal runner Birth.

### A.3 Turno reale (solo il coordinatore, dopo il rilascio)

```bash
# richiesta
curl -s -m 600 -X POST http://127.0.0.1:8770/agent/turn \
  -H "Authorization: Bearer $(cat ~/.config/metnos/admin.key)" \
  -H 'Content-Type: application/json' --data '{"query": "<richiesta>"}'
# verifica del registro dei turni
curl -s -H "Authorization: Bearer $(cat ~/.config/metnos/admin.key)" \
  'http://127.0.0.1:8770/admin/turns?limit=5'
```

Ogni scheda dice che cosa osservare. Non si modifica la richiesta per farla
passare (CLAUDE.md §8.5). Gli script che leggono i dati del servizio (account
`metnos`) si eseguono con il runner amministrativo esistente
(`sudo -n /usr/local/sbin/metnos-agent-admin <script> <sha256> <modo>`), dopo la
revisione dello SHA.

### A.4 Schede delle unità

#### P0 — Baseline dei numeri
- **Obiettivo:** rendere ripetibili i numeri del §2, senza modificare nulla.
- **Leggi prima:**
  - `engine/terminator.py`: `_db_path`, tabella `lacune`;
  - `change_intents.py`: `count_by_state`, `list_intents`;
  - `config.PATH_TURNS`, dove stanno i turni;
  - `jobs/promoter_state.py`.
- **Crea:** `internal/tools/rm0009_baseline.py` (sola lettura, SQLite in `mode=ro`)
  e `tests/internal/test_rm0009_evidence.py`.
- **Passi:**
  1. Lo script accetta `--out <dir>` e scrive `report.md` e `manifest.json`:
     commit, istante UTC, query esatte, risultati.
  2. Calcola:
     - lacune per `error_class`, come righe e come somma di `n_seen`;
     - intent per stato e per famiglia;
     - turni per `final_kind` e per presenza di `error_class`;
     - stati del promoter.
  3. Per ogni numero storico del §2 scrive `reproduced`, `changed` (con il
     nuovo valore) o `not_reconstructable` (con il motivo).
  4. Nessuna scrittura; un archivio non leggibile vale `not_reconstructable`.
- **Test:** su archivi di prova creati dal test, due esecuzioni danno numeri
  identici e i digest dei file restano gli stessi.
- **Coordinatore:** esegue lo script sui dati del servizio (A.3) e salva
  l'uscita in `internal/reports/rm0009-baseline/<AAAAMMGG>/`.

#### P1 — Registro di politica
- **Obiettivo:** un solo posto per soglie, finestre e obiettivi (§5.6).
- **Crea:** `runtime/growth_policy.py` e `tests/runtime/learning/test_growth_policy.py`.
- **Passi:**
  1. `@dataclass(frozen=True) class GrowthPolicy`: un campo per ogni riga del §5.6.
  2. `load_growth_policy() -> GrowthPolicy`:
     - i default sono quelli del §5.6;
     - l'override passa dalle variabili `METNOS_GROWTH_<NOME>`;
     - le due manopole esistenti tengono il nome attuale (`METNOS_PROPOSE_SEEN`,
       `METNOS_TELOS_ACCEPT_HARD_GATE`), senza duplicarle.
  3. Ogni valore viene validato per tipo e intervallo. Un valore non valido
     produce un errore tipizzato all'avvio, mai un ripiego silenzioso.
  4. `version` è un hash stabile dei valori effettivi, citato dalle ricevute.
  5. `learning_loop.py` legge la ricorrenza da qui invece che da `PROPOSE_SEEN`
     (riga ~29).
- **Test:** default; override valido; override non valido rifiutato; `version`
  cambia se cambia un valore.

#### F0.1 — Origine, esito e firma del turno
- **Obiettivo:** ogni turno dice chi l'ha chiesto, com'è andato e che cosa chiedeva.
- **Leggi prima:**
  - `agent_runtime.py`: `class TurnLog` (~4109; `final_kind`, `error_class` ~4185),
    `TurnLog._enforce_mutating_honesty` (forma dei passi falliti),
    `TurnLog.write` (~5173; `effect_counts`);
  - `engine/autopath.py`: `_compute_intent_sig` (~298).
- **Crea:** `runtime/turn_outcome.py` e `tests/runtime/infra/test_turn_provenance.py`.
- **Passi:**
  1. In `turn_outcome.py` definisci due enumerazioni:
     - `TurnOrigin`: `user`, `test`, `system`, `unknown`;
     - `TurnOutcome`: `success`, `partial`, `error`, `needs_input`, `unknown`.
  2. Funzione pura `derive_outcome(final_kind, error_class, steps) -> TurnOutcome`:
     - `final_kind == "ask"` → `needs_input`;
     - `final_kind == "error"`, oppure `"answer"` con `error_class` non vuoto → `error`;
     - `"answer"` con almeno un passo fallito (risultato con `ok` falso o
       `error_class`) → `partial`;
     - `"answer"` senza fallimenti → `success`;
     - qualunque altro valore → `unknown`.

     Oggi i valori di `final_kind` sono `answer`, `error` e `ask`: se ne trovi
     altri, fermati con `BLOCKED_DECISION`.
  3. Aggiungi a `TurnLog` tre campi, con i default `"unknown"`, `"unknown"` e `""`:
     `origin`, `outcome`, `intent_sig`. `intent_sig` è `_compute_intent_sig(intent)`
     unito in una stringa, quando il turno ha un intento.
  4. In `write()`, `outcome` si calcola una sola volta e va nel record persistito.
  5. I record storici non si toccano: chi li legge tratta il campo assente come
     `unknown`.
- **Test:** la tabella completa di `derive_outcome` (ogni riga più un valore
  sconosciuto); un record senza i campi letto come `unknown`.

#### F0.2 — Origine dal confine autenticato
- **Leggi prima:**
  - `run_turn` (`agent_runtime.py` ~7502: `actor`, `channel`, `owner_user_id`);
  - i chiamanti (`rg -n "run_turn\(" runtime`): `http_routes_agent.py`,
    `channels/daemon.py`, `recurring_tasks.py`, `change_applier.py`,
    `agent_server.py`, `orchestration.py`, `smoke.py`, `testing/`;
  - `users.py` (`ROLES`, riga ~46) e `http_auth.py` (ruolo dal Bearer).
- **Passi:**
  1. `run_turn` riceve il parametro obbligatorio `origin: TurnOrigin`, senza
     default, e lo salva nel `TurnLog`.
  2. Aggiungi `"test"` a `users.ROLES`, con le stesse autorizzazioni di `guest`.
     Verifica ogni confronto sul ruolo (`rg -n 'role ==|role !=' runtime`).
  3. Ogni chiamante calcola `origin` dall'identità già autenticata. Mai dal nome
     dell'attore, mai da un campo della richiesta.
     - principal con ruolo `test` → `test`;
     - turno avviato da scheduler, task ricorrenti o `change_applier` → `system`;
     - principal `host` o `guest` → `user`;
     - identità incerta → `unknown`.
  4. Un campo `origin` nel corpo della richiesta HTTP viene ignorato.
  5. Il ripristino di un turno sospeso conserva l'`origin` del turno originale.
  6. Aggiorna i finti `run_turn` usati nei test.
- **Test:** `tests/runtime/http/test_http_turn_ownership.py` (esistente) più tre casi:
  - Bearer di un principal `test` → `origin=test`;
  - un corpo con `origin: "user"` viene ignorato;
  - un turno dallo scheduler → `system`.
- **Coordinatore:** crea un principal di collaudo, con ruolo `test`, tramite
  l'abbinamento dispositivi esistente; ne usa il token per i turni E2E.

#### F0.3 — Vista amministrativa e indicatori 5 e 7
- **Leggi prima:** `http_routes_admin.py` (`admin_turns` ~1040),
  `nightly_orchestrator.py` (`NIGHTLY_SEQUENCE` ~37), `config.PATH_TURNS`,
  `config.PATH_COST`.
- **Passi:**
  1. `/admin/turns` mostra `origin` e `outcome`, sia nella vista HTML sia nel JSON.
  2. Una voce del riepilogo notturno calcola sugli ultimi 30 giorni:
     - la quota di turni per `origin` e per `outcome`;
     - la latenza mediana (`ts_end - ts_start`) dei turni `origin=user`;
     - il costo, se `PATH_COST` lo registra per turno, altrimenti `not_observed`.

     `unknown` è sempre riportato a parte.
- **Test:** HTTP della vista; la funzione dell'indicatore su file di turni di prova.
- **Turno reale:**
  - una richiesta qualunque (A.3) risulta `origin=user` con il suo `outcome`;
  - la stessa richiesta col token di collaudo risulta `origin=test`.

#### F1.1 — Test generico «chi lo chiama?»
- **Crea:** `tests/runtime/learning/test_growth_cycle_callers.py`.
- **Passi:**
  1. Il test dichiara l'elenco dei moduli del ciclo:
     - `learning_loop`, `change_intents`, `change_applier`, `change_rollback`;
     - `engine/terminator`, `jobs/promoter*`;
     - `telos_introspect`, `alignment_engine`;
     - `growth_policy` e i moduli nuovi di RM-0009.
  2. Con `ast`, per ogni funzione pubblica verifica che esista almeno un
     chiamante in `runtime/`, test esclusi.
  3. Per ogni file o tabella SQLite scritti da un modulo del ciclo verifica che
     esista almeno un lettore in `runtime/`.
  4. Un'eccezione è ammessa solo con un motivo scritto e la fase che la
     rimuoverà, per esempio «gated da RM-0008/F5».
- Oggi il test deve trovare `reject_pattern` (writer senza lettore) e
  `vaglio.judge` (senza chiamanti). Registrali come eccezioni motivate:
  le rimuovono F1.2 e F5.3.

#### F1.2 — Ritiro di `vaglio.judge` e documentazione vera
- **Leggi prima:**
  - `vaglio.py` (`JUDGE_KIND` ~46, ramo `llm-v1`), `prompts/{it,en}/vaglio.j2`;
  - `engine/executor.py` (`vaglio_judge` ~2050), `engine/dispatch.py` (~7086, ~7162);
  - `testing/populate_cases.py` (~433-452);
  - `change_applier.py:272`, `docs/{it,en}/architecture/lifecycle.html`,
    `executor_birth_shadow.py`.
- **Passi:**
  1. Rimuovi il ramo `llm-v1` di `vaglio.judge`, il prompt `vaglio.j2` (se
     `rg` non gli trova altri usi) e il parametro `vaglio_judge` con i suoi
     passaggi nel motore.
  2. Correggi la docstring di `change_applier.py:272` perché descriva ciò che fa
     davvero.
  3. Correggi `lifecycle.html`, in italiano e in inglese:
     - `reject_pattern` oggi non ha effetto e verrà ricollegato (F5.3);
     - l'anello 8 è un rapporto, non una transizione.
  4. Togli l'eccezione corrispondente da F1.1.
- **Test:** le suite del Vaglio e del motore toccate; F1.1 verde.
- **Coordinatore:** pubblica la doc con `./deploy.sh` (CLAUDE.md §9.2).

#### F1.3 — Pulizia meccanica del backlog
- **Leggi prima:** `change_intents.py` (`list_intents`, `get_by_fingerprint`,
  `_transition` ~544, `ALL_STATES` ~111, transizioni ammesse).
- **Crea:** `internal/tools/rm0009_backlog_cleanup.py`, che per default mostra
  soltanto (`--apply` per eseguire).
- **Passi:**
  1. Seleziona gli intent `proposed` con impronta duplicata (resta il più
     recente) o il cui bersaglio non esiste più nel catalogo.
  2. Per ciascuno applica la transizione ammessa verso uno stato terminale, con
     `decision_by="rm0009-cleanup"` e il motivo. Se nessuna transizione è
     ammessa, fermati con `BLOCKED_DECISION`.
  3. Il rapporto elenca gli id, per poterli ripristinare.
- **Test:** su un database di prova: dry-run senza modifiche; `--apply`
  idempotente.
- **Coordinatore:** esegue `--apply` sui dati del servizio (A.3).

#### F2.1 — Registro delle valutazioni e provenienza
- **Leggi prima:** `change_intents.py` (`ChangeIntent` ~150, `_conn` ~294,
  `init_db` ~304, `upsert_intent` ~356), `change_intent_adapters/_base.py`.
- **Passi**, in `init_db()` e in modo idempotente:
  1. Crea la tabella `change_intent_evaluations`:
     - colonne: `id`, `intent_id`, `dimension`, `value`, `status`, `reason`,
       `evaluator_id`, `evaluator_version`, `source_event_id`, `created_at`;
     - `dimension` accetta `alignment`, `recurrence`, `rejections`, `benefit`;
     - `status` accetta `ok` o `not_applicable`; `value` può essere nullo solo con
       `not_applicable`;
     - vincolo `UNIQUE(intent_id, dimension, evaluator_id, source_event_id)`.
  2. Crea `change_intent_sources`, con `UNIQUE(intent_id, source_event_id)`. Colonne:
     - `intent_id`, `source_event_id`;
     - `source_component_id`, `source_component_version`;
     - `owner_id`, `created_at`.
  3. Aggiungi a `change_intents`, solo se mancano:
     - `proposer_component_id`, `proposer_component_version`;
     - `origin_owner_id`;
     - `source_kind` (`gap`, `anticipation` o `optimization`);
     - `telos_goal`.

     Per i record storici valgono `unknown`.
  4. Le enumerazioni `EvaluationDimension`, `EvaluationStatus` e `SourceKind`
     si definiscono qui.
  5. La colonna `score` resta com'è, solo in lettura: nessuna migrazione la
     trasforma in valutazione.
- **Test:** `tests/runtime/learning/test_change_intent_evaluations.py`:
  - creazione idempotente;
  - vincolo di unicità;
  - dimensione o stato non validi rifiutati.

#### F2.2 — Scrittori e proiezione
- **Passi:**
  1. `append_evaluation(...) -> bool`, che restituisce `False` se l'evento è
     già registrato.
  2. `current_evaluations(intent_id) -> dict`: l'ultima valutazione per dimensione.
  3. In `upsert_intent` togli `max(existing["score"], ci.score)` (~405).
     - Gli adattatori chiamano `append_evaluation` con la propria dimensione:
       `observation` e `introvertiva` scrivono `recurrence`, `user_feedback`
       scrive `rejections`.
     - `alignment` arriva solo da F2.3.
  4. Ogni ordinamento o soglia che legge `score` passa a `current_evaluations`
     (cerca con `rg -n "score" runtime/change_intents.py runtime/change_intent_adapters runtime/http_routes_admin.py runtime/jobs`).
     Dove resta visibile, `score` è etichettato come storico.
  5. Conservazione: il riepilogo notturno elimina le valutazioni superate e più
     vecchie della conservazione di politica. Resta sempre l'ultima per
     (intent, dimensione, valutatore).
- **Test:**
  - una valutazione 0,8 corretta a 0,4 resta 0,4;
  - un'altra dimensione non la cambia;
  - lo stesso evento due volte produce una sola riga.

#### F2.3 — Giudizio TELOS per ogni famiglia
- **Leggi prima:** `alignment_engine.py`, `change_intent_adapters/telos.py`,
  `telos_loader.py`.
- **Passi:**
  1. `judge_intent(intent)` chiama `alignment_engine` su qualunque famiglia e
     scrive `alignment` con `evaluator_id="alignment_engine"` e la sua versione.
     Se il giudizio non è applicabile, scrive `not_applicable` con il motivo.
  2. Imposta `telos_goal` sul fine con il peso maggiore restituito dal giudice.
  3. Si chiama una volta per ogni intent nuovo. Per il backlog la chiama il
     riepilogo notturno, a lotti limitati dalla politica.
- **Test:** parametrico su `telos`, `introvertiva`, `synt`, `user`, `observation`:
  ognuna riceve `alignment` oppure `not_applicable` motivato.

#### F3.1 — Eventi di lacuna
- **Leggi prima:** `engine/terminator.py` (`_ensure_db` ~51, tabella `lacune` ~58,
  `_record_lacuna` ~76), `engine/autopath.py` (`_compute_intent_sig`).
- **Passi:**
  1. In `_ensure_db` crea `lacuna_events`:
     - colonne: `event_id` (chiave primaria), `turn_id`, `step`, `created_at`,
       `gap_kind`, `need_sig`, `owner_id`, `source_component_id`,
       `classifier_version`, `lacuna_id`;
     - `gap_kind` accetta i cinque tipi del §6/F3.
  2. `event_id` è lo sha256 di (`turn_id`, `step`, `gap_kind`, `need_sig`): un
     retry dello stesso fatto non aggiunge righe.
  3. `need_sig` è la firma d'intento, unita in una stringa.
  4. `_record_lacuna` scrive l'evento quando c'è un `turn_id`, e continua ad
     aggiornare `lacune` come oggi, come vista storica.
  5. L'enumerazione `GapKind` si definisce qui.
- **Test:** `tests/runtime/engine/test_lacuna_events.py`:
  - un retry produce una sola riga;
  - due owner con lo stesso bisogno producono due eventi con la stessa `need_sig`.

#### F3.2 — Classificazione dove c'è la prova
- **Leggi prima:**
  - `engine/dispatch.py`: `_dropped_required_verbs` (~2129) e le chiamate al
    terminator;
  - `prefilter.py`: `implements_intent_verb` (~212);
  - le classi d'errore `placement`, `permission_denied` e
    `capability_unavailable` in `agent_runtime.py`.
- **Passi:**
  1. `plan_step_missing` quando `_dropped_required_verbs` non è vuoto.
  2. `capability_absent` solo se nessun executor del catalogo verificato del
     turno implementa verbo e oggetto dell'intento (`implements_intent_verb`).
  3. `capability_unavailable` se l'executor esiste ma è negato, non collocabile
     o non disponibile: le tre classi d'errore stanno in una sola tabella di
     corrispondenza, nel modulo di F3.1.
  4. `out_of_scope` è la classe esistente.
  5. Ogni altro caso vale `unknown`.
  6. Il tipo si decide nel dispatch e arriva al terminator, che non lo ricalcola
     dal testo.
- **Test:** un caso per tipo; un executor esistente ma negato non diventa mai
  `capability_absent`.

#### F3.3 — Riconciliazione (indicatore 4)
- **Passi:** una voce del riepilogo notturno confronta, sugli ultimi 30 giorni,
  i turni `origin=user` con esito `error` o `partial` e i loro `lacuna_events`.
  Riporta mancanti, duplicati e orfani, e lo scostamento rispetto alla soglia
  di politica.
- **Test:** dati di prova con un mancante, un duplicato e un orfano.

#### F4.1 — Sorgente S1: lacune
- **Leggi prima:** `learning_loop.py` (`_CAPABILITY_GAP_CLASSES` ~34,
  `propose_from_lacuna` ~37).
- **Passi:**
  1. Sostituisci il filtro per classi d'errore. Innesca solo un aggregato di
     `lacuna_events` per `need_sig`, con `gap_kind = capability_absent` e un
     numero di `event_id` distinti nella finestra pari almeno alla soglia di
     politica.
  2. L'intent porta:
     - `source_kind="gap"`;
     - gli eventi in `change_intent_sources`;
     - `proposer_component_id="learning_loop"`.
  3. Nessun intent nasce da altri tipi o da righe storiche.
- **Test:** `tests/runtime/learning/test_learning_loop.py` aggiornato:
  - soglia meno uno e soglia esatta;
  - tipi esclusi;
  - retry.

#### F4.2 — Sorgente S2: anticipazione
- **Leggi prima:** `telos_introspect.py` (`_build_user_patterns` ~151,
  `run_all_telos` ~425), `change_intent_adapters/telos.py`.
- **Crea:** `change_intent_adapters/anticipation.py`.
- **Passi:**
  1. Conta le ripetizioni della stessa `intent_sig` nei turni `origin=user` con
     esito `success`, nella finestra di politica. Sopra la soglia nasce un intent
     `KIND_MATERIALIZE_PIPELINE` (automazione) con `source_kind="anticipation"`.
  2. Le proposte delle lenti TELOS esistenti ricevono
     `source_kind="anticipation"` e `telos_goal`.
  3. Nessuna notifica immediata (`t.discrezione`): tutto passa dal riepilogo.
- **Test:**
  - tre turni uguali producono un solo intent;
  - i turni `test` sono ignorati.

#### F4.3 — Sorgente S3: ottimizzazione
- **Leggi prima:** `engine/autopath.py` (`seed_from_run` ~699),
  `engine/guard_stats.py` (`dormant` ~163), `executor_aging.py` (`all_stats`
  ~297, `lifecycle_override_map` ~307).
- **Crea:** `change_intent_adapters/optimization.py`.
- **Passi:**
  1. I semi autopath in ombra diventano intent `KIND_CACHE_PATTERN`, con
     `source_kind="optimization"` e `telos_goal="t.parsimonia"`.
  2. Guardie dormienti ed executor inutilizzati diventano un tipo nuovo:
     - aggiungi `KIND_RETIRE_COMPONENT` ad `ALL_KINDS`;
     - il corpo è `{component_type, name}`, con `component_type` preso da
       un'enumerazione chiusa: `guard` o `executor`.
  3. Ogni intent registra `benefit` con la stima misurata dai dati esistenti
     (latenza o costo evitati); se non è misurabile, `not_applicable`.
- **Test:** un caso per tipo; un retry non duplica.

#### F5.1 — Regola di autonomia
- **Crea:** `runtime/growth_decision.py` e `tests/runtime/learning/test_growth_decision.py`.
- **Passi:**
  1. L'enumerazione `ChangeDecision` vale `auto`, `ask_user` o `deny`.
  2. `decide_change(facts, policy) -> (ChangeDecision, ReasonCode)` è una
     funzione pura, senza I/O.
  3. `ChangeFacts` è una dataclass immutabile con:
     - tipo e `source_kind` dell'intent;
     - allineamento;
     - capability dichiarate dal manifest del candidato;
     - reversibilità (`[undo]` o `reverse_pattern`);
     - rete in uscita, credenziali, costo;
     - stato del ciclo di vita e flag `legacy`.
  4. Applica la tabella del §5.3 in quest'ordine:
     1. veto → `deny`;
     2. autorità allargata o allineamento sotto la soglia → `ask_user`;
     3. ottimizzazione dentro le capacità esistenti → `auto`;
     4. capacità nuova reversibile, senza rete né credenziali, con preesercizio
        riuscito → `auto`;
     5. tutto il resto → `ask_user`.
  5. `ReasonCode` è un'enumerazione chiusa, non testo libero.
- **Test:**
  - una prova per ogni riga della tabella e per ogni veto;
  - con fatti mancanti l'esito è `ask_user`, mai `auto`.

#### F5.2 — Applicazione e riepilogo
- **Leggi prima:**
  - `change_intents.py`: `apply_decision` ~583, `_transition` ~544;
  - `jobs/promoter_digest.py`: ogni giorno alle 07:00, oggi solo `promoted_grace`;
  - `jobs/promoter_state.py`: `pending_notification`, `mark_notified`;
  - `change_applier.py`.
- **Passi:**
  1. Il riepilogo notturno calcola `decide_change` per ogni intent `proposed`
     con `alignment` presente, e registra `decision_by="policy:<version>"` e
     `decision_reason=<codice>`.
     - `auto` → `apply_decision` di accettazione, poi applicazione con
       `change_applier`;
     - `deny` → rifiuto motivato;
     - `ask_user` → l'intent resta `proposed` ed entra fra le domande.
  2. `promoter_digest` estende la selezione:
     - le domande `ask_user`, una riga ciascuna: fine TELOS, effetto, sì/no;
     - il resoconto delle decisioni `auto` delle ultime 24 ore;
     - `pending_notification` e `mark_notified` evitano le ripetizioni.
  3. La risposta sì/no torna dal canale già usato dal riepilogo e chiama
     `apply_decision`.
  4. Tutti i testi passano da i18n.
- **Test:** `tests/runtime/learning/test_promoter_digest.py` (esistente) più:
  - due esecuzioni non duplicano;
  - `auto` compare solo nel resoconto;
  - `ask_user` compare come domanda.

#### F5.3 — Il rifiuto che resta
- **Leggi prima:** `change_applier.py` (`reject_pattern` ~272,
  `rejected_patterns.jsonl`), `change_rollback.py` (~241), `KIND_REJECT_PATTERN`.
- **Passi:**
  1. Crea la tabella `rejected_fingerprints`, nel database degli intent:
     - colonne: `fingerprint` (chiave primaria), `reason`, `decided_by`,
       `created_at`, `expires_at`;
     - un rifiuto dell'utente, o un `deny`, scrive l'impronta con la scadenza
       di politica.
  2. `upsert_intent` non ricrea un intent con impronta rifiutata e non scaduta:
     restituisce l'id esistente e registra una valutazione `rejections`.
  3. Migra le righe di `rejected_patterns.jsonl`, poi rimuovi il vecchio writer
     e il suo ramo di rollback: il rollback cancella la riga della tabella.
  4. Togli l'eccezione da F1.1.
- **Test:**
  - un rifiuto impedisce la rigenerazione;
  - dopo la scadenza l'intent torna proponibile;
  - dopo un rollback torna proponibile.

#### F5.4 — Effetto e ritorno automatico
- **Leggi prima:** `change_intents.py` (`mark_applied` ~608, `mark_observed` ~615,
  `mark_rolled_back` ~628), `change_rollback.py`, ADR 0182.
- **Passi:**
  1. Dopo l'applicazione, `mark_applied` registra la ricevuta
     `{before, after, scope: "global_all_users"}`.
  2. Le capacità nuove passano dalla porta RM-0008 (Birth). Qui non si attivano:
     RM-0009 registra solo la richiesta e l'esito.
  3. Ritorno automatico: dopo 7 giorni il riepilogo confronta `benefit` misurato
     e atteso. Oltre la soglia di peggioramento chiama il rollback esistente e
     `mark_rolled_back` con il motivo.
  4. Le cache si invalidano da sole, tramite `tools_sig` e `pool_sig` (ADR 0182):
     niente invalidazioni manuali.
- **Test:** un peggioramento simulato provoca il rollback; un miglioramento non
  provoca nulla.
- **Turno reale:** approvare una domanda dal riepilogo reale e trovare l'effetto
  nel resoconto successivo.

#### F6.1 — Funzione dell'autorità d'invocazione
- **Crea:** `runtime/invocation_authority.py` e `tests/runtime/safety/test_invocation_authority.py`.
- **Passi:**
  1. `decide_invocation(facts)` è pura e restituisce `InvocationDecision`:
     `allowed_capabilities`, `veto`, `reason`, `policy_version`.
  2. L'autorità è l'intersezione fra le capability concesse dal manifest
     firmato e quelle consentite dai fatti (ciclo di vita, ricevuta, approvazione).
     Non aggiunge mai capability.
  3. I veto sono: ciclo di vita non `active`, attestazione mancante, identità
     o generazione discordante, legacy ignoto, approvazione richiesta ma assente.
  4. Riusa fatti ed enumerazioni di `growth_decision.py`, senza duplicarli.
- **Test:**
  - ogni veto;
  - due politiche con esito materialmente diverso sugli stessi argomenti;
  - nessuna chiamata a subprocess, rete o code.

#### F6.2 — Osservazione in ombra
- **Leggi prima:**
  - `agent_runtime.invoke_executor` (~3817: «universal scheduled choke-point
    for local and remote executors»);
  - `_invoke_executor_impl` (~3456);
  - `executor_scheduler.invoke_scheduled` (~933);
  - `durable_workloads/execution.py`.
- **Passi:**
  1. In `_invoke_executor_impl`, dopo l'iniezione degli argomenti e la
     risoluzione dei percorsi e prima di undo o subprocess: chiama
     `decide_invocation` e conta l'esito in memoria, per executor e motivo,
     senza cambiare il comportamento.
  2. Fai lo stesso nel ramo remoto, prima dell'accodamento, e nel durevole,
     prima dell'esecuzione.
  3. Il riepilogo notturno legge i contatori e riporta l'indicatore 10 e le
     divergenze.
  4. Nessuna scrittura su disco per invocazione.
- **Test:**
  - l'esito non cambia;
  - i contatori si aggiornano;
  - ogni percorso produce una sola decisione in ombra.

#### F6.3 e F6.4 — Enforcement
- **Precondizione dura:** la ricevuta di certificazione RM-0008/F5 esiste e
  FS-A e FS-B sono accettate; altrimenti `BLOCKED_DECISION`.
- **Passi:**
  1. Il veto nega prima di undo, accodamento, subprocess, mount o apertura di file.
  2. Per remoto e durevole, una ricevuta monouso con il TTL di politica:
     - è legata a executor, generazione, digest degli argomenti, destinazione
       e politica;
     - il consumatore la riverifica subito prima dell'effetto;
     - un cambio di ciclo di vita, generazione o politica la revoca.
  3. Il ramo durevole condizionato da `require_generation_attestation` lo
     attiva il proprietario RM-0008, non questa unità.
- **Test:** `tests/runtime/durable_workloads/test_execution_bridge.py` e
  `tests/runtime/remote/test_invocation_scope.py` (esistenti), più:
  - revoca fra accodamento ed esecuzione;
  - ricevuta riusata rifiutata;
  - zero effetti a ogni diniego.

#### FS-A — Un solo runner di nascita (subito)
- **Leggi prima:**
  - `synth_request.py`: `_validate_birth_tests` ~60, chiamata a `test_runner.py` ~72;
  - `test_runner.py`;
  - `executor_birth_runner.py`: `run_birth_phase` ~427;
  - `executor_birth_identity.py`: grammatica dei test ~275, con `setup` e `teardown`.
- **Passi:**
  1. Inventario: `rg -n "test_runner" runtime` e i manifest che usano `setup`,
     `teardown` o `env` nei test.
  2. `_validate_birth_tests` usa `executor_birth_runner.run_birth_phase`
     invece di lanciare `test_runner.py`.
  3. `setup`, `teardown` ed `env` diventano operazioni chiuse del runner, oppure
     vengono rifiutati con un errore tipizzato. Mai passati a una shell.
  4. Tolta l'ultima chiamata, rimuovi `test_runner.py` e togli i campi dalla
     grammatica.
  5. Firme e identità esistenti non si toccano a mano: i manifest da migrare
     passano dal ciclo di pubblicazione normale (coordinatore).
- **Test:** `tests/runtime/infra/test_executor_birth_runner.py` (esistente) più i
  casi negativi:
  - shell nei campi legacy;
  - rete;
  - lettura della configurazione;
  - figlio sopravvissuto al timeout;
  - symlink;
  - isolamento non disponibile → `test_environment_unavailable`.

#### FS-B — Radici protette e broker delle credenziali (subito)
- **Leggi prima:**
  - `sandbox.py`: ramo `metnos:credentials_metadata_only` ~549;
  - `credentials.py`: `store`, `load`, `list_domains`, `remove`, `fingerprint`,
    `assert_no_secrets_in_return`;
  - gli executor `find_credentials`, `set_credentials`, `delete_credentials`;
  - `vaglio.py`: `_FORBIDDEN_PATH_PATTERNS` ~51;
  - `config.PATH_USER_CONFIG` ~123;
  - i builtin in-process: `BUILTIN_INPROC_SPECS`,
    `scripts/generate_builtin_executor_contracts.py`.
- **Passi:**
  1. I tre executor delle credenziali diventano builtin in-process, con lo
     stesso meccanismo di `set_preferences`. Chiamano `credentials.py` nel core
     e restituiscono solo metadati (`assert_no_secrets_in_return`).
  2. Togli dal ramo di `sandbox.py` i mount del vault e di `admin.key`; se
     nessun executor usa più la capability, rimuovi il ramo.
  3. Un solo fornitore delle radici protette, derivate da `config`:
     configurazione, vault, `admin.key`, autorità Birth. Il Vaglio lo usa al posto
     dei soli pattern testuali. `~`, percorso assoluto, `XDG_CONFIG_HOME` e
     symlink verso la stessa radice danno lo stesso esito.
  4. Dove si valida e si apre un file protetto, apertura con `O_NOFOLLOW`,
     `openat` o equivalente.
- **Test:** `tests/runtime/safety/test_credentials.py` e
  `tests/runtime/safety/test_sandbox_runtime_bind.py` (esistenti), più:
  - nessun mount di chiavi negli argomenti di bwrap;
  - ogni forma di alias negata;
  - errori senza percorso né segreto nei log.
- **Turno reale:** «quali credenziali ho salvato?» funziona e non rivela segreti.

### A.5 Ordine di assegnazione

1. Sequenza principale:
   `P0 → P1 → F0.1 → F0.2 → F0.3 → F1.1 → F1.2 → F1.3 → F2.1 → F2.2 → F2.3 →
   F3.1 → F3.2 → F3.3 → F4.1 → F4.2 → F4.3 → F5.1 → F5.2 → F5.3 → F5.4 →
   F6.1 → F6.2`. F6.3 e F6.4 vengono solo dopo RM-0008/F5.
2. **FS-A e FS-B** partono subito, in parallelo alla sequenza, su file diversi,
   con un esecutore ciascuna.
3. Mai due unità contemporanee sugli stessi file: vedi le voci «Leggi prima».
4. Il coordinatore raggruppa le unità concluse (per esempio le F0.x, poi le
   F1.x) in un ciclo di rilascio, e dopo il riavvio esegue i turni reali delle
   schede.

## Appendice B — Esito dei rilievi della revisione 4

| Rilievo | Esito | Dove |
|---|---|---|
| §11.1 Baseline unica e congelata | accolto | P0, metadati in testa |
| §11.2 Numeri storici e definizione degli indicatori | accolto | §2 (storici), §7 |
| §11.3 Provenienza dal confine autenticato, `unknown` per il legacy | accolto, semplificato a due campi | F0 |
| §11.4 Tassonomia dei componenti inerti | accolto; il registro nominale diventa un test generico (KISS) | §2, F1 |
| §11.5 Valutazioni append-only, giudice TELOS per ogni famiglia, ambito globale, classificazione della lacuna | accolto; tipi di lacuna da sette a cinque | §5.2, F2, F3 |
| §11.6 Rifiuto come regola globale, consegna, backlog | accolto; riusato il riepilogo esistente; backlog in F1 (pulizia) e F5 (politica) | §5.4, §5.5, F1, F5 |
| §11.7 F6: veto, intersezione, punto di valutazione, dipendenza da RM-0008/F5 | accolto; punto unico, calcolo in memoria, ricevute solo per remoto e durevole | §5.3, F6 |
| §11.8 Runner unico, radici protette, broker | accolto; partono subito | FS-A, FS-B |
| §11.9 Criterio finale e prove negative | accolto | §8, §11 |
| §11.10 Review del 14/9 (criteri, copertura dell'obiettivo, autonomia) | accolto | §§1, 4, 5, 6, 7 |
| Proposta di «versione corta» | respinta da Roberto (14/9): piano completo | — |
| §12 Dossier per agenti medium | incorporato in forma compatta; processo alleggerito (manifest solo per P0) | appendice A |
| §12.2 Le dodici decisioni preliminari | chiuse dagli agenti con valori prudenti, senza chiederle a Roberto | §5.6, F1, §5.3 |

## Appendice C — Rilievi adversarial e multidisciplinari sulla revisione 5

> **Stato dell'appendice:** temporanea e non normativa. Registra i difetti da
> chiudere nella revisione successiva. Va rimossa insieme all'appendice B quando
> Roberto approva la parte normativa.
>
> **Metodo:** revisione indipendente di architettura, sicurezza multiutente e
> implementabilità da parte di agenti di medio livello, seguita da riscontro sui
> simboli e sui percorsi del codice del 14 settembre 2026.

### C.1 Verdetto

La revisione 5 **non è approvabile**. I rilievi `R5-01`-`R5-09` sono bloccanti:
la roadmap non passa a `ready` e l'implementazione F0-F6 non parte finché una
nuova revisione non li chiude nel testo normativo, nelle schede e nei test.
FS-A e FS-B restano autorizzate soltanto entro i confini di sicurezza già
decisi, dopo avere risolto le ambiguità specifiche `R5-14`.

### C.2 Rilievi bloccanti

#### R5-01 — Il ciclo delle capacità nuove è circolare

- **Prova:** §5.3 e F5.1 permettono `auto` soltanto dopo un preesercizio riuscito;
  F5.2 decide prima dell'effetto; F5.4 invia la nuova capacità a RM-0008 soltanto
  come effetto. Oggi `change_applier.apply_create_executor` crea il candidato
  solo dopo che l'intent è `accepted`.
- **Effetto:** al momento della decisione non può esistere la ricevuta richiesta;
  con i fatti mancanti l'intent finirebbe sempre in `ask_user` oppure un agente
  sarebbe costretto a fidarsi del candidato.
- **Correzione richiesta:** separare gli stati e le transizioni:
  `proposed -> birth_requested -> preexercise_receipted -> decision_pending ->
  accepted -> globally_active`. La prima decisione autorizza soltanto sintesi e
  preesercizio, mai l'attivazione. Ogni create, extend, promote, retire,
  rollback o riattivazione di executor passa come `BirthIntent` tipizzato dalla
  porta RM-0008; RM-0009 conserva richiesta e ricevuta e non modifica direttamente
  manifest o lifecycle.
- **Prova di chiusura:** un test integrato mostra una richiesta Birth idempotente,
  la ricevuta verificata e una sola promozione globale; senza ricevuta
  l'attivazione è impossibile.

#### R5-02 — Manca il costruttore autorevole dei fatti

- **Prova:** F5.1 elenca fatti decisionali ma F5.2 ordina soltanto di chiamare
  `decide_change` sull'intent. `ChangeFacts` non contiene ambiti correnti e
  richiesti, sensibilità dei dati, digest/generazione della ricevuta e la riga
  «regola globale». La sola presenza di `[undo]` o `reverse_pattern` viene
  trattata come reversibilità.
- **Effetto:** un body proposto dal modello potrebbe auto-dichiararsi reversibile,
  senza rete, credenziali o dati sensibili e ottenere più autorità.
- **Correzione richiesta:** creare `build_change_facts(intent_id)` nel core. Per
  ogni campo registra valore, fonte autorevole e versione; rilegge manifest
  firmato, autorità corrente, ricevute RM-0008 e classificazione dei dati. I
  valori dichiarati dall'intent non sono attestazioni. Distinguere reversibilità
  `guaranteed`, `conditional` e `none`; solo `guaranteed`, provata e collaudata,
  può portare ad `auto`. Definire inoltre `global_behavior_change` e la
  precedenza della relativa riga: oggi tutte le modifiche sono globali, ma la
  tabella assegna sia `auto` alle ottimizzazioni sia `ask_user` alle regole
  globali. Qualunque fatto assente o non verificato porta ad `ask_user` o
  `deny`, mai ad `auto`.
- **Prova di chiusura:** test anti-auto-attestazione per ciascun fatto e tabella
  esaustiva delle precedenze della decisione.

#### R5-03 — L'approvazione globale non ha un contratto di autorità

- **Prova:** §5.3 e F5.2 dicono che una risposta sì/no torna dal canale del
  riepilogo e chiama `apply_decision`, senza rendere normativi principal, TTL,
  nonce, contenuto approvato e stato atteso. Il callback corrente limita il
  promoter al ruolo `host`, ma non costituisce il contratto dei nuovi intent.
- **Effetto:** un guest, un messaggio inoltrato o una risposta vecchia potrebbero
  decidere una modifica diventata globale o cambiata dopo il riepilogo.
- **Correzione richiesta:** solo un principal autenticato `host/admin` possiede
  `global_change_decision`. La richiesta usa una ricevuta monouso legata a
  `intent_id`, fingerprint, digest dei fatti e dell'effetto mostrato, versione
  della politica, stato/versione attesi, canale, destinatario e scadenza. La
  transizione usa compare-and-swap e registra il principal verificato.
  `origin_owner_id` resta solo provenienza e non attribuisce autorità.
- **Prova di chiusura:** guest, inoltro, replay, doppio click, scadenza e
  rivalutazione dell'intent vengono rifiutati; la risposta valida produce una
  sola transizione.

#### R5-04 — L'attivazione atomica globale è solo dichiarata

- **Prova:** `change_intents` ha un indice non univoco sulla fingerprint e
  `upsert_intent` esegue `SELECT` seguito da `INSERT`; `_transition` esegue
  `SELECT` seguito da `UPDATE`. `change_applier` produce l'effetto e soltanto
  dopo chiama `mark_applied`. Catalogo, file, firme, database e code non
  condividono una transazione.
- **Effetto:** due worker possono duplicare o applicare due volte un intent; un
  crash può lasciare un effetto globale senza ricevuta o utenti su generazioni
  diverse.
- **Correzione richiesta:** pulizia deterministica del legacy e
  `UNIQUE(fingerprint)`; upsert atomico; transizioni con stato e versione attesi;
  stato `applying` con claim/lease; `operation_id` idempotente; protocollo
  `prepare -> verify -> commit -> reconcile`. Per gli effetti sul catalogo il
  commit è un unico puntatore firmato a una generazione immutabile. Un journal o
  outbox permette recovery all'avvio e compensazione specifica per kind.
- **Prova di chiusura:** test con due connessioni/processi e crash injection in
  ogni confine; nessun utente osserva uno stato misto e il riavvio converge.

#### R5-05 — Alcuni kind non hanno un effetto coerente o completo

- **Prova:** F4.2 chiama «automazione» `KIND_MATERIALIZE_PIPELINE`, ma l'handler
  corrente esegue la query una volta e il rollback dichiara che non esiste un
  artefatto persistente. F4.3 trasforma un seed autopath in
  `KIND_CACHE_PATTERN`, il cui handler richiede invece `canonical_query` e
  `tool_name`. `KIND_RETIRE_COMPONENT` viene aggiunto ad `ALL_KINDS` ma non a
  applier, observer e rollback.
- **Effetto:** S2 non anticipa nulla; intent accettati possono fallire con
  `no_handler` oppure produrre un effetto diverso da quello approvato.
- **Correzione richiesta:** scegliere tipi distinti e chiusi. Per ogni kind la
  roadmap deve specificare schema del body, fingerprint, fatti, decisione,
  handler, ricevuta, misura, rollback e porta RM-0008 quando tocca un executor.
  Un'esecuzione una tantum non è una capacità o automazione reversibile e non è
  mai `auto` per il solo fatto di riusare tool esistenti.
- **Prova di chiusura:** test parametrico sull'insieme completo dei kind: ciascuno
  possiede un contratto e completa apply/observe/rollback; un kind sconosciuto
  fallisce prima della decisione.

#### R5-06 — Provenienza globale e principal operativo sono confusi

- **Decisione conservata:** `origin_owner_id` e gli `owner_id` delle sorgenti
  restano metadati di provenienza. Non entrano in fingerprint, ranking, soglia,
  autorità o rollout.
- **Difetto:** F6 non rende obbligatorio un distinto
  `operational_owner_user_id` ricavato dal principal autenticato, né chiarisce
  che la nuova autorità si aggiunge ai controlli esistenti senza sostituirli.
- **Correzione richiesta:** l'envelope di ogni invocazione contiene principal,
  attore, canale, owner operativo, capability e ambiti effettivi, destinazione e
  contesto di ammissione. L'autorità F6 è un'ulteriore intersezione fail-closed
  dopo autenticazione, ACL, consenso e scope ordinari. La capacità e il routing
  sono globali; dati, credenziali, task e singole esecuzioni restano sempre
  circoscritti al principal corrente.
- **Prova di chiusura:** due utenti vedono la stessa capacità, ma tentativi di
  riuso di argomenti, ricevute, cache, resume o task dell'altro sono negati.

#### R5-07 — Un veto tecnico diventa un rifiuto globale per 180 giorni

- **Prova:** F5.1 include fra i `deny` lifecycle inattivo, attestazione mancante e
  generazione discordante; F5.3 ordina che qualunque `deny` scriva
  `rejected_fingerprints`.
- **Effetto:** un guasto o una condizione transitoria può sopprimere per tutti un
  bisogno valido anche dopo la riparazione.
- **Correzione richiesta:** separare `technical_veto` retryable/non-retryable da
  `global_rejection`. Solo una decisione globale autorizzata crea la regola di
  rifiuto. Scadenza e revoca sono eventi append-only (`revoked_at` e motivo), non
  cancellazioni; dopo scadenza è definita una riapertura atomica dell'intent.
  Il registro deve avere un consumatore effettivo prima sia del generatore sia
  del planner, come promette §5.4. La migrazione assegna decisione e generazione
  alle righe JSONL e agli intent già applicati, affinché il rollback di una
  decisione vecchia non cancelli una regola successiva sulla stessa fingerprint.
- **Prova di chiusura:** riparare un'attestazione rende di nuovo valutabile il
  bisogno; un rifiuto host valido continua invece a impedirne la rigenerazione
  fino a scadenza o revoca.

#### R5-08 — F6 non definisce davvero il punto unico e il monouso

- **Prova:** §5.3/F6 parla della stessa funzione, mentre F6.1 crea una funzione
  con risultato diverso. I percorsi locale, remoto, builtin, verb-unique e
  durevole entrano da punti differenti. La ricevuta descritta non ha issuer,
  store di consumo, clock/skew, garbage collection o protocollo contro due
  consumer concorrenti.
- **Correzione richiesta:** tenere separate `decide_change` e
  `decide_invocation`, condividendo soltanto fatti e primitive tipizzate.
  Definire un `InvocationEnvelope` autorevole e un admission hook obbligatorio
  per ogni effetto. La ricevuta firmata include token id, principal operativo,
  executor e generazione, capability/ambiti, digest canonico degli argomenti,
  destinazione, workload/tentativo, approval e policy. Il token id è consumato
  atomicamente in uno store centrale; subito prima dell'effetto si riverificano
  stato e revoche correnti.
- **Prova di chiusura:** inventario del call graph più test per locale, remoto,
  builtin, verb-unique, durable executor e durable internal; replay concorrente,
  reboot, scadenza e revoca falliscono senza effetti.

#### R5-09 — Un artefatto globale può incorporare dati privati

- **Prova:** `_compute_intent_sig` produce una forma leggibile con keyword;
  `canonical_query`, `suggested_query`, body e autopath possono contenere nomi,
  percorsi, destinazioni, PII o segreti dell'owner originario. La roadmap richiede
  isolamento ma non definisce la trasformazione che lo garantisce.
- **Correzione richiesta:** lo store globale contiene solo strutture canoniche,
  parametriche e versionate, senza valori letterali dell'utente. Gli argomenti
  concreti restano nello store per-owner e vengono forniti al momento
  dell'esecuzione dopo auth. Applicare scansione di segreti/PII prima
  dell'upsert; causal id opachi; provenienza visibile soltanto all'amministrazione
  e mai esposta in catalogo, Tutor o riepiloghi ordinari.
- **Prova di chiusura:** due owner con lo stesso bisogno e valori privati diversi
  convergono nello stesso artefatto globale; nessun valore dell'uno compare nelle
  viste o esecuzioni dell'altro.

### C.3 Rilievi importanti da chiudere prima dell'implementazione

#### R5-10 — S1 e la riconciliazione non selezionano i turni corretti

F4.1 aggrega `lacuna_events` senza imporre il collegamento a un TurnLog
`origin=user`; tre collaudi o turni di sistema possono quindi generare una
proposta globale. F3.3 considera inoltre tutti gli errori e i `partial` come
eventi attesi, anche quando la causa è input invalido, servizio esterno,
consenso o altro errore che non rappresenta una lacuna. La revisione deve:

1. definire l'insieme chiuso degli esiti/classi che devono emettere un evento;
2. fare join sul turno autenticato ed escludere `test`, `system`, `unknown` e
   legacy;
3. ricontrollare il catalogo e la sua generazione al momento dell'aggregazione,
   così eventi vecchi non propongono una capacità già presente;
4. applicare controlli anti-abuso al confine senza usare `origin_owner_id` come
   gate semantico o rollout.

I test coprono un turno di collaudo, un errore non pertinente, un executor
aggiunto fra evento e aggregazione e tre turni reali validi.

#### R5-11 — Identità e schema F2 non garantiscono convergenza e append-only

- La fingerprint attuale non è definita normativamente come
  `(change_kind, need_sig canonica, versione)`: alcune famiglie usano nome,
  query o soli tool e possono divergere o collidere.
- `source_event_id` non è dichiarato `NOT NULL`; in SQLite un vincolo `UNIQUE`
  accetta più righe con `NULL`.
- Lo stesso vincolo impedisce una correzione che conserva il medesimo evento
  causale, mentre §5.2 promette correzioni append-only.
- `SourceKind` non include l'`unknown` richiesto per il legacy e manca un ordine
  totale per scegliere l'ultima valutazione a parità di timestamp.

La revisione deve distinguere `evaluation_id` dall'evento causale, aggiungere
revision/supersedes, identificativi non nulli e namespaced, vincoli e foreign key,
ordine monotono e migrazione versionata. Un test concorrente deve dimostrare un
solo intent globale e una correzione verso il basso conservata integralmente.

#### R5-12 — La pulizia F1.3 può eliminare proposte valide

Per `KIND_CREATE_EXECUTOR` il fatto che il target non esista nel catalogo è la
precondizione normale, non obsolescenza. Le regole di pulizia devono essere
specifiche per kind e usare uno stato `superseded` o `obsolete`, distinto da
`rejected`; il dry-run deve indicare regola e prove, e il ripristino deve essere
eseguibile. Il test include una proposta create valida con target assente.

#### R5-13 — Benefit e rollback automatico non sono definiti

L'unico `value` della dimensione `benefit` non può rappresentare insieme
latenza, costo e successo. Il peggioramento del 20% non definisce direzione,
baseline, unità, attribuzione, campione minimo, più metriche, zero utilizzi o
dati mancanti. La revisione deve aggiungere `metric_name`, unità, finestra,
sample count, baseline, osservato e formula per kind. Con evidenza insufficiente
si registra `insufficient_evidence`: non si dichiara successo e non si esegue un
rollback arbitrario. Va inoltre definito il TTL di ritiro per capacità mai usate.

#### R5-14 — FS-A, FS-B e il parallelismo sono sotto-specificati

- FS-A cerca `test_runner` solo in `runtime`, ma riferimenti possono restare in
  strumenti, test, documentazione e release installata. L'inventario copre tutto
  il repository e l'installazione; prova rete, processi, utente/IPC, ambiente
  ammesso, cwd effimera, CPU/RAM/output e morte dell'intero albero.
- FS-A lascia aperta la scelta fra tradurre e rifiutare `setup`, `teardown` ed
  `env`: la revisione deve sceglierne una e definire la migrazione dei manifest
  firmati prima della rimozione.
- FS-B non può affidarsi a `O_NOFOLLOW` sul solo file finale: usa un dirfd con
  cammino no-follow per ogni segmento o `openat2` con risoluzione restrittiva.
  Il broker ricava l'owner operativo dal principal, mai dal payload; le radici
  protette includono per default tutto il control plane, con eccezioni tipizzate.
- FS-B, F0 e F6 condividono registri e `agent_runtime.py`: A.5 deve fornire una
  matrice file-owner/dipendenze e non dichiararle genericamente parallele.

#### R5-15 — Il test AST «chi lo chiama?» non prova l'effetto

Alias, callback, dispatch dinamico e SQL costruito rendono fragile il criterio
«ogni funzione pubblica ha un chiamante». Sostituirlo con un registro chiuso
`producer -> store/event -> consumer -> observable effect`, verificato da probe
runtime specifici. Le eccezioni hanno owner, motivo e fase di rimozione.

#### R5-16 — Cache e rifiuti richiedono una generazione globale

`tools_sig` e `pool_sig` non cambiano necessariamente quando cambiano policy,
rifiuti o rollback di routing/autopath. Introdurre un `global_change_epoch` o
includere la versione effettiva della politica e del registro di rifiuto nelle
firme controllate su ogni cache hit. I test verificano cache hit dopo rifiuto,
revoca, rollback e cambio policy.

#### R5-17 — Eventi F3 e adapter F4 non hanno tutti i collegamenti necessari

Il writer corrente delle lacune non riceve in modo uniforme turno, passo e
owner; alcune uscite del dispatch non passano dal terminator. Occorre un solo
contratto tipizzato dal punto che possiede la prova, con ordinal del passo,
principal derivato, generazione del catalogo e classificatore. Le schede F4
devono inoltre indicare esplicitamente la registrazione dei nuovi adapter,
l'hook che chiama `judge_intent` e la transazione unica intent+sorgente. Un test
integra scheduler, adattatore, intent, fonte e valutazione senza interventi
manuali.

#### R5-18 — Contatori e rollback possono dichiarare risultati falsi

I contatori F6 solo in memoria si perdono al riavvio e non sono condivisi fra
processi; servono snapshot aggregati con watermark e flush, senza una scrittura
per invocazione. Il rollback esistente può restituire un dizionario con errore
senza sollevare eccezione: il contratto deve diventare tipizzato e lo stato passa
a `rolled_back` solo dopo verifica dell'effetto inverso; altrimenti usa
`rollback_failed` con retry. I test coprono riavvio, multiprocesso e fallimento
reale del rollback.

#### R5-19 — F0 non copre tutti gli esiti e non risolve il ruolo autenticato

Il codice produttivo usa anche `loop_break`, mentre il dispatcher possiede uno
stato interno `needs_inputs`; F0.1 afferma invece che esistono solo `answer`,
`error` e `ask` e ordinerebbe subito `BLOCKED_DECISION`. Inoltre aggiungere il
ruolo `test` a `users.ROLES` non basta: il confine HTTP espone ruoli tecnici
diversi. La revisione deve:

1. definire l'outcome per ogni `final_kind` raggiungibile e il punto in cui
   `needs_inputs` viene normalizzato;
2. risolvere `authenticated_user_id` nel ruolo logico tramite il registro utenti,
   usando `unknown` in modo fail-closed;
3. serializzare le enumerazioni persistite come valori (`str, Enum` o `.value`),
   mai come `TurnOrigin.USER`;
4. provare host/admin, guest, test, chiamante LAN sintetico e resume.

#### R5-20 — Il giudizio di allineamento deve distinguere errore e non applicabilità

L'API corrente di `alignment_engine` può trasformare un errore del valutatore in
un risultato vuoto o in uno score basso. F2 deve prescrivere un risultato
tipizzato `ok | not_applicable | failed`: un errore non scrive né
`not_applicable` né un allineamento numerico e viene ritentato con backoff,
idempotenza e versione TELOS. I test simulano indisponibilità, risposta invalida,
non applicabilità reale e correzione con una nuova versione del valutatore.

#### R5-21 — Le schede degli adapter contengono percorsi e identificativi incompleti

F4.2 e F4.3 indicano `change_intent_adapters/...` dalla radice, ma i moduli reali
sono sotto `runtime/change_intent_adapters/...`. Le schede devono citare anche
`runtime/change_intent_adapters/__init__.py` e il job che materializza gli
intent. Per ogni sorgente S1-S3 va definita la formula stabile e namespaced di
`source_event_id`, comprensiva della versione necessaria, così retry e due worker
convergono davvero. Il test parte dal job registrato e prova che l'adapter nuovo
sia raggiungibile in produzione.

#### R5-22 — Baseline e registro di politica non sono ancora riproducibili

P0 confronta archivi vivi: due esecuzioni in tempi diversi possono produrre
numeri diversi senza che l'implementazione sia cambiata. Serve un cutoff o
watermark per ogni store, backup coerente dei database con WAL e digest degli
input. P1 deve includere anche limite del lotto TELOS, budget delle domande,
precedenza fra variabili legacy e nuove, e il punto di validazione nella
readiness di avvio. Un valore non valido impedisce l'avvio; il limite di tre
domande settimanali produce una coda deterministica, non perdita o duplicazione.

### C.4 Porta della revisione successiva

La revisione successiva è pronta per un nuovo giro adversarial soltanto quando:

1. ogni `R5-01`-`R5-09` ha una modifica normativa, una scheda medium e almeno un
   test di chiusura nominato;
2. ogni `R5-10`-`R5-22` è accolto oppure respinto con prova verificabile e senza
   lasciare una scelta all'esecutore;
3. una matrice per ogni kind mostra sorgente, fingerprint, fatti autorevoli,
   decisione, effetto globale, store, atomicità, misura e rollback;
4. una matrice per ogni percorso di invocazione mostra principal operativo,
   admission hook, ricevuta, punto di consumo e prova di zero effetti al diniego;
5. due agenti medium indipendenti, leggendo soltanto la roadmap e il repository,
   producono lo stesso elenco di file, transizioni, vincoli e test da realizzare.
