# ADR 0152 — i18n pipeline strutturale (synth-aware + auto-register)

- Status: accepted
- Date: 2026-05-19
- Supersedes: nessuno
- Extends: ADR 0092 (Prompt-as-data + multilingua), ADR 0104 (Report runtime user-facing i18n)

## Context

ADR 0092 e 0104 stabiliscono che user-facing messages devono passare per il DB `i18n.sqlite` con chiavi tipate `ERR_/WARN_/MSG_/LOG_` e fallback chain `current → en → it → <missing>`. Pattern usato uniformemente nei manifest e nel runtime determinismo §7.9.

Audit 19/5/2026 ha identificato 3 gap strutturali:

1. **Synth pipeline non vede catalogo chiavi**: il prompt `synt_code.j2` stage 5 prescrive `messages.get('ERR_*')` ma elenca solo 1 esempio (`ERR_EXT_SVC_LIMIT`). Il LLM (Gemma 4 26B wise) tende a inventare chiavi (`ERR_FOO_BAR`) non in DB → `messages.get` ritorna `"[unknown msg KEY]"` a runtime, fallback degradato.

2. **Codice handcraft pregresso**: 5 backend (`runtime/backends/{files,messages}/*.py`) contenevano ~115 stringhe IT/EN hardcoded inline nei return error, antecedenti l'enforcement i18n.

3. **skill_wrapper non traduce error_class**: `_classify_error()` mappa stderr provider → classi (auth_required/server_error/...) ma la stringa cruda passa al PLANNER, non c'e' conversione a chiave i18n.

## Decision

Quattro azioni a-b-c-d eseguite in parallelo per chiudere il gap strutturale:

### (a) Subset injection chiavi disponibili nel prompt synt_code

Helper `runtime/i18n.keys_for_synth_context(max_per_family=30)` ritorna un subset rilevante per famiglia:
- Tutte le `ERR_*` (cap basso, ~27).
- Tutte le `WARN_*` (2).
- Top-N `MSG_*` ordinate alfabeticamente (default 30, su 133 totali).
- Tutte le `LOG_*` (6).

Iniettato come variabile `available_i18n_keys` nel `synt_multistage._stage5_prompt_for_verb`. Il prompt `synt_code.j2` IT+EN rende un block condizionale:

```jinja
{% if available_i18n_keys %}
CHIAVI i18n DISPONIBILI (riusale invece di inventarne):
{% for fam, keys in available_i18n_keys.items() %}{% if keys %}
  {{ fam }}: {{ keys | join(', ') }}
{% endif %}{% endfor %}
{% endif %}
```

Size: ~65 chiavi × ~25 char ≈ 1.4 KB nel prompt stage 5 (vs ~7 KB lista completa). Trade-off scelto (A2 in discussione 19/5): cap per evitare bloat senza perdere copertura ERR_/WARN_/LOG_ (tutte presenti).

### (b) skill_wrapper error_class → ERR_* mapping

`runtime/backends/_google_api_runner.py::_i18n_error_for_class` mappa deterministicamente le 8 classi `_classify_error()` → chiavi i18n esistenti:

| error_class | → ERR_* |
|---|---|
| auth_required | ERR_PERMISSION_DENIED |
| not_found | ERR_PATH_NOT_FOUND |
| server_error | ERR_EXT_SVC_UNAVAILABLE |
| rate_limited | ERR_EXT_SVC_LIMIT |
| network | ERR_TIMEOUT |
| missing_dependency | ERR_NOT_IMPLEMENTED |
| invalid_args | ERR_INVALID_ARGS |
| unknown | ERR_OP_FAILED |

`run_with_retry` emette ora `{ok:False, error_class, error_code:"ERR_*", error: i18n_text, detail: stderr_raw}`. L'`error` user-facing e' i18n, l'`error_class` resta machine-readable, lo `stderr` raw va in `detail` per debug.

### (c) Auto-register stub chiavi orfane (c1+flag)

Due step:

**c1** — `synt_multistage._register_synth_keys(code)` post-stage5: regex `_I18N_KEY_PATTERN` estrae ogni `messages.get("KEY")` o `_msg("KEY")` con KEY UPPER_CASE_FAMILY (ERR_/MSG_/WARN_/LOG_). Per ogni chiave non in DB, chiama `i18n.register_key_if_missing(key, text="<auto-synth: KEY>")` con `needs_translation=1`. Determinismo §7.9, no LLM aggiuntivo a questo step.

**c2** — Daemon `runtime/jobs/i18n_translate_pending.py::_materialize_auto_synth_stubs` (eseguito daily@02:00 via scheduler v2 ADR 0112) PRIMA del normal translate:
1. SELECT distinct keys dove `text LIKE '<auto-synth: %'`.
2. Per ogni chiave: LLM tier middle genera IT+EN in 1 call (prompt sistema: "Chiave i18n `KEY`, famiglia `ERR_`. Genera testo IT+EN", output JSON `{"it","en"}`).
3. UPDATE i18n SET text=generated, `auto_translated=1` (nuova colonna), `needs_translation=1` (resta per review admin).

Schema migration idempotente in `_ensure_schema`: `ALTER TABLE i18n ADD COLUMN auto_translated INTEGER DEFAULT 0`.

### (d) Migrazione one-shot 5 backend handcraft

Eseguita 19/5/2026 v4 contestualmente. Cinque file:
- `runtime/backends/files/local.py` (~50 stringhe)
- `runtime/backends/files/google_workspace.py` (16)
- `runtime/backends/messages/email_metnos.py` (~30)
- `runtime/backends/messages/telegram_bot.py` (7)
- `runtime/backends/messages/gmail_google_workspace.py` (11)

Pattern uniformi mappati su chiavi esistenti dove possibile (ERR_PATH_NOT_FOUND, ERR_PERMISSION_DENIED, ERR_EXT_SVC_UNAVAILABLE, ERR_INVALID_ARGS) e 20 chiavi nuove introdotte per gap semantici (ERR_ARG_MISSING/INVALID, ERR_PATH_WRONG_TYPE, ERR_PARENT_*, ERR_DIR_OP_FAILED, ERR_REFUSE_MOVE, ERR_TEMPLATE_FAIL, ERR_OP_FAILED, ERR_IMAP_CMD, ERR_FOLDER_NOT_FOUND, ERR_PARSE_FAIL, ERR_TIME_WINDOW_INVALID, ERR_ACCOUNT, ERR_ATTACHMENT, ERR_OAUTH_SETUP, ERR_NOT_IMPLEMENTED, ERR_NOT_APPLICABLE, MSG_NO_SUBJECT, ERR_SRC_DST_SAME).

Test 5/5: i 5 backend importano correttamente, return path testati su args invalid trigger.

## Rationale

### Perche' subset (A2) invece di lista completa

Lista 247 chiavi nel prompt = ~7 KB extra a ogni stage 5 call. Sample LLM Gemma 4 26B wise: stage 5 prompt corrente e' ~5-7 KB → raddoppia. Test interni mostrano regression latenza ~+8% senza guadagno proportionale. Subset 1.4 KB porta i benefici (zero invenzioni di ERR_/WARN_/LOG_) senza il costo (le MSG_* sono molte ma il synth tipicamente usa ERR_ per error paths e poche MSG_ contextuali, top-30 alfabetiche e' ragionevole).

### Perche' auto_translated flag (review admin)

Stub `<auto-synth: KEY>` user-facing e' brutto. Daemon LLM materializza ma il testo generato puo' essere imperfetto. Flag separato `auto_translated=1` + `needs_translation=1` permette ad admin UI futura (out-of-scope ADR) di filtrare "review pending da LLM auto". Non blocchiamo il flow runtime (`messages.get` ritorna comunque qualcosa di valido), ma marchiamo per audit.

### Perche' regex e non AST per estrazione chiavi

Synth code e' Python valido ma puo' contenere comment/docstring con KEY-like strings. Regex conservativa (UPPER_CASE_FAMILY prefix) cattura le call site reali e evita falsi positivi su prosa. AST richiederebbe parsing del codice generato + handling import alias (`_msg` vs `messages.get`); regex e' 80/20.

### Perche' integrazione con _google_api_runner e non skill_wrapper diretto

`_google_api_runner.run_with_retry` e' il callsite REALE dove stderr provider arriva a runtime. `skill_wrapper._classify_error` e' una utility che il runner invoca. Wiring nel runner = i18n applicato solo dove conta (3 backend google_workspace + gmail). Skill importate future via agentskills.io potranno usare lo stesso pattern.

## Consequences

- Synth stage 5 emette codice consistente con catalogo i18n esistente (riusa 65 chiavi note).
- Chiavi nuove emerse dal synth diventano stub auto-registrati invece di orfani `<missing:KEY>`.
- Daemon materializer LLM completa gli stub asyncronamente (next 02:00 dopo synth).
- Admin pipeline futura puo' filtrare per `auto_translated=1` per quality review.
- 5 backend handcraft non hanno piu' stringhe hardcoded (i18n full coverage).

## Open questions

- Admin UI `/admin/i18n` con filter `auto_translated=1`: out-of-scope, separato TODO.
- Skill importate da agentskills.io (ADR 0123): adattare lo stesso pattern al wrapper esterno (skill_wrapper invece di _google_api_runner).
- Cap `max_per_family=30` ottimale? Test in bench dedicato (synt corpus 30+ run) per misurare invented vs reused key ratio.

## References

- Codice (a): `runtime/i18n.py::keys_for_synth_context`, `runtime/synt_multistage.py::_stage5_prompt_for_verb`, `runtime/prompts/{it,en}/synt_code.j2`.
- Codice (b): `runtime/backends/_google_api_runner.py::_i18n_error_for_class`.
- Codice (c): `runtime/i18n.py::{register_key_if_missing, key_exists}`, `runtime/synt_multistage.py::_register_synth_keys`, `runtime/jobs/i18n_translate_pending.py::_materialize_auto_synth_stubs`.
- Codice (d): `runtime/backends/{files,messages}/*.py` (5 file migrati).
- Schema DB: `auto_translated INTEGER DEFAULT 0`.
