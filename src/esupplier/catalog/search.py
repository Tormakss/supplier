"""Meklēšana katalogā: FTS5 pilnteksts + strukturētie atribūtu filtri."""

from __future__ import annotations

import math
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from . import db
from .models import Product
from .normalize import normalize_material

#: bm25 svari (name, aliases, description, category, material); lielāks = svarīgāks.
#: Aprakstam gandrīz neko: tie ir kopēti blokiem un noslīcina nosaukumu.
BM25_WEIGHTS = (10.0, 5.0, 0.5, 2.0, 2.0)

#: Vārdi, kas FTS vaicājumā tikai trokšņo.
_STOPWORDS = {
    "un", "ar", "bez", "no", "līdz", "lidz", "par", "uz", "kas", "vai",
    "man", "mums", "vajag", "vajadzīgs", "vajadzigs", "gribu", "lūdzu", "ludzu",
    "the", "and", "for", "with", "need", "want",
    "и", "с", "для", "без", "нужно", "нужна", "нужен",
}


@dataclass(slots=True)
class SearchResult:
    """Meklēšanas rezultāts ar piezīmēm par to, kā tas iegūts.

    Uzvedas kā saraksts (`len`, `for`, `[0]`) izsaucējiem, kas gaida
    `list[Product]`.
    """

    products: list[Product] = field(default_factory=list)
    #: FTS stratēģija, kas deva rezultātu: exact / stem / any / none
    strategy: str = "none"
    #: True, ja sākotnēji nekas neatradās un `in_stock_only` tika atmests.
    relaxed_in_stock: bool = False
    #: True, ja atlaidām arī pārējos filtrus (tikai pilnteksts).
    relaxed_filters: bool = False
    #: True, ja neviens rezultāts nesedz VISUS klienta nosauktos skaitļus.
    #: Bez tā tuvākais cits izmērs aiziet kā atbilde uz prasīto.
    size_mismatch: bool = False
    fts_query: str = ""

    def __iter__(self) -> Iterator[Product]:
        return iter(self.products)

    def __len__(self) -> int:
        return len(self.products)

    def __getitem__(self, index: int) -> Product:
        return self.products[index]

    def __bool__(self) -> bool:
        return bool(self.products)

    @property
    def notes(self) -> list[str]:
        out: list[str] = []
        if self.relaxed_in_stock:
            out.append(
                "Noliktavā šobrīd nav neviena atbilstoša produkta — "
                "rezultātā iekļauti arī tie, kas jāpasūta."
            )
        if self.relaxed_filters:
            out.append(
                "Ar visiem filtriem nekas neatradās — filtri atmesti, "
                "rādīti tikai teksta sakritības rezultāti. Pārbaudi parametrus!"
            )
        if self.size_mismatch:
            out.append(
                "NEVIENS rezultāts nesakrīt ar visiem vaicājumā nosauktajiem "
                "skaitļiem — šie ir CITI izmēri. Pirms tos piedāvā, pamēģini "
                "meklēt tikai pēc ģimenes un izmēra (bez apzīmētājiem) vai "
                "izsauc browse_category. Ja tomēr piedāvā šos, atšķirība "
                "vēstulē jāparāda salīdzinājuma tabulā."
            )
        return out


# ---------------------------------------------------------------------------
# FTS vaicājuma sagatavošana
# ---------------------------------------------------------------------------
#: Izmēru virkne vienā marķierī: "12x16mm", "1.5x6x12", "20X30X25mm".
_DIMENSION = re.compile(r"^\d+(?:[.,]\d+)?(?:x\d+(?:[.,]\d+)?)+(?:mm|cm|m)?$", re.I)
#: Skaitlis ar pielipušu mērvienību: "16mm", "70sha".
_NUMBER_WITH_UNIT = re.compile(r"^(\d+(?:[.,]\d+)?)(mm|cm|m|sha|shore)$", re.I)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _tokenize(query: str) -> list[str]:
    """Sadala lietotāja tekstu drošos marķieros.

    Viss, kas nav burts vai cipars, tiek izmests — tā ir arī aizsardzība pret
    FTS sintaksi. Viencipara un vienburta marķierus PATURAM: tie ir izmēri
    ("4") un profilu ģimenes ("U profils"), ne troksnis.
    """
    tokens = re.findall(r"[^\W_]+", query, re.UNICODE)
    return [t for t in tokens if t.lower() not in _STOPWORDS]


def token_forms(token: str) -> list[str]:
    """Kā šis marķieris var būt uzrakstīts katalogā.

    Izmēru meklējam arī pa atsevišķiem skaitļiem: "12x16mm" katalogā mēdz būt
    arī "12×16 mm" vai daļa no "1.5x6x12mm".
    """
    forms = [token]
    if _DIMENSION.match(token):
        forms += _NUMBER.findall(token)
    else:
        match = _NUMBER_WITH_UNIT.match(token)
        if match:
            forms.append(match.group(1))
    return list(dict.fromkeys(forms))


def _quote(token: str) -> str:
    """FTS5 virknes literālis — iekšējās pēdiņas dubultojam."""
    return '"' + token.replace('"', '""') + '"'


def _stem(token: str) -> str:
    """Rupjš latviešu galotņu nogriezums: "silikona" -> "silikon".

    Ciparus saturošus marķierus neaiztiekam — tiem galotnes nav.
    """
    if any(ch.isdigit() for ch in token):
        return token
    if len(token) > 6:
        return token[:-2]
    if len(token) > 4:
        return token[:-1]
    return token


def _prefixable(form: str) -> bool:
    """Vai marķierim drīkst pielikt `*`.

    Īsi BURTU marķieri paliek precīzi ("u*" atbilstu pusei kataloga); skaitļiem
    otrādi, jo izmērs katalogā ir viens marķieris ("12x22x25mm").
    """
    return len(form) >= 3 or any(ch.isdigit() for ch in form)


def _group(forms: list[str], *, prefix: bool) -> str:
    """Viena marķiera raksti vienā FTS grupā: ("12x16mm" OR "12" OR "16").

    Grupa AND ķēdē turas kā viens vārds, tāpēc izmēra pieraksts drīkst mainīties.
    """
    parts = [
        f"{_quote(form)}*" if prefix and _prefixable(form) else _quote(form)
        for form in dict.fromkeys(forms)
    ]
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"


def build_fts_queries(query: str) -> list[tuple[str, str]]:
    """Sagatavo (stratēģija, FTS vaicājums) variantus no stingrākā uz vaļīgāko."""
    tokens = _tokenize(query)
    if not tokens:
        return []

    forms = [token_forms(t) for t in tokens]
    stems = [[_stem(f) for f in group] for group in forms]

    exact = " AND ".join(_group(g, prefix=False) for g in forms)
    stem_and = " AND ".join(_group(g, prefix=True) for g in stems)
    stem_or = " OR ".join(_group(g, prefix=True) for g in stems)

    variants = [("exact", exact)]
    if stem_and != exact:
        variants.append(("stem", stem_and))
    if len(tokens) > 1:
        variants.append(("any", stem_or))
    return variants


def _fold(text: str) -> str:
    """Mazie burti bez diakritikas — tāpat kā `remove_diacritics 2` FTS pusē."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _contains(haystack: str, form: str) -> bool:
    """Vai marķieris ir tekstā. Īsiem prasām vārda robežu ("u" ir teju visur)."""
    if not form:
        return False
    if len(form) >= 3:
        return form in haystack
    return re.search(rf"(?<![^\W_]){re.escape(form)}(?![^\W_])", haystack) is not None


def _has_digit(group: list[str]) -> bool:
    return any(any(ch.isdigit() for ch in form) for form in group)


def _coverage_filter(
    products: list[Product], query: str, limit: int
) -> tuple[list[Product], bool]:
    """Atsijā `OR` tiera atkritumus. Atgriež (produkti, izmērs sakrita).

    Vaļīgākais variants savieno marķierus ar OR, tāpēc prasām vismaz pusi
    sakritību. IZMĒRS SVER VAIRĀK PAR APZĪMĒTĀJU: kamēr visi marķieri skaitījās
    vienādi, "D veida pašlīmējošs profils 12mm" atdeva 10×15, jo pieci
    aprakstošie vārdi pārsvēra vienīgo svarīgo skaitli.
    """
    groups = [
        [_fold(_stem(form)) for form in token_forms(token)]
        for token in _tokenize(query)
    ]
    if not groups:
        return products[:limit], True

    numeric = [g for g in groups if _has_digit(g)]
    words = [g for g in groups if not _has_digit(g)]
    # Griezums pēc VĀRDU pārklājuma: "12" pats par sevi sakrīt ar pusi kataloga.
    required = max(1, math.ceil(len(groups) / 2))

    scored: list[tuple[int, int, int, Product]] = []
    for order, product in enumerate(products):
        # `aliases` obligāti: tur dzīvo klienta vārdi ("pāreja", "серый"),
        # kuru nosaukumā nav.
        haystack = _fold(
            f"{product.name} {product.aliases} {product.category} "
            f"{product.material or ''} {product.description}"
        )
        matched = [
            group
            for group in groups
            if any(_contains(haystack, form) for form in group)
        ]
        if len(matched) < required:
            continue
        num_hits = sum(1 for g in matched if _has_digit(g))
        word_hits = len(matched) - num_hits
        scored.append((-num_hits, -word_hits, order, product))

    scored.sort()
    top = [product for _, _, _, product in scored[:limit]]
    # Pirmais rezultāts sedz VISUS klienta skaitļus. Ja nē, izsaucējam
    # jāpasaka: klusi atdots tuvākais cits izmērs ir tieši šī lauka jēga.
    size_ok = not numeric or bool(scored and -scored[0][0] == len(numeric))
    return top, size_ok


# ---------------------------------------------------------------------------
# SQL filtri
# ---------------------------------------------------------------------------
def _build_filters(
    *,
    material: str | None,
    dn_mm: int | None,
    dn_tolerance: int,
    type_code: str | None,
    temp_min_required: float | None,
    temp_max_required: float | None,
    food_grade: bool | None,
    category: str | None,
    in_stock_only: bool,
    max_price: float | None,
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if material:
        canonical = normalize_material(material) or material
        where.append("(p.material = ? COLLATE NOCASE OR p.material LIKE ? COLLATE NOCASE)")
        params += [canonical, f"%{canonical}%"]

    if dn_mm is not None:
        # Pārejai ir divi diametri, un klients var nosaukt jebkuru no tiem.
        if dn_tolerance:
            where.append(
                "(p.dn_mm BETWEEN ? AND ? OR p.dn_mm_2 BETWEEN ? AND ?)"
            )
            bounds = [dn_mm - dn_tolerance, dn_mm + dn_tolerance]
            params += bounds + bounds
        else:
            where.append("(p.dn_mm = ? OR p.dn_mm_2 = ?)")
            params += [dn_mm, dn_mm]

    if type_code:
        where.append("p.type_code = ? COLLATE NOCASE")
        params.append(type_code.strip().upper())

    # Bez zināmas temperatūras izslēdzam apzināti: nezināms nav "der".
    if temp_min_required is not None:
        where.append("p.temp_min_c IS NOT NULL AND p.temp_min_c <= ?")
        params.append(temp_min_required)
    if temp_max_required is not None:
        where.append("p.temp_max_c IS NOT NULL AND p.temp_max_c >= ?")
        params.append(temp_max_required)

    if food_grade is not None:
        where.append("p.food_grade = ?")
        params.append(int(food_grade))

    if category:
        where.append("(p.category LIKE ? COLLATE NOCASE OR p.categories_json LIKE ? COLLATE NOCASE)")
        params += [f"%{category}%", f"%{category}%"]

    if in_stock_only:
        where.append("p.is_in_stock = 1")

    if max_price is not None:
        where.append("p.price_excl_vat IS NOT NULL AND p.price_excl_vat <= ?")
        params.append(max_price)

    return where, params


def _run(
    conn: sqlite3.Connection,
    fts_query: str | None,
    where: list[str],
    params: list[Any],
    limit: int,
) -> list[Product]:
    if fts_query:
        # FTS5 MATCH un bm25() prasa īsto tabulas nosaukumu, aizstājvārds nederēs.
        sql = (
            "SELECT p.* FROM products p "
            "JOIN products_fts ON products_fts.rowid = p.id "
            "WHERE products_fts MATCH ? "
            + ("AND " + " AND ".join(where) + " " if where else "")
            + f"ORDER BY bm25(products_fts, {', '.join(str(w) for w in BM25_WEIGHTS)}), "
            "p.is_in_stock DESC LIMIT ?"
        )
        args = [fts_query, *params, limit]
    else:
        sql = (
            "SELECT p.* FROM products p "
            + ("WHERE " + " AND ".join(where) + " " if where else "")
            + "ORDER BY p.is_in_stock DESC, p.price_excl_vat IS NULL, "
            "p.price_excl_vat ASC LIMIT ?"
        )
        args = [*params, limit]

    try:
        rows = conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        # Nederīgs FTS vaicājums nedrīkst nogāzt sarunu.
        return []
    return [Product.from_row(row) for row in rows]


# ---------------------------------------------------------------------------
# Publiskais API
# ---------------------------------------------------------------------------
def search_products(
    query: str | None = None,
    material: str | None = None,
    dn_mm: int | None = None,
    dn_tolerance: int = 0,
    type_code: str | None = None,
    temp_min_required: float | None = None,
    temp_max_required: float | None = None,
    food_grade: bool | None = None,
    category: str | None = None,
    in_stock_only: bool = False,
    max_price: float | None = None,
    limit: int = 10,
    conn: sqlite3.Connection | None = None,
) -> SearchResult:
    """Meklē produktus pēc teksta un/vai strukturētajiem atribūtiem."""
    if conn is None:
        with db.session() as owned:
            return search_products(
                query, material, dn_mm, dn_tolerance, type_code,
                temp_min_required, temp_max_required, food_grade, category,
                in_stock_only, max_price, limit, conn=owned,
            )

    where, params = _build_filters(
        material=material, dn_mm=dn_mm, dn_tolerance=dn_tolerance, type_code=type_code,
        temp_min_required=temp_min_required, temp_max_required=temp_max_required,
        food_grade=food_grade, category=category, in_stock_only=in_stock_only,
        max_price=max_price,
    )
    variants = build_fts_queries(query) if query else []

    def attempt(w: list[str], p: list[Any]) -> tuple[list[Product], str, str, bool]:
        if not variants:
            return _run(conn, None, w, p, limit), "filters", "", True
        for strategy, fts in variants:
            size_ok = True
            if strategy == "any":
                # Vaļīgākais variants: paņemam vairāk un atsijām pēc pārklājuma.
                found, size_ok = _coverage_filter(
                    _run(conn, fts, w, p, limit * 5), query or "", limit
                )
            else:
                # exact / stem tieros skaitliskā grupa ir AND ķēdē, tāpēc
                # sakritība ar izmēru ir garantēta.
                found = _run(conn, fts, w, p, limit)
            if found:
                return found, strategy, fts, size_ok
        return [], "none", variants[-1][1], True

    products, strategy, fts_query, size_ok = attempt(where, params)
    if products:
        return SearchResult(
            products, strategy, size_mismatch=not size_ok, fts_query=fts_query
        )

    # 1. atkāpšanās: atmetam prasību pēc noliktavas atlikuma.
    if in_stock_only:
        relaxed_where, relaxed_params = _build_filters(
            material=material, dn_mm=dn_mm, dn_tolerance=dn_tolerance, type_code=type_code,
            temp_min_required=temp_min_required, temp_max_required=temp_max_required,
            food_grade=food_grade, category=category, in_stock_only=False,
            max_price=max_price,
        )
        products, strategy, fts_query, size_ok = attempt(relaxed_where, relaxed_params)
        if products:
            return SearchResult(
                products, strategy, relaxed_in_stock=True,
                size_mismatch=not size_ok, fts_query=fts_query,
            )

    # 2. atkāpšanās: tikai pilnteksts, un TIKAI bez šķirojošiem filtriem. Uz
    # "nerūsējošais AR DN150" pareizā atbilde ir "nav", ne astoņi alumīnija
    # produkti: filtru atmešana klusi maina jautājumu.
    discriminating = any(
        value is not None
        for value in (material, dn_mm, type_code, max_price,
                      temp_min_required, temp_max_required)
    )
    if discriminating:
        return SearchResult([], "none", fts_query=fts_query)

    # Pārtikas prasību NEATMETAM — nesertificēts aizstājējs pārtikai ir bīstams.
    if variants and (where or in_stock_only):
        keep_where, keep_params = _build_filters(
            material=None, dn_mm=None, dn_tolerance=0, type_code=None,
            temp_min_required=None, temp_max_required=None,
            food_grade=food_grade, category=None, in_stock_only=False,
            max_price=None,
        )
        products, strategy, fts_query, size_ok = attempt(keep_where, keep_params)
        if products:
            return SearchResult(
                products, strategy, relaxed_filters=True,
                size_mismatch=not size_ok, fts_query=fts_query,
            )

    return SearchResult([], "none", fts_query=fts_query)


def browse_category(
    category: str,
    page: int = 1,
    per_page: int = 30,
    in_stock_only: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Visi produkti kategorijā, bez teksta meklēšanas.

    `category` ir ceļa daļa ("Camlock Pārejas"): kataloga slug'i ir skaitliski
    un nelasāmi.
    """
    if conn is None:
        with db.session() as owned:
            return browse_category(category, page, per_page, in_stock_only, owned)

    per_page = max(1, min(100, per_page))
    page = max(1, page)
    where = ["(p.category LIKE ? COLLATE NOCASE OR p.categories_json LIKE ? COLLATE NOCASE)"]
    params: list[Any] = [f"%{category}%", f"%{category}%"]
    if in_stock_only:
        where.append("p.is_in_stock = 1")

    clause = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM products p WHERE {clause}", params
    ).fetchone()["n"]

    rows = conn.execute(
        f"SELECT p.* FROM products p WHERE {clause} "
        "ORDER BY p.is_in_stock DESC, p.price_excl_vat IS NULL, p.price_excl_vat ASC "
        "LIMIT ? OFFSET ?",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()

    return {
        "category": category,
        "total": total,
        "page": page,
        "total_pages": max(1, math.ceil(total / per_page)),
        "products": [Product.from_row(r).to_browse_dict() for r in rows],
        "note": (
            "Saīsināts saraksts (cena bez PVN). Pilnu info par konkrētu "
            "produktu paņem ar get_product."
        ),
    }


def get_product(sku: str, conn: sqlite3.Connection | None = None) -> Product | None:
    """Produkts pēc artikula (SKU). Ja SKU dublējas, atgriež pirmo pieejamo."""
    if conn is None:
        with db.session() as owned:
            return get_product(sku, owned)

    row = conn.execute(
        "SELECT * FROM products WHERE sku = ? COLLATE NOCASE "
        "ORDER BY is_in_stock DESC, id LIMIT 1",
        (sku.strip(),),
    ).fetchone()
    return Product.from_row(row) if row else None


def list_categories(
    conn: sqlite3.Connection | None = None, min_count: int = 1
) -> list[dict[str, Any]]:
    """Kataloga koks: saknes kategorijas ar apakškategorijām.

    Grupēts pēc saknes: katalogā ir desmitiem kategoriju "2mm" un "EPDM".
    """
    if conn is None:
        with db.session() as owned:
            return list_categories(owned, min_count)

    rows = conn.execute(
        "SELECT category_root, COUNT(*) AS n, SUM(is_in_stock) AS in_stock "
        "FROM products WHERE category_root != '' GROUP BY category_root "
        "HAVING n >= ? ORDER BY n DESC",
        (min_count,),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        subs = conn.execute(
            "SELECT category, COUNT(*) AS n FROM products "
            "WHERE category_root = ? AND category != category_root "
            "GROUP BY category",
            (row["category_root"],),
        ).fetchall()

        # Divi līmeņi: "Camlock Pārejas" ir 3. līmenī, un ar virsrakstiem
        # vien modelis pareizo apakškategoriju izvēlēties nevar.
        level2: dict[str, int] = {}
        level3: dict[str, int] = {}
        for sub in subs:
            parts = sub["category"].split(" > ")
            if len(parts) > 1:
                level2[parts[1]] = level2.get(parts[1], 0) + sub["n"]
            if len(parts) > 2:
                level3[parts[2]] = level3.get(parts[2], 0) + sub["n"]

        out.append(
            {
                "category": row["category_root"],
                "products": row["n"],
                "in_stock": row["in_stock"],
                "subcategories": [
                    {"name": name, "products": count}
                    for name, count in sorted(level2.items(), key=lambda x: -x[1])
                ],
                "deeper": [
                    {"name": name, "products": count}
                    for name, count in sorted(level3.items(), key=lambda x: -x[1])[:25]
                ],
            }
        )
    return out


def catalog_stats(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    if conn is None:
        with db.session() as owned:
            return catalog_stats(owned)
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(is_in_stock) AS in_stock, "
        "SUM(food_grade) AS food_grade FROM products"
    ).fetchone()
    return {
        "total": row["total"],
        "in_stock": row["in_stock"] or 0,
        "food_grade": row["food_grade"] or 0,
        "synced_at": db.get_meta(conn, "synced_at"),
    }
