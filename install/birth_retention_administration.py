"""Closed, stopped F6 maintenance behind the installed authority launcher.

Native owners supply every object and effect. Historical public bindings live
only inside one exclusion; the projection is disposable. An interrupted effect
can only resume its signed journal, and ordinary startup remains blocked until
the native catalog and complete physical inventory have been read back.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory

from executor_birth_retention import RetentionError
from install.birth_retention_maintenance import (
    Maintenance, _digest, _update_holds, _version, plan, read_holds,
)


_FIELDS = {
    'holds': {'holds', 'expected_version'},
    'plan': {'public_sources'},
    'collect': {'public_sources', 'expected_inventory', 'max_objects', 'max_seconds'},
    'resume': {'public_sources', 'max_objects', 'max_seconds'},
}
_MAX_INPUT = 48 << 20


def _read_input(stage):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result

    raw = sys.stdin.buffer.readline(_MAX_INPUT + 1)
    try:
        if len(raw) > _MAX_INPUT:
            raise ValueError('size')
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
        if type(document) is not dict or set(document) != _FIELDS[stage]:
            raise ValueError('fields')
    except (KeyError, UnicodeDecodeError, ValueError) as exc:
        raise RetentionError('retention_invalid', 'administrative document') from exc
    return document


def _public_sources(document):
    from executor_birth_prepared_root import _historical_source_candidates_v1

    encoded = document['public_sources']
    if type(encoded) is not list or len(encoded) > 256:
        raise RetentionError('retention_invalid', 'historical public sources')
    sources = []
    try:
        for item in encoded:
            if type(item) is not list or len(item) != 2 or type(item[1]) is not str:
                raise ValueError('source shape')
            sources.append((item[0], base64.b64decode(item[1], validate=True)))
        result = tuple(sources)
        # The native Producer reader validates path, per-file and total bounds.
        # These bytes stay inert; the historical signed hash selects them.
        _historical_source_candidates_v1(result)
    except (TypeError, ValueError, binascii.Error) as exc:
        raise RetentionError('retention_invalid', 'historical public sources') from exc
    return result


def _budget(document):
    count, seconds = document['max_objects'], document['max_seconds']
    if (type(count) is not int or not 0 < count <= 10_000
            or type(seconds) not in (int, float) or not math.isfinite(seconds)
            or not 0 < seconds <= 300):
        raise RetentionError('retention_invalid', 'administrative budget')
    return count, seconds


def _maintenance_root(*, create=False):
    from executor_birth_authority_files import (
        DEFAULT_OWNERSHIP_ROOT_V1, _directory_metadata, _root_owned_chain,
    )
    from install.birth_ownership_authority_provisioner import _sync_directory

    parent = DEFAULT_OWNERSHIP_ROOT_V1
    _root_owned_chain(parent)
    root = parent / 'retention-maintenance-v1'
    if create:
        try:
            root.mkdir(mode=0o755)
        except FileExistsError:
            pass
        else:
            os.chmod(root, 0o755, follow_symlinks=False)
            _sync_directory(parent)
    _directory_metadata(root, root_owned=True)
    return root


def _load_inventory(context, public_sources):
    from install.birth_retention_context_census import _historical_context_census_v1
    from install.birth_retention_installed_paths import _capture_installed_paths_v1
    from install.birth_retention_native_inventory import (
        _compose_native_inventory_v1, _historical_keys_v1,
    )
    from install.birth_retention_physical_census import _PhysicalCensusV1
    from install.operator_authority import PRIVATE_BASE
    from loader import _load_catalog_for_cutover_audit_v1

    installed = _capture_installed_paths_v1(
        require_stability=context.require_exclusion,
        require_held_stability=context.require_held_exclusion)
    if installed.non_python_services:
        raise RetentionError('retention_inventory_incomplete', 'unclassified native service')
    environment = installed.environment
    if environment.account != context.account or environment.layout != context.layout:
        raise RetentionError('retention_service_identity_invalid', 'installed account changed')
    paths = dict(installed.observations[0][1].paths)
    owner = (context.account.record.uid, context.account.record.gid)
    bindings = _historical_context_census_v1(
        session=context.session, require_exclusion=context.require_held_exclusion)
    if any(binding.required_head_id != context.frontier[0] for binding in bindings.values()):
        raise RetentionError('retention_installation_changed', 'historical frontier')
    author_keys, _ = _historical_keys_v1(bindings)

    def catalog_readback():
        context.require_exclusion()
        result = _load_catalog_for_cutover_audit_v1(
            catalog_trusted_owner=owner, trusted_publics=tuple(author_keys.items()))
        context.require_exclusion()
        return result

    catalog = catalog_readback()
    native = _compose_native_inventory_v1(
        paths=paths, account_owner=owner, birth_root=Path(context.session._root_path),
        session=context.session, context_bindings=bindings, catalog=catalog,
        reviews_root=PRIVATE_BASE / 'reviews' / str(owner[0]),
        require_exclusion=context.require_held_exclusion, public_sources=public_sources)
    census = _PhysicalCensusV1(native_inventory=native, paths=paths,
        account_owner=owner, birth_root=Path(context.session._root_path),
        session=context.session, require_exclusion=context.require_held_exclusion)

    def recovery_readback():
        catalog_readback()
        census.inventory(context=context)

    return census, recovery_readback


def _snapshot_digest(objects, holds, candidates=()):
    rows = []
    for obj in objects:
        rows.append(dict(identity=asdict(obj.identity), version=obj.version,
            state=obj.state.value, created_at=obj.created_at, eligible_after=obj.eligible_after,
            references=sorted((asdict(ref) for ref in obj.references), key=_digest),
            roots=sorted(root.value for root in obj.roots)))
    return _digest({'objects': sorted(rows, key=lambda row: _digest(row['identity'])),
                    'holds': sorted(holds),
                    'candidates': sorted(obj.identity.key.node_id for obj in candidates)})


def _projection(census, context, root, holds):
    objects = census.inventory(context=context)
    observed_roots = census.observed_roots(
        objects, holds_root=root, holds=holds, root_owned=True)
    observed_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    run_id = 'sha256:' + secrets.token_hex(32)
    with TemporaryDirectory(prefix='.projection-', dir=root) as temporary:
        candidates = plan(objects, observed_owners=frozenset(census.owners),
            required_owners=frozenset(census.owners), observed_roots=observed_roots,
            holds=holds, graph_path=Path(temporary) / 'graph.sqlite',
            run_id=run_id, observed_at=observed_at)
    counts = {name: {'objects': 0, 'candidates': 0} for name in census.owners}
    for obj in objects:
        entry = counts.setdefault(obj.identity.owner, {'objects': 0, 'candidates': 0})
        entry['objects'] += 1
    for obj in candidates:
        counts[obj.identity.owner]['candidates'] += 1
    report = {'inventory_digest': _snapshot_digest(objects, holds, candidates),
        'objects': len(objects), 'candidates': len(candidates), 'owners': counts,
        'observed_at': observed_at, 'run_id': run_id}
    context.require_exclusion()
    return candidates, report


def _receipt_keys(*, signing):
    from executor_birth_retention_authority import (
        DEFAULT_DIRECTORY_V1, PRIVATE_BASENAME_V1,
        load_retention_verification_keys_v1,
    )
    keys = {item.key_id: item.public_key for item in load_retention_verification_keys_v1()
            if item.status == 'active'}
    if not signing:
        return None, None, keys
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from executor_birth_authority_files import _read_regular, _root_owned_chain
    from install.birth_retention_authority_provisioner import (
        _forbidden_retention_public_keys_v1, _verify_pair,
    )
    _root_owned_chain(DEFAULT_DIRECTORY_V1)
    public = _verify_pair(DEFAULT_DIRECTORY_V1, root_owned=True,
                         forbidden_public_keys=_forbidden_retention_public_keys_v1())
    if public.status != 'active' or public.key_id not in keys:
        raise RetentionError('retention_invalid', 'inactive receipt authority')
    private = Ed25519PrivateKey.from_private_bytes(_read_regular(
        DEFAULT_DIRECTORY_V1 / PRIVATE_BASENAME_V1, maximum=32, mode=0o600, root_owned=True))
    return public.key_id, private, keys


def run_retention(stage):
    if stage == 'provision-key':
        from install.birth_retention_authority_provisioner import provision_retention_authority_v1
        public = provision_retention_authority_v1()
        return {'key_id': public.key_id, 'status': public.status,
                'public_key': base64.b64encode(public.public_key.public_bytes_raw()).decode('ascii')}
    document = _read_input(stage)
    if stage in ('collect', 'resume'):
        max_objects, max_seconds = _budget(document)
    if stage == 'collect':
        _version(document['expected_inventory'])
    if stage == 'holds':
        values = document['holds']
        if (type(values) is not list or any(type(value) is not str for value in values)
                or len(values) != len(set(values))):
            raise RetentionError('retention_invalid', 'explicit hold set')
        holds = frozenset(_version(value) for value in values)
    else:
        sources = _public_sources(document)
    from install.birth_retention_exclusion import administrative_retention_exclusion_v1
    with administrative_retention_exclusion_v1() as context:
        root = _maintenance_root(create=stage == 'holds')
        if stage == 'holds':
            version = _update_holds(root, root_owned=True, holds=holds,
                expected_version=document['expected_version'], require_exclusion=context.require_exclusion)
            return {'holds_version': version, 'holds': sorted(holds)}
        holds = read_holds(root, root_owned=True)
        if stage != 'resume':
            from executor_birth_authority_files import (
                DEFAULT_OWNERSHIP_ROOT_V1, _require_no_retention_maintenance_at_v1,
            )
            _require_no_retention_maintenance_at_v1(DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True)
        census, verify_recovery = _load_inventory(context, sources)
        maintenance = Maintenance(root, root_owned=True,
                                  require_exclusion=context.require_held_exclusion)
        if stage == 'resume':
            # A reference group may be partly gone: construct the native owners,
            # then use only the original signed intents until recovery completes.
            _, _, public_keys = _receipt_keys(signing=False)
        else:
            candidates, report = _projection(census, context, root, holds)
            if stage == 'plan':
                return report
            if report['inventory_digest'] != document['expected_inventory']:
                raise RetentionError('retention_state_changed', 'reviewed inventory')
            key_id, private_key, public_keys = _receipt_keys(signing=True)
            context.require_exclusion()
            maintenance.begin(candidates, run_id=report['run_id'], observed_at=report['observed_at'],
                key_id=key_id, private_key=private_key, public_keys=public_keys, owners=census.owners)
        # Reobserve the installed conditions at each phase boundary. Within a
        # phase, the same process retains every native lock; owner CAS and the
        # held-lock guard precede each effect without reauthenticating history.
        context.require_exclusion()
        outcome = maintenance.resume(census.owners, public_keys=public_keys,
            max_objects=max_objects, max_seconds=max_seconds)
        context.require_exclusion()
        finished = maintenance.finish(census.owners, public_keys=public_keys,
            verify_recovery=verify_recovery, preserve_remaining=True)
        context.require_exclusion()
        return {'maintenance': outcome, 'recovery': finished}
