"""Pielikumi: MIME daļa -> teksts, ko var padot modelim.

Rasējums, specifikācija Excel failā vai tehniskais apraksts PDF formātā ir
puse pieprasījuma. Kamēr aģents tos neatvēra, piedāvājums tika būvēts uz
otras puses un izskatījās pēc pilnas atbildes.

Nolasām TEKSTU. Skenēts rasējums teksta nesatur, un tāds pielikums paliek
atzīmēts kā neizlasīts: godīgs "atver ar roku" ir labāks par tukšu lapu, uz
kuras modelis neko neredz un tāpēc klusē.

Šeit dzīvo arī divi MIME palīgi (`decode_header_value`, `strip_html`), ko
lieto arī `message.py` — tas ir slānis virsū šim.
"""

from __future__ import annotations

import html
import re
import zipfile
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from io import BytesIO
from xml.etree import ElementTree

from ..config import (
    MAIL_ATTACHMENTS_TEXT_LIMIT,
    MAIL_ATTACHMENT_MAX_BYTES,
    MAIL_ATTACHMENT_PDF_PAGES,
    MAIL_ATTACHMENT_TEXT_LIMIT,
)

try:  # pragma: no cover — atkarība ir `pyproject.toml`, bet trūkums nedrīkst
    from pypdf import PdfReader  # nogāzt visu pastkastītes gājienu
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment]

#: Kodējumi, ar kuriem mēģinām teksta pielikumu, ja galvenē kodējuma nav.
#: `cp1257` ir tas, kurā Latvijā joprojām nāk vecās `.txt` un `.csv` izdrukas.
_FALLBACK_CHARSETS = ("utf-8", "cp1257", "cp1251", "latin-1")

#: Cik rindu no vienas Excel lapas vispār salasām. Zīmju limits nogrieztu tāpat,
#: bet miljons tukšu rindu ir jāizlasa pirms griešanas.
_MAX_SHEET_ROWS = 500

_XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DOC_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass(slots=True)
class Attachment:
    """Viens pielikums: nosaukums un tas, ko no tā izdevās izlasīt."""

    name: str
    content_type: str = ""
    size: int = 0
    #: Izvilktais teksts. Tukšs = neizlasīts, iemesls ir `note`.
    text: str = ""
    #: Kāpēc teksta nav. Cilvēkam lasāms, aiziet menedžerim iekšējā blokā.
    note: str = ""

    @property
    def read(self) -> bool:
        return bool(self.text.strip())


# --- MIME palīgi -----------------------------------------------------------
def decode_header_value(value: str | None) -> str:
    """`=?utf-8?B?...?=` -> teksts. Bojāta galvene nedrīkst mest izņēmumu."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return value.strip()


def strip_html(html_text: str) -> str:
    """Ļoti vienkāršs HTML -> teksts. Pietiek: tas ir tikai atkāpšanās ceļš,
    kad vēstulē nav `text/plain` daļas."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _decode_bytes(data: bytes, charset: str = "") -> str:
    for candidate in ([charset] if charset else []) + list(_FALLBACK_CHARSETS):
        try:
            return data.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _tidy(text: str) -> str:
    """Nost liekās atstarpes un tukšās rindas.

    Izvilktais teksts nāk ar dokumenta izkārtojuma pēdām — desmitiem tukšu
    rindu starp tabulas gabaliem. Modelim par tām jāmaksā ar tokeniem.
    """
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- formāti ---------------------------------------------------------------
def _from_pdf(data: bytes) -> tuple[str, str]:
    if PdfReader is None:  # pragma: no cover
        return "", "PDF lasītājs nav uzstādīts (pypdf)"
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            # Tukša parole atslēdz lielāko daļu "aizsargāto" PDF, ko sūta klienti.
            try:
                reader.decrypt("")
            except Exception:
                return "", "PDF ir ar paroli"
        pages = [(page.extract_text() or "") for page in reader.pages[:MAIL_ATTACHMENT_PDF_PAGES]]
    except Exception as exc:  # pypdf met dažādus izņēmumus par bojātiem failiem
        return "", f"PDF neizdevās atvērt ({type(exc).__name__})"
    text = _tidy("\n".join(pages))
    if not text:
        # Skenēts rasējums ir bilde PDF iepakojumā. Teksta tur nav un nebūs.
        return "", "PDF bez teksta (skenēts vai rasējums) — jāatver ar roku"
    return text, ""


def _from_docx(data: bytes) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return "", "Word failu neizdevās atvērt"
    xml = re.sub(r"(?i)<w:tab\b[^>]*/?>", "\t", xml)
    xml = re.sub(r"(?i)<w:br\b[^>]*/?>", "\n", xml)
    xml = xml.replace("</w:p>", "\n").replace("</w:tc>", " | ").replace("</w:tr>", "\n")
    text = html.unescape(re.sub(r"<[^>]+>", "", xml))
    # Rindkopas beigas tabulas šūnā uzmeta jaunu rindu pirms atdalītāja.
    text = re.sub(r"[ \t]*\n[ \t]*\|", " |", text)
    text = re.sub(r"(?m)^\s*\|\s*$", "", text)
    text = re.sub(r"(?m)\s*\|\s*$", "", text)  # tabulas rindas aste
    text = _tidy(text)
    if not text:
        return "", "Word fails bez teksta"
    return text, ""


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except (KeyError, ElementTree.ParseError, OSError):
        return []
    return ["".join(node.text or "" for node in item.iter(f"{_XL_NS}t")) for item in root]


def _xlsx_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(lapas nosaukums, ceļš arhīvā)] darbgrāmatas secībā.

    Secība nāk no `workbook.xml`, ne no failu nosaukumiem: `sheet1.xml` nav
    obligāti pirmā lapa, un lapas nosaukums ("Specifikācija") modelim pasaka
    vairāk nekā "sheet2".
    """
    try:
        book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ElementTree.ParseError, OSError):
        names = sorted(
            n for n in archive.namelist()
            if n.startswith("xl/worksheets/") and n.endswith(".xml")
        )
        return [(n.rsplit("/", 1)[-1].removesuffix(".xml"), n) for n in names]

    targets = {rel.get("Id"): rel.get("Target") or "" for rel in rels}
    sheets: list[tuple[str, str]] = []
    for sheet in book.iter(f"{_XL_NS}sheet"):
        target = targets.get(sheet.get(f"{_DOC_REL_NS}id") or "")
        if not target:
            continue
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target.lstrip("./")
        sheets.append((sheet.get("name") or "", path))
    return sheets


def _xlsx_cell(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find(f"{_XL_NS}is")
        if inline is None:
            return ""
        return "".join(node.text or "" for node in inline.iter(f"{_XL_NS}t"))
    value = cell.find(f"{_XL_NS}v")
    if value is None or value.text is None:
        return ""
    if kind == "s":
        try:
            return shared[int(value.text)]
        except (ValueError, IndexError):
            return ""
    return value.text.strip()


def _from_xlsx(data: bytes) -> tuple[str, str]:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        return "", "Excel failu neizdevās atvērt"
    with archive:
        shared = _xlsx_shared_strings(archive)
        blocks: list[str] = []
        for name, path in _xlsx_sheets(archive):
            try:
                sheet = ElementTree.fromstring(archive.read(path))
            except (KeyError, ElementTree.ParseError, OSError):
                continue
            rows: list[str] = []
            for row in sheet.iter(f"{_XL_NS}row"):
                cells = [_xlsx_cell(cell, shared) for cell in row.findall(f"{_XL_NS}c")]
                if not any(cell.strip() for cell in cells):
                    continue
                rows.append(" | ".join(cell.strip() for cell in cells))
                if len(rows) >= _MAX_SHEET_ROWS:
                    rows.append("[… lapa turpinās]")
                    break
            if not rows:
                continue
            body = "\n".join(rows)
            blocks.append(f"[lapa: {name}]\n{body}" if name else body)
    text = _tidy("\n\n".join(blocks))
    if not text:
        return "", "Excel fails bez datiem"
    return text, ""


#: Paplašinājumi, kuriem pat `application/octet-stream` nozīmē tekstu. Pasta
#: klienti tipu bieži nesaka vispār, tāpēc formātu izšķir nosaukums.
_TEXT_SUFFIXES = (".txt", ".csv", ".md", ".log", ".json", ".xml", ".yml", ".yaml", ".ini")
_HTML_SUFFIXES = (".html", ".htm")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic")
#: Formāti, kurus atvērt nemākam, bet par kuriem zinām, kas tie ir — menedžerim
#: derīgāks ir "vecais Word formāts" nekā "nezināms fails".
_KNOWN_BINARY = {
    ".doc": "vecais Word formāts",
    ".xls": "vecais Excel formāts",
    ".ppt": "PowerPoint",
    ".pptx": "PowerPoint",
    ".dwg": "AutoCAD rasējums",
    ".dxf": "CAD rasējums",
    ".step": "3D modelis",
    ".stp": "3D modelis",
    ".zip": "arhīvs",
    ".rar": "arhīvs",
    ".7z": "arhīvs",
}


def extract_text(name: str, content_type: str, data: bytes, charset: str = "") -> tuple[str, str]:
    """(teksts, piezīme) no viena pielikuma. Viens no diviem vienmēr ir tukšs."""
    lower = name.lower()
    ctype = (content_type or "").lower()

    if lower.endswith(".pdf") or ctype == "application/pdf":
        return _from_pdf(data)
    if lower.endswith(".docx") or "wordprocessingml" in ctype:
        return _from_docx(data)
    if lower.endswith((".xlsx", ".xlsm")) or "spreadsheetml" in ctype:
        return _from_xlsx(data)
    if lower.endswith(_HTML_SUFFIXES) or ctype == "text/html":
        text = _tidy(strip_html(_decode_bytes(data, charset)))
        return (text, "") if text else ("", "HTML fails bez teksta")
    if lower.endswith(_TEXT_SUFFIXES) or ctype.startswith("text/"):
        text = _tidy(_decode_bytes(data, charset))
        return (text, "") if text else ("", "tukšs fails")
    if lower.endswith(_IMAGE_SUFFIXES) or ctype.startswith("image/"):
        return "", "attēls — teksta tajā nav, jāatver ar roku"

    suffix = lower[lower.rfind("."):] if "." in lower else ""
    if suffix in _KNOWN_BINARY:
        return "", f"{_KNOWN_BINARY[suffix]} — jāatver ar roku"
    return "", "nezināms formāts — jāatver ar roku"


def extract_attachments(msg: EmailMessage) -> list[Attachment]:
    """Visi vēstules pielikumi ar izvilkto tekstu.

    Kopējo zīmju limitu turam šeit, ne promptā: divi 40 lapu PDF failu
    katalogi vienā vēstulē citādi izspiestu no konteksta pašu pieprasījumu.
    """
    budget = MAIL_ATTACHMENTS_TEXT_LIMIT
    found: list[Attachment] = []
    for part in msg.walk():
        raw_name = part.get_filename()
        if not raw_name:
            continue
        name = decode_header_value(raw_name)
        data = part.get_payload(decode=True) or b""
        item = Attachment(name=name, content_type=part.get_content_type(), size=len(data))

        if not data:
            item.note = "tukšs pielikums"
        elif len(data) > MAIL_ATTACHMENT_MAX_BYTES:
            item.note = f"pārāk liels ({len(data) // 1_000_000} MB) — jāatver ar roku"
        else:
            item.text, item.note = extract_text(
                name, item.content_type, data, part.get_content_charset() or ""
            )

        if item.text and len(item.text) > MAIL_ATTACHMENT_TEXT_LIMIT:
            item.text = item.text[:MAIL_ATTACHMENT_TEXT_LIMIT] + "\n[… pielikums apcirsts]"
        if item.text:
            if budget <= 0:
                item.text = ""
                item.note = "pārējie pielikumi neietilpa budžetā — jāatver ar roku"
            elif len(item.text) > budget:
                item.text = item.text[:budget] + "\n[… pielikums apcirsts]"
                budget = 0
            else:
                budget -= len(item.text)
        found.append(item)
    return found
