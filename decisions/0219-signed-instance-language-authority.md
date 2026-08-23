---
id: 0219
title: Signed instance-language authority and non-overridable request context
date: 2026-08-23
status: accepted
area: runtime | installer | i18n | configuration
related:
  - 0092
  - 0152
  - 0173
  - 0218
---

## Context

The installer wrote an unsigned `desired_locale.json` for an untested target,
while the runtime independently read `METNOS_LANG`. `i18n.current_lang()` could
then be replaced by a request-local `ContextVar` sourced from a user preference,
channel or recurring task. There was no single answer to “which language does
this instance use?”, no authenticated target request, and no restart-stable
state connecting installation to runtime.

The language choice occurs before phase 3 creates the installation author key.
Signing at the prompt would therefore require either a second trust root or an
improper change to the installation trust lifecycle.

## Decision

Language is one property of the Metnos instance. `runtime/config.py` defines
the boot-resolved `INSTANCE_LANG`, `REQUESTED_LANG`, and `LOCALIZATION_STATE`.
`i18n.current_lang()` returns only that instance value. A request context may
propagate it across asynchronous boundaries but cannot replace it; the legacy
call sites and user-preference lookups are removed separately in RM-0005/F1.

The installer keeps the accepted operational language, optional target and
localization state in the consent record. After phase 3 has created or reused
the installation author key, it writes one canonical JSON document at
`$METNOS_USER_STATE/i18n/localization_request.json`. The document contains:

- schema identity;
- operational and requested BCP-47 tags;
- `active` or `bootstrap_english` state;
- UTC request timestamp;
- deterministic SHA-256 identity of the localization corpus;
- an embedded Ed25519 signature made by the installation author key.

The write uses the common private-file path: temporary file, flush, `fsync`,
`os.replace`, mode 0600, then signature and payload verification. An identical
installer run is byte-idempotent. A corpus change updates the signed corpus
identity while retaining the original request timestamp; a changed target or
state creates a new timestamp.

BCP-47 validation is structural and language-neutral. It accepts no configured
language allowlist, stores case-insensitive tags in lowercase, and rejects
empty, malformed, duplicate and private-use-only tags. A bad input or invalid
signed document never prevents boot.

Resolution order at boot is:

1. a structurally valid request whose Ed25519 signature verifies with the
   current installation author public key;
2. a valid `METNOS_LANG` bootstrap value for installations without such a
   request;
3. Italian with an explicit invalid-configuration diagnostic for legacy
   installations that have no valid authority.

An invalid signed document is ignored rather than partially trusted. English
remains the operational language for an unready requested locale under
`bootstrap_english`; later activation remains subject to the RM-0005 coverage
gate and a controlled restart.

## Consequences

- User, channel and turn data cannot change the operational language.
- Installer and runtime share one authenticated, restart-stable state instead
  of parallel locale notes.
- Key creation remains in phase 3 and no second signing mechanism is added.
- Existing installations without the signed document still boot from their
  process configuration and can materialize it on an installer rerun.
- F1 remains necessary to delete now-ineffective preference lookups and
  `language_context` call sites; their presence no longer changes behavior.

## Verification

`tests/runtime/i18n/test_instance_language_config.py` covers structural BCP-47
normalization, restart, a conflicting bootstrap environment, malformed input,
signature tampering, byte-idempotent installer persistence and rejection of a
request-level override. The complete i18n suite verifies that explicit
resource-language APIs still support translation and coverage tooling.
