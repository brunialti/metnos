---
id: 0139
title: LLM query expansion language-sensitive (find_images_indices)
date: 2026-05-15
status: accepted
area: executor | runtime
related:
  - 0117  # unified image enrichment index
  - 0134  # semantic affinity fallback BGE-M3
supersedes: []
modifies:
  - 0117  # nuova strategia di filter per query brevi mono-token
---

## Context

`find_images_indices` (ADR 0117 unified image enrichment index) filtra
le foto del corpus combinando `name`/`names` (face match ArcFace) +
`query_text` (BGE-M3 cosine vs `embeddings_text.npy`). Il filtro testo
funzionava bene su query LUNGHE («foto di gruppo al mare in
spiaggia con bambini») dove il cosine BGE-M3 e' discriminante.

Casi degeneri su query brevi mono-token (`mare`, `neve`):

- BGE-M3 corpus-token expansion: si arricchiva la query con i top-K
  token piu' simili nel corpus → falsi positivi per **prefix-bias**.
  Esempio reale: `mare` → corpus expansion `["amare", "mappe",
  "mercato", "morte", "marrone", "marche", "marziale"]` (cosine
  0.75-0.85 puramente per prefisso o vicinanza embedding generico).
- Bug live test 1 (Roberto, 15/5/2026): «Quante foto di Matteo al
  mare ho dal 2020 al 2024?» → 100 foto, molte di Parigi/casa
  (face=Matteo, text=mare a basso score ma sommato a face=1.0 →
  ranking-boost invece di filtro AND).

Strategie esistenti:
1. Soglia `text_score_min` (commit `211dabb`): AND stretto su contributo
   testuale isolato. Risolve la degeneration ranking→filtro ma sulla
   query NON espansa (cosine `mare` isolato e' selettivo ma rigido).
2. Corpus BGE-M3 expansion: prefix-biased come sopra.

Serviva strategy 3: **query expansion semantica vera** (sinonimi
lessicali, non vicinanza embedding) language-sensitive.

## Decision

Funzione `_expand_query_via_llm(query, lang_hint=None)` in
`executors/find_images_indices/find_images_indices.py` (~70 LOC).

### Architettura

- **LLM tier middle** (Gemma 4 26B locale, fast tier insufficient su
  italiano/francese per qualita' sinonimi).
- **Prompt scritto in INGLESE** (lingua neutra per modello multilingua,
  riduce bias residuo language-locked).
- **6 few-shot examples** in IT/EN/FR per ancorare language fidelity
  («mare → mare, oceano, mar, marea, costa, ...» / «snow → snow,
  blizzard, snowfall, ...» / «neige → neige, flocon, poudreuse, ...»).
- **Output**: 8 sinonimi specifici, stesso linguaggio della query.
- **Cache disk indefinita**: `~/.cache/metnos/query_expansion_llm/
  <sha256(query)>.json`. Sinonimi lessicali sono stabili, no
  expiration. Atomic write tmp+rename.
- **Fallback graceful** (§2.8): LLM unreachable → strategy 2 corpus
  BGE-M3 → strategy 3 hybrid BM25+cosine puro.

### Pipeline `_filter_unified` rivista

```
1. query_text presente?
   a. cache hit → usa expansion cached (0ms)
   b. cache miss → LLM call ~500ms → cache write
   c. LLM fail → corpus BGE-M3 expansion (legacy)
   d. tutto fail → no expansion, cosine raw query
2. BM25 + cosine sui sinonimi espansi + soglia text_score_min
3. AND con face match (se name/names)
```

### Risultati misurati (live)

Query «mare» italiano:
- Expansion: `["mare", "oceano", "mar", "marea", "costa",
  "litorale", "baia", "golfo", "acque"]`.
- 12 foto, tutte Matteo + tutte mare/piscina (precision ~100%).
- vs 5 foto (filter hybrid puro senza expansion).
- vs 117 foto (cosine 0.40 unfiltered, includeva falsi positivi).

Language sensitivity 3 lingue, sample:
- `mare` (it) → italiano puro.
- `sea` (en) → sea, ocean, tide, shore, coast, marina, waves, water.
- `snow` (en) → snow, blizzard, snowfall, snowflake, ...
- `neige` (fr) → neige, flocon, poudreuse, blizzard, givre, ...

## Alternatives considered

**(a) WordNet/MultiWordNet local lookup**: dipendenze hard (NLTK +
corpus pesante). Copertura debole su IT/FR. Niente domain-specific
(`piscina` ↔ `acquatico` non collegati).

**(b) Estendere BGE-M3 corpus expansion con `min_cosine_threshold`**:
non risolve prefix-bias (cosine 0.85 anche per "mappe" vs "mare"
puro prefisso).

**(c) LLM expansion sincrona per query**: scelto. Latency 500ms cold
una volta per chiave (cached forever). Trade-off accettabile data
la rarita' di query brevi mono-token.

**(d) Cache TTL fissa**: sinonimi lessicali NON sono time-sensitive
(no «meteo», no «notizie»). TTL infinito + manual invalidate (rm
del file) e' coerente con la natura del dato.

## Consequences

- Modifica ADR 0117 §filter_unified pipeline (3 layer expansion).
- Costo storage cache trascurabile (~200 byte/query × ~10-100 query
  brevi/utente attivo = ~20KB lifetime).
- Costo LLM cold: 500ms ogni nuova query breve (~0 dopo cache hit).
  Su query lunghe `_filter_unified` continua a usare cosine raw.
- Generalizzabile §7.3: pattern `_expand_query_via_llm` puo' essere
  esposto come helper riusabile da altri executor search (futuro
  `find_messages` semantic, `find_events_topical`).

## Notes

- LLM tier middle scelto sopra fast: Gemma 4 26B emette sinonimi
  language-faithful con high accuracy; tier fast (qwen3:8b)
  contaminava IT con EN nei test informali.
- Prompt in inglese (non in `<lang>`): paradossale ma effettivo —
  modelli multilingua hanno la knowledge generale in EN piu' densa,
  e i few-shot localizzati ancorano la lingua di output. Prompt IT
  generava sinonimi piu' poveri in fr/en (modello tirato in IT
  dall'instruction).
- Estensibile a `find_messages` o `find_urls` per query brevi
  ricorrenti, con la stessa cache disk.
