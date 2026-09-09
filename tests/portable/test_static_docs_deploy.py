"""Publishing an audited public checkout must not need live signing keys."""
import os
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX documentation deployment")


@pytest.mark.parametrize("static_only", [False, True])
@pytest.mark.parametrize("validation_fails", [False, True])
def test_docs_deployment_scope(tmp_path, static_only, validation_fails):
    root = tmp_path / "public checkout"
    root.mkdir()
    source = Path(__file__).resolve().parents[2] / "deploy.sh"
    script = root / "deploy.sh"
    script.write_bytes(source.read_bytes())
    environment = tmp_path / "deploy.env"
    environment.write_text("CLOUDFLARE_API_TOKEN=test\n"
                           "CLOUDFLARE_ACCOUNT_ID=test\n"
                           "CLOUDFLARE_PAGES_PROJECT=test\n")
    environment.chmod(0o600)
    binaries = root / ".venv/bin"
    binaries.mkdir(parents=True)
    recorder = '#!/bin/sh\nprintf "%s|%s\\n" "$PWD" "$*" >> "$DEPLOY_TEST_LOG"\n'
    recorder += ('if [ "$DEPLOY_TEST_INVALID" = 1 ] && '
                 '[ "$1" = runtime/published_docs.py ]; then exit 1; fi\n')
    for name in ("python", "wrangler"):
        path = binaries / name
        path.write_text(recorder)
        path.chmod(0o755)
    log = tmp_path / "calls"
    env = dict(os.environ, METNOS_DEPLOY_ENV=str(environment),
               METNOS_VENV=str(root / ".venv"), DEPLOY_TEST_LOG=str(log),
               DEPLOY_TEST_INVALID=str(int(validation_fails)),
               PATH=f"{binaries}:{os.environ['PATH']}")
    args = ["bash", str(script)]
    if static_only:
        args.append("--static-only")
    args.extend(["--commit-message", "Verified documentation update"])
    result = subprocess.run(args, env=env, cwd=tmp_path, capture_output=True)
    assert result.returncode == int(validation_fails)
    calls = [line.split("|", 1) for line in log.read_text().splitlines()]
    assert all(directory == str(root) for directory, _ in calls)
    commands = [command for _, command in calls]
    for command in commands[:3]:
        assert command.endswith(" --check") is static_only
    assert commands[3] == "runtime/published_docs.py validate"
    if validation_fails:
        assert len(commands) == 4
        return
    assert ("scripts/compile_tutor_catalog.py --force" in commands) is not static_only
    assert commands[-1].startswith("pages deploy docs --project-name=test ")
    assert "Verified documentation update" in commands[-1]
