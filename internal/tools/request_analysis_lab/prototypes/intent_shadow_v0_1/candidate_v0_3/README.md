# Candidate v0.3 — prompt-only overlay

This offline laboratory candidate changes only the model-facing prompt projection.
It imports the candidate v0.2 registry, schema, validator, compiler, and adapter
contracts and pins their frozen bytes. The separate v0.2.1 BCP47 prerequisite
adds the complete IANA grandfathered-tag table without changing workload bytes. The prompt is
query-free, locale-neutral, registry-derived, and gives the three root kinds equal
placement before the catalog.

This is not production code. A future production port must use immutable typed
objects rather than laboratory JSON dictionaries.
