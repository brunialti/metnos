# Report — Headroom (compressione contesto input) per le chiamate LLM frontier (Opus) di Metnos

> Data: 2026-06-15 · Valutazione di adozione (no decisione presa) · Complementare a `report_llm_locale_vs_opus_2026-06-03`
> Oggetto: Headroom (OSS, T. Chopra/Netflix, rilascio gen 2026) — *context-optimization layer* che comprime l'INPUT prima del provider LLM.
> Stato telemetria: **LIVE** dal commit `942096c` (metering attivo + `runtime/llm_pricing.py` fonte unica). Vedi §7.
> Fonti esterne: Show HN, opensourceforu, substack autore (giu 2026). I numeri di compressione = claim autore/stampa, da validare sul nostro mix.

## TL;DR

- **Headroom comprime solo l'INPUT.** Su Opus l'output costa **$25/Mtok**, l'input **$5/Mtok** (5×) — tariffe da `runtime/llm_pricing.py` (consolidata 15/6). Quindi il risparmio è limitato dalla quota-input della spesa: serve workload **input-heavy** per avere ROI.
- **Fit per use-case Metnos:**
  - `consult_frontier` (supporto GitHub) → **ALTO**. Input 100–300 KB dominato da file/codice/JSON/tool-output ridondante → comprimibile **60–80%**. È *esattamente* il carico che il report 2026-06-03 ha deciso di **tenere su Opus**.
  - planner-fallback frontier (agent runtime) → **MEDIO**. System prompt ripetuto e comprimibile, ma query utente unica e corta.
  - output dei modelli e (eventuali) token vision → **NULLO** (Headroom non li tocca).
- ✅ **La telemetria ora c'è** (`llm_cost_sink` attivo): non servono più stime per il volume. Tra ~2 settimane avremo spesa e split input/output reali.
- ⚠️ **Spesa frontier in valore assoluto probabilmente piccola** (stima sotto: ~$50/mese input → risparmio Headroom ~$20–30/mese). Decidere sui numeri reali, non al buio.
- **Raccomandazione:** (1) raccogliere 2 settimane di telemetria; (2) valutare **prima** il **prompt caching Anthropic** (oggi assente) sul system prompt ripetuto — leva a rischio ~0; (3) **pilota Headroom solo su `consult_frontier`** con A/B di qualità; poi decidere.

## 1. Cosa è Headroom

Layer tra app e provider che **riscrive/comprime il contesto in input** (tool output, log, JSON, RAG, file di codice) in modo **reversibile** (es. 500 log → "487 passed, 2 failed", con possibilità per il modello di richiedere il dettaglio). Compressione dichiarata: **60–95%** su ridondante (log/JSON/codice), **20–30%** su prosa. Tre modalità: **libreria, proxy, MCP server**.

⚠️ Progetto giovane (gen 2026), terze parti, compressione *lossy-ma-reversibile*. Le % alte valgono su log/JSON, **non** automaticamente sul nostro mix.

## 2. Dove Metnos usa il frontier (mappa, evidence-backed)

Modello frontier configurato: **`claude-opus-4-7/4-8`** (`runtime/llm_router.py:110`). Tariffa (fonte unica `runtime/llm_pricing.py`): **$5 in / $25 out** per Mtok. *(consult_frontier ora usa `llm_pricing.cost_usd` — pricing coerente in tutto il codebase dopo `942096c`.)*

| # | Use-case | Dove | Forma input | Comprimibilità Headroom |
|---|---|---|---|---|
| 1 | **consult_frontier** — analisi GitHub/issue, loop tool read-only (Mode B) | `executors/consult_frontier/consult_frontier.py:449–470, 638–684` | `local_context.files` (fino 100 KB/file) + entries JSON + risultati tool (`github_read_file/search_code/list_dir`), cap `max_remote_bytes=500KB` | **ALTA** (codice/JSON/diff/log ridondanti); 60–80% su payload >100 KB |
| 2 | **planner fallback frontier** — disambiguazione/risoluzione tool quando il tier `middle` non basta | `runtime/agent_runtime.py:6936` | system planner (~15–25 KB, molto boilerplate ripetuto) + query utente (corta, unica) + 8–15 tool schema + history | **MEDIA** (system comprimibile/cacheabile; query no) |
| 3 | **planner retry con args alternativi** | `runtime/agent_runtime.py:7478` | come #2 + `_retry_hint` | **MEDIA** |

**Coerenza col report 2026-06-03:** synthesizer e autodiagnosi sono passati al **locale** (tier `wise`, Qwen3.6-35B-A3B) → non sono più frontier. Il carico che **resta** su Opus è il **supporto GitHub** (#1): è lì che Headroom ha il fit migliore. Interventi **allineati**, non alternativi.

## 3. Modello di costo (ora verificabile con la telemetria live)

Asimmetria chiave: **Headroom taglia solo l'input**.

```
risparmio ≈ N_call × input_tok_medi × $5/Mtok × quota_input_comprimibile × fattore_compressione
```

Niente sull'output ($25/Mtok) né su token vision.

**Scenario di sensibilità** (volumi = stima da code-path, **da sostituire con i dati di `llm_cost_sink` tra 2 settimane**):

| scenario | call/mese | input medi/call | costo input/mese | compressione efficace | risparmio/mese |
|---|---|---|---|---|---|
| conservativo | 200 (solo consult_frontier) | 50 K tok | ~$50 | 40% | **~$20** |
| centrale | 700 (consult + planner) | ~14 K tok | ~$49 | 45% | **~$22** |
| aggressivo | 700 | ~14 K tok | ~$49 | 65% | **~$32** |

> Ordine di grandezza atteso: **~$20–30/mese** di risparmio su una spesa-input frontier di **~$50/mese**. Piccolo in assoluto → **il valore vero resta lo spostamento su locale già fatto** (report 2026-06-03); Headroom è una rifinitura sul residuo Opus, da giustificare coi numeri reali.

## 4. Punto di integrazione (basso sforzo, chokepoint unico)

Tutte le chiamate Opus passano da **`runtime/llm_provider.py` → `AnthropicProvider.chat_with_tools()`** (build payload → `self._post()`). HTTP diretto (`urllib`), no SDK. È lo stesso punto dove ora è agganciato il metering (`llm_cost_sink`). Inserire Headroom **tra build del payload e POST** copre tutti gli use-case con ~15 righe (libreria), oppure come **proxy/MCP** sull'egress.

## 5. Leva complementare (probabilmente prioritaria): prompt caching Anthropic

**Oggi NON usato**: `AnthropicProvider` non setta `cache_control`. Il **system prompt del planner è quasi identico tra le chiamate** → candidato ideale per il **prompt caching** (cache read ~0.1×), a rischio qualità ~0.

- Caching → abbatte il costo del **system ripetuto** (#2/#3).
- Headroom → abbatte l'**input unico e ridondante** (#1: file/codice/tool-output).
- **Ortogonali e cumulabili.** Headroom dichiara di stabilizzare il dinamico per **migliorare gli hit di cache**.

👉 Per rapporto rischio/beneficio, **valutare il caching per primo**, Headroom per `consult_frontier`.

## 6. Rischi & mitigazioni

| Rischio | Mitigazione |
|---|---|
| Compressione *lossy*: su analisi codice load-bearing può perdere dettagli che cambiano la diagnosi | **A/B obbligatorio**: stesso payload con/senza Headroom su 30–50 casi reali GitHub; confronto verdetto/root-cause |
| Dipendenza terza parte giovane | Pin di versione; isolare come proxy/processo separato; fallback passthrough se giù |
| Latenza su payload grandi | `consult_frontier` è già ad alta latenza (Opus ~9 s rete) → overhead relativo marginale |
| ROI sopravvalutato | La telemetria live dice subito se la spesa è input- o output-heavy: se output-heavy → Headroom non conviene |

## 7. Telemetria (IMPLEMENTATA — commit `942096c`)

Metering attivo su ogni chiamata LLM (sink universale `llm_telemetry` → `runtime/llm_cost_sink.py`, auto-install da `llm_provider`). Scrive **solo metering** (niente prompt) in `data/telemetry/llm_usage.jsonl`: `ts, provider, model, kind, tier, in_tokens, out_tokens, cost_usd, latency_ms`. Costo via `llm_pricing.cost_usd`. Copre planner-fallback **e** `consult_frontier` (entrambi passano da `llm_provider`).

Lettura aggregata (dopo ~2 settimane):
```
python runtime/llm_cost_sink.py --provider anthropic --days 14
```
→ per (provider, model): chiamate, token in/out, **% input**, costo. Disattiva con `METNOS_LLM_COST_LOG=0`; path override `METNOS_LLM_COST_LOG_PATH`.

## 8. Raccomandazione & next step

1. ✅ **Telemetria frontier** — *fatto* (`942096c`). Lasciar girare **2 settimane** → spesa reale + split input/output per use-case.
2. **Abilitare prompt caching** sul system prompt del planner (intervento piccolo, rischio ~0) e misurarne l'effetto.
3. **Pilota Headroom su `consult_frontier`** (libreria nel chokepoint `AnthropicProvider`), con **A/B di qualità** e tracking token pre/post.
4. **Decidere** sui numeri reali: se la spesa Opus residua è input-heavy e l'A/B non degrada la qualità → estendere; altrimenti fermarsi al caching.

**Quando NON farlo:** se la telemetria mostra spesa Opus bassa in assoluto (poche decine di $/mese, come suggerisce la stima) o output-dominata → Headroom non vale la complessità.

## Appendice — assunzioni di stima (esplicite)

- Volumi (200–750 call/mese) = inferenza dai code-path, **da sostituire** con `llm_cost_sink`.
- Token/call: planner ~10–14 K input; `consult_frontier` ~50 K input (file fino 100 KB cap).
- Output ~0.5–2 K tok/call (non compresso da Headroom).
- Tariffa Opus: **$5 in / $25 out** per Mtok (fonte `runtime/llm_pricing.py`, 15/6).
- Compressione: estremi 20–30% (prosa) / 60–95% (codice/JSON/log) dai claim Headroom; "efficace" in tabella = media pesata prudenziale.
