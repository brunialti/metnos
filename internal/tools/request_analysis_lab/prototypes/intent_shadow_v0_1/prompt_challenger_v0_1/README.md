# Prompt challenger v0.1 — frozen before holdout

This laboratory package starts from the byte-exact frozen `S0_CURRENT` prompt.
Its only model-facing change is the reviewed coverage/root delta recorded in
`prompt_diff_inventory_v0_1.json`: one S0 line is replaced by three lines and
one new first bullet is inserted under `FINAL MINI-CHECK`.

Schema, registry, positive examples, IR core, validator, compiler, structured
response contract, and adapter semantics remain inherited and byte-bound. The
same registry-derived, Unicode path is used for every structurally valid BCP47
tag; there is no language allowlist, query rule, benchmark identifier, or
runtime repair.

`pre_holdout_chronology_v0_1.json` is frozen with this package. The build write
path fails if a new holdout or RUN4 namespace already exists. A future holdout
must bind the exact challenger freeze hash, establishing the order without
making holdout data an input to this package. No holdout was created here.

This is laboratory code only. It performs no GPU, network, endpoint, service,
runtime, or production operation. JSON dictionaries are confined to laboratory
requests and artifacts; a production port would require immutable typed objects.
