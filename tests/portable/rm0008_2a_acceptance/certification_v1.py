"""Canonical manifest, inventory, evidence, and workflow checks for RM-0008 2A."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import stat
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

try:
    from . import required_cells_v1 as _required_cells
except ImportError:  # Direct execution by the workflow-owned scripts.
    import required_cells_v1 as _required_cells

if Path(_required_cells.__file__).resolve() != Path(__file__).resolve().with_name(
    "required_cells_v1.py"
):
    raise RuntimeError("required_cells_v1 did not resolve beside certification_v1")
EXPECTED_ACTIVITY_COUNTS_V1 = _required_cells.EXPECTED_ACTIVITY_COUNTS_V1
REQUIRED_CELLS_V1 = _required_cells.REQUIRED_CELLS_V1


SUITE_ID = "rm-0008-increment-2a"
REPO_ROOT = Path(__file__).resolve().parents[3]
A_PORTABLE_ROOT = REPO_ROOT / "tests/portable/rm0008_2a_acceptance"
A_WINDOWS_ROOT = REPO_ROOT / "tests/windows_identity/rm0008_2a_acceptance"
MANIFEST_PATH = REPO_ROOT / "tests/portable/rm0008-2a-acceptance-manifest-v1.json"
INVENTORY_PATH = A_PORTABLE_ROOT / "production-python-inventory-v1.json"
SNAPSHOT_PATH = REPO_ROOT / "tests/portable/rm0008-2a-pre-fix-evidence-v1.json"
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/portable-contract-store.yml"
PYTEST_CONFIG_PATH = A_PORTABLE_ROOT / "pytest-certification.ini"

_EXACT_PYTEST_CONFIG = b"[pytest]\naddopts =\n"
_FROZEN_TREE_PREFIXES = (
    "tests/portable/rm0008_2a_acceptance",
    "tests/windows_identity/rm0008_2a_acceptance",
)
_FROZEN_EXACT_PATHS = (
    ".github/workflows/portable-contract-store.yml",
    "pytest.ini",
    "tests/portable/conftest.py",
    "tests/portable/requirements.txt",
    "tests/portable/rm0008-2a-acceptance-manifest-v1.json",
    "tests/windows_identity/conftest.py",
)
_FROZEN_TREE_EXCLUSIONS = {
    "tests/portable/rm0008_2a_acceptance/production-python-inventory-v1.json",
}

_FROZEN_WORKFLOW_SHA256 = (
    "3e953be12480be9a4e6dfa19812a053492b5e26e155c9ecb7b749c29bde135e9"
)
_EFFECTIVE_PYTEST_SUPPORT_SHA256 = {
    "conftest.py": "c31a567f781dcbd3e1ce06c67c901a1b3be07c21a5d8c4030cc8bf262a753015",
    "tests/portable/conftest.py": (
        "c4026c2a26baadf7e4a294d747abc97417ce50254d0319dcac0754fba8370fd9"
    ),
    "tests/runtime/conftest.py": (
        "6c3c097efa2cf52334cb4fc40945c1b1d9c91bf7f774a768958809bd8c9086ab"
    ),
    "tests/windows_identity/conftest.py": (
        "856572740b3f2246296ba064168da30894092e690ab5c65d1b0ea159029768b3"
    ),
}

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


def validate_pytest_boundary_configuration(
    path: Path = PYTEST_CONFIG_PATH,
) -> None:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CertificationError("cannot read the dedicated pytest configuration") from exc
    if raw != _EXACT_PYTEST_CONFIG:
        raise CertificationError("dedicated pytest configuration is not exact")


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
    if len(normalized) != len(REQUIRED_CELLS_V1) or (
        observed_required != expected_required
    ):
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


def tracked_python_index_paths() -> list[str]:
    """Return regular stage-0 Python paths from the authoritative Git index."""
    try:
        raw = subprocess.run(
            ["git", "ls-files", "--cached", "--stage", "-z", "--", "*.py"],
            cwd=REPO_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CertificationError("cannot obtain the staged Git Python inventory") from exc
    paths: list[str] = []
    for raw_record in (item for item in raw.split(b"\0") if item):
        header, separator, raw_path = raw_record.partition(b"\t")
        fields = header.split()
        if separator != b"\t" or len(fields) != 3:
            raise CertificationError("Git emitted a malformed staged Python record")
        mode, _object_id, stage = fields
        if stage != b"0":
            raise CertificationError("Python inventory contains an unmerged Git stage")
        if mode not in {b"100644", b"100755"}:
            path_hint = raw_path.decode("utf-8", errors="replace")
            raise CertificationError(
                f"Python inventory contains non-regular Git mode {mode.decode()}: "
                f"{path_hint}"
            )
        try:
            paths.append(raw_path.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise CertificationError("Git Python path is not UTF-8") from exc
    if len(paths) != len(set(paths)):
        raise CertificationError("staged Git Python inventory contains duplicates")
    return sorted(paths, key=lambda item: item.encode("utf-8"))


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
            # A file the public projection never ships is apparatus, not
            # product: the productive graph must mean what the exporter
            # actually installs (scripts/export-public.sh).
            "test"
            if path_text == "conftest.py"
            or path_text.startswith(("tests/", "internal/", "runtime/testing/"))
            else "documentation"
            if path_text.startswith("docs/")
            else "productive"
        )
        if classification != expected_class:
            raise CertificationError(f"wrong classification for {path_text}")
        if enforce_filesystem:
            candidate = REPO_ROOT / path_text
            try:
                mode = candidate.lstat().st_mode
            except OSError as exc:
                raise CertificationError(
                    f"Python inventory path is absent: {path_text}"
                ) from exc
            if not stat.S_ISREG(mode):
                raise CertificationError(
                    f"Python inventory path is not a regular file: {path_text}"
                )
        observed.append((path_text, classification))
    if observed != sorted(set(observed), key=lambda item: item[0].encode("utf-8")):
        raise CertificationError("production inventory is not byte-sorted and unique")
    tracked = tracked_python_index_paths()
    if [path_text for path_text, _ in observed] != tracked:
        raise CertificationError("production inventory differs from git ls-files")
    return inventory


def validate_clean_tracked_worktree() -> None:
    for command in (
        ["git", "diff-index", "--cached", "--quiet", "HEAD", "--"],
        ["git", "diff-files", "--quiet", "--"],
    ):
        try:
            result = subprocess.run(
                command,
                cwd=REPO_ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise CertificationError("cannot verify the tracked Git worktree") from exc
        if result.returncode != 0:
            raise CertificationError(
                "tracked worktree or index differs from the evidence Git SHA"
            )
    try:
        untracked_python = subprocess.run(
            [
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "*.py",
            ],
            cwd=REPO_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CertificationError("cannot verify untracked Python files") from exc
    if untracked_python:
        raise CertificationError("untracked Python files are outside the evidence SHA")


def collect_a_node_ids() -> list[str]:
    command = [
        sys.executable,
        "-P",
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
    paths: set[Path] = set()
    for root in (A_PORTABLE_ROOT, A_WINDOWS_ROOT):
        if not root.exists():
            continue
        paths.update(root.rglob("*.py"))
    paths.update(
        REPO_ROOT / relative
        for relative in _EFFECTIVE_PYTEST_SUPPORT_SHA256
        if (REPO_ROOT / relative).is_file()
    )
    yield from sorted(
        paths,
        key=lambda path: path.relative_to(REPO_ROOT).as_posix().encode("utf-8"),
    )


def _normalized_source_sha256(source: str) -> str:
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_effective_pytest_support_source(source: str, label: str) -> None:
    normalized_label = label.replace("\\", "/")
    repo_prefix = REPO_ROOT.as_posix().rstrip("/") + "/"
    if normalized_label.startswith(repo_prefix):
        normalized_label = normalized_label[len(repo_prefix) :]
    expected_digest = _EFFECTIVE_PYTEST_SUPPORT_SHA256.get(normalized_label)
    if (
        expected_digest is not None
        and _normalized_source_sha256(source) != expected_digest
    ):
        raise CertificationError(
            "effective pytest support differs from the frozen source: "
            f"{normalized_label}"
        )


def _test_aliases(tree: ast.AST) -> dict[str, str]:
    aliases = {
        "hasattr": "builtins.hasattr",
        "getattr": "builtins.getattr",
        "vars": "builtins.vars",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local = imported.asname or imported.name.split(".", 1)[0]
                aliases[local] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                if imported.name != "*":
                    aliases[imported.asname or imported.name] = (
                        f"{node.module}.{imported.name}"
                    )

    def dotted(value: ast.AST) -> str | None:
        if isinstance(value, ast.Name):
            return aliases.get(value.id, value.id)
        if isinstance(value, ast.Attribute):
            owner = dotted(value.value)
            return None if owner is None else f"{owner}.{value.attr}"
        if (
            isinstance(value, ast.Call)
            and dotted(value.func) == "builtins.getattr"
            and len(value.args) >= 2
        ):
            owner = dotted(value.args[0])
            name = _constant_text(value.args[1])
            return None if owner is None or name is None else f"{owner}.{name}"
        return None

    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for assignment in assignments:
            value = assignment.value
            targets: list[ast.AST]
            if isinstance(assignment, ast.Assign):
                targets = assignment.targets
            else:
                targets = [assignment.target]
            resolved = dotted(value)
            if resolved is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and aliases.get(target.id) != resolved:
                    aliases[target.id] = resolved
                    changed = True
        if not changed:
            break
    return aliases


def _resolved_test_name(node: ast.AST, aliases: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        owner = _resolved_test_name(node.value, aliases)
        return None if owner is None else f"{owner}.{node.attr}"
    return None


def _platform_scalar(node: ast.AST, aliases: Mapping[str, str]) -> str | None:
    resolved = _resolved_test_name(node, aliases)
    if resolved in {"os.name", "sys.platform"}:
        return resolved
    if (
        isinstance(node, ast.Call)
        and not node.args
        and not node.keywords
        and _resolved_test_name(node.func, aliases) == "platform.system"
    ):
        return "platform.system"
    return None


def _exact_platform_dispatch(node: ast.AST, aliases: Mapping[str, str]) -> bool:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return False
    if not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        return False
    left, right = node.left, node.comparators[0]
    scalar = _platform_scalar(left, aliases)
    literal = _constant_text(right)
    if scalar is None:
        scalar = _platform_scalar(right, aliases)
        literal = _constant_text(left)
    allowed = {
        "os.name": {"nt", "posix"},
        "sys.platform": {"win32", "linux", "darwin", "cygwin"},
        "platform.system": {"Windows", "Linux", "Darwin"},
    }
    return scalar is not None and literal in allowed[scalar]


def _direct_platform_composite(
    node: ast.AST,
    aliases: Mapping[str, str],
    product_names: set[str],
) -> bool:
    if _exact_platform_dispatch(node, aliases):
        return True
    if not isinstance(node, ast.BoolOp):
        return False
    has_dispatch = False
    for value in node.values:
        if _exact_platform_dispatch(value, aliases):
            has_dispatch = True
            continue
        if _contains_capability_probe(value, aliases, product_names):
            return False
    return has_dispatch


def _platform_dispatch_value(
    node: ast.AST, aliases: Mapping[str, str], platform_name: str
) -> bool:
    if not _exact_platform_dispatch(node, aliases):
        raise AssertionError("dispatch value requested for a non-exact selector")
    left, right = node.left, node.comparators[0]
    scalar = _platform_scalar(left, aliases)
    literal = _constant_text(right)
    if scalar is None:
        scalar = _platform_scalar(right, aliases)
        literal = _constant_text(left)
    actual = {
        "linux": {
            "os.name": "posix",
            "sys.platform": "linux",
            "platform.system": "Linux",
        },
        "windows": {
            "os.name": "nt",
            "sys.platform": "win32",
            "platform.system": "Windows",
        },
    }[platform_name][scalar]
    equal = actual == literal
    return equal if isinstance(node.ops[0], ast.Eq) else not equal


def _product_capability_names(
    tree: ast.AST, aliases: Mapping[str, str]
) -> set[str]:
    names = {
        local
        for local, resolved in aliases.items()
        if resolved.startswith("executor_birth_")
    }
    for assignment in ast.walk(tree):
        if not isinstance(assignment, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            continue
        value = assignment.value
        if not isinstance(value, ast.Call):
            continue
        called = _resolved_test_name(value.func, aliases) or ""
        product_factory = called.rsplit(".", 1)[-1] in {"product", "secure_fs"}
        dynamic_product = (
            called == "importlib.import_module"
            and bool(value.args)
            and (_constant_text(value.args[0]) or "").startswith(
                "executor_birth_"
            )
        )
        if not (product_factory or dynamic_product):
            continue
        targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
        names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def _contains_capability_probe(
    node: ast.AST,
    aliases: Mapping[str, str],
    product_names: set[str],
    tainted_names: set[int] | frozenset[int] = frozenset(),
    tainted_functions: set[str] | frozenset[str] = frozenset(),
) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and id(child) in tainted_names:
            return True
        resolved = _resolved_test_name(child, aliases)
        if resolved is not None and (
            resolved in {"os.name", "sys.platform", "platform.system"}
            or resolved.startswith("os.supports_")
            or resolved.startswith("importlib.")
        ):
            return True
        if isinstance(child, ast.Call):
            called = _resolved_test_name(child.func, aliases)
            if called is not None and called.rsplit(".", 1)[-1] in tainted_functions:
                return True
            if called in {"builtins.hasattr", "builtins.getattr"} and child.args:
                target = _resolved_test_name(child.args[0], aliases) or ""
                target_root = target.split(".", 1)[0]
                attribute = _constant_text(child.args[1]) if len(child.args) >= 2 else None
                if (
                    target_root in product_names
                    or target_root in {"os", "sys", "platform", "importlib"}
                    or target.startswith("executor_birth_")
                    or (attribute is not None and attribute.startswith("_"))
                ):
                    return True
            if called == "builtins.vars" and child.args:
                target = _resolved_test_name(child.args[0], aliases) or ""
                if (
                    target.split(".", 1)[0] in product_names
                    or target.startswith("executor_birth_")
                ):
                    return True
        if isinstance(child, ast.Attribute) and child.attr == "__dict__":
            target = _resolved_test_name(child.value, aliases) or ""
            if (
                target.split(".", 1)[0] in product_names
                or target.startswith("executor_birth_")
            ):
                return True
    return False


def _assignment_target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return _assignment_target_names(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        return {
            name
            for child in node.elts
            for name in _assignment_target_names(child)
        }
    return set()


def _contains_propagatable_capability_probe(
    node: ast.AST,
    aliases: Mapping[str, str],
    product_names: set[str],
    tainted_names: set[int],
    tainted_functions: set[str],
) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and (
            id(child) in tainted_names or child.id in tainted_functions
        ):
            return True
        resolved = _resolved_test_name(child, aliases)
        if resolved is not None and (
            resolved in {"os.name", "sys.platform", "platform.system"}
            or resolved.startswith("os.supports_")
        ):
            return True
        if isinstance(child, ast.Call):
            called = _resolved_test_name(child.func, aliases) or ""
            if called.rsplit(".", 1)[-1] in tainted_functions:
                return True
            if called in {"importlib.util.find_spec", "importlib.find_loader"}:
                return True
            if called in {"builtins.hasattr", "builtins.getattr"} and child.args:
                target = _resolved_test_name(child.args[0], aliases) or ""
                attribute = _constant_text(child.args[1]) if len(child.args) >= 2 else None
                if (
                    target.split(".", 1)[0] in product_names
                    or target.startswith("executor_birth_")
                    or (attribute is not None and attribute.startswith("_"))
                ):
                    return True
            if called == "builtins.vars" and child.args:
                target = _resolved_test_name(child.args[0], aliases) or ""
                if (
                    target.split(".", 1)[0] in product_names
                    or target.startswith("executor_birth_")
                ):
                    return True
        if isinstance(child, ast.Attribute) and child.attr == "__dict__":
            target = _resolved_test_name(child.value, aliases) or ""
            if (
                target.split(".", 1)[0] in product_names
                or target.startswith("executor_birth_")
            ):
                return True
    return False


def _capability_taint(
    tree: ast.AST,
    aliases: Mapping[str, str],
    product_names: set[str],
) -> tuple[set[int], set[str]]:
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def lexical_scope(node: ast.AST) -> ast.AST:
        current = parents.get(id(node))
        while current is not None:
            if isinstance(
                current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                return current
            current = parents.get(id(current))
        return tree

    names_by_scope: dict[tuple[int, str], set[int]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names_by_scope[(id(lexical_scope(node)), node.id)].add(id(node))

    assignments: list[tuple[int, set[str], ast.AST]] = []
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = {
                name
                for target in node.targets
                for name in _assignment_target_names(target)
            }
            assignments.append((id(lexical_scope(node)), targets, node.value))
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            if node.value is not None:
                assignments.append(
                    (
                        id(lexical_scope(node)),
                        _assignment_target_names(node.target),
                        node.value,
                    )
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node)

    tainted_names: set[int] = set()
    tainted_functions: set[str] = set()
    maximum_rounds = len(assignments) + len(functions) + 1
    for _ in range(maximum_rounds):
        previous = (frozenset(tainted_names), frozenset(tainted_functions))
        for scope_id, targets, value in assignments:
            if _contains_propagatable_capability_probe(
                value,
                aliases,
                product_names,
                tainted_names,
                tainted_functions,
            ):
                for target in targets:
                    tainted_names.update(names_by_scope[(scope_id, target)])
        for function in functions:
            if any(
                statement.value is not None
                and lexical_scope(statement) is function
                and _contains_propagatable_capability_probe(
                    statement.value,
                    aliases,
                    product_names,
                    tainted_names,
                    tainted_functions,
                )
                for statement in ast.walk(function)
                if isinstance(statement, ast.Return)
            ):
                tainted_functions.add(function.name)
        if previous == (frozenset(tainted_names), frozenset(tainted_functions)):
            break
    return tainted_names, tainted_functions


_EXPECTED_EVIDENCE_RECORDER_HOOK = ast.dump(
    ast.parse(
        """
class _EvidenceRecorder:
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if getattr(report, "wasxfail", None) is not None:
            self.expected_results.append(report.nodeid)
        if report.when == "call":
            self.calls.setdefault(report.nodeid, []).append(report.outcome)
        elif report.failed or report.skipped:
            self.non_call_failures.append(f"{report.nodeid}:{report.when}:{report.outcome}")
"""
    ).body[0].body[0],
    include_attributes=False,
)


def _validate_no_skip_xfail_source(
    source: str,
    label: str,
    owned_platforms: Mapping[str, set[str]] | None = None,
) -> None:
    forbidden = {"skip", "skipif", "xfail", "xpass"}
    try:
        tree = ast.parse(source, filename=label)
    except (UnicodeError, SyntaxError) as exc:
        raise CertificationError(f"cannot parse A test: {label}") from exc
    aliases = _test_aliases(tree)
    product_names = _product_capability_names(tree, aliases)
    tainted_names, tainted_functions = _capability_taint(
        tree, aliases, product_names
    )

    def capability_probe(node: ast.AST) -> bool:
        return _contains_capability_probe(
            node,
            aliases,
            product_names,
            tainted_names,
            tainted_functions,
        )

    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def owning_test(node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        current: ast.AST | None = node
        while current is not None:
            if (
                isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef))
                and current.name.startswith("test_")
            ):
                return current
            current = parents.get(id(current))
        return None

    def selected_platform_nodes(
        node: ast.AST, platform_name: str, *, root: bool = False
    ) -> Iterable[ast.AST]:
        if (
            not root
            and isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
            )
        ):
            return
        if isinstance(node, (ast.If, ast.IfExp)) and _exact_platform_dispatch(
            node.test, aliases
        ):
            selected = (
                node.body
                if _platform_dispatch_value(node.test, aliases, platform_name)
                else node.orelse
            )
            values = selected if isinstance(selected, list) else [selected]
            for value in values:
                yield from selected_platform_nodes(value, platform_name)
            return
        yield node
        for child in ast.iter_child_nodes(node):
            yield from selected_platform_nodes(child, platform_name)

    def owned_test_is_vacuous(
        owner: ast.FunctionDef | ast.AsyncFunctionDef, platform_name: str
    ) -> bool:
        selected = selected_platform_nodes(owner, platform_name, root=True)
        for candidate in selected:
            if isinstance(candidate, ast.Raise):
                return False
            if isinstance(candidate, ast.Assert) and not (
                isinstance(candidate.test, ast.Constant)
                and bool(candidate.test.value)
            ):
                return False
        return True

    checked_platform_owners: set[tuple[int, str]] = set()
    forbidden_pytest_hooks = {
        "pytest_collection_modifyitems",
        "pytest_collect_file",
        "pytest_ignore_collect",
        "pytest_make_collect_report",
        "pytest_pyfunc_call",
        "pytest_runtest_call",
        "pytest_runtest_logreport",
        "pytest_runtest_makereport",
        "pytest_runtest_protocol",
    }

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name in forbidden_pytest_hooks
        ):
            parent = parents.get(id(node))
            allowed_recorder_hook = (
                node.name == "pytest_runtest_logreport"
                and isinstance(parent, ast.ClassDef)
                and parent.name == "_EvidenceRecorder"
                and label.replace("\\", "/").endswith(
                    "/rm0008_2a_acceptance/run_activity_v1.py"
                )
                and ast.dump(node, include_attributes=False)
                == _EXPECTED_EVIDENCE_RECORDER_HOOK
            )
            if not allowed_recorder_hook:
                raise CertificationError(f"forbidden pytest control hook in {label}")
        if isinstance(node, ast.ImportFrom) and any(
            imported.name in forbidden for imported in node.names
        ):
            raise CertificationError(f"forbidden pytest outcome control in {label}")
        resolved = _resolved_test_name(node, aliases)
        if resolved is not None and resolved.rsplit(".", 1)[-1] in forbidden:
            raise CertificationError(f"forbidden pytest outcome control in {label}")
        if isinstance(node, ast.Call):
            called = _resolved_test_name(node.func, aliases)
            if called == "builtins.getattr" and len(node.args) >= 2:
                owner = _resolved_test_name(node.args[0], aliases)
                name = _constant_text(node.args[1])
                if (
                    owner is not None
                    and owner in {"pytest", "pytest.mark"}
                    and name in forbidden
                ):
                    raise CertificationError(
                        f"forbidden dynamic pytest outcome control in {label}"
                    )
            if (
                called == "builtins.getattr"
                and len(node.args) >= 3
                and capability_probe(node)
            ):
                raise CertificationError(
                    f"capability-based dynamic fallback in {label}"
                )
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and len(node.args) >= 2
                and capability_probe(node.func.value)
            ):
                raise CertificationError(
                    f"capability-based mapping fallback in {label}"
                )
            if called == "contextlib.suppress":
                suppressed = {
                    (_resolved_test_name(argument, aliases) or "").rsplit(".", 1)[-1]
                    for argument in node.args
                }
                if suppressed & {
                    "ImportError",
                    "ModuleNotFoundError",
                    "AttributeError",
                }:
                    raise CertificationError(
                        f"capability-based exception suppression in {label}"
                    )
        if isinstance(node, ast.Subscript):
            owner = _resolved_test_name(node.value, aliases)
            name = _constant_text(node.slice)
            if owner == "pytest.mark" and name in forbidden:
                raise CertificationError(
                    f"forbidden dynamic pytest marker in {label}"
                )
        if isinstance(node, (ast.If, ast.IfExp)):
            if not _exact_platform_dispatch(
                node.test, aliases
            ) and not _direct_platform_composite(
                node.test, aliases, product_names
            ) and capability_probe(node.test):
                raise CertificationError(
                    f"capability-based A selection in {label}"
                )
            if _exact_platform_dispatch(node.test, aliases):
                owner = owning_test(node)
                if owner is not None:
                    platforms = (
                        owned_platforms.get(owner.name, {"linux"})
                        if owned_platforms is not None
                        else {"linux"}
                    )
                    for platform_name in platforms:
                        owner_key = (id(owner), platform_name)
                        if owner_key in checked_platform_owners:
                            continue
                        checked_platform_owners.add(owner_key)
                        if owned_test_is_vacuous(owner, platform_name):
                            raise CertificationError(
                                "platform dispatch makes an owned A cell "
                                f"vacuous in {label} on {platform_name}"
                            )
        if (
            isinstance(node, (ast.BoolOp, ast.While))
            and not _direct_platform_composite(node, aliases, product_names)
            and capability_probe(node)
        ):
            raise CertificationError(f"capability-based A selection in {label}")
        if isinstance(node, ast.comprehension) and any(
            capability_probe(selector)
            for selector in node.ifs
        ):
            raise CertificationError(f"capability-based A selection in {label}")
        if isinstance(node, ast.Match) and capability_probe(node.subject):
            raise CertificationError(f"capability-based A selection in {label}")
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                handled = {
                    (_resolved_test_name(candidate, aliases) or "").rsplit(".", 1)[-1]
                    for candidate in ast.walk(handler.type)
                    if isinstance(candidate, (ast.Name, ast.Attribute))
                } if handler.type is not None else set()
                fail_closed = bool(handler.body) and all(
                    isinstance(statement, (ast.Raise, ast.Import, ast.ImportFrom))
                    for statement in handler.body
                )
                if (
                    handled & {"ImportError", "ModuleNotFoundError", "AttributeError"}
                    and not fail_closed
                ):
                    raise CertificationError(
                        f"capability-based exception selection in {label}"
                    )


def validate_no_skip_xfail(
    *,
    _source_mutants: Mapping[str, str] | None = None,
    _owned_platforms: Mapping[str, Mapping[str, set[str]]] | None = None,
) -> None:
    if _source_mutants is not None:
        for label, source in _source_mutants.items():
            _validate_effective_pytest_support_source(source, label)
            platforms = None if _owned_platforms is None else _owned_platforms.get(label)
            _validate_no_skip_xfail_source(source, label, platforms)
        return
    manifest, _ = _load_canonical_object(MANIFEST_PATH)
    platform_owners: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for cell in manifest.get("cells", []):
        if not isinstance(cell, dict) or not isinstance(cell.get("node_id"), str):
            continue
        path_text, separator, remainder = cell["node_id"].partition("::")
        if not separator:
            continue
        function_name = remainder.split("[", 1)[0]
        platform = cell.get("platform")
        if platform in {"linux", "windows"}:
            platform_owners[path_text][function_name].add(platform)
    for path in _test_python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CertificationError(f"cannot read A test: {path}") from exc
        relative = path.relative_to(REPO_ROOT).as_posix()
        _validate_effective_pytest_support_source(source, relative)
        if relative in _EFFECTIVE_PYTEST_SUPPORT_SHA256:
            continue
        _validate_no_skip_xfail_source(
            source,
            str(path),
            platform_owners.get(relative),
        )


def _terminal_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _constant_text(
    node: ast.AST, names: Mapping[str, str] | None = None
) -> str | None:
    if isinstance(node, ast.Name) and names is not None:
        return names.get(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_text(node.left, names)
        right = _constant_text(node.right, names)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                text = _constant_text(value.value, names)
            else:
                text = _constant_text(value, names)
            if text is None:
                return None
            parts.append(text)
        return "".join(parts)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and len(node.args) == 1
        and isinstance(node.args[0], (ast.List, ast.Tuple))
    ):
        separator = _constant_text(node.func.value, names)
        values = [_constant_text(item, names) for item in node.args[0].elts]
        if separator is not None and all(value is not None for value in values):
            return separator.join(value for value in values if value is not None)
    return None


def _reflective_mapping(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute) and node.attr == "__dict__"
    ) or (
        isinstance(node, ast.Call) and _terminal_name(node.func) == "vars"
    )


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
    call_nodes: dict[tuple[str, str], list[ast.Call]] = defaultdict(list)
    token_use_sites: dict[str, list[str]] = defaultdict(list)
    token_use_contexts: dict[str, list[str]] = defaultdict(list)
    return_shapes: dict[str, list[tuple[str, str] | None]] = defaultdict(list)
    direct_alias_exports: list[str] = []
    indirect_mutator_references: list[str] = []
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
            constant_texts: dict[str, str] = {}
            # A name is a reliable constant only when every constant-valued
            # assignment inside this owner agrees.  Two different texts, such as
            # the two branches of a try/except, make the name permanently
            # unknown.  The lattice is therefore monotone (unset -> text ->
            # ambiguous) and the fixpoint terminates; the previous rule
            # oscillated forever on any owner that rebinds a name.
            ambiguous_texts: set[str] = set()
            changed = True
            while changed:
                changed = False
                for node in body_nodes:
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    if node.value is None:
                        continue
                    value = _constant_text(node.value, constant_texts)
                    if value is None:
                        continue
                    targets = (
                        node.targets
                        if isinstance(node, ast.Assign)
                        else [node.target]
                    )
                    for target in targets:
                        if not isinstance(target, ast.Name):
                            continue
                        if target.id in ambiguous_texts:
                            continue
                        known = constant_texts.get(target.id)
                        if known is None:
                            constant_texts[target.id] = value
                            changed = True
                        elif known != value:
                            ambiguous_texts.add(target.id)
                            del constant_texts[target.id]
                            changed = True
                if changed:
                    continue
                capability_names: set[str] = set()
                if isinstance(owner_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    capability_names.update(
                        argument.arg
                        for argument in owner_node.args.args
                        if argument.arg == "session"
                    )
                if class_name == "_SecureRootSession":
                    capability_names.add("self")

                def imports_secure_filesystem(value: ast.AST) -> bool:
                    return (
                        isinstance(value, ast.Call)
                        and _terminal_name(value.func) == "import_module"
                        and bool(value.args)
                        and _normalize_productive_module(
                            _constant_text(value.args[0], constant_texts) or "",
                            known_modules,
                        )
                        == "runtime.executor_birth_secure_fs"
                    )

                def is_capability_target(value: ast.AST) -> bool:
                    if (
                        isinstance(value, ast.Name)
                        and value.id in capability_names
                    ):
                        return True
                    if imports_secure_filesystem(value):
                        return True
                    return _resolve_reference(
                        value,
                        module,
                        class_name,
                        owner_aliases,
                        simple_definitions,
                        class_methods,
                    ) == ("module", "runtime.executor_birth_secure_fs")

                alias_changed = True
                while alias_changed:
                    alias_changed = False
                    for assignment in body_nodes:
                        if not isinstance(assignment, (ast.Assign, ast.AnnAssign)):
                            continue
                        if not (
                            is_capability_target(assignment.value)
                            or imports_secure_filesystem(assignment.value)
                        ):
                            continue
                        targets = (
                            assignment.targets
                            if isinstance(assignment, ast.Assign)
                            else [assignment.target]
                        )
                        for target in targets:
                            if (
                                isinstance(target, ast.Name)
                                and target.id not in capability_names
                            ):
                                capability_names.add(target.id)
                                alias_changed = True
                for node in body_nodes:
                    dynamic_terminal: str | None = None
                    if isinstance(node, ast.Call):
                        terminal = _terminal_name(node.func)
                        if terminal in {"eval", "exec", "__import__"}:
                            indirect_mutator_references.append(
                                f"{path_text}:{getattr(node, 'lineno', 0)}:"
                                f"{owner}:{terminal}"
                            )
                        if (
                            terminal == "vars"
                            and node.args
                            and is_capability_target(node.args[0])
                        ):
                            indirect_mutator_references.append(
                                f"{path_text}:{getattr(node, 'lineno', 0)}:"
                                f"{owner}:capability-mapping"
                            )
                        capability_target: ast.AST | None = None
                        capability_attribute: ast.AST | None = None
                        if terminal == "getattr" and len(node.args) >= 2:
                            capability_target = node.args[0]
                            capability_attribute = node.args[1]
                        elif terminal == "__getattribute__" and node.args:
                            if (
                                isinstance(node.func, ast.Attribute)
                                and is_capability_target(node.func.value)
                            ):
                                capability_target = node.func.value
                                capability_attribute = node.args[-1]
                            elif len(node.args) >= 2:
                                capability_target = node.args[0]
                                capability_attribute = node.args[-1]
                        elif terminal == "partial" and len(node.args) >= 3:
                            if _terminal_name(node.args[0]) in {
                                "getattr",
                                "__getattribute__",
                            }:
                                capability_target = node.args[1]
                                capability_attribute = node.args[2]
                        if (
                            capability_target is not None
                            and is_capability_target(capability_target)
                            and not (
                                isinstance(capability_attribute, ast.Constant)
                                and isinstance(capability_attribute.value, str)
                            )
                        ):
                            indirect_mutator_references.append(
                                f"{path_text}:{getattr(node, 'lineno', 0)}:"
                                f"{owner}:dynamic-capability-name"
                            )
                        if terminal == "getattr" and len(node.args) >= 2:
                            dynamic_terminal = _constant_text(
                                node.args[1], constant_texts
                            )
                        elif terminal == "__getattribute__" and node.args:
                            dynamic_terminal = _constant_text(
                                node.args[-1], constant_texts
                            )
                        elif terminal in {"attrgetter", "methodcaller"} and node.args:
                            dynamic_terminal = _constant_text(
                                node.args[0], constant_texts
                            )
                        elif terminal == "partial" and node.args:
                            reflective = _terminal_name(node.args[0]) in {
                                "getattr",
                                "__getattribute__",
                                "attrgetter",
                                "methodcaller",
                            }
                            if reflective:
                                for argument in node.args[1:]:
                                    value = _constant_text(argument, constant_texts)
                                    if value in _SENSITIVE_TERMINALS:
                                        dynamic_terminal = value
                                        break
                        elif (
                            terminal == "get"
                            and isinstance(node.func, ast.Attribute)
                            and _reflective_mapping(node.func.value)
                            and node.args
                        ):
                            dynamic_terminal = _constant_text(
                                node.args[0], constant_texts
                            )
                        if dynamic_terminal not in _SENSITIVE_TERMINALS:
                            dynamic_terminal = None
                    if isinstance(node, ast.Subscript):
                        attribute = _constant_text(node.slice, constant_texts)
                        if (
                            attribute in _SENSITIVE_TERMINALS
                            and _reflective_mapping(node.value)
                        ):
                            dynamic_terminal = attribute
                    if (
                        isinstance(node, ast.Attribute)
                        and node.attr == "__dict__"
                        and is_capability_target(node.value)
                    ):
                        indirect_mutator_references.append(
                            f"{path_text}:{getattr(node, 'lineno', 0)}:"
                            f"{owner}:capability-dict"
                        )
                    if (
                        isinstance(node, ast.Attribute)
                        and node.attr == "modules"
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "sys"
                    ):
                        indirect_mutator_references.append(
                            f"{path_text}:{getattr(node, 'lineno', 0)}:"
                            f"{owner}:sys-modules"
                        )
                    if dynamic_terminal is not None:
                        canonical_target = f"@dynamic:{dynamic_terminal}"
                        calls[owner].add(canonical_target)
                        call_sites[(owner, canonical_target)].append(
                            f"{path_text}:{getattr(node, 'lineno', 0)}:dynamic"
                        )
                    if (
                        isinstance(node, ast.Attribute)
                        and isinstance(node.ctx, ast.Load)
                        and node.attr in _MUTATION_METHODS
                    ):
                        parent = parents.get(node)
                        if not (
                            isinstance(parent, ast.Call) and parent.func is node
                        ):
                            indirect_mutator_references.append(
                                f"{path_text}:{getattr(node, 'lineno', 0)}:"
                                f"{owner}:{node.attr}"
                            )
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
                    if (
                        isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom))
                        and node.value is not None
                    ):
                        if is_capability_target(node.value):
                            indirect_mutator_references.append(
                                f"{path_text}:{getattr(node, 'lineno', 0)}:"
                                f"{owner}:capability-return"
                            )
                        returned_node = (
                            node.value.func
                            if isinstance(node.value, ast.Call)
                            else node.value
                        )
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
                            canonical_target = (
                                target if kind == "symbol" else f"@{kind}:{target}"
                            )
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
                    if (
                        resolved is None
                        and _terminal_name(node.func) == "getattr"
                        and len(node.args) >= 2
                    ):
                        attribute = node.args[1]
                        if (
                            isinstance(attribute, ast.Constant)
                            and attribute.value in _SENSITIVE_TERMINALS
                        ):
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
                    call_nodes[(owner, canonical_target)].append(node)

        for name, resolved in aliases.get(module, {}).items():
            if not name.startswith("_") and _is_sensitive_reference(resolved):
                direct_alias_exports.append(f"{path_text}:{name}")

    descriptor_symbol = "runtime.executor_birth_secure_fs::_AuthenticatedRootDescriptor"
    catalog_symbol = "runtime.executor_birth_secure_fs::_BirthRoleCatalogV1"
    catalog_extension_symbol = (
        "runtime.executor_birth_secure_fs::_BirthRoleCatalogExtensionV1"
    )
    adopt_symbol = "runtime.executor_birth_secure_fs::_adopt_authenticated_root"
    session_symbol = "runtime.executor_birth_secure_fs::_SecureRootSession"
    session_token_symbol = "runtime.executor_birth_secure_fs::_SESSION_TOKEN"
    legacy_wrapper_symbol = "runtime.executor_birth_secure_fs::_LegacyReadSession"
    entry_symbol = "install.birth_authority_provisioning::open_birth_provisioning_layout_v1"
    layout_symbol = "install.birth_authority_provisioning::ProvisioningLayoutV1"
    installer_resolver_symbols = {
        "install.birth_authority_provisioning::_resolve_path_user_config_v1",
        "install.birth_authority_provisioning::_resolve_birth_service_identity_v1",
        "install.birth_authority_provisioning::_resolve_birth_root_v1",
        "install.birth_authority_provisioning::_resolve_operator_input_v1",
    }
    mutation_symbols = {
        f"runtime.executor_birth_secure_fs::_SecureRootSession.{name}"
        for name in _MUTATION_METHODS
    }
    sensitive_targets = {
        descriptor_symbol,
        catalog_symbol,
        catalog_extension_symbol,
        adopt_symbol,
        session_symbol,
        session_token_symbol,
        entry_symbol,
        *mutation_symbols,
    }
    sensitive_targets.update(f"@method:{name}" for name in _MUTATION_METHODS)
    sensitive_targets.update(f"@dynamic:{name}" for name in _SENSITIVE_TERMINALS)

    if entry_symbol not in definitions or layout_symbol not in definitions:
        raise CertificationError("installer-only entry is absent from the productive graph")
    constructor_sites = _sites_reaching_exact_call(calls, call_sites, descriptor_symbol)
    # Section 16.13.4 gives the historical loaders their own distinct and
    # constant catalogues, so only the authoritative construction — the one
    # that carries every Birth pattern and no exact binding — is required to
    # live at a single installer site.
    catalog_sites = [
        site
        for site in _sites_reaching_exact_call(calls, call_sites, catalog_symbol)
        if any(
            _is_authoritative_catalog_constructor(
                node,
                site[0].split("::", 1)[0],
                sources[site[0].split("::", 1)[0]][1],
                aliases,
                simple_definitions,
                class_methods,
            )
            for node in call_nodes.get((site[0], catalog_symbol), [])
        )
    ]
    adopter_sites = _sites_reaching_exact_call(calls, call_sites, adopt_symbol)
    session_sites = _sites_reaching_exact_call(calls, call_sites, session_symbol)
    layout_sites = _sites_reaching_exact_call(calls, call_sites, layout_symbol)
    expected_site_module = "install.birth_authority_provisioning::"
    if len(constructor_sites) != 1 or not constructor_sites[0][0].startswith(expected_site_module):
        raise CertificationError(
            f"descriptor must have one installer construction site: {constructor_sites!r}"
        )
    constructor_owner = constructor_sites[0][0]
    descriptor_calls = call_nodes.get((constructor_owner, descriptor_symbol), [])
    catalog_calls_for_owner = call_nodes.get((constructor_owner, catalog_symbol), [])
    if len(descriptor_calls) != 1 or len(catalog_calls_for_owner) != 1:
        raise CertificationError("installer descriptor/catalog construction is not singular")
    descriptor_call = descriptor_calls[0]
    descriptor_values = {
        keyword.arg: keyword.value
        for keyword in descriptor_call.keywords
        if keyword.arg is not None
    }
    if (
        descriptor_call.args
        or any(keyword.arg is None for keyword in descriptor_call.keywords)
        or set(descriptor_values)
        != {"handles", "root_path", "identity", "role_catalog"}
        or not isinstance(descriptor_values["role_catalog"], ast.Name)
        or descriptor_values["role_catalog"].id != "catalog"
    ):
        raise CertificationError(
            "installer descriptor must receive the authoritative catalog variable unchanged"
        )
    installer_tree = sources[constructor_owner.split("::", 1)[0]][1]
    installer_parents = {
        child: parent
        for parent in ast.walk(installer_tree)
        for child in ast.iter_child_nodes(parent)
    }
    catalog_assignment = installer_parents.get(catalog_calls_for_owner[0])
    if not (
        isinstance(catalog_assignment, ast.Assign)
        and len(catalog_assignment.targets) == 1
        and isinstance(catalog_assignment.targets[0], ast.Name)
        and catalog_assignment.targets[0].id == "catalog"
    ):
        raise CertificationError(
            "authoritative role catalog must be assigned once to catalog"
        )
    if len(catalog_sites) != 1 or not catalog_sites[0][0].startswith(
        expected_site_module
    ):
        raise CertificationError(
            f"role catalog must have one installer construction site: {catalog_sites!r}"
        )
    catalog_owner = catalog_sites[0][0]
    catalog_calls = call_nodes.get((catalog_owner, catalog_symbol), [])
    if len(catalog_calls) != 1 or not _is_authoritative_catalog_constructor(
        catalog_calls[0],
        catalog_owner.split("::", 1)[0],
        sources[catalog_owner.split("::", 1)[0]][1],
        aliases,
        simple_definitions,
        class_methods,
    ):
        raise CertificationError(
            "installer role catalog must use schema_version=1, generation=0, "
            "patterns=tuple(_BirthRolePatternV1), and no exact bindings"
        )
    if len(adopter_sites) != 1 or not adopter_sites[0][0].startswith(expected_site_module):
        raise CertificationError(
            f"descriptor must have one installer adoption site: {adopter_sites!r}"
        )
    if len(layout_sites) != 1 or layout_sites[0][0] != entry_symbol:
        raise CertificationError(
            f"layout must have one direct entry construction site: {layout_sites!r}"
        )
    layout_calls = call_nodes.get((entry_symbol, layout_symbol), [])
    if len(layout_calls) != 1:
        raise CertificationError("installer layout construction is not singular")
    layout_call = layout_calls[0]
    if (
        layout_call.args
        or any(keyword.arg is None for keyword in layout_call.keywords)
        or {keyword.arg for keyword in layout_call.keywords}
        != {"birth_session", "operator_input", "service_identity"}
        or any(
            not isinstance(keyword.value, ast.Name)
            or keyword.value.id
            != {
                "birth_session": "session",
                "operator_input": "operator_input",
                "service_identity": "identity",
            }[keyword.arg]
            for keyword in layout_call.keywords
        )
    ):
        raise CertificationError(
            "installer layout must preserve session, operator input and identity"
        )
    adopt_session_calls = call_nodes.get((adopt_symbol, session_symbol), [])
    if len(adopt_session_calls) != 1 or not _passes_descriptor_role_catalog(
        adopt_session_calls[0]
    ):
        raise CertificationError(
            "root adoption must pass role_catalog=descriptor.role_catalog "
            "unchanged to the secure session"
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
    if not {
        descriptor_symbol,
        catalog_symbol,
        adopt_symbol,
        layout_symbol,
        *installer_resolver_symbols,
    } <= entry_reachable:
        raise CertificationError(
            "installer entry does not own its closed resolver/layout graph transitively"
        )
    if return_shapes.get(entry_symbol) != [("symbol", layout_symbol)]:
        raise CertificationError(
            "installer entry must return exactly one ProvisioningLayoutV1"
        )

    entry_definition_reachable: set[str] = set()
    pending = [entry_symbol]
    while pending:
        current = pending.pop()
        for target in calls.get(current, ()):
            if target in definitions and target not in entry_definition_reachable:
                entry_definition_reachable.add(target)
                pending.append(target)

    violations = [*direct_alias_exports, *indirect_mutator_references]
    for owner in calls:
        reached = reachable(owner) & sensitive_targets
        if not reached:
            continue
        owner_module, owner_name = owner.split("::", 1)
        if owner_module == "runtime.executor_birth_secure_fs":
            reached_mutations = reached & mutation_symbols
            reached_mutations.update(
                target
                for target in reached
                if target.startswith("@method:")
                and target.removeprefix("@method:") in _MUTATION_METHODS
            )
            if reached_mutations and not owner_name.startswith(
                "_SecureRootSession."
            ):
                violations.append(
                    f"{owner} creates a second filesystem mutation surface: "
                    f"{sorted(reached_mutations)!r}"
                )
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
    "_extend_role_catalog_v1",
}
_SENSITIVE_TERMINALS = {
    "_AuthenticatedRootDescriptor",
    "_BirthRoleCatalogV1",
    "_BirthRoleCatalogExtensionV1",
    "_adopt_authenticated_root",
    "_SecureRootSession",
    "_SESSION_TOKEN",
    "open_birth_provisioning_layout_v1",
    *_MUTATION_METHODS,
}


def _is_authoritative_catalog_constructor(
    call: ast.Call,
    module: str,
    module_tree: ast.Module,
    aliases: Mapping[str, Mapping[str, tuple[str, str]]],
    simple_definitions: Mapping[str, Mapping[str, str]],
    class_methods: Mapping[str, Mapping[tuple[str, str], str]],
) -> bool:
    """Recognize the sole direct production construction allowed by section 16.13.4."""
    if not (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "_BirthRoleCatalogV1"
        and _resolve_reference(
            call.func,
            module,
            None,
            aliases,
            simple_definitions,
            class_methods,
        )
        == ("symbol", "runtime.executor_birth_secure_fs::_BirthRoleCatalogV1")
    ):
        return False
    if call.args or any(keyword.arg is None for keyword in call.keywords):
        return False
    values = {keyword.arg: keyword.value for keyword in call.keywords}
    if set(values) != {
        "schema_version",
        "patterns",
        "exact_bindings",
        "generation",
    }:
        return False
    schema = values["schema_version"]
    generation = values["generation"]
    if not (
        isinstance(schema, ast.Constant)
        and type(schema.value) is int
        and schema.value == 1
        and isinstance(generation, ast.Constant)
        and type(generation.value) is int
        and generation.value == 0
    ):
        return False
    exact_bindings = values["exact_bindings"]
    if not isinstance(exact_bindings, ast.Tuple) or exact_bindings.elts:
        return False
    patterns = values["patterns"]
    if not (
        isinstance(patterns, ast.Call)
        and isinstance(patterns.func, ast.Name)
        and patterns.func.id == "tuple"
        and not _ast_binds_name(module_tree, "tuple")
        and len(patterns.args) == 1
        and not patterns.keywords
        and isinstance(patterns.args[0], ast.Attribute)
        and patterns.args[0].attr == "_BirthRolePatternV1"
    ):
        return False
    resolved = _resolve_reference(
        patterns.args[0],
        module,
        None,
        aliases,
        simple_definitions,
        class_methods,
    )
    return resolved == (
        "symbol",
        "runtime.executor_birth_secure_fs::_BirthRolePatternV1",
    )


def _ast_binds_name(tree: ast.AST, name: str) -> bool:
    """Reject a syntactically canonical call whose apparent builtin is shadowed."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.id == name:
                return True
        elif isinstance(node, ast.arg) and node.arg == name:
            return True
        elif isinstance(node, ast.alias):
            bound = node.asname or node.name.split(".", 1)[0]
            if bound == name:
                return True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return True
    return False


def _passes_descriptor_role_catalog(call: ast.Call) -> bool:
    """Require identity-preserving transfer at the sole Birth adoption edge."""
    role_keywords = [
        keyword for keyword in call.keywords if keyword.arg == "role_catalog"
    ]
    if len(role_keywords) != 1 or any(keyword.arg is None for keyword in call.keywords):
        return False
    value = role_keywords[0].value
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "role_catalog"
        and isinstance(value.value, ast.Name)
        and value.value.id == "descriptor"
    )


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
    """Yield one owner's executable graph, including its nested scopes.

    Module-level definitions are separate owners. A function or method owns
    every nested function/class body it can create, so those bodies must not be
    invisible to the capability graph.
    """
    stack = list(ast.iter_child_nodes(owner))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if isinstance(owner, ast.Module):
                continue
            yield node
            stack.extend(ast.iter_child_nodes(node))
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
        "runtime.executor_birth_secure_fs::_BirthRoleCatalogV1",
        "runtime.executor_birth_secure_fs::_BirthRoleCatalogExtensionV1",
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


def _run_git_bytes(arguments: Sequence[str], purpose: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=REPO_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CertificationError(f"cannot {purpose}") from exc


def _require_snapshot_commit(source_git_sha: str) -> None:
    _run_git_bytes(
        ["cat-file", "-e", f"{source_git_sha}^{{commit}}"],
        "resolve the pre-fix source commit",
    )
    try:
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", source_git_sha, "HEAD"],
            cwd=REPO_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise CertificationError("cannot verify pre-fix commit ancestry") from exc
    if ancestry.returncode == 1:
        raise CertificationError("pre-fix source commit is not an ancestor of HEAD")
    if ancestry.returncode != 0:
        raise CertificationError("cannot verify pre-fix commit ancestry")


def _frozen_tree(commit: str) -> dict[str, tuple[str, str]]:
    requested = [*_FROZEN_TREE_PREFIXES, *_FROZEN_EXACT_PATHS]
    raw = _run_git_bytes(
        ["ls-tree", "-r", "-z", "--full-tree", commit, "--", *requested],
        f"read the frozen acceptance tree at {commit}",
    )
    entries: dict[str, tuple[str, str]] = {}
    for raw_record in (record for record in raw.split(b"\0") if record):
        header, separator, raw_path = raw_record.partition(b"\t")
        fields = header.split()
        if separator != b"\t" or len(fields) != 3:
            raise CertificationError("Git emitted a malformed frozen-tree record")
        mode, object_type, object_id = fields
        try:
            path_text = raw_path.decode("utf-8")
            mode_text = mode.decode("ascii")
            object_id_text = object_id.decode("ascii")
        except UnicodeDecodeError as exc:
            raise CertificationError("frozen-tree metadata is not canonical text") from exc
        if path_text in _FROZEN_TREE_EXCLUSIONS:
            continue
        if object_type != b"blob" or mode not in {b"100644", b"100755"}:
            raise CertificationError(
                f"frozen acceptance path is not a regular blob: {path_text}"
            )
        if path_text in entries:
            raise CertificationError(f"duplicate frozen acceptance path: {path_text}")
        entries[path_text] = (mode_text, object_id_text)
    missing = sorted(set(_FROZEN_EXACT_PATHS) - set(entries))
    if missing:
        raise CertificationError(f"frozen acceptance files are absent: {missing!r}")
    for prefix in _FROZEN_TREE_PREFIXES:
        if not any(path.startswith(prefix + "/") for path in entries):
            raise CertificationError(f"frozen acceptance tree is absent: {prefix}")
    return entries


def _git_blob(commit: str, path: Path) -> bytes:
    relative = path.relative_to(REPO_ROOT).as_posix()
    return _run_git_bytes(
        ["cat-file", "blob", f"{commit}:{relative}"],
        f"read historical blob {relative}",
    )


def _validate_frozen_acceptance_baseline(
    source_git_sha: str, evidence: Mapping[str, Any]
) -> None:
    _require_snapshot_commit(source_git_sha)
    source_tree = _frozen_tree(source_git_sha)
    current_tree = _frozen_tree("HEAD")
    if source_tree != current_tree:
        source_paths = set(source_tree)
        current_paths = set(current_tree)
        changed = sorted(
            path
            for path in source_paths & current_paths
            if source_tree[path] != current_tree[path]
        )
        raise CertificationError(
            "frozen acceptance baseline differs from the pre-fix commit; "
            f"missing={sorted(source_paths - current_paths)!r}, "
            f"added={sorted(current_paths - source_paths)!r}, "
            f"changed={changed!r}"
        )
    historical_manifest = _git_blob(source_git_sha, MANIFEST_PATH)
    if digest_bytes(historical_manifest) != evidence["manifest_sha256"]:
        raise CertificationError("pre-fix manifest digest differs from its source blob")
    historical_inventory = _git_blob(source_git_sha, INVENTORY_PATH)
    if digest_bytes(historical_inventory) != evidence["production_inventory_sha256"]:
        raise CertificationError(
            "pre-fix production inventory digest differs from its source blob"
        )


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
    _validate_frozen_acceptance_baseline(evidence["source_git_sha"], evidence)
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
    if not isinstance(results, list) or len(results) != len(REQUIRED_CELLS_V1):
        raise CertificationError(
            "pre-fix aggregate must contain one result per required cell"
        )
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
    checkout_action = (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    for job_id, block in jobs.items():
        if (
            block.count(checkout_action) != 1
            or block.count("persist-credentials: false") != 1
            or block.count("fetch-depth: 0") != 1
        ):
            raise CertificationError(
                f"{job_id} does not use the pinned full-history checkout"
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
        if block.count("\n      - name:") != 5:
            raise CertificationError(f"{job_id} does not have the closed five-step form")
        inline_runs = re.findall(
            r"^        run: (?![>|])([^\n]+)$", block, re.MULTILINE
        )
        expected_install = (
            "python -m pip install --disable-pip-version-check "
            "--requirement tests/portable/requirements.txt"
        )
        if inline_runs != [expected_install]:
            raise CertificationError(f"{job_id} has an unexpected inline command")
        folded_runs = re.findall(
            r"^        run: >-\n((?:          [^\n]+\n)+)",
            block,
            re.MULTILINE,
        )
        expected_mode = (
            '--mode "$env:RM0008_A_MODE"'
            if runner == "windows-2022"
            else '--mode "$RM0008_A_MODE"'
        )
        evidence_name = f"rm0008-2a-evidence-{activity}"
        expected_runner_lines = (
            "python -P tests/portable/rm0008_2a_acceptance/run_activity_v1.py\n"
            f"--activity {activity} {expected_mode} --import-mode=importlib\n"
            f"--evidence {evidence_name}.json"
        )
        normalized_runs = [
            "\n".join(line.strip() for line in value.splitlines())
            for value in folded_runs
        ]
        if normalized_runs != [expected_runner_lines]:
            raise CertificationError(f"{job_id} has an unexpected folded command")
        if re.search(rf"^    runs-on: {re.escape(runner)}$", block, re.MULTILINE) is None:
            raise CertificationError(f"{job_id} uses the wrong runner")
        command = "python -P tests/portable/rm0008_2a_acceptance/run_activity_v1.py"
        if block.count(command) != 1:
            raise CertificationError(f"{job_id} must invoke the A runner exactly once")
        if block.count(f"--activity {activity}") != 1:
            raise CertificationError(f"{job_id} selects the wrong activity")
        if block.count("--import-mode=importlib") != 1:
            raise CertificationError(f"{job_id} does not fix pytest importlib mode")
        if block.count(expected_mode) != 1:
            raise CertificationError(f"{job_id} does not use the closed snapshot/final mode")
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
        "python -P tests/portable/rm0008_2a_acceptance/aggregate_evidence_v1.py"
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
    if _normalized_source_sha256(text) != _FROZEN_WORKFLOW_SHA256:
        raise CertificationError("workflow differs from the frozen certification form")


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
