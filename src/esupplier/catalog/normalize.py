"""Atribūtu parsēšana un normalizēšana.

Veikala atribūti nāk kā brīvs teksts un ir nekonsekventi ("no -40 līdz 130°C",
"–40 °C līdz +120 °C", "līdz +550°C"). Šeit tos pārvēršam kolonnās, ko var
filtrēt ar SQL.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

#: Visas tipogrāfiskās rakstzīmes, ko lapa lieto ASCII vietā, -> ASCII.
#:
#: Kritiskā šeit ir U+2033 (DOUBLE PRIME): katalogā 574 nosaukumos collas ir
#: rakstītas kā 4\u2033, un parasto pēdiņu tajos NAV nevienā. Bez šīs kartes
#: lietotāja 4" nekad nesakrīt ar katalogu, un collu parsēšana klusi atdod None.
CHAR_MAP = str.maketrans({
    # domuzīmes -> mīnuss
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u00ad": "",          # soft hyphen
    # prīmas un pēdiņas -> ASCII
    "\u2032": "'", "\u2033": '"', "\u2034": '"',
    "\u2018": "'", "\u2019": "'", "\u201a": "'",
    "\u02bc": "'", "\u00b4": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u02ba": '"',
    # atstarpes
    "\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u200b": "",
    # Unicode daļskaitļi collās
    "\u00bc": ".25", "\u00bd": ".5", "\u00be": ".75",
    "\u2153": ".333", "\u215b": ".125", "\u215c": ".375",
    "\u215d": ".625", "\u215e": ".875",
})

#: Collu marķieris. Pēc clean_text U+2033 jau ir ", bet pieņemam arī '' ,
#: apostrofu (drukas kļūda lapā) un vārdus.
_INCH = r"(?:\"|''|'|coll(?:as|u|ām)?|inch)"

#: Collas -> DN (nominālais diametrs mm).
INCH_TO_DN: dict[float, int] = {
    0.125: 6,
    0.25: 8,
    0.375: 10,
    0.5: 15,
    0.75: 20,
    1.0: 25,
    1.25: 32,
    1.5: 40,
    2.0: 50,
    2.5: 65,
    3.0: 80,
    3.5: 90,
    4.0: 100,
    5.0: 125,
    6.0: 150,
    8.0: 200,
    10.0: 250,
    12.0: 300,
    14.0: 350,
    16.0: 400,
    18.0: 450,
    20.0: 500,
    24.0: 600,
}

#: Materiāla kods -> kanoniskais kods. Atslēgas meklē kā veselus vārdus.
MATERIAL_CODES = {
    "EPDM", "NBR", "HNBR", "MVQ", "VMQ", "FKM", "FPM", "FVMQ", "CR", "NR",
    "SBR", "IIR", "CSM", "AEM", "ACM", "PTFE", "PVC", "PU", "TPU", "TPE",
    "PE", "HDPE", "LDPE", "PP", "POM", "PA", "PET", "PMMA", "ABS", "EVA",
    "PVDF", "PEEK", "PC",
}

#: Latviešu/angļu apzīmējums -> kanoniskais kods. Pārbauda pēc kārtas.
_MATERIAL_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("silikon", "MVQ"),
    ("neopr", "CR"),
    ("nitril", "NBR"),
    ("viton", "FKM"),
    ("teflon", "PTFE"),
    ("poliuret", "PU"),
    ("poliprop", "PP"),
    ("polietil", "PE"),
    ("polikarbon", "PC"),
    ("plexi", "PMMA"),
    ("akril", "PMMA"),
    ("but", "IIR"),
    ("dabisk", "NR"),
    ("hipalon", "CSM"),
)

#: Ne-polimēru materiāli — atstājam lasāmu latvisku nosaukumu.
_MATERIAL_PLAIN: tuple[tuple[str, str], ...] = (
    ("nerūsējoš", "Nerūsējošais tērauds"),
    ("nerusejos", "Nerūsējošais tērauds"),
    ("alumīnij", "Alumīnijs"),
    ("aluminij", "Alumīnijs"),
    ("bronz", "Bronza"),
    ("mess", "Misiņš"),
    ("misiņ", "Misiņš"),
    ("čugun", "Čuguns"),
    ("cinkot", "Cinkots tērauds"),
    ("tērauds", "Tērauds"),
    ("terauds", "Tērauds"),
    ("varš", "Varš"),
    ("grafīt", "Grafīts"),
    ("aramīd", "Aramīds"),
    ("stikla šķiedr", "Stikla šķiedra"),
    ("keramik", "Keramika"),
    ("korķ", "Korķis"),
    ("filc", "Filcs"),
    ("gumij", "Gumija"),
)

#: Krāsas nosaukuma sākums -> meklēšanas sinonīmi. Katalogā krāsa ir tikai
#: latviski ("Pelēka"), bet pieprasījums nāk arī krieviski un angliski, un
#: tieši krāsa ir tas, ko klients nosauc pirmo ("vajag pelēku, ne melnu").
COLOR_ALIASES: tuple[tuple[str, str], ...] = (
    ("meln", "melns melna чёрный черный black"),
    ("balt", "balts balta белый white"),
    ("pelēk", "peleks peleka серый grey gray"),
    ("pelek", "peleks peleka серый grey gray"),
    ("sarkan", "sarkans sarkana красный red"),
    ("zil", "zils zila синий голубой blue"),
    ("zaļ", "zals zala зелёный зеленый green"),
    ("zal", "zals zala зелёный зеленый green"),
    ("dzelten", "dzeltens dzeltena жёлтый желтый yellow"),
    ("brūn", "bruns bruna коричневый brown"),
    ("brun", "bruns bruna коричневый brown"),
    ("oranž", "oranzs oranza оранжевый orange"),
    ("oranz", "oranzs oranza оранжевый orange"),
    ("bēš", "besa бежевый beige"),
    ("bes", "besa бежевый beige"),
    ("caurspīdīg", "caurspidigs прозрачный transparent clear"),
    ("caurspidig", "caurspidigs прозрачный transparent clear"),
    ("dabisk", "dabiska натуральный natural"),
)

_TRUE_WORDS = {"jā", "ja", "yes", "true", "да", "1", "ir"}
_FALSE_WORDS = {"nē", "ne", "no", "false", "нет", "0", "nav"}


# ---------------------------------------------------------------------------
# Palīgi
# ---------------------------------------------------------------------------
def clean_text(value: str | None) -> str:
    """Vienādo tipogrāfiskās rakstzīmes, atstarpes un decimālkomatu.

    Piemēro VISIEM teksta laukiem sinhronizācijas laikā (nosaukums, apraksti,
    atribūtu nosaukumi un vērtības), lai katalogā būtu viena rakstība.
    """
    if not value:
        return ""
    text = str(value).translate(CHAR_MAP)
    # decimālkomats starp cipariem -> punkts
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return " ".join(text.split())


def _numbers(text: str) -> list[float]:
    """Visi skaitļi ar zīmi, secībā."""
    out: list[float] = []
    for m in re.finditer(r"([+-]?)\s*(\d+(?:\.\d+)?)", text):
        sign = -1.0 if m.group(1) == "-" else 1.0
        out.append(sign * float(m.group(2)))
    return out


def parse_bool(value: str | None) -> bool | None:
    """`Jā` / `Nē` -> bool. Nezināmu vērtību atgriež kā None."""
    if value is None:
        return None
    token = clean_text(value).strip().lower().rstrip(".")
    if token in _TRUE_WORDS:
        return True
    if token in _FALSE_WORDS:
        return False
    return None


# ---------------------------------------------------------------------------
# Temperatūra
# ---------------------------------------------------------------------------
def parse_temperature_range(value: str | None) -> tuple[float | None, float | None]:
    """Izvelk (min, max) °C.

    Atbalsta: "no -40 līdz 130°C", "–40 °C līdz +120 °C", "līdz +550°C",
    "-60...+200", "no –200 līdz +260 °C".
    """
    text = clean_text(value)
    if not text:
        return None, None

    low = text.lower()
    # Nogriežam mērvienības, lai tās netraucē skaitļu lasīšanai.
    stripped = re.sub(r"°\s*[cf]|\bgrādi?\b|\bc\b(?![a-zā-ž])", " ", low)

    nums = _numbers(stripped)
    if not nums:
        return None, None

    if len(nums) == 1:
        only = nums[0]
        # "līdz +550" / "up to 550" / "max 550" -> tikai augšējā robeža
        before = stripped.split(str(abs(only)).rstrip("0").rstrip("."))[0]
        if re.search(r"l[īi]dz|up to|max|maks|до", before):
            return None, only
        if re.search(r"\bno\b|from|min|от", before):
            return only, None
        return None, only

    lo, hi = nums[0], nums[1]
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


# ---------------------------------------------------------------------------
# Izmēri
# ---------------------------------------------------------------------------
def parse_mm(value: str | None) -> float | None:
    """Pirmais mm skaitlis: "6mm", "1,5mm", "4.5 mm", "15"."""
    text = clean_text(value)
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\b", text, re.I)
    if m:
        return float(m.group(1))
    # Bez mērvienības — pieņemam mm, ja tas ir viss saturs.
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def inch_to_dn(inches: float) -> int | None:
    """Collas -> DN. Neatbilstošai vērtībai atgriež tuvāko no tabulas ±2%."""
    if inches in INCH_TO_DN:
        return INCH_TO_DN[inches]
    for key, dn in INCH_TO_DN.items():
        if abs(key - inches) < 0.02:
            return dn
    return None


def parse_inches(text: str) -> float | None:
    """Izvelk collu vērtību: '1"', '1 1/2"', '3/4"', '2.5"', '1¼"'."""
    text = clean_text(text)
    # jaukts skaitlis: 1 1/2"
    m = re.search(rf"(\d+)\s+(\d+)\s*/\s*(\d+)\s*{_INCH}", text, re.I)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    # daļa: 3/4"
    m = re.search(rf"(?<![\d.])(\d+)\s*/\s*(\d+)\s*{_INCH}", text, re.I)
    if m:
        return int(m.group(1)) / int(m.group(2))
    # decimāls (arī no ¼ -> 1.25): 1.25"
    m = re.search(rf"(\d+(?:\.\d+)?)\s*{_INCH}", text, re.I)
    if m:
        return float(m.group(1))
    return None


def parse_dn(value: str | None) -> int | None:
    """DN no "DN80", "80", '3"', '100mm / 4"'."""
    text = clean_text(value)
    if not text:
        return None

    m = re.search(r"\bDN\s*[-/]?\s*(\d+)", text, re.I)
    if m:
        return int(m.group(1))

    # "100mm / 4\"" — mm ir precīzāks nekā collas
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\b", text, re.I)
    if m:
        return int(round(float(m.group(1))))

    inches = parse_inches(text)
    if inches is not None:
        return inch_to_dn(inches)

    m = re.fullmatch(r"(\d+)", text)
    if m:
        return int(m.group(1))
    return None


#: Profili, blīvgumijas, ķīļi un aizsargprofili. Nosaukumā tiem ir mm izmēri
#: ("1.5x6x12mm"), bet diametra NAV: tie ir taisni gabali, ne apaļas detaļas.
_PROFILE_NAME = re.compile(
    r"profil|blīvgumij|blivgumij|blīvējum|blivejum", re.I | re.UNICODE
)


def is_profile(name: str) -> bool:
    """Vai produkts ir profils/blīvgumija, kam DN nav jēgas.

    Nosaukuma pēdējais mm skaitlis profilam ir augstums, nevis diametrs.
    Kamēr tas nonāca `dn_mm` kolonnā, 323 no 324 profiliem katalogā bija
    izdomāts diametrs: "3×10.7×12.8 mm" kļuva par DN13. Ar to meklēšana pēc
    diametra atgrieza profilus, un pats skaitlis aizceļoja modelim kā
    "dn_mm": 13.
    """
    return bool(_PROFILE_NAME.search(clean_text(name)))


def parse_dn_pair(value: str | None) -> tuple[int | None, int | None]:
    """Abi izmēri no pārejas: '4"x6"' -> (100, 150), 'DN100 x DN150' -> (100, 150).

    Pārejai ir DIVI diametri, un klients var jautāt pēc jebkura no tiem, tāpēc
    vienu skaitli glabāt nepietiek — 92 kataloga pārejas citādi atrodamas
    tikai pēc puses no sava izmēra.
    """
    text = clean_text(value)
    if not text:
        return None, None

    sizes: list[int] = []
    # DN pieraksts: "DN100 x DN150"
    for m in re.finditer(r"\bDN\s*[-/]?\s*(\d+)", text, re.I):
        sizes.append(int(m.group(1)))
    if not sizes:
        # Collu pieraksts: 4"x6", 1 1/2"x2", 2.5"x2"
        for m in re.finditer(
            rf"(\d+(?:\s+\d+/\d+|\.\d+)?)\s*{_INCH}", text, re.I
        ):
            dn = inch_to_dn(parse_inches(m.group(0)) or -1)
            if dn is not None:
                sizes.append(dn)
    if not sizes:
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*mm\b", text, re.I):
            sizes.append(int(round(float(m.group(1)))))

    first = sizes[0] if sizes else None
    second = sizes[1] if len(sizes) > 1 and sizes[1] != first else None
    return first, second


# ---------------------------------------------------------------------------
# Savienojumu tipu kodi
# ---------------------------------------------------------------------------
#: Camlock pamattipi — viens izmērs.
CAMLOCK_TYPES = {"A", "B", "C", "D", "E", "F", "DC", "DP"}

#: Pārejas starp diviem dažādiem izmēriem. Saraksts ievākts no paša kataloga,
#: nevis no dokumentācijas: `R` galotne nozīmē "reducing", un BR/FR/DR/ER/CR
#: visi reāli nes divus izmērus (31 produkts, ko sākotnējais saraksts izlaida).
REDUCER_TYPES = {
    "AR", "DAR", "SAR", "DRVR", "OLS",
    "BR", "FR", "DR", "ER", "CR", "CVR", "DVR",
}

#: Pārējie kategorijā "Camlock Pārejas" — adapteri, kas nav izmēru reducijas.
ADAPTER_TYPES = {"SA", "DD", "GZ", "DCL"}

ALL_TYPE_CODES = CAMLOCK_TYPES | REDUCER_TYPES | ADAPTER_TYPES


def parse_type_code(name: str, attributes: Mapping[str, str]) -> str | None:
    """Savienojuma tipa kods ("AR", "DAR", "C") kā strukturēta vērtība.

    Tekstā to meklēt nevar: "AR" kā FTS marķieris sakrīt ar latviešu vārdu
    "ar" un atgriež 2803 no 3552 produktiem. Tāpēc kods jāglabā kolonnā un
    jāfiltrē ar SQL.
    """
    for key, value in attributes.items():
        if re.fullmatch(r"\s*tips\s*", key, re.I):
            token = clean_text(value).upper()
            if token in ALL_TYPE_CODES:
                return token
    m = re.search(r"\btype\s+([A-Z]{1,4})\b", clean_text(name), re.I)
    if m and m.group(1).upper() in ALL_TYPE_CODES:
        return m.group(1).upper()
    return None


# ---------------------------------------------------------------------------
# Meklēšanas aliasi
# ---------------------------------------------------------------------------
#: Materiāla kods -> kā to sauc klienti. Katalogs raksta "MVQ gumija", klients
#: raksta "silikona gumija", un bez šīs kartes tie nekad nesatiekas.
MATERIAL_ALIASES: dict[str, str] = {
    "MVQ": "silikons silikona silikonu silicone силикон",
    "VMQ": "silikons silikona silikonu silicone силикон",
    "NBR": "nitrils nitrila nitrile buna бутадиен",
    "HNBR": "nitrils nitrila hidrogenēts nitrile",
    "FKM": "vitons vitona viton fpm фторкаучук",
    "FPM": "vitons vitona viton fkm",
    "PTFE": "teflons teflona teflon тефлон",
    "CR": "neoprēns neoprēna neoprene хлоропрен",
    "EPDM": "epdm etilēns эпдм",
    "NR": "dabiskā gumija kaučuks natural rubber",
    "SBR": "butadiēna stirola sbr",
    "PU": "poliuretāns poliuretāna polyurethane полиуретан",
    "TPU": "poliuretāns poliuretāna polyurethane",
    "PVC": "pvc vinils vinila",
    "PE": "polietilēns polietilēna polyethylene",
    "HDPE": "polietilēns polietilēna hdpe",
    "PP": "polipropilēns polipropilēna polypropylene",
    "Alumīnijs": "alumīnijs alumīnija alu aluminium aluminum алюминий",
    "Nerūsējošais tērauds": (
        "nerūsējošais nerūsējošā nerusejosais inox stainless "
        "aisi 304 aisi 316 ss304 ss316 нержавейка"
    ),
    "SS304": "nerūsējošais nerūsējošā inox stainless aisi 304 нержавейка",
    "SS316": "nerūsējošais nerūsējošā inox stainless aisi 316 нержавейка",
    "SS316L": "nerūsējošais nerūsējošā inox stainless aisi 316l",
    "Misiņš": "misiņš misiņa brass латунь",
    "Bronza": "bronza bronzas bronze",
    "Tērauds": "tērauds tērauda steel сталь",
    "Cinkots tērauds": "cinkots cinkota tērauds galvanized",
}

_ALIAS_REDUCER = (
    "pāreja pārejas pārejai reducija reducējošs adapteris adapters "
    "reducer adapter переход переходник"
)
_ALIAS_THREAD = "vītne vītni vītnes thread BSP NPT резьба"
_ALIAS_HOSE = "šļūtenes uzmava uzmavu eglīte hose tail штуцер"


def build_aliases(
    name: str,
    category: str = "",
    type_code: str | None = None,
    material: str | None = None,
    thickness_mm: float | None = None,
    color: str | None = None,
) -> str:
    """Papildu meklēšanas teksts: klienta vārdi -> kataloga kodi.

    Klients raksta aprakstoši ("pāreja no 4 collām uz 6"), katalogs runā
    kodos ("type AR 4\"x6\""). Šī ir tā tulkošanas josla.
    """
    text = clean_text(name)
    parts: list[str] = []

    # Katram izmēram: DN ekvivalents un collas vārdiem.
    for m in re.finditer(rf"(\d+(?:\s+\d+/\d+|\.\d+)?)\s*{_INCH}", text, re.I):
        inches = parse_inches(m.group(0))
        if inches is None:
            continue
        dn = inch_to_dn(inches)
        label = m.group(1).strip()
        if dn:
            parts.append(f"DN{dn}")
        parts += [f"{label} collas", f"{label} collu", f"{label} inch"]

    code = (type_code or "").upper()
    if code in REDUCER_TYPES or "pāreja" in category.lower():
        parts.append(_ALIAS_REDUCER)
    if code in {"A", "B", "D", "F", "AR", "BR", "FR", "DR"}:
        parts.append(_ALIAS_THREAD)
    if code in {"C", "E", "CR", "ER"}:
        parts.append(_ALIAS_HOSE)

    if material:
        synonyms = MATERIAL_ALIASES.get(material)
        if synonyms:
            parts.append(synonyms)

    # Biezums ir kolonnā, bet nosaukumā to bieži nav tādā formā, kā raksta
    # klients: "MVQ gumija 2x1200mm" nesatur marķieri "2mm", tāpēc vaicājums
    # "silikona gumija 2mm" to nekad neatrastu.
    if thickness_mm:
        value = int(thickness_mm) if float(thickness_mm).is_integer() else thickness_mm
        parts.append(f"{value}mm {value} mm")

    # Krāsa ir atribūtā, ne nosaukumā — bez šī "pelēks EPDM profils" neatrod
    # neko, lai gan katalogā pelēki profili ir.
    alias = color_aliases(color)
    if alias:
        parts.append(alias)

    return " ".join(dict.fromkeys(" ".join(parts).split()))


def parse_length_m(value: str | None) -> float | None:
    """Garums metros no "L-7m", "L-3.05m", "25 m", "garums 10m"."""
    text = clean_text(value)
    if not text:
        return None
    m = re.search(r"\bL\s*[-–]\s*(\d+(?:\.\d+)?)\s*m\b", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*m(?:etr[iu]?)?\b(?!m)", text, re.I)
    if m:
        return float(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Spiediens / cietība
# ---------------------------------------------------------------------------
def parse_pressure_bar(value: str | None) -> float | None:
    """No "PN16", "16 bar", "85-90 bar" (ņem zemāko), "~120 bar"."""
    text = clean_text(value)
    if not text:
        return None
    m = re.search(r"\bPN\s*[-/]?\s*(\d+(?:\.\d+)?)", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*\d+(?:\.\d+)?\s*)?bar", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*MPa", text, re.I)
    if m:
        return float(m.group(1)) * 10
    return None


def parse_hardness(value: str | None) -> float | None:
    """Cietība Shore A: "65 ShA", "50-70 Shore" (ņem zemāko)."""
    text = clean_text(value)
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*\d+(?:\.\d+)?\s*)?(?:sh|shore)", text, re.I)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Materiāls
# ---------------------------------------------------------------------------
def parse_color(value: str | None) -> str | None:
    """Krāsa tā, kā to raksta katalogs — bez izdomāta kanoniskā nosaukuma.

    Vērtības ir salikteņi ("Melna / tumši pelēka", "Dzeltens/Melns"), un
    saīsināt tās līdz vienam vārdam nozīmētu pateikt klientam ko citu, nekā
    ir preces aprakstā.
    """
    cleaned = clean_text(value)
    return cleaned or None


def color_aliases(color: str | None) -> str:
    """Krāsas sinonīmi meklēšanai (LV/RU/EN)."""
    if not color:
        return ""
    lowered = color.lower()
    parts = [
        aliases
        for prefix, aliases in COLOR_ALIASES
        if any(word.startswith(prefix) for word in re.split(r"[^\w]+", lowered) if word)
    ]
    return " ".join(dict.fromkeys(" ".join(parts).split()))


def normalize_material(value: str | None) -> str | None:
    """"Silikons(MVQ)" -> "MVQ", "NBR (Nitrila)" -> "NBR"."""
    text = clean_text(value)
    if not text:
        return None

    # Tērauda marka: SS316, AISI 304, 1.4301
    m = re.search(r"\b(?:SS|AISI)\s*[-]?\s*(\d{3}\w?)\b", text, re.I)
    if m:
        return f"SS{m.group(1).upper()}"
    m = re.search(r"\b1\.(4301|4401|4404|4571|4541)\b", text)
    if m:
        return {"4301": "SS304", "4401": "SS316", "4404": "SS316L",
                "4571": "SS316Ti", "4541": "SS321"}[m.group(1)]

    # Kods iekavās vai brīvi tekstā (veselu vārdu sakritība).
    for token in re.findall(r"[A-Za-z]{2,6}", text):
        if token.upper() in MATERIAL_CODES:
            return token.upper()

    lower = text.lower()
    for needle, code in _MATERIAL_KEYWORDS:
        if needle in lower:
            return code
    for needle, label in _MATERIAL_PLAIN:
        if needle in lower:
            return label

    # Nezināms — atdodam iztīrītu oriģinālu, lai neko nepazaudētu.
    return text[:60]


# ---------------------------------------------------------------------------
# Pārtikas / eļļas / ķīmijas karogi
# ---------------------------------------------------------------------------
_FOOD_STRONG = re.compile(
    r"\bFDA\b|\bEC\s*1935\b|1935\s*/\s*2004|\bBfR\b|\bUSP\s*(?:VI|class)\b"
    r"|p[āa]rtikas\s+(?:klas|kvalit|sertif|rūpniec|ražoš|produkt|saskar)"
    r"|piemērots\s+p[āa]rtikai|food[\s-]?grade|пищев",
    re.I,
)
_OIL_RE = re.compile(r"eļļ\w*\s*(?:/|un|,)?\s*benz\w*\s*izturīg|eļļas\s*izturīg|oil[\s-]?resistant|маслостойк", re.I)
_CHEM_RE = re.compile(r"ķīmij\w*\s*izturīg|chemical[\s-]?resistant|химически\s*стойк", re.I)


def detect_food_grade(name: str, attributes: Mapping[str, str], text: str = "") -> bool:
    haystack_strong = f"{name} {' '.join(f'{k} {v}' for k, v in attributes.items())}"
    if _FOOD_STRONG.search(haystack_strong):
        return True
    return bool(_FOOD_STRONG.search(text))


def detect_oil_resistant(attributes: Mapping[str, str], name: str = "", text: str = "") -> bool:
    for key, value in attributes.items():
        if _OIL_RE.search(key):
            flag = parse_bool(value)
            if flag is not None:
                return flag
    return bool(_OIL_RE.search(name) or _OIL_RE.search(text))


def detect_chemical_resistant(attributes: Mapping[str, str], name: str = "", text: str = "") -> bool:
    for key, value in attributes.items():
        if _CHEM_RE.search(key):
            flag = parse_bool(value)
            if flag is not None:
                return flag
    return bool(_CHEM_RE.search(name) or _CHEM_RE.search(text))


# ---------------------------------------------------------------------------
# Atribūtu atslēgu atpazīšana
# ---------------------------------------------------------------------------
def _find(attributes: Mapping[str, str], *patterns: str) -> str | None:
    for pattern in patterns:
        rx = re.compile(pattern, re.I)
        for key, value in attributes.items():
            if rx.search(key) and value:
                return value
    return None


def normalize_attributes(
    name: str,
    attributes: Mapping[str, str],
    description: str = "",
) -> dict[str, Any]:
    """Visu normalizēto kolonnu aprēķins vienam produktam."""
    text = f"{name}\n{description}"

    temp_raw = _find(attributes, r"temperat", r"darba\s*temp")
    temp_min, temp_max = parse_temperature_range(temp_raw)

    dn_raw = _find(attributes, r"^\s*DN\b", r"diametr", r"izm[ēe]r", r"^\s*Ø")
    dn = parse_dn(dn_raw)
    if dn is None:
        dn = parse_dn(_extract_dn_from_name(name))

    material = normalize_material(_find(attributes, r"materi[āa]l"))

    return {
        "temp_min_c": temp_min,
        "temp_max_c": temp_max,
        "thickness_mm": parse_mm(_find(attributes, r"biezum", r"thickness")),
        "dn_mm": dn,
        "pressure_bar": parse_pressure_bar(
            _find(attributes, r"spiedien", r"pressure", r"^\s*PN\b")
        ),
        "material": material,
        "length_m": parse_length_m(_find(attributes, r"garum") or "") or parse_length_m(name),
        "hardness_sha": parse_hardness(_find(attributes, r"cietīb", r"hardness")),
        "color": parse_color(_find(attributes, r"krāsa", r"krasa", r"colou?r", r"цвет")),
        "standard": (_find(attributes, r"standart") or None),
        "food_grade": detect_food_grade(name, attributes, description),
        "oil_resistant": detect_oil_resistant(attributes, name, description),
        "chemical_resistant": detect_chemical_resistant(attributes, name, description),
    }


def _extract_dn_from_name(name: str) -> str:
    """Nosaukumos izmērs mēdz būt bez atsevišķa atribūta.

    "Camlock blīve DN25 ...", "MILKFLEX šļūtene Ø51mm 6bar", 'Uzmava 2"'.
    Ø ir nominālais izmērs, ko norāda katalogs — tas ne vienmēr ir tas pats,
    kas DN, tāpēc to lietojam tikai tad, ja atsevišķa DN atribūta nav.
    """
    text = clean_text(name)
    m = re.search(r"\bDN\s*(\d+)", text, re.I)
    if m:
        return m.group(0)
    m = re.search(r"[ØØø⌀]\s*(\d+(?:\.\d+)?)\s*mm\b", text, re.I)
    if m:
        return f"{m.group(1)}mm"
    m = re.search(r"(?<![\d.x])(\d+(?:\s+\d+/\d+|\.\d+)?)\s*(?:\"|''|”|')", text)
    return m.group(0) if m else ""


def detect_unit(name: str, attributes: Mapping[str, str]) -> str | None:
    """Mērvienība, ja tā TIEŠĀM ir tekstā. `None` = teksts neko nepasaka.

    Veikala datos mērvienības praktiski nav (skat. `units.py`), tāpēc šis ir
    tikai papildu signāls tiem retajiem nosaukumiem, kur cena par metru ir
    ierakstīta ar roku. Kas paliek pāri, izšķiras pēc kategorijas.

    Agrāk šī funkcija atgrieza "gab" arī tad, kad neatrada neko, un tas
    aizsedza problēmu: visi 3568 produkti bija gabalos, un neviens signāls
    nerādīja, ka mērvienība vispār nav noteikta.
    """
    # Vērtības, ne tikai atslēgas: `' '.join(dict)` savieno atslēgas, un
    # "Mērvienība: m" tādā virknē nekad neparādījās.
    haystack = f"{name} {' '.join(attributes)} {' '.join(attributes.values())}"
    if re.search(r"€\s*/\s*m2|EUR\s*/\s*m2|kvadr[āa]tmetr", haystack, re.I):
        return "m2"
    if re.search(r"€\s*/\s*m\b|EUR\s*/\s*m\b|cena\s*par\s*metru", haystack, re.I):
        return "m"
    return None


def attributes_to_dict(raw: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Store API `attributes[]` -> {nosaukums: "vērtība, vērtība"}.

    Gan atslēgas, gan vērtības iet caur `clean_text` — atribūtos ir tie paši
    U+2033 un domuzīmes, kas nosaukumos.
    """
    out: dict[str, str] = {}
    for attr in raw or []:
        key = clean_text(attr.get("name"))
        if not key:
            continue
        terms = [clean_text(t.get("name")) for t in attr.get("terms") or []]
        terms = [t for t in terms if t]
        if not terms and attr.get("value"):
            terms = [clean_text(str(attr["value"]))]
            terms = [t for t in terms if t]
        if terms:
            out[key] = ", ".join(terms)
    return out


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
