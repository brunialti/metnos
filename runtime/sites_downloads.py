# SPDX-License-Identifier: MIT
"""Native-sandbox writer for captured browser bytes; never fetches the site."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import time
from types import SimpleNamespace

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILES = 100
DEFAULT_TIMEOUT_S = 120
_HINTS = ['~/notes/scratch/**', '/tmp/**', 'arg:dst_dir']


class DownloadError(ValueError):
    pass


def limits(raw=None):
    raw = {} if raw is None else raw
    if not isinstance(raw, dict) or set(raw)-{'max_file_bytes','max_total_bytes','max_files','timeout_s'}:
        raise DownloadError('download_limits_invalid')
    result = {}
    for key, ceiling in (('max_file_bytes',MAX_FILE_BYTES),('max_total_bytes',MAX_TOTAL_BYTES),
                         ('max_files',MAX_FILES),('timeout_s',DEFAULT_TIMEOUT_S)):
        value = raw.get(key, ceiling)
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=ceiling:
            raise DownloadError('download_limits_invalid')
        result[key] = value
    return result


def _open_directory(path):
    """Walk from / with O_NOFOLLOW; resolving a symlink would lose evidence."""
    candidate = Path(os.path.expanduser(path))
    if not candidate.is_absolute() or '..' in candidate.parts:
        raise DownloadError('download_destination_invalid')
    fd = os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        for part in candidate.parts[1:]:
            next_fd = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except OSError as exc:
        os.close(fd)
        raise DownloadError('download_destination_invalid') from exc


def check_scope(path, *, actor, executor=None):
    """Reload user revocations; the enclosing native sandbox enforces writes.

    The broker passes its verified catalog entry. The admitted writer uses its
    own fixed signed capability declaration, inside the actual runtime bind.
    Neither path accepts a capability from an HTTP request.
    """
    from capabilities import effective_capabilities
    from invocation_scope import check_invocation_scope
    import workspace_policy
    workspace_policy.load_workspace_policy()
    if executor is None:
        executor = SimpleNamespace(name='act_sites',args_schema={'properties':{'dst_dir':{'type':'string'}}},
            capabilities=[{'name':'fs:write','hint':_HINTS,'path_args':['dst_dir']}])
    effective = effective_capabilities(executor.capabilities,executor.args_schema,{'dst_dir':path})
    if not any(cap.get('name')=='fs:write' and 'dst_dir' in (cap.get('path_args') or ())
               for cap in effective):
        raise DownloadError('download_scope_missing')
    denial = check_invocation_scope(executor,{'dst_dir':path},actor=actor)
    if denial:
        raise DownloadError('download_scope_denied')


def destination_binding(path, *, actor, executor=None):
    if not isinstance(path,str) or not path.strip():
        raise DownloadError('download_destination_invalid')
    if '..' in Path(path).parts or not Path(os.path.expanduser(path)).is_absolute():
        raise DownloadError('download_destination_invalid')
    path = os.path.abspath(os.path.expanduser(path))
    check_scope(path,actor=actor,executor=executor)
    fd = _open_directory(path)
    try:
        st = os.fstat(fd)
        return {'dst_dir':path,'device':st.st_dev,'inode':st.st_ino}
    finally:
        os.close(fd)


def _digest(fd, max_bytes):
    size=0; digest=hashlib.sha256(); prefix=b''
    while data:=os.read(fd,64*1024):
        size+=len(data)
        if size>max_bytes:
            raise DownloadError('download_size_exceeded')
        if not prefix:
            prefix=data[:5]
        digest.update(data)
    return size,digest.hexdigest(),prefix


def publish(binding, captured, transfer, *, actor):
    """No-overwrite publication; an ACK retry finds precisely the same inode."""
    handle=captured.get('handle',''); expected=captured.get('sha256',''); size=captured.get('bytes')
    if not re.fullmatch(r'[0-9a-f]{48}',handle) or not re.fullmatch(r'[0-9a-f]{64}',expected):
        raise DownloadError('download_receipt_invalid')
    if isinstance(size,bool) or not isinstance(size,int) or not 0<=size<=MAX_FILE_BYTES:
        raise DownloadError('download_size_exceeded')
    path=binding['dst_dir']
    if destination_binding(path,actor=actor)!=binding:
        raise DownloadError('download_destination_changed')
    dirfd=_open_directory(path)
    temporary='.'+handle+'.partial'
    # The extension comes from verified bytes, never the site's filename.
    final=handle+('.pdf' if captured.get('media_type')=='application/pdf' else '.bin')
    temporary_owned=False
    try:
        try:
            existing=os.open(final,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=dirfd)
        except FileNotFoundError:
            existing=None
        if existing is not None:
            try:
                st=os.fstat(existing)
                if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid():
                    raise DownloadError('download_destination_collision')
                actual,sha,_=_digest(existing,MAX_FILE_BYTES)
            finally:
                os.close(existing)
            if (actual,sha)!=(size,expected):
                raise DownloadError('download_destination_collision')
            if destination_binding(path,actor=actor)!=binding:
                raise DownloadError('download_destination_changed')
            return {'path':str(Path(path)/final),'bytes':size,'sha256':expected,
                    'media_type':captured.get('media_type'),'reconciled':True}
        deadline=captured.get('deadline_monotonic',time.monotonic()+DEFAULT_TIMEOUT_S)
        if not isinstance(deadline,(int,float)) or time.monotonic()>=deadline:
            raise DownloadError('download_transfer_timeout')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=dirfd)
        temporary_owned=True
        try:
            class BoundedWriter:
                used=0
                def write(self,data):
                    if time.monotonic()>=deadline:
                        raise DownloadError('download_transfer_timeout')
                    if self.used+len(data)>size:
                        raise DownloadError('download_size_exceeded')
                    view=memoryview(data)
                    while view:
                        count=os.write(fd,view);view=view[count:]
                    self.used+=len(data)
            transfer(BoundedWriter())
            os.fsync(fd)
        finally:
            os.close(fd)
        fd=os.open(temporary,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=dirfd)
        try:
            actual,sha,prefix=_digest(fd,MAX_FILE_BYTES)
        finally:
            os.close(fd)
        if (actual,sha)!=(size,expected):
            raise DownloadError('download_integrity_failed')
        if captured.get('media_type')=='application/pdf' and prefix!=b'%PDF-':
            raise DownloadError('download_integrity_failed')
        if time.monotonic()>=deadline:
            raise DownloadError('download_transfer_timeout')
        if destination_binding(path,actor=actor)!=binding:
            raise DownloadError('download_destination_changed')
        st=os.fstat(dirfd)
        if (st.st_dev,st.st_ino)!=(binding['device'],binding['inode']):
            raise DownloadError('download_destination_changed')
        os.link(temporary,final,src_dir_fd=dirfd,dst_dir_fd=dirfd,follow_symlinks=False)
        os.unlink(temporary,dir_fd=dirfd)
        temporary_owned=False
        os.fsync(dirfd)
        return {'path':str(Path(path)/final),'bytes':size,'sha256':expected,'media_type':captured.get('media_type')}
    except OSError as exc:
        raise DownloadError('download_publication_failed') from exc
    finally:
        if temporary_owned:
            try:
                os.unlink(temporary,dir_fd=dirfd)
            except FileNotFoundError:
                pass
        os.close(dirfd)
