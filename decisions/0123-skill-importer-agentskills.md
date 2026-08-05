---
id: 0123
title: Importer skill agentskills.io → executor Metnos
date: 2026-05-10
status: accepted
area: synt
related:
  - 0114  # Synth admission policy 4 layers
  - 0122  # Proposal auto-evaluator
  - 0086  # Indici di dominio + reverse_pattern
  - 0089  # Credenziali UX 3 strati
  - 0090  # Engine UI dichiarativo get_inputs
  - 0092  # Prompt-as-data multilingua
extends:
  - 0114
---

## Context

agentskills.io e' uno standard aperto multi-vendor (origine Anthropic,
~40 client tra cui Claude Code, Cursor, Copilot, Gemini CLI) per
distribuire «skill»: cartelle con `SKILL.md` (YAML frontmatter +
Markdown istruttivo) + opzionali `scripts/` (Python/shell/JS) e
`references/`/`assets/`. Lo standard usa progressive disclosure: la
skill e' un addendum al prompt LLM piu' codice di supporto.

Metnos ha architettura ortogonale: executor firmati con manifest TOML,
sandbox bubblewrap, vocabolario chiuso (22 verbs + 16 OBJECTS §2.2),
admission policy a 5 layer (ADR 0114). Importare meccanicamente uno
SKILL.md come addendum promptuale duplica i canali di estensione (synth
interno vs skill esterne) bypassando ager, introvertiva e admission.

Decisione: importare le skill come **executor sintetizzati con
provenance esplicita**. Dopo import, indistinguibili da un synth
interno; passano per gli stessi 5 layer admission + auto-evaluator
(ADR 0122).

## Decision

### Pipeline import (5 stadi)

```
SKILL.md (locale o agentskills.io/<owner>/<skill>)
    |
    v
[1] skill_parser   -> ParsedSkill (frontmatter + body + sub-commands)
[2] skill_translator -> list[ExecutorPlan] (azione_oggetto[_qualifier])
[3] skill_codegen  -> manifest.toml + <name>.py via Jinja deterministico
[4] skill_description_llm -> [description].it/.en + affinity (LLM stage 4)
[5] skill_admission -> 5 layer ADR 0114 + credentials uniqueness
    |
    v
sign Ed25519 -> ~/.local/share/metnos/executors/_imports/<skill>/<name>/
```

CLI: `metnos-skills import <url_or_path>` + `list` + `uninstall` + `status` +
`evaluate`.

### Mapping deterministico (skill_vocab_map.json)

Tabella chiusa action+domain → verbo+oggetto canonico §2.2:
```json
{
  "actions": {"list":{"verb":"read"},"create":{"verb":"set"},
              "delete":{"verb":"delete"},"search":{"verb":"find"},
              "get":{"verb":"get"},"update":{"verb":"change"},
              "send":{"verb":"send"},"append":{"verb":"change"},
              "share":{"verb":"set"},"reply":{"verb":"send"}},
  "domains": {"calendar":"events","gmail":"messages",
              "drive":"files","sheets":"files","docs":"files",
              "contacts":"contacts"}
}
```

Qualifier non in vocab.QUALIFIERS (es. `_labels`, `_share`) → fold nel
verbo+oggetto base senza qualifier. NIENTE escalation a Roberto per
qualifier nuovi.

### Provenance esplicita

Ogni executor importato ha nel manifest:
```toml
[provenance]
synthesized = true
imported_from = "agentskills.io/<owner>/<skill>"
source_version = "1.1.0"
imported_at = "2026-05-10T22:00:00Z"
source_sha256 = "<sha256 di SKILL.md+scripts/>"
```

E' il segno UNICO che distingue un import da un handcrafted o synth.
Niente sandbox tier separato, niente capability speciali — il vaglio,
ager, evaluator lavorano come per qualsiasi altro executor.

### 5 layer admission (ADR 0114 esteso a import)

- **L1 vocab gate** (skill_admission.py): `name` deve essere `azione_oggetto[_qualifier]` ∈ §2.2. Reject silenzioso del singolo plan, NON dell'intero import.
- **L2 affinity overlap** (skill_admission.py): jaccard ≥0.5 vs handcrafted in `executors/` + altri synth. Reject per evitare hijacking.
- **L3 efficacy ager** (executor_aging.py, gia' attivo): post-import, ogni invocazione live aggiorna success_rate; <20% dopo ≥100 chiamate → `deprecated`; <5% dopo altre 30 → `archived`. Gli executor importati sono soggetti senza eccezione.
- **L5 smoke routing assertion** (smoke_imports.py + smoke.py BATTERY): vedi sotto.
- **L6 stage 6 semantic verifier** (synt_multistage stage 6, riusato): description vs codice generato allineati. Reject su misalignment.

### Layer 5 — Smoke routing assertion (Opzione B)

Nuovo file `runtime/smoke_imports.py` (separato dalla `BATTERY` canonica
in `runtime/smoke.py`):

```python
BATTERY_IMPORTS = [
  {"query": "lista miei appuntamenti domani",
   "expected_first_tool": "read_events",
   "expected_arg_keys": ["time_window"],
   "min_pass_rate": 1.0,
   "imported_from": "agentskills.io/googleworkspace/calendar"},
  ...
]
```

L'importer (`skills_cli.py::cmd_import`) APPENDE 1-2 case per executor
importato, dedotti da:
- affinity IT+EN del manifest (parafrasi come query naturale)
- name come `expected_first_tool`
- args required come `expected_arg_keys`

Lo smoke runner (`runtime/smoke.py`) concatena BATTERY + BATTERY_IMPORTS:
```python
try:
    from smoke_imports import BATTERY_IMPORTS
    BATTERY = BATTERY + BATTERY_IMPORTS
except ImportError:
    pass
```

**Pro**:
- Separation of concerns: la BATTERY canonica resta curata da Roberto;
  gli import si auto-popolano senza polluzione.
- Idempotenza: deleting smoke_imports.py riporta a comportamento pre-import.
- Audit: la sezione `imported_from` per case permette di tracciare
  l'origine di ogni assertion (debug).

**Contro**:
- Due file da mantenere (la concat e' triviale, 4 righe in smoke.py).
- Skill che producono molti executor (es. hermes google-workspace = 24
  sub-commands) gonfiano la batteria — lo smoke ha pero' `min_pass_rate`
  per case e cap globale.

### Setup credenziali (OAuth/API key) — ADR 0089 esteso

Ogni manifest importato che richiede credenziali dichiara:
```toml
[required_credentials]
binding = "<skill_name>"
fields = ["client_secret_json", "token_json"]
form_kind = "oauth_browser_flow"
prompt_it = "..."; prompt_en = "..."
```

Trigger: prima invocazione senza token → executor ritorna
`decision="needs_inputs"` con dialog multi-step (ADR 0090), `on_complete:
save_credentials_and_resume`. Storage Fernet+HKDF in
`~/.local/share/metnos/credentials/<binding>.json`. I 3 executor canonici
`find_credentials/set_credentials/delete_credentials` (ADR 0089 esteso
con object `credentials` come 16° OBJECT, 10/5/2026) gestiscono ciclo
vita; metadata-only invariante (`metnos:credentials_metadata_only`
capability) impedisce leak cleartext al PLANNER.

## Consequences

Verifica 19/7/2026: i tre executor canonici del vault dichiarano il vincolo
`metnos:credentials_metadata_only`; cluster credenziali, immagini e file
186 passati e 2 skip dichiarati per due cicli. Audit firme core 83/83.

**Positive**:
- Catalogo Metnos estendibile da skill terze senza duplicare canali
- Stessi controlli qualita' (admission + ager + evaluator) per import e synth
- Provenance audit completo (`provenance.imported_from` + `synth_audit/imports.jsonl`)
- Reuse: scripts/python delle skill diventano implementazione naturale

**Negative**:
- Dipendenza da formato SKILL.md (skill rotte/cambiate richiedono re-import)
- LLM stage 4 description dipende da Gemma 4 26B disponibile (fallback boilerplate se down)
- Skill con OAuth richiedono interazione utente (un solo step needs_inputs il primo uso)

**Neutral**:
- agentskills.io standard non ha trust model, ma admission policy Metnos
  lo aggiunge ex-post (L2+L6 catturano la maggior parte dei misbehavior)

## Implementation status (10/5/2026 sera)

Worktree `/tmp/skill_importer_work/` con 5400 LOC + 200 test PASS.

| Modulo | LOC | Test |
|---|---|---|
| skill_parser | 750 | 23 |
| skill_translator | 689 | 38 |
| skill_vocab_map.json | 41+ | — |
| time_window_parser | 296 | 35 |
| reverse_patterns_patch (5° pattern `delete_<object>_by_id`) | 228 | 13 |
| 3 credentials executors (find/set/delete) | 765 | 29 |
| skill_wrapper (5 helper condivisi) | 331 | — |
| skill_codegen + 3 Jinja templates | 1115 | 28 |
| skill_description_llm | 266 | — |
| skill_admission | 463 | 17 |
| skills_cli | 400 | 17 |
| **Totale** | **~5400** | **200 PASS** |

Demo: `metnos-skills import /tmp/skill_poc_hermes/SKILL.md` produce 19/24
executor `py_compile` clean dal SKILL.md hermes google-workspace (5
rejected per qualifier non in vocab — risolti dal gap 1 closure 10/5).

**Gap residui chiusi** (subagent BG 10/5 sera):
1. skill_vocab_map qualifier extension (append/share/reply/labels mapping)
2. LLM description wiring vero (Gemma 4 26B)
3. Sign keys verification + CLI check
4. Fetch remoto da agentskills.io (GitHub raw + git clone + cache)
5. L3 efficacy ager docs (no-code, gia' attivo)
6. Smoke battery auto-add (smoke_imports.py separato + concat in smoke.py)

### Amendamento 23/7/2026 — Google Workspace 24/24

Il mapping contestuale di ADR 0128 e l'asse provider di ADR 0136 sostituiscono
il vecchio esempio piatto sopra. La skill Google Workspace corrente espone 24
sotto-comandi e il traduttore ne ammette 24, senza rifiuti. Le tre ambiguità
residue sono chiuse con modalità canoniche generali `thread` e `labels`, più la
distinzione verbale fra metadata e contenuto Drive:

- `gmail reply` → `send_messages_thread_google_workspace`;
- `gmail labels` → `list_messages_labels_google_workspace`;
- `drive get` → `get_files_google_workspace`, distinto da
  `drive download` → `read_files_google_workspace`.

`sheets update` e `sheets append` restano distinti come `set_*` e `write_*`.
La Naming Authority ammette modalità e provider come assi ortogonali.
La documentazione precedente che indicava `sheets append` come terzo rifiuto
era obsoleta: il terzo rifiuto effettivo era `drive download`, in collisione
con `drive get` a causa della vecchia traduzione di entrambi come `read_files`.

## References

- Worktree: `/tmp/skill_importer_work/`
- IMPORTER_NOTES (modello operativo): `/tmp/skill_poc_hermes/IMPORTER_NOTES.md`
- POC manuale Calendar: `/tmp/skill_poc_hermes/output/` (3 executor + POC_REPORT)
- agentskills.io standard: https://agentskills.io
- Skill canoniche: github.com/anthropics/skills, github.com/googleworkspace/cli
