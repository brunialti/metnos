"""Anti-regressione dominio ENROLLMENT → persons (bug live 12/6/2026 T3).

«cancella l'enrollement di roberto brunialti» veniva pianificata con
delete_credentials (bindings) → «credenziali non trovate». L'enrollment e'
il registro biometrico delle PERSONE: il vocabolario enroll/enrollment/
enrollement/enrollato appartiene a *_persons, MAI a *_credentials.

Copertura deterministica (niente LLM):
  - affinity curata dei manifest persons (tripwire sul dato);
  - rank_with_intent: delete_persons davanti a delete_credentials sulla
    query incriminata (boost affinity §11);
  - build_routing_pool: delete_persons nel pool del Proposer;
  - regola di dominio presente nei prompt proposer/planner (IT+EN).
La scelta finale resta del Proposer (sez. K del prompt); pool/ranking/
prompt devono essere corretti a monte.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prefilter import rank_with_intent
from loader import load_catalog

_QUERY_DELETE = "cancella l'enrollement di roberto brunialti"


class TestPersonsAffinityCoversEnrollment(unittest.TestCase):
    """Tripwire sul dato curato: il vocabolario enroll* vive nei manifest
    *_persons (IT+EN)."""

    def setUp(self):
        self.catalog = load_catalog()
        self.by_name = {getattr(e, "name", None): e for e in self.catalog}
        if "delete_persons" not in self.by_name:
            self.skipTest("catalog privo di delete_persons")

    def _affinity_blob(self, name):
        return " ".join(getattr(self.by_name[name], "affinity", None) or [])

    def test_delete_persons_affinity(self):
        blob = self._affinity_blob("delete_persons")
        for term in ("enrollment", "enrollement", "unenroll"):
            self.assertIn(term, blob,
                          f"delete_persons affinity senza '{term}'")

    def test_get_persons_affinity(self):
        blob = self._affinity_blob("get_persons")
        for term in ("enrollate", "enrollato", "enrolled"):
            self.assertIn(term, blob,
                          f"get_persons affinity senza '{term}'")


class TestEnrollmentDeleteRanking(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        names = {getattr(e, "name", None) for e in self.catalog}
        for required in ("delete_persons", "delete_credentials"):
            if required not in names:
                self.skipTest(f"catalog privo di {required}")

    def test_delete_persons_beats_delete_credentials(self):
        ranked = rank_with_intent(
            _QUERY_DELETE, self.catalog,
            {"verb": "delete", "object": "persons"}, k=12)
        self.assertIsNotNone(ranked)
        names = [e.name for e in ranked]
        self.assertIn("delete_persons", names)
        if "delete_credentials" in names:
            self.assertLess(names.index("delete_persons"),
                            names.index("delete_credentials"),
                            f"delete_credentials davanti: {names}")
        self.assertEqual(names[0], "delete_persons",
                         f"delete_persons non e' top: {names[:5]}")

    def test_routing_pool_contains_delete_persons(self):
        from engine.routing_pool import build_routing_pool
        from engine.types import Intent
        intent = Intent(verb="delete", object="persons",
                        keywords=["enrollement", "roberto brunialti"],
                        lang="it")
        pool = build_routing_pool(_QUERY_DELETE, intent, self.catalog)
        self.assertIn("delete_persons", pool)


class TestEnrollmentPromptRule(unittest.TestCase):
    """La regola di dominio (sez. K / blocco ENROLLMENT) vive nei prompt
    spediti, IT+EN: la sua sparizione e' una regressione."""

    _PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

    def _read(self, rel):
        p = self._PROMPTS / rel
        self.assertTrue(p.is_file(), f"manca {p}")
        return p.read_text()

    def test_engine_proposer_has_enrollment_rule(self):
        for lang in ("it", "en"):
            txt = self._read(f"{lang}/engine_proposer.j2")
            self.assertIn("ENROLLMENT", txt)
            self.assertIn('delete_persons(names=["Carol"])', txt,
                          f"{lang}: regola delete_persons assente")
            self.assertIn("delete_credentials", txt,
                          f"{lang}: anti-pattern credentials assente")

    def test_planner_persons_section_has_enrollment_rule(self):
        for lang in ("it", "en"):
            txt = self._read(f"{lang}/planner/sections/persons.j2")
            self.assertIn("ENROLLMENT", txt)
            self.assertIn('delete_persons(names=["Carol"])', txt)


if __name__ == "__main__":
    unittest.main()
