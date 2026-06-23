"""test_engine_vaglio_guard — §sicurezza (gap confermato 23/6): il path engine
di produzione girava SENZA vaglio (`_try_engine_v2` non passava `vaglio_judge`),
mentre il legacy chiama `judge()` prima dell'invoke. Risultato: una mutazione su
forbidden-path (~/.ssh, /etc/shadow, .aws/credentials, /boot...) passava
dall'engine.

Fix: `Executor` ha una GUARDIA deterministica `vaglio_guard` (forbidden-path,
NON il giudice teleologico) eseguita PRE-invoke → previene l'azione, non la
blocca a valle. Wirata in `_try_engine_v2` via `vaglio.guard_check`.
"""
from __future__ import annotations
import os, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.executor import Executor            # noqa: E402
from engine.types import Framework, StepSpec     # noqa: E402
from vaglio import guard_check                    # noqa: E402


def _exec(calls):
    def _invoke(tool, args):
        calls.append((tool, dict(args)))
        return {"ok": True, "entries": []}
    return Executor(invoke_executor=_invoke, vaglio_guard=guard_check, catalog=[])


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
