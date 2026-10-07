# Metnos installer

The supported entry point is `install/bootstrap.sh`. It runs six phases on
Linux x86-64 with systemd and Python 3.12. See [INSTALL.md](INSTALL.md) for the
complete procedure and [SERVICES.md](SERVICES.md) to reuse existing services.

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh --check
bash install/bootstrap.sh
```

Install the system packages listed in `manifest.toml` first, as shown in the
complete procedure.
`polkitd` is required even with remote companions: before activation, the
installer prepares a rule limited to Metnos units for the **Services** controls.
The bootstrap prepares `.venv` to launch the installer;
`--check` can therefore create or update that environment. The direct
`./.venv/bin/python -m install --check` does not run application phases.

The installer requests administrative privileges after language selection,
initial consent and confirmation. It provisions a dedicated `metnos` service
account, fresh installation authorities and an exact-lock Python environment.
Application phases run without administrative privileges. The administrative
parent installs and verifies the signed distribution before activating its
system services. No separate authority-provisioning command is needed.

## Existing services

After cloning and before installing, optionally create
`~/.config/metnos/services.toml`. Listed services use explicit reachable
endpoints; omitted services are prepared locally. The installer does not scan
the network, add service-choice prompts or silently replace a failed service
with a cloud provider. Local models and remote compatible model servers are
supported. The frontier tier requires explicit consent and credentials.

The selected profile is included in the signed distribution. Resume with the
same profile. Changing an installed service topology requires a new
administrative release. The managed flow does not accept `--skip`.

## Six phases

| Phase | Responsibility |
|---:|---|
| 1 | Prerequisites, exact dependency verification, service-account directories |
| 2 | Local BGE-M3 embeddings, model bindings and companion preparation |
| 3 | Authenticated source and initial catalog, initial stores, translations, Tutor |
| 4 | Administrator key and optional encrypted credentials |
| 5 | Signed system-service activation and health checks |
| 6 | Capability selection, one-use onboarding link and summary |

Initial catalog adoption authenticates the distributed catalog. It does not
certify newly produced functions. Phase records support resumption but never
replace the authenticated transition or authorize activation.

The first Tutor compilation can take several minutes on CPU. It runs before
service startup; subsequent compilations reuse unchanged source vectors.
The compiler bounds native thread pools by the available CPU quota, including
container limits.
Google Workspace is connected after installation through its OAuth flow.

Run `bash install/bootstrap.sh` again to resume. `--only-phase N` requires
preceding phases to have completed; `--force-phase N` repeats one phase.
`--force` accepts non-fatal warnings. `--yes` accepts non-sensitive defaults;
it does not replace initial consent or opt into paid models.

## Files and services

The runtime uses an immutable managed source and Python environment.
Configuration, data and state remain separate under the service-account home:

- `/var/lib/metnos-service/.config/metnos`
- `/var/lib/metnos-service/.local/share/metnos`
- `/var/lib/metnos-service/.local/state/metnos`

Downloaded models belong to data, not the immutable source. Administrative
private keys remain root-only. Secrets collected during phase 4 are encrypted.
Existing incompatible service units are refused, not overwritten.

The system catalog includes the HTTP server, selected companions and mandatory
background services. Local vision starts on demand. LRE is disabled by default.
The watchdog interval can be configured on Services; its default is 30 minutes.

## First access

```bash
systemctl --failed
systemctl list-units 'metnos*'
curl http://127.0.0.1:8770/agent/health
```

Phase 6 prints local and private-LAN URLs and saves them to
`/var/lib/metnos-service/.local/share/metnos/install_summary.md`. The managed
listener uses port 8770 and accepts LAN connections. Use a trusted network;
HTTP is unencrypted and must not be exposed directly to the Internet.

The onboarding link expires after 15 minutes and can be used once. Repeat
phase 6 if it expires, or use `/admin/login` with the instance administrator
key. After onboarding, send a harmless real chat request, such as “What time
is it, and which time zone are you using?”, and verify Tutor and the chosen
services. A health response alone does not prove a successful application turn.

Model settings are available in **Settings → System → Models**. Service state
is shown in **Settings → System → Services**. Existing installations must use
the administrative release and migration path with an agreed restart window.

[INSTALL_NOTES.md](INSTALL_NOTES.md) contains maintainer invariants;
[`../README.md`](../README.md) describes the project and security model.
