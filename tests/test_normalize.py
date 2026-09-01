"""Normalizēšanas testi.

Visi piemēri ir reāli formāti no e-supplier.lv kataloga.
"""

from __future__ import annotations

import pytest

from esupplier.catalog.normalize import (
    clean_text,
    detect_chemical_resistant,
    detect_food_grade,
    detect_oil_resistant,
    inch_to_dn,
    is_profile,
    normalize_attributes,
    normalize_material,
    parse_bool,
    parse_dn,
    parse_hardness,
    parse_length_m,
    parse_mm,
    parse_pressure_bar,
    parse_temperature_range,
)


# ---------------------------------------------------------------------------
# Temperatūra
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # specifikācijā uzskaitītie
        ("no -40 līdz 130°C", (-40.0, 130.0)),
        ("No -10 līdz 80°C", (-10.0, 80.0)),
        ("no –50 līdz 200°C", (-50.0, 200.0)),  # en-dash mīnusa vietā
        # reālie katalogā atrastie
        ("–40 °C līdz +120 °C", (-40.0, 120.0)),
        ("-60 °C līdz +200 °C", (-60.0, 200.0)),
        ("no –200 līdz +260 °C", (-200.0, 260.0)),
        ("no -240 līdz 280°C", (-240.0, 280.0)),
        ("no -10 līdz +80°C", (-10.0, 80.0)),
        ("līdz +550°C", (None, 550.0)),
        ("līdz 250°C", (None, 250.0)),
        ("no -5 līdz 60°C", (-5.0, 60.0)),
        # citas domuzīmes
        ("no −30 līdz 140°C", (-30.0, 140.0)),  # U+2212 minus
        ("no —20 līdz 90°C", (-20.0, 90.0)),  # em-dash
        # tukšums
        ("", (None, None)),
        (None, (None, None)),
        ("nav norādīts", (None, None)),
    ],
)
def test_parse_temperature_range(raw, expected):
    assert parse_temperature_range(raw) == expected


def test_temperature_swaps_reversed_bounds():
    assert parse_temperature_range("no 130 līdz -40 °C") == (-40.0, 130.0)


# ---------------------------------------------------------------------------
# Biezums
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("6mm", 6.0),
        ("2mm", 2.0),
        ("1,5mm", 1.5),  # decimālkomats
        ("4.5mm", 4.5),
        ("100mm", 100.0),
        ("15", 15.0),
        ("3 mm", 3.0),
        ("", None),
        (None, None),
    ],
)
def test_parse_mm(raw, expected):
    assert parse_mm(raw) == expected


# ---------------------------------------------------------------------------
# DN un collas
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("inches", "dn"),
    [
        (0.5, 15), (0.75, 20), (1.0, 25), (1.25, 32), (1.5, 40),
        (2.0, 50), (2.5, 65), (3.0, 80), (4.0, 100), (6.0, 150), (8.0, 200),
    ],
)
def test_inch_to_dn_table(inches, dn):
    assert inch_to_dn(inches) == dn


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # specifikācijā uzskaitītie
        ("DN80", 80),
        ('1"', 25),
        ('3"', 80),
        # reālie katalogā atrastie
        ("DN50", 50),
        ("DN125", 125),
        ("15", 15),
        ('100mm / 4"', 100),  # mm ir precīzāks par collām
        ('50mm / 2"', 50),
        ('4"', 100),
        ('6"', 150),
        ("25mm", 25),
        ("dn 32", 32),
        # collu daļskaitļi
        ('1/2"', 15),
        ('3/4"', 20),
        ('1 1/2"', 40),
        ("1½\"", 40),
        ("1¼\"", 32),
        ("2½\"", 65),
        ("1'", 25),  # apostrofs kā collas zīme (drukas kļūda lapā)
        # nav DN
        ("", None),
        (None, None),
        ("Monolīts", None),
    ],
)
def test_parse_dn(raw, expected):
    assert parse_dn(raw) == expected


def test_parse_dn_ignores_sheet_dimensions():
    """"1500x1500mm" ir loksnes izmērs, nevis DN."""
    assert parse_dn("1500x1500mm") == 1500  # pirmais mm skaitlis
    # bet caur atribūtu loģiku loksnes izmērs nedrīkst kļūt par DN


# ---------------------------------------------------------------------------
# Spiediens
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("16 bar", 16.0),
        ("PN16", 16.0),
        ("PN6", 6.0),
        ("PN40", 40.0),
        ("PN16/PN16", 16.0),
        ("200 bar", 200.0),
        ("85-90 bar", 85.0),  # diapazons -> zemākā (droša) robeža
        ("~120 bar", 120.0),
        ("1,6 MPa", 16.0),
        ("", None),
        (None, None),
    ],
)
def test_parse_pressure_bar(raw, expected):
    assert parse_pressure_bar(raw) == expected


# ---------------------------------------------------------------------------
# Cietība
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("65 ShA", 65.0), ("85 ShA", 85.0), ("50–70 Shore", 50.0), ("", None)],
)
def test_parse_hardness(raw, expected):
    assert parse_hardness(raw) == expected


# ---------------------------------------------------------------------------
# Materiāls
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # specifikācijā uzskaitītie
        ("Silikons(MVQ)", "MVQ"),
        ("NBR (Nitrila)", "NBR"),
        ("Nerūsējošais tērauds SS316", "SS316"),
        # reālie katalogā atrastie
        ("EPDM", "EPDM"),
        ("Neoprēns (CR)", "CR"),
        ("FKM (Viton)", "FKM"),
        ("PTFE(Teflons)", "PTFE"),
        ("PARA (NR)", "NR"),
        ("Nerūsējošais tērauds", "Nerūsējošais tērauds"),
        ("Alumīnijs", "Alumīnijs"),
        ("Bronza", "Bronza"),
        ("Polipropilēns", "PP"),
        ("HDPE/polietilēns", "HDPE"),
        ("Poliuretāns", "PU"),
        ("W1 – cinkots tērauds", "Cinkots tērauds"),
        ("AISI 304", "SS304"),
        ("1.4301", "SS304"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_material(raw, expected):
    assert normalize_material(raw) == expected


def test_normalize_material_keeps_unknown_text():
    raw = "Aramīda šķiedras + minerālšķiedras ar NBR saistvielu"
    # satur kodu NBR -> atpazīst to
    assert normalize_material(raw) == "NBR"
    assert normalize_material("Kompozītmateriāls") == "Kompozītmateriāls"


# ---------------------------------------------------------------------------
# Boolean atribūti
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Jā", True), ("jā", True), ("Nē", False), ("nē", False), ("Yes", True),
     ("да", True), ("нет", False), ("varbūt", None), (None, None)],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected


def test_detect_chemical_resistant():
    assert detect_chemical_resistant({"Ķīmijas izturīgs": "Jā"}) is True
    assert detect_chemical_resistant({"Ķīmijas izturīgs": "Nē"}) is False
    assert detect_chemical_resistant({"Agresīvas ķīmijas izturīgs": "Jā"}) is True
    assert detect_chemical_resistant({}) is False


def test_detect_oil_resistant():
    assert detect_oil_resistant({"Eļļas/benzīna izturīga": "Jā"}) is True
    assert detect_oil_resistant({"Eļļas/benzīna izturīga": "Nē"}) is False
    assert detect_oil_resistant({}) is False


# ---------------------------------------------------------------------------
# Pārtikas sertifikācija
# ---------------------------------------------------------------------------
def test_detect_food_grade_from_name():
    assert detect_food_grade("Silikona profils 10x5mm L-10m, FDA", {}) is True


def test_detect_food_grade_from_description():
    assert detect_food_grade("Šļūtene", {}, "Atbilst EC 1935/2004 prasībām.") is True
    assert detect_food_grade("Šļūtene", {}, "FDA sertificēts materiāls") is True
    assert detect_food_grade("Šļūtene", {}, "pārtikas kvalitātes silikons") is True


def test_detect_food_grade_negative():
    """Vispārīgs teksts nedrīkst kļūdaini iezīmēt produktu kā pārtikas."""
    assert detect_food_grade("EPDM gumijas profils", {}) is False
    assert detect_food_grade(
        "EPDM profils", {}, "Izmanto būvniecībā, logiem un durvīm."
    ) is False


# ---------------------------------------------------------------------------
# Garums no nosaukuma
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("I veida EPDM gumijas profils 25x85mm L-7m", 7.0),
        ("I veida EPDM gumijas profils 20x75mm L-3.05m", 3.05),
        ("porains EPDM profils 4x60mm pašlīmējošs L-25m", 25.0),
        ("Blīve DN50", None),
    ],
)
def test_parse_length_m(raw, expected):
    assert parse_length_m(raw) == expected


# ---------------------------------------------------------------------------
# Viss kopā
# ---------------------------------------------------------------------------
def test_normalize_attributes_end_to_end():
    result = normalize_attributes(
        name="Silikona šļūtene DN25 L-10m, FDA",
        attributes={
            "Materiāls": "Silikons(MVQ)",
            "Darba temperatūra": "no –50 līdz 200°C",
            "Biezums": "6mm",
            "Spiediena klase": "PN16",
            "Ķīmijas izturīgs": "Jā",
            "Eļļas/benzīna izturīga": "Nē",
        },
        description="Pārtikas kvalitātes silikons ar FDA sertifikātu.",
    )
    assert result["material"] == "MVQ"
    assert result["temp_min_c"] == -50.0
    assert result["temp_max_c"] == 200.0
    assert result["thickness_mm"] == 6.0
    assert result["pressure_bar"] == 16.0
    assert result["dn_mm"] == 25
    assert result["length_m"] == 10.0
    assert result["food_grade"] is True
    assert result["chemical_resistant"] is True
    assert result["oil_resistant"] is False
    # Mērvienība šeit vairs nenāk: to nosaka kategorija, kuras
    # `normalize_attributes` neredz. Skat. `catalog/units.py`.
    assert "unit" not in result


def test_normalize_attributes_reads_colour():
    from esupplier.catalog.normalize import color_aliases, parse_color

    result = normalize_attributes("U veida profils", {"Krāsa": "Pelēka"})
    assert result["color"] == "Pelēka"
    # Salikteni nesaīsinām — "Melna / tumši pelēka" nav "Melna".
    assert parse_color("Melna / tumši pelēka") == "Melna / tumši pelēka"
    assert parse_color("") is None
    # Meklēšanai krāsa vajadzīga arī klienta valodā.
    assert "серый" in color_aliases("Pelēka")
    assert "чёрный" in color_aliases("Melns")
    assert color_aliases(None) == ""


def test_aliases_include_colour_synonyms():
    from esupplier.catalog.normalize import build_aliases

    aliases = build_aliases("U veida EPDM profils 12x22x25mm", color="Pelēka")
    assert "серый" in aliases
    assert "grey" in aliases


def test_normalize_attributes_missing_fields_stay_none():
    result = normalize_attributes("Alumīnija profils", {"Materiāls": "Alumīnijs"})
    assert result["material"] == "Alumīnijs"
    assert result["temp_min_c"] is None
    assert result["temp_max_c"] is None
    assert result["dn_mm"] is None
    assert result["pressure_bar"] is None
    assert result["food_grade"] is False


# ---------------------------------------------------------------------------
# Tipogrāfiskās rakstzīmes (U+2033 u.c.)
# ---------------------------------------------------------------------------
def test_clean_text_double_prime():
    """Katalogā collas ir U+2033, nevis parastās pēdiņas."""
    assert clean_text("AL Camlock type AR 4″x6″ BSP") == 'AL Camlock type AR 4"x6" BSP'


def test_clean_text_dashes_and_degrees():
    assert clean_text("no –50 līdz 200°C") == "no -50 līdz 200°C"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("‘viens’", "'viens'"),
        ("“divi”", '"divi"'),
        ("1′", "1'"),
        ("a b", "a b"),
        ("2−3", "2-3"),
        ("AL Camlock type SAR 1,5″x2″", 'AL Camlock type SAR 1.5"x2"'),
    ],
)
def test_clean_text_char_map(raw, expected):
    assert clean_text(raw) == expected


def test_inch_parsing_survives_double_prime():
    from esupplier.catalog.normalize import parse_inches
    assert parse_inches("AL Camlock type AR 4″x6″ BSP") == 4.0
    assert parse_dn("4″") == 100


# ---------------------------------------------------------------------------
# Pārejas: divi diametri, tipa kodi, aliasi
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AL Camlock type AR 4″x6″ BSP", (100, 150)),
        ("AL Camlock type DAR 2″x1.5″", (50, 40)),
        ("AL Camlock type C DN50 (2″)", (50, None)),
        ("DN100 x DN150", (100, 150)),
        ("Blīve 25mm", (25, None)),
        ("", (None, None)),
    ],
)
def test_parse_dn_pair(raw, expected):
    from esupplier.catalog.normalize import parse_dn_pair
    assert parse_dn_pair(raw) == expected


def test_parse_type_code_from_attribute():
    from esupplier.catalog.normalize import parse_type_code
    assert parse_type_code("AL Camlock type AR 4\"x6\" BSP", {"Tips": "AR"}) == "AR"
    assert parse_type_code("AL Camlock type DAR 4\"x6\"", {}) == "DAR"
    assert parse_type_code("AL Camlock type FR 2\"x3\" BSP", {}) == "FR"
    assert parse_type_code("Silikona gumija 2mm", {"Tips": "Monolīts"}) is None


def test_aliases_translate_customer_words_to_catalog_codes():
    from esupplier.catalog.normalize import build_aliases
    aliases = build_aliases(
        "AL Camlock type AR 4\"x6\" BSP",
        "Šļūtenu savienojumi > Camlock tipa savienojumi > Camlock Pārejas",
        "AR",
    )
    for word in ["DN100", "DN150", "pāreja", "reducija", "adapteris", "vītne", "переход"]:
        assert word in aliases, word


def test_aliases_do_not_leak_reducer_words_onto_plain_parts():
    from esupplier.catalog.normalize import build_aliases
    aliases = build_aliases("AL Camlock type C DN50 (2\")", "Camlock tipa savienojumi", "C")
    assert "pāreja" not in aliases
    assert "uzmava" in aliases


def test_material_aliases_bridge_catalog_codes_to_customer_words():
    """Katalogs raksta "MVQ gumija", klients meklē "silikona gumija"."""
    from esupplier.catalog.normalize import build_aliases
    aliases = build_aliases("MVQ gumija 2x1200mm", "Tehniskā gumija", None, "MVQ")
    assert "silikona" in aliases
    assert "silicone" in aliases

    steel = build_aliases("DIN 11851 savienojums DN50", "", None, "Nerūsējošais tērauds")
    assert "nerūsējošais" in steel
    assert "aisi" in steel

    # Materiāls bez sinonīmiem neko nesabojā
    assert build_aliases("Kaut kas", "", None, "Kompozītmateriāls") == ""


# ---------------------------------------------------------------------------
# Profils nav apaļa detaļa
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "U veida EPDM blīvēšanas profils 1.5x6x12mm",
        "AO veida armēts EPDM gumijas blīvēšanas profils 3×10.7×12.8 mm \\ Melns",
        "H veida EPDM gumijas profila ķīlis 7×8.5 mm",
        "B Pašlīmējošs malu aizsargprofils 40 mm",
    ],
)
def test_is_profile_atpazist_profilus(name: str) -> None:
    assert is_profile(name)


@pytest.mark.parametrize(
    "name",
    [
        'AR 4"x6" BSP camlock pāreja',
        "Camlock blīve DN25 NBR",
        "Silikona šļūtene DN50",
    ],
)
def test_is_profile_neaiztiek_apalas_detalas(name: str) -> None:
    assert not is_profile(name)

