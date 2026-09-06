"""Kataloga rīki Claude Code sesijai.

Ko šeit sargājam: MCP serveris un `uv run chat` NEDRĪKST kļūt par diviem
dažādiem aģentiem ar vienu nosaukumu. Rīku saraksts, parametri un apraksti nāk
no `TOOL_SPECS`; ja kāds tos šeit pārraksta ar roku, testi to pamana.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from esupplier.agent.tools import TOOL_SPECS
from esupplier.mcp_server import _description, server


def call(name: str, args: dict) -> dict:
    result = asyncio.run(server.call_tool(name, args))
    return json.loads(result.content[0].text)


@pytest.fixture(scope="module")
def tools() -> dict:
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


# --- viens rīku avots ------------------------------------------------------
def test_every_catalog_tool_is_exposed(tools) -> None:
    assert set(tools) == {spec["name"] for spec in TOOL_SPECS}


def test_parameters_match_the_shared_specs(tools) -> None:
    """Parametra vārds, kas šeit atšķiras no `TOOL_SPECS`, nozīmē filtru, kurš
    klusi nenostrādā: `execute_tool` to vienkārši neatrod."""
    for spec in TOOL_SPECS:
        schema = tools[spec["name"]].input_schema
        assert set(schema.get("properties", {})) == set(
            spec["parameters"].get("properties", {})
        ), spec["name"]


def test_required_parameters_survive(tools) -> None:
    assert tools["get_product"].input_schema.get("required") == ["sku"]
    assert tools["browse_category"].input_schema.get("required") == ["category"]


def test_parameter_prose_reaches_the_description() -> None:
    """MCP shēmu atvasina no Python paraksta, tāpēc parametru apraksti citādi
    pazustu — un tieši tie pasaka, kad filtru NELIKT."""
    text = _description("search_products")
    for spec in TOOL_SPECS:
        if spec["name"] != "search_products":
            continue
        for param, schema in spec["parameters"]["properties"].items():
            assert f"`{param}`" in text
            head = (schema.get("description") or "").strip().split("\n")[0]
            if head:
                assert head[:40] in text


def test_errors_come_back_as_text_not_exceptions() -> None:
    """`execute_tool` kļūdu atdod kā tekstu, lai modelis pats izlemtu. Izņēmums
    šeit nogāztu visu sesiju."""
    result = asyncio.run(server.call_tool("get_product", {"sku": "nav-taada-preces"}))
    assert "Kļūda" in result.content[0].text


# --- pret īsto katalogu ----------------------------------------------------
@pytest.mark.skipif(
    not __import__("esupplier.config", fromlist=["DB_PATH"]).DB_PATH.exists(),
    reason="katalogs nav sinhronizēts (uv run sync)",
)
def test_search_returns_catalog_rows() -> None:
    payload = call("search_products", {"query": "EPDM profils", "limit": 2})
    assert payload["count"] <= 2
    for product in payload["products"]:
        assert product["sku"]
        assert "price_eur_excl_vat" in product
        assert "price_eur_incl_vat" in product
        assert product["unit"] in ("m", "m²", "gab.")
