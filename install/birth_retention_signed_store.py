"""Authenticated physical signed history; no age-based publication closure.

The store owns immutable admissions, not an expiration or rollback revocation.
Consequently this projection retains history until native closure is joined.
It neither reconstructs today's authoring code nor grants publication rights.
Native crash staging is retained OPEN after read-only recovery validation.
Unknown staging/recovery namespaces stop the inventory rather than becoming
unreferenced files. Acquisition uses the same descriptor custody as F6 owners;
authentication uses the native pure historical/retirement verifiers.
"""
from dataclasses import replace
import os
import re
import time
from types import MappingProxyType, SimpleNamespace

import contract_store as native
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _iso
from sign import verify_manifest_bytes


_HEX = re.compile(r"[0-9a-f]{64}")
_V2 = native._ADMISSION_RECEIPTS_V2


def _receipt_staging_destination(name):
    """Recognize the receipt destination and reuse native atomic-write suffixes.

    The native direct-staging parser restricts its destination to binding or
    current. Receipt writes use the same _atomic_replace_file producer; only
    their destination is different. Recognition confers no receipt authority.
    """
    if not name.startswith('.'):
        return None
    digest, separator, suffix = name[1:].partition('.json.')
    if (separator and _HEX.fullmatch(digest)
            and native._DIRECT_STAGING_RE.fullmatch('.current.' + suffix)):
        return digest
    return None


class _SignedStoreOwner(_PrivateFiles):
    name = "signed_contract_history"

    def __init__(self, *, root, require_exclusion, owner, admission_keys, author_keys):
        super().__init__(root=root, require_exclusion=require_exclusion, owner=owner)
        self.admission_keys, self.author_keys = dict(admission_keys), dict(author_keys)

    def identity(self, local_id, contract=None):
        parts = local_id.split("/") if type(local_id) is str else []
        if not parts or not _HEX.fullmatch(parts[0]):
            raise RetentionError("retention_owner_invalid", "signed history identity")
        kind = "evidence"
        if len(parts) == 2 and parts[1] in {native.BINDING_FILE, "current", "writer.lock"}:
            pass
        elif len(parts) == 2 and native._DIRECT_STAGING_RE.fullmatch(parts[1]):
            pass
        elif (len(parts) == 4 and parts[1] == "generations"
              and parts[2].startswith('.generation-') and len(parts[2]) > len('.generation-')
              and parts[3] in set(native.GENERATION_FILES) | set(native.RETIREMENT_FILES)):
            pass
        elif len(parts) == 4 and parts[1] == "generations" and _HEX.fullmatch(parts[2]):
            if parts[3] in native.GENERATION_FILES:
                kind = "generation"
            elif parts[3] in native.RETIREMENT_FILES:
                kind = "retirement"
            else:
                raise RetentionError("retention_owner_invalid", "signed revision file")
        elif (len(parts) == 3 and parts[1] == "admission-receipts"
              and parts[2].endswith(".json") and _HEX.fullmatch(parts[2][:-5])):
            kind = "admission_receipt"
        elif (len(parts) == 4 and parts[1] == _V2 and _HEX.fullmatch(parts[2])
              and parts[3].endswith(".json") and _HEX.fullmatch(parts[3][:-5])):
            kind = "admission_receipt"
        elif (((len(parts) == 3 and parts[1] == 'admission-receipts')
               or (len(parts) == 4 and parts[1] == _V2 and _HEX.fullmatch(parts[2])))
              and _receipt_staging_destination(parts[-1]) is not None):
            pass
        else:
            raise RetentionError("retention_owner_invalid", "signed history path")
        return ObjectIdentity(self.name, self.root.as_uri(), kind, local_id, contract)

    def generation_identity(self, contract_id, generation_id):
        physical = native.generation_directory_name(generation_id)
        return self.identity(f"{native.contract_storage_key(contract_id)}/generations/{physical}/manifest.toml",
                             contract_id.value)

    def _identity(self, identity):
        if identity != self.identity(identity.local_id, identity.contract):
            raise RetentionError("retention_owner_invalid", "foreign signed history")
        if identity.contract is not None:
            from manifest_inventory import ContractId, ManifestOrigin
            origin, relative = identity.contract.split(":", 1)
            contract = ContractId(ManifestOrigin(origin), relative)
            if native.contract_storage_key(contract) != identity.local_id.split('/')[0]:
                raise RetentionError("retention_owner_invalid", "signed contract binding")
        return identity.local_id.split("/")

    def _files(self):
        files, directories, total = {}, {}, 0
        deadline = time.monotonic() + 15

        def walk(parts):
            nonlocal total
            if time.monotonic() > deadline or len(directories) + len(files) >= 100_000:
                raise RetentionError("retention_inventory_incomplete", "signed inventory budget")
            with self._directory(*parts) as (directory, custody):
                names = self._names(directory)
                directories[parts] = names
                for name in names:
                    child = (*parts, name)
                    # Only native structural directories are traversable.
                    is_directory = ((not parts and _HEX.fullmatch(name))
                        or (len(parts) == 1 and name in {"generations", "admission-receipts", _V2})
                        or (len(parts) == 2 and parts[1] in {"generations", _V2} and _HEX.fullmatch(name))
                        or (len(parts) == 2 and parts[1] == "generations"
                            and name.startswith('.generation-') and len(name) > len('.generation-')))
                    if is_directory:
                        walk(child)
                    else:
                        identity = self.identity('/'.join(child))
                        observed = self._read_file(directory, custody, name, identity,
                            include_payload=True, max_bytes=1024 * 1024)
                        if observed is None:
                            raise RetentionError("retention_owner_changed", "signed file disappeared")
                        files[child] = observed
                        total += len(observed[4])
                    if total > 128 << 20 or time.monotonic() > deadline or len(directories) + len(files) >= 100_000:
                        raise RetentionError("retention_inventory_incomplete", "signed inventory budget")
                if self._names(directory) != names:
                    raise RetentionError("retention_owner_changed", "signed namespace changed")
        try:
            walk(())
        except FileNotFoundError as exc:
            raise RetentionError("retention_inventory_incomplete", "signed store missing") from exc
        return files, directories

    def _project(self, files, directories):
        objects, refs, admissions, evidence_by_identity = {}, {}, {}, {}
        by_contract = {}
        deadline = time.monotonic() + 15
        for path in files:
            by_contract.setdefault(path[0], []).append(path)

        def add(path, contract, roots=(RootKind.OPEN_AUDIT,)):
            raw = files[path]
            identity = self.identity('/'.join(path), contract)
            objects[path] = OwnerObject(identity, raw[1], NodeState.OPEN,
                                       _iso(raw[3]), None, roots=roots)
            refs[path] = set()
            return identity

        def link(a, b, *, both=False):
            if b not in objects:
                raise RetentionError("retention_inventory_incomplete", "signed reference absent")
            refs[a].add(objects[b].identity)
            if both:
                refs[b].add(objects[a].identity)

        for key in directories[()]:
            if time.monotonic() > deadline:
                raise RetentionError("retention_inventory_incomplete", "signed authentication budget")
            names = set(directories[(key,)])
            if names == {"generations", "writer.lock"}:
                if directories[(key, "generations")] or files[(key, "writer.lock")][4] != b'\0':
                    raise RetentionError("retention_inventory_incomplete", "unbound signed namespace")
                add((key, "writer.lock"), None)
                continue
            binding_path = (key, native.BINDING_FILE)
            staged_bindings = [path for path in by_contract.get(key, ())
                               if len(path) == 2 and native._DIRECT_STAGING_RE.fullmatch(path[1])
                               and path[1].startswith('.binding.json.')]
            if native.BINDING_FILE not in names and staged_bindings:
                # A first binding can crash before its atomic installation.
                # Its canonical bytes identify the physical namespace only;
                # they grant no publication or deletion authority.
                binding_path = staged_bindings[0]
                if ("current" in names or directories.get((key, "generations"))
                        or any(path[1] in {'admission-receipts', _V2} for path in by_contract.get(key, ()))):
                    raise RetentionError("retention_inventory_incomplete", "unbound signed history")
            if binding_path not in files or "generations" not in names:
                raise RetentionError("retention_inventory_incomplete", "signed binding absent")
            binding = native.decode_binding(files[binding_path][4], storage_key=key)
            contract = binding.contract_id.value
            # Reuse native recovery validation, never its deleting half. The
            # bounded descriptor scan runs before and after these path readers.
            directory = self.root / key
            if any(len(path) == 2 and native._DIRECT_STAGING_RE.fullmatch(path[1])
                   for path in by_contract.get(key, ())):
                native._direct_staging_recovery_plan(
                    native._activation_manifest_ref(binding.contract_id), directory,
                    trusted_publics=tuple(self.author_keys.items()), store_root=self.root)
            native._staging_directory_recovery_plan(directory / 'generations')
            for path in by_contract.get(key, ()):
                add(path, contract)
            if "writer.lock" in names and files[(key, "writer.lock")][4] != b'\0':
                raise RetentionError("retention_owner_state_invalid", "signed writer lock")
            revisions, retired = {}, {}
            for generation in directories[(key, "generations")]:
                if generation.startswith('.generation-'):
                    continue
                if time.monotonic() > deadline:
                    raise RetentionError("retention_inventory_incomplete", "signed authentication budget")
                parent = (key, "generations", generation)
                children = set(directories[parent])
                payloads = {name: files[(*parent, name)][4] for name in children}
                identifier = 'sha256:' + generation
                if children == set(native.GENERATION_FILES):
                    if native.generation_id(payloads) != identifier:
                        raise RetentionError("retention_owner_state_invalid", "signed generation digest")
                    verify_manifest_bytes(payloads['manifest.toml'], payloads['manifest.toml.sig'],
                                          trusted_publics=self.author_keys.items())
                    hub = (*parent, 'manifest.toml')
                elif children == set(native.RETIREMENT_FILES):
                    retirement = native._authenticate_retirement_payloads(
                        SimpleNamespace(contract_id=binding.contract_id), payloads,
                        trusted_publics=tuple(self.author_keys.items()), identifier=identifier)
                    retired[identifier] = retirement.previous_generation_id
                    hub = (*parent, 'retirement.json')
                else:
                    raise RetentionError("retention_inventory_incomplete", "signed revision structure")
                revisions[identifier] = (hub, payloads)
                for name in children:
                    if (*parent, name) != hub:
                        link(hub, (*parent, name), both=True)
                link(hub, binding_path)
            for identifier, predecessor in retired.items():
                if predecessor not in revisions or predecessor in retired:
                    raise RetentionError("retention_inventory_incomplete", "retirement predecessor absent")
                link(revisions[identifier][0], revisions[predecessor][0])
            if 'current' in names:
                encoded = files[(key, 'current')][4]
                if (len(encoded) != 72 or not encoded.startswith(b'sha256:')
                        or not encoded.endswith(b'\n')):
                    raise RetentionError("retention_owner_state_invalid", "signed current pointer")
                current = encoded[:-1].decode('ascii')
                if current not in revisions:
                    raise RetentionError("retention_inventory_incomplete", "current revision absent")
                pointer = (key, 'current')
                objects[pointer] = replace(objects[pointer], roots=(RootKind.CURRENT_POINTER,))
                link(pointer, revisions[current][0])
                if current in retired:
                    path = revisions[retired[current]][0]
                    objects[path] = replace(objects[path], roots=(RootKind.RETIREMENT_PREDECESSOR,))
            for path in by_contract.get(key, ()):
                if len(path) == 2 and path[1].startswith('.current.'):
                    staged = files[path][4][:-1].decode('ascii')
                    if staged not in revisions:
                        raise RetentionError("retention_inventory_incomplete", "staged current revision absent")
                    link(path, revisions[staged][0])
            for path in by_contract.get(key, ()):
                if time.monotonic() > deadline:
                    raise RetentionError("retention_inventory_incomplete", "signed authentication budget")
                staged_receipt = _receipt_staging_destination(path[-1])
                if len(path) >= 3 and path[1] in {'admission-receipts', _V2} and staged_receipt is not None:
                    generation = 'sha256:' + (staged_receipt if path[1] == 'admission-receipts' else path[2])
                    if generation not in revisions or generation in retired:
                        raise RetentionError("retention_inventory_incomplete", "staged receipt generation absent")
                    # A crash can leave empty/truncated bytes. Preserve the
                    # physical write and its target, without treating it as a
                    # committed or authenticated admission (even if complete).
                    link(path, revisions[generation][0])
                    continue
                if objects[path].identity.node_type != 'admission_receipt':
                    continue
                generation = 'sha256:' + (path[2][:-5] if path[1] == 'admission-receipts' else path[2])
                context = None if path[1] == 'admission-receipts' else 'sha256:' + path[3][:-5]
                if generation not in revisions or generation in retired:
                    raise RetentionError("retention_inventory_incomplete", "admitted generation absent")
                hub, payloads = revisions[generation]
                evidence = native.HistoricalBirthEvidenceV1(binding.contract_id, generation, context,
                    files[binding_path][4], files[path][4], payloads['manifest.toml'],
                    payloads['manifest.toml.sig'], payloads['manifest.lang_state.json'])
                admission = native.verify_historical_birth_evidence_v1(evidence,
                    admission_verifier_keys=self.admission_keys, author_verifier_keys=self.author_keys)
                admissions[objects[path].identity] = admission
                evidence_by_identity[objects[path].identity] = evidence
                link(path, hub, both=True)
                if admission.predecessor_id is not None:
                    if admission.predecessor_id not in revisions:
                        raise RetentionError("retention_inventory_incomplete", "admission predecessor absent")
                    link(path, revisions[admission.predecessor_id][0])
            for generation in directories.get((key, _V2), ()):
                if 'sha256:' + generation not in revisions or 'sha256:' + generation in retired:
                    raise RetentionError("retention_inventory_incomplete", "empty receipt namespace binding")
        if time.monotonic() > deadline:
            raise RetentionError("retention_inventory_incomplete", "signed authentication budget")
        if set(files) != set(objects):
            raise RetentionError("retention_inventory_incomplete", "unprojected signed files")
        objects_tuple = tuple(replace(obj, references=tuple(sorted(refs[path], key=lambda item: item.key.node_id)))
                              for path, obj in sorted(objects.items()))
        return objects_tuple, MappingProxyType(admissions), MappingProxyType(evidence_by_identity)

    def inventory(self):
        return self.scan()[0]

    def scan(self):
        """Return physical objects and native authenticated admission values."""
        return self.scan_with_evidence()[:2]

    def scan_with_evidence(self):
        """Also expose the exact verified bytes for native terminal reconciliation."""
        before = self._files()
        try:
            objects = self._project(*before)
        except RetentionError:
            raise
        except (native.ContractStoreError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise RetentionError("retention_owner_state_invalid", "signed history authentication") from exc
        if before != self._files():
            raise RetentionError("retention_owner_changed", "signed history changed")
        return objects

    def version(self, identity):
        parts = self._identity(identity)
        try:
            with self._directory(*parts[:-1]) as (directory, custody):
                observed = self._read_file(directory, custody, parts[-1], identity,
                                           max_bytes=1024 * 1024)
                if observed is None:
                    os.fsync(directory)
                return None if observed is None else observed[1]
        except FileNotFoundError:
            return None

    def delete(self, identity, expected_version):
        self._identity(identity)
        self.require_exclusion()
        raise RetentionError("retention_owner_state_invalid", "signed history lacks native closure")
