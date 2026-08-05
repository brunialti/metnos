"""Test del path di generazione DETERMINISTICA per describe_entries
(12/6/2026, vedi llm_helpers blocco DETERMINISTICA).

Il llama-server condiviso non e' byte-riproducibile a parita' di richiesta
(stato di processo); `call_llm(deterministic=True)` genera via processo
llama-completion monouso. Qui si testa il CABLAGGIO (hermetico, nessun
GPU/server richiesto): opt-in describe, composizione comando, fallback
onesto, pass-through del budget di serializzazione. La prova live
byte-a-byte sta nel protocollo di verifica (2 run identici, diff vuoto).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import llm_helpers  # noqa: E402


class TestCallLlmDeterministicPath(unittest.TestCase):
    """call_llm(deterministic=True) instrada sul processo monouso."""

    def test_deterministic_true_uses_proc_and_flags_meta(self):
        with mock.patch.object(llm_helpers, "_call_llm_proc",
                               return_value="testo riproducibile") as proc:
            text, meta = llm_helpers.call_llm(
                [{"a": 1}], "PROMPT", deterministic=True, max_tokens=123)
        self.assertEqual(text, "testo riproducibile")
        self.assertIs(meta["deterministic"], True)
        proc.assert_called_once()
        _, kwargs = proc.call_args
        self.assertEqual(kwargs["max_tokens"], 123)
        self.assertEqual(kwargs["seed"], 42)  # METNOS_LLM_SEED default §11

    def test_proc_unavailable_falls_back_to_http_honestly(self):
        fake_resp = mock.Mock(text="fallback http", in_tokens=10, out_tokens=5)
        with mock.patch.object(llm_helpers, "_call_llm_proc",
                               return_value=None), \
             mock.patch.object(llm_helpers.LlamaCppProvider, "chat",
                               return_value=fake_resp):
            text, meta = llm_helpers.call_llm(
                "query", "PROMPT", deterministic=True)
        self.assertEqual(text, "fallback http")
        self.assertIs(meta["deterministic"], False)

    def test_default_path_has_no_deterministic_key(self):
        """deterministic=False (default): nessun claim nel meta, nessun
        processo spawnnato — gli altri consumer (enrichment, classify)
        restano sul path HTTP invariato."""
        fake_resp = mock.Mock(text="http", in_tokens=1, out_tokens=1)
        with mock.patch.object(llm_helpers, "_call_llm_proc") as proc, \
             mock.patch.object(llm_helpers.LlamaCppProvider, "chat",
                               return_value=fake_resp):
            _, meta = llm_helpers.call_llm("q", "P")
        proc.assert_not_called()
        self.assertNotIn("deterministic", meta)

    def test_thinking_tier_skips_proc_path(self):
        """A centrally configured thinking tier cannot use the deterministic
        process renderer, so the fallback to HTTP remains explicit."""
        fake_resp = mock.Mock(text="http", in_tokens=1, out_tokens=1)
        spec = {
            "provider": "llamacpp", "model": "local",
            "endpoint": "http://127.0.0.1:8080",
            "temperature": 0.0, "think": True, "reasoning_budget": 256,
        }
        with mock.patch.object(llm_helpers, "resolved_tier_spec",
                               return_value=spec), \
             mock.patch.object(llm_helpers, "_call_llm_proc") as proc, \
             mock.patch.object(llm_helpers.LlamaCppProvider, "chat",
                               return_value=fake_resp):
            _, meta = llm_helpers.call_llm(
                "q", "P", tier="wise", deterministic=True)
        proc.assert_not_called()
        self.assertIs(meta["deterministic"], False)

    def test_random_seed_env_skips_proc_path(self):
        """METNOS_LLM_SEED=-1 (random esplicito §11) rende il path
        deterministico privo di senso: fallback HTTP dichiarato."""
        fake_resp = mock.Mock(text="http", in_tokens=1, out_tokens=1)
        with mock.patch.dict("os.environ", {"METNOS_LLM_SEED": "-1"}), \
             mock.patch.object(llm_helpers, "_call_llm_proc") as proc, \
             mock.patch.object(llm_helpers.LlamaCppProvider, "chat",
                               return_value=fake_resp):
            _, meta = llm_helpers.call_llm("q", "P", deterministic=True)
        proc.assert_not_called()
        self.assertIs(meta["deterministic"], False)


class TestCallLlmProcWiring(unittest.TestCase):
    """Composizione del comando llama-completion e parsing output."""

    def _run(self, stdout="Riassunto pulito. [end of text]\n", rc=0):
        completed = mock.Mock(returncode=rc, stdout=stdout, stderr="")
        with mock.patch.object(llm_helpers, "_completion_bin",
                               return_value="/usr/bin/llama-completion"), \
             mock.patch.object(llm_helpers, "_server_model_path",
                               return_value="/models/m.gguf"), \
             mock.patch.object(llm_helpers, "_render_chat_prompt",
                               return_value="<rendered>" * 50), \
             mock.patch.object(llm_helpers.subprocess, "run",
                               return_value=completed) as run:
            text = llm_helpers._call_llm_proc(
                "SYS", "USER", max_tokens=400, seed=42)
        return text, run

    def test_command_is_greedy_seeded_single_shot(self):
        text, run = self._run()
        cmd = run.call_args[0][0]
        self.assertEqual(text, "Riassunto pulito.")  # [end of text] strippato
        self.assertIn("--temp", cmd)
        self.assertEqual(cmd[cmd.index("--temp") + 1], "0")
        self.assertIn("-s", cmd)
        self.assertEqual(cmd[cmd.index("-s") + 1], "42")
        self.assertIn("-no-cnv", cmd)
        self.assertIn("--no-display-prompt", cmd)
        self.assertEqual(cmd[cmd.index("-n") + 1], "400")

    def test_nonzero_rc_returns_none(self):
        text, _ = self._run(rc=1)
        self.assertIsNone(text)

    def test_empty_stdout_returns_none(self):
        text, _ = self._run(stdout="   \n")
        self.assertIsNone(text)

    def test_missing_binary_returns_none_without_spawn(self):
        with mock.patch.object(llm_helpers, "_completion_bin",
                               return_value=None), \
             mock.patch.object(llm_helpers.subprocess, "run") as run:
            text = llm_helpers._call_llm_proc(
                "SYS", "USER", max_tokens=100, seed=42)
        self.assertIsNone(text)
        run.assert_not_called()


class TestPromptShaAudit(unittest.TestCase):
    """Auditabilita' determinismo (E2E 12/6/2026, anomalia 1/7): il meta
    del path proc espone `prompt_sha` = sha256 del prompt RENDERIZZATO."""

    def test_call_llm_meta_carries_prompt_sha_of_rendered(self):
        import hashlib
        rendered = "<rendered>" * 50
        completed = mock.Mock(returncode=0, stdout="testo", stderr="")
        with mock.patch.object(llm_helpers, "_completion_bin",
                               return_value="/usr/bin/llama-completion"), \
             mock.patch.object(llm_helpers, "_server_model_path",
                               return_value="/models/m.gguf"), \
             mock.patch.object(llm_helpers, "_render_chat_prompt",
                               return_value=rendered), \
             mock.patch.object(llm_helpers.subprocess, "run",
                               return_value=completed):
            _, meta = llm_helpers.call_llm("q", "P", deterministic=True)
        self.assertIs(meta["deterministic"], True)
        self.assertEqual(
            meta["prompt_sha"],
            hashlib.sha256(rendered.encode("utf-8")).hexdigest())

    def test_http_fallback_has_no_prompt_sha(self):
        """Niente render → niente sha: il campo non viene inventato (§2.8)."""
        fake_resp = mock.Mock(text="http", in_tokens=1, out_tokens=1)
        with mock.patch.object(llm_helpers, "_call_llm_proc",
                               return_value=None), \
             mock.patch.object(llm_helpers.LlamaCppProvider, "chat",
                               return_value=fake_resp):
            _, meta = llm_helpers.call_llm("q", "P", deterministic=True)
        self.assertNotIn("prompt_sha", meta)


class TestSerializeBudgetPassThrough(unittest.TestCase):
    """max_query_chars: il bundle describe (fino a 24K char §2.7) deve
    passare INTERO; il default 12000 resta per gli altri consumer."""

    def test_default_truncates_at_12000(self):
        big = "x" * 20000
        out = llm_helpers._serialize_query(big)
        self.assertLess(len(out), 13000)
        self.assertIn("[truncated]", out)

    def test_describe_budget_passes_untruncated(self):
        entries = [{"body": "y" * 1000} for _ in range(20)]  # ~20K serializzati
        captured = {}

        def fake_proc(system, user, **kwargs):
            captured["user"] = user
            return "ok"

        with mock.patch.object(llm_helpers, "_call_llm_proc",
                               side_effect=fake_proc):
            llm_helpers.call_llm(entries, "P", deterministic=True,
                                 max_query_chars=26048)
        self.assertNotIn("[truncated]", captured["user"])
        self.assertEqual(json.loads(captured["user"]), entries)


class TestDescribeEntriesOptIn(unittest.TestCase):
    """handle_describe_entries passa deterministic + max_query_chars."""

    def test_describe_calls_llm_with_deterministic_flag(self):
        import describe_entries as de
        captured = {}

        def fake_call_llm(entries, prompt, **kwargs):
            captured.update(kwargs)
            return ("sintesi", {"in_tokens": 0, "out_tokens": 0,
                                "latency_ms": 1, "deterministic": True})

        with mock.patch.object(de, "call_llm", fake_call_llm), \
             mock.patch.object(de.prompt_loader, "get",
                               return_value="STUB"):
            res = de.handle_describe_entries({
                "entries": [{"from": "a@b.c", "subject": "s",
                             "body": "contenuto " * 60}],
            })
        self.assertTrue(res.get("ok"))
        self.assertIs(captured.get("deterministic"),
                      de._DESCRIBE_DETERMINISTIC)
        self.assertEqual(captured.get("max_query_chars"),
                         de._DESCRIBE_MAX_CHARS + 2048)
        # il meta del path deterministico arriva nell'output (§2.8)
        self.assertIs(res.get("deterministic"), True)


if __name__ == "__main__":
    unittest.main()
