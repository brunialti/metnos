"""Iniezione server-side dei form credenziale (ADR 0199, rev. 24/7 sera).

La sandbox degli executor non monta le chiavi trusted né i manifest skill:
`credential_form_kinds()` gira SOLO nel processo server e il runtime inietta
il risultato nell'arg runtime-owned `credential_forms` al choke-point
(`_fill_runtime_sourced_args`), per invocazione fresca e resume. L'executor
senza iniezione risponde con un errore onesto, mai col catalogo vuoto.
"""

from __future__ import annotations

from types import SimpleNamespace

import agent_runtime


FORMS = {
    "mail": {
        "kind": "mail",
        "label_key": "MSG_CREDENTIAL_KIND_OPTION_MAIL",
        "binding_prefix": "smtp_",
        "detect_prefixes": ["smtp_", "imap_"],
        "fields": [{
            "name": "password", "required": True, "secret": True,
            "input": "credentials",
            "prompt_key": "MSG_CREDENTIAL_FIELD_PASSWORD",
        }],
    },
}


def _executor_with_sourced_arg():
    return SimpleNamespace(
        name="set_credentials",
        args_schema={"type": "object", "properties": {
            "binding": {"type": "string"},
            "credential_forms": {
                "type": "object",
                "runtime_resolved": True,
                "runtime_source": "credential_forms",
            },
        }},
    )


def test_choke_point_fills_and_overwrites_sourced_arg(monkeypatch):
    monkeypatch.setitem(
        agent_runtime._RUNTIME_ARG_SOURCES, "credential_forms", lambda: FORMS)
    executor = _executor_with_sourced_arg()
    args = agent_runtime._fill_runtime_sourced_args(
        executor, {"binding": "x"})
    assert args["credential_forms"] == FORMS
    # Runtime-owned: un valore preesistente (leak coercito) viene sostituito.
    args = agent_runtime._fill_runtime_sourced_args(
        executor, {"binding": "x", "credential_forms": {"fake": {}}})
    assert args["credential_forms"] == FORMS


def test_failed_source_leaves_arg_absent(monkeypatch):
    def boom():
        raise RuntimeError("catalogo non disponibile")

    monkeypatch.setitem(
        agent_runtime._RUNTIME_ARG_SOURCES, "credential_forms", boom)
    executor = _executor_with_sourced_arg()
    args = agent_runtime._fill_runtime_sourced_args(
        executor, {"binding": "x", "credential_forms": {"fake": {}}})
    assert "credential_forms" not in args


def test_unknown_source_is_ignored():
    executor = SimpleNamespace(
        name="x", args_schema={"properties": {"a": {
            "runtime_resolved": True, "runtime_source": "inesistente"}}})
    args = agent_runtime._fill_runtime_sourced_args(executor, {})
    assert args == {}


def test_scheduler_health_source_overwrites_untrusted_value(monkeypatch):
    observation = {
        "component": "scheduler_v2", "state": "running",
        "healthy": True, "reason_code": "loop_active",
    }
    monkeypatch.setitem(
        agent_runtime._RUNTIME_ARG_SOURCES,
        "scheduler_health", lambda: observation,
    )
    executor = SimpleNamespace(
        name="get_processes", args_schema={"properties": {
            "scheduler_health": {
                "type": "object", "runtime_resolved": True,
                "runtime_source": "scheduler_health",
            },
        }},
    )
    args = agent_runtime._fill_runtime_sourced_args(
        executor, {"scheduler_health": {"state": "failed"}})
    assert args["scheduler_health"] == observation


def test_executor_description_source_is_localized_verified_and_bounded(
        monkeypatch):
    class FakeCatalog:
        def __iter__(self):
            return iter([
                SimpleNamespace(name="zeta", description="Z" * 900),
                SimpleNamespace(name="alpha", description="Descrizione alpha"),
                SimpleNamespace(name="", description="invalid"),
            ])

    calls: list[str] = []
    monkeypatch.setattr(
        "loader.load_catalog",
        lambda *, lang: calls.append(lang) or FakeCatalog(),
    )
    monkeypatch.setattr("i18n.current_lang", lambda: "xx")

    projection = agent_runtime._runtime_executor_descriptions()

    assert list(projection) == ["alpha", "zeta"]
    assert projection["alpha"] == "Descrizione alpha"
    assert len(projection["zeta"]) == 512
    assert calls == ["xx"]


def test_server_side_collector_matches_live_catalog():
    """Nel processo server (chiavi trusted presenti) il collettore raccoglie
    i 4 domini dichiaranti correnti; l'iniezione end-to-end li consegna."""

    from loader import load_catalog

    catalog = {ex.name: ex for ex in load_catalog()}
    executor = catalog.get("set_credentials")
    if executor is None:
        import pytest
        pytest.skip("catalogo senza set_credentials in questo ambiente")
    args = agent_runtime._fill_runtime_sourced_args(executor, {"binding": "x"})
    kinds = args.get("credential_forms")
    assert isinstance(kinds, dict) and "api" in kinds


def test_executor_without_injection_reports_honest_error():
    import sys
    from pathlib import Path

    root = Path(agent_runtime.__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "executors" / "set_credentials"))
    try:
        import set_credentials as sc
    finally:
        sys.path.pop(0)
    out = sc.invoke({"binding": "servizio-di-prova-inesistente"})
    assert out["ok"] is False
    assert "credential_forms" in out["error"]
