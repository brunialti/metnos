"""Regression: executor helpers remain visible in relocatable installs."""

import sys
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import sandbox  # noqa: E402


def test_runtime_is_bound_read_only_outside_system_roots(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "relocated-metnos" / "runtime"
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "__file__", str(runtime_dir / "sandbox.py"))

    code = tmp_path / "relocated-metnos" / "executors" / "get_now" / "get_now.py"
    code.parent.mkdir(parents=True)
    code.touch()

    args = sandbox._build_bwrap_args(code, capabilities=[])

    index = args.index(str(runtime_dir))
    assert args[index - 1] == "--ro-bind"
    assert args[index + 1] == str(runtime_dir)


def test_system_python_user_site_is_bound_read_only():
    import site

    args = sandbox._build_bwrap_args(Path(__file__), capabilities=[])
    user_site = Path(site.getusersitepackages())
    if sys.prefix == sys.base_prefix and user_site.exists():
        index = args.index(str(user_site))
        assert args[index - 1] == "--ro-bind"
        assert args[index + 1] == str(user_site)


def test_interpreter_dependency_root_survives_later_home_redirection(
        tmp_path, monkeypatch):
    original_site = tmp_path / "original-home" / "site-packages"
    original_site.mkdir(parents=True)
    monkeypatch.setattr(sys, "path", [str(original_site), *sys.path])
    monkeypatch.setenv("HOME", str(tmp_path / "sandbox-home"))

    args = sandbox._build_bwrap_args(Path(__file__), capabilities=[])

    resolved = str(original_site.resolve())
    index = args.index(resolved)
    assert args[index - 1] == "--ro-bind"
    assert args[index + 1] == resolved
