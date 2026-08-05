---
id: 0171
title: Intent extractor → NLU (intenzione + entità) con registro entità→arg
date: 2026-06-14
status: proposed
area: runtime
related:
  - 0044  # intent extractor LLM-based (origine)
  - 0092  # prompt multilang persistiti (.j2 per lingua)
  - 0119  # persons single-name → scena (0119-bis: single→scena intatto)
  - 0129  # implicit_actions (enrichment intent deterministico)
  - 0146  # consolidamento tier LLM (fast/middle/wise)
  - 0163  # users/persons object vocab
complements:
  - 0044  # estende l'intent extractor da {verb,object} a {verb,object,entities}
  - 0129  # stesso pattern: arricchimento additivo dell'intent, consumato a valle
modifies:
  - 0119  # 0119-bis (single-name→scena) diventa un caso del registro entità→arg
---

<!-- Convenzione tracciamento revisioni: questa ADR ESTENDE 0044/0129 (additiva,
non invalida) e MODIFICA il punto 0119-bis (la regola single-name resta valida ma
ora vive nel registro entità→arg, non più hardcoded nell'executor). -->


## Context

L'intent extractor (ADR 0044, `runtime/intent_extractor.py`) oggi produce un
intent **scarno**: `{verb, object}` dal vocabolario chiuso §2.2, più due
arricchimenti additivi — `actions=[{verb,object}, …]` per le query compound
(ranking pool per-clausola, `runtime/engine/routing_pool.py:59`) e
`implicit_actions` (ADR 0129). Una sola call LLM middle, ~370ms/query, verb+object
100/100 sul corpus gold (`tests/benchmarks/intent_accuracy_bench.py`, 25 casi).

Tutto ciò che NON è verbo/oggetto — i **valori** che diventano argomenti degli
executor (quante foto, con chi, di quale anno, dove) — viene oggi ricostruito in
**tre punti scollegati**, ciascuno con i suoi limiti:

1. **Split per-executor a execution-time.** `find_images_indices.py:754`
   (`_split_temporal_from_query`) e `:787` (`_split_persons_from_query`) ri-fanno
   la NER DENTRO l'executor, ad ogni chiamata. Il secondo matcha i nomi contro il
   set **STATICO** `PersonsRegistry().list_all()` (`:809`) — funziona solo per
   gli enrollati, è codice duplicato gemello-per-gemello, e ogni nuovo executor
   che voglia filtrare per persona/data deve riscrivere il proprio split.
2. **Regex ingenui nel resolver.** `args_extractor.py:156` (`_extract_ints`)
   prende QUALSIASI intero — anni («2020»), prezzi («50 euro») — senza sapere se
   è un conteggio. È il nodo bloccante di §E.2 (TODO-HIGH «N foto»→`max_results`):
   il wiring meccanico produce falsi positivi.
3. **Il proposer che INDOVINA.** In assenza di entità risolte a monte, l'LLM wise
   del proposer assegna gli argomenti «a naso» — instrada lo stesso valore ora su
   `name`, ora su `query_text`, ora lo omette → misroute + retry (è esattamente la
   ragione per cui §E.3 ha dovuto FORZARE `names` a valle, `_apply_persons_clause`
   in `dispatch.py`, branch `session/e3-multiname-enrollment` `cc6eee9`).

Il branch B (§E.3, `cc6eee9`) ha già aperto la strada giusta per UN tipo di
entità: `Intent.persons` additivo (`engine/types.py`), estratto come **NER LLM**
nel prompt intent (campo opzionale `persons`, niente liste statiche,
`intent_extractor.py:159`+), e iniettato **deterministicamente** come `names` su
`find_images_indices` quando ≥2 (`_apply_persons_clause`, override del proposer,
§7.9: la NER è LLM, il routing è codice). Funziona per-pezzo (intent estrae i
nomi, l'executor risolve l'enrollment e segnala i non-enrollati).

La domanda che obbliga a decidere ORA: **persons è un caso isolato o l'istanza #1
di un meccanismo generale?** §E.2 (counts) ha la stessa forma — un valore nella
query → un argomento di un executor. Se restano due fix separati, ogni futuro
filtro (date, luoghi) sarà un terzo, quarto split per-executor + un quinto regex
nel resolver. Il costo marginale di ogni nuovo executor cresce. Roberto ha
indicato la direzione (memoria §J): intent→NLU con entità, registro entità→arg,
e DUE vincoli espliciti — **estensibilità** (nuovo executor = una riga) e
**latency da misurare+comparare**, non assumere.

## Decision

Promuovere l'intent extractor a **strato NLU**: oltre a intenzione
(`verb`/`object`/`actions`) estrae le **entità** della richiesta, e un **registro
deterministico entità→arg** le instrada sugli argomenti degli executor. Persons
(da §E.3) diventa l'istanza #1; counts (§E.2) la #2; il meccanismo è uno.

### 1. Shape NLU additiva

`Intent` guadagna un campo `entities` (additivo, come `persons` in `cc6eee9` —
che vi confluisce):

```
entities = {
  "persons": ["roberto", "marco"],   # NER nomi-persona (già in cc6eee9)
  "counts":  [100],                  # conteggi-richiesta ("100 foto", "prime 30")
  "dates":   ["2020", "2020-03"],    # riferimenti temporali → time_window
  "places":  ["mare"],               # luoghi/scene (estendibile)
  # … nuovi tipi additivi
}
```

Vincolo cardine §7.9: **la NER è LLM, NON liste statiche.** Il prompt chiede al
modello di estrarre i nomi/conteggi/date COME tipi, senza enumerare valori
ammessi. La risoluzione del valore al referente reale (slug enrollato, finestra
temporale ISO) resta codice deterministico a valle. Niente dizionari di valori.

Vincolo cardine: **ADDITIVO.** `verb`/`object`/`actions` restano invariati;
`entities` assente quando vuoto (back-compat del ranking). Il bench verb/object
100/100 NON si tocca — come fatto per persons in `cc6eee9` (bench invariato).

### 2. Registro entità→arg (il cuore estensibile)

Una tabella DICHIARATIVA — codice deterministico, non LLM — che mappa
`(object, entity_type) → (arg_name, op?)`. Generalizza `_apply_persons_clause`
in un `_apply_entities_clause` (`runtime/engine/dispatch.py`, a valle del proposer
come `_apply_ordering_clause`/`_apply_persons_clause` oggi):

```
REGISTRY = {
  ("images",  "persons"): ("names", "and"),     # §E.3 (cc6eee9)
  (None,      "counts"):  ("max_results", None), # §E.2 — qualsiasi object con cap
  ("messages","dates"):   ("time_window", None),
  ("events",  "dates"):   ("time_window", None),
  ("images",  "places"):  ("query_text", None),  # scena residua
  # … una riga per ogni (executor-object × entità) supportato
}
```

`object=None` = regola cross-object (es. counts→max_results su qualunque step il
cui schema dichiari `max_results`/`max_total`). Il match finale è **guardato dallo
schema dell'executor**: l'iniezione avviene solo se lo step ha davvero
quell'argomento (evita di forzare `max_results` su chi non lo accetta). Questo
sostituisce il regex-and-pray di §E.2: `100` diventa `entities.counts=[100]`
SOLO se l'LLM l'ha classificato come conteggio-richiesta, e finisce su
`max_results` SOLO dove esiste — gli anni («2020») vanno in `dates`, i prezzi non
vengono classificati come counts.

**Estensibilità (motivo chiave, Roberto):** aggiungere un nuovo executor che
filtra per un'entità già nota = **UNA RIGA** nel registro + il manifest. La NER a
monte estrae GIÀ l'entità; niente nuovo codice di split/estrazione per-executor.

### 3. Executor consumano entità risolte

Gli split per-executor (`_split_temporal_from_query`, `_split_persons_from_query`
in `find_images_indices.py`) vengono **rimossi**: l'executor riceve `names`,
`time_window`, `max_results` già risolti dal registro. La risoluzione enrollment
(token→slug) e finestra (anno→`YYYY`/`YYYY-MM`) resta deterministica ma vive UNA
volta a monte, non duplicata in ogni gemello. Il caso 0119-bis (single-name→scena
intatto) diventa una regola del registro (persons con len==1 → non forzare
`names`, resta scena), non più hardcoded.

### 4. Caricamento dinamico del prompt (lega §I)

L'estrazione-entità è il candidato NATURALE al prompt dinamico (la macchina
`prompt_loader.compose(role, lang, sections=…)` esiste già, `prompt_loader.py:521`,
usata oggi dal planner a 3 strati): estrarre `persons` solo su query-foto,
`dates` solo su query con marca temporale, ecc. — la sezione-entità del prompt si
carica per object/segnale, non sempre. Mitiga la crescita del prompt (vedi
Latency). Selezione lessicale leggera a monte (NON un'altra call LLM: §I nota la
circolarità della selezione-via-intent).

## Alternatives considered

- **Status quo (split sparsi + regex + proposer indovina).** Zero lavoro, ma il
  costo marginale di ogni executor resta alto, §E.2 resta bloccata (regex
  ingenuo), i misroute del proposer continuano. È il problema, non la soluzione.

- **Solo §E.2 e §E.3 come due fix separati.** Più rapido a breve. Ma counts e
  persons hanno la STESSA forma (valore→arg): due meccanismi gemelli divergenti, e
  il terzo tipo (date) riapre la questione fra un mese. Rifiutato: duplicazione
  garantita.

- **NER per-executor generalizzata (split condivisi in un helper, ma a
  execution-time).** Toglie la duplicazione di codice ma NON i costi: la NER gira
  comunque ad ogni chiamata executor, contro un `PersonsRegistry` statico, e il
  proposer continua a indovinare gli args (la NER tardiva non informa il routing).
  Rifiutato: non risolve il misroute, non è multilingue senza LLM.

- **NLU completa a monte + registro entità→arg (scelta).** Una NER, una volta,
  che informa sia il routing (entità→arg deterministico) sia gli executor (args
  già risolti). Costo: prompt intent più grande (→ §I dinamico) e un registro da
  mantenere. Beneficio: estensibilità a una riga, §E.2 sbloccata correttamente,
  meno misroute. È la direzione indicata.

## Consequences

### Latency — DA MISURARE prima di accettare (gate, vincolo Roberto)

L'ADR è `proposed`: l'accettazione è subordinata a una misura, non a una stima.
Tre domande, da rispondere con numeri reali sul corpus gold (.33, Qwen 3.6
35B-A3B, baseline attuale ~370ms/query):

- **(a) costo aggiunto** alla call intent da un prompt+output più grande (sezione
  entità). Misurare con prompt STATICO-pieno e con prompt DINAMICO (§I).
- **(b) risparmio** togliendo gli split per-executor (NER a execution-time) +
  riducendo i misroute/retry del proposer (oggi un misroute = una pipeline
  rifiutata + ri-proposta).
- **(c) net** per-turno: neutro/positivo? Soglia di accettazione: il net NON deve
  peggiorare la latency di turno mediana; se (a) > (b) a prompt statico, il
  dinamico (§I) è prerequisito, non opzionale.

### Cosa cambia

- `runtime/intent_extractor.py` + `runtime/prompts/{it,en}/intent_extractor.j2`:
  estrazione entità additiva (costruisce su `cc6eee9`).
- `runtime/engine/types.py`: `Intent.entities` (assorbe `Intent.persons`).
- `runtime/engine/dispatch.py`: `_apply_entities_clause` (generalizza
  `_apply_persons_clause`) + il registro.
- `executors/find_images_indices/find_images_indices.py`: rimozione
  `_split_temporal_from_query`/`_split_persons_from_query`; consuma entità
  risolte; re-sign §7.10.
- §E.2 (counts→max_results) e §E.3 (persons→names) chiusi dallo STESSO
  meccanismo; 0119-bis migrato nel registro.

### Rischi e mitigazioni

- **Over-extraction LLM** (classifica come entità ciò che non lo è): vincolare i
  TIPI nel prompt (definitional, few-shot di output strutturato §6.1), non i
  valori. Guard a valle: iniezione solo se lo schema dell'executor ha l'arg.
- **Crescita del prompt** → §I (compattazione + caricamento dinamico per
  object/segnale). Prerequisito se (a)>(b).
- **Liste statiche di rientro** (anti-pattern): VIETATE. Il registro mappa
  TIPI→arg, non valori→referenti. La risoluzione valore→referente (slug, finestra)
  è codice deterministico, non enumerazione.
- **Regressione verb/object**: additivo per costruzione; il bench
  `tests/benchmarks/intent_accuracy_bench.py` resta gate (25/25), esteso con assert
  sull'estrazione entità (gold con `entities` attese), verb/object invariati.

### Lavoro generato (coda §J)

1. Misura latency (a)/(b)/(c) — gate di accettazione di questa ADR.
2. Design del registro entità→arg + lista iniziale `(object × tipo)`.
3. Migrazione `cc6eee9` (persons) in `entities.persons` + chiusura gap compound B
   (collasso find_images duplicati in `_apply_entities_clause`, decisione
   pregressa 14/6).
4. §I prompt dinamico per la sezione-entità.
5. Estensione bench gold con entità attese.
