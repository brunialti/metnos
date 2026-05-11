---
id: 0114
title: Synth admission policy 4 layers
date: 2026-05-08
status: accepted
area: synt
related:
  - 0072  # adaptive re-rank: pattern di guard al routing
  - 0086  # indici di dominio: simmetria handcrafted-vince
  - 0079  # GC synth in collisione (ADR di riferimento)
  - 0078  # admin UI: deve mostrare i synth rejected
complements:
  - 0079  # estende il pattern "rejected non distruttivo" a affinity_overlap +
          #   efficacy + semantic_drift
modifies: []
supersedes: []
---

## Context

L'8/5/2026 e' emerso un bug strutturale di routing: il synth executor
`find_texts` (lifecycle=active, signed) ha hijackato per giorni il
PLANNER per query web come "cerca su web ...". Diagnosi:

1. La sua `affinity` conteneva il catch-all `["cerca", "search", "find",
   "web", "google", ...]` con 5/8 termini in overlap diretto verso
   `find_urls` handcrafted.
2. La `description` recitava "motori di ricerca online" ma il `code`
   reale faceva tutt'altro (lookup su una tabella in-memory). Misalignment
   description ↔ code che la pipeline synth NON ha mai rilevato.
3. 7 chiamate a `find_texts` ritornavano sempre 0 entries (`success_rate ≈
   0`). Nessun ager le ha mai notate perche' i criteri esistenti
   (`apply_executor_ager`, ADR 0072) decay-ano per **inattivita'**, non
   per **inefficacia**.
4. La `smoke battery` (ADR 0072 §10.6) verificava `final_kind=="answer"` +
   regex su tool name, ma NON asseriva quale tool DOVREBBE essere il
   primo (regression canary mancante).

Il sistema ammetteva nuovi synth nel catalog senza alcun gate di:
(a) overlap affinity vs handcrafted, (b) tasso di successo nel tempo,
(c) coerenza description ↔ code, (d) smoke routing assertion.

## Decision

Quattro layer cumulativi di difesa, ognuno deterministico (§7.9) salvo
il L6 che usa LLM solo per il giudizio (mai per la decisione finale —
fail-safe = reject).

### Layer 2 — Affinity overlap guard al catalog load

In `runtime/loader.py::_check_affinity_overlap(catalog)`, eseguito
dopo il load completo e prima del GC. Pairwise Jaccard su affinity:
synth (manifest_path dentro `SYNTHESIZED_EXECUTORS_DIR`) con jaccard
>= 0.5 verso UN handcrafted (in `HANDCRAFTED_FAMILIES` lista canonica)
o un altro synth piu' vecchio (mtime manifest) e' rejected. Audit JSONL
in `~/.local/share/metnos/synth_audit/affinity_rejected.jsonl`. Catalog
mostra il rejected nell'admin UI tramite `catalog._rejected[]`.

### Layer 3 — Efficacy ager

In `runtime/executor_aging.py::apply_efficacy_ager(...)`. Sorgente:
turn JSONL log (`~/.local/share/metnos/turns/*.jsonl`) filtrato per
`chosen_tool`. Per ogni synth (source startswith `synth`) con
invocations >= 100:
- success_rate < 0.20 → deprecated
- success_rate < 0.05 dopo altre 30 invocations post-demotion → archived

Idempotente. Handcrafted MAI demoted da efficacy ager. PROTECTED_NAMES
sempre skipped. Configurabile via env (`METNOS_EFFICACY_*`).

Da wirare nello scheduler v2 come task `daily@04:30` (insieme agli altri
ager). Lasciato a manual wire perche' lo scheduler v2 e' fuori scope di
questa PR (ADR 0112).

### Layer 5 — Smoke battery con expected_tool

In `runtime/smoke.py::BATTERY[].expected_first_tool` + `expected_arg_keys`
+ `min_pass_rate`. Helper `_run_smoke_with_tool_assertion(case)`
simula il PLANNER usando solo intent_extractor BoW deterministico +
`prefilter.rank_with_intent` (NO LLM live se l'env non lo richiede).
Asserisce: `ranked[0].name == case["expected_first_tool"]`. Skip
gracefully se prefilter o catalog non sono disponibili.

`run_smoke_routing_battery()` per cron. Tool families coperte: get_now,
list_dirs, find_files, read_files, read_messages, get_location, get_urls,
find_persons_indices (anti ADR 0113 regression), get_processes (anti
health regression), find_urls (anti `find_texts` hijack regression).

### Layer 6 — LLM semantic verifier (stage 6 synt)

In `runtime/synt_stage6_verify.py::verify_semantic_alignment(description,
code_body, ...)`. Usa LLM tier wise (Gemma 4 26B locale) con prompt strict
JSON: `{"aligned": bool, "mismatch": "..."}`. Multi-model consensus
optional via env `LLM_VERIFY_MODELS=m1,m2,m3` (majority wins, tie =
fail-safe). Audit JSONL in `~/.local/share/metnos/synth_audit/verify_*.jsonl`.

Wired in `runtime/synt_multistage.py::run_full` dopo stage 5 (code) e
prima del sign + insert in catalog. Misalignment → `final_state =
"rejected_semantic_drift"`, log audit, niente sign. Disabilita via
`METNOS_SYNT_STAGE6_DISABLED=1` (test/dev). Determinismo §7.9: solo
JSON parsing, retry 1x, fallback `aligned=False` (fail-safe).

## Alternatives considered

**(A) Solo Layer 2.** Indirizza il caso `find_texts`, ma non risolve i
casi in cui il synth ha affinity stretta (no overlap) ma comunque
inefficace o misaligned. Rifiutata: copertura insufficiente.

**(B) Promuovere `vocab_semantic` (Layer 1)** come gate hard pre-stage 1.
Sub-progetto che richiede un dizionario semantico canonico per ogni
verbo del vocab chiuso (es. `find` = "cerca/discover su pattern", non
"lookup"). Out-of-scope per questa PR (segnalato per next sprint).
Senza L1, L2 e' la prima rete deterministica sul confine NL→synth.

**(C) Disabilitare synth on-the-fly** (richiedere review umana per ogni
nuovo synth). Conservativo ma blocca la crescita organica del sistema
(filosofia Metnos cap. 7 architettura). Rifiutata.

**(D) Gate via test esecuzione live** (run i test del manifest sui dati
reali al boot). Test stage 3 lo fa gia' nel sandbox del synt. Aggiungere
un test live al boot rallenta troppo (~10-30s per synth). L1+L6 con LLM
+ L5 con asserzione routing coprono lo stesso failure mode con costo
trascurabile.

## Consequences

**Positive.**
- Nuovi synth devono passare 4 gate: affinity (L2) + efficacy in tempo
  (L3) + routing assertion (L5) + semantic alignment (L6).
- Existing synth migrano gradualmente: L3 li demota se inefficaci, L2 li
  rifiuta al prossimo load se affinity overlap.
- Bug `find_texts` 8/5 catturato a livello L2 (avrebbe rifiutato il
  manifest al primo load post-deploy del fix).

**Costs.**
- L6 aggiunge ~1-3s di latenza per ogni nuovo synth (1 LLM call wise
  tier locale). Acceptable dato che synth non e' hot path.
- L3 lavora su un DB SQLite + scan turn JSONL. Costo trascurabile in
  cron daily.
- L2 e' O(N²) su numero executor (N≈55-100). Costo ~10-30ms per load,
  cached dal `_CATALOG_CACHE` (ADR 0099).

**Doors closed.**
- Synth catch-all con affinity sopra-soglia non possono piu' shadow
  handcrafted. Stage 1 di synt continua a usare un name canonico, ma
  ora c'e' un secondo controllo a posteriori.
- Synth che ritornano sempre 0 entries vivono al massimo 100 invocations
  + 30 di re-eval prima di essere archiviati.

**Doors open.**
- Layer 1 (vocab semantic gate) come step naturale successivo: dizionario
  canonico per i 22 verbi che il LLM pre-flight controlla.
- Multi-model consensus L6: oggi default single-model (`wise`), abilitabile
  via env per future audit critiche.

## References

- ADR 0072 — adaptive re-rank intra-turn (pattern di guard al routing).
- ADR 0086 — indici di dominio (simmetria "handcrafted-vince per
  costruzione").
- ADR 0079 — GC synth in collisione (pattern non-distruttivo).
- CLAUDE.md §3 — synth pipeline 5 stadi.
- CLAUDE.md §7.10 — re-sign post edit `.py`.
- CLAUDE.md §10.6.41 — voce indice anti-regressione.
