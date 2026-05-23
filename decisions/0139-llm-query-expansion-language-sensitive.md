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
1. Soglia `text_score_min` (commit `211dabb`): AND stretto sul
   contributo testuale isolato. Risolve la degenerazione
   ranking→filtro, ma sulla query NON espansa (`mare` isolato e'
   selettivo ma rigido).
2. Corpus BGE-M3 expansion: prefix-biased come sopra.

Serviva strategy 3: **query expansion semantica vera** (sinonimi
lessicali, non vicinanza embedding) language-sensitive.

## Decision

Funzione `_expand_query_via_llm(query, lang_hint=None)` in
`executors/find_images_indices/find_images_indices.py` (~70 LOC).

### Architettura

- **LLM tier middle** (Gemma 4 26B locale: il tier fast non e'
  sufficiente su italiano/francese, i sinonimi escono troppo poveri).
- **Prompt scritto in INGLESE** (lingua neutra per il modello
  multilingua, riduce il bias residuo di lingua).
- **6 esempi few-shot** in IT/EN/FR per ancorare il modello alla
  lingua della query («mare → mare, oceano, mar, marea, costa, ...» /
  «snow → snow, blizzard, snowfall, ...» / «neige → neige, flocon,
  poudreuse, ...»).
- **Output**: 8 sinonimi specifici, nella stessa lingua della query.
- **Cache su disco senza scadenza**:
  `~/.cache/metnos/query_expansion_llm/<sha256(query)>.json`. I
  sinonimi lessicali sono stabili, l'invalidazione e' manuale (`rm`
  del file). Scrittura atomica tmp+rename.
- **Fallback ordinato** (§2.8): LLM non raggiungibile → strategia 2
  (corpus BGE-M3) → strategia 3 (BM25+cosine puro, senza espansione).

### Pipeline `_filter_unified` rivista

```
1. query_text presente?
   a. cache hit → usa l'espansione gia' in cache (0 ms)
   b. cache miss → chiamata LLM ~500 ms → scrivi in cache
   c. LLM non raggiungibile → corpus BGE-M3 (strategia precedente)
   d. tutto fallito → nessuna espansione, cosine sulla query grezza
2. BM25 + cosine sui sinonimi espansi, soglia `text_score_min`
3. AND con il match dei volti (se `name`/`names` e' presente)
```

### Risultati misurati (in produzione)

Query «mare» italiano:
- Espansione: `["mare", "oceano", "mar", "marea", "costa",
  "litorale", "baia", "golfo", "acque"]`.
- 12 foto restituite, tutte con Matteo, tutte mare o piscina
  (precision ~100%).
- A confronto: 5 foto senza espansione (filtro ibrido puro), 117
  foto con cosine 0.40 senza filtro (molti falsi positivi).

Aderenza alla lingua su 3 lingue, esempi:
- `mare` (it) → solo italiano.
- `sea` (en) → sea, ocean, tide, shore, coast, marina, waves, water.
- `snow` (en) → snow, blizzard, snowfall, snowflake, ...
- `neige` (fr) → neige, flocon, poudreuse, blizzard, givre, ...

## Alternatives considered

**(a) WordNet/MultiWordNet locale**: dipendenze pesanti (NLTK +
corpus). Copertura debole su IT/FR. Niente specificita' di dominio
(`piscina` ↔ `acquatico` non collegati).

**(b) Estendere l'espansione BGE-M3 con `min_cosine_threshold`**: non
risolve il bias di prefisso (cosine 0.85 anche fra "mappe" e "mare",
solo per prefisso comune).

**(c) Espansione LLM sincrona per query**: la scelta adottata. Latenza
di 500 ms al primo accesso, poi cache permanente. Compromesso
accettabile data la rarita' di query brevi mono-token.

**(d) Cache con TTL fisso**: i sinonimi lessicali NON sono legati al
tempo (non sono «meteo» o «notizie»). TTL infinito + invalidazione
manuale (`rm` del file) e' coerente con la natura del dato.

## Consequences

- Modifica la pipeline `_filter_unified` di ADR 0117 (tre livelli di
  espansione).
- Costo di storage della cache trascurabile (~200 byte per query × ~10-100
  query brevi per utente attivo = ~20 KB sul lungo periodo).
- Costo LLM al primo accesso: 500 ms per ogni nuova query breve, ~0 ms
  con la cache calda. Sulle query lunghe `_filter_unified` continua a
  usare il cosine grezzo.
- Generalizzabile (§7.3): il pattern `_expand_query_via_llm` puo'
  diventare un helper riusabile da altri executor di ricerca (in
  futuro `find_messages` semantico, `find_events_topical`).

## Notes

- Tier middle preferito al tier fast: Gemma 4 26B emette sinonimi
  fedeli alla lingua con buona accuratezza, mentre il tier fast
  (qwen3:8b) contaminava l'italiano con l'inglese nei test
  informali.
- Prompt in inglese, non in `<lang>`: la scelta sembra paradossale ma
  funziona. I modelli multilingua hanno conoscenza generale piu'
  densa in inglese, e i few-shot localizzati bastano ad ancorare la
  lingua di output. Un prompt scritto in italiano tirava il modello
  verso l'italiano anche su query inglesi o francesi.
- Estendibile a `find_messages` o `find_urls` per query brevi
  ricorrenti, con la stessa cache su disco.
