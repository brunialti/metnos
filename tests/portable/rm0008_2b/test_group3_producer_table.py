"""Provenance: fixed author per producer, kind derived from the manifest."""
from __future__ import annotations

import pytest

import executor_birth_producer_table_v1 as table
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_intent import _producer_capabilities_for_bootstrap
from manifest_inventory import ManifestOrigin


def test_the_table_covers_every_capability_and_nothing_else():
    declared = {
        (item.producer_id, item.operation)
        for item in _producer_capabilities_for_bootstrap()
    }
    assert set(table.PRODUCER_AUTHOR_V1) == declared
    assert len(declared) == 11
    assert all(
        isinstance(value, RevisionAuthor)
        for value in table.PRODUCER_AUTHOR_V1.values()
    )


def test_an_unknown_capability_is_a_defect_not_a_default():
    with pytest.raises(table.ProducerTableError) as error:
        table.producer_author_v1("stranger", "invent")
    assert error.value.args == ("birth_producer_capability_unknown",)


def test_the_author_is_the_one_the_producer_always_writes():
    assert table.producer_author_v1(
        "synt_multistage", "create_or_replay"
    ) is RevisionAuthor.MODEL
    assert table.producer_author_v1(
        "skills_cli", "skill_import_or_reactivation"
    ) is RevisionAuthor.IMPORTER
    assert table.producer_author_v1(
        "installer_phase3", "install"
    ) is RevisionAuthor.MAINTENANCE


@pytest.mark.parametrize("manifest,expected", [
    (ManifestOrigin.CORE, ExecutorOrigin.CORE),
    (ManifestOrigin.BUILTIN, ExecutorOrigin.BUILTIN),
    (ManifestOrigin.BUILTIN_SKILL, ExecutorOrigin.BUILTIN),
    (ManifestOrigin.USER, ExecutorOrigin.HUMAN),
    (ManifestOrigin.EXPLICIT, ExecutorOrigin.HUMAN),
    (ManifestOrigin.USER_SKILL, ExecutorOrigin.IMPORTED),
    (ManifestOrigin.LEGACY_IMPORT, ExecutorOrigin.IMPORTED),
])
def test_the_kind_comes_from_where_the_manifest_lives(manifest, expected):
    assert table.executor_origin_v1(manifest) is expected


def test_a_location_without_a_birth_is_refused():
    with pytest.raises(table.ProducerTableError) as error:
        table.executor_origin_v1(ManifestOrigin.RETIRED)
    assert error.value.args == ("birth_executor_origin_unavailable",)


def test_no_producer_can_choose_the_kind():
    import inspect

    # The only input of the derivation is where the manifest lives: no
    # producer, no caller and no document can add a second one.
    signature = inspect.signature(table.executor_origin_v1)
    assert list(signature.parameters) == ["manifest_origin"]
    body = inspect.getsource(table.executor_origin_v1)
    assert "predecessor" not in body and "origin=" not in body


def test_the_store_name_is_derived_and_shared_by_both_sides():
    """Installer and runtime need the same name, so it has one owner."""
    from install import birth_authority_provisioner as provisioner

    caps = [
        (item.producer_id, item.operation)
        for item in _producer_capabilities_for_bootstrap()
    ]
    names = {table.producer_store_name_v1(*item) for item in caps}
    assert len(names) == len(caps)
    assert all(
        name.startswith("p-") and len(name) == 66 for name in names
    )
    # The provisioner does not own a second implementation of it.
    assert provisioner.producer_store_name_v1 is table.producer_store_name_v1
