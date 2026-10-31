"""Pinned POSIX file primitives shared by the server and transfer brokers."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
from contextlib import contextmanager

import config as C
from filesystem_permissions import PermissionError, normalize_path

CHUNK_SIZE = 128 * 1024
MAX_FILE_SIZE = 4 * 1024**3


def reserved(path):
    # Application state and credentials are not ordinary user documents.
    workspace = str(C.PATH_WORKSPACE).rstrip("/")
    if path == workspace or path.startswith(workspace + "/"):
        return
    roots = (C.PATH_ROOT, C.PATH_USER_CONFIG, C.PATH_USER_STATE, C.PATH_USER_CACHE, C.PATH_USER_DATA)
    if any(path == str(p) or path.startswith(str(p).rstrip("/") + "/") for p in roots):
        raise PermissionError("ERR_FS_RESERVED_PATH")


@contextmanager
def parent(path, *, internal=False):
    path = normalize_path(path, "posix")
    if not internal:
        reserved(path)
    parts = path[1:].split("/")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PermissionError("ERR_FS_LINK_REFUSED")
    if info.st_size > MAX_FILE_SIZE:
        raise PermissionError("ERR_FS_SIZE_LIMIT")
    return info


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)


def open_file(fd, name, flags=os.O_RDONLY):
    file = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    try:
        regular(file)
    except BaseException:
        os.close(file)
        raise
    return file


def digest(fd, check=lambda: None):
    os.lseek(fd, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while chunk := os.read(fd, CHUNK_SIZE):
        check()
        hasher.update(chunk)
    return hasher.hexdigest()


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError("short filesystem write")
        view = view[count:]


def backup(fd, turn_id, check=lambda: None):
    if not isinstance(turn_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", turn_id):
        raise PermissionError("ERR_FS_INVOCATION_INVALID")
    before = identity(regular(fd))
    root = C.PATH_USER_DATA / "_history" / turn_id / "blob"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = root / (".staging-" + secrets.token_hex(24))
    try:
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            hasher = hashlib.sha256()
            while chunk := os.read(fd, CHUNK_SIZE):
                check()
                with os.fdopen(os.dup(out), "wb") as stream:
                    stream.write(chunk)
                hasher.update(chunk)
            if identity(regular(fd)) != before:
                raise PermissionError("ERR_FS_SOURCE_CHANGED")
            os.fsync(out)
        finally:
            os.close(out)
        sha = hasher.hexdigest()
        target = root / (sha + ".bin")
        try:
            os.link(temp, target, follow_symlinks=False)
        except FileExistsError:
            existing = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                if digest(existing, check) != sha:
                    raise PermissionError("ERR_FS_HASH_MISMATCH")
            finally:
                os.close(existing)
        return str(target)
    finally:
        temp.unlink(missing_ok=True)
        os.lseek(fd, 0, os.SEEK_SET)


def existing(fd, name):
    try:
        file = open_file(fd, name)
    except FileNotFoundError:
        return None
    return file


def stage(fd):
    name = ".metnos-transfer-" + secrets.token_hex(24)
    file = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    return name, file


def commit(fd, temp, name, *, overwrite=False):
    if overwrite:
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
    else:
        os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.unlink(temp, dir_fd=fd)
    os.fsync(fd)
