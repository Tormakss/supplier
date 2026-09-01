"""Atbildes sagatavošana nosūtīšanai: sadalīšana, konsole, HTML e-pastam.

Modelis atbild divās daļās — vēstule klientam un iekšējās piezīmes menedžerim
(skat. ATBILDES FORMĀTS sistēmas promptā). Šeit tās sadalām, jo uz e-pastu
aiziet TIKAI pirmā daļa. Iekšējā daļa HTML failā nenonāk nekad: viena
neuzmanīga Ctrl+A, un klients izlasa, ko mēs par viņa pieprasījumu nezinām.
"""

from __future__ import annotations

import html
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from markdown_it import MarkdownIt

from .config import ANSWERS_DIR, CONTACT_EMAIL, SITE_URL

#: Virsraksts, ar ko sākas iekšējā daļa. Karogs ir primārais marķieris —
#: vārds "IEKŠĒJI" mainās līdz ar valodu, kurā menedžeris jautāja.
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

    Ja iekšējās daļas nav — visa atbilde ir vēstule. Tas ir apzināti drošāks
    virziens nekā otrādi: labāk menedžeris ierauga lieku rindkopu, nekā
    klients saņem tukšu vēstuli.
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


#: Mūsu iekšējā eskalācijas adrese. Vēstulē klientam tai nav ko darīt: klients
#: uz to tikko atrakstīja, un "nosūtiet šo pieprasījumu uz office@..." nozīmē,
#: ka viņš saņēma atpakaļ savu paša vēstuli.
_CONTACT = re.compile(re.escape(CONTACT_EMAIL), re.I)


def contact_leaks(text: str) -> list[str]:
    """Klienta vēstules rindas, kurās nonākusi mūsu iekšējā adrese.

    Prompts to aizliedz, bet aizliegums promptā nav garantija — un šī kļūda
    ir tieši tāda, ko menedžeris nepamana: vēstule izskatās pareiza, tikai
    beigās klientam pateikts uzrakstīt turp, kur viņš jau uzrakstīja.
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

    Ja modelis attēla adresi izdomā vai pieliek bildi no cita produkta, klients
    saņem foto ar nepareizu preci — un tas ir sliktāk nekā foto vispār bez.
    Tukšs `known` (nesinhronizēts katalogs) pārbaudi izslēdz.
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


def for_console(text: str) -> str:
    """Konsolei: attēls -> klikšķināma ikona.

    Pilns URL tabulas ailē terminālī izstiepj kolonnu pāri ekrānam un padara
    atbildi nelasāmu. Bet tukša ikona bija otra galējība: menedžeris redzēja
    📷 bez adreses un bez pielikuma, un, lai vispār ieraudzītu bildi, viņam
    bija jāatceras izsaukt /save. Tāpēc ikona paliek, bet kļūst par Markdown
    saiti — Rich to terminālī atdod kā īstu hipersaiti, kolonnas platums
    nemainās, un Cmd+klikšķis atver bildi.
    """
    return _IMAGE.sub(lambda m: f"[📷]({m.group(2)})", text)


def has_internal(text: str) -> bool:
    """Vai atbildē vispār ir iekšējais bloks.

    Bloks ir OBLIGĀTS, kad ir vēstule klientam (skat. ATBILDES FORMĀTS). Ja
    tā nav, tam ir tikai divi iemesli, un abi ir jāzina: modelis to izlaida,
    vai atbilde tika apcirsta pusvārdā. Klusējot izlaists bloks menedžerim
    izskatās pēc "nekas nav jādara".
    """
    _letter, internal = split_answer(text)
    return bool(internal.strip())


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
#: Stili rakstām katram tagam atsevišķi (nevis <style> blokā), jo Outlook un
#: Gmail dokumenta stilu lapu pie ielīmēšanas nomet.
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

#: `linkify` apzināti nav ieslēgts — tas prasītu vēl vienu atkarību, un saites
#: modelis tāpat raksta Markdown formātā.
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
