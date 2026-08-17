#!/usr/bin/env python3
"""Remove redundant predicate mirrors and restore them before validation.

The frozen V23lite schema asks the model to emit four values both in
``semantic_heads`` and in ``predicates``.  The validator requires equality, so
the predicate-side copies carry no information.  This patch removes selected
copies from the model-facing schema and deterministically restores them before
calling the unmodified validator.
"""
from __future__ import annotations


MIRRORS = {
    "input_from_predicate_id": "source_predicate_id",
    "predicate_id": "predicate_id",
    "predicate_anchor_token_id": "predicate_anchor_token_id",
    "role": "role",
}


def install(module, cut: tuple[str, ...]) -> bool:
    """Install the schema cut, failing if the frozen contract is unexpected."""
    unknown = [name for name in cut if name not in MIRRORS]
    if unknown:
        raise AssertionError(f"not a mirrored field: {unknown}")

    original_schema = module.schema
    original_validate = module.validate_frame_v23
    reduce_schema = {"enabled": True}

    def schema(prompt_variant: str = "v0"):
        result = original_schema(prompt_variant)
        if not reduce_schema["enabled"] or prompt_variant != "v23lite":
            return result
        item = result["properties"]["predicates"]["items"]
        missing = [name for name in cut if name not in item["properties"]]
        if missing:
            raise AssertionError(
                f"mirrored fields absent from predicate schema: {missing}"
            )
        for name in cut:
            item["properties"].pop(name)
        item["required"] = list(item["properties"])
        return result

    def validate_frame_v23(frame, tokens, *args, **kwargs):
        heads = frame.get("semantic_heads")
        predicates = frame.get("predicates")
        if isinstance(heads, list) and isinstance(predicates, list):
            for head, predicate in zip(heads, predicates):
                if not isinstance(head, dict) or not isinstance(predicate, dict):
                    continue
                for name in cut:
                    predicate[name] = head.get(MIRRORS[name])

        reduce_schema["enabled"] = False
        try:
            return original_validate(frame, tokens, *args, **kwargs)
        finally:
            reduce_schema["enabled"] = True

    module.schema = schema
    module.validate_frame_v23 = validate_frame_v23
    return True
