"""Compile bounded F2 knowledge units from admitted, local sources.

Public HTML comes from the same bounded inventory deployed on metnos.com;
``tutor/sources.toml`` is reserved for selected supplemental sources. Executor
facts come only from the verified live loader. The resulting units are data
for the signed Tutor catalog, not an alternate mutable store.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tomllib

from published_docs import (
    PUBLICATION_ROOT,
    catalog as published_documents,
    require_public_material,
)

from .cards import REPO_ROOT


SOURCES_CONFIG = REPO_ROOT / "tutor" / "sources.toml"
_AUDIENCES = frozenset({"user", "instance_admin"})
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_LANG = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{1,8})*$", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_MAX_CHARS = 920  # comfortably below BGE-M3's 256-token runtime cap
_OVERLAP_CHARS = 100


@dataclass(frozen=True, slots=True)
class KnowledgeUnit:
    unit_id: str
    concept_id: str
    lang: str
    audience: str
    source_kind: str
    authority: str
    priority: int
    title: str
    text: str
    semantic: str
    source_ref: str
    content_hash: str
    # Present only for documents admitted by `published_docs.catalog()`.
    # The URL is canonical, signed into the Tutor catalog, and safe to expose
    # as a navigable citation. Internal files and runtime registries stay empty.
    public_url: str = ""
    # Present only on units generated from the closed observation-view
    # registry.  A UI page, document, executor manifest, learned association,
    # or semantic neighbour can therefore never authorize a live probe.
    observation_ref: str = ""

    def visible_to(self, audience: str) -> bool:
        rank = {"user": 0, "instance_admin": 1}
        return rank.get(audience, -1) >= rank[self.audience]


class _HTMLBlocks(HTMLParser):
    """Extract headings and textual blocks while dropping active/chrome data."""

    _IGNORE = frozenset({"head", "script", "style", "svg", "nav", "footer"})
    _BLOCKS = frozenset({"h1", "h2", "h3", "p", "li", "pre", "tr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._ignore_depth = 0
        self._capture_tag = ""
        self._capture_depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self._ignore_depth:
            if tag not in {"br", "hr", "img", "input", "meta", "link"}:
                self._ignore_depth += 1
            return
        if tag in self._IGNORE:
            self._ignore_depth = 1
            return
        classes = {
            token
            for key, value in attrs
            if key.lower() == "class" and value
            for token in str(value).lower().split()
        }
        if ({"toc", "lang-switch", "tutor-exclude"} & classes):
            self._ignore_depth = 1
            return
        if self._capture_tag:
            if tag in {"br", "hr", "img", "input", "meta", "link"}:
                self._buffer.append(" ")
                return
            self._capture_depth += 1
            if tag in {"td", "th"}:
                self._buffer.append(" ")
            return
        if tag in self._BLOCKS:
            self._capture_tag = tag
            self._capture_depth = 1
            self._buffer = []

    def handle_startendtag(self, tag: str, attrs) -> None:
        if not self._ignore_depth and self._capture_tag and tag.lower() == "br":
            self._buffer.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignore_depth:
            self._ignore_depth -= 1
            return
        if not self._capture_tag:
            return
        self._capture_depth -= 1
        if self._capture_depth > 0:
            return
        text = _SPACE.sub(" ", "".join(self._buffer)).strip()
        if text:
            self.blocks.append((self._capture_tag, text))
        self._capture_tag = ""
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture_tag and not self._ignore_depth:
            self._buffer.append(data)


def _bounded_parts(text: str) -> tuple[str, ...]:
    """Split long blocks on words with a small retrieval-only overlap."""

    clean = _SPACE.sub(" ", text).strip()
    if len(clean) <= _MAX_CHARS:
        return (clean,) if clean else ()
    parts: list[str] = []
    cursor = 0
    while cursor < len(clean):
        end = min(len(clean), cursor + _MAX_CHARS)
        if end < len(clean):
            boundary = clean.rfind(" ", cursor + (_MAX_CHARS // 2), end)
            if boundary > cursor:
                end = boundary
        part = clean[cursor:end].strip()
        if part:
            parts.append(part)
        if end >= len(clean):
            break
        cursor = max(cursor + 1, end - _OVERLAP_CHARS)
    return tuple(parts)


def _unit(*, unit_id: str, concept_id: str, lang: str, audience: str,
          source_kind: str,
          authority: str, priority: int, title: str, text: str,
          source_ref: str, semantic: str = "", public_url: str = "",
          observation_ref: str = "",
          ) -> KnowledgeUnit:
    # The embedding text is built as ``title. semantic`` by the catalog, so
    # the default semantic body is the text alone: repeating the title would
    # dilute long sections and push their tail past the embedder token cap.
    semantic = str(semantic or text).strip()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return KnowledgeUnit(
        unit_id=unit_id,
        concept_id=concept_id,
        lang=lang,
        audience=audience,
        source_kind=source_kind,
        authority=authority,
        priority=priority,
        title=title,
        text=text,
        semantic=semantic,
        source_ref=source_ref,
        content_hash=f"sha256:{digest}",
        public_url=public_url,
        observation_ref=str(observation_ref or ""),
    )


def _document_units(*, source_id: str, lang: str, path: Path,
                    audience: str, source_kind: str,
                    priority: int,
                    concept_prefix: str | None = None,
                    public_url: str = "") -> list[KnowledgeUnit]:
    parser = _HTMLBlocks()
    parser.feed(path.read_text(encoding="utf-8"))
    units: list[KnowledgeUnit] = []
    heading = source_id.replace("-", " ")
    pending: list[str] = []
    pending_size = 0

    def flush() -> None:
        nonlocal pending, pending_size
        body = " ".join(pending).strip()
        pending = []
        pending_size = 0
        if not body:
            return
        for part in _bounded_parts(body):
            ordinal = len(units) + 1
            units.append(_unit(
                unit_id=f"doc-{source_id}-{lang}-{ordinal:04d}",
                concept_id=(
                    f"{concept_prefix}-{ordinal:04d}"
                    if concept_prefix else f"doc-{source_id}-{ordinal:04d}"
                ),
                lang=lang,
                audience=audience,
                source_kind=source_kind,
                authority="published_documentation",
                priority=priority,
                title=heading,
                text=part,
                source_ref=f"{path.relative_to(REPO_ROOT).as_posix()}#{ordinal}",
                public_url=public_url,
            ))

    for tag, text in parser.blocks:
        if tag in {"h1", "h2", "h3"}:
            flush()
            heading = text
            continue
        for part in _bounded_parts(text):
            projected = pending_size + len(part) + (1 if pending else 0)
            if pending and projected > _MAX_CHARS:
                flush()
            pending.append(part)
            pending_size += len(part) + (1 if pending_size else 0)
    flush()
    return units


def _resolve_language_paths(template: str, languages: tuple[str, ...], *,
                            repo_root: Path = REPO_ROOT) -> dict[str, Path]:
    """Resolve one bilingual source template without filesystem crawling."""

    if template.count("{lang}") != 1:
        raise ValueError("Tutor source path must contain one {lang} component")
    parts = Path(template).parts
    if "{lang}" not in parts or Path(template).is_absolute() or ".." in parts:
        raise ValueError("Tutor source {lang} must be a complete path component")
    root = repo_root.resolve()
    resolved: dict[str, Path] = {}
    for raw_lang in languages:
        lang = str(raw_lang).lower()
        if not _LANG.fullmatch(lang):
            raise ValueError(f"invalid Tutor document language: {raw_lang!r}")
        candidate = require_public_material(
            repo_root / template.replace("{lang}", lang),
            root=root,
            label="Tutor source",
        )
        if not candidate.is_file():
            raise ValueError(f"Tutor source unavailable: {candidate}")
        if lang in resolved:
            raise ValueError(f"duplicate Tutor language: {lang}")
        resolved[lang] = candidate
    return resolved


def _read_registry() -> tuple[dict, ...]:
    with SOURCES_CONFIG.open("rb") as handle:
        raw = tomllib.load(handle)
    declared = raw.get("source", [])
    if raw.get("version") != 2 or not isinstance(declared, list):
        raise ValueError("unsupported Tutor source registry")
    seen: set[str] = set()
    sources: list[dict] = []
    publication_root = PUBLICATION_ROOT.resolve()
    for item in declared:
        source_id = str(item.get("id") or "")
        audience = str(item.get("audience") or "")
        template = item.get("path")
        legacy_paths = item.get("paths")
        if (not _SOURCE_ID.fullmatch(source_id) or source_id in seen
                or audience not in _AUDIENCES
                or not (isinstance(template, str)
                        or isinstance(legacy_paths, dict))):
            raise ValueError(f"invalid Tutor source declaration: {source_id!r}")
        if isinstance(template, str):
            if legacy_paths is not None:
                raise ValueError(f"ambiguous Tutor source declaration: {source_id}")
            languages = tuple(item.get("languages") or ())
            if not languages:
                raise ValueError(
                    f"supplemental Tutor source has no languages: {source_id}"
                )
            resolved = _resolve_language_paths(template, languages)
        else:
            # Compatibility for an old private registry during rolling
            # upgrades.  New declarations always use the language template.
            resolved = {}
            for lang, relative in legacy_paths.items():
                lang = str(lang).lower()
                candidate = require_public_material(
                    REPO_ROOT / str(relative),
                    root=REPO_ROOT,
                    label="Tutor source",
                )
                if not _LANG.fullmatch(lang) or not candidate.is_file():
                    raise ValueError(f"Tutor source unavailable: {relative!r}")
                resolved[lang] = candidate
        if not resolved:
            raise ValueError(f"Tutor source has no languages: {source_id}")
        for candidate in resolved.values():
            try:
                candidate.relative_to(publication_root)
            except ValueError:
                continue
            raise ValueError(
                f"public Tutor source must not be declared twice: {source_id}"
            )
        sources.append({
            "id": source_id,
            "audience": audience,
            "kind": str(item.get("kind") or "manual"),
            "priority": int(item.get("priority") or 0),
            "paths": resolved,
        })
        seen.add(source_id)
    return tuple(sources)


def declared_source_files() -> tuple[Path, ...]:
    # The compiler implementation is part of the materialized-corpus identity:
    # changing how admitted manifests are projected must invalidate the catalog
    # even when no manifest or public page changed in the same deployment.
    files = [
        SOURCES_CONFIG,
        Path(__file__).resolve(),
        REPO_ROOT / "runtime" / "published_docs.py",
        REPO_ROOT / "runtime" / "services_registry.py",
        REPO_ROOT / "runtime" / "ui_surfaces.py",
    ]
    files.extend(document.path for document in published_documents())
    for source in _read_registry():
        files.extend(source["paths"].values())
    return tuple(sorted(set(files), key=lambda item: str(item)))


def executor_catalog_stamp() -> str:
    """Cheap identity of the admitted set used to decide whether to rebuild.

    The loader already owns signature verification and lifecycle resolution.
    We add file stat identity because manifests may live outside the repository
    (installed skills and synthesized executors).
    """

    from loader import load_catalog

    rows = []
    for executor in sorted(load_catalog(), key=lambda item: item.name):
        path = Path(getattr(executor, "manifest_path", ""))
        try:
            stat = path.stat()
            file_identity = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            file_identity = (0, 0)
        rows.append((
            executor.name,
            str(getattr(executor, "version", "") or ""),
            str(getattr(executor, "membership", "") or ""),
            str(getattr(executor, "lifecycle", "") or ""),
            bool(getattr(executor, "dormant", False)),
            str(path),
            file_identity,
        ))
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"),
                         sort_keys=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _manifest_document(executor) -> dict:
    path = Path(getattr(executor, "manifest_path", ""))
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _localized_descriptions(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(lang).lower(): _SPACE.sub(" ", str(text)).strip()
        for lang, text in value.items()
        if _LANG.fullmatch(str(lang)) and str(text).strip()
    }


def _manifest_descriptions(executor, manifest: dict | None = None) -> dict[str, str]:
    raw = manifest if manifest is not None else _manifest_document(executor)
    descriptions = raw.get("description")
    if isinstance(descriptions, dict):
        out = _localized_descriptions(descriptions)
        if out:
            return out
    fallback = _SPACE.sub(
        " ", str(getattr(executor, "description", "") or "")).strip()
    if not fallback:
        return {}
    try:
        import config
        lang = str(config.DEFAULT_LANG or "en").lower().split("-", 1)[0]
    except Exception:
        lang = "en"
    return {lang: fallback}


def _manifest_affinity(manifest: dict | None) -> tuple[str, ...]:
    """Return the canonical semantic vocabulary admitted by a manifest.

    ``affinity`` is already the executor catalog's reviewed, multilingual
    semantic surface.  Projecting it as the *embedding-only* body lets the
    parent operation compete with its argument fragments without exposing
    routing metadata in Tutor's answer or teaching query-specific aliases.
    """

    raw = manifest.get("affinity") if isinstance(manifest, dict) else None
    if not isinstance(raw, list):
        return ()
    return tuple(dict.fromkeys(
        _SPACE.sub(" ", str(value)).strip()
        for value in raw
        if _SPACE.sub(" ", str(value)).strip()
    ))


def _semantic_with_parent(parent: str, detail: str) -> str:
    """Embed a child contract together with its parent operation.

    An argument such as ``email`` is not a standalone capability: it may be a
    recipient, an account selector, or a sharing destination.  The reviewed
    manifest affinity provides that missing provenance.  The exact child text
    remains intact and receives the bounded semantic budget before its parent.
    """

    child = _SPACE.sub(" ", str(detail or "")).strip()
    context = _SPACE.sub(" ", str(parent or "")).strip()
    if not context:
        return child
    available = _MAX_CHARS - len(child) - 2
    if available <= 0:
        return child[:_MAX_CHARS].rstrip()
    if len(context) > available:
        boundary = context.rfind(" ", 0, available + 1)
        context = context[:boundary if boundary > 0 else available].rstrip()
    return f"{context}. {child}" if context else child


def _manifest_argument_descriptions(
        executor, manifest: dict | None = None,
) -> tuple[tuple[str, str, str, str], ...]:
    """Return ``(path, lang, schema, description)`` from manifest args.

    Argument descriptions are part of the admitted executor contract.  They
    often contain the operational detail absent from the short top-level
    description (account selectors, credential bindings, accepted values).
    Nested object and array properties are walked deterministically; no other
    filesystem source is consulted.
    """

    raw = manifest if manifest is not None else _manifest_document(executor)
    args = raw.get("args") if isinstance(raw, dict) else None
    root = args.get("properties") if isinstance(args, dict) else None
    if not isinstance(root, dict):
        return ()
    rows: list[tuple[str, str, str, str]] = []

    def schema_text(spec: dict) -> str:
        fields = []
        for key in ("type", "format", "default", "enum"):
            if key not in spec:
                continue
            rendered = json.dumps(
                spec[key], ensure_ascii=False, sort_keys=True, default=str)
            fields.append(f"{key}={rendered}")
        if spec.get("runtime_resolved") is True:
            fields.append("runtime_resolved=true")
        return "; ".join(fields)

    def walk(properties: dict, prefix: str = "") -> None:
        for raw_name, raw_spec in sorted(properties.items()):
            if not isinstance(raw_spec, dict):
                continue
            name = str(raw_name)
            path = f"{prefix}.{name}" if prefix else name
            schema = schema_text(raw_spec)
            for lang, description in sorted(
                    _localized_descriptions(raw_spec.get("description")).items()):
                rows.append((path, lang, schema, description))
            nested = raw_spec.get("properties")
            if isinstance(nested, dict):
                walk(nested, path)
            items = raw_spec.get("items")
            if isinstance(items, dict) and isinstance(items.get("properties"), dict):
                walk(items["properties"], f"{path}[]")

    walk(root)
    return tuple(rows)


def _executor_units() -> list[KnowledgeUnit]:
    from loader import load_catalog

    units: list[KnowledgeUnit] = []
    catalog = load_catalog()
    for executor in sorted(catalog, key=lambda item: item.name):
        manifest = _manifest_document(executor)
        descriptions = _manifest_descriptions(executor, manifest)
        affinity = _manifest_affinity(manifest)
        if not descriptions:
            continue
        membership = str(getattr(executor, "membership", "") or "unknown")
        lifecycle = str(getattr(executor, "lifecycle", "") or "active")
        availability = "dormant" if getattr(executor, "dormant", False) else lifecycle
        effect = str(
            (getattr(executor, "execution_policy", {}) or {}).get("effect")
            or "unknown")
        platforms = ", ".join(getattr(executor, "platforms", ()) or ())
        audience = "instance_admin" if executor.name == "admin" else "user"
        priority = 100 if membership == "builtin" else 85
        parent_semantic = (
            f"executor={executor.name}; affinity={', '.join(affinity)}"
            if affinity else ""
        )
        for lang, description in sorted(descriptions.items()):
            # Structured neutral labels reduce translation maintenance.  The
            # localized manifest description remains the substantive text.
            metadata = (
                f"executor={executor.name}; membership={membership}; "
                f"availability={availability}; effect={effect}"
                + (f"; platforms={platforms}" if platforms else "")
            )
            text = f"{metadata}. {description}"
            # Keep the rendered evidence exactly equal to the signed manifest
            # contract.  Affinity is retrieval metadata, so it belongs only to
            # the embedding projection and cannot leak into the composed reply.
            semantic = parent_semantic or text
            units.append(_unit(
                unit_id=f"executor-{executor.name}-{lang}",
                concept_id=f"executor-{executor.name}",
                lang=lang,
                audience=audience,
                source_kind="executor_manifest",
                authority="admitted_manifest",
                priority=priority,
                title=executor.name,
                text=text,
                semantic=semantic,
                source_ref=f"manifest:{executor.name}:{lang}",
            ))
        for path, lang, schema, description in _manifest_argument_descriptions(
                executor, manifest):
            body = f"executor={executor.name}; argument={path}" + (
                f"; {schema}" if schema else "")
            body = f"{body}. {description}"
            arg_key = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
            for part_number, part in enumerate(_bounded_parts(body), start=1):
                units.append(_unit(
                    unit_id=(f"executor-{executor.name}-arg-{arg_key}-"
                             f"{lang}-{part_number:02d}"),
                    concept_id=(f"executor-{executor.name}-arg-{arg_key}-"
                                f"{part_number:02d}"),
                    lang=lang,
                    audience=audience,
                    source_kind="executor_manifest_argument",
                    authority="admitted_manifest",
                    priority=priority,
                    title=f"{executor.name}.{path}",
                    text=part,
                    semantic=_semantic_with_parent(parent_semantic, part),
                    source_ref=(f"manifest:{executor.name}:arg:{path}:{lang}"
                                f"#{part_number}"),
                ))
    return units


def _provider_hints(executor, name: str) -> tuple[str, ...]:
    """Return provider identities from the canonical provider authority.

    A capability hint names the credential/transport binding.  It is not
    always the provider identity: Google Photos deliberately reuses the
    Google Workspace skill.  Explicit executor suffixes therefore come from
    ``vocab.PROVIDER_SUFFIXES``; capability hints cover provider-aware tools
    whose canonical name has no provider suffix.
    """

    from vocab import PROVIDER_DISPLAY_NAMES, PROVIDER_SUFFIXES

    explicit = {
        provider
        for provider in PROVIDER_SUFFIXES
        if name.endswith(f"_{provider}")
    }
    if explicit:
        return tuple(sorted(PROVIDER_DISPLAY_NAMES[provider]
                            for provider in explicit))

    hints = {
        str(hint).strip().lower()
        for capability in (getattr(executor, "capabilities", ()) or ())
        if isinstance(capability, dict)
        and str(capability.get("name") or "") == "provider:access"
        for hint in (capability.get("hint") or ())
        if str(hint).strip()
    }
    display_by_binding = {
        key.replace("_", "-"): value
        for key, value in PROVIDER_DISPLAY_NAMES.items()
    }
    return tuple(sorted(display_by_binding.get(hint, hint) for hint in hints))


def _catalog_action_object(name: str, providers: tuple[str, ...]) -> tuple[str, str]:
    """Derive the admitted action/object identity without a domain table."""

    action, separator, remainder = str(name or "").partition("_")
    if not separator or not action or not remainder:
        return "", ""
    for provider in sorted(providers, key=len, reverse=True):
        from vocab import PROVIDER_DISPLAY_NAMES
        technical = next(
            (key for key, label in PROVIDER_DISPLAY_NAMES.items()
             if label == provider),
            provider.replace("-", "_").replace(" ", "_").lower(),
        )
        suffix = technical
        if remainder == suffix:
            remainder = ""
            break
        marker = f"_{suffix}"
        if remainder.endswith(marker):
            remainder = remainder[:-len(marker)]
            break
    return (action, remainder) if remainder else ("", "")


def _catalog_parts(lines: list[str], *, limit: int = 1050) -> tuple[str, ...]:
    """Pack complete inventory rows into deterministic bounded parts.

    One row per line: the same structure serves the composer, the coverage
    ledger, and any future consumer without re-parsing localized prose.
    """

    parts: list[str] = []
    pending: list[str] = []
    size = 0
    for line in lines:
        projected = size + len(line) + (1 if pending else 0)
        if pending and projected > limit:
            parts.append("\n".join(pending))
            pending = []
            size = 0
        pending.append(line)
        size += len(line) + (1 if size else 0)
    if pending:
        parts.append("\n".join(pending))
    return tuple(parts)


_PURPOSE_MARKER = re.compile(
    r"\s+(?:PATTERN|NON|OUT|INPUT|OUTPUT):", re.IGNORECASE)
_PURPOSE_PREFIX = re.compile(r"^[^:]{1,20}:\s*")


def _catalog_purpose(description: str) -> str:
    """Extract one short user-facing purpose from a manifest description."""

    clean = _SPACE.sub(" ", str(description or "")).strip()
    clean = _PURPOSE_MARKER.split(clean, maxsplit=1)[0]
    clean = _PURPOSE_PREFIX.sub("", clean, count=1).strip(" .")
    if len(clean) <= 72:
        return clean
    boundary = clean.rfind(" ", 48, 72)
    return clean[:boundary if boundary > 0 else 72].rstrip(" ,;:") + "…"


def _capability_catalog_units() -> list[KnowledgeUnit]:
    """Project live capabilities into general, provider and area inventories.

    Groups are derived exclusively from admitted executor identities and
    provider capability hints.  Adding or removing an executor therefore
    changes these units without editing Tutor code or a domain-specific card.
    """

    from loader import load_catalog

    areas: dict[str, set[str]] = {}
    area_purposes: dict[str, dict[str, list[tuple[int, int, str, str]]]] = {
        "it": {}, "en": {},
    }
    provider_areas: dict[str, dict[str, set[str]]] = {}
    admitted = []
    for executor in sorted(load_catalog(), key=lambda item: item.name):
        name = str(getattr(executor, "name", "") or "")
        if not name or name == "admin":
            continue
        providers = _provider_hints(executor, name)
        action, object_name = _catalog_action_object(name, providers)
        if not action or not object_name:
            continue
        admitted.append(name)
        areas.setdefault(object_name, set()).add(action)
        from vocab import PROVIDER_SUFFIXES
        explicit_provider = any(
            name.endswith(f"_{provider}") for provider in PROVIDER_SUFFIXES)
        descriptions = _manifest_descriptions(
            executor, _manifest_document(executor))
        for lang in ("it", "en"):
            description = descriptions.get(lang, "")
            purpose = _catalog_purpose(description)
            if purpose:
                area_purposes[lang].setdefault(object_name, []).append((
                    1 if explicit_provider else 0,
                    -len(description),
                    name,
                    purpose,
                ))
        for provider in providers:
            provider_areas.setdefault(provider, {}).setdefault(
                object_name, set()).add(action)
    if not admitted or not areas:
        raise ValueError("Tutor capability catalog has no admitted areas")

    units: list[KnowledgeUnit] = []
    for lang in ("it", "en"):
        rows = []
        # The embedding fragment per row carries the natural-language purposes
        # (from the admitted manifests), never the technical area slugs: the
        # semantic text must describe what each part DOES, per part, so parts
        # stay individually retrievable instead of embedding as clones.
        fragment_by_row: dict[str, str] = {}
        for object_name, actions in sorted(areas.items()):
            purposes = []
            for _provider_rank, _length, _name, purpose in sorted(
                    area_purposes[lang].get(object_name, [])):
                if purpose not in purposes:
                    purposes.append(purpose)
                if len(purposes) >= 2:
                    break
            detail = " / ".join(purposes)
            row = (
                f"- {object_name} [{', '.join(sorted(actions))}]"
                + (f": {detail}" if detail else "")
            )
            rows.append(row)
            fragment_by_row[row] = detail or object_name.replace("_", " ")
        overview_parts = _catalog_parts(rows)
        all_names = ", ".join(
            object_name.replace("_", " ") for object_name in sorted(areas))
        if lang == "it":
            intro = (
                f"Catalogo vivo: {len(admitted)} operazioni ammesse in "
                f"{len(areas)} aree. Provider dichiarati: "
                f"{', '.join(sorted(provider_areas)) or 'nessuno'}."
            )
            head_semantic = (
                "Panoramica generale dell'assistente Metnos e dell'insieme "
                "delle capacità e operazioni concrete disponibili, senza "
                "limitarsi a un singolo dominio operativo. "
                f"Copre: {all_names}."
            )
            head_text = f"{intro}\nAree coperte: {all_names}."
        else:
            intro = (
                f"Live catalog: {len(admitted)} admitted operations in "
                f"{len(areas)} areas. Declared providers: "
                f"{', '.join(sorted(provider_areas)) or 'none'}."
            )
            head_semantic = (
                "General overview of the Metnos assistant and the whole set "
                "of available capabilities and concrete operations, without "
                "being limited to one operational domain. "
                f"Covers: {all_names}."
            )
            head_text = f"{intro}\nCovered areas: {all_names}."
        title = "Cosa può fare Metnos" if lang == "it" else "What Metnos can do"
        # The head is the inventory's own summary: the retrieval anchor for
        # overview-shaped questions.  Parts carry only their own rows, so a
        # single vector never has to represent generic and specific content
        # at once; sibling expansion rejoins the whole group at selection.
        units.append(_unit(
            unit_id=f"runtime-capabilities-overview-{lang}-00",
            concept_id="runtime-capabilities-overview-00",
            lang=lang,
            audience="user",
            source_kind="capability_catalog",
            authority="admitted_manifest",
            priority=100,
            title=title,
            text=head_text,
            semantic=head_semantic,
            source_ref="runtime:capability_catalog:overview#0",
        ))
        for part_number, part in enumerate(overview_parts, start=1):
            part_rows = [row for row in part.split("\n")
                         if row in fragment_by_row]
            fragments = "; ".join(fragment_by_row[row] for row in part_rows)
            if lang == "it":
                semantic = (
                    f"Operazioni disponibili in Metnos: {fragments}."
                )
            else:
                semantic = (
                    f"Operations available in Metnos: {fragments}."
                )
            units.append(_unit(
                unit_id=f"runtime-capabilities-overview-{lang}-{part_number:02d}",
                concept_id=f"runtime-capabilities-overview-{part_number:02d}",
                lang=lang,
                audience="user",
                source_kind="capability_catalog",
                authority="admitted_manifest",
                priority=100,
                title=title,
                text=intro + "\n" + part,
                semantic=semantic,
                source_ref=f"runtime:capability_catalog:overview#{part_number}",
            ))

        for provider, provider_objects in sorted(provider_areas.items()):
            provider_rows = [
                f"- {object_name}: {', '.join(sorted(actions))}"
                for object_name, actions in sorted(provider_objects.items())
            ]
            for part_number, part in enumerate(
                    _catalog_parts(provider_rows), start=1):
                if lang == "it":
                    title = f"Capacità {provider} disponibili"
                    text = (
                        f"Il catalogo vivo espone il provider {provider} in "
                        f"{len(provider_objects)} aree. L'accesso richiede una "
                        "credenziale valida configurata per il provider; le "
                        "operazioni di modifica, invio o eliminazione restano "
                        "soggette ai normali vagli di Metnos. "
                        f"Azioni ammesse:\n{part}"
                    )
                    semantic = (
                        f"Cosa può fare Metnos con {provider}; capacità, "
                        f"operazioni e oggetti disponibili. {part}"
                    )
                else:
                    title = f"Available {provider} capabilities"
                    text = (
                        f"The live catalog exposes provider {provider} across "
                        f"{len(provider_objects)} areas. Access requires a valid "
                        "credential configured for the provider; change, send, "
                        "and delete operations remain subject to the normal "
                        f"Metnos gates. Admitted actions:\n{part}"
                    )
                    semantic = (
                        f"What Metnos can do with {provider}; available "
                        f"capabilities, operations, and objects. {part}"
                    )
                provider_key = re.sub(
                    r"[^a-z0-9]+", "-", provider.casefold()).strip("-")
                units.append(_unit(
                    unit_id=(f"runtime-capabilities-provider-{provider_key}-"
                             f"{lang}-{part_number:02d}"),
                    concept_id=(f"runtime-capabilities-provider-{provider_key}-"
                                f"{part_number:02d}"),
                    lang=lang,
                    audience="user",
                    source_kind="capability_catalog",
                    authority="admitted_manifest",
                    priority=100,
                    title=title,
                    text=text,
                    semantic=semantic,
                    source_ref=(f"runtime:capability_catalog:provider:{provider}"
                                f"#{part_number}"),
                ))
    return units


def _service_registry_units() -> list[KnowledgeUnit]:
    """Project the canonical Settings service registry into F2 knowledge.

    Identity and descriptions come from the same typed registry consumed by
    the Settings UI.  Runtime status is deliberately not materialized here:
    it remains live data shown by Settings rather than stale catalog text.
    """

    from services_registry import catalog
    from ui_surfaces import by_key

    services = tuple(catalog())
    surface = by_key("services")
    units: list[KnowledgeUnit] = []
    for lang in ("it", "en"):
        if lang == "it":
            title = surface.breadcrumb(lang)
            intro = (
                "Pagina della chat web di Metnos. Dalla chat: "
                f"{title}; route {surface.route}. Mostra i servizi con stato "
                "systemd, salute applicativa, installazione, PID e gruppo; si "
                "aggiorna ogni 15 secondi. Dove disponibili offre Avvia, "
                "Arresta e Riavvia. Lo stato corrente è live e non è nel "
                "catalogo Tutor."
            )
        else:
            title = surface.breadcrumb(lang)
            intro = (
                "Page in the Metnos web chat. From the chat: "
                f"{title}; route {surface.route}. It shows services with systemd "
                "state, application health, installation, PID, and group, and "
                "refreshes every 15 seconds. Where available it offers Start, "
                "Stop, and Restart. Current state is live and is not in the "
                "Tutor catalog."
            )
        inventory = []
        for service in services:
            label = (service.label if lang == "it"
                     else service.label_en or service.label)
            description = (service.description if lang == "it"
                           else service.description_en or service.description)
            group = (service.group if lang == "it"
                     else service.group_en or service.group)
            inventory.append(
                f"{label} — {description} [{group}]"
            )
        units.append(_unit(
            unit_id=f"runtime-settings-services-{lang}",
            concept_id="runtime-settings-services",
            lang=lang,
            # From the registry, like every other surface: this unit used to
            # pin `instance_admin` on its own, which made Services the only
            # page whose knowledge audience lived outside `ui_surfaces`.
            audience=surface.knowledge_audience,
            source_kind="ui_surface",
            authority="runtime_registry",
            priority=100,
            title=title,
            text=f"{intro} " + "; ".join(inventory),
            source_ref=f"runtime:services_registry:{lang}",
            semantic=(
                f"Pagina UI {title}. Inventario e stato corrente live dei "
                "servizi Metnos: installati, attivi, in esecuzione, "
                "arrestati o degradati; "
                "nome, scopo, funzione, salute e controlli disponibili."
                if lang == "it" else
                f"Metnos UI page {title}. Inventory and current live state "
                "of Metnos services: installed, active, running, stopped, "
                "or degraded; their "
                "name, purpose, function, health, and available controls."
            ),
        ))
    return units


def _observation_view_units() -> list[KnowledgeUnit]:
    """Project the closed live-view registry into the signed Tutor catalog.

    The localized prose is retrieval/composition evidence only.  Live
    authority is the separate ``observation_ref`` field, checked against the
    same registry again at compile time and at request time.
    """

    from .observation_views import catalog

    units: list[KnowledgeUnit] = []
    for view in catalog():
        slug = view.view_id.lower().replace("_", "-")
        for lang in view.languages():
            title = view.localized("title", lang)
            coverage = view.localized("coverage", lang)
            excluded = view.localized("excluded", lang)
            # Every user-facing word comes from the localized registry.  The
            # compiler therefore needs no language branch: adding a complete
            # locale to one view automatically creates its signed unit.
            text_value = f"{coverage} {excluded}"
            semantic = f"{title}. {coverage}"
            units.append(_unit(
                unit_id=f"runtime-observation-{slug}-{lang}",
                concept_id=f"runtime-observation-{slug}",
                lang=lang,
                audience=view.audience,
                source_kind="live_observation",
                authority="runtime_registry",
                priority=100,
                title=title,
                text=text_value,
                semantic=semantic,
                source_ref=f"runtime:observation_view:{view.view_id}:{lang}",
                observation_ref=view.view_id,
            ))
    return units


def _ui_surface_units() -> list[KnowledgeUnit]:
    """Project every canonical Settings surface into channel-aware knowledge."""

    from ui_surfaces import catalog

    units: list[KnowledgeUnit] = []
    for surface in catalog():
        # The Services surface is enriched by the live component registry above.
        if surface.key == "services":
            continue
        for lang in ("it", "en"):
            title = surface.breadcrumb(lang)
            visible = "; ".join(surface.visible(lang))
            controls = "; ".join(surface.controls(lang))
            if lang == "it":
                text = (
                    "Questa è una pagina della chat web di Metnos. Si raggiunge "
                    f"dalla chat con il percorso {title}; "
                    f"la route è {surface.route}. Scopo: {surface.summary(lang)} "
                    f"Contenuto visibile: {visible}."
                )
                if controls:
                    text += f" Controlli e collegamenti disponibili: {controls}."
            else:
                text = (
                    "This is a page in the Metnos web chat. "
                    f"From the chat, follow {title}; its route is "
                    f"{surface.route}. Purpose: {surface.summary(lang)} "
                    f"Visible content: {visible}."
                )
                if controls:
                    text += f" Available controls and links: {controls}."
            units.append(_unit(
                unit_id=f"runtime-ui-{surface.key}-{lang}",
                concept_id=f"runtime-ui-{surface.key}",
                lang=lang,
                audience=surface.knowledge_audience,
                source_kind="ui_surface",
                authority="runtime_registry",
                priority=96,
                title=title,
                text=text,
                source_ref=f"runtime:ui_surface:{surface.key}:{lang}",
                semantic=(
                    f"Pagina UI di Metnos {title}. {surface.summary(lang)} "
                    f"Contenuto visibile: {visible}. Controlli: {controls}."
                    if lang == "it" else
                    f"Metnos UI page {title}. {surface.summary(lang)} "
                    f"Visible content: {visible}. Controls: {controls}."
                ),
            ))
            # A Settings page is a structured collection of independently
            # meaningful facts.  Embedding the whole page as one long vector
            # dilutes a focused question (for example, one metric among ten)
            # and lets broad prose outrank the canonical UI contract.  Give
            # every visible row its own semantic projection while retaining
            # the same source identity.  Retrieval can then match the row the
            # person actually describes; coverage still resolves the source
            # back to the complete typed surface.  This is registry-driven for
            # every page and language, with no query phrases or page names in
            # the selector.
            for item_number, item in enumerate(surface.visible(lang), start=1):
                if lang == "it":
                    facet_text = (
                        "Questa informazione è visibile nella pagina della "
                        f"chat web di Metnos {title}, route {surface.route}: "
                        f"{item}. Scopo della pagina: {surface.summary(lang)}"
                    )
                    facet_semantic = (
                        "Dove vedere nella chat web di Metnos questa "
                        f"informazione: {item}. Pagina {title}, route "
                        f"{surface.route}. {surface.summary(lang)}"
                    )
                else:
                    facet_text = (
                        "This information is visible on the Metnos web-chat "
                        f"page {title}, route {surface.route}: {item}. "
                        f"Page purpose: {surface.summary(lang)}"
                    )
                    facet_semantic = (
                        "Where to see this information in the Metnos web "
                        f"chat: {item}. Page {title}, route {surface.route}. "
                        f"{surface.summary(lang)}"
                    )
                units.append(_unit(
                    unit_id=(f"runtime-ui-{surface.key}-facet-"
                             f"{item_number:02d}-{lang}"),
                    concept_id=(f"runtime-ui-{surface.key}-facet-"
                                f"{item_number:02d}"),
                    lang=lang,
                    audience=surface.knowledge_audience,
                    source_kind="ui_surface",
                    authority="runtime_registry",
                    priority=96,
                    title=title,
                    text=facet_text,
                    source_ref=f"runtime:ui_surface:{surface.key}:{lang}",
                    semantic=facet_semantic,
                ))
            procedure = surface.procedure(lang)
            if procedure:
                stops = surface.stop_conditions(lang)
                if lang == "it":
                    lead = (
                        "Chiedi a Metnos con una richiesta come quella di "
                        f"questo esempio: «Guidami in sicurezza nella pagina "
                        f"{surface.label(lang)}.»"
                    )
                    body = [
                        lead,
                        "",
                        f"Percorso nella chat web di Metnos: **{title}** "
                        f"(`{surface.route}`).",
                        "",
                    ]
                    body.extend(
                        f"{number}. {step}"
                        for number, step in enumerate(procedure, start=1)
                    )
                    if stops:
                        body.extend(("", "**Fermati se:**"))
                        body.extend(f"- {stop}." for stop in stops)
                    controls_line = ", ".join(surface.controls(lang))
                    semantic = (
                        f"Procedura sicura nella pagina {title}. "
                        f"{surface.summary(lang)}"
                        + (f" Come usare in sicurezza i controlli: "
                           f"{controls_line}." if controls_line else "")
                        + " Verifiche e condizioni di arresto."
                    )
                else:
                    lead = (
                        "Ask Metnos with a request like this example: "
                        f"“Guide me safely through the {surface.label(lang)} "
                        "page.”"
                    )
                    body = [
                        lead,
                        "",
                        f"Path in the Metnos web chat: **{title}** "
                        f"(`{surface.route}`).",
                        "",
                    ]
                    body.extend(
                        f"{number}. {step}"
                        for number, step in enumerate(procedure, start=1)
                    )
                    if stops:
                        body.extend(("", "**Stop if:**"))
                        body.extend(f"- {stop}." for stop in stops)
                    controls_line = ", ".join(surface.controls(lang))
                    semantic = (
                        f"Safe procedure on the {title} page. "
                        f"{surface.summary(lang)}"
                        + (f" How to safely use the controls: "
                           f"{controls_line}." if controls_line else "")
                        + " Checks and stop conditions."
                    )
                units.append(_unit(
                    unit_id=f"runtime-ui-procedure-{surface.key}-{lang}",
                    concept_id=f"runtime-ui-procedure-{surface.key}",
                    lang=lang,
                    audience=surface.knowledge_audience,
                    source_kind="ui_procedure",
                    authority="runtime_registry",
                    priority=100,
                    title=title,
                    text="\n".join(body),
                    source_ref=(f"runtime:ui_surface:{surface.key}:procedure:"
                                f"{lang}"),
                    semantic=semantic,
                ))
    return units


def build_knowledge_units() -> tuple[KnowledgeUnit, ...]:
    units: list[KnowledgeUnit] = []
    for document in published_documents():
        units.extend(_document_units(
            source_id=document.source_id,
            lang=document.lang,
            path=document.path,
            audience="user",
            source_kind="manual",
            priority=80,
            concept_prefix=f"public-doc-{document.concept_key}",
            public_url=document.canonical_url,
        ))
    for source in _read_registry():
        for lang, path in sorted(source["paths"].items()):
            units.extend(_document_units(
                source_id=source["id"],
                lang=lang,
                path=path,
                audience=source["audience"],
                source_kind=source["kind"],
                priority=source["priority"],
            ))
    units.extend(_executor_units())
    units.extend(_capability_catalog_units())
    units.extend(_ui_surface_units())
    units.extend(_service_registry_units())
    units.extend(_observation_view_units())
    ids = [unit.unit_id for unit in units]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate Tutor knowledge unit id")
    if not units:
        raise ValueError("Tutor F2 knowledge corpus is empty")
    return tuple(sorted(units, key=lambda item: item.unit_id))


def snapshot_hash(units: tuple[KnowledgeUnit, ...]) -> str:
    payload = json.dumps(
        [
            {name: getattr(unit, name) for name in unit.__dataclass_fields__}
            for unit in units
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
