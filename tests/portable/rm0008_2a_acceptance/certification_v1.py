"""Canonical manifest, inventory, evidence, and workflow checks for RM-0008 2A."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

try:
    from .required_cells_v1 import EXPECTED_ACTIVITY_COUNTS_V1, REQUIRED_CELLS_V1
except ImportError:  # Direct execution by the workflow-owned scripts.
    from required_cells_v1 import EXPECTED_ACTIVITY_COUNTS_V1, REQUIRED_CELLS_V1


SUITE_ID = "rm-0008-increment-2a"
REPO_ROOT = Path(__file__).resolve().parents[3]
A_PORTABLE_ROOT = REPO_ROOT / "tests/portable/rm0008_2a_acceptance"
A_WINDOWS_ROOT = REPO_ROOT / "tests/windows_identity/rm0008_2a_acceptance"
MANIFEST_PATH = REPO_ROOT / "tests/portable/rm0008-2a-acceptance-manifest-v1.json"
INVENTORY_PATH = A_PORTABLE_ROOT / "production-python-inventory-v1.json"
SNAPSHOT_PATH = REPO_ROOT / "tests/portable/rm0008-2a-pre-fix-evidence-v1.json"
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/portable-contract-store.yml"

ACTIVITIES = (
    "manifest",
    "portable-ubuntu",
    "portable-windows",
    "concurrency-ubuntu",
    "concurrency-windows",
    "windows-acl",
)
ACTIVITY_PLATFORM = {
    "manifest": "platform-independent",
    "portable-ubuntu": "linux",
    "portable-windows": "windows",
    "concurrency-ubuntu": "linux",
    "concurrency-windows": "windows",
    "windows-acl": "windows",
}
CELL_FIELDS = {
    "criterion",
    "node_id",
    "activity",
    "platform",
    "production_symbols",
    "certification_symbols",
    "oracle",
    "normative_subcase",
    "pre_fix_disposition",
}
ORACLES = {
    "ast-call-graph",
    "byte-comparison",
    "handle-counter",
    "inventory-snapshot",
    "monotonic-barrier",
    "posix-disk-state",
    "posix-fstat",
    "posix-syscall-trace",
    "process-exit-state",
    "pytest-collection",
    "win32-acl",
    "win32-file-identity",
    "win32-token-access",
    "win32-volume",
    "workflow-structure",
}
CRITERION_RE = re.compile(r"^(?:R[1-8]|C[1-4]|G(?:[1-9]|1[0-2]))$")
SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*::[A-Za-z_][A-Za-z0-9_]*$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FINAL_EVIDENCE_FIELDS = {
    "schema_version",
    "suite_id",
    "git_sha",
    "manifest_sha256",
    "production_inventory_sha256",
    "runner_image",
    "activity",
    "results",
}


class CertificationError(AssertionError):
    """A stable certification failure suitable for pytest and workflow use."""


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def write_canonical_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def _load_canonical_object(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CertificationError(f"required canonical JSON is unavailable: {path}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CertificationError(f"invalid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CertificationError(f"top-level JSON object required: {path}")
    if raw != canonical_json_bytes(value):
        raise CertificationError(f"non-canonical JSON bytes: {path}")
    return value, raw


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise CertificationError(
            f"{label} fields differ: expected {sorted(expected)!r}, got {sorted(value)!r}"
        )


def _text(value: object, label: str, *, ascii_only: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise CertificationError(f"{label} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise CertificationError(f"{label} must be NFC")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise CertificationError(f"{label} contains a control character")
    if ascii_only and not value.isascii():
        raise CertificationError(f"{label} must be ASCII")
    return value


def _sorted_unique_strings(
    value: object, label: str, *, pattern: re.Pattern[str] | None = None
) -> list[str]:
    if not isinstance(value, list):
        raise CertificationError(f"{label} must be a list")
    result = [_text(item, label, ascii_only=pattern is not None) for item in value]
    if pattern is not None and any(pattern.fullmatch(item) is None for item in result):
        raise CertificationError(f"{label} contains an invalid value")
    if result != sorted(set(result), key=lambda item: item.encode("utf-8")):
        raise CertificationError(f"{label} must be byte-sorted and duplicate-free")
    return result


def _node_id(value: object, slug: str) -> str:
    node_id = _text(value, "node_id", ascii_only=True)
    if any(character.isspace() for character in node_id):
        raise CertificationError("node_id contains whitespace")
    if not node_id.endswith(f"[{slug}]") or "::" not in node_id:
        raise CertificationError("node_id must be complete and end in the normative slug")
    path_text = node_id.split("::", 1)[0]
    if "\\" in path_text or path_text.startswith("/"):
        raise CertificationError("node_id path must be repository-relative POSIX")
    if not (
        path_text.startswith("tests/portable/rm0008_2a_acceptance/")
        or path_text.startswith("tests/windows_identity/rm0008_2a_acceptance/")
    ):
        raise CertificationError("node_id is outside the two A-only trees")
    parts = PurePosixPath(path_text).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise CertificationError("node_id path is not canonical")
    return node_id


def validate_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest, _ = _load_canonical_object(path)
    _exact_keys(manifest, {"cells", "schema_version", "suite_id"}, "manifest")
    if manifest["schema_version"] != 1 or isinstance(manifest["schema_version"], bool):
        raise CertificationError("manifest schema_version must be integer 1")
    if manifest["suite_id"] != SUITE_ID:
        raise CertificationError("manifest suite_id mismatch")
    cells = manifest["cells"]
    if not isinstance(cells, list):
        raise CertificationError("manifest cells must be a list")

    normalized: list[dict[str, Any]] = []
    for index, raw_cell in enumerate(cells):
        if not isinstance(raw_cell, dict):
            raise CertificationError(f"cell {index} must be an object")
        _exact_keys(raw_cell, CELL_FIELDS, f"cell {index}")
        criterion = _text(raw_cell["criterion"], "criterion", ascii_only=True)
        if CRITERION_RE.fullmatch(criterion) is None:
            raise CertificationError(f"invalid criterion: {criterion}")
        activity = _text(raw_cell["activity"], "activity", ascii_only=True)
        if activity not in ACTIVITIES:
            raise CertificationError(f"invalid activity: {activity}")
        platform = _text(raw_cell["platform"], "platform", ascii_only=True)
        if platform != ACTIVITY_PLATFORM[activity]:
            raise CertificationError(f"platform does not belong to activity {activity}")
        slug = _text(raw_cell["normative_subcase"], "normative_subcase", ascii_only=True)
        if SLUG_RE.fullmatch(slug) is None:
            raise CertificationError(f"invalid normative_subcase: {slug}")
        node_id = _node_id(raw_cell["node_id"], slug)
        disposition = _text(
            raw_cell["pre_fix_disposition"], "pre_fix_disposition", ascii_only=True
        )
        if disposition not in {"red", "absent", "may_green"}:
            raise CertificationError("invalid pre_fix_disposition")
        production = _sorted_unique_strings(
            raw_cell["production_symbols"], "production_symbols", pattern=SYMBOL_RE
        )
        certification = _sorted_unique_strings(
            raw_cell["certification_symbols"], "certification_symbols", pattern=SYMBOL_RE
        )
        if criterion == "G6":
            if production or not certification:
                raise CertificationError("G6 must name certification symbols only")
        elif not production or certification:
            raise CertificationError(f"{criterion} must name production symbols only")
        oracle = _sorted_unique_strings(raw_cell["oracle"], "oracle")
        if not oracle or any(item not in ORACLES for item in oracle):
            raise CertificationError("invalid or empty oracle list")
        normalized.append(
            {
                "criterion": criterion,
                "node_id": node_id,
                "activity": activity,
                "platform": platform,
                "production_symbols": production,
                "certification_symbols": certification,
                "oracle": oracle,
                "normative_subcase": slug,
                "pre_fix_disposition": disposition,
            }
        )

    expected_order = sorted(
        normalized,
        key=lambda cell: tuple(
            cell[field].encode("utf-8")
            for field in ("activity", "criterion", "node_id", "normative_subcase")
        ),
    )
    if normalized != expected_order:
        raise CertificationError("manifest cells are not in canonical order")

    activity_nodes = [(cell["activity"], cell["node_id"]) for cell in normalized]
    criterion_cells = [
        (cell["criterion"], cell["activity"], cell["normative_subcase"])
        for cell in normalized
    ]
    if len(activity_nodes) != len(set(activity_nodes)):
        raise CertificationError("duplicate (activity, node_id)")
    if len(criterion_cells) != len(set(criterion_cells)):
        raise CertificationError("duplicate (criterion, activity, normative_subcase)")
    by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cell in normalized:
        by_node[cell["node_id"]].append(cell)
    for node_id, duplicates in by_node.items():
        if len(duplicates) == 1:
            continue
        if len(duplicates) != 2 or {cell["activity"] for cell in duplicates} != {
            "portable-ubuntu",
            "portable-windows",
        }:
            raise CertificationError(f"invalid cross-activity node duplication: {node_id}")
        comparable = {
            (
                cell["criterion"],
                cell["normative_subcase"],
                tuple(cell["production_symbols"]),
                tuple(cell["certification_symbols"]),
                tuple(cell["oracle"]),
            )
            for cell in duplicates
        }
        if len(comparable) != 1:
            raise CertificationError(f"duplicated common node changes meaning: {node_id}")

    observed_required = {
        (
            cell["criterion"],
            cell["activity"],
            cell["platform"],
            cell["normative_subcase"],
            cell["pre_fix_disposition"],
        )
        for cell in normalized
    }
    expected_required = set(REQUIRED_CELLS_V1)
    if len(normalized) != 248 or observed_required != expected_required:
        missing = sorted(expected_required - observed_required)
        extra = sorted(observed_required - expected_required)
        raise CertificationError(
            f"manifest normative inventory mismatch; missing={missing!r}, extra={extra!r}"
        )
    counts = Counter(cell["activity"] for cell in normalized)
    if dict(counts) != EXPECTED_ACTIVITY_COUNTS_V1:
        raise CertificationError(f"manifest activity counts mismatch: {dict(counts)!r}")
    return manifest


def select_cells(manifest: Mapping[str, Any], activity: str) -> list[dict[str, Any]]:
    if activity not in ACTIVITIES:
        raise CertificationError(f"unknown activity: {activity}")
    cells = [cell for cell in manifest["cells"] if cell["activity"] == activity]
    if len(cells) != EXPECTED_ACTIVITY_COUNTS_V1[activity]:
        raise CertificationError(f"activity {activity} has the wrong cell count")
    if any(cell["platform"] != ACTIVITY_PLATFORM[activity] for cell in cells):
        raise CertificationError(f"activity {activity} contains a foreign platform")
    return cells


def validate_production_inventory(
    path: Path = INVENTORY_PATH, *, enforce_filesystem: bool = True
) -> dict[str, Any]:
    inventory, _ = _load_canonical_object(path)
    _exact_keys(inventory, {"schema_version", "inventory_id", "files"}, "inventory")
    if inventory["schema_version"] != 1 or isinstance(inventory["schema_version"], bool):
        raise CertificationError("inventory schema_version must be integer 1")
    if inventory["inventory_id"] != "rm-0008-production-python":
        raise CertificationError("inventory_id mismatch")
    files = inventory["files"]
    if not isinstance(files, list):
        raise CertificationError("inventory files must be a list")
    observed: list[tuple[str, str]] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise CertificationError(f"inventory entry {index} must be an object")
        _exact_keys(entry, {"path", "classification"}, f"inventory entry {index}")
        path_text = _text(entry["path"], "inventory path")
        classification = _text(entry["classification"], "classification", ascii_only=True)
        if classification not in {"productive", "test", "documentation"}:
            raise CertificationError("unknown production inventory classification")
        if (
            "\\" in path_text
            or path_text.startswith("/")
            or not path_text.endswith(".py")
            or "//" in path_text
            or any(part in {"", ".", ".."} for part in PurePosixPath(path_text).parts)
        ):
            raise CertificationError(f"non-canonical inventory path: {path_text}")
        expected_class = (
            "test"
            if path_text == "conftest.py" or path_text.startswith("tests/")
            else "documentation"
            if path_text.startswith("docs/")
            else "productive"
        )
        if classification != expected_class:
            raise CertificationError(f"wrong classification for {path_text}")
        if enforce_filesystem and (REPO_ROOT / path_text).is_symlink():
            raise CertificationError(f"Python inventory contains a symbolic link: {path_text}")
        observed.append((path_text, classification))
    if observed != sorted(set(observed), key=lambda item: item[0].encode("utf-8")):
        raise CertificationError("production inventory is not byte-sorted and unique")
    try:
        tracked_raw = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.py"],
            cwd=REPO_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CertificationError("cannot obtain the Git Python inventory") from exc
    tracked = sorted(
        (item.decode("utf-8") for item in tracked_raw.split(b"\0") if item),
        key=lambda item: item.encode("utf-8"),
    )
    if [path_text for path_text, _ in observed] != tracked:
        raise CertificationError("production inventory differs from git ls-files")
    return inventory


def collect_a_node_ids() -> list[str]:
    command = [
        sys.executable,
        str(A_PORTABLE_ROOT / "collect_node_ids_v1.py"),
        str(A_PORTABLE_ROOT.relative_to(REPO_ROOT)),
        str(A_WINDOWS_ROOT.relative_to(REPO_ROOT)),
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise CertificationError(
            "A-only pytest collection failed: " + result.stderr.strip()
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CertificationError("collector did not emit canonical node-id JSON") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CertificationError("collector emitted an invalid node-id list")
    expected = sorted(set(value), key=lambda item: item.encode("utf-8"))
    if value != expected:
        raise CertificationError("collector output is not byte-sorted and unique")
    return value


def validate_collection(manifest: Mapping[str, Any]) -> list[str]:
    collected = collect_a_node_ids()
    expected = sorted(
        {cell["node_id"] for cell in manifest["cells"]},
        key=lambda item: item.encode("utf-8"),
    )
    if collected != expected:
        missing = sorted(set(expected) - set(collected))
        extra = sorted(set(collected) - set(expected))
        raise CertificationError(
            f"A-only collection mismatch; missing={missing!r}, extra={extra!r}"
        )
    return collected


def _test_python_files() -> Iterable[Path]:
    for root in (A_PORTABLE_ROOT, A_WINDOWS_ROOT):
        if not root.exists():
            continue
        yield from sorted(root.rglob("test_*.py"))
        yield from sorted(root.rglob("conftest.py"))


def validate_no_skip_xfail() -> None:
    forbidden = {"skip", "skipif", "xfail", "xpass"}
    capability_roots = {"platform", "sys", "os", "importlib"}
    for path in _test_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise CertificationError(f"cannot parse A test: {path}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _terminal_name(node.func)
                if name in forbidden:
                    raise CertificationError(f"forbidden pytest outcome control in {path}")
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                raise CertificationError(f"forbidden pytest marker in {path}")
            if isinstance(node, (ast.If, ast.IfExp)):
                roots = {
                    child.id
                    for child in ast.walk(node.test)
                    if isinstance(child, ast.Name)
                }
                if roots & capability_roots:
                    raise CertificationError(f"capability-based A selection in {path}")


def _terminal_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def validate_productive_mutation_graph(
    inventory: Mapping[str, Any] | None = None,
    *,
    _source_mutant: Mapping[str, str] | None = None,
) -> None:
    if _source_mutant is None and inventory is None:
        inventory = validate_production_inventory(enforce_filesystem=False)
    sources: dict[str, tuple[str, ast.Module]] = {}
    module_paths: dict[str, str] = {}
    if _source_mutant is None:
        assert inventory is not None
        source_items = (
            (entry["path"], (REPO_ROOT / entry["path"]).read_text(encoding="utf-8"))
            for entry in inventory["files"]
            if entry["classification"] == "productive"
        )
    else:
        source_items = iter(_source_mutant.items())
    for path_text, source_text in source_items:
        module = _module_name_from_path(path_text)
        if module in sources:
            raise CertificationError(f"duplicate productive module name: {module}")
        try:
            tree = ast.parse(source_text, filename=path_text)
        except (UnicodeError, SyntaxError) as exc:
            raise CertificationError(f"cannot parse productive Python: {path_text}") from exc
        sources[module] = (path_text, tree)
        module_paths[module] = path_text

    known_modules = set(sources)
    definitions: dict[str, ast.AST] = {}
    simple_definitions: dict[str, dict[str, str]] = defaultdict(dict)
    class_methods: dict[str, dict[tuple[str, str], str]] = defaultdict(dict)
    for module, (_, tree) in sources.items():
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = f"{module}::{node.name}"
                definitions[symbol] = node
                simple_definitions[module][node.name] = symbol
            elif isinstance(node, ast.ClassDef):
                class_symbol = f"{module}::{node.name}"
                definitions[class_symbol] = node
                simple_definitions[module][node.name] = class_symbol
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbol = f"{module}::{node.name}.{member.name}"
                        definitions[symbol] = member
                        class_methods[module][(node.name, member.name)] = symbol

    imports: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for module, (_, tree) in sources.items():
        for node in tree.body:
            if isinstance(node, ast.Import):
                for imported in node.names:
                    local = imported.asname or imported.name.split(".", 1)[0]
                    target = imported.name if imported.asname else local
                    target = _normalize_productive_module(target, known_modules)
                    imports[module][local] = ("module", target)
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_import_base(module, node.module, node.level)
                base = _normalize_productive_module(base, known_modules)
                for imported in node.names:
                    local = imported.asname or imported.name
                    possible_module = f"{base}.{imported.name}" if base else imported.name
                    if possible_module in known_modules:
                        imports[module][local] = ("module", possible_module)
                    else:
                        imports[module][local] = ("symbol", f"{base}::{imported.name}")

    aliases: dict[str, dict[str, tuple[str, str]]] = {
        module: dict(values) for module, values in imports.items()
    }
    changed = True
    while changed:
        changed = False
        for module, (_, tree) in sources.items():
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                if value is None:
                    continue
                resolved = _resolve_reference(
                    value,
                    module,
                    None,
                    aliases,
                    simple_definitions,
                    class_methods,
                )
                if resolved is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in aliases.setdefault(
                        module, {}
                    ):
                        aliases[module][target.id] = resolved
                        changed = True

    calls: dict[str, set[str]] = defaultdict(set)
    call_sites: dict[tuple[str, str], list[str]] = defaultdict(list)
    token_use_sites: dict[str, list[str]] = defaultdict(list)
    token_use_contexts: dict[str, list[str]] = defaultdict(list)
    return_shapes: dict[str, list[tuple[str, str] | None]] = defaultdict(list)
    direct_alias_exports: list[str] = []
    for module, (path_text, tree) in sources.items():
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        owners: list[tuple[str, ast.AST, str | None]] = [
            (f"{module}::<module>", tree, None)
        ]
        for symbol, node in definitions.items():
            if not symbol.startswith(module + "::") or isinstance(node, ast.ClassDef):
                continue
            class_name = symbol.split("::", 1)[1].split(".", 1)[0] if "." in symbol.split("::", 1)[1] else None
            owners.append((symbol, node, class_name))
        for owner, owner_node, class_name in owners:
            body_nodes = list(_owned_ast_nodes(owner_node))
            owner_aliases = {key: dict(value) for key, value in aliases.items()}
            for node in body_nodes:
                if isinstance(node, ast.Import):
                    for imported in node.names:
                        local = imported.asname or imported.name.split(".", 1)[0]
                        target = imported.name if imported.asname else local
                        owner_aliases.setdefault(module, {})[local] = (
                            "module",
                            _normalize_productive_module(target, known_modules),
                        )
                elif isinstance(node, ast.ImportFrom):
                    base = _normalize_productive_module(
                        _resolve_import_base(module, node.module, node.level),
                        known_modules,
                    )
                    for imported in node.names:
                        local = imported.asname or imported.name
                        possible_module = (
                            f"{base}.{imported.name}" if base else imported.name
                        )
                        owner_aliases.setdefault(module, {})[local] = (
                            ("module", possible_module)
                            if possible_module in known_modules
                            else ("symbol", f"{base}::{imported.name}")
                        )
            changed = True
            while changed:
                changed = False
                for node in body_nodes:
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = node.value
                    if value is None:
                        continue
                    resolved = _resolve_reference(
                        value,
                        module,
                        class_name,
                        owner_aliases,
                        simple_definitions,
                        class_methods,
                    )
                    if resolved is None:
                        continue
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id not in owner_aliases.setdefault(
                            module, {}
                        ):
                            owner_aliases[module][target.id] = resolved
                            changed = True
            for node in body_nodes:
                if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(
                    getattr(node, "ctx", None), ast.Load
                ):
                    token_reference = _resolve_reference(
                        node,
                        module,
                        class_name,
                        owner_aliases,
                        simple_definitions,
                        class_methods,
                    )
                    if token_reference == (
                        "symbol",
                        "runtime.executor_birth_secure_fs::_SESSION_TOKEN",
                    ):
                        token_use_sites[owner].append(
                            f"{path_text}:{getattr(node, 'lineno', 0)}"
                        )
                        parent = parents.get(node)
                        context = "invalid"
                        if (
                            owner
                            == "runtime.executor_birth_secure_fs::_SecureRootSession.__init__"
                            and isinstance(parent, ast.Compare)
                            and any(
                                isinstance(operator, (ast.Is, ast.IsNot))
                                for operator in parent.ops
                            )
                        ):
                            context = "constructor-identity-comparison"
                        elif isinstance(parent, ast.Call) and node in parent.args:
                            called = _resolve_reference(
                                parent.func,
                                module,
                                class_name,
                                owner_aliases,
                                simple_definitions,
                                class_methods,
                            )
                            if called == (
                                "symbol",
                                "runtime.executor_birth_secure_fs::_SecureRootSession",
                            ):
                                context = "session-constructor-argument"
                        token_use_contexts[owner].append(context)
                if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)) and node.value is not None:
                    returned_node = node.value.func if isinstance(node.value, ast.Call) else node.value
                    returned = _resolve_reference(
                        returned_node,
                        module,
                        class_name,
                        owner_aliases,
                        simple_definitions,
                        class_methods,
                    )
                    return_shapes[owner].append(returned)
                    if (
                        not isinstance(node.value, ast.Call)
                        and returned is not None
                        and _is_sensitive_reference(returned)
                    ):
                        kind, target = returned
                        canonical_target = target if kind == "symbol" else f"@{kind}:{target}"
                        calls[owner].add(canonical_target)
                        call_sites[(owner, canonical_target)].append(
                            f"{path_text}:{getattr(node, 'lineno', 0)}:return"
                        )
                if not isinstance(node, ast.Call):
                    continue
                resolved = _resolve_reference(
                    node.func,
                    module,
                    class_name,
                    owner_aliases,
                    simple_definitions,
                    class_methods,
                )
                if resolved is None:
                    terminal = _terminal_name(node.func)
                    if terminal in _MUTATION_METHODS:
                        resolved = ("method", terminal)
                if resolved is None and _terminal_name(node.func) == "getattr" and len(node.args) >= 2:
                    attribute = node.args[1]
                    if isinstance(attribute, ast.Constant) and attribute.value in _SENSITIVE_TERMINALS:
                        resolved = ("dynamic", str(attribute.value))
                if resolved is None:
                    continue
                kind, target = resolved
                resolved_terminal = target.split("::", 1)[-1].rsplit(".", 1)[-1]
                if resolved_terminal in _MUTATION_METHODS:
                    kind, target = "method", resolved_terminal
                canonical_target = (
                    target if kind == "symbol" else f"@{kind}:{target}"
                )
                calls[owner].add(canonical_target)
                call_sites[(owner, canonical_target)].append(
                    f"{path_text}:{getattr(node, 'lineno', 0)}"
                )

        for name, resolved in aliases.get(module, {}).items():
            if not name.startswith("_") and _is_sensitive_reference(resolved):
                direct_alias_exports.append(f"{path_text}:{name}")

    descriptor_symbol = "runtime.executor_birth_secure_fs::_AuthenticatedRootDescriptor"
    adopt_symbol = "runtime.executor_birth_secure_fs::_adopt_authenticated_root"
    session_symbol = "runtime.executor_birth_secure_fs::_SecureRootSession"
    session_token_symbol = "runtime.executor_birth_secure_fs::_SESSION_TOKEN"
    legacy_wrapper_symbol = "runtime.executor_birth_secure_fs::_LegacyReadSession"
    entry_symbol = "install.birth_authority_provisioning::open_birth_provisioning_layout_v1"
    mutation_symbols = {
        f"runtime.executor_birth_secure_fs::_SecureRootSession.{name}"
        for name in _MUTATION_METHODS
    }
    sensitive_targets = {
        descriptor_symbol,
        adopt_symbol,
        session_symbol,
        session_token_symbol,
        entry_symbol,
        *mutation_symbols,
    }
    sensitive_targets.update(f"@method:{name}" for name in _MUTATION_METHODS)
    sensitive_targets.update(f"@dynamic:{name}" for name in _SENSITIVE_TERMINALS)

    if entry_symbol not in definitions:
        raise CertificationError("installer-only entry is absent from the productive graph")
    constructor_sites = _sites_reaching_exact_call(calls, call_sites, descriptor_symbol)
    adopter_sites = _sites_reaching_exact_call(calls, call_sites, adopt_symbol)
    session_sites = _sites_reaching_exact_call(calls, call_sites, session_symbol)
    expected_site_module = "install.birth_authority_provisioning::"
    if len(constructor_sites) != 1 or not constructor_sites[0][0].startswith(expected_site_module):
        raise CertificationError(
            f"descriptor must have one installer construction site: {constructor_sites!r}"
        )
    if len(adopter_sites) != 1 or not adopter_sites[0][0].startswith(expected_site_module):
        raise CertificationError(
            f"descriptor must have one installer adoption site: {adopter_sites!r}"
        )
    expected_session_owners = {
        "runtime.executor_birth_secure_fs::_adopt_authenticated_root",
        "runtime.executor_birth_secure_fs::_open_legacy_root_session",
    }
    if {owner for owner, _ in session_sites} != expected_session_owners or len(session_sites) != 2:
        raise CertificationError(
            f"secure session has an unauthorized factory: {session_sites!r}"
        )
    expected_token_owners = {
        "runtime.executor_birth_secure_fs::_SecureRootSession.__init__",
        *expected_session_owners,
    }
    if set(token_use_sites) != expected_token_owners:
        raise CertificationError(
            f"session token escaped its constructor and two factories: {dict(token_use_sites)!r}"
        )
    if token_use_contexts.get(
        "runtime.executor_birth_secure_fs::_SecureRootSession.__init__"
    ) != ["constructor-identity-comparison"]:
        raise CertificationError("session token constructor check is not one identity comparison")
    for factory in expected_session_owners:
        contexts = token_use_contexts.get(factory, [])
        if contexts != ["session-constructor-argument"]:
            raise CertificationError(f"session token has an invalid context in {factory}: {contexts!r}")
    legacy_owner = "runtime.executor_birth_secure_fs::_open_legacy_root_session"
    if return_shapes.get(legacy_owner) != [("symbol", legacy_wrapper_symbol)]:
        raise CertificationError(
            "historical root factory must return exactly one _LegacyReadSession wrapper"
        )

    reachable_cache: dict[str, set[str]] = {}

    def reachable(symbol: str, visiting: set[str] | None = None) -> set[str]:
        if symbol in reachable_cache:
            return reachable_cache[symbol]
        active = set() if visiting is None else set(visiting)
        if symbol in active:
            return set()
        active.add(symbol)
        result = set(calls.get(symbol, ()))
        for target in tuple(result):
            if target in definitions:
                result.update(reachable(target, active))
        reachable_cache[symbol] = result
        return result

    entry_reachable = reachable(entry_symbol)
    if descriptor_symbol not in entry_reachable or adopt_symbol not in entry_reachable:
        raise CertificationError("installer entry does not own construction and adoption transitively")

    entry_definition_reachable: set[str] = set()
    pending = [entry_symbol]
    while pending:
        current = pending.pop()
        for target in calls.get(current, ()):
            if target in definitions and target not in entry_definition_reachable:
                entry_definition_reachable.add(target)
                pending.append(target)

    violations = list(direct_alias_exports)
    for owner in calls:
        reached = reachable(owner) & sensitive_targets
        if not reached:
            continue
        owner_module, owner_name = owner.split("::", 1)
        if owner_module == "runtime.executor_birth_secure_fs":
            continue
        if owner_module == "install.birth_authority_provisioning" and (
            owner == entry_symbol or owner_name.startswith("_")
        ):
            if owner == entry_symbol or owner in entry_definition_reachable:
                continue
        sites = sorted(
            site
            for (site_owner, _), values in call_sites.items()
            if site_owner == owner
            for site in values
        )
        violations.append(
            f"{owner} reaches {sorted(reached)!r} via {sites!r}"
        )
    if violations:
        raise CertificationError(
            "productive mutation capability escaped the single installer entry: "
            + "; ".join(sorted(violations))
        )


_MUTATION_METHODS = {
    "create_file_exclusive",
    "create_directory_exclusive",
    "rename_no_replace",
    "dispose_transaction_object",
}
_SENSITIVE_TERMINALS = {
    "_AuthenticatedRootDescriptor",
    "_adopt_authenticated_root",
    "_SecureRootSession",
    "_SESSION_TOKEN",
    "open_birth_provisioning_layout_v1",
    *_MUTATION_METHODS,
}


def _module_name_from_path(path_text: str) -> str:
    parts = list(PurePosixPath(path_text).parts)
    filename = parts.pop()
    if filename != "__init__.py":
        parts.append(filename[:-3])
    return ".".join(parts)


def _normalize_productive_module(target: str, known_modules: set[str]) -> str:
    if target in known_modules:
        return target
    runtime_target = f"runtime.{target}" if target else target
    if runtime_target in known_modules:
        return runtime_target
    return target


def _resolve_import_base(module: str, imported: str | None, level: int) -> str:
    if level == 0:
        return imported or ""
    package = module.rsplit(".", 1)[0]
    parts = package.split(".") if package else []
    if level > len(parts) + 1:
        return imported or ""
    prefix = parts[: len(parts) - level + 1]
    if imported:
        prefix.extend(imported.split("."))
    return ".".join(prefix)


def _resolve_reference(
    node: ast.AST,
    module: str,
    class_name: str | None,
    aliases: Mapping[str, Mapping[str, tuple[str, str]]],
    simple_definitions: Mapping[str, Mapping[str, str]],
    class_methods: Mapping[str, Mapping[tuple[str, str], str]],
) -> tuple[str, str] | None:
    if isinstance(node, ast.Name):
        if (
            node.id == "_SESSION_TOKEN"
            and module == "runtime.executor_birth_secure_fs"
        ):
            return (
                "symbol",
                "runtime.executor_birth_secure_fs::_SESSION_TOKEN",
            )
        if node.id in aliases.get(module, {}):
            return aliases[module][node.id]
        if node.id in simple_definitions.get(module, {}):
            return ("symbol", simple_definitions[module][node.id])
        return None
    if not isinstance(node, ast.Attribute):
        return None
    chain: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        chain.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    root = current.id
    chain.reverse()
    if root == "self" and class_name is not None and len(chain) == 1:
        method = class_methods.get(module, {}).get((class_name, chain[0]))
        return ("symbol", method) if method else None
    imported = aliases.get(module, {}).get(root)
    if imported is not None:
        kind, target = imported
        if kind == "module":
            if not chain:
                return imported
            return ("symbol", f"{target}::{'.'.join(chain)}")
        if chain:
            return ("symbol", f"{target}.{'.'.join(chain)}")
        return imported
    if root in simple_definitions.get(module, {}) and len(chain) == 1:
        method = class_methods.get(module, {}).get((root, chain[0]))
        return ("symbol", method) if method else None
    if chain and chain[-1] in _MUTATION_METHODS:
        return ("method", chain[-1])
    return None


def _owned_ast_nodes(owner: ast.AST) -> Iterable[ast.AST]:
    stack = list(ast.iter_child_nodes(owner))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _is_sensitive_reference(reference: tuple[str, str]) -> bool:
    kind, target = reference
    if kind in {"method", "dynamic"} and target in _SENSITIVE_TERMINALS:
        return True
    if kind != "symbol":
        return False
    return target in {
        "runtime.executor_birth_secure_fs::_AuthenticatedRootDescriptor",
        "runtime.executor_birth_secure_fs::_adopt_authenticated_root",
        "runtime.executor_birth_secure_fs::_SecureRootSession",
        "runtime.executor_birth_secure_fs::_SESSION_TOKEN",
        "install.birth_authority_provisioning::open_birth_provisioning_layout_v1",
    } or target.rsplit(".", 1)[-1] in _MUTATION_METHODS


def _sites_reaching_exact_call(
    calls: Mapping[str, set[str]],
    sites: Mapping[tuple[str, str], list[str]],
    target: str,
) -> list[tuple[str, str]]:
    return sorted(
        (owner, site)
        for owner, targets in calls.items()
        if target in targets
        for site in sites.get((owner, target), ())
    )


def validate_final_evidence(
    evidence: Mapping[str, Any], manifest: Mapping[str, Any], activity: str
) -> None:
    _exact_keys(evidence, FINAL_EVIDENCE_FIELDS, "final evidence")
    _validate_evidence_header(evidence, activity)
    results = evidence["results"]
    if not isinstance(results, list):
        raise CertificationError("evidence results must be a list")
    expected_cells = select_cells(manifest, activity)
    expected = [cell["node_id"] for cell in expected_cells]
    observed: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            raise CertificationError("evidence result must be an object")
        _exact_keys(result, {"node_id", "outcome"}, "final evidence result")
        if result["outcome"] != "passed":
            raise CertificationError("final evidence contains a non-pass outcome")
        observed.append(_text(result["node_id"], "evidence node_id", ascii_only=True))
    if observed != expected:
        raise CertificationError("final evidence result order differs from activity cells")


def validate_snapshot_activity_evidence(
    evidence: Mapping[str, Any], manifest: Mapping[str, Any], activity: str
) -> None:
    _exact_keys(evidence, FINAL_EVIDENCE_FIELDS, "snapshot activity evidence")
    _validate_evidence_header(evidence, activity)
    expected_cells = select_cells(manifest, activity)
    results = evidence["results"]
    if not isinstance(results, list):
        raise CertificationError("snapshot results must be a list")
    observed: list[tuple[str, str, str]] = []
    for result in results:
        if not isinstance(result, dict):
            raise CertificationError("snapshot result must be an object")
        _exact_keys(
            result,
            {"node_id", "declared_disposition", "observed_outcome"},
            "snapshot result",
        )
        outcome = result["observed_outcome"]
        if outcome not in {"passed", "failed"}:
            raise CertificationError("snapshot contains a non-call outcome")
        observed.append(
            (result["node_id"], result["declared_disposition"], outcome)
        )
    expected = [
        (cell["node_id"], cell["pre_fix_disposition"])
        for cell in expected_cells
    ]
    if [(node, disposition) for node, disposition, _ in observed] != expected:
        raise CertificationError("snapshot result order or disposition mismatch")
    for node_id, disposition, outcome in observed:
        if disposition in {"red", "absent"} and outcome != "failed":
            raise CertificationError(f"pre-fix cell unexpectedly passed: {node_id}")


def _validate_evidence_header(evidence: Mapping[str, Any], activity: str) -> None:
    if evidence["schema_version"] != 1 or isinstance(evidence["schema_version"], bool):
        raise CertificationError("evidence schema_version must be integer 1")
    if evidence["suite_id"] != SUITE_ID or evidence["activity"] != activity:
        raise CertificationError("evidence suite or activity mismatch")
    if not isinstance(evidence["git_sha"], str) or SHA_RE.fullmatch(evidence["git_sha"]) is None:
        raise CertificationError("evidence git_sha is not canonical")
    for field in ("manifest_sha256", "production_inventory_sha256"):
        if not isinstance(evidence[field], str) or DIGEST_RE.fullmatch(evidence[field]) is None:
            raise CertificationError(f"evidence {field} is not canonical")
    _text(evidence["runner_image"], "runner_image")


def validate_snapshot_aggregate(
    path: Path = SNAPSHOT_PATH, manifest: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if manifest is None:
        manifest = validate_manifest()
    evidence, _ = _load_canonical_object(path)
    _exact_keys(
        evidence,
        {
            "schema_version",
            "suite_id",
            "source_git_sha",
            "manifest_sha256",
            "production_inventory_sha256",
            "activities",
            "results",
        },
        "pre-fix aggregate",
    )
    if evidence["schema_version"] != 1 or evidence["suite_id"] != SUITE_ID:
        raise CertificationError("pre-fix aggregate header mismatch")
    if not isinstance(evidence["source_git_sha"], str) or SHA_RE.fullmatch(evidence["source_git_sha"]) is None:
        raise CertificationError("pre-fix source_git_sha is invalid")
    if evidence["manifest_sha256"] != digest_file(MANIFEST_PATH):
        raise CertificationError("pre-fix manifest digest differs from current manifest")
    if not isinstance(evidence["production_inventory_sha256"], str) or DIGEST_RE.fullmatch(
        evidence["production_inventory_sha256"]
    ) is None:
        raise CertificationError("pre-fix production inventory digest is invalid")
    activities = evidence["activities"]
    expected_activities = [
        {"activity": activity, "runner_image": next(
            item["runner_image"] for item in activities if isinstance(item, dict) and item.get("activity") == activity
        )}
        for activity in ACTIVITIES
    ] if isinstance(activities, list) and all(
        any(isinstance(item, dict) and item.get("activity") == activity for item in activities)
        for activity in ACTIVITIES
    ) else []
    if activities != sorted(expected_activities, key=lambda item: item["activity"].encode("utf-8")):
        raise CertificationError("pre-fix activities are not exact, ordered, and distinct")
    for item in activities:
        _exact_keys(item, {"activity", "runner_image"}, "pre-fix activity")
        _text(item["runner_image"], "runner_image")
    expected_results = [
        {
            "activity": cell["activity"],
            "node_id": cell["node_id"],
            "declared_disposition": cell["pre_fix_disposition"],
        }
        for cell in manifest["cells"]
    ]
    results = evidence["results"]
    if not isinstance(results, list) or len(results) != 248:
        raise CertificationError("pre-fix aggregate must contain 248 results")
    for result, expected in zip(results, expected_results, strict=True):
        if not isinstance(result, dict):
            raise CertificationError("pre-fix aggregate result must be an object")
        _exact_keys(
            result,
            {"activity", "node_id", "declared_disposition", "observed_outcome"},
            "pre-fix aggregate result",
        )
        if any(result[field] != expected[field] for field in expected):
            raise CertificationError("pre-fix aggregate result order mismatch")
        outcome = result["observed_outcome"]
        if outcome not in {"passed", "failed"}:
            raise CertificationError("pre-fix aggregate has an invalid outcome")
        if expected["declared_disposition"] in {"red", "absent"} and outcome != "failed":
            raise CertificationError("pre-fix aggregate contradicts required disposition")
    return evidence


def validate_workflow_structure(path: Path = WORKFLOW_PATH) -> None:
    text = path.read_text(encoding="utf-8")
    activity_jobs = {
        "rm0008-a-manifest": ("manifest", "ubuntu-24.04"),
        "rm0008-a-portable-ubuntu": ("portable-ubuntu", "ubuntu-24.04"),
        "rm0008-a-portable-windows": ("portable-windows", "windows-2022"),
        "rm0008-a-concurrency-ubuntu": ("concurrency-ubuntu", "ubuntu-24.04"),
        "rm0008-a-concurrency-windows": ("concurrency-windows", "windows-2022"),
        "rm0008-a-windows-acl": ("windows-acl", "windows-2022"),
    }
    expected_jobs = {"portable-contract-store", *activity_jobs, "rm0008-a-summary"}
    jobs = _workflow_job_blocks(text)
    if set(jobs) != expected_jobs:
        raise CertificationError(
            f"workflow jobs differ: expected={sorted(expected_jobs)!r}, got={sorted(jobs)!r}"
        )
    input_pattern = (
        r"^      {name}:\n"
        r"(?:        [^\n]+\n)*?"
        r"        required: true\n"
        r"        default: false\n"
        r"        type: boolean$"
    )
    for input_name in ("rm0008_snapshot_pre_fix", "rm0008_run_diagnostic"):
        if re.search(
            input_pattern.format(name=re.escape(input_name)),
            text,
            re.MULTILINE,
        ) is None:
            raise CertificationError(f"workflow input is not closed boolean false: {input_name}")
    exact_mode = (
        "RM0008_A_MODE: ${{ github.event_name == 'workflow_dispatch' && "
        "inputs.rm0008_snapshot_pre_fix && 'snapshot' || 'final' }}"
    )
    if text.count(exact_mode) != 1:
        raise CertificationError("workflow A mode expression is not exact")

    historic = jobs["portable-contract-store"]
    if text.count("--ignore=tests/portable/rm0008_2a_acceptance") != 1:
        raise CertificationError("historical suite must contain the one literal A-tree exclusion")
    if "--ignore=tests/portable/rm0008_2a_acceptance" not in historic:
        raise CertificationError("A-tree exclusion is outside the historical job")
    if historic.count("--import-mode=importlib") != 3:
        raise CertificationError("every historical pytest command must use importlib mode")
    if not all(os_name in historic for os_name in ("ubuntu-24.04", "windows-2022")):
        raise CertificationError("historical matrix no longer owns both certified runners")
    diagnostic_condition = (
        "if: runner.os == 'Windows' && github.event_name == 'workflow_dispatch' "
        "&& inputs.rm0008_run_diagnostic"
    )
    if historic.count(diagnostic_condition) != 1 or historic.count(
        "python tests/windows_identity/rm0008_increment_2a_windows_diagnostics.py"
    ) != 1:
        raise CertificationError("D diagnostic is not one explicit manual historical step")

    for job_id, (activity, runner) in activity_jobs.items():
        block = jobs[job_id]
        if re.search(rf"^    runs-on: {re.escape(runner)}$", block, re.MULTILINE) is None:
            raise CertificationError(f"{job_id} uses the wrong runner")
        command = "python tests/portable/rm0008_2a_acceptance/run_activity_v1.py"
        if block.count(command) != 1:
            raise CertificationError(f"{job_id} must invoke the A runner exactly once")
        if block.count(f"--activity {activity}") != 1:
            raise CertificationError(f"{job_id} selects the wrong activity")
        if block.count("--import-mode=importlib") != 1:
            raise CertificationError(f"{job_id} does not fix pytest importlib mode")
        expected_mode = (
            '--mode "$env:RM0008_A_MODE"'
            if runner == "windows-2022"
            else '--mode "$RM0008_A_MODE"'
        )
        if block.count(expected_mode) != 1:
            raise CertificationError(f"{job_id} does not use the closed snapshot/final mode")
        evidence_name = f"rm0008-2a-evidence-{activity}"
        if block.count(f"--evidence {evidence_name}.json") != 1:
            raise CertificationError(f"{job_id} writes the wrong evidence file")
        if block.count(f"name: {evidence_name}") != 1 or block.count(
            f"path: {evidence_name}.json"
        ) != 1:
            raise CertificationError(f"{job_id} does not upload its exact evidence")
        if block.count(
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
        ) != 1:
            raise CertificationError(f"{job_id} evidence upload is absent or unpinned")
        if "python -m pytest" in block or "rm0008_increment_2a_windows_diagnostics" in block:
            raise CertificationError(f"{job_id} bypasses its manifest selection or runs D")

    summary = jobs["rm0008-a-summary"]
    expected_dependencies = ["portable-contract-store", *activity_jobs]
    needs_match = re.search(
        r"^    needs:\n((?:      - [A-Za-z0-9_-]+\n)+)",
        summary,
        re.MULTILINE,
    )
    if needs_match is None:
        raise CertificationError("summary needs list is absent")
    observed_dependencies = [
        line.strip()[2:] for line in needs_match.group(1).splitlines()
    ]
    if observed_dependencies != expected_dependencies:
        raise CertificationError(
            f"summary dependencies differ: {observed_dependencies!r}"
        )
    if "if: always()" not in summary:
        raise CertificationError("summary must run with if: always()")
    for dependency in expected_dependencies:
        if summary.count(f"needs.{dependency}.result") != 1:
            raise CertificationError(f"summary does not test result of {dependency}")
    if summary.count(
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
    ) != 1:
        raise CertificationError("summary evidence download is absent or unpinned")
    aggregate_command = (
        "python tests/portable/rm0008_2a_acceptance/aggregate_evidence_v1.py"
    )
    if summary.count(aggregate_command) != 2:
        raise CertificationError("summary must have exact final and snapshot aggregation commands")
    if "--mode final --evidence-dir rm0008-2a-evidence" not in summary:
        raise CertificationError("summary lacks final evidence validation")
    snapshot_fragment = (
        "--mode snapshot --evidence-dir rm0008-2a-evidence --output "
        "tests/portable/rm0008-2a-pre-fix-evidence-v1.json"
    )
    if snapshot_fragment not in summary:
        raise CertificationError("summary lacks canonical pre-fix snapshot aggregation")
    if "inputs.rm0008_run_diagnostic" in summary or "rm0008_increment_2a_windows_diagnostics" in summary:
        raise CertificationError("summary or A jobs inherit the D diagnostic")


def _workflow_job_blocks(text: str) -> dict[str, str]:
    marker = "\njobs:\n"
    if marker not in text:
        raise CertificationError("workflow jobs mapping is absent")
    jobs_text = text.split(marker, 1)[1]
    matches = list(
        re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", jobs_text, re.MULTILINE)
    )
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(jobs_text)
        blocks[match.group(1)] = jobs_text[start:end]
    return blocks


def git_sha() -> str:
    try:
        value = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CertificationError("cannot resolve the public Git SHA") from exc
    if SHA_RE.fullmatch(value) is None:
        raise CertificationError("git rev-parse returned a non-canonical SHA")
    return value
