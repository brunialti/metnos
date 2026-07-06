"""Recovery «directory passata a un consumer di file» + onestà del final.

Bug live 6/7 (turn 4f35434c, PC remoto): «cancella su pc-roberto i file nella
directory C:\\...\\Downloads» → il proposer emette delete_files(paths=[<dir>]),
l'executor rifiuta (ERR_PATH_WRONG_TYPE expected=file actual=directory), il
re-propose ripete lo stesso call e il final dichiara il FALSO «Nessun elemento
trovato» (il path esiste: è una directory).

Due guardie testate qui:
 1. MetisRecovery._fix_dir_passed_as_file — deterministico §7.9: ricostruisce
    [find_files(base_path=<dir>), <tool>(from_step=N)] dai campi STRUTTURATI
    dei failed[] (funziona anche per path remoti, dove stat locale è
    impossibile).
 2. _enforce_mutating_honesty — split not-found vs fallimenti reali per
    error_code: «non trovato» SOLO per *_NOT_FOUND; il resto riporta il
    motivo vero (ri-renderizzato via i18n dai campi strutturati quando il
    device manda il fallback grezzo).

Run: `python3 -m pytest runtime/tests/test_recovery_wrong_type_dir.py -xvs`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from engine.recovery_metis import MetisRecovery  # noqa: E402
from engine.types import Intent, RunResult, StepRun  # noqa: E402


def _wrong_type_item(path, actual="directory", structured=True):
    it = {"index": 0, "path": path,
          "error_code": "ERR_PATH_WRONG_TYPE",
          "error": f"ERR_PATH_WRONG_TYPE expected=file actual={actual} "
                   f"path={path}"}
    if structured:
        it["expected"] = "file"
        it["actual"] = actual
    return it


def _failed_run(paths, failed, prior_steps=(), ok_count=0, results=None):
    steps = list(prior_steps) + [StepRun(
        step_idx=len(prior_steps), tool="delete_files",
        args={"paths": list(paths), "_actor": "X", "client": "local"},
        result={"ok": False, "ok_count": ok_count,
                "fail_count": len(failed), "results": results or [],
                "failed": failed},
        ok=False, latency_ms=1)]
    return RunResult(steps=steps, final_kind="error", ok_count=ok_count)


class _BoomProposer:
    """Se il fix deterministico copre il caso, il re-propose NON va chiamato."""
    def propose(self, **kw):
        raise AssertionError("re-propose chiamato: il fix deterministico "
                             "doveva coprire il caso")


class _NoneProposer:
    def __init__(self):
        self.called = False

    def propose(self, **kw):
        self.called = True
        return None


class FixDirPassedAsFileTests(unittest.TestCase):

    def setUp(self):
        self.rec = MetisRecovery()
        self.intent = Intent(verb="delete", object="files")

    def test_fire_single_dir(self):
        run = _failed_run([r"C:\Users\rober\Downloads"],
                          [_wrong_type_item(r"C:\Users\rober\Downloads")])
        fw = self.rec._fix_dir_passed_as_file(run, self.intent, None)
        self.assertIsNotNone(fw)
        self.assertEqual([s.tool for s in fw.steps],
                         ["find_files", "delete_files"])
        self.assertEqual(fw.steps[0].args["base_path"],
                         r"C:\Users\rober\Downloads")
        self.assertEqual(fw.steps[1].args.get("from_step"), 1)
        self.assertNotIn("paths", fw.steps[1].args)
        self.assertNotIn("_actor", fw.steps[1].args)

    def test_fire_via_recover_before_repropose(self):
        run = _failed_run(["/tmp/dirx"], [_wrong_type_item("/tmp/dirx")])
        fw = self.rec.recover(failed_run=run, query="cancella i file in /tmp/dirx",
                              intent=self.intent, pool=["delete_files"],
                              proposer=_BoomProposer(), catalog=None)
        self.assertIsNotNone(fw)
        self.assertEqual(fw.steps[0].tool, "find_files")

    def test_no_fire_intent_object_dirs(self):
        run = _failed_run(["/tmp/dirx"], [_wrong_type_item("/tmp/dirx")])
        prop = _NoneProposer()
        fw = self.rec.recover(failed_run=run, query="cancella la cartella",
                              intent=Intent(verb="delete", object="dirs"),
                              pool=["delete_files"], proposer=prop,
                              catalog=None)
        self.assertIsNone(fw)
        self.assertTrue(prop.called, "con object=dirs deve cadere al re-propose")

    def test_no_fire_actual_special(self):
        run = _failed_run(["/dev/null"],
                          [_wrong_type_item("/dev/null", actual="special")])
        self.assertIsNone(
            self.rec._fix_dir_passed_as_file(run, self.intent, None))

    def test_no_fire_two_distinct_dirs(self):
        run = _failed_run(["/a", "/b"], [_wrong_type_item("/a"),
                                         _wrong_type_item("/b")])
        self.assertIsNone(
            self.rec._fix_dir_passed_as_file(run, self.intent, None))

    def test_no_fire_partial_success(self):
        run = _failed_run(["/a/f.txt", "/b"], [_wrong_type_item("/b")],
                          ok_count=1,
                          results=[{"path": "/a/f.txt", "removed": True}])
        self.assertIsNone(
            self.rec._fix_dir_passed_as_file(run, self.intent, None))

    def test_fire_unstructured_item_on_files_tool(self):
        # Runtime device VECCHIO (shim stantio): failed[] senza
        # expected/actual. La direzione è garantita dal nome tool *_files
        # (bug live turn 5afe01e3, 6/7: il PC esegue local.py pre-fix).
        run = _failed_run(["/tmp/dirx"],
                          [_wrong_type_item("/tmp/dirx", structured=False)])
        fw = self.rec._fix_dir_passed_as_file(run, self.intent, None)
        self.assertIsNotNone(fw)
        self.assertEqual([s.tool for s in fw.steps],
                         ["find_files", "delete_files"])

    def test_no_fire_unstructured_item_on_non_files_tool(self):
        # Senza strutturati E senza garanzia dal nome (tool non *_files):
        # direzione non certificabile → no-fire.
        run = _failed_run(["/tmp/f.txt"],
                          [_wrong_type_item("/tmp/f.txt", structured=False)])
        run.steps[-1] = StepRun(
            step_idx=0, tool="delete_dirs",
            args={"paths": ["/tmp/f.txt"]},
            result=run.steps[-1].result, ok=False, latency_ms=1)
        self.assertIsNone(
            self.rec._fix_dir_passed_as_file(run, self.intent, None))

    def test_no_fire_structured_contradicts_files_tool(self):
        # Strutturati presenti che NEGANO la direzione (expected=directory):
        # vincono sui segnali dal nome → no-fire.
        it = _wrong_type_item("/tmp/f.txt", structured=False)
        it["expected"], it["actual"] = "directory", "file"
        run = _failed_run(["/tmp/f.txt"], [it])
        self.assertIsNone(
            self.rec._fix_dir_passed_as_file(run, self.intent, None))

    def test_no_fire_mixed_failures(self):
        run = _failed_run(["/tmp/dirx", "/tmp/ghost.txt"],
                          [_wrong_type_item("/tmp/dirx"),
                           {"index": 1, "path": "/tmp/ghost.txt",
                            "error_code": "ERR_PATH_NOT_FOUND",
                            "error": "x"}])
        self.assertIsNone(
            self.rec._fix_dir_passed_as_file(run, self.intent, None))

    def test_fire_preserves_readonly_prefix(self):
        prior = [StepRun(step_idx=0, tool="get_now", args={},
                         result={"ok": True}, ok=True, latency_ms=1)]
        run = _failed_run(["/tmp/dirx"], [_wrong_type_item("/tmp/dirx")],
                          prior_steps=prior)
        fw = self.rec._fix_dir_passed_as_file(run, self.intent, None)
        self.assertEqual([s.tool for s in fw.steps],
                         ["get_now", "find_files", "delete_files"])
        self.assertEqual(fw.steps[2].args.get("from_step"), 2)

    def test_no_fire_mutating_prefix(self):
        prior = [StepRun(step_idx=0, tool="move_files", args={},
                         result={"ok": True}, ok=True, latency_ms=1)]
        run = _failed_run(["/tmp/dirx"], [_wrong_type_item("/tmp/dirx")],
                          prior_steps=prior)
        self.assertIsNone(
            self.rec._fix_dir_passed_as_file(run, self.intent, None))


class FixGlobPassedAsPathTests(unittest.TestCase):
    """Glob literal in paths → ERR_PATH_NOT_FOUND → find_files(patterns)."""

    def setUp(self):
        self.rec = MetisRecovery()
        self.intent = Intent(verb="delete", object="files")

    @staticmethod
    def _nf_item(path):
        return {"index": 0, "path": path,
                "error_code": "ERR_PATH_NOT_FOUND", "error": "x"}

    def test_fire_windows_glob(self):
        run = _failed_run([r"C:\Users\rober\Downloads\output_compare\*"],
                          [self._nf_item(
                              r"C:\Users\rober\Downloads\output_compare\*")])
        fw = self.rec._fix_glob_passed_as_path(run, self.intent, None)
        self.assertIsNotNone(fw)
        self.assertEqual(fw.steps[0].tool, "find_files")
        self.assertEqual(fw.steps[0].args["base_path"],
                         r"C:\Users\rober\Downloads\output_compare")
        self.assertEqual(fw.steps[0].args["patterns"], ["*"])
        self.assertIs(fw.steps[0].args["recursive"], False)
        self.assertEqual(fw.steps[1].args.get("from_step"), 1)

    def test_fire_posix_glob_ext(self):
        run = _failed_run(["/tmp/logs/*.log"],
                          [self._nf_item("/tmp/logs/*.log")])
        fw = self.rec._fix_glob_passed_as_path(run, self.intent, None)
        self.assertEqual(fw.steps[0].args["base_path"], "/tmp/logs")
        self.assertEqual(fw.steps[0].args["patterns"], ["*.log"])

    def test_no_fire_plain_not_found(self):
        run = _failed_run(["/tmp/ghost.txt"],
                          [self._nf_item("/tmp/ghost.txt")])
        self.assertIsNone(
            self.rec._fix_glob_passed_as_path(run, self.intent, None))

    def test_no_fire_glob_in_parent(self):
        run = _failed_run(["/tmp/*/x.log"], [self._nf_item("/tmp/*/x.log")])
        self.assertIsNone(
            self.rec._fix_glob_passed_as_path(run, self.intent, None))

    def test_no_fire_two_parents(self):
        run = _failed_run(["/a/*.log", "/b/*.log"],
                          [self._nf_item("/a/*.log"),
                           self._nf_item("/b/*.log")])
        self.assertIsNone(
            self.rec._fix_glob_passed_as_path(run, self.intent, None))


class DeleteFilesGlobExpansionTests(unittest.TestCase):
    """Backend §2.4: glob espansi a soli file regolari; 0 match = not-found."""

    def setUp(self):
        import os
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="metnos_glob_test_")
        self.hist = tempfile.mkdtemp(prefix="metnos_glob_hist_")
        os.environ["METNOS_HISTORY_DIR"] = self.hist
        for name in ("a.log", "b.log", "c.txt"):
            Path(self.tmp, name).write_text("x")
        Path(self.tmp, "sub.log").mkdir()  # dir che matcha il glob: MAI presa

    def tearDown(self):
        import os
        import shutil
        os.environ.pop("METNOS_HISTORY_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.hist, ignore_errors=True)

    def test_glob_deletes_files_only(self):
        from backends.files import local
        out = local.delete_files({"paths": [f"{self.tmp}/*.log"]})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["ok_count"], 2)
        self.assertTrue(Path(self.tmp, "sub.log").is_dir())
        self.assertTrue(Path(self.tmp, "c.txt").is_file())

    def test_glob_zero_match_honest_not_found(self):
        from backends.files import local
        out = local.delete_files({"paths": [f"{self.tmp}/*.nope"]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["failed"][0]["error_code"], "ERR_PATH_NOT_FOUND")


class ScaledTimeoutTests(unittest.TestCase):
    """Deadline scalata per invocazioni remote MUTANTI di massa (stopgap A.0)."""

    def test_readonly_untouched(self):
        from remote_exec import _scaled_timeout_s
        self.assertEqual(_scaled_timeout_s(
            30, {"paths": ["x"] * 500}, "read_only"), 30)

    def test_small_vector_untouched(self):
        from remote_exec import _scaled_timeout_s
        self.assertEqual(_scaled_timeout_s(
            30, {"paths": ["x"] * 5}, "revertible"), 30)

    def test_mass_delete_scaled(self):
        # Caso live 1ba8e2c4: 681 path → 30+681 > cap → 600s (non più 30s).
        from remote_exec import _scaled_timeout_s
        self.assertEqual(_scaled_timeout_s(
            30, {"paths": ["x"] * 681, "client": "local"}, "revertible"), 600)

    def test_medium_vector_linear(self):
        from remote_exec import _scaled_timeout_s
        self.assertEqual(_scaled_timeout_s(
            30, {"paths": ["x"] * 100}, "revertible"), 130)

    def test_no_list_args_untouched(self):
        from remote_exec import _scaled_timeout_s
        self.assertEqual(_scaled_timeout_s(
            45, {"path": "/x"}, "revertible"), 45)


class MutatingHonestyMessageTests(unittest.TestCase):
    """Il final di un mutating fallito dice il VERO motivo, non «non trovato»."""

    def _turn_with_delete_result(self, result, final="Fatto."):
        import agent_runtime
        log = agent_runtime.TurnLog(ts_start=0.0, user_query="q")
        sl = agent_runtime.StepLog(step_num=1)
        sl.chosen_tool = "delete_files"
        sl.result = result
        log.steps.append(sl)
        log.final_message = final
        log.final_kind = "answer"
        log.write()
        return log.final_message or ""

    def test_wrong_type_not_claimed_as_not_found(self):
        # Shape REMOTA reale (turn 4f35434c): error grezzo, niente strutturati.
        final = self._turn_with_delete_result({
            "ok": False, "ok_count": 0, "fail_count": 1, "results": [],
            "failed": [{"index": 0, "path": r"C:\Users\rober\Downloads",
                        "error_code": "ERR_PATH_WRONG_TYPE",
                        "error": "ERR_PATH_WRONG_TYPE expected=file "
                                 r"actual=directory path=C:\Users\rober\Downloads"}],
        })
        self.assertNotIn("Nessun elemento", final)
        self.assertNotIn("No item", final)
        self.assertIn("ERR_PATH_WRONG_TYPE", final)  # motivo vero visibile

    def test_wrong_type_structured_rerendered_via_i18n(self):
        from messages import get as _msg
        final = self._turn_with_delete_result({
            "ok": False, "ok_count": 0, "fail_count": 1, "results": [],
            "failed": [{"index": 0, "path": "/tmp/dirx",
                        "error_code": "ERR_PATH_WRONG_TYPE",
                        "expected": "file", "actual": "directory",
                        "error": "raw fallback"}],
        })
        expected_reason = _msg("ERR_PATH_WRONG_TYPE", expected="file",
                               actual="directory", path="/tmp/dirx")
        self.assertIn(expected_reason, final)
        self.assertNotIn("Nessun elemento", final)

    def test_pure_not_found_keeps_legacy_message(self):
        final = self._turn_with_delete_result({
            "ok": False, "ok_count": 0, "fail_count": 1, "results": [],
            "failed": [{"index": 0, "path": "/tmp/ghost.txt",
                        "error_code": "ERR_PATH_NOT_FOUND",
                        "error": "x"}],
        })
        self.assertIn("/tmp/ghost.txt", final)
        self.assertTrue("Nessun elemento" in final or "No item" in final)

    def test_mutating_timeout_says_uncertain_not_none_done(self):
        # Bug live 1ba8e2c4 (6/7): delete di massa sul device uccisa dalla
        # deadline a metà → esecuzione PARZIALE reale. Il final NON deve dire
        # «nessuna operazione eseguita»: esito INCERTO, esplicito.
        final = self._turn_with_delete_result({
            "ok": False, "error": "deadline exceeded",
            "error_class": "timeout", "n_processed": 0,
            "_ran_on_device": "PC-ROBERTO",
        })
        from messages import get as _msg
        self.assertEqual(final, _msg("MSG_MUTATE_TIMEOUT_UNCERTAIN",
                                     tool="delete_files",
                                     device="PC-ROBERTO"))
        self.assertNotIn("Nessuna operazione eseguita", final)
        self.assertNotIn("Nessun elemento", final)

    def test_readonly_timeout_not_intercepted(self):
        # Timeout su step READ-ONLY: nessuna mutazione possibile → la guardia
        # incerta NON scatta (resta il final originale).
        import agent_runtime
        log = agent_runtime.TurnLog(ts_start=0.0, user_query="q")
        sl = agent_runtime.StepLog(step_num=1)
        sl.chosen_tool = "find_files"
        sl.result = {"ok": False, "error": "deadline exceeded",
                     "error_class": "timeout"}
        log.steps.append(sl)
        log.final_message = "Non ho potuto leggere i file."
        log.final_kind = "answer"
        log.write()
        self.assertNotIn("INCERTO", log.final_message or "")

    def test_partial_reports_reason(self):
        final = self._turn_with_delete_result({
            "ok": False, "ok_count": 1, "fail_count": 1,
            "results": [{"path": "/a/f.txt", "removed": True}],
            "failed": [{"index": 1, "path": "/tmp/dirx",
                        "error_code": "ERR_PATH_WRONG_TYPE",
                        "expected": "file", "actual": "directory",
                        "error": "raw"}],
        }, final="Ho cancellato 1 file.")
        # Il final può essere riscritto da altre guardie (unfulfilled-intent);
        # qui si verifica la NOTICE §2.8: path + motivo vero, non «non trovato».
        from messages import get as _msg
        self.assertIn("/tmp/dirx", final)
        self.assertIn(_msg("ERR_PATH_WRONG_TYPE", expected="file",
                           actual="directory", path="/tmp/dirx"), final)
        self.assertNotIn("Nessun elemento", final)


if __name__ == "__main__":
    unittest.main()
