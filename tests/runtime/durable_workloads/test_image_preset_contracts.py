from __future__ import annotations

from types import SimpleNamespace

from durable_workloads.compiler import compile_plan
from durable_workloads.image_preset import (
    ImagePresetWorkloadInvoker,
    SEMANTIC_SCHEMA_VERSION,
    image_questions_plan,
    output_schemas,
    runner_resolver,
)
from durable_workloads.schema import MAX_SNAPSHOT_JSON_BYTES, digest_json
from helpers import inventory, source


def _digest(label: str) -> str:
    return digest_json(
        "image-preset-contract-test", {"label": label}, max_bytes=MAX_SNAPSHOT_JSON_BYTES,
    )


def _catalog():
    executor = SimpleNamespace(
        signed_by="author",
        lifecycle="active",
        dormant=False,
        digest=_digest("ocr-implementation"),
        version="0.3.0",
        args_schema={
            "type": "object",
            "properties": {
                "paths": {"type": "array"},
                "source": {"type": "object"},
            },
            "required": ["paths"],
        },
        capabilities=(),
        placement={"kind": "local"},
        transport="local-subprocess",
        intelligence="agentic",
    )
    return SimpleNamespace(get=lambda name: executor if name == "read_files_ocr" else None)


def test_private_image_preset_compiles_with_closed_contracts():
    compiled = compile_plan(
        image_questions_plan(),
        inventory([source(0), source(1)]),
        runners=runner_resolver(
            catalog_loader=lambda **_kwargs: _catalog(),
            binding_resolver=lambda tier, *, level=None: {
                "provider": "fixture", "model": "fixture-model", "tier": tier,
                "level": level,
            },
        ),
        output_schemas=output_schemas(),
    )
    stages = {item["key"]: item for item in compiled.plan["stages"]}
    assert stages["solutions"]["cardinality"] == {
        "mode": "per_dependency",
        "max_units": 1_000_000,
        "entry_identity_field": "canonical_question_key",
    }
    assert stages["ocr"]["input_bindings"]["source"] == {"ref": "source.record"}
    assert SEMANTIC_SCHEMA_VERSION in output_schemas().resolve(
        "metnos.images.question-occurrences/1"
    ).schema["properties"]["entries"]["items"]["properties"]["semantic_schema_version"].values()


def test_image_preset_has_no_corpus_size_or_source_path_in_its_plan_data():
    rendered = str(image_questions_plan())
    assert "98" not in rendered
    assert "/tmp/" not in rendered
    assert "C:\\" not in rendered


def test_image_workload_invoker_derives_stable_question_identities():
    invoker = ImagePresetWorkloadInvoker(
        lambda _name, _prompt, _args: {
            "questions": [{
                "text": "  Qual è   la risposta? ",
                "coordinate_locale": "page:1",
                "confidence": 0.9,
            }],
        }
    )
    result = invoker(
        "durable.images.extract_questions",
        {
            "source": {"source_id": "source_00000000"},
            "ocr_entries": [{"source_id": "source_00000000", "content": "fixture"}],
        },
        object(),
    )
    entry = result["entries"][0]
    assert entry["normalized_text"] == "qual è la risposta?"
    assert entry["semantic_schema_version"] == SEMANTIC_SCHEMA_VERSION
    assert entry["question_occurrence_id"].startswith("sha256:")
    assert entry["canonical_question_key"].startswith("sha256:")
