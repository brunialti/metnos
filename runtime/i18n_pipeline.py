"""Provider-neutral translation, validation, and admission pipeline.

The module implements RM-0005 F4-F7 as data-driven operations over the F2
registry.  Translation providers receive prose plus BCP-47 tags; structural
validators, not provider promises, decide whether an artifact may advance.
Canonical IDs, TOML schemas, YAML keys, JSON keys, placeholders, code spans,
and HTML markup are never translated.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml
import config as _C

from i18n_materializer import (
    InventoryItem,
    LocalizationPaths,
    inventory,
    iter_localized_text_tables,
    sha256_text,
)
from i18n_registry import LocalizationRegistry, RegistryError, ResourceRecord, normalize_language


Translator = Callable[[str, str, str, str], str]
EquivalenceJudge = Callable[[str, str, str], bool]

_PROMPT_PROSE_FIELDS = frozenset({
    "body", "description", "error", "header", "help", "instruction",
    "label", "message", "must", "must_not", "ok", "preamble", "query",
    "summary", "title", "user", "when",
})
_SENTINEL_RE = re.compile(r"__METNOS_(?:INV|HTML)_[A-Za-z0-9_]+__")
_JINJA_RE = re.compile(r"\{\{[-+]?.*?[-+]?\}\}|\{%[-+]?.*?[-+]?%\}", re.DOTALL)
_FORMAT_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_.]*)(?:![rsa])?(?::[^{}]*)?\}(?!\})")
_CODE_RE = re.compile(r"```[A-Za-z0-9_+\-]*\n.*?\n```|`[^`\n]+`", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class CandidateValidationError(ValueError):
    """A candidate failed deterministic validation.

    ``findings`` keeps rule results machine-readable for callers that need
    diagnostics.  The optional field preserves compatibility with the older
    validation sites, which still raise this exception with a message only.
    """

    def __init__(self, message: str, *, findings: tuple[Any, ...] = ()) -> None:
        super().__init__(message)
        self.findings = findings


@dataclass(frozen=True, slots=True)
class TranslationReport:
    target_lang: str
    translated: int
    failed: int
    skipped: int
    errors: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PromotionReport:
    target_lang: str
    admitted: int
    skipped: int
    errors: Mapping[str, str]


def _atomic_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _atomic_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _default_translator(source: str, source_lang: str, target_lang: str, context: str) -> str:
    del context
    from i18n_translator import translate_document_text
    translated, errors = translate_document_text(
        source, source_lang=source_lang, target_lang=target_lang,
    )
    if translated is None or errors:
        raise RuntimeError("; ".join(errors or ["translation provider returned no text"]))
    return translated


def _candidate_path(
    item: InventoryItem,
    target: str,
    registry: LocalizationRegistry,
    paths: LocalizationPaths,
) -> Path:
    if item.layer == "prompt":
        relative = str(item.metadata["relative_path"])
        return paths.prompts / target / "_pending" / f"{relative}.candidate"
    digest = hashlib.sha256(item.resource_id.encode("utf-8")).hexdigest()
    return registry.path.parent / "i18n_candidates" / target / item.layer / f"{digest}.json"


def _artifact_payload(item: InventoryItem, target: str, translation: Any) -> str:
    return json.dumps({
        "schema": "metnos.localization-candidate/1",
        "resource_id": item.resource_id,
        "layer": item.layer,
        "source_lang": item.source_lang,
        "target_lang": target,
        "source_hash": item.source_hash,
        "translation": translation,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_artifact(record: ResourceRecord) -> Any:
    if not record.artifact_path:
        raise RegistryError(f"candidate artifact unavailable: {record.resource_id}")
    path = Path(record.artifact_path)
    if record.layer == "prompt":
        text = path.read_text(encoding="utf-8")
        if sha256_text(text) != record.translation_hash:
            raise CandidateValidationError("prompt candidate hash does not match the registry")
        return text
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "metnos.localization-candidate/1"
        or payload.get("resource_id") != record.resource_id
        or payload.get("source_hash") != record.source_hash
    ):
        raise CandidateValidationError("candidate identity does not match the registry")
    translation = payload.get("translation")
    encoded = json.dumps(
        translation, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    if sha256_text(encoded) != record.translation_hash:
        raise CandidateValidationError("candidate hash does not match the registry")
    return translation


def _update_state(path: Path, selector: str, target: str, record: ResourceRecord) -> None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    if path.name == ".lang_state.json":
        entry = state.setdefault(selector, {})
    else:
        entry = state.setdefault(selector, {}).setdefault(target, {})
    entry.update({
        "status": "admitted", "source_lang": record.source_lang,
        "source_hash": "sha256:" + record.source_hash,
        "version_hash": "sha256:" + str(record.translation_hash or ""),
    })
    _atomic_text(path, json.dumps(
        state, ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n", mode=0o644)


def _tokens(pattern: re.Pattern[str], value: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in pattern.finditer(value):
        found.append(match.group(0) if match.lastindex is None else match.group(1))
    return tuple(found)


def _jinja_tokens(value: str) -> tuple[str, ...]:
    """Return Jinja tokens with insignificant placeholder padding removed.

    Only whitespace immediately inside ``{{`` and ``}}`` is insignificant.
    Whitespace inside the expression, control-block syntax, order, and
    multiplicity remain exact invariants.
    """
    return tuple(
        "{{" + token[2:-2].strip() + "}}"
        if token.startswith("{{") and token.endswith("}}")
        else token
        for token in _tokens(_JINJA_RE, value)
    )


def _validate_common(source: str, translated: str) -> None:
    if not isinstance(translated, str) or not translated.strip():
        raise CandidateValidationError("translation is empty")
    if _jinja_tokens(source) != _jinja_tokens(translated):
        raise CandidateValidationError("jinja invariants changed")
    for name, pattern in (("format", _FORMAT_RE), ("code", _CODE_RE)):
        if _tokens(pattern, source) != _tokens(pattern, translated):
            raise CandidateValidationError(f"{name} invariants changed")
    if _SENTINEL_RE.search(translated):
        raise CandidateValidationError("translation contains an unresolved sentinel")
    ratio = len(translated) / max(1, len(source))
    if ratio < 0.35 or ratio > 3.5:
        raise CandidateValidationError(f"length ratio out of bounds: {ratio:.3f}")


def _set_prompt_lang(text: str, target: str) -> str:
    return re.sub(
        r"^(\s*lang\s*:\s*)[^\s#]+", lambda match: match.group(1) + target,
        text, count=1, flags=re.MULTILINE,
    )


def _validate_jinja(source: str, translated: str) -> None:
    _validate_common(source, translated)
    try:
        import minijinja
        env = minijinja.Environment()
        env.undeclared_variables_in_str(translated)
    except Exception as exc:
        raise CandidateValidationError(f"MiniJinja parse failed: {exc}") from exc


def _translate_yaml_node(
    node: Any,
    *,
    source_lang: str,
    target_lang: str,
    context: str,
    translator: Translator,
    field_name: str = "",
) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "lang" and isinstance(value, str):
                out[key] = target_lang
            else:
                out[key] = _translate_yaml_node(
                    value, source_lang=source_lang, target_lang=target_lang,
                    context=f"{context}.{key}", translator=translator,
                    field_name=str(key),
                )
        return out
    if isinstance(node, list):
        return [
            _translate_yaml_node(
                value, source_lang=source_lang, target_lang=target_lang,
                context=f"{context}[{index}]", translator=translator,
                field_name=field_name,
            )
            for index, value in enumerate(node)
        ]
    if isinstance(node, str) and field_name in _PROMPT_PROSE_FIELDS:
        translated = translator(node, source_lang, target_lang, context)
        _validate_common(node, translated)
        return translated
    return node


def _yaml_shape(node: Any, *, field_name: str = "") -> Any:
    if isinstance(node, dict):
        return {
            key: (
                "<lang>" if key == "lang" and isinstance(value, str)
                else _yaml_shape(value, field_name=str(key))
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_yaml_shape(value, field_name=field_name) for value in node]
    if isinstance(node, str) and field_name in _PROMPT_PROSE_FIELDS:
        return "<prose>"
    return node


def _translate_yaml(item: InventoryItem, target: str, translator: Translator) -> str:
    source_obj = yaml.safe_load(item.source_text)
    translated_obj = _translate_yaml_node(
        source_obj, source_lang=item.source_lang, target_lang=target,
        context=item.resource_id, translator=translator,
    )
    if _yaml_shape(source_obj) != _yaml_shape(translated_obj):
        raise CandidateValidationError("YAML structure or invariant scalar changed")
    return yaml.safe_dump(translated_obj, allow_unicode=True, sort_keys=False)


def _mask_html(source: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}
    counter = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal counter
        token = f"__METNOS_HTML_{counter:08d}__"
        counter += 1
        mapping[token] = match.group(0)
        return token

    protected = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", replace, source, flags=re.I | re.S)
    return _HTML_TAG_RE.sub(replace, protected), mapping


def _translate_html(item: InventoryItem, target: str, translator: Translator) -> str:
    masked, mapping = _mask_html(item.source_text)
    translated = translator(masked, item.source_lang, target, item.resource_id)
    for token, original in mapping.items():
        translated = translated.replace(token, original)
    _validate_common(item.source_text, translated)
    if _HTML_TAG_RE.findall(item.source_text) != _HTML_TAG_RE.findall(translated):
        raise CandidateValidationError("HTML markup changed")
    return _localize_html_metadata(
        translated, source_lang=item.source_lang, target_lang=target,
    )


def _localize_html_metadata(text: str, *, source_lang: str, target_lang: str) -> str:
    """Project locale metadata without translating arbitrary HTML markup."""
    localized = re.sub(
        r'(<html\b[^>]*\blang\s*=\s*["\'])[^"\']+(["\'])',
        lambda match: match.group(1) + target_lang + match.group(2),
        text, count=1, flags=re.I,
    )
    localized = re.sub(
        r'(<link\b[^>]*\brel\s*=\s*["\']canonical["\'][^>]*\bhref\s*=\s*["\'][^"\']*)'
        + re.escape(f"/{source_lang}/"),
        lambda match: match.group(1) + f"/{target_lang}/",
        localized, count=1, flags=re.I,
    )
    canonical = re.search(
        r'<link\b[^>]*\brel\s*=\s*["\']canonical["\'][^>]*\bhref\s*=\s*["\']([^"\']+)',
        localized, flags=re.I,
    )
    if canonical and not re.search(
        r'<link\b[^>]*\brel\s*=\s*["\']alternate["\'][^>]*\bhreflang\s*=\s*["\']'
        + re.escape(target_lang) + r'["\']',
        localized, flags=re.I,
    ):
        link = (
            f'  <link rel="alternate" hreflang="{target_lang}" '
            f'href="{canonical.group(1)}">\n'
        )
        localized = re.sub(r"</head>", link + "</head>", localized, count=1, flags=re.I)
    return localized


def _translate_input(item: InventoryItem, target: str, translator: Translator) -> Any:
    source = json.loads(item.source_text)
    kind = str(item.metadata.get("kind") or "")
    if kind == "regex":
        raise CandidateValidationError("regular expressions require explicit review")

    def translate_forms(node: Any, context: str) -> Any:
        if isinstance(node, list):
            out = []
            seen: set[str] = set()
            for index, value in enumerate(node):
                if not isinstance(value, str):
                    raise CandidateValidationError("lexicon form must be a string")
                translated = translator(
                    value, item.source_lang, target, f"{context}[{index}]",
                ).strip()
                _validate_common(value, translated)
                folded = translated.casefold()
                if translated and folded not in seen:
                    out.append(translated)
                    seen.add(folded)
            if not out:
                raise CandidateValidationError("lexicon form list is empty")
            return out
        if isinstance(node, dict):
            # Mapping keys are canonical concepts and therefore invariant.
            return {key: translate_forms(value, f"{context}.{key}") for key, value in node.items()}
        raise CandidateValidationError("unsupported lexicon payload")

    translated = translate_forms(source, item.resource_id)
    if isinstance(source, dict) and tuple(source) != tuple(translated):
        raise CandidateValidationError("canonical mapping keys changed")
    return translated


def _translate_item(item: InventoryItem, target: str, translator: Translator) -> Any:
    if item.layer == "prompt":
        if item.metadata.get("format") == "yaml":
            return _translate_yaml(item, target, translator)
        translated = translator(item.source_text, item.source_lang, target, item.resource_id)
        translated = _set_prompt_lang(translated, target)
        source_for_validation = _set_prompt_lang(item.source_text, target)
        _validate_jinja(source_for_validation, translated)
        return translated
    if item.layer == "contract":
        translated = translator(item.source_text, item.source_lang, target, item.resource_id)
        _validate_common(item.source_text, translated)
        selector = item.metadata.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise CandidateValidationError("contract selector is unavailable")
        from manifest_lint import lint_contract_translation
        findings = lint_contract_translation(
            item.source_text,
            translated,
            resource=selector,
            source_language=item.source_lang,
            target_language=target,
        )
        errors = tuple(finding for finding in findings if finding.severity == "error")
        if errors:
            checks = ", ".join(sorted({finding.check for finding in errors}))
            raise CandidateValidationError(
                f"contract translation invariants changed: {checks}",
                findings=errors,
            )
        return translated.strip()
    if item.layer == "message":
        translated = translator(item.source_text, item.source_lang, target, item.resource_id)
        _validate_common(item.source_text, translated)
        return translated.strip()
    if item.layer == "input":
        return _translate_input(item, target, translator)
    if item.layer == "knowledge":
        return _translate_html(item, target, translator)
    raise CandidateValidationError(f"derived layer {item.layer!r} has no translation payload")


def translate_pending(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths | None = None,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
    translator: Translator | None = None,
    limit: int = 0,
) -> TranslationReport:
    paths = paths or LocalizationPaths()
    target = normalize_language(target_lang)
    items = {item.resource_id: item for item in inventory(paths, source_lang=source_lang)}
    provider = translator or _default_translator
    translated_count = failed = skipped = 0
    errors: dict[str, str] = {}
    processed = 0
    for record in registry.resources(target):
        if limit > 0 and processed >= limit:
            break
        item = items.get(record.resource_id)
        if item is None or record.status in {"translated", "admitted", "manual_review"}:
            skipped += 1
            continue
        if record.layer in {"device", "tutor"}:
            skipped += 1
            continue
        lease = registry.claim(record.resource_id, target)
        if lease is None:
            skipped += 1
            continue
        processed += 1
        try:
            translation = _translate_item(item, target, provider)
            candidate = _candidate_path(item, target, registry, paths)
            if item.layer == "prompt":
                _atomic_text(candidate, str(translation), mode=0o644)
                digest_source = str(translation)
            else:
                payload = _artifact_payload(item, target, translation)
                _atomic_text(candidate, payload)
                digest_source = json.dumps(translation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            quality = "candidate" if item.layer == "knowledge" else "structural"
            registry.complete(
                item.resource_id, target, sha256_text(digest_source), quality,
                lease_token=lease.lease_token, artifact_path=str(candidate),
            )
            translated_count += 1
        except Exception as exc:  # validation/provider failures are retryable and bounded
            error_class = type(exc).__name__
            try:
                registry.fail(
                    record.resource_id, target, error_class,
                    lease_token=lease.lease_token,
                )
            except Exception:
                pass
            failed += 1
            errors[record.resource_id] = f"{error_class}: {exc}"
    return TranslationReport(
        target_lang=target, translated=translated_count, failed=failed,
        skipped=skipped, errors=dict(sorted(errors.items())),
    )


def review_semantics(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths | None = None,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
    judge: EquivalenceJudge,
    limit: int = 0,
) -> dict[str, bool]:
    """Review every translated prose surface with one equivalence contract."""
    paths = paths or LocalizationPaths()
    target = normalize_language(target_lang)
    items = {item.resource_id: item for item in inventory(paths, source_lang=source_lang)}
    results: dict[str, bool] = {}
    prompt_evidence: list[str] = []
    knowledge_evidence: list[str] = []
    contract_evidence: list[str] = []
    input_evidence: list[str] = []
    judged = 0
    for record in registry.resources(target):
        if record.status != "translated" or record.layer not in {
            "prompt", "knowledge", "contract", "message", "input",
        }:
            continue
        item = items.get(record.resource_id)
        if item is None:
            continue
        translated = _read_artifact(record)
        if record.quality == "reviewed":
            accepted = True
        elif limit > 0 and judged >= limit:
            continue
        else:
            accepted = bool(
                judge(item.source_text, str(translated), item.resource_id)
            )
            judged += 1
        results[record.resource_id] = accepted
        if accepted and record.quality != "reviewed":
            registry.review(record.resource_id, target, quality="reviewed")
        if accepted:
            evidence = sha256_text(item.source_hash + str(record.translation_hash))
            if record.layer == "prompt":
                prompt_evidence.append(evidence)
            elif record.layer == "knowledge":
                knowledge_evidence.append(evidence)
            elif record.layer == "input":
                input_evidence.append(evidence)
            else:
                contract_evidence.append(evidence)
    prompt_records = [row for row in registry.resources(target) if row.layer == "prompt"]
    prompt_ok = bool(prompt_records) and all(
        row.status in {"translated", "admitted"} and row.quality == "reviewed"
        for row in registry.resources(target) if row.layer == "prompt"
    )
    registry.record_check(
        "planner_proposer_equivalence", target,
        "passed" if prompt_ok else "failed",
        evidence_hash=sha256_text("\n".join(sorted(prompt_evidence))),
        details={"resources": len(prompt_records)},
    )
    knowledge_records = [row for row in registry.resources(target) if row.layer == "knowledge"]
    knowledge_ok = bool(knowledge_records) and all(
        row.status in {"translated", "admitted"} and row.quality == "reviewed"
        for row in registry.resources(target) if row.layer == "knowledge"
    )
    registry.record_check(
        "public_knowledge_review", target,
        "passed" if knowledge_ok else "failed",
        evidence_hash=sha256_text("\n".join(sorted(knowledge_evidence))),
        details={"resources": len(knowledge_records)},
    )
    contract_records = [
        row for row in registry.resources(target)
        if row.layer in {"contract", "message"}
    ]
    contract_ok = bool(contract_records) and all(
        row.status in {"translated", "admitted"} and row.quality == "reviewed"
        for row in contract_records
    )
    registry.record_check(
        "contract_message_equivalence", target,
        "passed" if contract_ok else "failed",
        evidence_hash=sha256_text("\n".join(sorted(contract_evidence))),
        details={"resources": len(contract_records)},
    )
    input_records = [
        row for row in registry.resources(target)
        if row.layer == "input" and row.status != "manual_review"
    ]
    input_ok = bool(input_records) and all(
        row.status in {"translated", "admitted"} and row.quality == "reviewed"
        for row in input_records
    )
    registry.record_check(
        "input_lexicon_equivalence", target,
        "passed" if input_ok else "failed",
        evidence_hash=sha256_text("\n".join(sorted(input_evidence))),
        details={"resources": len(input_records)},
    )
    return results


def default_equivalence_judge(source: str, target: str, resource_id: str) -> bool:
    """Use the configured fidelity workload as a strict, provider-neutral judge."""
    from llm_helpers import call_llm
    from llm_workloads import tier_for
    payload = json.dumps({
        "resource_id": resource_id, "source": source, "candidate": target,
    }, ensure_ascii=False)
    prompt = (
        "Assess whether candidate preserves every instruction, prohibition, "
        "fact, placeholder, identifier, and deontic strength of source. "
        "Different languages are expected. Reply only as JSON "
        f'{{"equivalent": true|false}}. Input: {payload}'
    )
    raw, _meta = call_llm(
        prompt, "You are a strict localization equivalence verifier.",
        tier=tier_for("translation.i18n"), max_tokens=120,
    )
    try:
        parsed = json.loads(str(raw or "").strip().removeprefix("```json").removesuffix("```").strip())
    except (TypeError, json.JSONDecodeError):
        return False
    return parsed.get("equivalent") is True


def _replace_toml_language(text: str, selector: str, target: str, value: str) -> str:
    from i18n_translator import _replace_lang_in_section
    return _replace_lang_in_section(text, f"[{selector}]", target, value)


def _strip_target_prose(manifest: dict[str, Any], target: str) -> dict[str, Any]:
    clone = copy.deepcopy(manifest)

    def visit(node: Any, key_name: str = "") -> None:
        if not isinstance(node, dict):
            return
        if key_name in {"description", "summary", "title", "label", "help", "message"}:
            node.pop(target, None)
        for key, value in list(node.items()):
            visit(value, str(key))

    visit(clone)
    return clone


def _promote_contracts(
    records: list[ResourceRecord], target: str, signer: Callable[[Path], Any],
) -> tuple[int, dict[str, str]]:
    grouped: dict[Path, list[ResourceRecord]] = {}
    for record in records:
        grouped.setdefault(Path(str(record.metadata["manifest_path"])), []).append(record)
    promoted = 0
    errors: dict[str, str] = {}
    for manifest_path, manifest_records in grouped.items():
        original_text = manifest_path.read_text(encoding="utf-8")
        signature_path = manifest_path.with_name("manifest.toml.sig")
        original_signature = (
            signature_path.read_bytes() if signature_path.is_file() else None
        )
        try:
            original = tomllib.loads(original_text)
            changed = original_text
            for record in manifest_records:
                translated = _read_artifact(record)
                if not isinstance(translated, str):
                    raise CandidateValidationError("contract translation must be text")
                changed = _replace_toml_language(
                    changed, str(record.metadata["selector"]), target, translated,
                )
            parsed = tomllib.loads(changed)
            if _strip_target_prose(original, target) != _strip_target_prose(parsed, target):
                raise CandidateValidationError("manifest technical contract changed")
            _atomic_text(manifest_path, changed, mode=0o644)
            signer(manifest_path.parent)
            state_path = manifest_path.with_name("manifest.lang_state.json")
            for record in manifest_records:
                _update_state(
                    state_path, str(record.metadata["selector"]), target, record,
                )
            promoted += len(manifest_records)
        except Exception as exc:
            # The live contract must never be left modified with a missing or
            # partial signature.  Configuration activation happens later, but
            # the currently running locale still loads this same manifest.
            _atomic_text(manifest_path, original_text, mode=0o644)
            if original_signature is None:
                try:
                    signature_path.unlink()
                except FileNotFoundError:
                    pass
            else:
                _atomic_bytes(signature_path, original_signature)
            for record in manifest_records:
                errors[record.resource_id] = f"{type(exc).__name__}: {exc}"
    return promoted, errors


def _promote_message(db: Path, record: ResourceRecord, target: str, translation: str) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """UPDATE i18n SET text=?,needs_translation=0,version_hash=?,
               source_text_hash=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE key=? AND lang=?""",
            (
                translation, "sha256:" + sha256_text(translation),
                "sha256:" + record.source_hash, record.metadata["key"], target,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _promote_input(db: Path, record: ResourceRecord, target: str, translation: Any) -> None:
    encoded = json.dumps(translation, ensure_ascii=False, sort_keys=True)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """UPDATE detection_lexicon SET payload=?,needs_translation=0,
               version_hash=?,source_text_hash=?,
               updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE concept=? AND lang=?""",
            (
                encoded, "sha256:" + sha256_text(encoded),
                "sha256:" + record.source_hash, record.metadata["concept"], target,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def promote_candidates(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths | None = None,
    signer: Callable[[Path], Any] | None = None,
) -> PromotionReport:
    """Promote validated artifacts; instance configuration is untouched."""
    paths = paths or LocalizationPaths()
    target = normalize_language(target_lang)
    if signer is None:
        from sign import sign_executor
        signer = sign_executor
    records = list(registry.resources(target))
    contract_records = [record for record in records if record.layer == "contract" and record.status == "translated"]
    admitted, errors = _promote_contracts(contract_records, target, signer)
    for record in contract_records:
        if record.resource_id not in errors:
            registry.admit(record.resource_id, target)
    skipped = 0
    for record in records:
        if record.layer == "contract":
            continue
        if record.status != "translated":
            skipped += 1
            continue
        try:
            translation = _read_artifact(record)
            if record.layer == "prompt":
                candidate = Path(str(record.artifact_path))
                relative = str(record.metadata["relative_path"])
                _atomic_text(paths.prompts / target / relative, str(translation), mode=0o644)
                _update_state(
                    paths.prompts / target / ".lang_state.json",
                    relative, target, record,
                )
            elif record.layer == "message":
                _promote_message(paths.messages_db, record, target, str(translation))
            elif record.layer == "input":
                _promote_input(paths.detection_db, record, target, translation)
            elif record.layer == "knowledge":
                if record.quality != "reviewed":
                    raise CandidateValidationError("public knowledge has not passed review")
                relative = str(record.metadata["relative_path"])
                _atomic_text(paths.docs / target / relative, str(translation), mode=0o644)
            else:
                skipped += 1
                continue
            registry.admit(record.resource_id, target)
            admitted += 1
        except Exception as exc:
            errors[record.resource_id] = f"{type(exc).__name__}: {exc}"
    return PromotionReport(
        target_lang=target, admitted=admitted, skipped=skipped,
        errors=dict(sorted(errors.items())),
    )
