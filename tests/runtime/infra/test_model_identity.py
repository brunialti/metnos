from __future__ import annotations


def test_shared_binding_is_probed_once_and_paths_are_redacted(monkeypatch):
    import model_identity as subject

    subject._clear_for_tests()
    calls = []

    def discover(endpoint: str, timeout_s: float) -> list[str]:
        calls.append((endpoint, timeout_s))
        return [r"C:\private\models\actual.gguf"]

    monkeypatch.setitem(
        subject._ADAPTERS, "test-provider", ("test_protocol", discover))
    first = {
        "provider": "test-provider", "endpoint": "http://127.0.0.1:9000",
        "model": "local",
    }
    second = {**first, "model": "another-selector"}

    result = subject.refresh_specs([first, second], timeout_s=0.2)

    assert result["bindings"] == 1
    assert len(calls) == 1
    assert subject.observation_for(first) == subject.observation_for(second)
    observation = subject.observation_for(first)
    assert observation["status"] == "observed"
    assert observation["identities"] == ["actual.gguf"]
    assert "private" not in repr(observation)


def test_multiple_models_are_not_presented_as_one(monkeypatch):
    import model_identity as subject

    subject._clear_for_tests()
    monkeypatch.setitem(
        subject._ADAPTERS, "many-provider",
        ("test_protocol", lambda _endpoint, _timeout: ["one", "two"]),
    )
    spec = {"provider": "many-provider", "endpoint": "https://models.test"}

    subject.refresh_specs([spec])

    observation = subject.observation_for(spec)
    assert observation["status"] == "ambiguous"
    assert observation["identities"] == ["one", "two"]


def test_failures_do_not_expose_endpoint_or_exception(monkeypatch):
    import model_identity as subject

    subject._clear_for_tests()

    def fail(_endpoint: str, _timeout_s: float) -> list[str]:
        raise OSError("secret host detail")

    monkeypatch.setitem(
        subject._ADAPTERS, "down-provider", ("test_protocol", fail))
    spec = {
        "provider": "down-provider",
        "endpoint": "https://models.test/path?api_key=must-not-leak",
    }

    subject.refresh_specs([spec])

    serialized = repr(subject.observation_for(spec))
    assert "must-not-leak" not in serialized
    assert "secret host detail" not in serialized
    assert subject.observation_for(spec)["status"] == "unreachable"


def test_snapshot_only_reads_cache(monkeypatch):
    import model_identity as subject
    from virt.configuration import snapshot

    subject._clear_for_tests()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("rendering must not perform provider I/O")

    monkeypatch.setattr(subject, "refresh_specs", forbidden)
    payload = snapshot()

    assert all("model_observation" in role
               for family in payload["families"] for role in family["roles"])
