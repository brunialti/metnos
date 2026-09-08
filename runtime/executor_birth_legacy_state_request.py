"""Canonical account-bound request for legacy-state observation/adoption."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from executor_birth_account_identity import PosixAccountSnapshotV1
from executor_birth_canonical import encode_canonical_ascii_v1
from executor_birth_crypto_framing import framed_sha256_v1, is_framed_sha256_v1
from executor_birth_host_layout import HostPathRoleV1, build_host_layout_spec_v1


_REQUEST_DOMAIN = b"metnos.executor-birth.legacy-state-request/v1\0"
_REQUEST_SEAL = object()


class LegacyStateError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.code = "birth_legacy_state_invalid"
        self.detail = detail
        super().__init__(self.code)


def _invalid(detail: str) -> LegacyStateError:
    return LegacyStateError(detail)


def is_legacy_state_utf8_path_v1(path: PurePosixPath) -> bool:
    raw = path.as_posix()
    if "\0" in raw or raw.startswith("//"):
        return False
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class LegacyStateRequestV1:
    state_root: PurePosixPath
    service_uid: int
    service_gid: int
    distribution_sha256: str
    _account: PosixAccountSnapshotV1 | None = field(repr=False, compare=False)
    _canonical: bool = field(repr=False, compare=False)
    _binding: tuple[PurePosixPath, int, int, str] | None = field(
        repr=False, compare=False,
    )
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        canonical_shape = (
            type(self._account) is PosixAccountSnapshotV1
            and type(self._binding) is tuple
        )
        raw_shape = self._account is None and self._binding is None
        if (
            self._seal is not _REQUEST_SEAL
            or type(self._canonical) is not bool
            or (self._canonical and not canonical_shape)
            or (not self._canonical and not raw_shape)
            or type(self.state_root) is not PurePosixPath
            or not self.state_root.is_absolute()
            or self.state_root == PurePosixPath("/")
            or ".." in self.state_root.parts
            or not is_legacy_state_utf8_path_v1(self.state_root)
            or type(self.service_uid) is not int or self.service_uid <= 0
            or type(self.service_gid) is not int or self.service_gid <= 0
            or not is_framed_sha256_v1(self.distribution_sha256)
        ):
            raise _invalid("request")

    def _deny_transfer(self, *_args: object) -> None:
        raise _invalid("request_transfer")

    __copy__ = __deepcopy__ = __reduce__ = __reduce_ex__ = _deny_transfer

    @property
    def request_id(self) -> str:
        value = {
            "distribution_sha256": self.distribution_sha256,
            "service_gid": self.service_gid,
            "service_uid": self.service_uid,
            "state_root": self.state_root.as_posix(),
        }
        return framed_sha256_v1(
            _REQUEST_DOMAIN, encode_canonical_ascii_v1(value),
        )


def _request_v1(
    state_root: PurePosixPath, uid: int, gid: int,
    distribution_sha256: str, *, account: PosixAccountSnapshotV1 | None,
    canonical: bool,
) -> LegacyStateRequestV1:
    binding = (state_root, uid, gid, distribution_sha256) if canonical else None
    return LegacyStateRequestV1(
        state_root, uid, gid, distribution_sha256,
        account, canonical, binding, _REQUEST_SEAL,
    )


def _canonical_state_path_v1(account: PosixAccountSnapshotV1) -> PurePosixPath:
    try:
        spec = build_host_layout_spec_v1(account)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _invalid("request_account") from exc
    matches = tuple(item for item in spec.objects if item.role is HostPathRoleV1.state)
    if len(matches) != 1:
        raise _invalid("request_layout")
    return matches[0].path


def build_legacy_state_request_v1(
    account: PosixAccountSnapshotV1, distribution_sha256: str,
) -> LegacyStateRequestV1:
    """Bind adoption to the canonical host state role and typed account."""
    path = _canonical_state_path_v1(account)
    try:
        record = account.record
        request = _request_v1(
            path, record.uid, record.gid, distribution_sha256,
            account=account, canonical=True,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise _invalid("request_account") from exc
    return require_canonical_legacy_state_request_v1(request)


def _build_legacy_state_request_for_test_v1(
    state_root: PurePosixPath, uid: int, gid: int, distribution_sha256: str,
) -> LegacyStateRequestV1:
    """Nominal isolated seam; production boundaries reject this request."""
    return _request_v1(
        state_root, uid, gid, distribution_sha256,
        account=None, canonical=False,
    )


def require_canonical_legacy_state_request_v1(
    request: LegacyStateRequestV1,
) -> LegacyStateRequestV1:
    """Re-derive the exact account-bound state role at every boundary."""
    if (
        type(request) is not LegacyStateRequestV1
        or request._seal is not _REQUEST_SEAL
        or request._canonical is not True
        or type(request._account) is not PosixAccountSnapshotV1
        or request._binding != (
            request.state_root, request.service_uid, request.service_gid,
            request.distribution_sha256,
        )
    ):
        raise _invalid("canonical_request")
    try:
        path = _canonical_state_path_v1(request._account)
        record = request._account.record
    except LegacyStateError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise _invalid("canonical_request") from exc
    if (
        request.state_root != path
        or (request.service_uid, request.service_gid) != (record.uid, record.gid)
    ):
        raise _invalid("canonical_request")
    return request


__all__ = [
    "LegacyStateError", "LegacyStateRequestV1",
    "build_legacy_state_request_v1", "is_legacy_state_utf8_path_v1",
    "require_canonical_legacy_state_request_v1",
]
