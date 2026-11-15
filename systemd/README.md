# Metnos service units

A supported installation runs Metnos as **system** services of the dedicated `metnos`
account. The installer creates and activates them from the signed service catalog; you copy
nothing by hand and no user lingering is needed. To install, follow the
[installer guide](../install/README.md).

The three unit files in this directory are user-level sources for a development machine
only. Do not install them beside a managed installation: the installer refuses service
units that differ from its own.

## Units of an installation

| Unit | Role |
|---|---|
| `metnos.target` | Owns the whole stack. |
| `metnos-http.service` | Web chat and API, port 8770. |
| `metnos-durable-worker.service` | Long jobs. |
| `metnos-telegram-daemon.service` | Telegram channel, when configured. |
| `metnos-playwright.service` | Browser service. |
| `metnos-side-display.service` | Xvfb display `:99` for graphical browser sessions. |
| `metnos-i18n-translator.timer` | Translations. Required: the target does not become ready without it. |
| `metnos-stack-ready.service` | Readiness gate of the stack. |
| `metnos-stack-watchdog.timer` | Periodic health check. |

A text model, SearXNG and Photon installed locally run as `llama-server.service`,
`searxng.service` and `photon.service`. Vision starts on demand and has no unit.

## Look at the services

```bash
systemctl status metnos.target
systemctl list-units 'metnos*'
journalctl -u metnos-http.service -f
```

The page **Settings → System → Services** (`/admin/services`) shows the same services from
one closed inventory. It offers Start for a stopped service and Stop or Restart for a running
one, when that action is valid.

Do not edit an installed unit and do not add drop-ins: the units are signed and a changed one
is refused. Changing units or services needs a new administrative release.

## Readiness and watchdog

- The stack is ready when the HTTP server answers with the expected contract, the executor
  catalog matches, the browser service (when installed) runs the current code, the
  translation timer is active and the browser session state is readable.
- The watchdog first runs three minutes after its timer starts, then every 30 minutes. An
  administrator can set 5 to 1440 minutes on the Services page.
- Automatic recovery is bounded: three failures in ten minutes stop further attempts for
  fifteen minutes, so it cannot become an endless restart loop.
- If Metnos cannot complete its startup checks, the HTTP server stays up in maintenance-only
  mode: model configuration and service control work, requests are not executed.

## Graphical website browser

`metnos-side-display.service` keeps an Xvfb display on `:99`. `metnos-playwright.service`
uses it for graphical browser sessions. Xvfb is in the package list of the installer guide.

If the display is not available, the graphical browser is reported as unavailable. Metnos
does not silently replace a requested graphical session with a headless one.

## Telegram channel

`metnos-telegram-daemon.service` performs Telegram long polling, passes each accepted
message through the normal Metnos turn path and sends the result back to the configured
chat. The last processed update is saved in the Metnos state directory, so a restart does
not replay old messages.

Telegram credentials come from the encrypted Metnos credential store. They are never placed
in the unit file.

## Changing an executor

Executor changes go through the stack reconciler, which admits the named executor, waits
until no turn or browser session is running and then activates it. The command and its rules
are in section 6 of [INSTALL_NOTES.md](../install/INSTALL_NOTES.md).
