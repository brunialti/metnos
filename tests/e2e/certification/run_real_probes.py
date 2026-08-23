#!/usr/bin/env python3
"""Run configurable, read-only RM-0006 probes against real boundaries."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _json_request(url: str, *, token: str = "", body: dict | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def _probe(name: str, passed: bool, contract: str, observed: dict[str, Any]) -> dict:
    return {
        "name": name,
        "passed": bool(passed),
        "contract": contract,
        "observed": observed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metnos-url", default="http://127.0.0.1:8770")
    parser.add_argument("--model-url", default="http://127.0.0.1:8080/v1/models")
    parser.add_argument("--site-url", default="https://metnos.com/en/")
    parser.add_argument("--public-repo", type=Path, default=Path("dist/.public-repo"))
    parser.add_argument("--output", type=Path, required=True)
    default_key = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "metnos" / "admin.key"
    parser.add_argument("--admin-key-file", type=Path, default=default_key)
    args = parser.parse_args()

    token = args.admin_key_file.read_text(encoding="utf-8").strip()
    health = _json_request(f"{args.metnos_url.rstrip('/')}/agent/stack/health", token=token)
    turn = _json_request(
        f"{args.metnos_url.rstrip('/')}/agent/turn",
        token=token,
        body={"query": "Che ore sono adesso?", "user_id": "host", "lang": "it"},
    )
    tools = [str(step.get("tool") or "") for step in turn.get("steps_summary", [])]
    models = _json_request(args.model_url)

    site_request = urllib.request.Request(args.site_url, headers={"User-Agent": "metnos-rm0006-probe/1"})
    with urllib.request.urlopen(site_request, timeout=30) as response:
        site_body = response.read(2_000_000)
        site_status = response.status
        site_server = response.headers.get("Server", "")
        site_final_url = response.url

    remote = subprocess.run(
        ["git", "-C", str(args.public_repo), "ls-remote", "--heads", "origin"],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.splitlines()
    refs = [line.split(maxsplit=1) for line in remote if line.strip()]

    probes = [
        _probe(
            "installed_metnos_stack",
            health.get("ok") is True and health.get("ready") is True
            and health.get("http", {}).get("contract_aligned") is True,
            "installed stack is ready and its HTTP/sidecar contracts are aligned",
            {
                "ready": health.get("ready"),
                "quiescent": health.get("quiescent"),
                "catalog_count": health.get("catalog", {}).get("count"),
                "contract_aligned": health.get("http", {}).get("contract_aligned"),
            },
        ),
        _probe(
            "installed_metnos_readonly_turn",
            turn.get("final_kind") == "answer" and tools == ["get_now"]
            and bool(turn.get("final_message")),
            "a real non-mutating turn reaches get_now and returns an answer",
            {"final_kind": turn.get("final_kind"), "tools": tools, "has_message": bool(turn.get("final_message"))},
        ),
        _probe(
            "local_model_catalog",
            models.get("object") == "list" and bool(models.get("data")),
            "the configured local inference service exposes an OpenAI-compatible model list",
            {"object": models.get("object"), "model_count": len(models.get("data") or [])},
        ),
        _probe(
            "cloudflare_public_site",
            site_status == 200 and b"<html" in site_body.lower()
            and site_final_url.startswith("https://"),
            "the public HTTPS site follows redirects and serves an HTML document",
            {"status": site_status, "server": site_server, "final_url": site_final_url},
        ),
        _probe(
            "github_public_remote",
            bool(refs) and all(
                len(parts) == 2 and re.fullmatch(r"[0-9a-f]{40,64}", parts[0])
                and parts[1].startswith("refs/heads/") for parts in refs
            ),
            "the configured public origin exposes at least one immutable branch ref",
            {"branch_count": len(refs), "branches": [parts[1] for parts in refs if len(parts) == 2]},
        ),
    ]
    report = {
        "schema_version": "metnos.real-boundary-probes/1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "non_destructive": True,
        "passed": sum(probe["passed"] for probe in probes),
        "total": len(probes),
        "probes": probes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"]}, sort_keys=True))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
