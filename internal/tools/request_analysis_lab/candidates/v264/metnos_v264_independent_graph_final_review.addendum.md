# V26.4 independent graph review — technical addendum

Date: 2026-08-08.

This addendum corrects one sentence in
`/tmp/metnos_v264_independent_graph_final_review.md` without rewriting the
already published review artifact.

The statement that the frozen registry permits no dependency chain deeper than
one is technically false. The frozen registry explicitly allows a
`spatial.position` output to feed the `entity` slot of another
`spatial.located_at` atom through the declared
`spatial_position_as_entity` coercion. A depth-four chain is therefore
representable with the frozen registry itself, and depth five is rejected by
the frozen depth bound.

The independent probe used a temporary, restored `identity.same_as` adapter to
exercise the same generic depth and diamond logic. That test remains valid,
but the qualification that a synthetic extension was necessary is withdrawn.

This correction does not weaken or change the PASS verdict. It removes an
overly restrictive description of the registry and strengthens the conclusion
that the frozen graph can represent deeper typed compositions.
