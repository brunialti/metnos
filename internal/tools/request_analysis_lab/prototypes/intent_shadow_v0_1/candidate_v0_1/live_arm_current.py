"""Arm A: exact current Metnos intent extractor plus a registry adapter.

No surface query or gold record is embedded here.  Production is imported
read-only only after its snapshot has passed byte and logical-state checks.
The adapter converts the current verb/object/actions carrier to the reviewed
registry, failing the whole compound closed if any indispensable action is not
registered.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import base64
from hashlib import sha256
import os
import sqlite3
import sys
from typing import Any, Iterator

from intent_shadow_extract import envelope_json, extract_raw_json
from intent_shadow_io import StrictJsonError, canonical_json_bytes, strict_json_loads
from intent_shadow_registry import load_frozen_registry, registry_payload_sha256
from live_protocol import (
    CONTROL_SNAPSHOT_PATH,
    LIVE_LIMITS,
    REGISTRY_PATH,
    ROOT,
    load_control_snapshot,
    openai_request,
    verify_control_environment,
)


ARM_ID = "A"


@lru_cache(maxsize=1)
def _verified_snapshot() -> dict[str, Any]:
    snapshot = load_control_snapshot(CONTROL_SNAPSHOT_PATH)
    verify_control_environment(snapshot)
    return snapshot


@contextmanager
def _runtime_imports() -> Iterator[tuple[Any, Any, Any, Any]]:
    _verified_snapshot()
    runtime = str(ROOT / "runtime")
    inserted = runtime not in sys.path
    if inserted:
        sys.path.insert(0, runtime)
    old_scaffold = os.environ.get("METNOS_INTENT_SCAFFOLD")
    os.environ["METNOS_INTENT_SCAFFOLD"] = "0"
    try:
        import detection_lexicon
        import i18n
        import intent_extractor
        import prompt_loader
        import vocab
        old_connection = detection_lexicon._conn
        old_seeded = detection_lexicon._seeded
        read_only = sqlite3.connect(
            "file:/home/roberto/.local/share/metnos/detection.sqlite?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        detection_lexicon._conn = read_only
        detection_lexicon._seeded = True
        try:
            yield intent_extractor, prompt_loader, vocab, i18n
        finally:
            detection_lexicon._conn = old_connection
            detection_lexicon._seeded = old_seeded
            read_only.close()
    finally:
        if old_scaffold is None:
            os.environ.pop("METNOS_INTENT_SCAFFOLD", None)
        else:
            os.environ["METNOS_INTENT_SCAFFOLD"] = old_scaffold
        if inserted:
            try:
                sys.path.remove(runtime)
            except ValueError:
                pass


def rendered_current_prompt(language: str) -> str:
    if type(language) is not str:
        raise TypeError("language must be exact string")
    with _runtime_imports() as (_extractor, prompt_loader, vocab, i18n):
        with i18n.language_context(language):
            return prompt_loader.get(
                "intent_extractor_v4",
                language,
                verbs_inline=vocab.render_actions_inline(),
                objects_inline=vocab.render_objects_inline(),
                boundaries_block=vocab.render_boundaries(language),
            )


def build_request(query: str, language: str) -> dict[str, Any]:
    return openai_request(rendered_current_prompt(language), query)


def _routes_from_result(result: Any, query: str, language: str) -> dict[str, Any]:
    registry, _identity = load_frozen_registry(REGISTRY_PATH)
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
    route_tokens: list[str] = []
    operation_by_pair = {
        (metadata["verb"], metadata["object"]): route
        for route, metadata in registry["operations"].items()
    }
    for action in actions:
        if type(action) is not dict:
            return {"kind": "unrepresentable", "reason": "outside_registry"}
        pair = (action.get("verb"), action.get("object"))
        if pair == ("get", "approval"):
            route_tokens.append("get/approval")
            continue
        route = operation_by_pair.get(pair)
        if route is None:
            return {"kind": "unrepresentable", "reason": "outside_registry"}
        route_tokens.append(route)
    if not route_tokens:
        return {"kind": "unrepresentable", "reason": "no_actionable_intent"}

    def body(tokens: list[str]) -> list[dict[str, Any]] | None:
        nodes: list[dict[str, Any]] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token != "get/approval":
                nodes.append({"kind": "operation", "route": token})
                index += 1
                continue
            owned = body(tokens[index + 1 :])
            if not owned:
                return None
            nodes.append(
                {
                    "kind": "barrier",
                    "barrier": "get/approval",
                    "cases": [
                        {"outcome": "approved", "body": owned},
                        {"outcome": "rejected", "body": []},
                    ],
                }
            )
            return nodes
        return nodes

    graph_body = body(route_tokens)
    if not graph_body:
        return {"kind": "unrepresentable", "reason": "missing_required_information"}
    return {"kind": "operation_graph", "body": graph_body}


def extract_response(raw_content: bytes, *, query: str, language: str) -> dict[str, Any]:
    if type(raw_content) is not bytes or type(query) is not str or type(language) is not str:
        raise TypeError("raw/query/language exact types required")
    registry, _identity = load_frozen_registry(REGISTRY_PATH)
    try:
        # The approved live limits precede semantic adaptation for both arms.
        # This deliberately rejects JSON constants/overflow/invalid Unicode as
        # technical failures, never as an abstention.
        strict_json_loads(raw_content, limits=LIVE_LIMITS)
    except StrictJsonError as exc:
        raw_sha = sha256(raw_content).hexdigest()
        return {
            "contract_version": registry["contract_version"],
            "registry_sha256": registry_payload_sha256(registry),
            "status": "technical_invalid",
            "raw_model_output_b64": base64.b64encode(raw_content).decode("ascii"),
            "raw_model_output_sha256": raw_sha,
            "decoded_document": None,
            "technical_failure": {"code": exc.code, "path": exc.path, "message": exc.message},
            "validation_result": {
                "valid": False,
                "issues": [{"code": "TECHNICAL_INVALID", "path": exc.path, "message": exc.code}],
            },
            "normalization_result": None,
            "projection_result": None,
            "adapter_metadata": {
                "primary_response_consumed": False,
                "current_result_present": False,
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
        calls += 1
        if calls > 1:
            # The current optional SITE/ELSEWHERE probe is deliberately not a
            # second measurement request; the extractor catches this and keeps
            # its primary classification.
            raise RuntimeError("single-request protocol: secondary probe disabled")
        return response_text

    with _runtime_imports() as (extractor, _loader, _vocab, i18n):
        with i18n.language_context(language):
            try:
                current_result = extractor.extract_intent(query, one_response)
            except Exception:
                current_result = None
    document = _routes_from_result(current_result, query, language)
    envelope = envelope_json(
        extract_raw_json(canonical_json_bytes(document), registry, limits=LIVE_LIMITS)
    )
    envelope["adapter_metadata"] = {
        "primary_response_consumed": calls == 1,
        "current_result_present": type(current_result) is dict,
        "implicit_actions_ignored": bool(
            type(current_result) is dict and current_result.get("implicit_actions")
        ),
        "semantic_document_source": "current_extractor_result_plus_registry_adapter",
    }
    return envelope
