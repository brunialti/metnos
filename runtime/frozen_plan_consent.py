"""Runtime-owned consent for one exact signed frozen-plan invocation."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Iterator


TOKEN_PATTERN = "^[0-9a-f]{64}$"
_TOKEN_RE = re.compile(TOKEN_PATTERN)
_ARTIFACT_SUFFIX_RE = re.compile(r"^\.[a-z0-9][a-z0-9-]{0,39}\.json$")
_CONSENT_ITEM_FIELDS = ("action", "source", "destination", "reference")
_CONSENT_MAX_ITEMS = 50
_CONSENT_MAX_VALUE_CHARS = 8192


@dataclass(frozen=True)
class FrozenPlanGrant:
    executor: str
    executor_binding: str
    args_fingerprint: str
    owner_user_id: str
    actor: str
    channel: str
    turn_id: str
    token: str


authorization: ContextVar[FrozenPlanGrant | None] = ContextVar(
    "frozen_plan_authorization", default=None)


def fingerprint(args: object) -> str:
    return hashlib.sha256(json.dumps(
        args, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


def executor_binding(executor) -> str:
    """Bind consent to the exact authenticated catalog generation and code."""
    return fingerprint({
        "name": str(getattr(executor, "name", "") or ""),
        "contract_id": str(getattr(executor, "contract_id", "") or ""),
        "generation_id": str(getattr(executor, "generation_id", "") or ""),
        "digest": str(getattr(executor, "digest", "") or ""),
    })


def validate_frozen_plan(manifest: dict) -> list[str]:
    execution = manifest.get("execution") or {}
    raw = execution.get("frozen_plan")
    if raw is None:
        return []
    required = {
        "argument", "preview_value", "apply_value", "token_argument",
        "token_result", "carry_arguments", "artifact_suffix", "journal_suffix",
        "recovery",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        return ["execution.frozen_plan requires exactly "
                + ", ".join(sorted(required))]
    properties = (manifest.get("args") or {}).get("properties") or {}
    argument = raw.get("argument")
    discriminator = properties.get(argument) if isinstance(argument, str) else None
    preview_value = raw.get("preview_value")
    apply_value = raw.get("apply_value")
    if (not isinstance(discriminator, dict)
            or discriminator.get("type") != "string"
            or preview_value == apply_value
            or preview_value not in (discriminator.get("enum") or [])
            or apply_value not in (discriminator.get("enum") or [])):
        return ["frozen plan discriminator must be a declared string enum "
                "with distinct values"]
    token_argument = raw.get("token_argument")
    token_spec = properties.get(token_argument) if isinstance(token_argument, str) else None
    if (not isinstance(token_spec, dict) or token_spec.get("type") != "string"
            or token_spec.get("pattern") != TOKEN_PATTERN
            or token_spec.get("runtime_resolved") is not True):
        return [f"frozen plan token argument must use pattern {TOKEN_PATTERN!r}"]
    if not isinstance(raw.get("token_result"), str) or not raw["token_result"]:
        return ["frozen plan token_result must be a non-empty field name"]
    carry = raw.get("carry_arguments")
    if (not isinstance(carry, list) or not carry or len(carry) > 32
            or len(set(carry)) != len(carry)
            or any(not isinstance(name, str) or name not in properties
                   or name in {argument, token_argument} for name in carry)):
        return ["frozen plan carry_arguments must be unique declared arguments"]
    if any((properties.get(name) or {}).get("runtime_resolved") for name in carry):
        return ["frozen plan carry_arguments cannot be runtime-resolved"]
    suffix = raw.get("artifact_suffix")
    if not isinstance(suffix, str) or not _ARTIFACT_SUFFIX_RE.fullmatch(suffix):
        return ["frozen plan artifact_suffix is invalid"]
    journal_suffix = raw.get("journal_suffix")
    if (not isinstance(journal_suffix, str)
            or not _ARTIFACT_SUFFIX_RE.fullmatch(journal_suffix)
            or journal_suffix == suffix):
        return ["frozen plan journal_suffix is invalid"]
    if raw.get("recovery") != "same_token_write_ahead_v1":
        return ["frozen plan recovery must use same_token_write_ahead_v1"]
    try:
        from policy import CAPABILITY_REGISTRY
    except ImportError:
        return ["frozen plan capability registry is unavailable"]
    for index, capability in enumerate(manifest.get("capabilities") or []):
        if not isinstance(capability, dict):
            continue
        spec = CAPABILITY_REGISTRY.get(capability.get("name"))
        if spec is None or not spec.grants_mutation:
            continue
        if capability.get("when") != {
                "arg": argument, "values": [apply_value]}:
            return [f"frozen plan mutating capability {index} must be "
                    "conditioned on apply"]
    if (execution.get("effect") not in {"reversible", "mutating"}
            or execution.get("parallelism_class", 0) != 0
            or (manifest.get("undo") or {}).get("outcome") != "per_execution"
            or manifest.get("revertible") is not True):
        return ["frozen plans require serial reversible per-execution undo"]
    if (manifest.get("placement") or {}).get("scope") != "server":
        return ["frozen plans require placement.scope = 'server'"]
    effects = execution.get("effects") or []
    if not any(
        isinstance(rule, dict)
        and rule.get("argument") == argument
        and rule.get("equals") == preview_value
        and rule.get("effect") == "read_only"
        for rule in effects
    ):
        return ["frozen plan preview must be declared read_only by execution.effects"]
    return []


def normalized_policy(manifest: dict) -> dict | None:
    if validate_frozen_plan(manifest):
        return None
    raw = (manifest.get("execution") or {}).get("frozen_plan")
    if not isinstance(raw, dict):
        return None
    value = dict(raw)
    value["carry_arguments"] = list(raw["carry_arguments"])
    return value


def policy_for(executor) -> dict | None:
    policy = getattr(executor, "execution_policy", {}) or {}
    value = policy.get("frozen_plan")
    return value if isinstance(value, dict) else None


def redact_args(executor, args: dict) -> dict:
    """Remove runtime-only consent material from logs and durable receipts."""
    out = dict(args or {})
    policy = policy_for(executor)
    if policy:
        token_argument = policy.get("token_argument")
        if isinstance(token_argument, str) and token_argument:
            out.pop(token_argument, None)
    return out


def redact_result(executor, result: dict) -> dict:
    out = dict(result or {})
    policy = policy_for(executor)
    if policy:
        token_result = policy.get("token_result")
        if isinstance(token_result, str) and token_result:
            out.pop(token_result, None)
    return out


def _consent_preview(executor, result: dict, token: str) -> dict | None:
    """Build the bounded, display-safe projection approved by the user.

    The public digest is domain-separated from the bearer token.  It binds
    the form to the complete content-addressed plan without disclosing the
    token itself.  Action values stay exact but are rendered later as JSON so
    unusual filename characters cannot forge extra rows in the form.
    """
    raw_items = result.get("results")
    if not isinstance(raw_items, list):
        return None
    projected: list[dict[str, str]] = []
    for raw in raw_items[:_CONSENT_MAX_ITEMS]:
        if not isinstance(raw, dict):
            return None
        item: dict[str, str] = {}
        for key in _CONSENT_ITEM_FIELDS:
            if key not in raw:
                continue
            value = raw[key]
            if (not isinstance(value, str)
                    or len(value) > _CONSENT_MAX_VALUE_CHARS):
                return None
            item[key] = value
        if not item.get("action") or not item.get("source"):
            return None
        projected.append(item)

    returned_count = len(raw_items)
    available_total = result.get("available_total", returned_count)
    if (not isinstance(available_total, int) or isinstance(available_total, bool)
            or available_total < returned_count or available_total < 0):
        return None
    executor_truncated = result.get("truncated") is True
    if available_total > returned_count and not executor_truncated:
        return None
    if executor_truncated and available_total <= returned_count:
        return None
    if "used" in result and result.get("used") != returned_count:
        return None

    counts = {
        key: result[key] for key in (
            "source_count", "compare_count", "move_count",
            "duplicate_count", "unresolved_count",
        )
        if isinstance(result.get(key), int)
        and not isinstance(result.get(key), bool)
        and result[key] >= 0
    }
    return {
        "schema": "metnos.frozen-consent-preview.v1",
        "plan_sha256": hashlib.sha256(
            b"metnos-consent-display-v1\0" + token.encode("ascii")
        ).hexdigest(),
        "executor_binding": executor_binding(executor),
        "items": projected,
        "shown_count": len(projected),
        "returned_count": returned_count,
        "total_count": available_total,
        "truncated": available_total > len(projected),
        "counts": counts,
    }


def consent_preview_binding(record: dict) -> str | None:
    """Verify and return the exact form projection binding in ``record``."""
    preview = record.get("consent_preview")
    claimed = record.get("consent_preview_binding")
    token = record.get("token")
    executor_bound = record.get("executor_binding")
    if (not isinstance(preview, dict) or not isinstance(claimed, str)
            or not isinstance(token, str) or not _TOKEN_RE.fullmatch(token)
            or not isinstance(executor_bound, str) or not executor_bound
            or preview.get("executor_binding") != executor_bound):
        return None
    expected = fingerprint({
        "schema": "metnos.frozen-consent-binding.v1",
        "executor_binding": executor_bound,
        "token": token,
        "preview": preview,
    })
    return claimed if hmac.compare_digest(claimed, expected) else None


def prepare_resume(executor, args: dict, result: dict) -> dict | None:
    """Derive private resume state from signed policy and preview output."""
    policy = policy_for(executor)
    if not policy or args.get(policy["argument"]) != policy["preview_value"]:
        return None
    if not isinstance(result, dict) or result.get("ok") is not True:
        return None
    token = result.get(policy["token_result"])
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        return None
    consent_preview = _consent_preview(executor, result, token)
    if consent_preview is None:
        return None
    from executor_helpers import normalize_unique_items
    final_args = normalize_unique_items(
        dict(args), getattr(executor, "args_schema", None))
    apply_args = {
        name: final_args[name]
        for name in policy["carry_arguments"] if name in final_args
    }
    apply_args[policy["argument"]] = policy["apply_value"]
    apply_args[policy["token_argument"]] = token
    record = {
        "executor": str(getattr(executor, "name", "") or ""),
        "executor_binding": executor_binding(executor),
        "args": apply_args,
        "token": token,
        "artifact_suffix": policy["artifact_suffix"],
        "journal_suffix": policy["journal_suffix"],
        "recovery": policy["recovery"],
        "expires_at": result.get("expires_at"),
        "preview": {
            key: result[key] for key in (
                "source_count", "compare_count", "move_count",
                "duplicate_count", "unresolved_count", "available_total",
            ) if isinstance(result.get(key), int)
        },
        "consent_preview": consent_preview,
    }
    record["consent_preview_binding"] = fingerprint({
        "schema": "metnos.frozen-consent-binding.v1",
        "executor_binding": record["executor_binding"],
        "token": token,
        "preview": consent_preview,
    })
    return record


@contextmanager
def grant(record: dict, *, owner_user_id: str, actor: str, channel: str,
          turn_id: str) -> Iterator[None]:
    args = dict(record.get("args") or {})
    value = FrozenPlanGrant(
        executor=str(record.get("executor") or ""),
        executor_binding=str(record.get("executor_binding") or ""),
        args_fingerprint=fingerprint(args),
        owner_user_id=str(owner_user_id or ""),
        actor=str(actor or ""),
        channel=str(channel or ""),
        turn_id=str(turn_id or ""),
        token=str(record.get("token") or ""),
    )
    scope = authorization.set(value)
    try:
        yield
    finally:
        authorization.reset(scope)


def authorized_token(executor, args: dict, *, owner_user_id: str, actor: str,
                     channel: str, turn_id: str) -> str | None:
    """Return a token only when every authenticated invocation field matches."""
    policy = policy_for(executor)
    if not policy or args.get(policy["argument"]) != policy["apply_value"]:
        return ""
    token = args.get(policy["token_argument"])
    current = authorization.get()
    expected = FrozenPlanGrant(
        executor=str(getattr(executor, "name", "") or ""),
        executor_binding=executor_binding(executor),
        args_fingerprint=fingerprint(args),
        owner_user_id=str(owner_user_id or ""),
        actor=str(actor or ""), channel=str(channel or ""),
        turn_id=str(turn_id or ""), token=str(token or ""),
    )
    if (not isinstance(token, str) or not _TOKEN_RE.fullmatch(token)
            or not all((expected.owner_user_id, expected.actor,
                        expected.channel, expected.turn_id))
            or current is None):
        return None
    fields = (
        "executor", "executor_binding", "args_fingerprint", "owner_user_id",
        "actor", "channel", "turn_id", "token",
    )
    if not all(hmac.compare_digest(getattr(current, field), getattr(expected, field))
               for field in fields):
        return None
    return token


def grant_environment(executor, args: dict, *, owner_user_id: str, actor: str,
                      channel: str, turn_id: str) -> dict[str, str]:
    token = authorized_token(
        executor, args, owner_user_id=owner_user_id, actor=actor,
        channel=channel, turn_id=turn_id)
    return {"METNOS_FROZEN_PLAN_AUTHORIZATION": token} if token else {}


def recovery_evidence(record: dict, *, turn_id: str) -> bool:
    """Verify the signed replay key and its durable plan/journal namespace."""
    token = str(record.get("token") or "")
    if (record.get("recovery") != "same_token_write_ahead_v1"
            or not _TOKEN_RE.fullmatch(token)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", str(turn_id or ""))
            or str(turn_id) in {".", ".."}):
        return False
    suffixes = [record.get("artifact_suffix"), record.get("journal_suffix")]
    if any(not isinstance(suffix, str)
           or not _ARTIFACT_SUFFIX_RE.fullmatch(suffix) for suffix in suffixes):
        return False
    try:
        import os
        from pathlib import Path
        import config as _C
        history = Path(os.environ.get("METNOS_HISTORY_DIR") or (
            Path(_C.PATH_USER_DATA) / "_history"))
        blob = history / str(turn_id) / "blob"
        if blob.is_symlink() or not blob.is_dir():
            return False
        plan = blob / f"{token}{suffixes[0]}"
        if plan.is_symlink() or not plan.is_file() or plan.stat().st_size > 128 << 20:
            return False
        plan_payload = json.loads(plan.read_text(encoding="utf-8"))
        if not isinstance(plan_payload, dict) or not hmac.compare_digest(
                str(plan_payload.get("token") or ""), token):
            return False
        journal = blob / f"{token}{suffixes[1]}"
        if journal.exists():
            if (journal.is_symlink() or not journal.is_file()
                    or journal.stat().st_size > 128 << 20):
                return False
            journal_payload = json.loads(journal.read_text(encoding="utf-8"))
            if not isinstance(journal_payload, dict) or not hmac.compare_digest(
                    str(journal_payload.get("token") or ""), token):
                return False
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
