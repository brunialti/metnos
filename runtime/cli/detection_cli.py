#!/usr/bin/env python3
"""CLI admin per il sottosistema detection_lexicon (gemello di i18n_cli).

Uso (da repo root):
    python3 runtime/cli/detection_cli.py stats
    python3 runtime/cli/detection_cli.py coverage [lang]
    python3 runtime/cli/detection_cli.py enqueue <lang>     # marca pending
    python3 runtime/cli/detection_cli.py translate          # esegue il daemon
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import detection_lexicon as _dl  # noqa: E402


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "stats"
    _dl.ensure_seeded()
    if cmd == "stats":
        print(json.dumps(_dl.stats(), ensure_ascii=False, indent=2))
    elif cmd == "coverage":
        lang = argv[1] if len(argv) > 1 else _dl.current_lang()
        print(json.dumps(_dl.verify_coverage(lang), ensure_ascii=False, indent=2))
    elif cmd == "enqueue":
        if len(argv) < 2:
            print("uso: enqueue <lang>", file=sys.stderr)
            return 2
        n = _dl.enqueue_language(argv[1].lower())
        print(f"accodati {n} concept per '{argv[1].lower()}'")
    elif cmd == "translate":
        from jobs.detection_translate_pending import task_detection_translate_pending
        res = task_detection_translate_pending()
        print(json.dumps(res["metadata"], ensure_ascii=False, indent=2))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
