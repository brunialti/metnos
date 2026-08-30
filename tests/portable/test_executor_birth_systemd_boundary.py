"""Static authority census for the G6-C administrative installer."""
from __future__ import annotations

from contract_boundary_guard import (
    BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
    BOUNDARY_APIS,
    _boundary_api_capabilities,
)


_WRITE_APIS = (
    "_install_group6_administrative_for_test_v1",
    "_install_locked_core_v1",
    "_open_parent_v1",
    "_publish_administrative_tree_v1",
    "install_group6_administrative_v1",
)


def test_every_systemd_installer_writer_is_a_closed_store_boundary() -> None:
    configured = BOUNDARY_APIS["executor_birth_systemd"]
    for api in _WRITE_APIS:
        assert configured[api] == ("store_write",)
        assert _boundary_api_capabilities(
            f"install.executor_birth_systemd.{api}",
        ) == ("store_write",)
        assert (
            f"install/executor_birth_systemd.py:{api}"
            in BIRTH_CLOSED_COORDINATOR_STORE_OWNERS
        )


def test_systemd_installer_read_helpers_do_not_gain_write_authority() -> None:
    configured = BOUNDARY_APIS["executor_birth_systemd"]
    for api in (
        "_capture_signed_file_v1",
        "_manifest_file_v1",
        "_require_descriptor_binding_v1",
        "_verify_installed_tree_v1",
    ):
        assert api not in configured
        assert _boundary_api_capabilities(
            f"install.executor_birth_systemd.{api}",
        ) == ()
