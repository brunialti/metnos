#!/usr/bin/env python3
"""Run the human F2 Tutor corpus without executing operational requests.

The harness calls the Tutor boundary directly.  A case expected to fall
through stops at that boundary: it is never passed to the planner or an
executor.  This makes false-steal certification safe and repeatable.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
CORPUS = ROOT / "tests/runtime/tutor/data/f2_human_certification.json"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))


NATURAL_LEADS = {
    "it": "Chiedi a Metnos con una richiesta come quella di questo esempio:",
    "en": "Ask Metnos with a request like this example:",
}
ANSWER_OUTCOMES = frozenset({"consolidata", "fondata"})
INTERNAL_MARKERS = (
    "object=", "actions=", "executor=", "source_kind=", "from_step",
    "CATALOG_AREAS", "CATALOG_PROVIDERS",
)


def load_corpus() -> dict[str, Any]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _contains(text: str, needle: str) -> bool:
    return needle.casefold() in text.casefold()


def _covers(text: str, value: str) -> bool:
    """Una voce di `must_cover`: literal substring, oppure gate a radice
    flessiva quando la voce e' "lex:<concept>" (delegato a
    detection_lexicon.match, che unisce le forme it∪en)."""
    if value.startswith("lex:"):
        import detection_lexicon
        return detection_lexicon.match(value[len("lex:"):], text)
    return _contains(text, value)


def _evaluate(case: dict[str, Any], answer) -> list[str]:
    expected = case["expected"]
    if expected == "fallthrough":
        return [] if answer is None else [f"Tutor ha intercettato: {answer.esito}"]
    if answer is None:
        return ["Tutor non ha risposto"]
    if expected == "answer" and answer.esito not in ANSWER_OUTCOMES:
        return [f"esito {answer.esito!r}, atteso risposta fondata"]
    if expected == "clarification" and answer.esito != "clarification":
        return [f"esito {answer.esito!r}, atteso clarification"]
    if expected == "restricted" and answer.esito != "restricted":
        return [f"esito {answer.esito!r}, atteso restricted"]

    text = answer.answer_md
    failures: list[str] = []
    route = case.get("route")
    if route and not _contains(text, route):
        failures.append(f"route assente: {route}")
    for alternatives in case.get("must_cover", []):
        if not any(_covers(text, value) for value in alternatives):
            failures.append("concetto assente: " + " | ".join(alternatives))
    for forbidden in case.get("must_not", []):
        if _contains(text, forbidden):
            failures.append(f"contenuto vietato: {forbidden}")
    language = str(case.get("lang") or "it").split("-", 1)[0]
    lead = NATURAL_LEADS.get(language)
    if case.get("natural_example") and lead and not text.lstrip().startswith(lead):
        failures.append("esempio naturale non in apertura")
    if route and case.get("channel") != "telegram":
        # Ratifica 24/7: vietata la NAVIGAZIONE via Telegram fuori dal canale
        # Telegram, non il nome del servizio omonimo (che una risposta sui
        # servizi deve poter citare). Il controllo è per riga: Telegram che
        # convive con un percorso di navigazione = confine violato.
        for line in text.splitlines():
            if "Telegram" in line and (route in line or "Settings >" in line):
                failures.append(
                    "navigazione Telegram fuori dal canale Telegram")
                break
    if case.get("channel") == "telegram" and route:
        if not _contains(text, "chat web"):
            failures.append("manca il confine chat web/Telegram")
    if expected == "answer":
        for marker in INTERNAL_MARKERS:
            if _contains(text, marker):
                failures.append(f"dettaglio interno esposto: {marker}")
    if case.get("registry_inventory") == "services":
        from services_registry import catalog
        for service in catalog():
            if not _contains(text, service.label):
                failures.append(f"servizio omesso: {service.label}")
    return failures


def _run_case(case: dict[str, Any], *, conversation_id: str):
    from tutor.conversation import recent_context, remember
    from tutor.models import TutorPrincipal, TutorRequest
    from tutor.service import answer_request

    principal = TutorPrincipal(
        user_id=f"cert-{case['audience']}",
        actor="host" if case["audience"] == "instance_admin" else "guest",
        audience=case["audience"],
        channel=case["channel"],
        conversation_id=conversation_id,
    )
    request = TutorRequest(
        query_redacted=case["query"],
        lang=case.get("lang", "it"),
        principal=principal,
        conversation_context=recent_context(principal),
    )
    answer = answer_request(request)
    if answer is not None:
        remember(request, answer)
    return answer


def _selected_cases(corpus: dict[str, Any], *, group: str, case_id: str,
                    f2_only: bool = False):
    selected: list[tuple[dict[str, Any], str]] = []
    for case in corpus["answer_cases"]:
        if group and case["group"] != group:
            continue
        if case_id and case["id"] != case_id:
            continue
        selected.append((case, f"cert-{case['id']}"))
    for acceptance in corpus.get("f1_equivalence_sets", []):
        if group and group != "f1_equivalence":
            continue
        if case_id and acceptance["id"] != case_id:
            continue
        for number, variant in enumerate(acceptance["queries"], start=1):
            lang = variant["lang"]
            case = {
                "id": f"{acceptance['id']}#{number}",
                "group": "f1_equivalence",
                "audience": acceptance["audience"],
                "channel": variant.get("channel", acceptance.get("channel", "http")),
                "query": variant["query"],
                "lang": lang,
                "expected": acceptance.get("expected", "answer"),
                "must_cover": acceptance.get("must_cover_by_lang", {}).get(lang, []),
            }
            for optional in ("route", "natural_example", "must_not"):
                if optional in acceptance:
                    case[optional] = acceptance[optional]
            if variant.get("known_equal_fail"):
                case["skip"] = ("eccezione documentata (ADR 0198: "
                                "pari-fallimento F1 misurato, card non "
                                "selezionata per questa formulazione)")
            if f2_only and acceptance.get("curated_card"):
                case["skip"] = ("card curata (RM-0003 §5.6, decisione c1): "
                                "servita dalla scheda pubblicata, fuori dal "
                                "perimetro F2-only")
            selected.append((case, f"cert-f1-{acceptance['id']}-{number}"))
    for flow in corpus["conversation_flows"]:
        if group and flow["group"] != group:
            continue
        if case_id and flow["id"] != case_id:
            continue
        conversation_id = f"cert-{flow['id']}"
        for number, turn in enumerate(flow["turns"], start=1):
            case = {
                "id": f"{flow['id']}#{number}",
                "group": flow["group"],
                "audience": flow["audience"],
                "channel": flow["channel"],
                **turn,
            }
            selected.append((case, conversation_id))
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", default="",
                        choices=("", "admin_ui", "typical_operations",
                                 "boundary", "conversation",
                                 "f1_equivalence"))
    parser.add_argument("--id", default="", help="case or conversation id")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--stop-on-fail", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--f2-only", action="store_true",
        help="exclude every legacy F1 card from retrieval",
    )
    parser.add_argument(
        "--catalog-dir", type=Path,
        help="use an isolated Tutor catalog without replacing other user data",
    )
    args = parser.parse_args()

    corpus = load_corpus()
    selected = _selected_cases(corpus, group=args.group, case_id=args.id,
                               f2_only=args.f2_only)
    if args.list:
        for case, _conversation_id in selected:
            print(f"{case['id']}\t{case['expected']}\t{case['query']}")
        return 0
    if not selected:
        print("Nessun caso selezionato", file=sys.stderr)
        return 2

    if args.catalog_dir:
        import tutor.catalog as tutor_catalog
        args.catalog_dir.mkdir(parents=True, exist_ok=True)
        tutor_catalog.CATALOG_PATH = args.catalog_dir / "tutor_catalog.sqlite"
        tutor_catalog.SIGNATURE_PATH = (
            args.catalog_dir / "tutor_catalog.sqlite.sig")
        tutor_catalog.BACKUP_PATH = (
            args.catalog_dir / "tutor_catalog.last_good.json")
        tutor_catalog.LOCK_PATH = args.catalog_dir / "tutor_catalog.lock"
    from tutor.conversation import _clear_for_tests
    if args.f2_only:
        import tutor.catalog as tutor_catalog
        tutor_catalog.load_cards = lambda: ()
    _clear_for_tests()
    started = time.monotonic()
    records = []
    totals = Counter()
    for position, (case, conversation_id) in enumerate(selected, start=1):
        skip_note = case.get("skip")
        if skip_note:
            totals["skipped"] += 1
            print(
                f"[{position:03d}/{len(selected):03d}] SKIP {case['id']} "
                f"— {skip_note}",
                flush=True,
            )
            records.append({
                "id": case["id"],
                "group": case["group"],
                "query": case["query"],
                "expected": case["expected"],
                "outcome": "skip",
                "passed": True,
                "skipped": skip_note,
                "failures": [],
                "answer": "",
                "source_ids": [],
                "card_ids": [],
                "detection": "",
                "elapsed_ms": 0,
            })
            continue
        case_started = time.monotonic()
        answer = None
        try:
            answer = _run_case(case, conversation_id=conversation_id)
            failures = _evaluate(case, answer)
            answer_text = answer.answer_md if answer is not None else ""
            outcome = answer.esito if answer is not None else "fallthrough"
            if args.f2_only and answer is not None and answer.card_ids:
                failures.append(
                    "F2-only ha usato schede: " + ", ".join(answer.card_ids))
        except Exception as exc:  # keep the whole certification inspectable
            failures = [f"eccezione {type(exc).__name__}: {exc}"]
            answer_text = ""
            outcome = "exception"
        passed = not failures
        totals["passed" if passed else "failed"] += 1
        elapsed_ms = int((time.monotonic() - case_started) * 1000)
        print(
            f"[{position:03d}/{len(selected):03d}] "
            f"{'PASS' if passed else 'FAIL'} {case['id']} "
            f"({outcome}, {elapsed_ms} ms)",
            flush=True,
        )
        for failure in failures:
            print(f"  - {failure}", flush=True)
        records.append({
            "id": case["id"],
            "group": case["group"],
            "query": case["query"],
            "expected": case["expected"],
            "outcome": outcome,
            "passed": passed,
            "failures": failures,
            "answer": answer_text,
            "source_ids": list(answer.source_ids) if answer is not None else [],
            "card_ids": list(answer.card_ids) if answer is not None else [],
            "detection": answer.detection if answer is not None else "",
            "repair_pass": (getattr(answer, "repair_pass", 0)
                            if answer is not None else 0),
            "repair_missing": list(getattr(answer, "repair_missing", ())
                                   if answer is not None else ()),
            "elapsed_ms": elapsed_ms,
        })
        if failures and args.stop_on_fail:
            break

    report = {
        "schema_version": 1,
        "corpus": str(CORPUS.relative_to(ROOT)),
        "selected": len(selected),
        "executed": len(records),
        "passed": totals["passed"],
        "failed": totals["failed"],
        "skipped": totals["skipped"],
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "f2_only": args.f2_only,
        "catalog_dir": str(args.catalog_dir) if args.catalog_dir else "",
        "records": records,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({key: report[key] for key in (
        "selected", "executed", "passed", "failed", "skipped",
        "elapsed_ms")}),
          flush=True)
    return 0 if totals["failed"] == 0 and len(records) == len(selected) else 1


if __name__ == "__main__":
    raise SystemExit(main())
