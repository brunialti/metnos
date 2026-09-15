"""The VLM lazy-start path must consume, not renew, a caller deadline."""
from __future__ import annotations

import subprocess
import urllib.error

import pytest


@pytest.fixture(autouse=True)
def isolated_startup_profile(monkeypatch, tmp_path):
    # Deadline tests must not depend on a machine's administrative profile.
    monkeypatch.setattr("config.PATH_VLM_STARTUP_PROFILE", tmp_path / "absent-profile.toml")


def test_lazy_start_uses_remaining_shared_budget(monkeypatch, tmp_path):
    import virt

    clock = [0.0]
    subprocess_timeouts = []
    health_timeouts = []
    helper = tmp_path / "vlm-server"
    helper.write_text("placeholder", encoding="utf-8")

    monkeypatch.setenv("METNOS_VLM_SERVER_SH", str(helper))
    monkeypatch.setattr(
        virt, "get_vlm",
        lambda _role="default": {"base_url": "http://127.0.0.1:8081"},
    )
    monkeypatch.setattr("config.PATH_USER_STATE", tmp_path / "state")

    def urlopen(_request, *, timeout):
        health_timeouts.append(timeout)
        clock[0] += timeout
        raise urllib.error.URLError("offline")

    def run(_argv, *, timeout, **_kwargs):
        subprocess_timeouts.append(timeout)
        clock[0] += timeout
        return subprocess.CompletedProcess(_argv, 0, "", "")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda seconds: clock.__setitem__(
        0, clock[0] + seconds))

    assert virt.ensure_vlm_up(deadline_at=5.0, wait_s=35) is False
    assert health_timeouts == [2.0, 2.0]
    assert subprocess_timeouts == [1.0]
    assert clock[0] == 5.0


@pytest.fixture
def local_launcher(monkeypatch, tmp_path):
    import virt

    helper = tmp_path / "vlm-server"
    helper.write_text("fixture launcher", encoding="utf-8")
    monkeypatch.setenv("METNOS_VLM_SERVER_SH", str(helper))
    monkeypatch.setattr("config.PATH_USER_STATE", tmp_path / "state")
    monkeypatch.setattr(virt, "get_vlm", lambda _role="default": {"base_url": "http://127.0.0.1:8081"})
    return virt


def test_a_new_job_can_start_again_after_idle_stop(local_launcher, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    ready = [False]
    starts = []

    @contextmanager
    def urlopen(*_args, **_kwargs):
        if not ready[0]:
            raise urllib.error.URLError("offline")
        yield SimpleNamespace(status=200)

    def run(argv, **_kwargs):
        starts.append(argv)
        ready[0] = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("subprocess.run", run)
    assert local_launcher.ensure_vlm_up()
    assert local_launcher.ensure_vlm_up()
    assert len(starts) == 1
    ready[0] = False
    assert local_launcher.ensure_vlm_up()
    assert len(starts) == 2


def test_concurrent_lanes_share_one_process_start_lock(local_launcher, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event
    from types import SimpleNamespace

    started, second_probe, release = Event(), Event(), Event()
    ready = [False]
    starts = []

    @contextmanager
    def urlopen(*_args, **_kwargs):
        if not ready[0]:
            if started.is_set():
                second_probe.set()
            raise urllib.error.URLError("offline")
        yield SimpleNamespace(status=200)

    def run(argv, **_kwargs):
        starts.append(argv)
        started.set()
        assert release.wait(2)
        ready[0] = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("subprocess.run", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(local_launcher.ensure_vlm_up)
        assert started.wait(1)
        second = pool.submit(local_launcher.ensure_vlm_up)
        assert second_probe.wait(1)
        release.set()
        assert first.result(timeout=2) and second.result(timeout=2)
    assert len(starts) == 1


def test_failed_start_can_be_retried_by_a_later_job(local_launcher, monkeypatch):
    starts = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(urllib.error.URLError("offline"))))
    monkeypatch.setattr("subprocess.run", lambda argv, **_kwargs: (
        starts.append(argv) or subprocess.CompletedProcess(argv, 1, "", "")))
    assert not local_launcher.ensure_vlm_up()
    assert not local_launcher.ensure_vlm_up()
    assert len(starts) == 2


def test_a_distinct_process_respects_the_startup_lock(local_launcher, monkeypatch):
    import config
    import hashlib
    import multiprocessing
    import time
    from process_lock import ProcessLock

    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("POSIX process-lock regression")

    def offline(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", offline)
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: pytest.fail("duplicate launcher"))
    digest = hashlib.sha256(b"http://127.0.0.1:8081/health").hexdigest()
    lock = ProcessLock(config.PATH_USER_STATE / "model-start" / (digest + ".lock"))
    process_context = multiprocessing.get_context("fork")
    receive, send = process_context.Pipe(duplex=False)

    def probe():
        receive.close()
        send.send(local_launcher.ensure_vlm_up(deadline_at=time.monotonic() + 0.1))
        send.close()

    with lock:
        child = process_context.Process(target=probe)
        child.start()
        send.close()
        try:
            assert receive.poll(3)
            assert receive.recv() is False
            child.join(timeout=3)
            assert child.exitcode == 0
        finally:
            receive.close()
            if child.is_alive():
                child.terminate()
                child.join(timeout=2)


def test_remote_endpoint_never_starts_a_local_process(local_launcher, monkeypatch):
    def offline(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(local_launcher, "get_vlm", lambda *_args: {"endpoint": "https://example.invalid/v1/chat/completions"})
    monkeypatch.setattr("urllib.request.urlopen", offline)
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: pytest.fail("local launcher"))
    assert not local_launcher.ensure_vlm_up()


def test_startup_projection_is_child_only(local_launcher, monkeypatch):
    import os

    before = dict(os.environ)
    projected = {**before, "METNOS_VLM_MODEL": "/fixture/model.gguf"}
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(urllib.error.URLError("offline"))))
    monkeypatch.setattr("virt.startup.vlm_startup_environment", lambda role: projected)
    received = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kwargs: (
        received.append(kwargs["env"]) or subprocess.CompletedProcess(argv, 1, "", "")))
    assert not local_launcher.ensure_vlm_up()
    assert received == [projected]
    assert dict(os.environ) == before


def test_untrusted_startup_profile_does_not_invoke_launcher(local_launcher, monkeypatch):
    from virt.startup import StartupProfileError

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(urllib.error.URLError("offline"))))

    def untrusted(_role):
        raise StartupProfileError("untrusted fixture")

    monkeypatch.setattr("virt.startup.vlm_startup_environment", untrusted)
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: pytest.fail("untrusted launcher"))
    assert not local_launcher.ensure_vlm_up()
