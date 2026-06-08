#!/usr/bin/env python3
"""metnos-i18n — admin CLI per il DB i18n.

Usage:
    python3 -m admin.i18n_cli stats
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
    print(f"{len(rows)} pending translations:")
    for r in rows:
        src = (r["source_text"] or "")[:60]
        print(f"  [{r['source_lang']} → {r['target_lang']}] {r['key']}: {src!r}")


def cmd_add_lang(args):
    """Bootstrap nuova lingua: per ogni chiave esistente nel default lang,
    crea placeholder row con needs_translation=1."""
    new_lang = args.code
    src_lang = args.source_lang or i18n.DEFAULT_LANG
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
    print(f"Added language '{new_lang}' (source={src_lang}): {n} placeholder rows created.")
    print(f"Daemon translator (TODO) o tool admin riempira' on-demand.")


def cmd_translate_pending(args):
    """Esegue 1 ciclo del daemon translator (sync). Per debug/manual run."""
    import i18n_translator
    n_ok, remaining = i18n_translator.run_one_cycle()
    print(f"Translated {n_ok} entries. Remaining pending: {remaining}.")


def cmd_translate_loop(_args):
    """Lancia daemon loop in foreground (Ctrl-C per stoppare)."""
    import i18n_translator
    i18n_translator.run_loop()


def cmd_validate(args):
    """Validation tool: scan completezza traduzioni.

    Per ogni chiave nel DB, verifica che esista in tutte le lingue dichiarate
    da vocab.LANGS. Stampa missing + needs_translation. Exit 1 se issues.
    """
    conn = i18n._open()
    try:
        from vocab import LANGS
    except Exception:
        LANGS = ("it", "en")
    # Set di tutte le chiavi
    keys = {r[0] for r in conn.execute("SELECT DISTINCT key FROM i18n")}
    issues = 0
    for key in sorted(keys):
        for lang in LANGS:
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
    print(f"\nTot keys: {len(keys)}; LANGS: {LANGS}; issues: {issues}")
    if issues:
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(prog="metnos-i18n")
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
    al = sub.add_parser("add-lang")
    al.add_argument("code"); al.add_argument("--source-lang")
    al.set_defaults(fn=cmd_add_lang)
    sub.add_parser("translate-pending").set_defaults(fn=cmd_translate_pending)
    sub.add_parser("translate-loop").set_defaults(fn=cmd_translate_loop)
    v = sub.add_parser("validate")
    v.add_argument("--verbose", action="store_true")
    v.set_defaults(fn=cmd_validate)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
