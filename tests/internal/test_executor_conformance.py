from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tests" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import executor_conformance as workbench  # noqa: E402


def _fixture(root: Path, *, name: str, capability: str, declaration: bool) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / f"{name}.py").write_text(
        "def invoke(args):\n    return {'ok': True, 'entries': []}\n",
        encoding="utf-8",
    )
    standard = f'executor_standard = "{workbench.STANDARD_ID}"\n' if declaration else ""
    manifest = f'''manifest_format = "1.0"
{standard}name = "{name}"
version = "1.0.0"
revertible = false

[description]
it = "SCOPO: legge dati. PATTERN: {name}(). NON: scrivere. OUT: entries=[]."
en = "SCOPO: reads data. PATTERN: {name}(). NON: write. OUT: entries=[]."

[code]
files = ["{name}.py"]
digest = "sha256:{'a' * 64}"

[args]
type = "object"
required = []

[output]
schema_inline = "{{ok: bool, entries: list}}"

[[capabilities]]
name = "{capability}"
hint = []

[[tests]]
name = "empty"
input = {{}}
expect = {{ok = true}}
'''
    path = directory / "manifest.toml"
    path.write_text(manifest, encoding="utf-8")
    (directory / "manifest.toml.sig").write_text("signature", encoding="ascii")
    return path


def test_report_separates_legacy_from_conformant_claim(tmp_path: Path) -> None:
    root = tmp_path / "executors"
    _fixture(root, name="read_files", capability="fs:read", declaration=False)
    _fixture(root, name="read_events", capability="network:http", declaration=True)

    report = workbench.build_report(root, root=tmp_path)

    assert report["summary"]["total"] == 2
    assert report["summary"]["states"] == {"conformant_claim": 1, "legacy": 1}
    assert report["summary"]["structural_ready"] == 2


def test_report_exposes_unknown_capability_as_legacy_debt(tmp_path: Path) -> None:
    root = tmp_path / "executors"
    path = _fixture(
        root, name="read_events", capability="network:read", declaration=False,
    )

    item = workbench.audit_manifest(path, root=tmp_path)

    assert item.state == "legacy"
    assert "capability_unknown" in {finding["code"] for finding in item.findings}
    assert item.structural_ready is False


def test_risk_and_gates_are_derived_from_contract(tmp_path: Path) -> None:
    root = tmp_path / "executors"
    path = _fixture(
        root, name="delete_credentials", capability="metnos:credentials_metadata_only",
        declaration=False,
    )
    text = path.read_text(encoding="utf-8").replace(
        "revertible = false", "revertible = true\nreverse_pattern = \"restore\"",
    )
    path.write_text(text, encoding="utf-8")

    item = workbench.audit_manifest(path, root=tmp_path)

    assert item.risk == "critical"
    assert {"authority", "mutation"} <= set(item.risk_axes)
    assert {"mandate-denial", "postcondition", "natural-paraphrases"} <= set(
        item.required_gates,
    )


def test_provider_and_remote_axes_are_not_hidden_by_generic_capability(tmp_path: Path) -> None:
    root = tmp_path / "executors"
    path = _fixture(
        root, name="read_files_google_workspace", capability="metnos:read",
        declaration=False,
    )
    text = path.read_text(encoding="utf-8").replace(
        'revertible = false',
        'revertible = false\n\n[placement]\nscope = "any"\n'
        'device_ok = true\nmin_sandbox = "appcontainer"',
    )
    path.write_text(text, encoding="utf-8")

    item = workbench.audit_manifest(path, root=tmp_path)

    assert item.transport == "local-or-remote"
    assert {"authority", "network", "provider", "remote"} <= set(item.risk_axes)
    assert "provider-error-normalization" in item.required_gates


def test_mail_and_system_network_authority_are_counted_from_capabilities(
        tmp_path: Path) -> None:
    root = tmp_path / "executors"
    mail = _fixture(
        root, name="read_messages", capability="mail:read", declaration=False,
    )
    system = _fixture(
        root, name="read_processes", capability="system:read", declaration=False,
    )
    system.write_text(
        system.read_text(encoding="utf-8").replace(
            "hint = []", 'hint = ["network_interfaces"]',
        ),
        encoding="utf-8",
    )

    mail_item = workbench.audit_manifest(mail, root=tmp_path)
    system_item = workbench.audit_manifest(system, root=tmp_path)

    assert mail_item.risk == "critical"
    assert {"authority", "network", "provider"} <= set(mail_item.risk_axes)
    assert {"mandate-denial", "secret-redaction"} <= set(
        mail_item.required_gates,
    )
    assert "network" in system_item.risk_axes


def test_image_index_and_person_registry_are_privacy_sensitive(
        tmp_path: Path) -> None:
    root = tmp_path / "executors"
    path = _fixture(
        root, name="find_images_indices", capability="index:read",
        declaration=False,
    )
    text = path.read_text(encoding="utf-8").replace(
        "hint = []", 'hint = ["image"]',
    ).replace(
        "[[tests]]", '[[capabilities]]\nname = "metnos:read"\n'
        'hint = ["persons_registry:local"]\n\n[[tests]]',
    )
    path.write_text(text, encoding="utf-8")

    item = workbench.audit_manifest(path, root=tmp_path)

    assert item.risk == "high"
    assert "privacy" in item.risk_axes
    assert {"data-minimization", "secret-redaction"} <= set(item.required_gates)


def test_semantic_filesystem_hint_must_reference_a_typed_argument(
        tmp_path: Path) -> None:
    root = tmp_path / "executors"
    path = _fixture(
        root, name="find_images_indices", capability="fs:read",
        declaration=True,
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "hint = []", 'hint = ["arg:reference_images"]',
        ),
        encoding="utf-8",
    )

    item = workbench.audit_manifest(path, root=tmp_path)

    assert "filesystem_authority_arg" in {
        finding["code"] for finding in item.findings
    }


def test_summary_counts_each_catalog_surface_independently(tmp_path: Path) -> None:
    root = tmp_path / "executors"
    first = _fixture(root, name="read_files", capability="fs:read", declaration=False)
    second = _fixture(root, name="read_events", capability="network:read", declaration=False)
    audits = [
        workbench.audit_manifest(
            first, root=tmp_path,
            surfaces=("configured", "loadable", "admitted", "planner"),
        ),
        workbench.audit_manifest(
            second, root=tmp_path,
            surfaces=("configured", "loadable"),
        ),
    ]

    summary = workbench._summarize(audits)

    assert summary["surfaces"] == {
        "admitted": 1,
        "configured": 2,
        "loadable": 2,
        "planner": 1,
    }


def test_virtual_executor_is_visible_debt_not_implicit_conformance() -> None:
    executor = SimpleNamespace(
        name="compare_entries",
        args_schema={"type": "object", "properties": {}},
        capabilities=[],
        placement={},
        manifest_path=Path("runtime/compare_entries.py"),
        lifecycle="active",
    )

    item = workbench._audit_virtual(executor, surfaces=("admitted", "planner"))

    assert item.source == "builtin"
    assert item.transport == "in-process"
    assert item.state == "legacy_virtual"
    assert {"signed_contract_missing", "output_schema", "tests"} <= {
        finding["code"] for finding in item.findings
    }
