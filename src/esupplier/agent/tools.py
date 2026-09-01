"""Rīku definīcijas un izpilde."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..catalog import normalize
from ..catalog import search as catalog_search

#: Cik produktu maksimāli atdodam modelim vienā rīka izsaukumā. Zems apzināti:
#: 28 rezultāti vienā gājienā noēda 22k tokenu un nepadarīja atbildi labāku.
#: Sešiem ir vēl viens iemesls: vēstulē tāpat nonāk 2–3 varianti, un pārējie
#: tikai dod modelim iespēju izvēlēties sliktāk.
MAX_RESULTS = 6
#: `browse_category` drīkst vairāk — tur mērķis ir pārskats, ne izlase. Bet
#: ne 100: kategorijas saraksts paliek vēsturē un tiek pārsūtīts pie KATRA
#: nākamā rīka izsaukuma, tāpēc viena 100 ierakstu lapa astoņu izsaukumu
#: gājienā maksā astoņas reizes. 30 pietiek, lai ģimeni atpazītu, un lapot
#: var tālāk.
MAX_BROWSE = 30
#: Cik gara apraksta daļa nonāk `get_product` atbildē.
DESCRIPTION_LIMIT = 1200

#: Rīku definīcijas neitrālā formā (nosaukums / apraksts / parametri), lai
#: tās varētu pārnest uz citu API bez pārrakstīšanas. OpenAI formātu no tām
#: uzbūvējam zemāk.
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "search_products",
        "description": (
            "Meklē produktus e-supplier.lv katalogā. Izsauc šo rīku vienmēr, "
            "pirms nosauc kaut vienu produktu, cenu vai pieejamību — bez tā tev "
            "nav datu.\n\n"
            "Izmanto plašu `query` (lietotāja vārdiem, garumzīmes nav svarīgas) "
            "un tikai tos filtrus, ko lietotājs tiešām nosauca. Ja lietotājs nav "
            "norādījis diametru vai temperatūru, NEIZDOMĀ tos — meklē bez šiem "
            "filtriem un pēc tam pajautā.\n\n"
            "Ja nekas neatrodas, pamēģini vēlreiz ar citiem vārdiem vai īsāku "
            "vaicājumu, pirms secini, ka katalogā tā nav.\n\n"
            "SVARĪGI: parametru, ko lietotājs nav nosaucis, IZLAID pavisam. "
            "Nesūti 0, false vai tukšu virkni kā \"nav norādīts\" — tie tiek "
            "saprasti kā īsti filtri un sabojā rezultātu.\n\n"
            f"Atgriež līdz {MAX_RESULTS} saīsinātiem ierakstiem. "
            "`price_eur_excl_vat` ir cena "
            "BEZ PVN; ja tā ir null, cena katalogā nav publicēta — neizdomā to. "
            "`image_url` ir produkta foto — to KOPĒ atbildē burtu pa burtam "
            "(![nosaukums](image_url)); nekad neizdomā attēla adresi. "
            "Lauks `notes` brīdina, ja filtri tika atlaisti, lai vispār kaut ko "
            "atrastu."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Brīvs teksts: produkta veids, materiāls, pielietojums. "
                        'Piem. "silikona šļūtene", "camlock", "blīve pienam".'
                    ),
                },
                "material": {
                    "type": "string",
                    "description": (
                        "Materiāls. Var būt kods (EPDM, NBR, MVQ, FKM, PTFE, PU) "
                        'vai latvisks nosaukums ("silikons", "nerūsējošais tērauds").'
                    ),
                },
                "dn_mm": {
                    "type": "integer",
                    "description": (
                        "Nominālais diametrs mm (DN25 -> 25). Collas pārvērt: "
                        "1\"=25, 2\"=50, 3\"=80.\n\n"
                        "TIKAI apaļām detaļām, kurām ir diametrs: šļūtenēm, "
                        "savienojumiem, blīvēm, caurulēm. Profiliem un "
                        "blīvgumijām diametra NAV — to izmērs \"2x8x12mm\" ir "
                        "spraugas platums × ārējais platums × augstums, un "
                        "jebkurš no šiem skaitļiem `dn_mm` laukā izmet tieši "
                        "to preci, ko meklē. Profila izmēru raksti `query` laukā."
                    ),
                },
                "dn_tolerance": {
                    "type": "integer",
                    "description": "Pielaide dn_mm abos virzienos. 0 = precīza sakritība. Izmanto 5-10, ja lietotājs nosauca aptuvenu izmēru.",
                },
                "type_code": {
                    "type": "string",
                    "description": (
                        "Savienojuma tipa kods: A, B, C, D, E, F, DC, DP "
                        "(pamattipi) vai AR, DAR, SAR, DRVR, OLS (pārejas starp "
                        "diviem izmēriem). ŠIS ir vienīgais pareizais veids, kā "
                        "meklēt pēc tipa — kodu NEDRĪKST likt `query` laukā, jo "
                        "\"AR\" sakrīt ar latviešu vārdu \"ar\" un atgriež 79% "
                        "kataloga."
                    ),
                },
                "temp_min_required": {
                    "type": "number",
                    "description": "Zemākā darba temperatūra °C, ko produktam JĀIZTUR. Norādi tikai, ja lietotājs to nosauca.",
                },
                "temp_max_required": {
                    "type": "number",
                    "description": "Augstākā darba temperatūra °C, ko produktam JĀIZTUR. Norādi tikai, ja lietotājs to nosauca.",
                },
                "food_grade": {
                    "type": "boolean",
                    "description": (
                        "true = tikai produkti ar pārtikas sertifikātu (FDA / EC 1935). "
                        "Liec true VIENMĒR, kad runa ir par pienu, dzērieniem, alu, "
                        "pārtiku, farmāciju vai kosmētiku."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": "Kategorijas nosaukuma daļa. Kategoriju sarakstu dod list_categories.",
                },
                "in_stock_only": {
                    "type": "boolean",
                    "description": "true = tikai noliktavā esošie. Ja ar to nekas neatrodas, rīks pats atlaiž šo filtru un pasaka to `notes` laukā.",
                },
                "max_price": {
                    "type": "number",
                    "description": "Augstākā pieņemamā cena EUR bez PVN.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Cik rezultātu atgriezt, 1-{MAX_RESULTS}. Noklusējums {MAX_RESULTS}.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_product",
        "description": (
            "Atgriež vienu produktu ar pilnu aprakstu un visiem tehniskajiem "
            "atribūtiem pēc artikula (SKU). Izsauc, kad lietotājs jautā par "
            "konkrētu produktu no iepriekšējiem meklēšanas rezultātiem un tev "
            "vajag detaļas, ko saīsinātais saraksts nesatur — pilnu aprakstu, "
            "standartu, cietību, visus atribūtus."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {
                    "type": "string",
                    "description": "Artikuls tieši tā, kā to atgrieza search_products, piem. \"000015196\".",
                }
            },
            "required": ["sku"],
        },
    },
    {
        "name": "browse_category",
        "description": (
            "Pārlūko VISU kategoriju bez atslēgvārdiem, pa lapām.\n\n"
            "Izmanto, kad meklēšana neko neatrada vai kad nezini, kā produkts "
            "katalogā nosaukts. Tas ir drošāks par minēšanu: kategorijā "
            "\"Camlock Pārejas\" ir 89 produkti, un tos var izlapot, nemēģinot "
            "uzminēt pareizo vārdu. Atbildē ir `total` un `page` — ja meklētā "
            "vēl nav, ņem nākamo lapu.\n\n"
            "OBLIGĀTI izsauc šo, pirms saki, ka produkta katalogā nav, un "
            "pirms piedāvā izgatavošanu pēc pasūtījuma.\n\n"
            "Kategorijas nosaukumu dod list_categories."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Kategorijas nosaukums vai tā daļa, piem. "
                        '"Camlock Pārejas", "Silikona caurules". Nav jābūt '
                        "pilnam ceļam."
                    ),
                },
                "page": {"type": "integer", "description": "Lapa, sākot no 1."},
                "per_page": {
                    "type": "integer",
                    "description": (
                        f"Ierakstu skaits lapā, 1-{MAX_BROWSE}. Noklusējums {MAX_BROWSE}."
                    ),
                },
                "in_stock_only": {
                    "type": "boolean",
                    "description": "true = tikai noliktavā esošie.",
                },
            },
            "required": ["category"],
        },
    },
    {
        "name": "list_categories",
        "description": (
            "Atgriež kataloga kategorijas ar produktu skaitu. Izsauc, kad "
            "nezini, vai katalogā vispār ir kaut kas no prasītās jomas, vai kad "
            "lietotāja jautājums ir tik plašs, ka vajag saprast kataloga uzbūvi. "
            "Neizsauc to pirms katras meklēšanas — parasti pietiek ar search_products."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "min_products": {
                    "type": "integer",
                    "description": "Rāda tikai kategorijas ar vismaz tik produktiem. Noklusējums 3.",
                }
            },
            "required": [],
        },
    },
]


#: Tas pats OpenAI Responses API formātā — tur rīks ir plakans, bez ligzdotā
#: "function" objekta (atšķirībā no Chat Completions).
TOOLS: list[dict[str, Any]] = [{"type": "function", **spec} for spec in TOOL_SPECS]

TOOL_NAMES = frozenset(spec["name"] for spec in TOOL_SPECS)


@dataclass(slots=True)
class ToolCall:
    """Viena rīka izsaukuma pieraksts — `/tools` komandai un debugam."""

    name: str
    input: dict[str, Any]
    result_count: int = 0
    is_error: bool = False
    notes: list[str] = field(default_factory=list)
    #: Neapstrādātā rīka atbilde — tikai `--verbose` un `/tools` vajadzībām.
    output: str = ""


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _positive(value: Any) -> float | None:
    """Nulli un tukšumu lasām kā "nav norādīts".

    Modeļi mēdz aizpildīt visus neobligātos parametrus ar noklusējumiem
    (`max_price: 0`, `dn_mm: 0`) tā vietā, lai tos izlaistu. Šeit 0 nekad nav
    jēdzīga vērtība — cena 0 un diametrs 0 neatlasītu neko, un meklēšana
    klusi nokristu uz rezerves ceļu ar atmestiem filtriem.
    """
    if value in (None, "", False):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _temperatures(args: dict[str, Any]) -> tuple[float | None, float | None]:
    """Temperatūras pāris, kur 0 var būt gan īsts, gan aizpildīts noklusējums.

    0 °C ir pilnīgi reāla darba temperatūra, tāpēc to vienu pašu neizmetam.
    Bet 0..0 nav diapazons, ko kāds prasītu — to lasām kā "nav norādīts".
    """
    low = args.get("temp_min_required")
    high = args.get("temp_max_required")
    if low in (None, "") and high in (None, ""):
        return None, None
    try:
        low_f = None if low in (None, "") else float(low)
        high_f = None if high in (None, "") else float(high)
    except (TypeError, ValueError):
        return None, None
    if low_f == 0 and high_f == 0:
        return None, None
    return low_f, high_f


def _drop_dn_for_profiles(
    query: str, dn: float | None
) -> tuple[float | None, str | None]:
    """Profila vaicājumam `dn_mm` ir vienmēr kļūda — izmetam to.

    Profilam diametra nav: "2x8x12mm" ir sprauga × platums × augstums. Kad kāds
    no šiem skaitļiem nonāk `dn_mm` filtrā, meklēšana izmet tieši to preci, ko
    klients prasīja, un atgriež nesaistītu apaļu profilu ar to pašu skaitli.

    Gan sistēmas prompts, gan rīka apraksts to aizliedz, un modelis to tāpat
    dara: "D veida pašlīmējošs profils 12 mm" aizgāja ar `dn_mm=12`, un no
    visa kataloga atgriezās viens O veida profils Ø10. Aizliegums, ko var
    neievērot, nav aizsardzība — tāpēc filtru noņemam šeit.
    """
    if dn is None or not normalize.is_profile(query):
        return dn, None
    return None, (
        f"`dn_mm={dn:g}` tika NOŅEMTS: vaicājums ir par profilu, un profilam "
        "diametra nav — nosaukuma skaitļi ir sprauga un ārējie gabarīti. "
        "Izmēru raksti `query` laukā."
    )


def _run_search(args: dict[str, Any], conn: sqlite3.Connection) -> tuple[dict[str, Any], int, list[str]]:
    query_text = (args.get("query") or "").strip()
    dn = _positive(args.get("dn_mm"))
    dn, dn_note = _drop_dn_for_profiles(query_text, dn)
    temp_min, temp_max = _temperatures(args)
    result = catalog_search.search_products(
        query=query_text or None,
        material=(args.get("material") or "").strip() or None,
        dn_mm=int(dn) if dn is not None else None,
        dn_tolerance=_clamp(args.get("dn_tolerance") or 0, 0, 500, 0),
        type_code=(args.get("type_code") or "").strip() or None,
        temp_min_required=temp_min,
        temp_max_required=temp_max,
        # Tikai True filtrē. `food_grade=False` nozīmētu "rādi TIKAI
        # nesertificētos", ko neviens neprasa — bet modelis to mēdz aizpildīt
        # kā noklusējumu, un tad pārtikas produkti tiktu izmesti ārā.
        food_grade=True if args.get("food_grade") is True else None,
        category=(args.get("category") or "").strip() or None,
        in_stock_only=bool(args.get("in_stock_only")),
        max_price=_positive(args.get("max_price")),
        limit=_clamp(args.get("limit") or MAX_RESULTS, 1, MAX_RESULTS, MAX_RESULTS),
        conn=conn,
    )
    notes = list(result.notes)
    if dn_note:
        notes.insert(0, dn_note)

    payload = {
        "count": len(result),
        "products": [p.to_search_dict() for p in result],
    }
    if notes:
        payload["notes"] = notes
    if not result:
        used = [
            name
            for name, value in (
                ("material", args.get("material")),
                ("dn_mm", dn),
                ("type_code", args.get("type_code")),
                ("temp_min_required", temp_min),
                ("temp_max_required", temp_max),
                ("max_price", _positive(args.get("max_price"))),
            )
            if value
        ]
        payload["hint"] = (
            "Nekas neatbilst ŠIEM filtriem: "
            + (", ".join(used) if used else "(tikai teksts)")
            + ". Filtri NETIKA atmesti — rezultāts ir godīgs. "
            "Pamēģini noņemt pa vienam filtram vai izsauc browse_category, "
            "pirms secini, ka katalogā tā nav."
        )
    return payload, len(result), notes


def _run_get_product(args: dict[str, Any], conn: sqlite3.Connection) -> tuple[dict[str, Any], int]:
    sku = str(args.get("sku") or "").strip()
    if not sku:
        raise ValueError("Trūkst `sku`.")

    product = catalog_search.get_product(sku, conn=conn)
    if product is None:
        raise LookupError(
            f"Katalogā nav produkta ar artikulu {sku!r}. "
            "Pārbaudi, vai artikuls nāk no search_products rezultāta."
        )

    payload = product.to_search_dict()
    payload.update(
        {
            "description": product.description[:DESCRIPTION_LIMIT],
            "standard": product.standard,
            "hardness_sha": product.hardness_sha,
            "oil_resistant": product.oil_resistant,
            "chemical_resistant": product.chemical_resistant,
            "stock_text": product.stock_text,
            "categories": product.categories,
            "attributes": product.attributes,
        }
    )
    return payload, 1


def _run_browse_category(
    args: dict[str, Any], conn: sqlite3.Connection
) -> tuple[dict[str, Any], int]:
    category = (args.get("category") or "").strip()
    if not category:
        raise ValueError("Trūkst `category`. Kategoriju sarakstu dod list_categories.")

    payload = catalog_search.browse_category(
        category=category,
        page=_clamp(args.get("page") or 1, 1, 10_000, 1),
        per_page=_clamp(args.get("per_page") or MAX_BROWSE, 1, MAX_BROWSE, MAX_BROWSE),
        in_stock_only=bool(args.get("in_stock_only")),
        conn=conn,
    )
    if not payload["total"]:
        payload["hint"] = (
            "Tādas kategorijas nav vai tā ir tukša. Izsauc list_categories, "
            "lai redzētu pieejamos nosaukumus."
        )
    return payload, len(payload["products"])


def _run_list_categories(args: dict[str, Any], conn: sqlite3.Connection) -> tuple[dict[str, Any], int]:
    min_products = _clamp(args.get("min_products", 3), 1, 1000, 3)
    categories = catalog_search.list_categories(conn=conn, min_count=min_products)
    stats = catalog_search.catalog_stats(conn=conn)
    return (
        {
            "total_products": stats["total"],
            "in_stock": stats["in_stock"],
            "count": len(categories),
            "categories": categories,
        },
        len(categories),
    )


def execute_tool(
    name: str, args: dict[str, Any], conn: sqlite3.Connection
) -> tuple[str, ToolCall]:
    """Izpilda rīku un atgriež (JSON teksts modelim, izsaukuma pieraksts).

    Kļūdas NEMET tālāk — tās atgriežam kā tekstu ar `is_error=True`, lai
    modelis pats izlemj, ko darīt.
    """
    call = ToolCall(name=name, input=args)
    try:
        if name == "search_products":
            payload, count, notes = _run_search(args, conn)
            call.notes = notes
        elif name == "get_product":
            payload, count = _run_get_product(args, conn)
        elif name == "browse_category":
            payload, count = _run_browse_category(args, conn)
        elif name == "list_categories":
            payload, count = _run_list_categories(args, conn)
        else:
            raise ValueError(f"Nezināms rīks: {name!r}")
        call.result_count = count
        call.output = json.dumps(payload, ensure_ascii=False)
        return call.output, call
    except Exception as exc:  # rīka kļūda nedrīkst nogāzt sarunu
        call.is_error = True
        call.output = f"Kļūda rīkā {name}: {exc}"
        return call.output, call
