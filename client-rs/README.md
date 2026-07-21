# metnos-client

`metnos-client` executes signed Metnos invocations on a registered Windows or
Linux device. It has no planning authority: it polls the server, verifies an
invocation, runs the selected executor inside the strongest available local
sandbox, and returns a signed result.

## Trust boundary

- The client creates its own Ed25519 device key on first start.
- Pairing uses a one-time token and binds the device identity to a Metnos user.
- Client requests are signed over canonical JSON bytes.
- Server invocations carry a signature verified against the pinned server key.
- Executor code and manifests are cached by digest and verified before use.
- A delivery spool retries results without executing the invocation again.
- The client never sends an unsolicited action to the server.

The current transport is HTTP and is intended for a trusted LAN or private
overlay network. Message signatures authenticate invocations and results; they
do not provide transport confidentiality. Do not expose the client/server
channel directly to an untrusted network.

## Runtime and sandbox

The client downloads a signed Python runtime and executor bundle from the Metnos
server when required. It does not depend on a system Python installation.

On Linux, executor processes run through Bubblewrap when available, with an
explicitly reported weaker fallback when it is not. Process groups ensure that
timeouts terminate the complete child tree.

On Windows, the client combines Job Objects with AppContainer isolation where
the executor profile permits it. Resource limits and kill-on-close remain active
for paths that cannot use AppContainer. The result reports the sandbox actually
applied rather than claiming stronger isolation.

Read and mutation executors use the same signed invocation path. Device-aware
undo is dispatched back to the device that performed the original action.

## Commands

```text
metnos-client register   pair this device with a one-time token
metnos-client run        start the poll, execute, and delivery loop
metnos-client whoami     show the local pairing identity
```

The long-running client enforces a single-instance lock. Automatic self-update
downloads a signed binary, switches through the launcher loop, and keeps the
stored device identity and spool.

## Build

```bash
cargo build --release --target x86_64-unknown-linux-musl
cargo build --release --target x86_64-pc-windows-gnu
```

Release artifacts are produced and signed with:

```bash
scripts/build-client.sh <version>
```

## Source map

| Module | Responsibility |
|---|---|
| `main.rs` | CLI and process entry point |
| `config.rs`, `state.rs` | platform paths and persistent pairing state |
| `identity.rs`, `pairing.rs` | device key and registration flow |
| `wire.rs` | cross-language message types and canonical JSON |
| `runner.rs` | poll, verify, execute, spool, deliver, and heartbeat loop |
| `executors.rs`, `pyenv.rs` | signed executor cache and managed Python runtime |
| `sandbox_common.rs` | shared sandbox result contract |
| `sandbox_linux.rs` | Bubblewrap and process-group enforcement |
| `sandbox_windows.rs`, `appcontainer.rs` | Job Object and AppContainer enforcement |
| `proclock.rs` | cross-platform single-instance lock |
| `selfupdate.rs`, `update_state.rs` | signed client update and launcher state |

Installation and pairing are normally initiated from the Metnos web interface;
manual builds are intended for development and release preparation.
