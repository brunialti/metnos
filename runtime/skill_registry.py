#!/usr/bin/env python3
"""skill_registry — registry runtime delle skill imported (ADR 0160).

Estende il modello skill (ADR 0123) con tre campi frontmatter opzionali
parsati da `SKILL.md`:

- `lang: <ISO 639-1>` (default `"any"`): filtro locale. Loader skip se
  `lang != "any"` e `lang != config.DEFAULT_LANG`.
- `trust: <metnos-official|community>` (default `"community"`): determina
  il regime di safety net (ADR 0159). `metnos-official` skip L6 LLM verify
  (codice trusted by Metnos team).
- `auto_enable: <bool>` (default `true`): se `false`, l'admin deve abilitarla
  esplicitamente via `metnos-skills enable <skill>`.

Lo stato enable/disable e' persistito in
`<USER_STATE>/skill_enabled.json` (mapping `{skill_name: bool}`). Skill
non presenti nel file: default = `auto_enable` dalla SKILL.md (default true).

Determinismo §7.9: pura lettura tabellare + filesystem scan.
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping

# Ensure runtime/ on path quando importato da subprocess / cli.
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))

import config as _C  # noqa: E402
from skills_paths import iter_skill_dirs as _isd  # noqa: E402


_STATE_LOCK_TIMEOUT = 15.0
_STATE_THREAD_LOCK = threading.Lock()


class SkillEnablementError(RuntimeError):
    """Fail-closed error raised before an enablement policy is persisted."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# --- Frontmatter parser deterministico ------------------------------------
# SKILL.md ha frontmatter YAML semplice (key: value, no nested liste).
# Usiamo regex line-by-line per evitare dipendenza da PyYAML.


def _parse_skill_md(path: Path) -> dict[str, str]:
    """Parsa il frontmatter YAML di SKILL.md (campi piatti only).

    Ritorna dict {key: str_value}. Valori complessi (liste, nested) saltati
    silenziosamente — questo registry e' interessato a 3 campi piatti.
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    # Split sul secondo "---".
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    block = parts[1]
    out: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        # Nested/indented keys: skip (siamo solo top-level).
        if line.startswith(" ") or line.startswith("\t"):
            continue
        key, _, val = line.partition(":")
        val = val.strip().strip('"').strip("'")
        # Esclude valori liste/nested ([] {})
        if val.startswith("[") or val.startswith("{"):
            continue
        out[key.strip()] = val
    return out


@dataclass
class SkillInfo:
    name: str
    path: Path
    lang: str = "any"
    trust: str = "community"
    auto_enable: bool = True
    enabled: bool = True
    n_executors: int = 0
    is_imported: bool = True       # default: skill imported via ADR 0123
    is_builtin_repo: bool = False  # True se sotto <install>/executors/skills/
    is_first_party: bool = False   # True se skill-capacità first-party (skills_catalog, non un bundle-dir)
    requires: str = ""             # prerequisito esterno (backend/creds) per la dormancy/installer

    @property
    def is_builtin(self) -> bool:
        """Identità autoritativa, indipendente dal path d'installazione.

        Una skill Metnos first-party resta builtin anche quando i suoi file
        sono materializzati in user-data (caso GitHub). ``is_builtin_repo``
        descrive soltanto la collocazione fisica e non deve degradare il tier.
        """
        return self.is_builtin_repo or self.is_first_party

    @property
    def is_metnos_official(self) -> bool:
        return self.trust == "metnos-official"


def _state_file() -> Path:
    return _C.PATH_USER_STATE / "skill_enabled.json"


def _validated_state(value: object) -> dict[str, bool]:
    if not isinstance(value, dict):
        raise ValueError("skill state must be a JSON object")
    if any(
        not isinstance(name, str)
        or not name.strip()
        or name != name.strip()
        or type(enabled) is not bool
        for name, enabled in value.items()
    ):
        raise ValueError("skill state must map canonical names to booleans")
    return dict(value)


def _is_link_like(path: Path, status: os.stat_result | None = None) -> bool:
    """Recognize links and Windows reparse points without following them."""
    try:
        current = status if status is not None else path.lstat()
        reparse = bool(
            getattr(current, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        return reparse or stat.S_ISLNK(current.st_mode) or (
            hasattr(path, "is_junction") and path.is_junction()
        )
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _state_parent_chain(directory: Path) -> tuple[Path, ...]:
    absolute = Path(os.path.abspath(directory))
    return tuple(reversed((absolute, *absolute.parents)))


def _require_plain_state_parent(
    directory: Path,
    *,
    create: bool,
) -> tuple[Path, os.stat_result] | None:
    """Reject links/reparse points in every skill-state parent component."""
    chain = _state_parent_chain(directory)
    for component in chain:
        try:
            current = component.lstat()
        except FileNotFoundError:
            if not create:
                return None
            try:
                component.mkdir()
            except FileExistsError:
                pass
            except OSError as exc:
                raise SkillEnablementError(
                    "skill_state_parent_invalid", f"{component}: {exc}",
                ) from exc
            try:
                current = component.lstat()
            except OSError as exc:
                raise SkillEnablementError(
                    "skill_state_parent_invalid", f"{component}: {exc}",
                ) from exc
        except OSError as exc:
            raise SkillEnablementError(
                "skill_state_parent_invalid", f"{component}: {exc}",
            ) from exc
        if _is_link_like(component, current) or not stat.S_ISDIR(current.st_mode):
            raise SkillEnablementError(
                "skill_state_parent_invalid", str(component),
            )

    immediate: os.stat_result | None = None
    for component in chain:
        try:
            current = component.lstat()
        except OSError as exc:
            raise SkillEnablementError(
                "skill_state_parent_invalid", f"{component}: {exc}",
            ) from exc
        if _is_link_like(component, current) or not stat.S_ISDIR(current.st_mode):
            raise SkillEnablementError(
                "skill_state_parent_invalid", str(component),
            )
        if component == chain[-1]:
            immediate = current
    assert immediate is not None
    return chain[-1], immediate


def _require_same_state_parent(
    parent: Path,
    expected: os.stat_result,
) -> None:
    observed = _require_plain_state_parent(parent, create=False)
    if observed is None or not os.path.samestat(expected, observed[1]):
        raise SkillEnablementError(
            "skill_state_parent_invalid", f"changed: {parent}",
        )


def _require_plain_owned_file(
    path: Path,
    *,
    code: str,
    allow_missing: bool = False,
) -> os.stat_result | None:
    """Fail closed on redirected, non-regular, or foreign policy files."""
    try:
        status = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise SkillEnablementError(code, str(path))
    except OSError as exc:
        raise SkillEnablementError(code, f"{path}: {exc}") from exc
    if _is_link_like(path, status) or not stat.S_ISREG(status.st_mode):
        raise SkillEnablementError(code, str(path))
    if (
        hasattr(os, "geteuid")
        and hasattr(status, "st_uid")
        and status.st_uid != os.geteuid()
    ):
        raise SkillEnablementError(code, f"foreign owner: {path}")
    return status


def _require_same_open_file(
    path: Path,
    opened: os.stat_result,
    *,
    code: str,
) -> None:
    current = _require_plain_owned_file(path, code=code)
    if current is None or not os.path.samestat(current, opened):
        raise SkillEnablementError(code, f"changed while opening: {path}")


def _read_state_payload() -> bytes | None:
    """Read one stable policy file without following a redirected entry.

    ``None`` has exactly one meaning: the directory entry was absent at the
    first observation.  A file which existed but disappeared, became
    unreadable, changed identity, or was redirected is an invalid policy, not
    an invitation to restore ``auto_enable`` defaults.
    """
    f = _state_file()
    descriptor = -1
    try:
        parent = _require_plain_state_parent(f.parent, create=False)
        if parent is None:
            return None
        before = _require_plain_owned_file(
            f, code="skill_state_invalid", allow_missing=True,
        )
        if before is None:
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(f, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise SkillEnablementError(
                "skill_state_invalid", f"changed while opening: {f}",
            )
        _require_same_open_file(f, opened, code="skill_state_invalid")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read()
        _require_same_open_file(f, opened, code="skill_state_invalid")
        _require_same_state_parent(parent[0], parent[1])
        return payload
    except SkillEnablementError:
        raise
    except OSError as exc:
        raise SkillEnablementError("skill_state_invalid", str(exc)) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode_state_payload(payload: bytes) -> dict[str, bool]:
    try:
        value = json.loads(payload.decode("utf-8"))
        return _validated_state(value)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise SkillEnablementError("skill_state_invalid", str(exc)) from exc


def _load_state(*, strict: bool = True) -> dict[str, bool]:
    """Load the policy; only a genuinely absent file selects defaults.

    ``strict`` is retained for source compatibility with older callers.  It no
    longer weakens integrity handling: returning an empty mapping for corrupt
    or inaccessible bytes would silently re-enable every auto-enabled skill.
    Read-only callers receive the same stable diagnostic and can render it.
    """
    del strict
    payload = _read_state_payload()
    if payload is None:
        return {}
    return _decode_state_payload(payload)


def skill_state_cache_signature() -> tuple[str, str, int, str]:
    """Return a validated cache token or raise ``skill_state_invalid``.

    The loader must validate the policy *before* consulting its catalog cache;
    otherwise an unreadable or redirected file is indistinguishable from an
    absent one and an older catalog can remain live.
    """
    payload = _read_state_payload()
    if payload is None:
        return ("skill_state", "absent", 0, "")
    _decode_state_payload(payload)
    return (
        "skill_state",
        "present",
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_state(temporary: Path, destination: Path) -> None:
    """Retry only transient Windows sharing failures around atomic replace."""
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.replace(temporary, destination)
            return
        except OSError as exc:
            if (
                os.name != "nt"
                or getattr(exc, "winerror", None) not in {5, 32, 33}
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))


def _save_state(state: Mapping[str, bool]) -> None:
    """Atomically replace the complete policy while its writer lock is held."""
    validated = _validated_state(dict(state))
    f = _state_file()
    parent = _require_plain_state_parent(f.parent, create=True)
    assert parent is not None
    _require_plain_owned_file(
        f, code="skill_state_invalid", allow_missing=True,
    )
    payload = (
        json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{f.name}.", suffix=".tmp", dir=f.parent,
    )
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise OSError("short write while saving skill state")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _replace_state(temporary, f)
        # Apply the policy to the final directory entry too.  On Windows this
        # is the portable fallback; on POSIX it also repairs legacy modes.
        _require_plain_owned_file(f, code="skill_state_invalid")
        os.chmod(f, 0o600)
        _require_same_state_parent(parent[0], parent[1])
        _fsync_directory(f.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _lock_conflict(exc: OSError) -> bool:
    return (
        isinstance(exc, BlockingIOError)
        or exc.errno in {errno.EACCES, errno.EAGAIN}
        or getattr(exc, "winerror", None) in {32, 33, 36}
    )


def _try_file_lock(descriptor: int) -> bool:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        import msvcrt

        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if _lock_conflict(exc):
                return False
            raise
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if _lock_conflict(exc):
            return False
        raise


def _unlock_file(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextlib.contextmanager
def _state_writer_lock(*, timeout: float = _STATE_LOCK_TIMEOUT) -> Iterator[None]:
    """Serialize read-preflight-replace across threads and processes."""
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    deadline = time.monotonic() + timeout
    if not _STATE_THREAD_LOCK.acquire(timeout=timeout):
        raise SkillEnablementError("skill_state_lock_timeout")
    descriptor = -1
    locked = False
    try:
        path = _state_file().with_name(f"{_state_file().name}.lock")
        parent = _require_plain_state_parent(path.parent, create=True)
        assert parent is not None
        before = _require_plain_owned_file(
            path, code="skill_state_lock_invalid", allow_missing=True,
        )
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise SkillEnablementError(
                "skill_state_lock_invalid", f"{path}: {exc}",
            ) from exc
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise SkillEnablementError("skill_state_lock_invalid", str(path))
        if (
            hasattr(os, "geteuid")
            and hasattr(status, "st_uid")
            and status.st_uid != os.geteuid()
        ):
            raise SkillEnablementError(
                "skill_state_lock_invalid", f"foreign owner: {path}",
            )
        if before is not None and not os.path.samestat(before, status):
            raise SkillEnablementError(
                "skill_state_lock_invalid", f"changed while opening: {path}",
            )
        _require_same_open_file(path, status, code="skill_state_lock_invalid")
        _require_same_state_parent(parent[0], parent[1])
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows has no fchmod
            os.chmod(path, 0o600)
        if status.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        while not _try_file_lock(descriptor):
            if time.monotonic() >= deadline:
                raise SkillEnablementError("skill_state_lock_timeout", str(path))
            time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))
        locked = True
        yield
    finally:
        if descriptor >= 0:
            try:
                if locked:
                    _unlock_file(descriptor)
            finally:
                os.close(descriptor)
        _STATE_THREAD_LOCK.release()


# --- Public API ------------------------------------------------------------


def list_skills(lang: str | None = None) -> list[SkillInfo]:
    """Ritorna l'inventario delle skill (skills/ + legacy _imports/).

    Args:
        lang: se valorizzato, filtra le skill con `lang in {"any", lang}`.
              `None` (default) = nessun filtro.
    """
    state = _load_state()
    out: list[SkillInfo] = []
    for skill_dir in _isd():
        skill_md = skill_dir / "SKILL.md"
        fm = _parse_skill_md(skill_md)
        sk_lang = (fm.get("lang") or "any").lower()
        sk_trust = (fm.get("trust") or "community").lower()
        ae_raw = (fm.get("auto_enable") or "true").lower()
        sk_auto_enable = ae_raw in ("true", "yes", "1", "on")
        # Stato enabled: state file override > auto_enable default.
        if skill_dir.name in state:
            enabled = bool(state[skill_dir.name])
        else:
            enabled = sk_auto_enable
        # Conta executor (sub-dir con manifest.toml).
        n_exec = sum(
            1 for ex in skill_dir.iterdir()
            if ex.is_dir() and (ex / "manifest.toml").is_file()
        )
        is_builtin_repo = str(_C.PATH_EXECUTORS) in str(skill_dir.resolve())
        try:
            from skills_catalog import FIRST_PARTY_BUNDLES as _FPB
        except Exception:
            _FPB = frozenset()
        # First-party per AUTORIALITÀ (lista curata), indipendente dalla
        # provenienza: github (user-data ma nostro), google-workspace/it_locale
        # (vendorizzati). Solo etichetta tier, non gating. Roberto 16/6.
        _is_fp = skill_dir.name in _FPB
        info = SkillInfo(
            name=skill_dir.name,
            path=skill_dir,
            lang=sk_lang,
            # Le nostre skill sono metnos-official anche se importate/vendorizzate
            # (Roberto 16/6). `trust` non alimenta gating (is_metnos_official non
            # ha chiamanti); coerenza display fra tier e fiducia.
            trust=("metnos-official" if _is_fp else sk_trust),
            auto_enable=sk_auto_enable,
            enabled=enabled,
            n_executors=n_exec,
            # Provenienza fisica e autorità sono assi distinti: GitHub vive
            # in user-data ma è mantenuto da Metnos e viene generato come
            # handcrafted senza [provenance]. Non esporlo mai come imported.
            is_imported=not (is_builtin_repo or _is_fp),
            is_builtin_repo=is_builtin_repo,
            is_first_party=_is_fp,
        )
        if lang is not None and info.lang not in ("any", lang.lower()):
            continue
        out.append(info)
    # Skill-capacità FIRST-PARTY (asse 2): photos/mail/web/geo/calendar/github/
    # frontier + core. Non sono bundle-dir ma gruppi di executor (skills_catalog).
    # lang="any" → mai locale-gated; enabled = state override > auto_enable(True).
    try:
        from skills_catalog import FIRST_PARTY_SKILLS, _CORE
        seen = {s.name for s in out}
        for sk in list(FIRST_PARTY_SKILLS) + [_CORE]:
            nm = sk["name"]
            if nm in seen:
                continue
            ae = bool(sk.get("auto_enable", True))
            enabled = bool(state[nm]) if nm in state else ae
            out.append(SkillInfo(
                name=nm, path=None, lang="any", trust="metnos-official",
                auto_enable=ae, enabled=enabled, n_executors=0,
                is_imported=False, is_builtin_repo=True, is_first_party=True,
                requires=sk.get("requires", ""),
            ))
    except Exception as _e:  # pragma: no cover — first-party listing best-effort
        import logging as _lg
        _lg.getLogger(__name__).warning("first-party skills listing failed: %s", _e)
    out.sort(key=lambda s: s.name)
    return out


def get_skill_info(name: str) -> SkillInfo | None:
    for s in list_skills():
        if s.name == name:
            return s
    return None


def _skill_definitions() -> dict[str, SkillInfo]:
    definitions: dict[str, SkillInfo] = {}
    for info in list_skills():
        previous = definitions.get(info.name)
        if previous is not None and (
            previous.lang != info.lang
            or previous.auto_enable != info.auto_enable
        ):
            raise SkillEnablementError(
                "skill_definition_conflict", info.name,
            )
        definitions.setdefault(info.name, info)
    return definitions


def _candidate_policy(
    state: Mapping[str, bool],
    definitions: Mapping[str, SkillInfo],
) -> Callable[[str], bool]:
    """Build the effective visibility predicate for an uncommitted state."""
    runtime_lang = str(_C.DEFAULT_LANG or "").lower()

    def enabled(skill_name: str) -> bool:
        info = definitions.get(skill_name)
        if info is None:
            # Preserve the historical treatment of structurally installed
            # bundles whose optional SKILL.md metadata is unavailable.  Their
            # manifests still cross the authenticated admission boundary.
            return True
        configured = state.get(skill_name, bool(info.auto_enable))
        locale_matches = info.lang in {"any", runtime_lang}
        return bool(configured) and locale_matches

    return enabled


def _inventory_fingerprint(inventory) -> tuple:
    return (
        tuple(
            (
                str(ref.contract_id), ref.status.value, ref.name,
                ref.manifest_hash,
            )
            for ref in inventory.manifests
        ),
        tuple(
            (
                problem.code, problem.path, problem.detail,
                tuple(problem.contracts),
            )
            for problem in inventory.problems
        ),
    )


def _require_clean_inventory(inventory) -> None:
    # Name collisions are rendered through the shared global rule below so
    # authoring and store-only produce the same stable error contract.
    problems = tuple(
        problem for problem in inventory.problems
        if problem.code != "name_collision"
    )
    if problems:
        detail = "; ".join(
            f"{problem.code}:{problem.path}" for problem in problems[:8]
        )
        raise SkillEnablementError("contract_inventory_invalid", detail)


def _require_unique_names(entries) -> None:
    from manifest_inventory import manifest_name_collisions

    collisions = manifest_name_collisions(entries)
    if not collisions:
        return
    detail = "; ".join(
        f"{name}=[{','.join(contract_ids)}]"
        for name, contract_ids in collisions[:8]
    )
    raise SkillEnablementError("contract_name_collision", detail)


def _authoring_name_entries(inventory) -> list[tuple[object, str]]:
    entries = []
    for ref in inventory.installed():
        if not isinstance(ref.name, str) or not ref.name.strip():
            raise SkillEnablementError(
                "contract_name_invalid", str(ref.contract_id),
            )
        entries.append((ref.contract_id, ref.name))
    return entries


def _authoring_candidate_preflight(policy: Callable[[str], bool]) -> None:
    from manifest_inventory import (
        default_manifest_sources,
        inventory_authoring_manifests,
    )

    first = inventory_authoring_manifests(
        default_manifest_sources(), skill_enabled=policy,
    )
    _require_clean_inventory(first)
    _require_unique_names(_authoring_name_entries(first))

    # A second observation avoids committing a policy derived from a manifest
    # tree that changed during the preflight.  Authoring remains the legacy
    # authority in this layout, so no store lock exists to acquire instead.
    second = inventory_authoring_manifests(
        default_manifest_sources(), skill_enabled=policy,
    )
    _require_clean_inventory(second)
    _require_unique_names(_authoring_name_entries(second))
    if _inventory_fingerprint(first) != _inventory_fingerprint(second):
        raise SkillEnablementError("contract_inventory_unstable")


def _revision_identity(revision) -> str:
    generation = getattr(revision, "generation_id", None)
    if isinstance(generation, str):
        return generation
    retirement = getattr(revision, "retirement_id", None)
    if isinstance(retirement, str):
        return retirement
    raise SkillEnablementError("contract_revision_invalid")


def _store_candidate_preflight(policy: Callable[[str], bool]) -> None:
    from contract_store import ContractRetirement, current_contract, current_revision_id
    from manifest_inventory import (
        ManifestStatus,
        default_manifest_sources,
        inventory_store_manifests,
    )
    from sign import list_trusted_publics

    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise SkillEnablementError("contract_trusted_keys_missing")

    for attempt in range(2):
        inventory = inventory_store_manifests(
            default_manifest_sources(), skill_enabled=policy,
        )
        _require_clean_inventory(inventory)
        # Disabled bindings remain installed publication targets.  Authenticate
        # every installed current revision, then let only the candidate-visible
        # subset contribute names.
        refs = inventory.installed()
        try:
            expected = {
                str(ref.contract_id): current_revision_id(ref)
                for ref in refs
            }
            entries = []
            changed = False
            for ref in refs:
                revision = current_contract(ref, trusted_publics=trusted)
                if _revision_identity(revision) != expected[str(ref.contract_id)]:
                    changed = True
                    break
                # A signed tombstone is authenticated above but deliberately
                # contributes no installed executor name.
                if isinstance(revision, ContractRetirement):
                    continue
                name = revision.parsed.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise SkillEnablementError(
                        "contract_name_invalid", str(ref.contract_id),
                    )
                entries.append((ref.contract_id, name))
            if changed:
                if attempt == 0:
                    continue
                raise SkillEnablementError("contract_snapshot_unstable")

            after_inventory = inventory_store_manifests(
                default_manifest_sources(), skill_enabled=policy,
            )
            _require_clean_inventory(after_inventory)
            if _inventory_fingerprint(inventory) != _inventory_fingerprint(
                after_inventory
            ):
                if attempt == 0:
                    continue
                raise SkillEnablementError("contract_snapshot_unstable")
            after_refs = after_inventory.installed()
            after = {
                str(ref.contract_id): current_revision_id(ref)
                for ref in after_refs
            }
            if expected != after:
                if attempt == 0:
                    continue
                raise SkillEnablementError("contract_snapshot_unstable")
        except SkillEnablementError:
            raise
        except Exception as exc:
            raise SkillEnablementError(
                "contract_preflight_failed",
                f"{type(exc).__name__}: {exc}",
            ) from exc
        _require_unique_names(entries)
        return
    raise SkillEnablementError("contract_snapshot_unstable")


def _candidate_preflight(policy: Callable[[str], bool]) -> None:
    from manifest_inventory import ManifestLayout, resolve_manifest_layout

    if resolve_manifest_layout() is ManifestLayout.AUTHORING:
        _authoring_candidate_preflight(policy)
        return
    _store_candidate_preflight(policy)


def set_skill_enabled_checked(name: str, enabled: bool) -> bool:
    """Validate and atomically commit one external visibility-policy change.

    Disabled bindings remain installed and immutable.  Enabling only changes
    the policy file: it never signs or publishes a contract.  The candidate
    catalog is proven globally name-unique before the atomic state replace,
    while one portable sidecar lock serializes competing CLI/UI processes.
    Returns whether the persisted mapping changed.
    """
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise SkillEnablementError("skill_name_invalid")
    if type(enabled) is not bool:
        raise SkillEnablementError("skill_enabled_invalid", name)

    # Global catalog membership is the first lock in the canonical order;
    # publication and visibility changes therefore cannot validate two
    # individually sound but mutually colliding candidates concurrently.
    from contract_store import catalog_admission_lock

    with catalog_admission_lock():
        with _state_writer_lock():
            state = _load_state(strict=True)
            definitions = _skill_definitions()
            info = definitions.get(name)
            if info is None:
                raise SkillEnablementError("skill_unknown", name)
            if name == "core" and not enabled:
                raise SkillEnablementError("skill_core_required", name)
            candidate = dict(state)
            candidate[name] = enabled
            _candidate_preflight(_candidate_policy(candidate, definitions))
            changed = candidate != state
            if changed:
                _save_state(candidate)
                # Same-process callers must not depend on timestamp resolution
                # for cache invalidation.  Other processes observe the atomic
                # file and its content-based cache signature.
                try:
                    from loader import invalidate_catalog_cache

                    invalidate_catalog_cache()
                except ImportError:
                    pass
            return changed


def validate_current_skill_policy() -> None:
    """Re-prove the persisted policy before another visibility reactivation.

    Executor aging is a separate external visibility policy.  Its explicit
    unarchive path calls this boundary so making an old catalog member visible
    again cannot bypass authenticated global name uniqueness.
    """
    from contract_store import catalog_admission_lock

    with catalog_admission_lock():
        with _state_writer_lock():
            state = _load_state(strict=True)
            definitions = _skill_definitions()
            _candidate_preflight(_candidate_policy(state, definitions))


def set_skill_enabled(name: str, enabled: bool) -> None:
    """Compatibility wrapper for the checked transactional operation."""
    set_skill_enabled_checked(name, enabled)


def is_skill_enabled(name: str) -> bool:
    """Ritorna True se la skill `<name>` e' abilitata.

    Default decisionale: legge lo state file; se mancante consulta
    `SKILL.md::auto_enable` (default True quando il campo manca).
    Locale gate (`lang`): skill non-`any` con lang != DEFAULT_LANG
    risultano DISABLED-by-locale (ritorna False).
    """
    info = get_skill_info(name)
    if info is None:
        # Skill sconosciuta: default True (loader fa comunque check su
        # `manifest.toml` presence). Niente "false-positive disable" per
        # skill non ancora installate via questo registry.
        return True
    # Locale gate.
    if info.lang != "any" and info.lang != _C.DEFAULT_LANG.lower():
        return False
    return info.enabled


def matches_locale(lang_field: str, runtime_lang: str | None = None) -> bool:
    """Helper: True se il campo `lang` di una skill matcha il runtime lang."""
    lf = (lang_field or "any").lower()
    if lf == "any":
        return True
    rt = (runtime_lang or _C.DEFAULT_LANG).lower()
    return lf == rt
