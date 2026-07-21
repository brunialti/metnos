# Playwright browser service

The Playwright service is Metnos's local browser boundary. It renders
JavaScript-heavy pages for URL readers and owns stateful website sessions for
the `*_sites` executors.

It listens on loopback by default and is not a public browser-automation API.
Session ownership, credentials, consent, target origins, and cleanup are
enforced by the broker.

## Install and service

Use the common sidecar installer:

```bash
python -m install.sidecar playwright
```

The installer creates a Metnos-owned virtual environment and browser cache
under the user data directory, installs Chromium, renders the user systemd
unit, and verifies `/health`. It does not reuse Python environments or browser
caches belonging to other projects.

Graphical sessions also use `metnos-side-display.service`, a persistent Xvfb
display. The selected browser surface is fixed when a session opens:

- `headless`: Chromium without a visible display;
- `side`: a real graphical Chromium window driven through Playwright.

Automation-reduction techniques are separate configuration switches. Selecting
the graphical surface does not automatically enable every technique.

## Responsibilities

The service exposes two families of operation:

1. **Stateless rendering.** `/render` loads one URL and returns the materialized
   DOM text and HTML to URL-reading executors.
2. **Stateful sessions.** `/session/open`, `/session/act`, login operations, and
   `/session/close` operate on owner-bound browser sessions used by
   `open_sites`, `login_sites`, and `act_sites`.

The broker maintains session leases, approved host sets, pending consent,
pending authentication factors, and cleanup state. A reaper closes abandoned
sessions; process loss returns `session_lost` instead of silently reusing stale
state.

## Intelligent website actions

Website executors keep a stable public contract while the broker may need to
observe and adapt to a changing page. Deterministic resolvers run first. An LLM
may rank only redacted, broker-owned action identifiers when the remaining
choice is ambiguous.

The model cannot:

- read credential values;
- invent a selector or arbitrary script to execute;
- approve an origin or additional host;
- bypass a credential mandate;
- declare login success without an observed postcondition.

Credential filling, exact-origin checks, host approval, screenshot masking, and
two-factor handoff remain deterministic. Email-based factor retrieval, when
configured and authorized, is restricted to the matching mailbox and messages
that arrived after the factor request.

## Health and diagnostics

```bash
curl -fsS http://127.0.0.1:8771/health
systemctl --user status metnos-playwright.service
journalctl --user -u metnos-playwright.service -n 100
```

The health response reports browser connectivity, generation, uptime, active
sessions, pending approvals, pending factors, and pending opens. The services
page at `/admin/services` exposes the same managed service through the central
Metnos inventory.

Every client request carries a content-derived contract fingerprint. The
sidecar checks both that fingerprint and its currently loaded code before any
browser operation; clients verify the response in the opposite direction.
Changing a browser-boundary module therefore makes a still-running component
unhealthy and requests fail with `sidecar_contract_mismatch` until the stale
service is restarted. `/health` exposes `contract_loaded`, `contract_current`,
and `contract_aligned` for diagnosis.

Typical failures are explicit:

- missing Chromium or display service prevents the requested surface from
  opening;
- navigation timeouts return a typed error;
- browser restart invalidates old sessions;
- unresolved CAPTCHA or factor verification returns a user handoff;
- changed or unapproved origins fail closed.

Do not increase retries or timeouts blindly. Inspect the health response, the
session error class, and the last redacted page observation first.
