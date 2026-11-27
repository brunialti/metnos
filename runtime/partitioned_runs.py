"""Partitioned execution: one declared argument, many independent runs.

A tool that works on one partition (for files: one folder) declares it in its
manifest. Its code never changes and never sees more than one partition:

    [execution.partition]
    arg      = "base_path"     # names ONE partition
    list_arg = "base_paths"    # the list a plan writes; it never reaches the code
    kind     = "directory"
    union    = "separate"      # or "exact"
    sums     = ["scanned_files"]

This module owns three things and runs nothing by itself:

* the declaration (``validate_partition``, ``normalized_partition``);
* the reduction of what a plan wrote to a literal list of partitions
  (``resolve``): several values as they are, or every child of a parent
  written as the single value ``<parent>/*``, listed by a caller-supplied
  function;
* the one result shape (``assemble``, ``combine``): every row carries the
  partition it belongs to, every partition has its own summary line, and a
  partition that failed is an item lost by the call, never silence.

``union`` says what the rows of all partitions mean together. ``exact``: they
are the result on the whole, and the declared counters add up. ``separate``:
they are N answers, each true inside its own partition only (duplicates inside
folders are not duplicates across folders). Absent or unproven means
``separate``, which is always true and promises nothing.

Design and measures: internal/design/parallelismo_su_piu_cartelle_20261116.md
"""
from __future__ import annotations

import ntpath
import posixpath
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

PARTITION_FIELD = "partition"
# A ceiling of policy, not an argument: a wider request is refused with the
# count, never served on its first values (§2.11).
MAX_PARTITIONS = 256
UNIONS = ("separate", "exact")
KINDS = ("directory", "independent_rows")

_SECTION_FIELDS = frozenset({"arg", "list_arg", "kind", "union", "sums"})
_REQUIRED_FIELDS = frozenset({"arg", "list_arg", "kind"})
_TRAVERSAL_SEMANTICS = {
    "directory_recursion": ("recursive", "boolean"),
    "directory_max_depth": ("depth", "integer"),
}
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MAX_SUMS = 32
_ROW_KEYS = ("entries", "results")
_ERROR_KEYS = ("error_class", "error_code", "error")
_TRUNCATION_KEYS = (
    "truncated", "truncated_what", "used", "available_total",
    "cap_field", "cap_value", "truncated_intentional",
)
# A row names its partition by the field this module writes, or by the path
# a listing tool gives it.
_ROW_PARTITION_FIELDS = (PARTITION_FIELD, "path")
_WILDCARDS = frozenset("*?[")
_WINDOWS_ROOT_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


# --------------------------------------------------------------- declaration

def _partition_section(manifest: Mapping[str, Any] | None) -> object:
    execution = (manifest or {}).get("execution")
    return execution.get("partition") if isinstance(execution, Mapping) else None


def _directory_traversal(properties: Mapping[str, Any]) -> tuple[dict | None, list[str]]:
    """Derive traversal metadata from signed argument semantics, not names.

    The pair promises directory-root depth 0, immediate entries depth 1,
    and 0 as unlimited. Explicit defaults preserve calls that omit either
    argument. A partial or ambiguous promise cannot safely lift the root.
    """
    selected = {semantic: [(name, spec) for name, spec in properties.items()
                           if isinstance(spec, Mapping)
                           and spec.get("semantic_type") == semantic]
                for semantic in _TRAVERSAL_SEMANTICS}
    if not any(selected.values()):
        return None, []
    if any(len(items) != 1 for items in selected.values()):
        return None, ["directory traversal requires exactly one directory_recursion "
                      "and one directory_max_depth argument"]
    traversal: dict[str, Any] = {}
    for semantic, (field, schema_type) in _TRAVERSAL_SEMANTICS.items():
        name, spec = selected[semantic][0]
        default = spec.get("default")
        valid_default = (type(default) is bool if field == "recursive" else
                         type(default) is int and default >= 0)
        if (spec.get("type") != schema_type or not valid_default
                or spec.get("runtime_resolved") is True):
            return None, [f"{semantic} requires a declared {schema_type} argument "
                          "with a valid explicit default"]
        traversal[field + "_arg"] = name
        traversal[field + "_default"] = default
    return traversal, []


def _partition_accepts_effect(kind: str, effect: object) -> bool:
    """The allowed effects of an already validated partition kind."""
    return (effect == "read_only" if kind == "directory"
            else effect in ("read_only", "reversible", "create_only", "mutating"))


def validate_partition(manifest: Mapping[str, Any] | None) -> list[str]:
    """Findings on ``[execution.partition]``; an absent section has none."""
    raw = _partition_section(manifest)
    if raw is None:
        return []
    if not isinstance(raw, Mapping):
        return ["execution.partition must be a table"]
    unknown = sorted(set(raw) - _SECTION_FIELDS)
    if unknown:
        return [f"unknown [execution.partition] fields: {unknown}"]
    missing = sorted(_REQUIRED_FIELDS - set(raw))
    if missing:
        return [f"execution.partition requires {missing}"]

    findings: list[str] = []
    args = (manifest or {}).get("args")
    args = args if isinstance(args, Mapping) else {}
    properties = args.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    _, traversal_findings = _directory_traversal(properties)
    findings.extend(traversal_findings)
    required = args.get("required")
    required = ({name for name in required if isinstance(name, str)}
                if isinstance(required, list) else set())

    kind = raw.get("kind")
    if kind not in KINDS:
        findings.append(f"execution.partition.kind must be one of {sorted(KINDS)}")
        return findings
    if not _partition_accepts_effect(kind, ((manifest or {}).get("execution") or {}).get("effect")):
        findings.append("execution.partition requires effect = 'read_only'")
    union = raw.get("union", "separate")
    if union not in UNIONS:
        findings.append(
            f"execution.partition.union must be one of {sorted(UNIONS)}")
    sums = raw.get("sums", [])
    if (not isinstance(sums, list) or len(sums) > _MAX_SUMS
            or len(set(map(str, sums))) != len(sums)
            or any(not isinstance(name, str) or not _NAME_RE.fullmatch(name)
                   for name in sums)):
        findings.append(
            "execution.partition.sums must list unique counter names")
    if union == "exact" and not _has_equivalence_proof(manifest):
        findings.append(
            "execution.partition.union = 'exact' requires a test with "
            "reference and equivalence_runs between 2 and 8")
    arg, list_arg = raw.get("arg"), raw.get("list_arg")
    single = properties.get(arg) if isinstance(arg, str) else None
    plural = properties.get(list_arg) if isinstance(list_arg, str) else None
    if kind == "independent_rows":
        if arg != list_arg or not isinstance(plural, Mapping) or plural.get("type") != "array":
            findings.append("independent rows require arg = list_arg naming an array")
        if union != "exact":
            findings.append("independent rows require a controlled exact equivalence test")
        return findings
    if (not isinstance(single, Mapping) or single.get("type") != "string"
            or single.get("runtime_resolved") is True):
        findings.append(
            "execution.partition.arg must be a declared string argument")
    if (not isinstance(plural, Mapping) or plural.get("type") != "array"
            or plural.get("runtime_resolved") is True or list_arg == arg):
        findings.append(
            "execution.partition.list_arg must be a declared array argument "
            "distinct from arg")
    elif (isinstance(plural.get("items"), Mapping)
            and plural["items"].get("type") not in (None, "string")):
        # What a plan writes is text. Rows reach the list only from the
        # runtime, which resolves them before the tool is called.
        findings.append("execution.partition.list_arg must list strings")
    if {name for name in (arg, list_arg) if isinstance(name, str)} & required:
        # A call names the one partition or the list: neither can be
        # required by itself.
        findings.append(
            "execution.partition arg and list_arg cannot be in args.required")
    return findings


def _has_equivalence_proof(manifest: Mapping[str, Any] | None) -> bool:
    tests = (manifest or {}).get("tests")
    return any(isinstance(test, Mapping)
               and isinstance(test.get("reference"), str) and bool(test["reference"].strip())
               and type(test.get("equivalence_runs")) is int and 2 <= test["equivalence_runs"] <= 8
               for test in (tests if isinstance(tests, list) else []))


def normalized_partition(manifest: Mapping[str, Any] | None) -> dict | None:
    """The declaration as the runtime uses it, or None.

    Fail-closed: a section with any finding declares nothing, so a broken
    manifest runs as an ordinary single call and never fans out."""
    raw = _partition_section(manifest)
    if not isinstance(raw, Mapping) or validate_partition(manifest):
        return None
    partition = {
        "arg": raw["arg"],
        "list_arg": raw["list_arg"],
        "kind": raw["kind"],
        "union": raw.get("union", "separate"),
        "sums": tuple(raw.get("sums", ())),
    }
    traversal, _ = _directory_traversal(manifest["args"]["properties"])
    if traversal is not None:
        partition["traversal"] = traversal
    return partition


def policy_of(executor: object) -> dict | None:
    """The partition a loaded tool declares, when the runtime may use it."""
    if getattr(executor, "execution_policy_declared", False) is not True:
        return None
    policy = getattr(executor, "execution_policy", None)
    if not isinstance(policy, Mapping):
        return None
    partition = policy.get("partition")
    if (not isinstance(partition, Mapping)
            or not _REQUIRED_FIELDS <= set(partition)
            or partition.get("kind") not in KINDS):
        return None
    if not _partition_accepts_effect(partition["kind"], policy.get("effect")):
        return None
    result = {
        "arg": str(partition["arg"]),
        "list_arg": str(partition["list_arg"]),
        "kind": str(partition["kind"]),
        "union": (partition.get("union")
                  if partition.get("union") in UNIONS else "separate"),
        "sums": tuple(name for name in (partition.get("sums") or ())
                      if isinstance(name, str)),
    }
    if "traversal" in partition:
        traversal = partition["traversal"]
        if (not isinstance(traversal, Mapping)
                or not isinstance(traversal.get("recursive_arg"), str)
                or not isinstance(traversal.get("depth_arg"), str)
                or traversal["recursive_arg"] == traversal["depth_arg"]
                or type(traversal.get("recursive_default")) is not bool
                or type(traversal.get("depth_default")) is not int
                or traversal["depth_default"] < 0):
            return None
        result["traversal"] = dict(traversal)
    return result


# ---------------------------------------------------------------- resolution

class PartitionRefusal(ValueError):
    """The list of partitions cannot be served as written: nothing starts.

    ``code`` is a message key; ``values`` are its named values. The caller
    renders the words in the instance language (§7.13)."""

    def __init__(self, code: str, **values: object) -> None:
        super().__init__(code)
        self.code = code
        self.values = values


@dataclass(frozen=True)
class Resolution:
    """What a call asked for, reduced to literal partitions.

    ``base_args`` are the other arguments, shared by every partition.
    ``ignored_single`` is true when the plan also wrote the single argument:
    the list wins. ``expanded`` names each parent written as ``<parent>/*``
    with how many partitions it gave."""

    partitions: tuple[str, ...]
    base_args: dict
    ignored_single: bool = False
    expanded: tuple[tuple[str, int], ...] = ()


def _flavour(value: str):
    return ntpath if (_WINDOWS_ROOT_RE.match(value) or "\\" in value) else posixpath


def _key(value: str) -> tuple[str, ...]:
    """Lexical identity of a directory, for equality and containment.

    Nothing is read: aliases and links are resolved later by the tool, inside
    its own sandbox. Two spellings of one folder that only the filesystem
    can equate are caught there, not here."""
    flavour = _flavour(value)
    normal = flavour.normpath(value)
    if flavour is ntpath:
        normal = normal.replace("/", "\\").casefold()
        parts = [part for part in normal.split("\\") if part]
        return ("\\", *parts) if normal.startswith("\\") else tuple(parts)
    parts = [part for part in normal.split("/") if part]
    return ("/", *parts) if normal.startswith("/") else tuple(parts)


def _children_parent(value: str) -> str | None:
    """``<parent>/*`` means every child of the parent; return the parent."""
    for tail in ("/*", "\\*"):
        if value.endswith(tail) and len(value) > len(tail):
            return value[:-len(tail)]
    return None


def _named_by_row(row: Mapping[str, Any]) -> object:
    return next((row[name] for name in _ROW_PARTITION_FIELDS
                 if isinstance(row.get(name), str)), None)


def resolve(policy: Mapping[str, Any], args: Mapping[str, Any], *,
            list_children: Callable[[str], Sequence[str]] | None = None,
            ) -> Resolution | None:
    """Reduce the arguments of one call to a literal list of partitions.

    None: the call does not use the list argument and is an ordinary call.
    ``list_children(parent)`` returns the partitions under a parent, in the
    order to keep; it may raise ``PartitionRefusal``. Without it a value
    written as ``<parent>/*`` cannot be served here.
    """
    arg, list_arg = policy["arg"], policy["list_arg"]
    values = args.get(list_arg)
    if values is None or values == []:
        return None
    if isinstance(values, (str, Mapping)):
        # §2.4: one value given where the list was expected.
        values = [values]
    if not isinstance(values, (list, tuple)):
        raise PartitionRefusal("ERR_PARTITION_VALUES", arg=list_arg)
    # A value is a partition as text, or a row that names one: the rows of a
    # step that listed folders, and the row a unit of a long job is given.
    values = [_named_by_row(value) if isinstance(value, Mapping) else value
              for value in values]
    if any(not isinstance(value, str) or not value.strip() or "\x00" in value
           for value in values):
        raise PartitionRefusal("ERR_PARTITION_VALUES", arg=list_arg)

    values = [value.strip() for value in values]
    if len(values) > 1:
        # Several values are folders named one by one. Measured on the local
        # planner (16/11/2026, 16 plans out of 16 over four variants): asked
        # for «the folders A, B and C, each on its own» it writes A/*, B/*,
        # C/*, copying onto each the mark its tool's example uses for «each on
        # its own». «Every child of» is therefore said by a single value; on
        # several values the mark is dropped and each one is the folder.
        values = [_children_parent(value) or value for value in values]
    partitions: list[str] = []
    expanded: list[tuple[str, int]] = []
    for value in values:
        parent = _children_parent(value)
        if parent is None:
            if _WILDCARDS & set(value):
                raise PartitionRefusal("ERR_PARTITION_PATTERN", value=value)
            partitions.append(value)
            continue
        if _WILDCARDS & set(parent):
            raise PartitionRefusal("ERR_PARTITION_PATTERN", value=value)
        if list_children is None:
            raise PartitionRefusal("ERR_PARTITION_UNRESOLVED", value=value)
        children = [child for child in list_children(parent)
                    if isinstance(child, str) and child.strip()]
        if not children:
            raise PartitionRefusal("ERR_PARTITION_EMPTY", parent=parent)
        expanded.append((parent, len(children)))
        partitions.extend(children)

    unique: dict[tuple[str, ...], str] = {}
    for partition in partitions:
        unique.setdefault(_key(partition), partition)
    if len(unique) > MAX_PARTITIONS:
        raise PartitionRefusal(
            "ERR_PARTITION_TOO_MANY", count=len(unique), cap=MAX_PARTITIONS)
    # One partition inside another would be worked on twice and counted
    # twice. Sorted keys put an ancestor right before its descendants.
    ordered = sorted(unique)
    for position, key in enumerate(ordered[:-1]):
        following = ordered[position + 1]
        if following[:len(key)] == key:
            raise PartitionRefusal(
                "ERR_PARTITION_OVERLAP",
                outer=unique[key], inner=unique[following])
    return Resolution(
        partitions=tuple(unique.values()),
        base_args={name: value for name, value in args.items()
                   if name not in (arg, list_arg)},
        ignored_single=arg in args,
        expanded=tuple(expanded),
    )


# Which catalog tool lists the partitions under a parent, per kind: its name,
# the argument that takes the parent, the fixed arguments (immediate children,
# all of them) and the values of ``type`` that mark a partition. A child that
# is a link may be a folder: the listing does not say what it points to, so it
# is worked like the others, and the tool says so itself if it is not one (the
# partition is then named with that reason, never left out in silence).
_LISTING = {
    "directory": ("list_dirs", "path", {"recursive": False, "max_results": 0},
                  ("dir", "symlink")),
}


def children_lister(policy: Mapping[str, Any],
                    call_tool: Callable[[str, dict], object],
                    *, excluded_files=None,
                    ) -> Callable[[str], list[str]]:
    """The ``list_children`` of ``resolve``, served by a catalog tool.

    ``call_tool(name, arguments)`` is an ordinary call of that tool, where the
    files are and under its own guard and sandbox: this module reads nothing
    itself. A listing that failed, came back cut or could not read some of the
    children is a refusal, never a shorter list.
    """
    tool, parent_arg, fixed, markers = _LISTING[policy["kind"]]

    def list_children(parent: str) -> list[str]:
        answer = call_tool(tool, {parent_arg: parent, **fixed})
        answer = answer if isinstance(answer, Mapping) else {}
        rows = answer.get("entries")
        if answer.get("ok") is not True or not isinstance(rows, list):
            reason = str(answer.get("error") or "").strip()
            if reason:
                raise PartitionRefusal(
                    "ERR_PARTITION_LIST", parent=parent, reason=reason)
            raise PartitionRefusal("ERR_PARTITION_LIST_FAILED", parent=parent)
        details = answer.get("metadata")
        cut = answer.get("truncated") is True or (
            isinstance(details, Mapping) and details.get("truncated") is True)
        if cut:
            raise PartitionRefusal("ERR_PARTITION_LIST_CUT", parent=parent)
        unread = answer.get("failed")
        if (isinstance(unread, list) and unread) or answer.get("partial") is True or (
                isinstance(details, Mapping) and details.get("source_complete") is False):
            # Children the listing could not read would be folders nobody
            # works on and nobody names.
            raise PartitionRefusal("ERR_PARTITION_LIST_INCOMPLETE", parent=parent)
        if excluded_files is not None:
            excluded_files[parent] = sum(isinstance(row, Mapping) and row.get("type") == "file" for row in rows)
        return [row["path"] for row in rows
                if isinstance(row, Mapping) and row.get("type") in markers
                and isinstance(row.get("path"), str)]

    return list_children


def unit_args(policy: Mapping[str, Any], base_args: Mapping[str, Any],
              partition: str) -> dict:
    """The arguments of the ordinary call that serves one partition."""
    return {**base_args, policy["arg"]: partition}


# -------------------------------------------------------------------- result

@dataclass(frozen=True)
class _Part:
    summary: dict
    rows_key: str | None
    rows: tuple
    failed: tuple
    roles: object = None


def _whole_number(value: object) -> bool:
    return type(value) is int


def _part(policy: Mapping[str, Any], partition: str, observation: object) -> _Part:
    """One partition as its tool answered it.

    Three outcomes, kept apart (§2.8): it worked; it worked and lost some
    items, which it names; it did not work. Only the last has no rows, and
    it is then one item lost by the whole call, with the cause the tool gave.
    """
    answer = observation if isinstance(observation, Mapping) else {}
    rows_key = next((key for key in _ROW_KEYS
                     if isinstance(answer.get(key), list)), None)
    own_error = any(answer.get(key) not in (None, "") for key in _ERROR_KEYS)
    lost = [item for item in (answer.get("failed") or ())
            if isinstance(item, Mapping)] if isinstance(
                answer.get("failed"), list) else []
    worked = answer.get("ok") is True or (
        answer.get("ok") is False and not own_error and bool(lost))

    summary: dict[str, Any] = {PARTITION_FIELD: partition, "ok": worked}
    context = answer.get("_filesystem")
    if isinstance(context, Mapping):
        from filesystem_permissions import served_operation
        summary["operation"] = served_operation(context["manifest"], context["args"])
    summary.update({key: answer[key] for key in _ERROR_KEYS if answer.get(key) not in (None, "")})
    if _whole_number(answer.get("ok_count")):
        summary["ok"] = answer.get("ok") is True and not lost and not answer.get("fail_count")
    for name in policy.get("sums") or ():
        if _whole_number(answer.get(name)):
            summary[name] = answer[name]
    if not worked and rows_key is None:
        summary["count"] = 0
        return _Part(summary, None, (), ({PARTITION_FIELD: partition, "path": partition,
                     **{key: summary[key] for key in _ERROR_KEYS if key in summary}},))

    rows = tuple(
        {**row, PARTITION_FIELD: partition} if isinstance(row, Mapping)
        else {"value": row, PARTITION_FIELD: partition}
        for row in (answer.get(rows_key) if rows_key else ()))
    summary["count"] = len(rows)
    if lost:
        summary.setdefault("fail_count", len(lost))
    for key in _TRUNCATION_KEYS:
        if key in answer:
            summary[key] = answer[key]
    return _Part(
        summary, rows_key, rows,
        tuple({**item, PARTITION_FIELD: partition} for item in lost),
        answer.get("entry_field_roles"))


def _finish(policy: Mapping[str, Any], parts: Sequence[_Part]) -> dict:
    rows_key = next((part.rows_key for part in parts if part.rows_key), "entries")
    rows = [row for part in parts for row in part.rows]
    failed = [item for part in parts for item in part.failed]
    summaries = [dict(part.summary) for part in parts]
    worked = [summary for summary in summaries if summary["ok"]]
    out: dict[str, Any] = {
        # A call that produced something did not fail: what was lost is
        # declared beside it. With nothing produced it is a failed call whose
        # lost items are the partitions; no partition at all is not a result.
        "ok": bool(worked),
        "ok_count": len(rows),
        "fail_count": len(failed),
        rows_key: rows,
        "failed": failed,
        "partitions": summaries,
        "partition_arg": policy["arg"],
        "partition_union": policy.get("union") or "separate",
        "partitions_total": len(summaries),
        "partitions_ok": len(worked),
        "partitions_failed": len(summaries) - len(worked),
    }
    if any("ok_count" in summary for summary in summaries):
        out["ok"] = bool(summaries) and all(summary["ok"] for summary in summaries) and not failed
        out.update(next(({key: summary[key] for key in _ERROR_KEYS if key in summary}
                         for summary in summaries if any(key in summary for key in _ERROR_KEYS)), {}))
    if rows and failed:
        out["partial"] = True

    cut = [summary for summary in worked if summary.get("truncated") is True]
    if cut:
        out["truncated"] = True
        out["truncated_partitions"] = len(cut)
        for key in ("truncated_what", "cap_field", "cap_value"):
            values = {repr(summary.get(key)) for summary in cut}
            if len(values) == 1 and cut[0].get(key) is not None:
                out[key] = cut[0][key]
        out["used"] = sum(
            summary["used"] if _whole_number(summary.get("used"))
            else summary["count"] for summary in worked)
        if all(_whole_number(summary.get("available_total")) for summary in cut):
            # What is there in all: every cut partition knows its own total,
            # the others gave everything they had.
            out["available_total"] = sum(
                summary["available_total"] if summary.get("truncated") is True
                else summary["count"] for summary in worked)
        if all(summary.get("truncated_intentional") is True for summary in cut):
            out["truncated_intentional"] = True

    for name in policy.get("sums") or ():
        values = [summary[name] for summary in summaries
                  if _whole_number(summary.get(name))]
        if values:
            out[name] = sum(values)
    # A sum over fewer partitions than asked, or over cut ones, is a lower
    # bound: it is never shown as the total.
    out["totals_complete"] = not cut and (not failed or all(
        part.rows_key is not None and not part.summary.get("truncated")
        and part.summary.get("ok_count") == len(part.rows)
        and part.summary.get("fail_count") == len(part.failed)
        and not any(part.summary.get(key) for key in _ERROR_KEYS)
        for part in parts))

    roles = [part.roles for part in parts if part.roles is not None]
    if roles and all(role == roles[0] for role in roles):
        out["entry_field_roles"] = roles[0]
    return out


def assemble(policy: Mapping[str, Any],
             outcomes: Sequence[tuple[str, object]]) -> dict:
    """The one result of a partitioned call, from ``(partition, answer)``
    pairs in the order to show. Pure: the same pairs give the same result."""
    return _finish(policy, [_part(policy, partition, answer)
                            for partition, answer in outcomes])


def combine(policy: Mapping[str, Any], results: Sequence[Mapping[str, Any]], *,
            order: Sequence[str] = (),
            sizes: Mapping[str, int] | None = None,
            unanswered: Mapping[str, Any] | None = None) -> dict:
    """Join results already in this shape, as the units of a long job return.

    Associative with ``assemble``: joining the results of single partitions
    gives what assembling all of them at once would. ``order`` is the list
    the job was admitted with; the store returns its units in no order.
    With ``unanswered`` (the cause to give), a partition of ``order`` that no
    result speaks of becomes a failed partition instead of an absent one.
    """
    parts: dict[str, _Part] = {}
    for result in results:
        if not isinstance(result, Mapping):
            continue
        rows_key = next((key for key in _ROW_KEYS
                         if isinstance(result.get(key), list)), None)
        rows_by: dict[str, list] = {}
        for row in (result.get(rows_key) if rows_key else ()):
            if isinstance(row, Mapping) and isinstance(row.get(PARTITION_FIELD), str):
                rows_by.setdefault(row[PARTITION_FIELD], []).append(dict(row))
        failed_by: dict[str, list] = {}
        for item in (result.get("failed") or ()):
            if isinstance(item, Mapping) and isinstance(item.get(PARTITION_FIELD), str):
                failed_by.setdefault(item[PARTITION_FIELD], []).append(dict(item))
        for summary in (result.get("partitions") or ()):
            if not isinstance(summary, Mapping):
                continue
            partition = summary.get(PARTITION_FIELD)
            if not isinstance(partition, str) or partition in parts:
                continue
            parts[partition] = _Part(
                dict(summary), rows_key,
                tuple(rows_by.get(partition, ())),
                tuple(failed_by.get(partition, ())),
                result.get("entry_field_roles"))
    if unanswered is not None:
        for partition in order:
            if partition not in parts:
                parts[partition] = _part(policy, partition, {"ok": False, **unanswered,
                    **({"ok_count": 0, "fail_count": sizes[partition]} if sizes and partition in sizes else {})})
    wanted = [partition for partition in order if partition in parts]
    wanted += [partition for partition in parts if partition not in wanted]
    return _finish(policy, [parts[partition] for partition in wanted])


def missing_partitions(result: Mapping[str, Any], order: Sequence[str]) -> list[str]:
    """Partitions a job was admitted with and no unit answered for."""
    seen = {summary.get(PARTITION_FIELD)
            for summary in (result.get("partitions") or ())
            if isinstance(summary, Mapping)}
    return [partition for partition in order if partition not in seen]


# ------------------------------------------------------- words for a person

def _display_parts(partition: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", partition) if part]


def labels(partitions: Sequence[str]) -> dict[str, str]:
    """What to call each partition: its path without what all of them share.

    Thirty-six folders of one archive are told apart by their last names; a
    single partition keeps only its last name."""
    unique = list(dict.fromkeys(partitions))
    parts = {partition: _display_parts(partition) for partition in unique}
    shared = max(0, len(parts[unique[0]]) - 1) if len(unique) == 1 else 0
    if len(unique) > 1:
        shortest = min(len(item) for item in parts.values())
        # The last name always stays: it is what tells one from the other.
        while shared < shortest - 1 and len(
                {item[shared] for item in parts.values()}) == 1:
            shared += 1
    return {partition: "/".join(parts[partition][shared:]) or partition
            if shared else partition for partition in unique}


# Causes that belong to a partition and to no single item: a child that is not
# what the tool works on, a partition a finished job has no result for.
_CAUSE_BY_CODE = {
    "ERR_PARTITION_UNANSWERED": "MSG_PARTITION_CAUSE_UNANSWERED",
    "ERR_PARTITION_RESULT_TOO_LARGE": "MSG_PARTITION_CAUSE_TOO_LARGE",
}
_CAUSE_BY_CLASS = {"wrong_type": "MSG_PARTITION_CAUSE_WRONG_TYPE"}


def _cause_words(error_class: object, error_code: object = None) -> str:
    from durable_workloads.domain_outcome import ITEM_FAILED, tool_cause
    from messages import get as message

    own = _CAUSE_BY_CODE.get(error_code) or _CAUSE_BY_CLASS.get(error_class)
    if own:
        return message(own)
    return message("UI_DURABLE_CAUSE_" + (tool_cause(error_class) or ITEM_FAILED).upper())


def is_partitioned(result: object) -> bool:
    """True for a result in the one partitioned shape."""
    return (isinstance(result, Mapping)
            and isinstance(result.get("partitions"), list)
            and result.get("partition_union") in UNIONS)


def notice(result: object) -> str:
    """What stands beside the answer of a partitioned step: that each
    partition was worked on its own when that is so, and one line for each
    partition with what was found there and how it ended.

    Empty for any other result. Only counts and outcomes: the rows themselves
    are the answer, shown or delivered as every answer is."""
    if not isinstance(result, Mapping):
        return ""
    lines = [line for line in (result.get("partitions") or ())
             if isinstance(line, Mapping)
             and isinstance(line.get(PARTITION_FIELD), str)]
    if not lines or result.get("partition_union") not in UNIONS:
        return ""
    from messages import get as message

    names = labels([line[PARTITION_FIELD] for line in lines])
    lines.sort(key=lambda line: names[line[PARTITION_FIELD]].casefold())
    moving = all(line.get("operation") == "move" for line in lines)

    def outcome(line: Mapping[str, Any]) -> str:
        if line.get("ok") is not True:
            return message("MSG_PARTITION_FAILED",
                           reason=_cause_words(line.get("error_class"),
                                               line.get("error_code")))
        if line.get("truncated") is True:
            used = line["used"] if _whole_number(line.get("used")) else line.get("count", 0)
            if _whole_number(line.get("available_total")):
                return message("MSG_PARTITION_CUT", used=used,
                               total=line["available_total"])
            return message("MSG_PARTITION_CUT_UNKNOWN", used=used)
        if _whole_number(line.get("fail_count")) and line["fail_count"]:
            return message("MSG_PARTITION_DONE_LOST", n=line["fail_count"])
        return message("MSG_PARTITION_DONE")

    def cell(text: object) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ")

    table = ["| %s | %s | %s |" % (
        message("MSG_PARTITION_COL_NAME"), message("MSG_PARTITION_COL_MOVED" if moving else "MSG_PARTITION_COL_COUNT"),
        message("MSG_PARTITION_COL_NOT_MOVED" if moving else "MSG_PARTITION_COL_OUTCOME")), "|---|---|---|"]
    table += ["| %s | %s | %s |" % (
        cell(names[line[PARTITION_FIELD]]),
        line.get("count", 0) if line.get("ok") is True else "–",
        line.get("fail_count", 0) if moving else cell(outcome(line))) for line in lines]
    table += [message("MSG_PARTITION_PARENT_ROW", name=cell(labels([parent])[parent]), n=count)
              for parent, count in sorted((result.get("excluded_files") or {}).items())]
    table.append("| %s | %s | %s |" % (message("MSG_PARTITION_TOTAL"), sum(line.get("count", 0) for line in lines),
                 sum(line.get("fail_count", 0) for line in lines) + sum((result.get("excluded_files") or {}).values()) if moving else ""))
    head = message(
        "MSG_PARTITION_SEPARATE" if result["partition_union"] == "separate"
        else "MSG_PARTITION_EXACT", total=len(lines))
    parts = [head, "\n".join(table)]
    if result.get("totals_complete") is False:
        parts.append(message("MSG_PARTITION_TOTALS_MINIMUM"))
    return "\n\n".join(parts)


def notices(results: Sequence[object]) -> list[str]:
    """Join complementary columns only for the same complete partition identities."""
    groups = []
    for result in results:
        if not (text := notice(result)):
            continue
        parts = text.split("\n\n")
        lines = [line for line in result["partitions"] if isinstance(line, Mapping) and isinstance(line.get(PARTITION_FIELD), str)]
        names = labels([line[PARTITION_FIELD] for line in lines])
        keys = sorted(names, key=lambda key: names[key].casefold()) + [("parent", p) for p in sorted(result.get("excluded_files") or {})] + [None]
        failed = {line[PARTITION_FIELD] for line in lines if line.get("ok") is not True}
        rows = [re.split(r"(?<!\\)\|", row)[1:-1] for row in parts[1].splitlines()]
        for old_identity, old_keys, old_parts, old_rows, unavailable in groups:
            if frozenset(names) != old_identity or set(rows[0][1:]) & set(old_rows[0][1:]):
                continue
            extra, width = dict(zip(keys, rows[2:])), len(rows[0]) - 1
            for key, row in zip(old_keys, old_rows[2:]):
                row.extend(extra[key][1:] if key in extra and key not in unavailable else [" – "] * width)
            for key in (key for key in keys if key not in old_keys):
                old_keys.insert(-1, key)
                old_rows.insert(-1, [extra[key][0]] + [" – "] * (len(old_rows[0]) - 1) + extra[key][1:])
            old_rows[0].extend(rows[0][1:])
            old_rows[1].extend(rows[1][1:])
            old_parts.extend(part for part in [parts[0], *parts[2:]] if part not in old_parts)
            unavailable.update(failed)
            break
        else:
            groups.append([frozenset(names), keys, parts, rows, failed])
    return ["\n\n".join([parts[0], "\n".join("|" + "|".join(row) + "|" for row in rows), *parts[2:]])
            for _, _, parts, rows, _ in groups]


def with_partial_reason(result: dict) -> dict:
    """Say in words why a partitioned result is partial, where every step
    that reads it looks for the reason (`partial_reason`): a sheet written
    from these rows notes it, the answer shows it once."""
    if result.get("partial") is not True or result.get("partial_reason"):
        return result
    from messages import get as message

    lines = [line for line in (result.get("partitions") or ())
             if isinstance(line, Mapping)]
    names = labels([line[PARTITION_FIELD] for line in lines
                    if isinstance(line.get(PARTITION_FIELD), str)])
    failed = [line for line in lines if line.get("ok") is not True]
    words = []
    if failed:
        words.append(message(
            "MSG_PARTITION_PARTIAL_FOLDERS", failed=len(failed), total=len(lines),
            names=", ".join(
                f"{names.get(line.get(PARTITION_FIELD), '?')} "
                f"({_cause_words(line.get('error_class'), line.get('error_code'))})"
                for line in failed[:8])))
    lost = sum(line["fail_count"] for line in lines
               if line.get("ok") is True and _whole_number(line.get("fail_count")))
    if lost:
        words.append(message("MSG_PARTITION_PARTIAL_ITEMS", n=lost))
    if words and not any("<missing:" in text for text in words):
        result["partial_reason"] = " ".join(words)
    return result


def counts_text(rows: object, *, max_chars: int | None = None, field_roles: object = None, excluded_files=None, refused_files=None) -> str:
    """How many of these rows belong to each partition, in the existing table.

    For a question that must say where something is about to happen (a
    consent before moving files folder by folder). Empty when the rows do not
    carry a partition. A budget keeps whole folders in name order,
    accounting for both folders and copies omitted from the text."""
    counts: dict[str, int] = {}
    for row in rows if isinstance(rows, (list, tuple)) else ():
        if isinstance(row, Mapping) and isinstance(row.get(PARTITION_FIELD), str):
            counts[row[PARTITION_FIELD]] = counts.get(row[PARTITION_FIELD], 0) + 1
    if not counts:
        return ""
    from messages import get as message

    names = labels(list(counts))
    counts = dict(sorted(counts.items(), key=lambda item: names[item[0]].casefold()))
    tokens = ["| %s | %s | %s |" % (names[partition].replace("|", "\\|").replace("\n", " "), count, (refused_files or {}).get(partition, 0))
              for partition, count in counts.items()]
    parents = [message("MSG_PARTITION_PARENT_ROW", name=labels([parent])[parent].replace("|", "\\|").replace("\n", " "), n=count)
               for parent, count in sorted((excluded_files or {}).items())]
    def table(tokens):
        return message("MSG_PARTITION_COUNTS", items="\n".join(parents + tokens), total=sum(counts.values()),
                       excluded=sum((excluded_files or {}).values()) + sum((refused_files or {}).values()))
    full = table(tokens)
    if max_chars is None or len(full) <= max_chars:
        return full
    roles = [set(item["roles"]) for item in field_roles if isinstance(item, Mapping)
             and isinstance(item.get("roles"), (list, tuple))
             and all(isinstance(role, str) for role in item["roles"])] \
        if isinstance(field_roles, (list, tuple)) else []
    copies = (any({"path", "duplicate"} <= role for role in roles)
              and any({"path", "origin"} <= role for role in roles))
    closing_key = "MSG_PARTITION_COUNTS_OMITTED_COPIES" if copies else "MSG_PARTITION_COUNTS_OMITTED"
    quantities = list(counts.values())
    omitted_copies = 0
    for shown in range(len(tokens) - 1, -1, -1):
        omitted_copies += quantities[shown]
        closing = message(closing_key, folders=len(tokens) - shown, items=omitted_copies)
        candidate = table(tokens[:shown]) + "\n\n" + closing if shown or parents else closing
        if len(candidate) <= max_chars:
            return candidate
    raise ValueError("The folder-count budget cannot hold even the omission totals")


# ----------------------------------------------------------------------- run

def refusal_result(refusal: PartitionRefusal) -> dict:
    """A refusal as the result of the step: nothing ran."""
    from messages import get as message

    return {
        "ok": False,
        "error_class": "invalid_args",
        "error_code": refusal.code,
        "error": message(refusal.code, **refusal.values),
    }


def _first_level_partition(root: str, path: object) -> str | None:
    """The first child containing a returned path, without filesystem access.

    A file directly in the root belongs to the root. A path outside it, or
    one the tool did not name, cannot be assigned a destination by guessing.
    """
    if not isinstance(path, str) or not path or _flavour(path) is not _flavour(root):
        return None
    flavour = _flavour(root)
    root = flavour.normpath(root)
    path = flavour.normpath(path)
    if flavour.isabs(path) != flavour.isabs(root):
        return None
    root_key, path_key = _key(root), _key(path)
    if (len(path_key) <= len(root_key)
            or path_key[:len(root_key)] != root_key):
        return None
    try:
        relative = flavour.relpath(path, root)
    except ValueError:
        return None
    pieces = relative.split(flavour.sep)
    if pieces[0] == flavour.pardir:
        return None
    return root if len(pieces) == 1 else flavour.join(root, pieces[0])


def _project_first_level(policy: Mapping[str, Any], args: Mapping[str, Any],
                         observation: dict) -> dict:
    """Group one global answer, keeping its scope, counters and evidence.

    ``exact`` here describes a projection of the same global answer: no
    independent invocation or local counter is claimed. Global truncation
    cannot tell how many missing rows belong to any child, so group counts
    are lower bounds without invented group totals.
    """
    if not isinstance(observation, Mapping):
        return observation
    rows_key = next((key for key in _ROW_KEYS
                     if isinstance(observation.get(key), list)), None)
    failed = observation.get("failed", [])
    worked = observation.get("ok") is True or (
        observation.get("ok") is False and bool(failed)
        and not any(observation.get(key) not in (None, "") for key in _ERROR_KEYS))
    if not worked or rows_key is None or not isinstance(failed, list):
        return observation

    metadata = observation.get("metadata")
    resolved = metadata.get(policy["arg"]) if isinstance(metadata, Mapping) else None
    root = resolved if isinstance(resolved, str) and resolved else args[policy["arg"]]
    root = _flavour(root).normpath(root)
    groups: dict[str, dict] = {}
    projected: dict[str, list] = {}
    for key, items in ((rows_key, observation[rows_key]), ("failed", failed)):
        projected[key] = []
        for row in items:
            partition = (_first_level_partition(root, row.get("path"))
                         if isinstance(row, Mapping) else None)
            if partition is None:
                # Preserve the complete evidence when its contract does not
                # provide enough information for a truthful projection.
                return observation
            summary = groups.setdefault(partition, {
                PARTITION_FIELD: partition, "ok": True, "count": 0})
            counter = "fail_count" if key == "failed" else "count"
            summary[counter] = summary.get(counter, 0) + 1
            projected[key].append({**row, PARTITION_FIELD: partition})

    cut = any(observation.get(key) is True for key in (
        "truncated", "scan_truncated", "results_truncated"))
    if cut:
        for summary in groups.values():
            summary.update(truncated=True, used=summary["count"])
            for key in ("truncated_what", "cap_field", "cap_value", "truncated_intentional"):
                if key in observation:
                    summary[key] = observation[key]
    return {
        **observation, **projected,
        "partitions": list(groups.values()),
        "partition_arg": policy["arg"],
        "partition_union": "exact",
        "partitions_total": len(groups),
        "partitions_ok": len(groups),
        "partitions_failed": 0,
        "totals_complete": (
            not failed and not cut and observation.get("partial") is not True
            and observation.get("source_complete") is not False
            and observation.get("totals_complete") is not False),
    }


def _single_directory(policy: Mapping[str, Any], args: Mapping[str, Any],
                      observation: dict) -> dict:
    """Label rows with their declared root without aggregating one answer."""
    if not isinstance(observation, Mapping):
        return observation
    worked = observation.get("ok") is True or (
        observation.get("ok") is False and bool(observation.get("failed"))
        and not any(observation.get(key) not in (None, "") for key in _ERROR_KEYS))
    metadata = observation.get("metadata")
    resolved = metadata.get(policy["arg"]) if isinstance(metadata, Mapping) else None
    root = resolved or args.get(policy["arg"])
    if (not worked or not isinstance(root, str) or not root.strip()
            or "${" in root or _WILDCARDS.intersection(root) or "\0" in root):
        return observation
    root = _flavour(root).normpath(root)
    return {**observation, **{
        key: [{**row, PARTITION_FIELD: root} if isinstance(row, Mapping) else row
              for row in observation[key]]
        for key in (*_ROW_KEYS, "failed") if isinstance(observation.get(key), list)}}


def run(executor: object, args: object, invoke: Callable[[dict], dict], *,
        list_children: Callable[[str], Sequence[str]] | None = None) -> dict:
    """Serve one call of a tool, partition by partition when it asks for it.

    ``invoke(arguments)`` is the ordinary call of the tool, with its own
    sandbox, guard and time limit; here it is made once per partition, one
    after the other. A single directory labels its rows with that root. A single
    directory call with ``group_by="first_level"`` consumes that argument
    here and groups one global answer, without partitioning the invocation.
    """
    policy = policy_of(executor)
    if policy is None or policy["kind"] == "independent_rows" or not isinstance(args, Mapping):
        return invoke(args)
    try:
        resolution = resolve(policy, args, list_children=list_children)
    except PartitionRefusal as refusal:
        return refusal_result(refusal)
    if resolution is None:
        if (policy["kind"] == "directory" and policy["union"] == "separate"
                and args.get("group_by") == "first_level"
                and isinstance(args.get(policy["arg"]), str)
                and args[policy["arg"]].strip()):
            ordinary_args = {key: value for key, value in args.items()
                             if key != "group_by"}
            return with_partial_reason(_project_first_level(
                policy, ordinary_args, invoke(ordinary_args)))
        return _single_directory(policy, args, invoke(args))
    return with_partial_reason(assemble(policy, [
        (partition, invoke(unit_args(policy, resolution.base_args, partition)))
        for partition in resolution.partitions]))


__all__ = [
    "KINDS", "MAX_PARTITIONS", "PARTITION_FIELD", "UNIONS",
    "PartitionRefusal", "Resolution", "assemble", "children_lister", "combine",
    "counts_text", "is_partitioned", "labels", "notice", "with_partial_reason",
    "missing_partitions", "normalized_partition", "policy_of",
    "refusal_result", "resolve", "run", "unit_args", "validate_partition",
]
