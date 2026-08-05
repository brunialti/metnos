"""Regressioni famiglia «elenca i file della cartella <path Windows> sul PC»
(turni 8b675402 → 2cd8862a → e130c549 → 68f28b01 → a9ec3b06, 5/7/2026).

Catena di difetti misurati, ognuno col suo fix deterministico §7.9:
  1. path_alias: un assoluto WINDOWS su host POSIX veniva FUSO col CWD
     (`/opt/metnos/C:\\Windows\\…`) da `Path.resolve()`.
  2. args_extractor: `_PATH_RE` non riconosceva i path Windows → il resolver
     cadeva su un default appreso AVVELENATO (`/opt/metnos/executors/…`).
  3. args_resolver: i path dell'install root non vanno MAI ricordati come
     scope-default né iniettati (si auto-rinforzano: 46 usi).
  4. dispatch `_overwrite_phantom_install_args`: un arg path install-root NON
     nominato dalla query è un fantasma → rimosso (ripara i piani cachati).
  5. dispatch `_degenerate_find_to_list` (§2.2): con intento LIST, un
     find_files(base_path=X) senza selettore è un'enumerazione di contenitore
     → list_dirs (device-eligible).
  6. dispatch `_fs_equivalent` (files↔dirs): l'align/enforce non demolisce più
     il piano corretto list_dirs per un intent (list, files); niente fratello
     scelto a ordine-hash (2cd8862a: find_files; 68f28b01: get_files).
  7. fill_clause_args: uno step SINK (create/write) non riceve mai un path
     auto-estratto (sarebbe l'OUTPUT; il path in query è l'INPUT del produttore).
  8. create_spreadsheet: output senza estensione nota → suffisso .xlsx (§2.4).
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

os.environ.setdefault("METNOS_ENGINE", "v3")

WIN_DIR = "C:\\Windows\\System32\\drivers\\etc"
Q = ("elenca i file della cartella " + WIN_DIR
     + " e metti i path in uno spreadsheet")


def _intent_list_files():
    from engine.types import Intent
    return Intent(verb="list", object="files", actions=[
        {"verb": "list", "object": "files"},
        {"verb": "write", "object": "files"}])


def _catalog():
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    return filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER)


# ── 1. path_alias: mai fondere un assoluto estraneo all'host ─────────────

def test_normalize_foreign_absolute_not_fused():
    from path_alias import normalize_input_path
    out = str(normalize_input_path(WIN_DIR))
    assert not out.startswith("/"), f"fuso col CWD/root POSIX: {out}"
    assert out == WIN_DIR


def test_normalize_native_absolute_still_resolved():
    from path_alias import normalize_input_path
    assert str(normalize_input_path("/tmp")) == "/tmp"


# ── 2. args_extractor: path Windows e UNC riconosciuti ───────────────────

def test_extractor_windows_path():
    from args_extractor import regex_extract
    schema = {"properties": {"base_path": {"type": "string"}},
              "required": ["base_path"]}
    got = regex_extract(Q, schema).get("base_path")
    assert got == WIN_DIR, got


def test_extractor_windows_path_with_spaces_segment():
    from args_extractor import regex_extract
    schema = {"properties": {"base_path": {"type": "string"}}}
    got = regex_extract("leggi C:\\Program Files\\App\\doc.txt adesso",
                        schema).get("base_path")
    assert got == "C:\\Program Files\\App\\doc.txt", got


# ── 3. args_resolver: install root mai ricordato/iniettato ───────────────

def test_install_root_never_remembered(monkeypatch):
    import args_resolver as ar
    import args_defaults
    captured = []
    monkeypatch.setattr(args_defaults, "set_default",
                        lambda *a, **k: captured.append(a))
    ar.remember_scope_args("find_files",
                           {"base_path": "/opt/metnos/executors/read_files"},
                           actor="tester")
    assert captured == [], f"path install-root ricordato: {captured}"
    ar.remember_scope_args("find_files", {"base_path": "/tmp/dati"},
                           actor="tester")
    assert captured, "path legittimo NON ricordato"


def test_install_root_never_injected(monkeypatch):
    import args_resolver as ar
    import args_defaults
    monkeypatch.setattr(args_defaults, "get_default",
                        lambda *a, **k: "/opt/metnos/executors/read_files")
    schema = {"properties": {"base_path": {"type": "string"}},
              "required": ["base_path"]}
    out = ar.resolve_scope_args("find_files", {}, schema,
                                actor="tester", query="che ore sono")
    assert out.get("base_path") in (None, ""), out


# ── 4-7. guard chain: dal piano avvelenato al piano corretto ─────────────

def test_poisoned_cached_plan_repaired_to_list_dirs():
    """Piano L0 avvelenato (find senza selettore + base_path fantasma) →
    la catena guard lo ripara: fantasma rimosso, path dalla clausola,
    swap a list_dirs. Il create NON riceve il path come output."""
    from engine.types import Framework, StepSpec
    from engine import dispatch as D
    cat = _catalog()
    fw = Framework(steps=[
        StepSpec(tool="find_files",
                 args={"client": "local",
                       "base_path": "/opt/metnos/executors/read_files"}),
        StepSpec(tool="create_files_spreadsheet",
                 args={"title": "File in etc", "columns": ["path"],
                       "client": "local"}),
        StepSpec(tool="final_answer", args={})])
    out = D._apply_deterministic_structure_guards(
        fw, _intent_list_files(), Q, cat)
    tools = [s.tool for s in out.steps if s.tool != "final_answer"]
    assert tools[0] == "list_dirs", tools
    assert out.steps[0].args.get("path") == WIN_DIR
    cre = next(s for s in out.steps if s.tool.startswith("create"))
    assert "path" not in cre.args and "base_path" not in cre.args, cre.args


def test_correct_cached_plan_survives_align():
    """Il piano CORRETTO cachato (list_dirs+create) sopravvive intatto ai
    guard con intent (list, files) — files↔dirs equivalenti (§2.2): niente
    demolizione a fratello hash-ordinato, niente find_files spurio appeso."""
    import dataclasses
    from engine.types import Framework, StepSpec
    from engine import dispatch as D
    cat = _catalog()
    fw = Framework(steps=[
        StepSpec(tool="list_dirs",
                 args={"path": WIN_DIR, "recursive": False}),
        StepSpec(tool="create_files_spreadsheet",
                 args={"title": "File in etc", "columns": ["path"],
                       "from_step": 1, "client": "local"}),
        StepSpec(tool="final_answer", args={})])
    ref = copy.deepcopy(fw)
    out = D._apply_deterministic_structure_guards(
        fw, _intent_list_files(), Q, cat)
    tools = [s.tool for s in out.steps if s.tool != "final_answer"]
    assert tools == ["list_dirs", "create_files_spreadsheet"], tools
    # idempotenza (T4 spot-check): guard(guard(fw)) == guard(fw)
    out2 = D._apply_deterministic_structure_guards(
        copy.deepcopy(out), _intent_list_files(), Q, cat)
    assert dataclasses.asdict(out) == dataclasses.asdict(out2)
    del ref


def test_degenerate_find_respects_real_selector():
    from engine.types import Framework, StepSpec, Intent
    from engine import dispatch as D
    cat = _catalog()
    fw = Framework(steps=[
        StepSpec(tool="find_files",
                 args={"base_path": "/tmp", "pattern": "*.py"}),
        StepSpec(tool="final_answer", args={})])
    it = Intent(verb="list", object="files",
                actions=[{"verb": "list", "object": "files"}])
    out = D._degenerate_find_to_list(fw, it, cat)
    assert out.steps[0].tool == "find_files"


def test_align_github_sibling_still_corrected():
    """No-regressione: il caso originario dell'align (issue→pull) resta."""
    from engine.types import Framework, StepSpec, Intent
    from engine import dispatch as D
    cat = _catalog()
    fw = Framework(steps=[StepSpec(tool="find_pulls_github", args={}),
                          StepSpec(tool="final_answer", args={})])
    it = Intent(verb="find", object="issues",
                actions=[{"verb": "find", "object": "issues"}])
    out = D._align_framework_objects(fw, it, cat)
    assert out.steps[0].tool == "find_issues_github"


def test_enforce_missing_objects_multidomain_intact():
    """No-regressione: con domini DAVVERO distinti (messages+files) il
    produttore mancante viene ancora appeso."""
    from engine.types import Framework, StepSpec, Intent
    from engine import dispatch as D
    cat = _catalog()
    fw = Framework(steps=[StepSpec(tool="read_messages", args={}),
                          StepSpec(tool="final_answer", args={})])
    it = Intent(verb="read", object="messages", actions=[
        {"verb": "read", "object": "messages"},
        {"verb": "find", "object": "files"}])
    out = D._enforce_missing_objects(fw, it,
                                     "leggi le mail e trova i file pdf", cat)
    tools = [s.tool for s in out.steps if s.tool != "final_answer"]
    assert "find_files" in tools, tools


def test_typed_file_globs_satisfy_specific_object_without_extra_search():
    """A hash search over image globs is already an image producer."""
    from types import SimpleNamespace
    from engine.types import Framework, StepSpec, Intent
    from engine import dispatch as D
    from file_kinds import globs_for_kinds

    catalog = [
        SimpleNamespace(name="list_dirs", args_schema={"properties": {}}),
        SimpleNamespace(name="find_files_hash", args_schema={"properties": {
            "patterns": {"type": "array", "semantic_type": "file_globs"},
        }}),
        SimpleNamespace(name="find_images_indices",
                        args_schema={"properties": {}}),
    ]
    fw = Framework(steps=[
        StepSpec(tool="list_dirs", args={"path": "Immagini"}),
        StepSpec(tool="find_files_hash", args={
            "base_path": "Immagini",
            "patterns": globs_for_kinds(["image"]),
        }),
        StepSpec(tool="final_answer", args={}),
    ])
    intent = Intent(verb="list", object="dirs", actions=[
        {"verb": "list", "object": "dirs"},
        {"verb": "find", "object": "images"},
    ])

    out = D._enforce_missing_objects(fw, intent,
                                     "conta tutto e trova immagini duplicate",
                                     catalog)

    assert [step.tool for step in out.steps] == [
        "list_dirs", "find_files_hash", "final_answer",
    ]


def _affinity_catalog():
    from types import SimpleNamespace
    return [
        SimpleNamespace(
            name="list_dirs", affinity=["elenca directory"],
            args_schema={"properties": {"path": {"type": "string"}}}),
        SimpleNamespace(
            name="find_images_indices",
            affinity=["immagini indicizzate", "ricerca semantica"],
            args_schema={"properties": {
                "base_path": {"type": "string"},
                "query_text": {"type": "string"},
            }}),
        SimpleNamespace(
            name="find_files_hash",
            affinity=["immagini duplicate", "duplicate images"],
            args_schema={
                "required": ["base_path"],
                "properties": {
                    "base_path": {"type": "string"},
                    "patterns": {"type": "array",
                                 "semantic_type": "file_globs"},
                },
            }),
    ]


def test_strong_manifest_affinity_routes_duplicate_images_to_hash():
    """A unique compound affinity corrects the visual-search sibling."""
    from engine.types import Framework, StepSpec, Intent
    from engine import dispatch as D
    from file_kinds import globs_for_kinds

    fw = Framework(steps=[
        StepSpec(tool="list_dirs", args={"path": "Immagini"}),
        StepSpec(tool="find_images_indices", args={}),
        StepSpec(tool="final_answer", args={}),
    ])
    intent = Intent(verb="list", object="dirs", actions=[
        {"verb": "list", "object": "dirs"},
        {"verb": "find", "object": "images"},
    ])
    query = ("controlla la directory Immagini. sul server metnos conta i "
             "file e le directory. trova immagini duplicate")

    out = D._align_strong_affinity_producers(
        fw, intent, query, _affinity_catalog())

    assert [step.tool for step in out.steps] == [
        "list_dirs", "find_files_hash", "final_answer",
    ]
    assert out.steps[1].args["base_path"] == "Immagini"
    assert out.steps[1].args["patterns"] == globs_for_kinds(["image"])

    # The semantic correction is a normalization, not a one-shot patch.
    first_args = dict(out.steps[1].args)
    again = D._align_strong_affinity_producers(
        out, intent, query, _affinity_catalog())
    assert [step.tool for step in again.steps] == [
        "list_dirs", "find_files_hash", "final_answer",
    ]
    assert again.steps[1].args == first_args


def test_compound_affinity_never_falls_back_to_an_unrelated_clause():
    from engine import dispatch as D

    chunks = D._affinity_chunks(
        "elenca i file. trova immagini duplicate",
        "read", "messages",
    )

    assert chunks == []


def test_final_message_reference_marks_a_step_as_consumed():
    from engine.types import StepSpec
    from engine import dispatch as D

    steps = [StepSpec(tool="list_dirs", args={})]
    assert D._step_is_consumed(
        steps, 1, "Totale: ${step1.available_total}") is True


def test_strong_affinity_is_not_greedy_on_single_token_or_visual_search():
    """One generic token cannot rewrite a normal indexed-photo search."""
    from engine.types import Framework, StepSpec, Intent
    from engine import dispatch as D

    intent = Intent(verb="find", object="images", actions=[
        {"verb": "find", "object": "images"},
    ])
    for query in ("trova immagini", "trova duplicati",
                  "trova immagini indicizzate del mare"):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices", args={"query_text": "mare"}),
            StepSpec(tool="final_answer", args={}),
        ])
        out = D._align_strong_affinity_producers(
            fw, intent, query, _affinity_catalog())
        assert out.steps[0].tool == "find_images_indices", query


def test_missing_object_enforce_uses_same_strong_affinity_semantics():
    """The object-level fallback must not reintroduce visual search."""
    from engine.types import Framework, StepSpec, Intent
    from engine import dispatch as D

    fw = Framework(steps=[
        StepSpec(tool="list_dirs", args={"path": "Immagini"}),
        StepSpec(tool="final_answer", args={}),
    ])
    intent = Intent(verb="list", object="dirs", actions=[
        {"verb": "list", "object": "dirs"},
        {"verb": "find", "object": "images"},
    ])
    out = D._enforce_missing_objects(
        fw, intent,
        "conta i file nella directory Immagini e trova immagini duplicate",
        _affinity_catalog())
    assert [step.tool for step in out.steps] == [
        "list_dirs", "find_files_hash", "final_answer",
    ]
    assert out.steps[1].args["base_path"] == "Immagini"


# ── 8. create_spreadsheet: suffisso .xlsx su output senza estensione ─────

def test_create_spreadsheet_extensionless_gets_xlsx(tmp_path):
    from backends.files import local
    out = local.create_spreadsheet({
        "title": "x", "path": str(tmp_path / "senza_estensione"),
        "values": [["a"], ["1"]]})
    assert out["ok"] is True
    p = (out.get("results") or [{}])[0].get("path", "")
    assert p.endswith(".xlsx"), p
    import openpyxl
    rows = [list(r) for r in openpyxl.load_workbook(
        p, read_only=True, data_only=True).active.iter_rows(values_only=True)]
    assert rows == [["a"], ["1"]]
