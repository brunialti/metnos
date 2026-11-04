"""Isolated interpretation of document bytes, with no document path authority."""
from __future__ import annotations

import base64
import json
import math
import subprocess
from pathlib import Path
import sys

import document_parse

MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_MEMORY_BYTES = 512 * 1024 * 1024


def _command():
    import shutil
    bwrap = shutil.which('bwrap')
    if not bwrap:
        raise ValueError('document_parser_isolation_unavailable')
    command = [bwrap, '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
               '--tmpfs', '/', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    # Only interpreter/libraries and these three immutable source files are
    # mounted. No account home, grant store, supplied path or working tree.
    for name in ('/usr', '/lib', '/lib64'):
        if Path(name).exists():
            command += ['--ro-bind', name, name]
    if sys.prefix != sys.base_prefix:
        command += ['--ro-bind', str(Path(sys.prefix).resolve()), sys.prefix]
    source = Path(__file__).resolve().parent
    command += ['--dir', '/parser']
    for name in ('document_parse_worker.py', 'document_parse.py', 'pdf_extract.py'):
        command += ['--ro-bind', str(source / name), '/parser/' + name]
    return command + ['--chdir', '/tmp', '--', sys.executable, '-E', '-s', '-B',
                      '/parser/document_parse_worker.py', '--worker']


def parse_bytes_bounded(raw, *, suffix, file_size, max_bytes=document_parse.MAX_BYTES, timeout_s=15):
    from bounded_subprocess import run_bounded_subprocess
    if (not isinstance(raw, bytes) or len(raw) > document_parse.MAX_BYTES
            or suffix not in {'.pdf', '.docx', '.xlsx'}
            or type(file_size) is not int or file_size < len(raw)
            or type(max_bytes) is not int or not 0 < max_bytes <= document_parse.MAX_BYTES
            or type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError('document_input_invalid')
    payload = json.dumps({'data': base64.b64encode(raw).decode('ascii'), 'suffix': suffix,
                          'file_size': file_size, 'max_bytes': max_bytes}, separators=(',', ':'))
    try:
        result = run_bounded_subprocess(_command(), input_text=payload, timeout_s=min(timeout_s, 15),
            env={}, stdout_limit_bytes=MAX_OUTPUT_BYTES, stderr_limit_bytes=4096)
    except subprocess.SubprocessError as exc:
        raise ValueError('document_parser_failed') from exc
    if result.returncode != 0:
        raise ValueError('document_parser_failed')
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get('ok') is not True or not isinstance(value.get('content'), str):
        raise ValueError('document_parser_failed')
    return value


def _worker():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    size = 4 * ((document_parse.MAX_BYTES + 2) // 3) + 1024
    payload = sys.stdin.buffer.read(size + 1)
    if len(payload) > size:
        raise ValueError('document_input_invalid')
    request = json.loads(payload)
    if not isinstance(request, dict) or set(request) != {'data', 'suffix', 'file_size', 'max_bytes'}:
        raise ValueError('document_input_invalid')
    raw = base64.b64decode(request.pop('data'), validate=True)
    result = document_parse.parse_bytes(raw, **request)
    output = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    if len(output) > MAX_OUTPUT_BYTES:
        raise ValueError('document_output_limit')
    sys.stdout.write(output)


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit(2)
    try:
        _worker()
    except BaseException:
        raise SystemExit(1) from None
