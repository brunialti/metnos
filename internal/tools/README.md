# Internal release lifecycle gate

This directory is excluded from the public Metnos export. The verifier checks
the release lifecycle without modifying or restarting the running instance.

## What is duplicated

- two public code trees, normally about a few tens of MB;
- one virtualenv per distinct `requirements.txt` digest;
- small synthetic config, data, state, and workspace directories.

It does **not** copy models, production credentials, browser profiles, SearXNG,
Photon, Playwright, or the LLM server. Isolated Metnos HTTP processes run one at
a time on temporary ports and reuse the configured/default LLM endpoint.
Credential compatibility is checked with a random synthetic credential created
through the real encrypted credential API; only its fingerprint enters reports.

## Run

Use a previous public checkout as baseline and the current public export as
candidate:

```bash
python3 tests/tools/release_gate.py \
  --baseline /path/to/previous-public-checkout \
  --candidate current
```

For a remote public baseline:

```bash
python3 tests/tools/release_gate.py \
  --baseline https://github.com/brunialti/metnos.git \
  --baseline-ref main \
  --candidate current
```

Normal runs create isolated virtualenvs. `--skip-dependencies` is only a fast
developer diagnostic and is recorded as `dependency_mode=current-python`; it is
not sufficient to certify dependency installation.

The JSON report defaults to `/tmp/metnos-release-gate-*.json`, with a Markdown
view beside it. Successful work directories are removed. Failed runs retain
their isolated directory and logs; `--keep` retains successful ones too.

## Result boundary

`PASS` certifies the `portable-isolated` profile: exported code, dependencies,
executor catalog, direct HTTP boot, health, real turn, persistent-data
compatibility, rollback compatibility, and production non-interference.

It does not certify system packages, systemd policy, hardware acceleration, or
large optional sidecars. Those need a managed test host/profile and must never
be inferred from this result.

## Executor migration inventory

`executor_conformance.py` produces the deterministic core-executor migration
inventory. It classifies structural findings and applicable risk gates but does
not edit manifests or certify semantics:

```bash
python3 tests/tools/executor_conformance.py
```

Per confrontare il filesystem con il catalogo realmente costruito dal runtime:

```bash
python3 tests/tools/executor_conformance.py --live
```

Il report live separa quattro superfici, che non sono intercambiabili:

- `configured`: manifest presenti nelle sorgenti configurate;
- `loadable`: executor costruibili senza verifica della firma;
- `admitted`: executor ammessi dal loader con tutte le verifiche abilitate;
- `planner`: executor effettivamente visibili al planner, inclusi i builtin
  virtuali aggiunti dal motore.

Le differenze tra le superfici sono debito o rifiuti da spiegare, non executor
da contare due volte. Il report espone inoltre origine, trasporto e stato dello
standard usando gli stessi metadati canonici del catalogo runtime.

The JSON report is the machine source and the adjacent Markdown file is only a
view. A structurally ready executor still requires its behavioral, authority,
paraphrase, regression, and E2E evidence before declaring conformance.
