# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 2 — Infrastructure (embedder + LLM tiers + optional services).

The LLM policy is **tier-based**, not model-based. Metnos routes every
call to one of four tiers — ``fast`` / ``middle`` / ``wise`` /
``frontier`` — and the concrete model behind each tier is a deployment
choice, recorded in one place: ``runtime/llm_router.py::DEFAULT_TIERS``.

- ``BGE-M3 embedder`` — mandatory, no degraded mode. Downloaded to the
  exact path the runtime reads (``<install_root>/models/embedding-bge``).
  A failure here ABORTS the phase: Metnos cannot function without it.
- ``fast`` / ``middle`` / ``wise`` — the three LOCAL tiers. They share a
  single ``llama-server`` endpoint; they differ only in per-call
  parameters (``think``, ``num_predict``). Heavy provisioning (hardware
  detection, model choice, llama.cpp + GGUF download, health check) is
  delegated to ``install/llm_manager.py`` — the smart managed path.
- ``frontier`` — opt-in cloud API (Anthropic primary).
- Optional services: VLM, Photon (geocoder), SearXNG (web search).

The user may point the local tiers at an existing ``llama-server`` via
``METNOS_LLM_ENDPOINT`` (default ``http://127.0.0.1:8080``); if that
endpoint already answers, the installer wires the tiers to it instead of
downloading a model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import llm_manager, ui
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


def _install_root() -> Path:
    """Source/code tree — the runtime reads models relative to this."""
    return Path(os.environ.get("METNOS_INSTALL_ROOT", Path.cwd()))


def _model_dir() -> Path:
    # runtime/config.py: PATH_ROOT/models — keep the embedder where the
    # runtime (bge_embedding.py) actually looks for it.
    return _install_root() / "models"


def _bge_m3() -> Component:
    # runtime/bge_embedding.py opens:
    #   <models>/embedding-bge/onnx/sentence_transformers_int8.onnx
    #   <models>/embedding-bge/tokenizer.json
    # The int8 ONNX outputs token embeddings (3-D); the runtime mean-pools
    # them, so Xenova's quantized export is compatible saved under this name.
    d = _model_dir() / "embedding-bge"
    return Component(
        key="bge_m3",
        label="BGE-M3 embedder (ONNX int8)",
        mandatory=True,
        recommended=True,
        size_estimate="~560 MB",
        description="Underpins affinity matching, query expansion, github QA dedup. No degraded mode — Metnos is not functional without it.",
        assets=[
            Asset(
                name="BGE-M3 sentence_transformers_int8.onnx",
                # ⚠️ release pipeline: pin the mirror + sha256 before go-public.
                url="https://huggingface.co/Xenova/bge-m3/resolve/main/onnx/model_quantized.onnx",
                dest=d / "onnx" / "sentence_transformers_int8.onnx",
                sha256=None,  # set by release pipeline
            ),
            Asset(
                name="BGE-M3 tokenizer.json",
                url="https://huggingface.co/Xenova/bge-m3/resolve/main/tokenizer.json",
                dest=d / "tokenizer.json",
                sha256=None,
            ),
        ],
    )


# ─── LLM tier configuration (tier-based; provisioning via llm_manager) ──

def _print_tuning_warning() -> None:
    ui.console().print()
    ui.console().print(
        "  [bold yellow]Tuning notice[/bold yellow]\n"
        "  [yellow]The default tier configuration has been tested end-to-end.[/yellow]\n"
        "  [yellow]Alternative models work — but their effects are not predicted.[/yellow]\n"
        "  [dim]Use the defaults first. Swap one tier at a time afterwards via[/dim]\n"
        "  [dim]~/.config/metnos/llm_tiers.toml. Canonical defaults: runtime/llm_router.py::DEFAULT_TIERS.[/dim]"
    )


def _endpoint_alive(endpoint: str, *, timeout: float = 2.0) -> bool:
    """True only if a REAL OpenAI-compatible LLM server answers there.

    Requires a 200 on a known LLM path (``/health`` or ``/v1/models``). A
    non-LLM service occupying the port (e.g. a 404 from an unrelated web
    app) must NOT be mistaken for a model server — otherwise the installer
    wires the tiers to it instead of provisioning a real model.
    """
    import httpx  # in venv
    for path in ("/v1/models", "/health"):
        try:
            r = httpx.get(endpoint.rstrip("/") + path, timeout=timeout)
            if r.status_code == 200:
                return True
        except httpx.RequestError:
            continue
    return False


def _tiers_toml_path() -> Path:
    cfg = Path(os.environ.get("METNOS_USER_CONFIG", Path.home() / ".config" / "metnos"))
    cfg.mkdir(parents=True, exist_ok=True)
    return cfg / "llm_tiers.toml"


def _write_tiers_toml(*, local_endpoint: str | None, local_model: str | None,
                      frontier: bool) -> None:
    """Write ~/.config/metnos/llm_tiers.toml.

    ``local_endpoint`` set → fast/middle/wise route to that llama-server.
    ``local_endpoint`` None → all three fall back to frontier (cloud).
    """
    p = _tiers_toml_path()
    if p.exists():
        ui.info(f"{p} already exists — leaving in place. Edit by hand to change tiers.")
        return

    lines = [
        "# Metnos LLM tier routing.",
        "# Canonical defaults live in runtime/llm_router.py::DEFAULT_TIERS.",
        "# Generated by the installer; edit to swap tier providers.",
        "",
    ]
    for tier in ("fast", "middle", "wise"):
        lines.append(f"[{tier}]")
        if local_endpoint:
            lines.append('provider = "llamacpp"')
            lines.append(f'endpoint = "{local_endpoint}"')
            if local_model:
                lines.append(f'model    = "{local_model}"')
        else:
            lines.append('provider = "anthropic"')
            lines.append('model    = "claude-opus-4-7"')
        # per-call params that distinguish the otherwise-identical local tiers
        if tier == "fast":
            lines += ['think       = false', 'num_predict = 400']
        lines.append("")
    lines += [
        "[frontier]",
        'provider = "anthropic"' if frontier else 'provider = "none"',
    ]
    if frontier:
        lines.append('model    = "claude-opus-4-7"')
    lines.append("")
    p.write_text("\n".join(lines))
    p.chmod(0o600)
    ui.ok(f"wrote {p}")


def _configure_llm_tiers(args: Any) -> dict[str, Any]:
    """Configure the four tiers. Returns notes for the phase sentinel."""
    _print_tuning_warning()
    endpoint = os.environ.get("METNOS_LLM_ENDPOINT", llm_manager.DEFAULT_ENDPOINT)

    ui.console().print()
    ui.console().print("  [bold]Local tiers[/bold] · fast / middle / wise")
    ui.console().print("  [dim]One llama-server serves all three; they differ only in per-call[/dim]")
    ui.console().print("  [dim]parameters (think, num_predict). Concrete model per tier: runtime/llm_router.py::DEFAULT_TIERS.[/dim]")

    # 1. Already-running endpoint → wire to it, no download.
    if _endpoint_alive(endpoint):
        ui.ok(f"an LLM endpoint already answers at {endpoint} — wiring the local tiers to it")
        _write_tiers_toml(local_endpoint=endpoint, local_model=None, frontier=True)
        return {"llm_local": "existing_endpoint", "llm_endpoint": endpoint}

    # 2. Non-interactive: do not block on a multi-GB download.
    if args.yes:
        ui.warn("--yes and no local LLM serving: deferring local tiers to the frontier API. "
                "Provision a local model later with `python install/llm_manager.py provision --yes`.")
        _write_tiers_toml(local_endpoint=None, local_model=None, frontier=True)
        return {"llm_local": "deferred_frontier"}

    # 3. Interactive: offer the smart managed install (hardware-aware).
    hw = llm_manager.detect_hardware()
    plan = llm_manager.recommend(hw)
    plan.endpoint = endpoint
    ui.console().print()
    ui.console().print(f"  [bold]Recommended local model[/bold]: {plan.model_label or '(none feasible)'} "
                       f"[dim](backend {plan.backend}, memory budget ~{plan.budget_gb} GB, "
                       f"wise-capable: {plan.wise_ok})[/dim]")
    for w in plan.warnings:
        ui.warn(w)

    if not plan.feasible:
        ui.warn("No local model fits this hardware — the local tiers will use the frontier API.")
        _write_tiers_toml(local_endpoint=None, local_model=None, frontier=True)
        return {"llm_local": "infeasible_frontier"}

    if not ui.confirm("Provision the local model now (recommended)?", default=True):
        ui.warn("Skipping local provisioning — every planner turn will hit the frontier API "
                "(higher latency and cost).")
        _write_tiers_toml(local_endpoint=None, local_model=None, frontier=True)
        return {"llm_local": "declined_frontier"}

    res = llm_manager.provision(plan, dry_run=False, assume_yes=True)
    if res.get("ok"):
        svc = res.get("service") or {}
        if svc.get("healthy"):
            ui.ok("local LLM provisioned — service installed, running and healthy")
        else:
            # Honest outcome (§2.8): artifacts are in place but the server is
            # NOT serving yet — say so instead of claiming "verified".
            ui.warn("local LLM provisioned but the service is not healthy yet: "
                    f"{svc.get('reason') or 'unknown'} — "
                    "retry: `systemctl --user enable --now metnos-llm`.")
        return {"llm_local": "provisioned", "llm_endpoint": endpoint,
                "llm_model": plan.model_label,
                "llm_service_healthy": bool(svc.get("healthy"))}
    ui.warn("local provisioning did not complete — falling back to frontier for now. "
            "Re-run: `python install/llm_manager.py provision --yes`.")
    _write_tiers_toml(local_endpoint=None, local_model=None, frontier=True)
    return {"llm_local": "provision_failed_frontier", "provision": res}


def _configure_frontier(args: Any) -> dict[str, Any]:
    """Frontier tier credential intent (the key itself is collected in phase 4)."""
    ui.console().print()
    ui.console().print("  [bold]Frontier tier[/bold] · hard synthesis, consult_frontier")
    ui.console().print("  [dim]Cloud API (Anthropic Opus by default). The API key is collected"
                       " in the next phase.[/dim]")
    if args.yes:
        return {"frontier_provider": "deferred"}
    if ui.confirm("Use Anthropic for the frontier tier?", default=True):
        return {"frontier_provider": "anthropic", "frontier_model": "claude-opus-4-7"}
    return {"frontier_provider": "none"}


# ─── Optional components (real, self-hosted sidecars) ─────────────
# The optional list + each installer live in ``install/sidecar.py`` (single
# source of truth, also runnable post-install as `python -m install.sidecar`).
# A ready sidecar installs for real here; one not yet shipped reports honestly
# (§2.8) instead of pretending.


def _offer_optionals(args: Any) -> dict[str, Any]:
    from .. import sidecar  # local import: keep phase import light
    out: dict[str, Any] = {}
    for key, entry in sidecar.SIDECARS.items():
        label, size, desc = entry["label"], entry["size"], entry["desc"]
        if key in getattr(args, "skip", []):
            ui.info(f"{label}: skipping (--skip {key})")
            out[key] = "skipped"
            continue
        if args.yes and key not in getattr(args, "enable", []):
            out[key] = "skipped"
            continue
        soon = "" if entry["ready"] else "  [yellow](coming soon)[/yellow]"
        ui.console().print(f"\n  [bold]{label}[/bold] · {size}{soon}")
        ui.console().print(f"  [dim]{desc}[/dim]")
        if not ui.confirm(f"Install {label}?", default=False):
            out[key] = "skipped"
            continue
        if not entry["ready"]:
            ui.warn(f"{label}: real installer not shipped yet — add it later with "
                    f"`python -m install.sidecar {key}`.")
            out[key] = "not_implemented"
            continue
        # Real install. Honest outcome (§2.8): the installer reports running /
        # started_unhealthy / *_failed; we surface it verbatim, never "done".
        notes = sidecar.install(key, yes=args.yes)
        out.update(notes)
    return out


# ─── Orchestration ───────────────────────────────────────────────

def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    ui.banner("Phase 2 — Infrastructure", "Embedder + LLM tier configuration + optional services")

    # 1. BGE-M3 — mandatory. A failure here ABORTS the phase (§2.8: no
    #    silent half-install — the runtime cannot embed without it).
    ui.step("Installing BGE-M3 ONNX embedder (mandatory, ~560 MB)")
    bge = _bge_m3()
    failed = [a.name for a in bge.assets if not fetch(a)]
    if failed:
        ui.fail("BGE-M3 install failed (" + ", ".join(failed) + "). Metnos cannot "
                "function without the embedder. Fix the network and re-run "
                "`python -m install --force-phase 2`.", exit_code=2)
    notes["bge_m3"] = "installed"

    # 2. LLM tiers (tier-based; provisioning delegated to llm_manager)
    notes.update(_configure_llm_tiers(args))
    notes.update(_configure_frontier(args))

    # 3. Optional components
    notes.update(_offer_optionals(args))

    return notes
