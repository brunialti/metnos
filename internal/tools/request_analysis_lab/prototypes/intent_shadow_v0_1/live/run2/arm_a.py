"""RUN 2 arm A: current Metnos intent extractor through a read-only adapter."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from hashlib import sha256
import base64
import os
import sqlite3
import sys
from typing import Any, Iterator

from .protocol import (
    CONTROL_REGISTRY_PATH,
    CONTROL_SNAPSHOT_PATH,
    LIVE_LIMITS,
    ROOT,
    ProtocolError,
    apply_generation_profile,
    canonical_json_bytes,
    load_control_snapshot,
    strict_json_file,
    strict_json_loads,
    verify_control_environment,
)


ARM_ID = "A"


@lru_cache(maxsize=1)
def _snapshot() -> dict[str, Any]:
    snapshot = load_control_snapshot(CONTROL_SNAPSHOT_PATH)
    verify_control_environment(snapshot)
    return snapshot


@contextmanager
def _runtime_imports() -> Iterator[tuple[Any, Any, Any, Any]]:
    _snapshot()
    runtime_path = str(ROOT / "runtime")
    inserted = runtime_path not in sys.path
    if inserted:
        sys.path.insert(0, runtime_path)
    previous_scaffold = os.environ.get("METNOS_INTENT_SCAFFOLD")
    os.environ["METNOS_INTENT_SCAFFOLD"] = "0"
    previous_dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        import detection_lexicon
        import i18n
        import intent_extractor
        import prompt_loader
        import vocab
        old_connection = detection_lexicon._conn
        old_seeded = detection_lexicon._seeded
        connection = sqlite3.connect(
            "file:/home/roberto/.local/share/metnos/detection.sqlite?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        detection_lexicon._conn = connection
        detection_lexicon._seeded = True
        try:
            yield intent_extractor, prompt_loader, vocab, i18n
        finally:
            detection_lexicon._conn = old_connection
            detection_lexicon._seeded = old_seeded
            connection.close()
    finally:
        sys.dont_write_bytecode = previous_dont_write
        if previous_scaffold is None:
            os.environ.pop("METNOS_INTENT_SCAFFOLD", None)
        else:
            os.environ["METNOS_INTENT_SCAFFOLD"] = previous_scaffold
        if inserted:
            try:
                sys.path.remove(runtime_path)
            except ValueError:
                pass


@lru_cache(maxsize=16)
def rendered_prompt(language: str) -> str:
    if type(language) is not str:
        raise TypeError("language")
    with _runtime_imports() as (_extractor, loader, vocab, i18n):
        with i18n.language_context(language):
            return loader.get(
                "intent_extractor_v4",
                language,
                verbs_inline=vocab.render_actions_inline(),
                objects_inline=vocab.render_objects_inline(),
                boundaries_block=vocab.render_boundaries(language),
            )


def build_request(query: str, language: str) -> dict[str, Any]:
    if type(query) is not str or not query.strip():
        raise TypeError("query")
    request = {
        "messages": [
            {"role": "system", "content": rendered_prompt(language)},
            {"role": "user", "content": query},
        ]
    }
    return apply_generation_profile(request)


@lru_cache(maxsize=1)
def _operation_by_pair() -> dict[tuple[Any, Any], str]:
    registry = strict_json_file(CONTROL_REGISTRY_PATH)
    return {
        (metadata["verb"], metadata["object"]): route
        for route, metadata in registry["operations"].items()
    }


def _semantic_from_current(result: Any, query: str, language: str) -> dict[str, Any]:
    with _runtime_imports() as (_extractor, _loader, _vocab, i18n):
        import detection_lexicon as detection
        with i18n.language_context(language):
            if detection.match("undo.intent_bypass", query):
                return {"kind": "system_control", "control": "undo_last_turn"}
    if type(result) is not dict:
        return {"kind": "unrepresentable", "reason": "no_actionable_intent"}
    actions = result.get("actions")
    if type(actions) is not list:
        actions = [{"verb": result.get("verb"), "object": result.get("object")}]
    tokens: list[str] = []
    by_pair = _operation_by_pair()
    for action in actions:
        if type(action) is not dict:
            return {"kind": "unrepresentable", "reason": "outside_registry"}
        pair = (action.get("verb"), action.get("object"))
        if pair == ("get", "approval"):
            tokens.append("get/approval")
            continue
        route = by_pair.get(pair)
        if route is None:
            return {"kind": "unrepresentable", "reason": "outside_registry"}
        tokens.append(route)
    if not tokens:
        return {"kind": "unrepresentable", "reason": "no_actionable_intent"}

    def body(items: list[str]) -> list[dict[str, Any]] | None:
        nodes: list[dict[str, Any]] = []
        for index, token in enumerate(items):
            if token != "get/approval":
                nodes.append({"kind": "operation", "route": token})
                continue
            owned = body(items[index + 1:])
            if not owned:
                return None
            nodes.append({
                "kind": "barrier", "barrier": "get/approval",
                "cases": [
                    {"outcome": "approved", "body": owned},
                    {"outcome": "rejected", "body": []},
                ],
            })
            return nodes
        return nodes

    graph = body(tokens)
    if not graph:
        return {"kind": "unrepresentable", "reason": "missing_required_information"}
    return {"kind": "operation_graph", "body": graph}


def extract_response(raw_content: bytes, *, query: str, language: str) -> dict[str, Any]:
    if type(raw_content) is not bytes or type(query) is not str or type(language) is not str:
        raise TypeError("raw/query/language")
    raw_sha = sha256(raw_content).hexdigest()
    raw_b64 = base64.b64encode(raw_content).decode("ascii")
    try:
        decoded = strict_json_loads(raw_content, LIVE_LIMITS)
    except ProtocolError as exc:
        return {
            "adapter_version": "current-control/run2", "status": "technical_invalid",
            "raw_model_output_b64": raw_b64, "raw_model_output_sha256": raw_sha,
            "decoded_document": None, "semantic_document": None,
            "technical_failure": str(exc), "issues": ["TECHNICAL_INVALID"],
            "adapter_metadata": {
                "primary_response_consumed": False, "current_result_present": False,
                "implicit_actions_ignored": False,
                "semantic_document_source": "technical_limit_precedes_adapter",
            },
        }
    try:
        response_text = raw_content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        response_text = ""
    calls = 0

    def one_response(_system: str, _user: str, *, max_tokens: int) -> str:
        nonlocal calls
        del max_tokens
        calls += 1
        if calls > 1:
            raise RuntimeError("single-request protocol: secondary probe disabled")
        return response_text

    with _runtime_imports() as (extractor, _loader, _vocab, i18n):
        with i18n.language_context(language):
            try:
                current = extractor.extract_intent(query, one_response)
            except Exception:
                current = None
    semantic = _semantic_from_current(current, query, language)
    return {
        "adapter_version": "current-control/run2", "status": "valid_representable" if semantic["kind"] != "unrepresentable" else "valid_unrepresentable",
        "raw_model_output_b64": raw_b64, "raw_model_output_sha256": raw_sha,
        "decoded_document": decoded, "semantic_document": semantic,
        "semantic_sha256": sha256(canonical_json_bytes(semantic)).hexdigest(),
        "technical_failure": None, "issues": [],
        "adapter_metadata": {
            "primary_response_consumed": calls == 1,
            "current_result_present": type(current) is dict,
            "implicit_actions_ignored": bool(type(current) is dict and current.get("implicit_actions")),
            "semantic_document_source": "current_extractor_result_plus_registry_adapter",
        },
    }
