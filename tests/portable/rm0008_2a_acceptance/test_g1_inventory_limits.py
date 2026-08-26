from __future__ import annotations

import ast
import ctypes
import importlib
import inspect
import os
from pathlib import Path
import textwrap

import pytest

from ._support import (
    inventory_once_helper_name,
    mkdir_private,
    lock_role_binding,
    make_root,
    open_session,
    private_role,
    provision_semantic,
    public_role,
    role_binding,
    secure_fs,
    write_private,
    write_public,
)


CASES = (
    "add-between-scans",
    "remove-between-scans",
    "rename-between-scans",
    "replace-same-name-between-scans",
    "non-json-entry",
    "local-4096",
    "local-4097",
    "aggregate-4096",
    "aggregate-4097",
)


def _provision_semantic_operation(root: Path, module, *, inventory_count: int):
    semantic_root = root / "logical-semantic"
    provision_semantic(semantic_root)
    (semantic_root / "semantic.json").rename(semantic_root / "authority.json")
    authority = ("logical-semantic", "authority.json")
    public = ("logical-semantic", "public")
    evidence = ("logical-semantic", "evidence")
    # review.pub is the first real record.  Evidence supplies the remainder so
    # this one productive semantic load crosses two required subtrees and the
    # 4096/4097 boundary without a caller-injected budget.
    evidence_count = inventory_count - 1
    for index in range(evidence_count):
        write_public(
            semantic_root / "evidence" / f"entry-{index:04d}.json", b"{}"
        )
    bindings = (
        role_binding(
            module,
            ("logical-semantic",),
            directory=True,
            role=public_role(module),
        ),
        role_binding(
            module, authority, directory=False, role=public_role(module)
        ),
        role_binding(module, public, directory=True, role=public_role(module)),
        role_binding(
            module,
            public + ("review.pub",),
            directory=False,
            role=public_role(module),
        ),
        role_binding(module, evidence, directory=True, role=public_role(module)),
        *(
            role_binding(
                module,
                evidence + (f"entry-{index:04d}.json",),
                directory=False,
                role=public_role(module),
            )
            for index in range(evidence_count)
        ),
    )
    return authority, public, evidence, bindings


def _single_operation_budget(observed, required_paths, budget_type):
    assert set(required_paths) <= {components for components, _ in observed}
    budgets = [budget for _, budget in observed]
    assert budgets and all(isinstance(budget, budget_type) for budget in budgets)
    assert all(budget is budgets[0] for budget in budgets)
    return budgets[0]


def _terminal_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _direct_budget_include(statement: ast.stmt) -> bool:
    value: ast.AST | None = None
    if isinstance(statement, ast.Expr):
        value = statement.value
    elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
        value = statement.value
    if not isinstance(value, ast.Call) or _terminal_name(value.func) != "include":
        return False
    if len(value.args) > 2 or any(keyword.arg is None for keyword in value.keywords):
        return False
    provided = list(("path", "identity")[: len(value.args)])
    provided.extend(keyword.arg for keyword in value.keywords)
    return sorted(provided) == ["identity", "path"]


def _budget_dominates(
    target: ast.Call,
    boundary: ast.For | ast.While,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST = target
    while current is not boundary and current in parents:
        parent = parents[current]
        for field in ("body", "orelse", "finalbody"):
            statements = getattr(parent, field, None)
            if isinstance(statements, list) and current in statements:
                position = statements.index(current)
                if any(
                    _direct_budget_include(statement)
                    for statement in statements[:position]
                ):
                    return True
        current = parent
    return False


def _assert_incremental_windows_decoder(source: str) -> None:
    tree = ast.parse(textwrap.dedent(source))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_win_inventory"
    ]
    assert len(functions) == 1
    function = functions[0]
    scope_boundaries = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.Lambda,
        ast.ClassDef,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )

    def lexical_nodes(root: ast.AST) -> tuple[ast.AST, ...]:
        nodes: list[ast.AST] = []
        pending = [root]
        while pending:
            node = pending.pop()
            nodes.append(node)
            if node is not root and isinstance(node, scope_boundaries):
                continue
            pending.extend(reversed(tuple(ast.iter_child_nodes(node))))
        return tuple(nodes)

    comprehension_boundaries = (
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )

    def pattern_bound_names(pattern: ast.AST) -> tuple[str, ...]:
        names: list[str] = []
        pending = [pattern]
        while pending:
            item = pending.pop()
            if isinstance(item, ast.MatchAs):
                if item.name is not None:
                    names.append(item.name)
                if item.pattern is not None:
                    pending.append(item.pattern)
                continue
            if isinstance(item, ast.MatchStar):
                if item.name is not None:
                    names.append(item.name)
                continue
            if isinstance(item, ast.MatchMapping) and item.rest is not None:
                names.append(item.rest)
            pending.extend(ast.iter_child_nodes(item))
        return tuple(names)

    def lexical_body_nodes(scope: ast.AST) -> tuple[ast.AST, ...]:
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            roots: tuple[ast.AST, ...] = tuple(scope.body)
        elif isinstance(scope, ast.Lambda):
            roots = (scope.body,)
        else:
            roots = (scope,)
        nodes: list[ast.AST] = []
        pending = list(reversed(roots))
        while pending:
            node = pending.pop()
            nodes.append(node)
            if node is not scope and isinstance(node, scope_boundaries):
                continue
            pending.extend(reversed(tuple(ast.iter_child_nodes(node))))
        return tuple(nodes)

    def comprehension_named_expression_targets(
        boundary: ast.AST,
    ) -> tuple[str, ...]:
        """Return PEP 572 bindings owned by the surrounding function scope."""
        names: list[str] = []
        pending = list(ast.iter_child_nodes(boundary))
        while pending:
            node = pending.pop()
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
            ):
                continue
            if isinstance(node, ast.NamedExpr):
                names.extend(assigned_names(node.target))
            pending.extend(ast.iter_child_nodes(node))
        return tuple(names)

    function_nodes = lexical_nodes(function)
    function_node_set = set(function_nodes)
    parents = {
        child: parent
        for parent in function_nodes
        if parent is function or not isinstance(parent, scope_boundaries)
        for child in ast.iter_child_nodes(parent)
        if child in function_node_set
    }
    decoded = [
        node
        for node in function_nodes
        if isinstance(node, ast.Call) and _terminal_name(node.func) == "wstring_at"
    ]
    records = [
        node
        for node in function_nodes
        if isinstance(node, ast.Call)
        and _terminal_name(node.func) == "_InventoryEntry"
    ]
    assert decoded and records
    all_native_queries = [
        node
        for node in function_nodes
        if isinstance(node, ast.Call)
        and _terminal_name(node.func) == "GetFileInformationByHandleEx"
    ]
    assert all_native_queries, "Windows enumeration must remain directly observable"

    def assigned_names(target: ast.AST) -> tuple[str, ...]:
        if isinstance(target, ast.Name):
            return (target.id,)
        if isinstance(target, ast.Starred):
            return assigned_names(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            return tuple(
                name for item in target.elts for name in assigned_names(item)
            )
        return ()

    # Resolve only immutable, locally visible integer bindings.  These are used
    # both by the real module constants and by a named 64-KiB buffer constant;
    # accepting an unresolved name here would turn the structural oracle into
    # a spelling convention rather than a proof.
    integer_bindings: dict[str, list[ast.AST]] = {}
    for statements in (
        tree.body,
        tuple(
            node
            for node in function_nodes
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ),
    ):
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                if value is not None:
                    for target in targets:
                        for name in assigned_names(target):
                            integer_bindings.setdefault(name, []).append(value)

    known_information_classes = {
        "FILE_ID_INFO_CLASS": 18,
        "_FILE_ID_INFO_CLASS": 18,
        "FILE_ID_EXTD_DIRECTORY_INFO_CLASS": 19,
        "_FILE_ID_EXTD_DIRECTORY_INFO_CLASS": 19,
        "FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS": 20,
        "_FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS": 20,
    }

    def integer_values(node: ast.AST, seen: frozenset[str] = frozenset()) -> set[int] | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return {node.value}
        if isinstance(node, ast.Name):
            if node.id in known_information_classes:
                return {known_information_classes[node.id]}
            values = integer_bindings.get(node.id, ())
            if len(values) != 1 or node.id in seen:
                return None
            return integer_values(values[0], seen | {node.id})
        if isinstance(node, ast.IfExp):
            left = integer_values(node.body, seen)
            right = integer_values(node.orelse, seen)
            return None if left is None or right is None else left | right
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult)
        ):
            left = integer_values(node.left, seen)
            right = integer_values(node.right, seen)
            if left is None or right is None:
                return None
            operation = (
                (lambda a, b: a + b)
                if isinstance(node.op, ast.Add)
                else (lambda a, b: a - b)
                if isinstance(node.op, ast.Sub)
                else (lambda a, b: a * b)
            )
            return {operation(a, b) for a in left for b in right}
        return None

    def exact_attribute_call(
        call: ast.Call, receiver: str, attribute: str
    ) -> bool:
        return (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == attribute
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == receiver
        )

    def exact_native_query(call: ast.Call) -> bool:
        return (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "GetFileInformationByHandleEx"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "_KERNEL32"
        )

    native_queries: list[ast.Call] = []
    for query in all_native_queries:
        if not exact_native_query(query) or len(query.args) < 4:
            raise AssertionError("Windows native query authority must be exact")
        information_classes = integer_values(query.args[1])
        if information_classes is None:
            raise AssertionError("Windows native information class must be static")
        if information_classes <= {19, 20}:
            native_queries.append(query)
        elif information_classes & {19, 20}:
            raise AssertionError("Windows enumeration information class is ambiguous")
    assert native_queries, "Windows enumeration query classes 19/20 are required"

    query_buffer_names: set[str] = set()
    for query in native_queries:
        if len(query.args) < 4 or not isinstance(query.args[2], ast.Name):
            raise AssertionError(
                "Windows enumeration must query into one direct local buffer name"
            )
        query_buffer_names.add(query.args[2].id)
    if len(query_buffer_names) != 1:
        raise AssertionError("Windows enumeration must reuse one local fixed buffer")
    buffer_name = next(iter(query_buffer_names))
    module_nodes = lexical_nodes(tree)

    def argument_names(scope: ast.AST) -> set[str]:
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return set()
        result = {
            argument.arg
            for argument in (
                *scope.args.posonlyargs,
                *scope.args.args,
                *scope.args.kwonlyargs,
            )
        }
        if scope.args.vararg is not None:
            result.add(scope.args.vararg.arg)
        if scope.args.kwarg is not None:
            result.add(scope.args.kwarg.arg)
        return result

    def scope_declarations(scope: ast.AST) -> tuple[set[str], set[str]]:
        nodes = lexical_body_nodes(scope)
        globals_: set[str] = set()
        nonlocals: set[str] = set()
        for declaration in nodes:
            if isinstance(declaration, ast.Global):
                globals_.update(declaration.names)
            elif isinstance(declaration, ast.Nonlocal):
                nonlocals.update(declaration.names)
        return globals_, nonlocals

    def scope_bindings(scope: ast.AST) -> tuple[set[str], dict[str, tuple[int, int]]]:
        nodes = lexical_body_nodes(scope)
        positions: dict[str, tuple[int, int]] = {}

        def bind(name: str, node: ast.AST) -> None:
            position = (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
            if name not in positions or position < positions[name]:
                positions[name] = position

        if isinstance(scope, comprehension_boundaries):
            for generator in scope.generators:
                for name in assigned_names(generator.target):
                    bind(name, generator.target)
            return set(positions), positions

        for name in argument_names(scope):
            positions[name] = (0, 0)
        for node in nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bind(node.id, node)
            elif isinstance(node, ast.ExceptHandler) and node.name is not None:
                bind(node.name, node)
            elif isinstance(node, ast.Match):
                for case in node.cases:
                    for name in pattern_bound_names(case.pattern):
                        bind(name, case.pattern)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bind(node.name, node)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bind(alias.asname or alias.name.split(".", 1)[0], node)
            if isinstance(node, comprehension_boundaries):
                for name in comprehension_named_expression_targets(node):
                    bind(name, node)
        globals_, nonlocals = scope_declarations(scope)
        for declared in globals_ | nonlocals:
            positions.pop(declared, None)
        return set(positions), positions

    parameters = argument_names(function)
    function_globals, function_nonlocals = scope_declarations(function)
    declarations = function_globals | function_nonlocals
    local_bound_names, _function_binding_positions = scope_bindings(function)

    module_comprehension_bindings = {
        name
        for node in module_nodes
        if isinstance(node, comprehension_boundaries)
        for name in comprehension_named_expression_targets(node)
    }
    module_handler_bindings = {
        node.name
        for node in module_nodes
        if isinstance(node, ast.ExceptHandler) and node.name is not None
    }
    module_pattern_bindings = {
        name
        for node in module_nodes
        if isinstance(node, ast.Match)
        for case in node.cases
        for name in pattern_bound_names(case.pattern)
    }
    module_non_import_bindings = {
        node.id
        for node in module_nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    } | {
        node.name
        for node in module_nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    } | module_comprehension_bindings | module_handler_bindings | module_pattern_bindings
    module_import_bindings = {
        alias.asname or alias.name.split(".", 1)[0]: (
            node.module or "" if isinstance(node, ast.ImportFrom) else alias.name
        )
        for node in module_nodes
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    def assignment_targets_name(statement: ast.AST, name: str) -> bool:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            return False
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        return any(name in assigned_names(target) for target in targets)

    kernel32_bindings = [
        node
        for node in module_nodes
        if assignment_targets_name(node, "_KERNEL32")
    ]
    canonical_kernel32 = (
        len(kernel32_bindings) == 1
        and kernel32_bindings[0].value is not None
        and isinstance(kernel32_bindings[0].value, ast.Call)
        and exact_attribute_call(kernel32_bindings[0].value, "ctypes", "WinDLL")
        and len(kernel32_bindings[0].value.args) == 1
        and isinstance(kernel32_bindings[0].value.args[0], ast.Constant)
        and kernel32_bindings[0].value.args[0].value == "kernel32"
        and len(kernel32_bindings[0].value.keywords) == 1
        and kernel32_bindings[0].value.keywords[0].arg == "use_last_error"
        and isinstance(kernel32_bindings[0].value.keywords[0].value, ast.Constant)
        and kernel32_bindings[0].value.keywords[0].value.value is True
    )
    structure_classes = [
        node
        for node in module_nodes
        if isinstance(node, ast.ClassDef) and node.name == "_FILE_ID_EXTD_DIR_INFO"
    ]
    module_authority_name_stores = [
        node
        for node in module_nodes
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.id in {"_KERNEL32", "_FILE_ID_EXTD_DIR_INFO"}
    ]
    module_authority_definitions = [
        node
        for node in module_nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in {"_KERNEL32", "_FILE_ID_EXTD_DIR_INFO"}
    ]
    canonical_structure = (
        len(structure_classes) == 1
        and not structure_classes[0].decorator_list
        and not structure_classes[0].keywords
        and len(structure_classes[0].bases) == 1
        and isinstance(structure_classes[0].bases[0], ast.Attribute)
        and structure_classes[0].bases[0].attr == "Structure"
        and isinstance(structure_classes[0].bases[0].value, ast.Name)
        and structure_classes[0].bases[0].value.id == "ctypes"
        and not any(
            isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and member.name == "from_buffer"
            or isinstance(member, (ast.Assign, ast.AnnAssign))
            and assignment_targets_name(member, "from_buffer")
            for member in structure_classes[0].body
        )
    )
    if (
        module_import_bindings.get("ctypes") != "ctypes"
        or "ctypes" in module_non_import_bindings
        or not canonical_kernel32
        or not canonical_structure
        or "_KERNEL32" in module_import_bindings
        or "_FILE_ID_EXTD_DIR_INFO" in module_import_bindings
        or [node.id for node in module_authority_name_stores] != ["_KERNEL32"]
        or module_authority_definitions != structure_classes
        or (
            module_comprehension_bindings
            | module_handler_bindings
            | module_pattern_bindings
        )
        & {"_KERNEL32", "_FILE_ID_EXTD_DIR_INFO"}
    ):
        raise AssertionError("Windows decoder native authorities must be canonical")

    def exact_authority_attribute(node: ast.AST) -> tuple[str, str] | None:
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            return None
        candidate = (node.value.id, node.attr)
        return candidate if candidate in {
            ("_KERNEL32", "GetFileInformationByHandleEx"),
            ("_FILE_ID_EXTD_DIR_INFO", "from_buffer"),
        } else None

    for node in ast.walk(tree):
        targets: tuple[ast.AST, ...] = ()
        if isinstance(node, ast.Assign):
            targets = tuple(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        elif isinstance(node, (ast.AugAssign, ast.NamedExpr)):
            targets = (node.target,)
        if any(exact_authority_attribute(target) is not None for target in targets):
            raise AssertionError("Windows decoder native authority is reassigned")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"setattr", "delattr"}
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and (node.args[0].id, node.args[1].value)
            in {
                ("_KERNEL32", "GetFileInformationByHandleEx"),
                ("_FILE_ID_EXTD_DIR_INFO", "from_buffer"),
            }
        ):
            raise AssertionError("Windows decoder native authority is reassigned")

    for candidate_scope in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    ):
        candidate_globals, _ = scope_declarations(candidate_scope)
        if not candidate_globals & {"_KERNEL32", "_FILE_ID_EXTD_DIR_INFO"}:
            continue
        candidate_nodes = lexical_body_nodes(candidate_scope)
        if any(
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and node.id in candidate_globals
            for node in candidate_nodes
        ):
            raise AssertionError("Windows decoder native authority is rebound globally")

    def lexically_unshadowed(name: str, *, builtin: bool = False) -> bool:
        return name not in local_bound_names and (
            not builtin
            or (
                name not in module_non_import_bindings
                and name not in module_import_bindings
            )
        )

    allocations = [
        assignment
        for assignment in function_nodes
        if isinstance(assignment, (ast.Assign, ast.AnnAssign))
        and assignment.value is not None
        and any(
            isinstance(target, ast.Name) and target.id == buffer_name
            for target in (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
        )
        and isinstance(assignment.value, ast.Call)
        and exact_attribute_call(assignment.value, "ctypes", "create_string_buffer")
        and lexically_unshadowed("ctypes")
        and "ctypes" not in module_non_import_bindings
        and module_import_bindings.get("ctypes") == "ctypes"
        and len(assignment.value.args) == 1
        and integer_values(assignment.value.args[0]) == {65536}
    ]
    buffer_stores = [
        node
        for node in function_nodes
        if isinstance(node, ast.Name)
        and node.id == buffer_name
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    if (
        buffer_name in parameters
        or buffer_name in declarations
        or len(allocations) != 1
        or len(buffer_stores) != 1
        or allocations[0].lineno >= min(query.lineno for query in native_queries)
    ):
        raise AssertionError(
            "Windows enumeration buffer must be one per-call 64 KiB local allocation"
        )

    record_names = {
        target.id
        for assignment in function_nodes
        if isinstance(assignment, (ast.Assign, ast.AnnAssign))
        and assignment.value is not None
        and any(record in set(ast.walk(assignment.value)) for record in records)
        for target in (
            assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
        )
        if isinstance(target, ast.Name)
    }
    raw_names = {query.args[2].id for query in native_queries}

    fixed_file_id_names: set[str] = set()
    scalar_names: set[str] = set()
    scalar_fields = {
        "NextEntryOffset",
        "FileIndex",
        "CreationTime",
        "LastAccessTime",
        "LastWriteTime",
        "ChangeTime",
        "EndOfFile",
        "AllocationSize",
        "FileAttributes",
        "FileNameLength",
        "EaSize",
        "ReparsePointTag",
        "offset",
    }

    def scalar_expression(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, (bool, int))
        if isinstance(node, ast.Name):
            return node.id in scalar_names and node.id not in raw_names
        if isinstance(node, ast.Attribute):
            return node.attr in scalar_fields
        if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare)):
            children = tuple(ast.iter_child_nodes(node))
            return bool(children) and all(
                isinstance(
                    child,
                    (ast.operator, ast.unaryop, ast.boolop, ast.cmpop),
                )
                or scalar_expression(child)
                for child in children
            )
        if isinstance(node, ast.Call) and _terminal_name(node.func) in {"bool", "int"}:
            return bool(node.args) and all(
                scalar_expression(argument) for argument in node.args
            )
        return False

    def fixed_file_id_expression(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute) and node.attr == "Identifier"
        ) or (
            isinstance(node, ast.Name)
            and node.id in fixed_file_id_names
            and node.id not in raw_names
        )

    def raw_expression(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in raw_names or node.id in fixed_file_id_names
        if scalar_expression(node):
            return False
        if isinstance(node, ast.Call):
            if node in all_native_queries:
                return False
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "len"
                and len(node.args) == 1
            ):
                return False
            if exact_attribute_call(node, "ctypes", "wstring_at"):
                return False
            terminal = _terminal_name(node.func)
            if (
                terminal in {"bytes", "tuple"}
                and isinstance(node.func, ast.Name)
                and len(node.args) == 1
                and fixed_file_id_expression(node.args[0])
            ):
                return False
            receiver_is_raw = (
                isinstance(node.func, ast.Attribute)
                and raw_expression(node.func.value)
            )
            return (
                receiver_is_raw
                or any(raw_expression(argument) for argument in node.args)
                or any(raw_expression(keyword.value) for keyword in node.keywords)
            )
        return any(raw_expression(child) for child in ast.iter_child_nodes(node))

    changed = True
    while changed:
        changed = False
        for assignment in function_nodes:
            if not isinstance(assignment, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                continue
            value = assignment.value
            if value is None:
                continue
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            target_names = tuple(
                name for target in targets for name in assigned_names(target)
            )
            if scalar_expression(value):
                for name in target_names:
                    if name not in scalar_names:
                        scalar_names.add(name)
                        changed = True
                continue
            if fixed_file_id_expression(value):
                for name in target_names:
                    if name not in fixed_file_id_names:
                        fixed_file_id_names.add(name)
                        changed = True
                continue
            if not raw_expression(value):
                continue
            for name in target_names:
                if name not in raw_names:
                    raw_names.add(name)
                    changed = True

        for match_statement in (
            node for node in function_nodes if isinstance(node, ast.Match)
        ):
            if not raw_expression(match_statement.subject):
                continue
            for case in match_statement.cases:
                for name in pattern_bound_names(case.pattern):
                    if name not in raw_names:
                        raw_names.add(name)
                        changed = True

    scope_parents: dict[ast.AST, ast.AST | None] = {function: None}

    def register_descendants(node: ast.AST, active_scope: ast.AST) -> None:
        if isinstance(node, scope_boundaries):
            scope_parents[node] = active_scope
            register_scope_contents(node, active_scope)
            return
        for child in ast.iter_child_nodes(node):
            register_descendants(child, active_scope)

    def register_scope_contents(scope: ast.AST, parent_scope: ast.AST) -> None:
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            header_values = [
                *scope.decorator_list,
                *scope.args.defaults,
                *(value for value in scope.args.kw_defaults if value is not None),
            ]
            for value in header_values:
                register_descendants(value, parent_scope)
            for statement in scope.body:
                register_descendants(statement, scope)
            return
        if isinstance(scope, ast.Lambda):
            for value in (
                *scope.args.defaults,
                *(value for value in scope.args.kw_defaults if value is not None),
            ):
                register_descendants(value, parent_scope)
            register_descendants(scope.body, scope)
            return
        if isinstance(scope, ast.ClassDef):
            for value in (
                *scope.decorator_list,
                *scope.bases,
                *(keyword.value for keyword in scope.keywords),
            ):
                register_descendants(value, parent_scope)
            for statement in scope.body:
                register_descendants(statement, scope)
            return
        assert isinstance(scope, comprehension_boundaries)
        first, *remaining = scope.generators
        register_descendants(first.iter, parent_scope)
        for value in (first.target, *first.ifs):
            register_descendants(value, scope)
        for generator in remaining:
            for value in (generator.iter, generator.target, *generator.ifs):
                register_descendants(value, scope)
        element_values = (
            (scope.key, scope.value) if isinstance(scope, ast.DictComp) else (scope.elt,)
        )
        for value in element_values:
            register_descendants(value, scope)

    for statement in function.body:
        register_descendants(statement, function)
    scope_binding_cache = {
        scope: scope_bindings(scope) for scope in scope_parents
    }
    scope_declaration_cache = {
        scope: scope_declarations(scope) for scope in scope_parents
    }

    def enclosing_non_class(scope: ast.AST | None) -> ast.AST | None:
        current = scope
        while isinstance(current, ast.ClassDef):
            current = scope_parents.get(current)
        return current

    def resolve_binding(
        name: str, scope: ast.AST | None, load: ast.AST
    ) -> ast.AST | None:
        current = scope
        position = (getattr(load, "lineno", 0), getattr(load, "col_offset", 0))
        while current is not None:
            globals_, nonlocals = scope_declaration_cache[current]
            bindings, binding_positions = scope_binding_cache[current]
            if name in globals_:
                return None
            if name in nonlocals:
                current = enclosing_non_class(scope_parents.get(current))
                continue
            if isinstance(current, ast.ClassDef):
                if name in bindings and position > binding_positions[name]:
                    return current
                current = scope_parents.get(current)
                continue
            if name in bindings:
                return current
            current = enclosing_non_class(scope_parents.get(current))
        return None

    sensitive_names = raw_names | fixed_file_id_names

    def immediate_expression_nodes(root: ast.AST) -> tuple[ast.AST, ...]:
        nodes: list[ast.AST] = []
        pending = [root]
        while pending:
            node = pending.pop()
            nodes.append(node)
            if isinstance(node, scope_boundaries):
                continue
            pending.extend(ast.iter_child_nodes(node))
        return tuple(nodes)

    def loads_outer_sensitive(nodes: Iterable[ast.AST], scope: ast.AST | None) -> bool:
        return any(
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in sensitive_names
            and resolve_binding(node.id, scope, node) is function
            for node in nodes
        )

    nested_scopes = [scope for scope in scope_parents if scope is not function]
    for nested in nested_scopes:
        evaluated_in_outer_scope: list[ast.AST] = []
        if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
            evaluated_in_outer_scope.extend(nested.decorator_list)
            evaluated_in_outer_scope.extend(nested.args.defaults)
            evaluated_in_outer_scope.extend(
                value for value in nested.args.kw_defaults if value is not None
            )
        elif isinstance(nested, ast.Lambda):
            evaluated_in_outer_scope.extend(nested.args.defaults)
            evaluated_in_outer_scope.extend(
                value for value in nested.args.kw_defaults if value is not None
            )
        elif isinstance(nested, ast.ClassDef):
            evaluated_in_outer_scope.extend(nested.decorator_list)
            evaluated_in_outer_scope.extend(nested.bases)
            evaluated_in_outer_scope.extend(
                keyword.value for keyword in nested.keywords
            )
        parent_scope = scope_parents[nested]
        if any(
            loads_outer_sensitive(immediate_expression_nodes(value), parent_scope)
            for value in evaluated_in_outer_scope
        ):
            raise AssertionError(
                "Windows decoder captures raw native data while defining a nested scope"
            )
        if loads_outer_sensitive(lexical_body_nodes(nested), nested):
            label = (
                "comprehension"
                if isinstance(nested, comprehension_boundaries)
                else "nested scope"
            )
            raise AssertionError(
                f"Windows decoder {label} captures raw native data"
            )
        if isinstance(nested, comprehension_boundaries) and any(
            isinstance(item, ast.Call)
            and _terminal_name(item.func) == "native_batches"
            for item in ast.walk(nested)
        ):
            raise AssertionError(
                "Windows decoder cannot materialize native batches with a comprehension"
            )

    for node in function_nodes:
        if isinstance(node, ast.Match) and raw_expression(node.subject):
            raise AssertionError("Windows decoder pattern-matches raw native data")
        if isinstance(node, ast.Subscript) and raw_expression(node.value):
            raise AssertionError("Windows decoder slices or indexes a raw native buffer")
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and any(
            raw_expression(element) for element in node.elts
        ):
            raise AssertionError("Windows decoder stores raw native data in a container")
        if isinstance(node, ast.Dict) and any(
            raw_expression(item)
            for item in (*node.keys, *node.values)
            if item is not None
        ):
            raise AssertionError("Windows decoder stores raw native data in a mapping")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if value is not None and raw_expression(value) and any(
                not isinstance(target, ast.Name) for target in targets
            ):
                raise AssertionError("Windows decoder destructures raw native data")
            if value is not None and raw_expression(value) and any(
                isinstance(target, (ast.Attribute, ast.Subscript))
                for target in targets
            ):
                raise AssertionError("Windows decoder exports a raw native alias")
        if isinstance(node, (ast.For, ast.AsyncFor)) and raw_expression(node.iter):
            raise AssertionError("Windows decoder iterates a raw native buffer")

    for call in (node for node in function_nodes if isinstance(node, ast.Call)):
        terminal = _terminal_name(call.func)
        receives_raw_buffer = any(
            raw_expression(argument) for argument in call.args
        ) or any(raw_expression(keyword.value) for keyword in call.keywords) or (
            isinstance(call.func, ast.Attribute)
            and raw_expression(call.func.value)
        )
        exact_structure_view = (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "from_buffer"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "_FILE_ID_EXTD_DIR_INFO"
            and lexically_unshadowed("_FILE_ID_EXTD_DIR_INFO")
        )
        exact_builtin = (
            isinstance(call.func, ast.Name)
            and call.func.id in {"bool", "int", "len"}
            and lexically_unshadowed(call.func.id, builtin=True)
        )
        exact_ctypes_reader = exact_attribute_call(
            call, "ctypes", "addressof"
        ) or exact_attribute_call(call, "ctypes", "wstring_at")
        exact_ctypes_reader = (
            exact_ctypes_reader
            and lexically_unshadowed("ctypes")
            and "ctypes" not in module_non_import_bindings
            and module_import_bindings.get("ctypes") == "ctypes"
        )
        exact_fixed_id_copy = (
            isinstance(call.func, ast.Name)
            and call.func.id in {"bytes", "tuple"}
            and len(call.args) == 1
            and fixed_file_id_expression(call.args[0])
            and lexically_unshadowed(call.func.id, builtin=True)
        )
        authorized_raw_call = (
            (call in all_native_queries and lexically_unshadowed("_KERNEL32"))
            or exact_structure_view
            or exact_builtin
            or exact_ctypes_reader
            or exact_fixed_id_copy
        )
        if receives_raw_buffer and not authorized_raw_call:
            raise AssertionError("Windows decoder passes its native buffer to a helper")
        if terminal in {
            "bytes",
            "bytearray",
            "deque",
            "frozenset",
            "list",
            "memoryview",
            "set",
            "sorted",
            "tuple",
        } and receives_raw_buffer:
            if not (
                isinstance(call.func, ast.Name)
                and terminal in {"bytes", "tuple"}
                and len(call.args) == 1
                and fixed_file_id_expression(call.args[0])
            ):
                raise AssertionError("Windows decoder materializes native records")
        if terminal in {"append", "extend"}:
            arguments = tuple(item for argument in call.args for item in ast.walk(argument))
            if any(
                isinstance(item, ast.Call)
                and _terminal_name(item.func) in {"wstring_at", "native_batches"}
                for item in arguments
            ):
                raise AssertionError("Windows decoder accumulates a raw record")
            if any(isinstance(item, ast.Name) and item.id in record_names for item in arguments):
                continue
    if any(
        isinstance(node, ast.Attribute)
        and node.attr in {"raw", "value"}
        and isinstance(node.value, ast.Name)
        and node.value.id in raw_names
        for node in function_nodes
    ):
        raise AssertionError("Windows decoder copies a raw native buffer")
    loops = [
        node
        for node in function_nodes
        if isinstance(node, (ast.For, ast.While))
    ]
    for record in records:
        containing = [
            loop
            for loop in loops
            if record in set(ast.walk(loop))
            and any(item in set(ast.walk(loop)) for item in decoded)
            and any(item in set(ast.walk(loop)) for item in native_queries)
        ]
        assert containing, "Windows records must be built in the decoding loop"
        decoding_loop = min(containing, key=lambda node: len(tuple(ast.walk(node))))
        assert _budget_dominates(record, decoding_loop, parents), (
            "Windows budget accounting must dominate record construction"
        )


def _assert_windows_decoder_guard_rejects_mutants() -> None:
    valid = """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                identity = tuple(native.FileId.Identifier)
                state.include((name,), identity)
                record = _InventoryEntry(name, identity)
                result.append(record)
            return tuple(result)
    """
    valid_realistic = """
        def _win_inventory(handle, state):
            result = []
            volume = "volume"
            buffer = ctypes.create_string_buffer(64 * 1024)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                entry = _FILE_ID_EXTD_DIR_INFO.from_buffer(buffer)
                name = ctypes.wstring_at(buffer)
                identifier = entry.FileId.Identifier
                identity = _ObjectIdentity(volume, bytes(identifier).hex())
                state.include((name,), identity)
                directory = bool(entry.FileAttributes & DIRECTORY_FLAG)
                record = _InventoryEntry(name, identity, directory, 1)
                result.append(record)
            return tuple(result)
    """
    valid_named_constant = """
        ENUM_BUFFER_SIZE = 64 * 1024
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(ENUM_BUFFER_SIZE)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
    """
    valid_with_metadata_query = """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, FILE_ID_INFO_CLASS, info, len(info))
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
    """
    valid_with_unrelated_nested_scope = """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            def unrelated():
                buffer = ctypes.create_string_buffer(65536)
                return len(buffer)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
    """
    valid_with_unrelated_comprehension = """
        def _win_inventory(handle, state):
            result = []
            unrelated = [0 for len in ()]
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include(path=(name,), identity=identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
    """
    valid_with_nested_safe_capture = """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            def unrelated():
                buffer = ctypes.create_string_buffer(8)
                def size_of_local():
                    return len(buffer)
                return size_of_local()
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
    """
    valid_with_safe_python_bindings = """
        def _win_inventory(handle, state):
            result = []
            try:
                unrelated = 1
            except ValueError as temporary:
                unrelated = temporary
            match unrelated:
                case safe_capture:
                    unrelated = safe_capture
            [(scratch := 1) for _ in (0,)]
            safe_values = [buffer for buffer in ()]
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
    """
    mutants = (
        """
        def _win_inventory(handle, state):
            buffer = ctypes.create_string_buffer(65536)
            decoded = []
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                decoded.append(ctypes.wstring_at(buffer))
            result = []
            for name in decoded:
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                result.append(_InventoryEntry(name, identity))
                state.include((name,), identity)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.ensure_room((name,), identity)
                result.append(_InventoryEntry(name, identity))
                state.include((name,), identity)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            buffer = ctypes.create_string_buffer(65536)
            _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
            raw_records = [item for item in native_batches(handle)]
            result = []
            for raw in raw_records:
                name = ctypes.wstring_at(raw)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            buffer = ctypes.create_string_buffer(65536)
            _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
            raw_records = tuple(native_batches(handle))
            result = []
            for raw in raw_records:
                name = ctypes.wstring_at(raw)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                copied = bytes(buffer)
                name = ctypes.wstring_at(copied)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                raw_records = _parse_current_buffer_to_list(buffer)
                for raw in raw_records:
                    name = ctypes.wstring_at(raw)
                    state.include((name,), identity)
                    result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                address = ctypes.addressof(buffer)
                raw_records = _parse_address_to_list(address)
                for raw in raw_records:
                    name = ctypes.wstring_at(raw)
                    state.include((name,), identity)
                    result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                address = ctypes.addressof(buffer) + 0
                raw_records = _parse_address_to_list(address)
                for raw in raw_records:
                    name = ctypes.wstring_at(raw)
                    state.include((name,), identity)
                    result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                view = _FILE_ID_EXTD_DIR_INFO.from_buffer(buffer)
                raw_records = _walk_from_header_to_list(view)
                for raw in raw_records:
                    name = ctypes.wstring_at(raw)
                    state.include((name,), identity)
                    result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                copied = buffer[:]
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                snapshots += [buffer[:]]
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                copied = buffer.value
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                snapshots.append(buffer.__getitem__(slice(None)))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = (ctypes.c_ubyte * 65536)()
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                copied = buffer[:]
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(64 * 1024)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                copied = _copy_buffer(source=buffer)
                snapshots.append(copied)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            sink = Sink()
            buffer = ctypes.create_string_buffer(64 * 1024)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                sink.store(item=buffer)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = SHARED_BUFFER
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            raw_ids = []
            buffer = ctypes.create_string_buffer(64 * 1024)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                entry = _FILE_ID_EXTD_DIR_INFO.from_buffer(buffer)
                name = ctypes.wstring_at(buffer)
                identifier = entry.FileId.Identifier
                identity = _ObjectIdentity(volume, bytes(identifier).hex())
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                raw_ids.append(list(identifier))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            safe = 0
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                safe = buffer.__getitem__(slice(None))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                snapshots.append(safe)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                entry = _FILE_ID_EXTD_DIR_INFO.from_buffer(buffer)
                identifier = entry.FileId.Identifier
                identifier = buffer
                copied = bytes(identifier)
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                snapshots.append(copied)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            copied = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                for octet in buffer:
                    copied.append(octet)
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                first, *copied = buffer
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            buffer = SHARED_BUFFER
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                copied = evil.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                snapshots.append(copied)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                if (copied := buffer.__getitem__(slice(None))):
                    snapshots.append(copied)
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            def unused():
                buffer = ctypes.create_string_buffer(65536)
            result = []
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                Evil.from_buffer(buffer)
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            snapshots = []
            def len(value):
                snapshots.append(bytes(value))
                return 65536
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state, *, len=evil_len):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        from evil import len
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                def snapshot(value=bytes(buffer)):
                    return value
                snapshots.append(snapshot)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                def copy_current_batch():
                    return bytes(buffer)
                snapshots.append(copy_current_batch())
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                @sink(buffer)
                def marker():
                    pass
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                class Snapshot(sink(buffer)):
                    pass
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            def copy_current_batch():
                nonlocal buffer
                snapshot = bytes(buffer)
                buffer = buffer
                return snapshot
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                snapshots.append(copy_current_batch())
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                class Snapshot:
                    nonlocal buffer
                    if False:
                        buffer = None
                    copied = bytes(buffer)
                snapshots.append(Snapshot.copied)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                match buffer:
                    case captured:
                        snapshots.append(captured[:])
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            snapshots = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
                match buffer:
                    case [*captured]:
                        snapshots.append(captured)
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            [(len := copying_len) for _ in (0,)]
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        def _win_inventory(handle, state):
            result = []
            try:
                raise EvilLen()
            except EvilLen as len:
                buffer = ctypes.create_string_buffer(65536)
                while True:
                    _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                    name = ctypes.wstring_at(buffer)
                    state.include((name,), identity)
                    result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        _KERNEL32 = copying_kernel32_proxy
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
        """
        _FILE_ID_EXTD_DIR_INFO.from_buffer = copying_structure_view
        def _win_inventory(handle, state):
            result = []
            buffer = ctypes.create_string_buffer(65536)
            while True:
                _KERNEL32.GetFileInformationByHandleEx(handle, 19, buffer, len(buffer))
                entry = _FILE_ID_EXTD_DIR_INFO.from_buffer(buffer)
                name = ctypes.wstring_at(buffer)
                state.include((name,), identity)
                result.append(_InventoryEntry(name, identity))
            return tuple(result)
        """,
    )
    def with_canonical_ctypes_import(source: str) -> str:
        authority = """
            import ctypes
            class _FILE_ID_EXTD_DIR_INFO(ctypes.Structure):
                _fields_ = []
            _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
        """
        return textwrap.dedent(authority) + textwrap.dedent(source)

    _assert_incremental_windows_decoder(with_canonical_ctypes_import(valid))
    _assert_incremental_windows_decoder(with_canonical_ctypes_import(valid_realistic))
    _assert_incremental_windows_decoder(with_canonical_ctypes_import(valid_named_constant))
    _assert_incremental_windows_decoder(
        with_canonical_ctypes_import(valid_with_metadata_query)
    )
    _assert_incremental_windows_decoder(
        with_canonical_ctypes_import(valid_with_unrelated_nested_scope)
    )
    _assert_incremental_windows_decoder(
        with_canonical_ctypes_import(valid_with_unrelated_comprehension)
    )
    _assert_incremental_windows_decoder(
        with_canonical_ctypes_import(valid_with_nested_safe_capture)
    )
    _assert_incremental_windows_decoder(
        with_canonical_ctypes_import(valid_with_safe_python_bindings)
    )
    for source in mutants:
        with pytest.raises(AssertionError):
            _assert_incremental_windows_decoder(with_canonical_ctypes_import(source))


def _decode_windows_native_batch(buffer, size: int) -> tuple[tuple[str, str], ...]:
    """Count and decode one FILE_ID_EXTD_DIR_INFO chain from ABI literals."""
    if size < 88:
        raise AssertionError("Windows enumeration buffer is smaller than its ABI header")
    raw = ctypes.string_at(buffer, size)
    offset = 0
    records: list[tuple[str, str]] = []
    while True:
        if offset + 88 > size:
            raise AssertionError("Windows enumeration record header is truncated")
        next_offset = int.from_bytes(raw[offset : offset + 4], "little")
        name_length = int.from_bytes(raw[offset + 60 : offset + 64], "little")
        if name_length == 0 or name_length % 2:
            raise AssertionError("Windows enumeration name length is invalid")
        name_end = offset + 88 + name_length
        if name_end > size:
            raise AssertionError("Windows enumeration name crosses the native buffer")
        name = raw[offset + 88 : name_end].decode("utf-16-le", errors="strict")
        if name not in {".", ".."}:
            records.append((name, raw[offset + 72 : offset + 88].hex()))
        if next_offset == 0:
            break
        if next_offset < 88 or next_offset % 8:
            raise AssertionError("Windows enumeration NextEntryOffset is invalid")
        offset += next_offset
        if offset >= size:
            raise AssertionError("Windows enumeration chain escapes the native buffer")
    return tuple(records)


def _assert_decoder_observation(
    decoded_names: int, constructed_records: int, *, rejected: bool
) -> None:
    is_windows_decoder = inventory_once_helper_name() == "_win_inventory"
    assert not is_windows_decoder or decoded_names > 0
    if decoded_names:
        difference = decoded_names - constructed_records
        assert difference in ({0, 1} if rejected else {0})


def _exercise_product_owned_budget(
    module,
    monkeypatch: pytest.MonkeyPatch,
    session,
    paths,
    *,
    expected_count: int,
) -> None:
    semantic = importlib.import_module("executor_birth_semantic_authority")
    authority, public, evidence = paths
    operation_budgets = []
    for _ in range(2):
        observed = []
        original = module._SecureRootSession._inventory_state
        real_entry = module._InventoryEntry
        inventory_scope: tuple[str, ...] = ()
        distinct_records: set[tuple[tuple[str, ...], str, str]] = set()

        def counted_entry(*args, **kwargs):
            name = kwargs["name"] if "name" in kwargs else args[0]
            identity = kwargs["identity"] if "identity" in kwargs else args[1]
            key = (
                inventory_scope + (name,),
                identity.volume,
                identity.object_id,
            )
            if key not in distinct_records:
                if len(distinct_records) == 4096:
                    raise AssertionError(
                        "productive loader constructed its 4097th aggregate record"
                    )
                distinct_records.add(key)
            return real_entry(*args, **kwargs)

        def traced_inventory(active, components, *args, **kwargs):
            nonlocal inventory_scope
            normalized = tuple(components)
            inventory_scope = normalized
            budget = kwargs.get("budget")
            if budget is None and args:
                budget = args[0]
            observed.append((normalized, budget))
            return original(active, components, *args, **kwargs)

        with monkeypatch.context() as scoped:
            scoped.setattr(
                module._SecureRootSession, "_inventory_state", traced_inventory
            )
            scoped.setattr(module, "_InventoryEntry", counted_entry)
            if expected_count == 4097:
                with pytest.raises(module.BirthSecureFSError) as caught:
                    with session.global_lock(exclusive=False, create=False):
                        semantic._load_semantic_authority_in_session(
                            authority, public, evidence, session
                        )
                assert caught.value.code == "birth_provisioning_recovery_ambiguous"
                assert len(distinct_records) == 4096
            else:
                with session.global_lock(exclusive=False, create=False):
                    loaded = semantic._load_semantic_authority_in_session(
                        authority, public, evidence, session
                    )
                assert set(loaded.verifier_keys) == {"review-key"}
                assert len(distinct_records) == 4096
        operation_budgets.append(
            _single_operation_budget(
                observed,
                {public, evidence},
                module._InventoryBudgetV1,
            )
        )
    assert operation_budgets[0] is not operation_budgets[1]


def _mutating_inventory(module, monkeypatch, root: Path, session, case: str) -> None:
    # ``anchor.bin`` is materialised before the session so the exact catalogue
    # keeps its binding.  A name created after the adoption is unknown to the
    # session and would be refused on the first scan, hiding the mutation
    # oracle behind an unrelated rejection.
    helper_name = inventory_once_helper_name()
    original = getattr(module, helper_name)
    calls = 0

    def barrier(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            if case == "add-between-scans":
                write_private(root / "added.bin", b"added")
            elif case == "remove-between-scans":
                (root / "anchor.bin").unlink()
            elif case == "rename-between-scans":
                (root / "anchor.bin").rename(root / "renamed.bin")
            else:
                (root / "anchor.bin").unlink()
                write_private(root / "anchor.bin", b"replacement")
        return result

    monkeypatch.setattr(module, helper_name, barrier)
    with pytest.raises(module.BirthSecureFSError) as caught:
        with session.global_lock(exclusive=False, create=False):
            session._inventory_state(())
    assert caught.value.code == "birth_provisioning_recovery_ambiguous"
    assert str(caught.value) == caught.value.code
    assert calls == 2


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_inventory_closure_and_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    bindings = [lock_role_binding(module)]
    semantic_paths = None
    if case.endswith("between-scans"):
        for name in ("anchor.bin", "added.bin", "renamed.bin"):
            bindings.append(
                role_binding(
                    module, (name,), directory=False, role=private_role(module)
                )
            )
    elif case == "non-json-entry":
        bindings.append(
            role_binding(
                module,
                ("opaque.bin",),
                directory=False,
                role=private_role(module),
            )
        )
    elif case.startswith("local-"):
        count = int(case.split("-")[1])
        root_payload_count = count - 1
        bindings.extend(
            role_binding(
                module,
                (f"root-entry-{index:04d}.bin",),
                directory=False,
                role=private_role(module),
            )
            for index in range(root_payload_count)
        )
        bindings.append(
            role_binding(
                module, ("local",), directory=True, role=private_role(module)
            )
        )
        bindings.extend(
            role_binding(
                module,
                ("local", f"entry-{index:04d}.bin"),
                directory=False,
                role=private_role(module),
            )
            for index in range(count)
        )
    else:
        aggregate_count = 4096 if case == "aggregate-4096" else 4097
        authority, public, evidence, semantic_bindings = (
            _provision_semantic_operation(
                root, module, inventory_count=aggregate_count
            )
        )
        semantic_paths = (authority, public, evidence)
        bindings.extend(semantic_bindings)

    # Every object the session must resolve is materialised before adoption:
    # an exact catalogue only keeps bindings for names that already exist, and
    # a name created afterwards outside the session has no overlay entry.
    if case.endswith("between-scans"):
        write_private(root / "anchor.bin", b"anchor")
    elif case == "non-json-entry":
        write_private(root / "opaque.bin", b"not-json")
    elif case.startswith("local-"):
        for index in range(root_payload_count):
            write_private(root / f"root-entry-{index:04d}.bin", b"")
        mkdir_private(root / "local")
        for index in range(count):
            write_private(root / "local" / f"entry-{index:04d}.bin", b"")
        bindings = [
            binding
            for binding in bindings
            if binding.components != ("local",)
            or binding.kind is module._ObjectKind.directory
        ]

    with open_session(root, role_bindings=tuple(bindings)) as session:
        with session.global_lock(exclusive=True, create=True):
            pass
        if case.endswith("between-scans"):
            _mutating_inventory(module, monkeypatch, root, session, case)
            return
        if case == "non-json-entry":
            with session.global_lock(exclusive=False, create=False):
                entries = session._inventory_state(())
            entry = next(item for item in entries if item.name == "opaque.bin")
            assert entry.size == len(b"not-json")
            return
        if case.startswith("local-"):
            _assert_windows_decoder_guard_rejects_mutants()
            _assert_incremental_windows_decoder(
                inspect.getsource(module)
            )
            count = int(case.split("-")[1])
            root_payload_count = count - 1
            real_entry = module._InventoryEntry
            inventory_scope: tuple[str, ...] = ()
            distinct_records: set[tuple[tuple[str, ...], str, str]] = set()
            decoded_names = 0
            accounting_attempts = 0
            constructed_records = 0
            expected_batch: tuple[tuple[str, str], ...] | None = None
            expected_index = 0
            phase = "idle"
            accounted_budget = None
            accounted_key = None

            is_windows_decoder = inventory_once_helper_name() == "_win_inventory"
            real_native_query = (
                module._KERNEL32.GetFileInformationByHandleEx
                if is_windows_decoder
                else None
            )
            real_budget_include = (
                module._InventoryBudgetV1.include if is_windows_decoder else None
            )

            def scalar(value) -> int:
                return int(getattr(value, "value", value) or 0)

            def require_complete_batch() -> None:
                nonlocal expected_batch, expected_index, phase
                if expected_batch is None:
                    return
                assert expected_index == len(expected_batch), (
                    "Windows inventory requested a new native batch before "
                    "consuming every record of the preceding batch"
                )
                assert phase == "await-decode"
                expected_batch = None
                expected_index = 0
                phase = "idle"

            def reset_trace() -> None:
                nonlocal decoded_names, accounting_attempts, constructed_records
                nonlocal expected_batch, expected_index, phase
                nonlocal accounted_budget, accounted_key
                decoded_names = 0
                accounting_attempts = 0
                constructed_records = 0
                expected_batch = None
                expected_index = 0
                phase = "idle"
                accounted_budget = None
                accounted_key = None

            def counted_entry(*args, **kwargs):
                nonlocal constructed_records, expected_index, phase
                name = kwargs["name"] if "name" in kwargs else args[0]
                identity = kwargs["identity"] if "identity" in kwargs else args[1]
                key = (
                    inventory_scope + (name,),
                    identity.volume,
                    identity.object_id,
                )
                if is_windows_decoder:
                    assert expected_batch is not None and expected_index < len(
                        expected_batch
                    )
                    expected_name, expected_object_id = expected_batch[expected_index]
                    assert phase == "await-construct", (
                        "Windows InventoryEntry was built before real budget accounting"
                    )
                    assert name == expected_name
                    assert identity.object_id == expected_object_id
                    expected_key = (inventory_scope + (name,), identity)
                    assert accounted_key == expected_key
                    assert accounted_budget is not None and expected_key in accounted_budget._seen
                if key not in distinct_records:
                    if len(distinct_records) == 4096:
                        raise AssertionError(
                            "the 4097th distinct inventory record was constructed"
                        )
                    distinct_records.add(key)
                result = real_entry(*args, **kwargs)
                constructed_records += 1
                if is_windows_decoder:
                    expected_index += 1
                    phase = "await-decode"
                return result

            real_wstring_at = ctypes.wstring_at

            def decoded_name(*args, **kwargs):
                nonlocal decoded_names, phase
                name = real_wstring_at(*args, **kwargs)
                if name not in {".", ".."}:
                    assert expected_batch is not None and expected_index < len(
                        expected_batch
                    )
                    expected_name, _ = expected_batch[expected_index]
                    assert phase == "await-decode", (
                        "Windows decoder advanced before accounting and construction"
                    )
                    assert name == expected_name
                    decoded_names += 1
                    phase = "await-account"
                return name

            def counted_include(budget, path, identity):
                nonlocal accounting_attempts, phase
                nonlocal accounted_budget, accounted_key
                assert real_budget_include is not None
                assert expected_batch is not None and expected_index < len(expected_batch)
                expected_name, expected_object_id = expected_batch[expected_index]
                assert phase == "await-account", (
                    "Windows budget accounting did not immediately follow decoding"
                )
                assert tuple(path) == inventory_scope + (expected_name,)
                assert identity.object_id == expected_object_id
                before = set(budget._seen)
                key = (tuple(path), identity)
                accounting_attempts += 1
                try:
                    result = real_budget_include(budget, path, identity)
                except module.BirthSecureFSError:
                    assert key not in before and len(before) == 4096
                    assert set(budget._seen) == before
                    phase = "rejected"
                    raise
                after = set(budget._seen)
                if key in before:
                    assert after == before
                else:
                    assert after == before | {key}
                accounted_budget = budget
                accounted_key = key
                phase = "await-construct"
                return result

            def next_native_batch(*args, **kwargs):
                nonlocal expected_batch, expected_index, phase
                assert real_native_query is not None
                information_class = scalar(args[1])
                if information_class not in {19, 20}:
                    return real_native_query(*args, **kwargs)
                require_complete_batch()
                result = real_native_query(*args, **kwargs)
                if result:
                    expected_batch = _decode_windows_native_batch(
                        args[2], scalar(args[3])
                    )
                    expected_index = 0
                    phase = "await-decode"
                return result

            def assert_trace_finished(*, rejected: bool) -> None:
                if not is_windows_decoder:
                    return
                if rejected:
                    assert phase == "rejected"
                    assert decoded_names == constructed_records + 1
                    assert accounting_attempts == decoded_names
                else:
                    require_complete_batch()
                    assert phase == "idle"
                    assert decoded_names == accounting_attempts == constructed_records

            monkeypatch.setattr(module, "_InventoryEntry", counted_entry)
            if is_windows_decoder:
                monkeypatch.setattr(
                    module._InventoryBudgetV1, "include", counted_include
                )
            real_scandir = os.scandir

            class IncrementalScandir:
                def __init__(self, path):
                    self._inner = real_scandir(path)
                    self._yielded = 0

                def __enter__(self):
                    self._inner.__enter__()
                    return self

                def __exit__(self, *args):
                    return self._inner.__exit__(*args)

                def __iter__(self):
                    return self

                def __next__(self):
                    if self._yielded and len(distinct_records) < self._yielded:
                        raise AssertionError(
                            "inventory enumerated ahead of record construction"
                        )
                    item = next(self._inner)
                    self._yielded += 1
                    return item

            monkeypatch.setattr(os, "scandir", IncrementalScandir)
            monkeypatch.setattr(
                os,
                "listdir",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("inventory materialized names with os.listdir")
                ),
            )

            with monkeypatch.context() as decoder_patch:
                decoder_patch.setattr(ctypes, "wstring_at", decoded_name)
                if is_windows_decoder:
                    decoder_patch.setattr(
                        module._KERNEL32,
                        "GetFileInformationByHandleEx",
                        next_native_batch,
                    )
                if count == 4097:
                    with pytest.raises(module.BirthSecureFSError) as caught:
                        with session.global_lock(exclusive=False, create=False):
                            session._inventory_state(())
                    assert caught.value.code == "birth_provisioning_recovery_ambiguous"
                    assert len(distinct_records) == 4096
                    assert_trace_finished(rejected=True)
                else:
                    with session.global_lock(exclusive=False, create=False):
                        root_entries = session._inventory_state(())
                    assert {item.name for item in root_entries} == {
                        "provisioning-v1.lock",
                        *(f"root-entry-{index:04d}.bin" for index in range(4095)),
                    }
                    assert len(distinct_records) == 4096
                    assert_trace_finished(rejected=False)
            _assert_decoder_observation(
                decoded_names, constructed_records, rejected=count == 4097
            )

            distinct_records.clear()
            inventory_scope = ("local",)
            reset_trace()
            with monkeypatch.context() as decoder_patch:
                decoder_patch.setattr(ctypes, "wstring_at", decoded_name)
                if is_windows_decoder:
                    decoder_patch.setattr(
                        module._KERNEL32,
                        "GetFileInformationByHandleEx",
                        next_native_batch,
                    )
                if count == 4097:
                    with pytest.raises(module.BirthSecureFSError) as caught:
                        with session.global_lock(exclusive=False, create=False):
                            session._inventory_state(("local",))
                    assert caught.value.code == "birth_provisioning_recovery_ambiguous"
                    assert len(distinct_records) == 4096
                    assert_trace_finished(rejected=True)
                else:
                    with session.global_lock(exclusive=False, create=False):
                        local_entries = session._inventory_state(("local",))
                    assert {item.name for item in local_entries} == {
                        f"entry-{index:04d}.bin" for index in range(4096)
                    }
                    assert len(distinct_records) == 4096
                    assert_trace_finished(rejected=False)
            _assert_decoder_observation(
                decoded_names, constructed_records, rejected=count == 4097
            )
            return

        assert semantic_paths is not None
        _exercise_product_owned_budget(
            module,
            monkeypatch,
            session,
            semantic_paths,
            expected_count=(4096 if case == "aggregate-4096" else 4097),
        )
