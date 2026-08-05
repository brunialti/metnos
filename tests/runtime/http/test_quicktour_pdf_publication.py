from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_quicktour_print_master_and_public_pdf_are_wired_for_both_languages():
    for language in ("it", "en"):
        html = ROOT / "docs" / language / "Metnos_QuickTour.html"
        pdf = html.with_suffix(".pdf")
        landing = ROOT / "docs" / language / "index.html"

        source = html.read_text(encoding="utf-8")
        landing_source = landing.read_text(encoding="utf-8")
        assert '../assets/quicktour-print.css" media="print"' in source
        assert '../assets/metnos-quicktour-cover.png"' in source
        assert 'href="Metnos_QuickTour.pdf"' in landing_source
        assert "Metnos_QuickTour_v1" not in source
        assert "Metnos_QuickTour_v1" not in landing_source
        assert not html.with_name("Metnos_QuickTour_v1.html").exists()
        assert not pdf.with_name("Metnos_QuickTour_v1.pdf").exists()
        assert pdf.read_bytes().startswith(b"%PDF-")

    assert (ROOT / "docs" / "assets" / "quicktour-print.css").is_file()
    assert (ROOT / "docs" / "assets" / "metnos-quicktour-cover.png").is_file()
    assert (ROOT / "scripts" / "build_quick_tour_pdf.py").is_file()

    redirects = (ROOT / "docs" / "_redirects").read_text(encoding="utf-8")
    assert "/it/Metnos_QuickTour_v1.html        /it/Metnos_QuickTour.html 301" in redirects
    assert "/en/Metnos_QuickTour_v1.html        /en/Metnos_QuickTour.html 301" in redirects
