"""Who wrote a revision, and what kind of executor is being born (RM-0008).

Two facts used to be chosen by ``bootstrap.json`` for each of the eleven
producers.  Looking at the eleven one by one, only one of them is really a
property of the producer:

- the **author** of the revision — model, importer, maintenance, human — is
  fixed per producer and lives in the closed table below;
- the **kind** of executor is a property of the executor being born, not of
  who asks for it: five producers revise an executor that already exists, and
  the installer installs contracts of several kinds without having any
  predecessor on a fresh installation.

The kind therefore comes from one place only: where the manifest lives, which
the inventory already computes and authenticates.  A revision does not move a
manifest, so the same fact serves a birth from nothing and a revision alike —
one source instead of two, and no dependency on a predecessor being
authenticated at the point where the kind is needed.

Decision taken with Roberto on 27/8/2026.
"""
from __future__ import annotations

from types import MappingProxyType

from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from manifest_inventory import ManifestOrigin


class ProducerTableError(RuntimeError):
    """The provenance of one birth cannot be established without inventing it."""


# (producer_id, operation) -> who writes this revision.
PRODUCER_AUTHOR_V1 = MappingProxyType({
    ("synt_multistage", "create_or_replay"): RevisionAuthor.MODEL,
    ("synt_specialize", "specialize_or_replay"): RevisionAuthor.MODEL,
    ("synt_approve", "approve_or_replay"): RevisionAuthor.MODEL,
    ("skills_cli", "skill_import_or_reactivation"): RevisionAuthor.IMPORTER,
    ("builtin_contract_generator", "generate_builtin"): RevisionAuthor.MAINTENANCE,
    ("installer_phase3", "install"): RevisionAuthor.MAINTENANCE,
    ("change_applier", "extend"): RevisionAuthor.MODEL,
    ("change_rollback", "rollback"): RevisionAuthor.MAINTENANCE,
    ("promoter", "promote"): RevisionAuthor.MAINTENANCE,
    ("promoter", "rollback"): RevisionAuthor.MAINTENANCE,
    ("stack_reconcile", "restart_sign_first"): RevisionAuthor.MAINTENANCE,
})

# Where a manifest lives is a structural fact the inventory already computes,
# and it is the only source of the kind for an executor born from nothing.
_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({
    ManifestOrigin.CORE: ExecutorOrigin.CORE,
    ManifestOrigin.BUILTIN: ExecutorOrigin.BUILTIN,
    ManifestOrigin.BUILTIN_SKILL: ExecutorOrigin.BUILTIN,
    ManifestOrigin.USER: ExecutorOrigin.HUMAN,
    ManifestOrigin.EXPLICIT: ExecutorOrigin.HUMAN,
    ManifestOrigin.USER_SKILL: ExecutorOrigin.IMPORTED,
    ManifestOrigin.LEGACY_IMPORT: ExecutorOrigin.IMPORTED,
})


def producer_author_v1(producer_id: str, operation: str) -> RevisionAuthor:
    """The author this producer always writes; an unknown pair is a defect."""
    author = PRODUCER_AUTHOR_V1.get((producer_id, operation))
    if author is None:
        raise ProducerTableError("birth_producer_capability_unknown")
    return author


def executor_origin_v1(manifest_origin: ManifestOrigin) -> ExecutorOrigin:
    """The kind of the executor being born, derived and never declared.

    It comes from where the manifest lives, for a birth from nothing and for a
    revision alike, because a revision does not move a manifest.  A location
    that has no birth is a refusal: a provenance that has to be guessed is
    worse than a birth that does not happen.
    """
    origin = _MANIFEST_ORIGIN_TO_EXECUTOR_V1.get(manifest_origin)
    if origin is None:
        # ``retired`` has no birth: refusing is the honest answer.
        raise ProducerTableError("birth_executor_origin_unavailable")
    return origin


__all__ = [
    "PRODUCER_AUTHOR_V1", "ProducerTableError", "executor_origin_v1",
    "producer_author_v1",
]
