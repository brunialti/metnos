"""test_github_filename_routing — «i/tutti i file <nome> su github» deve
diventare find_files_github(pattern)+read, non un read di UN path.

Bug live (turn 22f32adb/582b4824): «riassumi i file readme.md su github» →
il proposer metteva `read_files_github(paths=["README.md"])` (copiando il
PATTERN del manifest) e leggeva UN solo README su 9; oppure sbandava sulla
web-search (`find_urls`→pagine HTML sporche, con un PDF Microsoft fra i
risultati). `readme.md` con un marcatore di PLURALITÀ è un pattern, non un path.

§7.3 (generale) + §7.9 (deterministico): il guard `_route_filename_pattern_to_find`
inserisce `find_files_github(pattern=<nome>)` e ricuce il read alle sue entries.
Vale per ogni nome-file plurale su github, non solo readme.
"""
import sys
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")

from engine.dispatch import _route_filename_pattern_to_find  # noqa: E402
from engine.types import Framework, StepSpec                 # noqa: E402

_CAT = [type("E", (), {"name": n})() for n in
        ("find_files_github", "read_files_github", "describe_entries")]


def _run(paths, query):
    args = {"repo": "brunialti/metnos", "paths": paths}
    fw = Framework(steps=[
        StepSpec(tool="read_files_github", args=args),
        StepSpec(tool="describe_entries", args={"from_step": 1}),
        StepSpec(tool="final_answer", args={})])
    out = _route_filename_pattern_to_find(fw, query, _CAT)
    return out.steps


def _is_routed(steps):
    if steps[0].tool != "find_files_github":
        return False
    rd = next((s for s in steps if s.tool == "read_files_github"), None)
    return bool(rd and "from_step" in rd.args and "paths" not in rd.args)


def test_plural_filename_inserts_find():
    for q in ("riassumi i file readme.md su github nel repo brunialti/metnos",
              "riassumi tutti i file readme.md su github",
              "leggi tutti i file config.toml nel repo github brunialti/metnos"):
        steps = _run(["README.md"] if "readme" in q else ["config.toml"], q)
        assert _is_routed(steps), q
        assert steps[0].args["pattern"] in ("README.md", "config.toml")
        assert steps[0].args["repo"] == "brunialti/metnos"


def test_singular_left_as_read():
    # «il readme.md» (singolare) → resta un read di UN path.
    steps = _run(["README.md"], "leggi il readme.md su github")
    assert steps[0].tool == "read_files_github"
    assert steps[0].args.get("paths") == ["README.md"]


def test_full_path_left_alone():
    # path completo (contiene '/') → l'utente sa quale file, non cercare.
    steps = _run(["install/README.md"], "riassumi i file readme.md")
    assert steps[0].tool == "read_files_github"


def test_multiple_paths_left_alone():
    steps = _run(["README.md", "INSTALL.md"], "i file su github")
    assert steps[0].tool == "read_files_github"


def test_generalizes_to_local_read_files():
    # GENERALITÀ §4.3: la regola non è per-github. read_files↔find_files locale.
    cat = [type("E", (), {"name": n})() for n in
           ("find_files", "read_files", "describe_entries")]
    fw = Framework(steps=[
        StepSpec(tool="read_files", args={"paths": ["config.toml"],
                                          "base_path": "/etc"}),
        StepSpec(tool="describe_entries", args={"from_step": 1}),
        StepSpec(tool="final_answer", args={})])
    out = _route_filename_pattern_to_find(
        fw, "riassumi tutti i file config.toml", cat).steps
    assert out[0].tool == "find_files"
    assert out[0].args["pattern"] == "config.toml"
    assert out[0].args.get("base_path") == "/etc"   # propaga base_path
    rd = next(s for s in out if s.tool == "read_files")
    assert "from_step" in rd.args and "paths" not in rd.args


def test_idempotent_when_already_from_step():
    # un read già instradato da un find (from_step) non viene ri-agganciato.
    fw = Framework(steps=[
        StepSpec(tool="find_files_github",
                 args={"repo": "x", "pattern": "README.md"}),
        StepSpec(tool="read_files_github", args={"repo": "x", "from_step": 1}),
        StepSpec(tool="final_answer", args={})])
    out = _route_filename_pattern_to_find(fw, "tutti i file readme.md", _CAT)
    assert [s.tool for s in out.steps] == [
        "find_files_github", "read_files_github", "final_answer"]


def test_no_op_without_find_executor():
    # tool-existence-safe: se find_files_github non è nel catalogo, no-op.
    cat = [type("E", (), {"name": "read_files_github"})()]
    fw = Framework(steps=[
        StepSpec(tool="read_files_github",
                 args={"repo": "x", "paths": ["README.md"]}),
        StepSpec(tool="final_answer", args={})])
    out = _route_filename_pattern_to_find(fw, "tutti i file readme.md", cat)
    assert out.steps[0].tool == "read_files_github"
