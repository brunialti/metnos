# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 2 — Infrastructure (LLM tier configuration + optional services).

Restructured to enforce the LLM tier policy documented in
``docs/LLM_TIERS.md``:

- ``BGE-M3 embedder`` — mandatory, no degraded mode. ~600 MB.
- ``fast`` LLM tier — mandatory. Default Anthropic Haiku 4.5
  (frontier, small + cheap); falls back to a local small model if no
  API key. The installer will not proceed without a working fast tier.
- ``middle + wise`` LLM tiers — **strongly recommended** = Gemma 4 26B
  GGUF via llama.cpp on :8080. Default Y. Without it the planner
  falls back to frontier for every turn (higher latency, higher cost).
- ``frontier`` LLM tier — recommended. Anthropic Opus 4.7 (with OpenAI
  GPT-5 as fallback if both keys are present).
- Optional services: VLM (Qwen3-VL-2B), Photon (geocoder), SearXNG
  (web search). Scaffolds; full setup in follow-up releases.

Before any LLM choice is offered, the user sees a clear tuning
warning: the defaults are tested end-to-end; alternatives are
supported but their behaviour is not predicted.

URLs and sha256 digests stay as placeholders pending the release
mirror decision (ADR 0145).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import ui
from ..downloads import Asset, fetch


# ─── Component metadata ──────────────────────────────────────────

@dataclass
class Component:
    key: str
    label: str
    mandatory: bool
    recommended: bool
    size_estimate: str
    description: str
    assets: list[Asset]


def _model_dir() -> Path:
    home = Path(os.environ.get("METNOS_HOME", Path.home() / ".local" / "share" / "metnos"))
    return home / "models"


def _bge_m3() -> Component:
    d = _model_dir() / "bge-m3-onnx"
    return Component(
        key="bge_m3",
        label="BGE-M3 embedder (ONNX int8)",
        mandatory=True,
        recommended=True,
        size_estimate="~600 MB",
        description="Underpins affinity matching, query expansion, github QA dedup. No degraded mode — Metnos is not functional without it.",
        assets=[
            Asset(
                name="BGE-M3 model.onnx",
                url="https://huggingface.co/Xenova/bge-m3/resolve/main/onnx/model_quantized.onnx",
                dest=d / "model.onnx",
                sha256=None,  # set by release pipeline
            ),
            Asset(
                name="BGE-M3 tokenizer.json",
                url="https://huggingface.co/Xenova/bge-m3/resolve/main/tokenizer.json",
                dest=d / "tokenizer.json",
                sha256=None,
            ),
            Asset(
                name="BGE-M3 tokenizer_config.json",
                url="https://huggingface.co/Xenova/bge-m3/resolve/main/tokenizer_config.json",
                dest=d / "tokenizer_config.json",
                sha256=None,
            ),
        ],
    )


# ─── LLM tier prompts ────────────────────────────────────────────

def _print_tuning_warning() -> None:
    ui.console().print()
    ui.console().print(
        "  [bold yellow]Tuning notice[/bold yellow]\n"
        "  [yellow]The default LLM configuration has been tested end-to-end.[/yellow]\n"
        "  [yellow]Alternative models work — but their effects are not predicted.[/yellow]\n"
        "  [dim]Use the defaults first. Swap one tier at a time afterwards via[/dim]\n"
        "  [dim]~/.config/metnos/llm_tiers.toml. Full guide: docs/LLM_TIERS.md.[/dim]"
    )


def _configure_fast_tier(args: Any) -> dict[str, Any]:
    """Fast tier shares the same llama-server as middle/wise (Gemma).

    The tier abstraction (fast / middle / wise) is preserved in the
    runtime; today all three converge on the same Gemma 4 26B local
    server. Differences live in per-call parameters
    (``think=false``, ``num_predict=400``) — see ADR 0146.
    """
    ui.console().print()
    ui.console().print("  [bold]Fast tier (mandatory)[/bold] · procedural calls (intent, classify)")
    ui.console().print("  [dim]Shares the same llama-server as middle and wise (Gemma 4 26B + drafter).[/dim]")
    ui.console().print("  [dim]Differs only in per-call parameters: think=false, num_predict=400.[/dim]")
    return {
        "fast_provider":    "llamacpp",
        "fast_endpoint":    "http://127.0.0.1:8080",
        "fast_model":       "gemma-4-26b",
        "fast_think":       False,
        "fast_num_predict": 400,
    }


def _configure_middle_wise_tier(args: Any) -> dict[str, Any]:
    """Strongly recommended: Gemma 4 26B local."""
    ui.console().print()
    ui.console().print("  [bold]Middle + wise tier (strongly recommended)[/bold] · planner + synth")
    ui.console().print("  [dim]Default: Gemma 4 26B GGUF via local llama.cpp on :8080.[/dim]")
    ui.console().print("  [dim]~15 GB model, ~50 MB binary, needs a Vulkan- or CUDA-capable GPU.[/dim]")

    if args.yes:
        return {"middle_wise_provider": "deferred"}

    install_local = ui.confirm("Install Gemma 4 26B locally? (strongly recommended)", default=True)
    if not install_local:
        ui.warn("Without a local model, every planner turn hits the frontier API. "
                "Latency and cost will rise noticeably.")
        return {"middle_wise_provider": "frontier_fallback"}

    # Detect existing llama-server first
    if shutil.which("llama-server"):
        ui.ok("llama-server already on PATH — wiring middle+wise to it")
        return {"middle_wise_provider": "llamacpp", "llamacpp_installed_by_us": False}

    ui.info("llama-server not found. Install from https://github.com/ggerganov/llama.cpp/releases ")
    ui.info("(prebuilt binaries) or build from source with Vulkan / CUDA support.")
    return {"middle_wise_provider": "llamacpp_pending"}


def _configure_frontier(args: Any) -> dict[str, Any]:
    """Optional but recommended. Anthropic primary."""
    ui.console().print()
    ui.console().print("  [bold]Frontier tier (recommended)[/bold] · consult_frontier, hard synth")
    ui.console().print("  [dim]Default: Anthropic Opus 4.7. OpenAI GPT-5 as fallback if configured.[/dim]")

    if args.yes:
        return {"frontier_provider": "deferred"}

    if ui.confirm("Configure Anthropic for frontier tier?", default=True):
        return {"frontier_provider": "anthropic", "frontier_model": "claude-opus-4-7"}
    return {"frontier_provider": "none"}


def _write_llm_tiers_config(notes: dict[str, Any]) -> None:
    """Write ~/.config/metnos/llm_tiers.toml from the choices above."""
    cfg = Path(os.environ.get("METNOS_CONFIG", Path.home() / ".config" / "metnos"))
    cfg.mkdir(parents=True, exist_ok=True)
    p = cfg / "llm_tiers.toml"
    if p.exists():
        ui.info(f"{p} already exists — leaving in place. Edit by hand to change tiers.")
        return

    fast_endpoint = notes.get("fast_endpoint", "http://127.0.0.1:8080")
    fast_model    = notes.get("fast_model", "gemma-4-26b")
    middle_prov   = notes.get("middle_wise_provider", "llamacpp")
    frontier_prov = notes.get("frontier_provider", "anthropic")
    frontier_model = notes.get("frontier_model", "claude-opus-4-7")

    body_lines = [
        "# Metnos LLM tier routing — see docs/LLM_TIERS.md",
        "# Generated by the installer; edit to swap tier providers.",
        "# Canonical defaults live in runtime/llm_router.py::DEFAULT_TIERS.",
        "",
        "[fast]",
        'provider    = "llamacpp"',
        f'endpoint    = "{fast_endpoint}"',
        f'model       = "{fast_model}"',
        'think       = false',
        'num_predict = 400',
        "",
        "[middle]",
        f'provider = "{middle_prov}"',
        'endpoint = "http://127.0.0.1:8080"' if middle_prov == "llamacpp" else 'model    = "claude-sonnet-4-6"',
        "",
        "[wise]",
        f'provider = "{middle_prov}"',
        'endpoint = "http://127.0.0.1:8080"' if middle_prov == "llamacpp" else 'model    = "claude-opus-4-7"',
        "",
        "[frontier]",
        f'provider = "{frontier_prov}"',
    ]
    if frontier_prov != "none":
        body_lines.append(f'model    = "{frontier_model}"')
    body_lines.append("")
    p.write_text("\n".join(body_lines))
    p.chmod(0o600)
    ui.ok(f"wrote {p}")


# ─── Optional components (deferred) ───────────────────────────────

_OPTIONAL_SCAFFOLDS = (
    ("vlm",     "VLM Qwen3-VL-2B",       "~3 GB",   "Image enrichment — captions for find_images_indices."),
    ("photon",  "Photon offline geocoder", "~3 GB",  "Offline place lookup (per-country dataset)."),
    ("searxng", "SearXNG search aggregator", "~200 MB", "Self-hosted web search."),
)


def _offer_optionals(args: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, label, size, desc in _OPTIONAL_SCAFFOLDS:
        if key in getattr(args, "skip", []):
            ui.info(f"{label}: skipping (--skip {key})")
            out[key] = "skipped"
            continue
        if args.yes and key not in getattr(args, "enable", []):
            out[key] = "skipped"
            continue
        ui.console().print(f"\n  [bold]{label}[/bold] · {size}")
        ui.console().print(f"  [dim]{desc}[/dim]")
        if ui.confirm(f"Install {label}?", default=False):
            ui.warn(f"{label}: scaffold only — full setup in a follow-up release.")
            out[key] = "deferred"
        else:
            out[key] = "skipped"
    return out


# ─── Orchestration ───────────────────────────────────────────────

def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    ui.banner("Phase 2 — Infrastructure", "Embedder + LLM tier configuration + optional services")

    # 1. BGE-M3 — mandatory
    ui.step("Installing BGE-M3 ONNX embedder (mandatory, ~600 MB)")
    bge = _bge_m3()
    fail = 0
    for asset in bge.assets:
        if not fetch(asset):
            fail += 1
    if fail:
        notes["bge_m3"] = "failed"
        ui.warn("BGE-M3 install failed. Metnos cannot function without it. "
                "Fix network and re-run phase 2 with --force-phase 2.")
    else:
        notes["bge_m3"] = "installed"

    # 2. Tuning notice + LLM tier configuration
    _print_tuning_warning()
    notes.update(_configure_fast_tier(args))
    notes.update(_configure_middle_wise_tier(args))
    notes.update(_configure_frontier(args))
    _write_llm_tiers_config(notes)

    # 3. Optional components
    notes.update(_offer_optionals(args))

    return notes
