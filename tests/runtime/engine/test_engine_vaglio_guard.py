"""test_engine_vaglio_guard — §sicurezza (gap confermato 23/6): il path engine
di produzione girava SENZA vaglio (`_run_engine` non passava `vaglio_judge`),
mentre il legacy chiama `judge()` prima dell'invoke. Risultato: una mutazione su
forbidden-path (~/.ssh, /etc/shadow, .aws/credentials, /boot...) passava
dall'engine.

Fix: `Executor` ha una GUARDIA deterministica `vaglio_guard` (forbidden-path,
NON il giudice teleologico) eseguita PRE-invoke → previene l'azione, non la
blocca a valle. Wirata in `_run_engine` via `vaglio.guard_check`.
"""
from __future__ import annotations
import os, sys, unittest
from pathlib import Path
from types import SimpleNamespace

import pytest


from engine.executor import Executor            # noqa: E402
from engine.types import Framework, StepSpec     # noqa: E402
from vaglio import guard_check                    # noqa: E402


def _exec(calls, *, catalog=None, guard=guard_check):
    def _invoke(tool, args):
        calls.append((tool, dict(args)))
        return {"ok": True, "entries": []}
    return Executor(invoke_executor=_invoke, vaglio_guard=guard, catalog=catalog or [])


def _run(ex, tool, args):
    fw = Framework(steps=[StepSpec(tool=tool, args=args),
                          StepSpec(tool="final_answer", args={})])
    return ex.run(fw, query="q", runtime_ctx={}, remediate_args_cb=None, progress=None)


class TestEngineVaglioGuard(unittest.TestCase):
    def test_forbidden_path_bloccato_PRIMA_dell_invoke(self):
        calls = []
        r = _run(_exec(calls), "delete_files", {"paths": ["~/.ssh/id_rsa"]})
        self.assertEqual(r.final_kind, "error")
        self.assertIn("vaglio_guard", str(r.aborted_reason))
        self.assertEqual(len(calls), 0)  # PREVENZIONE: l'invoke NON è avvenuto

    def test_etc_shadow_bloccato(self):
        calls = []
        r = _run(_exec(calls), "write_files", {"paths": ["/etc/shadow"], "content": "x"})
        self.assertEqual(r.final_kind, "error")
        self.assertEqual(len(calls), 0)

    def test_path_normale_passa(self):
        calls = []
        r = _run(_exec(calls), "read_files", {"paths": ["/tmp/x.txt"]})
        self.assertGreaterEqual(len(calls), 1)  # eseguito normalmente

    def test_senza_guard_nessun_blocco(self):
        # Executor senza vaglio_guard (default) → comportamento invariato.
        calls = []
        def _invoke(tool, args):
            calls.append(tool); return {"ok": True, "entries": []}
        ex = Executor(invoke_executor=_invoke, catalog=[])
        _run(ex, "delete_files", {"paths": ["~/.ssh/id_rsa"]})
        self.assertEqual(len(calls), 1)  # nessuna guardia → esegue (back-compat)


if __name__ == "__main__":
    unittest.main()


def _source_executor():
    return SimpleNamespace(
        name="create_objects_indices", signed_by="fixture-signer", lifecycle="active", dormant=False,
        args_schema={"type": "object", "properties": {"base_path": {"type": "string"}}},
        capabilities=[{"name": "fs:read", "hint": ["arg:base_path"]},
                      {"name": "metnos:cache", "hint": ["objects:local"]}],
    )


def test_preinvoke_binds_catalog_for_protected_source_but_not_secrets():
    ex = _source_executor()
    calls = []
    engine = _exec(calls, catalog=[ex])
    _run(engine, ex.name, {"base_path": "/var/lib/app/user_data/Images"})
    assert len(calls) == 1
    result = _run(engine, ex.name, {"base_path": "~/.ssh/id_rsa"})
    assert len(calls) == 1
    assert result.final_kind == "error"
    assert "vaglio_guard" in result.aborted_reason


def test_parallel_preflight_uses_the_same_verified_catalog_authority():
    ex = _source_executor()
    engine = _exec([], catalog=[ex])
    step = StepSpec(tool=ex.name, args={})
    assert engine._parallel_preflight(step, {"base_path": "/var/photos"}, query="q", runtime_ctx={})
    assert not engine._parallel_preflight(step, {"base_path": "~/.ssh/id_rsa"}, query="q", runtime_ctx={})
    assert not engine._parallel_preflight(step, {"base_path": "/var/photos", "dst": "/var/output"}, query="q", runtime_ctx={})


@pytest.mark.parametrize("error_type", [TypeError, RuntimeError])
def test_guard_error_never_invokes_or_submits_and_is_not_retried(error_type):
    calls, guards = [], []
    def broken(name, args):
        guards.append(name)
        raise error_type("guard unavailable")
    engine = _exec(calls, guard=broken)
    result = _run(engine, "read_files", {"paths": ["/tmp/test"]})
    assert result.final_kind == "error"
    assert calls == []
    assert guards == ["read_files"]
    assert not engine._parallel_preflight(StepSpec("read_files", {}), {}, query="q", runtime_ctx={})
    assert guards == ["read_files", "read_files"]


def test_engine_custom_two_argument_guard_remains_compatible():
    calls, guards = [], []
    def custom(name, args):
        guards.append((name, args))
        return True, None
    engine = _exec(calls, guard=custom)
    _run(engine, "read_files", {"paths": ["/tmp/test"]})
    assert len(calls) == len(guards) == 1
