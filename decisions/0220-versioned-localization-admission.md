---
id: 0220
title: Versioned localization resources and atomic instance activation
date: 2026-08-23
status: accepted
area: runtime | i18n | executors | devices | tutor | documentation
related:
  - 0092
  - 0152
  - 0173
  - 0219
---

## Context

Metnos already had bilingual prompt directories, manifest prose tables, an
output-message database and a separate input lexicon. Their translators used
different discovery rules and state files. A directory that already existed
could be aligned, but a newly requested language did not have a complete,
reproducible inventory. File dates and finite Italian/English branches could
not establish that proposer, planner, executor contracts, deterministic UI,
remote devices and Tutor all represented the same instance language.

Nominal translation completion was also weaker than runtime admission. A
candidate could be syntactically plausible while losing a placeholder,
changing a canonical key, adding a private URL or remaining unreadable by the
real prompt loader. Updating the instance language before every consumer was
ready would create a partly localized runtime that could survive a restart.

## Decision

Every localizable surface is a versioned resource identified by a stable
`resource_id`, layer, source and target BCP-47 tags, and the SHA-256 hash of its
semantic source. `runtime/i18n_registry.py` persists those identities and
their lifecycle. Its workers use expiring lease tokens, bounded attempts and
explicit `manual_review`; it contains no translation provider and no finite
language list.

`runtime/i18n_materializer.py` deterministically enumerates prompt Jinja/YAML,
localized manifest prose, message and UI catalogs, detection concepts, public
HTML, the public device catalog and the derived Tutor catalog. Registration
precedes placeholder creation and no model runs during materialization.
Generated reciprocal `hreflang` links are excluded from the public document's
semantic hash, so admitting a locale does not recursively stale every sibling
translation.

`runtime/i18n_pipeline.py` accepts a provider-neutral text translator. It
preserves Jinja expressions, format fields, code spans, YAML and JSON keys,
HTML structure and canonical concept identifiers. Candidates live outside the
runtime path until structural validation and semantic-equivalence review have
passed. Manifest promotion compares the complete technical TOML structure,
writes atomically and signs the resulting contract. Pending prompt candidates
are never visible to `prompt_loader`.

Input regexes and concepts whose registry policy is `manual` are reported as
typed exceptions rather than treated as missing translations or silently
generated. The policy lives with the concept, not in a Python list of names.

`runtime/i18n_activation.py` is the only activation transaction. Before the
signed configuration changes, it requires current source hashes, complete
reviewed coverage, proposer/planner equivalence, manifest validation and
signatures, deterministic-key coverage, a public-only device bundle and a
successful Tutor compilation. It rereads the promoted live artifacts and
rejects unresolved sentinels, missing resources, unknown registry entries and
new private-looking paths, addresses or hosts. Only then does it atomically
write `instance_lang=<target>, state=active`; restart is an explicit
administrative option.

The nightly systemd job reads the signed pending or active target, refreshes
the inventory and processes a bounded batch. It never activates a language.
When no target exists it exits successfully as a no-op. UI and service
registries expose stable message keys, so a third language uses the same data
path as existing baselines instead of a new conditional branch. The remote
device shim contains no embedded prose and is generated only from the released
public message seed.

## Consequences

A language is now data admitted for one instance, not a fork of the runtime or
a preference attached to a user, channel or turn. A translation interruption
is resumable and idempotent; source drift creates a new version and stales the
old one. English bootstrap remains operational until an administrator sees a
passing coverage report and activates the target.

The stricter gate deliberately makes localization slower than copying files.
Public documentation and executor prose require semantic review, and safety-
sensitive input forms remain explicit exceptions. This cost prevents partial
or structurally altered language packs from acquiring runtime authority.

## Verification

`tests/runtime/i18n/` covers registry leases and drift, deterministic
materialization, provider-neutral validation, semantic review, UI/service
catalog enumeration, policy lint and activation. The full acceptance fixture
activates a synthetic third language twice, proves idempotence, target prompt,
manifest, message, input, public documentation and device rendering, then
proves controlled bootstrap fallback for one absent prompt. Manifest suites
verify all installable signatures and builtin birth behavior.
