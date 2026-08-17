#!/usr/bin/env python3
"""Compare the production intent extractor with the best V23lite arm.

The production data and state trees are never opened for writes. The script
reads the frozen held-out sample from the real turn history, but redirects all
runtime data/state/cache paths to a temporary directory before importing any
runtime module. The two model-backed arms run sequentially on the same query
order: the current extractor first, then V23lite plus the deterministic role
repair.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile
import time
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRATCH = pathlib.Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"


def _isolate_runtime_paths(tmp: pathlib.Path) -> None:
    """Redirect every potentially writable runtime tree outside production."""
    os.environ["METNOS_USER_DATA"] = str(tmp / "data")
    os.environ["METNOS_USER_STATE"] = str(tmp / "state")
    os.environ["METNOS_USER_CACHE"] = str(tmp / "cache")
    os.environ["METNOS_WORKSPACE"] = str(tmp / "workspace")
    os.environ["METNOS_LOG_FILE"] = str(tmp / "state" / "confronto.log")


def _route(verb: Any, obj: Any) -> str | None:
    verb = str(verb or "").strip().lower()
    obj = str(obj or "").strip().lower()
    if not verb and not obj:
        return None
    return f"{verb or 'none'}/{obj or 'none'}"


def _current_routes(intent: dict | None) -> list[str]:
    if not isinstance(intent, dict):
        return []
    actions = intent.get("actions")
    if isinstance(actions, list) and actions:
        routes = [_route(a.get("verb"), a.get("object"))
                  for a in actions if isinstance(a, dict)]
    else:
        routes = [_route(intent.get("verb"), intent.get("object"))]
    return [route for route in routes if route is not None]


def _implicit_routes(intent: dict | None) -> list[str]:
    if not isinstance(intent, dict):
        return []
    routes: list[str] = []
    for action in intent.get("implicit_actions") or []:
        if not isinstance(action, dict):
            continue
        route = _route(action.get("verb_canonical"), action.get("object"))
        if route is not None:
            routes.append(route)
    return routes


def _request_routes(frame: dict) -> list[str]:
    routes: list[str] = []
    for predicate in frame.get("predicates") or []:
        if not isinstance(predicate, dict) or predicate.get("role") != "request":
            continue
        route = _route(predicate.get("verb"), predicate.get("object"))
        if route is not None:
            routes.append(route)
    return routes


def _matching_substrings(forms: list[str], query: str) -> list[str]:
    low = query.lower()
    return [form for form in forms if form.lower() in low]


def _matching_words(forms: list[str], query: str) -> list[str]:
    low = query.lower()
    return [form for form in forms
            if re.search(r"\b" + re.escape(form.lower()) + r"\b", low)]


def _shortcut_evidence(query: str, intent: dict | None, llm_calls: int,
                       intent_extractor) -> dict:
    """Name the no-model branch and the exact lexicon entries that fired."""
    lexicon = intent_extractor._dl
    evidence: dict[str, Any] = {"source": "model", "entries": []}
    if llm_calls:
        return evidence

    undo = _matching_substrings(lexicon.forms("undo.intent_bypass"), query)
    if undo:
        return {"source": "shortcut:undo.intent_bypass",
                "entries": [{"concept": "undo.intent_bypass", "form": form}
                            for form in undo]}

    status = _matching_substrings(lexicon.forms("system.status_query"), query)
    if status:
        return {"source": "shortcut:system.status_query",
                "entries": [{"concept": "system.status_query", "form": form}
                            for form in status]}

    machine = _matching_substrings(lexicon.forms("machine.reference"), query)
    focus: list[dict[str, str]] = []
    for section, forms in lexicon.mapping("health.section_focus").items():
        for form in _matching_words(forms, query):
            focus.append({"concept": "health.section_focus", "key": section,
                          "form": form})
    if machine and focus and intent == {
            "verb": "get", "object": "processes", "confidence": 1.0}:
        return {
            "source": "shortcut:machine.reference+health.section_focus",
            "entries": ([{"concept": "machine.reference", "form": form}
                         for form in machine] + focus),
        }

    if intent is None:
        evidence["source"] = "no_model_no_route"
    else:
        evidence["source"] = "no_model_unclassified"
    return evidence


def _write_snapshot(path: pathlib.Path, payload: dict) -> None:
    """Persist a diagnostic checkpoint atomically inside the shadow lab."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="metnos-confronto-intento-") as td:
        _isolate_runtime_paths(pathlib.Path(td))
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(RUNTIME))
        sys.path.insert(0, str(SCRATCH))

        import prova_cieca as P
        import riparo_ruolo as RR
        import intent_extractor as IE
        from llm_router import LLMRouter
        from llm_workloads import tier_for

        queries, groups = P.sample()
        if len(queries) != P.SIZE or P.HELDOUT_SEED != 20260811:
            raise RuntimeError("held-out sample contract changed")

        output = SCRATCH / "confronto_intento.json"
        partial = SCRATCH / "confronto_intento.partial.json"
        payload: dict[str, Any] = {
            "heldout_seed": P.HELDOUT_SEED,
            "budget_v23lite": P.BUDGET,
            "query_count": len(queries),
            "groups": groups,
            "arm_order": ["current", "new_v23lite_role_repair"],
            "runtime_paths": "isolated_temporary",
            "queries": queries,
            "current": [],
            "new": [],
        }

        provider = LLMRouter().provider(tier_for("intent.extract"))
        print(f"confronto intento | {len(queries)} query | corrente poi nuovo",
              flush=True)
        print("runtime data/state/cache: temporary isolated trees", flush=True)

        current_started = time.perf_counter()
        for index, query in enumerate(queries, 1):
            traces: list[str] = []

            def llm_call(system: str, user: str, *, max_tokens: int = 80,
                         **kwargs):
                call_kwargs: dict[str, Any] = {"max_tokens": max_tokens,
                                               "request_timeout_s": 30.0}
                if kwargs.get("grammar") is not None:
                    call_kwargs["grammar"] = kwargs["grammar"]
                response = provider.chat(system, user, **call_kwargs)
                text = (getattr(response, "text", response) or "").strip()
                traces.append(text)
                return text

            started = time.perf_counter()
            error = ""
            try:
                intent = IE.extract_intent(query, llm_call)
            except Exception as exc:  # noqa: BLE001
                intent = None
                error = f"{type(exc).__name__}: {exc}"
            row = {
                "index": index - 1,
                "query": query,
                "intent": intent,
                "routes": _current_routes(intent),
                "implicit_routes": _implicit_routes(intent),
                "llm_calls": len(traces),
                "shortcut": _shortcut_evidence(query, intent, len(traces), IE),
                "error": error,
                "ms": (time.perf_counter() - started) * 1000,
            }
            payload["current"].append(row)
            print(f"corrente {index:3d}/{len(queries)} "
                  f"{row['routes'] or '-'} {row['shortcut']['source']}",
                  flush=True)
            if index % 5 == 0:
                _write_snapshot(partial, payload)

        payload["current_elapsed_s"] = time.perf_counter() - current_started
        _write_snapshot(partial, payload)

        module = P.arm("confronto_i", contract=False, drop_tie_break=False)
        if RR.install(module) is not True:
            raise RuntimeError("role repair was not installed")

        new_started = time.perf_counter()
        for index, query in enumerate(queries, 1):
            started = time.perf_counter()
            try:
                frame, info = module.folded_call(query, prompt_variant="v23lite")
                error = ""
            except Exception as exc:  # noqa: BLE001
                frame, info = {}, {"valid": False,
                                   "reason": type(exc).__name__}
                error = f"{type(exc).__name__}: {exc}"
            raw_routes = _request_routes(frame)
            row = {
                "index": index - 1,
                "query": query,
                "valid": bool(info.get("valid")),
                "reason": str(info.get("reason") or ""),
                "routes": raw_routes,
                "usable_routes": raw_routes if info.get("valid") else [],
                "frame": frame,
                "error": error,
                "ms": (time.perf_counter() - started) * 1000,
            }
            payload["new"].append(row)
            print(f"nuovo    {index:3d}/{len(queries)} "
                  f"{'valido' if row['valid'] else row['reason']} "
                  f"{raw_routes or '-'}", flush=True)
            if index % 5 == 0:
                _write_snapshot(partial, payload)

        payload["new_elapsed_s"] = time.perf_counter() - new_started
        rows = []
        for current, new in zip(payload["current"], payload["new"], strict=True):
            rows.append({
                "index": current["index"],
                "query": current["query"],
                "current_routes": current["routes"],
                "current_implicit_routes": current["implicit_routes"],
                "current_source": current["shortcut"]["source"],
                "new_valid": new["valid"],
                "new_reason": new["reason"],
                "new_routes": new["routes"],
                "new_usable_routes": new["usable_routes"],
                "agree_raw": current["routes"] == new["routes"],
                "agree_usable": current["routes"] == new["usable_routes"],
            })
        payload["comparison"] = rows
        payload["summary"] = {
            "agreement_raw": sum(row["agree_raw"] for row in rows),
            "agreement_usable": sum(row["agree_usable"] for row in rows),
            "disagreement_raw": sum(not row["agree_raw"] for row in rows),
            "disagreement_usable": sum(not row["agree_usable"] for row in rows),
            "current_empty": sum(not row["current_routes"] for row in rows),
            "new_empty_raw": sum(not row["new_routes"] for row in rows),
            "new_empty_usable": sum(not row["new_usable_routes"] for row in rows),
            "new_valid": sum(row["new_valid"] for row in rows),
            "current_shortcut": sum(
                row["current_source"].startswith("shortcut:") for row in rows),
        }
        _write_snapshot(output, payload)
        partial.unlink(missing_ok=True)
        print(json.dumps(payload["summary"], ensure_ascii=False, indent=2),
              flush=True)
        print(f"scritto {output.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
