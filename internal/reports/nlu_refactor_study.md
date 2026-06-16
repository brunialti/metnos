# Studio refactor — da regex pre-cablati a NLU centralizzato (frase→JSON via LLM vincolato)

Stato: STUDIO (16/6/2026). Nessun codice di produzione toccato. POC in
`runtime/poc/`. Decisioni a Roberto in fondo.

## 1. Problema

L'estrazione di struttura dalla query (ordering, finestra temporale,
ricorrenza, count/visualize, intent verbo/oggetto, compound, notify, provider,
availability, propose, NER…) e' oggi fatta da ~42 lessici/regex IT+EN cablati
a mano (censimento 16/6). Difetti: (1) **per-lingua** — falliscono in
silenzio su lingue ≠ it/en; (2) **manutenzione** — morfologia intrecciata,
fragili; (3) **pre-cablati** — codificano superficie, non significato.

## 2. Approccio

Frase → **JSON strutturato** prodotto da un LLM con **output VINCOLATO a
JSON-schema** (constrained decoding). Lo schema (frame semantico) e'
**lingua-indipendente, UNO solo**; l'input in qualunque lingua lo capisce il
modello (competenza multilingue appresa). Niente grammatica per-lingua: la
grammatica/schema vincola solo l'**output**. Deterministico §7.9 (temp0+seed);
JSON **valido per costruzione**.

Confine: i formati-macchina lingua-invarianti (path, date ISO, numeri, e la
*validazione* dei valori normalizzati) restano a parser deterministici.

## 3. Evidenze (POC)

- **Multilingue, una sola schema**: 17/17 = 100% su it/en/fr/es/de — FR/ES/DE
  **non hanno regex** nel codice e funzionano. (`poc/bench.py`)
- **Constrained decoding**: overhead <40µs/token (XGrammar/llama.cpp); JSON
  sempre valido.
- **Comparativa modelli** (stesso task, `poc/compare_models.sh`):
  | modello | accuratezza | p50 |
  |---|---|---|
  | Qwen3.6-35B-A3B (prod, MoE 3B-attivi + MTP) | 16/17 (≈100%*) | ~1000ms |
  | gemma-4-E2B (dense ~2B) | 17/17 | ~1155ms |
  | Qwen3.5-9B Q4 (dense) | 17/17 | ~2240ms |
  | Qwen3-0.6B | 10/17 (59%) | ~277ms |
  \*il miss e' `last-24h`≡`last-1d` (gold rigido).
  → **il modello di prod e' il punto ottimale** (MoE+MTP batte il dense 9B e
  pareggia il tiny gemma, a accuratezza ≥). NON serve un modello nuovo.
- **Leve di latenza**: non la taglia, ma (a) MTP/speculative, (b) **token di
  output** (1 slot ~300ms → 5 slot ~1000ms), (c) **fusione** nella call intent
  gia' esistente (costo marginale = token extra, non nuovo round-trip).
- **Corpus reale** (1207 query storiche uniche, regex-vs-LLM, `poc/bench_corpus.py`):
  - **0 JSON invalidi su 1207** (constrained decoding regge al 100%).
  - latenza core-5: p50 **1012ms**, p90 1084, p99 1259 (1 outlier 6.8s).
  - accordo per campo: ordering_mode 93.9%, ordering_desc 95.1%,
    time_present **87.2%**, recur 99.7%, count 98.2%, visualize 98.4%.
  - **Classificazione disaccordi** (ispezione): in larga parte **LLM>regex**
    e quasi sempre **valore aggiunto** — l'LLM cattura riferimenti temporali
    che il regex NON copre («domani», «prossima settimana», «questa
    settimana», «più di 6 mesi»: 150 casi) e ordinamenti impliciti
    («le 5 più grandi», «top-10 per memoria»: 68). Molti `regex>LLM` sono
    **falsi positivi del regex** corretti dall'LLM (count su «numero di
    emergenza»/«quanto tempo»; viz su «guarda»=osserva). **Veri miss
    dell'LLM: pochissimi** (group-by sepolto in compound lunghi; «mostrami
    quanti»). → l'LLM non è solo equivalente: **migliora** copertura e
    precisione, ma alcuni disaccordi sono **cambi di comportamento**
    (es. «top-N per X» → sort) da approvare prima del flip ⇒ shadow-first.
- **Envelope esteso** (15 campi, `poc/envelope.py`, server scarico):
  - latenza p50 **2836ms** vs core-5 1002ms → **Δ ≈ +1.8s** per assorbire
    ~10 funzioni in più.
  - qualità (10 casi it/en/fr/es): risolve **§E.2** (`count.n=100`) **e §E.3**
    (`persons=[Silvia,Carol]`, `repo=brunialti/metnos`, `paths=[/tmp]`),
    assorbe undo/notify/provider/format/availability/propose/multi-step/
    negation/actions, multilingue; minori over-extraction («ultime 5»→today).
  - ⇒ conferma il **TIERING**: core-5 (~1s) sempre attivo; slot estesi
    on-demand (la frame-15 a ~2.8s è troppo per ogni turno).

## 4. Architettura proposta — modulo `nlu` centralizzato (drop-in)

`runtime/nlu.py` (proto in `poc/nlu_central.py`):
- `frame(query) -> dict|None`: UNA estrazione LLM vincolata, **memoizzata per
  (query, lingua)** → piu' consumatori nello stesso turno = UNA call.
- **Adapter drop-in** con shape IDENTICA alle funzioni legacy:
  `ordering()`==ordering_clause.detect, `time_window()`==parse_query_time_window,
  `recurrence()`==parse_recurrence_query, `count_intent()/visualize_intent()`.
  → i call-site diventano deleganti di 1 riga; i chiamanti a monte non cambiano.
- **Gate `METNOS_NLU` = llm|regex|off** + fallback regex registrato: rollout
  reversibile, zero rischio se l'LLM e' giu'.
- **Una spec, multilingue**; prompt in inglese (regola funzioni
  language-independent), pattern-oriented (placeholder, non letterali).

## 5. Assorbire & arricchire (l'envelope)

Poiche' la call c'e' comunque, **sostituisce** la call `intent_extractor` gia'
esistente ed estrae di piu' nello stesso passaggio.

ASSORBE (ritira logica separata): intent verbo/oggetto + compound (`actions`),
undo, notify+channel, scheduling+recurrence, provider/skill, count/visualize,
output_format, **availability + propose (le 2 regex giganti)**, multi-step,
system-action.

ARRICCHISCE (il regex non puo'): `ordering.field` canonico (assorbe
_FIELD_FAMILIES); `count.{n,of}` → **chiude §E.2** (numero adiacente a
sostantivo-conteggio, multilingue); `entities.{persons,places,paths,repo}` →
**chiude §E.3** (multi-nome) + args_extractor; `negation`; `written_lang`.

Vincoli: latenza ∝ token output; precisione cala su slot astratti; il routing
e' ad alto rischio (foldare l'intent → gold-bench obbligatorio). → **tiering**
`core` (actions+ordering+time+count, sempre) vs `extended` (on-demand).

## 6. Ulteriori enhancement (esplorazione)

1. **GBNF closed-vocab sui campi di routing**: vincolare `verb`/`object` alla
   tassonomia §2.2 ESATTA via grammatica → impossibile allucinare un verbo non
   valido. Unisce la garanzia closed-vocab al capire dell'LLM. (Alta priorita'.)
2. **Few-shot da Praxis/corpus** (no retrain, [[feedback-no-training-amplify-reality]]):
   recuperare i K frame validati piu' simili come esempi → accuratezza sulla
   distribuzione reale che cresce nel tempo. Closed-loop.
3. **Cache cross-turno (L0-style)**: il frame e' deterministico → cache per
   query (hash) + riuso per cosine-simili. Ammortizza la latenza sui
   heavy-hitter ("trova foto simili" x439).
4. **Confidence/abstention per slot**: campo confidence → slot a bassa
   confidenza ignorati o verificati; mitiga la precisione.
5. **Tiering a 2 modelli**: core di routing su modello rapido; envelope esteso
   solo quando serve. (Ma la comparativa dice: un modello solo, il 35B-A3B-MTP,
   gia' basta.)
6. **Streaming/early-exit**: emettere `actions` per primo (routing) e iniziare
   il planning mentre il resto del frame si completa.
7. **Slot registry dichiarativo**: ogni capability registra il suo slot +
   consumer; l'envelope si ASSEMBLA dai registrati → aggiungere una capacita' =
   registrare uno slot, niente chirurgia di prompt. Endgame estensibile.
8. **Telemetria closed-loop**: log frame+esito; turni falliti/corretti →
   coppie (query, frame giusto) → alimentano few-shot (#2) + gold di
   regressione. Migliora senza retrain.
9. **written_lang → lingua di risposta** automatica (lega all'i18n output).
10. **Self-consistency** sul solo routing per i casi critici (2-sample + agree).

## 6.bis Tuning / ottimizzazione parametri (test completo, `poc/optimize.py`)

VINCOLO: **accuratezza irrinunciabile** (Roberto) → latenza solo come
tie-breaker fra varianti a parità di accuratezza massima.

A `temperature=0` i sampler (top_k/top_p/min_p/repeat_penalty) sono **inerti**
(greedy); `max_tokens` irrilevante (EOS). Leve reali: forma schema, few-shot,
prompt, thinking.

**Fase 1 — gold-24 (accuratezza assoluta):**
| variante | acc | p50 | tok |
|---|---|---|---|
| A full-required 0shot | **24/24** | 1000ms | 80 |
| B flat-keys 0shot | **24/24** | **829ms** | 69 |
| C +2shot | **24/24** | 949ms | 52 |
| D min-instr | 22/24 | 1524ms | 93 |
| E lean-optional | 21/24 | 322ms | 16 |
| F/G/H thinking 128/256/512 | **0/24** (24 invalid ×3) | — | — |

**Thinking: ESCLUSO** definitivamente — incompatibile con json_schema (vincolo
forza JSON dal 1° token), inutile in free-parse (0/24 a ogni budget).
**Lean: ESCLUSO** — l'aggregato 21/24 inganna (passa i no-slot, fallisce gli
slot). Lo scaffolding `required` è portante per l'accuratezza.

**Fase 2 — corpus reale N=400 (accordo-vs-baseline-A):**
| variante | accordo-vsA | p50 | p90 |
|---|---|---|---|
| A (rif) | 100% | 1004ms | 1072ms |
| B flat | 97.2% | 817ms | 926ms |
| C 2shot | 93.5% | 970ms | 1066ms |

Insieme accuratezza-safe = {A, B, C}. C drifta (93.5%) → fuori. **B flat** =
unico guadagno latenza (−19%) restando 24/24; promuovibile SOLO se le ~2.8%
divergenze da A sono B-equivalenti/migliori (classificazione `poc/diff_ab.py`).
Altrimenti **resta A** (accuratezza prevale). [VERDETTO B-vs-A: in corso]

NB: tutte le varianti = STESSO modello (Qwen3.6-35B-A3B-MTP prod); differenze
solo di prompt-engineering.

## 7. Rischi & mitigazioni

- **Latenza sul critical path**: il routing aspetta il frame (~1s; netto ~+600ms
  rimosso l'intent). → cache (#3), tiering, streaming (#6), core snello.
- **Precisione slot astratti**: vista over-extraction. → prompt esplicito
  "explicit only" (gia' applicato), confidence (#4), GBNF enum (#1).
- **Routing = single point**: → gate+fallback regex finche' non provato; gold
  bench 25/25 deve restare verde prima del fold.
- **Re-baseline al cambio modello**: → gold-set di frame come guard (come il
  routing bench).
- **Determinismo**: temp0+seed (§7.9); confermato riproducibile.

## 8. Piano di migrazione — VINCOLO GOVERNANTE: zero rischio di regressione

Principio (Roberto 16/6): **estendere con prudenza, nessuna regressione**. Il
regex resta la verita' viva finche' l'LLM non e' provato sul traffico reale; il
default non cambia; ogni passo e' reversibile e cancellato da test verdi.

0. **Validazione offline** (in corso): corpus 1217 regex-vs-LLM + envelope
   latency. Nessun codice di prod toccato.
1. **Modulo `nlu` + adapter drop-in** dei 5 slot. Gate `METNOS_NLU` con
   default **`regex`** → comportamento IDENTICO a oggi (l'LLM non e' sul path).
2. **SHADOW MODE** (`METNOS_NLU=shadow`): in produzione si calcola il frame LLM
   e si **logga il confronto frame-vs-regex**, ma il comportamento resta quello
   del regex. Zero impatto, raccoglie accordo su traffico reale (= il corpus
   test, ma live e continuo). Cancello per i passi seguenti: accordo ≥ soglia
   stabile su N giorni.
3. **Flip PER-CONCEPT** a `llm` solo dove: (a) shadow-accordo ≥ soglia, (b)
   gold/bench del dominio verdi, (c) **fallback regex mantenuto** (LLM giu' o
   JSON invalido → regex). Un concetto alla volta, reversibile via gate.
4. **Fold di `intent_extractor`** (actions/routing) — il passo ad alto rischio:
   solo dopo shadow esteso + **routing bench 29/29 e intent gold 25/25 verdi**;
   fallback al vecchio intent_extractor; reversibile.
5. **Assorbi** undo/notify/provider/format/availability/propose/multi-step,
   ognuno shadow→flip→(eventuale) ritiro del lessico solo a parita' provata.
6. **Arricchisci**: count.{n,of} (§E.2), entities (§E.3) — additivi, non
   toccano i path esistenti finche' non consumati.
7. **Enhancement**: GBNF closed-vocab, cache, few-shot, telemetria.

REGOLA: nessun regex/lessico viene **rimosso** finche' il suo concept non e'
in `llm` stabile da abbastanza tempo; fino ad allora resta come fallback. La
rimozione e' l'ULTIMO passo, non il primo.

## 9. Relazione col lavoro detection_lexicon (stanotte)

Il sottosistema `detection_lexicon` (store+daemon+coverage, 21 concept
migrati) risolve i lessici **booleani** rendendoli traducibili. L'envelope NLU
li **supera** per i concept che assorbe. Riconciliazione: detection_lexicon
diventa il **layer di fallback deterministico** (gate `METNOS_NLU=regex`/LLM
giu'), l'envelope il primario. Niente doppio mantenimento: i concept assorbiti
dall'envelope si **ritirano** da detection_lexicon una volta provati. I concept
NON-NLU (es. cookie_banner su testo web, non query utente) restano in
detection_lexicon. → i due sforzi convergono, non competono.

## 10. Decisioni per Roberto

1. Confine **core vs extended** dell'envelope (budget latenza).
2. **Fold di intent_extractor**: sì (massimo guadagno) o tenere intent separato
   e affiancare solo gli slot? (rischio routing).
3. Priorita' enhancement: GBNF closed-vocab (#1) e cache (#3) sembrano i primi.
4. Politica fallback: tenere il regex come rete (gate) o rimuoverlo del tutto
   una volta validato?
