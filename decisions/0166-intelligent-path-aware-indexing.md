# ADR 0166 — Indicizzazione immagini path-aware (contesto di cartella via LLM)

**Date**: 2026-06-01
**Status**: proposed
**Related**: ADR 0117 (unified image enrichment index), ADR 0086 (indici di dominio), ADR 0134 (BGE-M3 semantic fallback), ADR 0139 (LLM query expansion), ADR 0092 (prompt multilingua), §2.4 (robustezza NL→det), §7.3 (generale), §7.9 (codice det > LLM)

## Context

Query live 1/6/2026 "Cerca foto dei viaggi del 2016" → `find_images_indices(query_text="viaggi 2016", match_all=true)` → `describe_entries` → risposta "Non sono state trovate foto relative a viaggi del 2016; solo documenti, elenchi e un distintivo". User ✗.

Falso: il corpus 2016 È PIENO di viaggi — Parigi (89 foto), Malta (34), Palermo (37), Cammino degli Dei (30), gita montagna (21), Circeo (21), Como (6), Roseto degli Abruzzi (166). Sono organizzati in cartelle NOMINATE per viaggio. Due difetti distinti:

1. **Parse**: l'anno "2016" resta in `query_text` invece di diventare `time_window`. L'embedding di "viaggi 2016" è dominato dal token "2016" → vincono i documenti le cui caption CITANO "2016" (cerimonie, certificati). 957 "match", tutti documenti in cima.

2. **Recall (il problema profondo)**: anche col parse corretto (`query_text='viaggi', time_window='2016'`) la ricerca rendeva **0**. Misure decisive (su dati reali 2016):

   | segnale per separare viaggi↔non-viaggi | gap medio | esito |
   |---|---|---|
   | coseno caption "viaggi" | **+0.011** | inutile |
   | coseno centroide query-expansion | +0.013 | inutile |
   | path nell'embedding (token grezzi) | +0.008 | aiuta query di LUOGO, non "viaggi" |
   | GPS lontano-da-casa | — | scartato: **6%** copertura corpus |
   | **classificazione LLM del nome-cartella** | — | **100%** accurata |

   Causa: BGE-M3 embedda la SCENA ("una vetrata gotica") e "viaggi" è una categoria ASTRATTA senza firma separabile nelle caption. Il VLM-7B, pur ricevendo il path come hint (`_vlm_prompt`), lo SCARTA (la foto di Notre-Dame resta "vetrata gotica", niente "Parigi"). La conoscenza "Parigi→viaggio" ce l'ha solo un LLM con conoscenza del mondo; un gazetteer place→trip sarebbe hardcoding (§7.3) e non multilingue.

## Decision

**Indicizzazione intelligente path-aware**: al build, ogni CARTELLA-unica viene classificata da un LLM-testo (Gemma 26B middle) in `categoria ∈ {VIAGGIO, EVENTO, PERSONE, DOCUMENTI, ALTRO}` + `luogo`. Da questa si assembla deterministicamente una **frase di contesto discriminante** (solo VIAGGIO contiene "viaggio"/"trip"), che viene **fusa nell'embedding testuale**: `embedding_text = BGE-M3(path_context + ". " + description)`. Il `path_context` è salvato nell'entry (cercabile anche via BM25).

- `executors/create_images_indices/create_images_indices.py`: `folder_path_context(parent_dir, lang)` (API pubblica) → `_classify_folder_label` (LLM strutturato, cache per label) + `_assemble_path_context` (template per-lingua). Wired nei due punti di embed di `_build_unified`. Una chiamata LLM **per cartella-unica** (non per foto): 796 cartelle sul corpus attuale.
- `executors/find_images_indices/find_images_indices.py`:
  - §2.4 **parse**: `_split_temporal_from_query` estrae anno/mese-anno da `query_text` → `time_window` prima dello scoring (l'anno è un filtro, non contenuto).
  - **escape semantico** nel filtro: nel branch query-expansion (`bm25>0 AND cos≥0.25`) si aggiunge `OR cos≥_COSINE_STRONG` — coerente col branch non-expanded. Necessario perché "viaggio"≠"viaggi" lessicalmente (BM25=0) ma il coseno separa.
  - `path_context` aggiunto ai `doc_terms` BM25 (query di luogo "Malta" matchano).
- **Multilingue** (§2.2, ADR 0092): l'enum di categoria è universale; il prompt di classificazione e il template di framing sono per-lingua (IT+EN inline, fallback EN), guidati da `config.DEFAULT_LANG`. Per Metnos distribuito ogni istanza arricchisce nella propria lingua.

**Validazione** (prototipo su 2016, modello reale): "viaggi" → **TOP-100 = 93% viaggi** (era 0%: top-100 tutti documenti). A soglia 0.55 prec 74%/recall 91%, a 0.57 prec 90%/recall 71%. Il describe ora descrive Parigi/Malta/Circeo → risposta vera.

## Alternatives considered

1. **Ri-caption VLM con path nel prompt**. Rifiutata: il VLM-7B integra male gli hint testuali (verificato: scarta "Parigi") e ri-eseguirlo su 31445 foto è costosissimo. La cartella è lo STESSO contesto per tutte le foto del folder → classificarla per-foto è spreco.
2. **Path-token grezzi nell'embedding**. Rifiutata: gap +0.008 (insufficiente per "viaggi"); BGE-M3 non lega "parigi"→"viaggi". Aiuta solo query di luogo esplicite.
3. **GPS lontano-da-casa**. Rifiutata: 6% copertura corpus (DSLR/scansioni/vecchie foto senza GPS); presente sia in viaggi che a casa.
4. **Gazetteer/dizionario place→categoria**. Rifiutata: hardcoding (§7.3), non multilingue, non manutenibile, non copre eventi/persone.
5. **Free-form framing LLM** (una frase libera per cartella). Rifiutata: inaffidabile (proto: echeggiava il nome cartella per ~25%). L'output STRUTTURATO (enum+luogo) + assembly deterministico è robusto.

## Consequences

- **Nuovo campo entry** `path_context` (schema unified). Retro-compatibile: assente sulle entry vecchie finché non ri-embeddate (default "").
- **Re-embed retroattivo** dell'indice esistente: `jobs/reembed_path_context.py` (riusa le caption VLM, ri-embedda solo il testo; cache cartelle persistente su disco `folder_context_cache.json`; backup + scrittura atomica). Costo: 796 classificazioni LLM (una-tantum, cache) + ~31k embed BGE-M3 (veloce). NON ri-esegue VLM/face/clip.
- **Build futuri/distribuiti**: arricchimento automatico, per-lingua, generale per qualsiasi struttura di cartelle utente — "indicizzazione intelligente" come da requisito.
- **Apre** query di categoria astratta su immagini (viaggi, eventi, documenti) prima impossibili. **Chiude** il falso-negativo §2.8 "nessuna foto" su corpus ricco.
- Costo di un build incrementale: +1 chiamata LLM per ogni cartella NUOVA (cache evita ripetizioni).

## Implementation status

Codice implementato 1/6/2026 (parse-fix + escape filtro + helper indexer + script re-embed); verificato in prototipo (93% top-100). **PENDENTE**: esecuzione `reembed_path_context.py` sul corpus live (carica il llama-server ~20-40 min → da fare off-peak per non disturbare il servizio, §8.6) + re-sign dei due executor + restart `metnos-http` + verifica live 2× (§8.5). Alla conferma live lo status passa ad `accepted`.
