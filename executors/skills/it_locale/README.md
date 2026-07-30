# `it_locale`: Italian locale helpers

This first-party bundle contains deterministic helpers whose data formats or
recognition rules are specific to Italy. It is a support library for executors,
not a planner-visible executor itself.

## Current contents

```text
it_locale/
├── SKILL.md
├── README.md
├── vendors.json
└── scripts/
    └── bills_extract.py
```

`bills_extract.py` recognizes supported Italian bill layouts and returns
structured billing records. `vendors.json` is the explicit provider catalog
used by that parser. Executor code reaches the helper through the installed
skill path rather than embedding locale-specific provider rules in a generic
executor.

## Boundary

- The bundle does not contain a root `manifest.toml` and is not loaded as an
  executor.
- User-visible messages remain in the Metnos i18n store, not in this README.
- Provider recognition is deterministic and data-driven.
- A new locale-specific helper belongs here only when the rule cannot be
  expressed by a locale-neutral executor and the data source is maintainable.

Other locales should use separate sibling bundles with the same boundary;
Italian examples and provider names must not become defaults in generic code.
