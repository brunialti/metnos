"""Test sanitize surrogati UTF-16 (ADR 0121)."""
from __future__ import annotations

import json
import sys

if "/opt/myclaw/runtime" not in sys.path:
    sys.path.insert(0, "/opt/myclaw/runtime")

from utf8_safe import strip_surrogates, clean_obj, safe_json_dumps  # type: ignore


# Surrogate alto + basso (formano un pair UTF-16 rotto in UTF-8)
SURROGATE_HI = "\ud83d"  # U+D83D
SURROGATE_LO = "\udca9"  # U+DCA9


def test_strip_surrogates_removes_high_and_low():
    s = f"hello {SURROGATE_HI}{SURROGATE_LO} world"
    assert strip_surrogates(s) == "hello  world"


def test_strip_surrogates_idempotent():
    s = "no surrogates qui ✓"
    assert strip_surrogates(s) == s
    assert strip_surrogates(strip_surrogates(s)) == s


def test_strip_surrogates_preserves_valid_unicode():
    s = "italiano à è ì ò ù 你好 🎉"
    cleaned = strip_surrogates(s)
    # 🎉 è U+1F389 (non surrogate); 你好 sono CJK validi
    assert cleaned == s


def test_strip_surrogates_non_string_passthrough():
    assert strip_surrogates(None) is None
    assert strip_surrogates(42) == 42
    assert strip_surrogates(["x"]) == ["x"]


def test_clean_obj_dict_recursive():
    obj = {
        "key": f"value {SURROGATE_HI}",
        "nested": {"inner": f"x{SURROGATE_LO}y"},
    }
    cleaned = clean_obj(obj)
    assert cleaned["key"] == "value "
    assert cleaned["nested"]["inner"] == "xy"


def test_clean_obj_list_recursive():
    obj = [f"a{SURROGATE_HI}", "b", {"c": f"d{SURROGATE_LO}"}]
    cleaned = clean_obj(obj)
    assert cleaned[0] == "a"
    assert cleaned[1] == "b"
    assert cleaned[2]["c"] == "d"


def test_clean_obj_tuple_creates_new():
    obj = (f"x{SURROGATE_HI}", "y")
    cleaned = clean_obj(obj)
    assert isinstance(cleaned, tuple)
    assert cleaned == ("x", "y")


def test_safe_json_dumps_strips_surrogates():
    obj = {"prompt": f"describe {SURROGATE_HI}{SURROGATE_LO} this"}
    raw = safe_json_dumps(obj)
    # Il JSON e' valido UTF-8 e non contiene escape surrogate
    enc = raw.encode("utf-8")
    decoded = enc.decode("utf-8")  # NON deve sollevare
    parsed = json.loads(decoded)
    assert parsed["prompt"] == "describe  this"


def test_safe_json_dumps_preserves_unicode():
    obj = {"text": "Descrivi: una foto al mare 🌊"}
    raw = safe_json_dumps(obj)
    parsed = json.loads(raw)
    assert parsed["text"] == "Descrivi: una foto al mare 🌊"


def test_safe_json_dumps_compatible_with_kwargs():
    obj = {"a": "x", "b": "y"}
    indented = safe_json_dumps(obj, indent=2, sort_keys=True)
    assert "  " in indented
    parsed = json.loads(indented)
    assert parsed == obj


def test_real_world_filename_with_surrogate():
    """Filename con encoding storico rotto (es. iconv windows-1252 → utf-8 mismatched)."""
    bad_path = f"/photos/2014/{SURROGATE_HI}_image.jpg"
    payload = {"image_path": bad_path, "instruction": "describe"}
    # Senza sanitize: encode utf-8 con default fa eccezione su surrogate
    # nel JSON via ensure_ascii=False
    raw = safe_json_dumps(payload)
    # Verifica round-trip OK
    parsed = json.loads(raw)
    assert "_image.jpg" in parsed["image_path"]
    assert SURROGATE_HI not in parsed["image_path"]
