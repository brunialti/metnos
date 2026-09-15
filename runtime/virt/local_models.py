"""Metadata-only local-model selection and sandbox configuration projection.

Semantic model grants never expose the model-tier configuration itself: it may
also contain remote endpoints and credentials. Only local provider options and
the artifacts consumed by that provider cross the sandbox boundary.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import tiers
from .interfaces import EmbeddingUnavailableError

DEFAULT_EMBEDDERS = {
    "text": {"provider": "bge"},
    "image": {"provider": "siglip"},
}
PROJECTION_ENV = "METNOS_LOCAL_MODELS_V1"
PROJECTION_ROOT = Path("/.metnos-models-v1")
RESOURCE_ROLES = {
    "embedding_text:local": "text",
    "embedding_image:local": "image",
    "embedding_face:local": "face",
}
_PROVIDERS = {"text": {"bge", "qwen"}, "image": {"siglip"}}
_ARTIFACTS = {
    "bge": ("tokenizer.json", "onnx/sentence_transformers_int8.onnx"),
    "siglip": (
        "text_model.onnx", "text_model_quantized.onnx", "vision_model.onnx",
        "vision_model_quantized.onnx", "tokenizer.json", "config.json",
        "preprocessor_config.json",
    ),
    "face": ("det_10g.onnx", "w600k_r50.onnx"),
    "qwen": (
        "config.json", "config_sentence_transformers.json", "modules.json",
        "sentence_bert_config.json", "tokenizer.json", "tokenizer_config.json",
        "special_tokens_map.json", "added_tokens.json", "vocab.json",
        "merges.txt", "tokenizer.model", "model.safetensors",
        "model.safetensors.index.json", "model-*.safetensors",
        "pytorch_model.bin", "pytorch_model.bin.index.json", "pytorch_model-*.bin",
    ),
}


def projected_embedding_spec(role: str) -> dict | None:
    """Read only the parent's closed, non-sensitive projection when present."""
    raw = os.environ.get(PROJECTION_ENV)
    if raw is None:
        return None
    try:
        document = json.loads(raw)
        spec = document.get(role)
        if spec is None:
            return None
        if (spec["provider"] not in _PROVIDERS.get(role, ())
                or spec["model_dir"] != str(PROJECTION_ROOT / role)):
            raise ValueError("invalid local model projection")
        result = {"provider": spec["provider"], "model_dir": spec["model_dir"]}
        if spec["provider"] == "qwen" and isinstance(spec.get("query_instruction"), str):
            result["query_instruction"] = spec["query_instruction"]
        return result
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise EmbeddingUnavailableError("invalid local model projection") from exc


def local_embedding_spec(role: str, *, projected: bool = True) -> dict:
    """Select an in-process backend without following a remote tier."""
    if role not in _PROVIDERS:
        raise EmbeddingUnavailableError(f"unsupported local embedding role: {role!r}")
    if projected and PROJECTION_ENV in os.environ:
        spec = projected_embedding_spec(role)
        if spec is None:
            raise EmbeddingUnavailableError("local embedding role is not declared")
        return spec
    configured = tiers.spec("embedding", role, DEFAULT_EMBEDDERS)
    provider = configured.get("provider")
    if provider not in _PROVIDERS[role]:
        return dict(DEFAULT_EMBEDDERS[role])
    result = {"provider": provider}
    if isinstance(configured.get("model_dir"), str) and configured["model_dir"]:
        result["model_dir"] = configured["model_dir"]
    if provider == "qwen" and isinstance(configured.get("query_instruction"), str):
        result["query_instruction"] = configured["query_instruction"]
    return result


def model_spec(role: str) -> dict:
    """Resolve host-side local paths without instantiating or downloading models."""
    import config

    spec = ({"provider": "face"} if role == "face"
            else local_embedding_spec(role, projected=False))
    if role == "face":
        configured = tiers.spec("embedding", role, {role: spec})
        if (configured.get("provider") == "face"
                and isinstance(configured.get("model_dir"), str)
                and configured["model_dir"]):
            spec["model_dir"] = configured["model_dir"]
    provider = spec["provider"]
    if "model_dir" not in spec:
        if provider == "qwen":
            from qwen_embedding import resolved_model_dir
            path = resolved_model_dir()
            # No installed snapshot is an unavailable model, not permission to
            # download one or to grant its surrounding Hugging Face cache.
            if path is None:
                return spec
        else:
            directory, variable = {
                "bge": ("embedding-bge", None),
                "siglip": ("siglip", "METNOS_CLIP_MODEL_DIR"),
                "face": ("face", "METNOS_FACE_MODEL_DIR"),
            }[provider]
            path = (Path(os.environ[variable]) if variable and os.environ.get(variable)
                    else config.PATH_ROOT / "models" / directory)
        spec["model_dir"] = str(path)
    return spec


def model_artifacts(spec: dict) -> tuple[tuple[Path, Path], ...]:
    """Return exact regular files and their relative projection destinations.

    File-level binds preserve cached model symlinks without exposing the cache
    or unrelated contents of a configured model directory. Model code, account
    files and arbitrary configuration names are never included.
    """
    if not spec.get("model_dir"):
        return ()
    root = Path(spec["model_dir"]).expanduser()
    provider = spec["provider"]
    roots = [root]
    if provider == "qwen" and (root / "modules.json").is_file():
        modules = json.loads((root / "modules.json").read_text(encoding="utf-8"))
        if not isinstance(modules, list):
            raise ValueError("invalid local embedding modules")
        for module in modules:
            relative = Path(module["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe local embedding module path")
            if root / relative not in roots:
                roots.append(root / relative)
    artifacts: dict[Path, Path] = {}
    for directory in roots:
        for pattern in _ARTIFACTS[provider]:
            for candidate in sorted(directory.glob(pattern)):
                if candidate.is_file():
                    artifacts[candidate.relative_to(root)] = candidate.resolve(strict=True)
    return tuple((source, relative) for relative, source in sorted(artifacts.items()))
