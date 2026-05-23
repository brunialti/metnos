"""E2E §7.11 audit: niente path hardcoded `Path.home()` o `/opt/metnos`
fuori da `runtime/config.py`.

Pattern §7.11: ogni callsite con un path verso install root o
~/.local/share/metnos/ DEVE usare `from runtime import config as C`.

Test deterministico §7.9 + statico (no LLM).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]

# Pattern violation: Path.home() / ".local/share/metnos" o
# Path("/opt/metnos/...") in codice (non comments, non docstrings).
_BAD_PATTERNS = [
    re.compile(r"Path\.home\(\)\s*/\s*['\"]\.local/share/metnos"),
    re.compile(r"Path\.home\(\)\s*/\s*['\"]\.local/state/metnos"),
    re.compile(r"Path\.home\(\)\s*/\s*['\"]\.config/metnos"),
    re.compile(r"Path\(['\"]/opt/metnos/"),
    re.compile(r"Path\(['\"]/opt/myclaw/"),
]

# Exception list: file dove i path canonical sono legittimi (config.py
# stesso, sign keys, ecc.). Niente test files.
_EXCEPTIONS = {
    "runtime/config.py",         # SoT canonical
    "runtime/sign.py",           # keys path
    "runtime/cli/skills_cli.py", # ha fallback con override env
    "runtime/synt.py",           # wrapper template emission: il code generato
                                  # deve scoprire PATH_USER_DATA via env senza
                                  # runtime/config (subprocess sandbox standalone)
}


def _scan_file(p: Path) -> list:
    issues = []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return issues
    lines = text.splitlines()
    for lno, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        # Skip comments + docstrings (heuristic: docstring se la riga inizia
        # con triple quote OR siamo dentro un block triple-quoted)
        if stripped.startswith("#"):
            continue
        # Skip line if it has triple-quoted in this line or block.
        # Heuristic: il count di triple-quote prima della linea pari = fuori
        # docstring.
        preceding = "\n".join(lines[:lno - 1])
        if preceding.count('"""') % 2 == 1:
            continue
        for pat in _BAD_PATTERNS:
            if pat.search(line):
                issues.append(f"{p.relative_to(_REPO_ROOT)}:{lno}: {line.strip()[:120]}")
                break
    return issues


def test_hardcoded_metnos_paths_audit():
    """Audit informativo §7.11: conta path hardcoded in runtime/.
    Watermark baseline 23/5/2026 = 53. Test FAIL solo se peggiora
    (regression guard), passa se uguale/inferiore.

    Per dropping il watermark: fixare moduli uno per uno e abbassare
    il numero (deve scendere progressivamente verso 0).
    """
    all_issues = []
    for f in (_REPO_ROOT / "runtime").rglob("*.py"):
        rel = str(f.relative_to(_REPO_ROOT))
        if rel in _EXCEPTIONS:
            continue
        if "/tests/" in rel:
            continue
        issues = _scan_file(f)
        all_issues.extend(issues)

    # Watermark monotono: ogni fix sistemico §7.11 abbassa progressivamente.
    # Storia:
    #   23/5/2026 12:00  baseline = 53
    #   23/5/2026 13:05  -5  (i18n/proposals_state/scheduler_v2/recurring_tasks/safety/executor_aging)
    #   23/5/2026 13:10  -12 (mail_client/credentials_migrate + skill_admission/credentials/audit
    #                          + github_watch_state/issue_qa + jobs/i18n+sandbox)
    #   23/5/2026 13:20  -17 (prefilter_stats/prefilter/alignment_engine + agent_runtime 3×
    #                          + scheduler_v2/migrate + admin/i18n+manifest_refactor + verb_unique/admin
    #                          + smoke_imports + reverse_patterns_patch + scheduler_v2/storage
    #                          + 3 bench scripts (thinking_budget/prefilter_strategies/latency_breakdown))
    WATERMARK = 0  # §7.11 fully resolved 23/5/2026 13:20
    if len(all_issues) > WATERMARK:
        sample = "\n  ".join(all_issues[:20])
        pytest.fail(
            f"§7.11 regression: {len(all_issues)} hardcoded paths > watermark {WATERMARK}.\n"
            f"  Nuove violazioni rispetto a baseline 23/5/2026:\n  {sample}\n"
            f"  Solution: usa runtime.config.PATH_USER_DATA / PATH_USER_STATE / PATH_USER_CONFIG."
        )
    # Save report (non blocca, informativo)
    report_dir = Path(__file__).resolve().parents[1] / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "path_audit_71.txt").write_text(
        f"Violations §7.11: {len(all_issues)} (watermark {WATERMARK})\n\n"
        + "\n".join(all_issues)
    )
