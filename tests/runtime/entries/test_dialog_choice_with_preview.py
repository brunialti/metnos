"""Test deterministici per kind=`choice_with_preview` (PR5).

Coprono:
  - _validate_options_with_preview: payload completo accettato.
  - missing preview_image_path → ValueError.
  - parse_preview_path: bbox shape `<path>#bbox=x,y,w,h` → tuple int.
  - assert_safe_path: path fuori dai root consentiti → ValueError.
  - get_inputs.invoke con dialog choice_with_preview → input_required.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
_EXECUTORS = _RUNTIME.parent / "executors" / "get_inputs"
sys.path.insert(0, str(_EXECUTORS))


@pytest.fixture(autouse=True)
def isolate_dialog_dir(tmp_path, monkeypatch):
    import dialog_pending
    monkeypatch.setattr(dialog_pending, "DIALOG_DIR",
                         tmp_path / "get_inputs")
    yield


@pytest.fixture
def gi():
    import importlib
    import get_inputs as _gi
    importlib.reload(_gi)
    return _gi


@pytest.fixture
def safe_path(tmp_path, monkeypatch):
    """Costruisce un'immagine reale + monkeypatcha allowed_roots verso
    tmp_path cosi' la safe path check passa nei test."""
    from PIL import Image
    p = tmp_path / "img.jpg"
    Image.new("RGB", (100, 100), "blue").save(p, "JPEG")
    import dialog_preview as _dpv
    monkeypatch.setattr(_dpv, "_ALLOWED_ROOTS_RAW", (str(tmp_path),))
    return p


# ── Schema validation ────────────────────────────────────────────────────

def test_choice_with_preview_schema_valid(gi, safe_path):
    """Payload completo (2 option con value+label+preview) → input_required."""
    r = gi.invoke({
        "title": "Quale Ospite?",
        "dialog": [{
            "var": "chosen_slug",
            "prompt": "Pick the right Ospite",
            "schema": {
                "kind": "choice_with_preview",
                "options": [
                    {"value": "ospite_alfa", "label": "Ospite Alfa",
                     "preview_image_path": f"{safe_path}#bbox=10,10,40,40"},
                    {"value": "ospite_beta", "label": "Ospite Beta",
                     "preview_image_path": str(safe_path)},
                ],
            },
        }],
    })
    assert r["ok"] is True, r
    assert r["decision"] == "input_required"
    assert r["step_total"] == 1


def test_choice_with_preview_missing_path_raises(gi, safe_path):
    """option senza preview_image_path → invoke fallisce con error chiaro."""
    r = gi.invoke({
        "title": "x",
        "dialog": [{
            "var": "y",
            "prompt": "?",
            "schema": {
                "kind": "choice_with_preview",
                "options": [
                    {"value": "a", "label": "Opzione A",
                     "preview_image_path": str(safe_path)},
                    {"value": "b", "label": "Opzione B"},
                ],
            },
        }],
    })
    assert r["ok"] is False
    assert "preview_image_path" in r["error"]


def test_choice_with_preview_with_bbox_format(gi):
    """`path#bbox=10,20,100,150` → parse_preview_path ritorna tuple bbox int."""
    import dialog_preview as _dpv
    path, bbox = _dpv.parse_preview_path("/abs/img.jpg#bbox=10,20,100,150")
    assert str(path) == "/abs/img.jpg"
    assert bbox == (10, 20, 100, 150)


def test_choice_with_preview_path_traversal_rejected(gi, tmp_path, monkeypatch):
    """Path fuori da root consentito (es. /etc/passwd) → ValueError."""
    import dialog_preview as _dpv
    monkeypatch.setattr(_dpv, "_ALLOWED_ROOTS_RAW", (str(tmp_path),))
    with pytest.raises(ValueError, match="fuori dai root"):
        _dpv.assert_safe_path(Path("/etc/passwd"))


def test_parse_preview_path_no_bbox(gi):
    import dialog_preview as _dpv
    path, bbox = _dpv.parse_preview_path("/abs/img.jpg")
    assert str(path) == "/abs/img.jpg"
    assert bbox is None


def test_parse_preview_path_bbox_negative_rejected(gi):
    import dialog_preview as _dpv
    with pytest.raises(ValueError):
        _dpv.parse_preview_path("/abs/img.jpg#bbox=-1,0,10,10")


def test_parse_preview_path_bbox_zero_dim_rejected(gi):
    import dialog_preview as _dpv
    with pytest.raises(ValueError):
        _dpv.parse_preview_path("/abs/img.jpg#bbox=0,0,0,10")


def test_options_too_few_rejected(gi, safe_path):
    """choice_with_preview richiede >=2 options."""
    r = gi.invoke({
        "title": "x",
        "dialog": [{
            "var": "y", "prompt": "?",
            "schema": {
                "kind": "choice_with_preview",
                "options": [
                    {"value": "a", "label": "A",
                     "preview_image_path": str(safe_path)},
                ],
            },
        }],
    })
    assert r["ok"] is False
    assert ">=2" in r["error"] or "almeno" in r["error"] or "2 elementi" in r["error"]


def test_options_value_duplicate_rejected(gi, safe_path):
    r = gi.invoke({
        "title": "x",
        "dialog": [{
            "var": "y", "prompt": "?",
            "schema": {
                "kind": "choice_with_preview",
                "options": [
                    {"value": "a", "label": "A",
                     "preview_image_path": str(safe_path)},
                    {"value": "a", "label": "A2",
                     "preview_image_path": str(safe_path)},
                ],
            },
        }],
    })
    assert r["ok"] is False
    assert "duplicat" in r["error"]


def test_crop_image_bytes_with_bbox(gi, tmp_path):
    """crop_image_bytes ritaglia secondo bbox e produce JPEG."""
    from PIL import Image
    import io
    import dialog_preview as _dpv
    p = tmp_path / "src.jpg"
    Image.new("RGB", (200, 200), "red").save(p, "JPEG")
    body = _dpv.crop_image_bytes(p, (10, 10, 50, 50))
    assert isinstance(body, bytes) and len(body) > 100
    # Verifica che il JPEG sia decodificabile.
    out = Image.open(io.BytesIO(body))
    out.load()


def test_crop_image_bytes_no_bbox_fullimage(gi, tmp_path):
    from PIL import Image
    import io
    import dialog_preview as _dpv
    p = tmp_path / "src.jpg"
    Image.new("RGB", (50, 80), "green").save(p, "JPEG")
    body = _dpv.crop_image_bytes(p, None, max_dim=320)
    out = Image.open(io.BytesIO(body))
    out.load()
    # max_dim non ha forzato downscale (50/80 < 320)
    assert out.size == (50, 80)


# ── Inline keyboard compatibility ────────────────────────────────────────

def test_choice_with_preview_inline_compatible(gi):
    """choice_with_preview e' inline-compatible (Telegram album +
    keyboard label-only)."""
    dialog = [{"schema": {"kind": "choice_with_preview"}}]
    assert gi._all_inline_compatible(dialog) is True


def test_telegram_choice_with_preview_inline(gi):
    """telegram + dialog choice_with_preview → telegram_inline."""
    fmt = gi._decide_fmt("auto", 1, "telegram",
                          [{"schema": {"kind": "choice_with_preview"}}])
    assert fmt == "telegram_inline"
