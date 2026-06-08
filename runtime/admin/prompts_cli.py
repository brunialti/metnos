#!/usr/bin/env python3
"""metnos-prompts — admin CLI per i prompt LLM in `runtime/prompts/`.

ADR 0092 (5/5/2026): prompt come dati persistiti su filesystem.

Usage:
    python3 -m admin.prompts_cli list
    python3 -m admin.prompts_cli show <role> [--lang=it]
    python3 -m admin.prompts_cli validate
    python3 -m admin.prompts_cli translate <role> [--to=en]
    python3 -m admin.prompts_cli translate-all [--to=en]
    python3 -m admin.prompts_cli sync-status
    python3 -m admin.prompts_cli review <role> [--lang=en]
    python3 -m admin.prompts_cli mark-synced <role> [--lang=en]
    python3 -m admin.prompts_cli validate-cross-lang
    python3 -m admin.prompts_cli add-language <code> [--source-lang=it]

list                Tabella ruoli: size, lingue, last commit (git log o mtime).
show                Render finale del prompt con vars stub (variabili non
                    risolte appaiono come `<unresolved:var>` placeholder).
                    Flag `--lang` (default `config.DEFAULT_LANG`).
validate            Lint sintassi MiniJinja per ogni .j2 + boot
                    validate_invariant().
translate           One-shot: traduce un ruolo da IT a `--to=<lang>` via LLM
                    tier=wise; salva il candidato in `prompts/<lang>/_pending/`.
translate-all       Batch: traduce ogni `.j2` di `prompts/it/` non gia'
                    presente in `prompts/<lang>/`.
sync-status         Tabella ruolo, mtime IT vs EN, lag, presenza candidato.
                    Fallback mtime se git non disponibile.
review              Mostra diff strutturale del candidato `_pending/<role>.j2.candidate`
                    vs sorgente IT (preview) e validation report.
mark-synced         Promuove `_pending/<role>.j2.candidate` a
                    `prompts/<lang>/<role>.j2`. Solo se validation OK.
validate-cross-lang Per ogni ruolo, verifica match placeholder + sintassi
                    + len range tra IT e ogni altra lingua presente.
add-language        Bootstrap di una nuova lingua: crea `prompts/<code>/`,
                    chiama `i18n_cli add-lang` per popolare i18n.sqlite con
                    placeholder, e stampa istruzioni per attivazione e
                    triggering manuale del translator notturno. Idempotente.

Quality flag (translate / translate-all): default `wise` = Gemma 4 26B
locale (gratuito); `frontier` = Anthropic Opus 4.7 (~$0.015/call). Doc:
docs/it/architecture/multilang.html cap. 7.
"""
import argparse
import difflib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import minijinja  # type: ignore
import prompt_loader
import config as _C  # §7.11
from config import DEFAULT_LANG

PROMPTS_BASE = Path(__file__).resolve().parent.parent / "prompts"


def _git_last_commit(file_path: Path) -> str:
    """Ritorna data ultimo commit (YYYY-MM-DD) o '-' se file non in git."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=short", "--", str(file_path)],
            capture_output=True, text=True, cwd=str(PROMPTS_BASE.parent.parent),
            timeout=5,
        )
        return out.stdout.strip() or "-"
    except Exception:
        return "-"


def _list_languages() -> list[str]:
    if not PROMPTS_BASE.is_dir():
        return []
    return sorted(p.name for p in PROMPTS_BASE.iterdir() if p.is_dir())


def cmd_list(_args) -> int:
    """Tabella: ruolo, size (it), lingue presenti, last commit."""
    if not PROMPTS_BASE.is_dir():
        print(f"prompts dir non esiste: {PROMPTS_BASE}", file=sys.stderr)
        return 1
    langs = _list_languages()
    if "it" not in langs:
        print("manca runtime/prompts/it/ (canonical reference)", file=sys.stderr)
        return 1
    canonical_files = sorted(p.name for p in (PROMPTS_BASE / "it").glob("*.j2"))
    if not canonical_files:
        print(f"(nessun prompt in {PROMPTS_BASE / 'it'}/)")
        return 0

    print(f"{'role':<24} {'size_it':>8}  {'languages':<20}  last_commit")
    print("-" * 72)
    for fname in canonical_files:
        role = fname[:-3] if fname.endswith(".j2") else fname
        it_path = PROMPTS_BASE / "it" / fname
        size = it_path.stat().st_size if it_path.is_file() else 0
        present = []
        for lang in langs:
            if (PROMPTS_BASE / lang / fname).is_file():
                present.append(lang)
        last = _git_last_commit(it_path)
        print(f"{role:<24} {size:>8}  {','.join(present):<20}  {last}")
    return 0


class _UndefinedTracker:
    """Helper per evidenziare variabili non risolte come <unresolved:NAME>."""


def cmd_show(args) -> int:
    """Render finale del prompt con vars stub.

    Le variabili non passate appaiono come `<unresolved:NAME>` invece di
    triggerare errori — utile per inspection veloce senza il runtime
    context completo.
    """
    role = args.role
    lang = args.lang or DEFAULT_LANG
    role_path = PROMPTS_BASE / lang / f"{role}.j2"
    if not role_path.is_file():
        print(f"prompt non trovato: {role_path}", file=sys.stderr)
        return 1
    # Estrai le variabili dichiarate via undeclared_variables_in_template
    try:
        tpl_src = role_path.read_text(encoding="utf-8")
        env = minijinja.Environment()
        undeclared = env.undeclared_variables_in_str(tpl_src)
    except Exception as e:
        print(f"parse error: {e}", file=sys.stderr)
        return 1
    stub_vars = {name: f"<unresolved:{name}>" for name in undeclared}
    out = prompt_loader.get(role, lang, **stub_vars)
    print(out)
    return 0


def cmd_validate(_args) -> int:
    """Lint sintassi MiniJinja + boot invariant check."""
    if not PROMPTS_BASE.is_dir():
        print(f"prompts dir non esiste: {PROMPTS_BASE}", file=sys.stderr)
        return 1
    env = minijinja.Environment()
    n_files = 0
    n_errors = 0
    for j2 in PROMPTS_BASE.glob("**/*.j2"):
        # Skip pending candidates: sono draft, non parte del runtime
        if "_pending" in j2.parts:
            continue
        n_files += 1
        try:
            env.add_template(str(j2), j2.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"SYNTAX ERROR {j2}: {e}", file=sys.stderr)
            n_errors += 1
    if n_errors:
        print(f"{n_errors}/{n_files} files con errori di sintassi", file=sys.stderr)
        return 1
    try:
        prompt_loader.validate_invariant()
    except RuntimeError as e:
        print(f"INVARIANT FAIL: {e}", file=sys.stderr)
        return 1
    print(f"OK: {n_files} file .j2 sintatticamente validi, invariante rispettato.")
    return 0


# ===========================================================================
# Phase 3 (ADR 0092): bilinguismo IT+EN attivo — translate / sync / review
# ===========================================================================

def _file_mtime(p: Path) -> float:
    """Mtime del file, oppure 0.0 se non esiste."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _resolve_tier_from_quality(args) -> str:
    """Resolve quality flag → LLM tier. `frontier` -> tier 'frontier'
    (Anthropic Opus 4.7, ~$0.015/call); `wise` (default) -> tier 'wise'
    (Gemma 4 26B locale o equivalente). Compat: --tier override esplicito.
    """
    if getattr(args, "tier", None):
        return args.tier
    quality = getattr(args, "quality", None) or "wise"
    if quality == "frontier":
        return "frontier"
    return "wise"


def cmd_translate(args) -> int:
    """One-shot: traduce un ruolo IT → <target lang>; salva candidato in _pending/."""
    role = args.role
    target = args.to or "en"
    src = PROMPTS_BASE / "it" / f"{role}.j2"
    if not src.is_file():
        print(f"sorgente non esiste: {src}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(PROMPTS_BASE.parent))
    from i18n_translator import translate_prompt_file  # type: ignore
    tier = _resolve_tier_from_quality(args)
    res = translate_prompt_file(role, target_lang=target, source_lang="it",
                                  tier=tier)
    print(f"role={res.get('role')} ok={res.get('ok')} "
          f"it_len={res.get('it_len')} en_len={res.get('en_len')} "
          f"ratio={res.get('ratio')}")
    if res.get("validation"):
        print("validation issues:")
        for v in res["validation"]:
            print(f"  - {v}")
    if res.get("error"):
        print(f"error: {res['error']}", file=sys.stderr)
    if res.get("candidate_path"):
        print(f"candidate saved: {res['candidate_path']}")
    return 0 if res.get("ok") else 1


def cmd_translate_all(args) -> int:
    """Batch: traduce ogni `.j2` IT non gia' presente in `prompts/<target>/`."""
    target = args.to or "en"
    sys.path.insert(0, str(PROMPTS_BASE.parent))
    from i18n_translator import translate_all_prompts  # type: ignore
    tier = _resolve_tier_from_quality(args)
    print(f"translate-all: tier={tier} target={target}"
          + (" [FRONTIER opt-in: ~$0.015/call]" if tier == "frontier" else ""))
    print(f"{'role':<32} {'status':<10} {'it_len':>8} {'en_len':>8} {'ratio':>6}")
    print("-" * 72)
    results = translate_all_prompts(
        target_lang=target, source_lang="it", tier=tier,
        skip_existing_synced=not args.force,
    )
    n_ok, n_skip, n_fail = 0, 0, 0
    for r in results:
        role = r.get("role", "?")
        if r.get("skipped"):
            print(f"{role:<32} {'SKIP':<10} {'':>8} {'':>8} {'':>6}")
            n_skip += 1
            continue
        if r.get("ok"):
            print(f"{role:<32} {'OK':<10} {r.get('it_len',0):>8} "
                  f"{r.get('en_len',0):>8} {r.get('ratio',''):>6}")
            n_ok += 1
        else:
            err_short = (r.get("error") or
                         ("; ".join(r.get("validation", [])) if r.get("validation") else "?"))
            print(f"{role:<32} {'FAIL':<10} {r.get('it_len',0):>8} "
                  f"{r.get('en_len',0):>8} {r.get('ratio',''):>6}")
            print(f"    └─ {err_short[:200]}")
            n_fail += 1
    print()
    print(f"summary: ok={n_ok} skipped={n_skip} fail={n_fail} total={len(results)}")
    return 0 if n_fail == 0 else 1


def cmd_sync_status(_args) -> int:
    """Tabella ruolo + mtime IT vs EN + lag + presenza candidato.

    Determinismo (the design guide §7.9): preferiamo `git log` se il working dir
    e' un git repo, altrimenti fallback a mtime. <install_root> NON e' un
    git repo, quindi normalmente fallback mtime.
    """
    if not PROMPTS_BASE.is_dir():
        print(f"prompts dir non esiste: {PROMPTS_BASE}", file=sys.stderr)
        return 1
    canonical = sorted(p.name for p in (PROMPTS_BASE / "it").glob("*.j2"))
    if not canonical:
        print("(nessun prompt in prompts/it/)")
        return 0
    langs = [d.name for d in PROMPTS_BASE.iterdir()
             if d.is_dir() and d.name not in ("it",)]
    langs = sorted(langs)
    use_git = _detect_git_root() is not None

    print(f"sync-status (source-of-truth: {'git' if use_git else 'mtime'})")
    print(f"  langs detected (besides 'it'): {langs or '(none)'}")
    print()
    header = f"{'role':<32} {'it_mtime':<19}"
    for lang in langs:
        header += f" {lang+'_mtime':<19} {'lag(s)':>10} {'cand':<5}"
    print(header)
    print("-" * len(header))
    n_lag = 0
    for fname in canonical:
        role = fname[:-3]
        it_p = PROMPTS_BASE / "it" / fname
        it_mt = _file_mtime(it_p)
        row = f"{role:<32} {_fmt_mtime(it_mt):<19}"
        for lang in langs:
            tgt_p = PROMPTS_BASE / lang / fname
            tgt_mt = _file_mtime(tgt_p)
            cand_p = PROMPTS_BASE / lang / "_pending" / (fname + ".candidate")
            cand_present = "y" if cand_p.is_file() else "n"
            if tgt_mt == 0.0:
                lag = "MISSING"
            else:
                # Lag: positivo se IT piu' nuovo (EN out-of-sync)
                lag_s = int(it_mt - tgt_mt)
                lag = str(lag_s) if lag_s != 0 else "0"
                if lag_s > 0:
                    n_lag += 1
            row += f" {_fmt_mtime(tgt_mt):<19} {lag:>10} {cand_present:<5}"
        print(row)
    print()
    print(f"({n_lag} files with lang lag > 0; cand=y means a pending candidate exists in _pending/)")
    return 0


def _fmt_mtime(t: float) -> str:
    if t == 0.0:
        return "(missing)"
    import datetime as _dt
    return _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def _detect_git_root() -> Path | None:
    """Ritorna la radice del git repo se PROMPTS_BASE e' dentro a uno, sonno None."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
            cwd=str(PROMPTS_BASE), timeout=3,
        )
        if out.returncode == 0:
            return Path(out.stdout.strip())
    except Exception:
        pass
    return None


def cmd_review(args) -> int:
    """Mostra diff strutturale del candidato `_pending/<role>.j2.candidate` vs IT
    (preview) e validation report. NON promuove.
    """
    role = args.role
    lang = args.lang or "en"
    cand = PROMPTS_BASE / lang / "_pending" / f"{role}.j2.candidate"
    src = PROMPTS_BASE / "it" / f"{role}.j2"
    if not cand.is_file():
        print(f"candidato non esiste: {cand}", file=sys.stderr)
        print(f"esegui prima: metnos-prompts translate {role} --to={lang}",
              file=sys.stderr)
        return 1
    if not src.is_file():
        print(f"sorgente IT non esiste: {src}", file=sys.stderr)
        return 1
    src_text = src.read_text(encoding="utf-8")
    cand_text = cand.read_text(encoding="utf-8")
    sys.path.insert(0, str(PROMPTS_BASE.parent))
    from i18n_translator import _validate_translation  # type: ignore
    ok, errors = _validate_translation(src_text, cand_text)
    print(f"role={role} lang={lang}")
    print(f"  src_path={src}")
    print(f"  cand_path={cand}")
    print(f"  it_len={len(src_text)} cand_len={len(cand_text)} "
          f"ratio={round(len(cand_text)/max(1,len(src_text)),3)}")
    print(f"  validation={'OK' if ok else 'FAIL'}")
    if errors:
        for e in errors:
            print(f"    - {e}")
    print()
    print("--- structural diff (first 80 lines, IT left, candidate right) ---")
    src_lines = src_text.splitlines(keepends=False)
    cand_lines = cand_text.splitlines(keepends=False)
    diff = list(difflib.unified_diff(src_lines, cand_lines,
                                       fromfile=f"it/{role}.j2",
                                       tofile=f"{lang}/_pending/{role}.j2.candidate",
                                       lineterm=""))
    if not diff:
        print("(no diff lines — files identical, suspicious)")
    else:
        for line in diff[:160]:
            print(line)
        if len(diff) > 160:
            print(f"... ({len(diff)-160} more diff lines truncated)")
    print()
    print("If validation OK, promote with:")
    print(f"  metnos-prompts mark-synced {role} --lang={lang}")
    return 0 if ok else 1


def cmd_mark_synced(args) -> int:
    """Promuove `_pending/<role>.j2.candidate` a `prompts/<lang>/<role>.j2`.

    Solo se validation OK (rifiuta drift candidate). Non distrugge il
    candidato (resta in _pending per audit).
    """
    role = args.role
    lang = args.lang or "en"
    cand = PROMPTS_BASE / lang / "_pending" / f"{role}.j2.candidate"
    src = PROMPTS_BASE / "it" / f"{role}.j2"
    target = PROMPTS_BASE / lang / f"{role}.j2"
    if not cand.is_file():
        print(f"candidato non esiste: {cand}", file=sys.stderr)
        return 1
    if not src.is_file():
        print(f"sorgente IT non esiste: {src}", file=sys.stderr)
        return 1
    src_text = src.read_text(encoding="utf-8")
    cand_text = cand.read_text(encoding="utf-8")
    sys.path.insert(0, str(PROMPTS_BASE.parent))
    from i18n_translator import _validate_translation  # type: ignore
    ok, errors = _validate_translation(src_text, cand_text)
    if not ok and not args.force:
        print(f"REFUSED: validation failed for {role}/{lang}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print("use --force to bypass (not recommended)", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(cand_text, encoding="utf-8")
    print(f"promoted: {cand} -> {target}")
    return 0


def cmd_validate_cross_lang(_args) -> int:
    """Per ogni ruolo, controlla cross-lang: placeholder match + sintassi
    + len range tra IT e ogni altra lingua presente. Exit 0 se tutto OK.
    """
    if not PROMPTS_BASE.is_dir():
        print(f"prompts dir non esiste: {PROMPTS_BASE}", file=sys.stderr)
        return 1
    canonical_dir = PROMPTS_BASE / "it"
    if not canonical_dir.is_dir():
        print(f"manca canonical: {canonical_dir}", file=sys.stderr)
        return 1
    langs = sorted(d.name for d in PROMPTS_BASE.iterdir()
                   if d.is_dir() and d.name != "it")
    sys.path.insert(0, str(PROMPTS_BASE.parent))
    from i18n_translator import _validate_translation  # type: ignore

    n_total, n_fail = 0, 0
    print(f"validate-cross-lang: secondary langs={langs or '(none)'}")
    for fname in sorted(p.name for p in canonical_dir.glob("*.j2")):
        role = fname[:-3]
        src_text = (canonical_dir / fname).read_text(encoding="utf-8")
        for lang in langs:
            tgt = PROMPTS_BASE / lang / fname
            if not tgt.is_file():
                continue  # missing handled by validate_invariant separately
            n_total += 1
            tgt_text = tgt.read_text(encoding="utf-8")
            ok, errors = _validate_translation(src_text, tgt_text)
            if not ok:
                n_fail += 1
                print(f"  FAIL {lang}/{role}: {'; '.join(errors)}")
    print()
    if n_total == 0:
        print("no cross-lang pairs to check (only 'it' present).")
        return 0
    if n_fail:
        print(f"{n_fail}/{n_total} cross-lang pairs failed validation.")
        return 1
    print(f"OK: {n_total} cross-lang pairs validated.")
    return 0


def cmd_add_language(args) -> int:
    """Bootstrap di una nuova lingua. Crea `prompts/<code>/`, chiama
    `i18n_cli add-lang` per popolare i18n.sqlite con placeholder, e
    stampa istruzioni. Idempotente.

    Layer 1 (prompts): directory creata vuota; daemon notturno
    `i18n_translator.run_loop()` generera' candidati in `_pending/`.
    Layer 2 (manifest description): nessuna azione immediata; il daemon a
    ogni cycle scansiona i manifest TOML con tabella mancante.
    Layer 3 (i18n.sqlite): bootstrap rows con `needs_translation=1`.
    """
    code = args.code
    src = args.source_lang or "it"

    # Validazione minimale: codice ISO 639-1 (2 lettere) o BCP-47 (es. zh-Hans)
    if not code or not code[:2].isalpha():
        print(f"codice lingua non valido: {code!r}", file=sys.stderr)
        print("usa codice ISO 639-1 (es. fr, de, es) o BCP-47 (es. zh-Hans)",
              file=sys.stderr)
        return 1
    if code == "it" or code == src:
        print(f"NOTE: '{code}' coincide con la sorgente; bootstrap idempotente.",
              file=sys.stderr)

    # Layer 1 — crea prompts/<code>/
    promptsd = PROMPTS_BASE / code
    promptsd.mkdir(parents=True, exist_ok=True)
    pendingd = promptsd / "_pending"
    pendingd.mkdir(parents=True, exist_ok=True)

    # Layer 3 — bootstrap i18n.sqlite via i18n_cli (invocazione del modulo).
    # Eseguito come subprocess per riusare la logica esistente in cmd_add_lang
    # senza duplicare codice (the design guide §7.1: niente shim).
    rt_dir = PROMPTS_BASE.parent  # <install_root>/runtime
    import os as _os
    env = _os.environ.copy()
    env["PYTHONPATH"] = str(rt_dir) + ":" + env.get("PYTHONPATH", "")
    cmd_argv = [sys.executable, "-m", "admin.i18n_cli", "add-lang", code,
                "--source-lang", src]
    try:
        r = subprocess.run(cmd_argv, env=env, cwd=str(rt_dir),
                            capture_output=True, text=True, timeout=60)
    except Exception as e:
        print(f"ERROR: invocazione i18n_cli fallita: {e}", file=sys.stderr)
        return 2
    if r.stdout:
        for line in r.stdout.splitlines():
            print(f"  i18n_cli> {line}")
    if r.returncode != 0:
        print(f"i18n_cli add-lang exit={r.returncode}", file=sys.stderr)
        if r.stderr:
            print(r.stderr, file=sys.stderr)
        return r.returncode

    # Audit log opt-in (best-effort, non blocca su errori).
    try:
        audit_dir = _C.PATH_USER_DATA / "multilang"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / "audit.jsonl"
        import json as _json
        import datetime as _dt
        entry = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "event": "add_language",
            "code": code,
            "source_lang": src,
            "prompts_dir": str(promptsd),
        }
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # audit best-effort

    # Output utente — istruzioni passo-passo.
    print(f"Lingua '{code}' bootstrap completato.")
    print(f"  - prompts/{code}/ creata (vuota, daemon notturno generera' candidate)")
    print(f"  - i18n.sqlite: {code} bootstrap rows pending")
    print(f"  - Manifest description: il daemon scansionera' al prossimo cycle")
    print()
    print("Per triggerare manualmente la traduzione subito:")
    print("  <install_root>/deploy/run_prompts_translator.sh")
    print()
    print(f"Per attivare la lingua: METNOS_LANG={code} nei systemd unit + restart")
    return 0


# ===========================================================================
# Audit qualita' traduzione (sprint 6/5/2026)
# ===========================================================================

def _config_translator_tier_path() -> Path:
    """Path della config persistente di tier-resolution per il daemon."""
    return _C.PATH_USER_CONFIG / "translator_tier.toml"


def _audit_pick_prompts(sample: str) -> list[str]:
    """Sceglie i ruoli da audit. `sample='all'` tutti, altrimenti N random
    deterministico (seed fisso)."""
    src = PROMPTS_BASE / "it"
    roles = sorted(p.stem for p in src.glob("*.j2"))
    if sample == "all" or sample == "":
        return roles
    try:
        n = int(sample)
    except (TypeError, ValueError):
        return roles
    if n >= len(roles):
        return roles
    import random
    rng = random.Random(42)  # seed fisso per ripetibilita' audit
    return sorted(rng.sample(roles, n))


def _audit_one(role: str, target_lang: str, *, dry_run: bool) -> dict:
    """Esegue audit per UN ruolo: traduce con wise + frontier, calcola score
    di entrambi.

    `dry_run`: niente call LLM, scores fittizi deterministici (seed=hash(role)).
    """
    src_path = PROMPTS_BASE / "it" / f"{role}.j2"
    if not src_path.is_file():
        return {"role": role, "ok": False, "error": "source not found"}
    src_text = src_path.read_text(encoding="utf-8")

    if dry_run:
        # Score simulati deterministici (per test della logica di
        # threshold senza chiamare LLM).
        h = abs(hash(role)) % 1000
        score_wise = 0.80 + (h % 17) / 100  # [0.80, 0.96]
        score_frontier = score_wise + 0.02 + (h % 7) / 100  # +[0.02, 0.08]
        return {
            "role": role,
            "ok": True,
            "wise": {
                "score": round(score_wise, 4),
                "cosine_sim": round(score_wise - 0.05, 4),
                "roundtrip_sim": round(score_wise + 0.02, 4),
                "placeholder_integrity": True,
            },
            "frontier": {
                "score": round(score_frontier, 4),
                "cosine_sim": round(score_frontier - 0.04, 4),
                "roundtrip_sim": round(score_frontier + 0.01, 4),
                "placeholder_integrity": True,
            },
            "dry_run": True,
        }

    sys.path.insert(0, str(PROMPTS_BASE.parent))
    from i18n_translator import translate_prompt_file  # type: ignore
    from translator_quality import score_translation  # type: ignore

    out: dict = {"role": role, "ok": True}
    for tier in ("wise", "frontier"):
        try:
            res = translate_prompt_file(role, target_lang=target_lang,
                                          source_lang="it", tier=tier)
        except Exception as exc:
            out[tier] = {"score": 0.0, "error": f"translate failed: {exc}"}
            out["ok"] = False
            continue
        cand = res.get("candidate_path")
        if not cand or not Path(cand).is_file():
            out[tier] = {"score": 0.0,
                          "error": res.get("error") or "no candidate emitted"}
            out["ok"] = False
            continue
        translated = Path(cand).read_text(encoding="utf-8")
        sc = score_translation(src_text, translated, "it", target_lang,
                                 tier_used=tier)
        out[tier] = {
            "score": sc["score"],
            "cosine_sim": sc["cosine_sim"],
            "roundtrip_sim": sc["roundtrip_sim"],
            "placeholder_integrity": sc["placeholder_integrity"],
        }
    return out


def _audit_decide(per_prompt: list[dict], *,
                    individual_pct: float, threshold_pct: float) -> dict:
    """Aggrega risultati per-prompt e decide tier raccomandato.

    Logica:
      - per ogni prompt con score_wise e score_frontier validi:
          individual_above = score_wise >= individual_pct * score_frontier
      - se (count_above / total_valid) >= threshold_pct → tier 'wise'
        (Gemma sufficiente per la maggioranza)
      - altrimenti → tier 'frontier'.
    """
    valid = [p for p in per_prompt if p.get("ok")
              and "score" in (p.get("wise") or {})
              and "score" in (p.get("frontier") or {})]
    if not valid:
        return {
            "recommended_tier": "wise",
            "n_total": 0, "n_valid": 0,
            "n_individual_above": 0,
            "individual_above_pct": 0.0,
            "mean_wise": 0.0, "mean_frontier": 0.0,
            "delta": 0.0,
            "lagging_prompts": [],
            "threshold_satisfied": False,
        }
    mean_wise = sum(p["wise"]["score"] for p in valid) / len(valid)
    mean_frontier = sum(p["frontier"]["score"] for p in valid) / len(valid)
    above: list[dict] = []
    lagging: list[dict] = []
    for p in valid:
        sw = p["wise"]["score"]
        sf = p["frontier"]["score"]
        if sf <= 0 or sw >= individual_pct * sf:
            above.append(p)
        else:
            lagging.append(p)
    pct_above = len(above) / len(valid)
    threshold_satisfied = pct_above >= threshold_pct
    recommended = "wise" if threshold_satisfied else "frontier"
    return {
        "recommended_tier": recommended,
        "n_total": len(per_prompt),
        "n_valid": len(valid),
        "n_individual_above": len(above),
        "individual_above_pct": round(pct_above, 4),
        "mean_wise": round(mean_wise, 4),
        "mean_frontier": round(mean_frontier, 4),
        "delta": round(mean_frontier - mean_wise, 4),
        "lagging_prompts": [p["role"] for p in lagging],
        "threshold_satisfied": threshold_satisfied,
    }


def _audit_apply_config(decision: dict, *,
                          threshold_pct: float, individual_pct: float) -> Path:
    """Salva la decisione in `~/.config/metnos/translator_tier.toml`.

    File TOML letto dal wrapper `run_prompts_translator.sh` come fallback
    se la env `METNOS_TRANSLATOR_QUALITY` non e' settata.
    """
    import datetime as _dt
    cfg_path = _config_translator_tier_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = (
        "# Generato da `metnos-prompts audit-quality --apply`.\n"
        "# Letto da <install_root>/deploy/run_prompts_translator.sh come fallback\n"
        "# se METNOS_TRANSLATOR_QUALITY env var non e' settata.\n"
        "\n"
        "[translator]\n"
        f"tier = \"{decision['recommended_tier']}\"\n"
        f"audit_date = \"{now}\"\n"
        f"audit_score_wise = {decision['mean_wise']}\n"
        f"audit_score_frontier = {decision['mean_frontier']}\n"
        f"audit_n_prompts = {decision['n_valid']}\n"
        f"audit_n_individual_above = {decision['n_individual_above']}\n"
        f"audit_threshold_pct = {threshold_pct}\n"
        f"audit_individual_pct = {individual_pct}\n"
        f"recommended_tier = \"{decision['recommended_tier']}\"\n"
    )
    cfg_path.write_text(body, encoding="utf-8")
    try:
        cfg_path.chmod(0o600)
    except OSError:
        pass
    return cfg_path


def cmd_audit_quality(args) -> int:
    """Audit qualita' traduzione: confronta tier `wise` vs `frontier` su un
    sample di prompt. Calcola statistiche aggregate, raccomanda tier per il
    daemon, opzionalmente persiste config (`--apply`).

    Determinismo (the design guide §7.9): logica di soglia + decisione e' interamente
    deterministica. L'unica chiamata LLM e' `translate_prompt_file` (e
    back-translate dentro `score_translation`), invocata offline-batch.
    """
    target = args.to or "en"
    sample = args.sample or "all"
    threshold_pct = float(args.threshold_pct)
    individual_pct = float(args.individual_pct)
    dry_run = bool(args.dry_run)

    roles = _audit_pick_prompts(sample)
    if not roles:
        print(f"nessun prompt in {PROMPTS_BASE / 'it'}", file=sys.stderr)
        return 1

    print(f"Audit qualita' traduzione IT→{target.upper()} ({len(roles)} prompt)"
          + (" [DRY-RUN]" if dry_run else ""))
    print("─" * 65)

    per_prompt: list[dict] = []
    for i, role in enumerate(roles, 1):
        print(f"  [{i:>2}/{len(roles)}] {role:<40}", end="", flush=True)
        res = _audit_one(role, target, dry_run=dry_run)
        per_prompt.append(res)
        if res.get("ok"):
            sw = (res.get("wise") or {}).get("score", 0.0)
            sf = (res.get("frontier") or {}).get("score", 0.0)
            print(f" wise={sw:.3f}  frontier={sf:.3f}")
        else:
            err_w = (res.get("wise") or {}).get("error", "?")
            err_f = (res.get("frontier") or {}).get("error", "?")
            print(f" FAIL wise={err_w[:30]} frontier={err_f[:30]}")

    decision = _audit_decide(per_prompt,
                              individual_pct=individual_pct,
                              threshold_pct=threshold_pct)

    print()
    print(f"                         wise (Gemma)    frontier (Opus 4.7)")
    print(f"mean score:              {decision['mean_wise']:<15} "
          f"{decision['mean_frontier']:<15}")
    print(f"Δ mean:                  {decision['delta']:+.4f} "
          f"({100 * decision['delta'] / max(1e-9, decision['mean_frontier']):.1f}%)")
    print()
    print(f"Per-prompt analysis:")
    print(f"  prompts where wise >= {individual_pct:.2f} * frontier: "
          f"{decision['n_individual_above']}/{decision['n_valid']} "
          f"({decision['individual_above_pct'] * 100:.1f}%)")
    print(f"  prompts where wise lags significantly: "
          f"{len(decision['lagging_prompts'])} "
          f"({', '.join(decision['lagging_prompts'][:5])}"
          f"{'...' if len(decision['lagging_prompts']) > 5 else ''})")
    print()
    print(f"THRESHOLD: {threshold_pct * 100:.0f}% prompt sopra "
          f"{individual_pct:.2f} → "
          f"{'SODDISFATTA' if decision['threshold_satisfied'] else 'NON SODDISFATTA'} "
          f"({decision['individual_above_pct'] * 100:.1f}% "
          f"{'>=' if decision['threshold_satisfied'] else '<'} "
          f"{threshold_pct * 100:.0f}%)")
    print()
    print(f"RACCOMANDAZIONE: tier='{decision['recommended_tier']}'"
          + (" (Gemma 4 26B locale, $0/call)"
             if decision['recommended_tier'] == "wise"
             else " (Anthropic Opus 4.7, ~$0.015/call)"))
    if decision["lagging_prompts"]:
        print(f"  - {len(decision['lagging_prompts'])} prompt lagging: "
              "futuro override per-role possibile")

    if args.apply:
        cfg_path = _audit_apply_config(decision,
                                        threshold_pct=threshold_pct,
                                        individual_pct=individual_pct)
        print()
        print(f"Config salvata in {cfg_path}")
    if args.report:
        import json as _json
        report = {
            "decision": decision,
            "threshold_pct": threshold_pct,
            "individual_pct": individual_pct,
            "target_lang": target,
            "per_prompt": per_prompt,
        }
        rep_path = Path.cwd() / f"audit_quality_{target}.json"
        rep_path.write_text(_json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        print(f"Report dettagliato in {rep_path}")

    if not dry_run:
        # Stima costi: 2 call (wise + frontier) per ruolo + 2 back-translate.
        # Wise locale = $0; frontier ~$0.015/call * 2 = $0.030/ruolo.
        n_valid = decision["n_valid"]
        cost = round(n_valid * 0.030, 2)
        print(f"\nCosto audit stimato: ~{n_valid * 2} call frontier = ${cost} "
              "(wise gratis).")

    return 0


def cmd_bench(args) -> int:
    """Bench prompt-build perf: renderizza N=100 turni simulati con sezioni
    varie e riporta tempi mean/p50/p95, cache hit ratio e bytes totali.

    Determinismo §7.9: zero LLM, solo lookup tabellare + filesystem +
    MiniJinja env. Sample di sezioni deterministicamente fissato (seed=42)
    per ripetibilita' del bench.

    Subcommand options:
        --runs N         Numero render (default 100)
        --lang LANG      Lingua prompt (default config.DEFAULT_LANG)
        --warm           Warm cache prima della misura (default off,
                         misura mix cold+warm)
        --compare        Stampa anche mean per (core-only, mail-only, all)
    """
    from time import perf_counter
    import random
    import statistics

    runs = int(getattr(args, "runs", 100) or 100)
    lang = args.lang or DEFAULT_LANG
    warm_cache = bool(getattr(args, "warm", False))
    compare = bool(getattr(args, "compare", False))

    # Lista sezioni disponibili per questa lang. Se la struttura split
    # planner non esiste (raro su dev fresca), fallisci con messaggio chiaro.
    available_sections = prompt_loader.list_planner_sections(lang)
    if not available_sections:
        print(f"prompts/{lang}/planner/sections/ vuota o assente — "
              "split planner non disponibile, niente da benchare.",
              file=sys.stderr)
        return 1

    # Stub vars: scopri quali servono al planner e produci placeholder
    # stringhificati hashable per la cache. Estrai dal _core (le sub-sections
    # potrebbero avere variabili specifiche, ma usiamo lo stesso stub set).
    core_path = PROMPTS_BASE / lang / "planner" / "_core.j2"
    footer_path = PROMPTS_BASE / lang / "planner" / "_footer.j2"
    stub_vars: dict[str, str] = {}
    env = minijinja.Environment()
    for p in (core_path, footer_path):
        if not p.is_file():
            continue
        try:
            tpl_src = p.read_text(encoding="utf-8")
            for v in env.undeclared_variables_in_str(tpl_src):
                stub_vars.setdefault(v, f"<stub:{v}>")
        except Exception:
            continue
    for sec in available_sections:
        sec_path = PROMPTS_BASE / lang / "planner" / "sections" / f"{sec}.j2"
        if not sec_path.is_file():
            continue
        try:
            tpl_src = sec_path.read_text(encoding="utf-8")
            for v in env.undeclared_variables_in_str(tpl_src):
                stub_vars.setdefault(v, f"<stub:{v}>")
        except Exception:
            continue

    # Sample deterministico di "intent → sections" per simulare il turno.
    # Distribuzione: 30% degrade fallback (None → tutte le sezioni, caso
    # confidence-low), 30% single section, 25% multi (2-3 sezioni), 15%
    # all sezioni esplicite. NB: `sections=None` in compose() significa
    # "include TUTTE" per ADR Fase C (degrade graceful), non "solo core".
    rng = random.Random(42)
    samples: list[tuple[str, ...] | None] = []
    sec_pool = list(available_sections)
    for _ in range(runs):
        r = rng.random()
        if r < 0.30:
            samples.append(None)  # fallback all-sections
        elif r < 0.60 and "mail" in sec_pool:
            samples.append(("mail",))
        elif r < 0.85:
            k = rng.randint(2, min(3, len(sec_pool)))
            samples.append(tuple(sorted(rng.sample(sec_pool, k))))
        else:
            samples.append(tuple(sorted(sec_pool)))

    # Reset stato cache + warm opzionale (rende warm-ratio piu' rappresentativo
    # di un long-running daemon vs cold start).
    prompt_loader.invalidate_cache()
    if warm_cache:
        # Touch ogni combinazione del sample una volta per pre-popolare.
        seen: set = set()
        for combo in samples:
            key = combo if combo is not None else ("__none__",)
            if key in seen:
                continue
            seen.add(key)
            try:
                prompt_loader.compose("planner", lang,
                                       sections=list(combo) if combo else None,
                                       **stub_vars)
            except Exception as e:
                print(f"  warm error on {combo}: {e}", file=sys.stderr)
        # Reset counters dopo warm-up: i tempi di seguito non includono warm.
        # NB: invalidate_cache resetterebbe anche la lru → perdiamo il warm
        # data. Resettiamo solo i contatori esterni.
        prompt_loader._compose_cache_counters["hits"] = 0
        prompt_loader._compose_cache_counters["misses"] = 0
        prompt_loader._compose_cache_counters["no_cache"] = 0

    durations_us: list[float] = []
    bytes_total = 0
    n_errors = 0
    for combo in samples:
        t0 = perf_counter()
        try:
            out = prompt_loader.compose("planner", lang,
                                          sections=list(combo) if combo else None,
                                          **stub_vars)
        except Exception as e:
            n_errors += 1
            durations_us.append(0.0)
            print(f"  error compose({combo}): {e}", file=sys.stderr)
            continue
        elapsed_us = (perf_counter() - t0) * 1_000_000
        durations_us.append(elapsed_us)
        bytes_total += len(out)

    valid = [d for d in durations_us if d > 0]
    if not valid:
        print("nessun render riuscito.", file=sys.stderr)
        return 1
    mean_us = statistics.mean(valid)
    median_us = statistics.median(valid)
    # p95 = 95-esimo percentile semplice (nlargest)
    sorted_us = sorted(valid)
    p95_idx = max(0, int(len(sorted_us) * 0.95) - 1)
    p95_us = sorted_us[p95_idx]

    stats = prompt_loader.cache_stats()

    print(f"Bench prompt-build (lang={lang}, runs={runs}, warm={warm_cache})")
    print(f"  errors           : {n_errors}/{runs}")
    print(f"  mean             : {mean_us / 1000:.3f} ms")
    print(f"  p50  (median)    : {median_us / 1000:.3f} ms")
    print(f"  p95              : {p95_us / 1000:.3f} ms")
    print(f"  total bytes      : {bytes_total:,}")
    print(f"  avg bytes/render : {bytes_total // max(1, len(valid)):,}")
    print()
    print(f"Cache stats:")
    print(f"  hits             : {stats['hits']}")
    print(f"  misses           : {stats['misses']}")
    print(f"  no_cache         : {stats['no_cache']}")
    print(f"  size / maxsize   : {stats['size']} / {stats['maxsize']}")
    print(f"  hit ratio        : {stats['hit_ratio'] * 100:.2f}%")

    if compare:
        print()
        print("Compare presets (single render each, fresh cache):")
        # NB: sections=()/None significa "include TUTTE" (degrade graceful).
        # Per "solo core" non c'e' API oggi → l'analogo piu' vicino e' una
        # singola sezione minimale (1 tag) o nessuna. Misuriamo i 3 scenari
        # realistici: mail-only (intent rilevato), multi (2 sezioni), all.
        presets = [
            ("mail_only", ("mail",) if "mail" in sec_pool else ()),
            ("mail_calendar", tuple(s for s in ("mail", "calendar") if s in sec_pool)),
            ("all_or_default", None),
        ]
        for label, combo in presets:
            prompt_loader.invalidate_cache()
            t0 = perf_counter()
            out = prompt_loader.compose("planner", lang,
                                          sections=list(combo) if combo else None,
                                          **stub_vars)
            cold_ms = (perf_counter() - t0) * 1000
            t0 = perf_counter()
            out = prompt_loader.compose("planner", lang,
                                          sections=list(combo) if combo else None,
                                          **stub_vars)
            warm_ms = (perf_counter() - t0) * 1000
            print(f"  {label:15s}: cold={cold_ms:.3f}ms  warm={warm_ms:.3f}ms  "
                  f"size={len(out):,}b")

    return 0


def cmd_lint(args) -> int:
    """Esegue il linter deterministico (`runtime/prompts_lint`) sui prompt
    e stampa gli issue in formato human-readable.

    Exit codes:
      0  → no errori (e niente warn, oppure warn presenti ma --strict assente).
      1  → almeno 1 error trovato.
      2  → solo warn trovati + --strict (treat warn as error).
    """
    sys.path.insert(0, str(PROMPTS_BASE.parent))
    from prompts_lint import scan as _lint_scan  # type: ignore
    from prompts_lint import format_issue as _lint_format  # type: ignore

    if not PROMPTS_BASE.is_dir():
        print(f"prompts dir non esiste: {PROMPTS_BASE}", file=sys.stderr)
        return 1
    langs = [args.lang] if args.lang else None
    issues = _lint_scan(PROMPTS_BASE, langs=langs)
    n_err = sum(1 for i in issues if i.level == "error")
    n_warn = sum(1 for i in issues if i.level == "warn")
    for issue in issues:
        out = _lint_format(issue)
        if issue.level == "error":
            print(out, file=sys.stderr)
        else:
            print(out)
    print()
    print(f"lint: {n_err} error(s), {n_warn} warn(s) "
          f"(strict={'yes' if args.strict else 'no'})")
    if n_err > 0:
        return 1
    if args.strict and n_warn > 0:
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="metnos-prompts", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Tabella prompt + lingue + size + last commit")

    p_show = sub.add_parser("show", help="Render finale del prompt")
    p_show.add_argument("role", help="Nome ruolo (es. 'planner')")
    p_show.add_argument("--lang", default=None,
                        help=f"Lingua prompt (default: {DEFAULT_LANG})")

    sub.add_parser("validate", help="Lint sintassi + invariant check")

    p_tr = sub.add_parser("translate", help="Traduce un ruolo IT → target")
    p_tr.add_argument("role", help="Nome ruolo (es. 'planner')")
    p_tr.add_argument("--to", default="en", help="Lingua target (default: en)")
    p_tr.add_argument("--quality", choices=["wise", "frontier"], default="wise",
                       help="wise=Gemma 4 26B locale (default, gratis); "
                            "frontier=Anthropic Opus 4.7 (~$0.015/call)")
    p_tr.add_argument("--tier", default=None,
                       help="(advanced) Override tier LLM diretto. Sovrascrive --quality.")

    p_tra = sub.add_parser("translate-all", help="Batch traduzione di tutti i .j2 di prompts/it/")
    p_tra.add_argument("--to", default="en", help="Lingua target (default: en)")
    p_tra.add_argument("--quality", choices=["wise", "frontier"], default="wise",
                        help="wise (default, gratis) o frontier (~$0.015/call)")
    p_tra.add_argument("--tier", default=None,
                        help="(advanced) Override tier LLM diretto.")
    p_tra.add_argument("--force", action="store_true",
                        help="Ritraduci anche se gia' presente in target dir")

    sub.add_parser("sync-status",
                    help="Tabella mtime/lag/candidate per ruolo")

    p_rev = sub.add_parser("review", help="Mostra diff + validation di un candidato")
    p_rev.add_argument("role", help="Nome ruolo")
    p_rev.add_argument("--lang", default="en", help="Lingua candidato (default: en)")

    p_mark = sub.add_parser("mark-synced",
                              help="Promuove _pending candidate a runtime")
    p_mark.add_argument("role", help="Nome ruolo")
    p_mark.add_argument("--lang", default="en", help="Lingua (default: en)")
    p_mark.add_argument("--force", action="store_true",
                         help="Promuovi anche con validation FAIL (sconsigliato)")

    sub.add_parser("validate-cross-lang",
                    help="Verifica placeholder + sintassi + len cross-lang")

    p_addl = sub.add_parser("add-language",
                              help="Bootstrap di una nuova lingua "
                                   "(prompts dir + i18n.sqlite placeholder)")
    p_addl.add_argument("code", help="Codice lingua ISO 639-1 (es. fr, de, es)")
    p_addl.add_argument("--source-lang", default="it",
                         help="Lingua sorgente per la traduzione (default: it)")

    p_lint = sub.add_parser("lint",
                              help="Linter deterministico sui prompt .j2 "
                                   "(frontmatter, hedge, LOC, newline, simmetria)")
    p_lint.add_argument("--strict", action="store_true",
                         help="Esce con code !=0 anche se solo warn presenti.")
    p_lint.add_argument("--lang", default=None,
                         help="Lingua specifica (default: tutte). 'all' = tutte.")

    p_audit = sub.add_parser("audit-quality",
                               help="Audit qualita' traduzione wise vs frontier "
                                    "+ raccomandazione tier per il daemon")
    p_audit.add_argument("--to", default="en",
                          help="Lingua target (default: en)")
    p_audit.add_argument("--sample", default="all",
                          help="Numero ruoli (random seed=42) o 'all' "
                               "(default: all)")
    p_audit.add_argument("--apply", action="store_true",
                          help="Salva config in ~/.config/metnos/translator_tier.toml")
    p_audit.add_argument("--report", action="store_true",
                          help="Dump JSON dettagliato per-prompt in cwd")
    p_audit.add_argument("--dry-run", action="store_true",
                          help="Non chiama LLM, simula scores deterministici")
    p_audit.add_argument("--threshold-pct", type=float, default=0.80,
                          help="Soglia %% prompt above-individual (default 0.80)")
    p_audit.add_argument("--individual-pct", type=float, default=0.95,
                          help="Soglia individuale wise vs frontier "
                               "(default 0.95)")

    p_bench = sub.add_parser("bench",
                               help="Bench prompt-build perf (mean/p50/p95) + cache stats")
    p_bench.add_argument("--runs", type=int, default=100,
                          help="Numero render simulati (default 100)")
    p_bench.add_argument("--lang", default=None,
                          help=f"Lingua prompt (default: {DEFAULT_LANG})")
    p_bench.add_argument("--warm", action="store_true",
                          help="Warm cache prima della misura (default: cold mix)")
    p_bench.add_argument("--compare", action="store_true",
                          help="Stampa anche cold/warm per (core_only, mail_only, all)")

    args = parser.parse_args()
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "show":
        return cmd_show(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "translate":
        return cmd_translate(args)
    if args.cmd == "translate-all":
        return cmd_translate_all(args)
    if args.cmd == "sync-status":
        return cmd_sync_status(args)
    if args.cmd == "review":
        return cmd_review(args)
    if args.cmd == "mark-synced":
        return cmd_mark_synced(args)
    if args.cmd == "validate-cross-lang":
        return cmd_validate_cross_lang(args)
    if args.cmd == "add-language":
        return cmd_add_language(args)
    if args.cmd == "audit-quality":
        return cmd_audit_quality(args)
    if args.cmd == "lint":
        return cmd_lint(args)
    if args.cmd == "bench":
        return cmd_bench(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
