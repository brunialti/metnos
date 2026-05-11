"""skills_cli - metnos-skills CLI per importer SKILL.md -> executor Metnos.

Sub-commands:
- metnos-skills import <skill_url_or_path>  Full pipeline: fetch -> parse ->
  translate -> codegen -> admission -> sign Ed25519 -> stage.
- metnos-skills list                         Elenco skill importate.
- metnos-skills uninstall <skill_name>       Rimuove executor + audit.
- metnos-skills status <skill_name>          Invocazioni, success rate, age.
- metnos-skills evaluate <skill_name>        Re-invoca admission policy.

Determinismo §7.9: tutto procedurale. LLM solo nel sub-step codegen
description (Task C.2) e nel verifier L6 (Task D).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _runtime_dir() -> Path:
    return Path(__file__).resolve().parent.parent


# Ensure runtime/ on sys.path
_RUNTIME = _runtime_dir()
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


def _executors_base() -> Path:
    """~/.local/share/metnos/executors/_imports/<skill_name>/.

    Override per test via METNOS_EXECUTORS_DIR (path completo).
    """
    override = os.environ.get("METNOS_EXECUTORS_DIR")
    if override:
        return Path(override)
    base = Path(os.environ.get(
        "XDG_DATA_HOME",
        str(Path.home() / ".local/share"),
    ))
    return base / "metnos" / "executors" / "_imports"


def _skills_dir() -> Path:
    """~/.local/share/metnos/skills/<skill_name>/ (file della skill copiati)."""
    override = os.environ.get("METNOS_SKILLS_DIR")
    if override:
        return Path(override)
    base = Path(os.environ.get(
        "XDG_DATA_HOME",
        str(Path.home() / ".local/share"),
    ))
    return base / "metnos" / "skills"


def _audit_log_path() -> Path:
    base = Path(os.environ.get(
        "METNOS_AUDIT_DIR",
        str(Path.home() / ".local/share/metnos/synth_audit"),
    ))
    base.mkdir(parents=True, exist_ok=True)
    return base / "imports.jsonl"


# ---------------------------------------------------------------------------
# Sign helper (importa da /opt/myclaw/runtime/sign.py senza modificarlo)
# ---------------------------------------------------------------------------


def _sign_keys_present(key_name: str = "author") -> tuple[bool, Optional[Path]]:
    """Gap 3 (10/5/2026): verifica `~/.config/metnos/keys/<name>_priv.bin`.

    Ritorna `(present, expected_path)`. Caller (cmd_import) puo' fallire
    gentilmente con istruzioni se il key non esiste.
    """
    keys_dir = Path.home() / ".config" / "metnos" / "keys"
    priv = keys_dir / f"{key_name}_priv.bin"
    pub = keys_dir / f"{key_name}_pub.bin"
    return (priv.is_file() and pub.is_file()), priv


def _try_sign_executor(manifest_dir: Path) -> Optional[str]:
    """Wrapper di runtime/sign.py::sign_executor.

    Importa modulo da /opt/myclaw/runtime/ se disponibile. Restituisce
    il digest sha256 calcolato (str) o None se signer non disponibile.
    """
    canonical = Path("/opt/myclaw/runtime")
    if not canonical.exists():
        return None
    if str(canonical) not in sys.path:
        sys.path.insert(0, str(canonical))
    try:
        import sign as sign_mod  # type: ignore
    except Exception as e:
        return f"signer_import_failed: {e}"
    # sign_executor richiede keys gia' presenti; in dry-run skippato.
    if os.environ.get("METNOS_SKILLS_NO_SIGN") == "1":
        return "skipped_by_env"
    try:
        sign_mod.sign_executor(str(manifest_dir))
        return "signed"
    except Exception as e:
        return f"sign_failed: {e}"


# ---------------------------------------------------------------------------
# Fetch (locale: path; remoto: stub)
# ---------------------------------------------------------------------------


def _resolve_skill_source(arg: str) -> Path:
    """Risolve un argomento <skill_url_or_path> in un path locale a SKILL.md.

    Da gap 4 (10/5/2026) supporta:
    - path assoluto a SKILL.md (locale)
    - path a dir con SKILL.md dentro (locale)
    - `agentskills.io/<owner>/<skill>` -> canonical mapping a raw.githubusercontent
    - `https://github.com/<owner>/<skill>` -> git clone shallow
    - `https://raw.githubusercontent.com/<owner>/<repo>/<branch>/SKILL.md` -> direct GET

    Cache: `~/.cache/metnos/skill_imports/<sha256(url)>/` TTL 7d.
    """
    # Priorita' locale: se arg e' un path che esiste, usa quello.
    p = Path(arg)
    if p.is_file() and p.name == "SKILL.md":
        return p
    if p.is_dir() and (p / "SKILL.md").exists():
        return p / "SKILL.md"
    # Remote: delega a skill_fetch.fetch_skill_source.
    from skill_fetch import fetch_skill_source, SkillFetchError
    try:
        return fetch_skill_source(arg)
    except SkillFetchError as e:
        # Mantieni interfaccia: NotImplementedError per URL pattern non noti
        # solo se il caller ha esplicitamente passato un URL HTTP/S non riconosciuto.
        if str(e).startswith("URL non supportato"):
            raise NotImplementedError(str(e))
        raise FileNotFoundError(str(e))


# ---------------------------------------------------------------------------
# Sub-command: import
# ---------------------------------------------------------------------------


def _cmd_import(args) -> int:
    from skill_parser import parse_skill_md
    from skill_translator import translate_skill
    from skill_codegen import generate_executor_files
    from skill_admission import admit_skill_import

    try:
        skill_path = _resolve_skill_source(args.skill)
    except (FileNotFoundError, NotImplementedError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # Gap 3: verifica sign keys PRIMA di processare la skill (a meno che
    # l'utente abbia dichiarato --no-sign / METNOS_SKILLS_NO_SIGN=1).
    skip_sign = bool(args.no_sign) or os.environ.get("METNOS_SKILLS_NO_SIGN") == "1"
    if not skip_sign:
        keys_ok, expected = _sign_keys_present("author")
        if not keys_ok:
            print(
                "ERROR: chiave Ed25519 author non trovata.\n"
                f"  Atteso: {expected}\n"
                "  Genera con: python3 /opt/myclaw/runtime/sign.py keygen author\n"
                "  Oppure: usa --no-sign per saltare la firma (sviluppo).",
                file=sys.stderr,
            )
            return 2

    print(f"Parsing {skill_path}...")
    parsed = parse_skill_md(skill_path)
    print(f"  skill: {parsed.name} v{parsed.version} - {len(parsed.sub_commands)} sub-commands")

    print(f"Translating sub-commands -> ExecutorPlans...")
    plans, rejected = translate_skill(
        parsed,
        imported_from_url=args.url or f"agentskills.io/local/{parsed.name}",
        imported_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    print(f"  plans: {len(plans)}, translator-rejected: {len(rejected)}")

    executors_dir = _executors_base() / parsed.name
    executors_dir.mkdir(parents=True, exist_ok=True)

    print(f"Codegen in {executors_dir}...")
    generated = []
    for p in plans:
        out = generate_executor_files(p, parsed, executors_dir)
        generated.append((p.name, out))

    print(f"Copying skill scripts/references to {_skills_dir() / parsed.name}...")
    if skill_path.parent.exists() and skill_path.parent != Path("/"):
        dest = _skills_dir() / parsed.name
        dest.mkdir(parents=True, exist_ok=True)
        for sub in ("scripts", "references"):
            src = skill_path.parent / sub
            if not (src.exists() and src.is_dir()):
                continue
            target = dest / sub
            # Re-import dalla cache locale: src e target coincidono.
            if src.resolve() == target.resolve():
                continue
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)
        # Copy SKILL.md anche per provenance. Skip se la sorgente e' gia'
        # nella cache locale (re-import: dest == skill_path).
        dest_skill = dest / "SKILL.md"
        if skill_path.resolve() != dest_skill.resolve():
            shutil.copy2(skill_path, dest_skill)

    print(f"Admission policy (ADR 0114 + ADR 0122)...")
    skip_binding = bool(args.update)
    report = admit_skill_import(
        parsed, plans,
        executor_dir=executors_dir,
        skip_binding_check=skip_binding,
        skip_l2=bool(args.skip_l2),
        skip_l6=bool(args.skip_l6),
    )
    print(f"  accepted: {len(report.accepted)}")
    print(f"  rejected: {len(report.rejected)}")

    # Rimuovi file dei plan rejected (non vogliamo executor non admitti).
    for v in report.rejected:
        d = executors_dir / v.plan_name
        if d.exists():
            shutil.rmtree(d)

    if args.no_sign or os.environ.get("METNOS_SKILLS_NO_SIGN") == "1":
        print("Signing: skipped (--no-sign or METNOS_SKILLS_NO_SIGN=1)")
    else:
        print(f"Signing accepted executors with Ed25519...")
        for v in report.accepted:
            d = executors_dir / v.plan_name
            if d.exists():
                status = _try_sign_executor(d)
                print(f"  {v.plan_name}: {status}")

    # Gap 6 (10/5/2026): auto-add smoke routing assertion per ogni accepted.
    # NOTA: lo smoke runner canonico (`smoke.py`) carica BATTERY_IMPORTS via
    # `smoke_imports.py` modulo a /opt/myclaw/runtime/. In test env locale
    # (sys.path non punta a /opt/myclaw) il modulo non e' raggiungibile e
    # il blocco e' silentemente saltato.
    if not args.skip_smoke_battery:
        added_n = _add_smoke_cases_for(report, parsed)
        if added_n > 0:
            print(f"Smoke battery: +{added_n} case in BATTERY_IMPORTS")

    # Audit JSONL append: ogni import lascia traccia in imports.jsonl,
    # con le rejection sia del translator (vocab gate) sia dell'admission (L2/L6).
    _append_audit(parsed, plans, report, translator_rejected=rejected)

    print()
    print(f"Summary:")
    for v in report.accepted:
        print(f"  + {v.plan_name}")
    for v in report.rejected:
        print(f"  - {v.plan_name} ({'; '.join(v.reasons)})")

    print()
    print(f"Audit log: {report.audit_log_path}")
    return 0 if report.accepted else 1


def _add_smoke_cases_for(report, parsed) -> int:
    """Aggiunge case smoke per ogni accepted. Riusa il `smoke_battery_case`
    gia' calcolato in admission (`_smoke_case_for_plan`).

    Ritorna il numero di case aggiunti (esclusi i duplicati skippati).
    """
    canonical = Path("/opt/myclaw/runtime")
    if not canonical.exists():
        return 0
    if str(canonical) not in sys.path:
        sys.path.insert(0, str(canonical))
    try:
        import smoke_imports  # type: ignore
    except ImportError:
        return 0
    n = 0
    skill_url = ""
    for v in report.accepted:
        case = v.smoke_battery_case or {}
        query = case.get("query")
        if not query:
            continue
        arg_keys = case.get("expected_arg_keys") or []
        # Provenance per audit nel BATTERY_IMPORTS case.
        if not skill_url:
            skill_url = f"agentskills.io/local/{parsed.name}"
        if smoke_imports.add_case(
            query=query,
            expected_first_tool=v.plan_name,
            expected_arg_keys=set(arg_keys),
            imported_from=skill_url,
            min_pass_rate=case.get("min_pass_rate", 0.9),
        ):
            n += 1
    return n


def _append_audit(parsed, plans, report,
                  *, translator_rejected: Optional[list] = None) -> None:
    """Append una riga JSONL per ogni import (timestamp + outcome).

    Path: $METNOS_AUDIT_DIR/imports.jsonl o
    ~/.local/share/metnos/synth_audit/imports.jsonl.

    `translator_rejected` (list of (domain, action, reason)) raccoglie i
    sub-command rifiutati a vocab-gate level (Layer 1, ADR 0114): vocab
    map exclude_reason, qualifier non in vocab.QUALIFIERS, name collision.
    """
    audit = _audit_log_path()
    tr_rej = []
    for item in (translator_rejected or []):
        if isinstance(item, (list, tuple)) and len(item) == 3:
            d, a, reason = item
            tr_rej.append({"domain": d, "action": a, "reason": reason})
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "skill": parsed.name,
        "version": parsed.version,
        "plans": len(plans),
        "accepted": [v.plan_name for v in report.accepted],
        "rejected": [
            {"plan": v.plan_name, "reasons": v.reasons}
            for v in report.rejected
        ],
        "translator_rejected": tr_rej,
    }
    with audit.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Sub-command: list
# ---------------------------------------------------------------------------


def _cmd_list(args) -> int:
    base = _executors_base()
    if not base.exists():
        print("(no imported skills)")
        return 0
    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        executors = []
        binding = ""
        version = ""
        for child in sorted(skill_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest = child / "manifest.toml"
            if manifest.exists():
                executors.append(child.name)
                if not binding:
                    text = manifest.read_text(errors="ignore")
                    import re
                    m = re.search(r'imported_from\s*=\s*"([^"]+)"', text)
                    if m:
                        binding = m.group(1).split("/")[-1]
                    m = re.search(r'source_version\s*=\s*"([^"]+)"', text)
                    if m:
                        version = m.group(1)
        print(f"- {skill_dir.name} (binding={binding} v{version}): {len(executors)} executors")
        if args.verbose:
            for e in executors:
                print(f"    {e}")
    return 0


# ---------------------------------------------------------------------------
# Sub-command: uninstall
# ---------------------------------------------------------------------------


def _cmd_uninstall(args) -> int:
    skill_dir = _executors_base() / args.skill
    if not skill_dir.exists():
        print(f"Not found: {skill_dir}", file=sys.stderr)
        return 1
    n = sum(1 for _ in skill_dir.iterdir())
    shutil.rmtree(skill_dir)
    print(f"Removed {n} executors from {skill_dir}")

    sk = _skills_dir() / args.skill
    if sk.exists():
        shutil.rmtree(sk)
        print(f"Removed skill data {sk}")
    return 0


# ---------------------------------------------------------------------------
# Sub-command: status
# ---------------------------------------------------------------------------


def _cmd_status(args) -> int:
    skill_dir = _executors_base() / args.skill
    if not skill_dir.exists():
        print(f"Not found: {skill_dir}", file=sys.stderr)
        return 1
    executors = []
    for child in sorted(skill_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "manifest.toml"
        if manifest.exists():
            executors.append(child.name)
    print(f"Skill: {args.skill}")
    print(f"Executors: {len(executors)}")
    for e in executors:
        # Cerca turn JSONL per stats (richiede runtime/turn_logs - skipped).
        print(f"  - {e}: (live stats post Layer 3 efficacy ager)")
    audit = _audit_log_path()
    if audit.exists():
        relevant = []
        for line in audit.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("skill") == args.skill:
                relevant.append(e)
        if relevant:
            last = relevant[-1]
            print(f"Last import: {last.get('ts')}")
            print(f"  accepted: {last.get('accepted', [])}")
            print(f"  rejected: {len(last.get('rejected', []))}")
    return 0


# ---------------------------------------------------------------------------
# Sub-command: evaluate
# ---------------------------------------------------------------------------


def _cmd_evaluate(args) -> int:
    from skill_parser import parse_skill_md
    from skill_translator import translate_skill
    from skill_admission import admit_skill_import

    skill_path = _skills_dir() / args.skill / "SKILL.md"
    if not skill_path.exists():
        print(f"Skill source non trovato: {skill_path}", file=sys.stderr)
        return 1

    parsed = parse_skill_md(skill_path)
    plans, _rej = translate_skill(parsed)
    executors_dir = _executors_base() / parsed.name

    report = admit_skill_import(
        parsed, plans,
        executor_dir=executors_dir,
        skip_binding_check=True,  # re-evaluation, binding gia' nostro
    )
    print(f"Re-evaluation of {args.skill}:")
    print(f"  accepted: {len(report.accepted)}")
    print(f"  rejected: {len(report.rejected)}")
    for v in report.rejected:
        print(f"    - {v.plan_name}: {'; '.join(v.reasons)}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="metnos-skills",
        description="Importer SKILL.md -> executor Metnos.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("import", help="Import SKILL.md")
    sp.add_argument("skill", help="path a SKILL.md o dir contenente SKILL.md")
    sp.add_argument("--url", default="", help="provenance URL (optional)")
    sp.add_argument("--update", action="store_true",
                    help="re-import: skip binding uniqueness check")
    sp.add_argument("--no-sign", action="store_true", help="skip Ed25519 sign")
    sp.add_argument("--skip-l2", action="store_true", help="skip L2 affinity check (dev)")
    sp.add_argument("--skip-l6", action="store_true", help="skip L6 semantic verifier (dev)")
    sp.add_argument("--skip-smoke-battery", action="store_true",
                    help="skip auto-add to BATTERY_IMPORTS (dev/test)")
    sp.set_defaults(func=_cmd_import)

    sp = sub.add_parser("list", help="Elenca skill importate")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=_cmd_list)

    sp = sub.add_parser("uninstall", help="Rimuove skill importata")
    sp.add_argument("skill", help="skill name")
    sp.set_defaults(func=_cmd_uninstall)

    sp = sub.add_parser("status", help="Stato skill (invocazioni, success rate)")
    sp.add_argument("skill", help="skill name")
    sp.set_defaults(func=_cmd_status)

    sp = sub.add_parser("evaluate", help="Re-invoca admission policy")
    sp.add_argument("skill", help="skill name")
    sp.set_defaults(func=_cmd_evaluate)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
