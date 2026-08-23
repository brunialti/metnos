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
from i18n_materializer import LocalizationPaths, inventory, materialize
from i18n_pipeline import _read_artifact, promote_candidates
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


def _manual_exception(record: ResourceRecord) -> bool:
    return record.layer == "input" and (
        record.metadata.get("kind") == "regex"
        or record.metadata.get("review_policy") == "manual"
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


def _validate_live_resource(
    record: ResourceRecord,
    *,
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
                "SELECT text,needs_translation FROM i18n WHERE key=? AND lang=?",
                (str(record.metadata["key"]), target),
            )
            if not rows or not str(rows[0][0] or "").strip() or int(rows[0][1] or 0):
                return "message is missing or pending"
        elif record.layer == "input":
            rows = _query_exact(
                paths.detection_db,
                "SELECT payload,needs_translation FROM detection_lexicon "
                "WHERE concept=? AND lang=?",
                (str(record.metadata["concept"]), target),
            )
            if not rows or not str(rows[0][0] or "").strip() or int(rows[0][1] or 0):
                return "input lexicon is missing or pending"
            json.loads(str(rows[0][0]))
        elif record.layer == "knowledge":
            live = paths.docs / target / str(record.metadata["relative_path"])
            text = live.read_text(encoding="utf-8")
            if not text.strip() or "__METNOS_" in text:
                return "public knowledge is empty or contains a sentinel"
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
) -> GateReport:
    """Evaluate complete, current, reviewed coverage without changing state."""
    paths = paths or LocalizationPaths()
    target = normalize_language(target_lang)
    source_items = {
        item.resource_id: item for item in inventory(paths, source_lang=source_lang)
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
        if record.source_hash != item.source_hash:
            errors.append(f"source drift: {record.resource_id}")
            continue
        if record.status == "manual_review" and _manual_exception(record):
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
                    record, paths=paths, target=target,
                )
                if live_error:
                    errors.append(f"{record.resource_id}: {live_error}")
        if record.layer in _REVIEWED_LAYERS and record.quality != "reviewed":
            errors.append(f"{record.resource_id}: quality={record.quality or 'missing'}")
        if record.layer not in _DERIVED_LAYERS:
            try:
                candidate = _read_artifact(record)
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


def _extract_canonical(text: str) -> str:
    match = re.search(
        r'<link\b[^>]*\brel\s*=\s*["\']canonical["\'][^>]*\bhref\s*=\s*["\']([^"\']+)',
        text, flags=re.I,
    )
    return match.group(1) if match else ""


def _ensure_alternate(text: str, lang: str, href: str) -> str:
    if not href or "</head>" not in text.lower():
        return text
    pattern = (
        r'(<link\b[^>]*\brel\s*=\s*["\']alternate["\'][^>]*\bhreflang\s*=\s*["\']'
        + re.escape(lang) + r'["\'][^>]*\bhref\s*=\s*["\'])[^"\']+(["\'])'
    )
    if re.search(pattern, text, flags=re.I):
        return re.sub(pattern, lambda match: match.group(1) + href + match.group(2), text, flags=re.I)
    link = f'  <link rel="alternate" hreflang="{lang}" href="{href}">\n'
    return re.sub(r"</head>", link + "</head>", text, count=1, flags=re.I)


def synchronize_public_hreflang(target_lang: str, paths: LocalizationPaths) -> int:
    """Add the admitted locale to every existing reciprocal document group."""
    target = normalize_language(target_lang)
    target_root = paths.docs / target
    changed = 0
    if not target_root.is_dir():
        return 0
    language_roots = sorted(
        path for path in paths.docs.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    for target_path in sorted(target_root.rglob("*.html")):
        relative = target_path.relative_to(target_root)
        target_text = target_path.read_text(encoding="utf-8")
        href = _extract_canonical(target_text)
        if not href:
            continue
        for root in language_roots:
            sibling = root / relative
            if not sibling.is_file():
                continue
            original = sibling.read_text(encoding="utf-8")
            updated = _ensure_alternate(original, target, href)
            if updated != original:
                _write_public_text(sibling, updated)
                changed += 1
    return changed


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
    validator: Callable[[Path], tuple[bool, str]] | None = None,
) -> None:
    target = normalize_language(target_lang)
    if validator is None:
        from manifest_lint import lint_file
        from sign import verify_executor

        def validator(path: Path) -> tuple[bool, str]:
            findings = [finding for finding in lint_file(path) if finding.severity == "error"]
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
        ok, detail = validator(path)
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
    signer: Callable[[Path], Any] | None = None,
    manifest_validator: Callable[[Path], tuple[bool, str]] | None = None,
    tutor_compiler: Callable[[], tuple[str, set[str]]] | None = None,
    request_writer: Callable[..., tuple[Any, bool]] | None = None,
    restart: Callable[[], None] | None = None,
) -> ActivationReport:
    """Promote a complete locale, flip signed authority, then restart."""
    target = normalize_language(target_lang)
    paths = paths or LocalizationPaths()
    registry = registry or LocalizationRegistry()
    materialize(target, registry=registry, paths=paths, source_lang=source_lang)
    before = gate(
        target, registry=registry, paths=paths, source_lang=source_lang,
        require_admitted=False,
    )
    if not before.ok:
        raise ActivationBlocked("pre-activation gate failed: " + "; ".join(before.errors[:8]))
    promoted = promote_candidates(
        target, registry=registry, paths=paths, signer=signer,
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
    validate_manifests(target, registry=registry, validator=manifest_validator)
    tutor_digest = reconcile_tutor_catalog(
        target, registry=registry, compiler=tutor_compiler,
    )
    after = gate(
        target, registry=registry, paths=paths, source_lang=source_lang,
        require_admitted=True,
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
        target_lang=target, promoted=promoted.admitted,
        exceptions=after.exceptions, device_templates=device_templates,
        tutor_digest=tutor_digest, configuration_changed=bool(changed),
        restarted=restarted,
    )
