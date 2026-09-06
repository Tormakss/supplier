"""Izcelsmes pārbaude: vēstulē drīkst būt tikai tas, ko rīks tiešām atdeva.

Pieraksts ņemts no Anthropic `commerce-agents`: rakstīšana pieņem tikai tos
identifikatorus, ko sesijā atgrieza rīks. Viņiem tie ir vārti pirms groza; mums
vēstule jau ir uzrakstīta, tāpēc pārbaudām pēc fakta un sakām menedžerim.

Kalibrēšana ir puse no darba. Brīdinājums, kas iedegas pie katras normālas
vēstules, ir sliktāks par nekādu: pēc nedēļas to vairs neviens nelasa.
"""

from __future__ import annotations

import json

from esupplier import report
from esupplier.agent.tools import ToolCall

PRODUCT = {
    "sku": "000013357",
    "name": "EPDM profils D 12 mm",
    "price_eur_excl_vat": 47.38,
    "price_eur_incl_vat": 57.33,
    "unit": "m",
}


def search_call(*products: dict) -> ToolCall:
    payload = {"count": len(products), "products": list(products)}
    return ToolCall(
        name="search_products",
        input={},
        result_count=len(products),
        output=json.dumps(payload, ensure_ascii=False),
    )


def seen(*products: dict) -> report.Provenance:
    return report.tool_provenance([search_call(*products)])


# --- kas nāk no rīkiem -----------------------------------------------------
def test_skus_and_prices_come_out_of_the_tool_output() -> None:
    found = seen(PRODUCT)
    assert found.skus == {"000013357"}
    assert found.prices == {4738, 5733}


def test_nested_payloads_are_walked() -> None:
    call = ToolCall(
        name="browse_category",
        input={},
        output=json.dumps({"category": {"page": 1, "items": [PRODUCT]}}),
    )
    assert report.tool_provenance([call]).skus == {"000013357"}


def test_failed_tool_call_contributes_nothing() -> None:
    call = ToolCall(name="get_product", input={}, is_error=True, output="Kļūda rīkā get_product")
    assert not report.tool_provenance([call])


def test_broken_json_does_not_raise() -> None:
    call = ToolCall(name="search_products", input={}, output="{nav json")
    assert not report.tool_provenance([call])


# --- artikuli --------------------------------------------------------------
def test_invented_sku_is_caught() -> None:
    letter = "| 000013357 | EPDM D12 |\n| 000099999 | Blīvaukla 8 mm |"
    assert report.unbacked_skus(letter, seen(PRODUCT).skus) == ["000099999"]


def test_catalog_sku_passes() -> None:
    assert report.unbacked_skus("Artikuls 000013357.", seen(PRODUCT).skus) == []


def test_quantities_and_years_are_not_mistaken_for_skus() -> None:
    """Seši cipari ir apakšējā robeža tieši tāpēc, ka 358 un 2026 nav artikuli."""
    letter = "358 m, piegāde 2026. gadā, cietība 60 Sh."
    assert report.unbacked_skus(letter, seen(PRODUCT).skus) == []


def test_check_is_off_when_no_tool_ran() -> None:
    """Bez rīku atbildes salīdzināt nav ar ko, un tad klusēšana ir godīgāka
    par brīdinājumu pie katra artikula."""
    assert report.unbacked_skus("Artikuls 000099999.", set()) == []


# --- cenas -----------------------------------------------------------------
def test_catalog_price_and_its_totals_pass() -> None:
    letter = (
        "| 000013357 | 47.38 € bez PVN (57.33 € ar PVN) / m |\n"
        "358 m × 47.38 € = 16 962.04 € bez PVN (20 524.07 € ar PVN)."
    )
    assert report.unbacked_prices(letter, seen(PRODUCT).prices) == []


def test_grand_total_of_two_positions_passes() -> None:
    """Kopsumma nav nevienas kataloga cenas reizinājums, bet ir divu pozīciju
    summa — un tāpat ir kopsumma ar PVN."""
    letter = (
        "Poz.1: 10 m × 47.38 € = 473.80 €\n"
        "Poz.2: 5 m × 57.33 € = 286.65 €\n"
        "Kopā: 760.45 € bez PVN (920.14 € ar PVN)."
    )
    assert report.unbacked_prices(letter, seen(PRODUCT).prices) == []


def test_invented_price_is_caught() -> None:
    letter = "| 000013357 | EPDM D12 | 12.50 € bez PVN / m |"
    assert report.unbacked_prices(letter, seen(PRODUCT).prices) == ["12.50"]


def test_sku_is_not_swallowed_by_a_money_figure() -> None:
    """"Poz.1 000013357 47.38 €" reiz tika nolasīts kā viena summa ar
    atstarpēm, un artikuls pazuda skaitļa iekšienē."""
    letter = "Poz.1 000013357 47.38 € bez PVN"
    assert report.unbacked_prices(letter, seen(PRODUCT).prices) == []


def test_numbers_without_a_currency_sign_are_left_alone() -> None:
    """Bez `€` "12,50" ir izmērs vai cietība, ne cena."""
    letter = "Profils 12,50 mm plats, cietība 60,00 Sh."
    assert report.unbacked_prices(letter, seen(PRODUCT).prices) == []


def test_price_check_is_off_when_no_tool_ran() -> None:
    assert report.unbacked_prices("Cena 12.50 €.", set()) == []


def test_second_product_price_passes() -> None:
    other = dict(PRODUCT, sku="000013358", price_eur_excl_vat=12.50, price_eur_incl_vat=15.13)
    letter = "47.38 € un 12.50 € bez PVN."
    assert report.unbacked_prices(letter, seen(PRODUCT, other).prices) == []
