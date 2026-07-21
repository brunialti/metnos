<div align="center">

# Metnos

**A self-hosted architecture for governed agents, shaped by its signed executor set.**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
![Status](https://img.shields.io/badge/status-pre--1.0-orange)
![Python](https://img.shields.io/badge/python-%E2%89%A53.12-green)
![Self-hosted](https://img.shields.io/badge/cloud-optional-success)
[![Docs](https://img.shields.io/badge/docs-metnos.com-1A477A.svg)](https://metnos.com)

*mētis* (practical intelligence) + *noûs* (mind).

[Architecture documentation](https://metnos.com) | [Installer guide](install/README.md) | [Security](https://metnos.com/security/)

</div>

> [!IMPORTANT]
> Metnos is working pre-1.0 software, not a turnkey consumer product. The public
> repository is suitable for evaluation, self-hosted experimentation, and
> development. Interfaces and defaults may change when the architecture needs a
> cleaner contract.

## What Metnos is

Metnos receives natural-language requests through a web chat or Telegram,
constructs a typed plan, and executes it through **executors**. An executor is a
small capability with a signed manifest, declared arguments, a known result
shape, explicit authority, and a sandbox profile.

The catalog is not limited to a fixed set shipped with the application. Metnos
can synthesize or import additional executors, but they enter through the same
naming, signing, admission, sandbox, and test boundaries as first-party code.
The planner never receives an unrestricted shell of arbitrary tools.

The default planning path uses local LLM tiers. Remote frontier models are an
explicit opt-in fallback, not a hidden dependency.

<p align="center">
  <img src="https://metnos.com/assets/architecture-flow.png" alt="A Metnos request is classified into a canonical intent, checked against L0 fastpath and L1 autopath, or proposed as a typed plan. Deterministic guards and a validator check the plan before a direct or intelligent executor runs and verifies its outcome. Failures enter bounded recovery or an honest terminator." width="850">
</p>

## The executor contract

The normative definition is the versioned
[Metnos Executor Standard](EXECUTOR_STANDARD.md). New executors must satisfy it
before activation; existing executors migrate incrementally without a mass
rewrite.

Every executor follows the same external model:

- **Canonical identity.** Names use `verb_object[_qualifier]` from a governed
  vocabulary, so tool selection does not become an open-ended command language.
- **Typed data.** Executors are vector-oriented: lists in, lists out, including
  the zero- and one-item cases. Results remain pipeable between steps.
- **Declared authority.** Filesystem, network, credentials, device placement,
  and privileged operations are explicit rather than ambient.
- **Honest outcomes.** Partial results, truncation, failed items, and missing
  postconditions are visible. A success result must correspond to an observed
  effect.
- **Reversibility where claimed.** Mutations use a closed set of reverse
  patterns; a move is copy, verify, then delete.
- **Signed identity.** The loader verifies manifests while keeping product
  membership, origin, and transport separate. GitHub executors maintained by
  Metnos are builtin with handcrafted origin, not imports.

The verified reference inventory currently contains 115 planner-visible
executors: 82 handcrafted executors in the source tree, 16 handcrafted GitHub
builtins, and 17 signed in-process runtime executors. All 115 declare the same
Executor Standard; transport is not a Composer preference.

The current first-party catalog is generated from signed manifests and is
published in the [executor catalog](https://metnos.com/en/architecture/executor_catalog).

### Central execution policy

All executor invocations pass through one runtime-owned scheduler. The loader
defaults every missing, legacy, or invalid execution policy to serial execution;
an executor may request concurrency only through signed metadata and verified
equivalence tests.

Parallelism classes are portable ceilings rather than literal thread counts:
`0` stays on the caller thread, while `1`, `2`, and `3` request moderate, high,
and hardware-bounded maximum concurrency. The runtime may always lower a class
according to the backend, available hardware, global limits, or a missing
concurrency identity. It never raises the signed class. This lets one central
policy apply queues, backpressure, resource pools, and timing metrics without
changing arguments, result shapes, planner order, authority, or success
criteria. Read-only work is the simplest case, but create-only and mutating
executors can opt in when they declare an isolation key and pass the stronger
effect-specific gates.

LLM-backed executors use the same model: startup detection maps the configured
inference framework and hardware to an LLM concurrency ceiling. A single-slot
backend remains serial; a batching backend such as vLLM can admit more work
without requiring executor-specific thread pools.

### Verification boundaries

Signed executor birth tests are executed by a common reference runner. A
positive parallelism class additionally requires repeated equivalence runs, so
parallel scheduling is an admission decision rather than an optimistic runtime
guess. Full-suite collection redirects mutable scheduler state and HTTP lock
files to an isolated temporary environment before test modules are imported;
tests therefore cannot operate on a live installation by accident. HTTP
application state is also stored under centrally defined typed keys, preventing
route modules from silently colliding on string names.

## Intelligent executors

Some tasks have a stable purpose but an unpredictable route. Website login is a
typical example: the relevant control may be a link, a dialog, a two-step form,
or a page in another language.

For these cases Metnos can place a **narrow-mandate agent inside an executor**.
The planner still sees the same input and output. Internally, the executor runs
a bounded cycle:

```text
observe -> resolve -> verify preconditions -> consent -> act -> verify outcome
```

Deterministic resolvers run first. An LLM handles only residual ambiguity and
may select only actions enumerated by the runtime. It cannot change the goal,
grant itself authority, expose credentials, or turn an unverified state into
success. See [Intelligent executors](https://metnos.com/en/architecture/intelligent_executors).

## Skills and backends

Metnos separates two ideas that are often fused:

- A **skill** controls which group of capabilities is installed, enabled,
  dormant, or available to a user.
- A **backend** decides how a canonical capability reaches a concrete provider
  or local implementation.

The provider is selected from configuration, not inferred from wording in the
user request. The planner can therefore keep using one canonical executor while
the backend changes underneath it.

Core capabilities need no external service. First-party skills cover system,
photos, mail, web, geocoding, calendars, GitHub, Google Workspace, SQL-backed
stores, and the optional frontier tier. A skill with a missing prerequisite is
reported as dormant rather than pretending to work.

```bash
python3 runtime/cli/skills_cli.py list
python3 runtime/cli/skills_cli.py disable github
```

## Security model

Metnos assumes that generated and third-party code may be wrong. Trust is built
at several boundaries:

1. The closed vocabulary limits what may become a planner-visible capability.
2. Signed manifests bind identity, code digest, schema, and declared authority.
3. Runtime validation removes arguments that are outside the manifest schema.
4. Filesystem grants are derived from the actual call rather than a broad static
   permission.
5. Sensitive or destructive actions pass through explicit consent and mandate
   checks.
6. The execution sandbox limits filesystem, network, process, and device access.
7. The result is checked against an observable postcondition where the action
   requires one.

Credentials are stored encrypted and resolved at execution time. LLM prompts do
not receive raw secrets. Persistent credential mandates can restrict how a site
account is used in both interactive and scheduled requests.

## Remote devices

A registered Windows or Linux device can execute signed invocations from the
Metnos server. The client is outbound-only, verifies the server signature,
enforces the local sandbox, returns a signed result, and retries result delivery
without repeating the action.

Device placement is part of the executor contract. A request about a named
computer is not silently executed on the server when the target device is
unavailable.

## Web and graphical sessions

The website executors use a local Playwright service. They support headless
sessions and a graphical side browser, with independently configurable browser
surface and automation-reduction techniques. Login and navigation keep secrets,
origin checks, consent, and allowed actions outside the model.

This is controlled browser automation, not a promise to bypass a site's access
policy. A site may still require user interaction, reject automation, present a
CAPTCHA, or revoke a session.

## MCP status

Metnos does **not** currently expose MCP tools to the planner. The integration
design treats MCP as a possible transport at the system boundary, not as a
replacement for the executor contract.

An admitted MCP tool would need to become one of:

- a backend of an existing canonical executor;
- a narrow proxy executor with mapped authority and validated results;
- a quarantined capability that is not available to the planner.

A generic public `mcp_executor(server, tool, args)` is intentionally excluded
from the design because it would create a second command language and bypass
per-capability mandates. Native conversion, where technically possible, would
be a reviewed promotion with equivalence tests rather than an automatic rewrite
of remote behavior.

## Requirements

The managed server installation currently targets Linux with Python 3.12 or
newer and systemd. Planning quality depends on the LLM assigned to the logical
`fast`, `middle`, and `wise` tiers; Metnos does not require one hard-coded model.
The endpoint may be local or remote and must implement the configured compatible
API.

The installer can provision optional self-hosted services for web search,
geocoding, visual-language processing, and Playwright. Features that depend on
an omitted service remain dormant or return an explicit degraded result.

Hardware requirements vary mainly with the models you choose. A CPU-only or
remote endpoint is valid but will have different latency and privacy trade-offs
from a local accelerator.

## Install

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh --check
bash install/bootstrap.sh
```

The installer is idempotent and may be resumed. It creates the Python
environment, runtime directories, encrypted credential store, selected model
bindings, optional companion services, systemd units, and the first admin
onboarding link.

After installation, use the managed service or start the HTTP server directly:

```bash
python3 runtime/metnos_http_server.py --host 0.0.0.0 --port 8770
curl http://127.0.0.1:8770/agent/health
```

Read [install/README.md](install/README.md) before choosing model endpoints,
optional services, or a non-interactive installation.

## Documentation

The architecture site is maintained in Italian and English and includes:

- the request engine, caches, and deterministic guards;
- executor anatomy, lifecycle, catalog, and intelligent executors;
- sandboxing, remote execution, pairing, and policy;
- skills, backends, importing, localization, and observability;
- web interaction, user approvals, and runtime channels.

Start at [metnos.com](https://metnos.com). Documentation describes implemented
behavior unless a section is explicitly marked as a proposed constraint.

## Public repository

This repository is a sanitized, deterministic export of the run-essential
source tree. Internal reports, private state, credentials, benchmarks, and
development-only documentation are excluded. The export gate scans the final
tree for secrets and identifying data before publication.

Issues and patches are welcome. Include the failing request, relevant platform,
observable result, and logs with secrets removed.

## License

[AGPL-3.0](LICENSE). If you run a modified version as a network service, the
AGPL network-use clause applies.
