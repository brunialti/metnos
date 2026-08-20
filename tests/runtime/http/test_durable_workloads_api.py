"""F9 HTTP contract tests for the owner-scoped durable-workload façade."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase


RUNTIME = Path(__file__).resolve().parents[3] / "runtime"
_DURABLE_TESTS = Path(__file__).resolve().parents[1] / "durable_workloads"
if str(_DURABLE_TESTS) not in sys.path:
    sys.path.insert(0, str(_DURABLE_TESTS))
ADMIN_KEY = "test-durable-control-admin-key"


class DurableWorkloadApiTests(AioHTTPTestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        root = Path(cls._tmpdir.name)
        cls._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(root)
        os.environ["METNOS_USERS_DB"] = str(root / "users.db")
        cls._store_path = root / "durable" / "state.sqlite3"

        import http_auth
        import http_routes_admin
        import metnos_http_server
        import users

        importlib.reload(http_auth)
        importlib.reload(users)
        importlib.reload(http_routes_admin)
        importlib.reload(metnos_http_server)
        cls._server = metnos_http_server

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()
        if cls._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = cls._old_home

    async def get_application(self):
        from durable_workloads.storage import DurableWorkloadStore
        from http_app_state import DURABLE_WORKLOAD_STORE_FACTORY

        app = self._server.make_app(admin_key=ADMIN_KEY)
        app[DURABLE_WORKLOAD_STORE_FACTORY] = lambda: DurableWorkloadStore.open(
            self._store_path,
        )
        return app

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {ADMIN_KEY}"}

    def owner(self) -> str:
        import users

        hosts = users.list_users(role="host")
        self.assertEqual(len(hosts), 1)
        return str(hosts[0]["id"])

    def _create_admitted(self, owner: str, number: int):
        from durable_workloads.storage import DurableWorkloadStore
        from helpers import inventory, plan, source

        with DurableWorkloadStore.open(self._store_path) as store:
            draft = store.create_draft(
                owner,
                f"http-request-{number}",
                redacted_request={"summary": "synthetic"},
                workload_id=f"wrk_http_{number:08d}",
            )
            store.admit_revision(
                owner,
                draft.workload_id,
                plan(with_map=True),
                inventory([source(0)]),
                expected_version=draft.version,
                usage_complete=True,
            )
            return store.get_workload(owner, draft.workload_id)

    def _queue(self, owner: str, workload_id: str):
        from durable_workloads.models import WorkloadState
        from durable_workloads.storage import DurableWorkloadStore

        with DurableWorkloadStore.open(self._store_path) as store:
            current = store.get_workload(owner, workload_id)
            return store.transition_workload(
                owner,
                workload_id,
                WorkloadState.QUEUED,
                expected_version=current.version,
            )

    async def test_authentication_and_closed_read_dtos_match_the_facade(self):
        unauthorized = await self.client.get("/agent/workloads")
        self.assertEqual(unauthorized.status, 401)

        owner = self.owner()
        workload = self._create_admitted(owner, 1)
        response = await self.client.get(
            f"/agent/workloads/{workload.workload_id}", headers=self.headers(),
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(set(payload), {"schema_version", "workload", "revision"})
        self.assertNotIn("request_key", payload["workload"])
        self.assertNotIn("plan_json", payload["revision"])

        from durable_workloads.control import DurableWorkloadControl
        from durable_workloads.storage import DurableWorkloadStore

        with DurableWorkloadStore.open(self._store_path) as store:
            direct = DurableWorkloadControl(
                store, cursor_secret=ADMIN_KEY,
            ).detail(owner, workload.workload_id)
        self.assertEqual(payload, direct)

    async def test_routes_enforce_owner_scope_pagination_and_body_authority(self):
        owner = self.owner()
        own = self._create_admitted(owner, 11)
        foreign = self._create_admitted("foreign-owner", 12)

        for suffix in ("", "/events", "/units"):
            response = await self.client.get(
                f"/agent/workloads/{foreign.workload_id}{suffix}",
                headers=self.headers(),
            )
            self.assertEqual(response.status, 404)
            self.assertEqual((await response.json())["error"]["code"], "durable_workload.not_found")
        denied_command = await self.client.post(
            f"/agent/workloads/{foreign.workload_id}/cancel",
            headers=self.headers(),
            json={"expected_version": foreign.version, "idempotency_key": "foreign-cancel"},
        )
        self.assertEqual(denied_command.status, 404)

        too_large = await self.client.get(
            "/agent/workloads?limit=101", headers=self.headers(),
        )
        self.assertEqual(too_large.status, 400)
        self.assertEqual((await too_large.json())["error"]["code"], "durable_workload.invalid_limit")
        invalid_cursor = await self.client.get(
            "/agent/workloads?cursor=not-a-cursor", headers=self.headers(),
        )
        self.assertEqual(invalid_cursor.status, 400)
        self.assertEqual((await invalid_cursor.json())["error"]["code"], "durable_workload.invalid_cursor")

        queued = self._queue(owner, own.workload_id)
        rejected_owner = await self.client.post(
            f"/agent/workloads/{own.workload_id}/pause",
            headers=self.headers(),
            json={
                "owner_user_id": "foreign-owner",
                "expected_version": queued.version,
                "idempotency_key": "rejected-owner-field",
            },
        )
        self.assertEqual(rejected_owner.status, 400)
        self.assertEqual(
            (await rejected_owner.json())["error"]["code"],
            "durable_workload.owner_in_body_rejected",
        )

    async def test_commands_are_idempotent_versioned_and_safe_under_concurrency(self):
        owner = self.owner()
        workload = self._create_admitted(owner, 21)
        queued = self._queue(owner, workload.workload_id)
        path = f"/agent/workloads/{workload.workload_id}/pause"
        body = {"expected_version": queued.version, "idempotency_key": "pause-http-21"}
        first = await self.client.post(path, headers=self.headers(), json=body)
        self.assertEqual(first.status, 200)
        first_payload = await first.json()
        replay = await self.client.post(path, headers=self.headers(), json=body)
        self.assertEqual(replay.status, 200)
        self.assertEqual(await replay.json(), first_payload)

        stale = await self.client.post(
            f"/agent/workloads/{workload.workload_id}/resume",
            headers=self.headers(),
            json={"expected_version": queued.version, "idempotency_key": "resume-stale-http"},
        )
        self.assertEqual(stale.status, 409)
        self.assertEqual((await stale.json())["error"]["code"], "durable_workload.version_conflict")

        resumed = await self.client.post(
            f"/agent/workloads/{workload.workload_id}/resume",
            headers=self.headers(),
            json={
                "expected_version": first_payload["workload"]["version"],
                "idempotency_key": "resume-http-21",
            },
        )
        self.assertEqual(resumed.status, 200)
        current_version = (await resumed.json())["workload"]["version"]
        cancel_path = f"/agent/workloads/{workload.workload_id}/cancel"
        left, right = await asyncio.gather(
            self.client.post(
                cancel_path,
                headers=self.headers(),
                json={"expected_version": current_version, "idempotency_key": "cancel-left"},
            ),
            self.client.post(
                cancel_path,
                headers=self.headers(),
                json={"expected_version": current_version, "idempotency_key": "cancel-right"},
            ),
        )
        self.assertEqual(sorted((left.status, right.status)), [200, 409])

    async def test_error_message_is_localized_while_the_api_code_stays_stable(self):
        import users

        owner = self.owner()
        users.set_pref(owner, "lang", "it")
        italian = await self.client.get("/agent/workloads?limit=101", headers=self.headers())
        italian_payload = await italian.json()
        users.set_pref(owner, "lang", "en")
        english = await self.client.get("/agent/workloads?limit=101", headers=self.headers())
        english_payload = await english.json()
        self.assertEqual(italian_payload["error"]["code"], english_payload["error"]["code"])
        self.assertEqual(italian_payload["error"]["message_code"], "ERR_OP_FAILED")
        self.assertIn("Operazione fallita", italian_payload["error"]["message"])
        self.assertIn("Operation failed", english_payload["error"]["message"])


def test_durable_workload_routes_contain_no_sql_or_generic_action_route():
    source = (RUNTIME / "http_routes_durable_workloads.py").read_text(encoding="utf-8")
    for token in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", ".execute("):
        assert token not in source
    assert "action=<" not in source
