"""Tutor retains temporal clarification and mail-boundary conditions together."""
import pytest

from published_docs import catalog
from tutor.sources import _document_units


@pytest.mark.parametrize("language,heading,required", [
    ("it", "Date e periodi", ("sistema comune", "fuso orario", "form", "non è un permesso", "resta fissa")),
    ("en", "Dates and periods", ("shared system", "timezone", "form", "not an additional permission", "stays fixed")),
    ("it", "Quale data", ("ricezione", "ripiega", "esclude", "non cambia", "limiti visibili")),
    ("en", "Which date", ("receipt", "falls back", "excludes", "unchanged", "visible result limits")),
])
def test_temporal_guarantees_and_limits_survive_compilation(language, heading, required):
    document = next(item for item in catalog()
                    if item.relative_path == f"{language}/architecture/mail_accounts.html")
    units = [unit for unit in _document_units(
        source_id=document.source_id, lang=document.lang, path=document.path,
        audience="user", source_kind="manual", priority=80,
        concept_prefix=f"public-doc-{document.concept_key}", public_url=document.canonical_url,
    ) if unit.title.startswith(heading)]
    assert len(units) == 1
    assert all(phrase in units[0].text for phrase in required)
    assert units[0].visible_to("user") and units[0].lang == language
