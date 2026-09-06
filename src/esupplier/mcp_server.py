"""Kataloga rīki Claude Code sesijai (MCP, stdio).

Claude Code iet uz abonementa, un API atslēga tam nav vajadzīga. Bet pats par
sevi tas neko nezina par e-supplier.lv katalogu, un piedāvājums, kas uzbūvēts
no atmiņas, ir tieši tas, ko sistēmas prompta pirmais noteikums aizliedz.
Šis serveris atdod tos PAŠUS četrus rīkus, ko lieto `uv run chat`.

Rīku definīcijas un izpilde nāk no `agent/tools.py` — otras kopijas nav. Tur
`TOOL_SPECS` jau ir neitrālā formā tieši šim gadījumam.

    uv run mcp          # parasti nepalaiž ar roku; to dara Claude Code

Pieslēgums ir `.mcp.json` repozitorija saknē. Uzvedības noteikumi (cenas ar PVN
un bez, mērvienības, ko nedrīkst apsolīt) ir prasmē `.claude/skills/piedavajums/`.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .agent.tools import TOOL_SPECS, execute_tool
from .catalog import db

_SPECS = {spec["name"]: spec for spec in TOOL_SPECS}

SERVER_INSTRUCTIONS = """\
e-supplier.lv produktu katalogs (SQLite kopija, ~3600 produkti).

Nekad nenosauc produktu, cenu, artikulu vai pieejamību, ko neesi redzējis šo
rīku atbildē. Ja katalogā nav — saki to, neizdomā.

Cenas atdotas divējādi: `price_eur_excl_vat` (bez PVN) un `price_eur_incl_vat`
(ar PVN). Uzņēmumi rēķina bez PVN, privātpersonas domā ar PVN — nosauc abas.
`unit` lauks ir mērvienība (m, m² vai gab.); tā nāk no datiem, ne no nojautas.
"""


def _description(name: str) -> str:
    """Rīka apraksts kopā ar parametru aprakstiem.

    MCP shēmu atvasina no Python paraksta, tāpēc parametru apraksti, kas
    `TOOL_SPECS` dzīvo atsevišķi, citādi pazustu. Tieši tie pasaka, KAD filtru
    nelikt ("ja lietotājs nav norādījis diametru, NEIZDOMĀ to"), un tur ir
    dārgākās kļūdas — izlaista prece, jo filtrs bija izdomāts.
    """
    spec = _SPECS[name]
    lines = [spec["description"]]
    properties: dict[str, Any] = spec["parameters"].get("properties", {})
    if properties:
        lines.append("PARAMETRI")
        for param, schema in properties.items():
            text = (schema.get("description") or "").strip().replace("\n", " ")
            lines.append(f"- `{param}` ({schema.get('type', '?')}): {text}")
    return "\n\n".join(lines)


def _call(name: str, args: dict[str, Any]) -> str:
    """Rīka izsaukums ar savu savienojumu.

    Savienojumu veram uz katru izsaukumu. Serveris stāv atvērts tik ilgi, cik
    sesija, un SQLite savienojums, kas nostāvējis stundas, ir lieka riska vieta
    par ietaupījumu, ko neviens nepamana: katalogs ir lokāls fails.
    """
    with db.session() as conn:
        output, _call_record = execute_tool(name, args, conn)
    return output


server = MCPServer(name="e-supplier-katalogs", instructions=SERVER_INSTRUCTIONS)


@server.tool(name="search_products", description=_description("search_products"))
def search_products(
    query: str = "",
    material: str = "",
    dn_mm: int | None = None,
    dn_tolerance: int | None = None,
    type_code: str = "",
    temp_min_required: float | None = None,
    temp_max_required: float | None = None,
    food_grade: bool | None = None,
    category: str = "",
    in_stock_only: bool = False,
    max_price: float | None = None,
    limit: int | None = None,
) -> str:
    return _call(
        "search_products",
        {
            "query": query,
            "material": material,
            "dn_mm": dn_mm,
            "dn_tolerance": dn_tolerance,
            "type_code": type_code,
            "temp_min_required": temp_min_required,
            "temp_max_required": temp_max_required,
            "food_grade": food_grade,
            "category": category,
            "in_stock_only": in_stock_only,
            "max_price": max_price,
            "limit": limit,
        },
    )


@server.tool(name="get_product", description=_description("get_product"))
def get_product(sku: str) -> str:
    return _call("get_product", {"sku": sku})


@server.tool(name="browse_category", description=_description("browse_category"))
def browse_category(
    category: str,
    page: int | None = None,
    per_page: int | None = None,
    in_stock_only: bool = False,
) -> str:
    return _call(
        "browse_category",
        {
            "category": category,
            "page": page,
            "per_page": per_page,
            "in_stock_only": in_stock_only,
        },
    )


@server.tool(name="list_categories", description=_description("list_categories"))
def list_categories(min_products: int | None = None) -> str:
    return _call("list_categories", {"min_products": min_products})


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
