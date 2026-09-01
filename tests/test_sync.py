"""Kataloga sinhronizācijas testi.

Kategoriju koks: Store API neatgriež starpkategorijas, kurās nav produktu
tieši (ir tikai apakškategorijas). Tā pazuda "U Tips", un visiem 53 U
profiliem ceļš sabruka līdz "EPDM" — `browse_category("U Tips")` neatrada
neko, un aģents secināja, ka profila katalogā nav.
"""

from __future__ import annotations

from esupplier.catalog.sync import build_category_paths

BASE = "https://etms.lv/produkta-kategorija"


def cat(cid: int, name: str, parent: int, slug: str, url: str) -> dict:
    return {"id": cid, "name": name, "parent": parent, "slug": slug, "permalink": url}


def test_pilns_koks_paliek_neskarts() -> None:
    cats = [
        cat(1, "Gumijas blīvēšanas profili", 0, "profili", f"{BASE}/profili/"),
        cat(2, "P Tips", 1, "p-tips", f"{BASE}/profili/p-tips/"),
        cat(3, "EPDM", 2, "epdm-p", f"{BASE}/profili/p-tips/epdm-p/"),
    ]
    paths = build_category_paths(cats)
    assert paths[3] == ["Gumijas blīvēšanas profili", "P Tips", "EPDM"]


def test_trukstoso_posmu_atjauno_no_adreses() -> None:
    """"U Tips" API sarakstā nav — ceļu salasām no kategorijas adreses."""
    cats = [
        cat(1, "Gumijas blīvēšanas profili", 0, "profili", f"{BASE}/profili/"),
        # vecāks 99 ("U Tips") sarakstā NAV
        cat(4, "EPDM", 99, "epdm-u", f"{BASE}/profili/u-tips/epdm-u/"),
    ]
    paths = build_category_paths(cats)
    assert paths[4] == ["Gumijas blīvēšanas profili", "U Tips", "EPDM"]


def test_zinamu_posmu_nosaukumu_nemam_no_kataloga_nevis_no_slug() -> None:
    """Saknes nosaukums ir "Gumijas blīvēšanas profili", ne "Profili"."""
    cats = [
        cat(1, "Gumijas blīvēšanas profili", 0, "profili", f"{BASE}/profili/"),
        cat(4, "NBR", 99, "nbr-u", f"{BASE}/profili/u-tips/nbr-u/"),
    ]
    assert build_category_paths(cats)[4][0] == "Gumijas blīvēšanas profili"


def test_kategorija_bez_adreses_nesabruk() -> None:
    cats = [{"id": 7, "name": "Vāciņi", "parent": 0, "slug": "vacini"}]
    assert build_category_paths(cats)[7] == ["Vāciņi"]
