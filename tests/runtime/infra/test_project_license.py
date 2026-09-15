"""Keep first-party license declarations consistent across public surfaces."""

from pathlib import Path
import tomllib

import pytest
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[3]


def test_root_license_is_mit_without_trailing_legacy_terms():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n\nCopyright (c) 2026 Roberto Brunialti\n")
    assert "Permission is hereby granted, free of charge" in license_text
    assert "The above copyright notice and this permission notice" in license_text
    assert license_text.endswith("SOFTWARE.\n")
    assert "GNU" not in license_text
    assert "Affero" not in license_text
    assert "AGPL" not in license_text


@pytest.mark.parametrize("project", ("client-rs", "helper-rs"))
def test_first_party_client_package_uses_mit(project):
    package = tomllib.loads((ROOT / project / "Cargo.toml").read_text())
    assert package["package"]["license"] == "MIT"


@pytest.mark.parametrize("language", ("it", "en"))
def test_user_documentation_and_pdf_use_current_license(language):
    for name in ("index.html", "code.html", "Metnos_QuickTour.html"):
        content = (ROOT / "docs" / language / name).read_text(encoding="utf-8")
        assert "MIT" in content
        assert "AGPL" not in content
    reader = PdfReader(ROOT / "docs" / language / "Metnos_QuickTour.pdf")
    content = "\n".join(page.extract_text() for page in reader.pages)
    # PDF glyph positioning may insert spaces inside words (for example M IT).
    compact = "".join(content.split())
    assert {"it": "codiceMIT", "en": "MITcode"}[language] in compact
    assert "AGPL" not in compact


def test_installer_languages_and_readme_keep_license_scope_explicit():
    from install import disclaimer

    for language in ("it", "en"):
        assert "MIT" in disclaimer._TEXT[language]
        assert "AGPL" not in disclaimer._TEXT[language]
    for relative in ("install/bootstrap.sh", "install/i18n.py"):
        content = (ROOT / relative).read_text(encoding="utf-8")
        assert "MIT" in content
        assert "AGPL" not in content
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[MIT](LICENSE)" in readme
    assert "Third-party libraries, companion services and downloaded models" in readme
    assert "AGPL" not in readme
