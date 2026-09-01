"""Mērvienība: metri, kvadrātmetri vai gabali.

Veikala datos mērvienības NAV. Ne Store API (`prices`, `attributes`,
`add_to_cart`), ne produkta lapā nav neviena lauka, kas pateiktu, vai 2.50 €
ir par metru vai par gabalu — pārbaudīts pret dzīvo katalogu. Tāpēc tā ir
biznesa patiesība, kas jātur šeit un jāpieliek klāt katrā sinhronizācijā.

Kamēr tās nebija, VISI 3568 produkti bija `gab`, un piedāvājumos aizgāja
"4.35 € bez PVN / gab." par blīvēšanas profilu, ko klients pērk metros.

Divi slāņi:
  1. `RULES` — kategoriju likumi, pirmā sakritība uzvar. Tie sedz ģimenes.
  2. `data/units.csv` — SKU izņēmumi ar roku. Tie uzvar pār likumiem.

Kategorijas, par kurām nav skaidrības, likumos NAV: tās paliek `gab`, un
sistēmas prompts liek modelim tādā gadījumā prasīt apstiprinājumu iekšējā
blokā, nevis klusi rēķināt gabalos.
"""

from __future__ import annotations

import csv
import sqlite3
import unicodedata
from pathlib import Path

from ..config import PROJECT_ROOT

#: Metri — profili, šļūtenes, auklas, lentes.
M = "m"
#: Kvadrātmetri — loksnes un rullveida materiāli, ko griež pēc laukuma.
M2 = "m2"
#: Gabali — noklusējums.
PIECE = "gab"

VALID_UNITS = frozenset({M, M2, PIECE})

#: Cilvēkam rādāmā forma. `m2` tabulā un vēstulē ir "m²".
LABELS = {M: "m", M2: "m²", PIECE: "gab."}

#: Kur glabājas SKU izņēmumi.
OVERRIDES_PATH = PROJECT_ROOT / "data" / "units.csv"

#: (kategorijas ceļa daļa, mērvienība). PIRMĀ sakritība uzvar, tāpēc
#: izņēmumiem jābūt pirms plašākā likuma — "Šļūteņu balsti" ir gabali, kaut
#: gan viss pārējais "Šļūtenes un aprīkojums" ir metri.
#:
#: Salīdzinām pret PILNO kategorijas ceļu, bez diakritikas, mazajiem burtiem.
RULES: list[tuple[str, str]] = [
    # --- izņēmumi pirms plašajiem likumiem -------------------------------
    ("slutenu balsti", PIECE),          # turētāji, nevis šļūtene
    ("aizsarguzmavas", PIECE),          # gatavas uzmavas ar izmēru
    ("din & sms savienojumu atslegas", PIECE),

    # --- kvadrātmetri: loksnes, plēves, segumi ---------------------------
    ("tehniska gumija", M2),            # visa sakne — loksnēs griežama gumija
    ("loksnu blivmateriali", M2),
    ("ptfe loksnes", M2),               # sedz arī "E-PTFE Loksnes"
    ("ptfe folijs", M2),
    ("ptfe pleves", M2),
    ("poliuretans pu > loksnes", M2),
    ("gumijas gridu segumi", M2),
    ("pretslides gumijas pakli", M2),

    # --- metri: profili, šļūtenes, auklas, lentes ------------------------
    ("gumijas blivesanas profili un blivgumijas", M),
    ("slutenes un aprikojums", M),
    ("vieglas aspiracijas slutenes", M),
    ("tvaika slutene", M),
    ("blivauklas", M),
    ("snores", M),
    ("lentes blivesanai", M),
    ("e-ptfe lentes", M),
    ("ptfe caurule", M),
    ("neoprena (cr) lentes", M),
    ("gumijas malas sniega lapstam", M),
    ("silikona caurules", M),
]


def _fold(text: str) -> str:
    """Mazie burti bez diakritikas — tāpat kā meklēšanas pusē.

    Kategoriju nosaukumos ir gan "Šļūtenes", gan "Slutenes", gan HTML
    entītijas; likumus rakstīt ar visām garumzīmēm nozīmētu tos uzturēt divreiz.
    """
    decomposed = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def category_unit(category: str) -> str:
    """Mērvienība pēc kategorijas ceļa. Nezināmam — `gab`."""
    folded = _fold(category)
    for needle, unit in RULES:
        if needle in folded:
            return unit
    return PIECE


def load_overrides(path: Path | str | None = None) -> dict[str, str]:
    """SKU -> mērvienība no `data/units.csv`. Faila nav — nav izņēmumu.

    Formāts: `sku,unit`, ar `#` komentāriem. Nederīgu mērvienību ignorējam
    klusi: pārrakstīšanās CSV failā nedrīkst apturēt sinhronizāciju.
    """
    target = Path(path) if path else OVERRIDES_PATH
    if not target.exists():
        return {}

    out: dict[str, str] = {}
    with target.open(encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle):
            if not row or row[0].lstrip().startswith("#") or len(row) < 2:
                continue
            sku, unit = row[0].strip(), row[1].strip().lower()
            if sku.lower() == "sku":  # galvene
                continue
            if sku and unit in VALID_UNITS:
                out[sku] = unit
    return out


def resolve_unit(
    sku: str, category: str, overrides: dict[str, str] | None = None
) -> str:
    """Gala mērvienība: SKU izņēmums, citādi kategorijas likums."""
    if overrides and sku in overrides:
        return overrides[sku]
    return category_unit(category)


def apply_units(
    conn: sqlite3.Connection, overrides: dict[str, str] | None = None
) -> dict[str, int]:
    """Pārrēķina `unit` visiem produktiem DB. Atgriež skaitu pa mērvienībām.

    Atsevišķi no sinhronizācijas tāpēc, ka likumu labojums nedrīkst prasīt
    pilnu kataloga pārvilkšanu no jauna — `data/units.csv` labo ar roku, un
    izmaiņai jāaiziet DB dažās sekundēs.
    """
    if overrides is None:
        overrides = load_overrides()

    rows = conn.execute("SELECT id, sku, category FROM products").fetchall()
    updates = [
        (resolve_unit(row["sku"] or "", row["category"] or "", overrides), row["id"])
        for row in rows
    ]
    conn.executemany("UPDATE products SET unit = ? WHERE id = ?", updates)
    conn.commit()

    counts: dict[str, int] = {}
    for unit, _id in updates:
        counts[unit] = counts.get(unit, 0) + 1
    return counts
