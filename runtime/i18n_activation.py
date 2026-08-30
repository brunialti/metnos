"""Coverage gate and atomic instance-language activation for RM-0005 F8."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import config as _C
from i18n_materializer import InventoryItem, LocalizationPaths, inventory, materialize
from i18n_pipeline import (
    _read_artifact,
    live_contract_context,
    promote_candidates,
    publish_versioned_contract_candidates,
)
from i18n_registry import LocalizationRegistry, RegistryError, ResourceRecord, normalize_language


_REQUIRED_CHECKS = frozenset({
    "planner_proposer_equivalence",
    "public_knowledge_review",
    "contract_message_equivalence",
    "input_lexicon_equivalence",
})
_REVIEWED_LAYERS = frozenset({
    "prompt", "contract", "message", "input", "knowledge",
})
_DERIVED_LAYERS = frozenset({"device", "tutor"})
_PATH_TOKEN = re.compile(r"(?:[A-Za-z]:\\[^\s<>'\"]+|/(?:home|Users|var/lib|etc)/[^\s<>'\"]+)")
_EMAIL_TOKEN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_TOKEN = re.compile(r"https?://[^\s<>'\"]+")


class ActivationBlocked(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GateReport:
    target_lang: str
    ok: bool
    total: int
    admitted: int
    exceptions: tuple[str, ...]
    errors: tuple[str, ...]
    checks: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ActivationReport:
    target_lang: str
    promoted: int
    exceptions: tuple[str, ...]
    device_templates: int
    tutor_digest: str
    configuration_changed: bool
    restarted: bool


def _manual_exception(item: InventoryItem) -> bool:
    """Whether the fresh source inventory explicitly requires human review."""
    return item.layer == "input" and (
        item.metadata.get("kind") == "regex"
        or item.metadata.get("review_policy") == "manual"
    )


def _new_sensitive_tokens(source: str, candidate: str) -> set[str]:
    added = set(_PATH_TOKEN.findall(candidate)) - set(_PATH_TOKEN.findall(source))
    added.update(set(_EMAIL_TOKEN.findall(candidate)) - set(_EMAIL_TOKEN.findall(source)))
    source_hosts = {
        urlsplit(value.rstrip(".,;)")).hostname
        for value in _URL_TOKEN.findall(source)
    }
    for value in _URL_TOKEN.findall(candidate):
        if urlsplit(value.rstrip(".,;)")).hostname not in source_hosts:
            added.add(value)
    return {value for value in added if value}


def _translation_hash(value: Any) -> str:
    """Registry hash of a v1 candidate translation (canonical JSON)."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_live_resource(
    record: ResourceRecord,
    *,
    item: InventoryItem,
    paths: LocalizationPaths,
    target: str,
) -> str | None:
    """Validate the admitted runtime representation, not only its candidate."""

    try:
        if record.layer == "prompt":
            relative = str(record.metadata["relative_path"])
            live = paths.prompts / target / relative
            text = live.read_text(encoding="utf-8")
            if hashlib.sha256(text.encode()).hexdigest() != record.translation_hash:
                return "live prompt hash differs from admitted candidate"
            if live.suffix == ".yaml":
                import yaml
                if not isinstance(yaml.safe_load(text), Mapping):
                    return "live YAML prompt is not a mapping"
            else:
                import minijinja
                minijinja.Environment().undeclared_variables_in_str(text)
            state = json.loads(
                (paths.prompts / target / ".lang_state.json").read_text(
                    encoding="utf-8"
                )
            )
            if (state.get(relative) or {}).get("status") != "admitted":
                return "prompt language state is not admitted"
        elif record.layer == "contract":
            if item.contract_snapshot is not None:
                snapshot = item.contract_snapshot
                if snapshot.contract_id != item.contract_ref.contract_id:
                    return "verified contract identity changed"
                if snapshot.generation_id != record.basis_id:
                    return "verified contract generation differs from registry basis"
                manifest = snapshot.parsed
            else:
                # Legacy authoring remains authoritative only before cutover.
                path = Path(str(record.metadata["manifest_path"]))
                manifest = tomllib.loads(path.read_text(encoding="utf-8"))
            node: Any = manifest
            for part in str(record.metadata["selector"]).split("."):
                node = node[part]
            if not isinstance(node, Mapping) or not str(node.get(target) or "").strip():
                return "manifest target prose is missing"
        elif record.layer == "message":
            rows = _query_exact(
                paths.messages_db,
                "SELECT text,needs_translation,version_hash,source_text_hash,"
                "source_lang FROM i18n WHERE key=? AND lang=?",
                (str(record.metadata["key"]), target),
            )
            if not rows or not str(rows[0][0] or "").strip() or int(rows[0][1] or 0):
                return "message is missing or pending"
            text, _pending, version_hash, source_text_hash, source_lang = rows[0]
            if _translation_hash(str(text)) != record.translation_hash:
                return "live message differs from admitted candidate"
            if version_hash != "sha256:" + hashlib.sha256(
                    str(text).encode("utf-8")).hexdigest():
                return "live message version hash differs from admitted candidate"
            if source_text_hash != "sha256:" + record.source_hash:
                return "live message source hash differs from admitted source"
            if source_lang != record.source_lang:
                return "live message source language differs from admitted source"
        elif record.layer == "input":
            rows = _query_exact(
                paths.detection_db,
                "SELECT payload,needs_translation,kind,match_mode,"
                "version_hash,source_text_hash,review_policy,source_lang "
                "FROM detection_lexicon "
                "WHERE concept=? AND lang=?",
                (str(record.metadata["concept"]), target),
            )
            if not rows or not str(rows[0][0] or "").strip() or int(rows[0][1] or 0):
                return "input lexicon is missing or pending"
            (raw_payload, _pending, kind, match_mode, version_hash,
             source_text_hash, review_policy, live_source_lang) = rows[0]
            if (
                kind != record.metadata.get("kind")
                or match_mode != record.metadata.get("match_mode")
                or review_policy != record.metadata.get("review_policy", "automatic")
                or live_source_lang != record.source_lang
            ):
                return "input lexicon metadata differs from admitted source"
            payload = json.loads(str(raw_payload))
            source_payload = json.loads(item.source_text)
            from detection_lexicon import validate_payload_shape
            validation = validate_payload_shape(kind, source_payload, payload)
            if not validation["ok"]:
                return "input lexicon shape is invalid: " + "; ".join(
                    validation["errors"]
                )
            encoded = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != record.translation_hash:
                return "input lexicon payload differs from admitted candidate"
            live_version = hashlib.sha256(
                str(raw_payload).encode("utf-8")
            ).hexdigest()
            if version_hash != "sha256:" + live_version:
                return "input lexicon version hash differs from admitted candidate"
            if source_text_hash != "sha256:" + record.source_hash:
                return "input lexicon source hash differs from admitted source"
        elif record.layer == "knowledge":
            live = paths.docs / target / str(record.metadata["relative_path"])
            text = live.read_text(encoding="utf-8")
            if not text.strip() or "__METNOS_" in text:
                return "public knowledge is empty or contains a sentinel"
            if _translation_hash(text) != record.translation_hash:
                return "live public knowledge differs from admitted candidate"
        elif record.layer == "device":
            catalog = json.loads(paths.device_catalog.read_text(encoding="utf-8"))
            translated = catalog.get(target) if isinstance(catalog, Mapping) else None
            if not isinstance(translated, Mapping) or not translated:
                return "device target catalog is missing"
            if _translation_hash(dict(translated)) != record.translation_hash:
                return "live device catalog differs from admitted candidate"
            expected_keys = record.metadata.get("keys")
            if isinstance(expected_keys, list) and set(translated) != set(expected_keys):
                return "live device catalog keys differ from source inventory"
        elif record.layer == "tutor":
            # Tutor is a compiled store rather than a canonical live file.
            # Its compiler evidence is tied to this record below in ``gate``.
            pass
    except Exception as exc:
        return f"live resource invalid: {type(exc).__name__}: {exc}"
    return None


def _query_exact(path: Path, query: str, params: tuple[Any, ...]) -> list[tuple]:
    if not path.is_file():
        return []
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        return connection.execute(query, params).fetchall()
    finally:
        connection.close()


def gate(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths | None = None,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
    require_admitted: bool = False,
    contract_snapshot_provider=None,
) -> GateReport:
    """Evaluate complete, current, reviewed coverage without changing state."""
    paths = paths or LocalizationPaths()
    target = normalize_language(target_lang)
    source_items = {
        item.resource_id: item for item in inventory(
            paths,
            source_lang=source_lang,
            contract_snapshot_provider=contract_snapshot_provider,
        )
    }
    records = registry.resources(target)
    record_ids = {record.resource_id for record in records}
    errors: list[str] = []
    exceptions: list[str] = []
    if not source_items:
        errors.append("source inventory is empty")
    missing_registry = sorted(set(source_items) - record_ids)
    unknown_registry = sorted(record_ids - set(source_items))
    if missing_registry:
        errors.append("unregistered resources: " + ", ".join(missing_registry[:8]))
    if unknown_registry:
        errors.append("unknown resources: " + ", ".join(unknown_registry[:8]))
    admitted = 0
    for record in records:
        item = source_items.get(record.resource_id)
        if item is None:
            continue
        if (
            record.layer != item.layer
            or record.source_lang != item.source_lang
            or record.target_lang != target
            or record.basis_id != item.basis_id
            or dict(record.metadata) != dict(item.metadata)
        ):
            errors.append(f"inventory identity drift: {record.resource_id}")
            continue
        if record.source_hash != item.source_hash:
            errors.append(f"source drift: {record.resource_id}")
            continue
        if record.status == "manual_review" and _manual_exception(item):
            exceptions.append(record.resource_id)
            continue
        if record.status == "manual_review":
            errors.append(f"unapproved manual review: {record.resource_id}")
            continue
        if record.layer in _DERIVED_LAYERS and not require_admitted:
            continue
        expected = {"admitted"} if require_admitted else {"translated", "admitted"}
        if record.status not in expected:
            errors.append(f"{record.resource_id}: status={record.status}")
            continue
        if record.status == "admitted":
            admitted += 1
            if require_admitted:
                live_error = _validate_live_resource(
                    record, item=item, paths=paths, target=target,
                )
                if live_error:
                    errors.append(f"{record.resource_id}: {live_error}")
        published_contract = (
            record.layer == "contract"
            and record.basis_id is not None
            and record.status == "admitted"
            and record.quality == "published"
        )
        if (
            record.layer in _REVIEWED_LAYERS
            and record.quality != "reviewed"
            and not published_contract
        ):
            errors.append(f"{record.resource_id}: quality={record.quality or 'missing'}")
        if record.layer not in _DERIVED_LAYERS:
            try:
                candidate = _read_artifact(
                    record, authoritative_item=item,
                )
                candidate_text = (
                    candidate if isinstance(candidate, str)
                    else json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                )
                leaked = _new_sensitive_tokens(item.source_text, candidate_text)
                if leaked:
                    errors.append(
                        f"{record.resource_id}: new private-looking tokens: "
                        + ", ".join(sorted(leaked)[:3])
                    )
            except Exception as exc:
                errors.append(f"{record.resource_id}: candidate invalid: {exc}")
    check_rows = registry.checks(target)
    checks = {key: str(value.get("status")) for key, value in check_rows.items()}
    for required in sorted(_REQUIRED_CHECKS):
        if checks.get(required) != "passed":
            errors.append(f"required check not passed: {required}")
    if require_admitted:
        for required in ("device_public_catalog", "tutor_catalog_compile", "manifest_admission"):
            if checks.get(required) != "passed":
                errors.append(f"required check not passed: {required}")
        derived_checks = {
            "device": "device_public_catalog",
            "tutor": "tutor_catalog_compile",
        }
        for record in records:
            check_id = derived_checks.get(record.layer)
            if check_id is None or record.status != "admitted":
                continue
            check = check_rows.get(check_id) or {}
            if (
                not record.translation_hash
                or check.get("evidence_hash") != record.translation_hash
            ):
                errors.append(
                    f"{record.resource_id}: {check_id} evidence differs from "
                    "the admitted resource"
                )
            if record.layer == "tutor" and target not in set(
                    (check.get("details") or {}).get("languages") or ()):
                errors.append(
                    f"{record.resource_id}: tutor check omits target language"
                )
    return GateReport(
        target_lang=target, ok=not errors, total=len(records), admitted=admitted,
        exceptions=tuple(sorted(exceptions)), errors=tuple(errors),
        checks=dict(sorted(checks.items())),
    )


def _upsert_public_message(
    db: Path, *, key: str, target: str, source_lang: str,
    source_hash: str, text: str,
) -> None:
    conn = sqlite3.connect(str(db))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(i18n)")}
        values: dict[str, Any] = {
            "key": key, "lang": target, "text": text,
            "needs_translation": 0, "source_lang": source_lang,
        }
        optional = {
            "source_hash": source_hash[:16],
            "source_text_hash": "sha256:" + source_hash,
            "version_hash": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
            "auto_translated": 0,
        }
        values.update({key: value for key, value in optional.items() if key in columns})
        names = list(values)
        assignments = ",".join(
            f"{name}=excluded.{name}" for name in names if name not in {"key", "lang"}
        )
        conn.execute(
            f"INSERT INTO i18n({','.join(names)}) VALUES ({','.join('?' for _ in names)}) "
            f"ON CONFLICT(key,lang) DO UPDATE SET {assignments}",
            tuple(values[name] for name in names),
        )
        conn.commit()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass
    finally:
        conn.close()


def publish_public_messages(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths,
) -> int:
    target = normalize_language(target_lang)
    count = 0
    for record in registry.resources(target):
        if (
            record.layer != "message" or record.status != "admitted"
            or not record.metadata.get("public") or record.quality != "reviewed"
        ):
            continue
        text = _read_artifact(record)
        if not isinstance(text, str):
            raise ActivationBlocked(f"public message is not text: {record.resource_id}")
        _upsert_public_message(
            paths.public_messages_db, key=str(record.metadata["key"]),
            target=target, source_lang=record.source_lang,
            source_hash=record.source_hash, text=text,
        )
        count += 1
    return count


def reconcile_device_catalog(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
) -> int:
    from device_shim.gen_i18n import build_templates, write
    target = normalize_language(target_lang)
    catalogs = build_templates(
        paths.public_messages_db, languages=[source_lang, target],
    )
    source = catalogs.get(source_lang) or {}
    translated = catalogs.get(target) or {}
    missing = sorted(set(source) - set(translated))
    if not source or missing:
        raise ActivationBlocked(
            "device public catalog incomplete: " + ", ".join(missing[:8])
        )
    templates = write(paths.public_messages_db, paths.device_catalog)
    record = next((
        row for row in registry.resources(target)
        if row.resource_id == "device:public-message-catalog"
    ), None)
    if record is None:
        raise ActivationBlocked("device catalog resource is not registered")
    digest = hashlib.sha256(json.dumps(
        translated, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    if record.status in {"pending", "failed"}:
        lease = registry.claim(record.resource_id, target)
        if lease is None:
            raise ActivationBlocked("device catalog resource cannot be claimed")
        registry.complete(
            record.resource_id, target, digest, "public",
            lease_token=lease.lease_token, artifact_path=str(paths.device_catalog),
        )
    current = next(row for row in registry.resources(target) if row.resource_id == record.resource_id)
    if current.status == "translated":
        registry.admit(record.resource_id, target)
    registry.record_check(
        "device_public_catalog", target, "passed", evidence_hash=digest,
        details={"templates": len(translated), "bundle_templates": templates},
    )
    return len(translated)


@dataclass(frozen=True, slots=True)
class _PublicLinks:
    lang: str
    canonical: str
    alternates: Mapping[str, str]


def _public_links(text: str) -> _PublicLinks:
    # Use the deployment gate's parser so synchronization and publication
    # interpret HTML identity with exactly the same rules.
    from published_docs import _HeadParser

    parser = _HeadParser()
    try:
        parser.feed(text)
        parser.close()
        head = parser.result()
    except ValueError as exc:
        raise ActivationBlocked(str(exc)) from exc
    alternates: dict[str, str] = {}
    for language, href in head.alternates:
        if language in alternates:
            raise ActivationBlocked(
                f"public document has duplicate hreflang {language!r}"
            )
        alternates[language] = href
    return _PublicLinks(
        lang=head.lang,
        canonical=head.canonical,
        alternates=alternates,
    )


_LINK_TAG = re.compile(r"<link\b[^>]*>", flags=re.I)
_HREF_ATTRIBUTE = re.compile(
    r'''(\bhref\s*=\s*)(?:"[^"]*"|'[^']*'|[^\s>]+)''',
    flags=re.I,
)


def _ensure_alternate(text: str, lang: str, href: str) -> str:
    if not href or "</head>" not in text.lower():
        return text
    wanted = lang.casefold()
    matches: list[re.Match[str]] = []
    for match in _LINK_TAG.finditer(text):
        if wanted in _public_links(match.group(0)).alternates:
            matches.append(match)
    if len(matches) > 1:
        raise ActivationBlocked(f"public document has duplicate hreflang {lang!r}")
    if matches:
        match = matches[0]
        tag = match.group(0)
        if _HREF_ATTRIBUTE.search(tag):
            updated_tag = _HREF_ATTRIBUTE.sub(
                lambda attribute: attribute.group(1) + f'"{href}"',
                tag,
                count=1,
            )
        else:
            updated_tag = f'<link rel="alternate" hreflang="{lang}" href="{href}">'
        return text[:match.start()] + updated_tag + text[match.end():]
    link = f'  <link rel="alternate" hreflang="{lang}" href="{href}">\n'
    return re.sub(r"</head>", link + "</head>", text, count=1, flags=re.I)


def synchronize_public_hreflang(target_lang: str, paths: LocalizationPaths) -> int:
    """Complete reciprocal families using declared public identity, not paths.

    A translated filename is prose and may legitimately differ by language.
    Canonical and non-default hreflang URLs already identify the logical
    family, so they are the sole cross-language join key.  The complete update
    is planned before any file is written and fails closed on ambiguous URLs,
    missing members, or language mismatches.
    """
    target = normalize_language(target_lang)
    target_root = paths.docs / target
    if not target_root.is_dir():
        return 0
    language_roots = sorted(
        path for path in paths.docs.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    texts: dict[Path, str] = {}
    links_by_path: dict[Path, _PublicLinks] = {}
    path_by_canonical: dict[str, Path] = {}
    for root in language_roots:
        for path in sorted(root.rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            links = _public_links(text)
            texts[path] = text
            links_by_path[path] = links
            if not links.canonical:
                continue
            previous = path_by_canonical.get(links.canonical)
            if previous is not None and previous != path:
                raise ActivationBlocked(
                    "duplicate public canonical URL: " + links.canonical
                )
            path_by_canonical[links.canonical] = path

    planned: dict[Path, str] = {}
    visited: set[str] = set()
    for target_path in sorted(target_root.rglob("*.html")):
        target_links = links_by_path.get(target_path)
        if target_links is None or not target_links.canonical:
            continue
        if target_links.lang != target:
            raise ActivationBlocked(
                f"target document language is {target_links.lang!r}, expected {target!r}: "
                f"{target_path}"
            )
        if target_links.canonical in visited:
            continue
        family: dict[str, Path] = {}
        by_language: dict[str, str] = {}
        pending = [target_links.canonical]
        while pending:
            canonical = pending.pop()
            if canonical in family:
                continue
            path = path_by_canonical.get(canonical)
            if path is None:
                raise ActivationBlocked(
                    "hreflang is not a published document: " + canonical
                )
            family[canonical] = path
            language = links_by_path[path].lang
            if not language:
                raise ActivationBlocked(f"public document has no language: {path}")
            previous = by_language.get(language)
            if previous is not None and previous != canonical:
                raise ActivationBlocked(
                    f"translation family has two {language!r} documents"
                )
            by_language[language] = canonical
            for alternate_lang, alternate_url in links_by_path[path].alternates.items():
                if alternate_lang == "x-default":
                    continue
                alternate_path = path_by_canonical.get(alternate_url)
                if alternate_path is None:
                    raise ActivationBlocked(
                        f"hreflang {alternate_lang!r} is not a published document: "
                        f"{alternate_url}"
                    )
                actual_lang = links_by_path[alternate_path].lang
                if actual_lang != alternate_lang:
                    raise ActivationBlocked(
                        f"hreflang {alternate_lang!r} points to lang {actual_lang!r}"
                    )
                pending.append(alternate_url)
        visited.update(family)

        for path in family.values():
            updated = planned.get(path, texts[path])
            for language, alternate_url in sorted(by_language.items()):
                updated = _ensure_alternate(updated, language, alternate_url)
            projected = _public_links(updated)
            if any(
                projected.alternates.get(language) != alternate_url
                for language, alternate_url in by_language.items()
            ):
                raise ActivationBlocked(
                    f"cannot write a complete hreflang family: {path}"
                )
            planned[path] = updated

    changed_paths = [path for path, text in planned.items() if text != texts[path]]
    for path in sorted(changed_paths):
        _write_public_text(path, planned[path])
    return len(changed_paths)


def _write_public_text(path: Path, text: str) -> None:
    from i18n_pipeline import _atomic_text
    _atomic_text(path, text, mode=0o644)


def reconcile_tutor_catalog(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    compiler: Callable[[], tuple[str, set[str]]] | None = None,
) -> str:
    target = normalize_language(target_lang)
    if compiler is None:
        from tutor.catalog import compile_catalog, load_knowledge_units

        def compiler() -> tuple[str, set[str]]:
            digest = compile_catalog(force=True)
            return digest, {unit.lang for unit in load_knowledge_units()}

    digest, languages = compiler()
    if target not in languages:
        raise ActivationBlocked(f"Tutor catalog has no units for {target}")
    raw_digest = str(digest).removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", raw_digest):
        raw_digest = hashlib.sha256(str(digest).encode()).hexdigest()
    record = next((
        row for row in registry.resources(target)
        if row.resource_id == "tutor:public-catalog"
    ), None)
    if record is None:
        raise ActivationBlocked("Tutor catalog resource is not registered")
    if record.status in {"pending", "failed"}:
        lease = registry.claim(record.resource_id, target)
        if lease is None:
            raise ActivationBlocked("Tutor catalog resource cannot be claimed")
        registry.complete(
            record.resource_id, target, raw_digest, "compiled",
            lease_token=lease.lease_token,
        )
    current = next(row for row in registry.resources(target) if row.resource_id == record.resource_id)
    if current.status == "translated":
        registry.admit(record.resource_id, target)
    registry.record_check(
        "tutor_catalog_compile", target, "passed", evidence_hash=raw_digest,
        details={"languages": sorted(languages)},
    )
    return "sha256:" + raw_digest


def validate_manifests(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths | None = None,
    validator: Callable[[Path, str], tuple[bool, str]] | None = None,
    contract_snapshot_provider=None,
) -> None:
    target = normalize_language(target_lang)
    if contract_snapshot_provider is not None:
        from i18n_materializer import _manifest_sources
        from manifest_inventory import inventory_manifests
        from manifest_lint import lint_manifest

        selected_paths = paths or LocalizationPaths()
        manifest_inventory = inventory_manifests(
            _manifest_sources(selected_paths.manifest_roots),
        )
        if manifest_inventory.problems:
            raise ActivationBlocked("manifest inventory is not clean")
        failures = []
        evidence = hashlib.sha256()
        count = 0
        for ref in manifest_inventory.admitted():
            snapshot = contract_snapshot_provider(ref)
            from contract_store import ContractRetirement, VerifiedManifest

            if isinstance(snapshot, ContractRetirement):
                continue
            if not isinstance(snapshot, VerifiedManifest):
                raise ActivationBlocked(
                    f"contract revision is not verified: {ref.contract_id}",
                )
            findings = [
                finding for finding in lint_manifest(
                    snapshot.parsed, language=target,
                )
                if finding.severity == "error"
            ]
            if findings:
                failures.append(
                    f"{ref.contract_id}: "
                    + "; ".join(str(finding) for finding in findings[:3])
                )
            else:
                evidence.update(snapshot.manifest_bytes)
                count += 1
        if failures:
            raise ActivationBlocked(
                "manifest admission failed: " + "; ".join(failures[:5]),
            )
        registry.record_check(
            "manifest_admission", target, "passed",
            evidence_hash=evidence.hexdigest(),
            details={"manifests": count},
        )
        return
    if validator is None:
        from manifest_lint import lint_file
        from sign import verify_executor

        def validator(path: Path, language: str) -> tuple[bool, str]:
            findings = [
                finding for finding in lint_file(path, language=language)
                if finding.severity == "error"
            ]
            if findings:
                return False, "; ".join(str(finding) for finding in findings[:3])
            ok, info = verify_executor(path.parent)
            return bool(ok), "" if ok else str(info.get("reason") or info)

    paths = sorted({
        Path(str(record.metadata["manifest_path"]))
        for record in registry.resources(target) if record.layer == "contract"
    })
    failures = []
    evidence = hashlib.sha256()
    for path in paths:
        ok, detail = validator(path, target)
        if not ok:
            failures.append(f"{path}: {detail}")
        else:
            evidence.update(path.read_bytes())
    if failures:
        raise ActivationBlocked("manifest admission failed: " + "; ".join(failures[:5]))
    digest = evidence.hexdigest()
    registry.record_check(
        "manifest_admission", target, "passed", evidence_hash=digest,
        details={"manifests": len(paths)},
    )


def restart_http_service() -> None:
    completed = subprocess.run(
        ["systemctl", "restart", "metnos-http.service"],
        check=False, capture_output=True, text=True, timeout=45,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "service restart failed")


def activate_language(
    target_lang: str,
    *,
    registry: LocalizationRegistry | None = None,
    paths: LocalizationPaths | None = None,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
    manifest_validator: Callable[[Path, str], tuple[bool, str]] | None = None,
    tutor_compiler: Callable[[], tuple[str, set[str]]] | None = None,
    request_writer: Callable[..., tuple[Any, bool]] | None = None,
    restart: Callable[[], None] | None = None,
    contract_snapshot_provider=None,
    contract_publisher=None,
) -> ActivationReport:
    """Promote a complete locale, flip signed authority, then restart."""
    target = normalize_language(target_lang)
    paths = paths or LocalizationPaths()
    registry = registry or LocalizationRegistry()
    injected_versioned = (
        contract_snapshot_provider is not None
        or contract_publisher is not None
    )
    if injected_versioned and (
        contract_snapshot_provider is None or contract_publisher is None
    ):
        raise ValueError(
            "contract snapshot provider and publisher must be supplied together",
        )
    if injected_versioned:
        store_only = True
        snapshot_provider = contract_snapshot_provider
        publisher = contract_publisher
    else:
        context = live_contract_context(registry, publication=True)
        store_only = context.store_only
        snapshot_provider = context.snapshot_provider
        publisher = context.publisher
    materialize(
        target, registry=registry, paths=paths, source_lang=source_lang,
        contract_snapshot_provider=snapshot_provider,
    )
    before = gate(
        target, registry=registry, paths=paths, source_lang=source_lang,
        require_admitted=False,
        contract_snapshot_provider=snapshot_provider,
    )
    if not before.ok:
        raise ActivationBlocked("pre-activation gate failed: " + "; ".join(before.errors[:8]))
    versioned_promoted = 0
    if store_only:
        assert snapshot_provider is not None and publisher is not None
        versioned = publish_versioned_contract_candidates(
            target,
            registry=registry,
            paths=paths,
            contract_snapshot_provider=snapshot_provider,
            publisher=publisher,
            source_lang=source_lang,
        )
        if versioned.errors:
            raise ActivationBlocked(
                "versioned contract publication failed: "
                + "; ".join(
                    f"{key}: {value}"
                    for key, value in list(versioned.errors.items())[:5]
                ),
            )
        versioned_promoted = versioned.published_resources
    promoted = promote_candidates(
        target, registry=registry, paths=paths,
        versioned_contracts_published=store_only,
    )
    if promoted.errors:
        raise ActivationBlocked(
            "candidate promotion failed: "
            + "; ".join(f"{key}: {value}" for key, value in list(promoted.errors.items())[:5])
        )
    synchronize_public_hreflang(target, paths)
    publish_public_messages(target, registry=registry, paths=paths)
    device_templates = reconcile_device_catalog(
        target, registry=registry, paths=paths, source_lang=source_lang,
    )
    validate_manifests(
        target,
        registry=registry,
        paths=paths,
        validator=manifest_validator,
        contract_snapshot_provider=snapshot_provider,
    )
    tutor_digest = reconcile_tutor_catalog(
        target, registry=registry, compiler=tutor_compiler,
    )
    after = gate(
        target, registry=registry, paths=paths, source_lang=source_lang,
        require_admitted=True,
        contract_snapshot_provider=snapshot_provider,
    )
    if not after.ok:
        raise ActivationBlocked("post-activation gate failed: " + "; ".join(after.errors[:8]))
    writer = request_writer or _C.write_localization_request
    _request, changed = writer(
        instance_lang=target, requested_lang=target, state="active",
        corpus_version=_C.localization_corpus_version(),
    )
    restarted = False
    if restart is not None:
        restart()
        restarted = True
    return ActivationReport(
        target_lang=target, promoted=promoted.admitted + versioned_promoted,
        exceptions=after.exceptions, device_templates=device_templates,
        tutor_digest=tutor_digest, configuration_changed=bool(changed),
        restarted=restarted,
    )
