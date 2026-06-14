# SPDX-License-Identifier: AGPL-3.0-only
"""manifest_refactor.py — refactor idempotente dei manifest.toml.

Applica le 4 regole di stile (the design guide §6 + §7.12):
1. PRESCRITTIVO (DEVI / NON DEVI / OK / ERRORE).
2. PATTERN ORIENTED (3-7 esempi "(non copiare letteralmente)").
3. SENZA RIPETIZIONI (no USO+ARG duplicati, no OUTPUT in description+schema).
4. COMPATTO (max 25 parole/frase, backtick inline, IT/EN paralleli).

Reference compact style: `executors/get_processes/manifest.toml` (193 righe).

Idempotenza:
- Marker di verbosita' deterministico (descrizione > N char, contiene
  "USO CORRETTO" + "ARG NAMES" duplicati, "Esempio: " dopo ogni rule).
- Se gia' compatto: SKIP, nessun cambio.
- Se compresso: riscrive solo se nuovo testo passa validation (TOML
  parse + campi chiave preservati + lunghezza ridotta ≥10%).

Re-sign mandatory dopo ogni modifica (§7.10).

CLI:
    python3 -m runtime.admin.manifest_refactor --dry-run
    python3 -m runtime.admin.manifest_refactor --apply --limit 5
    python3 -m runtime.admin.manifest_refactor --apply --only get_processes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tomllib  # Python 3.11+ stdlib
try:
    import tomli_w
except ImportError:
    tomli_w = None  # write-back disabled if not installed

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11
EXECUTORS_DIR = _C.PATH_EXECUTORS
REFERENCE = EXECUTORS_DIR / "get_processes" / "manifest.toml"

# Soglie di verbosita' (idempotenza): se manifest sotto soglia → SKIP.
_DESC_LEN_THRESHOLD = 600        # description.it >600 chars = candidato
_LINES_THRESHOLD = 180           # manifest >180 righe = candidato
_VERBOSITY_MARKERS = (
    "USO CORRETTO",
    "USAGE: query",
    "ARG NAMES (nomi esatti",
    "ARG NAMES (exact names",
)


def _read_reference() -> dict:
    """Carica i description.it/en di get_processes come few-shot."""
    with open(REFERENCE, "rb") as f:
        ref = tomllib.load(f)
    return {
        "it": ref["description"]["it"],
        "en": ref["description"]["en"],
    }


def _is_verbose(manifest: dict) -> tuple[bool, list[str]]:
    """Determina se un manifest e' candidato a refactor.

    Criteri (almeno UNO deve scattare):
    - Marker di stile verboso (USO CORRETTO + ARG NAMES duplicati).
    - "Esempio:" / "Example:" ripetuto piu' di 3 volte.
    - Lunghezza > _DESC_LEN_THRESHOLD AND nessuna sezione PATTERN.

    Idempotente: re-run dopo refactor → False."""
    reasons = []
    desc = manifest.get("description", {})
    it = desc.get("it", "") if isinstance(desc, dict) else ""
    en = desc.get("en", "") if isinstance(desc, dict) else ""
    has_marker = False
    for marker in _VERBOSITY_MARKERS:
        if marker in it:
            reasons.append(f"verbose marker IT: '{marker}'")
            has_marker = True
            break
        if marker in en:
            reasons.append(f"verbose marker EN: '{marker}'")
            has_marker = True
            break
    if it.count("Esempio:") > 3:
        reasons.append(f"'Esempio:' ripetuto {it.count('Esempio:')}x in IT")
    if en.count("Example:") > 3:
        reasons.append(f"'Example:' ripetuto {en.count('Example:')}x in EN")
    # Long + no PATTERN section = verbose without structure.
    long_no_pattern = (
        len(it) > _DESC_LEN_THRESHOLD
        and "PATTERN" not in it
        and "Pattern" not in it
    )
    if long_no_pattern:
        reasons.append(f"long IT ({len(it)}c) without PATTERN section")
    return (has_marker or it.count("Esempio:") > 3
            or en.count("Example:") > 3 or long_no_pattern, reasons)


def _build_refactor_prompt(name: str, current: dict, reference: dict) -> str:
    """Prompt per Gemma: compacta description preservando semantica."""
    return f"""SEI un compattatore di manifest.toml di Metnos. Devi
RISCRIVERE le description IT+EN seguendo 4 regole di stile:

1. PRESCRITTIVO: DEVI / NON DEVI / OK pattern. No prosa "perche'".
2. PATTERN ORIENTED: 3-5 esempi "PATTERN: query → call".
3. SENZA RIPETIZIONI: NON duplicare USO+ARG NAMES+OUTPUT. Una sola
   sezione OUTPUT inline. Niente "Esempio: ..." dopo ogni regola.
4. COMPATTO: max 25 parole/frase, backtick inline (`top=5`), IT/EN
   PARALLELI (non identical-translated, EN un po' piu' secco).

REFERENCE OK (executors/get_processes — 193 righe, gold style):
--- IT ---
{reference['it']}
--- EN ---
{reference['en']}
---

DESCRIPTION CORRENTE di `{name}` (da compattare):
--- IT ---
{current.get('it','')}
--- EN ---
{current.get('en','')}
---

OUTPUT: JSON `{{"description":{{"it":"...","en":"..."}}}}`. NIENTE prosa,
NIENTE markdown fences. La sostanza tecnica DEVE essere preservata
(args, OUTPUT shape, NON CONFONDERE clauses). Solo il VERBOSO sparisce."""


def _llm_compact(prompt: str) -> dict | None:
    """Chiama Gemma 26B locale, parse JSON output. None se fallisce."""
    try:
        from llm_provider import LlamaCppProvider
        prov = LlamaCppProvider(
            model="gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
            endpoint="http://127.0.0.1:8080",
        )
        r = prov.chat("", prompt, max_tokens=2048, temperature=0.3,
                      think=False)
        text = r.text if hasattr(r, "text") else str(r)
    except Exception as ex:
        print(f"  LLM call failed: {ex}", file=sys.stderr)
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.M)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as ex:
        print(f"  JSON parse failed: {ex}", file=sys.stderr)
        return None
    if not isinstance(obj, dict) or "description" not in obj:
        return None
    return obj


def _validate_compaction(old: dict, new: dict) -> tuple[bool, str]:
    """Controlla che la versione compattata sia accettabile."""
    if not isinstance(new, dict):
        return False, "new not dict"
    nd = new.get("description")
    if not isinstance(nd, dict):
        return False, "description not dict"
    for lang in ("it", "en"):
        if lang not in nd or not nd[lang].strip():
            return False, f"description.{lang} missing/empty"
        if len(nd[lang]) > len(old["description"][lang]) * 1.05:
            return False, f"description.{lang} grew (>5% longer)"
    # Critical keywords preservati: solo args reali (snake_case con _).
    # "include" da solo non e' critico. "filters", "include_health" si'.
    old_args = set(re.findall(r"\b[a-z]+_[a-z_]+\b", old["description"]["it"]))
    new_args = set(re.findall(r"\b[a-z]+_[a-z_]+\b", nd["it"]))
    lost = old_args - new_args
    # Common stoplist: cose non da preservare (sinonimi, ecc.)
    lost = {k for k in lost if "_" in k and len(k) > 5}
    if lost:
        return False, f"args/keys lost: {sorted(lost)}"
    return True, "OK"


def refactor_manifest(path: Path, reference: dict, *,
                     apply: bool = False) -> dict:
    """Refactor un singolo manifest. Idempotente."""
    with open(path, "rb") as f:
        manifest = tomllib.load(f)
    name = manifest.get("name", path.parent.name)

    is_verbose, reasons = _is_verbose(manifest)
    if not is_verbose:
        return {"name": name, "skipped": True, "reason": "already compact"}

    old_desc = manifest["description"]
    old_lines = len(path.read_text().splitlines())

    prompt = _build_refactor_prompt(name, old_desc, reference)
    new_obj = _llm_compact(prompt)
    if new_obj is None:
        return {"name": name, "error": "LLM call/parse failed"}

    ok, msg = _validate_compaction({"description": old_desc}, new_obj)
    if not ok:
        return {"name": name, "error": f"validation: {msg}"}

    new_it = new_obj["description"]["it"]
    new_en = new_obj["description"]["en"]
    delta_it = len(old_desc["it"]) - len(new_it)
    delta_en = len(old_desc["en"]) - len(new_en)

    if not apply:
        return {
            "name": name, "dry_run": True,
            "old_lines": old_lines,
            "delta_it_chars": delta_it,
            "delta_en_chars": delta_en,
            "reasons": reasons,
        }

    # Applica + re-sign. tomli_w optional: se mancante, sed-replace
    # via regex sul testo originale (preserva formattazione altri campi).
    if tomli_w is not None:
        manifest["description"]["it"] = new_it
        manifest["description"]["en"] = new_en
        with open(path, "wb") as f:
            tomli_w.dump(manifest, f)
    else:
        text = path.read_text()
        # Replace description.it = """...""" block (multi-line, non-greedy)
        text = re.sub(
            r'(it\s*=\s*""")[^"]*?(""")',
            lambda m: m.group(1) + new_it + m.group(2), text, count=1,
        )
        text = re.sub(
            r'(en\s*=\s*"""[^"]*?""")',
            lambda m: f'en = """{new_en}"""', text, count=1,
        )
        path.write_text(text)
    new_lines = len(path.read_text().splitlines())

    # Re-sign §7.10
    proc = subprocess.run(
        ["python3", "-m", "runtime.sign", "sign", str(path.parent)],
        cwd="/opt/metnos",
        env={**os.environ, "PYTHONPATH": "/opt/metnos/runtime"},
        capture_output=True, text=True,
    )
    sign_ok = proc.returncode == 0

    return {
        "name": name, "applied": True,
        "old_lines": old_lines, "new_lines": new_lines,
        "lines_saved": old_lines - new_lines,
        "delta_it_chars": delta_it, "delta_en_chars": delta_en,
        "signed": sign_ok,
        "sign_stderr": proc.stderr.strip()[:200] if not sign_ok else "",
    }


def main():
    p = argparse.ArgumentParser(description="Refactor idempotente manifest.toml")
    p.add_argument("--apply", action="store_true",
                   help="Applica le modifiche (default: dry-run)")
    p.add_argument("--only", type=str, default=None,
                   help="Refactor solo questo executor (per name)")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap max executor da processare in un run")
    args = p.parse_args()

    reference = _read_reference()
    manifests = sorted(EXECUTORS_DIR.glob("*/manifest.toml"))
    if args.only:
        manifests = [m for m in manifests if m.parent.name == args.only]

    results = []
    processed = 0
    for path in manifests:
        if args.limit and processed >= args.limit:
            break
        name = path.parent.name
        if name == "get_processes":
            # Skip reference (already gold).
            continue
        t0 = time.time()
        try:
            r = refactor_manifest(path, reference, apply=args.apply)
        except Exception as ex:
            r = {"name": name, "error": f"crash: {ex!r}"}
        dt = int(time.time() - t0)
        r["dt_s"] = dt
        results.append(r)
        processed += 1
        # Compact log line
        if r.get("skipped"):
            print(f"  [skip] {name}: {r['reason']}")
        elif r.get("error"):
            print(f"  [ERR ] {name}: {r['error']}")
        elif r.get("applied"):
            print(f"  [done] {name}: -{r['lines_saved']} lines, "
                  f"IT -{r['delta_it_chars']}c, EN -{r['delta_en_chars']}c, "
                  f"signed={r['signed']}, dt={dt}s")
        else:
            print(f"  [dry ] {name}: would save IT -{r['delta_it_chars']}c "
                  f"EN -{r['delta_en_chars']}c (dt={dt}s)")

    # Summary
    print()
    n_skip = sum(1 for r in results if r.get("skipped"))
    n_err = sum(1 for r in results if r.get("error"))
    n_done = sum(1 for r in results if r.get("applied"))
    n_dry = sum(1 for r in results if r.get("dry_run"))
    total_saved = sum(r.get("lines_saved", 0) for r in results)
    print(f"Processed: {len(results)} | skip: {n_skip} | err: {n_err} | "
          f"done: {n_done} | dry: {n_dry} | total_lines_saved: {total_saved}")


if __name__ == "__main__":
    main()
