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

The installer creates a Metnos-owned virtual environment and browser cache,
installs the selected browser (Chromium by default), renders the user systemd
unit, and verifies `/health`. It does not reuse Python environments or browser
caches belonging to other projects.

Graphical sessions also use `metnos-side-display.service`, a persistent Xvfb
display. The selected browser surface is fixed when a session opens:

- `headless`: the selected browser without a visible display;
- `side`: a real graphical browser window driven through Playwright.

Automation-reduction techniques are separate configuration switches. Selecting
the graphical surface does not automatically enable every technique.

### Explicit Camoufox installation

On Linux x86_64, select Camoufox for the whole instance before installation:

```bash
METNOS_SITES_BROWSER_ENGINE=camoufox METNOS_SITES_WEBSOCKETS_ALLOWED=1 \
  python -m install.playwright_sidecar --prepare
```

`--prepare` installs without starting services. The installer persists the engine,
WebSocket choice, stealth ceiling and browser path in `browser-engine.env` under
the Metnos data directory. The server reads these four values before selecting
the browser, including when the signed launcher supplies a minimal environment;
subsequent installer runs read the same configuration.
An explicit environment value overrides the saved choice during installation.
Changing the browser requires the normal authorized service restart.

Camoufox includes integrated fingerprint masking. Selecting it is an explicit
instance choice of that behavior; individual Chromium stealth switches do not
disable it. `METNOS_SITES_STEALTH_ALLOWED=false` forbids Camoufox. Chromium-only
techniques are rejected explicitly. There is no automatic fallback between engines.

`METNOS_SITES_WEBSOCKETS_ALLOWED` defaults to false. When true, site WebSockets
are unrestricted, including their destination hosts: the HTTP request allowlist
does not cover that transport. The exception applies to either browser engine.
Camoufox cannot enforce the default WebSocket block through Playwright's isolated
world and therefore refuses to start unless this exception is explicitly enabled.
HTTP host checks, credential origins, consent, blocked service workers and WebRTC,
and session isolation remain in force for site sessions. No extension, main-world
evaluation bypass, COOP bypass, or external solver is enabled by this engine choice.

The installer pins Python Camoufox 0.5.6, Playwright 1.61.0 and browser
156.0.1-beta.33. It checks the official Linux archive's exact length and SHA-256
before extraction (about 1.3 GB download, plus extracted space). Runtime startup
requires the pinned installation receipt and never fetches a browser. A missing
or different installation fails explicitly. The browser is a prerelease; its
synthetic checks do not guarantee access to a particular website or solve CAPTCHA.

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

Since release 122, the broker automatically saves a static page copy when an owned
action ends with `selector_hidden`; it needs no browser-console action from
the user. Synthetic module and security-boundary checks cover this capture;
the website workflow still requires its own end-to-end verification.
The copy records original control/obstruction geometry alongside sanitized
HTML. It omits scripts, external links, form contents, marked private fields
and email addresses. Other visible text may remain personal. There is no new
session-access endpoint, and no capture during pending consent, secrets or
human authentication steps. Files use the screenshot owner directory (0700),
exclusive 0600 writes and the same 30-minute cleanup threshold, swept on the
next capture. Internal audit records the path or the reason capture was omitted;
the original action error remains unchanged. Static replay can differ from
the original layout and cannot certify a complete website workflow.

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

- missing selected browser or display service prevents the requested surface from
  opening;
- navigation timeouts return a typed error;
- browser restart invalidates old sessions;
- unresolved CAPTCHA or factor verification returns a user handoff;
- changed or unapproved origins fail closed.

Do not increase retries or timeouts blindly. Inspect the health response, the
session error class, and the last redacted page observation first.
