"""multi_tool_paths anti-pattern detection (22/5/2026).

`_is_count_antipattern` previene cache di `find_* → compute_entries(op=count
senza key)` che produce numeri sbagliati (es. find_dirs entries=858 dirs
ma user chiede "quanti file" = 33526).

Run: python3 -m pytest runtime/tests/test_multi_tool_count_antipattern.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


class AntipatternDetectionTests(unittest.TestCase):

    def test_find_dirs_count_no_key_is_antipattern(self):
        from multi_tool_paths import _is_count_antipattern
        tools = ["find_dirs", "compute_entries", "final_answer"]
        shape = [{"base_path": "x"}, {"from_step": 1, "op": "count"},
                 {"message": "..."}]
        self.assertTrue(_is_count_antipattern(tools, shape))

    def test_find_dirs_sum_with_key_is_ok(self):
        """find_dirs + compute_entries(sum, key=file_count) è LEGITTIMO:
        somma il file_count dei dirs = totale file ricorsivo."""
        from multi_tool_paths import _is_count_antipattern
        tools = ["find_dirs", "compute_entries", "final_answer"]
        shape = [{"base_path": "x"},
                 {"from_step": 1, "op": "sum", "key": "file_count"},
                 {"message": "..."}]
        self.assertFalse(_is_count_antipattern(tools, shape))

    def test_find_dirs_count_with_key_is_ok(self):
        """compute_entries(count, key=X) conta entries con X non-null,
        legittimo (es. contare dirs con almeno 1 file)."""
        from multi_tool_paths import _is_count_antipattern
        tools = ["find_dirs", "compute_entries", "final_answer"]
        shape = [{"base_path": "x"},
                 {"from_step": 1, "op": "count", "key": "file_count"},
                 {"message": "..."}]
        self.assertFalse(_is_count_antipattern(tools, shape))

    def test_find_files_count_no_key_is_antipattern(self):
        from multi_tool_paths import _is_count_antipattern
        tools = ["find_files", "compute_entries", "final_answer"]
        shape = [{"base_path": "x"}, {"from_step": 1, "op": "count"},
                 {"message": "..."}]
        self.assertTrue(_is_count_antipattern(tools, shape))

    def test_find_messages_count_no_key_is_antipattern(self):
        from multi_tool_paths import _is_count_antipattern
        tools = ["find_messages", "compute_entries", "final_answer"]
        shape = [{}, {"from_step": 1, "op": "count"}, {}]
        self.assertTrue(_is_count_antipattern(tools, shape))

    def test_count_distinct_is_ok(self):
        from multi_tool_paths import _is_count_antipattern
        tools = ["read_messages", "compute_entries", "final_answer"]
        shape = [{}, {"from_step": 1, "op": "count_distinct", "key": "from"},
                 {}]
        self.assertFalse(_is_count_antipattern(tools, shape))

    def test_non_find_source_is_ok(self):
        """get_files_metadata + compute_entries(count) e' OK: il metadata
        non ha aggregato pre-calcolato."""
        from multi_tool_paths import _is_count_antipattern
        tools = ["get_files_metadata", "compute_entries", "final_answer"]
        shape = [{}, {"from_step": 1, "op": "count"}, {}]
        self.assertFalse(_is_count_antipattern(tools, shape))

    def test_empty_sequence(self):
        from multi_tool_paths import _is_count_antipattern
        self.assertFalse(_is_count_antipattern([], []))


if __name__ == "__main__":
    unittest.main()
