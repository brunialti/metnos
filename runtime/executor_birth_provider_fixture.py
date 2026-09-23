"""Private provider protocol model for Birth; never contacts a live service.

The observer owns the state. Candidate and inverse backends use the normal
bridge contract through a local socket. Issue undo closes the created issue;
its history is retained, so the state oracle measures open issues, not erasure.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket

from _metnos_birth_helper_model_v1 import ManagedHelperFixture


class ProviderObjectsFixture(ManagedHelperFixture):
    def __init__(self, directory):
        super().__init__(directory, ["fixture"])
        self.objects = {
            (area, repo, 7): {"id": 9007, "number": 7, "state": "open", "body": "keep"}
            for area in ("issues", "comments") for repo in ("birth/fixture", "birth/other")
        }
        self.serial = 100

    def digest(self):
        with self.lock:
            rows = [[list(key), value] for key, value in sorted(self.objects.items())
                    if key[0] != "issues" or value["state"] == "open"]
            payload = json.dumps(rows, sort_keys=True).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def command(self, argv):
        failure = {"ok": False, "error": "provider_fixture_request_invalid"}
        if (not isinstance(argv, list) or len(argv) < 2 or len(argv) % 2
                or any(not isinstance(item, str) for item in argv)):
            return failure
        area, operation, *flags = argv
        options = dict(zip(flags[::2], flags[1::2]))
        if len(options) != len(flags) // 2 or options.get("--repo") != "birth/fixture":
            return failure
        repo = options["--repo"]
        with self.lock:
            if (area, operation) in (("issues", "create"), ("comments", "send")):
                required = ("--title",) if area == "issues" else ("--target", "--body")
                if any(not options.get(key) for key in required):
                    return failure
                if area == "comments" and options["--target"] not in ("issue:7", "pr:8", "issue:9"):
                    return failure
                self.serial += 1
                row = {"id": self.serial + 9000, "number": self.serial,
                       "state": "open", "body": options.get("--body", ""),
                       "title": options.get("--title", "")}
                identifier = row["number"] if area == "issues" else row["id"]
                self.objects[area, repo, identifier] = row
                return {"results": [dict(row)]}
            if area not in ("issues", "comments") or operation != "delete":
                return failure
            try:
                identifier = int(options["--number" if area == "issues" else "--comment-id"])
            except (KeyError, ValueError):
                return failure
            key = area, repo, identifier
            if identifier == 7 or key not in self.objects:
                return failure
            if area == "issues":
                self.objects[key]["state"] = "closed"
                return {"results": [dict(self.objects[key])]}
            del self.objects[key]
            return {"results": [{"id": identifier, "status": "deleted"}]}


def configure_bridge():
    """Replace only the external protocol endpoint in the isolated child."""
    from backends import _github_bridge

    def run_api(_script, argv, **_kwargs):
        address = Path(__file__).resolve().parent.parent / "helper.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2.0)
            connection.connect(str(address))
            connection.sendall(json.dumps(argv).encode() + b"\n")
            with connection.makefile("rb") as stream:
                reply = stream.readline(65537)
        if not reply or len(reply) > 65536:
            raise RuntimeError("provider_fixture_response_invalid")
        return 0, reply.decode(), ""

    _github_bridge.bridge = lambda: (Path("fixture"), run_api, lambda *_: "server_error")


def reverse(patterns, results):
    """Exercise the real call builder and inverse backend, without a live catalog."""
    from backends import load
    from reverse_patterns_patch import build_undo_calls

    if len(patterns) != 1:
        return {"ok": False}
    calls, error = build_undo_calls(patterns[0], results)
    if error or not calls:
        return {"ok": False}
    for call in calls:
        verb, area = call["executor"].split("_", 1)
        if verb != "delete" or area not in ("comments", "issues"):
            return {"ok": False}
        backend = load(area, call["args"].get("client", "github"))
        result = getattr(backend, call["executor"])(call["args"])
        if result.get("ok") is not True:
            return {"ok": False}
    return {"ok": True}
