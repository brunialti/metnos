"""Prepare local dependencies of the signed catalog without starting them.

All service commands run as the fixed Metnos account. Existing host units are
never replaced: an exact previous preparation is the only resumable state.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess

from . import sidecar
from runtime.service_profile import validate_profile

UNITS = {"llm": "llama-server.service", "searxng": "searxng.service", "photon": "photon.service"}


def _quote(value: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid service argument")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def _artifact(value, root: Path) -> Path:
    if not isinstance(value, str):
        raise ValueError("invalid infrastructure artifact")
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("infrastructure artifact outside service data")
    if not path.is_file():
        raise ValueError("missing infrastructure artifact")
    return path


def _write_exact(path: Path, text: str) -> None:
    """Publish in an administrative directory; refuse conflicts and links."""
    for directory in (*reversed(path.parent.parents), path.parent):
        directory.mkdir(mode=0o755, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("untrusted infrastructure directory")
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or path.read_text() != text):
            raise ValueError("existing infrastructure conflicts with installation")
        return
    temporary = path.with_suffix(path.suffix + '.preparing')
    with temporary.open('x') as stream:
        stream.write(text)
        stream.flush()
        os.fchmod(stream.fileno(), 0o644)
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()


def prepare(profile: dict, environment: dict, *, unit_dir=Path('/etc/systemd/system'),
            vision_path=Path('/etc/metnos/vlm-startup.toml')) -> None:
    if os.geteuid() != 0:
        raise PermissionError("administrative infrastructure preparation required")
    from executor_birth_host_path_policy import SERVICE_ACCOUNT_NAME_V1

    profile = validate_profile(profile)
    home = Path(environment['HOME'])
    data = Path(environment.get('METNOS_USER_DATA', home / '.local/share/metnos'))
    config = Path(environment.get('METNOS_USER_CONFIG', home / '.config/metnos'))
    units = {}
    if 'llm' not in profile:
        with (data / 'llm/server.json').open() as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError('local LLM configuration size')
        entry = json.loads(raw)
        if (not isinstance(entry, dict) or set(entry) != {'executable', 'model', 'ngl'}
                or type(entry['ngl']) is not int or entry['ngl'] not in (0, 999)):
            raise ValueError('local LLM configuration')
        executable = _artifact(entry['executable'], data)
        model = _artifact(entry['model'], data)
        units['llm'] = ([str(executable), '-m', str(model), '--host', '127.0.0.1',
                         '--port', '8080', '-ngl', str(entry['ngl']), '-c', '8192'], data, {})
    if 'searxng' not in profile:
        root = data / 'sidecars/searxng'
        units['searxng'] = ([str(root / 'venv/bin/python'), '-m', 'searx.webapp'],
                            root / 'searxng', {'TMPDIR': str(root / 'cache'),
                            'SEARXNG_SETTINGS_PATH': str(config / 'searxng/settings.yml')})
    if 'photon' not in profile:
        root = data / 'sidecars/photon'
        units['photon'] = (['/usr/bin/java', '-Xmx4G', '-jar',
                           str(root / f'photon-{sidecar._PHOTON_VERSION}.jar'), 'serve',
                           '-data-dir', str(root / 'data/current'), '-listen-ip', '127.0.0.1',
                           '-listen-port', '2322', '-j', '4'], root, {})
    # Render only fixed directives. No prepared user-unit text is interpreted.
    for name, (argv, directory, variables) in units.items():
        variables = {'HOME': str(home), 'PYTHONUNBUFFERED': '1', **variables}
        text = ('[Unit]\nDescription=Metnos local ' + name + '\nAfter=network.target\n'
                '\n[Service]\nType=simple\nUser=' + SERVICE_ACCOUNT_NAME_V1 +
                '\nGroup=' + SERVICE_ACCOUNT_NAME_V1 + '\nNoNewPrivileges=yes\nUMask=0077\n'
                'ExecStart=' + ' '.join(_quote(v) for v in argv) + '\nWorkingDirectory=' +
                _quote(str(directory)) + '\nRestart=on-failure\nRestartSec=10\n' +
                ''.join('Environment=' + _quote(k + '=' + v) + '\n' for k, v in variables.items()))
        unit = unit_dir / UNITS[name]
        # Refuse to shadow a different unit supplied by the operating system.
        result = subprocess.run(['systemctl', 'show', UNITS[name], '-p', 'FragmentPath', '--value'],
                                capture_output=True, text=True, check=True)
        if result.stdout.strip() not in ('', str(unit)):
            raise ValueError('existing host service requires explicit services.toml selection')
        _write_exact(unit, text)
    if 'vlm' not in profile:
        from .llm_manager import _find_llama_bin
        import tomlkit
        model_root = data / 'models/vlm'
        executable = _find_llama_bin(data / 'llm/llama.cpp', 'llama-server')
        paths = {'model': str(model_root / sidecar._VLM_MODEL),
                 'mmproj': str(model_root / sidecar._VLM_MMPROJ), 'llama_bin': str(executable)}
        for value in paths.values():
            _artifact(value, data)
        _write_exact(vision_path, tomlkit.dumps({'default': paths}))
    if units:
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
