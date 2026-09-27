"""Native isolation facts cannot activate F5 without a signed permit."""
from dataclasses import replace
import sys

import pytest

import executor_birth_rehearsal_isolation as isolation


def _mount(root, target="/", *, device="8:1", filesystem="ext4", options=("rw",)):
    return isolation._Mount(device, root, target, options, filesystem)


HOST = (_mount("/"), _mount("/", "/run", device="0:1", filesystem="tmpfs"))
GUEST = (_mount("/machines/isolated"), _mount("/", "/run", device="0:2", filesystem="tmpfs"))


def test_private_root_and_public_supervisor_metadata_are_accepted():
    isolation._validate_mounts(GUEST, HOST)
    isolation._validate_mounts((*GUEST, _mount(
        "/usr/lib/os-release", "/run/host/os-release", options=("ro", "nosuid"),
    )), HOST)


@pytest.mark.parametrize("mount", [
    _mount("/var/lib/production", "/data"),
    _mount("/var/lib/production", "/data", options=("ro",)),
    _mount("/machines/isolated-other", "/data"),
    _mount("/usr/lib/os-release", "/run/host/os-release"),
    _mount("/etc/secret", "/run/host/os-release", options=("ro",)),
    _mount("/", "/host-run", device="0:1", filesystem="tmpfs"),
    _mount("/", "/unknown", device="0:4", filesystem="fuse.unknown"),
])
def test_host_data_cannot_be_smuggled_into_the_guest(mount):
    with pytest.raises(isolation.RehearsalIsolationError):
        isolation._validate_mounts((*GUEST, mount), HOST)


def test_relabelled_host_root_is_refused():
    with pytest.raises(isolation.RehearsalIsolationError, match="host root shared"):
        isolation._validate_mounts(HOST, HOST)


def test_shared_process_filesystem_is_refused_despite_a_private_pid_namespace():
    proc = _mount("/", "/proc", device="0:9", filesystem="proc")
    with pytest.raises(isolation.RehearsalIsolationError, match="virtual filesystem shared"):
        isolation._validate_mounts((*GUEST, proc), (*HOST, proc))


@pytest.mark.parametrize("status", [b"NSpid:\t9000\t12\n", b"NSpid:\t9000\n", b"",
                                   b"NSpid:\t12\nNSpid:\t12\n"])
def test_procfs_must_show_only_the_callers_local_pid(status):
    with pytest.raises(isolation.RehearsalIsolationError, match="process filesystem shared"):
        isolation._validate_proc(status, (), "0:9", 12)


def test_private_procfs_cannot_be_accompanied_by_another_procfs():
    proc = _mount("/", "/proc", device="0:9", filesystem="proc")
    isolation._validate_proc(b"NSpid:\t12\n", (proc,), "0:9", 12)
    host = _mount("/", "/host-proc", device="0:10", filesystem="proc")
    with pytest.raises(isolation.RehearsalIsolationError, match="additional process filesystem"):
        isolation._validate_proc(b"NSpid:\t12\n", (proc, host), "0:9", 12)


def test_shared_control_groups_must_be_scoped_to_the_observed_guest():
    cgroup = _mount("/machine/payload", "/sys/fs/cgroup", device="0:9", filesystem="cgroup2")
    isolation._validate_mounts((*GUEST, cgroup), (*HOST, cgroup), cgroup="/machine/payload")
    for observed in (None, "/", "/machine/another-payload"):
        with pytest.raises(isolation.RehearsalIsolationError, match="control groups shared"):
            isolation._validate_mounts((*GUEST, cgroup), (*HOST, cgroup), cgroup=observed)


def test_native_inaccessible_cover_hides_the_original_mount():
    shared = _mount("/incoming", "/host/incoming", device="0:1", filesystem="tmpfs", options=("ro",))
    isolation._validate_mounts((*GUEST, shared), HOST, inaccessible=frozenset({"/host/incoming"}))
    with pytest.raises(isolation.RehearsalIsolationError):
        isolation._validate_mounts((*GUEST, shared), HOST, inaccessible=frozenset({"/host/inc"}))


@pytest.mark.parametrize("raw", [
    b"0 0 4294967295\n", b"0 1000 65536\n", b"1 65536 65536\n",
    b"0 65536 65536\n1 0 1\n", b"0 65536 1\n", b"0 4294967290 65536\n",
    b"0 not-an-id 65536\n", b"", b"0 65536 65536 extra\n",
])
def test_identity_maps_must_exclude_host_system_accounts(raw):
    with pytest.raises(isolation.RehearsalIsolationError, match="mapping"):
        isolation._mapping(raw)


def test_kernel_mount_path_escaping_and_component_boundary():
    mounts = isolation._mounts(b"12 1 8:1 /machines/lab\\040one / rw shared:2 - ext4 /dev/sda rw\n")
    assert mounts[0].root == "/machines/lab one"
    isolation._validate_mounts(mounts, HOST)
    with pytest.raises(isolation.RehearsalIsolationError):
        isolation._validate_mounts((*mounts, _mount("/machines/lab one-backup", "/data")), HOST)


def _facts():
    return isolation.RehearsalIsolationV1(
        "00000000-0000-0000-0000-000000000001",
        tuple((name, 1, index + 100) for index, name in enumerate(isolation._NAMESPACES)),
        (0, 65536, 65536), (0, 65536, 65536), (8, 1000, 65536, 65536),
        "sha256:" + "a" * 64,
    )


def test_observation_identity_carries_native_lifetime_and_mounts():
    observed = _facts()
    assert observed.observation_id == _facts().observation_id
    assert observed.observation_id != replace(observed, mounts_hash="sha256:" + "b" * 64).observation_id
    assert observed.observation_id != replace(observed, boot_id="00000000-0000-0000-0000-000000000002").observation_id
    from executor_birth_lifecycle import LifecycleCoordinator, LifecycleError
    with pytest.raises(LifecycleError, match="f5_activation_required"):
        LifecycleCoordinator(observed, db_path=None, publish_and_reread=None, verify_admission=None)



def test_unsupported_platform_cannot_claim_isolation(monkeypatch):
    monkeypatch.setattr(isolation.sys, "platform", "win32")
    with pytest.raises(isolation.RehearsalIsolationError, match="unsupported"):
        isolation.observe_local_rehearsal_v1()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux kernel observer")
def test_unreadable_kernel_facts_refuse(monkeypatch):
    def unreadable(*args):
        raise PermissionError("kernel observation")
    monkeypatch.setattr(isolation, "_read", unreadable)
    with pytest.raises(isolation.RehearsalIsolationError, match="kernel observation unavailable"):
        isolation.observe_local_rehearsal_v1()
