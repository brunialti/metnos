---
id: 0087
title: CIFS/SMB mount via the admin → sudoer chain
date: 2026-05-04
status: accepted
area: runtime, executor, security, naming
related:
  - 0070  # admin → sudoer chain — shell orchestration via NL intent
  - 0071  # safety signatures — deterministic tools and seed
  - 0082  # encrypted credentials store
complements:
  - 0070
  - 0071
---

## Context

A daily-life intent surfaced on 4 May 2026: «monta nella mia area
personale lo share `\\Public\\Images` del server `192.168.1.20` come
utente XXXX e password YYY». The request maps cleanly onto the existing
admin → sudoer chain (ADR 0070) for shell orchestration: the user
expresses intent in natural language, the LLM translates it to an argv,
the deterministic safety tools classify it, the user approves once,
sudoer executes.

The temptation, when designing for «mount NAS share», is to spawn a
dedicated handcrafted executor (e.g. `set_shares`, `mount_shares`,
`create_shares_cifs`). That route would multiply executors per remote
filesystem (CIFS, NFS, SSHFS, WebDAV), per credential UI, per state
mode (mount once, persistent via fstab, autofs). Each of those is one
more file to write, one more manifest to keep aligned with the planner
prompt, one more naming-convention discussion.

Instead, three observations:

1. The admin → sudoer chain already covers privileged shell operations
   end-to-end: gate, translate, classify, ask, execute, audit (ADR 0070).
2. The deterministic safety layer (ADR 0071) already classifies argv
   into whitelist / graylist / blacklist by canonical signature
   `binary:subcommand:target_kind`. A new `mount.cifs:fs-mount-cifs`
   slot fits the existing taxonomy with one new target_kind.
3. The encrypted credentials store (ADR 0082) already provides
   per-domain cifrate-on-disk credentials. Adding a small temp-file
   helper for `mount.cifs -o credentials=...` is ~80 LOC, not a new
   subsystem.

The result: zero new dedicated executors. Three micro-components: a
canonicalisation rule for `mount`, a seed entry for the `mount.cifs`
graylist signature, and a `cifs_helper.py` module that materialises
the credentials temp file at fire time.

## Decision

### 1. Canonicalisation rule (runtime/safety/canonicalize.py)

The `compute_signature` function gains an explicit branch for the
`mount` and `umount` binaries:

- `mount`: subcommand = the value of `-t TYPE` (`cifs`, `nfs`, `nfs4`,
  `smb3`, `smbfs`, `ext4`, ...) or `auto` if absent.
- `umount`: subcommand = `*` (no informative subcommand).

The target_kind taxonomy is extended with three new kinds:

- `fs-mount-cifs` — argv contains a CIFS/SMB share source `//host/share`
  (regex `^//[^/]+/.+$`).
- `fs-mount-nfs` — argv contains an NFS source `host:/exported/path`.
- `fs-mount` — generic non-remote mount (loopback, bind, tmpfs).

The specificity ordering ranks `fs-mount-cifs` and `fs-mount-nfs` above
all `fs:*` kinds, so a mount of `//192.168.1.20/Public/Images` to
`/home/roberto/nas-images` resolves to `fs-mount-cifs`, not `fs:user`
(the destination path).

Examples:

```
mount -t cifs //192.168.1.20/Public/Images /home/roberto/nas-images \
       -o credentials=/tmp/x.creds,uid=1000
   → mount:cifs:fs-mount-cifs

mount -t nfs nas.lan:/exports/images /mnt/images
   → mount:nfs:fs-mount-nfs

umount /home/roberto/nas-images
   → umount:*:fs:user

umount //192.168.1.20/Public/Images
   → umount:*:fs-mount-cifs
```

### 2. Seed v2 (runtime/safety_seeds/v1.toml, version=2)

Eight new entries:

```toml
mount:cifs:fs-mount-cifs    graylist  reversible
mount:smb3:fs-mount-cifs    graylist  reversible
mount:smbfs:fs-mount-cifs   graylist  reversible
mount:nfs:fs-mount-nfs      graylist  reversible
mount:nfs4:fs-mount-nfs     graylist  reversible
umount:*:fs-mount-cifs      graylist  reversible
umount:*:fs-mount-nfs       graylist  reversible
umount:*:fs:user            graylist  reversible
```

Graylist (not whitelist) by design: the first execution surfaces an
approval card. After threshold uses (5 by default, ADR 0070), the entry
auto-promotes to whitelist for that user. This honours the «non-trivial
operation, ask first» principle while not requiring a card every time
the user mounts the same NAS.

The seed bumps from `version=1` to `version=2`. Existing DBs at v1
receive only the new entries (idempotent upgrade per ADR 0071); no
overwrite of `source='user'` rows.

### 3. cifs_helper.py (~80 LOC)

A small module under `runtime/cifs_helper.py` exposing two functions:

- `temp_credentials_file(domain) -> contextmanager` yielding
  `(path, error)`. The path is a fresh temp file under `/tmp/.cifs_*`
  with mode `0600`, containing `username=...\npassword=...\ndomain=...`.
  Cleanup is guaranteed at context exit, even on exception. The function
  reads the cifrate credentials via `runtime/credentials.py` (ADR 0082).
- `store_cifs_credentials(domain, *, username, password, workgroup,
  server, share)` for first-time setup; thin wrapper over
  `credentials.store`.
- `domain_for_server(server)` for the canonical store key
  `cifs_<host>` (lowercase), to disambiguate from web credential
  domains in ADR 0082.

### 4. Sudoer integration: placeholder substitution at fire time

Rather than couple `admin` to credential-file orchestration (which
would require admin to know about `mount.cifs` specifically), the
chain uses an opaque placeholder:

The LLM prompt in `verb_unique/admin.py` is updated to instruct the
translator: «for CIFS mount, emit
`-o credentials=${{METNOS_CIFS_CREDS}},uid=...`; never include literal
passwords in the argv». The placeholder is preserved verbatim through
the safety classification, the approval card, and the chain.

At fire time, `verb_unique/sudoer.py::execute` detects a
`${METNOS_CIFS_CREDS}` token in any argv element. If present:

1. derive the CIFS domain key from the share source
   `//host/share` in the argv → `cifs_<host>`;
2. open `cifs_helper.temp_credentials_file(domain)` as a context
   manager;
3. substitute the placeholder with the concrete temp-file path;
4. spawn the subprocess inside the still-open context;
5. exit the context: temp file is removed, regardless of exec outcome.

If credentials are missing for the derived domain, sudoer returns an
`error` ExecResult with stderr explaining «save credentials first via
`cifs_helper.store_cifs_credentials`». Subprocess is not invoked.

This split keeps `admin` ignorant of CIFS specifics — it only validates
argv shape. Sudoer's CIFS branch is ~50 LOC, isolated, easily
extensible to NFS or SSHFS later.

### 5. Workspace policy: read scope under /mnt and ~/nas-*

`~/.config/metnos/workspace_policy.toml` should include in
`[host.fs.read].scope`:

```toml
"/mnt/**",
"~/nas-*/**",
```

so executors that walk filesystem paths can see the mounted share. This
is policy on the host side, not seed-managed; users edit it freely.

## Alternatives considered

**(a) Dedicated executor `set_shares` + `mount_shares` + `unmount_shares`.**
Rejected: would multiply five-to-eight executors (per remote filesystem
× per state mode) and replicate logic that the chain already provides.
The vocabulary is stretched (`set` not natural for «mount a share»).
Loses the auto-promote graylist→whitelist behaviour.

**(b) Embed credential-file logic in `admin`.** Rejected: `admin`
must stay ignorant of specific binaries. Today CIFS, tomorrow ssh-add,
gpg-agent, curl `--config`. The placeholder + sudoer-side resolution
generalises to any binary that takes a credentials file path.

**(c) Pass credentials via stdin (`-o user=alice,password=hunter2`).**
Rejected: the password ends up on the kernel argv visible in `ps -ef`
to other processes during the lifetime of the mount call. The
credentials file approach exposes only the path (which is `/tmp/.cifs_*`)
and the file is `0600`, removed within seconds.

**(d) Persistent mount via /etc/fstab.** Out of scope for this ADR.
Could be a future executor (`write_files_fstab` + reload) once a real
use case demands persistence. For now, mount per-session is enough.

## Consequences

**What this opens.** Any user with credentials cifrate already saved
can mount their NAS share from chat in three turns: «monta lo share
X», approval card, mount executes. Future remote filesystems (NFS,
SSHFS, WebDAV) follow the same pattern.

**What this closes.** The temptation to write a `set_shares` family of
dedicated executors. The pattern «admin → sudoer for privileged ops»
proves itself again on a non-trivial intent.

**Work items spawned.**

- `runtime/safety/canonicalize.py`: ~40 LOC for the `mount`/`umount`
  branch + new target_kinds.
- `runtime/safety_seeds/v1.toml`: 8 entries, version bumped 1→2.
- `runtime/cifs_helper.py`: new file, ~140 LOC including docstrings.
- `runtime/verb_unique/admin.py`: prompt update, ~10 LOC.
- `runtime/verb_unique/sudoer.py`: placeholder substitution branch,
  ~70 LOC.
- `runtime/tests/test_cifs_helper.py`: 6 tests (roundtrip, cleanup,
  missing domain, missing fields, concurrency, domain canonicalisation).
- `runtime/tests/test_admin_mount_cifs.py`: 7 tests (canonicalisation,
  seed lookup, admin decide, sudoer substitution, missing credentials,
  end-to-end chain).
- `~/.config/metnos/workspace_policy.toml`: extend `[host.fs.read].scope`
  with `/mnt/**` and `~/nas-*/**` (manual edit, outside repo).

**What becomes more expensive.** Negligible: the extra branch in
canonicalize and sudoer is bounded and well-tested. The seed grows
from ~200 to ~210 entries.

**What becomes easier.** Adding a new remote filesystem (NFS already
covered; SSHFS would need a placeholder for SSH key, similar pattern).
The credential-file pattern in sudoer is reusable for any binary that
accepts `--config FILE` or `-o credentials=FILE` style.
