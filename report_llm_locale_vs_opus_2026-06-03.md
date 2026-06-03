# Report — LLM locale (Qwen3.6) vs Opus 4.7 a pagamento, per la produzione Metnos

> Data: 2026-06-03 · HW: Strix Halo gfx1151 / Vulkan · llama.cpp build-mtp (HEAD con MTP)
> Bench su workload reali Metnos: **synthesizer** (codegen executor), **autodiagnosi** (stage6 verify), **supporto GitHub** (consult_frontier).
> Artefatti: `/opt/suprastructure/data/health_reports/mtp_loadtest_20260603_0510*`.

## TL;DR
- **Sì alla sostituzione locale per synthesizer + autodiagnosi**, con **un solo modello: Qwen3.6-35B-A3B (MoE)** + MTP.
- **No alla sostituzione sul supporto GitHub agentico**: lì **Opus resta**. I locali leggono il codice giusto ma non riconoscono i bug sottili in un codebase grande.
- Mossa pulita: **Qwen3.6-35B-A3B MoE come modello unico** che rimpiazza Gemma su `:8080` (è più veloce) **ed** è il tier `wise` di Metnos → meno costo, meno complessità, un solo KV. Opus solo come `frontier` per i casi GitHub difficili.

## Dati misurati

### Velocità (decode tok/s, best n_max, gfx1151)
| modello | tipo | baseline → spec | note |
|---|---|---|---|
| **Qwen3.6-35B-A3B** | MoE (3B attivi) | 60 → **~88–90** (MTP 1.3–1.5×) | il più veloce; MTP NON regredisce su gfx1151 (a differenza di #23011 su Metal) |
| Qwen3.6-27B | dense | 12 → ~26 (MTP ~2×) | % alta ma lento in assoluto |
| Gemma 31B | dense | 10 → ~17 (classic E2B) | n=2 ottimo, n≥6 collassa |
| **Gemma 26B-A4B** (PRODUZIONE) | MoE (4B attivi) | **~48 baseline → ~37 con E2B** | ⚠️ spec REGREDISCE (0.77×): overhead drafter > beneficio su MoE veloce |
| **Opus 4.7** (paid) | — | 49–67 tok/s + ~9s rete | costo $0.02–0.07/call |

I **dense non raggiungono la velocità di un MoE** (calcolano tutta la rete per token): fuori dal ruolo di modello unico veloce. **Misura testa-a-testa (2026-06-03)**: Qwen 35B-A3B MoE ~88 tok/s (MTP) vs Gemma 26B-A4B MoE **~48 baseline / ~37 con drafter E2B** → **Qwen ~1.8× più veloce del baseline Gemma, ~2.4× di Gemma-come-gira-in-prod**.

**🔴 Finding collaterale**: in produzione Gemma 26B gira col drafter E2B classic-spec (`--spec-draft-n-max 4`), che la **rallenta** (48→37). Su un MoE già veloce l'overhead del drafter esterno supera il beneficio (≠ MTP, che è baked-in senza overhead). **Azione suggerita**: verificare/rimuovere il drafter dalla Gemma di produzione → ~+30% gratis (~48 vs ~37). _Caveat: misura a ctx 8192/batch default, non con tutta la tuning prod (`-b 4096 -ub 256 --cache-reuse 256`); pattern robusto, numero esatto può variare._

### Qualità (output identico tra baseline/spec — spec è lossless)
- **single-call** (file/spec dati): Qwen 35B-A3B, 27B e Gemma **≈ Opus** — autodiag verdetto corretto, github root-cause corretta, codegen executor valido.
- **agentico FACILE** (comprensione, file piccolo): tutti ✅.
- **agentico HARD** (bug reale `find_images_indices.py:1100`, `path` come identità non-univoca): **35B-A3B e 27B FALLITI** — a 64K trovano e *leggono* il blocco buggato ma **non lo riconoscono** né concludono (24 iterazioni esaurite). Il dense non fa meglio del MoE.

## Vincoli memoria / KV (128GB unified, heap GPU Vulkan ~84GB)
- Collo di bottiglia = **KV, non i pesi**. Gemma prod gira `-c 131072 --parallel 2` (~64K/slot: slot0 voce, slot1 Metnos) e usa ~35GB di heap.
- Qwen 35B-A3B alla stessa config (`131072` / 2 slot): pesi 22GB + KV ~14GB ≈ **~36GB** → **ci sta** nei 84GB come **rimpiazzo** di Gemma (è uno swap, non un'aggiunta).
- ⚠️ Metnos richiede ≥64K e l'investigazione agentica satura il contesto: a 32K va in overflow (HTTP 400) → servono ≥64K (coincide con il fabbisogno Metnos).
- Non tenere **due** modelli giganti a ctx massimo insieme.

## Raccomandazioni operative
1. **Modello unico locale = Qwen3.6-35B-A3B-MTP (MoE)**. Config suggerita su `:8080`:
   `-c 131072 --parallel 2 --spec-type draft-mtp --spec-draft-n-max 3 --cache-type-k f16 --cache-type-v f16` (slot0 voce, slot1 Metnos).
2. **Tier Metnos**: `wise` → locale (synthesizer/autodiag). **`frontier` resta Opus 4.7** ma **solo** per `consult_frontier` su GitHub / ragionamento ad alto rischio.
3. **NON** instradare il supporto GitHub agentico sul locale: il function-calling regge (naviga, grep), ma non riconosce i bug sottili nel grande → falsi negativi/non-diagnosi.

## Caveat e prossimi passi (prima di committare lo swap)
- Validare **MTP a 131072 / 2-slot** in produzione (testato a 32–64K / 1-slot) + **misura diretta velocità** Gemma-26B-prod vs Qwen-35B-A3B sullo stesso prompt.
- Agentico hard: **1 solo caso di bug** testato; il risultato potrebbe migliorare con prompt più mirato / più iterazioni, ma "legge il bug e non lo riconosce" su **entrambi** i modelli è un segnale forte. Per confidenza: più casi + eventuale tool-loop GitHub live.
- Qwen3.6-27B-dense quality: confermata buona su single-call (autodiag/github corretti); resta lento.

---
*Generato durante la sessione di bench del 2026-06-03. Memory di riferimento: `metnos-frontier-local-eval`, `qwen36-mtp-mamba-hybrid`, `llm-slot-pinning`.*
