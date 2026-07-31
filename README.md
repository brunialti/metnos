<div align="center">

# Metnos

**A self-hosted assistant that turns natural language into governed action.**

Across your files, mail, calendars, web, services, and paired devices — with
typed plans, explicit authority, and undo wherever the operation safely
supports it.

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-%E2%89%A53.12-green)
![Self-hosted](https://img.shields.io/badge/cloud-optional-success)
[![Docs](https://img.shields.io/badge/docs-metnos.com-1A477A.svg)](https://metnos.com)

*mētis* (practical intelligence) + *noûs* (mind).

[Explore the documentation](https://metnos.com) ·
[Take the visual tour](https://metnos.com/en/Metnos_QuickTour) ·
[Install Metnos](install/README.md) ·
[Read the policy and authority model](https://metnos.com/en/architecture/policy.html)

</div>

## Ask for the outcome

Metnos is made to be asked in ordinary language:

| Ask Metnos | What happens |
|---|---|
| “Find the exact duplicate images in Pictures. Count every file and directory first.” | It scans the requested tree, reports complete counts, and declares a duplicate only after matching the full SHA-256 content digest. A display limit never becomes a false scan limit. |
| “Find last month's invoice emails, extract the date, supplier, and amount, and create a spreadsheet.” | It composes mail search, structured extraction, and document creation into one typed data flow. |
| “Move these messages to Trash.” | It keeps each UID bound to its account and source folder, uses a safe server-side move, and fails closed if the server cannot target only those messages. |
| “Where can I see the configured models, and how do I change them?” | The integrated Tutor answers from the installed documentation and gives the exact path through the web interface. |
| “On my Windows PC, sort these photos into folders by year.” | Compatible steps run on the paired device; planning, consent, signing, and audit stay on the Metnos server. |

You describe the result. Metnos determines which admitted capabilities can
produce it, how their outputs connect, where they must run, and which decisions
still belong to you.

## Easy to start, built to remain understandable

You do not need to learn executor names before Metnos becomes useful. Run the
read-only preflight, follow the guided installer, open the web chat, and ask for
an outcome. The six installation phases are idempotent and resumable: an
interrupted model download or Tutor build can continue instead of turning into
a fresh installation.

When you do not know whether a feature exists, where a setting lives, or why an
operation stopped, ask Metnos itself. The integrated Tutor answers from the
documentation and manifests installed on that instance, in the user's language,
and links to the relevant official page. The product therefore remains
discoverable from the same chat in which it is used.

## Not a shell with a chat box

Metnos receives requests through a web chat or Telegram. It classifies each
request into a canonical intent, builds a typed plan, checks that plan against
policy and declared authority, and executes it through **executors**.

An executor is a small capability with:

- a canonical name and typed arguments;
- a signed manifest and code digest;
- a declared result shape and execution policy;
- explicit filesystem, network, credential, and device authority;
- an observable success condition;
- a reverse pattern when the operation is genuinely reversible.

The planner never receives an unrestricted shell or an arbitrary bag of tools.
It sees only the capabilities admitted by the installed executor set. Shell
access exists as a declared, policy-gated, disableable system capability — not
as an open channel the planner can use outside that contract.

<p align="center">
  <img src="https://metnos.com/assets/architecture-flow.png" alt="A Metnos request becomes a canonical intent and a typed plan, passes deterministic guards and policy, then runs through a direct or narrow-mandate executor before an observed result is reported." width="850">
</p>

## Why it feels different

- **One request can become a real workflow.** Typed list-in/list-out results
  connect files, messages, events, contacts, pages, places, and documents
  without ad hoc glue.
- **The answer follows the evidence.** Counts, partial failures, truncation,
  and postconditions come from executor results. Generated prose cannot turn a
  bounded display into a complete scan or an estimate into an exact match.
- **Local-first by default.** Local LLM tiers can plan and answer without a
  cloud model. A remote frontier tier is an explicit, credentialed fallback;
  if no local planning tier is bound, the installer says that turns will use
  the remote tier instead.
- **Authority is minted for the call.** Filesystem access follows the resolved
  path; provider access follows the selected backend; device placement follows
  the request and manifest. Authority is not ambient.
- **It asks before protected actions.** Policy can allow, refuse, or suspend a
  step for explicit consent. Approval is bound to the requesting user and
  operation.
- **It can take work to the data.** Signed Windows and Linux clients execute
  compatible invocations outbound-only and return signed results without
  becoming independent agents.

## Undo means an inverse operation

Say: **“Undo the last operation.”**

Undo is not an LLM improvising a repair. For an operation that supports it, the
executor declares a governed reverse pattern — or, for a few domain stores, a
declared domain reverse — and the runtime records the exact resources affected.
Reversal runs against the same account and, for remote work, the same device. A
second undo is idempotent.

Not every action can be reversed. A sent email cannot be recalled, and an API
may not expose restoration. Metnos reports those limits, reverses only the
parts it can prove, and gives honest success and skipped counts. It never labels
an operation undoable merely because a model can imagine the opposite command.

## Tutor: Metnos can explain Metnos

Tutor answers questions about capabilities, configuration, interface
navigation, and official documentation before the operational planner gets
involved. It can bind an exact filename, relative documentation path, or
canonical URL to the intended source and include navigable official links.

Its semantic catalog is compiled locally from public documentation, signed
manifests, and admitted runtime registries. Documentation changes invalidate
the catalog and trigger a rebuild. Internal development notes and user files
are not Tutor sources.

Tutor explains; it does not quietly act. If a request contains both a product
question and a separable action, it answers the question first and can hand the
literal action clause to the normal engine only after confirmation. Retrieval
feedback is scoped to the current user and never changes another user's
answers.

## A different design boundary

OpenClaw, Hermes Agent, and Metnos can all be installed locally and used in
natural language. The meaningful difference is not who has a chat box; it is
where each system places trust, guidance, and recovery.

OpenClaw and Hermes both provide capable setup and operating surfaces. Metnos's
distinction is the depth of the integrated whole: one installer provisions and
verifies the stack, one authenticated Settings area governs the instance, and
one Tutor explains the capabilities and live configuration that instance
actually admits.

| | OpenClaw | Hermes Agent | Metnos |
|---|---|---|---|
| **Getting started** | One-line installers followed by guided onboarding; broad desktop and messaging support. | Desktop packages or a one-line installer, then a setup wizard; native Windows is supported. | Four commands on a Linux host start a read-only preflight and six idempotent, resumable phases. They configure the isolated environment, model tiers, optional sidecars, encrypted secrets, services, and verified Tutor catalog — then prove the instance ready. |
| **Everyday use** | A personal assistant across WebChat and many messaging channels. | CLI, desktop, and several messaging channels, with natural-language automation and delegation. | Web chat and Telegram turn ordinary-language outcomes into typed workflows across local, provider, and paired-device capabilities. |
| **Managing the instance** | A Control UI and CLI cover the gateway, channels, agents, and configuration. | Desktop, TUI, CLI configuration, and a self-hosted dashboard provide operational surfaces. | The authenticated web Settings area unifies 14 pages for turns, schedules, index builds, executor lifecycle, memory, editable model configuration, service health and controls, safety, users, and paired devices. |
| **Help inside the product** | Documentation, setup flows, and configuration commands are available from chat and the UI. | `/help`, setup, doctor, and extensive documentation guide the operator. | The Tutor is part of the product: it is compiled from that instance's admitted manifests, published documentation, and permitted live registries. It answers capability, configuration, and UI-navigation questions in the user's language, with exact paths and direct official links. |
| **Execution boundary** | The main personal session runs tools on the host by default; sandbox modes, tool policies, pairing, and security audits are available. | Dangerous-command approval, protected paths, pairing, and optional container or remote isolation provide defence in depth. | The planner sees admitted executors rather than a general shell; signed contracts, per-call authority, policy, consent, and observed postconditions apply to every execution path. |
| **Undo and recovery** | Recovery is session- or tool-specific; the official core documentation does not describe a uniform cross-domain inverse-operation contract. | `/undo` manages conversation state; opt-in project checkpoints can restore files changed by file tools and destructive shell commands. | Supported executors declare an inverse operation and the runtime records the exact resources affected, including account and device identity. Unsupported or irreversible effects are stated explicitly. |
| **Extending the system** | Skills and plugins teach the agent new procedures and tools. | Skills can be installed or learned from experience; MCP and toolsets extend the agent. | New code becomes routable only after its executor name, schema, authority, signature, tests, and admission status satisfy the same governed contract as built-in code. |

The recovery row is deliberately two-sided. Hermes checkpoints can restore
everything below a tracked project root, including changes no individual tool
declared reversible; inside that tree, Metnos has no equivalent snapshot.
Metnos inverse operations instead reach effects a workspace snapshot cannot —
for example a message moved on an IMAP server or files moved on a paired PC —
but only when the responsible executor declares and records a valid reverse.
Neither approach recalls a sent email.

This is a comparison of documented design defaults, not an absolute security or
quality ranking. It was checked against the official
[OpenClaw project](https://github.com/openclaw/openclaw),
[OpenClaw security documentation](https://docs.openclaw.ai/cli/security),
[Hermes Agent project](https://github.com/NousResearch/hermes-agent), and
[Hermes checkpoint documentation](https://hermes-agent.nousresearch.com/docs/user-guide/checkpoints-and-rollback/)
on 31 July 2026; all three projects continue to evolve.

## What it can connect

Core capabilities do not depend on a third-party provider and include local
files, directories, processes, time, scheduling, typed transformations, and
system observations. First-party skills add:

- mail, calendars, contacts, and tasks;
- Google Workspace: Gmail, Calendar, Drive, Docs, Sheets, and Contacts;
- GitHub repositories, issues, pull requests, files, and tasks;
- web search, page and PDF reading, controlled browser sessions, and sites;
- geocoding, places, local photo indexes, and Google Photos;
- SQL-backed stores, selected system operations, and an optional frontier tier.

Skills are enabled, disabled, or left dormant independently. A missing
credential or companion service makes that capability visibly unavailable; it
does not make unrelated features fail.

```bash
./.venv/bin/python runtime/cli/skills_cli.py list
./.venv/bin/python runtime/cli/skills_cli.py disable github
```

Backends remain separate from skills. The planner asks for a canonical
capability; configuration decides whether it reaches a local implementation,
IMAP, Google Workspace, GitHub, or another admitted provider.

## Security is a sequence of boundaries

Metnos assumes generated and third-party code may be wrong. Trust is earned
step by step:

1. A closed vocabulary limits planner-visible capability names.
2. Signed manifests bind identity, schema, authority, code digest, and
   execution policy.
3. Runtime validation rejects or removes arguments outside the declared
   contract.
4. Per-call grants and the sandbox constrain filesystem, network, process,
   credentials, and device access.
5. Policy and consent guard sensitive or destructive operations.
6. Results are checked against observable effects where the action requires
   one.

The strong Linux sandbox uses Bubblewrap when it is installed; the Windows
client uses Job Objects and, where the profile permits it, AppContainer. The
result records the isolation profile actually applied instead of claiming a
stronger one.

Credentials are encrypted at rest and resolved only at execution time; raw
secrets are kept out of LLM prompts. User-owned sessions, feedback, schedules,
and undo records are bound to authenticated identities. Italian and English
interface text and prompts are maintained as localization data rather than
embedded in routing logic.

## Bounded intelligence, governed growth

Some jobs have a stable purpose but an unpredictable route — logging into a
website is the clearest example. Metnos can place a **narrow-mandate agent
inside an executor**: the internal cycle may observe and choose among
runtime-enumerated actions, but it cannot change the goal, authority, or output
contract.

When a capability is missing, Metnos can compose existing executors or create
and import a candidate. Candidates do not become planner tools merely because
code exists: they pass naming, signing, sandbox, birth-test, and admission
gates first; a claimed parallel execution policy also requires equivalence
evidence.

All invocation concurrency passes through one host-aware scheduler. Executors
request a signed parallelism class and may lower their assigned budget; they
cannot create a larger private pool. The same contract applies whether code is
handwritten, generated, imported, local, or executed on a paired device.

Metnos has no generic MCP passthrough. An MCP tool would have to become an
admitted backend or narrow executor under the same contract.

## Install

The managed server installation targets Linux, Python 3.12 or newer, and
systemd. A GPU is useful for local models but not required: logical LLM tiers
may point to compatible local or remote endpoints. Optional companion services
provide self-hosted web search, geocoding, visual-language processing, and
Playwright.

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh --check
bash install/bootstrap.sh
```

The six-phase installer is idempotent and resumable. It creates the
installation-owned `.venv`, runtime directories, encrypted credential store,
model bindings, selected companion services, user-level systemd units, the
verified Tutor catalog, and a one-shot admin onboarding link.

After installation:

```bash
systemctl --user status metnos.target
curl http://127.0.0.1:8770/agent/health
```

Then open the web chat and send a harmless complete turn, such as:
“What time is it, and which time zone are you using?” An open health endpoint
proves reachability; the answer proves that planning and execution work
together.

Read the [installer guide](install/README.md) before choosing model endpoints,
optional services, or non-interactive installation.

## For readers and builders

- [Visual quick tour](https://metnos.com/en/Metnos_QuickTour) — the product
  through real natural-language scenes.
- [Architecture guide](https://metnos.com/en/architecture/) — request flow,
  policy, memory, Tutor, devices, intelligent executors, and observability.
- [Generated executor catalog](https://metnos.com/en/architecture/executor_catalog)
  — the current signed capability inventory.
- [Executor Standard](EXECUTOR_STANDARD.md) — the normative capability
  contract.
- [Policy and authority](https://metnos.com/en/architecture/policy.html) and
  [sandboxing](https://metnos.com/en/architecture/sandbox.html) — trust
  boundaries, execution grants, and isolation.

The public repository is a deterministic, sanitized export of the
run-essential source tree. Internal reports, credentials, private state, and
development-only material are excluded and scanned before publication.

Issues and patches are welcome. Include the failing request, platform,
observable result, and logs with secrets removed.

## License

[AGPL-3.0](LICENSE). If you run a modified version as a network service, the
AGPL network-use clause applies.
