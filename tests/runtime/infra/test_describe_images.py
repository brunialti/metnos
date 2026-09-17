"""Test builtin describe_images (VLM content-describe). VLM mockato: nessuna
call reale. Verifica raccolta path + shape output (entries + query_text) §2.6."""
from __future__ import annotations

import json
import unittest
import pytest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import describe_images as di  # noqa: E402


@pytest.mark.parametrize("content,finish,expected", [
    ({"description": "A beach", "keywords": ["sea"], "location_hint": "coast", "activity_hint": ""}, "stop", None),
    ({"description": "A beach", "keywords": ["sea"], "location_hint": "coast", "activity_hint": ""}, "length", "output_truncated"),
    ('{"description":"cut off', "length", "output_truncated"),
    ({"description": "x" * 401, "keywords": [], "location_hint": "", "activity_hint": ""}, "stop", "response_schema_mismatch"),
    ({"description": None, "keywords": None}, "stop", "response_schema_mismatch"),
    ({"description": "x", "keywords": [], "location_hint": "", "activity_hint": "", "extra": "private"}, "stop", "response_schema_mismatch"),
])
def test_structured_vision_is_bounded_validated_and_accounted(
    monkeypatch, tmp_path, content, finish, expected,
):
    import llm_telemetry
    import vlm_client
    from image_index_build import DESCRIPTION_SCHEMA
    from PIL import Image

    path = tmp_path / "photo.png"
    Image.new("RGB", (8, 8), "blue").save(path)
    calls = []
    wire = json.dumps({"choices": [{"message": {"content": content if isinstance(content, str) else json.dumps(content)}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 23, "completion_tokens": 512 if finish == "length" else 20}}).encode()

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read(self): return wire

    def request(req, **_kwargs):
        calls.append(json.loads(req.data))
        return Response()

    monkeypatch.setattr(vlm_client.urllib.request, "urlopen", request)
    usage = llm_telemetry.BoundedTransportUsageSink()
    with llm_telemetry.transport_usage_context(usage):
        result = vlm_client.describe_image(path, prompt="fixture", max_tokens=512,
                                          allow_lazy_start=False, response_schema=DESCRIPTION_SCHEMA)
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 512
    assert calls[0]["response_format"] == {"type": "json_object", "schema": DESCRIPTION_SCHEMA}
    assert result.get("_vlm_error") == expected
    assert usage.export()["records"][0]["out_tokens"] == (512 if finish == "length" else 20)
    if expected:
        assert result["description"] == ""


def test_malformed_vision_values_fail_without_exception():
    import vlm_client
    assert vlm_client._parse_vlm_text('{"keywords":null}')["_vlm_error"] == "invalid_keywords"
    assert vlm_client._parse_vlm_text(["private"])["_vlm_error"] == "invalid_response_type"


def test_structured_vision_rejection_does_not_retry_or_claim_zero_usage(monkeypatch, tmp_path):
    import urllib.error
    import llm_telemetry
    import vlm_client
    from image_index_build import DESCRIPTION_SCHEMA
    from PIL import Image

    path = tmp_path / "photo.png"
    Image.new("RGB", (8, 8)).save(path)
    calls = []

    def rejected(request, **_kwargs):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 400, "unsupported schema", {}, None)

    monkeypatch.setattr(vlm_client.urllib.request, "urlopen", rejected)
    sink = llm_telemetry.BoundedTransportUsageSink()
    with llm_telemetry.transport_usage_context(sink):
        result = vlm_client.describe_image(path, allow_lazy_start=False,
                                          response_schema=DESCRIPTION_SCHEMA, max_tokens=512)
    assert result["_vlm_error"]
    assert len(calls) == sink.export()["calls_started"] == 1
    assert sink.export()["records"] == []
    parent = llm_telemetry.BoundedUsageSink()
    parent.ingest_transport(sink.export(), workload_id="wrk-test", stage_id="stage-test",
                            unit_key="unit-test", attempt_id="attempt-test")
    assert parent.summary()["usage_missing"] is True
    assert parent.summary()["zero_calls_verified"] is False


def test_vlm_client_reports_bounded_provider_usage(monkeypatch, tmp_path):
    import llm_telemetry
    import vlm_client
    from PIL import Image

    image_path = tmp_path / "fixture.png"
    Image.new("RGB", (8, 8), "white").save(image_path)
    payload = json.dumps({
        "choices": [{
            "message": {
                "content": json.dumps({
                    "description": "fixture",
                    "keywords": ["test"],
                }),
            },
        }],
        "usage": {"prompt_tokens": 23, "completion_tokens": 6},
    }).encode("utf-8")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return payload

    monkeypatch.setattr(vlm_client.urllib.request, "urlopen", lambda *_a, **_k: Response())
    sink = llm_telemetry.BoundedTransportUsageSink()
    with llm_telemetry.transport_usage_context(sink):
        result = vlm_client.describe_image(
            image_path,
            prompt="private vision prompt",
            url="http://127.0.0.1:9999/v1/chat/completions",
            model="private-model",
        )

    assert result["description"] == "fixture"
    wire = sink.export()
    assert wire["calls_started"] == 1
    record = wire["records"][0]
    assert record["kind"] == "vision"
    assert record["tier"] == "vlm:default"
    assert (record["in_tokens"], record["out_tokens"]) == (23, 6)
    assert "private vision prompt" not in str(record)


def test_vlm_attempt_without_usage_is_not_reported_as_zero_calls(
    monkeypatch, tmp_path,
):
    import llm_telemetry
    import vlm_client
    from PIL import Image

    image_path = tmp_path / "fixture.png"
    Image.new("RGB", (8, 8), "white").save(image_path)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"not-json"

    monkeypatch.setattr(
        vlm_client.urllib.request, "urlopen", lambda *_a, **_k: Response(),
    )
    child = llm_telemetry.BoundedTransportUsageSink()
    with llm_telemetry.transport_usage_context(child):
        result = vlm_client.describe_image(
            image_path,
            url="http://127.0.0.1:9999/v1/chat/completions",
        )

    assert result["_vlm_error"] == "resp_unparseable"
    parent = llm_telemetry.BoundedUsageSink()
    parent.ingest_transport(
        child.export(),
        workload_id="wrk-test",
        stage_id="stg-test",
        unit_key="unit-test",
        attempt_id="att-test",
    )
    summary = parent.summary()
    assert summary["zero_calls_verified"] is False
    assert summary["usage_missing"] is True
    assert summary["dropped"] == 1


def _fake_describe(path, **kw):
    return {"description": f"contenuto di {Path(path).name}",
            "keywords": ["k1", "k2"], "location_hint": "", "activity_hint": ""}


class DescribeImagesTests(unittest.TestCase):
    def setUp(self):
        self._p = mock.patch("vlm_client.describe_image", _fake_describe)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_reference_images_to_entries_and_query_text(self):
        out = di.handle_describe_images({"reference_images": ["/u/a.jpg", "/u/b.jpg"]})
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["entries"]), 2)
        self.assertEqual(out["entries"][0]["path"], "/u/a.jpg")
        self.assertIn("contenuto di a.jpg", out["entries"][0]["description"])
        # query_text = descrizioni unite (per ricerca contenuto)
        self.assertIn("contenuto di a.jpg", out["query_text"])
        self.assertIn("contenuto di b.jpg", out["query_text"])
        self.assertEqual(out["ok_count"], 2)

    def test_entries_form_seed_wired(self):
        out = di.handle_describe_images(
            {"entries": [{"path": "/u/c.jpg", "reference_image": "/u/c.jpg"}]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["entries"][0]["path"], "/u/c.jpg")

    def test_dedup_paths(self):
        out = di.handle_describe_images(
            {"reference_images": ["/u/a.jpg", "/u/a.jpg"], "paths": ["/u/a.jpg"]})
        self.assertEqual(len(out["entries"]), 1)

    def test_empty_args_honest_error(self):
        out = di.handle_describe_images({})
        self.assertFalse(out["ok"])
        self.assertEqual(out["entries"], [])
        self.assertEqual(out["query_text"], "")

    def test_vlm_error_propagated_not_crash(self):
        with mock.patch("vlm_client.describe_image",
                        lambda p, **k: {"description": "", "keywords": [],
                                        "_vlm_error": "http_failed"}):
            out = di.handle_describe_images({"reference_images": ["/u/x.jpg"]})
        self.assertFalse(out["ok"])  # nessuna descrizione → ok False
        self.assertEqual(out["entries"][0]["_vlm_error"], "http_failed")

    def test_upstream_cardinality_is_bounded_and_reported(self):
        calls = []

        def describe(path, **kwargs):
            calls.append((path, kwargs))
            return _fake_describe(path, **kwargs)

        paths = [f"/u/{index}.jpg" for index in range(1000)]
        with (mock.patch("vlm_client.describe_image", describe),
              mock.patch.object(di, "_work_policy", return_value=(8, 45.0))):
            out = di.handle_describe_images({"reference_images": paths})

        self.assertEqual(len(calls), 8)
        self.assertTrue(all("deadline_at" in kwargs for _, kwargs in calls))
        self.assertEqual(out["used"], 8)
        self.assertEqual(out["available_total"], 1000)
        self.assertEqual(out["cap_field"], "max_images_per_request")
        self.assertEqual(out["cap_value"], 8)
        self.assertFalse(out["cap_expandable"])
        self.assertTrue(out["truncated"])
        self.assertFalse(out["budget_exhausted"])

    def test_shared_wall_budget_stops_new_vlm_calls(self):
        clock = [0.0]
        calls = []

        def describe(path, **kwargs):
            calls.append((path, kwargs))
            clock[0] += 2.0
            return _fake_describe(path, **kwargs)

        with (mock.patch("vlm_client.describe_image", describe),
              mock.patch.object(di, "_work_policy", return_value=(8, 3.0)),
              mock.patch.object(di.time, "monotonic",
                                side_effect=lambda: clock[0])):
            out = di.handle_describe_images({
                "reference_images": [f"/u/{index}.jpg" for index in range(8)],
            })

        self.assertEqual(len(calls), 2)
        self.assertEqual(out["used"], 2)
        self.assertEqual(out["available_total"], 8)
        self.assertEqual(out["cap_field"], "request_budget_s")
        self.assertEqual(out["cap_value"], 3)
        self.assertTrue(out["budget_exhausted"])
        self.assertTrue(out["partial"])


if __name__ == "__main__":
    unittest.main()
