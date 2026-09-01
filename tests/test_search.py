"""Meklēšanas testi pret reālo katalogu.

Testi neizmanto izdomātus datus — ja `data/catalog.db` nav, tie tiek izlaisti
ar norādi palaist sinhronizāciju.
"""

from __future__ import annotations

import sqlite3

import pytest

from esupplier.catalog import db
from esupplier.catalog.search import (
    build_fts_queries,
    catalog_stats,
    get_product,
    list_categories,
    search_products,
)


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


# ---------------------------------------------------------------------------
# FTS vaicājuma sagatavošana (nav vajadzīga DB)
# ---------------------------------------------------------------------------
def test_fts_escapes_quotes():
    """`DN25"` nedrīkst uzspridzināt FTS parseri."""
    variants = build_fts_queries('DN25" šļūtene')
    assert variants
    for _, query in variants:
        assert query.count('"') % 2 == 0
        assert "''" not in query


@pytest.mark.parametrize(
    "hostile",
    ['DN25"', "NEAR(", "a OR OR b", "*", '"""', "^caurule", "col1 : val", "a AND"],
)
def test_fts_survives_hostile_input(hostile, conn):
    """Jebkura lietotāja ievade drīkst atgriezt 0 rezultātu, bet ne kļūdu."""
    result = search_products(hostile, conn=conn, limit=3)
    assert isinstance(len(result), int)


def test_stopwords_removed():
    _, query = build_fts_queries("man vajag šļūteni")[0]
    assert "vajag" not in query
    assert "šļūteni" in query


def test_empty_query_has_no_variants():
    assert build_fts_queries("  ...  ") == []


def test_single_letter_type_is_kept():
    """"U profils" bez burta "U" atrod jebkuru profilu, tikai ne to, ko vajag."""
    _, query = build_fts_queries("U profils EPDM")[0]
    assert '"U"' in query


def test_single_letter_gets_no_prefix_star():
    """"u"* atbilstu pusei kataloga — burtam zvaigznīte nav vajadzīga."""
    for strategy, query in build_fts_queries("U profils"):
        assert '"U"*' not in query


def test_dimension_is_searched_by_parts():
    """Katalogā izmērs ir "12x22x25mm" — "12x16mm" jāatrod arī pa daļām."""
    exact = dict(build_fts_queries("EPDM profils 12x16mm"))["exact"]
    assert '"12x16mm"' in exact
    assert '"12"' in exact
    assert '"16"' in exact
    # Grupa AND ķēdē turas kā viens vārds.
    assert '("12x16mm" OR "12" OR "16")' in exact


def test_dimension_parts_get_prefix_star_in_stem_tier():
    stem = dict(build_fts_queries("EPDM profils 12x16mm"))["stem"]
    assert '"12"*' in stem


def test_number_with_unit_is_searched_without_it():
    exact = dict(build_fts_queries("profils 16mm"))["exact"]
    assert '"16mm"' in exact and '"16"' in exact


def test_finds_u_profile_family(conn):
    """Regresijas tests: agrāk šis vaicājums neatgrieza nevienu U profilu."""
    result = search_products("U profils EPDM", conn=conn, limit=5)
    assert result
    assert all("U veida" in p.name for p in result)


def test_finds_by_colour_word(conn):
    """Krāsa ir atribūtā, nevis nosaukumā — bez aliasiem to neatrast."""
    result = search_products("pelēks EPDM profils", conn=conn, limit=5)
    assert result
    assert any(p.color and "elēk" in p.color for p in result)


def test_finds_by_russian_colour_word(conn):
    """Menedžeris jautā krieviski, katalogs ir latviski."""
    result = search_products("серый EPDM", conn=conn, limit=5)
    assert result
    assert all(p.color and "elēk" in p.color for p in result)


def test_search_result_carries_colour_and_photo(conn):
    """Krāsu un foto vajag jau meklēšanas atbildē, ne pēc get_product."""
    result = search_products("U profils EPDM", conn=conn, limit=3)
    assert result
    payload = result[0].to_search_dict()
    assert payload["image_url"].startswith("http")
    assert payload["color"]


def test_finds_nearest_sizes_of_the_right_family(conn):
    """Precīzā 12x16 nav — bet 12x-sākuma izmēriem jāatrodas."""
    result = search_products("EPDM U-profils 12x16mm", conn=conn, limit=5)
    assert result
    assert any("U veida EPDM" in p.name and "12x" in p.name for p in result)


# ---------------------------------------------------------------------------
# Pilnteksta meklēšana
# ---------------------------------------------------------------------------
def test_finds_camlock(conn):
    result = search_products("camlock", conn=conn, limit=10)
    assert len(result) > 0
    assert all("camlock" in p.name.lower() for p in result)


def test_finds_without_diacritics(conn):
    """"slutene" bez garumzīmēm jāatrod tāpat kā "šļūtene"."""
    with_marks = {p.sku for p in search_products("šļūtene", conn=conn, limit=10)}
    without = {p.sku for p in search_products("slutene", conn=conn, limit=10)}
    assert without
    assert with_marks & without


def test_inflected_form_falls_back_to_stem(conn):
    """Latviešu galotnes: "šļūtenes" jāatrod caur prefiksa meklēšanu."""
    result = search_products("silikona šļūtenes", conn=conn, limit=5)
    assert len(result) > 0


def test_nonsense_query_returns_nothing(conn):
    result = search_products("kaut kas tāds neeksistē zzzqqq", conn=conn, limit=5)
    assert len(result) == 0
    assert result.strategy == "none"


# ---------------------------------------------------------------------------
# Strukturētie filtri
# ---------------------------------------------------------------------------
def test_dn_filter_is_exact(conn):
    result = search_products(dn_mm=25, conn=conn, limit=20)
    assert len(result) > 0
    assert all(p.dn_mm == 25 for p in result)


def test_dn_tolerance_widens_range(conn):
    exact = search_products(dn_mm=25, conn=conn, limit=100)
    loose = search_products(dn_mm=25, dn_tolerance=5, conn=conn, limit=100)
    assert len(loose) >= len(exact)
    # Sakrist drīkst JEBKURŠ no diviem pārejas diametriem.
    assert all(
        (p.dn_mm is not None and 20 <= p.dn_mm <= 30)
        or (p.dn_mm_2 is not None and 20 <= p.dn_mm_2 <= 30)
        for p in loose
    )


def test_dn_filter_matches_either_end_of_a_reducer(conn):
    """Pāreja 4"x6" jāatrod gan pēc DN100, gan pēc DN150."""
    by_small = search_products(dn_mm=100, type_code="AR", conn=conn, limit=50)
    by_large = search_products(dn_mm=150, type_code="AR", conn=conn, limit=50)
    assert "x000001782" in {p.sku for p in by_small}
    assert "x000001782" in {p.sku for p in by_large}


def test_material_filter(conn):
    result = search_products(material="EPDM", conn=conn, limit=20)
    assert len(result) > 0
    assert all("EPDM" in (p.material or "") for p in result)


def test_material_filter_accepts_latvian_name(conn):
    """"Silikons" jānormalizē uz MVQ pirms filtrēšanas."""
    result = search_products(material="silikons", conn=conn, limit=10)
    assert len(result) > 0
    assert all("MVQ" in (p.material or "") for p in result)


def test_food_grade_filter(conn):
    result = search_products("šļūtene", food_grade=True, conn=conn, limit=20)
    assert len(result) > 0
    assert all(p.food_grade for p in result)


def test_temperature_filter_means_product_withstands_range(conn):
    """Filtrs nozīmē "produkts iztur šo diapazonu", ne "diapazons pārklājas"."""
    result = search_products(
        temp_min_required=-30, temp_max_required=150, conn=conn, limit=20
    )
    assert len(result) > 0
    for p in result:
        assert p.temp_min_c is not None and p.temp_min_c <= -30
        assert p.temp_max_c is not None and p.temp_max_c >= 150


def test_max_price_filter(conn):
    result = search_products(max_price=5.0, conn=conn, limit=20)
    assert len(result) > 0
    assert all(p.price_excl_vat is not None and p.price_excl_vat <= 5.0 for p in result)


def test_in_stock_filter(conn):
    result = search_products("blīve", in_stock_only=True, conn=conn, limit=20)
    assert len(result) > 0
    assert all(p.is_in_stock for p in result)


def test_filters_apply_on_top_of_fts(conn):
    result = search_products("blīve", material="EPDM", dn_mm=25, conn=conn, limit=10)
    for p in result:
        assert "blīve" in p.name.lower()
        assert p.material == "EPDM"
        assert p.dn_mm == 25


# ---------------------------------------------------------------------------
# Kārtošana un atkāpšanās
# ---------------------------------------------------------------------------
def test_no_query_sorts_by_stock_then_price(conn):
    result = search_products(material="EPDM", conn=conn, limit=20)
    assert len(result) > 1
    keys = [
        (not p.is_in_stock, p.price_excl_vat if p.price_excl_vat is not None else 1e9)
        for p in result
    ]
    assert keys == sorted(keys)


def test_relaxes_in_stock_when_nothing_found(conn):
    """Neiespējami dārgs filtrs + in_stock_only -> jāatzīmē atkāpšanās."""
    result = search_products(
        "camlock", dn_mm=25, in_stock_only=True, max_price=0.01, conn=conn, limit=5
    )
    if len(result) > 0:
        assert result.relaxed_in_stock or result.relaxed_filters
        assert result.notes


def test_food_grade_is_never_relaxed(conn):
    """Nesertificēts aizstājējs pārtikai nedrīkst iznirt caur atkāpšanos."""
    result = search_products(
        "blīve", food_grade=True, dn_mm=9999, in_stock_only=True, conn=conn, limit=10
    )
    assert all(p.food_grade for p in result)


# ---------------------------------------------------------------------------
# Palīgfunkcijas
# ---------------------------------------------------------------------------
def test_get_product_roundtrip(conn):
    first = search_products("camlock", conn=conn, limit=1)[0]
    fetched = get_product(first.sku, conn=conn)
    assert fetched is not None
    assert fetched.sku == first.sku
    assert fetched.description == first.description


def test_get_product_unknown_sku(conn):
    assert get_product("NAV-TADA-SKU", conn=conn) is None


def test_list_categories(conn):
    categories = list_categories(conn=conn)
    assert len(categories) > 0
    counts = [c["products"] for c in categories]
    assert counts == sorted(counts, reverse=True)


def test_catalog_stats(conn):
    stats = catalog_stats(conn=conn)
    assert stats["total"] > 0
    assert 0 <= stats["in_stock"] <= stats["total"]
    assert stats["synced_at"]


def test_limit_is_respected(conn):
    assert len(search_products("blīve", conn=conn, limit=3)) <= 3


# ---------------------------------------------------------------------------
# Izmēra svars ranžēšanā
# ---------------------------------------------------------------------------
def test_size_outweighs_adjectives_in_the_or_tier(conn):
    """Klienta skaitlis nedrīkst zaudēt aprakstošajiem vārdiem.

    "D veida pašlīmējošs blīvēšanas profils 12mm" nokrīt uz vaļīgāko OR tieru,
    jo "pašlīmējošs" un "12" kopā katalogā nesakrīt. Kamēr visi marķieri tika
    skaitīti vienādi, uzvarēja 10×15 un 10×13 — piecas vārdu sakritības pret
    vienu skaitli. 12 mm profili katalogā ir un ir noliktavā; tie vienkārši
    nokrita zem griezuma, un klientam aizgāja nepareizs izmērs.
    """
    result = search_products(
        "D veida pašlīmējošs blīvēšanas profils 12mm", conn=conn, limit=6
    )
    assert result, "12 mm D profili katalogā ir"
    names = [p.name for p in result]
    assert "12" in names[0], f"pirmais rezultāts bez 12 mm: {names[0]!r}"
    # Vismaz puse no izlases sedz klienta izmēru, nevis tikai apzīmētājus.
    with_size = sum(1 for n in names if "12" in n)
    assert with_size >= len(names) // 2, names


def test_size_mismatch_is_reported_not_hidden(conn):
    """Ja izmērs nesakrīt, izsaucējam par to jāzina."""
    result = search_products(
        "D veida pašlīmējošs blīvēšanas profils 999mm", conn=conn, limit=4
    )
    if result:
        assert result.size_mismatch
        assert any("nesakrīt" in note for note in result.notes)


def test_exact_tier_never_flags_size_mismatch(conn):
    """exact/stem tieros skaitlis ir AND ķēdē — sakritība ir garantēta."""
    result = search_products("U veida EPDM profils 2x8x12mm", conn=conn, limit=4)
    assert result
    assert result.strategy in ("exact", "stem")
    assert not result.size_mismatch
