"""Pielikumi: MIME daļa -> teksts, ko var padot modelim.

Nolasām TEKSTU. Kas teksta nesatur, paliek atzīmēts kā neizlasīts: godīgs
"atver ar roku" ir labāks par klusēšanu. Attēlus atšifrē `vision.py`.
"""

from __future__ import annotations

import html
import importlib.util
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from ..config import (
    MAIL_ARCHIVE_MAX_FILES,
    MAIL_ATTACHMENTS_TEXT_LIMIT,
    MAIL_ATTACHMENT_MAX_BYTES,
    MAIL_ATTACHMENT_PDF_PAGES,
    MAIL_ATTACHMENT_TEXT_LIMIT,
    SOFFICE_BIN,
    SOFFICE_TIMEOUT_S,
)

try:  # pragma: no cover — atkarība ir `pyproject.toml`, bet trūkums nedrīkst
    from pypdf import PdfReader  # nogāzt visu pastkastītes gājienu
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment]

try:  # pragma: no cover — vecais Excel formāts; bez tā tas paliek cilvēkam
    import xlrd
except ImportError:  # pragma: no cover
    xlrd = None  # type: ignore[assignment]

#: Kodējumi bez galvenes. `cp1257` — vecās latviešu `.txt` un `.csv` izdrukas.
_FALLBACK_CHARSETS = ("utf-8", "cp1257", "cp1251", "latin-1")

#: Cik rindu no Excel lapas salasām, pirms zīmju limits paspēj nogriezt.
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
    #: True, ja tekstu nolasīja modelis no attēla. Var kļūdīties izmēros.
    transcribed: bool = False
    #: Baiti, kamēr tie vēl var noderēt atšifrēšanai. Pēc tās tiek iztukšoti.
    data: bytes = b""
    #: `image/...` vai `application/pdf`. Tukšs = atšifrēt nav ko.
    image_mime: str = ""

    @property
    def read(self) -> bool:
        return bool(self.text.strip())

    @property
    def can_transcribe(self) -> bool:
        """Vai pielikumu vēl var izlasīt, uz to paskatoties."""
        return bool(self.image_mime and self.data and not self.read)


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
    """Vienkāršs HTML -> teksts. Atkāpšanās ceļš, kad nav `text/plain` daļas."""
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
    """Nost liekās atstarpes un tukšās rindas — par tām maksā ar tokeniem."""
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
        # Skenēts rasējums ir bilde PDF iepakojumā; to atšifrē `vision.py`.
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
    # Rindkopas beigas šūnā uzmeta jaunu rindu pirms atdalītāja.
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

    Secība nāk no `workbook.xml`: `sheet1.xml` nav obligāti pirmā lapa.
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


#: PowerPoint teksts sēž DrawingML `<a:t>` mezglos, ne savā telpvārdā.
_DRAW_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _from_pptx(data: bytes) -> tuple[str, str]:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        return "", "PowerPoint failu neizdevās atvērt"
    with archive:
        names = [n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        # `slide10` failu sarakstā stāv pirms `slide2`; secība nāk no skaitļa.
        names.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
        blocks: list[str] = []
        for number, name in enumerate(names, start=1):
            try:
                root = ElementTree.fromstring(archive.read(name))
            except (KeyError, ElementTree.ParseError, OSError):
                continue
            lines = []
            for para in root.iter(f"{_DRAW_NS}p"):
                line = "".join(node.text or "" for node in para.iter(f"{_DRAW_NS}t")).strip()
                if line:
                    lines.append(line)
            if lines:
                blocks.append(f"[slaids {number}]\n" + "\n".join(lines))
    text = _tidy("\n\n".join(blocks))
    if not text:
        return "", "PowerPoint fails bez teksta"
    return text, ""


def _xls_cell(value: object) -> str:
    """`xlrd` katru skaitli atdod kā `float`; "358.0" tabulā ir troksnis."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _from_xls(data: bytes) -> tuple[str, str]:
    """Vecais binārais Excel (BIFF). Tāds nāk no grāmatvedības programmām."""
    if xlrd is None:  # pragma: no cover
        return "", "vecais Excel formāts — lasītājs nav uzstādīts (xlrd)"
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception:  # xlrd met savus izņēmumus par katru bojājuma veidu
        return "", "vecais Excel formāts — failu neizdevās atvērt"
    blocks: list[str] = []
    for sheet in book.sheets():
        rows: list[str] = []
        for index in range(min(sheet.nrows, _MAX_SHEET_ROWS)):
            cells = [_xls_cell(value) for value in sheet.row_values(index)]
            if not any(cell.strip() for cell in cells):
                continue
            rows.append(" | ".join(cells))
        if sheet.nrows > _MAX_SHEET_ROWS:
            rows.append("[… lapa turpinās]")
        if rows:
            body = "\n".join(rows)
            blocks.append(f"[lapa: {sheet.name}]\n{body}" if sheet.name else body)
    text = _tidy("\n\n".join(blocks))
    if not text:
        return "", "vecais Excel formāts bez datiem"
    return text, ""


#: RTF grupas, kuru saturs ir dokumenta iekšas: fontu tabula, stili, bildes.
_RTF_SKIP = frozenset(
    {
        "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata",
        "datastore", "listtable", "listoverridetable", "rsidtbl", "generator",
        "xmlnstbl", "latentstyles", "filetbl", "revtbl",
    }
)
_RTF_WORD = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?")
_RTF_HEX = re.compile(r"\\'([0-9a-fA-F]{2})")


def _rtf_to_text(raw: str) -> str:
    r"""RTF -> teksts. Pietiekami, lai izlasītu pieprasījumu, ne lai atveidotu.

    Ar roku, ne ar regulāro izteiksmi: `{\*\pict ...}` jāizlaiž veselas, un tas
    prasa zināt grupas dziļumu.
    """
    out: list[str] = []
    depth = 0
    skip_depth = 0  # 0 = nekas netiek izlaists
    index = 0
    length = len(raw)

    while index < length:
        char = raw[index]
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            if skip_depth and depth <= skip_depth:
                skip_depth = 0
            depth -= 1
            index += 1
            continue
        if char == "\\":
            hexed = _RTF_HEX.match(raw, index)
            if hexed:
                index = hexed.end()
                if not skip_depth:
                    out.append(bytes([int(hexed.group(1), 16)]).decode("cp1252", "replace"))
                continue
            word = _RTF_WORD.match(raw, index)
            if word:
                name, argument = word.group(1), word.group(2)
                index = word.end()
                if name in _RTF_SKIP:
                    skip_depth = depth
                elif skip_depth:
                    pass
                elif name in ("par", "line", "sect", "row", "pard"):
                    out.append("\n")
                elif name == "tab":
                    out.append("\t")
                elif name == "cell":
                    out.append(" | ")
                elif name == "u" and argument:
                    out.append(chr(int(argument) % 0x10000))
                    # Aiz `\uN` stāv aizvietotājzīme vecākiem lasītājiem.
                    if index < length and raw[index] not in "\\{}":
                        index += 1
                continue
            if index + 1 < length:
                if raw[index + 1] == "*":
                    skip_depth = depth
                elif not skip_depth:
                    out.append(raw[index + 1])
                index += 2
                continue
            index += 1
            continue
        if not skip_depth:
            out.append(char)
        index += 1

    return "".join(out)


def _from_rtf(data: bytes) -> tuple[str, str]:
    text = _tidy(_rtf_to_text(_decode_bytes(data)))
    text = re.sub(r"(?m)\s*\|\s*$", "", text)  # tabulas rindas aste
    text = _tidy(text)
    if not text:
        return "", "RTF fails bez teksta"
    return text, ""


#: DXF tekstu tur grupas ar kodu 1 (pamatteksts) un 3 (garā MTEXT turpinājums).
_DXF_TEXT_CODES = ("1", "3")
#: MTEXT iekšā ir formatējums: `\P` rindas pārnesums, `\fArial|b0;` fonta maiņa.
_DXF_FORMAT = re.compile(r"\\[A-Za-z][^;\\]*;")


#: Sadaļas, kurās DXF glabā to, ko cilvēks rasējumā redz. `HEADER` un
#: `CLASSES` iekšā kods 1 ir CAD klases nosaukums, ne uzraksts.
_DXF_SECTIONS = ("ENTITIES", "BLOCKS")

#: Entītijas, kurās kods 1 vai 3 ir cilvēka rakstīts teksts. Bez šī no
#: `BLOCKS` iznāca bloku nosaukumi (`*Model_Space`).
_DXF_TEXT_ENTITIES = frozenset(
    {"TEXT", "MTEXT", "ATTDEF", "ATTRIB", "DIMENSION", "MLEADER", "MULTILEADER", "TOLERANCE"}
)


def _from_dxf(data: bytes) -> tuple[str, str]:
    """ASCII DXF: uzraksti, izmēru atzīmes un tabulas rindas no rasējuma.

    Ģeometriju izlaižam apzināti: koordinātas modelim neko nepasaka.
    """
    if data[:18] == b"AutoCAD Binary DXF":
        return "", "binārs DXF rasējums — jāatver ar roku"
    lines = [line.strip() for line in _decode_bytes(data).splitlines()]
    values: list[str] = []
    seen: set[str] = set()
    section = ""
    entity = ""
    expect_section = False
    index = 0
    while index < len(lines) - 1:
        code, value = lines[index], lines[index + 1]
        if not code.lstrip("-").isdigit():
            index += 1
            continue
        index += 2

        if code == "0":
            entity = value.upper()
            if value == "SECTION":
                expect_section = True
            elif value == "ENDSEC":
                section = ""
            continue
        if code == "2" and expect_section:
            section, expect_section = value.upper(), False
            continue
        if section not in _DXF_SECTIONS or entity not in _DXF_TEXT_ENTITIES:
            continue
        if code in _DXF_TEXT_CODES and value:
            cleaned = _DXF_FORMAT.sub("", value.replace("\\P", "\n"))
            cleaned = cleaned.replace("{", "").replace("}", "").strip()
            # Rāmja un stūra uzraksti rasējumā atkārtojas katrā izkārtojumā.
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                values.append(cleaned)
    text = _tidy("\n".join(values))
    if not text:
        # Rasējums bez uzrakstiem ir tikai līnijas, un DXF attēlot neprotam.
        return "", "DXF rasējums bez uzrakstiem — jāatver ar roku"
    return text, ""


# --- vecie binārie Office formāti ------------------------------------------
def _soffice_bin() -> str:
    """LibreOffice, ja tas uz šīs mašīnas ir. Obligāta atkarība tas nav."""
    return SOFFICE_BIN or shutil.which("soffice") or shutil.which("libreoffice") or ""


def _convert_with_soffice(data: bytes, suffix: str, target: str) -> tuple[bytes, str]:
    """(rezultāta baiti, iemesls). Viens no diviem vienmēr ir tukšs."""
    binary = _soffice_bin()
    if not binary:
        return b"", "jāatver ar roku (LibreOffice uz servera nav)"
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / f"pielikums{suffix}"
        source.write_bytes(data)
        try:
            subprocess.run(
                [binary, "--headless", "--norestore", "--convert-to", target,
                 "--outdir", folder, str(source)],
                capture_output=True,
                timeout=SOFFICE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return b"", "konvertācija neizdevās"
        result = Path(folder) / f"pielikums.{target.split(':')[0]}"
        if not result.exists():
            return b"", "konvertācija neizdevās"
        try:
            return result.read_bytes(), ""
        except OSError:  # pragma: no cover
            return b"", "konvertācija neizdevās"


def _from_ole(data: bytes, suffix: str) -> tuple[str, str]:
    """Vecais `.doc`, `.xls`, `.ppt` — OLE konteiners, ne ZIP.

    `.xls` prot `xlrd`; pārējos tikai LibreOffice, kuras uz servera var nebūt.
    """
    label = _KNOWN_BINARY.get(suffix, "vecais Office formāts")

    if suffix in ("", ".xls", ".xlt"):
        text, _ = _from_xls(data)
        if text:
            return text, ""

    if suffix in (".ppt", ".pps"):
        # Impress uz tekstu nekonvertē; caur PDF teksta slānis saglabājas.
        converted, problem = _convert_with_soffice(data, suffix, "pdf")
        if converted:
            return _from_pdf(converted)
        return "", f"{label} — {problem}"

    converted, problem = _convert_with_soffice(data, suffix or ".doc", "txt:Text")
    if converted:
        text = _tidy(_decode_bytes(converted))
        if text:
            return text, ""
        return "", f"{label} bez teksta"
    return "", f"{label} — {problem}"


# --- arhīvi ----------------------------------------------------------------
def _zip_members(data: bytes) -> tuple[list[tuple[str, bytes | None]], bool] | None:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        return None
    members: list[tuple[str, bytes | None]] = []
    truncated = False
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if len(members) >= MAIL_ARCHIVE_MAX_FILES:
                truncated = True
                break
            if info.file_size > MAIL_ATTACHMENT_MAX_BYTES:
                members.append((info.filename, b""))
                continue

            try:
                members.append((info.filename, archive.read(info)))
            except (RuntimeError, zipfile.BadZipFile, OSError):
                # `None` atšķir parolētu ierakstu no tukša faila: menedžerim
                # tā ir starpība.
                members.append((info.filename, None))
    return members, truncated


def _sevenzip_members(data: bytes) -> tuple[list[tuple[str, bytes]], bool] | None:
    try:  # pragma: no cover — neobligāta atkarība
        import py7zr
    except ImportError:
        return None
    try:  # pragma: no cover
        with py7zr.SevenZipFile(BytesIO(data)) as archive:
            extracted = archive.readall() or {}
    except Exception:
        return None
    members = []  # pragma: no cover
    for name, buffer in list(extracted.items())[:MAIL_ARCHIVE_MAX_FILES]:
        members.append((name, buffer.read()))
    return members, len(extracted) > MAIL_ARCHIVE_MAX_FILES


def _rar_members(data: bytes) -> tuple[list[tuple[str, bytes]], bool] | None:
    try:  # pragma: no cover — prasa arī `unrar` bināro failu
        import rarfile
    except ImportError:
        return None
    try:  # pragma: no cover
        with rarfile.RarFile(BytesIO(data)) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
            members = [(i.filename, archive.read(i)) for i in infos[:MAIL_ARCHIVE_MAX_FILES]]
        return members, len(infos) > MAIL_ARCHIVE_MAX_FILES
    except Exception:
        return None


def archive_members(name: str, data: bytes) -> tuple[list[tuple[str, bytes | None]], bool] | None:
    """Arhīva saturs vai `None`, ja tas nav arhīvs (vai to atvērt nevaram).

    Iekšējie faili iet pa to pašu ceļu, kas pielikumi — arī līdz atšifrēšanai.
    """
    lower = name.lower()
    if lower.endswith(".7z"):
        return _sevenzip_members(data)
    if lower.endswith(".rar"):
        return _rar_members(data)
    if data[:4] == b"PK\x03\x04":
        # `.docx`, `.xlsx`, `.pptx` arī ir ZIP: izpakot nozīmētu padot
        # modelim `[Content_Types].xml` specifikācijas vietā.
        return _zip_members(data) if _sniff_zip(data) == "zip" else None
    if lower.endswith(".zip"):
        return _zip_members(data)
    return None


# --- ko mums iedeva --------------------------------------------------------
def _sniff_zip(data: bytes) -> str:
    """ZIP iekšpuse pasaka, kas tas ir; nosaukums mēdz melot."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return ""
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    return "zip"


def _sniff(data: bytes) -> str:
    """Formāts pēc baitiem. Tukšs = neizdevās, tad izšķir nosaukums."""
    head = data[:12]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"{\\rt"):
        return "rtf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM")):
        return "image"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"hevc"):
        return "image"
    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip(data)
    return ""


#: Paplašinājumi, kuriem `application/octet-stream` tomēr nozīmē tekstu.
_TEXT_SUFFIXES = (".txt", ".csv", ".md", ".log", ".json", ".xml", ".yml", ".yaml", ".ini")
_HTML_SUFFIXES = (".html", ".htm")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic")
#: Formāti, kurus atvērt nemākam, bet varam nosaukt vārdā.
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


def _kind_from_name(suffix: str, ctype: str) -> str:
    """Formāts pēc nosaukuma un MIME tipa. Izmanto tikai tad, ja baiti klusē."""
    if suffix == ".pdf" or ctype == "application/pdf":
        return "pdf"
    if suffix == ".docx" or "wordprocessingml" in ctype:
        return "docx"
    if suffix in (".xlsx", ".xlsm") or "spreadsheetml" in ctype:
        return "xlsx"
    if suffix == ".pptx" or "presentationml" in ctype:
        return "pptx"
    if suffix in (".xls", ".xlt"):
        return "xls"
    if suffix in (".doc", ".ppt", ".pps"):
        return "ole"
    if suffix == ".rtf" or ctype in ("application/rtf", "text/rtf"):
        return "rtf"
    if suffix == ".dxf":
        return "dxf"
    if suffix in _HTML_SUFFIXES or ctype == "text/html":
        return "html"
    if suffix in _TEXT_SUFFIXES or ctype.startswith("text/"):
        return "text"
    if suffix in _IMAGE_SUFFIXES or ctype.startswith("image/"):
        return "image"
    if suffix in (".zip", ".rar", ".7z"):
        return "archive"
    return ""


def file_kind(name: str, content_type: str, data: bytes) -> str:
    """Ko mums iedeva. Baiti sver vairāk par nosaukumu un par MIME tipu."""
    lower = name.lower()
    suffix = lower[lower.rfind(".") :] if "." in lower else ""
    return _sniff(data) or _kind_from_name(suffix, (content_type or "").lower())


def extract_text(name: str, content_type: str, data: bytes, charset: str = "") -> tuple[str, str]:
    """(teksts, piezīme) no viena pielikuma. Viens no diviem vienmēr ir tukšs."""
    lower = name.lower()
    suffix = lower[lower.rfind(".") :] if "." in lower else ""
    kind = file_kind(name, content_type, data)

    if kind == "pdf":
        return _from_pdf(data)
    if kind == "docx":
        return _from_docx(data)
    if kind == "xlsx":
        return _from_xlsx(data)
    if kind == "pptx":
        return _from_pptx(data)
    if kind == "xls":
        return _from_xls(data)
    if kind == "ole":
        return _from_ole(data, suffix)
    if kind == "rtf":
        return _from_rtf(data)
    if kind == "dxf":
        return _from_dxf(data)
    if kind == "html":
        text = _tidy(strip_html(_decode_bytes(data, charset)))
        return (text, "") if text else ("", "HTML fails bez teksta")
    if kind == "text":
        text = _tidy(_decode_bytes(data, charset))
        return (text, "") if text else ("", "tukšs fails")
    if kind == "image":
        # Izlasīt var tikai paskatoties; to dara `vision.transcribe`.
        return "", "attēls — teksta tajā nav, jāatver ar roku"
    if kind in ("zip", "archive"):
        # Trūkstošs lasītājs vai bojāts fails: pirmo var salabot uz servera.
        if suffix == ".7z":
            missing = importlib.util.find_spec("py7zr") is None
            return "", "7z arhīvs — " + (
                "jāatver ar roku (`py7zr` nav uzstādīts)" if missing
                else "izpakot neizdevās, jāatver ar roku"
            )
        if suffix == ".rar":
            missing = importlib.util.find_spec("rarfile") is None
            return "", "RAR arhīvs — " + (
                "jāatver ar roku (`rarfile` nav uzstādīts)" if missing
                else "izpakot neizdevās, jāatver ar roku"
            )
        return "", "arhīvu atvērt neizdevās (bojāts vai parolēts) — jāatver ar roku"

    if suffix in _KNOWN_BINARY:
        return "", f"{_KNOWN_BINARY[suffix]} — jāatver ar roku"
    return "", "nezināms formāts — jāatver ar roku"


def _make_attachment(
    name: str, content_type: str, data: bytes | None, charset: str
) -> Attachment:
    """Viens pielikums ar visu, ko no tā izdevās izlasīt bez tīkla.

    `data is None` = baitus dabūt neizdevās (parolēts ieraksts arhīvā).
    """
    if data is None:
        return Attachment(
            name=name,
            content_type=content_type,
            note="parolēts vai bojāts ieraksts arhīvā — jāatver ar roku",
        )
    item = Attachment(name=name, content_type=content_type, size=len(data))
    if not data:
        item.note = "tukšs pielikums"
        return item
    if len(data) > MAIL_ATTACHMENT_MAX_BYTES:
        item.note = f"pārāk liels ({len(data) // 1_000_000} MB) — jāatver ar roku"
        return item

    item.text, item.note = extract_text(name, content_type, data, charset)
    if not item.text:
        # Baitus paturam tikai tad, ja uz tiem vēl ir vērts paskatīties.
        kind = file_kind(name, content_type, data)
        if kind == "pdf":
            item.data, item.image_mime = data, "application/pdf"
        elif kind == "image":
            item.data, item.image_mime = data, content_type or "image/*"
    return item


def apply_budget(items: list[Attachment]) -> None:
    """Nogriež pielikumu tekstu pa vienam un kopā. Maina sarakstu uz vietas.

    Atsevišķa funkcija tāpēc, ka atšifrējums pienāk vēlāk un ieskaitās tajā
    pašā budžetā.
    """
    budget = MAIL_ATTACHMENTS_TEXT_LIMIT
    for item in items:
        if not item.text:
            continue
        if len(item.text) > MAIL_ATTACHMENT_TEXT_LIMIT:
            item.text = item.text[:MAIL_ATTACHMENT_TEXT_LIMIT] + "\n[… pielikums apcirsts]"
        if budget <= 0:
            item.text = ""
            item.note = "pārējie pielikumi neietilpa budžetā — jāatver ar roku"
        elif len(item.text) > budget:
            item.text = item.text[:budget] + "\n[… pielikums apcirsts]"
            budget = 0
        else:
            budget -= len(item.text)


def extract_attachments(msg: EmailMessage) -> list[Attachment]:
    """Visi vēstules pielikumi ar izvilkto tekstu.

    Arhīvs pazūd, un tā vietā parādās tas, kas bija iekšā.
    """
    found: list[Attachment] = []
    for part in msg.walk():
        raw_name = part.get_filename()
        if not raw_name:
            continue
        name = decode_header_value(raw_name)
        data = part.get_payload(decode=True) or b""
        content_type = part.get_content_type()
        charset = part.get_content_charset() or ""

        unpacked = None
        if data and len(data) <= MAIL_ATTACHMENT_MAX_BYTES:
            unpacked = archive_members(name, data)
        if unpacked is None:
            found.append(_make_attachment(name, content_type, data, charset))
            continue

        members, truncated = unpacked
        for member_name, member_data in members:
            # Vārds paliek salikts: jāzina, kurā arhīvā failu meklēt.
            found.append(_make_attachment(f"{name} → {member_name}", "", member_data, ""))
        if not members:
            found.append(
                Attachment(name=name, content_type=content_type, size=len(data),
                           note="arhīvs ir tukšs")
            )
        elif truncated:
            found.append(
                Attachment(
                    name=name,
                    content_type=content_type,
                    size=len(data),
                    note=f"arhīvā ir vairāk nekā {MAIL_ARCHIVE_MAX_FILES} faili — "
                         "pārējie netika atvērti",
                )
            )

    apply_budget(found)
    return found
