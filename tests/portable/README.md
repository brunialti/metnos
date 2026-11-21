# Portable verification: development and release

Use the Python interpreter from a dedicated test environment with
`tests/portable/requirements.txt` installed. Run commands from the repository
root. The portable suite does not require the full application environment.

Every test here must have its required source files in the public export.
Checks for private administrative tooling belong in the development-only
`tests/internal/` suite. In particular, the local Synth review launcher is
checked there; `test_synth_review.py` verifies the review entry shipped in the
public source.

## During development

Select the changed boundary's test files and its directly affected integration
tests. For development, use pytest file paths and `file.py::test_name`
selectors.

For example, changes to boundary policy comparisons use these four families:

```sh
python -m pytest -q -p no:cacheprovider --import-mode=importlib --confcutdir=tests/portable tests/portable/test_contract_boundary_policy_projection.py tests/portable/test_contract_boundary_birth_policy.py tests/portable/test_contract_boundary_standalone_characterization.py tests/portable/test_policy_test_support.py
```

Choose tests by the behavior and dependencies changed, not a case-count
budget or filename match alone. Include consumer tests when a shared contract
changes. Expand to the general suite when the impact is unclear. Recheck the
productive graph when its boundaries or inventory change. A focused pass is
not release certification.

Complete the causal increment locally before publishing it. Use one complete
public run at its conclusion, or when a required platform is unavailable
locally, rather than publishing each intermediate diagnostic edit. Record
the tested commit, selection and result so unchanged checks are not repeated
merely to increase the test count.

## General suite and public verification

Run the general portable suite directly:

```sh
python -m pytest -q -p no:cacheprovider --import-mode=importlib --confcutdir=tests/portable --ignore=tests/portable/rm0008_2a_acceptance tests/portable
```

To check that every test can be collected using only the declared dependencies,
create a fresh environment, install `tests/portable/requirements.txt`, and run:

```sh
python -m pytest --collect-only -q -p no:cacheprovider --import-mode=importlib --confcutdir=tests/portable tests/portable
```

The frozen acceptance activities have their own runners in
`rm0008_2a_acceptance/`. Collection checks imports without running test bodies.
Collection on Linux does not qualify imports specific to Windows.

A focused local run does not replace the complete
[portable certification workflow](../../.github/workflows/portable-contract-store.yml)
on the exact published commit. It includes Linux and Windows general suites,
native identity and sandbox/service proofs, frozen acceptance activities and
the final summary.

Keep real crash, concurrent-access, permissions and recovery checks. Run
privileged service probes only in the workflow's disposable environment,
never on a production host. Platform or environment skips are not successful
proofs of those capabilities; the dedicated native jobs must exercise them.
Declare native platform requirements on the consuming tests: importing a
fixture does not carry the source module's platform markers. Keep independent
SQL and logical assertions portable, while preserving the native custody cases.

## Removing duplicate cases

Remove a case only when a retained test checks the same boundary and catches
the same defect. Preserve exact types, values, ordering, limits and negative
outcomes. Verify the coverage mapping and introduce controlled mismatches to
check the retained assertions. Do not replace independent cases with a loop
just to make the reported count smaller. Timing, not count alone, determines
which checks are expensive.
