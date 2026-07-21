"""Startup capability profile for LLM request concurrency.

Hardware is only one signal.  The serving framework and its batching model
decide whether multiple in-flight requests are useful.  The resolved profile
is exported once so executors consume a common budget instead of re-detecting
GPU/backend details independently.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping


_CONTINUOUS_BATCHERS = {"vllm", "sglang", "tensorrt_llm", "tensorrt-llm"}


@dataclass(frozen=True)
class LLMConcurrencyProfile:
    framework: str
    gpu_count: int
    batching: str
    max_in_flight: int

    @property
    def parallelism_class(self) -> int:
        if self.max_in_flight <= 1:
            return 0
        if self.max_in_flight <= 2:
            return 1
        if self.max_in_flight <= 8:
            return 2
        return 3


def _bounded_int(value: object, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _configured_framework(env: Mapping[str, str]) -> str:
    explicit = str(env.get("METNOS_LLM_FRAMEWORK") or "").strip().lower()
    if explicit:
        return explicit
    try:
        from llm_router import DEFAULT_TIERS, _tiers_from_config
        tiers = _tiers_from_config() or DEFAULT_TIERS
        spec = tiers.get("middle") or tiers.get("wise") or tiers.get("fast") or {}
        return str(spec.get("provider") or "unknown").strip().lower()
    except Exception:
        return "unknown"


def _detected_gpu_count(env: Mapping[str, str]) -> int:
    explicit = env.get("METNOS_LLM_GPU_COUNT")
    if explicit is not None:
        return _bounded_int(explicit, 0, 0, 64)
    visible = str(env.get("CUDA_VISIBLE_DEVICES") or "").strip()
    if visible:
        if visible in {"-1", "none", "void"}:
            return 0
        return len([part for part in visible.split(",") if part.strip()])
    nvidia_root = Path("/proc/driver/nvidia/gpus")
    try:
        nvidia = sum(1 for item in nvidia_root.iterdir() if item.is_dir())
    except OSError:
        nvidia = 0
    if nvidia:
        return nvidia
    return _drm_gpu_count()


def _drm_gpu_count(root: Path = Path("/sys/class/drm")) -> int:
    """Count exact ``cardN`` devices, excluding ``cardN-*`` connectors."""
    try:
        return sum(
            1 for item in root.glob("card*")
            if item.is_dir() and item.name[4:].isdigit()
        )
    except OSError:
        return 0


def detect_profile(
        env: Mapping[str, str] | None = None, *, framework: str | None = None,
        gpu_count: int | None = None,
) -> LLMConcurrencyProfile:
    values = os.environ if env is None else env
    resolved_framework = (
        str(framework).strip().lower() if framework is not None
        else _configured_framework(values)
    )
    resolved_gpus = (
        max(0, int(gpu_count)) if gpu_count is not None
        else _detected_gpu_count(values)
    )

    if resolved_framework in _CONTINUOUS_BATCHERS:
        batching = "continuous"
        sequences = _bounded_int(
            values.get("METNOS_VLLM_MAX_NUM_SEQS")
            or values.get("VLLM_MAX_NUM_SEQS"),
            max(2, min(8, max(1, resolved_gpus) * 4)), 1, 32)
        max_in_flight = sequences
    elif resolved_framework in {"openai", "anthropic"}:
        batching = "provider_managed"
        max_in_flight = 4
    elif resolved_framework == "llamacpp":
        batching = "static_slots"
        max_in_flight = _bounded_int(
            values.get("METNOS_LLAMACPP_PARALLEL"), 1, 1, 32)
    else:
        batching = "unknown"
        max_in_flight = 1

    explicit_max = values.get("METNOS_LLM_MAX_IN_FLIGHT")
    if explicit_max is not None:
        max_in_flight = _bounded_int(explicit_max, 1, 1, 32)
    explicit_class = values.get("METNOS_LLM_PARALLELISM_CLASS")
    if explicit_class is not None:
        class_value = _bounded_int(explicit_class, 0, 0, 3)
        class_cap = (1, 2, 8, 32)[class_value]
        max_in_flight = min(max_in_flight, class_cap)

    return LLMConcurrencyProfile(
        framework=resolved_framework or "unknown",
        gpu_count=resolved_gpus,
        batching=batching,
        max_in_flight=max_in_flight,
    )


def initialize_environment(
        env: MutableMapping[str, str] | None = None,
) -> LLMConcurrencyProfile:
    target = os.environ if env is None else env
    profile = detect_profile(target)
    target["METNOS_LLM_FRAMEWORK_RESOLVED"] = profile.framework
    target["METNOS_LLM_GPU_COUNT_RESOLVED"] = str(profile.gpu_count)
    target["METNOS_LLM_BATCHING"] = profile.batching
    target["METNOS_LLM_MAX_IN_FLIGHT"] = str(profile.max_in_flight)
    target["METNOS_LLM_PARALLELISM_CLASS"] = str(profile.parallelism_class)
    return profile
