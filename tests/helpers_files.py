"""Īsti PDF/DOCX/XLSX faili testiem.

Bibliotēku failu ģenerēšanai projektā nav, un `b"%PDF fake"` neko nepārbauda:
tieši parsēšana ir tā vieta, kur pielikumu lasīšana var klusi salūzt.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_pdf(lines: list[str]) -> bytes:
    """Viena lapa ar tekstu. ASCII, jo saturs iet PDF straumē kā latin-1."""
    drawn = "\n".join(
        f"BT /F1 12 Tf 72 {720 - 20 * i} Td ({line}) Tj ET" for i, line in enumerate(lines)
    )
    stream = drawn.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start,
    )
    return bytes(out)


def make_empty_pdf() -> bytes:
    """Lapa bez teksta — tā izskatās skenēts rasējums."""
    return make_pdf([]).replace(b"BT", b"  ")


def make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    for row in table or []:
        cells = "".join(
            f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row
        )
        body += f"<w:tr>{cells}</w:tr>"
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{_WORD_NS}"><w:body>{body}</w:body></w:document>'
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def make_xlsx(rows: list[list[object]], sheet_name: str = "Specifikācija") -> bytes:
    shared: list[str] = []

    def shared_index(value: str) -> int:
        if value not in shared:
            shared.append(value)
        return shared.index(value)

    xml_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for column, value in enumerate(row):
            ref = f"{chr(ord('A') + column)}{row_number}"
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{shared_index(str(value))}</v></c>')
        xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    sheet = (
        f'<worksheet xmlns="{_SHEET_NS}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )
    strings = "".join(f"<si><t>{value}</t></si>" for value in shared)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{_SHEET_NS}" xmlns:r="{_DOC_NS}"><sheets>'
            f'<sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="{_PKG_NS}"><Relationship Id="rId1" '
            f'Type="{_DOC_NS}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{_SHEET_NS}" count="{len(shared)}">{strings}</sst>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()
