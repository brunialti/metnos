#!/usr/bin/env python3
"""Administrative CLI for the RM-0005 localization lifecycle."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as _C  # noqa: E402
import i18n  # noqa: E402
from i18n_activation import activate_language, gate, restart_http_service  # noqa: E402
from i18n_materializer import materialize, materialize_requested  # noqa: E402
from i18n_pipeline import (  # noqa: E402
    default_equivalence_judge,
    live_contract_context,
    review_semantics,
    translate_pending,
)
from i18n_registry import LocalizationRegistry, normalize_language  # noqa: E402


def cmd_stats(_args) -> None:
    stats = i18n.stats()
    _print({"db": str(i18n.DB_PATH), **stats})


def cmd_get(args) -> None:
    print(i18n.get(args.key))


def cmd_set(args) -> None:
    i18n.set(args.key, args.lang, args.text)
    _print({"status": "set", "key": args.key, "lang": args.lang})


def cmd_list(args) -> None:
    sql = "SELECT key,lang,text,needs_translation FROM i18n WHERE 1=1"
    params: list[str] = []
    if args.lang:
        sql += " AND lang=?"
        params.append(args.lang)
    if args.prefix:
        sql += " AND key LIKE ?"
        params.append(args.prefix + "%")
    sql += " ORDER BY key,lang"
    _print({"rows": [dict(zip(
        ("key", "lang", "text", "needs_translation"), row, strict=True,
    )) for row in i18n._open().execute(sql, params)]})


def cmd_pending(args) -> None:
    _print({
        "rows": i18n.list_pending(limit=args.limit),
        "total": i18n.count_pending(),
        "actionable": i18n.count_pending(actionable_only=True),
    })


def cmd_repair_pending(_args) -> None:
    _print(i18n.repair_complete_pending())


def cmd_delete_keys(args) -> None:
    _print(i18n.delete_keys(args.keys))


def cmd_queue(args) -> None:
    i18n.mark_for_translation(args.key, args.target_lang, args.source_lang)
    _print({
        "status": "queued", "key": args.key,
        "source_lang": args.source_lang, "target_lang": args.target_lang,
    })


def cmd_add_lang(args) -> None:
    """Compatibility entry point backed by the shared output/input queues."""

    new_lang = i18n.normalize_language(args.code)
    source = i18n.normalize_language(
        args.source_lang or _C.BOOTSTRAP_LANGUAGE
    )
    if not new_lang or not source or new_lang == source:
        raise SystemExit("language codes must be valid and different")
    connection = i18n._open()
    output = 0
    for (key,) in connection.execute(
        "SELECT DISTINCT key FROM i18n WHERE lang=? ORDER BY key", (source,),
    ):
        if connection.execute(
            "SELECT 1 FROM i18n WHERE key=? AND lang=?", (key, new_lang),
        ).fetchone():
            continue
        i18n.mark_for_translation(key, new_lang, source)
        output += 1
    import detection_lexicon
    detection_lexicon.ensure_seeded()
    input_count = detection_lexicon.enqueue_language(new_lang)
    print(
        f"Added language '{new_lang}' (source={source}): "
        f"output={output} input={input_count} placeholder rows created."
    )


def cmd_translate_pending(_args) -> None:
    from jobs.i18n_translate_pending import task_i18n_translate_pending
    _print(task_i18n_translate_pending(payload={"origin": "admin_cli"}))


def cmd_translate_loop(_args) -> None:
    import i18n_translator
    i18n_translator.run_loop()


def cmd_validate(args) -> None:
    from vocab import action_vocabulary_coverage
    import detection_lexicon

    detection_lexicon.ensure_seeded()
    languages = tuple(sorted(
        set(i18n.available_languages())
        | set(detection_lexicon.stats()["by_lang"])
    ))
    connection = i18n._open()
    keys = {row[0] for row in connection.execute("SELECT DISTINCT key FROM i18n")}
    issues: list[dict] = []
    for key in sorted(keys):
        for lang in languages:
            row = connection.execute(
                "SELECT text,needs_translation FROM i18n WHERE key=? AND lang=?",
                (key, lang),
            ).fetchone()
            if row is None or row[0] is None or int(row[1] or 0):
                issues.append({"key": key, "lang": lang})
    for lang in languages:
        report = action_vocabulary_coverage(lang)
        if not report.get("ok"):
            issues.append({"action_vocabulary": lang, "report": report})
    _print({"keys": len(keys), "languages": languages, "issues": issues})
    if issues:
        raise SystemExit(1)


def _print(value) -> None:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _requested_target() -> str | None:
    request, error = _C.read_localization_request()
    if request is None:
        if error == "missing":
            return None
        raise RuntimeError(f"localization request is invalid ({error})")
    if request.requested_lang:
        return request.requested_lang
    if (
        request.state == "active"
        and request.instance_lang != _C.BOOTSTRAP_LANGUAGE
    ):
        return request.instance_lang
    return None


def _advance_requested(limit: int) -> dict:
    target = _requested_target()
    if target is None:
        return {"status": "no_target"}
    return _advance(target, limit)


def _request(target: str) -> dict:
    normalized = normalize_language(target)
    registry = LocalizationRegistry()
    context = live_contract_context(registry)
    request, changed = _C.write_localization_request(
        instance_lang=_C.BOOTSTRAP_LANGUAGE,
        requested_lang=normalized,
        state="bootstrap_english",
        corpus_version=_C.localization_corpus_version(),
    )
    report = materialize(
        normalized,
        registry=registry,
        contract_snapshot_provider=context.snapshot_provider,
    )
    return {"request": asdict(request), "changed": changed, "materialization": asdict(report)}


def _advance(target: str, limit: int) -> dict:
    registry = LocalizationRegistry()
    context = live_contract_context(registry)
    materialized = materialize(
        target,
        registry=registry,
        contract_snapshot_provider=context.snapshot_provider,
    )
    translated = translate_pending(
        target,
        registry=registry,
        limit=limit,
        contract_snapshot_provider=context.snapshot_provider,
    )
    reviewed = review_semantics(
        target, registry=registry, judge=default_equivalence_judge,
        limit=limit,
        contract_snapshot_provider=context.snapshot_provider,
    )
    readiness = gate(
        target,
        registry=registry,
        contract_snapshot_provider=context.snapshot_provider,
    )
    return {
        "materialization": asdict(materialized),
        "translation": asdict(translated),
        "reviewed": sum(1 for accepted in reviewed.values() if accepted),
        "gate": asdict(readiness),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path,
        help="explicit runtime catalog database",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    get_cmd = sub.add_parser("get")
    get_cmd.add_argument("key")
    get_cmd.set_defaults(fn=cmd_get)
    set_cmd = sub.add_parser("set")
    set_cmd.add_argument("key")
    set_cmd.add_argument("lang")
    set_cmd.add_argument("text")
    set_cmd.set_defaults(fn=cmd_set)
    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--lang")
    list_cmd.add_argument("--prefix")
    list_cmd.set_defaults(fn=cmd_list)
    pending = sub.add_parser("pending")
    pending.add_argument("--limit", type=int, default=50)
    pending.set_defaults(fn=cmd_pending)
    repair = sub.add_parser("repair-pending")
    repair.add_argument("--all-complete", action="store_true", required=True)
    repair.set_defaults(fn=cmd_repair_pending)
    delete = sub.add_parser("delete-keys")
    delete.add_argument("keys", nargs="+")
    delete.set_defaults(fn=cmd_delete_keys)
    queue = sub.add_parser("queue")
    queue.add_argument("key")
    queue.add_argument("target_lang")
    queue.add_argument("source_lang")
    queue.set_defaults(fn=cmd_queue)
    add = sub.add_parser("add-lang")
    add.add_argument("code")
    add.add_argument("--source-lang")
    add.set_defaults(fn=cmd_add_lang)
    sub.add_parser("translate-pending").set_defaults(fn=cmd_translate_pending)
    sub.add_parser("translate-loop").set_defaults(fn=cmd_translate_loop)
    validate = sub.add_parser("validate")
    validate.add_argument("--verbose", action="store_true")
    validate.set_defaults(fn=cmd_validate)
    request = sub.add_parser("request")
    request.add_argument("target")
    materialize_cmd = sub.add_parser("materialize")
    materialize_cmd.add_argument("target")
    sub.add_parser("materialize-requested")
    translate = sub.add_parser("translate")
    translate.add_argument("target")
    translate.add_argument("--limit", type=int, default=0)
    advance = sub.add_parser("advance")
    advance.add_argument("target")
    advance.add_argument("--limit", type=int, default=0)
    advance_requested = sub.add_parser("advance-requested")
    advance_requested.add_argument("--limit", type=int, default=0)
    status = sub.add_parser("status")
    status.add_argument("target", nargs="?")
    activate = sub.add_parser("activate")
    activate.add_argument("target")
    activate.add_argument("--restart", action="store_true")
    args = parser.parse_args(argv)
    if args.db is not None:
        if i18n._conn is not None:
            i18n._conn.close()
        i18n._conn = None
        i18n.DB_PATH = args.db.expanduser().resolve()
    if hasattr(args, "fn"):
        args.fn(args)
        return 0
    registry = LocalizationRegistry()
    context = None

    def selected_context(*, publication: bool = False):
        nonlocal context
        if context is None or (publication and context.publisher is None):
            context = live_contract_context(registry, publication=publication)
        return context
    try:
        if args.command == "request":
            _print(_request(args.target))
        elif args.command == "materialize":
            live = selected_context()
            _print(materialize(
                args.target,
                registry=registry,
                contract_snapshot_provider=live.snapshot_provider,
            ))
        elif args.command == "materialize-requested":
            live = selected_context()
            report = materialize_requested(
                registry=registry,
                contract_snapshot_provider=live.snapshot_provider,
            )
            _print(asdict(report) if report else {"status": "no_pending_request"})
        elif args.command == "translate":
            live = selected_context()
            _print(translate_pending(
                args.target,
                registry=registry,
                limit=args.limit,
                contract_snapshot_provider=live.snapshot_provider,
            ))
        elif args.command == "advance":
            _print(_advance(args.target, args.limit))
        elif args.command == "advance-requested":
            _print(_advance_requested(args.limit))
        elif args.command == "status":
            target = args.target or _requested_target()
            if target is None:
                _print({"status": "no_target"})
                return 0
            live = selected_context()
            _print({
                "coverage": asdict(registry.coverage(target)),
                "gate": asdict(gate(
                    target,
                    registry=registry,
                    contract_snapshot_provider=live.snapshot_provider,
                )),
                "checks": registry.checks(target),
            })
        elif args.command == "activate":
            live = selected_context(publication=True)
            _print(activate_language(
                args.target, registry=registry,
                restart=restart_http_service if args.restart else None,
                contract_snapshot_provider=live.snapshot_provider,
                contract_publisher=live.publisher,
            ))
        return 0
    except Exception as exc:
        _print({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
