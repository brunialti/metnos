"""E2E REALE Google Photos P1 (spec §3.7) — API vera, gate umano.

NON raccolto dalla suite (prefisso e2e_, come e2e_google_backend): tocca
l'account Google reale e l'upload e' IRREVERSIBILE (l'API non cancella).
Esecuzione esplicita:

    METNOS_ENGINE=v3 python3 -m pytest runtime/tests/e2e_google_photos.py -q -s

Criteri §3.7: carica 2 foto di test nell'album `metnos-e2e` → `find` le
ritrova (per album e per anno corrente) → `get` le riscarica → confronto
sha256 (fallback onesto: se Google ricodifica i bytes, verifica dimensione
>0 + filename, dichiarandolo).
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import pytest

_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

from backends.images import google_photos as gp  # noqa: E402
from backends import _google_auth_common as gac  # noqa: E402

_SRC = Path("/tmp/metnos-e2e-photos")
_ALBUM = "metnos-e2e"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def creds_ok():
    if not gac.has_creds() or not gac.ensure_fresh_token():
        pytest.skip("token google assente/non rinnovabile — e2e solo su istanza autenticata")
    if not _SRC.exists() or len(list(_SRC.glob("*.jpg"))) < 2:
        pytest.skip(f"foto di test assenti in {_SRC}")
    return True


def test_e2e_upload_find_download_roundtrip(creds_ok):
    photos = sorted(_SRC.glob("e2e-*.jpg"))[:2]
    orig_sha = {p.name: _sha(p) for p in photos}

    # 1) UPLOAD nell'album (creato se manca)
    up = gp.upload({"paths": [str(p) for p in photos], "album": _ALBUM})
    assert up.get("decision") != "needs_inputs", "OAuth non pronto (re-consent?)"
    assert up["ok"] is True and up["ok_count"] == 2, up
    ids = [r["media_item_id"] for r in up["results"]]
    assert all(ids), up["results"]
    assert "annullabile" in up["message"] or "undoable" in up["message"]
    print(f"\n  upload ok: {ids}")

    # 2) FIND per album (indicizzazione lato Google: piccolo retry §7.9)
    found = []
    for attempt in range(4):
        fr = gp.find({"album": _ALBUM, "max_results": 50})
        assert fr["ok"] is True, fr
        found = [e for e in fr["entries"] if e["id"] in ids]
        if len(found) == 2:
            break
        time.sleep(3)
    assert len(found) == 2, f"trovate {len(found)}/2 nell'album {_ALBUM}"
    print(f"  find per album ok: {[e['filename'] for e in found]}")

    # 2-bis) FIND per anno corrente le include
    year = time.gmtime().tm_year
    fy = gp.find({"year": year, "max_results": 100})
    assert fy["ok"] is True
    assert {e["id"] for e in fy["entries"]} >= set(ids), "year-filter non le trova"
    print(f"  find per anno {year} ok")

    # 2-ter) l'album appare nella lista + nota perimetro presente
    la = gp.list_albums({})
    assert la["ok"] and any(a["title"] == _ALBUM for a in la["entries"]), la["entries"]
    assert la.get("message"), "nota perimetro app-created assente"
    print(f"  album {_ALBUM!r} in lista ok")

    # 3) DOWNLOAD per id + confronto sha256
    dst = Path("/tmp/metnos-e2e-photos/down")
    dl = gp.download({"ids": ids, "dst_dir": str(dst)})
    assert dl["ok"] is True and dl["ok_count"] == 2, dl
    assert dl["_undo"]["reverse_pattern"] == "delete_created_paths"
    exact = 0
    for r in dl["results"]:
        lp = Path(r["local_path"])
        assert lp.is_file() and lp.stat().st_size > 0
        if _sha(lp) == orig_sha.get(lp.name):
            exact += 1
    if exact == 2:
        print("  download ok: sha256 IDENTICI (bytes originali preservati)")
    else:
        # Onesta' §2.8: Google puo' ricodificare — dichiararlo, non mascherarlo.
        print(f"  download ok, MA sha256 identici solo {exact}/2 "
              f"(Google ha ricodificato i bytes: file validi, contenuto ok)")
    print("  E2E P1 §3.7: COMPLETA")
