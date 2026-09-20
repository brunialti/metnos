# Internal release lifecycle gate

This directory is excluded from the public Metnos export. The verifier checks
the release lifecycle without modifying or restarting the running instance.

## F5 administrative launcher

`install_f5_authority.sh` generates the separately installed administrative
launcher. Both Python interpreters use `-B`; the launcher sets its workspace
outside the signed release and validates mutable paths before importing F5.
`HOME`, user data/state/config/cache roots and the workspace must be absolute,
contain no `..` components, and remain disjoint from the selected release and
verifier trees after resolving symbolic links. Ancestors of either tree are
also refused. Valid external paths retain their configured values.

`refused: unsafe F5 path NAME` identifies the environment setting to correct.
No application module has been imported at that point, and the launcher does
not relocate existing administrator data. Import-time initialization in
`runtime/config.py` remains active for valid configurations.

Run the development checks from the repository root:

```sh
python -m pytest -q tests/internal/test_f5_authority_launcher.py tests/portable/test_f5_authority_entry.py
```

These launcher tests execute the generated shell and real Python imports in
temporary writable trees, compare contents and metadata, and remove individual
protections as negative controls. They do not install the launcher, restart
services or certify the privileged F5 operations. The launcher checks remain
private because their installer is excluded from the public export.

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

## Measurement harnesses (added 2026-08-08)

Small A/B benches that make a change provable instead of plausible. They run
against the live instance and change nothing.

For the active structured request-analysis work, do not start from an
individual scratch benchmark. Read
`request_analysis_lab/README.md` and
`../design/handover_request_analysis_8_8_2026.md` first. They identify the
single current freeze, rejected variants, denominators, hashes, and the next
safe command.

- `misura_intent.py` — 13 control queries through the real intent extractor.
  `--dump prima.json` before a change, `--confronta prima.json` after; the
  output names every query whose clauses moved. Temperature 0 and a fixed seed
  make the difference a difference, not noise. It is what showed that a rule
  added to the intent prompt changed 0 of 13, and that the binary open-source
  probe changes exactly 1 — the intended one.
- `misura_ambito.py` — asks whether the planner DECLARES the goal scope.
  **Known limitation**: it calls the proposer with a hand-written `Intent`, so
  the plan it returns omits the domain precursors the engine adds in
  production. Read its result as a hint, never as a verdict.
