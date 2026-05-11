---
id: 0004
title: Centralized message repository (runtime/messages.py)
date: 2026-04-28
status: accepted
area: messages
related:
  - 0002
---

## Context

On April 28, 2026, while implementing `get_files_metadata` with
reverse-geocoding via public Nominatim, we had to decide how to handle
the case where the external service exhausts its daily quota
(~1000 requests/day for public Nominatim). Roberto raised the structural
point: user-facing responses for "external service limit reached" must
be uniform across executors. The Telegram user must not know whether
Nominatim, OpenAI, mail SMTP, or calendar API is failing — they only
know that some external service has a cap and that the administrator
must be involved.

He also observed that the same centralized-template pattern would serve
structured logs (audit, debug, telemetry) and informational messages to
the user (progress, outcomes, hints). Four families of strings sharing
the same need: format substitution, graceful fallback, future
translation.

## Decision

A single module `runtime/messages.py` with a dict `MESSAGES: dict[str,
str]`. Each key uses a prefix identifying its family:

- `ERR_*` user-facing errors (e.g., `ERR_EXT_SVC_LIMIT`,
  `ERR_PATH_OUTSIDE_SCOPE`, `ERR_DST_EXISTS`).
- `WARN_*` soft warnings (e.g., `WARN_EXT_SVC_DEGRADED`,
  `WARN_PARTIAL_RESULT`).
- `MSG_*` informational/status messages (e.g., `MSG_NO_RESULTS`,
  `MSG_UNDO_DONE`, `MSG_LOOP_BREAK`, `MSG_CAP_STEPS`, `MSG_PROGRESS`).
- `LOG_*` templates for turn-log/audit (e.g., `LOG_EXEC_INVOKED`,
  `LOG_UNDO_PENDING`).

Single function `get(code, **kwargs) -> str`: template lookup,
substitution via `str.format`, graceful fallback (unknown code returns
the code itself, useful in debug; missing kwargs returns the raw
template without crashing).

In executor call sites:

```python
sys.path.insert(0, "/opt/myclaw/runtime")
from messages import get as msg
return {"ok": False, "error_code": "ERR_EXT_SVC_LIMIT",
        "error": msg("ERR_EXT_SVC_LIMIT")}
```

Convention: the executor output reports both `error_code` (structured
key) and `error` (localized text). The code enables automatic dispatch
(Telegram bot can do its own lookup, future retry logic); the text
serves as fallback and for direct logs.

Anchor key: `ERR_EXT_SVC_LIMIT` = "Servizio esterno: limite di utilizzo
superato. Rivolgiti all'amministratore." — uniform for any remote
service with rate/quota.

Contextual migration: `agent_runtime.py` replaced the inline messages
for loop_break (`"(stop: 3 step consecutivi senza progresso)"`) and
cap_steps (`"(stop: superato cap di 30 step)"`) with
`msg("MSG_LOOP_BREAK", n=..., last_error=...)` and
`msg("MSG_CAP_STEPS", cap=...)`. The new messages are explanatory and
suggest how to reformulate the request, instead of closing with an
opaque code.

(Note: the actual user-facing strings remain in Italian, as it is the
project's primary language at this stage. Only the ADR text is in
English. A second `MESSAGES_EN` dict can be added later for full
i18n; the architecture supports it without executor changes.)

## Alternatives considered

**Inline strings in the executors.** Pro: zero infrastructure. Con:
duplication, drift between executors saying the same thing in different
words, total block for future i18n. Previous state, abandoned after
Roberto made the rule explicit.

**Per-family repositories** (`runtime/errors.py`, `runtime/warnings.py`,
`runtime/messages.py`, `runtime/logs.py`). Pro: separation of concerns.
Con: 4 imports in every call site, 4 dicts to keep aligned; the
templates all share the same shape (`code -> string with format`),
no reason to split them beyond the prefix. Rejected.

**i18n from day one** (per-locale dicts, `MESSAGES_IT`, `MESSAGES_EN`,
runtime selection). Pro: ready for multilingual. Con: today we are
Italian-only; the locale-selection overhead is premature. Adopted the
form `MESSAGES = {...}` (Italian implicit); adding a secondary dict per
locale is a backward-compatible extension.

**Template engine** (Jinja, str.Template). Pro: power. Con: templates
are simple (a few `{var}` per line), `str.format` is enough and brings
no dependencies. Rejected.

## Consequences

Executors that hit standardized errors now return both `error_code` and
`error`. The first executor to use the new pattern is
`get_files_metadata` (28/4/2026). The other 12 executors still return
only the textual `error`; they will be migrated incrementally when
touched for other reasons (no mass refactor to avoid disturbing
signatures).

The dict is small today (8 ERR, 2 WARN, 7 MSG, 6 LOG). It will grow.
When keys exceed ~100 we may evaluate a per-domain partition
(`messages_fs.py`, `messages_net.py`), but we are far from that
threshold.

The pattern enables a future translation pipeline: by adding a second
`MESSAGES_EN = {...}` and selecting locale per channel (e.g., user
preference on Telegram), Metnos can answer in multiple languages
without modifying any executor.
