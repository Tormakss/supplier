"""Noliktava un saites vēstulē.

Divi noteikumi, ko šeit sargājam:

1. Klientam pieejamība ir "ir" vai "nav". Precīzs atlikums mainās ātrāk, nekā
   vēstule aiziet, un skaitlis, ko klients izlasīja, kļūst par solījumu. Modelis
   to vairs neredz VISPĀR — tā ir mehāniska garantija, ne prompta lūgums.
2. Katrai pozīcijai ir saite uz veikalu, un tā nāk no kataloga. Adrese, salikta
   no artikula, klientam atveras kā 404.
"""

from __future__ import annotations

import json

import pytest

from esupplier import report
from esupplier.agent.tools import execute_tool
from esupplier.catalog import db
from esupplier.catalog.models import Product

CATALOG_URL = "https://etms.lv/produkts/camlock-seal-dn38-epdm/"


@pytest.fixture()
def conn(tmp_path):
    with db.session(tmp_path / "test.db") as connection:
        db.upsert_products(
            connection,
            [
                Product(
                    id=1,
                    sku="000015202",
                    name="Camlock blīve DN38 EPDM",
                    permalink=CATALOG_URL,
                    price_excl_vat=1.23,
                    price_incl_vat=1.49,
                    unit="gab",
                    is_in_stock=True,
                    stock_qty=121,
                ),
                Product(
                    id=2,
                    sku="000029023",
                    name="Temagraph plāksne",
                    permalink="https://etms.lv/produkts/temagraph/",
                    unit="m2",
                    is_in_stock=False,
                ),
            ],
        )
        yield connection


# --- atlikuma skaitlis modelim neaiziet ------------------------------------
def test_search_payload_has_no_stock_number(conn) -> None:
    """Ja modelis skaitli neredz, tas to nevar ne nokopēt, ne pārrakstīt."""
    output, _ = execute_tool("search_products", {"query": "Camlock"}, conn)
    product = json.loads(output)["products"][0]

    assert product["in_stock"] is True
    assert "stock_qty" not in product
    assert "stock_text" not in product


def test_get_product_payload_has_no_stock_number(conn) -> None:
    output, _ = execute_tool("get_product", {"sku": "000015202"}, conn)
    payload = json.loads(output)

    assert payload["in_stock"] is True
    assert "stock_qty" not in payload
    assert "stock_text" not in payload
    assert payload["url"] == CATALOG_URL


def test_browse_payload_has_no_stock_number(conn) -> None:
    assert "stock_qty" not in Product(id=3, sku="x", name="x", permalink="").to_browse_dict()


# --- noplūde vēstulē -------------------------------------------------------
def test_stock_number_in_the_letter_is_caught() -> None:
    leaks = report.stock_leaks("Noliktavā ir 19 metri.\n\n---\n⚑ IEKŠĒJI\nviss labi")
    assert leaks == ["Noliktavā ir 19 metri."]


def test_the_word_atlikums_is_caught() -> None:
    assert report.stock_leaks("Pašreizējais atlikums 12 gab.")


def test_the_allowed_sentence_passes() -> None:
    """Tieši šo formulējumu prompts liek lietot — brīdinājums te būtu troksnis."""
    letter = "Prece ir noliktavā; vajadzīgo daudzumu (25 m) apstiprināšu atsevišķi."
    assert report.stock_leaks(letter) == []


def test_a_total_with_a_quantity_is_not_a_stock_leak() -> None:
    assert report.stock_leaks("25 m × 4.10 € = 102.50 € bez PVN.") == []


def test_internal_block_is_not_scanned() -> None:
    """Iekšējā blokā atlikums ir vietā — tieši tur programma to pati ieliek."""
    text = "Labdien!\n\n---\n⚑ IEKŠĒJI\n- art. 000015202 — noliktavā 121 gab."
    assert report.stock_leaks(text) == []


# --- atlikums menedžerim ---------------------------------------------------
def test_stock_notes_come_from_the_database(conn) -> None:
    notes = report.stock_notes(conn, ["000015202", "000029023"])
    assert notes == [
        "art. 000015202 — noliktavā 121 gab.",
        "art. 000029023 — noliktavā NAV",
    ]


def test_unknown_sku_is_skipped(conn) -> None:
    assert report.stock_notes(conn, ["000000000"]) == []


def test_cited_skus_keeps_order_without_repeats() -> None:
    letter = "| 000015202 | ... |\n| 000029023 | ... |\nKopā art. 000015202."
    assert report.cited_skus(letter) == ["000015202", "000029023"]


# --- saites ----------------------------------------------------------------
def test_catalog_link_survives(conn) -> None:
    text = f"[Skatīt]({CATALOG_URL})"
    out, dropped = report.verify_links(text, report.known_product_urls(conn))
    assert out == text
    assert dropped == []


def test_invented_link_is_dropped(conn) -> None:
    """Adrese, salikta no artikula, klientam atveras kā 404."""
    text = "[Skatīt](https://etms.lv/produkts/000015202/)"
    out, dropped = report.verify_links(text, report.known_product_urls(conn))
    assert out == "—"
    assert dropped == ["https://etms.lv/produkts/000015202/"]


def test_images_are_left_to_verify_images(conn) -> None:
    """Divkārša pārbaude vienu un to pašu bildi izmestu divreiz."""
    text = "![blīve](https://foto.lv/a.jpg)"
    out, dropped = report.verify_links(text, report.known_product_urls(conn))
    assert out == text
    assert dropped == []


def test_check_is_off_without_a_catalog() -> None:
    text = "[Skatīt](https://jebkas.lv/)"
    assert report.verify_links(text, set()) == (text, [])


# --- prompts ---------------------------------------------------------------
def test_prompt_forbids_the_stock_number_and_demands_a_link() -> None:
    from esupplier.agent.prompts import SYSTEM_PROMPT

    assert "PIEEJAMĪBA IR DIVVĒRTĪGA" in SYSTEM_PROMPT
    assert "Noliktavā (Ir/Nav) | Saite" in SYSTEM_PROMPT
    assert "[Skatīt](url)" in SYSTEM_PROMPT
