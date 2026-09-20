"""Static ownership boundary for the G6-B2 receiver."""
from __future__ import annotations

from contract_boundary_guard import (
    BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
    BOUNDARY_APIS,
    _boundary_api_capabilities,
)


_RECEIVER_WRITE_APIS = (
    "_copy_source_file_v1",
    "_create_private_directory_v1",
    "_create_source_directories_v1",
    "_ensure_child_directory_v1",
    "_open_received_tree_at_v1",
    "_receive_source_for_test_v1",
    "_receive_source_locked_core_v1",
    "_receive_source_v1",
    "_receive_source_with_product_session_v1",
    "_receive_source_with_test_session_v1",
    "_remove_owned_tree_at_v1",
    "_rename_no_replace_v1",
    "_seal_temporary_directories_v1",
    "_verify_received_tree_fd_v1",
    "_write_all_v1",
    "_write_descriptor_v1",
    "main",
)

_RECEIVER_NESTED_WRITE_SCOPES = {
    "copied_chunks": "_copy_source_file_v1.copied_chunks",
}


def test_every_receiver_write_alias_is_a_closed_store_boundary() -> None:
    configured = BOUNDARY_APIS["executor_birth_source_receiver"]
    for api in _RECEIVER_WRITE_APIS:
        assert configured[api] == ("store_write",)
        assert _boundary_api_capabilities(
            f"install.executor_birth_source_receiver.{api}",
        ) == ("store_write",)
        assert (
            f"install/executor_birth_source_receiver.py:{api}"
            in BIRTH_CLOSED_COORDINATOR_STORE_OWNERS
        )
    for api, scope in _RECEIVER_NESTED_WRITE_SCOPES.items():
        assert configured[api] == ("store_write",)
        assert (
            f"install/executor_birth_source_receiver.py:{scope}"
            in BIRTH_CLOSED_COORDINATOR_STORE_OWNERS
        )


def test_receiver_read_helpers_do_not_gain_write_authority() -> None:
    configured = BOUNDARY_APIS["executor_birth_source_receiver"]
    for api in (
        "_entry_map",
        "_expected_tree_v1",
        "_read_bounded_file_at_v1",
        "_scan_source_v1",
        "_service_account_snapshot_v1",
    ):
        assert api not in configured
        assert _boundary_api_capabilities(
            f"install.executor_birth_source_receiver.{api}",
        ) == ()
