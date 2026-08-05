#!/usr/bin/env python3
"""Create, verify, or remove the Windows fixture for complex query tests.

The fixture is created through Metnos' real remote-executor pipeline on
PC-ROBERTO.  Cleanup is deliberately conservative: it proceeds only when the
marker matches and the directory contains exactly the files generated here.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import sys
import uuid
import zipfile
from pathlib import Path, PureWindowsPath


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import agent_runtime  # noqa: E402
import devices  # noqa: E402
import placement  # noqa: E402
from loader import load_catalog  # noqa: E402


FIXTURE_ID = "metnos-complex-query-v1-20260720"
DEVICE_NAME = "PC-ROBERTO"
ROOT = PureWindowsPath(
    r"C:\Users\rober\.local\share\metnos\Documenti\Progetto Atlas"
)
MARKER_NAME = "00_METNOS_FIXTURE.json"
RESULT_DIR_PREFIX = "Risultati_Metnos_"


def _is_result_artifact(relative: str) -> bool:
    """True only for files below a query-created result directory."""
    parts = PureWindowsPath(relative).parts
    return bool(len(parts) >= 2 and parts[0].startswith(RESULT_DIR_PREFIX))


def _pdf(lines: list[str]) -> bytes:
    """Build a small, valid, uncompressed PDF using only the stdlib."""
    escaped = [
        line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        for line in lines
    ]
    commands = ["BT", "/F1 12 Tf", "72 760 Td", "14 TL"]
    for index, line in enumerate(escaped):
        if index:
            commands.append("T*")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = ("\n".join(commands) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"endstream",
    ]
    out = bytearray(b"%PDF-1.4\n%MetnosFixture\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
         f"startxref\n{xref}\n%%EOF\n").encode("ascii")
    )
    return bytes(out)


def _docx(paragraphs: list[str]) -> bytes:
    from xml.sax.saxutils import escape

    body = "".join(
        f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
        for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>' + body
        + '<w:sectPr/></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()


def _xlsx() -> bytes:
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover - host dependency guard
        raise SystemExit("openpyxl is required to build the fixture") from exc

    wb = Workbook()
    ws = wb.active
    ws.title = "Scadenze"
    ws.append(["Attivita", "Responsabile", "Scadenza", "Importo EUR", "Stato"])
    ws.append(["Analisi requisiti", "Luca Neri", "2026-08-05", 15000, "completata"])
    ws.append(["Prototipo", "Giulia Ferri", "2026-09-15", 45000, "a rischio"])
    ws.append(["Consegna", "Roberto B.", "2026-10-15", 75000, "pianificata"])
    ws.append(["TOTALE", "", "", 135000, "previsione"])
    wb.properties.title = "Progetto Atlas - scadenze e costi"
    wb.properties.subject = "Fixture Metnos per estrazione strutturata"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv_bytes() -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(["id", "data", "decisione", "responsabile", "stato"])
    writer.writerow(["D-001", "2026-07-15", "Budget approvato EUR 120000", "Roberto B.", "approvata"])
    writer.writerow(["D-002", "2026-07-15", "Fornitore selezionato: Orion", "Roberto B.", "approvata"])
    writer.writerow(["D-003", "2026-07-18", "Anticipo consegna al 2026-09-15", "Giulia Ferri", "proposta"])
    return buf.getvalue().encode("utf-8")


def fixture_files() -> dict[str, bytes]:
    approved = _pdf([
        "PROGETTO ATLAS - BUDGET APPROVATO",
        "Data decisione: 2026-07-15",
        "Responsabile: Roberto B.",
        "Scadenza contrattuale: 2026-09-30",
        "Importo approvato: EUR 120000",
        "Fornitore selezionato: Orion",
        "Stato: APPROVATO",
    ])
    revision = _docx([
        "Progetto Atlas - revisione operativa",
        "Data revisione: 2026-07-18",
        "Responsabile proposta: Giulia Ferri",
        "Nuova scadenza proposta: 2026-09-15",
        "Previsione aggiornata: EUR 135000",
        "Fornitore alternativo proposto: Vega",
        "Stato: BOZZA NON APPROVATA",
        "Questi valori contraddicono il documento approvato e richiedono verifica.",
    ])
    payloads = {
        r"Contratti\Budget_Atlas_approvato.pdf": approved,
        r"Contratti\Budget_Atlas_approvato_COPIA.pdf": approved,
        r"Contratti\Budget_Atlas_revisione.docx": revision,
        r"Dati\Scadenze_Atlas.xlsx": _xlsx(),
        r"Dati\Decisioni_Atlas.csv": _csv_bytes(),
        r"Dati\Allegato_corrotto.pdf": b"%PDF-1.4\nfixture intentionally truncated and unreadable\n",
        r"Note\Contesto_escluso_dalla_query.txt": (
            b"Questo TXT verifica che i pattern PDF/DOCX/XLSX/CSV lo escludano.\n"
        ),
    }
    marker = {
        "fixture_id": FIXTURE_ID,
        "device": DEVICE_NAME,
        "root": str(ROOT),
        "purpose": "complex multi-domain query test",
        "expected_payload_files": sorted(payloads),
        "approved_pdf_sha256": hashlib.sha256(approved).hexdigest(),
        "duplicate_pdf_sha256": hashlib.sha256(approved).hexdigest(),
    }
    payloads[MARKER_NAME] = json.dumps(
        marker, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")
    return payloads


class RemoteFixture:
    def __init__(self) -> None:
        self.catalog = {executor.name: executor for executor in load_catalog()}
        matches = [d for d in devices.list_devices() if d.name == DEVICE_NAME]
        if len(matches) != 1:
            raise SystemExit(f"expected one device named {DEVICE_NAME}, found {len(matches)}")
        self.device = matches[0]
        if not placement.is_available(self.device):
            raise SystemExit(f"device {DEVICE_NAME} is not currently available")

    def invoke(self, executor: str, args: dict) -> dict:
        ex = self.catalog.get(executor)
        if ex is None:
            raise SystemExit(f"executor {executor!r} is not in the signed catalog")
        result = agent_runtime.invoke_executor(
            ex,
            args,
            timeout_s=60,
            turn_id=f"fixture-{uuid.uuid4().hex[:12]}",
            actor="host",
            channel="e2e-fixture",
            target_device=DEVICE_NAME,
        )
        if result.get("_ran_on_device") != DEVICE_NAME:
            raise SystemExit(f"{executor} did not run on {DEVICE_NAME}: {result}")
        return result

    def listing(self) -> dict:
        return self.invoke(
            "find_files",
            {
                "base_path": str(ROOT),
                "patterns": ["*"],
                "recursive": True,
                "max_results": 100,
                "client": "local",
            },
        )

    def create(self) -> None:
        existing = self.listing()
        if existing.get("ok"):
            raise SystemExit(f"refusing to reuse existing directory {ROOT}")
        if existing.get("error_code") != "ERR_PATH_NOT_FOUND":
            raise SystemExit(f"fixture preflight failed unexpectedly: {existing}")

        directories = [
            str(ROOT),
            str(ROOT / "Contratti"),
            str(ROOT / "Dati"),
            str(ROOT / "Note"),
        ]
        made = self.invoke(
            "create_dirs",
            {"paths": directories, "parents": True, "exist_ok": False, "client": "local"},
        )
        if not made.get("ok") or made.get("fail_count"):
            raise SystemExit(f"directory creation failed: {made}")

        files = fixture_files()
        request_files = [
            {
                "path": str(ROOT / relative),
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "binary",
                "mode": "fail_if_exists",
            }
            for relative, content in files.items()
        ]
        written = self.invoke("write_files", {"files": request_files, "client": "local"})
        if not written.get("ok") or written.get("fail_count"):
            raise SystemExit(f"file creation failed: {written}")
        self.verify()
        print(json.dumps({
            "ok": True,
            "action": "created",
            "device": DEVICE_NAME,
            "root": str(ROOT),
            "files": len(files),
            "cleanup": f"{Path(__file__).name} delete",
        }, ensure_ascii=False))

    def _marker(self) -> dict:
        result = self.invoke(
            "read_files",
            {"path": str(ROOT / MARKER_NAME), "encoding": "utf-8", "client": "local"},
        )
        if not result.get("ok"):
            raise SystemExit(f"fixture marker is unreadable: {result}")
        content = result.get("content")
        if content is None and result.get("entries"):
            content = result["entries"][0].get("content")
        try:
            marker = json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SystemExit("fixture marker is not valid JSON") from exc
        if marker.get("fixture_id") != FIXTURE_ID or marker.get("root") != str(ROOT):
            raise SystemExit("fixture marker does not match this script; refusing operation")
        return marker

    def verify(self, *, allow_results: bool = True) -> None:
        self._marker()
        listing = self.listing()
        if not listing.get("ok"):
            raise SystemExit(f"fixture listing failed: {listing}")
        actual_all = {
            str(PureWindowsPath(item["path"]).relative_to(ROOT))
            for item in listing.get("entries", [])
            if item.get("path")
        }
        result_artifacts = {
            relative for relative in actual_all
            if _is_result_artifact(relative)
        }
        actual = actual_all - result_artifacts
        expected = set(fixture_files())
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if not allow_results:
            unexpected.extend(sorted(result_artifacts))
        if missing or unexpected:
            raise SystemExit(
                f"fixture mismatch; missing={missing}, unexpected={unexpected}"
            )
        pdf_a = fixture_files()[r"Contratti\Budget_Atlas_approvato.pdf"]
        pdf_b = fixture_files()[r"Contratti\Budget_Atlas_approvato_COPIA.pdf"]
        if hashlib.sha256(pdf_a).digest() != hashlib.sha256(pdf_b).digest():
            raise SystemExit("fixture generator no longer creates the expected duplicate")
        print(json.dumps({
            "ok": True,
            "action": "verified",
            "device": DEVICE_NAME,
            "root": str(ROOT),
            "files": len(actual),
            "preserved_result_artifacts": len(result_artifacts),
            "duplicate_sha256": hashlib.sha256(pdf_a).hexdigest(),
        }, ensure_ascii=False))

    def delete(self) -> None:
        # Cleanup stays fail-closed: query outputs are user data, even when
        # their directory follows the standard result prefix.
        self.verify(allow_results=False)
        removed = self.invoke(
            "delete_dirs",
            {"paths": [str(ROOT)], "force": True, "client": "local"},
        )
        if not removed.get("ok") or removed.get("fail_count"):
            raise SystemExit(f"fixture cleanup failed: {removed}")
        gone = self.listing()
        if gone.get("error_code") != "ERR_PATH_NOT_FOUND":
            raise SystemExit(f"fixture directory still exists after cleanup: {gone}")
        print(json.dumps({
            "ok": True,
            "action": "deleted",
            "device": DEVICE_NAME,
            "root": str(ROOT),
        }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify", "delete"))
    args = parser.parse_args()
    manager = RemoteFixture()
    getattr(manager, args.action)()


if __name__ == "__main__":
    main()
