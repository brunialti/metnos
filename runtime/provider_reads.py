# SPDX-License-Identifier: MIT
"""Reads from a provider on behalf of a tool Metnos built by itself.

Roberto, 13/11/2026: a generated tool may only search and read on Google. It
gets neither the network nor the account's token. When its code needs data it
calls ``provider_get``: that call ends the run with the request, the service
performs the read and runs the tool again with the answers collected so far.
The tool is a function of its arguments and of those answers, so running it
again is harmless, and nothing but a read ever leaves.

What a read may be (GET only, the provider's API bases only, no redirect) is
decided in the provider's own program, the last door before the token; here
the service only carries the request there.

Two halves in one module: ``provider_get`` runs inside the tool's sandbox,
``run`` in the service around the tool's invocation.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

CAPABILITY = "provider:read"
# Runtime-owned argument: the answers of the reads already done, in order.
RESPONSES_KEY = "_provider_responses"
# Runtime-owned result field: the one read the tool is waiting for.
REQUESTS_KEY = "provider_requests"
# Reads one invocation may ask for; each one also runs the tool once more.
MAX_READS = 20

_asked = 0


# ---- inside the tool ----------------------------------------------------------

def _address(url) -> tuple:
    """Where a read goes, without its parameters."""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ()
    return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.path.rstrip("/"))


def _leave(result: dict) -> None:
    """End this run with ``result`` as its whole output."""
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    os._exit(0)


def provider_get(args: dict, url: str, params: dict | None = None):
    """The JSON a provider API returns for one read.

    ``args`` is the tool's own ``args``, unchanged. The answer is there when
    the service (or a test case) already holds it; otherwise this run ends
    here and the tool is run again once the read is done. A read that fails
    never comes back: the service answers the person itself.
    """
    global _asked
    answers = args.get(RESPONSES_KEY) if isinstance(args, dict) else None
    index, _asked = _asked, _asked + 1
    if isinstance(answers, list) and index < len(answers):
        answer = answers[index]
        if isinstance(answer, dict) and _address(answer.get("url")) == _address(url) != ():
            return answer.get("response")
        from messages import get as _msg
        _leave({"ok": False, "error_class": "provider_read_order",
                "error": _msg("ERR_PROVIDER_READ_ORDER", position=index + 1,
                              asked=str(url)[:200])})
    _leave({"ok": False, REQUESTS_KEY: [{"url": str(url), "params": dict(params or {})}]})


# ---- in the service -----------------------------------------------------------

def _read_google(request: dict, executor_name: str, args_base: dict):
    from backends._google_api_runner import run_with_retry
    from backends._google_auth_common import auth_needs_inputs

    return run_with_retry(
        ["api", "get", "--url", request["url"],
         "--params", json.dumps(request["params"], ensure_ascii=False)],
        executor=executor_name, args_base=args_base,
        auth_handler=lambda base: auth_needs_inputs(
            base, executor=executor_name, result_kind="entries"))


# One reader per provider binding (`vocab.PROVIDER_SKILLS` values).
_READERS = {"google-workspace": _read_google}
BINDINGS = frozenset(_READERS)


def readable_bases(binding: str) -> tuple[str, ...]:
    """The addresses a read on this provider may begin with, taken from the
    provider's own program: the rule is written once, where it is enforced."""
    if binding != "google-workspace":
        return ()
    import ast
    from skill_wrapper import _skill_code_home

    source = (_skill_code_home(binding) / "scripts" / "google_api.py").read_text(
        encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
                getattr(target, "id", "") == "_READ_API_BASES" for target in node.targets):
            return tuple(ast.literal_eval(node.value))
    return ()


def under_a_base(url, bases) -> bool:
    """Whether ``url``, parameters aside, begins with one of ``bases``."""
    address = str(url or "").split("?", 1)[0].split("#", 1)[0]
    return any(address.startswith(base) or address + "/" == base for base in bases)


def declared_binding(capabilities) -> str | None:
    """The readable provider a list of declared capabilities names, if any."""
    for capability in capabilities or []:
        if not isinstance(capability, dict) or capability.get("name") != CAPABILITY:
            continue
        for hint in capability.get("hint") or []:
            if hint in _READERS:
                return hint
    return None


def binding_of(executor) -> str | None:
    """The provider this tool reads through the service, if it declares one."""
    return declared_binding(getattr(executor, "capabilities", None))


def _refused(code: str, error_class: str, **values) -> dict:
    from messages import get as _msg
    return {"ok": False, "error_class": error_class, "error_code": code,
            "error": _msg(code, **values), "entries": [], "used": 0}


def run(executor, args, invoke):
    """Invoke a tool, doing for it the provider reads it asks for.

    ``invoke(arguments)`` runs the tool once and returns its result. A tool
    that declares no provider read is invoked exactly as before.
    """
    binding = binding_of(executor)
    if binding is None:
        return invoke(args)
    # The answers are the service's own: whatever a plan put there is dropped.
    base = {key: value for key, value in (args or {}).items() if key != RESPONSES_KEY}
    answers: list[dict] = []
    while True:
        result = invoke({**base, RESPONSES_KEY: list(answers)})
        asked = result.get(REQUESTS_KEY) if isinstance(result, dict) else None
        if not isinstance(asked, list) or not asked:
            if isinstance(result, dict):
                result.pop(REQUESTS_KEY, None)
            return result
        request = asked[0]
        if (not isinstance(request, dict) or not isinstance(request.get("url"), str)
                or not isinstance(request.get("params"), dict)):
            return _refused("ERR_PROVIDER_READ_REFUSED", "provider_read_refused")
        if len(answers) >= MAX_READS:
            return {**_refused("ERR_PROVIDER_READ_LIMIT", "provider_read_limit",
                               limit=MAX_READS),
                    "cap_field": "provider_reads", "cap_value": MAX_READS}
        data, error = _READERS[binding](
            {"url": request["url"], "params": request["params"]},
            str(getattr(executor, "name", "") or ""), base)
        if error is not None:
            return error
        answers.append({"url": request["url"], "response": data})
