# RM-0008: separate historical retirement from current deployment

Status: proposed, not implemented or activated. This replaces the proposed
ownership change to the development checkout. The change to the retirement
contract needs Roberto's architectural approval before product edits.

## Cause and decision

Production already runs from a signed, immutable release outside the development
checkout. The remaining coupling is in the successor transition:

- `install/birth_authority_provisioner.py::_transition_roots_v2` always treats
  the initial predecessor's installation directory as a root-owned live root.
- `_retire_bound_catalog_v2` calls that function before distinguishing an initial
  retirement from a successor's read-only observation.
- `_observe_previous_retirement_v2` rereads repository retirement artifacts for
  every successor, in addition to the still-live service-unit restrictions.
- `internal/tools/rm0008_release_cycle.py::_verify_retirement_transition` repeats
  the same dependency before stopping services. Removing only this early check
  would merely move the refusal into the authoritative crossing.

Changing the checkout's ownership addresses the first refusal, not this logical
coupling. It also prevents ordinary atomic saves, creation and replacement of
top-level files. Descendant retirement checks have their own ownership and
absence requirements. There is no justification for making all development
files administrative property.

Recommended decision: use the authenticated completed predecessor's existing
retirement receipt for unchanged historical repository retirements. Keep live
checks for current authority, service topology, executable paths, quiescence
and isolation. The checkout supplies untrusted candidate bytes only; it is not
an installation root or a retirement-evidence store for future releases.

## Existing evidence, verified on the host

Read-only administrative observation `run-ty_2t_gz`, 22 September 2026:

- Selected release: 72, transaction state `PREFLIGHT_VERIFIED`; selection stable
  across two authenticated reads.
- Authenticated service catalog: 40 retirement steps, including 20 repository
  steps.
- The installed pure modules recomputed the dominant-startup receipt from the
  selected transaction, signed catalog, prerequisite topology hash and the
  manifest-verified enforcement source. It exactly matches the recorded receipt:
  `sha256:23d91da8f39cf0d3c28d907b5b90990cd110a15f97462062a9d5e782e1dd39ff`.
- Replacing the retirement-plan digest with a different digest fails the same
  equality. This is a negative check of the existing receipt, not a new
  certificate or a complete regression suite.
- The initial predecessor identity is
  `sha256:89e279f1305730882c5139c2d518c19d80d705075e09a9e385bc38c13380a58e`.
- `/opt/metnos` is uid/gid 1000:1000, mode 0755. The observed `install` and
  `scripts` directories remain 0:0, mode 0755; the observed retired installer
  module remains 0:0, mode 0644. No ownership was changed.

The probe authenticated the fixed ownership state with the installed verifier
before loading hash-verified, pure installed modules. It imported no development
runtime or application configuration and issued no service or publication action.
Probe SHA-256: `721a395a19b36a057a192f66b20bd7283d561793e1def152472a786fc7974ba5`.
Private receipt: `/var/lib/metnos-admin/agent-runs/run-ty_2t_gz/result.json`.

The durable certificate already binds the dominant-startup receipt. No extra
signing key, new service, parallel journal or copy of the whole repository is
needed to represent this historical fact.

## Required behavior

Roberto explicitly requires a general, universal solution. The policy must
depend on authenticated identities and obligations, never on a particular
checkout path, account name, release number or executor name. The three cases
are ordinary control flow in the existing transition, not a new framework:

- Historical fact, unchanged: reuse authenticated completed evidence.
- Current operational condition: observe it now.
- New or modified obligation: verify the delta and bind its result into the
  next receipt; do not recertify unrelated history.

This rule applies to supported installations regardless of their source layout.
Platform-specific filesystem and service operations stay in their existing
adapters. It neither claims unimplemented Windows support nor requires another
policy for Windows. Ordinary individual-executor Birth retains its own scoped
checks; this release correction adds no history scan to Birth or user turns.

1. Initial transition: retain strict one-time retirement and its original
   evidence. An installation without completed authenticated evidence does not
   qualify for the successor path.
2. Successor: select the actual authenticated immediate predecessor, require its
   completed transition, recompute and match its signed retirement receipt.
   Do not accept an arbitrary digest, mutable marker, caller-selected old release
   or merely built/pending/abandoned candidate as that proof.
3. Inherit only identical historical repository steps. A removed, modified or
   newly added step cannot silently inherit the old proof. A changed retirement
   obligation needs an explicit, verified delta; unsupported deltas refuse
   before service shutdown. Do not claim absence from a mutable checkout.
4. Continue observing operational restrictions: exact system units and legacy
   masks, selected runtime and interpreter, required current authority, service
   account isolation and no conflicting legacy process. A historical receipt
   alone does not prove that a competing service cannot run now.
5. Ensure development bytes cannot become privileged runtime imports or regain
   publication authority. The approved named-executor bridge copies a candidate
   into Birth; only the installed signed runtime processes it. Existing signed
   context, generation, capability and receipt checks remain mandatory.
6. Reobserve the selected predecessor and current conditions under the existing
   deployment/startup/maintenance locks. A changed head or unfinished transition
   refuses rather than using a stale early observation.
7. Keep original evidence immutable. Do not rewrite `predecessor-v1.json`, fake
   retirement artifacts, restore retired entrypoints or delete old evidence to
   make the new policy pass.

The intended steady-state property is that normal Git edits, renames or removal
of the development checkout cannot invalidate an already established historical
retirement. Actual changes to production authority or service topology still can.

## Implementation boundary and acceptance

Use one shared completed-retirement verifier for the release controller and the
authoritative transition. Split repository-history reuse from live unit
observation in the existing retirement code; do not add a second release path.
Keep root ownership checks for actual administrative roots. Do not replace them
with permissive ownership rules for arbitrary paths.

Focused acceptance must cover:

- valid completed evidence without reading the old repository, including an
  absent or renamed development tree;
- changed plan/receipt/signature/head, incomplete and abandoned transitions,
  and replay under a different installation;
- unchanged first-transition requirements and explicit refusal of unproven
  deltas before stopping services;
- recreated competing units or execution paths, and stale/development code
  unable to publish into the current store (isolated tests, not attacks on live
  production);
- repeat, interruption and resume through the same controller;
- fresh operational checks, signed release activation and the actual R-003
  named-executor publication, authenticated catalog reread and harmless real turn.

The current signed but inactive release 73 does not include this change. Preserve
it as evidence and let the release controller resolve its pending state using
the supported procedure; never patch its immutable files or force selection.
No new release or R-003/F5 completion is claimed by this analysis.
