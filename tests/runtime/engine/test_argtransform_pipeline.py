"""test_argtransform_pipeline — contratto del registro ArgTransform
(gemello di test_guard_pipeline_contract). Il registro unifica la famiglia
resolver deterministica pre-esecuzione (ADR 0177 T3 estensione, 7/7/2026)."""
import importlib
import sys
from pathlib import Path


import engine.executor as ex  # noqa: E402

REG = ex.ARG_TRANSFORM_PIPELINE
VALID_SCOPES = {"query-det", "exec-only"}


def test_entry_contract():
    names = [t.name for t in REG]
    assert len(names) == len(set(names)), "nomi ArgTransform duplicati"
    for t in REG:
        assert t.scope in VALID_SCOPES, f"{t.name}: scope invalido {t.scope!r}"
        assert t.reads, f"{t.name}: reads non dichiarati"
        assert t.writes, f"{t.name}: writes non dichiarati"
        # module + func realmente risolvibili (niente entry-fantasma).
        mod = importlib.import_module(t.module)
        assert hasattr(mod, t.func), f"{t.name}: {t.module}.{t.func} assente"
        assert callable(getattr(mod, t.func))


def test_due_scope_presenti():
    scopes = {t.scope for t in REG}
    assert scopes == VALID_SCOPES, "attesi entrambi i gruppi query-det + exec-only"


def test_driver_filtra_per_scope():
    # photo_fields (query-det) NON deve girare in exec-only.
    out = ex.apply_arg_transforms(
        "get_files", {"fields": ["camera"]}, "", scope="exec-only")
    assert out.get("fields") == ["camera"], "query-det non deve applicarsi in exec-only"
    # ...ma girare in query-det.
    out2 = ex.apply_arg_transforms(
        "get_files", {"fields": ["camera"]}, "", scope="query-det")
    assert out2.get("fields") == ["device"], "photo_fields query-det deve risolvere"


def test_query_det_idempotente():
    # Riapplicare la catena query-det = no-op (requisito record L0, §2.8).
    a0 = {"fields": ["date", "camera"], "account": "metnos_system"}
    a1 = ex.resolve_query_canonical_args("get_files", dict(a0), "date e metadata foto")
    a2 = ex.resolve_query_canonical_args("get_files", dict(a1), "date e metadata foto")
    assert a1 == a2, "catena query-det NON idempotente (avvelenerebbe il record L0)"


def test_ordine_stabile_query_det_prima_gruppi():
    # L'ordine dichiarato: i 5 query-det precedono i 3 exec-only (contratto).
    scopes_in_order = [t.scope for t in REG]
    first_exec = scopes_in_order.index("exec-only")
    assert all(s == "query-det" for s in scopes_in_order[:first_exec]), \
        "i query-det devono precedere gli exec-only nel registro"
