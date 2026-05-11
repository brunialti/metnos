"""seed_bootstrap.py — apply runtime/safety_seeds/v*.toml idempotently.

Algorithm (ADR 0071):
  1. read seed file, parse `version` V_seed.
  2. query MAX(seed_version) from safety_meta = V_db.
  3. if V_seed <= V_db: nothing to do.
  4. for each entry in seed:
       - if existing row has source='user': skip.
       - else: INSERT OR REPLACE with source='seed', seed_version=V_seed.
  5. record (V_seed, applied_count, skipped_count) in safety_meta.

Idempotent for the same version. Respects user curation (source='user').
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# tomllib is stdlib in Python 3.11+; tolerate older interpreters with a fallback.
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ImportError as e:
        raise RuntimeError("tomllib (3.11+) or tomli is required") from e

from .storage import SafetyStore


DEFAULT_SEED_PATH = Path(__file__).parent.parent / "safety_seeds" / "v1.toml"


@dataclass
class BootstrapResult:
    seed_version: int
    db_version: int
    applied: int
    skipped: int
    skipped_signatures: list[str]
    upgraded: bool

    def summary_line(self) -> str:
        if not self.upgraded:
            return (
                f"seed v{self.seed_version} already applied (db v{self.db_version}); "
                "nothing to do"
            )
        return (
            f"seed v{self.seed_version} applied: {self.applied} entries, "
            f"{self.skipped} skipped (user-curated)"
        )


def bootstrap_safety_seed(
    *,
    store: SafetyStore | None = None,
    seed_path: Path | None = None,
) -> BootstrapResult:
    """Apply the seed file to the safety store. Idempotent on version."""
    seed_path = seed_path or DEFAULT_SEED_PATH
    own_store = False
    if store is None:
        store = SafetyStore()
        own_store = True
    try:
        with open(seed_path, "rb") as f:
            seed = tomllib.load(f)
        seed_version = int(seed.get("version", 0))
        if seed_version <= 0:
            raise ValueError(
                f"seed file {seed_path} must declare a positive `version`"
            )
        db_version = store.latest_seed_version()
        if seed_version <= db_version:
            return BootstrapResult(
                seed_version=seed_version,
                db_version=db_version,
                applied=0,
                skipped=0,
                skipped_signatures=[],
                upgraded=False,
            )
        applied = 0
        skipped = 0
        skipped_sigs: list[str] = []
        for entry in seed.get("signatures", []):
            sig = str(entry["sig"])
            kind = str(entry["kind"])
            severity = entry.get("severity")
            reason = entry.get("reason")
            ok = store.upsert_seed(
                sig, kind,
                severity=severity,
                reason=reason,
                seed_version=seed_version,
            )
            if ok:
                applied += 1
            else:
                skipped += 1
                skipped_sigs.append(sig)
        store.record_seed_application(
            seed_version=seed_version,
            applied=applied,
            skipped=skipped,
        )
        return BootstrapResult(
            seed_version=seed_version,
            db_version=db_version,
            applied=applied,
            skipped=skipped,
            skipped_signatures=skipped_sigs,
            upgraded=True,
        )
    finally:
        if own_store:
            store.close()


# CLI for manual use during development / smoke.
def _cli() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=Path, default=None)
    args = ap.parse_args()
    res = bootstrap_safety_seed(seed_path=args.seed)
    print(res.summary_line())
    if res.skipped:
        for s in res.skipped_signatures:
            print(f"  skipped (user): {s}")


if __name__ == "__main__":
    _cli()
