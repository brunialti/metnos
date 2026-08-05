from __future__ import annotations

from llm_concurrency import (
    _drm_gpu_count, detect_profile, initialize_environment,
)


def test_drm_fallback_counts_cards_but_not_display_connectors(tmp_path) -> None:
    for name in ("card0", "card1", "card0-HDMI-A-1", "renderD128"):
        (tmp_path / name).mkdir()

    assert _drm_gpu_count(tmp_path) == 2


def test_llamacpp_is_serial_unless_slots_are_explicit() -> None:
    serial = detect_profile({}, framework="llamacpp", gpu_count=1)
    parallel = detect_profile(
        {"METNOS_LLAMACPP_PARALLEL": "3"},
        framework="llamacpp", gpu_count=1)

    assert serial.max_in_flight == 1 and serial.parallelism_class == 0
    assert parallel.max_in_flight == 3 and parallel.parallelism_class == 2


def test_vllm_continuous_batching_can_use_one_gpu_concurrently() -> None:
    profile = detect_profile({}, framework="vllm", gpu_count=1)

    assert profile.batching == "continuous"
    assert profile.max_in_flight == 4
    assert profile.parallelism_class == 2


def test_explicit_limits_and_class_cap_can_force_serial() -> None:
    profile = detect_profile({
        "METNOS_VLLM_MAX_NUM_SEQS": "16",
        "METNOS_LLM_MAX_IN_FLIGHT": "6",
        "METNOS_LLM_PARALLELISM_CLASS": "0",
    }, framework="vllm", gpu_count=2)

    assert profile.max_in_flight == 1


def test_startup_exports_one_common_profile() -> None:
    env = {
        "METNOS_LLM_FRAMEWORK": "vllm",
        "METNOS_LLM_GPU_COUNT": "1",
        "METNOS_VLLM_MAX_NUM_SEQS": "5",
    }

    profile = initialize_environment(env)

    assert profile.max_in_flight == 5
    assert env["METNOS_LLM_PARALLELISM_CLASS"] == "2"
    assert env["METNOS_LLM_MAX_IN_FLIGHT"] == "5"
    assert env["METNOS_LLM_BATCHING"] == "continuous"
