"""Maintain the ordinary-user filesystem host after authenticated cutover.

The caller supplies the runtime and Python selected by the verified distribution.
This independent host unit has no edges into the signed service topology. Its
administrative binding preserves the installer's OS identity across releases.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile

from .managed_infrastructure import _quote
from runtime.executor_birth_account_identity import (
    PosixAccountRecordV1, metnos_xdg_layout_v1,
)

CONFIG = Path('/etc/metnos/filesystem-host.json')
UNIT = 'metnos-filesystem-host.service'
STATE = '/var/lib/metnos-filesystem-host'
SOCKET = '/run/metnos-filesystem-host/control.sock'
_MARKER = '# Managed by Metnos filesystem host: '
_FIELDS = {'version', 'owner_uid', 'owner_gid', 'service_uid', 'socket', 'state_dir'}


def installer_identity() -> tuple[int, int]:
    """Capture the invoking OS account, never infer it from an app account."""
    uid = os.getuid()
    if os.geteuid() == 0:
        value = os.environ.get('SUDO_UID', '')
        if not value.isascii() or not value.isdigit():
            raise ValueError('filesystem host requires the ordinary installer identity')
        uid = int(value)
    account = pwd.getpwuid(uid)
    if account.pw_uid <= 0 or account.pw_gid <= 0:
        raise ValueError('filesystem host cannot use root')
    return account.pw_uid, account.pw_gid


def _path(value) -> Path:
    raw = os.fspath(value)
    path = Path(raw)
    if (not path.is_absolute() or str(path) != raw or '..' in path.parts
            or any(ord(c) < 32 or ord(c) == 127 for c in raw)):
        raise ValueError('invalid filesystem host path')
    return path


def _directory(path: Path, *, create=False) -> None:
    for directory in (*reversed(path.parents), path):
        if create:
            directory.mkdir(mode=0o755, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('untrusted filesystem host directory')


def _read(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > 16384):
            raise ValueError('untrusted filesystem host file')
        with os.fdopen(os.dup(fd), encoding='utf-8') as stream:
            text = stream.read(16385)
        if len(text) > 16384:
            raise ValueError('filesystem host file size')
        return text
    finally:
        os.close(fd)


def _runtime(runtime_root, python) -> tuple[Path, Path]:
    root, executable = _path(runtime_root), _path(python)
    for artifact in (root / 'runtime/server_filesystem_host.py', executable):
        _directory(artifact.parent)
        info = artifact.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('filesystem host requires protected installed code')
    return root, executable


def _binding(previous, service_uid, owner_uid, owner_gid) -> dict:
    if previous is None:
        value = dict(version=1, owner_uid=owner_uid, owner_gid=owner_gid,
                     service_uid=service_uid, socket=SOCKET, state_dir=STATE)
    else:
        value = json.loads(previous)
    if (not isinstance(value, dict) or set(value) != _FIELDS
            or type(value['version']) is not int or value['version'] != 1
            or value['socket'] != SOCKET or value['state_dir'] != STATE):
        raise ValueError('filesystem host binding schema')
    if (any(type(value[k]) is not int or value[k] <= 0
            for k in ('owner_uid', 'owner_gid', 'service_uid'))
            or value['owner_uid'] == value['service_uid']):
        raise ValueError('filesystem host requires distinct ordinary identities')
    if type(service_uid) is not int or value['service_uid'] != service_uid:
        raise ValueError('filesystem host service identity changed')
    account = pwd.getpwuid(value['owner_uid'])
    if account.pw_gid != value['owner_gid']:
        raise ValueError('filesystem host owner identity changed')
    pwd.getpwuid(service_uid)
    return value


def _unit(binding: dict, root: Path, python: Path, config_path: Path) -> str:
    metadata = json.dumps([str(root), str(python)], separators=(',', ':'))
    entry = pwd.getpwuid(binding['service_uid'])
    account = PosixAccountRecordV1(entry.pw_name, entry.pw_uid, entry.pw_gid,
                                  entry.pw_dir, entry.pw_shell)
    environment = {
        'HOME': STATE, 'METNOS_INSTALL_ROOT': str(root),
        'METNOS_WORKSPACE': STATE + '/workspace',
        'METNOS_SERVER_WORKSPACE': str(_path(metnos_xdg_layout_v1(account).workspace)),
        'METNOS_USER_DATA': STATE + '/data', 'METNOS_USER_STATE': STATE,
        'METNOS_USER_CONFIG': STATE + '/config', 'METNOS_USER_CACHE': STATE + '/cache',
    }
    command = [str(python), '-E', '-s', '-B', str(root / 'runtime/server_filesystem_host.py'),
               '--serve', '--config', str(config_path)]
    return (_MARKER + metadata + '\n[Unit]\nDescription=Metnos local filesystem access\n'
            'After=local-fs.target\n\n[Service]\nType=exec\n'
            f"User={binding['owner_uid']}\nGroup={binding['owner_gid']}\n"
            'NoNewPrivileges=yes\nCapabilityBoundingSet=\nUMask=0077\n'
            'RuntimeDirectory=metnos-filesystem-host\nRuntimeDirectoryMode=0755\n'
            'StateDirectory=metnos-filesystem-host\nStateDirectoryMode=0700\n'
            # bubblewrap configures loopback in its private network namespace
            # through NETLINK_ROUTE. Internet socket families remain denied.
            'RestrictAddressFamilies=AF_UNIX AF_NETLINK\n'
            'ExecStart=' + ' '.join(_quote(value) for value in command) + '\n'
            'WorkingDirectory=' + str(root).replace('%', '%%') + '\n'
            + ''.join('Environment=' + _quote(key + '=' + value) + '\n'
                      for key, value in environment.items())
            + 'Restart=on-failure\nRestartSec=3\nTimeoutStopSec=35\n'
            '\n[Install]\nWantedBy=multi-user.target\n')


def _previous_unit(text: str, binding: dict, config_path: Path) -> None:
    """Only replace this exact managed recipe, with its earlier runtime."""
    if not text.startswith(_MARKER):
        raise ValueError('existing filesystem host unit conflicts with installation')
    try:
        values = json.loads(text.split('\n', 1)[0][len(_MARKER):])
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError('unit metadata')
        root, python = (_path(value) for value in values)
    except (ValueError, TypeError) as exc:
        raise ValueError('existing filesystem host unit conflicts with installation') from exc
    expected = _unit(binding, root, python, config_path)
    # Upgrade the exact previously installed recipe, never an edited unit.
    previous = expected.replace('RestrictAddressFamilies=AF_UNIX AF_NETLINK\n',
                                'RestrictAddressFamilies=AF_UNIX\n', 1)
    if text not in (expected, previous):
        raise ValueError('existing filesystem host unit conflicts with installation')


def _publish(path: Path, text: str, previous: str | None) -> None:
    if _read(path) != previous:
        raise ValueError('filesystem host installation changed')
    if text == previous:
        return
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        if previous is None:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def refresh(runtime_root, python, *, service_uid, owner_uid=None, owner_gid=None,
            activate=True, config_path=CONFIG, unit_dir=Path('/etc/systemd/system')) -> dict:
    """Install/update only after verified cutover; retain an existing identity.

    A new binding requires the OS installer's UID/GID. Later updates keep the
    administrative binding even when a different administrator ships a release.
    Runtime selection is authenticated by the caller, never by these paths alone.
    """
    if os.geteuid() != 0:
        raise PermissionError('administrative filesystem host installation required')
    if type(activate) is not bool:
        raise ValueError('filesystem host activation')
    root, executable = _runtime(runtime_root, python)
    config_path, unit_dir = _path(config_path), _path(unit_dir)
    _directory(config_path.parent, create=True)
    _directory(unit_dir, create=True)
    previous = _read(config_path)
    binding = _binding(previous, service_uid, owner_uid, owner_gid)
    unit = unit_dir / UNIT
    observed = subprocess.run(['systemctl', 'show', UNIT, '-p', 'FragmentPath', '--value'],
                              capture_output=True, text=True, check=True)
    if observed.stdout.strip() not in ('', str(unit)):
        raise ValueError('existing filesystem host service must not be shadowed')
    overrides = subprocess.run(['systemctl', 'show', UNIT, '-p', 'DropInPaths', '--value'],
                               capture_output=True, text=True, check=True)
    if overrides.stdout.strip():
        raise ValueError('existing filesystem host service has overrides')
    old_unit = _read(unit)
    if old_unit is not None:
        if previous is None:
            raise ValueError('existing filesystem host unit has no administrative binding')
        _previous_unit(old_unit, binding, config_path)
    new_unit = _unit(binding, root, executable, config_path)
    _publish(config_path, json.dumps(binding, sort_keys=True) + '\n', previous)
    _publish(unit, new_unit, old_unit)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    if activate:
        subprocess.run(['systemctl', 'enable', UNIT], check=True)
        subprocess.run(['systemctl', 'restart', UNIT], check=True)
        subprocess.run(['systemctl', 'is-active', '--quiet', UNIT], check=True)
    return binding
