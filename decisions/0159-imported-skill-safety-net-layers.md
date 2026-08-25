# ADR 0159 — Safety net layers per skill imported third-party

Stato: accepted
Data: 2026-05-24
Supersedes: nessuno
Related: ADR 0114 (synth admission), ADR 0123 (skill-importer agentskills),
ADR 0140 (sandbox skill audit), ADR 0136 (skill dormancy + provider qualifier)

## Context

Metnos importa skill da repo esterni (`agentskills.io` o filesystem locale)
via `metnos-skills import <SKILL.md>` (ADR 0123). Lo skill manifest viene
trasformato in 1+ executor `<verb>_<obj>_<provider>` nel catalog.

Differenza con builtin handcrafted:
- **Builtin**: codice scritto/revisionato dal team Metnos, manifest e
  comportamento allineati per costruzione, audit via git blame.
- **Imported**: codice third-party non-trusted; manifest dichiara "X" ma
  codice puo' fare "Y" (drift volontario o accidentale). Provenance esterna
  e' trust gate.

Senza controlli espliciti, una skill malevola/buggata potrebbe:
1. Esfiltrare credenziali via Y diverso da X dichiarato.
2. Imitare un builtin esistente per dirottare query (squatting).
3. Operare senza traccia (no audit forensics post-incident).
4. Continuare a fallire silently (non-degrazione del catalog).

## Decision

Stack di safety net a **7 layer**, applicati at-import + at-runtime. Sono
filtri progressivi: ogni layer riduce un certo tipo di rischio, e i layer
sono indipendenti (failure isolato).

### Tabella sintetica

| Layer | Rischio mitigato | Quando | Componente | Reject/Audit |
|---|---|---|---|---|
| L1 sign verify | manomissione codice post-import | ogni boot | `sign.verify_executor` | reject silente (catalog drop) |
| L2 affinity overlap | squatting su builtin | at-import | `loader.check_affinity_pair` (Jaccard ≥0.5, ≥0.85 binding-suffixed) | reject (audit JSONL) |
| L3 efficacy ager | skill silently broken / inutilizzata | daily cron | `executor_aging.lifecycle_override_map` | auto-deprecate 30g, archive 14g |
| L4 sandbox per-skill | exfiltration runtime | ogni invocazione | (planned-not-implemented, Fase C ADR 0140) | — |
| L5 smoke battery | output schema-broken / crash | at-import | `skill_admission._run_smoke_for_plan` (delega a `smoke._run_smoke_with_tool_assertion`) | reject + audit |
| L6 LLM semantic verify | manifest description ↔ behavior mismatch | at-import | `synt_stage6_verify` (Gemma 4 26B) — default ON per imported | reject (LLM judge JSON `{ok:false, reason}`) |
| Runtime judge | invocazione attuale viola policy | ogni step | `vaglio.judge` (guard + safe-verb shortcut + LLM judge) | block (Verdict approved=False) |
| Skill audit | exfiltration / abuso post-fatto | ogni invocazione | `skill_audit.audit_skill_invocation` (sharded daily) | append-only JSONL (no block) |

### Layer 1 — Sign verify

Ogni executor (handcrafted, synth, imported) ha `<name>.py.sig` con sha256
del codice + signature. Al boot `loader.verify_executor` confronta
`declared digest != actual digest` → scarta silenziosamente.

Per imported: la firma e' applicata da `metnos-skills import` di default;
opt-out solo via `--no-sign` (test/dev). Override env `METNOS_LOADER_VERIFY=0`
per test E2E.

### Layer 2 — Affinity overlap guard (Jaccard ≥0.5 / ≥0.85 binding)

Una skill imported che dichiara affinity `["leggi mail", "cerca email"]`
con un builtin esistente `read_messages` (stessa affinity) attiva l'allarme:
indistinguibili al prefilter → ambiguity → skill imported puo' essere
scelta al posto del builtin trusted.

Single source of truth: `loader.check_affinity_pair(a, b, threshold)`
calcola Jaccard puro (deterministico §7.9). Due callers consumano la SoT
con threshold diversi:

- `loader._check_affinity_overlap(catalog)` al boot: threshold
  `AFFINITY_OVERLAP_THRESHOLD=0.5` (pre-binding, vs synth+handcrafted).
- `skill_admission._affinity_overlap_check(plan, ...)` at-import:
  threshold `0.5` di default, sale a `AFFINITY_OVERLAP_THRESHOLD_BINDING=0.85`
  quando il plan e' binding-suffixed (es. `read_messages_google_workspace`)
  perche' il binding qualifica esplicitamente il dominio remoto e le
  keyword sovrapposte sono attese.

Audit JSONL in `~/.local/share/metnos/synth_audit/affinity_rejected.jsonl`
(loader) o `imports.jsonl` (skill_admission).

### Layer 4 — Sandbox per-skill (PLANNED-NOT-IMPLEMENTED)

Fase C ADR 0140: namespace + seccomp profile per ogni invocazione skill
imported. Oggi `Executor.sandbox_profile` e' letto da `manifest.toml::[sandbox]`
(`loader.py::~1004`) ma NON enforced.

Watchdog trigger (Fase C activation gate):
- ≥5 skill third-party installate distinte, OR
- ≥1 user_channel con `role != 'host'` (guest paired)

Implementato in `runtime/jobs/skill_sandbox_watchdog.py`
(scheduler v2 daily@06:35); emette `MSG_SKILL_SANDBOX_THRESHOLD` quando
le soglie sono superate. Roberto decide se attivare Fase C dopo la
notifica.

### Layer 3 — Efficacy ager

`executor_aging` traccia ogni invocazione (success/fail/never_chosen).
Daily cron `lifecycle_override_map`:
- `inactive_30d` + `chosen_count=0` → `deprecated` (warning utente)
- `deprecated_14d` → `archived` (escluso dal catalog)

Handcrafted MAI archived (policy ADR 0114). Solo imported + synth.

### Layer 5 — Smoke battery

`runtime/smoke.py::_run_smoke_with_tool_assertion`: routing assertion
deterministico (intent BoW + prefilter, no LLM) per executor: la query
realistica IT della mappa `queries_by_pattern` (in
`skill_admission._smoke_case_for_plan`) DEVE produrre come #1 candidato
il `expected_first_tool` (typicamente builtin canonical equivalente).

Per imported: eseguito at-import via `skill_admission._run_smoke_for_plan`
PRIMA di `final accepted`. Reject se fail. Skip gracefully se il pattern
(verb, obj) non e' in mappa (`_no_smoke=True`).

Il flusso ordinario non può disattivare il controllo tramite ambiente. Le prove
isolate sostituiscono esplicitamente la dipendenza.

NB: il smoke battery file (`smoke.py::BATTERY`) contiene case curati a
mano per il catalog builtin; gli imported popolano
`smoke_imports.py::BATTERY_IMPORTS` (cron / boot) come telemetria
continua post-import — NON come gate.

### Layer 6 — LLM semantic verifier (stage 6 synt)

`runtime/synt_stage6_verify.py`: presenta a Gemma 4 26B (`wise` tier):
- manifest description (cosa promette)
- codice generato
- 3-5 test sample

Domanda chiusa: "la description e' coerente col codice? JSON
`{ok:bool, reason:str}`". Reject se `aligned=false` (mandatory).

Default per imported: ON (ADR 0159). Le variabili storiche
`METNOS_STAGE6_VERIFY_IMPORTED` e `METNOS_SYNT_STAGE6_DISABLED` sono ritirate e
non modificano l'esito. Un payload non tipizzato o un verificatore indisponibile
respinge il piano.

### Runtime judge (`vaglio.judge`)

Identico per builtin e imported:
1. **Guard check** (binario): forbidden paths, signature scopo, args invalidi.
2. **Safe-verb shortcut** (ADR 0107): se action ∈ `vocab.SAFE_VERBS`
   (read/find/get/list/filter/sort/group/classify/compute/compare/describe/render)
   → approva senza LLM judge. Risparmio ~3-5s.
3. **LLM judge** (graduato 0-1): per verbi mutanti (send/delete/move/...)
   Gemma 4 26B valuta `intent ↔ executor_name ↔ args` coerenza.

### Skill audit (audit JSONL sharded daily)

`skill_audit.audit_skill_invocation`: append a
`~/.local/share/metnos/skill_audit/<YYYY-MM-DD>.jsonl` per ogni invocazione
imported. Fields: `ts`, `executor_name`, `provenance`, `args_hash`,
`result_status`, `error_class`, `elapsed_ms`. NO PII.

Sharding daily: query per giorno facile (`grep audit_24_5`), retention
semplice (`rm` shard > 90gg), log rotation naturale, statistiche granulari.

Migration: il flat legacy `~/.local/share/metnos/skill_audit.jsonl` viene
splittato per giorno al primo `stats()` (lazy, idempotente), il flat e'
archiviato in `<flat>.migrated`.

Post-incident forensics: `metnos-cli skill-audit search <pattern>`.

Watchdog scheduler `daily@06:35`: trigger admin notify se
- ≥5 skill third-party attive
- OR ≥1 guest paired
- OR ≥10% invocazioni con `error_class != null`

## Alternatives considered

1. **Single fat judge LLM** che fa tutto. Reject: latency 5-10s per
   invocazione, e singolo punto di failure (se LLM down → catalog dead).
2. **Sandbox kernel-level** (gVisor, Firecracker). Reject: complessita'
   eccessiva per il modello self-hosted; Python in-process gia' isolato
   abbastanza con audit.
3. **Whitelist statica skill** (no import dinamico). Reject: contraddice
   estensibilita' agentskills.io (ADR 0123).
4. **Solo runtime judge** (no L2/L6). Reject: squatting + manifest drift
   sono attacchi at-import time, non si vedono a runtime.

## Consequences

**Positive**:
- Multi-strato copre attacchi diversi (manomissione, squatting, drift,
  silent break, exfiltration).
- Layer indipendenti: failure L6 non rompe L1/L2/L5.
- Trust gate granulare: utente vede "skill imported" vs "builtin" in
  catalog UI con badge.
- Audit forensics retroattivo (~1 anno JSONL append-only).

**Negative**:
- Latenza at-import: L5 smoke ~5-10s, L6 LLM verify ~2-5s, totale ~10-15s
  per skill. Accettabile (one-time).
- L2 Jaccard threshold 0.5 puo' rigettare skill legitimely affini (es.
  google-workspace vs metnos-mail per `read_messages`). Mitigato dal
  provider qualifier (`_google_workspace` distingue, ADR 0136).
- L6 LLM judge non deterministico: stesso input puo' produrre verdetto
  diverso fra runs. Mitigato da temperature=0 + few-shot examples.

**Watchpoint**:
- Quando guest paired aumentano (>1), abilitare sandboxing per-skill
  (`Executor.sandbox_profile`, ADR 0140 ext fase C: namespace + seccomp).
- L6 LLM throughput limit: max 1 import LLM-pesante in parallelo
  (queue serial).

## References

- ADR 0114: synth admission policy (L1-L6 originale per synth).
- ADR 0123: skill-importer agentskills (pipeline 5-stadi at-import).
- ADR 0140: prefilter modular + sandbox foundation per imported.
- ADR 0107: safe-verb shortcut vaglio.
- ADR 0136: skill dormancy + provider qualifier.
- Code: `runtime/loader.py`, `runtime/vaglio.py`, `runtime/skill_audit.py`,
  `runtime/synt_stage6_verify.py`, `runtime/cli/skills_cli.py`.
- Docs: `docs/it/architecture/skills.html`.
