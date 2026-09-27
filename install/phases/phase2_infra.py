# SPDX-License-Identifier: MIT
"""Phase 2 — Infrastructure (embedder + LLM tiers + optional services).

The LLM policy is **tier-based**, not model-based. Metnos routes every call to
``fast`` (``micro``, ``procedural``, or ``fidelity``), ``middle``, ``wise``, ``creative``
or ``frontier``. The concrete model behind each tier is a deployment choice,
recorded in ``runtime/llm_router.py::DEFAULT_TIERS`` and
``DEFAULT_FAST_LEVELS``.

- ``BGE-M3 embedder`` — mandatory, no degraded mode. Downloaded to the
  exact path the runtime reads (``<user_data>/models/embedding-bge``).
  A failure here ABORTS the phase: Metnos cannot function without it.
- The four non-frontier tiers are LOCAL by default. They may share a single
  ``llama-server`` endpoint while retaining independent logical policies.
  Heavy provisioning (hardware
  detection, model choice, llama.cpp + GGUF download, health check) is
  delegated to ``install/llm_manager.py`` — the smart managed path.
- ``frontier`` — opt-in cloud API (Anthropic primary).
- Optional services: VLM, Photon (geocoder), SearXNG (web search).

Existing services must be selected explicitly in ``services.toml``.
An occupied local endpoint is reported before provisioning; it is never
silently adopted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import i18n, llm_manager, services, ui
from ..downloads import Asset, fetch


# ─── Component metadata ──────────────────────────────────────────

# Hugging Face's ``main`` is a moving ref.  Keep every integrity-pinned asset
# on the immutable revision whose LFS object digest is recorded below; otherwise
# a legitimate upstream update is indistinguishable from a corrupt download.
_BGE_M3_REVISION = "46ce10a0ac77ca4bab460d6676ba8260b33e68dc"
_BGE_M3_BASE_URL = (
    f"https://huggingface.co/Xenova/bge-m3/resolve/{_BGE_M3_REVISION}"
)

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
    """Source/code tree, separate from downloaded model data."""
    return Path(os.environ.get("METNOS_INSTALL_ROOT", Path.cwd()))


def _model_dir() -> Path:
    from runtime import config
    return config.PATH_MODELS


def _bge_m3() -> Component:
    # runtime/bge_embedding.py opens:
    #   <models>/embedding-bge/onnx/sentence_transformers_int8.onnx
    #   <models>/embedding-bge/tokenizer.json
    # This official sentence-transformers export provides both token and
    # sentence embeddings.  The runtime consumes the first output and handles
    # either shape explicitly, so the artifact contract remains stable.
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
                url=f"{_BGE_M3_BASE_URL}/onnx/sentence_transformers_int8.onnx",
                dest=d / "onnx" / "sentence_transformers_int8.onnx",
                sha256="e5b42096865cef734247dd8c7a44e35d305b70ba0c3736758065e1cad50f6453",
            ),
            Asset(
                name="BGE-M3 tokenizer.json",
                url=f"{_BGE_M3_BASE_URL}/tokenizer.json",
                dest=d / "tokenizer.json",
                sha256="6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790",
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


def _write_tiers_toml(*, local_endpoint: str, local_model: str | None,
                      frontier: bool) -> None:
    """Bind the local roles to an existing model without a cloud fallback."""
    import tomlkit
    p = _tiers_toml_path()
    if p.exists():
        ui.info(f"{p} already exists — leaving in place. Edit by hand to change tiers.")
        return

    binding = {"provider": "llamacpp", "endpoint": local_endpoint}
    if local_model:
        binding["model"] = local_model
    doc = {tier: dict(binding) for tier in llm_manager.LOCAL_TIER_NAMES}
    doc["frontier"] = {"provider": "anthropic" if frontier else "none"}
    services._write(p, tomlkit.dumps(doc))
    ui.ok(f"wrote {p}")


def _configure_llm_tiers(args: Any) -> dict[str, Any]:
    """Configure the local logical tiers. Returns notes for the phase sentinel."""
    _print_tuning_warning()
    endpoint = os.environ.get("METNOS_LLM_ENDPOINT", llm_manager.DEFAULT_ENDPOINT)

    ui.console().print()
    ui.console().print(
        "  [bold]Local tiers[/bold] · fast / middle / wise / creative")
    ui.console().print(
        "  [dim]One llama-server may serve all three; fast has micro, procedural and fidelity defaults.[/dim]")
    ui.console().print("  [dim]Concrete bindings and defaults: runtime/llm_router.py::DEFAULT_TIERS.[/dim]")

    # Reuse is explicit in services.toml, never inferred from an open port.
    if _endpoint_alive(endpoint):
        raise ValueError(i18n.t("services_local_conflict", endpoint=endpoint))

    # 2. No existing local endpoint: provision the hardware-selected model.
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
        raise ValueError(i18n.t("services_local_llm_failed"))

    res = llm_manager.provision(plan, dry_run=False, assume_yes=True, activate=False)
    if res.get("ok"):
        svc = res.get("service") or {}
        if not svc.get("prepared"):
            raise ValueError(i18n.t("services_local_llm_failed"))
        ui.ok("local LLM prepared — activation and health verification deferred")
        return {"llm_local": "prepared", "llm_endpoint": endpoint,
                "llm_model": plan.model_label,
                "llm_service_healthy": False,
                "llm_service_unit": res["systemd_unit"]}
    raise ValueError(i18n.t("services_local_llm_failed"))


def _configure_frontier(args: Any) -> dict[str, Any]:
    """Cloud is explicit opt-in; declining actually disables the binding."""
    import tomlkit

    enabled = not args.yes and ui.confirm(i18n.t("services_frontier_prompt"), default=False)
    path = _tiers_toml_path()
    doc = tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()
    doc["frontier"] = {"provider": "anthropic" if enabled else "none"}
    services._write(path, tomlkit.dumps(doc))
    return {"frontier_provider": "anthropic" if enabled else "none"}


# ─── Optional components (real, self-hosted sidecars) ─────────────
# The optional list + each installer live in ``install/sidecar.py`` (single
# source of truth, also runnable post-install as `python -m install.sidecar`).
# A ready sidecar installs for real here; one not yet shipped reports honestly
# (§2.8) instead of pretending.


def _offer_optionals(args: Any, profile: dict | None = None) -> dict[str, Any]:
    from .. import sidecar

    profile = services.load() if profile is None else profile
    services.validate_choices(profile, args)
    out: dict[str, Any] = {}
    for key, entry in sidecar.SIDECARS.items():
        if key in profile:
            out[key] = "external"
            continue
        if key in getattr(args, "skip", []):
            out[key] = "skipped"
            continue
        ui.info(i18n.t("services_local_install", name=entry["label"], size=entry["size"]))
        notes = sidecar.install(key, yes=True, activate=False,
                               managed=getattr(args, "managed", False))
        out.update(notes)
        if notes.get(key) != "prepared":
            raise ValueError(i18n.t("services_local_failed", name=key, status=notes.get(key)))
    return out


# ─── Orchestration ───────────────────────────────────────────────

def run(args: Any) -> dict[str, Any]:
    profile = services.load()
    services.validate_choices(profile, args)
    services.validate_units(profile)
    models = services.probe(profile)
    notes: dict[str, Any] = {"services_profile": services.fingerprint(profile)}
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
    if "llm" in profile:
        notes.update(llm_local="external", llm_endpoint=profile["llm"]["url"],
                     llm_model=models["llm"],
                     frontier_provider="anthropic" if profile["llm"].get("frontier", False) else "none")
    else:
        notes.update(_configure_llm_tiers(args))
        notes.update(_configure_frontier(args))
    services.apply(profile, models)

    # 3. Optional components
    notes.update(_offer_optionals(args, profile))

    return notes
