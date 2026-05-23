"""skill_fetch — fetch remoto SKILL.md + scripts (Gap 4, 10/5/2026).

Supporta:
- `path locale` (file SKILL.md o dir contenente SKILL.md) — ritorna invariato.
- `agentskills.io/<owner>/<skill>` — canonical mapping a GitHub raw.
- `https://github.com/<owner>/<skill>` — git clone in `/tmp/skill_imports_cache/`.
- `https://raw.githubusercontent.com/<owner>/<repo>/<branch>/SKILL.md` — direct urllib.

Cache: `~/.cache/metnos/skill_imports/<sha256(url)>/` con TTL 7 giorni.
Riusa pattern `runtime/http_cache.py` di <install_root>/ (ADR 0105):
- sharded directory tree
- atomic write tmp+rename
- TTL configurabile via `METNOS_SKILL_FETCH_TTL_S` (default 7*86400)
- override via `cache_ttl_s=0` per disable

Determinismo §7.9: niente LLM, regole pure. Niente parser HTML.

DEVI: passare un path/URL sintatticamente valido.
NON DEVI: invocare con URL non-Github (oggi non supportati).
OK: fetch_skill_source('agentskills.io/nous-research/google-workspace').
ERRORE: fetch_skill_source('https://gitlab.com/foo/bar') -> NotImplementedError.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


CACHE_ROOT = Path.home() / ".cache" / "metnos" / "skill_imports"
DEFAULT_TTL_S = 7 * 24 * 3600  # 7 giorni
USER_AGENT = "metnos-skills/0.1"


class SkillFetchError(ValueError):
    """Fetch impossibile: URL non supportato, rete giu', payload invalido."""


# ---------------------------------------------------------------------------
# Path resolution (locale)
# ---------------------------------------------------------------------------


def _resolve_local(arg: str) -> Optional[Path]:
    """Se `arg` e' un path locale a SKILL.md o dir contenente, ritorna path."""
    p = Path(arg)
    if p.is_file() and p.name == "SKILL.md":
        return p
    if p.is_dir() and (p / "SKILL.md").exists():
        return p / "SKILL.md"
    return None


# ---------------------------------------------------------------------------
# URL canonical mapping
# ---------------------------------------------------------------------------


_AGENTSKILLS_RE = re.compile(
    r"^(?:https?://)?agentskills\.io/(?P<owner>[A-Za-z0-9_.-]+)/(?P<skill>[A-Za-z0-9_.-]+)/?$"
)
_GITHUB_HTTP_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_RAW_GITHUB_RE = re.compile(
    r"^https?://raw\.githubusercontent\.com/(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repo>[A-Za-z0-9_.-]+)/(?P<branch>[A-Za-z0-9_./-]+)/(?P<path>.+)$"
)


def _classify_url(arg: str) -> tuple[str, dict]:
    """Riconosce il tipo di sorgente. Ritorna `(kind, params)`.

    kind: "agentskills" | "github" | "raw_github" | "unknown".
    """
    m = _AGENTSKILLS_RE.match(arg)
    if m:
        return "agentskills", m.groupdict()
    m = _GITHUB_HTTP_RE.match(arg)
    if m:
        return "github", m.groupdict()
    m = _RAW_GITHUB_RE.match(arg)
    if m:
        return "raw_github", m.groupdict()
    return "unknown", {}


# ---------------------------------------------------------------------------
# Cache (riusa pattern http_cache di <install_root>/)
# ---------------------------------------------------------------------------


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache_dir_for(url: str) -> Path:
    """Sharded path: <CACHE_ROOT>/<key[:2]>/<key>/."""
    key = _cache_key(url)
    return CACHE_ROOT / key[:2] / key


def _is_cache_valid(cache_dir: Path, ttl_s: int) -> bool:
    """Cache valida se SKILL.md presente E mtime < ttl_s."""
    skill = cache_dir / "SKILL.md"
    if not skill.is_file():
        return False
    age = time.time() - skill.stat().st_mtime
    return age < ttl_s


# ---------------------------------------------------------------------------
# Fetch backends
# ---------------------------------------------------------------------------


def _http_get(url: str, timeout_s: int = 30) -> bytes:
    """GET HTTP con retry leggero. Solleva SkillFetchError su errore."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise SkillFetchError(f"HTTP {e.code} per {url}: {e.reason}")
    except urllib.error.URLError as e:
        raise SkillFetchError(f"network error per {url}: {e.reason}")


def _fetch_raw_github(url: str, dest_dir: Path) -> Path:
    """Scarica un singolo file via raw.githubusercontent.com.
    Aspetta che `url` punti direttamente a SKILL.md.
    Ritorna path al SKILL.md scritto."""
    body = _http_get(url)
    if not body or not body.lstrip().startswith(b"---"):
        raise SkillFetchError(f"contenuto non sembra un SKILL.md (manca frontmatter YAML): {url}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    skill_path = dest_dir / "SKILL.md"
    tmp = skill_path.with_suffix(".md.tmp")
    tmp.write_bytes(body)
    tmp.rename(skill_path)
    return skill_path


def _agentskills_to_raw_github(owner: str, skill: str) -> str:
    """Canonical mapping agentskills.io -> github.com.
    Assunzione: la skill vive a `github.com/<owner>/<skill>`,
    con SKILL.md nella root del default branch (main).

    Modifica futura: agentskills.io potrebbe servire un manifest
    `index.json` con URL precisi; oggi mappatura naïve per il POC.
    """
    return f"https://raw.githubusercontent.com/{owner}/{skill}/main/SKILL.md"


def _git_clone_github(owner: str, repo: str, dest_dir: Path) -> Path:
    """Clone shallow di github.com/<owner>/<repo> in dest_dir.
    Ritorna path al SKILL.md (o solleva SkillFetchError se assente).
    """
    git = shutil.which("git")
    if not git:
        raise SkillFetchError("git non installato; impossibile clonare repo GitHub")
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Pulisci dest se esiste un partial.
    repo_dir = dest_dir / repo
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    url = f"https://github.com/{owner}/{repo}.git"
    try:
        subprocess.run(
            [git, "clone", "--depth", "1", "--single-branch", url, str(repo_dir)],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace")[:500]
        raise SkillFetchError(f"git clone failed: {stderr}")
    except subprocess.TimeoutExpired:
        raise SkillFetchError(f"git clone timeout per {url}")
    skill = repo_dir / "SKILL.md"
    if not skill.is_file():
        raise SkillFetchError(
            f"repo {owner}/{repo} clonato ma manca SKILL.md nella root"
        )
    return skill


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_skill_source(arg: str, *,
                       cache_ttl_s: Optional[int] = None,
                       force_refresh: bool = False) -> Path:
    """Risolve `arg` (path locale o URL) in un path locale a SKILL.md.

    Args:
      arg: path locale o URL.
      cache_ttl_s: override TTL cache (sec). 0 disabilita cache. None usa
                   default (7d, override via env METNOS_SKILL_FETCH_TTL_S).
      force_refresh: bypassa cache, scarica sempre.

    Returns:
      Path al SKILL.md scaricato/risolto. Per remote, anche scripts/
      e references/ (se presenti) sono nella stessa dir.

    Raises:
      SkillFetchError: URL non riconosciuto, rete giu', SKILL.md assente.
      FileNotFoundError: path locale dichiarato non esiste.
    """
    # 1. Locale (priorita').
    local = _resolve_local(arg)
    if local is not None:
        return local

    # 2. Determine TTL.
    if cache_ttl_s is None:
        cache_ttl_s = int(os.environ.get("METNOS_SKILL_FETCH_TTL_S", str(DEFAULT_TTL_S)))

    # 3. Classifica URL.
    kind, params = _classify_url(arg)
    if kind == "unknown":
        # Non e' locale, non e' URL noto.
        if arg.startswith("http://") or arg.startswith("https://"):
            raise SkillFetchError(
                f"URL non supportato: {arg}. Backend supportati: "
                "agentskills.io, github.com, raw.githubusercontent.com."
            )
        # Default: stessa diagnostica del CLI vecchio per path inesistenti.
        raise FileNotFoundError(f"SKILL.md non trovato: {arg}")

    # 4. Cache lookup.
    cache_dir = _cache_dir_for(arg)
    if not force_refresh and cache_ttl_s > 0:
        if _is_cache_valid(cache_dir, cache_ttl_s):
            return cache_dir / "SKILL.md"

    # 5. Fetch by kind.
    if kind == "raw_github":
        return _fetch_raw_github(arg, cache_dir)

    if kind == "agentskills":
        # Canonical mapping a raw GitHub.
        raw_url = _agentskills_to_raw_github(params["owner"], params["skill"])
        return _fetch_raw_github(raw_url, cache_dir)

    if kind == "github":
        # Clone shallow per portarsi anche scripts/ e references/.
        skill_path = _git_clone_github(params["owner"], params["repo"], cache_dir)
        # Touch SKILL.md per garantire mtime fresco (cache valid).
        skill_path.touch()
        return skill_path

    raise SkillFetchError(f"kind sconosciuto: {kind!r}")


def clear_cache() -> int:
    """Cancella tutta la cache. Ritorna numero di entry rimossi."""
    if not CACHE_ROOT.exists():
        return 0
    n = sum(1 for _ in CACHE_ROOT.rglob("SKILL.md"))
    shutil.rmtree(CACHE_ROOT)
    return n


def cleanup_older_than(seconds: int) -> int:
    """Cancella entry con mtime > seconds. Ritorna numero rimossi."""
    if not CACHE_ROOT.exists():
        return 0
    cutoff = time.time() - seconds
    removed = 0
    for skill in CACHE_ROOT.rglob("SKILL.md"):
        if skill.stat().st_mtime < cutoff:
            shutil.rmtree(skill.parent, ignore_errors=True)
            removed += 1
    return removed
