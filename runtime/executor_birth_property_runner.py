"""Core-owned orchestration of the seven RM-0008 property groups."""
from __future__ import annotations

import tomllib
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping, Protocol

from executor_birth_identity import encode_framed_v1
from executor_birth_properties import (
    PROPERTY_CATALOG_V1,
    PropertyContractError,
    PropertyEvidence,
    PropertySpec,
    PropertyStatus,
)
from executor_birth_primitive_table_v1 import (
    PrimitiveTableError, check_primitive_v1, check_registry_v1,
)
from executor_birth_runner import (
    FixtureOp, FixtureOpKind, LinuxSandboxRegistry, RunnerStatus,
    WindowsSandboxRegistry,
    run_birth_phase,
)


@dataclass(frozen=True, slots=True)
class PropertyCase:
    case_id: str
    input_value: Mapping[str, object]
    expectation: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PropertyRunResult:
    output: Mapping[str, object]
    observations: Mapping[str, object]
    runner_attestation_hash: str


class PropertyRunner(Protocol):
    def run(self, case: PropertyCase, *, fixture_id: str, isolation: str) -> PropertyRunResult: ...


def _attestation_hash(result: object, *, candidate_id: str, case_id: str,
                      fixture_id: str, isolation: str) -> str:
    attestation = getattr(result, "attestation")
    return _hash({
        "candidate_id": candidate_id,
        "case_id": case_id,
        "fixture_id": fixture_id,
        "isolation": isolation,
        "backend": attestation.backend,
        "sandboxed": attestation.sandboxed,
        "network_unshared": attestation.network_unshared,
        "pid_unshared": attestation.pid_unshared,
        "user_unshared": attestation.user_unshared,
        "ipc_unshared": attestation.ipc_unshared,
        "uts_unshared": attestation.uts_unshared,
        "cgroup_v2": attestation.cgroup_v2,
        "tree_empty": attestation.tree_empty,
        "termination_attested": attestation.termination_attested,
    })


_HARNESS_PATH = "_metnos_birth_property_harness_v1.py"
_STDIO_PATH = "_metnos_birth_property_stdio_v1.py"
_HELPER_MODEL_PATH = "_metnos_birth_helper_model_v1.py"
_REVERSE_PATH = "_metnos_birth_reverse_v1.py"
_REVERSE_SOURCE = b'''import json, os, pathlib, sys
os.environ['METNOS_WORKSPACE'] = str(pathlib.Path(sys.argv[2]) / 'workspace')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / 'runtime'))
from reverse_patterns import apply_patterns
value = json.load(sys.stdin)
patterns = json.loads(sys.argv[1])
if os.environ.get('METNOS_BIRTH_PROVIDER_FIXTURE') == '1':
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _metnos_birth_provider_fixture_v1 import configure_bridge, reverse
    configure_bridge()
    output = reverse(patterns, value['results'])
else:
    output = apply_patterns(patterns, value['plan'], value['results'])
print(json.dumps(output))
'''
_SESSION_CLIENT_SOURCE = b'''import json, pathlib, socket
def _request(operation, arguments):
    address = pathlib.Path(__file__).resolve().parents[3] / 'helper.sock'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1.0)
        connection.connect(str(address))
        connection.sendall(json.dumps({'operation': operation, 'arguments': arguments}).encode() + b'\\n')
        with connection.makefile('rb') as stream:
            reply = stream.readline(65537)
        if not reply or len(reply) > 65536: raise ValueError('fixture_frame_invalid')
        return json.loads(reply)
def session_open(**kwargs): return _request('open', kwargs)
def session_close(**kwargs): return _request('close', kwargs)
'''
# The registered interpreter runs these read-only CLI entrypoints. No new
# executable, executable permission, service or production helper is installed.
_HELPER_CLIENT_SOURCE = r'''
import json, pathlib, socket, sys
address = pathlib.Path(__file__).resolve().parent.parent / 'helper.sock'
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1.0)
        connection.connect(str(address))
        request = [pathlib.Path(sys.argv[0]).name, *sys.argv[1:]]
        connection.sendall(json.dumps(request).encode() + b'\n')
        with connection.makefile('rb') as stream:
            reply = stream.readline(65537)
        if not reply or len(reply) > 65536: raise ValueError('helper_frame_invalid')
        result = json.loads(reply)
except (OSError, ValueError):
    result = {'ok': False, 'error_code': 'property_helper_unavailable'}
print(json.dumps(result))
'''.encode("utf-8")
_HELPER_MODEL_SOURCE = r'''
"""Private CLI contract model. Its process state exists only in the observer."""
import hashlib, json, socketserver, threading

class ManagedHelperFixture:
    def __init__(self, directory, packages):
        if (not isinstance(packages, list) or not 1 <= len(packages) <= 10
                or any(not isinstance(item, str) or not item for item in packages)):
            raise ValueError('property_helper_input_invalid')
        self.packages = frozenset(packages)
        self.processes = {1: {'package_id': 'metnos.fixture.preexisting',
                             'pid': 1, 'creation_time': 1}}
        self.serial = 1
        self.lock = threading.Lock()
        self.address = directory / 'helper.sock'
        for package in sorted(self.packages):
            if package.startswith('appx:'):
                self.serial += 1
                self.processes[self.serial] = {
                    'package_id': package, 'pid': self.serial, 'creation_time': self.serial}

    def digest(self):
        with self.lock:
            data = json.dumps(list(self.processes.values()), sort_keys=True,
                              separators=(',', ':')).encode()
        return 'sha256:' + hashlib.sha256(data).hexdigest()

    def command(self, argv):
        fail = {'ok': False, 'error_code': 'package_process_identity_mismatch'}
        if (not isinstance(argv, list) or len(argv) < 4 or len(argv) > 150
                or any(not isinstance(item, str) for item in argv)
                or argv[0] not in ('helper', 'package-app')):
            return fail
        transport, operation, *arguments = argv
        options = {}; preexisting = []
        if len(arguments) % 2: return fail
        for key, value in zip(arguments[::2], arguments[1::2]):
            if key == '--preexisting-process': preexisting.append(value)
            elif key in options: return fail
            else: options[key] = value
        package = options.get('--package-id')
        if package not in self.packages: return {'ok': False, 'error_code': 'package_not_registered'}
        appx = package.startswith('appx:')
        if appx != (transport == 'package-app'): return fail
        expected = {
            'query': {'--package-id'},
            'start': {'--package-id', '--lifetime'},
            'stop': {'--package-id', '--pid', '--creation-time'} |
                    ({'--activation-boundary'} if appx else set()),
        }.get(operation)
        if set(options) != expected or (preexisting and (not appx or operation != 'stop')):
            return fail
        with self.lock:
            if operation == 'query':
                return {'ok': True, 'aligned': True, 'lifetimes': ['session']}
            previous = [dict(item) for item in self.processes.values()
                        if item['package_id'] == package]
            if operation == 'start':
                if options['--lifetime'] != 'session':
                    return {'ok': False, 'error_code': 'package_persistence_unsupported'}
                if previous and not appx:
                    return {'ok': True, 'aligned': True, 'payload': {'created_process': False}}
                self.serial += 1
                process = {'package_id': package, 'pid': self.serial, 'creation_time': self.serial}
                self.processes[self.serial] = process
                payload = {'created_process': True, 'process': dict(process),
                           'persistent_registration_changed': False}
                if appx:
                    payload.update(activation_boundary=self.serial,
                                   preexisting_processes=[{'pid': p['pid'], 'creation_time': p['creation_time']}
                                                         for p in previous])
                return {'ok': True, 'aligned': True, 'payload': payload}
            try:
                pid, created = int(options['--pid']), int(options['--creation-time'])
                process = self.processes.get(pid)
                if (not process or process['package_id'] != package
                        or process['creation_time'] != created): return fail
                if appx:
                    others = {f"{p['pid']}:{p['creation_time']}" for p in previous if p['pid'] != pid}
                    if (int(options['--activation-boundary']) != created
                            or set(preexisting) != others or len(preexisting) != len(others)):
                        return fail
            except ValueError:
                return fail
            del self.processes[pid]
            return {'ok': True, 'aligned': True, 'payload': {'restored': True, 'stopped': True}}

    def __enter__(self):
        owner = self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(1.0)
                try:
                    raw = self.rfile.readline(65537)
                    if not raw or len(raw) > 65536: return
                    reply = owner.command(json.loads(raw))
                    self.wfile.write(json.dumps(reply).encode() + b'\n')
                except (OSError, ValueError):
                    return
        self.server = socketserver.UnixStreamServer(str(self.address), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.address.unlink(missing_ok=True)

class SessionBrokerFixture(ManagedHelperFixture):
    """Private session protocol model, never a connection to the live browser."""
    def __init__(self, directory):
        super().__init__(directory, ['fixture'])
        self.sessions = {
            'existing': {'owner': 'host', 'url': 'https://fixture.invalid/existing'},
            'other-owner': {'owner': 'other', 'url': 'https://fixture.invalid/other'}}
        self.allowed = frozenset(('https://fixture.invalid/existing',
                                  'https://fixture.invalid/new',
                                  'https://fixture.invalid/second'))

    def digest(self):
        with self.lock:
            data = json.dumps(self.sessions, sort_keys=True, separators=(',', ':')).encode()
        return 'sha256:' + hashlib.sha256(data).hexdigest()

    def command(self, request):
        fail = {'ok': False, 'error_class': 'fixture_request_invalid'}
        if not isinstance(request, dict) or set(request) != {'operation', 'arguments'}:
            return fail
        args = request['arguments']
        if not isinstance(args, dict) or args.get('owner') != 'host': return fail
        with self.lock:
            if request['operation'] == 'open':
                url = args.get('url')
                if url not in self.allowed or args.get('allowlist'): return fail
                for key, session in self.sessions.items():
                    if session == {'owner': 'host', 'url': url}:
                        return {'ok': True, 'session_id': key, 'url': url, 'reused': True}
                self.serial += 1
                key = 'created-' + str(self.serial)
                self.sessions[key] = {'owner': 'host', 'url': url}
                return {'ok': True, 'session_id': key, 'url': url, 'reused': False}
            if request['operation'] == 'close':
                if args.get('all'):
                    keys = [key for key, session in self.sessions.items()
                            if session['owner'] == args['owner']]
                    for key in keys: del self.sessions[key]
                    return {'ok': True, 'count': len(keys)}
                key = args.get('session_id')
                if not isinstance(key, str): return fail
                session = self.sessions.get(key)
                if session is not None and session['owner'] != args['owner']: return fail
                if session is not None: del self.sessions[key]
                return {'ok': True, 'count': int(session is not None)}
        return fail
'''.encode("utf-8")
_STDIO_SOURCE = r'''
import os, pathlib, runpy, sys
root = pathlib.Path(__file__).resolve().parent
entrypoint = sys.argv[1]
# Only the child imports candidate code and its private, fixed support files.
work = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path.cwd()
os.environ['METNOS_WORKSPACE'] = str(work / 'workspace')
os.environ['METNOS_SHIM_DIR'] = str(root / 'runtime')
sys.path.insert(0, str(root / 'runtime'))
sys.path.insert(1, str(root))
if os.environ.get('METNOS_BIRTH_PROVIDER_FIXTURE') == '1':
    from _metnos_birth_provider_fixture_v1 import configure_bridge
    configure_bridge()
sys.argv = [entrypoint]
runpy.run_path(entrypoint, run_name='__main__')
'''.encode("utf-8")
_HARNESS_SOURCE = r'''
import contextlib, hashlib, json, os, pathlib, socketserver, subprocess, sys, threading
harness_dir = pathlib.Path(__file__).resolve().parent
entrypoint = str(harness_dir.joinpath(*pathlib.PurePosixPath(sys.argv[1]).parts))
work = pathlib.Path.cwd()
request = json.loads(pathlib.Path('request.json').read_text())
fixture = pathlib.Path('fixture')
model = None
def tree_hash():
    if model is not None: return model.digest()
    digest = hashlib.sha256(b'metnos.birth.fixture-tree/v1\0')
    for path in sorted(fixture.rglob('*')):
        relative = path.relative_to(fixture).as_posix().encode()
        digest.update(len(relative).to_bytes(8, 'big')); digest.update(relative)
        if path.is_file():
            payload = path.read_bytes()
            digest.update(b'f'); digest.update(len(payload).to_bytes(8, 'big')); digest.update(payload)
        elif path.is_dir(): digest.update(b'd')
        else: raise RuntimeError('fixture_node_invalid')
    return 'sha256:' + digest.hexdigest()
def file_hash(path):
    return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
def invoke(value, operation='invoke', authorization=None):
    environment = dict(os.environ, METNOS_EXECUTOR_OPERATION=operation)
    if request.get('file_contract'):
        environment.update(METNOS_HISTORY_DIR=str(work / 'history'),
                           METNOS_TURN_ID='birth-round-trip', METNOS_ACTOR='host',
                           METNOS_OWNER_USER_ID='birth-owner', METNOS_CHANNEL='birth',
                           METNOS_DEVICE_ID='birth-fixture')
    if authorization is not None:
        environment['METNOS_FROZEN_PLAN_AUTHORIZATION'] = authorization
    if model is not None:
        environment.update(METNOS_CLIENT_EXE=sys.executable, PYTHONSAFEPATH='1')
    if request.get('provider_contract'):
        environment['METNOS_BIRTH_PROVIDER_FIXTURE'] = '1'
    command = [sys.executable, '-I', str(harness_dir / '@@STDIO_PATH@@'), entrypoint, str(work)]
    if operation == 'reverse' and request.get('reverse_pattern'):
        patterns = request['reverse_pattern']
        selected = value['results'].get('_undo', {}).get('reverse_pattern', patterns)
        allowed = [patterns] if isinstance(patterns, str) else patterns
        selected = [selected] if isinstance(selected, str) else selected
        if not isinstance(selected, list) or not set(selected) <= set(allowed):
            raise RuntimeError('reverse_pattern_outside_contract')
        command = [sys.executable, '-I', str(harness_dir / '@@REVERSE_PATH@@'), json.dumps(selected), str(work)]
    result = subprocess.run(command,
                            input=json.dumps(value).encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=environment, cwd=harness_dir if model is not None else work)
    if result.returncode:
        sys.stderr.buffer.write(result.stderr); raise SystemExit(result.returncode)
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict): raise RuntimeError('candidate_output_invalid')
    if parsed.get('error_code') == 'property_helper_unavailable':
        raise RuntimeError('property_helper_unavailable')
    return parsed
scope = contextlib.nullcontext()
if request.get('provider_contract'):
    sys.path.insert(0, str(harness_dir))
    from _metnos_birth_provider_fixture_v1 import ProviderObjectsFixture
    model = ProviderObjectsFixture(work)
    scope = model
elif request.get('session_contract'):
    sys.path.insert(0, str(harness_dir))
    from _metnos_birth_helper_model_v1 import SessionBrokerFixture
    model = SessionBrokerFixture(work)
    scope = model
elif request.get('helper_contract'):
    # Only this fixed core support module is imported by the observer. The
    # candidate remains exclusively in its separate stdio child.
    sys.path.insert(0, str(harness_dir))
    from _metnos_birth_helper_model_v1 import ManagedHelperFixture
    model = ManagedHelperFixture(work, request['input'].get('programs'))
    scope = model
with scope:
    before = tree_hash(); observations = {}; args = request['input']
    if request.get('file_contract'):
        # These paths are generated by the core, never copied from shell setup.
        def local_paths(value):
            if isinstance(value, str) and value.startswith('fixture/'):
                return str(work / value)
            if isinstance(value, list): return [local_paths(item) for item in value]
            if isinstance(value, dict): return {key: local_paths(item) for key, item in value.items()}
            return value
        args = local_paths(args)
    if request['fixture_id'] == 'private_deletion_tree':
        source = fixture / 'source.bin'; recovery = fixture / 'recovery.bin'
        source_hash = file_hash(source)
        domain = request.get('deletion_contract')
        if domain == 'deleted_files':
            # A file in place of the recovery directory makes backup fail on
            # every platform, including privileged test processes. Observe the
            # source independently; never infer event ordering from a receipt.
            denied = invoke(args)
            blocked_hash = tree_hash()
            history = work / 'history'
            if denied.get('ok') is False and blocked_hash == before and history.is_file():
                history.unlink(); history.mkdir()
                output = invoke(args)
                copies = [path for path in history.rglob('*.bin') if path.is_file()]
                observations.update(
                    backup_failure_preserved_source=True,
                    source_before_hash=source_hash,
                    source_removed=not source.exists(),
                    recovery_copy_hash=file_hash(copies[0]) if len(copies) == 1 else None)
            else:
                output = denied
        elif domain == 'deleted_dirs':
            # Empty directories have no payload to copy. A nonempty directory
            # must be refused by the default reversible operation.
            output = invoke(args)
            observations['nonempty_directory_preserved'] = (
                output.get('ok') is False and tree_hash() == before)
        else:
            output = invoke(args)
            if source.is_file() and recovery.is_file() and file_hash(recovery) == source_hash:
                output = invoke(args)
                if not source.exists() and recovery.is_file() and file_hash(recovery) == source_hash:
                    observations.update(filesystem_events=['copy', 'delete'],
                                        source_before_hash=source_hash,
                                        recovery_copy_hash=file_hash(recovery))
        after = tree_hash()
    elif request.get('frozen_plan'):
        contract = request['frozen_plan']
        observations['frozen_plan_completed'] = False
        args[contract['argument']] = contract['preview_value']
        output = invoke(args)
        token = output.get(contract['token_result'])
        if (output.get('ok') is True and tree_hash() == before
                and isinstance(token, str) and len(token) == 64
                and all(char in '0123456789abcdef' for char in token)):
            args = {key: args[key] for key in contract['carry_arguments'] if key in args}
            args.update({contract['argument']: contract['apply_value'],
                         contract['token_argument']: token})
            output = invoke(args, authorization=token)
            observations['frozen_plan_completed'] = output.get('ok') is True
        after = tree_hash()
    else:
        output = invoke(args); after = tree_hash()
    if request['fixture_id'] == 'private_mutable_state':
        restored = None
        if output.get('ok') is True and output.get('_undo', {}).get('outcome') != 'no_effect':
            reverse = invoke({'plan': {'args': args}, 'results': output}, 'reverse')
            if reverse.get('ok') is True: restored = tree_hash()
        observations.update(state_before_hash=before, state_after_forward_hash=after,
                            state_after_undo_hash=restored)
        if model is not None:
            observations['fixture_contract'] = (
                'provider_objects/v1' if request.get('provider_contract') else
                'session_broker/v1' if request.get('session_contract') else 'managed_helper/v1')
print(json.dumps({'output': output, 'observations': observations},
                 sort_keys=True, separators=(',', ':')))
'''.replace('@@STDIO_PATH@@', _STDIO_PATH).replace('@@REVERSE_PATH@@', _REVERSE_PATH).encode("utf-8")


def _candidate_files_with_support(code_files: Mapping[str, bytes]) -> dict[str, bytes]:
    from executor_birth_functional import _support_files
    from executor_birth_snapshot import CandidateSnapshotError, _portable_relative

    try:
        owned = _support_files()
    except (OSError, ValueError) as exc:
        raise RuntimeError("property_support_unavailable") from exc
    from pathlib import Path
    runtime = Path(__file__).resolve().parent
    owned.update({_HARNESS_PATH: _HARNESS_SOURCE, _STDIO_PATH: _STDIO_SOURCE,
                  _REVERSE_PATH: _REVERSE_SOURCE,
                  "runtime/reverse_patterns.py": (runtime / "reverse_patterns.py").read_bytes(),
                  "runtime/reverse_patterns_patch.py": (runtime / "reverse_patterns_patch.py").read_bytes(),
                  "runtime/playwright_sidecar/__init__.py": b"",
                  "runtime/playwright_sidecar/session_client.py": _SESSION_CLIENT_SOURCE,
                  "runtime/playwright_sidecar/stealth.py": (runtime / "playwright_sidecar/stealth.py").read_bytes(),
                  _HELPER_MODEL_PATH: _HELPER_MODEL_SOURCE,
                  "_metnos_birth_provider_fixture_v1.py":
                      (runtime / "executor_birth_provider_fixture.py").read_bytes(),
                  "helper": _HELPER_CLIENT_SOURCE, "package-app": _HELPER_CLIENT_SOURCE})
    for name in ("backends/_github_bridge.py", "backends/issues/__init__.py",
                 "backends/issues/github.py", "backends/comments/__init__.py",
                 "backends/comments/github.py"):
        owned['runtime/' + name] = (runtime / name).read_bytes()
    reserved = tuple(PurePosixPath(name.casefold()) for name in owned)
    for name in code_files:
        try:
            path = PurePosixPath(_portable_relative(name).casefold())
        except CandidateSnapshotError as exc:
            raise PropertyContractError("property_candidate_invalid", "support_path") from exc
        if any(path == item or path in item.parents or item in path.parents
               for item in reserved):
            raise PropertyContractError("property_candidate_invalid", "reserved_support_path")
    return {**code_files, **owned}


_PUBLIC_UNAVAILABLE_REASONS = frozenset({
    "linux_sandbox_registry_unavailable", "linux_sandbox_program_unavailable",
    "linux_sandbox_program_mismatch", "platform_backend_unavailable",
    "property_support_unavailable",
    "property_case_unavailable",
    "sandbox_setup_unattested", "candidate_process_failed",
    "cgroup_delegate_subgroup_missing", "cgroup_delegate_not_writable",
    "cgroup_scope_unavailable", "phase_timeout", "total_timeout",
})


def _uses_managed_helper(manifest: Mapping[str, object]) -> bool:
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, (list, tuple)):
        return False
    return any(isinstance(cap, Mapping) and cap.get("name") == "system:admin"
               and isinstance(cap.get("hint"), list) and "managed-package-start" in cap["hint"]
               for cap in capabilities)


def _domain_contract(manifest: Mapping[str, object]) -> str:
    """Select fixed fixture semantics from declared contracts, never a tool name."""
    args = manifest.get('args')
    properties = args.get('properties', {}) if isinstance(args, Mapping) else {}
    if not isinstance(properties, Mapping):
        return ''
    caps = manifest.get('capabilities')
    caps = {cap['name'] for cap in caps if isinstance(cap, Mapping)
            and isinstance(cap.get('name'), str)} if isinstance(caps, list) else set()
    def typed(key, kind):
        item = properties.get(key)
        return isinstance(item, Mapping) and item.get('type') == kind
    if 'provider:access' in caps and typed('repo', 'string') and typed('client', 'string'):
        if manifest.get('reverse_pattern') == 'delete_issues_by_id' and typed('title', 'string'):
            return 'created_issues'
        if (manifest.get('reverse_pattern') == 'delete_comments_by_id'
                and typed('targets', 'array') and typed('body', 'string')):
            return 'created_comments'
    if (manifest.get('reverse_pattern') == 'module.reverse'
            and 'network:sites' in caps and typed('urls', 'array')):
        return 'session_broker'
    if (manifest.get('reverse_pattern') == 'delete_created_paths'
            and {'fs:read', 'fs:write'} <= caps
            and typed('paths', 'array') and typed('dest', 'string')):
        return 'created_paths'
    patterns = manifest.get('reverse_pattern')
    reverse = {patterns} if isinstance(patterns, str) else set(patterns or ())
    if 'fs:write' in caps:
        if typed('paths', 'array') and not typed('dest', 'string'):
            if 'delete_created_paths' in reverse:
                return 'created_dirs'
            if 'restore_blob_backup' in reverse:
                return 'deleted_files'
            if 'module.reverse' in reverse and typed('force', 'boolean'):
                return 'deleted_dirs'
        if typed('path', 'string'):
            if 'restore_blob_backup' in reverse and typed('content', 'string'):
                return 'written_files'
            if 'delete_created_paths' in reverse and typed('values', 'array'):
                return 'created_table'
    if ('fs:write' in caps and isinstance(patterns, list)
            and 'swap_src_dst' in patterns
            and typed('entries', 'array') and typed('dst_template', 'string')):
        return 'file_moves'
    execution = manifest.get('execution')
    if (isinstance(execution, Mapping) and isinstance(execution.get('frozen_plan'), Mapping)
            and manifest.get('reverse_pattern') == 'module.reverse'
            and {'fs:read', 'fs:write'} <= caps
            and all(typed(key, 'array') for key in ('source_paths', 'destination_roots', 'operations'))):
        from frozen_plan_consent import validate_frozen_plan
        if not validate_frozen_plan(dict(manifest)):
            return 'frozen_file_plan'
    return ''


class ObservedPropertyRunner:
    """Core-owned adapter bound to the exact private candidate observation.

    The candidate receives a closed JSON request and may emit only its ordinary
    JSON result.  Evidence about fixtures and isolation is reconstructed here;
    candidate fields that resemble attestations are never trusted.
    """

    def __init__(self, observed: object, *,
                 windows_registry: WindowsSandboxRegistry | None = None,
                 linux_registry: LinuxSandboxRegistry | None = None) -> None:
        from executor_birth import ObservedCandidate
        if not isinstance(observed, ObservedCandidate):
            raise PropertyContractError("property_candidate_invalid", "observation")
        self._observed = observed
        self._windows_registry = windows_registry
        self._linux_registry = linux_registry

    def _entrypoint(self) -> str:
        try:
            manifest = tomllib.loads(
                self._observed.snapshot.manifest_bytes.decode("utf-8")
            )
            files = manifest["code"]["files"]
            entrypoint = files[0]
        except (KeyError, IndexError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise PropertyContractError("property_candidate_invalid", "entrypoint") from exc
        if not isinstance(entrypoint, str) or PurePosixPath(entrypoint).is_absolute():
            raise PropertyContractError("property_candidate_invalid", "entrypoint")
        return entrypoint

    def _fixture(self, case: PropertyCase, fixture_id: str) -> tuple[FixtureOp, ...]:
        count = case.input_value.get("fixture_count", case.expectation.get("fixture_total", 0))
        count = count if type(count) is int and 0 <= count <= 16 else 0
        request = {"case_id": case.case_id, "fixture_id": fixture_id,
                   "input": dict(case.input_value), "fixture_root": "fixture"}
        if fixture_id == "private_mutable_state":
            if case.expectation.get("declared_input") is not True:
                raise RuntimeError("property_case_unavailable")
            request["state_path"] = "fixture/state.json"
            manifest = tomllib.loads(self._observed.snapshot.manifest_bytes.decode("utf-8"))
            request["helper_contract"] = _uses_managed_helper(manifest)
            domain = _domain_contract(manifest)
            request['provider_contract'] = domain in ('created_issues', 'created_comments')
            request['session_contract'] = domain == 'session_broker'
            request['file_contract'] = domain in (
                'file_moves', 'frozen_file_plan', 'created_dirs', 'deleted_dirs',
                'deleted_files', 'written_files', 'created_table')
            if domain == 'frozen_file_plan':
                request['frozen_plan'] = dict(manifest['execution']['frozen_plan'])
            if domain in ('created_paths', 'file_moves', 'created_dirs',
                          'deleted_files', 'written_files', 'created_table',
                          'created_issues', 'created_comments'):
                request['reverse_pattern'] = manifest['reverse_pattern']
        if fixture_id == "private_deletion_tree":
            request.update(source_path="fixture/source.bin",
                           recovery_path="fixture/recovery.bin")
            manifest = tomllib.loads(self._observed.snapshot.manifest_bytes.decode('utf-8'))
            domain = _domain_contract(manifest)
            request['deletion_contract'] = domain
            request['file_contract'] = domain in ('deleted_files', 'deleted_dirs')
        ops: list[FixtureOp] = [
            FixtureOp(FixtureOpKind.MKDIR, "fixture"),
            FixtureOp(FixtureOpKind.SEED_JSON, "request.json", request),
        ]
        if count:
            ops.append(FixtureOp(FixtureOpKind.MKDIR, "fixture/entries"))
            for index in range(count):
                ops.append(FixtureOp(FixtureOpKind.SEED_JSON,
                                     f"fixture/entries/{index}.json", {"index": index}))
        if fixture_id == "private_mutable_state":
            ops.append(FixtureOp(FixtureOpKind.SEED_JSON, "fixture/state.json", {"value": "before"}))
            if _domain_contract(manifest) == 'created_paths':
                for index in range(3):
                    ops.append(FixtureOp(FixtureOpKind.WRITE_BYTES,
                                         f"fixture/source-{index}.bin", b"birth-fixture-v1"))
            if request['file_contract']:
                ops.extend(FixtureOp(FixtureOpKind.MKDIR, path)
                           for path in ('history', 'fixture/source', 'fixture/destination'))
                if domain == 'frozen_file_plan':
                    ops.append(FixtureOp(FixtureOpKind.MKDIR, 'fixture/destination/nested'))
                for index in range(3):
                    ops.append(FixtureOp(FixtureOpKind.MKDIR, f'fixture/empty-{index}'))
                    ops.append(FixtureOp(FixtureOpKind.WRITE_BYTES,
                                         f'fixture/source/{index}.txt', f'birth-{index}'.encode()))
                if case.input_value.get('overwrite') is True:
                    ops.append(FixtureOp(FixtureOpKind.WRITE_BYTES,
                                         'fixture/destination/0.txt', b'preexisting-destination'))
        if fixture_id == "private_deletion_tree":
            ops.append(FixtureOp(FixtureOpKind.WRITE_BYTES, "fixture/source.bin", b"birth-fixture-v1"))
            if domain == 'deleted_files':
                ops.append(FixtureOp(FixtureOpKind.WRITE_BYTES, 'history', b'backup-unavailable'))
            elif domain == 'deleted_dirs':
                ops.extend((FixtureOp(FixtureOpKind.MKDIR, 'fixture/nonempty'),
                            FixtureOp(FixtureOpKind.WRITE_BYTES, 'fixture/nonempty/keep.txt', b'keep')))
        return tuple(ops)

    def run(self, case: PropertyCase, *, fixture_id: str, isolation: str) -> PropertyRunResult:
        # A fixed core harness supplies stdin from the immutable request file;
        # neither command nor candidate bytes come from the Birth caller.
        entrypoint = self._entrypoint()
        if sys.platform == "win32":
            command = (_HARNESS_PATH, entrypoint)
        elif sys.platform.startswith("linux"):
            if not isinstance(self._linux_registry, LinuxSandboxRegistry):
                raise RuntimeError("linux_sandbox_registry_unavailable")
            # The caller's venv is not part of the sandbox. Use the registered
            # interpreter; run_birth_phase still verifies its exact digest.
            command = (str(self._linux_registry.interpreter_path), "-I",
                       "candidate/" + _HARNESS_PATH, entrypoint)
        else:
            raise RuntimeError("platform_backend_unavailable")
        candidate_files = _candidate_files_with_support(self._observed.snapshot.code_files)
        result = run_birth_phase(
            command,
            fixture_ops=self._fixture(case, fixture_id),
            candidate_id=self._observed.identities.candidate_id,
            candidate_files=candidate_files,
            windows_registry=self._windows_registry,
            linux_registry=self._linux_registry,
        )
        attestation_hash = _attestation_hash(
            result, candidate_id=self._observed.identities.candidate_id,
            case_id=case.case_id, fixture_id=fixture_id, isolation=isolation,
        )
        if result.status is not RunnerStatus.PASSED:
            raise RuntimeError(result.error_code or "property_runner_unavailable")
        try:
            envelope = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise PropertyContractError("property_runner_result_invalid", "json") from exc
        if (not isinstance(envelope, Mapping) or set(envelope) != {"output", "observations"}
                or not isinstance(envelope["output"], Mapping)
                or not isinstance(envelope["observations"], Mapping)):
            raise PropertyContractError("property_runner_result_invalid", "output")
        output = dict(envelope["output"])
        observations: dict[str, object] = dict(envelope["observations"])
        fixture_total = case.expectation.get("fixture_total")
        if type(fixture_total) is int:
            observations["fixture_total"] = fixture_total
        return PropertyRunResult(output, observations, attestation_hash)


@dataclass(frozen=True, slots=True)
class PropertyCandidateProfile:
    """Core-derived applicability facts; never decoded from a manifest table."""
    output_schema: tuple[tuple[str, str], ...] = ()
    collection_output: bool = False
    limit_input: bool = False
    truncation_declared: bool = False
    revertible: bool = False
    destructive_with_undo: bool = False
    entries_and_results: bool = False
    positive_inputs: tuple[Mapping[str, object], ...] = ()
    helper_contract: bool = False
    domain_contract: str = ""

    def __post_init__(self) -> None:
        if self.domain_contract not in ('', 'created_paths', 'session_broker',
                                        'file_moves', 'frozen_file_plan', 'created_dirs',
                                        'deleted_dirs', 'deleted_files', 'written_files',
                                        'created_table', 'created_issues', 'created_comments'):
            raise PropertyContractError('property_candidate_invalid', 'domain_contract')
        allowed_types = {"array", "boolean", "integer", "null", "number", "object", "string"}
        keys: set[str] = set()
        for item in self.output_schema:
            if (
                not isinstance(item, tuple) or len(item) != 2
                or not isinstance(item[0], str) or not item[0]
                or item[0] in keys or not isinstance(item[1], str)
                or item[1] not in allowed_types
            ):
                raise PropertyContractError("property_candidate_invalid", "output_schema")
            keys.add(item[0])
        for name in (
            "collection_output", "limit_input", "truncation_declared", "revertible",
            "destructive_with_undo", "entries_and_results", "helper_contract",
        ):
            if type(getattr(self, name)) is not bool:
                raise PropertyContractError("property_candidate_invalid", name)
        if (not isinstance(self.positive_inputs, tuple)
                or any(not isinstance(item, Mapping) for item in self.positive_inputs)):
            raise PropertyContractError("property_candidate_invalid", "positive_inputs")


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(encode_framed_v1(value)).hexdigest()


def _collection_cases(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    return tuple(PropertyCase(f"cardinality.{count}", {"fixture_count": count}, {"count": count}) for count in (0, 1, 3))


def _limit_cases(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    return (PropertyCase("limit.0", {"fixture_count": 3, "limit": 0}, {"max_count": 0}),
            PropertyCase("limit.below_total", {"fixture_count": 3, "limit": 2}, {"max_count": 2}))


def _single(case_id: str):
    def generate(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
        return (PropertyCase(case_id, {}, {}),)
    return generate


def _truncation_cases(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    expectation = {"fixture_total": 3, "limit": 2}
    return (PropertyCase("truncation.boundary", expectation, expectation),)


def _undo_cases(candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    domain = candidate.domain_contract
    if domain == 'created_issues':
        return (PropertyCase('undo.created_issues',
                    {'repo': 'birth/fixture', 'title': 'Birth fixture', 'client': 'github'},
                    {'declared_input': True}),)
    if domain == 'created_comments':
        return tuple(PropertyCase(f'undo.created_comments.{count}',
                         {'repo': 'birth/fixture', 'body': 'Birth fixture', 'client': 'github',
                          'targets': ['issue:7', 'pr:8', 'issue:9'][:count]},
                         {'declared_input': True}) for count in (1, 3))
    if domain in ('created_dirs', 'deleted_dirs', 'deleted_files'):
        template = {'created_dirs': 'fixture/destination/new-{index}',
                    'deleted_dirs': 'fixture/empty-{index}',
                    'deleted_files': 'fixture/source/{index}.txt'}[domain]
        return tuple(PropertyCase(f'undo.{domain}.{count}',
                         {'paths': [template.format(index=index) for index in range(count)]},
                         {'declared_input': True}) for count in (1, 3))
    if domain == 'written_files':
        return tuple(PropertyCase(f'undo.write.{index}', value, {'declared_input': True})
                     for index, value in enumerate((
                         {'path': 'fixture/destination/new.txt', 'content': 'new'},
                         {'path': 'fixture/source/0.txt', 'content': 'replace'},
                         {'path': 'fixture/source/0.txt', 'content': 'append', 'mode': 'append'})))
    if domain == 'created_table':
        return tuple(PropertyCase(f'undo.table.{suffix}',
                         {'path': f'fixture/destination/table.{suffix}',
                          'values': [['heading', 'value'], ['row', 2]]},
                         {'declared_input': True}) for suffix in ('xlsx', 'csv'))
    if candidate.domain_contract == 'file_moves':
        return tuple(PropertyCase(f'undo.file_moves.{count}',
                         {'entries': [{'path': f'fixture/source/{index}.txt'} for index in range(count)],
                          'dst_template': 'fixture/destination/{name}', 'overwrite': count == 3},
                         {'declared_input': True}) for count in (1, 3))
    if candidate.domain_contract == 'frozen_file_plan':
        return (PropertyCase('undo.frozen_file_plan',
                    {'source_paths': ['fixture/source'],
                     'destination_roots': ['fixture/destination'],
                     'operations': [{'type': 'move', 'destination_root': 'fixture/destination',
                                     'path_template': 'nested/{name}',
                                     'on_missing': 'fail', 'on_conflict': 'fail'}]},
                    {'declared_input': True}),)
    if candidate.domain_contract == 'session_broker':
        return tuple(PropertyCase(f'undo.session_contract.{index}', {'urls': urls}, {'declared_input': True})
                     for index, urls in enumerate((
                         ['https://fixture.invalid/new'],
                         ['https://fixture.invalid/existing', 'https://fixture.invalid/new',
                          'https://fixture.invalid/second'])))
    if candidate.domain_contract == 'created_paths':
        # Existing successful cases supply formats, not host fixtures or shell commands.
        formats = []
        for value in candidate.positive_inputs:
            paths, dest = value.get('paths'), value.get('dest')
            if not isinstance(paths, list) or not paths or not isinstance(dest, str):
                continue
            suffix = ''.join(PurePosixPath(dest).suffixes)
            fmt = value.get('format')
            if fmt is not None and not isinstance(fmt, str): continue
            item = (suffix, fmt)
            if item not in formats: formats.append(item)
        if not formats:
            return _single('undo.round_trip')(candidate)
        return tuple(PropertyCase(f'undo.created_paths.{index}',
                         {'paths': ['fixture/source-0.bin'],
                          'dest': f'fixture/new/nested/output{suffix}',
                          **({'format': fmt} if fmt is not None else {})},
                         {'declared_input': True})
                     for index, (suffix, fmt) in enumerate(formats))
    if not candidate.positive_inputs:
        return _single("undo.round_trip")(candidate)
    prefix = "undo.helper_contract" if candidate.helper_contract else "undo.round_trip"
    return tuple(PropertyCase(f"{prefix}.{index}", dict(value), {"declared_input": True})
                 for index, value in enumerate(candidate.positive_inputs))


def _delete_cases(candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    paths = {'deleted_files': 'fixture/source.bin', 'deleted_dirs': 'fixture/nonempty'}
    path = paths.get(candidate.domain_contract)
    return (PropertyCase('delete.copy_before', {'paths': [path]} if path else {}, {}),)


_GENERATORS = {
    "declared_output_cases": _single("output.actual"),
    "cardinality_cases": _collection_cases,
    "limit_boundary_cases": _limit_cases,
    "truncation_cases": _truncation_cases,
    "undo_round_trip_cases": _undo_cases,
    "delete_copy_cases": _delete_cases,
    "entries_results_cases": _single("entries.results"),
}


def _output_schema(output, candidate, _expect, _observations):
    def matches(value: object, type_name: str) -> bool:
        return {
            "array": lambda: isinstance(value, list),
            "boolean": lambda: type(value) is bool,
            "integer": lambda: type(value) is int,
            "null": lambda: value is None,
            "number": lambda: type(value) is int or (
                type(value) is float and math.isfinite(value)
            ),
            "object": lambda: isinstance(value, Mapping),
            "string": lambda: isinstance(value, str),
        }[type_name]()

    return bool(candidate.output_schema) and all(
        key in output and matches(output[key], type_name)
        for key, type_name in candidate.output_schema
    )


def _cardinality(output, _candidate, expect, _observations):
    entries = output.get("entries", output.get("results"))
    return isinstance(entries, list) and len(entries) == expect["count"]


def _limit(output, _candidate, expect, _observations):
    entries = output.get("entries", output.get("results"))
    return isinstance(entries, list) and len(entries) <= expect["max_count"]


def _truncation(output, _candidate, expect, observations):
    entries = output.get("entries", output.get("results"))
    return (
        isinstance(entries, list)
        and len(entries) == expect["limit"]
        and output.get("truncated") is True
        and observations.get("fixture_total") == expect["fixture_total"]
    )


def _state_round_trip(_output, _candidate, _expect, observations):
    before = observations.get("state_before_hash")
    mutated = observations.get("state_after_forward_hash")
    restored = observations.get("state_after_undo_hash")
    return (
        isinstance(before, str) and _DIGEST_RE.fullmatch(before) is not None
        and isinstance(mutated, str) and _DIGEST_RE.fullmatch(mutated) is not None
        and isinstance(restored, str) and _DIGEST_RE.fullmatch(restored) is not None
        and restored == before and mutated != before
        and observations.get('frozen_plan_completed', True) is True
    )


def _copy_precedes_delete(output, candidate, _expect, observations):
    if candidate.domain_contract == 'deleted_dirs':
        return observations.get('nonempty_directory_preserved') is True
    if candidate.domain_contract == 'deleted_files':
        source = observations.get('source_before_hash')
        return (output.get('ok') is True
                and observations.get('backup_failure_preserved_source') is True
                and observations.get('source_removed') is True
                and isinstance(source, str) and _DIGEST_RE.fullmatch(source) is not None
                and observations.get('recovery_copy_hash') == source)
    events = observations.get("filesystem_events")
    source = observations.get("source_before_hash")
    recovery = observations.get("recovery_copy_hash")
    return (
        isinstance(events, list)
        and events.count("copy") == 1
        and events.count("delete") == 1
        and events.index("copy") < events.index("delete")
        and isinstance(source, str) and _DIGEST_RE.fullmatch(source) is not None
        and isinstance(recovery, str) and _DIGEST_RE.fullmatch(recovery) is not None
        and recovery == source
    )


def _coherent(output, _candidate, _expect, _observations):
    return isinstance(output.get("entries"), list) and output.get("entries") == output.get("results")


_ORACLES = {
    "output_schema": _output_schema,
    "cardinality": _cardinality,
    "limit_semantics": _limit,
    "truncation": _truncation,
    "state_round_trip": _state_round_trip,
    "copy_precedes_delete": _copy_precedes_delete,
    "entries_results_coherence": _coherent,
}

_FIXTURES = frozenset({
    "empty_private_root", "bounded_collection", "oversized_collection",
    "private_mutable_state", "private_deletion_tree",
})
_APPLICABILITY = {
    "output_schema_declared": lambda c: bool(c.output_schema),
    "collection_output": lambda c: c.collection_output,
    # A field named ``limit`` does not by itself establish collection
    # semantics.  The oracle can prove a bound only when the same
    # machine-readable profile also declares an entries/results collection.
    "bounded_collection_input": lambda c: c.limit_input and c.collection_output,
    "truncation_declared": lambda c: c.truncation_declared,
    "revertible_executor": lambda c: c.revertible,
    "destructive_with_undo": lambda c: c.destructive_with_undo,
    "entries_and_results_output": lambda c: c.entries_and_results,
}

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")

# The four registries above are the implementation; the table is the closed set
# the admission context speaks about.  Comparing them here, at import, is what
# stops the identity and the reachable set from drifting apart in silence.
for _kind, _names in (
    ("applicability", _APPLICABILITY),
    ("fixture", _FIXTURES),
    ("generator", _GENERATORS),
    ("oracle", _ORACLES),
):
    check_registry_v1(_kind, _names)
del _kind, _names


def _resolve(spec: PropertySpec):
    """Resolve one property against the closed table, then the registries."""
    try:
        check_primitive_v1("applicability", spec.applicability_id)
        check_primitive_v1("generator", spec.generator_id)
        check_primitive_v1("oracle", spec.oracle_id)
        return (_APPLICABILITY[spec.applicability_id], _GENERATORS[spec.generator_id],
                _ORACLES[spec.oracle_id])
    except PrimitiveTableError as exc:
        raise PropertyContractError("property_registry_invalid", exc.detail) from exc
    except KeyError as exc:  # closed core configuration failure
        raise PropertyContractError("property_registry_invalid", str(exc)) from exc


def run_property(
    property_id: str,
    candidate: PropertyCandidateProfile,
    *,
    _runner: PropertyRunner,
) -> tuple[PropertyEvidence, ...]:
    """Run a core property.  `_runner` is a Birth-owned/test seam, not manifest data."""
    if not isinstance(candidate, PropertyCandidateProfile):
        raise PropertyContractError("property_candidate_invalid", "profile")
    try:
        spec = PROPERTY_CATALOG_V1[property_id]
    except KeyError as exc:
        raise PropertyContractError("property_unknown", property_id) from exc
    applicable, generate, oracle = _resolve(spec)
    try:
        check_primitive_v1("fixture", spec.fixture_id)
    except PrimitiveTableError as exc:
        raise PropertyContractError("property_registry_invalid", exc.detail) from exc
    if not applicable(candidate):
        return ()
    cases = generate(candidate)
    if not cases or len(cases) > spec.max_cases:
        raise PropertyContractError("property_cases_invalid", property_id)
    evidence = []
    for case in cases:
        try:
            result = _runner.run(case, fixture_id=spec.fixture_id, isolation=spec.isolation.value)
        except PropertyContractError:
            raise
        except Exception as exc:  # runner unavailability is fail-closed evidence
            status = PropertyStatus.UNAVAILABLE
            reason = exc.args[0] if len(exc.args) == 1 else None
            error = (reason if isinstance(reason, str) and reason in _PUBLIC_UNAVAILABLE_REASONS
                     else "property_runner_unavailable")
            output_hash = _hash({"unavailable": type(exc).__name__})
            attestation_hash = _hash({"attestation": "unavailable"})
        else:
            if not isinstance(result, PropertyRunResult):
                raise PropertyContractError("property_runner_result_invalid")
            if not isinstance(result.output, Mapping) or not isinstance(result.observations, Mapping):
                raise PropertyContractError("property_runner_result_invalid", "mappings")
            if not isinstance(result.runner_attestation_hash, str) or _DIGEST_RE.fullmatch(result.runner_attestation_hash) is None:
                raise PropertyContractError("property_runner_result_invalid", "attestation")
            passed = oracle(
                result.output, candidate, case.expectation, result.observations,
            )
            status = PropertyStatus.PASSED if passed else PropertyStatus.FAILED
            error = "" if passed else "property_oracle_failed"
            output_hash = _hash({
                "output": dict(result.output),
                "trusted_observations": dict(result.observations),
            })
            attestation_hash = result.runner_attestation_hash
        evidence.append(PropertyEvidence(
            property_id=spec.property_id, property_version=spec.version,
            case_id=case.case_id, status=status,
            input_hash=_hash(dict(case.input_value)), output_hash=output_hash,
            oracle_hash=_hash({"oracle_id": spec.oracle_id, "version": spec.version}),
            runner_attestation_hash=attestation_hash, error_code=error,
        ))
    return tuple(evidence)


def run_applicable_properties(
    candidate: PropertyCandidateProfile, *, _runner: PropertyRunner,
) -> tuple[PropertyEvidence, ...]:
    return tuple(
        evidence
        for property_id in PROPERTY_CATALOG_V1
        for evidence in run_property(property_id, candidate, _runner=_runner)
    )
