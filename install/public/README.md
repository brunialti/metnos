# Metnos public install overview

Entry page for a Metnos instance cloned from GitHub. There is one supported installer,
`install/bootstrap.sh`. The complete guide is [`../README.md`](../README.md), also in
[Italian](../INSTALL.md).

## Quick start

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh --check   # checks requirements; may create .venv
bash install/bootstrap.sh           # guided, six phases, resumable
```

Install the system packages first, with the command in the complete guide.

The installer asks for administrator access, creates the dedicated `metnos` service account
and fresh signing keys, prepares models and helper services, then starts the system services
and checks the HTTP health endpoint. At the end it prints the address of the web interface
and a one-use first-access link.

The installation is checked only when one harmless request has gone through the chat. The
web interface is plain HTTP on the private LAN: never forward its port or expose it to the
Internet.

## Models are configuration, not code

Metnos is self-contained: runtime, executors, web chat and text embeddings are in this
repository and run locally. Only the language model is your choice.

Metnos sees logical **tiers** (`fast`, with the levels `micro`, `procedural` and `fidelity`;
`middle`; `wise`; `creative`; `frontier`), never model names. Local tiers may share one
compatible llama-server endpoint or use separate ones, local or remote. `frontier`, the cloud
tier, is opt-in.

To change a model after installation open **Settings → System → Models** in the web chat.
The page shows the tier bindings in effect, where they come from and their generation
parameters; edit and save there, or use **Restore** for the defaults of the installed
version. No reinstall is needed.

See [`../../README.md`](../../README.md) for the project overview and
[`../INSTALL_NOTES.md`](../INSTALL_NOTES.md) for the rules the installer must keep.
