"""Atbildes sagatavošana nosūtīšanai: sadalīšana, konsole, HTML e-pastam.

Modelis atbild divās daļās (skat. ATBILDES FORMĀTS promptā). Uz e-pastu aiziet
TIKAI pirmā; iekšējā daļa HTML failā nenonāk nekad.
"""

from __future__ import annotations

import html
import itertools
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from markdown_it import MarkdownIt

from .catalog import units
from .config import ANSWERS_DIR, CONTACT_EMAIL, SITE_URL, VAT_RATE

#: Iekšējās daļas virsraksts. Karogs ir primārais: vārds mainās ar valodu.
_INTERNAL_HEADING = re.compile(
    r"^\s{0,3}#{0,4}\s*(?:\*\*)?\s*(?:⚑|IEKŠĒJI|IEKSEJI|ВНУТРЕН|INTERNAL)",
    re.IGNORECASE,
)
#: Markdown attēls: ![alt](url)
_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*(\S+?)\s*\)")
#: Horizontālā līnija, kas pirms iekšējās daļas ir tikai atdalītājs.
_RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")


def split_answer(text: str) -> tuple[str, str]:
    """Sadala atbildi (vēstule klientam, iekšējās piezīmes).

    Bez iekšējās daļas visa atbilde ir vēstule: labāk lieka rindkopa
    menedžerim nekā tukša vēstule klientam.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not _INTERNAL_HEADING.match(line):
            continue
        head = lines[:i]
        # Nogriežam atdalītāju un tukšās rindas pirms virsraksta.
        while head and (not head[-1].strip() or _RULE.match(head[-1])):
            head.pop()
        return "\n".join(head).strip(), "\n".join(lines[i:]).strip()
    return text.strip(), ""


#: Mūsu iekšējā eskalācijas adrese. Klientam tā nozīmē "sūti to, ko tikko sūtīji".
_CONTACT = re.compile(re.escape(CONTACT_EMAIL), re.I)


def contact_leaks(text: str) -> list[str]:
    """Klienta vēstules rindas, kurās nonākusi mūsu iekšējā adrese.

    Prompts to aizliedz, bet aizliegums promptā nav pārbaude.
    """
    letter, _internal = split_answer(text)
    return [line.strip() for line in letter.splitlines() if _CONTACT.search(line)]


def known_image_urls(conn: sqlite3.Connection) -> set[str]:
    """Visas kataloga bilžu adreses — pret tām pārbaudām, ko modelis uzrakstīja."""
    rows = conn.execute(
        "SELECT DISTINCT image_url FROM products WHERE image_url IS NOT NULL AND image_url != ''"
    )
    return {row[0] for row in rows}


def verify_images(text: str, known: set[str] | None) -> tuple[str, list[str]]:
    """Izmet attēlus, kuru nav katalogā. Atgriež (teksts, izmesto URL saraksts).

    Foto ar nepareizu preci ir sliktāk nekā bez foto. Tukšs `known`
    (nesinhronizēts katalogs) pārbaudi izslēdz.
    """
    if not known:
        return text, []

    dropped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        url = match.group(2)
        if url in known:
            return match.group(0)
        dropped.append(url)
        return "—"

    return _IMAGE.sub(replace, text), dropped


#: Markdown saite, kas NAV attēls. `(?<!!)` tur nost `![alt](url)`.
_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*(\S+?)\s*\)")
#: Atlikuma skaitlis vēstulē. Modelis to neredz, tāpēc katrs ir izdomāts.
_STOCK_LEAK = (
    re.compile(r"noliktavā\s+(?:ir\s+|pieejam\w+\s+)?\d", re.IGNORECASE),
    re.compile(r"\d\s*(?:gab\.?|m²|m)\s+noliktavā", re.IGNORECASE),
    re.compile(r"\b(atlikum\w*|krājum\w*)\b", re.IGNORECASE),
)


def known_product_urls(conn: sqlite3.Connection) -> set[str]:
    """Visas kataloga produktu lapas — pret tām pārbaudām saites vēstulē."""
    rows = conn.execute(
        "SELECT DISTINCT permalink FROM products WHERE permalink IS NOT NULL AND permalink != ''"
    )
    return {row[0] for row in rows}


def verify_links(text: str, known: set[str] | None) -> tuple[str, list[str]]:
    """Izmet saites, kuru nav katalogā. Atgriež (teksts, izmesto URL saraksts).

    Tas pats iemesls, kas bildēm; no artikula salikta adrese dod 404.
    """
    if not known:
        return text, []

    dropped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        url = match.group(2)
        if url in known:
            return match.group(0)
        dropped.append(url)
        return "—"

    return _LINK.sub(replace, text), dropped


def stock_leaks(text: str) -> list[str]:
    """Rindas, kurās vēstulē parādījies atlikuma skaitlis vai vārds.

    Atlikums mainās ātrāk, nekā vēstule aiziet, bet klientam tas ir solījums.
    """
    letter, _internal = split_answer(text)
    found: list[str] = []
    for line in letter.splitlines():
        if any(pattern.search(line) for pattern in _STOCK_LEAK):
            found.append(line.strip())
    return found


def stock_notes(conn: sqlite3.Connection, skus: list[str]) -> list[str]:
    """Atlikums menedžerim — pa vienai rindai uz artikulu.

    Skaitli pieliek PROGRAMMA, ne modelis: modelim tā nav vispār, tāpēc
    klientam tas nevar nonākt pat kļūdas ceļā.
    """
    if not skus:
        return []
    placeholders = ",".join("?" for _ in skus)
    rows = conn.execute(
        f"SELECT sku, is_in_stock, stock_qty, unit FROM products WHERE sku IN ({placeholders})",
        skus,
    ).fetchall()
    by_sku = {row["sku"]: row for row in rows}

    notes: list[str] = []
    for sku in skus:
        row = by_sku.get(sku)
        if row is None:
            continue
        label = units.LABELS.get(row["unit"], row["unit"])
        if row["stock_qty"] is not None:
            notes.append(f"art. {sku} — noliktavā {row['stock_qty']} {label}")
        elif row["is_in_stock"]:
            notes.append(f"art. {sku} — noliktavā ir, precīzs skaits katalogā nav")
        else:
            notes.append(f"art. {sku} — noliktavā NAV")
    return notes


# --- izcelsme --------------------------------------------------------------
# Anthropic `commerce-agents` pieraksts, bet pēc fakta: vēstule jau ir
# uzrakstīta, tāpēc nesakritību sakām menedžerim, ne aizturam.

#: Artikuls ir cipari ar vadošajām nullēm. Zem sešiem sākas gadi un izmēri.
_SKU = re.compile(r"\b\d{6,12}\b")
#: Naudas summa: divas zīmes aiz komata un valūta blakus. Bez valūtas "12,50"
#: var būt izmērs. Atstarpe kā tūkstošu atdalītājs derīga TIKAI pa trim
#: cipariem: citādi "Poz.1 000013357 47.38 €" nolasās kā viena summa.
_MONEY = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[ \u00a0]\d{3})+[.,]\d{2}|\d+[.,]\d{2})\s*(?:€|EUR\b)"
)
#: Jebkurš skaitlis vēstulē. Tie ir daudzumi, ar kuriem modelis reizina cenu.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
#: Cik daudzumu ņemam vērā, pirms reizinājumu kopa kļūst bezjēdzīgi plata.
_MAX_MULTIPLIERS = 25
#: Cik pozīciju summu vēl uzskatām par kopsummu.
_MAX_SUM_TERMS = 6


@dataclass(slots=True)
class Provenance:
    """Ko rīki tiešām atdeva šajā gājienā."""

    skus: set[str] = field(default_factory=set)
    #: Cenas centos: 47.38 * 358 peldošajā komatā nesakrīt ar modeļa summu.
    prices: set[int] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.skus or self.prices)


def _cents(value: float) -> int:
    return int(round(float(value) * 100))


def _to_cents(text: str) -> int | None:
    cleaned = text.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return _cents(float(cleaned))
    except ValueError:
        return None


def _collect(node: Any, found: Provenance) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "sku" and isinstance(value, str) and value.strip():
                found.skus.add(value.strip())
            elif key.startswith("price_eur") and isinstance(value, (int, float)):
                found.prices.add(_cents(value))
            else:
                _collect(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect(item, found)


def tool_provenance(tool_calls: Iterable[Any]) -> Provenance:
    """Artikuli un cenas, ko rīki atgrieza šajā gājienā.

    Lasām rīka atbildi, ne katalogu: katalogā esošs, bet neizsaukts artikuls
    ir tikpat izdomāts kā jebkurš cits.
    """
    found = Provenance()
    for call in tool_calls:
        if getattr(call, "is_error", False) or not getattr(call, "output", ""):
            continue
        try:
            _collect(json.loads(call.output), found)
        except (ValueError, TypeError):
            continue
    return found


def cited_skus(letter: str) -> list[str]:
    """Artikuli, ko vēstule nosauc — parādīšanās secībā, bez atkārtojumiem."""
    seen: list[str] = []
    for sku in _SKU.findall(letter):
        if sku not in seen:
            seen.append(sku)
    return seen


def unbacked_skus(letter: str, seen: set[str]) -> list[str]:
    """Artikuli vēstulē, kurus neviens rīks neatdeva."""
    if not seen:
        return []
    return sorted({sku for sku in _SKU.findall(letter) if sku not in seen})


def _derived_prices(seen: set[int], multipliers: set[float]) -> set[int]:
    """Cenas, kas no kataloga cenām izriet ar reizināšanu.

    Divi soļi: pozīcijas summa un tā pati ar PVN. Trešais padarītu kopu tik
    platu, ka tajā trāpa jebkas.
    """
    with_vat = 1.0 + VAT_RATE
    products = {int(round(price * factor)) for price in seen for factor in multipliers}
    return seen | products | {int(round(price * with_vat)) for price in products}


def _sums(values: list[int]) -> set[int]:
    """Kopsummas no jau atzītajām summām."""
    totals: set[int] = set()
    pool = values[:12]
    for size in range(2, min(_MAX_SUM_TERMS, len(pool)) + 1):
        for combo in itertools.combinations(pool, size):
            totals.add(sum(combo))
    return totals


def unbacked_prices(letter: str, seen: set[int]) -> list[str]:
    """Cenas vēstulē, kas nav ne katalogā, ne izrēķināmas no tā, kas tur ir.

    Pieņemam plaši apzināti: brīdinājumu pie KATRAS vēstules pēc nedēļas
    vairs neviens nelasa.
    """
    if not seen:
        return []
    figures = [(raw, _to_cents(raw)) for raw in _MONEY.findall(letter)]
    figures = [(raw, value) for raw, value in figures if value is not None]
    if not figures:
        return []

    multipliers: set[float] = set()
    for raw in _NUMBER.findall(letter):
        try:
            number = float(raw.replace(",", "."))
        except ValueError:
            continue
        if 0 < number and len(multipliers) < _MAX_MULTIPLIERS:
            multipliers.add(number)
    multipliers.add(1.0)

    derived = _derived_prices(seen, multipliers)

    def known(value: int, pool: set[int]) -> bool:
        # Centa pielaide: modelis noapaļo pozīcijas summu, mēs rēķinām no cenas.
        return any(abs(value - candidate) <= 1 for candidate in pool)

    accepted = [value for _, value in figures if known(value, derived)]
    # Kopsumma pati nav kataloga cenas reizinājums, tāpēc PVN solis jāatkārto.
    totals = _sums(accepted)
    totals |= {int(round(total * (1.0 + VAT_RATE))) for total in totals}
    return sorted(
        {
            raw
            for raw, value in figures
            if not known(value, derived) and not known(value, totals)
        }
    )


def for_console(text: str) -> str:
    """Konsolei: attēls -> klikšķināma ikona.

    Pilns URL izstiepj tabulas kolonnu pāri ekrānam; tukša ikona neļauj bildi
    atvērt. Markdown saite Rich terminālī dod abus.
    """
    return _IMAGE.sub(lambda m: f"[📷]({m.group(2)})", text)


def has_internal(text: str) -> bool:
    """Vai atbildē vispār ir iekšējais bloks.

    Bloks ir OBLIGĀTS. Tā trūkumam ir divi iemesli, un abi jāzina: modelis to
    izlaida vai atbilde tika apcirsta.
    """
    _letter, internal = split_answer(text)
    return bool(internal.strip())


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
#: Stili katram tagam atsevišķi: Outlook un Gmail <style> bloku nomet.
_STYLES = {
    "table": "border-collapse:collapse;width:100%;margin:16px 0;font-size:14px",
    "th": "border:1px solid #d0d5dd;padding:8px 10px;background:#f5f6f8;text-align:left",
    "td": "border:1px solid #d0d5dd;padding:8px 10px;vertical-align:middle",
    "img": "max-width:120px;height:auto;display:block",
    "p": "margin:10px 0",
    "ul": "margin:10px 0;padding-left:20px",
    "ol": "margin:10px 0;padding-left:20px",
    "li": "margin:4px 0",
    "h1": "font-size:20px;margin:18px 0 10px",
    "h2": "font-size:17px;margin:18px 0 8px",
    "h3": "font-size:15px;margin:16px 0 8px",
    "a": "color:#0b5fff",
    "hr": "border:0;border-top:1px solid #d0d5dd;margin:18px 0",
    "code": "font-family:ui-monospace,Menlo,monospace;font-size:13px",
}

_DOCUMENT = """\
<!doctype html>
<html lang="lv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:24px;background:#f0f1f3;\
font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;\
font-size:14px;line-height:1.5;color:#1a1d21">
<div style="max-width:820px;margin:0 auto;background:#ffffff;padding:28px 32px;\
border:1px solid #d0d5dd;border-radius:6px">
{body}
<p style="margin:24px 0 0;padding-top:16px;border-top:1px solid #e4e7ec;\
font-size:12px;color:#667085">
Tehnisko Materiālu Sagāde &middot; <a href="{site}" style="color:#667085">{site_label}</a>
&middot; <a href="mailto:{email}" style="color:#667085">{email}</a>
</p>
</div>
</body>
</html>
"""

#: `linkify` apzināti izslēgts: prasītu atkarību, un modelis raksta Markdown.
_markdown = MarkdownIt("commonmark").enable("table")


def _inline_styles(body: str) -> str:
    for tag, style in _STYLES.items():
        body = re.sub(
            rf"<{tag}(?=[\s>])(?![^>]*\bstyle=)",
            f'<{tag} style="{style}"',
            body,
        )
        body = body.replace(f"<{tag}>", f'<{tag} style="{style}">')
    return body


def render_html(text: str, *, title: str = "Piedāvājums") -> str:
    """Markdown -> HTML ar iebūvētiem stiliem, gatavs ielīmēšanai e-pastā."""
    body = _inline_styles(_markdown.render(text))
    return _DOCUMENT.format(
        title=html.escape(title),
        body=body,
        site=SITE_URL,
        site_label=SITE_URL.replace("https://", ""),
        email=CONTACT_EMAIL,
    )


def _default_path(directory: Path | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (directory or ANSWERS_DIR) / f"piedavajums-{stamp}.html"


def save_answer(
    text: str,
    *,
    path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
    title: str = "Piedāvājums",
) -> tuple[Path, list[str]]:
    """Saglabā vēstules daļu kā HTML. Atgriež (ceļš, izmesto attēlu saraksts)."""
    letter, _internal = split_answer(text)
    letter, dropped = verify_images(letter, known_image_urls(conn) if conn else None)

    out = Path(path) if path else _default_path()
    if out.suffix.lower() not in (".html", ".htm"):
        out = out.with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(letter, title=title), encoding="utf-8")
    return out, dropped


def save_internal(text: str, *, answer_path: Path | str) -> Path:
    out = Path(answer_path)
    out = out.with_name(out.stem + "-IEKSEJI.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text.strip() + "\n", encoding="utf-8")
    return out

