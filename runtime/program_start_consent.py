"""Remember explicit installed-program start permissions, never commands.

The existing policy registry owns persistence and revocation. These grants
use a separate channel namespace so they cannot authorize ordinary policy
checks, application closure, installation or startup registration. Creation
is called only at the authenticated, claimed user-dialog boundary.
"""
from __future__ import annotations

import json
import re

from executor_helpers import approval_digest

_ROLE = "managed-package-start"
_CHANNEL = "program-start-consent-v1"
_CAPABILITY = "system:admin"


def _supports(executor) -> bool:
    props = (getattr(executor, "args_schema", None) or {}).get("properties", {})
    return (any(
        isinstance(cap, dict) and cap.get("name") == _CAPABILITY
        and _ROLE in (cap.get("hint") or [])
        for cap in (getattr(executor, "capabilities", None) or []))
        and all(isinstance(props.get(key), dict)
                and props[key].get("runtime_resolved") is True
                for key in ("authorization_scope", "authorization_boot_id",
                            "actor_consent_token", "lifetime")))


def _programs(args: dict) -> list[str]:
    programs = args.get("programs")
    if (not isinstance(programs, list) or not 1 <= len(programs) <= 10
            or any(not isinstance(p, str) or not p or len(p) > 132
                   for p in programs)
            or len({p.casefold() for p in programs}) != len(programs)):
        return []
    return programs


def _material(args: dict) -> dict:
    return {key: args.get(key) for key in (
        "programs", "lifetime", "authorization_scope", "authorization_boot_id")}


def _target(executor_name: str, device_id: str, program: str,
            scope: str, boot_id: str) -> dict:
    return {"executor": executor_name, "device": device_id, "program": program,
            "scope": scope, "boot_id": boot_id, "lifetime": "session"}


def _rows(owner: str):
    import policy
    from timefmt import now_iso_z
    now = now_iso_z()
    for grant in policy.list_grants(channel=_CHANNEL, sender_id=owner):
        if grant.capability != _CAPABILITY or (grant.expires_at and grant.expires_at <= now):
            continue
        try:
            target = json.loads(grant.target)
        except (ValueError, TypeError):
            continue
        if isinstance(target, dict):
            yield grant, target


def bind_prompt(executor, result, *, device_id: str):
    """Bind the human choice to the actual PC UUID, not its mutable name."""
    if not _supports(executor) or not isinstance(result, dict):
        return result
    payload = result.get("needs_inputs")
    callback = payload.get("on_complete") if isinstance(payload, dict) else None
    if isinstance(callback, dict) and callback.get("type") == "gate_dispatch":
        callback["target_device"] = device_id
    return result


def apply_saved(executor, args: dict, *, owner: str, device_id: str) -> dict:
    """Project saved permission only after authenticated placement chose a PC.

    The device checks its current boot before an until-restart permission can
    cause any launch. An old boot returns a fresh question, not an execution.
    """
    if (not owner or not device_id or not _supports(executor)
            or args.get("actor_consent_token") or args.get("lifetime") not in (None, "session")):
        return args
    programs = _programs(args)
    if not programs:
        return args
    grants = {}
    for _, target in _rows(owner):
        if (target.get("executor") == executor.name
                and target.get("device") == device_id
                and target.get("lifetime") == "session"
                and isinstance(target.get("scope"), str)
                and isinstance(target.get("boot_id"), str)
                and target.get("program") in programs):
            grants.setdefault(target["program"], target)
    if len(grants) != len(programs):
        return args
    scopes = {g.get("scope") for g in grants.values()}
    if not scopes <= {"until_restart", "always"}:
        return args
    boots = {g.get("boot_id") for g in grants.values()
             if g.get("scope") == "until_restart"}
    if boots and (len(boots) != 1 or not all(
            isinstance(b, str) and re.fullmatch(r"[0-9]{1,20}", b) for b in boots)):
        return args
    out = dict(args, lifetime="session",
               authorization_scope="until_restart" if boots else "always",
               authorization_boot_id=next(iter(boots), ""))
    out["actor_consent_token"] = approval_digest(_material(out))
    return out


def remember_verified_dialog(state: dict, *, actor: str, owner: str) -> None:
    """Persist only a new, submitted selection, never a historical completed flag.

    The caller has already claimed the callback exactly once. We also check
    the original owner, expiry, selected branch, admitted starter and owned
    device; planner text and ordinary executor results cannot create grants.
    """
    import dialog_pending
    import devices
    import policy
    from loader import load_catalog

    if (not owner or state.get("owner_user_id") != owner
            or not state.get("completed") or state.get("cancelled")
            or dialog_pending.is_expired(state)
            or devices.owner_id_for_actor(actor) != owner):
        return
    callback = state.get("on_complete") or {}
    if (callback.get("owner_user_id") != owner
            or callback.get("type") not in {"gate_dispatch", "managed_dependency_resume"}):
        return
    values = state.get("values_collected") or {}
    if set(values) != {"decision"}:
        return
    scope = values["decision"]
    if scope not in {"until_restart", "always"}:
        return
    submission = (state.get("submissions") or {}).get("decision") or {}
    if submission.get("source") not in {
            "http_chat", "http_form_owner", "http_form_capability",
            "telegram_chat", "telegram_button"}:
        return
    branch = (callback.get("branches") or {}).get(scope)
    if not isinstance(branch, dict):
        return
    args = branch.get("args") or {}
    programs = _programs(args)
    boot = args.get("authorization_boot_id")
    if (not programs or args.get("authorization_scope") != scope
            or args.get("lifetime") != "session"
            or not isinstance(boot, str)
            or (scope == "until_restart" and not re.fullmatch(r"[0-9]{1,20}", boot))
            or (scope == "always" and boot != "")
            or args.get("actor_consent_token") != approval_digest(_material(args))):
        return
    catalog = load_catalog(verify=True, include_synth=True)
    executor = catalog.executors.get(branch.get("tool"))
    if executor is None or not _supports(executor):
        return
    target = callback.get("target_device")
    if not isinstance(target, str) or not target:
        return
    device = devices.get_device(target)
    if device is None or device.owner_user_id != owner:
        return
    device_id = target
    # Supersede old boot permissions for these exact targets; keep revoked
    # rows as audit history in the registry's existing revocation mechanism.
    for grant, prior in _rows(owner):
        if (prior.get("executor") == executor.name
                and prior.get("device") == str(device_id)
                and prior.get("program") in programs):
            policy.revoke_grant(grant.id)
    for program in programs:
        policy.record_grant(
            channel=_CHANNEL, sender_id=owner, capability=_CAPABILITY,
            target=json.dumps(_target(executor.name, str(device_id), program,
                                      scope, boot), sort_keys=True),
            granted_by=owner)
