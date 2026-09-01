"""Rīku slāņa testi.

Galvenais, ko šeit sargājam: modeļi mēdz aizpildīt VISUS neobligātos
parametrus ar noklusējumiem (`max_price: 0`, `food_grade: false`) tā vietā,
lai tos izlaistu. Ja tos padod tālāk kā īstus filtrus, meklēšana klusi
atgriež nepareizu rezultātu — tāpēc tie jāatpazīst un jāizmet.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from esupplier.agent.tools import (
    TOOL_NAMES,
    TOOLS,
    execute_tool,
    _positive,
    _temperatures,
)
from esupplier.catalog import db


@pytest.fixture(scope="module")
def conn() -> sqlite3.Connection:
    connection = db.connect()
    try:
        db.init_db(connection)
        if db.product_count(connection) == 0:
            pytest.skip("Katalogs tukšs — palaid: uv run sync")
        yield connection
    finally:
        connection.close()


def search(conn, **args) -> dict:
    payload, call = execute_tool("search_products", args, conn)
    assert not call.is_error
    return json.loads(payload)


# ---------------------------------------------------------------------------
# Rīku definīcijas
# ---------------------------------------------------------------------------
def test_tools_are_flat_responses_api_shape():
    """Responses API prasa plakanu rīku, bez ligzdotā "function" objekta."""
    for tool in TOOLS:
        assert tool["type"] == "function"
        assert "function" not in tool
        assert isinstance(tool["name"], str)
        assert tool["parameters"]["type"] == "object"
        assert len(tool["description"]) > 100


def test_tool_names():
    assert TOOL_NAMES == {
        "search_products",
        "get_product",
        "browse_category",
        "list_categories",
    }


# ---------------------------------------------------------------------------
# Aizpildīto noklusējumu atsijāšana
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, None), (0.0, None), ("", None), (None, None), (False, None), ("abc", None),
     (-5, None), (25, 25.0), ("25", 25.0), (1.5, 1.5)],
)
def test_positive(raw, expected):
    assert _positive(raw) == expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({}, (None, None)),
        ({"temp_min_required": 0, "temp_max_required": 0}, (None, None)),
        ({"temp_min_required": -30, "temp_max_required": 140}, (-30.0, 140.0)),
        # 0 °C viena pati ir īsta temperatūra — to neizmetam
        ({"temp_min_required": 0, "temp_max_required": 120}, (0.0, 120.0)),
        ({"temp_min_required": -20, "temp_max_required": 0}, (-20.0, 0.0)),
    ],
)
def test_temperatures(args, expected):
    assert _temperatures(args) == expected


def test_zero_max_price_does_not_filter(conn):
    """`max_price: 0` nedrīkst nogriezt visu — tas ir aizpildīts noklusējums."""
    filled = search(conn, query="camlock", dn_mm=50, max_price=0)
    clean = search(conn, query="camlock", dn_mm=50)
    assert filled["count"] == clean["count"] > 0
    # un galvenais: nedrīkst būt nokritis uz rezerves ceļu ar atmestiem filtriem
    assert "notes" not in filled


def test_zero_temperatures_do_not_filter(conn):
    filled = search(conn, query="camlock", dn_mm=50, temp_min_required=0, temp_max_required=0)
    clean = search(conn, query="camlock", dn_mm=50)
    assert filled["count"] == clean["count"] > 0
    assert "notes" not in filled


def test_false_food_grade_does_not_filter(conn):
    """`food_grade: false` nedrīkst izmest pārtikas produktus."""
    filled = search(conn, query="piena šļūtene", food_grade=False)
    clean = search(conn, query="piena šļūtene")
    assert filled["count"] == clean["count"] > 0


def test_true_food_grade_still_filters(conn):
    payload = search(conn, query="šļūtene", food_grade=True)
    assert payload["count"] > 0
    assert all(p["food_grade"] for p in payload["products"])


def test_empty_strings_do_not_filter(conn):
    filled = search(conn, query="camlock", material="", category="")
    clean = search(conn, query="camlock")
    assert filled["count"] == clean["count"] > 0


def test_all_defaults_at_once(conn):
    """Tieši tas, ko modelis reāli nosūtīja pirmajā testā."""
    payload = search(
        conn,
        query="camlock DN50",
        material="",
        dn_mm=50,
        dn_tolerance=0,
        temp_min_required=0,
        temp_max_required=0,
        food_grade=False,
        category="",
        in_stock_only=False,
        max_price=0,
        limit=10,
    )
    assert payload["count"] > 0
    assert "notes" not in payload  # filtri netika atmesti
    assert all(p["dn_mm"] == 50 for p in payload["products"])


# ---------------------------------------------------------------------------
# Bojāta ievade
# ---------------------------------------------------------------------------
def test_limit_is_capped(conn):
    assert search(conn, query="blīve", limit=999)["count"] <= 10


def test_hostile_query_does_not_crash(conn):
    payload, call = execute_tool("search_products", {"query": 'DN25" NEAR( *'}, conn)
    assert not call.is_error


def test_unknown_tool_is_error_not_exception(conn):
    payload, call = execute_tool("nav_taada", {}, conn)
    assert call.is_error
    assert "Nezināms rīks" in payload


def test_get_product_unknown_sku_is_error(conn):
    payload, call = execute_tool("get_product", {"sku": "NAV-TADA"}, conn)
    assert call.is_error
    assert "nav produkta" in payload


def test_get_product_roundtrip(conn):
    sku = search(conn, query="camlock")["products"][0]["sku"]
    payload, call = execute_tool("get_product", {"sku": sku}, conn)
    assert not call.is_error
    data = json.loads(payload)
    assert data["sku"] == sku
    assert "attributes" in data


def test_list_categories_returns_roots(conn):
    payload, call = execute_tool("list_categories", {"min_products": 50}, conn)
    assert not call.is_error
    data = json.loads(payload)
    assert data["total_products"] > 0
    names = [c["category"] for c in data["categories"]]
    assert "Šļūtenu savienojumi" in names
    # lapu nosaukumi ("2mm", "EPDM") šeit vairs neparādās
    assert "2mm" not in names


# ---------------------------------------------------------------------------
# dn_mm profila vaicājumā
# ---------------------------------------------------------------------------
def test_dn_is_dropped_for_profile_queries():
    """Profilam diametra nav — `dn_mm` tur vienmēr izmet pareizo preci.

    Modelis to dara, neskatoties uz aizliegumu promptā UN rīka aprakstā:
    "D veida pašlīmējošs profils 12 mm" aizgāja ar `dn_mm=12`, un no visa
    kataloga atgriezās viens O veida profils Ø10.
    """
    from esupplier.agent.tools import _drop_dn_for_profiles

    dn, note = _drop_dn_for_profiles("D veida EPDM blīvēšanas profils 12 mm", 12.0)
    assert dn is None
    assert note and "dn_mm" in note

    dn, note = _drop_dn_for_profiles("U veida blīvgumija 2x8x12mm", 8.0)
    assert dn is None


def test_dn_survives_for_round_parts():
    """Šļūtenēm un savienojumiem diametrs ir īsts filtrs."""
    from esupplier.agent.tools import _drop_dn_for_profiles

    assert _drop_dn_for_profiles("camlock pāreja DN100", 100.0) == (100.0, None)
    assert _drop_dn_for_profiles("silikona šļūtene DN25", 25.0) == (25.0, None)
    assert _drop_dn_for_profiles("U veida profils", None) == (None, None)
