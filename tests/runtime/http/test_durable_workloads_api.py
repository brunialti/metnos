"""F9 HTTP contract tests for the owner-scoped durable-workload façade."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import ClientSession
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
        cls._artifact_root = root / "artifacts"

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
        from durable_workloads.artifacts import (
            ArtifactDownloadRegistry,
            ArtifactRepository,
            ArtifactStore,
        )
        from durable_workloads.storage import DurableWorkloadStore
        from http_app_state import (
            DURABLE_ARTIFACT_DOWNLOADS,
            DURABLE_ARTIFACT_STORE_FACTORY,
            DURABLE_WORKLOAD_STORE_FACTORY,
        )

        app = self._server.make_app(admin_key=ADMIN_KEY)
        app[DURABLE_WORKLOAD_STORE_FACTORY] = lambda: DurableWorkloadStore.open(
            self._store_path,
        )
        app[DURABLE_ARTIFACT_STORE_FACTORY] = lambda: ArtifactStore(
            self._artifact_root,
            ArtifactRepository.open(self._store_path),
        )
        self._download_registry = ArtifactDownloadRegistry()
        app[DURABLE_ARTIFACT_DOWNLOADS] = self._download_registry
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

    def _create_artifact(self, owner: str, workload, artifact_id: str):
        from durable_workloads.artifacts import ArtifactRepository, ArtifactStore

        repository = ArtifactRepository.open(self._store_path)
        artifacts = ArtifactStore(self._artifact_root, repository)
        try:
            return artifacts.commit(
                owner,
                workload.workload_id,
                workload.active_revision_id,
                "durable-report.txt",
                "text/plain",
                "metnos.test-artifact/1",
                b"durable download bytes",
                artifact_id=artifact_id,
            )
        finally:
            artifacts.close()

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
        self.assertIn("execution", payload["revision"])
        self.assertNotIn("objective_redacted", payload["revision"])

        from durable_workloads.control import DurableWorkloadControl
        from durable_runtime_registry import describe_plan
        from durable_workloads.coordinator import parse_instant
        from durable_workloads.storage import DurableWorkloadStore

        # Compare identical observation instants: the DTO now timestamps its
        # progress read so browsers can suppress expired estimates.
        observed_at = payload["workload"]["progress"]["observed_at"]
        with DurableWorkloadStore.open(self._store_path) as store, mock.patch.object(
            DurableWorkloadStore, "_operation_now",
            return_value=(parse_instant(observed_at), observed_at),
        ):
            direct = DurableWorkloadControl(
                store, cursor_secret=ADMIN_KEY, describe_plan=describe_plan,
            ).detail(owner, workload.workload_id)
        self.assertEqual(payload, direct)

    async def test_interactive_http_turn_is_unchanged_with_lre_off_and_on(self):
        import http_routes_agent as routes

        observed = []

        class TurnLog:
            turn_id = "turn-f12-http"
            final_message = "F12 HTTP gate ok"
            final_kind = "answer"
            ts_start = 0.0
            ts_end = 0.01
            steps = []
            expandable_caps = []
            attachments = []

        def run_turn(query, **kwargs):
            observed.append((
                os.environ.get("METNOS_DURABLE_WORKLOADS_ENABLED"),
                query,
                kwargs["channel"],
            ))
            return TurnLog()

        with mock.patch.object(
            routes,
            "_apply_tutor_http",
            new=mock.AsyncMock(return_value=None),
        ), mock.patch.object(
            routes, "_save_cap_pending_if_any",
        ), mock.patch.object(
            routes, "_gallery_url_for", return_value=(None, 0),
        ), mock.patch.object(
            routes, "_enrich_attachments", return_value=[],
        ), mock.patch("agent_runtime.run_turn", side_effect=run_turn):
            payloads = []
            for enabled in ("0", "1"):
                with mock.patch.dict(
                    os.environ,
                    {"METNOS_DURABLE_WORKLOADS_ENABLED": enabled},
                ):
                    response = await self.client.post(
                        "/agent/turn",
                        headers=self.headers(),
                        json={
                            "query": "F12 interactive probe",
                            "conversation_id": f"f12-http-{enabled}",
                        },
                    )
                    self.assertEqual(response.status, 200)
                    payloads.append(await response.json())

        self.assertEqual(
            observed,
            [
                ("0", "F12 interactive probe", "http"),
                ("1", "F12 interactive probe", "http"),
            ],
        )
        self.assertEqual(
            [payload["final_message"] for payload in payloads],
            ["F12 HTTP gate ok", "F12 HTTP gate ok"],
        )

    async def test_html_control_surface_uses_the_same_api_without_page_local_css(self):
        import users

        users.set_pref(self.owner(), "lang", "it")
        login = await self.client.get(
            "/admin/lre", headers={"Accept": "text/html"},
        )
        self.assertEqual(login.status, 200)
        login_html = await login.text()
        self.assertIn('action="/admin/login"', login_html)
        self.assertIn('name="next" value="/admin/lre"', login_html)
        navigable = await self.client.get(
            "/admin/lre",
            headers={**self.headers(), "Accept": "text/html"},
        )
        self.assertEqual(navigable.status, 200)
        navigable_html = await navigable.text()
        self.assertIn('href="/admin/lre"', navigable_html)
        self.assertIn('id="durableWorkloads"', navigable_html)

        response = await self.client.get(
            "/agent/workloads",
            headers={**self.headers(), "Accept": "text/html"},
        )
        self.assertEqual(response.status, 200)
        html = await response.text()
        self.assertIn('id="durableWorkloads"', html)
        self.assertIn("LRE (Long Run Engine)", html)
        # Before the first API response, absence of data is not an empty list.
        placeholder = html.split('id="dwPlaceholder">', 1)[1].split("</div>", 1)[0]
        self.assertNotIn("Non ci sono attività LRE da mostrare.", placeholder)
        self.assertIn("Caricamento", placeholder)
        self.assertIn("La politica di esecuzione ammette risultati parziali.", html)
        self.assertNotIn("La policy", html)
        self.assertIn("Errore non classificato", html)
        self.assertIn('id="dwEngine"', html)
        self.assertIn('id="dwFreshness"', html)
        self.assertIn("Batch completati", html)
        self.assertIn("Fase {number}/{total}", html)
        self.assertIn("Avanzamento di questa fase", html)
        self.assertIn("Contabilizzazione dei consumi incompleta", html)
        self.assertIn("Dettagli tecnici", html)
        template = (RUNTIME / "templates" / "durable_workloads.html").read_text("utf-8")
        self.assertNotIn("<style", template)
        self.assertIn("EventSource", template)

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
        rejected_unknown = await self.client.post(
            f"/agent/workloads/{own.workload_id}/pause",
            headers=self.headers(),
            json={
                "expected_version": queued.version,
                "idempotency_key": "rejected-unknown-field",
                "unexpected": True,
            },
        )
        self.assertEqual(rejected_unknown.status, 400)
        self.assertEqual(
            (await rejected_unknown.json())["error"]["code"],
            "durable_workload.invalid_request",
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

    async def test_command_json_body_has_its_own_small_boundary(self):
        owner = self.owner()
        workload = self._create_admitted(owner, 22)
        queued = self._queue(owner, workload.workload_id)
        response = await self.client.post(
            f"/agent/workloads/{workload.workload_id}/pause",
            headers={**self.headers(), "Content-Type": "application/json"},
            data=json.dumps({
                "expected_version": queued.version,
                "idempotency_key": "x" * 5000,
            }),
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(
            (await response.json())["error"]["code"],
            "durable_workload.invalid_request",
        )

    async def test_attention_resolution_uses_closed_owner_scoped_routes(self):
        from durable_workloads.models import WorkloadState
        from durable_workloads.storage import DurableWorkloadStore

        owner = self.owner()
        workload = self._create_admitted(owner, 31)
        with DurableWorkloadStore.open(self._store_path) as store:
            attention = store.transition_workload(
                owner,
                workload.workload_id,
                WorkloadState.NEEDS_ATTENTION,
                expected_version=workload.version,
            )
        response = await self.client.post(
            f"/agent/workloads/{workload.workload_id}/attention/retry",
            headers=self.headers(),
            json={
                "expected_version": attention.version,
                "idempotency_key": "http-attention-retry-31",
            },
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["command"], "resolve_attention")
        self.assertEqual(payload["decision"], "retry")
        self.assertEqual(payload["workload"]["state"], "queued")

    async def test_sse_replays_persistent_events_from_last_event_id_owner_scoped(self):
        owner = self.owner()
        workload = self._create_admitted(owner, 41)
        response = await self.client.get(
            f"/agent/workloads/{workload.workload_id}/stream",
            headers={**self.headers(), "Last-Event-ID": "1"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "text/event-stream")
        event_id = (await response.content.readline()).decode("utf-8").strip()
        event_name = (await response.content.readline()).decode("utf-8").strip()
        data_line = (await response.content.readline()).decode("utf-8").strip()
        self.assertEqual(event_id, "id: 2")
        self.assertEqual(event_name, "event: workload")
        data = json.loads(data_line.removeprefix("data: "))
        self.assertEqual(data["event"]["event_id"], 2)
        self.assertNotIn("payload_json", data["event"])
        response.close()

        recent = await self.client.get(
            f"/agent/workloads/{workload.workload_id}/events?recent=1&limit=1",
            headers=self.headers(),
        )
        self.assertEqual(recent.status, 200)
        self.assertEqual((await recent.json())["items"][-1]["event_id"], 2)

        foreign = self._create_admitted("foreign-sse-owner", 42)
        denied = await self.client.get(
            f"/agent/workloads/{foreign.workload_id}/stream",
            headers=self.headers(),
        )
        self.assertEqual(denied.status, 404)
        invalid = await self.client.get(
            f"/agent/workloads/{workload.workload_id}/stream",
            headers={**self.headers(), "Last-Event-ID": "not-an-id"},
        )
        self.assertEqual(invalid.status, 400)

    async def test_sse_connections_are_bounded_per_owner(self):
        import http_routes_durable_workloads as routes

        owner = self.owner()
        workload = self._create_admitted(owner, 43)
        previous = routes._SSE_MAX_PER_OWNER
        routes._SSE_MAX_PER_OWNER = 1
        first = None
        try:
            first = await self.client.get(
                f"/agent/workloads/{workload.workload_id}/stream",
                headers={**self.headers(), "Last-Event-ID": "1"},
            )
            self.assertEqual(first.status, 200)
            second = await self.client.get(
                f"/agent/workloads/{workload.workload_id}/stream",
                headers={**self.headers(), "Last-Event-ID": "1"},
            )
            self.assertEqual(second.status, 429)
            self.assertEqual(
                (await second.json())["error"]["code"],
                "durable_workload.stream_limit",
            )
        finally:
            routes._SSE_MAX_PER_OWNER = previous
            if first is not None:
                first.close()

    async def test_artifact_download_uses_an_expiring_revocable_registry_capability(self):
        owner = self.owner()
        workload = self._create_admitted(owner, 51)
        artifact = self._create_artifact(owner, workload, "artifact_http_000051")
        issued = await self.client.post(
            f"/agent/workloads/{workload.workload_id}/artifacts/{artifact.artifact_id}/download",
            headers=self.headers(),
        )
        self.assertEqual(issued.status, 200)
        capability = await issued.json()
        self.assertNotIn("path", capability)
        download = await self.client.get(capability["download_url"], headers=self.headers())
        self.assertEqual(download.status, 200)
        self.assertEqual(await download.read(), b"durable download bytes")
        async with ClientSession() as fresh_session:
            fresh_download = await fresh_session.get(
                self.server.make_url(capability["download_url"]),
                headers=self.headers(),
            )
            self.assertEqual(fresh_download.status, 200)
            self.assertEqual(
                await fresh_download.read(),
                b"durable download bytes",
            )
        token = capability["download_url"].rsplit("/", 1)[-1]
        self.assertTrue(self._download_registry.revoke(token))
        revoked = await self.client.get(capability["download_url"], headers=self.headers())
        self.assertEqual(revoked.status, 404)

        foreign_workload = self._create_admitted("foreign-artifact-owner", 52)
        foreign = self._create_artifact(
            "foreign-artifact-owner", foreign_workload, "artifact_http_000052",
        )
        denied = await self.client.post(
            f"/agent/workloads/{foreign_workload.workload_id}/artifacts/{foreign.artifact_id}/download",
            headers=self.headers(),
        )
        self.assertEqual(denied.status, 404)

    async def test_user_preference_cannot_override_instance_error_language(self):
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
        self.assertIn("Operazione fallita", english_payload["error"]["message"])


def test_durable_workload_routes_contain_no_sql_or_generic_action_route():
    source = (RUNTIME / "http_routes_durable_workloads.py").read_text(encoding="utf-8")
    for token in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", ".execute("):
        assert token not in source
    assert "action=<" not in source
