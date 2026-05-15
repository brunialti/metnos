"""Test del rendering YAML strutturato per le sezioni planner (asse B PoC,
12/5/2026).

Verifica che `prompt_loader._render_yaml_section` produca prosa compatta
deterministica preservando il pattern §6 (DEVI/NON DEVI/OK/ERRORE) per
ciascuna rule, e che la size sia significativamente minore (-25-35%)
rispetto al sibling `.j2` Jinja prosa.

Determinismo §7.9: zero LLM, zero network. Schema in
`runtime/prompts/SCHEMA_section_rules.md`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


_NOW_VARS = dict(
    now_iso="2026-05-12T12:00:00+02:00",
    now_date="2026-05-12",
    now_year="2026",
    now_weekday="Tue",
    now_time="12:00",
)


_CALENDAR_RULES = (
    "events_core",
    "propose_intent",
    "propose_and_fire",
    "propose_and_notify",
    "check_availability",
    "date_relative_resolution",
)


class TestYamlSectionRenderBasics(unittest.TestCase):
    """Smoke: il render produce output non vuoto, contiene §6 markers,
    contiene tutte le rules."""

    def test_render_calendar_yaml_it_non_empty(self):
        import prompt_loader
        yaml_path = (prompt_loader._BASE / "it" / "planner" / "sections" /
                     "calendar.yaml")
        self.assertTrue(yaml_path.is_file(), "calendar.yaml IT manca")
        out = prompt_loader._render_yaml_section(yaml_path)
        self.assertGreater(len(out), 200,
                           "render YAML troppo corto, probabile bug")

    def test_render_calendar_yaml_en_non_empty(self):
        import prompt_loader
        yaml_path = (prompt_loader._BASE / "en" / "planner" / "sections" /
                     "calendar.yaml")
        self.assertTrue(yaml_path.is_file(), "calendar.yaml EN manca")
        out = prompt_loader._render_yaml_section(yaml_path)
        self.assertGreater(len(out), 200)

    def test_render_includes_all_six_rules_it(self):
        """Tutte le 6 rule names appaiono nel rendered output (IT)."""
        import prompt_loader
        yaml_path = (prompt_loader._BASE / "it" / "planner" / "sections" /
                     "calendar.yaml")
        out = prompt_loader._render_yaml_section(yaml_path)
        for rule_name in _CALENDAR_RULES:
            self.assertIn(f"({rule_name})", out,
                           f"rule '{rule_name}' assente in render YAML IT")

    def test_render_includes_all_six_rules_en(self):
        """Tutte le 6 rule names appaiono nel rendered output (EN)."""
        import prompt_loader
        yaml_path = (prompt_loader._BASE / "en" / "planner" / "sections" /
                     "calendar.yaml")
        out = prompt_loader._render_yaml_section(yaml_path)
        for rule_name in _CALENDAR_RULES:
            self.assertIn(f"({rule_name})", out,
                           f"rule '{rule_name}' assente in render YAML EN")

    def test_render_preserves_section_6_markers_it(self):
        """Format §6: DEVI/NON DEVI/OK/ERRORE per ogni rule."""
        import prompt_loader
        yaml_path = (prompt_loader._BASE / "it" / "planner" / "sections" /
                     "calendar.yaml")
        out = prompt_loader._render_yaml_section(yaml_path)
        # Conta occorrenze per ogni marker: deve essere >= num rule.
        n_rules = len(_CALENDAR_RULES)
        self.assertGreaterEqual(out.count("DEVI: "), n_rules)
        self.assertGreaterEqual(out.count("NON DEVI: "), n_rules)
        self.assertGreaterEqual(out.count("OK: "), n_rules)
        self.assertGreaterEqual(out.count("ERRORE: "), n_rules)

    def test_render_preserves_section_6_markers_en(self):
        """EN mantiene gli stessi marker IT (§6 prescrittivo, vedi planner/
        _core.j2 EN che usa DEVI/NON DEVI come canonical)."""
        import prompt_loader
        yaml_path = (prompt_loader._BASE / "en" / "planner" / "sections" /
                     "calendar.yaml")
        out = prompt_loader._render_yaml_section(yaml_path)
        n_rules = len(_CALENDAR_RULES)
        self.assertGreaterEqual(out.count("DEVI: "), n_rules)
        self.assertGreaterEqual(out.count("NON DEVI: "), n_rules)
        self.assertGreaterEqual(out.count("OK: "), n_rules)
        self.assertGreaterEqual(out.count("ERRORE: "), n_rules)


class TestYamlSizeReduction(unittest.TestCase):
    """Target asse B: -25-35% byte size vs .j2 sibling."""

    def _size_pair(self, lang: str) -> tuple[int, int]:
        """Ritorna (yaml_rendered_size, j2_rendered_size) per calendar."""
        import prompt_loader
        sec_dir = prompt_loader._BASE / lang / "planner" / "sections"
        yaml_path = sec_dir / "calendar.yaml"
        sec_dir / "calendar.j2"
        env = prompt_loader._env_for(lang)
        out_yaml = prompt_loader._render_yaml_section(yaml_path)
        out_j2 = env.render_template(
            "planner/sections/calendar.j2",
            vocab_actions="read,write",
            vocab_objects="events,messages",
            vocab_qualifiers="",
            project_paths="",
            users_known="",
            **_NOW_VARS,
        )
        return len(out_yaml), len(out_j2)

    def test_yaml_size_below_80pct_j2_it(self):
        """Target asse B PoC: -25%/-35% byte size vs .j2 prosa.

        Soglia <80% (= -20%) per smoke; valore reale misurato ~75% IT
        (-25%), allineato al lower bound dell'intervallo target.
        Test piu' stretto NON appropriato in PoC: ulteriori riduzioni
        richiederebbero cambi semantici, non solo refactor di formato.
        """
        n_yaml, n_j2 = self._size_pair("it")
        ratio = n_yaml / n_j2
        self.assertLess(
            n_yaml, int(n_j2 * 0.80),
            f"YAML rendered IT = {n_yaml}, j2 rendered IT = {n_j2}, "
            f"ratio={ratio:.2%} >= 80% (target <80%)",
        )

    def test_yaml_size_below_80pct_j2_en(self):
        n_yaml, n_j2 = self._size_pair("en")
        ratio = n_yaml / n_j2
        self.assertLess(
            n_yaml, int(n_j2 * 0.80),
            f"YAML rendered EN = {n_yaml}, j2 rendered EN = {n_j2}, "
            f"ratio={ratio:.2%} >= 80% (target <80%)",
        )


class TestSchemaSymmetryItEn(unittest.TestCase):
    """Asse B simmetria: IT/EN hanno la stessa lista di rule names."""

    def test_calendar_yaml_rules_symmetric(self):
        import yaml as _yaml
        import prompt_loader
        it_path = (prompt_loader._BASE / "it" / "planner" / "sections" /
                   "calendar.yaml")
        en_path = (prompt_loader._BASE / "en" / "planner" / "sections" /
                   "calendar.yaml")
        it_data = _yaml.safe_load(it_path.read_text(encoding="utf-8"))
        en_data = _yaml.safe_load(en_path.read_text(encoding="utf-8"))
        it_names = [r["name"] for r in it_data["rules"]]
        en_names = [r["name"] for r in en_data["rules"]]
        self.assertEqual(it_names, en_names,
                          f"IT rules {it_names} != EN rules {en_names}")
        # Asserto anche contro la lista canonica del test.
        self.assertEqual(tuple(it_names), _CALENDAR_RULES)


class TestComposeUsesYaml(unittest.TestCase):
    """compose("planner", lang, sections=["calendar"]) usa il `.yaml` se
    presente. Confronta la dimensione contro l'equivalente forzato a `.j2`
    (rinominando temporaneamente il YAML)."""

    def test_compose_with_calendar_yaml_present(self):
        import prompt_loader
        # Reset cache per evitare interferenza con altri test.
        prompt_loader.invalidate_cache()
        out = prompt_loader.compose(
            "planner", "it",
            sections=["calendar"],
            vocab_actions="read,write",
            vocab_objects="events",
            vocab_qualifiers="",
            project_paths="",
            users_known="",
            **_NOW_VARS,
        )
        # Deve contenere marker delle rule YAML (parentesi rule).
        self.assertIn("(events_core)", out)
        self.assertIn("(propose_intent)", out)
        self.assertIn("DEVI:", out)
        self.assertIn("NON DEVI:", out)

    def test_compose_yaml_smaller_than_pure_j2(self):
        """Compose end-to-end: con `calendar.yaml` presente, output piu'
        piccolo rispetto a un compose forzato sul `.j2` (rinominato
        temporaneamente)."""
        import prompt_loader
        prompt_loader.invalidate_cache()
        out_yaml = prompt_loader.compose(
            "planner", "it",
            sections=["calendar"],
            vocab_actions="read,write",
            vocab_objects="events",
            vocab_qualifiers="",
            project_paths="",
            users_known="",
            **_NOW_VARS,
        )
        # Forza il fallback al .j2 rinominando temporaneamente .yaml -> .yaml.bak.
        sec_dir = prompt_loader._BASE / "it" / "planner" / "sections"
        yaml_path = sec_dir / "calendar.yaml"
        bak = sec_dir / "calendar.yaml.bak"
        yaml_path.rename(bak)
        try:
            prompt_loader.invalidate_cache()
            out_j2 = prompt_loader.compose(
                "planner", "it",
                sections=["calendar"],
                vocab_actions="read,write",
                vocab_objects="events",
                vocab_qualifiers="",
                project_paths="",
                users_known="",
                **_NOW_VARS,
            )
        finally:
            bak.rename(yaml_path)
            prompt_loader.invalidate_cache()
        self.assertLess(
            len(out_yaml), len(out_j2),
            f"compose() con .yaml ({len(out_yaml)}) "
            f"non e' piu' piccolo del fallback .j2 ({len(out_j2)})",
        )


class TestYamlRenderDeterministic(unittest.TestCase):
    """Determinismo §7.9: due render successivi byte-equivalenti."""

    def test_render_idempotent(self):
        import prompt_loader
        yaml_path = (prompt_loader._BASE / "it" / "planner" / "sections" /
                     "calendar.yaml")
        a = prompt_loader._render_yaml_section(yaml_path)
        b = prompt_loader._render_yaml_section(yaml_path)
        self.assertEqual(a, b)


class TestYamlLinter(unittest.TestCase):
    """Linter `_check_yaml_section` (5 check)."""

    def test_lint_passes_on_real_calendar_yaml_it(self):
        import prompts_lint
        p = (Path(__file__).resolve().parent.parent
             / "prompts" / "it" / "planner" / "sections" / "calendar.yaml")
        content = p.read_text(encoding="utf-8")
        issues = prompts_lint._check_yaml_section(p, content)
        # Niente issue errori sul YAML reale.
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(errors, [], f"linter ha trovato errori: {errors}")

    def test_lint_detects_missing_frontmatter(self):
        import prompts_lint
        bad = "section:\n  name: x\nrules: []\n"
        issues = prompts_lint._check_yaml_section(Path("/tmp/bad.yaml"), bad)
        codes = {i.code for i in issues}
        self.assertIn("YL_FRONTMATTER_FIELDS", codes)

    def test_lint_detects_empty_must(self):
        import prompts_lint
        bad = (
            "role: planner\ntier: middle\nlang: it\nstyle: prescriptive\n"
            "version: 1\nowner: roberto\nupdated: 2026-05-12\nsha_prev: \"\"\n"
            "section:\n  name: x\nrules:\n  - name: r1\n    when: w\n"
            "    must: \"\"\n    must_not: n\n    ok: o\n    error: e\n"
        )
        issues = prompts_lint._check_yaml_section(Path("/tmp/bad.yaml"), bad)
        codes = {i.code for i in issues}
        self.assertIn("YL_RULE_FIELD_EMPTY", codes)

    def test_lint_detects_duplicate_rule_name(self):
        import prompts_lint
        bad = (
            "role: planner\ntier: middle\nlang: it\nstyle: prescriptive\n"
            "version: 1\nowner: roberto\nupdated: 2026-05-12\nsha_prev: \"\"\n"
            "section:\n  name: x\nrules:\n"
            "  - name: dup\n    when: w\n    must: m\n    must_not: n\n"
            "    ok: o\n    error: e\n"
            "  - name: dup\n    when: w\n    must: m\n    must_not: n\n"
            "    ok: o\n    error: e\n"
        )
        issues = prompts_lint._check_yaml_section(Path("/tmp/bad.yaml"), bad)
        codes = {i.code for i in issues}
        self.assertIn("YL_RULE_NAME_DUPLICATE", codes)


if __name__ == "__main__":
    unittest.main()
