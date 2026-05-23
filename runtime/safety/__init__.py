"""runtime.safety — safety primitives for shell orchestration (ADR 0070, 0071).

Three concerns live here, kept separate by module:

- canonicalize.py: argv → signature (deterministic, pure Python).
- storage.py:      SQLite-backed signature store + CRUD operations.
- seed_bootstrap.py: idempotent application of `safety_seeds/v*.toml`.
- sanity_check.py: fast-LLM second-opinion wrapper (the only LLM thing).
- secret_slot.py:  one-time password slot with explicit zeroing.

The public API of this package is what `runtime/builtins/admin.py` and
`runtime/builtins/sudoer.py` consume; ordinary handcrafted executors in
`<install_root>/executors/find_signatures_*` import from here too.

Invariants enforced across the package:
- Signatures are colon-separated `binary:subcommand_or_flag:target_kind`.
- The store distinguishes `source` (seed | user | auto-promoted) and
  refuses to overwrite `source='user'` during seed bootstrap.
- The `forbidden` severity is non-derogable (Law 1: no irrecoverable state).
- Every guarded operation emits an audit row in `events.turn_id`
  (ADR 0067), with the `caller_authorised` boolean so verb-unique
  invariant 3 (ADR 0069) can be checked retrospectively.
"""

from .canonicalize import (
    compute_signature,
    Signature,
    classify_target,
)
from .storage import (
    SafetyStore,
    SignatureRow,
)
from .seed_bootstrap import (
    bootstrap_safety_seed,
    BootstrapResult,
)

__all__ = [
    "compute_signature",
    "Signature",
    "classify_target",
    "SafetyStore",
    "SignatureRow",
    "bootstrap_safety_seed",
    "BootstrapResult",
]
