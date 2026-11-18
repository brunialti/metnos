"""Preserve the comparison universe while grouping actions by folder.

A directory partition is an execution strategy. With ``union=exact`` the
manifest declares that independent calls preserve the result, so a listing
of immediate children becomes the literal ``parent/*`` runtime input.
With ``union=separate`` that is not equivalent: compare the entire parent
once, including its direct files, then project rows by first-level folder.
Only an asserted restriction from the reviewed recognition lexicon selects
independent comparisons. Uncertainty retains the whole comparison.

Rows carry their own folder in ``partition``. Downstream actions use it,
create destinations only for returned rows, and retain relative subfolders
when moving namesakes. No executor name selects this behavior. The rewrite
is idempotent and leaves selective folder listings intact (ADR 0244).
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import replace
from typing import Optional

import partitioned_runs
import detection_lexicon as _lexicon
import detection_lexicon_seed_parsers as _parser_lexicons

from .types import Framework, StepSpec

_LISTING_VERBS = frozenset({"list", "find"})
_FOLDER_OBJECT = "dirs"
# The object of the tools that work on rows already in hand (`vocab`).
_ROWS_OBJECT = "entries"
_PARENT_ARGS = ("path", "base_path")
# How deep a listing goes: with `recursive` false it lists one level whatever
# the depth says.
_LEVEL_ARGS = ("recursive", "max_depth")
_ROWS_PATHS = r"\$\{\s*step(\d+)\.(?:entries|results)\.\*\.path\s*\}"
_ONLY_ROWS_PATHS = re.compile(r"^" + _ROWS_PATHS + r"(?:[\\/]\*)?$")
_ROWS_PATHS_AND_TAIL = re.compile(r"^" + _ROWS_PATHS + r"([\\/][^*?\[\]${}]+)$")
_ROWS_PATHS_ANYWHERE = re.compile(_ROWS_PATHS)
_ROW_TEMPLATES = ("dst_template", "path_template")
# A source leaf under its own partition keeps its relative subtree, whether
# the planner writes the source name or its absolute path.
_INSIDE_PARTITION_SOURCE = re.compile(r"^(.*\{partition\}.*[\\/])\{(?:name|path)\}$")
_DESTINATION_REFERENCE = re.compile(
    r"^\$\{\s*step(\d+)\.(entries|results)(\.\*)?\.(path|partition)\s*\}([\\/].*)?$")
_DIAGNOSTIC_NAME = r"[A-Za-z_][A-Za-z0-9_]{0,63}"
_DIAGNOSTIC_REFERENCE = re.compile(
    r"\$\{\s*step([0-9]{1,6})\.(" + _DIAGNOSTIC_NAME + r")([^{}]{0,256})\}")


class PartitionDestinationError(ValueError):
    """A row destination cannot be proved; reject the plan before execution."""

    def __init__(self, step: int, *, steps=(), argument=None, arguments=None,
                 template=None, by_name=None):
        self.step = step
        self.reason = "unproven_destination"
        self.diagnostic = _destination_diagnostic(
            steps, step, argument, arguments, template, by_name)
        # Private repair input only. The public diagnostic deliberately keeps
        # values out of logs; this snapshot identifies the pre-normalized
        # argument even when earlier structure guards have reordered steps.
        self.repair_steps = deepcopy(steps)
        super().__init__(f"{self.reason}: step{step}")


def _diagnostic_name(value: object) -> Optional[str]:
    return value if isinstance(value, str) and re.fullmatch(_DIAGNOSTIC_NAME, value) else None


def _diagnostic_step(value: object) -> Optional[int]:
    return value if type(value) is int and 0 < value <= 999999 else None


def _destination_diagnostic(steps, index, argument, arguments, template, by_name):
    """Copy bounded declarations from the local plan, never argument values.

    Normalization can change sources before a refusal. The caller supplies
    that local view instead of reconstructing it from the original framework.
    Selector values and literal destination text are deliberately discarded.
    """
    number = _diagnostic_step(index)
    consumer = steps[number - 1] if number and number <= len(steps) else None
    args = arguments if isinstance(arguments, dict) else getattr(consumer, "args", {})
    args = args if isinstance(args, dict) else {}
    source = _diagnostic_step(args.get("from_step"))

    def producer_metadata(position):
        producer = steps[position - 1] if position and position <= len(steps) else None
        name = _diagnostic_name(getattr(producer, "tool", None))
        declaration = by_name.get(name) if name and isinstance(by_name, dict) else None
        producer_args = getattr(producer, "args", {})
        producer_args = producer_args if isinstance(producer_args, dict) else {}
        return {
            "tool": name, "from_step": _diagnostic_step(producer_args.get("from_step")),
            # Declaring a row destination does not prove this reference valid.
            "declares_row_destination": (
                {"from_step", "path_template"} <= _properties(declaration).keys()
                if declaration is not None else None),
        }

    diagnostic = {
        "kind": "partition_destination", "step": number,
        "tool": _diagnostic_name(getattr(consumer, "tool", None)),
        "argument": _diagnostic_name(argument), "from_step": source,
        "row_source_tool": producer_metadata(source)["tool"],
        "reason": "unproven_destination", "references": [], "row_fields": [],
    }
    if template is None and isinstance(argument, str):
        template = args.get(argument)
    if isinstance(template, str):
        for match in _DIAGNOSTIC_REFERENCE.finditer(template[:8192]):
            step, container, suffix = match.groups()
            selection, field = "unknown", None
            for mode, pattern in (
                ("all", r"\.\*\.(" + _DIAGNOSTIC_NAME + r")"),
                ("index", r"(?:\.[0-9]{1,9}|\[[^\]\r\n]{1,64}\])\.(" + _DIAGNOSTIC_NAME + r")"),
                ("single", r"\.(" + _DIAGNOSTIC_NAME + r")"),
            ):
                found = re.fullmatch(pattern, suffix.strip())
                if found:
                    selection, field = mode, found.group(1)
                    break
            reference_number = _diagnostic_step(int(step))
            diagnostic["references"].append({
                "step": reference_number, "container": container,
                "selection": selection, "field": field,
                **producer_metadata(reference_number),
            })
            if len(diagnostic["references"]) == 4:
                break
    if source and source <= len(steps) and isinstance(by_name, dict):
        declaration = by_name.get(steps[source - 1].tool)
        schema = getattr(declaration, "output_schema", None)
        if isinstance(schema, str) and len(schema) <= 16384:
            from executor_metadata import output_record
            _, fields = output_record(schema)
            diagnostic["row_fields"] = [name for name in fields
                                        if _diagnostic_name(name)][:20]
    return diagnostic


def _properties(executor: object) -> dict:
    schema = getattr(executor, "args_schema", None)
    properties = schema.get("properties") if isinstance(schema, dict) else None
    return properties if isinstance(properties, dict) else {}


def _parent_of_children(text: str) -> Optional[str]:
    """`<parent>/*` gives `<parent>`; None when the text is not that form."""
    for tail in ("/*", "\\*"):
        if text.endswith(tail):
            return text[:-len(tail)]
    return None


def _a_parent(text: str) -> Optional[str]:
    """A folder named without patterns, without its trailing separators."""
    text = text.rstrip("/\\") or text
    return text if text and not set("*?[") & set(text) else None


def _every_child_of(value: object) -> Optional[str]:
    """The parent whose children this literal value names, or None."""
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, str) or "${" in value:
        return None
    parent = _parent_of_children(value.strip())
    return _a_parent(parent) if parent else None


def _immediate_folders_of(step: StepSpec, executor: object) -> Optional[str]:
    """The parent whose immediate folders this step lists, or None."""
    name = str(step.tool or "").split("_")
    if (len(name) != 2 or name[0] not in _LISTING_VERBS
            or name[1] != _FOLDER_OBJECT or executor is None):
        return None
    args = step.args if isinstance(step.args, dict) else {}
    recursive = args.get("recursive")
    if recursive is None:
        recursive = (_properties(executor).get("recursive") or {}).get("default")
    if recursive is not False:
        return None
    # A limit, an order, hidden folders, another machine: then the step lists
    # some folders of the parent, or other ones, and «every child» would be
    # more than the plan asked for («the 3 most recent folders of X» became
    # all of them: review of 16/11/2026). Only what the tool does by default.
    properties = _properties(executor)
    for key, value in args.items():
        spec = properties.get(key) if isinstance(properties.get(key), dict) else {}
        if (key in _PARENT_ARGS or key in _LEVEL_ARGS or key.startswith("_")
                or spec.get("runtime_resolved") is True
                or ("default" in spec and value == spec["default"])):
            continue
        return None
    parent = next((args[key] for key in _PARENT_ARGS
                   if isinstance(args.get(key), str) and args[key].strip()), None)
    if parent is None or "${" in parent:
        return None
    # The planner sometimes writes the children of the parent as the parent.
    children = _parent_of_children(parent)
    return _a_parent(parent if children is None else children)


def _listed_by(value: object) -> Optional[int]:
    """The step whose rows this value hands over as folders, or None."""
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    match = _ONLY_ROWS_PATHS.match(value.strip()) if isinstance(value, str) else None
    return int(match.group(1)) if match else None


def _destination_row_source(steps: list[StepSpec], index: int, listing: int,
                            template: str, partitioned: dict[int, Optional[int]],
                            comparison_rows: set[int], *, by_name=None,
                            arguments=None) -> Optional[int]:
    """Use the same rows as the action that writes into this destination.

    A filter may remove every copy from a folder; an unrelated later filter
    may serve only a spreadsheet. Neither the original comparison nor the
    most recent filter necessarily describes the folders to create.
    """
    destination = template.replace("{path}", "{partition}").replace("\\", "/").rstrip("/")
    sources: set[int] = set()
    for step in steps:
        args = step.args if isinstance(step.args, dict) else {}
        source, target = args.get("from_step"), args.get("dst_template")
        if (not isinstance(source, int) or not 0 < source < index
                or source not in comparison_rows or partitioned.get(source) != listing
                or not isinstance(target, str)):
            continue
        target = _ROWS_PATHS_ANYWHERE.sub(
            lambda found: "{partition}" if int(found.group(1)) == listing
            else found.group(0), target).replace("\\", "/")
        reference = _DESTINATION_REFERENCE.fullmatch(target)
        if (target.startswith(destination + "/")
                or (reference and int(reference.group(1)) == index
                    and reference.group(2, 3, 4) == ("results", ".*", "path"))):
            sources.add(source)
    if len(sources) > 1:
        raise PartitionDestinationError(index, steps=steps, argument="path_template",
                                        arguments=arguments, template=template, by_name=by_name)
    return next(iter(sources)) if len(sources) == 1 else None


def _row_destination(steps: list[StepSpec], index: int, source: int,
                     template: str, by_name: dict, *, argument: str) -> str:
    """Compose the declared destination template on the very same rows.

    A collection of created paths is not a scalar directory, and its order
    need not match the source rows. Substitution of a pure row template is
    equivalent only for the same input, including any filters. It creates
    no dependency on a future result and moves no operation across consent.
    Merely consuming the rows (for example, to write a report) does not
    declare an equivalent destination.
    """
    def rejection():
        return PartitionDestinationError(index, steps=steps, argument=argument,
                                         template=template, by_name=by_name)

    reference = _DESTINATION_REFERENCE.fullmatch(template)
    if reference is None:
        # An unproved interpolation must not become a literal directory or
        # fail only after earlier operations have already made directories.
        # Ordinary per-row placeholders and explicit destinations stay valid.
        if "${" in template:
            raise rejection()
        return template
    number, collection, many, field, tail = reference.groups()
    number = int(number)
    tail = tail or ""
    consumer = steps[index - 1]
    if ("${" in tail or consumer.args.get("entries") not in (None, [], "")
            or consumer.args.get("paths") not in (None, [], "")):
        raise rejection()
    if number == source and collection == "entries" and many and field == "partition":
        return "{partition}" + tail
    if not 0 < number <= len(steps):
        raise rejection()
    producer = steps[number - 1]
    args = producer.args if isinstance(producer.args, dict) else {}
    declaration = by_name.get(producer.tool)
    properties = _properties(declaration)
    base = args.get("path_template")
    consumer_properties = _properties(by_name.get(consumer.tool))

    def client(arguments, schema):
        return arguments.get("client", (schema.get("client") or {}).get("default"))

    if (collection != "results" or not many or field != "path"
            or not {"from_step", "path_template"} <= properties.keys()
            or args.get("from_step") != source
            or args.get("entries") not in (None, [], "")
            or args.get("paths") not in (None, [], "")
            or not isinstance(base, str)
            or not (base == "{partition}" or base.startswith(("{partition}/", "{partition}\\")))
            or "${" in base or ".." in base.replace("\\", "/").split("/")
            or client(args, properties) != client(consumer.args, consumer_properties)):
        raise rejection()
    composed = base.rstrip("/\\") + tail
    # Inside the row's partition a source path denotes its relative path;
    # otherwise appending the absolute source would nest an unrelated root.
    if composed.endswith(("/{path}", "\\{path}")):
        composed = composed[:-len("{path}")] + "{rel_path}"
    return composed


def _separate_comparisons(query: str) -> bool:
    """Only an asserted reviewed restriction can narrow a comparison."""
    family = _parser_lexicons.load_family("partition_scope")
    if not family or not isinstance(query, str):
        return False
    for form in family["parser.partition_scope.separate"]:
        pattern = r"(?<!\w)" + re.escape(form) + r"(?!\w)"
        for match in re.finditer(pattern, query, re.IGNORECASE):
            if _lexicon.polarity_state_at(query, match.start()) == "asserted":
                return True
    return False


def _lift_traversal(args: dict, policy: dict) -> Optional[dict]:
    """Preserve the visited child entries when their parent becomes root.

    ``None`` leaves invalid inputs untouched for the ordinary validator.
    Unknown traversal semantics are never inferred from argument spelling.
    """
    traversal = policy.get("traversal")
    if traversal is None:
        return {}
    recursive_arg, depth_arg = traversal["recursive_arg"], traversal["depth_arg"]
    recursive = args.get(recursive_arg, traversal["recursive_default"])
    depth = args.get(depth_arg, traversal["depth_default"])
    if type(recursive) is not bool or type(depth) is not int or depth < 0:
        return None
    if not recursive:
        # Original calls read immediate child files only. The global call
        # must reach that second level, but no grandchildren's files.
        return {recursive_arg: True, depth_arg: 2}
    return {depth_arg: depth + 1} if depth > 0 else {}


def normalize_partition_plan(framework: Framework,
                             catalog: Optional[list], *, query: str = "") -> Framework:
    steps = list(getattr(framework, "steps", None) or [])
    if not steps or not catalog:
        return framework
    by_name = {getattr(item, "name", None): item for item in catalog}
    listings = {index: parent for index, step in enumerate(steps, 1)
                if (parent := _immediate_folders_of(step, by_name.get(step.tool)))}
    changed = False
    separate = _separate_comparisons(query)
    # Partitioned step -> the listing step it took its folders from, if any.
    partitioned: dict[int, Optional[int]] = {}
    comparisons: set[int] = set()
    for index, step in enumerate(steps, 1):
        policy = partitioned_runs.policy_of(by_name.get(step.tool))
        args = step.args if isinstance(step.args, dict) else None
        if policy is None or args is None:
            continue
        arg, list_arg = policy["arg"], policy["list_arg"]
        source = _listed_by(args.get(list_arg))
        if source is None and args.get(list_arg) in (None, [], ""):
            source = _listed_by(args.get(arg))
            if source is None and isinstance(args.get("from_step"), int):
                source = args["from_step"]
        parent = listings.get(source) if source is not None and source < index else None
        if parent is None:
            parent = _every_child_of(args.get(list_arg))
        lifting_root = parent is not None
        if parent is None and args.get("group_by") == "first_level":
            parent = _a_parent(args.get(arg)) if isinstance(args.get(arg), str) else None
        if parent is not None:
            if source not in listings or source >= index:
                source = next((listing for listing, listed in sorted(listings.items())
                               if listing < index and listed == parent), None)
            rewritten = {key: value for key, value in args.items()
                         if key not in (arg, list_arg, "from_step")}
            if policy.get("union") == "exact" or separate:
                rewritten.pop("group_by", None)
                rewritten[list_arg] = [parent + "/*"]
            else:
                traversal = _lift_traversal(args, policy) if lifting_root else {}
                if traversal is None:
                    continue
                rewritten.update(traversal)
                rewritten[arg] = parent
                rewritten["group_by"] = "first_level"
            if policy.get("union") != "exact":
                comparisons.add(index)
            if rewritten != args:
                steps[index - 1] = replace(step, args=rewritten)
                changed = True
            partitioned[index] = source
        elif args.get(list_arg) not in (None, [], ""):
            # Written out by the plan itself. An earlier step that lists the
            # same parent lists those same folders for what comes after.
            parent = _every_child_of(args.get(list_arg))
            partitioned[index] = next(
                (listing for listing, listed in sorted(listings.items())
                 if parent is not None and listing < index and listed == parent), None)
    if not partitioned:
        return framework
    fed = {source for source in partitioned.values() if source is not None}
    # The listing step stays in the plan and runs with the rest of it: its
    # parent written as «the children of the parent» would not be found.
    for index in fed:
        step = steps[index - 1]
        args = dict(step.args)
        for key in _PARENT_ARGS:
            if isinstance(args.get(key), str) and args[key] != listings[index]:
                args[key] = listings[index]
        if args != step.args:
            steps[index - 1] = replace(step, args=args)
            changed = True
    # A step that sorts or filters rows hands the same rows on: what reads it
    # reads the rows of the partitioned step, each still with its folder.
    comparison_rows = set(comparisons)
    for index, step in enumerate(steps, 1):
        args = step.args if isinstance(step.args, dict) else {}
        if (args.get("from_step") in partitioned and index not in partitioned
                and str(step.tool or "").split("_")[1:] == [_ROWS_OBJECT]):
            partitioned[index] = partitioned[args["from_step"]]
            if args["from_step"] in comparison_rows:
                comparison_rows.add(index)

    for index, step in enumerate(steps, 1):
        args = step.args if isinstance(step.args, dict) else None
        if args is None:
            continue
        rewritten = dict(args)
        properties = _properties(by_name.get(step.tool))
        paths = rewritten.get("paths")
        if (isinstance(paths, list) and len(paths) == 1 and isinstance(paths[0], str)
                and "path_template" in properties and "from_step" in properties
                and "from_step" not in rewritten):
            built = _ROWS_PATHS_AND_TAIL.match(paths[0].strip())
            if built and int(built.group(1)) in fed:
                del rewritten["paths"]
                rewritten["from_step"] = int(built.group(1))
                rewritten["path_template"] = "{path}" + built.group(2)
        # A destination is required only for rows actually returned by the
        # comparison, including copies directly in the parent itself.
        reads = rewritten.get("from_step")
        if reads in fed and "path_template" in rewritten:
            producer = _destination_row_source(
                steps, index, reads, rewritten["path_template"], partitioned, comparison_rows,
                by_name=by_name, arguments=rewritten)
            if producer is None:
                candidates = [position for position in comparisons
                              if position < index and partitioned[position] == reads]
                if len(candidates) > 1:
                    raise PartitionDestinationError(
                        index, steps=steps, argument="path_template",
                        arguments=rewritten, by_name=by_name)
                producer = next(iter(candidates), None)
            if producer is not None:
                rewritten["from_step"] = producer
                rewritten["path_template"] = rewritten["path_template"].replace(
                    "{path}", "{partition}")
        reads = rewritten.get("from_step")
        for field in _ROW_TEMPLATES:
            template = rewritten.get(field)
            if not isinstance(template, str):
                continue
            if reads in partitioned:
                listing = partitioned[reads]
                template = _ROWS_PATHS_ANYWHERE.sub(
                    lambda found: "{partition}" if int(found.group(1)) == listing
                    else found.group(0), template)
                if field == "dst_template":
                    template = _INSIDE_PARTITION_SOURCE.sub(r"\1{rel_path}", template)
            elif reads in fed:
                # The rows of the listing are the folders themselves.
                template = _ROWS_PATHS_ANYWHERE.sub(
                    lambda found: "{path}" if int(found.group(1)) == reads
                    else found.group(0), template)
                template = template.replace("{partition}", "{path}")
            rewritten[field] = template
        if rewritten != args:
            steps[index - 1] = replace(step, args=rewritten)
            changed = True
    # All directory templates now use the rows that will act. Resolve their
    # projections in a separate pass, including declarations later in the
    # plan, without changing the operation order.
    for index, step in enumerate(steps, 1):
        args = step.args if isinstance(step.args, dict) else {}
        source = args.get("from_step")
        if source not in partitioned:
            continue
        rewritten = dict(args)
        for field in _ROW_TEMPLATES:
            template = args.get(field)
            if not isinstance(template, str):
                continue
            served = _row_destination(steps, index, source, template, by_name, argument=field)
            if field == "dst_template":
                served = _INSIDE_PARTITION_SOURCE.sub(r"\1{rel_path}", served)
            rewritten[field] = served
        if rewritten != args:
            steps[index - 1] = replace(step, args=rewritten)
            changed = True
    return replace(framework, steps=steps) if changed else framework


__all__ = ["normalize_partition_plan", "PartitionDestinationError"]
