#!/usr/bin/env python3
"""metnos-i18n — admin CLI per il DB i18n.

Usage:
    python3 -m admin.i18n_cli [--db PATH] stats
    python3 -m admin.i18n_cli get <key>
    python3 -m admin.i18n_cli set <key> <lang> <text>
    python3 -m admin.i18n_cli list [--lang <lang>] [--prefix <prefix>]
    python3 -m admin.i18n_cli pending [--limit N]
    python3 -m admin.i18n_cli add-lang <code>     bootstrap nuova lingua
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import i18n


def cmd_stats(_args):
    s = i18n.stats()
    print(f"DB: {i18n.DB_PATH}")
    print(f"Total entries: {s['total']}, pending translation: {s['pending']}")
    print("By lang:")
    for lang, info in s["by_lang"].items():
        print(f"  {lang}: {info['count']} ({info['pending']} pending)")


def cmd_get(args):
    text = i18n.get(args.key)
    print(text)


def cmd_set(args):
    i18n.set(args.key, args.lang, args.text)
    print(f"set [{args.key}, {args.lang}] = {args.text[:80]!r}")


def cmd_list(args):
    conn = i18n._open()
    sql = "SELECT key, lang, text, needs_translation FROM i18n WHERE 1=1"
    params = []
    if args.lang:
        sql += " AND lang=?"; params.append(args.lang)
    if args.prefix:
        sql += " AND key LIKE ?"; params.append(args.prefix + "%")
    sql += " ORDER BY key, lang"
    n = 0
    for row in conn.execute(sql, params):
        marker = "⏳" if row[3] else "  "
        text = (row[2] or "<NULL>")[:60]
        print(f"  {marker} [{row[1]}] {row[0]}: {text!r}")
        n += 1
    print(f"({n} rows)")


def cmd_pending(args):
    rows = i18n.list_pending(limit=args.limit)
    total = i18n.count_pending()
    actionable = i18n.count_pending(actionable_only=True)
    print(f"{len(rows)} shown; total={total}, actionable={actionable}, "
          f"blocked_or_stale={total - actionable}:")
    for r in rows:
        src = (r["source_text"] or "")[:60]
        print(f"  [{r['source_lang']} → {r['target_lang']}] {r['key']}: {src!r}")


def cmd_repair_pending(_args):
    report = i18n.repair_complete_pending()
    print(
        "repaired complete pending: "
        f"keys={report['keys']} rows={report['rows']} "
        f"skipped_keys={report['skipped_keys']}"
    )


def cmd_delete_keys(args):
    report = i18n.delete_keys(args.keys)
    print(f"deleted exact keys={report['keys']} rows={report['rows']}")


def cmd_queue(args):
    i18n.mark_for_translation(args.key, args.target_lang, args.source_lang)
    print(
        f"queued {args.key}: {args.source_lang} -> {args.target_lang}"
    )


def cmd_add_lang(args):
    """Bootstrap unitario: catalogo output + lessici input RM-0005."""
    new_lang = i18n.normalize_language(args.code)
    src_lang = i18n.normalize_language(args.source_lang or i18n.DEFAULT_LANG)
    if not new_lang or not src_lang or new_lang == src_lang:
        raise SystemExit("language codes must be valid and different")
    conn = i18n._open()
    rows = conn.execute("SELECT DISTINCT key FROM i18n WHERE lang=?", (src_lang,)).fetchall()
    n = 0
    for (key,) in rows:
        # skippa se gia' esistente (anche tradotto)
        existing = conn.execute(
            "SELECT 1 FROM i18n WHERE key=? AND lang=?", (key, new_lang)
        ).fetchone()
        if existing:
            continue
        i18n.mark_for_translation(key, new_lang, src_lang)
        n += 1
    import detection_lexicon
    detection_lexicon.ensure_seeded()
    detection_rows = detection_lexicon.enqueue_language(new_lang)
    print(
        f"Added language '{new_lang}' (source={src_lang}): "
        f"output={n} input={detection_rows} placeholder rows created."
    )
    print("I daemon di traduzione materializzeranno entrambe le superfici.")


def cmd_translate_pending(args):
    """Esegue il motore unico usato da timer systemd e scheduler fallback."""
    from jobs.i18n_translate_pending import task_i18n_translate_pending

    result = task_i18n_translate_pending(payload={"origin": "admin_cli"})
    meta = result.get("metadata") or {}
    remaining = meta.get("pending_actionable")
    if remaining is None:
        remaining = i18n.count_pending(actionable_only=True)
    print(
        f"Translated {int(result.get('ok_count') or 0)} entries. "
        f"Errors: {int(result.get('error_count') or 0)}. "
        f"Remaining actionable: {remaining}. "
        f"Reason: {meta.get('reason') or 'cycle_complete'}."
    )
    if not result.get("ok", False):
        raise SystemExit(1)


def cmd_translate_loop(_args):
    """Lancia daemon loop in foreground (Ctrl-C per stoppare)."""
    import i18n_translator
    i18n_translator.run_loop()


def cmd_validate(args):
    """Validation tool: scan completezza traduzioni.

    Per ogni chiave nel DB, verifica tutte le lingue enumerate dal catalogo.
    Include il gate action-vocabulary (superfici + confini). Exit 1 se issues.
    """
    conn = i18n._open()
    from vocab import action_vocabulary_coverage
    import detection_lexicon
    detection_lexicon.ensure_seeded()
    detected_langs = detection_lexicon.stats()["by_lang"]
    languages = tuple(sorted(set(i18n.available_languages()) | set(detected_langs)))
    # Set di tutte le chiavi
    keys = {r[0] for r in conn.execute("SELECT DISTINCT key FROM i18n")}
    issues = 0
    for key in sorted(keys):
        for lang in languages:
            row = conn.execute(
                "SELECT text, needs_translation FROM i18n WHERE key=? AND lang=?",
                (key, lang),
            ).fetchone()
            if row is None:
                if args.verbose:
                    print(f"  MISSING [{lang}] {key}")
                issues += 1
            elif row[1] == 1 or row[0] is None:
                if args.verbose:
                    print(f"  PENDING [{lang}] {key}")
                issues += 1
    for lang in languages:
        coverage = action_vocabulary_coverage(lang)
        if not coverage.get("ok"):
            issues += 1
            if args.verbose:
                print(f"  ACTION-VOCAB GAP [{lang}] {coverage}")
        elif args.verbose and coverage.get("ambiguous_surfaces"):
            print(
                f"  ACTION-VOCAB POLYSEMY [{lang}] "
                f"{coverage['ambiguous_surfaces']}"
            )
    print(
        f"\nTot keys: {len(keys)}; languages: {languages}; issues: {issues}"
    )
    if issues:
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(prog="metnos-i18n")
    p.add_argument(
        "--db", type=Path,
        help="DB i18n esplicito (default: percorso runtime configurato)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    g = sub.add_parser("get"); g.add_argument("key"); g.set_defaults(fn=cmd_get)
    s = sub.add_parser("set")
    s.add_argument("key"); s.add_argument("lang"); s.add_argument("text")
    s.set_defaults(fn=cmd_set)
    l = sub.add_parser("list")
    l.add_argument("--lang"); l.add_argument("--prefix")
    l.set_defaults(fn=cmd_list)
    pe = sub.add_parser("pending")
    pe.add_argument("--limit", type=int, default=50)
    pe.set_defaults(fn=cmd_pending)
    rp = sub.add_parser("repair-pending")
    rp.add_argument(
        "--all-complete", action="store_true", required=True,
        help="conferma esplicita: normalizza come baseline tutte le coppie complete",
    )
    rp.set_defaults(fn=cmd_repair_pending)
    dk = sub.add_parser("delete-keys")
    dk.add_argument("keys", nargs="+")
    dk.set_defaults(fn=cmd_delete_keys)
    qu = sub.add_parser("queue")
    qu.add_argument("key")
    qu.add_argument("target_lang")
    qu.add_argument("source_lang")
    qu.set_defaults(fn=cmd_queue)
    al = sub.add_parser("add-lang")
    al.add_argument("code"); al.add_argument("--source-lang")
    al.set_defaults(fn=cmd_add_lang)
    sub.add_parser("translate-pending").set_defaults(fn=cmd_translate_pending)
    sub.add_parser("translate-loop").set_defaults(fn=cmd_translate_loop)
    v = sub.add_parser("validate")
    v.add_argument("--verbose", action="store_true")
    v.set_defaults(fn=cmd_validate)
    args = p.parse_args()
    if args.db is not None:
        if i18n._conn is not None:
            i18n._conn.close()
        i18n._conn = None
        i18n.DB_PATH = args.db.expanduser().resolve()
    args.fn(args)


if __name__ == "__main__":
    main()
