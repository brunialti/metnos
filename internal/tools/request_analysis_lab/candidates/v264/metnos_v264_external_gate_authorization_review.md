# V26.4 external gate authorization review

Date: 2026-08-08.

Verdict: **AUTHORIZE ONE K1/34 RUN** after, and only after, the external gate
verifier passes on the exact lock and frozen artifact chain described here.

This authorization is limited to one native evaluation of the 34 frozen Phase
1 controls, one model call per case, zero retries, and the exact output path:

`/tmp/metnos_v264_typed_phase1_controls_k1.json`

It does not authorize runtime cutover, the principal 109 suite, historical
holdout execution, a second repetition, a different output path, or edits to
the frozen bytes.

Required roots:

- V26.4 freeze:
  `83d3b4441d83d8f90e5eb591f9eeaf052885150e6806cd3ceefd046d948ccde6`;
- typed registry:
  `448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f`;
- contamination audit:
  `6845ef5ab1a3b7f7871909a285b070c292cfe97b2cf923471ebfeee99af78ff9`;
- oracle freeze:
  `f2f5d6046a7fb0b6fead8f5ab27c35c230aebd928411d5d5a962bce90209a70f`;
- independent graph review MD:
  `9e77d7261a144615d2329febefcfbab1d2b05c174e2c43819d149bf8d6dca35a`;
- independent graph review JSON:
  `ccbc518837af72a60e67da5e2ae445bffd17f64ff1dfba727d54fadcd2c8ac62`;
- mandatory review correction:
  `53254dca278c2d2da7f4ac2958502866b3758dd332f672643950c87ccffe1c4d`;
- independent probe result:
  `7b0ff821783d9f5d071b4da0ccaa7f562f57afd47136cfc73c863a12663fb62c`;
- external verifier:
  `9fec580283aa514b16e685d8f5117fd06878145ee43c4a714a6bfc8960facec5`.

The correction must exist byte-identically both in `/tmp` and at
`/opt/metnos/internal/tools/request_analysis_lab/candidates/v264/metnos_v264_independent_graph_final_review.addendum.md`.

The verifier is
`/tmp/metnos_v264_external_gate_verifier.py`. It must validate actual hashes,
oracle transitive artifacts, contamination status, review verdict and
correction, probe result, exact output absence and the frozen runner's own
gate validator. Its mutation suite must reject altered gate status, freeze,
registry, reviews, oracle artifacts, contamination audit, probe result,
verifier, case count, repetition count, output path, author-gate substitution,
missing/extra fields and a pre-existing output.

No server/model call was used to create this authorization.
