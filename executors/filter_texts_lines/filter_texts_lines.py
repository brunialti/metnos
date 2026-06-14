#!/usr/bin/env python3
"""filter_texts_lines — executor di Metnos v1.1.

Filtra/estrae righe da contenuti testuali secondo criteri (regex,
substring, range numerico). Input puro in-memory: passa il `content`
di un read_files o una lista di stringhe.

Decomposizione: scompone un blob testuale in righe selezionate.
Pure compute: nessuna I/O esterna.

Contratto:
    stdin: JSON {content?: str | list[str], regex?: str,
                 substring?: str, case_insensitive?: bool = true,
                 max_results?: int = 1000, with_line_numbers?: bool = false}
    stdout: JSON {ok, ok_count, lines, regex_used, total_input_lines}
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402


def invoke(args):
    content = args.get("content")
    regex = args.get("regex")
    substring = args.get("substring")
    case_insensitive = bool(args.get("case_insensitive", True))
    max_results = int(args.get("max_results", 1000))
    with_line_numbers = bool(args.get("with_line_numbers", False))

    if content is None:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="content")}
    if isinstance(content, list):
        try:
            content_str = "\n".join(str(c) for c in content)
        except Exception as e:
            return {"ok": False, "error": f"content list contains non-stringifiable item: {e}"}
    elif isinstance(content, str):
        content_str = content
    else:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="content", reason=type(content).__name__)}
    if regex is None and substring is None:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING_ONE_OF", options="regex, substring")}
    if max_results <= 0 or max_results > 100000:
        return {"ok": False, "error": _msg("ERR_ARG_RANGE", arg="max_results", min=1, max=100000)}

    flags = re.IGNORECASE if case_insensitive else 0
    matcher = None
    if regex is not None:
        try:
            matcher = re.compile(regex, flags)
        except re.error as e:
            return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="regex", reason=str(e))}
    sub = (substring.lower() if (substring and case_insensitive) else substring)

    lines_in = content_str.split("\n")
    lines_out = []
    for i, line in enumerate(lines_in, start=1):
        keep = True
        if matcher is not None:
            keep = bool(matcher.search(line))
        if keep and sub is not None:
            haystack = line.lower() if case_insensitive else line
            keep = sub in haystack
        if not keep:
            continue
        item = {"line": line}
        if with_line_numbers:
            item["line_number"] = i
        lines_out.append(item)
        if len(lines_out) >= max_results:
            break

    # Conta totale matchanti per available_total reale (the design guide 2.7+2.11):
    # se siamo usciti per cap, scansiona il resto solo per contare (sopra
    # i tetti di max_results, niente raccolta).
    available_total = len(lines_out)
    truncated = False
    if len(lines_out) >= max_results and i < len(lines_in):
        # `i` e' 1-based (enumerate start=1) = indice 0-based della PROSSIMA
        # riga non ancora processata. Prima `i + 1` saltava quella riga →
        # available_total sotto-contava di 1 (the design guide §2.7/§2.11).
        for j in range(i, len(lines_in)):
            line = lines_in[j]
            keep = True
            if matcher is not None:
                keep = bool(matcher.search(line))
            if keep and sub is not None:
                haystack = line.lower() if case_insensitive else line
                keep = sub in haystack
            if keep:
                available_total += 1
        truncated = available_total > len(lines_out)

    out = {
        "ok": True,
        "ok_count": len(lines_out),
        "lines": lines_out,
        "total_input_lines": len(lines_in),
        "regex_used": regex,
        "substring_used": substring,
    }
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "riga"
        out["used"] = len(lines_out)
        out["available_total"] = available_total
        out["cap_field"] = "max_results"
        out["cap_value"] = max_results
    return out


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
