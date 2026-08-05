#!/usr/bin/env python3
"""Deterministic inventory for incremental executor-standard migration.

The report is evidence for planning, not a conformance certificate.  It never
edits manifests and never infers semantic correctness from structural lint.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import tempfile
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from executor_standard import (  # noqa: E402
    STANDARD_ID,
    uses_active_profile,
    validate_manifest,
)
from executor_metadata import (  # noqa: E402
    membership_kind as _membership_kind,
    source_kind as _source_kind,
    standard_state as _standard_state,
    transport_kind as _transport_kind,
)
from naming_grammar import parse_name  # noqa: E402
from vocab import DESTRUCTIVE_VERBS  # noqa: E402


REPORT_SCHEMA = 3
_INTELLIGENCE_SIGNALS = (
    "agentic_executor",
    "AgenticExecutor",
    "LLMClient",
    "_call_llm",
    "vlm_client",
)


@dataclasses.dataclass(frozen=True)
class ExecutorAudit:
    name: str
    path: str
    domain: str
    membership: str
    source: str
    transport: str
    state: str
    lifecycle: str
    risk: str
    risk_axes: tuple[str, ...]
    findings: tuple[dict[str, str], ...]
    structural_ready: bool
    birth_tests: int
    signature_present: bool
    required_gates: tuple[str, ...]
    surfaces: tuple[str, ...] = ()
    rejection_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        for key in ("risk_axes", "findings", "required_gates"):
            data[key] = list(data[key])
        return data


def _capability_names(manifest: dict) -> tuple[str, ...]:
    values = []
    for capability in manifest.get("capabilities") or []:
        if isinstance(capability, dict) and isinstance(capability.get("name"), str):
            values.append(capability["name"].lower())
    return tuple(sorted(set(values)))


def _has_intelligence_signal(executor_dir: Path, manifest: dict) -> bool:
    for filename in (manifest.get("code") or {}).get("files") or []:
        path = executor_dir / str(filename)
        if not path.is_file() or path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(signal in text for signal in _INTELLIGENCE_SIGNALS):
            return True
    return False


def _risk_profile(executor_dir: Path, manifest: dict) -> tuple[str, tuple[str, ...]]:
    name = str(manifest.get("name") or executor_dir.name)
    components = parse_name(name)
    verb = components.verb if components else name.split("_", 1)[0]
    capabilities = _capability_names(manifest)
    placement = manifest.get("placement") or {}
    axes: set[str] = set()

    if verb in DESTRUCTIVE_VERBS or bool(manifest.get("revertible")):
        axes.add("mutation")
    if any("credentials" in cap or cap.startswith("dialog") for cap in capabilities):
        axes.add("authority")
    if any("network" in cap for cap in capabilities):
        axes.add("network")
    if any(cap.startswith("mail:") for cap in capabilities):
        axes.update(("authority", "network", "provider"))
    capability_hints = {
        str(capability.get("name")): {
            hint for hint in capability.get("hint", [])
            if isinstance(hint, str)
        }
        for capability in manifest.get("capabilities") or []
        if isinstance(capability, dict)
    }
    if "image" in capability_hints.get("index:read", set()):
        axes.add("privacy")
    if capability_hints.get("metnos:read", set()) & {
        "persons_registry:local", "identity_profile:local",
    }:
        axes.add("privacy")
    if "provider:access" in capabilities:
        axes.update(("authority", "network", "provider"))
    if "system:read" in capabilities:
        system_hints = {
            hint
            for capability in manifest.get("capabilities") or []
            if isinstance(capability, dict)
            and capability.get("name") == "system:read"
            for hint in capability.get("hint", [])
            if isinstance(hint, str)
        }
        if "network_interfaces" in system_hints:
            axes.add("network")
    provider_suffixes: frozenset[str] = frozenset()
    provider_skills: dict[str, str] = {}
    try:
        from vocab import PROVIDER_SKILLS, PROVIDER_SUFFIXES
        provider_suffixes = PROVIDER_SUFFIXES
        provider_skills = PROVIDER_SKILLS
    except ImportError:
        pass
    client_spec = (((manifest.get("args") or {}).get("properties") or {}).get("client") or {})
    client_values = client_spec.get("enum") or []
    if (
        any(name.endswith(f"_{suffix}") for suffix in provider_suffixes)
        or any(value in provider_skills for value in client_values if isinstance(value, str))
        or any(cap.startswith("skill:") for cap in capabilities)
        or bool((manifest.get("provenance") or {}).get("imported_from"))
    ):
        axes.update(("authority", "network", "provider"))
    if isinstance(placement, dict) and (
        placement.get("scope") == "device"
        or (placement.get("scope") == "any" and placement.get("device_ok") is True)
    ):
        axes.add("remote")
    if _has_intelligence_signal(executor_dir, manifest):
        axes.add("intelligent")

    if "authority" in axes or ({"mutation", "network"} <= axes):
        risk = "critical"
    elif axes & {"mutation", "remote", "intelligent", "privacy"}:
        risk = "high"
    elif "network" in axes:
        risk = "medium"
    else:
        risk = "low"
    return risk, tuple(sorted(axes))


def _required_gates(axes: tuple[str, ...]) -> tuple[str, ...]:
    gates = {
        "manifest",
        "signature",
        "success-empty-invalid-dependency",
        "i18n-it-en",
        "natural-paraphrases",
        "regression",
        "e2e",
    }
    if "mutation" in axes:
        gates.update(("postcondition", "repeat-interruption", "reverse-or-nonreversible"))
    if "authority" in axes:
        gates.update(("mandate-denial", "secret-redaction", "handoff"))
    if "remote" in axes:
        gates.update(("remote-equivalence", "target-no-fallback"))
    if "intelligent" in axes:
        gates.update(("proposal-rejection", "budget-exhaustion", "untrusted-observation"))
    if "provider" in axes:
        gates.update(("dependency-unavailable", "provider-error-normalization"))
    if "privacy" in axes:
        gates.update(("data-minimization", "secret-redaction"))
    return tuple(sorted(gates))


def _display_path(path: Path, *, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        try:
            from config import PATH_USER_DATA
            return "${METNOS_USER_DATA}/" + path.relative_to(PATH_USER_DATA).as_posix()
        except (ImportError, ValueError):
            return path.as_posix()


def _manifest_source(path: Path, manifest: dict, *, root: Path) -> str:
    try:
        path.relative_to(root / "executors")
        synthesized = False
    except ValueError:
        synthesized = True
    return _source_kind(manifest, synthesized=synthesized)


def _manifest_transport(manifest: dict) -> str:
    return _transport_kind(manifest)


def audit_manifest(path: Path, *, root: Path, source: str | None = None,
                   transport: str | None = None,
                   surfaces: tuple[str, ...] = ("configured",),
                   rejection_reasons: tuple[str, ...] = ()) -> ExecutorAudit:
    with path.open("rb") as handle:
        manifest = tomllib.load(handle)
    name = str(manifest.get("name") or path.parent.name)
    declaration = manifest.get("executor_standard")
    lifecycle = str(manifest.get("lifecycle", "active"))
    active = uses_active_profile(lifecycle)
    findings = validate_manifest(
        manifest,
        require_declaration=declaration is not None,
        active=active,
    )
    risk, axes = _risk_profile(path.parent, manifest)
    components = parse_name(name)
    domain = components.obj if components else "unclassified"
    declared_state = _standard_state(declaration, lifecycle)
    state = {
        "declared": "conformant_claim",
        "candidate": "candidate",
        "legacy": "legacy",
        "invalid": "unknown_standard",
    }[declared_state]
    return ExecutorAudit(
        name=name,
        path=_display_path(path, root=root),
        domain=domain,
        membership=_membership_kind(manifest),
        source=source or _manifest_source(path, manifest, root=root),
        transport=transport or _manifest_transport(manifest),
        state=state,
        lifecycle=lifecycle,
        risk=risk,
        risk_axes=axes,
        findings=tuple({"code": item.code, "message": item.message} for item in findings),
        structural_ready=not findings,
        birth_tests=len(manifest.get("tests") or []),
        signature_present=path.with_suffix(path.suffix + ".sig").is_file(),
        required_gates=_required_gates(axes),
        surfaces=tuple(sorted(set(surfaces))),
        rejection_reasons=rejection_reasons,
    )


def _summarize(audits: list[ExecutorAudit]) -> dict[str, Any]:
    finding_counts = Counter(
        finding["code"] for item in audits for finding in item.findings
    )
    state_counts = Counter(item.state for item in audits)
    risk_counts = Counter(item.risk for item in audits)
    source_counts = Counter(item.source for item in audits)
    transport_counts = Counter(item.transport for item in audits)
    surface_counts = Counter(surface for item in audits for surface in item.surfaces)
    return {
        "total": len(audits),
        "states": dict(sorted(state_counts.items())),
        "risks": dict(sorted(risk_counts.items())),
        "sources": dict(sorted(source_counts.items())),
        "transports": dict(sorted(transport_counts.items())),
        "surfaces": dict(sorted(surface_counts.items())),
        "structural_ready": sum(item.structural_ready for item in audits),
        "findings": sum(finding_counts.values()),
        "findings_by_code": dict(sorted(finding_counts.items())),
    }


def _report(audits: list[ExecutorAudit], *, scope: str) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA,
        "standard": STANDARD_ID,
        "scope": scope,
        "summary": _summarize(audits),
        "executors": [item.as_dict() for item in audits],
    }


def build_report(executors_root: Path, *, root: Path = ROOT) -> dict[str, Any]:
    core_paths = sorted(executors_root.glob("*/manifest.toml"))
    builtin_root = root / "runtime" / "builtin_executor_contracts"
    builtin_paths = sorted(builtin_root.glob("*/manifest.toml"))
    audits = [audit_manifest(path, root=root) for path in core_paths]
    audits.extend(
        audit_manifest(path, root=root, source="builtin",
                       transport="in-process",
                       surfaces=("configured",))
        for path in builtin_paths
    )
    audits.sort(key=lambda item: (item.name, item.source, item.path))
    return _report(audits, scope="core-manifests")


def _same_manifest(executor: object, path: Path) -> bool:
    manifest_path = Path(getattr(executor, "manifest_path", ""))
    try:
        return manifest_path.resolve() == path.resolve()
    except OSError:
        return manifest_path == path


def _rejections_for(path: Path, rejected: list[tuple[str, str]]) -> tuple[str, ...]:
    reasons = []
    for rejected_path, reason in rejected:
        candidate = Path(rejected_path)
        if candidate == path or candidate == path.parent:
            reasons.append(str(reason))
    return tuple(sorted(set(reasons)))


def _audit_virtual(executor: object, *, surfaces: tuple[str, ...]) -> ExecutorAudit:
    name = str(getattr(executor, "name", "") or "unknown")
    capabilities = list(getattr(executor, "capabilities", []) or [])
    pseudo_manifest = {
        "name": name,
        "args": getattr(executor, "args_schema", {}) or {},
        "capabilities": capabilities,
        "placement": getattr(executor, "placement", {}) or {},
    }
    risk, axes = _risk_profile(Path(getattr(executor, "manifest_path", "") or "."), pseudo_manifest)
    components = parse_name(name)
    findings = [
        {"code": "standard_missing", "message": "virtual executor has no standard declaration"},
        {"code": "signed_contract_missing", "message": "virtual executor has no signed contract artifact"},
        {"code": "output_schema", "message": "virtual executor does not expose an output schema"},
        {"code": "tests", "message": "virtual executor does not expose contract tests"},
    ]
    if not capabilities:
        findings.append({"code": "capabilities", "message": "virtual executor declares no capability"})
    manifest_path = Path(getattr(executor, "manifest_path", "") or ".")
    return ExecutorAudit(
        name=name,
        path=manifest_path.as_posix(),
        domain=components.obj if components else "unclassified",
        membership="builtin",
        source="builtin",
        transport="in-process",
        state="legacy_virtual",
        lifecycle=str(getattr(executor, "lifecycle", "active")),
        risk=risk,
        risk_axes=axes,
        findings=tuple(findings),
        structural_ready=False,
        birth_tests=0,
        signature_present=False,
        required_gates=_required_gates(axes),
        surfaces=tuple(sorted(set(surfaces))),
    )


def build_live_report(*, root: Path = ROOT) -> dict[str, Any]:
    """Inventory configured, loadable, admitted and planner-visible surfaces."""
    from loader import SYNTHESIZED_EXECUTORS_DIR, invalidate_catalog_cache, load_catalog
    import agent_runtime

    invalidate_catalog_cache()
    loadable = load_catalog(verify=False)
    invalidate_catalog_cache()
    admitted = load_catalog(verify=True)
    planner = agent_runtime._engine_v2_catalog_with_builtins(list(admitted))
    planner_by_name = {executor.name: executor for executor in planner}

    paths = list(sorted((root / "executors").glob("*/manifest.toml")))
    builtin_contract_paths = list(sorted(
        (root / "runtime" / "builtin_executor_contracts").glob(
            "*/manifest.toml")))
    paths.extend(builtin_contract_paths)
    if SYNTHESIZED_EXECUTORS_DIR.exists():
        paths.extend(sorted(SYNTHESIZED_EXECUTORS_DIR.rglob("manifest.toml")))

    audits: list[ExecutorAudit] = []
    for path in paths:
        with path.open("rb") as handle:
            manifest = tomllib.load(handle)
        name = str(manifest.get("name") or path.parent.name)
        surfaces = ["configured"]
        if name in loadable.executors and _same_manifest(loadable.executors[name], path):
            surfaces.append("loadable")
        if name in admitted.executors and _same_manifest(admitted.executors[name], path):
            surfaces.append("admitted")
        if name in planner_by_name and _same_manifest(planner_by_name[name], path):
            surfaces.append("planner")
        source = "builtin" if path in builtin_contract_paths else None
        transport = "in-process" if path in builtin_contract_paths else None
        audits.append(audit_manifest(
            path,
            root=root,
            source=source,
            transport=transport,
            surfaces=tuple(surfaces),
            rejection_reasons=_rejections_for(path, admitted.rejected),
        ))

    virtual_by_name: dict[str, object] = {}
    for executor in loadable:
        if not str(getattr(executor, "manifest_path", "")).endswith("manifest.toml"):
            virtual_by_name[executor.name] = executor
    for executor in planner:
        if not str(getattr(executor, "manifest_path", "")).endswith("manifest.toml"):
            virtual_by_name[executor.name] = executor
    for name, executor in sorted(virtual_by_name.items()):
        surfaces = ["loadable"] if name in loadable.executors else []
        if name in admitted.executors:
            surfaces.append("admitted")
        if name in planner_by_name:
            surfaces.append("planner")
        audits.append(_audit_virtual(executor, surfaces=tuple(surfaces)))

    audits.sort(key=lambda item: (item.name, item.source, item.path))
    return _report(audits, scope="live-catalog-surfaces")


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Executor legacy conformance inventory",
        "",
        f"- Standard: `{report['standard']}`",
        f"- Total: `{summary['total']}`",
        f"- Structural ready: `{summary['structural_ready']}`",
        f"- Findings: `{summary['findings']}`",
        f"- Surfaces: `{summary.get('surfaces', {})}`",
        "",
        "| Executor | Source | Surfaces | State | Risk | Findings | Tests |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for item in report["executors"]:
        lines.append(
            f"| {item['name']} | {item['source']} | {', '.join(item['surfaces'])} | "
            f"{item['state']} | {item['risk']} | "
            f"{len(item['findings'])} | {item['birth_tests']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executors", type=Path, default=ROOT / "executors")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--live", action="store_true",
                        help="include installed and virtual catalog surfaces")
    args = parser.parse_args(argv)
    report = build_live_report() if args.live else build_report(args.executors.resolve())
    report_path = args.report or (
        ROOT / "internal" / "reports" /
        ("executor_conformance_live.json" if args.live else "executor_conformance.json")
    )
    _write_atomic(report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    _write_atomic(report_path.with_suffix(".md"), _markdown(report))
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
