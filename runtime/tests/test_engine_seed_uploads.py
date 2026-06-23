"""Engine seed-state / foto-allegate (ADR 0177 M1).

Verifica il meccanismo di ASSORBIMENTO del path PLANNER legacy (ADR 0092) nel
motore v3: un seed-state `@uploaded` (foto allegate come step-0 virtuale) viene
consumato dal primo step reale via `from_step=1` + consumer-match, SENZA LLM.

Test deterministici (niente Proposer/llm): costruiamo il Framework a mano e uno
`invoke` stub che registra gli args ricevuti da ogni tool.

Run: `python3 -m pytest runtime/tests/test_engine_seed_uploads.py -xvs`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from engine.executor import Executor                      # noqa: E402
from engine.types import Framework, StepSpec, StepRun     # noqa: E402


# Schema minimale di find_images_indices: l'arg-lista consumer è
# `reference_images` (singolare `reference_image` = campo nelle entries seed).
_FIMI_SCHEMA = {
    "type": "object",
    "properties": {
        "reference_images": {"type": "array", "items": {"type": "string"}},
        "query_text": {"type": "string"},
    },
}


class _StubExec:
    def __init__(self, name, args_schema):
        self.name = name
        self.args_schema = args_schema


def _seed():
    """Seed-state come lo costruisce agent_runtime._try_engine_v2 per le foto."""
    obs = {
        "ok": True,
        "entries": [
            {"path": "/u/a.jpg", "reference_image": "/u/a.jpg",
             "source": "upload"},
            {"path": "/u/b.jpg", "reference_image": "/u/b.jpg",
             "source": "upload"},
        ],
        "_virtual": True, "_kind": "uploaded_reference_images", "n": 2,
    }
    return [StepRun(step_idx=0, tool="@uploaded",
                    args={"source": "upload", "n": 2},
                    result=obs, ok=True, latency_ms=0)]


class EngineSeedUploadsTests(unittest.TestCase):

    def _run(self, framework, seed, catalog):
        seen = {}

        def invoke(tool, args):
            seen[tool] = dict(args)
            return {"ok": True, "entries": [{"path": "/r/x.jpg", "score": 0.9}]}

        ex = Executor(invoke_executor=invoke, seed_steps=seed, catalog=catalog)
        run = ex.run(framework, query="trova foto simili")
        return seen, run

    # 1. find_images_indices senza sorgente → riceve reference_images dal seed.
    def test_find_images_indices_gets_reference_images_from_seed(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices", args={}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertIn("find_images_indices", seen)
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])
        self.assertNotIn("from_step", seen["find_images_indices"])

    # 2. PRECEDENZA: il proposer ha messo query_text, ma le foto allegate
    #    VINCONO (reference_images iniettato), query_text preservato.
    def test_uploaded_photos_win_over_query_text(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices",
                     args={"query_text": "foto simili"}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])
        self.assertEqual(seen["find_images_indices"].get("query_text"),
                         "foto simili")

    # 2.bis CASO REALE (e2e 23/6): il proposer Mētis, ignaro del seed, emette
    #    `reference_images="${step0.entries.*.path}"` — placeholder 0-index che
    #    NON risolve (stepref è 1-index). Il seed-wiring lo droppa e instrada
    #    from_step=1 → reference_images = i path del seed.
    def test_proposer_step0_placeholder_rewired_from_seed(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices",
                     args={"reference_images": "${step0.entries.*.path}",
                           "top_k": 100}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])
        self.assertEqual(seen["find_images_indices"].get("top_k"), 100)

    # 2.ter Variante 1-index `${step1...}` (risolverebbe già): comunque
    #    canonicalizzato a reference_images dai path del seed.
    def test_proposer_step1_placeholder_rewired_from_seed(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices",
                     args={"reference_images": "${step1.entries.*.path}"}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])

    # 3. from_step esplicito del proposer non viene toccato (idempotente).
    def test_explicit_from_step_preserved(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])

    # 4. describe_entries (entries-consumer) eredita le entries del seed.
    def test_describe_entries_inherits_seed_entries(self):
        fw = Framework(steps=[
            StepSpec(tool="describe_entries", args={}),
            StepSpec(tool="final_answer", args={}),
        ])
        cat = [_StubExec("describe_entries",
                         {"type": "object",
                          "properties": {"entries": {"type": "array"}}})]
        seen, _ = self._run(fw, _seed(), cat)
        self.assertIn("describe_entries", seen)
        self.assertEqual(len(seen["describe_entries"].get("entries") or []), 2)

    # 5. REGRESSIONE: senza seed nessun wiring (find_images resta text-only).
    def test_no_seed_no_wiring(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices", args={"query_text": "x"}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, [],
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertNotIn("reference_images", seen.get("find_images_indices", {}))
        self.assertNotIn("from_step", seen.get("find_images_indices", {}))

    # 6. Uno step che NON può consumare il seed (no consumer-arg, non
    #    entries-consumer) non viene cablato (no falso from_step).
    def test_non_consumer_first_step_not_wired(self):
        fw = Framework(steps=[
            StepSpec(tool="get_now", args={}),
            StepSpec(tool="final_answer", args={}),
        ])
        cat = [_StubExec("get_now", {"type": "object", "properties": {}})]
        seen, _ = self._run(fw, _seed(), cat)
        self.assertNotIn("from_step", seen.get("get_now", {}))
        self.assertNotIn("reference_images", seen.get("get_now", {}))


if __name__ == "__main__":
    unittest.main()
