from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "runtime"))


def _module():
    path = ROOT / "executors" / "read_files_ocr" / "read_files_ocr.py"
    spec = importlib.util.spec_from_file_location("read_files_ocr_agentic", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_good_tesseract_output_skips_vlm(monkeypatch):
    module = _module()
    monkeypatch.setattr(module.vlm_client, "describe_image", lambda *a, **k: (
        (_ for _ in ()).throw(AssertionError("VLM must not run"))))
    text = "Invoice number 12345"
    assert module._improve_ocr_with_agentic_fallback(
        Path("scan.png"), text, "en") == text


def test_empty_tesseract_output_uses_validated_vlm_fallback(monkeypatch):
    module = _module()
    monkeypatch.setattr(module.vlm_client, "describe_image", lambda *a, **k: {
        "description": "Invoice number 12345", "keywords": []})
    assert module._improve_ocr_with_agentic_fallback(
        Path("scan.png"), "", "en") == "Invoice number 12345"


def test_failed_vlm_preserves_deterministic_output(monkeypatch):
    module = _module()
    monkeypatch.setattr(module.vlm_client, "describe_image", lambda *a, **k: {
        "description": "", "_vlm_error": "offline"})
    assert module._improve_ocr_with_agentic_fallback(
        Path("scan.png"), "abc", "it") == "abc"


def test_invoke_caps_files_and_reports_truncation(monkeypatch, tmp_path):
    module = _module()
    seen = []
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/tool")

    def fake_read(path, _lang):
        seen.append(path)
        return "enough deterministic text", None

    monkeypatch.setattr(module, "_read_one", fake_read)
    paths = [str(tmp_path / f"scan-{i}.png") for i in range(3)]

    result = module.invoke({"paths": paths, "max_files": 2, "lang": "eng"})

    assert result["ok"] is True
    assert len(seen) == 2
    assert result["truncated"] is True
    assert result["used"] == 2
    assert result["available_total"] == 3
    assert result["cap_field"] == "max_files"
    assert result["cap_value"] == 2


def test_durable_source_provenance_redacts_local_path(monkeypatch, tmp_path):
    module = _module()
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(
        module, "_read_one", lambda _path, _lang: ("testo leggibile", None),
    )

    result = module.invoke({
        "paths": [str(tmp_path / "source.png")],
        "source": {"source_id": "source_00000000"},
    })

    assert result["source_id"] == "source_00000000"
    assert result["entries"] == [{
        "source_id": "source_00000000",
        "content": "testo leggibile",
        "char_count": len("testo leggibile"),
        "lang": "ita+eng",
    }]


def test_durable_language_environment_controls_the_vlm_prompt(monkeypatch, tmp_path):
    module = _module()
    monkeypatch.setenv("METNOS_LANG", "en-US")
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(module, "_read_one", lambda _path, _lang: ("", None))
    observed = []
    monkeypatch.setattr(
        module,
        "_improve_ocr_with_agentic_fallback",
        lambda _path, content, response_lang: observed.append(response_lang) or content,
    )

    result = module.invoke({"paths": [str(tmp_path / "source.png")]})

    assert result["ok"] is True
    assert observed == ["en-us"]
