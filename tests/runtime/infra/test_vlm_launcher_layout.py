"""The normal launcher follows its runtime roots and relocated binary layout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def test_launcher_uses_canonical_roots_and_binary_sibling_libraries(tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts/vlm_server.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    stubs = tmp_path / "stubs"
    libraries = tmp_path / "installed/bin"
    stubs.mkdir()
    libraries.mkdir(parents=True)
    for name, text in {"pgrep": "#!/bin/sh\nexit 1\n", "curl": "#!/bin/sh\nexit 0\n"}.items():
        target = stubs / name
        target.write_text(text)
        target.chmod(0o700)
    binary = libraries / "llama-server"
    binary.write_text(
        "#!" + sys.executable + "\nimport json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['METNOS_TEST_LAUNCH_CAPTURE']).write_text(json.dumps({"
        "'args': sys.argv[1:], 'libraries': os.environ.get('LD_LIBRARY_PATH')}))\n"
    )
    binary.chmod(0o700)
    link = tmp_path / "selected-binary"
    link.symlink_to(binary)
    capture = tmp_path / "capture.json"
    data, state = tmp_path / "data", tmp_path / "state"
    environment = {
        **os.environ, "PATH": str(stubs) + ":/usr/bin:/bin",
        "METNOS_USER_DATA": str(data), "METNOS_USER_STATE": str(state),
        "METNOS_VLM_MODEL": str(tmp_path / "model.gguf"),
        "METNOS_VLM_MMPROJ": str(tmp_path / "projector.gguf"),
        "METNOS_VLM_LLAMA_BIN": str(link), "METNOS_TEST_LAUNCH_CAPTURE": str(capture),
        "METNOS_RUNTIME_DIR": str(tmp_path / "no-runtime"),
        "LD_LIBRARY_PATH": str(tmp_path / "administrator-libraries"),
    }
    subprocess.run(["bash", str(script), "start"], env=environment,
                   check=True, capture_output=True, text=True, timeout=5)
    deadline = time.monotonic() + 2
    while not capture.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    report = json.loads(capture.read_text())
    assert report["libraries"] == str(libraries) + ":" + environment["LD_LIBRARY_PATH"]
    assert report["args"][:4] == ["-m", environment["METNOS_VLM_MODEL"],
                                  "--mmproj", environment["METNOS_VLM_MMPROJ"]]
    assert (state / "vlm_server.pid").is_file()
    assert (data / "logs/vlm_server.log").is_file()
