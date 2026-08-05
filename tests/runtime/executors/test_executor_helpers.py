"""Test runtime/executor_helpers.py — normalizzazione paths/urls (§2.4).

Pattern §2.4 CLAUDE.md: il PLANNER LLM occasionalmente confonde `urls` con
`paths` su argomenti generici. `normalize_paths_urls` corregge sistematica-
mente PRIMA che l'executor consumi gli args. Idempotente §7.9.

Run: python3 -m pytest tests/runtime/executors/test_executor_helpers.py -v
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from messages import get as _msg  # noqa: E402


def test_assigned_workers_is_runtime_owned_and_bounded(monkeypatch):
    from executor_helpers import assigned_workers

    monkeypatch.setattr("os.cpu_count", lambda: 16)
    monkeypatch.delenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", raising=False)
    assert assigned_workers(default=1) == 1
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "12")
    assert assigned_workers(maximum=8) == 8
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "invalid")
    assert assigned_workers(default=2) == 2


def test_assigned_workers_clamps_to_remote_visible_hardware(monkeypatch):
    from executor_helpers import assigned_workers

    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "32")
    monkeypatch.setattr("os.cpu_count", lambda: 6)
    assert assigned_workers(maximum=32) == 6
    monkeypatch.setattr("os.cpu_count", lambda: None)
    assert assigned_workers(maximum=32) == 1


def test_vector_result_distinguishes_complete_partial_and_empty_success():
    from executor_helpers import vector_result

    assert vector_result([], []) == {
        "ok": True,
        "ok_count": 0,
        "fail_count": 0,
        "entries": [],
        "failed": [],
    }
    partial = vector_result([{"value": 1}], [{"error_code": "bad_item"}])
    assert partial["ok"] is False
    assert partial["partial"] is True
    assert partial["ok_count"] == 1
    assert partial["fail_count"] == 1
    assert "partial" not in vector_result([], [{"error_code": "bad_item"}])


def test_normalize_vector_result_preserves_metadata_and_marks_partial():
    from executor_helpers import normalize_vector_result

    partial = normalize_vector_result({
        "ok": True,
        "entries": [{"uid": "1"}],
        "failed": [{"uid": "2", "error": "bad"}],
        "window": "today",
    })
    assert partial == {
        "ok": False,
        "ok_count": 1,
        "fail_count": 1,
        "partial": True,
        "entries": [{"uid": "1"}],
        "failed": [{"uid": "2", "error": "bad"}],
        "window": "today",
    }

    failed = normalize_vector_result({
        "ok": True, "entries": [], "failed": [{"error": "bad"}],
        "partial": True,
    })
    assert failed["ok"] is False
    assert failed["ok_count"] == 0
    assert failed["fail_count"] == 1
    assert "partial" not in failed


def _run_stdio(invoke, stdin_text, **kw):
    """Esegue run_stdio con stdin/stdout finti; ritorna l'output deserializzato.

    Mirror del path subprocess reale: la stringa entra da stdin, l'oggetto JSON
    esce da stdout (run_stdio fa `import sys` interno → punta a questo modulo
    `sys` globale, quindi il patch di sys.stdin/sys.stdout funziona)."""
    from executor_helpers import run_stdio
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(stdin_text)
    sys.stdout = io.StringIO()
    try:
        run_stdio(invoke, **kw)
        raw = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return raw


class TestNormalizePathsUrls(unittest.TestCase):
    def test_path_in_urls_moves_to_paths(self):
        from executor_helpers import normalize_paths_urls
        out = normalize_paths_urls({
            "urls": ["/home/user/foto.jpg"], "max_results": 10,
        })
        self.assertEqual(out["paths"], ["/home/user/foto.jpg"])
        self.assertEqual(out["urls"], [])
        self.assertEqual(out["max_results"], 10)

    def test_http_url_in_paths_moves_to_urls(self):
        from executor_helpers import normalize_paths_urls
        out = normalize_paths_urls({
            "paths": ["https://example.com/foo.jpg"], "urls": [],
        })
        self.assertEqual(out["urls"], ["https://example.com/foo.jpg"])
        self.assertEqual(out["paths"], [])

    def test_mixed_normalization(self):
        from executor_helpers import normalize_paths_urls
        out = normalize_paths_urls({
            "paths": ["/local/a.jpg", "https://b.com/img.png"],
            "urls": ["/local/c.jpg", "https://d.com/img.png"],
        })
        self.assertEqual(set(out["paths"]),
                          {"/local/a.jpg", "/local/c.jpg"})
        self.assertEqual(set(out["urls"]),
                          {"https://b.com/img.png", "https://d.com/img.png"})

    def test_already_normalized_idempotent(self):
        from executor_helpers import normalize_paths_urls
        args = {"paths": ["/x.jpg"], "urls": ["https://y/img"]}
        out1 = normalize_paths_urls(args)
        out2 = normalize_paths_urls(out1)
        self.assertEqual(out1, out2)
        self.assertEqual(out1["paths"], ["/x.jpg"])
        self.assertEqual(out1["urls"], ["https://y/img"])

    def test_no_paths_no_urls_passthrough(self):
        from executor_helpers import normalize_paths_urls
        args = {"query": "foo", "max_results": 5}
        out = normalize_paths_urls(args)
        self.assertEqual(out, args)

    def test_non_dict_args_passthrough(self):
        from executor_helpers import normalize_paths_urls
        self.assertEqual(normalize_paths_urls(None), None)
        self.assertEqual(normalize_paths_urls("foo"), "foo")
        self.assertEqual(normalize_paths_urls([]), [])

    def test_non_list_paths_wrapped(self):
        from executor_helpers import normalize_paths_urls
        args = {"paths": "single.jpg"}
        out = normalize_paths_urls(args)
        # §2.4 forgiving: una stringa singola in `paths` viene wrappata in lista
        # 1-elemento (+ urls:[]). L'executor accetta plurale anche con 1 elemento.
        self.assertEqual(out, {"paths": ["single.jpg"], "urls": []})

    def test_other_schemas_not_treated_as_paths(self):
        from executor_helpers import normalize_paths_urls
        # file:// e ftp:// non sono path-like (sono URL veri ma non http)
        # NON spostati in paths.
        out = normalize_paths_urls({
            "urls": ["file:///etc/hosts", "ftp://ftp.example.com/a.jpg"],
        })
        # entrambi restano in urls
        self.assertEqual(out["urls"],
                          ["file:///etc/hosts", "ftp://ftp.example.com/a.jpg"])

    def test_does_not_mutate_input(self):
        from executor_helpers import normalize_paths_urls
        args = {"urls": ["/local.jpg"]}
        _ = normalize_paths_urls(args)
        # input non mutato
        self.assertEqual(args, {"urls": ["/local.jpg"]})


class TestRunStdio(unittest.TestCase):
    """run_stdio = single source of truth del main() I/O (§2.1/§2.8). Copre il
    contratto base + i 3 keyword (default/error_extra/allow_empty) che
    riproducono fedelmente le varianti del main() copiato negli executor."""

    def test_valid_input_calls_invoke(self):
        seen = {}

        def invoke(args):
            seen.update(args)
            return {"ok": True, "echo": args.get("x")}

        out = json.loads(_run_stdio(invoke, '{"x": 42}'))
        self.assertEqual(seen, {"x": 42})
        self.assertEqual(out, {"ok": True, "echo": 42})

    def test_empty_stdin_is_err_empty_input(self):
        out = json.loads(_run_stdio(lambda a: {"ok": True}, ""))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], _msg("ERR_EMPTY_INPUT"))

    def test_whitespace_stdin_is_empty(self):
        out = json.loads(_run_stdio(lambda a: {"ok": True}, "   \n  "))
        self.assertEqual(out["error"], _msg("ERR_EMPTY_INPUT"))

    def test_invalid_json_is_err_json_invalid(self):
        out = json.loads(_run_stdio(lambda a: {"ok": True}, "x{not json"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], _msg("ERR_JSON_INVALID"))

    def test_invoke_error_propagates_not_masked(self):
        # invoke() e' fuori dal try sul JSONDecodeError: un suo errore NON
        # viene mascherato come «JSON non valido».
        def invoke(args):
            raise json.JSONDecodeError("boom", "doc", 0)

        with self.assertRaises(json.JSONDecodeError):
            _run_stdio(invoke, '{"x": 1}')

    def test_allow_empty_calls_invoke_with_empty_dict(self):
        seen = {}

        def invoke(args):
            seen["called"] = True
            seen["args"] = args
            return {"ok": True, "n": len(args)}

        out = json.loads(_run_stdio(invoke, "", allow_empty=True))
        self.assertTrue(seen["called"])
        self.assertEqual(seen["args"], {})
        self.assertEqual(out, {"ok": True, "n": 0})

    def test_allow_empty_still_errors_on_invalid_json(self):
        # allow_empty tollera lo stdin VUOTO, non il JSON malformato.
        out = json.loads(_run_stdio(lambda a: {"ok": True}, "x{",
                                    allow_empty=True))
        self.assertEqual(out["error"], _msg("ERR_JSON_INVALID"))

    def test_error_extra_merged_on_invalid(self):
        extra = {"error_class": "invalid_args", "results": [], "n_created": 0}
        out = json.loads(_run_stdio(lambda a: {"ok": True}, "{bad",
                                    error_extra=extra))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], _msg("ERR_JSON_INVALID"))
        self.assertEqual(out["error_class"], "invalid_args")
        self.assertEqual(out["results"], [])
        self.assertEqual(out["n_created"], 0)

    def test_error_extra_merged_on_empty(self):
        extra = {"error_class": "invalid_args", "entries": [], "used": 0}
        out = json.loads(_run_stdio(lambda a: {"ok": True}, "",
                                    error_extra=extra))
        self.assertEqual(out["error"], _msg("ERR_EMPTY_INPUT"))
        self.assertEqual(out["entries"], [])
        self.assertEqual(out["used"], 0)

    def test_default_serializer_for_non_json_values(self):
        # senza default=str questo solleverebbe TypeError (crash, §2.8);
        # con default=str il valore non-serializzabile diventa stringa.
        class Weird:
            def __str__(self):
                return "WEIRD"

        out = json.loads(_run_stdio(lambda a: {"ok": True, "v": Weird()},
                                    '{"x":1}', default=str))
        self.assertEqual(out["v"], "WEIRD")

    def test_non_ascii_preserved(self):
        raw = _run_stdio(lambda a: {"ok": True, "msg": "città però"},
                         '{"x":1}')
        # ensure_ascii=False: i caratteri accentati NON sono \uXXXX
        self.assertIn("città però", raw)


if __name__ == "__main__":
    unittest.main()
