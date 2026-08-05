"""Modalita' ortogonali di find_images_web e guard anti-scope-leak."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME = str(_ROOT / "runtime")
_EXECUTOR = str(_ROOT / "executors" / "find_images_web")
for path in (_RUNTIME, _EXECUTOR):
    if path not in sys.path:
        sys.path.insert(0, path)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_text_mode_uses_searxng_and_returns_distinct_image_records(monkeypatch):
    import find_images_web as executor
    from backends.images import searxng

    seen_urls = []

    def fake_urlopen(request, timeout):
        seen_urls.append(request.full_url)
        return _Response({"results": [
            {"title": "Result A", "img_src": "https://img.example/a.jpg",
             "thumbnail_src": "https://thumb.example/a.jpg",
             "url": "https://page.example/a", "content": "first",
             "engine": "engine-a", "score": 0.9},
            {"title": "Duplicate", "img_src": "https://img.example/a.jpg",
             "url": "https://page.example/duplicate", "score": 0.8},
            {"title": "Result B", "img_src": "https://img.example/b.jpg",
             "url": "https://page.example/b", "resolution": "800x600",
             "score": 0.7},
        ]})

    monkeypatch.setattr(searxng.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        executor, "_reverse",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("text mode must not call Google Vision")))

    out = executor.invoke({"queries": ["soggetto esempio"], "max_results": 10})

    assert out["ok"] is True
    assert out["mode"] == "text_search"
    assert out["ok_count"] == 2
    assert [entry["image_url"] for entry in out["entries"]] == [
        "https://img.example/a.jpg", "https://img.example/b.jpg"]
    assert [attachment["url"] for attachment in out["attachments"]] == [
        "https://img.example/a.jpg", "https://img.example/b.jpg"]
    assert "categories=images" in seen_urls[0]
    assert "q=soggetto+esempio" in seen_urls[0]


def test_reverse_mode_preserves_google_vision_contract(monkeypatch):
    import find_images_web as executor

    expected = {"ok": True, "entries": [{"source": "/tmp/source.jpg"}]}
    monkeypatch.setattr(executor, "_reverse", lambda args: expected)
    monkeypatch.setattr(
        executor, "_text_search",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("reverse mode must not call SearXNG")))

    assert executor.invoke({"paths": ["/tmp/source.jpg"]}) is expected


def test_mixed_text_and_reverse_modes_are_rejected(monkeypatch):
    import find_images_web as executor

    monkeypatch.setattr(executor, "_reverse", lambda _args: None)
    monkeypatch.setattr(executor, "_text_search", lambda _args: None)
    out = executor.invoke({
        "queries": ["soggetto esempio"], "paths": ["/tmp/source.jpg"]})
    assert out["ok"] is False
    assert out["error_class"] == "invalid_args"


def test_text_results_use_deterministic_relevance_before_agentic_fallback():
    from backends.images import searxng
    entries = [
        {"title": "generic landscape", "snippet": "", "source": "a"},
        {"title": "Priscilla Giorgia Marra profile", "snippet": "", "source": "b"},
        {"title": "another image", "snippet": "", "source": "c"},
        {"title": "portrait", "snippet": "", "source": "d"},
    ]
    out = searxng._agentic_rerank(
        entries, "Priscilla Giorgia Marra", "it")
    assert out[0]["source"] == "b"


def test_invalid_agentic_image_ranking_preserves_original(monkeypatch):
    from backends.images import searxng

    class Provider:
        mode = "local"
        def chat(self, *args, **kwargs):
            return types.SimpleNamespace(text='{"ids":["invented"]}')

    router_module = types.SimpleNamespace(
        LLMRouter=lambda: types.SimpleNamespace(provider=lambda _tier: Provider()))
    monkeypatch.setitem(sys.modules, "llm_router", router_module)
    entries = [{"title": f"candidate {i}", "snippet": "", "source": str(i)}
               for i in range(4)]
    assert searxng._agentic_rerank(entries, "unrelated tokens", "en") == entries


def test_direct_web_guard_removes_local_corpus_and_remaps_steps(monkeypatch):
    from engine import dispatch as module
    from engine.types import Framework, Intent, StepSpec

    monkeypatch.setattr(
        module, "_dl_match",
        lambda concept, _query: concept == "images.web_search_scope")
    query = "cerca sul web immagini del soggetto esempio"
    framework = Framework(steps=[
        StepSpec("find_images_indices", {"name": "soggetto esempio"}),
        StepSpec("find_images_web", {
            "paths": "${step1.entries.*.path}", "max_results": 12}),
        StepSpec("final_answer", {}),
    ], final_message="${step2.ok_count}")
    intent = Intent(verb="find", object="images", actions=[
        {"verb": "find", "object": "images"}])

    out = module._route_text_web_image_search(
        framework, intent, query, None)

    assert [step.tool for step in out.steps] == [
        "find_images_web", "final_answer"]
    assert out.steps[0].args == {"queries": [query], "max_results": 12}
    assert out.final_message == "${step1.ok_count}"
    again = module._route_text_web_image_search(out, intent, query, None)
    assert again.to_dict() == out.to_dict()


def test_reverse_marker_preserves_local_source_pipeline(monkeypatch):
    from engine import dispatch as module
    from engine.types import Framework, Intent, StepSpec

    monkeypatch.setattr(module, "_dl_match", lambda concept, _query: concept in {
        "images.web_search_scope", "images.reverse_search_intent"})
    framework = Framework(steps=[
        StepSpec("find_images_indices", {"reference_images": ["/tmp/a.jpg"]}),
        StepSpec("find_images_web", {"from_step": 1}),
    ])
    intent = Intent(verb="find", object="images")

    out = module._route_text_web_image_search(
        framework, intent, "cerca sul web immagini simili a questa foto", None)
    assert out.to_dict() == framework.to_dict()
