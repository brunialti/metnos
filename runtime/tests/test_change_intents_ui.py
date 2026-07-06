"""change_intents_ui — test handler /admin/changes (GET + POST).

Run: `python3 -m pytest runtime/tests/test_change_intents_ui.py -v`.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


def _reset_modules():
    for m in list(sys.modules):
        if m.startswith("runtime.change_intents") or m == "change_intents":
            del sys.modules[m]


class TestChangesHandlers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        import config as C
        C.DB_CHANGE_INTENTS = self.tmpdir / "ci.sqlite"
        _reset_modules()
        import change_intents as ci_mod
        # chiavi UI_CHANGE_* dal catalogo i18n (seed/live); il bootstrap
        # runtime è stato ritirato (§7.13, 7/7).
        ci_mod.init_db()
        # Popola 2 intent
        self.id1 = ci_mod.upsert_intent(ci_mod.ChangeIntent.new(
            origin_family="telos", origin_module="scamper",
            intent_kind=ci_mod.KIND_CREATE_EXECUTOR,
            intent_target="aaa", intent_summary="Crea aaa",
            intent_body={"name": "aaa", "action": "find", "object": "files"},
            score=0.8,
        ))
        self.id2 = ci_mod.upsert_intent(ci_mod.ChangeIntent.new(
            origin_family="introvertiva", origin_module="dedupe",
            intent_kind=ci_mod.KIND_DEDUPE_EXECUTORS,
            intent_target="bbb", intent_summary="Unifica bbb+ccc",
            intent_body={"a": "ccc", "b": "bbb"},
            score=0.5,
        ))
        self.ci_mod = ci_mod

    def tearDown(self):
        self.tmp.cleanup()

    def _mock_request(self, method, path, *, query=None, match=None, headers=None):
        from aiohttp.test_utils import make_mocked_request
        return make_mocked_request(
            method, path,
            headers=headers or {},
            match_info=match or {},
        )

    def test_get_admin_changes_returns_html_with_rows(self):
        import http_routes_admin
        from aiohttp.test_utils import make_mocked_request
        req = make_mocked_request("GET", "/admin/changes?state=proposed",
                                  headers={"Accept": "text/html"})
        resp = asyncio.run(http_routes_admin.admin_changes(req))
        self.assertEqual(resp.status, 200)
        body = resp.body.decode() if isinstance(resp.body, bytes) else resp.text
        self.assertIn("Cambiamenti al sistema", body)
        self.assertIn("aaa", body)
        self.assertIn("bbb", body)

    def test_get_admin_changes_filter_family(self):
        import http_routes_admin
        from aiohttp.test_utils import make_mocked_request
        req = make_mocked_request("GET", "/admin/changes?state=proposed&family=telos",
                                  headers={"Accept": "text/html"})
        resp = asyncio.run(http_routes_admin.admin_changes(req))
        body = resp.body.decode() if isinstance(resp.body, bytes) else resp.text
        self.assertIn("aaa", body)
        self.assertNotIn("bbb</code>", body)

    def test_post_accept_transitions_state(self):
        import http_routes_admin
        req = self._mock_request("POST", f"/admin/changes/{self.id1}/accept",
                                  match={"id": self.id1, "action": "accept"})
        resp = asyncio.run(http_routes_admin.admin_change_action(req))
        self.assertEqual(resp.status, 200)
        ci = self.ci_mod.get_intent(self.id1)
        self.assertEqual(ci.state, "accepted")
        self.assertEqual(ci.decision_action, "accept")

    def test_post_reject(self):
        import http_routes_admin
        req = self._mock_request("POST", f"/admin/changes/{self.id1}/reject",
                                  match={"id": self.id1, "action": "reject"})
        resp = asyncio.run(http_routes_admin.admin_change_action(req))
        self.assertEqual(resp.status, 200)
        ci = self.ci_mod.get_intent(self.id1)
        self.assertEqual(ci.state, "rejected")

    def test_post_stage_then_accept(self):
        import http_routes_admin
        req = self._mock_request("POST", f"/admin/changes/{self.id1}/stage",
                                  match={"id": self.id1, "action": "stage"})
        resp = asyncio.run(http_routes_admin.admin_change_action(req))
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.ci_mod.get_intent(self.id1).state, "staged")
        req2 = self._mock_request("POST", f"/admin/changes/{self.id1}/accept",
                                  match={"id": self.id1, "action": "accept"})
        resp2 = asyncio.run(http_routes_admin.admin_change_action(req2))
        self.assertEqual(resp2.status, 200)
        self.assertEqual(self.ci_mod.get_intent(self.id1).state, "accepted")

    def test_post_invalid_action(self):
        import http_routes_admin
        req = self._mock_request("POST", f"/admin/changes/{self.id1}/foobar",
                                  match={"id": self.id1, "action": "foobar"})
        resp = asyncio.run(http_routes_admin.admin_change_action(req))
        self.assertEqual(resp.status, 400)

    def test_post_rollback_from_applied(self):
        import http_routes_admin
        # Trans via API: accept → mark_applied
        self.ci_mod.apply_decision(self.id1, action="accept", by="t")
        self.ci_mod.mark_applied(self.id1, effect={"executor_name": "aaa"})
        req = self._mock_request("POST", f"/admin/changes/{self.id1}/rollback",
                                  match={"id": self.id1, "action": "rollback"})
        resp = asyncio.run(http_routes_admin.admin_change_action(req))
        self.assertEqual(resp.status, 200)
        ci = self.ci_mod.get_intent(self.id1)
        self.assertEqual(ci.state, "rolled_back")

    def test_htmx_request_returns_html_fragment(self):
        import http_routes_admin
        req = self._mock_request("POST", f"/admin/changes/{self.id2}/accept",
                                  match={"id": self.id2, "action": "accept"},
                                  headers={"HX-Request": "true"})
        resp = asyncio.run(http_routes_admin.admin_change_action(req))
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "text/html")
        body = resp.body.decode() if isinstance(resp.body, bytes) else resp.text
        self.assertIn("<tr", body)


if __name__ == "__main__":
    unittest.main()
