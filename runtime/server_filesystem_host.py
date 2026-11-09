"""Local file access as the installation owner, with live server authorization.

The service keeps the account database and decides every permission. This small
ordinary-user process provides only the same closed operations as the device
broker. It cannot run executor code or commands, and does not read account DBs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import socket
import stat
import time

import config as C
import device_paths
import filesystem_io as io
import filesystem_permissions as permissions
import server_filesystem_protocol as protocol

CONFIG = Path('/etc/metnos/filesystem-host.json')
_FIELDS = {'version', 'owner_uid', 'owner_gid', 'service_uid', 'socket', 'state_dir'}
_GRANT_FIELDS = {'path', 'kind', 'can_read', 'can_write', 'recursive', 'can_overwrite', 'path_style'}


def configuration(path=CONFIG):
    """Only an administrator-installed, immutable binding selects a host UID."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise permissions.PermissionError('ERR_FS_HOST_UNAVAILABLE') from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or info.st_size > 4096 or info.st_nlink != 1):
            raise ValueError('untrusted binding')
        for parent in Path(path).parents:
            metadata = parent.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise ValueError('untrusted binding directory')
        with os.fdopen(os.dup(fd), encoding='utf-8') as source:
            value = json.load(source)
        if (not isinstance(value, dict) or set(value) != _FIELDS
                or type(value['version']) is not int or value['version'] != 1):
            raise ValueError('binding schema')
        if any(type(value[k]) is not int or value[k] <= 0 for k in ('owner_uid', 'owner_gid', 'service_uid')):
            raise ValueError('binding identity')
        if value['owner_uid'] == value['service_uid']:
            raise ValueError('binding must be ordinary owner')
        if value['socket'] != '/run/metnos-filesystem-host/control.sock' or value['state_dir'] != '/var/lib/metnos-filesystem-host':
            raise ValueError('binding paths')
        return value
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise permissions.PermissionError('ERR_FS_HOST_UNAVAILABLE') from exc
    finally:
        os.close(fd)


def _workspace(path):
    root = str(C.PATH_WORKSPACE).rstrip('/')
    return path == root or path.startswith(root + '/')


def _boundary(path, action, **kwargs):
    permissions.normalize_path(path, 'posix')
    io.reserved(path)


def _probe(grant):
    """Open metadata/descriptors only: no contents, creation or chmod."""
    path = grant['path']
    io.reserved(path)
    try:
        with io.parent(path) as (parent, name):
            if grant['kind'] == 'folder':
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    mask = os.X_OK | (os.R_OK if grant['can_read'] else 0) | (os.W_OK if grant['can_write'] else 0)
                    if not os.access('.', mask, dir_fd=fd, effective_ids=True):
                        raise OSError('folder inaccessible')
                finally:
                    os.close(fd)
            else:
                try:
                    fd = io.open_file(parent, name)
                except FileNotFoundError:
                    if grant['can_read'] or not grant['can_write']:
                        raise
                else:
                    os.close(fd)
                # Atomic writes replace a directory entry; existing data is
                # opened above for the undo backup even for write-only grants.
                if grant['can_write'] and not os.access('.', os.W_OK | os.X_OK, dir_fd=parent, effective_ids=True):
                    raise OSError('parent inaccessible')
    except OSError as exc:
        raise permissions.PermissionError('ERR_FS_ACCESS_UNAVAILABLE') from exc


def _exchange(binding, request, authority, *, timeout_s=30, source_fd=None, receive_fd=False):
    deadline = time.monotonic() + min(timeout_s, 30)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(min(_remaining(deadline), 2))
            connection.connect(binding['socket'])
            return protocol.client_exchange(connection, request, authority,
                expected_peer_uid=binding['owner_uid'], timeout_s=_remaining(deadline),
                source_fd=source_fd, receive_fd=receive_fd)
    except OSError as exc:
        raise permissions.PermissionError('ERR_FS_HOST_UNAVAILABLE') from exc


def probe_grant(owner, grant):
    grant = {key: grant[key] for key in _GRANT_FIELDS}
    request = {'action': 'probe', 'owner': owner, 'grant': grant}
    protocol.validate_request(request)
    io.reserved(grant['path'])
    for field, action in (('can_read', 'read'), ('can_write', 'write')):
        if grant[field] and permissions.server_excluded(owner, grant['path'], action):
            raise permissions.PermissionError('ERR_FS_PERMISSION_REQUIRED')
    if _workspace(grant['path']):
        _probe(grant)
        return
    binding = configuration()
    if binding is None:
        _probe(grant)
        return
    result = _exchange(binding, request, None, timeout_s=2)
    if result != {'ok': True}:
        raise permissions.PermissionError('ERR_FS_ACCESS_UNAVAILABLE')


def _owner_home():
    """The OS account's own home: the unit points HOME at private state."""
    return Path(pwd.getpwuid(os.geteuid()).pw_dir)


def known_folders():
    """The installation owner's named folders, as a paired client reports its own."""
    binding = configuration()
    if binding is None:
        return device_paths.local_known_folders(_owner_home())
    reply = _exchange(binding, {'action': 'known_folders'}, None, timeout_s=2)
    folders = reply.get('known_folders') if isinstance(reply, dict) else None
    if not isinstance(folders, dict):
        return {}
    checked = {}
    for name, path in folders.items():
        try:
            if name in device_paths._XDG_KEYS and permissions.normalize_path(path, 'posix') == path:
                checked[name] = path
        except permissions.PermissionError:
            continue
    return checked


def anchor_named_folders(args):
    """«Documenti» on the server becomes the owner's real folder; nothing is granted.

    The broker then checks the account's grants on that exact path. A folder
    of that name already in the Metnos workspace keeps the workspace meaning.
    """
    if not isinstance(args, dict) or not device_paths.has_named_folder(args):
        return args
    try:
        folders = known_folders()
    except permissions.PermissionError:
        return args
    from path_alias import workspace_default
    return device_paths.anchor_folders(
        args, folders, keep=lambda name: (workspace_default() / name).exists())


def source_size(owner, manifest, args, path):
    """Size of a server file outside the service's reach, read by the
    owner's host under the owner's current grant; None when unknown."""
    import server_filesystem as server
    if _workspace(path):
        return None
    binding = configuration()
    if binding is None:
        return None
    request = {'action': 'size', 'owner': owner, 'path': path}
    reply = _exchange(binding, request, server.LocalAuthority(owner, manifest, args), timeout_s=5)
    size = reply.get('size') if reply.get('ok') is True else None
    return size if type(size) is int and size >= 0 else None


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise permissions.PermissionError('ERR_FS_REQUEST_EXPIRED')
    return remaining


def _single_args(operation, args, spec):
    if operation == 'write':
        return {**args, 'files': [spec]}
    if operation == 'copy':
        return {**args, 'entries': [{'path': spec['src'], 'destination': spec['dst']}], 'dst_template': '{destination}',
                'source_location': 'server', 'destination_location': 'server'}
    return {**args, 'paths': [spec]}


def execute(owner, manifest, args, turn_id, *, timeout_s=30, db_path=None):
    import server_filesystem as server
    deadline = time.monotonic() + min(timeout_s, 30)
    operation = permissions.contract(manifest, args)
    requested = server.requests(operation, args)
    if all(_workspace(path) for path, _ in requested):
        return None
    binding = configuration()
    if binding is None:
        return None
    authority = server.LocalAuthority(owner, manifest, args, db_path=db_path)
    broker = server.Broker(owner, manifest, args, turn_id, timeout_s=_remaining(deadline), authority=authority)
    envelope = {'action': 'execute', 'owner': owner, 'manifest': manifest, 'args': args, 'turn_id': turn_id}
    if operation == 'list':
        broker.check(requested[0][0], 'read', directory=True)
        return _exchange(binding, envelope, authority, timeout_s=_remaining(deadline))
    specs = (server.write_specs(args) if operation == 'write' else
             server.copy_specs(args) if operation == 'copy' else server.paths(args))
    if len(specs) > 100:
        raise permissions.PermissionError('ERR_FS_SIZE_LIMIT')
    results, failed = [], []
    for index, spec in enumerate(specs):
        source_fd = None
        try:
            item_args = _single_args(operation, args, spec)
            # Check every path before opening it under either Linux identity.
            for path, action in server.requests(operation, item_args):
                broker.check(path, action)
            target = spec['dst'] if operation == 'copy' else spec['path'] if operation == 'write' else spec
            native = _workspace(target)
            message = {**envelope, 'args': item_args}
            if operation == 'copy' and _workspace(spec['src']) != native:
                if native:
                    opened, source_fd = _exchange(binding, {**message, 'action': 'open_source'},
                        authority, timeout_s=_remaining(deadline), receive_fd=True)
                    if opened != {'ok': True} or source_fd is None:
                        raise permissions.PermissionError('ERR_FS_AUTHORITY_INVALID')
                else:
                    with io.parent(spec['src']) as (parent, name):
                        source_fd = io.open_file(parent, name)
            if native:
                entry = (broker.copy(spec, source_fd=source_fd) if operation == 'copy'
                         else getattr(broker, operation)(spec))
            else:
                reply = _exchange(binding, message, authority, timeout_s=_remaining(deadline), source_fd=source_fd)
                entries = reply.get('entries' if operation == 'read' else 'results', [])
                if not reply.get('ok') or len(entries) != 1:
                    code = reply.get('error_code', 'ERR_FS_IO')
                    raise permissions.PermissionError(code if code in permissions.ERROR_CODES else 'ERR_FS_IO')
                entry = entries[0]
            if operation in {'copy', 'write', 'delete'}:
                entry['_filesystem_backend'] = 'native' if native else 'host'
            results.append(entry)
        except (OSError, ValueError, TypeError, LookupError) as exc:
            error = server.error_result(exc)
            failed.append({'index': index, 'error': error['error'], 'error_code': error['error_code']})
        finally:
            if source_fd is not None:
                os.close(source_fd)
    result = server.batch_result(owner, manifest, args, turn_id, operation, results, failed)
    if '_filesystem' in result:
        backends = {entry['_filesystem_backend'] for entry in results}
        result['_filesystem']['backend'] = next(iter(backends)) if len(backends) == 1 else 'mixed'
    return result


def undo(pattern, results, *, actor=None, db_path=None, timeout_s=30):
    context = results['_filesystem']
    if context.get('backend') not in {'host', 'mixed'}:
        return None
    deadline = time.monotonic() + min(timeout_s, 30)
    import devices
    import server_filesystem as server
    owner = devices.owner_id_for_actor(actor or os.environ.get('METNOS_ACTOR') or 'host')
    if owner != context['owner']:
        raise permissions.PermissionError('ERR_FS_PERMISSION_REQUIRED')
    authority = server.LocalAuthority(owner, context['manifest'], context['args'], db_path=db_path)
    done, failed = [], []
    binding = None
    for index, entry in enumerate(results.get('results') or []):
        try:
            backend = entry.get('_filesystem_backend', context['backend'])
            if backend not in {'native', 'host'}:
                raise permissions.PermissionError('ERR_FS_AUTHORITY_INVALID')
            item = {**results, 'results': [entry], '_filesystem': {**context, 'backend': backend}}
            if backend == 'host':
                if binding is None:
                    binding = configuration()
                if binding is None:
                    raise permissions.PermissionError('ERR_FS_HOST_UNAVAILABLE')
                reply = _exchange(binding, {'action': 'undo', 'owner': owner, 'pattern': pattern,
                    'results': item}, authority, timeout_s=_remaining(deadline))
            else:
                reply = server.undo_stage(pattern, item, authority=authority, trusted_owner=owner,
                                          timeout_s=_remaining(deadline))
            done.extend(reply.get('results') or [])
            if not reply.get('ok'):
                errors = reply.get('failed') or [reply]
                failed.extend({**error, 'index': index} for error in errors)
        except (OSError, ValueError, TypeError, LookupError) as exc:
            failed.append({'index': index, **server.error_result(exc)})
    return {'ok': not failed, 'ok_count': len(done), 'fail_count': len(failed), 'results': done, 'failed': failed}


def handle(request, authority, source_fd=None):
    import server_filesystem as server
    if request['action'] == 'probe':
        _probe(request['grant'])
        return {'ok': True}
    if request['action'] == 'known_folders':
        return {'ok': True, 'known_folders': device_paths.local_known_folders(_owner_home())}
    if request['action'] == 'size':
        path = request['path']
        authority.check(path, 'read')
        with io.parent(path) as (parent, name):
            source = io.open_file(parent, name)
        try:
            size = io.regular(source).st_size
            authority.check(path, 'read')
            return {'ok': True, 'size': size}
        finally:
            os.close(source)
    if request['action'] == 'transfer':
        import filesystem_transfer_io
        return filesystem_transfer_io.execute(request['transfer'], request['turn_id'], authority)
    if request['action'] == 'execute':
        return server.execute(request['owner'], request['manifest'], request['args'],
            request['turn_id'], authority=authority, source_fd=source_fd)
    if request['action'] == 'open_source':
        if permissions.contract(request['manifest'], request['args']) != 'copy':
            raise permissions.PermissionError('ERR_FS_AUTHORITY_INVALID')
        specs = server.copy_specs(request['args'])
        if len(specs) != 1:
            raise permissions.PermissionError('ERR_FS_AUTHORITY_INVALID')
        path = specs[0]['src']
        broker = server.Broker(request['owner'], request['manifest'], request['args'],
                               request['turn_id'], authority=authority)
        broker.check(path, 'read')
        with io.parent(path) as (parent, name):
            source = io.open_file(parent, name)
        try:
            broker.check(path, 'read')
            return {'ok': True}, source
        except BaseException:
            os.close(source)
            raise
    return server.undo_stage(request['pattern'], request['results'],
        authority=authority, trusted_owner=request['owner'])


def serve(binding):
    if os.geteuid() != binding['owner_uid'] or os.getegid() != binding['owner_gid']:
        raise permissions.PermissionError('ERR_FS_HOST_UNAVAILABLE')
    workspace = permissions.normalize_path(os.environ['METNOS_SERVER_WORKSPACE'], 'posix')
    C.PATH_WORKSPACE = Path(workspace)
    state = Path(binding['state_dir'])
    metadata = state.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise permissions.PermissionError('ERR_FS_HOST_UNAVAILABLE')
    C.PATH_USER_DATA = state / 'data'
    C.PATH_USER_STATE = state
    C.PATH_USER_CONFIG = state / 'config'
    C.PATH_USER_CACHE = state / 'cache'
    # The listener stays serialized: each request has a bounded deadline and
    # one current authorization transaction. No shell or worker subprocesses.
    location = Path(binding['socket'])
    if location.exists():
        info = location.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
            raise permissions.PermissionError('ERR_FS_HOST_UNAVAILABLE')
        location.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(location))
        os.chmod(location, 0o666)
        listener.listen(8)
        while True:
            connection, _ = listener.accept()
            with connection:
                try:
                    protocol.serve_connection(connection, handle,
                        check_boundary=lambda request, path, action, kwargs: _boundary(path, action, **kwargs),
                        expected_peer_uid=binding['service_uid'])
                except (OSError, ValueError, TypeError, LookupError):
                    # The transport returns only closed errors, without paths
                    # or private content in journals.
                    continue


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true', required=True)
    parser.add_argument('--config', type=Path, default=CONFIG)
    options = parser.parse_args()
    binding = configuration(options.config)
    if binding is None:
        raise SystemExit('filesystem host binding unavailable')
    serve(binding)
