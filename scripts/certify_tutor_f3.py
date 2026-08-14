#!/usr/bin/env python3
"""Certify F3 live observations, document identity, and mixed handoff.

The certification uses the admitted production catalog and local model, but
keeps its one-time dialog in a temporary directory.  It never executes the
operational clause and leaves no pending interaction or user association.
"""

from __future__ import annotations

import argparse
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))


def _answer_payload(answer) -> dict | None:
    if answer is None:
        return None
    # F4 evidence contains the full embedding vector and is deliberately not
    # part of this human-readable F3 certificate.
    return {
        field.name: getattr(answer, field.name)
        for field in fields(answer)
        if field.name != "evidence"
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="host")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    from published_docs import resolve_reference
    from services_registry import snapshots
    from tutor.catalog import admitted_catalog_version, load_knowledge_units
    from tutor.handoff import create_pending
    from tutor.models import TutorPrincipal, TutorRequest
    from tutor.service import answer_request
    import dialog_pending

    mixed_query = "Come funziona la pagina Servizi? Poi cerca README.md"
    explanation_clause = "Come funziona la pagina Servizi?"
    action_clause = "cerca README.md"
    observation_query = "Qual è lo stato corrente dei servizi?"
    document_query = "cosa contiene il file metnos_prospettive_estese_v1.html"
    principal = TutorPrincipal(
        user_id=args.owner,
        actor="host",
        audience="instance_admin",
        channel="http",
        conversation_id="f3-cert-conversation",
    )
    catalog_version = admitted_catalog_version()
    mixed = answer_request(TutorRequest(mixed_query, "it", principal))
    observation = answer_request(TutorRequest(
        observation_query, "it", principal))

    pending = None
    with tempfile.TemporaryDirectory(prefix="metnos-tutor-f3-") as temporary:
        dialog_pending.DIALOG_DIR = Path(temporary) / "get_inputs"
        handed = (
            create_pending(
                sender_id="f3-cert-sender",
                principal=principal,
                action_query=mixed.handoff_query,
                catalog_version=catalog_version,
                answer=mixed,
            )
            if mixed is not None and mixed.handoff_query else mixed
        )
        if handed is not None and handed.pending_dialog_id:
            state = dialog_pending.load_pending(
                "f3-cert-sender", handed.pending_dialog_id,
                owner_user_id=principal.user_id) or {}
            choices = [
                choice.get("value")
                for step in state.get("dialog", ())
                for choice in (step.get("schema") or {}).get("choices", ())
            ]
            callback = state.get("on_complete") or {}
            pending = {
                "dialog_id": state.get("dialog_id"),
                "owner_user_id": state.get("owner_user_id"),
                "conversation_id": state.get("conversation_id"),
                "timeout_s": state.get("timeout_s"),
                "choices": choices,
                "callback_type": callback.get("type"),
                "literal_query": callback.get("literal_query"),
                "query_hash": callback.get("query_hash"),
                "catalog_version": callback.get("catalog_version"),
            }

    document = resolve_reference(document_query, lang="it")
    document_answer = answer_request(TutorRequest(
        document_query, "it", principal))
    relative_path = document.relative_path if document is not None else ""
    document_units = {
        unit.unit_id
        for unit in load_knowledge_units()
        if unit.source_ref.split("#", 1)[0] == f"docs/{relative_path}"
    }
    answer_unit_ids = {
        source_id.removeprefix("knowledge:")
        for source_id in (
            document_answer.source_ids if document_answer is not None else ())
    }
    llm = next(
        (row for row in snapshots() if row.get("key") == "llm"), {})

    checks = {
        "answer_is_handoff": bool(mixed and mixed.esito == "handoff"),
        "semantic_mixed_detection": bool(
            mixed and mixed.detection == "semantic_mixed_handoff"),
        "literal_action_preserved": bool(
            mixed and mixed.handoff_query == action_clause),
        "literal_hash_bound": bool(
            pending and pending["query_hash"] == hashlib.sha256(
                action_clause.encode("utf-8")).hexdigest()),
        "pending_created": bool(pending),
        "owner_bound": bool(
            pending and pending["owner_user_id"] == principal.user_id
            and pending["conversation_id"] == principal.conversation_id),
        "closed_choices": bool(
            pending and pending["choices"] == ["execute", "cancel"]),
        # A static explanation must not acquire live authority merely because
        # retrieval selected the Services page. Exercise the signed live view
        # through a separate, explicit observation request instead.
        "service_probe_ok": bool(
            observation
            and ("service_health", "ok") in observation.probe_statuses),
        "service_observation_is_live": bool(
            observation
            and observation.detection == "semantic_live_observation"
            and "view:SERVICES_STATUS" in observation.source_ids
            and "probe:service_health" in observation.source_ids),
        "catalog_version_bound": bool(
            pending and pending["catalog_version"] == catalog_version),
        "local_llm_observed_running": bool(
            llm.get("installed") and llm.get("healthy") is True
            and llm.get("status") == "running"),
        "published_document_resolved": document is not None,
        "published_document_sources_bound": bool(
            document_units & answer_unit_ids),
        "published_document_no_action": bool(
            document_answer and not document_answer.handoff_query),
        "published_document_linked": bool(
            document and document_answer
            and document.canonical_url in document_answer.answer_md),
    }
    report = {
        "catalog_version": catalog_version,
        "query": mixed_query,
        "explanation_clause": explanation_clause,
        "action_clause": action_clause,
        "checks": checks,
        "answer": _answer_payload(mixed),
        "observation_query": observation_query,
        "observation_answer": _answer_payload(observation),
        "document_query": document_query,
        "document_source_ref": (
            f"docs/{relative_path}" if relative_path else ""),
        "document_answer": _answer_payload(document_answer),
        "pending": pending,
        "llm_service": {
            key: llm.get(key)
            for key in (
                "unit", "scope", "installed", "active_state", "healthy",
                "status",
            )
        },
        "passed": all(checks.values()),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
