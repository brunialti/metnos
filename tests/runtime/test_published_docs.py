from __future__ import annotations

from pathlib import Path

import pytest

from published_docs import (
    PUBLICATION_ROOT,
    catalog,
    distribution_files,
    resolve_reference,
)


def _page(*, lang: str, canonical: str, alternates: dict[str, str] | None = None,
          robots: str = "index, follow", body: str = "Reference") -> str:
    links = "\n".join(
        f'<link rel="alternate" hreflang="{key}" href="{value}">'
        for key, value in (alternates or {}).items()
    )
    return f"""<!doctype html>
<html lang="{lang}"><head>
<meta charset="utf-8">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{canonical}">
{links}
</head><body><h1>{body}</h1></body></html>
"""


def test_live_publication_inventory_includes_every_indexable_html():
    documents = catalog()
    relative = {document.relative_path for document in documents}
    assert "it/architecture/agent_runtime.html" in relative
    assert "en/architecture/agent_runtime.html" in relative
    assert "security/index.html" in relative
    assert "it/Metnos_Architettura_Intro_v1.html" not in relative
    assert "en/Metnos_Architecture_Intro_v1.html" not in relative
    assert all(document.path.is_relative_to(PUBLICATION_ROOT)
               for document in documents)
    assert {document.lang for document in documents} == {"it", "en"}
    assert len({document.canonical_url for document in documents}) == len(documents)


def test_new_domain_document_is_discovered_without_a_registry_edit(tmp_path):
    domain = tmp_path / "it" / "domini"
    domain.mkdir(parents=True)
    page = domain / "file.html"
    page.write_text(_page(
        lang="it",
        canonical="https://metnos.com/it/domini/file",
        alternates={"it": "https://metnos.com/it/domini/file"},
        body="Dominio file ed esempi in linguaggio naturale",
    ), encoding="utf-8")

    documents = catalog(tmp_path)

    assert [document.relative_path for document in documents] == [
        "it/domini/file.html"
    ]
    assert documents[0].lang == "it"
    assert documents[0].source_id.startswith("public-file-")


def test_public_distribution_inventory_includes_non_html_assets(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "manual.css").write_text(
        "body { color: black; }", encoding="utf-8")

    assert [
        path.relative_to(tmp_path).as_posix()
        for path in distribution_files(tmp_path)
    ] == ["assets/manual.css"]


def test_translations_share_a_stable_concept_family(tmp_path):
    it_url = "https://metnos.com/it/domains/mail"
    en_url = "https://metnos.com/en/domains/mail"
    alternates = {"it": it_url, "en": en_url, "x-default": en_url}
    for lang, url in (("it", it_url), ("en", en_url)):
        directory = tmp_path / lang / "domains"
        directory.mkdir(parents=True)
        (directory / "mail.html").write_text(_page(
            lang=lang, canonical=url, alternates=alternates,
        ), encoding="utf-8")

    documents = catalog(tmp_path)

    assert len(documents) == 2
    assert len({document.concept_key for document in documents}) == 1


def test_public_reference_resolves_filename_path_and_canonical_url(tmp_path):
    it_url = "https://metnos.com/it/guide"
    en_url = "https://metnos.com/en/guide"
    alternates = {"it": it_url, "en": en_url, "x-default": en_url}
    for lang, url in (("it", it_url), ("en", en_url)):
        directory = tmp_path / lang
        directory.mkdir(parents=True)
        (directory / "Guide.html").write_text(_page(
            lang=lang, canonical=url, alternates=alternates,
        ), encoding="utf-8")
    documents = catalog(tmp_path)

    by_name = resolve_reference(
        "Cosa contiene guide.HTML?", lang="it", documents=documents)
    by_path = resolve_reference(
        "Read docs/en/Guide.html", lang="it", documents=documents)
    by_url = resolve_reference(en_url, lang="it", documents=documents)

    assert by_name is not None and by_name.relative_path == "it/Guide.html"
    assert by_path is not None and by_path.relative_path == "en/Guide.html"
    assert by_url is not None and by_url.relative_path == "en/Guide.html"


def test_public_reference_fails_closed_on_unknown_or_ambiguous_basename(
        tmp_path):
    for lang in ("it", "en"):
        directory = tmp_path / lang
        directory.mkdir(parents=True)
        url = f"https://metnos.com/{lang}/same"
        (directory / "same.html").write_text(_page(
            lang=lang, canonical=url,
            alternates={lang: url},
        ), encoding="utf-8")
    documents = catalog(tmp_path)

    assert resolve_reference(
        "same.html", lang="fr", documents=documents) is None
    assert resolve_reference(
        "private-notes.html", lang="it", documents=documents) is None
    assert resolve_reference(
        "same.html.bak", lang="it", documents=documents) is None


def test_public_reference_does_not_capture_a_noncanonical_file_path(tmp_path):
    url = "https://metnos.com/it/guide"
    directory = tmp_path / "it"
    directory.mkdir(parents=True)
    (directory / "Guide.html").write_text(_page(
        lang="it", canonical=url, alternates={"it": url},
    ), encoding="utf-8")
    documents = catalog(tmp_path)

    assert resolve_reference(
        "Leggi /tmp/Guide.html", lang="it", documents=documents) is None
    assert resolve_reference(
        r"Leggi C:\Users\guest\Guide.html", lang="it",
        documents=documents,
    ) is None
    assert resolve_reference(
        "Leggi docs/it/Guide.html", lang="it", documents=documents,
    ) is not None


def test_public_reference_fails_closed_when_two_documents_are_named(tmp_path):
    directory = tmp_path / "it"
    directory.mkdir(parents=True)
    for name in ("First.html", "MuchLongerSecond.html"):
        url = f"https://metnos.com/it/{Path(name).stem.casefold()}"
        (directory / name).write_text(_page(
            lang="it", canonical=url, alternates={"it": url},
        ), encoding="utf-8")
    documents = catalog(tmp_path)

    assert resolve_reference(
        "Confronta First.html e MuchLongerSecond.html",
        lang="it", documents=documents,
    ) is None
    assert resolve_reference(
        "Confronta docs/it/First.html e MuchLongerSecond.html",
        lang="it", documents=documents,
    ) is None


def test_noindex_page_is_deployed_but_not_admitted_as_tutor_evidence(tmp_path):
    page = tmp_path / "redirect.html"
    page.write_text(_page(
        lang="en",
        canonical="https://metnos.com/en/new-location",
        robots="noindex, follow",
    ), encoding="utf-8")

    assert catalog(tmp_path) == ()


def test_internal_document_is_rejected_in_every_environment(tmp_path):
    directory = tmp_path / "it" / "internal"
    directory.mkdir(parents=True)
    (directory / "design.html").write_text(_page(
        lang="it",
        canonical="https://metnos.com/it/internal/design",
        alternates={"it": "https://metnos.com/it/internal/design"},
    ), encoding="utf-8")

    with pytest.raises(ValueError, match="internal material"):
        catalog(tmp_path)


@pytest.mark.parametrize("canonical", [
    "https://example.com/it/domains/file",
    "http://metnos.com/it/domains/file",
    "https://metnos.com/it/domains/file?draft=1",
])
def test_publication_rejects_noncanonical_or_external_urls(tmp_path, canonical):
    (tmp_path / "bad.html").write_text(_page(
        lang="it", canonical=canonical,
    ), encoding="utf-8")

    with pytest.raises(ValueError, match="canonical https://metnos.com"):
        catalog(tmp_path)


def test_deploy_validates_the_same_public_inventory():
    deploy = (PUBLICATION_ROOT.parent / "deploy.sh").read_text(encoding="utf-8")
    validation = '"$PYTHON" runtime/published_docs.py validate'
    publication = "wrangler pages deploy docs"
    assert validation in deploy
    assert publication in deploy
    assert deploy.index(validation) < deploy.index(publication)


def test_tutor_compiler_uses_public_inventory_without_duplicate_declarations():
    from tutor.sources import _read_registry, build_knowledge_units

    assert _read_registry() == ()
    units = build_knowledge_units()
    references = {unit.source_ref.split("#", 1)[0] for unit in units}
    assert "docs/it/architecture/agent_runtime.html" in references
    assert "docs/en/architecture/agent_runtime.html" in references
    assert "docs/security/index.html" in references
    assert "docs/it/domains.html" in references
    assert "docs/en/domains.html" in references
    assert "docs/it/Metnos_Architettura_Intro_v1.html" not in references
