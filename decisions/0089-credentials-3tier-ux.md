---
id: 0089
title: Credentials UX — three-tier flow (auto-extract / explicit-ask / cli-fallback)
date: 2026-05-04
status: accepted
area: runtime, security, executor, ux
related:
  - 0070  # admin → sudoer chain
  - 0078  # http api phase 1 (admin.key as master)
  - 0082  # encrypted credentials store + scrub
  - 0087  # CIFS mount via admin → sudoer
  - 0088  # admin exposed to PLANNER
complements:
  - 0082
  - 0087
  - 0088
---

## Context

Live observation 4 May 2026 (turn `345b9070` + the «si» that stayed
stuck): the user typed «monta nella mia area lo share `\\\\Public\\media\\Immagini`
del server `192.168.1.20` come utente roberto password Y». The PLANNER
selected `admin` correctly (ADR 0088), built the right argv with the
`${METNOS_CIFS_CREDS}` placeholder, and produced the approval card. The
user replied «sì» and… the chain stopped. Two failures stacked on top of
each other:

1. The `extract_credentials → store(cifs_<host>) → redact in jsonl` flow
   had been *designed* (ADR 0082 implements scrubbing) but the
   *extraction-and-store* half had not been wired into `run_turn`. So the
   `cifs_192.168.1.20` domain was not present in the cifrato store when
   `cifs_helper.temp_credentials_file` looked for it, and the
   placeholder did not resolve at fire time.
2. As a separate issue, sudo asked for a password (NOPASSWD not yet
   configured on the host), so even after fixing point 1 the chain would
   have stalled at the kernel-level prompt.

Roberto patched both manually for the demo. But the *design* is
incomplete: the user typed the credentials in the chat exactly because
that is the natural way to convey them, and Metnos must absorb that
input automatically — without forcing the user to learn a separate CLI
ritual *unless they want to*.

A second observation: the same shape recurs across binding kinds. CIFS
shares need user+pwd. So do web logins (school registry, bank, mail
behind paywall — ADR 0081). So does ssh, rsync, smtp, vpn, api tokens.
The pattern is uniform: «we need a secret, here is the binding
descriptor, the user can supply it via three channels of decreasing
intimacy».

Hence: lift the design out of CIFS-specific glue and define it once, for
every credential kind.

## Decision

Three layers, priority-ordered, each strictly more invasive than the
previous. Each layer is deterministic by construction — code, not LLM
(CLAUDE.md §7.9).

### Layer 1 — Automatic extraction from the user's query

`runtime/agent_runtime.py:extract_credentials(query)` runs at the start
of `run_turn`. Regex pairs (LONGEST FIRST keyword to avoid `username` ⇒
`user` partial match):

```
USER ∈ {username, usernam, utente, user, nome utente, login}
PWD  ∈ {password, passwd, pasw, pwd, psw, pass}
VAL  = [^\s,;]+
```

Two compiled patterns: USER then PWD, PWD then USER. The captured
ranges (`scrub_spans`) are exact value offsets in the original query.

The *binding* is detected from linguistic strong signals first
(`https?://`, `//host/share`, `\\bssh\\s`, …) and weak signals second
(words `share`, `nas`, `monta`, `login`, `ssh`, `cifs`, `webmail`, …).
Strong signals win because the user could write «accedi al sito ssh.com
con utente X» and we want `web_ssh.com`, not `ssh_ssh.com`.

The *host* is then derived from the same query: CIFS share source
`//host/share` → host; URL → host; bare FQDN/IPv4 → host. The canonical
domain becomes `<binding>_<host>` (lower-case): `cifs_192.168.1.20`,
`web_webmail.example.com`, `ssh_nas.local`.

Then `apply_credentials_extraction(query)`:

1. Calls `extract_credentials`.
2. For every record: `credentials.store(domain, {username, password,
   binding, host, share, …})` — Fernet-cifrato per ADR 0082.
3. Replaces value spans with `<REDACTED:cred:domain>` in the query
   passed to the PLANNER + prefilter + intent extractor + vaglio +
   synth pipeline.
4. Returns metadata (only `domain` + `context`, never username/password)
   that `run_turn` injects into the system prompt as a prescriptive
   block:

   ```
   CREDENZIALI ESTRATTE DALLA QUERY (Strato 1 — ADR 0089)
     - domain="cifs_192.168.1.20" binding=cifs host=192.168.1.20 share=Public/media/Immagini
   ```

   The PLANNER reads this, sets `credentials_domain="cifs_192.168.1.20"`
   on the admin call, and the placeholder resolves at fire time.

### Layer 2 — Explicit ask when needed

`runtime/verb_unique/admin.py::_detect_credentials_placeholder(argv)`
scans the argv for `${METNOS_<KIND>_CREDS}` and derives the expected
domain `<kind>_<host>`. If the domain is NOT in
`credentials.list_domains()`, `invoke()` returns:

```
{
  "ok": True,
  "decision": "credentials_required",
  "credentials_domain": "cifs_192.168.1.20",
  "credentials_context": {binding, host, share, …},
  "summary": <human-readable prompt>,
  ...
}
```

`agent_runtime.run_turn` recognises `decision == "credentials_required"`
and emits a final_answer with the prompt. The `expandable_caps` list is
populated with one entry of `kind="credentials_required"` so the
channel daemon (Telegram) and HTTP route both handle the next turn
correctly.

The user's next turn can be:

- **(a)** `user X pwd Y` — Layer 1 captures, stores, and the daemon
  rebuilds the original query verbatim. The original turn replays.
- **(b)** `cli` / `terminale` — Layer 3 emits CLI instructions.
- **(c)** `annulla` / `no` — pending state cleared, polite
  cancellation message.
- **(d)** anything else — re-shows Layer 2 prompt.

### Layer 3 — CLI fallback for users who don't want to type creds in chat

`/opt/myclaw/scripts/metnos-cli` (mode 0755, symlinked to
`/usr/local/bin/metnos-cli` by `install/setup.sh`):

```
metnos-cli credentials add cifs_192.168.1.20 --binding cifs --host 192.168.1.20
  > username: ...
  > password: ***          (input nascosto)
  > password (conferma): ***
metnos-cli credentials list
metnos-cli credentials remove cifs_192.168.1.20
metnos-cli credentials fingerprint cifs_192.168.1.20   # sha256[:16], MAI plaintext
```

When the user replies `cli` to a Layer 2 prompt, admin emits:

```
Per inserire le credenziali via terminale:

  ssh roberto@192.168.1.33
  metnos-cli credentials add cifs_192.168.1.20 --binding cifs --host 192.168.1.20
    > username: ...
    > password: ...

Quando hai finito, ripeti la richiesta originale e procedero'.
```

The pending state is preserved so the user can ssh in, run the CLI, and
then come back to the chat to retry the original intent. (Polling for
auto-resume is carry-over, see below.)

## Pattern generalised

The same shape applies to every binding the chain can encounter:

| Binding | Domain key            | Placeholder            | Helper           |
|---------|-----------------------|------------------------|------------------|
| CIFS    | `cifs_<host>`         | `${METNOS_CIFS_CREDS}` | `cifs_helper.py` |
| Web     | `web_<host>` (storico) | (login_session args)  | `login_session`  |

Per il dominio `sites`, ADR 0188 usa invece l'host esatto come binding
canonico; il broker mantiene lettura compatibile dei record `web_<host>`.
| SSH     | `ssh_<host>`          | `${METNOS_SSH_CREDS}`  | (future)         |
| API     | `api_<service>`       | `${METNOS_API_TOKEN}`  | (future)         |

The Layer 1 regex cover them all by emitting the right `<binding>_<host>`
key. The Layer 2 admin branch reads the *kind* segment of the
placeholder (`CIFS`, `SSH`, `API`, …) and combines it with the host
detection to ask for the correct domain. Adding a new binding means:
one entry in `_BINDING_STRONG`/`_BINDING_WEAK`, one helper module akin
to `cifs_helper.py`, and (for placeholder-style helpers) one branch in
sudoer's substitution logic.

## Alternatives considered

**(a) Force every credential through the CLI (no in-chat extraction).**
Rejected: hostile to the natural way users phrase the request. The user
sees a chat assistant and types «here are my creds»; refusing them and
demanding a separate ritual breaks the model.

**(b) Use an LLM to parse `user X pwd Y`.** Rejected on §7.9 grounds:
regex covers the cases (IT + EN, the syntactic variants the user really
types), is faster, deterministic, and cannot exfiltrate credentials to
a remote model by mistake.

**(c) Have admin call `cifs_helper.store_cifs_credentials` directly when
it sees inline plaintext creds in the argv.** Rejected: by design, admin
must never see plaintext credentials. The PLANNER receives a redacted
query (Layer 1 happens *before* the prompt) and emits an argv with
placeholder *only*. Centralising extraction in `run_turn` (single
chokepoint) is safer than scattering it into N verb-unique builtins.

**(d) Encode user/pwd as args of the executor (`admin(intent=…,
username=…, password=…)`).** Rejected: same problem as (c) plus the
PLANNER LLM would have to *receive* the credentials in its context to
emit them on the call, which violates the «LLM never sees plaintext
secrets» invariant.

## Consequences

**What this opens.** The user can mount, ssh, log into web portals,
authenticate to APIs from chat. The first time per-domain they type
their secret inline, Metnos absorbs it once-for-all (cifrato per ADR
0082). The next times the placeholder resolves silently. For users who
prefer keyboard hygiene, the CLI exists.

**What this closes.** The temptation to teach the PLANNER «password
patterns» (which would mean the LLM in fact does see plaintext) and
the temptation to write per-binding credential UI inside each verb-
unique builtin.

**What becomes easier.** Adding a new binding kind is one helper +
one regex hint. The pattern is uniform; the user-facing UX is uniform;
the storage / scrub / CLI surface is one and the same.

**What becomes more expensive.** Marginally: one regex pass per turn,
two TOML lines per new binding, one extra layer of state-machine in the
daemon (`kind="credentials_required"`).

**Work items spawned.**

- `runtime/agent_runtime.py`: ~280 LOC for `extract_credentials`,
  `_redact_spans`, `apply_credentials_extraction`, planner-system prompt
  injection block, runtime branch on `decision == "credentials_required"`.
- `runtime/verb_unique/admin.py`: ~80 LOC for
  `_detect_credentials_placeholder`, `_format_credentials_required`,
  `_format_cli_instructions`, `decision="credentials_required"` branch
  in `invoke()`.
- `runtime/channels/daemon.py`: ~60 LOC for
  `_consume_credentials_required` and `kind="credentials_required"`
  branch in cap-pending consume.
- `runtime/http_routes_agent.py`: ~30 LOC for
  `kind="credentials_required"` branch in `_apply_cap_pending`.
- `runtime/credentials.py`: 4 LOC fix to support flat `payload["password"]`
  in `fingerprint()` (had only `form_data.password` per ADR 0082 schema).
- `scripts/metnos-cli`: new file, ~170 LOC (`credentials add/list/remove/
  fingerprint` + argparse).
- `install/manifest.toml` + `install/setup.sh`: `[[scripts.entry]]`
  section + `step_scripts()` to symlink in `/usr/local/bin/`.
- `tests/runtime/safety/test_credentials_extraction.py`: 10 tests (regex
  variants IT+EN, scrub spans, host derivation, store roundtrip).
- `tests/runtime/safety/test_cli_credentials.py`: 3 tests (subprocess add+list+
  remove, password mismatch, missing remove).
- `tests/runtime/safety/test_credentials_3tier_flow.py`: 5 smoke tests of the
  three-layer pipeline (mocked at the sudoer boundary).

**Carry-over.**

- Auto-resume polling for Layer 3: today the user must repeat the
  original query after running `metnos-cli credentials add`. A simple
  daemon poll could detect the new domain and resume the pending state
  automatically; deferred until a real workflow asks for it.
- Per-binding placeholder helpers for SSH and API tokens (today only
  CIFS via `cifs_helper.py` is realised; web is handled inside
  `login_session`). Add when the first concrete intent arrives.

## References

- `runtime/agent_runtime.py` (`extract_credentials`,
  `apply_credentials_extraction`, runtime branch).
- `runtime/verb_unique/admin.py` (`_detect_credentials_placeholder`,
  `_format_credentials_required`, `_format_cli_instructions`).
- `runtime/channels/daemon.py` (cap-pending kind handler).
- `runtime/http_routes_agent.py` (HTTP cap-pending kind handler).
- `scripts/metnos-cli` (Layer 3 binary).
- `install/manifest.toml` (`[[scripts.entry]]` section).
- ADR 0070 (admin → sudoer chain).
- ADR 0082 (encrypted store + scrub).
- ADR 0087 (CIFS via admin/sudoer).
- ADR 0088 (admin exposed to PLANNER).
