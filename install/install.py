# SPDX-License-Identifier: AGPL-3.0-only
"""Metnos interactive installer (English-only — i18n is NOT applied here).

A friendly, multi-stage, interactive setup for a self-hosted Metnos instance.
It does NOT pretend to be plug-and-play: Metnos needs real hardware (a capable
local LLM) and external services for some skills. This installer is honest about
prerequisites, checks congruence, lets you pick which skills to enable, and
writes the minimal configuration. Heavy lifting (models, services) stays yours.

Stages:
  1. Welcome + framing (showcase project — bring patience).
  2. System congruence checks (OS / Python / RAM / disk / git / venv).
  3. AI backend selection (suprastructure vs bring-your-own llama-server + ONNX).
  4. Skill selection (enable/disable first-party capabilities + prereq probe).
  5. Core config (dirs, admin key, runtime.toml, instance language).
  6. Summary + next steps.

Modes:
  (default)            interactive setup.
  --check              run congruence checks only, write nothing.
  --non-interactive    accept defaults, no prompts (CI / re-provision).

Re-runnable and idempotent: it reads current state and only changes what you
confirm. Run from the repo root:  python3 install/install.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = REPO_ROOT / "runtime"
sys.path.insert(0, str(RUNTIME))

# ── tiny TTY helpers (no deps) ─────────────────────────────────────────────
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s


def hdr(s: str) -> None:
    print("\n" + _c("1;36", f"── {s} " + "─" * max(0, 56 - len(s))))


def ok(s: str) -> None:
    print(f"  {_c('1;32', 'ok')}    {s}")


def warn(s: str) -> None:
    print(f"  {_c('1;33', 'warn')}  {s}")


def bad(s: str) -> None:
    print(f"  {_c('1;31', 'miss')}  {s}")


def info(s: str) -> None:
    print(f"  {_c('2', '·')}     {s}")


class Ctx:
    """Run context: interactivity + write toggle + collected choices."""

    def __init__(self, interactive: bool, do_write: bool):
        self.interactive = interactive
        self.do_write = do_write
        self.warnings = 0
        self.choices: dict = {}

    def ask(self, prompt: str, default: str = "") -> str:
        if not self.interactive:
            return default
        sfx = f" [{default}]" if default else ""
        try:
            ans = input(f"  {_c('1;37', '?')} {prompt}{sfx}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        return ans or default

    def yesno(self, prompt: str, default: bool = True) -> bool:
        d = "Y/n" if default else "y/N"
        ans = self.ask(f"{prompt} ({d})", "Y" if default else "N").lower()
        return ans in ("y", "yes", "si", "s", "true", "1")


# ── stage 1: welcome ───────────────────────────────────────────────────────

def stage_welcome(ctx: Ctx) -> None:
    try:
        from __version__ import __version__ as ver
    except Exception:
        ver = "0.x"
    print(_c("1;36", f"\n  Metnos installer — v{ver}"))
    print("""
  Metnos is a self-hosted personal assistant. This is a SHOWCASE project for
  homelab / AI-architecture enthusiasts, not a polished consumer product.
  Many features (e.g. non-Italian i18n) exist but have barely been tested.
  If something breaks: please open an issue, and bring some patience. Thanks!

  What you really need is hardware: a machine that can run a capable LLM
  locally (the reference instance uses a 96GB unified-memory box). The code
  is the easy part — this installer just checks, configures, and gets out of
  your way.""")


# ── stage 2: congruence checks ─────────────────────────────────────────────

def _read_total_ram_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / (1024 * 1024)
    except Exception:
        return None
    return None


def stage_checks(ctx: Ctx) -> None:
    hdr("System congruence checks")
    # OS
    if sys.platform.startswith("linux"):
        ok(f"OS: {sys.platform}")
    else:
        warn(f"OS: {sys.platform} — Metnos is developed/tested on Linux only.")
        ctx.warnings += 1
    # Python
    py = sys.version_info
    if py >= (3, 11):
        ok(f"Python {py.major}.{py.minor}.{py.micro}")
    else:
        bad(f"Python {py.major}.{py.minor} — Metnos needs >= 3.11.")
        ctx.warnings += 1
    # RAM
    ram = _read_total_ram_gb()
    if ram is None:
        info("RAM: could not read /proc/meminfo")
    elif ram >= 64:
        ok(f"RAM: {ram:.0f} GB")
    elif ram >= 32:
        warn(f"RAM: {ram:.0f} GB — works, but a big local LLM wants more (64-96 GB).")
        ctx.warnings += 1
    else:
        warn(f"RAM: {ram:.0f} GB — too little for a capable local LLM; "
             "use a remote llama-server endpoint (asked below).")
        ctx.warnings += 1
    # Disk
    try:
        free_gb = shutil.disk_usage(REPO_ROOT).free / (1024 ** 3)
        (ok if free_gb >= 20 else warn)(f"Disk free at repo: {free_gb:.0f} GB")
        if free_gb < 20:
            ctx.warnings += 1
    except Exception:
        info("Disk: could not measure free space")
    # git
    (ok if shutil.which("git") else warn)(
        f"git: {'found' if shutil.which('git') else 'missing (recommended)'}")
    # venv / python deps marker
    venv = os.environ.get("VIRTUAL_ENV") or (
        "/opt/suprastructure/.venv" if Path("/opt/suprastructure/.venv").exists()
        else "")
    if venv:
        ok(f"Python env: {venv}")
    else:
        warn("No virtualenv detected — create one and install requirements.")
        ctx.warnings += 1
    # import smoke: can we import config?
    try:
        import config  # noqa: F401
        ok("runtime import: config loads")
    except Exception as e:
        bad(f"runtime import failed: {e}")
        ctx.warnings += 1


# ── stage 3: AI backend ────────────────────────────────────────────────────

def _http_reachable(url: str, timeout: float = 3.0) -> bool:
    """True if an HTTP server answers (any status < 500). A 4xx still means
    'something is listening'. Transport errors → False (caller may TCP-probe)."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def stage_ai_backend(ctx: Ctx) -> None:
    hdr("AI backend (LLM + embeddings)")
    supra = False
    try:
        import suprastructure  # noqa: F401
        supra = True
    except Exception:
        supra = False
    if supra:
        info("suprastructure detected (the reference AI hub).")
    print("""  Metnos needs (a) a chat LLM via an OpenAI-compatible llama-server and
  (b) a text-embedding model. Pick how to provide them:""")
    print(f"    1) bring-your-own llama-server + local ONNX embeddings  "
          f"{_c('2','(public default)')}")
    print(f"    2) suprastructure backend  "
          f"{_c('2','(reference instance; only if installed)' if supra else '(not available here)')}")
    default = "2" if supra else "1"
    sel = ctx.ask("AI backend", default)
    if sel == "2" and supra:
        ctx.choices["ai_backend"] = "suprastructure"
        ok("AI backend: suprastructure")
    else:
        ctx.choices["ai_backend"] = "local"
        endpoint = ctx.ask("llama-server endpoint", "http://127.0.0.1:8080")
        ctx.choices["llm_endpoint"] = endpoint
        # Probe: /v1/models then /health then raw TCP.
        reachable = _http_reachable(endpoint.rstrip("/") + "/v1/models") \
            or _http_reachable(endpoint.rstrip("/") + "/health")
        if not reachable:
            reachable = _tcp_probe(endpoint)
        if reachable:
            ok(f"llama-server reachable at {endpoint}")
        else:
            warn(f"No server answered at {endpoint} — start one before using "
                 "chat. (You can configure the endpoint later in runtime.toml.)")
            ctx.warnings += 1
        # Embeddings probe
        try:
            import bge_embedding  # noqa: F401
            ok("local embeddings: bge_embedding importable")
        except Exception:
            warn("local embeddings (bge_embedding) not importable — the 'web' "
                 "deep-search path will degrade gracefully until provided.")
            ctx.warnings += 1


def _tcp_probe(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=3):
            return True
    except Exception:
        return False


# ── stage 4: skills ────────────────────────────────────────────────────────

def _probe_skill(name: str, ctx: Ctx) -> tuple[bool, str]:
    """Light prerequisite probe → (configured?, note). Never fatal."""
    if name == "web":
        url = ctx.ask("  SearXNG base URL (blank = configure later)", "")
        if url and (_http_reachable(url) or _tcp_probe(url)):
            return True, f"SearXNG at {url}"
        return False, "SearXNG not set — skill stays dormant until configured"
    if name == "geo":
        url = ctx.ask("  Photon/Nominatim base URL (blank = later)", "")
        if url and (_http_reachable(url) or _tcp_probe(url)):
            return True, f"geocoder at {url}"
        return False, "geocoder not set — dormant until configured"
    if name == "github":
        has = bool(os.environ.get("GITHUB_TOKEN") or
                   (Path.home() / ".config/metnos/github_watched_repos.json").exists())
        return has, ("PAT/config present" if has else
                     "needs a GitHub PAT — dormant until configured")
    if name == "mail":
        return False, "needs IMAP/SMTP accounts (metnos-cli credentials) — dormant until set"
    if name == "photos":
        try:
            import clip_embedding  # noqa: F401
            return True, "image models importable (still needs an index)"
        except Exception:
            return False, "needs image models + a photo index — dormant until built"
    if name == "frontier":
        has = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        return has, ("frontier API key present" if has else
                     "opt-in: needs a frontier API key — dormant until set")
    if name == "calendar":
        return True, "local calendar works; Google/CalDAV optional"
    return True, ""


def stage_skills(ctx: Ctx) -> None:
    hdr("Skills (modular capabilities)")
    try:
        from skills_catalog import FIRST_PARTY_SKILLS
    except Exception as e:
        warn(f"skills_catalog not importable ({e}); skipping skill selection.")
        return
    print("""  Each skill is a group of capabilities tied to an external backend or
  credential. Enable the ones you want; a skill you enable but don't configure
  stays DORMANT (visible, inert) until its prerequisite is met. 'core'
  (local files, processes, time, scheduler, helpers) is always on.""")
    decisions: dict[str, bool] = {}
    for sk in FIRST_PARTY_SKILLS:
        name = sk["name"]
        print(f"\n  {_c('1;37', name)} — {sk.get('desc','')}")
        info(f"requires: {sk.get('requires','')}")
        enable = ctx.yesno(f"enable '{name}'?", default=True)
        decisions[name] = enable
        if enable:
            configured, note = _probe_skill(name, ctx)
            (ok if configured else warn)(note or "enabled")
            if not configured and note:
                ctx.warnings += 1
        else:
            info("disabled")
    ctx.choices["skills"] = decisions


# ── stage 5: core config ───────────────────────────────────────────────────

def stage_config(ctx: Ctx) -> None:
    hdr("Core configuration")
    # Language
    lang = ctx.ask("instance language (it = best tested, en = experimental)",
                   "en").lower()[:2] or "en"
    ctx.choices["lang"] = lang
    if lang != "it":
        warn(f"language '{lang}': i18n beyond Italian is largely UNTESTED.")
        ctx.warnings += 1
    else:
        ok("language: it")
    if not ctx.do_write:
        info("(--check) skipping writes (dirs / admin key / runtime.toml / skills)")
        return
    # Dirs
    try:
        import config as C
        if hasattr(C, "ensure_dirs"):
            C.ensure_dirs()
        ok(f"data/state/config dirs ready under {C.PATH_USER_DATA.parent}")
    except Exception as e:
        warn(f"could not ensure dirs: {e}")
    # Admin key
    try:
        from http_auth import get_or_create_admin_key, ADMIN_KEY_PATH
        get_or_create_admin_key()
        ok(f"admin key: {ADMIN_KEY_PATH} (mode 0600)")
    except Exception as e:
        warn(f"admin key not created: {e}")
    # runtime.toml (ai backend + language)
    try:
        import config as C
        toml_path = C.PATH_USER_CONFIG / "runtime.toml"
        _merge_runtime_toml(toml_path, ctx)
        ok(f"runtime.toml written: {toml_path}")
    except Exception as e:
        warn(f"runtime.toml not written: {e}")
    # Persist skill enable/disable
    try:
        from skill_registry import set_skill_enabled
        for name, enabled in (ctx.choices.get("skills") or {}).items():
            set_skill_enabled(name, enabled)
        if ctx.choices.get("skills"):
            ok("skill state saved (skill_enabled.json)")
    except Exception as e:
        warn(f"skill state not saved: {e}")


def _merge_runtime_toml(path: Path, ctx: Ctx) -> None:
    """Write/patch a tiny [ai_backend] + [instance] block (no toml dep)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = ["", "[ai_backend]",
             f'backend = "{ctx.choices.get("ai_backend", "local")}"']
    if ctx.choices.get("llm_endpoint"):
        block.append(f'llm_endpoint = "{ctx.choices["llm_endpoint"]}"')
    block += ["", "[instance]", f'lang = "{ctx.choices.get("lang", "en")}"', ""]
    marker = "# --- written by install/install.py ---"
    new = existing
    if marker in existing:
        new = existing.split(marker)[0].rstrip() + "\n"
    new = new.rstrip() + "\n\n" + marker + "\n" + "\n".join(block)
    path.write_text(new, encoding="utf-8")


# ── stage 6: summary ───────────────────────────────────────────────────────

def stage_summary(ctx: Ctx) -> None:
    hdr("Summary & next steps")
    ab = ctx.choices.get("ai_backend", "?")
    skills = ctx.choices.get("skills") or {}
    on = [k for k, v in skills.items() if v]
    print(f"  AI backend : {ab}"
          + (f"  ({ctx.choices.get('llm_endpoint')})" if ctx.choices.get("llm_endpoint") else ""))
    print(f"  Language   : {ctx.choices.get('lang','en')}")
    print(f"  Skills on  : {', '.join(on) if on else '(core only)'}")
    if ctx.warnings:
        print(_c("1;33", f"\n  {ctx.warnings} warning(s) above — Metnos will run, "
                          "but some skills stay dormant until configured."))
    print(f"""
  Next:
    1) Start the LLM (llama-server) and any backends for the skills you enabled.
    2) Launch the HTTP server:
         {_c('1;37', 'python3 runtime/metnos_http_server.py --host 0.0.0.0 --port 8770')}
       (or install the provided systemd unit; see README).
    3) Check health:   {_c('1;37', 'curl http://127.0.0.1:8770/agent/health')}
    4) Manage skills any time:  {_c('1;37', 'python3 runtime/cli/skills_cli.py list')}
       …or just ask in chat: "which skills do I have?", "enable photos".

  Showcase project — feedback and patience welcome. See README for the rest.""")


# ── main ───────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Metnos interactive installer")
    ap.add_argument("--check", action="store_true",
                    help="run congruence checks only, write nothing")
    ap.add_argument("--non-interactive", action="store_true",
                    help="accept defaults, no prompts")
    args = ap.parse_args(argv)
    ctx = Ctx(interactive=not args.non_interactive, do_write=not args.check)

    stage_welcome(ctx)
    stage_checks(ctx)
    if args.check:
        hdr("Check-only mode")
        print(f"  {ctx.warnings} warning(s). Nothing written. "
              "Re-run without --check to configure.")
        return 1 if ctx.warnings else 0
    stage_ai_backend(ctx)
    stage_skills(ctx)
    stage_config(ctx)
    stage_summary(ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
