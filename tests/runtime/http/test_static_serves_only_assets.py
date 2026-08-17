"""La route `/static/` pubblica asset web, non qualunque file (17/8/2026).

`/static/` e' anonima per costruzione (esente in `http_auth`), quindi la
cartella pubblica cio' che contiene. Finche' il tipo sconosciuto ricadeva su
`application/octet-stream`, un documento lasciato li' dentro era scaricabile
da chiunque: e' successo con un mandato di progettazione di 365 righe, servito
in chiaro sulla porta 8770.

Il rimedio e' la CAUSA, non il file: `_STATIC_CT` e' l'elenco dei tipi
ammessi, e cio' che non e' un asset web risponde 404.

Run: `python3 -m pytest tests/runtime/http/test_static_serves_only_assets.py -v`
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


@pytest.fixture
def static_dir(monkeypatch, tmp_path):
    """Sostituisce la cartella degli asset con una temporanea."""
    import http_routes_agent as H
    monkeypatch.setattr(H, "_STATIC_DIR", tmp_path)
    return tmp_path


def _serve(name: str):
    import http_routes_agent as H
    return H._static_response(name)


# ── I tipi ammessi vengono serviti ────────────────────────────────────
@pytest.mark.parametrize("name,content_type", [
    ("icon.png", "image/png"),
    ("app.js", "application/javascript"),
    ("style.css", "text/css"),
    ("page.html", "text/html"),
    ("manifest.webmanifest", "application/manifest+json"),
])
def test_un_asset_web_viene_servito(static_dir, name, content_type):
    (static_dir / name).write_bytes(b"asset")
    res = _serve(name)
    assert res.status == 200
    assert res.content_type == content_type


# ── Tutto il resto no ─────────────────────────────────────────────────
@pytest.mark.parametrize("name", [
    "mandato.md",              # il caso reale: un documento di progettazione
    "note.markdown",
    "appunti.pdf",
    "dati.sqlite",
    "chiavi.pem",
    "backup.zip",
    "config.yaml",
    "config.toml",
    "script.sh",
    "modulo.py",
    "registro.log",
    "senza_estensione",
])
def test_un_documento_non_e_un_asset(static_dir, name):
    (static_dir / name).write_bytes(b"contenuto interno")
    res = _serve(name)
    assert res.status == 404, f"{name} non deve essere pubblicato"


def test_il_contenuto_non_trapela_nel_corpo(static_dir):
    """404 vuol dire 404: il corpo non contiene il documento."""
    segreto = b"# Mandato interno: architettura /opt/metnos"
    (static_dir / "mandato.md").write_bytes(segreto)
    res = _serve("mandato.md")
    assert res.status == 404
    assert segreto not in (res.body or b"")


# ── Le difese preesistenti restano ────────────────────────────────────
@pytest.mark.parametrize("name", [
    "../secrets.json",         # uscita dalla cartella, tipo ammesso
    "../../runtime/config.py",
])
def test_la_traversata_resta_chiusa(static_dir, name):
    res = _serve(name)
    assert res.status == 404


def test_un_file_assente_resta_404(static_dir):
    assert _serve("mai_esistito.png").status == 404


def test_una_cartella_non_e_un_file(static_dir):
    (static_dir / "sottocartella.png").mkdir()
    assert _serve("sottocartella.png").status == 404


# ── La cartella reale contiene solo asset ─────────────────────────────
def test_la_cartella_reale_contiene_solo_asset():
    """Guardia anti-deriva: se qualcuno lascia un documento in
    `runtime/static/`, questo test lo dice prima che lo dica un estraneo."""
    import http_routes_agent as H
    ammessi = set(H._STATIC_CT)
    intrusi = [p.name for p in Path(H._STATIC_DIR).iterdir()
               if p.is_file() and p.suffix.lower() not in ammessi]
    assert not intrusi, (
        f"file non-asset in runtime/static/: {intrusi}. "
        "Un documento va in internal/design/, non nella cartella pubblicata.")
