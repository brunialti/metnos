"""Test del generatore smoke case di skill_admission (_smoke_case_for_plan)
+ filtro qualifier provider in _add_smoke_cases_for (skills_cli).

Regression canary per i 4 bug pre-15/5/2026 sera dell'importer:
1. mappa queries_by_pattern incompleta -> fallback "verb object" stub.
2. expected_first_tool con suffix provider anche su query senza marker
   -> aspettativa irraggiungibile (filtro grammar ADR 0136 esclude).
3. nessuno skip per pattern non mappato -> bad test data in BATTERY_IMPORTS.
4. nessuna dedup naturale (cf. smoke_imports.add_case).

Fix: _smoke_case_for_plan ritorna `_no_smoke=True` per pattern non mappati;
_strip_unmarked_provider strippa il suffix provider quando la query non ha
marker.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
sys.path.insert(0, str(_RUNTIME / "cli"))


class _FakePlan:
    def __init__(self, verb, obj, qualifier=None, name=None, args=None):
        self.verb = verb
        self.obj = obj
        self.qualifier = qualifier
        self.name = name or f"{verb}_{obj}"
        self.args = args or []


class TestSmokeCaseForPlan:
    """_smoke_case_for_plan: mappa pattern -> query realistica IT."""

    def test_mapped_pattern_returns_realistic_query(self):
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("send", "messages",
                      name="send_messages_google_workspace")
        case = _smoke_case_for_plan(p)
        assert "_no_smoke" not in case
        assert case["query"] == "scrivi a Roberto: ciao, ci vediamo alle 8"
        # expected_first_tool autoritativo dalla mappa = builtin canonical.
        # NON deriva da plan.name (che ha suffix provider).
        assert case["expected_first_tool"] == "send_messages"

    def test_find_messages_maps_to_read_messages_builtin(self):
        """find_messages non esiste come builtin → mappa ritorna read_messages."""
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("find", "messages",
                      name="find_messages_google_workspace")
        case = _smoke_case_for_plan(p)
        assert case["expected_first_tool"] == "read_messages"

    def test_set_events_maps_to_create_events_builtin(self):
        """set_events non esiste come builtin → mappa ritorna create_events."""
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("set", "events",
                      name="set_events_google_workspace")
        case = _smoke_case_for_plan(p)
        assert case["expected_first_tool"] == "create_events"

    def test_unmapped_pattern_returns_no_smoke_flag(self):
        """Pattern non in queries_by_pattern -> skip auto-add."""
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("compress", "files", qualifier="zip",
                      name="compress_files_zip")
        case = _smoke_case_for_plan(p)
        assert case.get("_no_smoke") is True
        assert "_reason" in case

    def test_no_stub_fallback_query_emitted(self):
        """Niente case con query stub "verb object" generica EN."""
        from skill_admission import _smoke_case_for_plan
        # Verbo+obj che non sono nella mappa.
        p = _FakePlan("render", "texts", name="render_texts")
        case = _smoke_case_for_plan(p)
        # Stub "render texts" NON deve essere emesso.
        assert "query" not in case or case.get("_no_smoke")

    def test_mapped_pattern_with_qualifier(self):
        """Pattern con qualifier (es. files_xlsx) deve matchare."""
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("read", "files", qualifier="xlsx",
                      name="read_files_xlsx")
        case = _smoke_case_for_plan(p)
        assert "_no_smoke" not in case
        assert "foglio" in case["query"].lower()
        assert case["expected_first_tool"] == "read_files_xlsx"


class TestStripUnmarkedProvider:
    """_strip_unmarked_provider: allinea expected con filtro grammar ADR 0136."""

    def test_strips_suffix_when_query_has_no_marker(self):
        from skills_cli import _strip_unmarked_provider
        out = _strip_unmarked_provider(
            "send_messages_google_workspace",
            "scrivi a Roberto",
        )
        assert out == "send_messages"

    def test_keeps_suffix_when_query_has_marker(self):
        from skills_cli import _strip_unmarked_provider
        # Marker "gmail" presente nella query.
        out = _strip_unmarked_provider(
            "send_messages_google_workspace",
            "manda via gmail a Roberto",
        )
        assert out == "send_messages_google_workspace"

    def test_passthrough_canonical_tool(self):
        """Tool senza suffix provider -> identita'."""
        from skills_cli import _strip_unmarked_provider
        out = _strip_unmarked_provider(
            "send_messages", "scrivi a Roberto",
        )
        assert out == "send_messages"

    def test_workspace_marker_word_keeps_suffix(self):
        from skills_cli import _strip_unmarked_provider
        out = _strip_unmarked_provider(
            "find_files_google_workspace",
            "cerca documenti nel mio google drive",
        )
        assert out == "find_files_google_workspace"


class TestQueriesByPatternCoverage:
    """La mappa `queries_by_pattern` copre i pattern comuni §2.2."""

    @pytest.mark.parametrize("verb,obj", [
        ("read", "events"), ("set", "events"), ("delete", "events"),
        ("find", "messages"), ("read", "messages"), ("send", "messages"),
        ("move", "messages"), ("find", "files"), ("read", "files"),
        ("write", "files"), ("delete", "files"), ("create", "dirs"),
        ("list", "dirs"), ("read", "contacts"), ("share", "files"),
    ])
    def test_common_pattern_is_mapped(self, verb, obj):
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan(verb, obj, name=f"{verb}_{obj}")
        case = _smoke_case_for_plan(p)
        assert "_no_smoke" not in case, (
            f"pattern ({verb}, {obj}) non mappato in queries_by_pattern"
        )
        assert case["query"] and len(case["query"]) > len(f"{verb} {obj}"), (
            f"query troppo corta per ({verb}, {obj}): {case['query']!r}"
        )


# ── ADR 0159: L5 smoke at-import reject ─────────────────────────────────

class TestRunSmokeForPlanADR0159:
    """`_run_smoke_for_plan` (ADR 0159 L5): esegue smoke at-import, reject se fail."""

    def test_no_smoke_case_passes_with_skip_reason(self):
        from skill_admission import _run_smoke_for_plan
        p = _FakePlan("compress", "files", qualifier="zip",
                      name="compress_files_zip")
        case = {"_no_smoke": True, "_reason": "no mapping"}
        ok, reason = _run_smoke_for_plan(p, case)
        assert ok is True
        assert "skip" in reason.lower()

    def test_empty_expected_first_tool_passes_skipped(self):
        from skill_admission import _run_smoke_for_plan
        p = _FakePlan("read", "events", name="read_events")
        case = {"query": "x", "expected_first_tool": ""}
        ok, reason = _run_smoke_for_plan(p, case)
        assert ok is True
        assert "skip" in reason.lower()

    def test_smoke_ok_passes(self, monkeypatch):
        """Mock smoke runner che ritorna ok=True → accept."""
        from skill_admission import _run_smoke_for_plan
        import smoke

        def _mock_runner(case, *, catalog=None):
            return {"ok": True, "skip": False,
                    "expected": case["expected_first_tool"],
                    "actual_first": case["expected_first_tool"]}

        monkeypatch.setattr(smoke, "_run_smoke_with_tool_assertion", _mock_runner)
        p = _FakePlan("read", "events", name="read_events")
        case = {"query": "elenca appuntamenti", "expected_first_tool": "read_events"}
        ok, reason = _run_smoke_for_plan(p, case)
        assert ok is True

    def test_smoke_fail_rejects(self, monkeypatch):
        """Mock smoke runner che ritorna ok=False → reject (ADR 0159)."""
        from skill_admission import _run_smoke_for_plan
        import smoke

        def _mock_runner(case, *, catalog=None):
            return {"ok": False, "skip": False,
                    "expected": case["expected_first_tool"],
                    "actual_first": "some_other_tool"}

        monkeypatch.setattr(smoke, "_run_smoke_with_tool_assertion", _mock_runner)
        p = _FakePlan("read", "events", name="read_events")
        case = {"query": "elenca appuntamenti", "expected_first_tool": "read_events"}
        ok, reason = _run_smoke_for_plan(p, case)
        assert ok is False
        assert "smoke fail" in reason

    def test_smoke_runner_exception_rejects(self, monkeypatch):
        """Runner raise → reject (no silent failure §2.8)."""
        from skill_admission import _run_smoke_for_plan
        import smoke

        def _bad_runner(case, *, catalog=None):
            raise RuntimeError("catalog broken")

        monkeypatch.setattr(smoke, "_run_smoke_with_tool_assertion", _bad_runner)
        p = _FakePlan("read", "events", name="read_events")
        case = {"query": "x", "expected_first_tool": "read_events"}
        ok, reason = _run_smoke_for_plan(p, case)
        assert ok is False
        assert "raised" in reason

    def test_admit_skill_import_rejects_on_smoke_fail(self, monkeypatch, tmp_path):
        """End-to-end: admit_skill_import con L5 smoke fail → plan rejected."""
        from skill_admission import admit_skill_import
        import smoke

        # Mock smoke runner: fail per ogni case con query non vuota.
        def _mock_runner(case, *, catalog=None):
            return {"ok": False, "skip": False,
                    "expected": case.get("expected_first_tool"),
                    "actual_first": "other_tool"}

        monkeypatch.setattr(smoke, "_run_smoke_with_tool_assertion", _mock_runner)
        # Disabilita L6 per test isolato.
        monkeypatch.setenv("METNOS_SYNT_STAGE6_DISABLED", "1")

        class _FakeParsed:
            name = "test-skill-l5-fail"
            source_sha256 = "abc"

        # Plan con pattern mappato (read_events) → smoke runner verra' chiamato.
        plan = _FakePlan("read", "events", name="read_events_test_skill_l5_fail")
        report = admit_skill_import(
            _FakeParsed(), [plan],
            executor_dir=tmp_path,
            skip_l2=True,  # L2 needs codegen, isoliamo L5
            skip_binding_check=True,
            audit_log=False,
        )
        assert len(report.rejected) == 1
        v = report.rejected[0]
        assert v.layer_results.get("L5_smoke_exec") is False
        assert any("L5_smoke_exec" in r for r in v.reasons)

    def test_admit_skill_import_skip_l5_exec_flag(self, monkeypatch, tmp_path):
        """`skip_l5_exec=True` bypassa il smoke gate even on broken runner."""
        from skill_admission import admit_skill_import
        import smoke

        def _bad_runner(case, *, catalog=None):
            raise RuntimeError("should not be called when skip_l5_exec=True")

        monkeypatch.setattr(smoke, "_run_smoke_with_tool_assertion", _bad_runner)
        monkeypatch.setenv("METNOS_SYNT_STAGE6_DISABLED", "1")

        class _FakeParsed:
            name = "test-skill-l5-skip"
            source_sha256 = "abc"

        plan = _FakePlan("read", "events", name="read_events_test_skill_l5_skip")
        report = admit_skill_import(
            _FakeParsed(), [plan],
            executor_dir=tmp_path,
            skip_l2=True,
            skip_l5_exec=True,
            skip_binding_check=True,
            audit_log=False,
        )
        # smoke runner non chiamato → no rejection from L5.
        assert len(report.accepted) == 1
        v = report.accepted[0]
        assert "L5_smoke_exec" not in v.layer_results

    def test_legacy_env_metnos_smoke_at_import_off_disables_gate(
        self, monkeypatch, tmp_path,
    ):
        """`METNOS_SMOKE_AT_IMPORT=0` legacy kill-switch."""
        from skill_admission import admit_skill_import
        import smoke

        def _bad_runner(case, *, catalog=None):
            raise RuntimeError("should not be called")

        monkeypatch.setattr(smoke, "_run_smoke_with_tool_assertion", _bad_runner)
        monkeypatch.setenv("METNOS_SYNT_STAGE6_DISABLED", "1")
        monkeypatch.setenv("METNOS_SMOKE_AT_IMPORT", "0")

        class _FakeParsed:
            name = "test-skill-legacy-smoke-off"
            source_sha256 = "abc"

        plan = _FakePlan("read", "events", name="read_events_test_skill_legacy_smoke_off")
        report = admit_skill_import(
            _FakeParsed(), [plan],
            executor_dir=tmp_path,
            skip_l2=True,
            skip_binding_check=True,
            audit_log=False,
        )
        assert len(report.accepted) == 1


# ── ADR 0159: L6 default-ON for imported ─────────────────────────────────

class TestL6DefaultOnADR0159:
    """L6 semantic verifier default ON per imported (ADR 0159)."""

    def test_l6_runs_by_default_on_imported(self, monkeypatch, tmp_path):
        """Default: L6 viene invocato (no env override)."""
        from skill_admission import admit_skill_import
        import smoke

        # Disable smoke L5 per isolare L6.
        monkeypatch.setattr(smoke, "_run_smoke_with_tool_assertion",
                            lambda c, catalog=None: {"ok": True, "skip": True,
                                                      "reason": "test"})

        called = {"n": 0}

        def _mock_verify(description, code_body, *, name_hint="", **kw):
            called["n"] += 1
            return {"aligned": True, "mismatch": ""}

        monkeypatch.setenv("METNOS_STAGE6_VERIFY_FAKE",
                           f"{_mock_verify.__module__}.test_l6_verifier_fn")
        # Garbage in env path won't be importable; patch direct via stage6 fn.
        import skill_admission as sa
        monkeypatch.setattr(sa, "_stage6_verify_callable",
                            lambda: _mock_verify)

        class _FakeParsed:
            name = "test-skill-l6-on"
            source_sha256 = "abc"

        plan = _FakePlan("read", "events", name="read_events_test_skill_l6_on")
        # Create dummy manifest+code so _stage6_check has something to read.
        d = tmp_path / plan.name
        d.mkdir()
        (d / "manifest.toml").write_text("name='x'")
        (d / f"{plan.name}.py").write_text("def invoke(args): return {}")

        admit_skill_import(
            _FakeParsed(), [plan],
            executor_dir=tmp_path,
            skip_l2=True,
            skip_l5_exec=True,
            skip_binding_check=True,
            audit_log=False,
        )
        assert called["n"] == 1, "L6 must run by default for imported (ADR 0159)"

    def test_l6_legacy_env_off_disables(self, monkeypatch, tmp_path):
        """`METNOS_STAGE6_VERIFY_IMPORTED=0` legacy kill-switch."""
        from skill_admission import admit_skill_import
        import smoke
        import skill_admission as sa

        monkeypatch.setattr(smoke, "_run_smoke_with_tool_assertion",
                            lambda c, catalog=None: {"ok": True, "skip": True,
                                                      "reason": "test"})
        called = {"n": 0}

        def _mock_verify(*a, **kw):
            called["n"] += 1
            return {"aligned": False, "mismatch": "should not be called"}

        monkeypatch.setattr(sa, "_stage6_verify_callable",
                            lambda: _mock_verify)
        monkeypatch.setenv("METNOS_STAGE6_VERIFY_IMPORTED", "0")

        class _FakeParsed:
            name = "test-skill-l6-legacy-off"
            source_sha256 = "abc"

        plan = _FakePlan("read", "events", name="read_events_test_skill_l6_legacy_off")
        report = admit_skill_import(
            _FakeParsed(), [plan],
            executor_dir=tmp_path,
            skip_l2=True,
            skip_l5_exec=True,
            skip_binding_check=True,
            audit_log=False,
        )
        assert called["n"] == 0
        assert len(report.accepted) == 1

    def test_l6_global_disable_env(self, monkeypatch, tmp_path):
        """`METNOS_SYNT_STAGE6_DISABLED=1` global kill-switch."""
        from skill_admission import admit_skill_import
        import smoke
        import skill_admission as sa

        monkeypatch.setattr(smoke, "_run_smoke_with_tool_assertion",
                            lambda c, catalog=None: {"ok": True, "skip": True,
                                                      "reason": "test"})
        called = {"n": 0}

        def _mock_verify(*a, **kw):
            called["n"] += 1
            return {"aligned": False, "mismatch": "should not be called"}

        monkeypatch.setattr(sa, "_stage6_verify_callable",
                            lambda: _mock_verify)
        monkeypatch.setenv("METNOS_SYNT_STAGE6_DISABLED", "1")

        class _FakeParsed:
            name = "test-skill-l6-global-off"
            source_sha256 = "abc"

        plan = _FakePlan("read", "events", name="read_events_test_skill_l6_global_off")
        admit_skill_import(
            _FakeParsed(), [plan],
            executor_dir=tmp_path,
            skip_l2=True,
            skip_l5_exec=True,
            skip_binding_check=True,
            audit_log=False,
        )
        assert called["n"] == 0


# ── ADR 0159: L2 SoT in loader ────────────────────────────────────────────

class TestL2AffinitySoT:
    """`loader.check_affinity_pair` single source of truth (ADR 0159)."""

    def test_jaccard_affinity_basic(self):
        from loader import jaccard_affinity
        assert jaccard_affinity({"a", "b"}, {"a", "b"}) == 1.0
        assert jaccard_affinity({"a"}, {"b"}) == 0.0
        assert jaccard_affinity({"a", "b"}, {"a", "c"}) == 1 / 3
        assert jaccard_affinity(set(), {"a"}) == 0.0

    def test_check_affinity_pair_threshold(self):
        from loader import check_affinity_pair
        triggered, j = check_affinity_pair({"a", "b"}, {"a", "b"}, threshold=0.5)
        assert triggered is True
        assert j == 1.0
        triggered, j = check_affinity_pair({"a"}, {"b"}, threshold=0.5)
        assert triggered is False

    def test_skill_admission_uses_loader_sot(self):
        """`_affinity_overlap_check` delega a loader.check_affinity_pair."""
        from skill_admission import _affinity_overlap_check

        class _P:
            name = "read_messages_xyz"

        # Match esatto su handcrafted simulato.
        scan_handcrafted = {"read_messages": {"posta", "email", "mail"}}
        scan_synth: dict = {}
        ok, reason = _affinity_overlap_check(
            _P(), ["posta", "email", "mail"],
            scan_handcrafted, scan_synth,
            binding="",
        )
        assert ok is False
        assert "handcrafted" in reason
        assert "jaccard=1.00" in reason

    def test_binding_threshold_relaxed(self):
        """Plan binding-suffixed → soglia 0.85 (sopporta overlap parziale)."""
        from skill_admission import _affinity_overlap_check

        class _P:
            name = "read_messages_google_workspace"

        # Jaccard 2/3 = 0.67: sotto 0.85 binding threshold ma sopra 0.5 standard.
        scan_handcrafted = {"read_messages": {"posta", "email", "mail"}}
        ok, reason = _affinity_overlap_check(
            _P(), ["posta", "email", "altro"],
            scan_handcrafted, {},
            binding="google_workspace",
        )
        assert ok is True, f"binding-suffixed deve passare con jaccard<0.85: {reason}"

    def test_binding_threshold_still_rejects_near_identical(self):
        """Anche binding-suffixed reject se overlap ≥ 0.85."""
        from skill_admission import _affinity_overlap_check

        class _P:
            name = "read_messages_google_workspace"

        scan_handcrafted = {"read_messages": {"a", "b", "c", "d"}}
        ok, _ = _affinity_overlap_check(
            _P(), ["a", "b", "c", "d", "e"],  # jaccard 4/5 = 0.8 < 0.85
            scan_handcrafted, {},
            binding="google_workspace",
        )
        assert ok is True
        ok, _ = _affinity_overlap_check(
            _P(), ["a", "b", "c", "d"],  # jaccard 4/4 = 1.0 ≥ 0.85
            scan_handcrafted, {},
            binding="google_workspace",
        )
        assert ok is False


# ── ADR 0159: Audit sharded daily + migration ─────────────────────────────

class TestSkillAuditShardingADR0159:
    """`skill_audit` storage sharded daily + migrazione flat legacy."""

    def test_shard_path_for_ts_uses_yyyy_mm_dd(self, monkeypatch, tmp_path):
        monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path))
        # Re-import per riprendere il path patchato.
        import importlib
        import config as _C
        importlib.reload(_C)
        import skill_audit
        importlib.reload(skill_audit)
        import time
        # 2026-05-24 12:00:00 UTC -> ts 1779999600
        ts = 1779969600.0
        p = skill_audit._shard_path_for_ts(ts)
        assert p.name == time.strftime("%Y-%m-%d", time.gmtime(ts)) + ".jsonl"

    def test_audit_writes_to_shard(self, monkeypatch, tmp_path):
        monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path))
        import importlib
        import config as _C
        importlib.reload(_C)
        import skill_audit
        importlib.reload(skill_audit)

        skill_audit.audit_skill_invocation(
            executor_name="read_events_test",
            provenance={"imported_from": "agentskills.io/local/test"},
            args={"a": 1},
            result={"ok": True},
            elapsed_ms=5,
        )
        shards = skill_audit._iter_shards()
        assert len(shards) >= 1, "must write at least one daily shard"
        text = shards[0].read_text(encoding="utf-8")
        assert "read_events_test" in text

    def test_migration_splits_legacy_flat(self, monkeypatch, tmp_path):
        """Flat legacy splittato per giorno al primo `stats()`."""
        monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path))
        import importlib
        import json
        import config as _C
        importlib.reload(_C)
        import skill_audit
        importlib.reload(skill_audit)

        # Crea flat legacy con 2 record di giorni diversi.
        flat = skill_audit._legacy_flat_path()
        flat.parent.mkdir(parents=True, exist_ok=True)
        recs = [
            {"ts": 1779696000.0, "executor_name": "a", "skill_id": "s1", "outcome": "ok"},  # 2026-05-21
            {"ts": 1779782400.0, "executor_name": "b", "skill_id": "s2", "outcome": "ok"},  # 2026-05-22
        ]
        with flat.open("w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

        # Trigger migration via stats().
        s = skill_audit.stats()
        assert s["records"] == 2, "tutti i record migrati"
        assert s["n_distinct_skills"] == 2

        # Flat archiviato.
        archive = flat.with_suffix(flat.suffix + skill_audit._MIGRATION_SUFFIX)
        assert archive.exists()
        assert not flat.exists()

        # Due shard creati.
        shards = skill_audit._iter_shards()
        assert len(shards) == 2

    def test_migration_idempotent(self, monkeypatch, tmp_path):
        """Migrazione non duplica record su second call."""
        monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path))
        import importlib
        import json
        import config as _C
        importlib.reload(_C)
        import skill_audit
        importlib.reload(skill_audit)

        flat = skill_audit._legacy_flat_path()
        flat.parent.mkdir(parents=True, exist_ok=True)
        with flat.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": 1779696000.0, "executor_name": "a",
                                "skill_id": "s1", "outcome": "ok"}) + "\n")

        skill_audit.stats()  # migrate
        n1 = skill_audit.stats()["records"]
        skill_audit._MIGRATION_DONE = False  # forza re-attempt
        n2 = skill_audit.stats()["records"]
        assert n1 == n2 == 1, "no duplication on second migration attempt"


class TestL4DocumentedNotImplemented:
    """ADR 0159 documenta L4 sandbox come planned-not-implemented."""

    def test_executor_has_sandbox_profile_attr(self):
        """`Executor.sandbox_profile` esiste ma e' read-only (no enforcement)."""
        from loader import Executor
        # Verifica solo che l'attributo esista (sia readable).
        sig = Executor.__init__.__doc__ or ""
        import dataclasses
        fields = {f.name for f in dataclasses.fields(Executor)}
        assert "sandbox_profile" in fields, (
            "Executor.sandbox_profile field richiesto da ADR 0159 L4 "
            "planned-not-implemented"
        )

    def test_watchdog_module_exists(self):
        """`jobs/skill_sandbox_watchdog.py` esistente per Fase C trigger."""
        import importlib
        m = importlib.import_module("jobs.skill_sandbox_watchdog")
        assert hasattr(m, "check_threshold")
        assert hasattr(m, "task_skill_sandbox_watchdog")
