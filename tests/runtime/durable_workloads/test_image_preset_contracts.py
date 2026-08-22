from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
                    "provider": "llamacpp", "model": "fixture-model", "tier": tier,
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
    assert {"model_binding.digest", "prompt.digest"} <= set(
        stages["ocr"]["invalidation_keys"]
    )
    ocr_snapshot = next(
        item for item in compiled.catalog_snapshot["entries"]
        if item["stage_key"] == "ocr"
    )
    assert ocr_snapshot["model_binding_digest"].startswith("sha256:")
    assert ocr_snapshot["prompt_digest"].startswith("sha256:")
    assert ocr_snapshot["prompt_language"] == "it"
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
        lambda _name, _prompt, _args, _context: {
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
        SimpleNamespace(language="it"),
    )
    entry = result["entries"][0]
    assert entry["normalized_text"] == "qual è la risposta?"
    assert entry["semantic_schema_version"] == SEMANTIC_SCHEMA_VERSION
    assert entry["question_occurrence_id"].startswith("sha256:")
    assert entry["canonical_question_key"].startswith("sha256:")


def test_image_workload_invoker_normalizes_a_fenced_root_question_array():
    """The local model's common root-array variant keeps strict item checks."""

    invoker = ImagePresetWorkloadInvoker(
        lambda _name, _prompt, _args, _context: """```json
[{"text":"Quanto fa 12 x 7?","coordinate_locale":null,"confidence":0.95}]
```"""
    )

    result = invoker(
        "durable.images.extract_questions",
        {"source": {"source_id": "source_00000000"}, "ocr_entries": []},
        SimpleNamespace(language="it"),
    )

    assert result["entries"][0]["original_text"] == "Quanto fa 12 x 7?"
    assert result["entries"][0]["coordinate_locale"] == "whole_image"


def test_image_workload_invoker_rejects_a_root_array_for_scalar_contracts():
    invoker = ImagePresetWorkloadInvoker(
        lambda _name, _prompt, _args, _context: [{"valid": True, "reason": "ok"}]
    )

    assert invoker(
        "durable.images.validate",
        {"answer": {"canonical_question_key": "question-a"}},
        SimpleNamespace(language="it"),
    ) == {"ok": False, "error_class": "contract_violation"}


def test_image_workload_prompt_uses_the_frozen_language_without_translating_sources():
    prompts = []

    invoker = ImagePresetWorkloadInvoker(
        lambda _name, prompt, _args, _context: (
            prompts.append(prompt)
            or {"status": "unresolved", "answer": "", "reason": "", "confidence": 0}
        )
    )
    result = invoker(
        "durable.images.answer",
        {"question": {"canonical_question_key": "question-a"}},
        SimpleNamespace(language="en"),
    )

    assert result["entries"][0]["status"] == "unresolved"
    assert "Keep source text unchanged" in prompts[0]
    assert "language en" in prompts[0]


def test_image_workload_invoker_rejects_out_of_range_confidence():
    questions = ImagePresetWorkloadInvoker(
        lambda _name, _prompt, _args, _context: {
            "questions": [{
                "text": "Domanda",
                "coordinate_locale": "page:1",
                "confidence": 1.1,
            }],
        }
    )
    assert questions(
        "durable.images.extract_questions",
        {"source": {"source_id": "source_00000000"}},
        SimpleNamespace(language="it"),
    ) == {"ok": False, "error_class": "contract_violation"}

    answers = ImagePresetWorkloadInvoker(
        lambda _name, _prompt, _args, _context: {
            "status": "answered",
            "answer": "Risposta",
            "reason": "",
            "confidence": -0.1,
        }
    )
    assert answers(
        "durable.images.answer",
        {"question": {"canonical_question_key": "question-a"}},
        SimpleNamespace(language="it"),
    ) == {"ok": False, "error_class": "contract_violation"}


def test_image_workload_invoker_ignores_impossible_merge_confidence():
    invoker = ImagePresetWorkloadInvoker(
        lambda _name, _prompt, _args, _context: {
            "merge_groups": [{
                "keys": ["question-a", "question-b"],
                "confidence": 2.0,
            }],
        }
    )
    result = invoker(
        "durable.images.deduplicate",
        {
            "occurrences": [
                {
                    "canonical_question_key": "question-a",
                    "question_occurrence_id": "sha256:" + "a" * 64,
                    "normalized_text": "domanda a",
                },
                {
                    "canonical_question_key": "question-b",
                    "question_occurrence_id": "sha256:" + "b" * 64,
                    "normalized_text": "domanda b",
                },
            ],
        },
        SimpleNamespace(language="it"),
    )
    assert [entry["canonical_question_key"] for entry in result["entries"]] == [
        "question-a",
        "question-b",
    ]


def test_image_workload_invoker_rejects_duplicate_artifact_names():
    invoker = ImagePresetWorkloadInvoker(
        lambda _name, _prompt, _args, _context: {
            "artifacts": [
                {"logical_name": "solutions_markdown", "markdown": "solutions"},
                {"logical_name": "notes_markdown", "markdown": "notes"},
                {"logical_name": "formulae_markdown", "markdown": "formulae"},
                {"logical_name": "notes_markdown", "markdown": "other notes"},
            ],
        }
    )
    assert invoker(
        "durable.images.assemble",
        {"answers": [], "validation": [], "notes": [], "formulae": []},
        SimpleNamespace(language="it"),
    ) == {"ok": False, "error_class": "contract_violation"}


def test_image_workload_router_uses_the_attempt_deadline(monkeypatch):
    calls = []

    class Provider:
        def chat(self, _system, _user, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=json.dumps({
                "status": "answered",
                "answer": "Risposta",
                "reason": "",
                "confidence": 1.0,
            }))

    class Router:
        def provider(self, _tier):
            return Provider()

    monkeypatch.setattr("llm_router.LLMRouter", Router)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=2)
    context = SimpleNamespace(
        deadline_at=deadline.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        language="it",
    )
    result = ImagePresetWorkloadInvoker()(
        "durable.images.answer",
        {"question": {"canonical_question_key": "question-a"}},
        context,
    )
    assert result["entries"][0]["status"] == "answered"
    assert 0 < calls[0]["request_timeout_s"] <= 2
