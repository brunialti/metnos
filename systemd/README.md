# Metnos user services

This directory contains source units for services that run in the user's systemd
manager. The managed installer renders and installs the appropriate templates;
manual copying is intended for development only.

The Metnos service panel at `/admin/services` presents registered services
through one inventory. Components owned by `metnos.target` are read-only in the
panel: start, stop and restart must go through the serialized stack reconciler,
never through a component-level action.

## Integrated stack ownership

`metnos.target` is the single user-level owner of the installed Metnos stack:
HTTP, graphical display, Playwright and the installed Telegram, LLM, search,
geocoder, tunnel, issues sidecar and translation units. Optional components
remain optional; the target owns only units that exist on that host.

`metnos-stack-ready.service` gates readiness on the HTTP server contract,
catalog parity, Playwright fingerprint equality when that sidecar is installed,
and a readable broker state. `metnos-stack-quarantine.service` stops the closed
unit set after a failed start, including both i18n timer and in-flight translator
service. The first watchdog check is scheduled three minutes after target
activation, later than the readiness timeout. Recovery is bounded by a
persistent circuit breaker: three failures in ten minutes open it for fifteen
minutes, so it cannot become an infinite restart loop.

Inspect or deploy a named executor change through the serialized reconciler:

```bash
python3 runtime/stack_reconcile.py check
python3 runtime/stack_reconcile.py deploy --executor NAME --sign
```

`deploy` signs and verifies only explicitly named executor directories, then
requires broker/turn quiescence, restarts the complete target and waits for
composite readiness. It never discovers arbitrary units or performs a mass
signature rewrite.

## Migrating a legacy system HTTP service

Do not manually start the user HTTP service beside an active system service and
do not disable the system unit before the pilot. The migration helper preserves
the effective command, working directory and allowlisted environment in a
mode-0600 user drop-in, restores the system baseline after every pilot cycle and
accepts cutover only with matching evidence:

```bash
python3 runtime/stack_migration.py prepare
python3 runtime/stack_migration.py pilot --cycles 2
sudo python3 runtime/stack_migration.py cutover \
  --service-user "$USER" \
  --evidence ~/.local/state/metnos/stack_migration_pilot.json
```

The pilot performs two natural turns, each with at least one successful step and
a distinct conversation identifier, then proves rollback while leaving the
legacy system service active. Evidence is bound to the effective HTTP contract,
effective stack units, control-plane sources, host and explicit service user.
Cutover requires root because it changes unit enablement; it rechecks every
binding and restores the legacy baseline after any failed start or readiness
check.

## Graphical website browser

`metnos-side-display.service` keeps an Xvfb display available on `:99`.
`metnos-playwright.service` depends on that display and uses it for graphical
browser sessions with `headless=False`.

Host prerequisite:

```bash
sudo apt install xvfb
```

If the display service is unavailable, the graphical browser is reported as
unavailable. The runtime does not silently replace a requested graphical
surface with headless execution.

## Telegram channel

`metnos-telegram-daemon.service` performs Telegram long polling, dispatches each
accepted message through the normal Metnos turn path, and sends the result back
to the configured chat. The last processed update identifier is persisted under
the Metnos state directory so a restart does not replay old messages.

Telegram credentials are resolved through the encrypted Metnos credential
store. They are not placed in the unit file.

## Manual service inspection

```bash
systemctl --user daemon-reload
systemctl --user status metnos-side-display.service
systemctl --user status metnos-playwright.service
systemctl --user status metnos-telegram-daemon.service
journalctl --user -u metnos-playwright.service -f
```

User services that must survive logout require lingering for the service owner:

```bash
sudo loginctl enable-linger "$USER"
```

An upgraded HTTP server may remain a system service until the guarded migration
passes. Use the service scope selected by the installer rather than installing
a second copy manually.
