"""Isolated HTTP E2E for the ``organize_files`` frozen-plan transaction.

The scenario deliberately crosses the public turn and dialog-form boundaries.
It never talks to a live Metnos service or to an external model endpoint.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import E2EClient, E2EServer


pytestmark = pytest.mark.asyncio

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORM_MARKER = re.compile(
    r"INLINE_FORM:(?P<path>/agent/dialog/"
    r"(?P<dialog>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})/form\?cap=[^\s<]+)"
)


class _DeterministicModel(ThreadingHTTPServer):
    """Minimal OpenAI-compatible model endpoint, bound to loopback only."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _DeterministicModelHandler)
        self.plan_args: dict = {}
        self.requests: list[dict] = []
        self.lock = threading.Lock()


class _DeterministicModelHandler(BaseHTTPRequestHandler):
    server: _DeterministicModel

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        with self.server.lock:
            self.server.requests.append(payload)
        grammar = str(payload.get("grammar") or "")
        if grammar and '\\"steps\\"' in grammar:
            # Select only a tool the runtime exposed for this turn.  This is
            # retry-safe: call ordering is irrelevant.  The built-in undo is
            # currently also present in the initial effective pool, so bind
            # it to the isolated per-query tail, not to examples in SYSTEM.
            messages = payload.get("messages") or []
            user_messages = [
                str(message.get("content") or "")
                for message in messages
                if isinstance(message, dict) and message.get("role") == "user"
            ]
            per_query = user_messages[-1] if user_messages else ""
            # The split prompt's variable tail ends with the exact query in
            # every locale.  Matching the suffix keeps examples earlier in
            # the same message from influencing this deterministic fixture.
            wants_undo = per_query.strip().casefold().endswith(
                "annulla l'ultima azione")
            if wants_undo and '"\\"undo_last_turn\\""' in grammar:
                tool, args = "undo_last_turn", {}
            else:
                assert '"\\"organize_files\\""' in grammar, grammar[:1000]
                tool, args = "organize_files", self.server.plan_args
            content = json.dumps({
                "steps": [
                    {"tool": tool, "args": args},
                    {"tool": "final_answer", "args": {}},
                ],
                "fillers": {},
                "final_message": "",
            }, separators=(",", ":"))
        else:
            content = json.dumps({
                "kind": "action",
                "verb": "organize",
                "object": "files",
                "keywords": ["file"],
                "confidence": 1.0,
            }, separators=(",", ":"))
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


@dataclass
class _OrganizeHarness:
    server: E2EServer
    model: _DeterministicModel
    roots: dict[str, Path]


def _write_tier_config(path: Path, endpoint: str) -> None:
    sections = []
    for tier in ("fast", "middle", "wise", "creative"):
        sections.append(
            f'[{tier}]\n'
            'provider = "llamacpp"\n'
            'model = "organize-e2e"\n'
            f'endpoint = "{endpoint}"\n'
            'think = false\n'
            'temperature = 0.0\n'
            'reasoning_budget = 0\n'
        )
    path.write_text("\n".join(sections), encoding="utf-8")


@pytest.fixture(scope="module")
def organize_harness() -> _OrganizeHarness:
    model = _DeterministicModel()
    model_thread = threading.Thread(target=model.serve_forever, daemon=True)
    model_thread.start()
    roots: dict[str, Path] = {}

    def configure(env: dict[str, str], tmp_root: Path) -> None:
        fixture = tmp_root / "organize-fixture"
        source = fixture / "source"
        compare = fixture / "compare"
        destination = fixture / "destination"
        for directory in (source, compare, destination):
            directory.mkdir(parents=True)
        # organize_files deliberately refuses to create unapproved directory
        # components during apply; make the year bucket part of the fixture's
        # pre-existing destination authority.
        (destination / "2024").mkdir()

        (compare / "kept.bin").write_bytes(b"duplicate payload\x00")
        (source / "duplicate.bin").write_bytes(b"duplicate payload\x00")
        (source / "20240102_alpha.txt").write_bytes(b"alpha\n")
        (source / "20240103_beta.txt").write_bytes(b"beta\x00payload")
        (source / "20240104_gamma.txt").write_bytes(b"gamma\n")
        roots.update(source=source, compare=compare, destination=destination)

        model.plan_args = {
            "mode": "preview",
            "source_paths": [str(source)],
            "compare_paths": [str(compare)],
            "destination_roots": [str(destination)],
            "operations": [
                {
                    "type": "deduplicate",
                    "match": "sha256",
                    "keep": "outside_sources_or_first",
                },
                {
                    "type": "move",
                    "destination_root": str(destination),
                    "path_template": "{capture_date.year}/{name}",
                    "on_missing": "fail",
                    "on_conflict": "fail",
                },
            ],
            # The public form shows one row, but apply must execute all four.
            "max_preview": 1,
        }
        tiers = tmp_root / "llm-tiers.toml"
        _write_tier_config(
            tiers, f"http://127.0.0.1:{model.server_address[1]}")
        env.update({
            "METNOS_LLM_TIERS_CONFIG": str(tiers),
            "METNOS_ENGINE": "simple",
            "METNOS_FASTPATH": "0",
            "METNOS_AUTOPATH": "0",
            "METNOS_PROPOSER_GRAMMAR": "1",
            "METNOS_PROPOSER_GRAMMAR_ARGS": "0",
            "METNOS_PROPOSER_VERB_FILTER": "1",
            "METNOS_INTENT_SCAFFOLD": "0",
            "METNOS_TUTOR": "0",
        })

    try:
        server = E2EServer.spawn(
            seed_realistic=False, ready_timeout_s=45.0,
            pre_spawn_hook=configure)
    except BaseException:
        model.shutdown()
        model.server_close()
        model_thread.join(timeout=5)
        raise
    try:
        yield _OrganizeHarness(server=server, model=model, roots=roots)
    finally:
        server.shutdown(cleanup=True)
        model.shutdown()
        model.server_close()
        model_thread.join(timeout=5)


@pytest_asyncio.fixture
async def organize_driver(organize_harness: _OrganizeHarness):
    server = organize_harness.server
    async with E2EClient(server.url, server.admin_key, timeout_s=120.0) as driver:
        yield driver


def _snapshot_files(roots: dict[str, Path]) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for label, root in sorted(roots.items()):
        for path in sorted(root.rglob("*")):
            if path.is_file():
                snapshot[f"{label}/{path.relative_to(root)}"] = path.read_bytes()
    return snapshot


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _assert_secret_absent(secret: str, *values: object) -> None:
    assert secret and len(secret) == 64
    for value in values:
        if isinstance(value, bytes):
            haystack = value.decode("utf-8", errors="replace")
        elif isinstance(value, str):
            haystack = value
        else:
            haystack = json.dumps(value, ensure_ascii=False, default=str)
        assert secret not in haystack, "frozen-plan bearer leaked through a public surface"


def _dialog_state(server: E2EServer, dialog_id: str) -> tuple[Path, dict]:
    matches = list((server.user_data / "get_inputs").rglob(f"{dialog_id}.json"))
    assert len(matches) == 1, "expected one isolated pending dialog"
    return matches[0], json.loads(matches[0].read_text(encoding="utf-8"))


def _run_isolated_python(
    server: E2EServer,
    program: str,
    payload: dict,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict:
    env = dict(server.runtime_env)
    env.update(extra_env or {})
    completed = subprocess.run(
        [sys.executable, "-c", program],
        input=json.dumps(payload), text=True, capture_output=True,
        cwd=_REPO_ROOT, env=env, timeout=45, check=False)
    assert completed.returncode == 0, completed.stderr[-1200:]
    return json.loads(completed.stdout)


def _check_authorization_boundary(server: E2EServer, state: dict) -> dict:
    """Companion check: public forms cannot override these immutable fields."""
    program = r'''
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd() / "runtime"))
import agent_runtime
import loader
from frozen_plan_consent import grant, grant_environment

p = json.load(sys.stdin)
record = p["record"]
owner, actor, channel, turn = (p[k] for k in ("owner", "actor", "channel", "turn"))
executor = loader.load_catalog(verify=True, include_synth=False).executors["organize_files"]
args = dict(record["args"])
args.update(_actor=actor, _channel=channel, _turn_id=turn)
args = agent_runtime.finalize_local_executor_args(executor, args)
grant_record = dict(record)
grant_record["args"] = dict(args)

def allowed(candidate, candidate_owner=owner, candidate_actor=actor,
            candidate_channel=channel, candidate_turn=turn):
    return bool(grant_environment(
        executor, candidate, owner_user_id=candidate_owner,
        actor=candidate_actor, channel=candidate_channel, turn_id=candidate_turn))

with grant(grant_record, owner_user_id=owner, actor=actor,
           channel=channel, turn_id=turn):
    checks = {
        "exact": allowed(args),
        "owner": allowed(args, candidate_owner=owner + "-other"),
        "actor": allowed(args, candidate_actor=actor + "-other"),
        "channel": allowed(args, candidate_channel=channel + "-other"),
        "turn": allowed(args, candidate_turn=turn + "-other"),
        "args": allowed({**args, "source_paths": []}),
        "token": allowed({**args, "confirmation_token": "b" * 64}),
    }
checks["outside"] = allowed(args)
print(json.dumps(checks, sort_keys=True))
'''
    callback = state["on_complete"]
    return _run_isolated_python(server, program, {
        "record": callback["record"],
        "owner": state["owner_user_id"],
        "actor": state["actor"],
        "channel": state["channel"],
        "turn": callback["turn_id"],
    })


def _telegram_markup(server: E2EServer, proposal: dict, state: dict) -> dict:
    program = r'''
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd() / "runtime"))
from channels.inline_ui import keyboard_for_proposal
from channels.telegram_markup import inline_keyboard

p = json.load(sys.stdin)
buttons, preview = keyboard_for_proposal(
    p["proposal"], sender_candidates=[p["sender"]],
    owner_user_id=p["owner"])
print(json.dumps({"markup": inline_keyboard(buttons), "preview": preview}))
'''
    return _run_isolated_python(
        server, program,
        {"proposal": proposal, "sender": state["__sender_id"],
         "owner": state["owner_user_id"]},
        extra_env={"METNOS_PUBLIC_ORIGIN": "https://metnos.example"},
    )


async def test_organize_files_preview_apply_replay_and_public_undo(
    organize_harness: _OrganizeHarness,
    organize_driver: E2EClient,
) -> None:
    """Real turn → form → apply → receipt → replay → public undo."""
    server = organize_harness.server
    roots = organize_harness.roots
    original = _snapshot_files(roots)

    preview = await organize_driver.chat(
        f"Deduplica e organizza i file in {roots['source']} per anno, "
        f"confrontandoli con {roots['compare']}; prepara solo l'anteprima."
    )
    assert preview.error is None
    marker = _FORM_MARKER.search(preview.final_text)
    with organize_harness.model.lock:
        preview_requests = list(organize_harness.model.requests)
    assert marker is not None, {
        "final_text": preview.final_text,
        "grammars": [[line for line in
                      str(item.get("grammar") or "").splitlines()
                      if "toolName ::=" in line]
                     for item in preview_requests],
    }
    form_path = marker.group("path").rstrip(".,;)")
    dialog_id = marker.group("dialog")
    proposals = preview.raw.get("expandable_caps") or []
    assert proposals and proposals[0].get("dialog_id") == dialog_id
    state_path, state = _dialog_state(server, dialog_id)
    state["__sender_id"] = state_path.parent.name
    record = state["on_complete"]["record"]
    token = record["token"]
    consent = record["consent_preview"]
    assert consent["shown_count"] == 1
    assert consent["returned_count"] == 1
    assert consent["total_count"] == 4
    assert consent["truncated"] is True
    assert consent["counts"]["duplicate_count"] == 1
    assert consent["counts"]["move_count"] == 3
    assert _snapshot_files(roots) == original

    boundary = _check_authorization_boundary(server, state)
    assert boundary == {
        "actor": False,
        "args": False,
        "channel": False,
        "exact": True,
        "outside": False,
        "owner": False,
        "token": False,
        "turn": False,
    }

    telegram = _telegram_markup(server, proposals[0], state)
    assert telegram["preview"] is None
    buttons = telegram["markup"]["inline_keyboard"]
    assert len(buttons) == 1 and len(buttons[0]) == 1
    button = buttons[0][0]
    parsed_button = urlsplit(button["url"])
    assert parsed_button.scheme == "https"
    assert parsed_button.netloc == "metnos.example"
    assert parsed_button.path == f"/agent/dialog/{dialog_id}/form"
    assert parse_qs(parsed_button.query).get("cap")
    assert "callback_data" not in json.dumps(telegram)

    async with organize_driver.session.get(
        server.url + form_path, headers={"Accept": "text/html"}) as response:
        form_html = await response.text()
        assert response.status == 200
    assert f"/agent/dialog/{dialog_id}/submit" in form_html
    _assert_secret_absent(token, preview.raw, form_html, telegram, boundary)

    undo_path = server.user_data / "undo.jsonl"
    undo_before_apply = _read_jsonl(undo_path)
    submit_path = form_path.replace("/form?", "/submit?")
    async with organize_driver.session.post(
        server.url + submit_path,
        data={"decision": "apply"},
        headers={"Accept": "text/html"},
    ) as response:
        apply_html = await response.text()
        apply_headers = dict(response.headers)
        assert response.status == 200
    assert "data-completion-text=" in apply_html
    applied = _snapshot_files(roots)
    assert "source/duplicate.bin" not in applied
    assert all(f"source/2024010{day}_{name}.txt" not in applied for day, name in (
        (2, "alpha"), (3, "beta"), (4, "gamma")))
    assert applied["compare/kept.bin"] == original["compare/kept.bin"]
    assert applied["destination/2024/20240102_alpha.txt"] == b"alpha\n"
    assert applied["destination/2024/20240103_beta.txt"] == b"beta\x00payload"
    assert applied["destination/2024/20240104_gamma.txt"] == b"gamma\n"

    undo_after_apply = undo_path.read_bytes()
    apply_records = _read_jsonl(undo_path)[len(undo_before_apply):]
    assert [row.get("type") for row in apply_records] == ["pending", "done"]
    assert apply_records[0].get("executor") == "organize_files"
    assert len({row.get("op_id") for row in apply_records}) == 1
    apply_op_id = apply_records[0]["op_id"]

    # A repeated POST is a terminal replay, not a second filesystem mutation.
    async with organize_driver.session.post(
        server.url + submit_path,
        data={"decision": "apply"},
        headers={"Accept": "text/html"},
    ) as response:
        replay_html = await response.text()
        replay_headers = dict(response.headers)
        assert response.status == 200
    assert replay_headers.get("X-Metnos-Dialog-State") == "completed"
    assert _snapshot_files(roots) == applied
    assert undo_path.read_bytes() == undo_after_apply

    with organize_harness.model.lock:
        requests_before_undo = len(organize_harness.model.requests)
    undo = await organize_driver.chat("annulla l'ultima azione")
    assert undo.error is None
    with organize_harness.model.lock:
        requests_after_undo = len(organize_harness.model.requests)
    assert requests_after_undo == requests_before_undo
    assert _snapshot_files(roots) == original
    final_undo_records = _read_jsonl(undo_path)[len(undo_before_apply):]
    assert [row.get("type") for row in final_undo_records] == [
        "pending", "done", "undone"]
    assert {row.get("op_id") for row in final_undo_records} == {apply_op_id}

    turn_logs = "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (server.user_data / "turns").rglob("*.jsonl")
    )
    server_log = (server.tmp_root / "server.log").read_text(
        encoding="utf-8", errors="replace")
    _assert_secret_absent(
        token, preview.raw, form_html, apply_html, apply_headers,
        replay_html, replay_headers, undo.raw, turn_logs, server_log,
        undo_path.read_bytes(),
    )

    with organize_harness.model.lock:
        requests = list(organize_harness.model.requests)
    assert any("organize_files" in str(request.get("grammar") or "")
               for request in requests)
