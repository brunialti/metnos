"""Safety boundaries for the development-only remote Atlas fixture manager.

The manager is deliberately absent from a public installation, so its tests
belong to the internal/release gate rather than the installed runtime suite.
"""

from scripts.manage_complex_query_fixture import _is_result_artifact


def test_query_result_artifacts_are_recognized() -> None:
    assert _is_result_artifact(
        r"Risultati_Metnos_a676fbbf17ab4acb\dati_estratti.xlsx"
    )


def test_fixture_source_is_not_a_result_artifact() -> None:
    assert not _is_result_artifact(r"Dati\Scadenze_Atlas.xlsx")
    assert not _is_result_artifact(r"Risultati_Metnos_fake.txt")
