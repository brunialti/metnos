# Prompt challenger v0.1 — adversarial self-review

The delta surface is closed: line comparison permits exactly one removed line
and exactly four added lines. The three positive JSON examples, registry data,
schema, current request envelope, and inherited core/adapter remain unchanged.
The `outside_registry` reason already exists in the reviewed registry; this
package adds no reason, route, precedence rule, repair, or output field.

Universal behavior is checked with synthetic renamed registries, unrelated
Unicode queries, regional/private/grandfathered BCP47 tags, and malformed tags.
Anti-leakage checks cover prior open regression queries and forbid benchmark
identifiers or query-specific branches in the package logic. No future holdout
is read, generated, named as an input, or imported.

Chronology is one-way: the write path verifies absence first, then freezes this
package; any later holdout must bind this freeze SHA-256. The normal integrity
check intentionally verifies the historical proof rather than rejecting a
legitimate holdout created later.

Residual limitation: this is an offline prompt hypothesis, not evidence of live
semantic accuracy. It must not be promoted or measured until the separate
holdout protocol is explicitly built and audited.
