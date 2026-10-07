"""Keep rejected Synth candidates in the existing proposal store, never authoring.

The content address is a storage identifier, not a Birth candidate identity or
approval. Birth must observe these bytes again in its authenticated context.
"""
from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path

from executor_birth_identity import encode_framed_v1
from executor_birth_snapshot import acquire_candidate_snapshot


class PendingSynthProposal(RuntimeError):
    def __init__(self, proposal_id: str, reason: str) -> None:
        self.proposal_id = proposal_id
        self.reason = reason
        super().__init__(reason)


def _identity(snapshot, *, reason: str, contract_id: str, producer: str) -> str:
    fields = {
        "manifest.toml": snapshot.manifest_bytes,
        "manifest.lang_state.json": snapshot.language_state_bytes,
        **dict(snapshot.code_files),
    }
    envelope = {
        "files": {key: value.hex() for key, value in fields.items()},
        "reason": reason, "contract_id": contract_id, "producer": producer,
    }
    return hashlib.sha256(
        b"metnos.synth.proposal/v1\0" + encode_framed_v1(envelope)
    ).hexdigest()


def preserve_candidate(source: Path, *, proposals_dir: Path, reason: str,
                       contract_id: str, producer: str, error_code: str) -> str:
    """Atomically retain exact bytes; a retry cannot replace an existing proposal."""
    proposals_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=proposals_dir))
    try:
        with acquire_candidate_snapshot(source) as snapshot:
            proposal_id = _identity(snapshot, reason=reason,
                                    contract_id=contract_id, producer=producer)
            target = proposals_dir / proposal_id
            if target.exists():
                load_candidate(proposals_dir, proposal_id)
                return proposal_id
            candidate = staging / "candidate"
            shutil.copytree(snapshot.private_root, candidate)
            manifest = tomllib.loads(snapshot.manifest_bytes.decode("utf-8"))
            metadata = {
                "proposal_id": proposal_id, "name": manifest["name"],
                "description": manifest.get("description", {}),
                "stage": "birth_blocked", "birth_error": error_code,
                "producer": producer, "contract_id": contract_id,
                "reason": reason,
            }
            with (staging / "proposal.json").open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                staging.rename(target)
            except OSError:
                # Another attempt may have completed the same content address.
                # Never overwrite or trust it without checking its exact bytes.
                if not target.exists():
                    raise
                load_candidate(proposals_dir, proposal_id)
            return proposal_id
    finally:
        if staging.exists():
            for path in staging.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
            shutil.rmtree(staging)


def load_candidate(proposals_dir: Path, proposal_id: str) -> tuple[Path, dict]:
    """Detect substituted files/intent before review; links are never candidates."""
    if not isinstance(proposal_id, str) or not re.fullmatch(r"[0-9a-f]{64}", proposal_id):
        raise ValueError("synth_proposal_id_invalid")
    root = proposals_dir / proposal_id
    if root.is_symlink() or (root / "proposal.json").is_symlink():
        raise ValueError("synth_proposal_changed")
    metadata = json.loads((root / "proposal.json").read_text(encoding="utf-8"))
    with acquire_candidate_snapshot(root / "candidate") as snapshot:
        identity = _identity(snapshot, reason=metadata["reason"],
                             contract_id=metadata["contract_id"],
                             producer=metadata["producer"])
        name = tomllib.loads(snapshot.manifest_bytes.decode("utf-8"))["name"]
        if identity != proposal_id or metadata["proposal_id"] != identity or metadata["name"] != name:
            raise ValueError("synth_proposal_changed")
    return root / "candidate", metadata


def review_document(proposals_dir: Path, proposal_id: str, human_cases: list) -> dict:
    """Export data only; this neither signs proof nor records human consent."""
    root, meta = load_candidate(proposals_dir, proposal_id)
    with acquire_candidate_snapshot(root) as snapshot:
        # Recheck inside this snapshot: a replacement between the preceding
        # validation and this read cannot become an export of the old proposal.
        if _identity(snapshot, reason=meta["reason"], contract_id=meta["contract_id"],
                     producer=meta["producer"]) != proposal_id:
            raise ValueError("synth_proposal_changed")
        files = {"manifest.toml": snapshot.manifest_bytes,
                 "manifest.lang_state.json": snapshot.language_state_bytes,
                 **dict(snapshot.code_files)}
        return {"proposal": {"files": {name: base64.b64encode(raw).decode("ascii")
                                        for name, raw in files.items()},
                             **{key: meta[key] for key in ("contract_id", "reason", "producer")}},
                "human_cases": human_cases}
