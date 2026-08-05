"""test_photo_fields_resolver — risoluzione NL→canonico dei campi get_files
via detection_lexicon (i18n-compliant, §7.9). Gemello di
test_junk_mail_resolver."""
import sys
from pathlib import Path


import detection_lexicon_seed as _seed  # noqa: E402
import photo_fields_resolver as pfr  # noqa: E402

_seed.register_all()  # idempotente: assicura il concept nel DB


def _fields(args):
    return args.get("fields")


def test_synonimi_nl_risolti_a_canonico():
    out = pfr.resolve_photo_fields(
        "get_files",
        {"fields": ["date", "gps", "camera", "location"], "entries": []},
        "mostra le date e i metadata delle foto",
    )
    # date->dates.semantic, gps->gps, camera->device, location->place
    assert _fields(out) == ["dates.semantic", "gps", "device", "place"]


def test_all_metadata_espande_tutti_i_campi():
    out = pfr.resolve_photo_fields("get_files", {"fields": ["metadata"]}, "")
    got = set(_fields(out))
    assert {"dates.semantic", "gps", "place", "device", "size",
            "image_dimensions", "dates.created", "dates.modified"} <= got
    assert "all" not in got  # 'all' e' chiave-intento, non un valore-enum


def test_canonico_invariato_idempotente():
    args = {"fields": ["dates.semantic", "gps"]}
    out = pfr.resolve_photo_fields("get_files", dict(args), "")
    assert out == args  # nessun cambiamento → stesso oggetto-valore


def test_sconosciuto_resta_backstop_get_files():
    # 'pippo' non risolvibile → lasciato com'e' → get_files erra onestamente.
    out = pfr.resolve_photo_fields("get_files", {"fields": ["pippo"]}, "")
    assert _fields(out) == ["pippo"]


def test_mix_noto_e_ignoto_preserva_entrambi():
    out = pfr.resolve_photo_fields("get_files", {"fields": ["date", "pippo"]}, "")
    assert _fields(out) == ["dates.semantic", "pippo"]


def test_scalare_coerciato_a_lista():
    out = pfr.resolve_photo_fields("get_files", {"fields": "camera"}, "")
    assert _fields(out) == ["device"]


def test_noop_altri_tool():
    args = {"fields": ["camera"]}
    assert pfr.resolve_photo_fields("read_messages", dict(args), "") == args


def test_noop_senza_fields():
    args = {"entries": []}
    assert pfr.resolve_photo_fields("get_files", dict(args), "") == args
