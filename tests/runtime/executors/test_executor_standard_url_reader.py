"""Authority, input and routing gates for standardized ``read_urls_html``."""
from __future__ import annotations

import json
import sys
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
EXECUTOR_DIR = ROOT / "executors" / "read_urls_html"
GET_EXECUTOR_DIR = ROOT / "executors" / "get_urls"
for path in (ROOT, RUNTIME, EXECUTOR_DIR, GET_EXECUTOR_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import read_urls_html  # noqa: E402
import get_urls  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


MANIFEST = EXECUTOR_DIR / "manifest.toml"
GET_MANIFEST = GET_EXECUTOR_DIR / "manifest.toml"


def _catalog() -> Catalog:
    value = Catalog()
    _load_dir_into_catalog(ROOT / "executors", value, False,
                           is_synthesized=False)
    return value


def test_manifest_declares_network_and_exact_optional_cookie_authority() -> None:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["placement"] == {"scope": "server", "device_ok": False}
    assert manifest["capabilities"] == [
        {"name": "network:http", "hint": ["http://*", "https://*"]},
        {"name": "fs:read", "hint": ["arg:auth_cookies_file"]},
    ]
    cookie_arg = manifest["args"]["properties"]["auth_cookies_file"]
    assert cookie_arg["runtime_resolved"] is True
    assert "~" not in json.dumps(manifest["capabilities"])


def test_get_urls_manifest_declares_closed_server_network_authority() -> None:
    manifest = tomllib.loads(GET_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["placement"] == {"scope": "server", "device_ok": False}
    assert manifest["capabilities"] == [{
        "name": "network:http", "hint": ["127.0.0.1"],
    }]
    assert manifest["args"]["requires_one_of"] == [["url", "urls"]]


def test_dynamic_cookie_mount_is_exact_and_absent_without_argument(
        tmp_path: Path) -> None:
    import sandbox

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    unrelated = tmp_path / "unrelated-secret.txt"
    unrelated.write_text("secret", encoding="utf-8")
    executor = _catalog().executors["read_urls_html"]

    selected = sandbox.filesystem_extras(
        executor, {"auth_cookies_file": str(cookie_file)})

    assert selected == [cookie_file]
    assert unrelated not in selected
    assert sandbox.filesystem_extras(executor, {}) == []
    assert sandbox.filesystem_extras(
        executor, {"urls": [str(unrelated)]}) == []


def test_invalid_inputs_fail_before_backend_or_network(monkeypatch) -> None:
    touched = []
    monkeypatch.setattr(
        read_urls_html, "_resolve_backend",
        lambda _client: touched.append(True),
    )

    root = read_urls_html.invoke([])
    client = read_urls_html.invoke({"urls": [], "client": 7})

    assert root["error_class"] == "invalid_input"
    assert root["error_code"] == "args_not_object"
    assert client["error_class"] == "invalid_input"
    assert client["error_code"] == "client_not_string"
    assert touched == []


def test_non_http_and_mixed_item_errors_are_typed_without_fetch(monkeypatch) -> None:
    fetched = []
    monkeypatch.setattr(
        read_urls_html, "_fetch_one_with_retry",
        lambda *args, **kwargs: fetched.append(args[0]),
    )

    result = read_urls_html._invoke_default({
        "urls": ["file:///etc/passwd", "not-a-url"],
        "cache_ttl_s": 0,
    })

    assert result["ok"] is False
    assert result["error_class"] == "invalid_input"
    assert result["error_code"] == "invalid_url"
    assert result["ok_count"] == 0
    assert result["fail_count"] == 2
    assert {item["error_code"] for item in result["failed"]} == {
        "invalid_url"}
    assert fetched == []


def test_invalid_optional_values_fail_even_for_empty_url_batch() -> None:
    result = read_urls_html.invoke({
        "urls": [], "follow_iframes": "yes", "cache_ttl_s": 0})

    assert result["ok"] is False
    assert result["error_class"] == "invalid_input"
    assert result["error_code"] == "follow_iframes_not_boolean"


def test_get_urls_vector_contract_is_strict_and_typed() -> None:
    root = get_urls.invoke([])
    conflict = get_urls.invoke({
        "url": "https://example.test/a",
        "urls": ["https://example.test/b"],
    })
    vector = get_urls.invoke({"urls": [7]})

    assert root["error_code"] == "args_not_object"
    assert conflict["error_code"] == "url_urls_conflict"
    assert vector["ok"] is False
    assert vector["failed"][0]["error_code"] == "invalid_url"
    assert vector["error_class"] == "invalid_input"


def test_natural_paraphrases_remain_routable() -> None:
    entries = list(_catalog().executors.values())
    for query in (
        "leggi il contenuto principale di queste pagine web",
        "extract the article text from these HTML links",
    ):
        names = [item.name for item in rank(query, entries, k=8, min_score=1)]
        assert "read_urls_html" in names, (query, names)
    for query in (
        "scarica il corpo JSON da questo endpoint HTTP",
        "perform a HEAD request on this API URL",
    ):
        names = [item.name for item in rank(query, entries, k=8, min_score=1)]
        assert "get_urls" in names, (query, names)


def test_sandboxed_reader_receives_only_the_declared_cookie_file(
        tmp_path: Path) -> None:
    import agent_runtime
    import sandbox

    if not sandbox.bwrap_available():
        pytest.skip("bubblewrap unavailable")

    cookie_value = "sandbox-cookie-value"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if f"session={cookie_value}" not in self.headers.get("Cookie", ""):
                self.send_response(403)
                self.end_headers()
                return
            body = b"<html><body><article>authorized fixture</article></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        "127.0.0.1\tFALSE\t/\tFALSE\t2147483647\tsession\t"
        f"{cookie_value}\n",
        encoding="utf-8",
    )
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("must-not-be-mounted", encoding="utf-8")
    executor = _catalog().executors["read_urls_html"]
    try:
        result = agent_runtime.invoke_executor(
            executor,
            {
                "urls": [f"http://127.0.0.1:{server.server_port}/page"],
                "auth_cookies_file": str(cookie_file),
                "cache_ttl_s": 0,
                "follow_iframes": False,
            },
            timeout_s=15,
            actor="host",
            channel="test",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    if (not result.get("ok") and "bwrap:" in str(result.get("error"))
            and "Operation not permitted" in str(result.get("error"))):
        pytest.skip("kernel temporarily denied bubblewrap namespace creation")
    assert result["ok"] is True, result
    assert result["entries"][0]["body_text"] == "authorized fixture"
    serialized = json.dumps(result, sort_keys=True)
    assert cookie_value not in serialized
    assert "must-not-be-mounted" not in serialized
