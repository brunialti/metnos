"""Geographic subjects use live location producers, never machine-name POIs."""
import copy
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import detection_lexicon as dl
import target_device
from engine.dispatch import _ensure_proximity_center
from engine.types import Framework, StepSpec
from engine.validator import Validator

QUERY = "farmacia piu vicina a dove è il server metnos"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def catalog():
    return [SimpleNamespace(name=name, args_schema=tomllib.loads(
        (ROOT / "executors" / name / "manifest.toml").read_text())["args"])
        for name in ("find_places", "get_location")]


@pytest.mark.parametrize("near", [None, "server metnos", "server", "METNOS"])
def test_server_centre_is_resolved_not_searched_as_a_place(catalog, near, monkeypatch):
    monkeypatch.setattr(target_device, "_server_aliases", lambda: ("metnos",))
    fw = Framework(steps=[StepSpec("find_places", {"queries": ["farmacia"], "near": near})])
    _ensure_proximity_center(fw, QUERY, catalog)
    assert [(s.tool, s.args.get("subject")) for s in fw.steps] == [
        ("get_location", "server"), ("find_places", None)]
    assert fw.steps[1].args["near"] == "${step1.location}"
    assert Validator(catalog).check(fw).ok
    first = copy.deepcopy(fw)
    assert _ensure_proximity_center(fw, QUERY, catalog) == first


@pytest.mark.parametrize("query,subject,lang", [
    ("farmacia più vicina a me", "actor", "it"),
    ("nearest pharmacy to the server", "server", "en"),
    ("search on the server for the nearest pharmacy to me", "actor", "en"),
    ("cerca sul server la farmacia più vicina a me", "actor", "it"),
])
def test_subject_not_execution_placement(catalog, query, subject, lang, monkeypatch):
    monkeypatch.setattr(dl, "current_lang", lambda: lang)
    fw = Framework(steps=[StepSpec("find_places", {"queries": ["pharmacy"]})])
    _ensure_proximity_center(fw, query, catalog)
    assert fw.steps[0].args == {"subject": subject}


@pytest.mark.parametrize("near", [
    "Padova", "Hotel Server Metnos", {"lat": 45.4, "lon": 11.8},
    [45.4, 11.8], "${step1.location}",
])
def test_explicit_places_coordinates_and_references_are_preserved(catalog, near):
    fw = Framework(steps=[StepSpec("find_places", {"queries": ["farmacia"], "near": near})])
    before = copy.deepcopy(fw)
    assert _ensure_proximity_center(fw, QUERY, catalog) == before


@pytest.mark.parametrize("existing_subject,reuse", [("actor", False), ("server", True)])
def test_only_reuses_a_producer_for_the_requested_subject(catalog, existing_subject, reuse):
    fw = Framework(steps=[
        StepSpec("get_location", {"subject": existing_subject}),
        StepSpec("find_places", {"queries": ["farmacia"], "near": "server metnos"}),
    ], final_message="${step2.entries}")
    _ensure_proximity_center(fw, QUERY, catalog)
    assert len(fw.steps) == (2 if reuse else 3)
    assert fw.steps[-1].args["near"] == ("${step1.location}" if reuse else "${step2.location}")
    assert fw.final_message == ("${step2.entries}" if reuse else "${step3.entries}")


def test_contract_driven_for_other_consumers_and_no_extra_producer(catalog):
    other = SimpleNamespace(name="find_candidates", args_schema={"properties": {"near": {}}})
    fw = Framework(steps=[
        StepSpec("find_candidates", {"near": "server metnos"}),
        StepSpec("find_places", {"queries": ["farmacia"]}),
    ])
    _ensure_proximity_center(fw, QUERY, catalog + [other])
    assert [s.tool for s in fw.steps] == ["get_location", "find_candidates", "find_places"]
    assert fw.steps[1].args["near"] == fw.steps[2].args["near"] == "${step1.location}"


@pytest.mark.parametrize("source_args", [{}, {"subject": "actor"}, {"subject": "server", "verify": True}])
def test_server_request_cannot_reuse_a_reference_to_the_actor(catalog, source_args):
    fw = Framework(steps=[
        StepSpec("get_location", source_args),
        StepSpec("find_places", {"queries": ["farmacia"], "near": "${step1.location}"}),
    ])
    _ensure_proximity_center(fw, QUERY, catalog)
    assert fw.steps[0].args == source_args
    assert fw.steps[1].args == {"subject": "server"}
    assert fw.steps[2].args["near"] == "${step2.location}"


@pytest.mark.parametrize("near", ["Padova", [45.4, 11.8], {"lat": 45.4, "lon": 11.8}])
def test_declared_centre_forms_pass_validation(catalog, near):
    fw = Framework(steps=[StepSpec("find_places", {"queries": ["farmacia"], "near": near})])
    assert Validator(catalog).check(fw).ok


def test_shipped_find_places_language_state_matches_its_descriptions():
    from i18n_materializer import decode_language_state
    directory = ROOT / "executors" / "find_places"
    manifest = tomllib.loads((directory / "manifest.toml").read_text())
    state = decode_language_state((directory / "manifest.lang_state.json").read_bytes(), manifest=manifest)
    assert set(state["selectors"]["args.properties.near.description"]) == {"it", "en"}
