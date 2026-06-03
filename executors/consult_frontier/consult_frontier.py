#!/usr/bin/env python3
"""consult_frontier — primitiva Metnos per delegare task a frontier LLM.

Verbo `consult` = system verb riservato (vocab.SYSTEM_VERBS), fuori dai
22 verbi canonici §2.2.

Due modi:
  A) single-call monolitico con local_context inline (file + entries)
  B) tool-use loop con remote_context (frontier esplora repo GitHub,
     issue, file remoti via tool read-only)

Tier auto-bump a `middle` se mode=B e tier='fast' (Haiku non gestisce
bene tool loop). Cache opzionale su disco con TTL configurabile.

Determinismo §7.9 per ogni cosa che non sia la call LLM stessa:
selezione tool, validazione args, parsing output, cache lookup, fallback
chain. L'LLM e' confinato al ragionamento richiesto dall'utente.

Output sempre dict con `ok` bool + telemetria (tokens, cost, tier_used,
provider_used, model_used, cached, iters_done, remote_bytes_read,
files_read, mode, fallback_used). §2.6 non-producer => NON ritorna
`entries`.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))
from messages import get as _msg  # noqa: E402

# ---- Tools whitelist (read-only by hard constraint) ----------------------

# Mappa kind -> tool name(s) di default esposti al frontier in mode B.
# I tool sono SEMPRE read-only. Nessun write/merge/delete e' qui (constraint
# duro di sicurezza, doc github_provider_architecture §5.5).
_KIND_TO_TOOLS: dict[str, tuple[str, ...]] = {
    "github_repo": ("github_read_file", "github_list_dir", "github_search_code"),
    "github_issue": ("github_read_issue",),
    "url": ("fs_read_local_file",),  # url generico: oggi solo fs_read_local_file
}

# Tutti i tool noti, usati per validare `tools_allowed` esplicito.
_ALL_TOOLS = (
    "github_read_file",
    "github_list_dir",
    "github_search_code",
    "github_read_issue",
    "fs_read_local_file",
)


# ---- Cache helpers --------------------------------------------------------

def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(base) / "metnos" / "consult_frontier"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cache_key(args: dict) -> str:
    """sha256 deterministico degli args. Escludiamo `cache_ttl_s` dal
    key (parametro di caching, non di contenuto)."""
    sanitized = {k: v for k, v in args.items() if k != "cache_ttl_s"}
    canon = json.dumps(sanitized, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _cache_get(key: str, ttl_s: int) -> Optional[dict]:
    p = _cache_dir() / f"{key}.json"
    if not p.exists():
        return None
    try:
        age = time.time() - p.stat().st_mtime
    except OSError:
        return None
    if age > ttl_s:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(key: str, payload: dict) -> None:
    p = _cache_dir() / f"{key}.json"
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8",
        )
        tmp.rename(p)
    except OSError:
        # cache best-effort, ignoro errori di scrittura
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# ---- Local context loading ------------------------------------------------

# Path roots consentiti per fs_read_local_file (constraint di sicurezza:
# il frontier non puo' uscire dal sandbox utente).
# ADR 0148 rename-resilient: install root + user home env-driven.
def _allowed_read_roots() -> tuple[Path, ...]:
    """Compute on each call so env overrides + future rename are seen."""
    install_root = Path(os.environ.get("METNOS_INSTALL_ROOT")
                        or os.environ.get("METNOS_HOME")
                        or Path(__file__).resolve().parents[2])
    return (install_root, Path.home(), Path("/tmp"))

_ALLOWED_READ_ROOTS = _allowed_read_roots()


def _path_allowed(p: Path) -> bool:
    """True se `p` resolved e' dentro uno dei root consentiti."""
    try:
        rp = p.resolve()
    except OSError:
        return False
    for root in _ALLOWED_READ_ROOTS:
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _load_local_files(files: list, max_bytes_per_file: int = 100000) -> tuple[str, list[str]]:
    """Carica `files` come testo, troncando a `max_bytes_per_file`.
    Ritorna (concatenated_text, [paths_actually_read]).
    """
    chunks = []
    read = []
    for raw in files or []:
        try:
            p = Path(str(raw)).expanduser()
        except Exception:
            continue
        if not _path_allowed(p):
            chunks.append(f"\n[FILE NON CONSENTITO: {raw}]\n")
            continue
        if not p.exists() or not p.is_file():
            chunks.append(f"\n[FILE NON TROVATO: {p}]\n")
            continue
        try:
            data = p.read_bytes()[:max_bytes_per_file]
            text = data.decode("utf-8", errors="replace")
        except OSError as e:
            chunks.append(f"\n[FILE ILLEGGIBILE: {p}: {e}]\n")
            continue
        chunks.append(f"\n=== FILE: {p} ===\n{text}\n=== END FILE ===\n")
        read.append(str(p))
    return ("".join(chunks), read)


def _serialize_entries(entries: list) -> str:
    if not entries:
        return ""
    try:
        return json.dumps(entries, ensure_ascii=False, default=str, indent=2)
    except Exception:
        return repr(entries)[:50000]


def _format_inline(inline: dict) -> str:
    if not inline:
        return ""
    lines = []
    for k, v in inline.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


# ---- Tool call dispatcher (mode B) ----------------------------------------

def _github_api_path() -> Optional[Path]:
    """Path a github_api.py installato dalla skill Phase C.
    Ritorna None se la skill non e' installata.
    """
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    p = Path(base) / "metnos" / "skills" / "github" / "scripts" / "github_api.py"
    return p if p.exists() else None


def _run_github_subcmd(subcmd: str, extra_argv: list[str], timeout_s: int = 30) -> dict:
    """Invoca github_api.py <subcmd> [args]. Ritorna dict parsato.
    Error_class chiari su missing_skill / non_json / subprocess_fail."""
    script = _github_api_path()
    if script is None:
        return {
            "ok": False, "results": [],
            "error": _msg("ERR_GITHUB_SKILL_MISSING"),
            "error_class": "missing_skill",
        }
    cmd = [sys.executable, str(script), subcmd] + extra_argv
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "results": [], "error": "timeout",
                "error_class": "timeout"}
    except Exception as e:
        return {"ok": False, "results": [], "error": str(e),
                "error_class": "subprocess_fail"}
    if proc.returncode != 0 and not proc.stdout.strip():
        return {"ok": False, "results": [],
                "error": (proc.stderr or "subprocess fail").strip()[:500],
                "error_class": "subprocess_fail"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "results": [],
                "error": f"non-json stdout: {proc.stdout[:200]!r}",
                "error_class": "non_json"}


def _tool_github_read_file(tool_input: dict) -> dict:
    repo = tool_input.get("repo")
    path = tool_input.get("path")
    ref = tool_input.get("ref")
    if not isinstance(repo, str) or "/" not in repo:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="repo", reason="owner/name"),
                "error_class": "invalid_args"}
    if not isinstance(path, str) or not path:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="path"),
                "error_class": "invalid_args"}
    argv = ["--repo", repo, "--path", path]
    if isinstance(ref, str) and ref:
        argv += ["--ref", ref]
    return _run_github_subcmd("repos_read_file", argv)


def _tool_github_list_dir(tool_input: dict) -> dict:
    repo = tool_input.get("repo")
    path = tool_input.get("path", "")
    if not isinstance(repo, str) or "/" not in repo:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="repo", reason="owner/name"),
                "error_class": "invalid_args"}
    return _run_github_subcmd("repos_list_dir", ["--repo", repo, "--path", path])


def _tool_github_search_code(tool_input: dict) -> dict:
    repo = tool_input.get("repo")
    query = tool_input.get("query")
    if not isinstance(repo, str) or "/" not in repo:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="repo", reason="owner/name"),
                "error_class": "invalid_args"}
    if not isinstance(query, str) or not query:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="query"),
                "error_class": "invalid_args"}
    return _run_github_subcmd("code_search", ["--repo", repo, "--query", query])


def _tool_github_read_issue(tool_input: dict) -> dict:
    repo = tool_input.get("repo")
    number = tool_input.get("number")
    if not isinstance(repo, str) or "/" not in repo:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="repo", reason="owner/name"),
                "error_class": "invalid_args"}
    try:
        n = int(number)
    except (TypeError, ValueError):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_INT", arg="number"),
                "error_class": "invalid_args"}
    argv = ["--repo", repo, "--number", str(n)]
    if tool_input.get("include_comments"):
        argv.append("--include-comments")
    return _run_github_subcmd("issues_read", argv)


def _tool_fs_read_local_file(tool_input: dict) -> dict:
    raw = tool_input.get("path")
    if not isinstance(raw, str) or not raw:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="path"),
                "error_class": "invalid_args"}
    p = Path(raw).expanduser()
    if not _path_allowed(p):
        return {"ok": False, "error": _msg("ERR_PATH_OUTSIDE_SCOPE", path=raw),
                "error_class": "forbidden"}
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": _msg("ERR_PATH_NOT_FOUND", path=p),
                "error_class": "not_found"}
    try:
        max_b = int(tool_input.get("max_bytes") or 100000)
    except (TypeError, ValueError):
        max_b = 100000
    try:
        data = p.read_bytes()[:max_b]
        text = data.decode("utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": str(e), "error_class": "io_error"}
    return {"ok": True, "results": [{"path": str(p), "content": text,
                                       "size_bytes": len(data)}]}


_TOOL_DISPATCH = {
    "github_read_file":   _tool_github_read_file,
    "github_list_dir":    _tool_github_list_dir,
    "github_search_code": _tool_github_search_code,
    "github_read_issue":  _tool_github_read_issue,
    "fs_read_local_file": _tool_fs_read_local_file,
}


def _tool_specs(tool_names: list[str]) -> list[dict]:
    """Schema Anthropic per i tool esposti al frontier."""
    specs_map = {
        "github_read_file": {
            "name": "github_read_file",
            "description": "Read a file from a GitHub repo. Read-only.",
            "input_schema": {
                "type": "object",
                "required": ["repo", "path"],
                "properties": {
                    "repo": {"type": "string", "description": "owner/name"},
                    "path": {"type": "string", "description": "file path in repo"},
                    "ref":  {"type": "string", "description": "branch/tag/sha (optional)"},
                },
            },
        },
        "github_list_dir": {
            "name": "github_list_dir",
            "description": "List entries of a directory in a GitHub repo. Read-only.",
            "input_schema": {
                "type": "object",
                "required": ["repo"],
                "properties": {
                    "repo": {"type": "string"},
                    "path": {"type": "string", "default": ""},
                },
            },
        },
        "github_search_code": {
            "name": "github_search_code",
            "description": "Search code in a GitHub repo via the /search/code endpoint. Read-only.",
            "input_schema": {
                "type": "object",
                "required": ["repo", "query"],
                "properties": {
                    "repo":  {"type": "string"},
                    "query": {"type": "string"},
                },
            },
        },
        "github_read_issue": {
            "name": "github_read_issue",
            "description": "Read a GitHub issue (and optionally its comments). Read-only.",
            "input_schema": {
                "type": "object",
                "required": ["repo", "number"],
                "properties": {
                    "repo":   {"type": "string"},
                    "number": {"type": "integer"},
                    "include_comments": {"type": "boolean", "default": False},
                },
            },
        },
        "fs_read_local_file": {
            "name": "fs_read_local_file",
            "description": ("Read a local Metnos-side file under the Metnos "
                             "install root, the user home, or /tmp. Read-only."),
            "input_schema": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "max_bytes": {"type": "integer", "default": 100000},
                },
            },
        },
    }
    return [specs_map[n] for n in tool_names if n in specs_map]


# ---- Prompt building ------------------------------------------------------

_TIME_VARS_CACHE: dict[str, str] | None = None


def _now_vars() -> dict[str, str]:
    """Replica leggera di agent_runtime._render_now_vars per evitare
    import del runtime pesante. Iniettiamo sempre data/ora nei prompt
    (memoria 'Inietta sempre data/ora corrente nei prompt LLM')."""
    global _TIME_VARS_CACHE
    if _TIME_VARS_CACHE is not None:
        return _TIME_VARS_CACHE
    from datetime import datetime
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Europe/Rome")
        now = datetime.now(tz)
        tzname = "Europe/Rome"
    except Exception:
        now = datetime.now()
        tzname = "local"
    wd_it = ("lunedi", "martedi", "mercoledi", "giovedi", "venerdi",
              "sabato", "domenica")[now.weekday()]
    wd_en = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
              "Saturday", "Sunday")[now.weekday()]
    _TIME_VARS_CACHE = {
        "today_iso": now.strftime("%Y-%m-%d"),
        "now_hhmm": now.strftime("%H:%M"),
        "weekday_it": wd_it,
        "weekday_en": wd_en,
        "tz": tzname,
    }
    return _TIME_VARS_CACHE


def _build_system_prompt(role: str, output_spec: dict) -> str:
    nv = _now_vars()
    parts = [
        f"You are acting in the following role: {role}",
        "",
        f"Current date/time: {nv['today_iso']} {nv['now_hhmm']} "
        f"({nv['weekday_en']}, {nv['tz']}).",
        "",
        "OUTPUT SPEC (declarative, follow strictly):",
        json.dumps(output_spec, ensure_ascii=False, indent=2),
    ]
    fmt = (output_spec or {}).get("format")
    if fmt == "json":
        parts.append("")
        parts.append("Return ONLY a JSON object that conforms to the schema. "
                     "No prose around it, no code fences.")
        if isinstance(output_spec.get("schema"), dict):
            parts.append("Schema:")
            parts.append(json.dumps(output_spec["schema"],
                                      ensure_ascii=False, indent=2))
    elif fmt == "code":
        parts.append("")
        parts.append("Return ONLY the code, no prose around it.")
    return "\n".join(parts)


def _build_user_prompt(local_context: dict, remote_context: list) -> str:
    parts = []
    files_text, files_read = _load_local_files(
        (local_context or {}).get("files") or [],
    )
    if files_text.strip():
        parts.append("### LOCAL FILES ###")
        parts.append(files_text)
    entries = (local_context or {}).get("entries") or []
    if entries:
        parts.append("### LOCAL ENTRIES ###")
        parts.append(_serialize_entries(entries))
    inline = (local_context or {}).get("inline") or {}
    if inline:
        parts.append("### INLINE CONTEXT ###")
        parts.append(_format_inline(inline))
    if remote_context:
        parts.append("### REMOTE CONTEXT (explore via tools) ###")
        parts.append(json.dumps(remote_context, ensure_ascii=False, indent=2))
    if not parts:
        parts.append("(No additional context provided. Reason from the role + output_spec only.)")
    return "\n\n".join(parts), files_read


# ---- Validation -----------------------------------------------------------

_VALID_FORMATS = {"markdown", "json", "code", "text"}
_VALID_REMOTE_KINDS = {"github_repo", "github_issue", "url"}


def _validate_args(args: dict) -> Optional[dict]:
    """Ritorna dict di errore se invalido, None altrimenti."""
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args"}
    role = args.get("role")
    if not isinstance(role, str) or not role.strip():
        return {"ok": False, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="role"),
                "error_class": "invalid_args"}
    output_spec = args.get("output_spec")
    if not isinstance(output_spec, dict):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_DICT", arg="output_spec"),
                "error_class": "invalid_args"}
    fmt = output_spec.get("format")
    if fmt is not None and fmt not in _VALID_FORMATS:
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="output_spec.format", reason=str(fmt)),
                "error_class": "invalid_args"}
    rc = args.get("remote_context")
    if rc is not None:
        if not isinstance(rc, list):
            return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="remote_context"),
                    "error_class": "invalid_args"}
        for i, entry in enumerate(rc):
            if not isinstance(entry, dict):
                return {"ok": False,
                        "error": _msg("ERR_ARG_NOT_DICT", arg=f"remote_context[{i}]"),
                        "error_class": "invalid_args"}
            k = entry.get("kind")
            if k not in _VALID_REMOTE_KINDS:
                return {"ok": False,
                        "error": _msg("ERR_ARG_INVALID", arg=f"remote_context[{i}].kind", reason=str(k)),
                        "error_class": "invalid_args"}
    tier = args.get("tier", "wise")
    if tier not in ("fast", "middle", "wise", "frontier"):
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="tier", reason=repr(tier)),
                "error_class": "invalid_args"}
    return None


def _resolve_tools_allowed(args: dict) -> list[str]:
    """Risolve la whitelist di tool. Esplicita > derivata da remote_context.
    Filtra a tool noti."""
    explicit = args.get("tools_allowed")
    if isinstance(explicit, list) and explicit:
        return [t for t in explicit
                if isinstance(t, str) and t in _ALL_TOOLS]
    derived: list[str] = []
    seen: set[str] = set()
    for entry in (args.get("remote_context") or []):
        kind = (entry or {}).get("kind")
        for t in _KIND_TO_TOOLS.get(kind, ()):
            if t not in seen:
                seen.add(t)
                derived.append(t)
    return derived


# ---- Cost calculation (best-effort, public pricing tabelle) ---------------

# USD per 1M tokens (input, output). Fonte: pagine pubbliche pricing dei
# provider, aggiornata al 17/5/2026. Approssimato (mancanze -> 0.0).
_PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-opus-4-7"):    (15.0, 75.0),
    ("anthropic", "claude-sonnet-4-6"):  (3.0,  15.0),
    ("anthropic", "claude-haiku-4-5"):   (0.8,  4.0),
    ("openai",    "gpt-5"):              (10.0, 40.0),
}


def _estimate_cost(provider: str, model: str,
                    in_tokens: int, out_tokens: int) -> float:
    in_p, out_p = _PRICING.get((provider, model), (0.0, 0.0))
    return round(in_tokens * in_p / 1e6 + out_tokens * out_p / 1e6, 6)


# ---- Tier resolution & LLM call -------------------------------------------

def _resolve_tier(args: dict) -> str:
    tier = args.get("tier", "wise")
    if tier == "fast" and args.get("remote_context"):
        return "middle"  # auto-bump §6 / docstring
    return tier


def _try_call(spec: dict, system: str, user: str,
              tools: list[dict] | None, history: list[dict] | None,
              max_tokens: int) -> tuple[Any, dict]:
    """Tenta una call con `spec` (provider+model). Ritorna (result, meta)
    o (None, meta_with_error). `result` e' un `ToolUseResult` o `ChatResult`
    a seconda di tools.
    """
    from llm_provider import make_provider_from_spec, ProviderError
    try:
        prov = make_provider_from_spec(spec)
    except Exception as e:
        return None, {"error": str(e), "error_class": "provider_unavailable",
                       "provider": spec.get("provider"),
                       "model": spec.get("model")}
    try:
        if tools is not None:
            res = prov.chat_with_tools(
                system, user, tools, history=history, max_tokens=max_tokens,
            )
        else:
            res = prov.chat(system, user, max_tokens=max_tokens)
    except ProviderError as e:
        ec = "missing_credentials" if (
            "API_KEY" in str(e) or "mancante" in str(e)
        ) else "provider_error"
        return None, {"error": str(e), "error_class": ec,
                       "provider": spec.get("provider"),
                       "model": spec.get("model")}
    except Exception as e:
        return None, {"error": str(e), "error_class": "provider_error",
                       "provider": spec.get("provider"),
                       "model": spec.get("model")}
    return res, {"provider": spec.get("provider"),
                  "model": spec.get("model"), "error": None}


# ---- Mode A: single-call --------------------------------------------------

def _run_mode_a(args: dict, tier: str, system: str, user: str,
                 files_read: list[str]) -> dict:
    from llm_router import LLMRouter
    router = LLMRouter()
    chain = router.fallback_chain(tier)
    if not chain:
        return {"ok": False,
                "error": _msg("ERR_TIER_NOT_CONFIGURED", tier=tier),
                "error_class": "tier_not_configured",
                "mode": "A", "tier_used": tier, "files_read": files_read,
                "remote_bytes_read": 0, "iters_done": 0, "cached": False}
    max_tokens = int(args.get("max_tokens", 4096))
    last_err = None
    fallback_used = False
    for idx, spec in enumerate(chain):
        res, meta = _try_call(spec, system, user, None, None, max_tokens)
        if res is None:
            last_err = meta
            fallback_used = True
            continue
        return _wrap_chat_result(
            res, meta, mode="A", tier_used=tier, files_read=files_read,
            remote_bytes_read=0, iters_done=0, cached=False,
            fallback_used=(idx > 0),
            structured=_maybe_parse_structured(res.text, args.get("output_spec") or {}),
        )
    return {"ok": False,
            "error": (last_err or {}).get("error", "all providers failed"),
            "error_class": (last_err or {}).get("error_class", "all_failed"),
            "mode": "A", "tier_used": tier, "fallback_used": fallback_used,
            "files_read": files_read, "remote_bytes_read": 0,
            "iters_done": 0, "cached": False}


# ---- Mode B: tool-use loop ------------------------------------------------

def _run_mode_b(args: dict, tier: str, system: str, user: str,
                 files_read: list[str], tools_allowed: list[str]) -> dict:
    from llm_router import LLMRouter
    router = LLMRouter()
    chain = router.fallback_chain(tier)
    if not chain:
        return {"ok": False,
                "error": _msg("ERR_TIER_NOT_CONFIGURED", tier=tier),
                "error_class": "tier_not_configured",
                "mode": "B", "tier_used": tier, "files_read": files_read,
                "remote_bytes_read": 0, "iters_done": 0, "cached": False,
                "tools_allowed": tools_allowed}
    if not tools_allowed:
        return {"ok": False,
                "error": _msg("ERR_NO_TOOLS_RESOLVED"),
                "error_class": "no_tools",
                "mode": "B", "tier_used": tier, "files_read": files_read,
                "remote_bytes_read": 0, "iters_done": 0, "cached": False}
    tools = _tool_specs(tools_allowed)
    max_iters = int(args.get("max_tool_iters", 30))
    max_bytes = int(args.get("max_remote_bytes", 500000))
    max_tokens = int(args.get("max_tokens", 4096))

    # Loop su primary; sui fallback ripartiamo da history vuota (catena spec).
    last_err = None
    for spec_idx, spec in enumerate(chain):
        try:
            return _tool_loop_once(
                spec, system, user, tools, max_iters, max_bytes,
                max_tokens, tools_allowed, files_read, tier,
                fallback_used=(spec_idx > 0),
                output_spec=args.get("output_spec") or {},
            )
        except _ProviderFailed as e:
            last_err = {"error": str(e), "error_class": e.error_class,
                         "provider": spec.get("provider"),
                         "model": spec.get("model")}
            continue
    return {"ok": False,
            "error": (last_err or {}).get("error", "all providers failed"),
            "error_class": (last_err or {}).get("error_class", "all_failed"),
            "mode": "B", "tier_used": tier, "fallback_used": True,
            "files_read": files_read, "remote_bytes_read": 0,
            "iters_done": 0, "cached": False,
            "tools_allowed": tools_allowed}


class _ProviderFailed(Exception):
    def __init__(self, msg, error_class):
        super().__init__(msg)
        self.error_class = error_class


def _tool_loop_once(spec: dict, system: str, user: str,
                     tools: list[dict], max_iters: int, max_bytes: int,
                     max_tokens: int, tools_allowed: list[str],
                     files_read: list[str], tier: str,
                     fallback_used: bool, output_spec: dict) -> dict:
    """Esegue un singolo loop di tool-use con `spec`. Solleva
    _ProviderFailed se la call iniziale non parte (per consentire
    al chiamante di provare un fallback)."""
    history: list[dict] = []
    iters = 0
    remote_bytes = 0
    in_tokens_total = 0
    out_tokens_total = 0
    provider_used = spec.get("provider", "")
    model_used = spec.get("model", "")
    current_user = user

    while iters < max_iters:
        iters += 1
        res, meta = _try_call(
            spec, system, current_user, tools, history, max_tokens,
        )
        if res is None:
            if iters == 1:
                raise _ProviderFailed(meta.get("error", ""),
                                       meta.get("error_class", "provider_error"))
            # Mid-loop fail: chiudi onestamente con partial.
            return {
                "ok": False, "response_text": "",
                "response_structured": None,
                "tokens_in": in_tokens_total, "tokens_out": out_tokens_total,
                "cost_usd": _estimate_cost(provider_used, model_used,
                                             in_tokens_total, out_tokens_total),
                "tier_used": tier, "provider_used": provider_used,
                "model_used": model_used, "cached": False,
                "iters_done": iters, "remote_bytes_read": remote_bytes,
                "files_read": files_read, "mode": "B",
                "fallback_used": fallback_used,
                "tools_allowed": tools_allowed,
                "error": meta.get("error", ""),
                "error_class": meta.get("error_class", "provider_error"),
            }
        in_tokens_total += getattr(res, "in_tokens", 0) or 0
        out_tokens_total += getattr(res, "out_tokens", 0) or 0
        tool_calls = getattr(res, "tool_calls", None) or []
        if not tool_calls:
            # Final answer.
            text = getattr(res, "text", "") or ""
            return _wrap_final(
                ok=True, text=text, output_spec=output_spec,
                tier_used=tier, provider_used=provider_used,
                model_used=model_used, in_tokens=in_tokens_total,
                out_tokens=out_tokens_total, iters=iters,
                remote_bytes=remote_bytes, files_read=files_read,
                tools_allowed=tools_allowed, fallback_used=fallback_used,
                mode="B",
            )
        # Esegui i tool calls richiesti. Aggiorniamo history nel formato
        # Anthropic: assistant turn con i tool_use blocks, poi user turn
        # con i tool_result blocks.
        assistant_blocks = [
            {"type": "text", "text": getattr(res, "text", "") or ""}
        ] if getattr(res, "text", "") else []
        for tc in tool_calls:
            assistant_blocks.append({
                "type": "tool_use",
                "id": tc.call_id, "name": tc.name,
                "input": tc.arguments or {},
            })
        history.append({"role": "assistant", "content": assistant_blocks})

        user_blocks = []
        for tc in tool_calls:
            name = tc.name
            if name not in _TOOL_DISPATCH or name not in tools_allowed:
                user_blocks.append({
                    "type": "tool_result", "tool_use_id": tc.call_id,
                    "content": json.dumps({"ok": False,
                                            "error": _msg("ERR_TOOL_NOT_ALLOWED", name=name),
                                            "error_class": "forbidden"}),
                    "is_error": True,
                })
                continue
            try:
                tool_res = _TOOL_DISPATCH[name](tc.arguments or {})
            except Exception as e:
                tool_res = {"ok": False, "error": str(e),
                             "error_class": "tool_crash"}
            # Budget bytes (campiona sul payload string).
            payload_str = json.dumps(tool_res, ensure_ascii=False,
                                      default=str)
            payload_bytes = len(payload_str.encode("utf-8"))
            if remote_bytes + payload_bytes > max_bytes:
                tool_res = {"ok": False,
                             "error": _msg("ERR_MAX_BYTES_EXCEEDED"),
                             "error_class": "budget_exceeded"}
                payload_str = json.dumps(tool_res, ensure_ascii=False)
            remote_bytes += min(payload_bytes,
                                  max_bytes - remote_bytes + payload_bytes)
            user_blocks.append({
                "type": "tool_result", "tool_use_id": tc.call_id,
                "content": payload_str,
                "is_error": not tool_res.get("ok", True),
            })
        history.append({"role": "user", "content": user_blocks})
        # Lasciamo che la prossima call usi history; il nuovo user vuoto
        # andrebbe a duplicare l'ultimo turn. Usiamo "Continue."
        # come prompt nullo (Anthropic accetta string user content).
        current_user = "Continue."

    # Esaurito budget iters.
    return {
        "ok": False, "response_text": "",
        "response_structured": None,
        "tokens_in": in_tokens_total, "tokens_out": out_tokens_total,
        "cost_usd": _estimate_cost(provider_used, model_used,
                                     in_tokens_total, out_tokens_total),
        "tier_used": tier, "provider_used": provider_used,
        "model_used": model_used, "cached": False,
        "iters_done": iters, "remote_bytes_read": remote_bytes,
        "files_read": files_read, "mode": "B",
        "fallback_used": fallback_used, "tools_allowed": tools_allowed,
        "error": _msg("ERR_MAX_ITERS_REACHED", max_iters=max_iters),
        "error_class": "iters_exceeded",
    }


# ---- Output helpers -------------------------------------------------------

def _maybe_parse_structured(text: str, output_spec: dict) -> dict | None:
    if (output_spec or {}).get("format") != "json":
        return None
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, AttributeError, ValueError):
        return None


def _wrap_chat_result(res, meta, *, mode, tier_used, files_read,
                      remote_bytes_read, iters_done, cached, fallback_used,
                      structured=None) -> dict:
    in_tok = getattr(res, "in_tokens", 0) or 0
    out_tok = getattr(res, "out_tokens", 0) or 0
    provider = meta.get("provider", "")
    model = meta.get("model", "")
    return {
        "ok": True,
        "response_text": getattr(res, "text", "") or "",
        "response_structured": structured,
        "tokens_in": in_tok,
        "tokens_out": out_tok,
        "cost_usd": _estimate_cost(provider, model, in_tok, out_tok),
        "tier_used": tier_used,
        "provider_used": provider,
        "model_used": model,
        "cached": cached,
        "iters_done": iters_done,
        "remote_bytes_read": remote_bytes_read,
        "files_read": files_read,
        "mode": mode,
        "fallback_used": fallback_used,
    }


def _wrap_final(*, ok, text, output_spec, tier_used, provider_used,
                 model_used, in_tokens, out_tokens, iters, remote_bytes,
                 files_read, tools_allowed, fallback_used, mode) -> dict:
    return {
        "ok": ok,
        "response_text": text,
        "response_structured": _maybe_parse_structured(text, output_spec),
        "tokens_in": in_tokens,
        "tokens_out": out_tokens,
        "cost_usd": _estimate_cost(provider_used, model_used,
                                     in_tokens, out_tokens),
        "tier_used": tier_used,
        "provider_used": provider_used,
        "model_used": model_used,
        "cached": False,
        "iters_done": iters,
        "remote_bytes_read": remote_bytes,
        "files_read": files_read,
        "mode": mode,
        "fallback_used": fallback_used,
        "tools_allowed": tools_allowed,
    }


# ---- Public entry ---------------------------------------------------------

def invoke(args: dict) -> dict:
    err = _validate_args(args)
    if err is not None:
        return err

    # Cache lookup.
    ttl = int(args.get("cache_ttl_s") or 0)
    cache_key = None
    if ttl > 0:
        cache_key = _cache_key(args)
        hit = _cache_get(cache_key, ttl)
        if hit is not None:
            hit["cached"] = True
            return hit

    tier = _resolve_tier(args)
    role = args["role"]
    output_spec = args["output_spec"]
    local_context = args.get("local_context") or {}
    remote_context = args.get("remote_context") or []

    system = _build_system_prompt(role, output_spec)
    user_text, files_read = _build_user_prompt(local_context, remote_context)

    if remote_context:
        tools_allowed = _resolve_tools_allowed(args)
        result = _run_mode_b(args, tier, system, user_text,
                               files_read, tools_allowed)
    else:
        result = _run_mode_a(args, tier, system, user_text, files_read)

    if ttl > 0 and result.get("ok") and cache_key:
        _cache_put(cache_key, result)
    return result


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps(
            {"ok": False, "error": _msg("ERR_JSON_INVALID"),
              "error_class": "invalid_args"},
        ))
        return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
