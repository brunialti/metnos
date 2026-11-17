"""Plans that work folder by folder, brought to the one form the runtime serves.

Measured on the local planner (16/11/2026, two rounds, four catalog copies
each): asked to do something «for each folder of X», it writes the plan it
would write for any «for each»: a step that lists the folders of X, then the
tool on those rows, then steps that act inside «the folder of each row». The
shape is right and the values are not: the list of folders is a placeholder
the long job cannot be frozen with, and the folder of a row is written three
different ways. Rules of prose did not change that; these rewrites do. Each
one keeps what the plan means and changes only how it is written:

1. the folders of a parent, listed by a step and handed to a tool that
   declares a partition, are «every child of that parent»: the tool's list
   argument takes `["<parent>/*"]`, a literal value. The planner also writes
   those rows with a trailing `/*`, the mark the tool's example uses for
   «each on its own»: read literally that is the children of each folder, a
   form the runtime does not serve, so in every measured plan it meant the
   folders themselves and it is read so here. A listing that asks for
   anything but every folder (a limit, an order, hidden folders, another
   machine) is not that, and the plan is left as written;
2. in a step that reads the rows of the partitioned step, the paths of the
   listing step are the folder of each row: `{partition}`;
3. in a step that reads the rows of the listing step itself, the folder of
   each row is the row: `{path}`;
4. a list of paths built from those rows plus a fixed tail is a template on
   the rows (`from_step` + `path_template`), for a tool that has one;
5. the listing step itself names its parent as the parent, not as «the
   children of the parent»: it stays in the plan and must find its folder.
   This holds also when the plan wrote «every child of that parent» by
   itself and lists the same parent for the steps that come after;
6. a file moved inside the folder of its row keeps the sub-folders it came
   from (`{rel_path}` in place of `{name}`): two files with the same name do
   not land on each other. Decided by Roberto on 16/11/2026.

Nothing here knows a tool by name: what lists folders is said by its verb and
object, what can be partitioned by its manifest, what takes a template by its
arguments. Idempotent: a plan already in this form is returned as it is.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Optional

import partitioned_runs

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
_INSIDE_PARTITION_BY_NAME = re.compile(r"^(.*\{partition\}.*[\\/])\{name\}$")


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


def normalize_partition_plan(framework: Framework,
                             catalog: Optional[list]) -> Framework:
    steps = list(getattr(framework, "steps", None) or [])
    if not steps or not catalog:
        return framework
    by_name = {getattr(item, "name", None): item for item in catalog}
    listings = {index: parent for index, step in enumerate(steps, 1)
                if (parent := _immediate_folders_of(step, by_name.get(step.tool)))}
    changed = False
    # Partitioned step -> the listing step it took its folders from, if any.
    partitioned: dict[int, Optional[int]] = {}
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
        if source in listings and source < index:
            rewritten = {key: value for key, value in args.items()
                         if key not in (arg, list_arg, "from_step")}
            rewritten[list_arg] = [listings[source] + "/*"]
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
    for index, step in enumerate(steps, 1):
        args = step.args if isinstance(step.args, dict) else {}
        if (args.get("from_step") in partitioned and index not in partitioned
                and str(step.tool or "").split("_")[1:] == [_ROWS_OBJECT]):
            partitioned[index] = partitioned[args["from_step"]]

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
                    template = _INSIDE_PARTITION_BY_NAME.sub(r"\1{rel_path}", template)
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
    return replace(framework, steps=steps) if changed else framework


__all__ = ["normalize_partition_plan"]
