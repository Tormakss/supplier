"""Mērvienības: kategoriju likumi, SKU izņēmumi, DB pārrēķins."""

from __future__ import annotations

from esupplier.catalog import db, units
from esupplier.catalog.models import Product
from esupplier.catalog.normalize import detect_unit


def test_profiles_are_sold_by_the_metre():
    """Blīvēšanas profils ir metros — tieši šis bija salauzts.

    Piedāvājumā aizgāja "4.35 € bez PVN / gab." par preci, ko klients pērk
    metros, un neviens datu lauks tam nerunāja pretī: visi 3568 produkti bija
    `gab`.
    """
    assert (
        units.category_unit(
            "Gumijas blīvēšanas profili un blīvgumijas > D Tips > EPDM"
        )
        == units.M
    )
    assert units.category_unit("Šļūtenes un aprīkojums > PVC šļūtenes") == units.M
    assert units.category_unit("Blīvēšanas materiāli > Blīvauklas > PTFE") == units.M


def test_sheet_materials_are_square_metres():
    assert units.category_unit("Tehniskā gumija > SBR gumija / universāla") == units.M2
    assert (
        units.category_unit(
            "Blīvēšanas materiāli > Lokšņu blīvmateriāli (Blivju izgriešanai) > "
            "Paranīta loksnes > Temafast"
        )
        == units.M2
    )


def test_connectors_stay_pieces():
    assert (
        units.category_unit("Šļūtenu savienojumi > Camlock tipa savienojumi > AL")
        == units.PIECE
    )
    assert units.category_unit("") == units.PIECE
    assert units.category_unit("Kaut kas jauns katalogā") == units.PIECE


def test_narrow_rule_beats_the_broad_one():
    """Šļūteņu balsti ir gabali, kaut arī visa sakne ir metri."""
    assert (
        units.category_unit(
            "Šļūtenes un aprīkojums > Šļūteņu aizsardzībai > Šļūteņu balsti"
        )
        == units.PIECE
    )


def test_rules_ignore_diacritics():
    """Kategorijās mēdz būt gan "Šļūtenes", gan "Slutenes"."""
    assert units.category_unit("Slutenes un aprikojums > PVC slutenes") == units.M


def test_overrides_win_over_rules(tmp_path):
    path = tmp_path / "units.csv"
    path.write_text(
        "# komentārs\nsku,unit\nx000001143,gab\nBOJATS,litri\n,m\n",
        encoding="utf-8",
    )
    overrides = units.load_overrides(path)
    # Nederīga mērvienība un tukšs SKU tiek izlaisti klusi — pārrakstīšanās
    # CSV failā nedrīkst apturēt sinhronizāciju.
    assert overrides == {"x000001143": "gab"}

    profile = "Gumijas blīvēšanas profili un blīvgumijas > D Tips > EPDM"
    assert units.resolve_unit("x000001143", profile, overrides) == units.PIECE
    assert units.resolve_unit("x000001146", profile, overrides) == units.M


def test_missing_override_file_is_not_an_error(tmp_path):
    assert units.load_overrides(tmp_path / "nav.csv") == {}


def test_detect_unit_returns_none_when_text_says_nothing():
    """`None`, ne "gab" — citādi kategorijas likums nekad netiktu pie vārda."""
    assert detect_unit("D veida EPDM profils 10×15 mm", {}) is None
    assert detect_unit("Blīvgumija 4.10 €/m", {}) == "m"
    # Vērtības, ne tikai atslēgas: agrāk `' '.join(dict)` savienoja atslēgas,
    # un mērvienība atribūta vērtībā palika neredzama.
    assert detect_unit("Loksne", {"Cena": "12.00 EUR/m2"}) == "m2"


def test_apply_units_rewrites_the_whole_catalog(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    db.upsert_products(
        conn,
        [
            Product(
                id=1, sku="A", name="D profils", permalink="",
                category="Gumijas blīvēšanas profili un blīvgumijas > D Tips > EPDM",
            ),
            Product(
                id=2, sku="B", name="Camlock C", permalink="",
                category="Šļūtenu savienojumi > Camlock tipa savienojumi > AL",
            ),
            Product(
                id=3, sku="C", name="SBR loksne", permalink="",
                category="Tehniskā gumija > SBR gumija / universāla",
            ),
        ],
    )
    counts = units.apply_units(conn, overrides={})
    assert counts == {units.M: 1, units.PIECE: 1, units.M2: 1}

    rows = {r["sku"]: r["unit"] for r in conn.execute("SELECT sku, unit FROM products")}
    assert rows == {"A": "m", "B": "gab", "C": "m2"}


def test_model_hands_the_printable_label_to_the_agent():
    """Modelim atdodam "m²", ne "m2" — to tas kopē vēstulē bez pārrakstīšanas."""
    sheet = Product(id=1, sku="C", name="Loksne", permalink="", unit="m2")
    assert sheet.to_search_dict()["unit"] == "m²"
    piece = Product(id=2, sku="B", name="Camlock", permalink="", unit="gab")
    assert piece.to_search_dict()["unit"] == "gab."
