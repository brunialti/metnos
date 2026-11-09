"""Bounded local RPC for a filesystem helper running as an explicit OS owner.

The listener owns socket placement, binding configuration and helper history.
It supplies a closed operation handler and a mandatory boundary check; this
module neither opens the permissions database nor selects an OS identity.
Sockets remain owned by the caller. No exception detail crosses the socket.
"""
from __future__ import annotations

from contextlib import contextmanager
import array
import base64
import binascii
import fcntl
import json
import math
import os
import re
import socket
import stat
import struct
import time

import filesystem_permissions as permissions

MAX_FRAME_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 30
_INVALID = "ERR_FS_AUTHORITY_INVALID"
MAX_SOURCE_BYTES = 4 * 1024 * 1024 * 1024
MAX_TRANSFER_CHUNK = 128 * 1024


def _error(code=_INVALID):
    return permissions.PermissionError(code)


def _code(error):
    value = str(error)
    if isinstance(error, permissions.PermissionError) and value in permissions.ERROR_CODES:
        return value
    if isinstance(error, TimeoutError):
        return "ERR_FS_REQUEST_EXPIRED"
    return "ERR_FS_IO"


def verify_peer_uid(sock, expected_uid):
    """Authenticate the service/host UID outside executor user namespaces.

    Connected sockets must never be inherited by executors: SO_PEERCRED binds
    the process that connected, not a later holder of a shared descriptor.
    """
    if (type(expected_uid) is not int or expected_uid < 0 or sock.family != socket.AF_UNIX
            or not hasattr(socket, "SO_PEERCRED")):
        raise _error()
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, _ = struct.unpack("3i", raw)
    except (OSError, ValueError, struct.error) as error:
        raise _error() from error
    if uid != expected_uid or pid <= 0 or _uid_map(pid) != _uid_map("self"):
        raise _error()


def _uid_map(pid):
    try:
        fd = os.open(f"/proc/{pid}/uid_map", os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            data = os.read(fd, 65537)
        finally:
            os.close(fd)
    except OSError as error:
        raise _error() from error
    if not data or len(data) > 65536:
        raise _error()
    return data


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _error()
        result[key] = value
    return result


def _constant(_value):
    raise _error()


def _source_fd(fd):
    if type(fd) is not int or fd < 0:
        raise _error()
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
            or fcntl.fcntl(fd, fcntl.F_GETFL) & getattr(os, "O_PATH", 0)):
        raise _error()
    if info.st_size > MAX_SOURCE_BYTES:
        raise _error("ERR_FS_SIZE_LIMIT")


def _single_copy(request):
    from server_filesystem import copy_specs
    if (permissions.contract(request["manifest"], request["args"]) != "copy"
            or len(copy_specs(request["args"])) != 1):
        raise _error()


class _Channel:
    def __init__(self, sock, timeout_s, max_frame_bytes):
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s) or timeout_s <= 0 or timeout_s > TIMEOUT_S
                or type(max_frame_bytes) is not int or not 0 < max_frame_bytes <= MAX_FRAME_BYTES):
            raise _error()
        self.sock = sock
        self.deadline = time.monotonic() + timeout_s
        self.limit = max_frame_bytes

    def _timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _error("ERR_FS_REQUEST_EXPIRED")
        self.sock.settimeout(remaining)

    def send(self, message, fd=None):
        try:
            raw = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        except (TypeError, ValueError, RecursionError) as error:
            raise _error() from error
        if len(raw) > self.limit:
            raise _error("ERR_FS_SIZE_LIMIT")
        self._timeout()
        try:
            if fd is not None:
                _source_fd(fd)
                sent = self.sock.sendmsg([raw], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                                 array.array("i", [fd]))])
                raw = raw[sent:]
            if raw:
                self.sock.sendall(raw)
        except (OSError, TimeoutError) as error:
            raise _error(_code(error)) from error

    def receive(self, *, allow_fd=False):
        pending, descriptors = bytearray(), []
        try:
            while True:
                if len(pending) >= self.limit:
                    raise _error("ERR_FS_SIZE_LIMIT")
                self._timeout()
                try:
                    data, ancillary, flags, _ = self.sock.recvmsg(
                        min(65536, self.limit - len(pending)), socket.CMSG_SPACE(16 * array.array("i").itemsize),
                        getattr(socket, "MSG_CMSG_CLOEXEC", 0))
                except (OSError, TimeoutError) as error:
                    raise _error(_code(error)) from error
                unknown = False
                previous_count = len(descriptors)
                for level, kind, raw in ancillary:
                    if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
                        unknown = True
                        continue
                    found = array.array("i")
                    found.frombytes(raw[:len(raw) - len(raw) % found.itemsize])
                    descriptors.extend(found)
                if (unknown or flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC)
                        or len(descriptors) > 1 or descriptors and not allow_fd
                        or len(descriptors) > previous_count and pending):
                    raise _error()
                if not data:
                    raise _error("ERR_FS_IO")
                pending.extend(data)
                split = pending.find(b"\n")
                if split < 0:
                    continue
                # One request/reply at a time: reject pipelining and ambiguous FD attachment.
                if split != len(pending) - 1:
                    raise _error()
                try:
                    value = json.loads(pending[:-1].decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise _error() from error
                if not isinstance(value, dict):
                    raise _error()
                if allow_fd:
                    fd = descriptors[0] if descriptors else None
                    if fd is not None:
                        _source_fd(fd)
                        os.set_inheritable(fd, False)
                    descriptors.clear()
                    return value, fd
                return value
        finally:
            for fd in descriptors:
                os.close(fd)


def _turn_id(value):
    if (not isinstance(value, str) or not value or len(value) > 256
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value)):
        raise _error()


def _integer(value, maximum=MAX_SOURCE_BYTES):
    return type(value) is int and 0 <= value <= maximum


def _transfer_row(row):
    if row is None:
        return
    if (not isinstance(row, dict)
            or set(row) != {"temp_name", "destination", "written", "inode", "created_at", "committed", "receipt"}
            or not isinstance(row["temp_name"], str)
            or row["temp_name"] != "" and not re.fullmatch(r"\.metnos-transfer-[a-f0-9]{48}", row["temp_name"])
            or permissions.normalize_path(row["destination"], "posix") != row["destination"]
            or not _integer(row["written"]) or not _integer(row["inode"], 2**64 - 1)
            or row["inode"] == 0 and (row["written"] or row["committed"] or not row["temp_name"]
                                      or row["receipt"] is not None)
            or not _integer(row["created_at"], 2**63 - 1)
            or type(row["committed"]) not in (bool, int) or row["committed"] not in (0, 1)
            or row["receipt"] is not None and not isinstance(row["receipt"], dict)):
        raise _error()


def _transfer_request(transfer):
    if (not isinstance(transfer, dict)
            or set(transfer) != {"action", "path", "check_path", "internal", "overwrite", "row",
                                 "offset", "content", "size", "sha256", "expected_sha256"}):
        raise _error()
    action = transfer["action"]
    if (not isinstance(action, str) or action not in {"read", "write", "commit", "abort", "delete"}
            or permissions.normalize_path(transfer["path"], "posix") != transfer["path"]
            or permissions.normalize_path(transfer["check_path"], "posix") != transfer["check_path"]
            or type(transfer["internal"]) is not bool or type(transfer["overwrite"]) is not bool
            or transfer["internal"] and action != "read"
            or not transfer["internal"] and transfer["path"] != transfer["check_path"]):
        raise _error()
    _transfer_row(transfer["row"])
    if transfer["row"] is not None and transfer["row"]["destination"] != transfer["path"]:
        raise _error()
    used = {"read": {"offset"}, "write": {"offset", "content"}, "commit": {"size", "sha256"},
            "abort": set(), "delete": set()}[action]
    if any(transfer[key] is not None for key in {"offset", "content", "size", "sha256"} - used):
        raise _error()
    expected = transfer["expected_sha256"]
    if expected is not None and (action not in {"commit", "delete"} or not isinstance(expected, str)
                                 or not re.fullmatch(r"[a-f0-9]{64}", expected)):
        raise _error()
    if action in {"read", "write"} and not _integer(transfer["offset"]):
        raise _error()
    if action == "read" and transfer["row"] is not None:
        raise _error()
    if action == "write":
        content = transfer["content"]
        if not isinstance(content, str) or len(content) > 4 * ((MAX_TRANSFER_CHUNK + 2) // 3):
            raise _error("ERR_FS_SIZE_LIMIT")
        try:
            data = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error) as error:
            raise _error() from error
        if len(data) > MAX_TRANSFER_CHUNK or transfer["offset"] + len(data) > MAX_SOURCE_BYTES:
            raise _error("ERR_FS_SIZE_LIMIT")
    if action == "commit" and (transfer["row"] is None or not _integer(transfer["size"])
            or not isinstance(transfer["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", transfer["sha256"])):
        raise _error()
    if transfer["row"] is not None and not transfer["row"]["temp_name"] and action != "delete":
        raise _error()


def validate_request(request):
    """Validate the closed request envelope before calling a helper handler."""
    if not isinstance(request, dict):
        raise _error()
    if request.get("action") == "known_folders":
        # The owner's named folders: no account, path or callback involved.
        if set(request) != {"action"}:
            raise _error()
        return
    owner = request.get("owner")
    if not isinstance(owner, str) or not owner or len(owner) > 256 or any(ord(c) < 32 for c in owner):
        raise _error()
    action = request.get("action")
    if action == "probe":
        if set(request) != {"action", "owner", "grant"} or not isinstance(request["grant"], dict):
            raise _error()
        grant = request["grant"]
        if (set(grant) != {"path", "kind", "can_read", "can_write", "recursive", "can_overwrite", "path_style"}
                or grant["path_style"] != "posix" or not isinstance(grant["kind"], str)
                or grant["kind"] not in {"file", "folder"}
                or permissions.normalize_path(grant["path"], "posix") != grant["path"]):
            raise _error()
        flags = [grant[field] for field in ("can_read", "can_write", "recursive", "can_overwrite")]
        if (any(type(value) not in (bool, int) or value not in (0, 1) for value in flags)
                or not (grant["can_read"] or grant["can_write"])
                or (grant["can_overwrite"] and not grant["can_write"])
                or (grant["recursive"] and grant["kind"] != "folder")):
            raise _error()
        values = []
    elif action == "size":
        # The size of one regular file the owner may read: metadata only.
        if (set(request) != {"action", "owner", "path"} or not isinstance(request["path"], str)
                or permissions.normalize_path(request["path"], "posix") != request["path"]):
            raise _error()
        values = []
    elif action == "transfer":
        if set(request) != {"action", "owner", "turn_id", "transfer"}:
            raise _error()
        _turn_id(request["turn_id"])
        _transfer_request(request["transfer"])
        values = []
    elif action in ("execute", "open_source"):
        if set(request) != {"action", "owner", "manifest", "args", "turn_id"}:
            raise _error()
        if not isinstance(request["manifest"], dict) or not isinstance(request["args"], dict):
            raise _error()
        _turn_id(request["turn_id"])
        values = [request["args"]]
        if action == "open_source":
            _single_copy(request)
    elif action == "undo":
        if (set(request) != {"action", "owner", "pattern", "results"}
                or not isinstance(request["pattern"], str)
                or request["pattern"] not in {"swap_src_dst", "delete_created_paths", "restore_blob_backup"}
                or not isinstance(request["results"], dict)):
            raise _error()
        context = request["results"].get("_filesystem")
        if not isinstance(context, dict):
            raise _error()
        validate_request({"action": "execute", "owner": context.get("owner"),
                          "manifest": context.get("manifest"), "args": context.get("args"),
                          "turn_id": context.get("turn_id")})
        if context["owner"] != owner:
            raise _error()
        values = [request["results"], context["args"]]
    else:
        raise _error()
    for value in values:
        for field in ("paths", "entries", "files", "contents", "results", "failed"):
            items = value.get(field)
            if isinstance(items, list) and len(items) > 100:
                raise _error("ERR_FS_SIZE_LIMIT")


def _check_message(message):
    if set(message) != {"type", "id", "path", "action", "kwargs"}:
        raise _error()
    path, action, kwargs = message["path"], message["action"], message["kwargs"]
    if (permissions.normalize_path(path, "posix") != path or not isinstance(action, str) or action not in {"read", "write"}
            or not isinstance(kwargs, dict) or set(kwargs) - {"directory", "recursive", "overwrite"}
            or any(type(value) is not bool for value in kwargs.values())):
        raise _error()
    return path, action, kwargs


def _callback_allowed(request, kind, action=None):
    if kind == "checkpoint" and request["action"] != "transfer":
        return False
    if request["action"] in {"probe", "known_folders"}:
        return False
    if request["action"] in {"open_source", "size"}:
        return kind == "check" and action == "read"
    if request["action"] == "transfer" and request["transfer"]["action"] == "read":
        # A private undo backup is authorized by write access to its original
        # destination. It never grants general read access to that destination.
        required = "write" if request["transfer"]["internal"] else "read"
        return kind == "check" and action == required
    return True


def _checkpoint_row(request, row):
    _transfer_row(row)
    if request["action"] != "transfer":
        raise _error()
    transfer = request["transfer"]
    if row is not None and (row["destination"] != transfer["path"]
            or not row["temp_name"] and transfer["action"] != "delete"):
        raise _error()


class RemoteAuthority:
    """Helper-side authority. Every check also applies the listener's boundary."""

    def __init__(self, channel, request, check_boundary):
        self._channel, self._request, self._check_boundary = channel, request, check_boundary
        self._sequence = 0
        self._mutating = False

    def _call(self, kind, **fields):
        if not _callback_allowed(self._request, kind, fields.get("action")):
            raise _error()
        self._sequence += 1
        self._channel.send({"type": kind, "id": self._sequence, **fields})
        reply = self._channel.receive()
        if (reply.get("type") != "reply" or type(reply.get("id")) is not int
                or reply["id"] != self._sequence or type(reply.get("ok")) is not bool):
            raise _error()
        if reply["ok"]:
            if set(reply) != {"type", "id", "ok"}:
                raise _error()
        else:
            if (set(reply) != {"type", "id", "ok", "error_code"}
                    or not isinstance(reply["error_code"], str) or reply["error_code"] not in permissions.ERROR_CODES):
                raise _error()
            raise _error(reply["error_code"])

    def check(self, path, action, **kwargs):
        _check_message({"type": "check", "id": 0, "path": path, "action": action, "kwargs": kwargs})
        if self._check_boundary(self._request, path, action, kwargs) is False:
            raise _error("ERR_FS_PERMISSION_REQUIRED")
        self._call("check", path=path, action=action, kwargs=kwargs)

    def checkpoint(self, row):
        _checkpoint_row(self._request, row)
        self._call("checkpoint", row=row)

    @contextmanager
    def mutation(self):
        if self._mutating:
            raise _error()
        self._call("begin")
        self._mutating = True
        try:
            yield
        except BaseException:
            try:
                self._call("abort")
            except (OSError, ValueError):
                pass  # Parent also rolls back when its connection fails.
            raise
        else:
            self._call("end")
        finally:
            self._mutating = False


def client_exchange(sock, request, authority, *, expected_peer_uid=None,
                    timeout_s=TIMEOUT_S, max_frame_bytes=MAX_FRAME_BYTES,
                    source_fd=None, receive_fd=False):
    """Run one request and answer helper checks using current local grants.

    The authority's mutation context is held only between begin and end/abort.
    Any disconnect, timeout, malformed frame or premature result rolls it back.
    A supplied source_fd stays owned by the caller. For open_source only,
    receive_fd=True returns (result, fd), transferring the received FD to it.
    """
    validate_request(request)
    if (type(receive_fd) is not bool or receive_fd != (request["action"] == "open_source")
            or (source_fd is not None or receive_fd or request["action"] == "transfer") and expected_peer_uid is None):
        raise _error()
    if source_fd is not None:
        if request["action"] != "execute":
            raise _error()
        _single_copy(request)
        _source_fd(source_fd)
    if expected_peer_uid is not None:
        verify_peer_uid(sock, expected_peer_uid)
    previous_timeout = sock.gettimeout()
    channel = _Channel(sock, timeout_s, max_frame_bytes)
    transaction = None
    sequence = 0
    result_fd = None
    try:
        channel.send({"type": "request", "request": request}, fd=source_fd)
        while True:
            message, result_fd = channel.receive(allow_fd=True)
            kind = message.get("type")
            if result_fd is not None and (not receive_fd or kind != "result"):
                raise _error()
            if kind == "result":
                if transaction is not None or set(message) != {"type", "result"} or not isinstance(message["result"], dict):
                    raise _error()
                if receive_fd:
                    if message["result"] != {"ok": True} or result_fd is None:
                        raise _error()
                    result, result_fd = (message["result"], result_fd), None
                    return result
                return message["result"]
            if kind == "error":
                if (set(message) != {"type", "error_code"} or not isinstance(message["error_code"], str)
                        or message["error_code"] not in permissions.ERROR_CODES):
                    raise _error()
                raise _error(message["error_code"])
            sequence += 1
            if (type(message.get("id")) is not int
                    or message["id"] != sequence or not isinstance(kind, str)
                    or kind not in {"check", "checkpoint", "begin", "end", "abort"}
                    or not _callback_allowed(request, kind, message.get("action"))):
                raise _error()
            if kind == "check":
                path, action, kwargs = _check_message(message)
            elif kind == "checkpoint":
                if set(message) != {"type", "id", "row"}:
                    raise _error()
                _checkpoint_row(request, message["row"])
            elif set(message) != {"type", "id"} or ((kind == "begin") == (transaction is not None)):
                raise _error()
            reply = {"type": "reply", "id": sequence, "ok": True}
            try:
                if kind == "check":
                    if authority.check(path, action, **kwargs) is False:
                        raise _error("ERR_FS_PERMISSION_REQUIRED")
                elif kind == "checkpoint":
                    if authority.checkpoint(message["row"]) is False:
                        raise _error("ERR_FS_PERMISSION_REQUIRED")
                elif kind == "begin":
                    candidate = authority.mutation()
                    candidate.__enter__()
                    transaction = candidate
                else:
                    current, transaction = transaction, None
                    if kind == "end":
                        current.__exit__(None, None, None)
                    else:
                        error = _error("ERR_FS_INVOCATION_INVALID")
                        current.__exit__(type(error), error, None)
            except Exception as error:
                reply.update(ok=False, error_code=_code(error))
            channel.send(reply)
    finally:
        try:
            if result_fd is not None:
                os.close(result_fd)
            if transaction is not None:
                error = _error("ERR_FS_INVOCATION_INVALID")
                transaction.__exit__(type(error), error, None)
        finally:
            sock.settimeout(previous_timeout)


def serve_connection(sock, handler, *, check_boundary, expected_peer_uid=None,
                     timeout_s=TIMEOUT_S, max_frame_bytes=MAX_FRAME_BYTES):
    """Serve one authenticated connection with a closed operation handler.

    The handler dispatches only the broker's execute/undo operations. Its
    boundary callback must enforce the administrative binding's owner, allowed
    roots and private helper directories on every check, raising on denial.
    Authentication and request rejection happen before the handler is called.
    An execute request may pass source_fd= to the handler, borrowed for this
    call. An open_source handler transfers (result, fd) to this transport.
    """
    if not callable(handler) or not callable(check_boundary):
        raise _error()
    if expected_peer_uid is not None:
        verify_peer_uid(sock, expected_peer_uid)
    previous_timeout = sock.gettimeout()
    channel = _Channel(sock, timeout_s, max_frame_bytes)
    source_fd = result_fd = None
    try:
        message, source_fd = channel.receive(allow_fd=True)
        if set(message) != {"type", "request"} or message["type"] != "request":
            raise _error()
        request = message["request"]
        validate_request(request)
        if source_fd is not None:
            if expected_peer_uid is None or request["action"] != "execute":
                raise _error()
            _single_copy(request)
        if request["action"] in {"open_source", "transfer"} and expected_peer_uid is None:
            raise _error()
        authority = RemoteAuthority(channel, request, check_boundary)
        result = (handler(request, authority, source_fd=source_fd) if source_fd is not None
                  else handler(request, authority))
        if request["action"] == "open_source":
            if not isinstance(result, tuple) or len(result) != 2:
                raise _error()
            result, result_fd = result
            if result != {"ok": True} or result_fd is None:
                raise _error()
            _source_fd(result_fd)
        if authority._mutating or not isinstance(result, dict):
            raise _error()
        channel.send({"type": "result", "result": result}, fd=result_fd)
    except Exception as error:
        try:
            channel.send({"type": "error", "error_code": _code(error)})
        except (OSError, ValueError):
            pass
        raise
    finally:
        for fd in (source_fd, result_fd):
            if type(fd) is int and fd >= 0:
                os.close(fd)
        sock.settimeout(previous_timeout)
