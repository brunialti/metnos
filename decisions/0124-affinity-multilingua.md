---
id: 0124
title: Affinity multilingua — dict expansion (opzione D) come schema canonico
date: 2026-05-12
status: proposed
area: prefilter | naming
related:
  - 0092  # prompt-as-data + multilingua
  - 0114  # synth admission policy (L1 vocab semantic gate, ancora da scrivere)
complements:
  - 0092  # estende §G «affinity non si traduce» con un meccanismo di
          # espansione morfologica/sinonimica deterministica
modifies:
  - 0092  # §G «flat list invariata» resta valida ma viene affiancata da una
          # tabella di lemmas condivisa fra executor
---

## Context

Il prefilter (`runtime/prefilter.py::affinity_score`) usa token matching
exact-substring sulla lista `affinity = [...]` del manifest TOML. Lista
flat IT+EN mixed, ~10-15 termini per executor (ADR 0092 Phase 4 §G del
5/5/2026: «affinity tags non sono testo prompt, non vanno tradotti»).

Trigger 11/5/2026: turno «che appuntamento ho domani» ha fallito routing
perche' la affinity di `read_events` conteneva solo il plurale
«appuntamenti», non il singolare. Fix tampone: ampliata
`_AFFINITY_BY_OBJ` in `runtime/skill_codegen.py` con sing+plur IT+EN.
Stessa giornata, secondo miss: «scadenze della settimana» → top-1
`read_messages` invece di `read_events` perche' la affinity di
`read_messages` contiene «settimana» e quella di `read_events` no.

Stato del corpus al 12/5/2026 (`~/.local/share/metnos/turns/*.jsonl`,
ultimi 30 giorni, 2715 query analizzate via euristica stop-words
deterministica):

| lang     | count | %      |
|----------|------:|-------:|
| IT       | 2329  | 85.8%  |
| EN       |   82  |  3.0%  |
| mixed    |   36  |  1.3%  |
| unknown  |  268  |  9.9%  |

L'inglese e' marginale (3%), il misto trascurabile (1.3%), gli «unknown»
sono in larga parte query brevi senza stop-words (es. «ps», «inbox»,
«CI SEI?»). 82 manifest TOML in catalogo (50 handcrafted in
`/opt/myclaw/executors/` + 32 imported in `~/.local/share/metnos/
executors/_imports/`).

Il prefilter e' un BoW deterministico (§7.9). Il segnale primario sono le
affinity tag. Senza una soluzione strutturale, ogni nuova lingua o ogni
sinonimo non previsto produce un miss di routing. Il fix tampone in
`skill_codegen` copre solo i 32 imported google-workspace; gli
handcrafted restano con curatela manuale.

Quattro opzioni discusse nel TODO:

- **A** flat list (status quo): semplice ma cresce O(N_lingue × N_termini),
  ogni miss richiede edit manuale del manifest e re-sign.
- **B** struct per-lang `affinity = {it: [...], en: [...]}`: chiaro ma
  raddoppia la dimensione dello schema, il prefilter deve detect lingua
  query, 82 manifest da migrare, breaking change.
- **C** lemmatize a runtime (simplemma/snowball): schema flat invariato,
  copre sing/plur/coniugazioni, dipendenza esterna ~70MB (simplemma con
  data IT+EN+FR+ES+DE), latenza ~50us/query.
- **D** dict lemmas esterno (`runtime/affinity_lemmas.json`): tabella
  curata `lemma → {varianti}` condivisa fra executor, prefilter espande
  i token query al match time. Schema flat invariato.

## Decision

**Adottiamo l'opzione D — dict lemmas esterno + query expansion.**

Schema canonico:

- File JSON `runtime/data/affinity_lemmas.json` con shape:
  ```json
  {
    "appuntamento": ["appuntamento", "appuntamenti", "appointment",
                     "appointments"],
    "agenda":       ["agenda", "agende", "schedule", "schedules"],
    "evento":       ["evento", "eventi", "event", "events"],
    "messaggio":    ["messaggio", "messaggi", "message", "messages",
                     "mail", "email", "posta"],
    ...
  }
  ```
- Loader `runtime/affinity_lemmas.py` precomputa al boot la mappa
  inversa `D_TOKEN_TO_LEMMA: dict[str, set[str]]` (O(1) lookup).
- `affinity_score` in `runtime/prefilter.py` espande i token della query
  via `D_TOKEN_TO_LEMMA` prima del match con le affinity tag dei manifest.
  La struttura del manifest resta INVARIATA (flat list IT+EN).
- Estensione a una nuova lingua = una passata di traduzione del file
  JSON (LLM-assisted offline, no obbligo di toccare ogni manifest).

Bench su 50 query rappresentative (10 IT morfologiche, 10 EN, 10 IT
sinonimi rari, 10 IT-EN mix, 10 edge), 5 executor reali (`find_files`,
`read_messages`, `read_events`, `get_processes`, `find_urls`):

| opzione                          |  n | top-1 acc | false-pos | tempo |
|----------------------------------|---:|----------:|----------:|------:|
| A (flat — status quo)            | 50 |     80.0% |      8.0% | 20us  |
| B (per-lang struct)              | 50 |     80.0% |     10.0% | 27us  |
| C (lemmatize regex fallback)     | 50 |     82.0% |      8.0% | 41us  |
| **D (dict expansion)**           | 50 | **92.0%** |  **2.0%** | 21us  |

D ha la migliore accuracy con il false-positive rate piu' basso e
overhead trascurabile (+1us su 20us baseline = +5%). Nessuna dipendenza
esterna nuova. Compatibile §7.9.

Razionale §7.2 (KISS): la lista flat resta nei manifest, il dizionario
e' UN file JSON centrale. Aggiungere una variante sinonimica futura =
edit di una riga, non 82 manifest. La «cattura dei sinonimi» e' un
problema globale di prodotto, non di ogni executor; centralizzarlo
elimina lo §1.5 drift («single point of failure: tutte le 21 imported
dipendono dalla curatela di `_AFFINITY_BY_OBJ`» nel TODO).

## Alternatives considered

**Opzione A (flat — status quo).** Pro: zero change. Contro: ogni nuova
lingua moltiplica i token per executor (a 5 lingue: lista da 50+ entry
da curare per ognuno degli 82 manifest = ~4100 token totali). Il fix
tampone in `_AFFINITY_BY_OBJ` (skill_codegen) copre solo gli imported.
Drift inevitabile.

**Opzione B (per-lang struct).** Pro: schema esplicito, separazione
delle responsabilita' per lingua, integra naturalmente con
`metnos-prompts add-language` (ADR 0092 Phase 4). Contro: bench mostra
top-1 acc invariata (80%) e false-positive piu' alto (10% vs 8%),
perche' la lingua va detected sulla query (euristica fragile su token
brevi tipo «inbox», «ps»). Migrazione costosa: 82 manifest, breaking
change su `loader.py` + `prefilter.py`. Non risolve sinonimi rari
all'interno della stessa lingua («cosa devo fare domani» resta miss
perche' nessuna affinity contiene «fare»/«devo»).

**Opzione C (lemmatize a runtime).** Pro: schema invariato, copre
morfologia automaticamente. Contro: dipendenza esterna nuova
(simplemma ~70MB con language data IT+EN+FR+ES+DE, oppure
nltk+snowball). Latenza 2× (40us vs 20us). Non risolve sinonimi
non-morfologici («incontri» vs «meeting», «scadenze» vs «appuntamento»).
Bench mostra +2% accuracy (82% vs 80%) — guadagno marginale per costo
non marginale. Fallback regex stemmer e' fragile e copre solo IT
morfologico semplice.

**Opzione D (dict lemmas) — scelta.** Centralizza il problema in un
unico artefatto (`runtime/data/affinity_lemmas.json`) leggibile,
versionabile, estendibile. Risolve sia varianti morfologiche
(plurale/genere) sia sinonimi (incontro = meeting = riunione). Bench
+12% accuracy (92% vs 80%) con -75% false-positive rate (2% vs 8%) e
overhead 5%. Limite noto: il file e' una nuova superficie da curare —
mitigato dal fatto che la curatela e' globale (un sinonimo aggiunto
beneficia tutti gli executor).

## Consequences

**Cosa cambia subito** (implementazione, sprint dedicato 1-2 giorni):

- Nuovo file `runtime/data/affinity_lemmas.json` con bootstrap
  iniziale di ~30 lemmas coprendo i 16 OBJECT canonici + i verbi piu'
  usati (`trova`/`leggi`/`scrivi`/`mostra`/`cancella`/`manda`).
- Nuovo modulo `runtime/affinity_lemmas.py` (~60 LOC): caricatore +
  mappa inversa + funzione `expand_tokens(tokens) -> set[str]`.
- Modifica `runtime/prefilter.py::affinity_score`: aggiungere call a
  `expand_tokens(query_tokens)` prima del match. Cap di sicurezza:
  espansione max 10× del set originale (anti-explosion in caso di
  errori di curatela).
- Test `runtime/tests/test_affinity_lemmas.py`: 8-10 case su miss
  storici del corpus turn JSONL (appuntamento singolare, scadenze,
  incontri, posta, ps, inbox, cosa devo fare domani).
- Aggiornare smoke battery (`runtime/smoke.py`) con 3 case anti-regressione
  sui pattern morfologici live (singolare/plurale IT, EN dominio events).
- ADR 0092 Phase 4 §G: annotare «modificata da 0124 — flat invariata,
  affianca dict lemmas centrale».

**Cosa diventa piu' facile**:

- Aggiungere una nuova lingua FR/ES/DE: una sola passata sul dict
  lemmas (~30 lemmas × 5 varianti = 150 entry), no edit dei manifest.
- Cattura sinonimi colloquiali: un PR su un file JSON, beneficia tutti
  gli executor.
- Imported skill non hanno piu' bisogno di `_AFFINITY_BY_OBJ` in
  `skill_codegen.py`: la curatela vive nel dict lemmas centrale.

**Cosa diventa piu' costoso**:

- Una nuova superficie da mantenere (il dict JSON). Si attenua con
  ownership unica (`runtime/data/`) e revisione tramite PR — niente
  scattering across 82 manifest.

**Porte chiuse**:

- Opzione B (per-lang struct nei manifest) sostanzialmente esclusa: ha
  metriche peggiori e costo migration alto. Se in futuro >20% del corpus
  diventa non-IT, riapriremo.

**Porte aperte**:

- L1 vocab semantic gate (ADR 0114 sprint futuro): il dict lemmas e' la
  prima incarnazione concreta di un «dizionario canonico per i 22 verbi
  del vocab chiuso». Connessione naturale.
- Soluzione strutturale «BGE-M3 embedding match su description multilingua»
  (TODO 11/5 riflessione strutturale): resta valida come prossima
  evoluzione, ma il dict lemmas e' il passo intermedio low-risk a
  determinismo §7.9 puro. Quando si introdurra' BGE-M3 come segnale
  primario, il dict lemmas restera' valido come tie-break secondario
  exact-match.

**Status**: proposed, in attesa di approvazione Roberto. Sprint
implementazione consigliato dopo TODO VERY HIGH «re-ingegnerizzare
prompt PLANNER» (modularizzazione per sezioni ambito).
