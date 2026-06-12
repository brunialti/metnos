"""Installer: asset-picker llama.cpp robusto al runtime ROCm incompleto.

Flag E2E installer #3 (12/6/2026): `_pick_llama_asset` sceglieva l'asset
`rocm/hip` se esisteva `rocminfo`, ma senza `librocblas.so` il binario HIP
ricade su CPU IN SILENZIO (la produzione usa Vulkan). Fix: con runtime
ROCm incompleto si preferisce l'asset Vulkan; nessuna regressione sui
path cpu/cuda/vulkan. Test hermetici: mock, nessuna rete.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from install import llm_manager  # noqa: E402


def _a(name: str) -> dict:
    return {"name": name, "browser_download_url": f"https://x/{name}", "size": 0}


ASSETS_FULL = [
    _a("llama-b6100-bin-ubuntu-x64-cpu.tar.gz"),
    _a("llama-b6100-bin-ubuntu-x64-cuda.tar.gz"),
    _a("llama-b6100-bin-ubuntu-x64-hip.tar.gz"),
    _a("llama-b6100-bin-ubuntu-x64-vulkan.tar.gz"),
    _a("llama-b6100-bin-ubuntu-x64-openvino.tar.gz"),
]
ASSETS_NO_VULKAN = [
    _a("llama-b6100-bin-ubuntu-x64-cpu.tar.gz"),
    _a("llama-b6100-bin-ubuntu-x64-hip.tar.gz"),
    _a("llama-b6100-bin-ubuntu-x64-openvino.tar.gz"),
]


class TestPickLlamaAssetRocm(unittest.TestCase):
    """rocm completo → hip/rocm; rocm incompleto → vulkan (mai CPU muta)."""

    def test_rocm_complete_picks_hip_asset(self):
        with mock.patch.object(llm_manager, "_rocm_runtime_complete",
                               return_value=True):
            got = llm_manager._pick_llama_asset(ASSETS_FULL, "rocm")
        self.assertIn("hip", got["name"])

    def test_rocm_incomplete_prefers_vulkan(self):
        with mock.patch.object(llm_manager, "_rocm_runtime_complete",
                               return_value=False):
            got = llm_manager._pick_llama_asset(ASSETS_FULL, "rocm")
        self.assertIn("vulkan", got["name"])

    def test_rocm_incomplete_without_vulkan_falls_back_to_plain_cpu(self):
        """Niente vulkan in release: fallback al build PLAIN (mai una
        variante specializzata tipo openvino che non parte)."""
        with mock.patch.object(llm_manager, "_rocm_runtime_complete",
                               return_value=False):
            got = llm_manager._pick_llama_asset(ASSETS_NO_VULKAN, "rocm")
        self.assertIn("cpu", got["name"])
        self.assertNotIn("openvino", got["name"])


class TestPickLlamaAssetNoRegressions(unittest.TestCase):
    """cpu/cuda/vulkan invariati; il probe ROCm non viene nemmeno chiamato."""

    def test_cpu_backend_untouched(self):
        with mock.patch.object(llm_manager, "_rocm_runtime_complete") as probe:
            got = llm_manager._pick_llama_asset(ASSETS_FULL, "cpu")
        probe.assert_not_called()
        self.assertIn("cpu", got["name"])

    def test_cuda_backend_untouched(self):
        with mock.patch.object(llm_manager, "_rocm_runtime_complete") as probe:
            got = llm_manager._pick_llama_asset(ASSETS_FULL, "cuda")
        probe.assert_not_called()
        self.assertIn("cuda", got["name"])

    def test_vulkan_backend_untouched(self):
        got = llm_manager._pick_llama_asset(ASSETS_FULL, "vulkan")
        self.assertIn("vulkan", got["name"])


class TestRocmRuntimeProbe(unittest.TestCase):
    """_rocm_runtime_complete: ldconfig prima, glob dei lib-dir poi."""

    def test_ldconfig_hit_is_complete(self):
        with mock.patch("ctypes.util.find_library",
                        return_value="librocblas.so.4"):
            self.assertTrue(llm_manager._rocm_runtime_complete())

    def test_nothing_found_is_incomplete(self):
        with mock.patch("ctypes.util.find_library", return_value=None), \
             mock.patch("glob.glob", return_value=[]):
            self.assertFalse(llm_manager._rocm_runtime_complete())

    def test_rocm_tree_glob_hit_is_complete(self):
        def fake_glob(pat):
            return (["/opt/rocm-6.2/lib/librocblas.so.4"]
                    if pat.startswith("/opt/rocm") else [])

        with mock.patch("ctypes.util.find_library", return_value=None), \
             mock.patch("glob.glob", side_effect=fake_glob):
            self.assertTrue(llm_manager._rocm_runtime_complete())


if __name__ == "__main__":
    unittest.main()
