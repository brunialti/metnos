"""Share only a bounded administrative unit's cgroup with its Birth service.

Used by release preparation and independent review. It transfers the same
systemd delegation as a User= service, never a host or unrelated cgroup.
"""
from contextlib import ExitStack
import os
import re

READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def require(condition, detail):
    if not condition:
        raise RuntimeError(detail)


def delegate_service_checks(descriptor, *, unit_prefix):
    import executor_birth_runner as runner

    require(os.geteuid() == 0 and descriptor.service_uid > 0
            and descriptor.service_gid > 0, "administrative Birth identity invalid")
    current = runner._current_unified_cgroup()
    require(current is not None and current.name == runner._CGROUP_HOST_SUBGROUP
            and re.fullmatch(re.escape(unit_prefix) + r"[0-9a-f]{16}\.service",
                             current.parent.name), "administrative Birth delegate missing")
    delegate = runner._CGROUP_V2_MOUNT.joinpath(*current.parent.parts[1:])
    with ExitStack() as handles:
        parent_fd = os.open(delegate, READ | os.O_DIRECTORY)
        handles.callback(os.close, parent_fd)
        require(os.getxattr(parent_fd, "user.delegate") == b"1",
                "administrative Birth cgroup is not delegated by systemd")
        subgroup_fd = os.open(current.name, READ | os.O_DIRECTORY, dir_fd=parent_fd)
        handles.callback(os.close, subgroup_fd)
        descriptors = [parent_fd, subgroup_fd]
        for directory_fd in (parent_fd, subgroup_fd):
            for name in ("cgroup.procs", "cgroup.threads", "cgroup.subtree_control"):
                fd = os.open(name, READ, dir_fd=directory_fd)
                handles.callback(os.close, fd)
                descriptors.append(fd)
        require(all(os.fstat(fd).st_uid == 0 for fd in descriptors),
                "administrative Birth cgroup ownership changed")
        for fd in descriptors:
            os.fchown(fd, descriptor.service_uid, descriptor.service_gid)
    # Verify the real runner's precondition under the same temporary identity
    # used by the coordinator. Fail before any service is stopped.
    from install.birth_authority_provisioner import _service_owned_birth_identity_v2
    with _service_owned_birth_identity_v2(descriptor):
        observed, error = runner._cgroup_v2_delegate()
        require(observed == delegate and error is None,
                "administrative Birth native runner unavailable: " + str(error))
