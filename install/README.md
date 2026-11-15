# Install Metnos

English · [Italiano](INSTALL.md)

One guided installer sets up a complete Metnos server on Linux: a dedicated service account,
the verified code, models, helper services and a first-access link.

## In short

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
sudo apt-get update
sudo apt-get install -y $(python3 -c 'import tomllib; p=tomllib.load(open("install/manifest.toml","rb"))["system_packages"]; print(" ".join(p["debian"]+p["debian_optional"]+p["ubuntu_24_04_all_local"]))')
bash install/bootstrap.sh --check
bash install/bootstrap.sh
```

Then open the link printed at the end and send one real request in the chat.

## Before you start

- **System:** Linux x86-64 with systemd and Python 3.12. Ubuntu 24.04 is the reference; the
  installer accepts only the systemd versions listed in [INSTALL_NOTES.md](INSTALL_NOTES.md).
- **Access:** an account that can use `sudo`. The installer asks for it after your consent.
- **Resources:** Internet, Git, 8 GB of free disk besides the models, 8 GB of RAM
  recommended. A GPU helps local models but is not required.
- **Packages:** the ones in [`manifest.toml`](manifest.toml), installed by the `apt-get`
  command above. `polkitd` is needed even when you reuse remote services.
- **Existing services (optional):** to reuse a model server, search, geocoder or browser you
  already run, prepare `~/.config/metnos/services.toml` first. See [SERVICES.md](SERVICES.md).

Approximate downloads when everything is local: embeddings 560 MB, vision 1.9 GB, search
200 MB, geocoder 3 GB for one country, browser 700 MB, plus the text model you choose.

## What the installer does

It asks the language, shows the safety notice and asks for confirmation. Then it asks for
administrator access, creates the `metnos` service account and runs six phases. Your answers
are collected under that account; only preparation and activation run as administrator.

| Phase | Result |
|---:|---|
| 1 | Prerequisites and exact dependencies checked; service-account directories |
| 2 | Local BGE-M3, models, and helper services prepared or linked |
| 3 | Verified source and initial catalog; databases, translations, Tutor |
| 4 | Administrator key and optional encrypted credentials |
| 5 | Signed system services activated and checked |
| 6 | Capability selection, one-use first-access link, summary |

- The installer creates new signing keys for this installation. You run no separate command
  and copy no keys from another instance. Administrative keys stay readable by root only.
- Phase 3 builds Tutor, the built-in help. On a CPU the first build can take several
  minutes; later builds reuse what did not change.
- Phase 4 can store credentials for Telegram, mail, GitHub and cloud model providers. All
  are optional. Google Workspace is connected after installation through its OAuth flow.

## Options

| Option | Effect |
|---|---|
| `--check` | Checks prerequisites and runs no phase. Through `bootstrap.sh` it may create or update `.venv`; `./.venv/bin/python -m install --check` writes nothing. |
| `--yes` | Accepts non-sensitive defaults. It does not replace the initial consent and never enables a paid model. |
| `--force` | Continues past non-fatal warnings. |
| `--force-phase N` | Repeats phase N. |
| `--only-phase N` | Runs phase N alone; earlier phases must be complete. |

`--skip` is not accepted: every service is either installed locally or declared in
`services.toml`.

**To resume** an interrupted installation, run `bash install/bootstrap.sh` again with the
same `services.toml`. Completed phases are kept, and the verification is repeated every time.

## Where things are

| Content | Directory |
|---|---|
| Configuration and credentials | `/var/lib/metnos-service/.config/metnos` |
| Data, models and summary | `/var/lib/metnos-service/.local/share/metnos` |
| State and phase markers | `/var/lib/metnos-service/.local/state/metnos` |

The verified code and its Python live in separate root-owned stores. The `.venv` in the clone
only launches the installer. Existing service units that do not match are refused, never
overwritten.

## Models and services

- **Embeddings** (BGE-M3) are always local and run inside Metnos.
- **Text model:** local, or an existing compatible server declared in `services.toml`. A
  cloud model needs your explicit choice and its credentials; it is never chosen for you.
- **Vision** starts on demand. **Search** (SearXNG), **geocoding** (Photon) and the
  **browser** (Playwright) run as system services of the `metnos` account.
- **Long jobs** are off by default; turn them on in **Settings → System → Services**.
- Model settings are in **Settings → System → Models**; changing a model needs no reinstall.

Phase 2 asks whether to download the optional reCAPTCHA image-grid classifier,
[recaptcha-classification-57k](https://huggingface.co/DannyLuna/recaptcha-classification-57k)
(MIT license), from Hugging Face. The default answer is no and `--yes` leaves it out; without
it, image grids are left to you. To add it later:

```bash
sudo -H -u metnos bash -s captcha-grid < install/download_models.sh
```

The chosen services are part of the signed installation. Changing them later is not done by
editing generated files or restarting: it needs a new administrative release.

## First access and check

```bash
systemctl --failed
systemctl list-units 'metnos*'
curl http://127.0.0.1:8770/agent/health
```

Phase 6 prints the address for this server and for each private-LAN address, with a
first-access link that lasts 15 minutes and works once. The same addresses are saved in
`/var/lib/metnos-service/.local/share/metnos/install_summary.md`.

Metnos listens on port 8770 and accepts the private LAN. The connection is plain HTTP: use a
trusted network, and never forward the port or expose it to the Internet.

A health answer only proves that the server started. To finish the check, send a harmless
real request in the chat, such as “What time is it, and which time zone are you using?”, then
try Tutor and the services you chose.

## If something stops

| What you see | What to do |
|---|---|
| The installation was interrupted | Run `bash install/bootstrap.sh` again. |
| “Required system programs are missing” | Install the packages of “In short” and run again. |
| “The service profile differs from the prepared installation” | Restore the `services.toml` you started with. |
| A declared service does not answer | Fix the address or the service; nothing replaces it automatically. |
| The first-access link expired | Run `bash install/bootstrap.sh --force-phase 6`, or open `/admin/login` with the administrator key of the instance. Do not share that key. |

More cases are in the troubleshooting table of [INSTALL_NOTES.md](INSTALL_NOTES.md).

## Updates

An existing installation is updated through the administrative release path, with a check of
the configuration and an agreed restart window.

[INSTALL_NOTES.md](INSTALL_NOTES.md) holds the rules for those who maintain the installer;
[`../README.md`](../README.md) describes the project and its security model.
