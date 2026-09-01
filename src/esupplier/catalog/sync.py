"""Kataloga sinhronizācija no e-supplier.lv.

Primārais avots — WooCommerce Store API (publisks, bez autentifikācijas).
Rezerves variants — `--source=scrape`, kas iet caur sitemap un lasa JSON-LD.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from ..config import (
    CACHE_DIR,
    PER_PAGE,
    REQUEST_TIMEOUT,
    SCRAPE_DELAY_S,
    SITEMAP_URL,
    STORE_API,
    USER_AGENT,
    VAT_RATE,
)
from . import db, normalize, units
from .models import Product

HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strip_html(raw: str | None) -> str:
    """Nogriež HTML un uzreiz vienādo tipogrāfiskās rakstzīmes.

    `clean_text` te ir obligāts: katalogā collas rakstītas ar U+2033 (``4″``),
    un bez pārveides ne lietotāja ``4"``, ne collu parsēšana nesakrīt.
    """
    if not raw:
        return ""
    text = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize.clean_text(html.unescape(text))


def _stock_qty(text: str) -> int | None:
    """"10 noliktavā" -> 10. "Nav noliktavā" -> None."""
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else None


def _first_image(images: Any) -> str:
    """Pirmās bildes pilnā adrese.

    Ņemam `src`, nevis `thumbnail`: 300x300 sīktēls e-pastā, kur klients
    salīdzina divas gandrīz vienādas blīves, ir par mazu. Platumu ierobežo
    HTML, nevis avota fails.
    """
    for image in images or []:
        if not isinstance(image, dict):
            continue
        src = (image.get("src") or image.get("thumbnail") or "").strip()
        if src:
            return src
    return ""


# ---------------------------------------------------------------------------
# Store API
# ---------------------------------------------------------------------------
def _price_excl_vat(item: dict[str, Any], incl: float | None) -> float | None:
    """Cena bez PVN.

    Store API `prices.price` nāk AR PVN. Cenu bez PVN veikals renderē
    `price_html` atribūtā `data-no-tax`; ja tā nav, dalām ar PVN likmi.
    """
    m = re.search(r'data-no-tax="([\d.,]+)', item.get("price_html") or "")
    if m:
        try:
            value = round(float(m.group(1).replace(",", ".")), 2)
        except ValueError:
            value = 0.0
        # 0.00 nav cena, bet "cena nav publicēta". Neizliekamies, ka zinām.
        if value > 0:
            return value
    if not incl:
        return None
    return round(incl / (1 + VAT_RATE), 2)


#: Kategorijas lapas adrese: .../produkta-kategorija/<vecāks>/<bērns>/
_CATEGORY_URL = re.compile(r"/produkta-kategorija/(.+?)/?$")


def _slug_segments(node: dict[str, Any]) -> list[str]:
    match = _CATEGORY_URL.search(node.get("permalink") or node.get("link") or "")
    return [seg for seg in match.group(1).split("/") if seg] if match else []


def _name_from_slug(slug: str) -> str:
    """"u-tips" -> "U Tips". Vienburta vārdi ir profila ģimenes, tie ir lielie."""
    words = [w for w in slug.replace("_", "-").split("-") if w]
    return " ".join(w.upper() if len(w) == 1 else w.capitalize() for w in words)


def build_category_paths(categories: list[dict[str, Any]]) -> dict[int, list[str]]:
    """Kategorijas ID -> ceļš no saknes līdz lapai.

    Produktā ir tikai lapas kategorija ("EPDM", "2mm"), kas pati par sevi neko
    nepasaka. Īsto koku zina tikai `/products/categories`, tāpēc to salasām
    pirms produktiem.

    Bet ne visu: starpkategorijas, kurās nav produktu tieši (ir tikai
    apakškategorijas), Store API sarakstā NEPARĀDĀS. Tā pazuda "U Tips", un
    visiem U profiliem ceļš sabruka līdz "EPDM" — `browse_category("U Tips")`
    neatrada neko, lai gan katalogā ir 53 U profili. Trūkstošos posmus
    atjaunojam no kategorijas adreses, kurā ceļš vienmēr ir pilns.
    """
    by_id = {int(c["id"]): c for c in categories if c.get("id") is not None}
    by_slug = {
        (c.get("slug") or ""): (c.get("name") or "").strip()
        for c in categories
        if c.get("slug")
    }
    cache: dict[int, list[str]] = {}

    def path(cid: int, seen: frozenset[int] = frozenset()) -> list[str]:
        if cid in cache:
            return cache[cid]
        node = by_id.get(cid)
        if node is None or cid in seen:
            return []
        parent = int(node.get("parent") or 0)
        prefix = path(parent, seen | {cid}) if parent else []
        name = (node.get("name") or "").strip()

        segments = _slug_segments(node)
        if len(segments) > len(prefix) + 1:
            # Vecāku ķēde ir īsāka par adresi -> kāds posms sarakstā trūkst.
            prefix = [by_slug.get(seg) or _name_from_slug(seg) for seg in segments[:-1]]

        cache[cid] = prefix + [name]
        return cache[cid]

    return {cid: path(cid) for cid in by_id}


def parse_store_product(
    item: dict[str, Any],
    category_paths: dict[int, list[str]] | None = None,
    unit_overrides: dict[str, str] | None = None,
) -> Product:
    prices = item.get("prices") or {}
    minor = int(prices.get("currency_minor_unit") or 2)
    raw_price = prices.get("price")
    incl: float | None = None
    if raw_price not in (None, "", "0"):
        try:
            incl = int(raw_price) / (10**minor)
        except (TypeError, ValueError):
            incl = None

    attributes = normalize.attributes_to_dict(item.get("attributes") or [])

    paths = category_paths or {}
    categories: list[str] = []
    for raw_cat in item.get("categories") or []:
        cid = raw_cat.get("id")
        chain = paths.get(int(cid)) if cid is not None else None
        if not chain:
            chain = [c for c in [(raw_cat.get("name") or "").strip()] if c]
        if chain:
            categories.append(" > ".join(chain))
    roots = [c.split(" > ")[0] for c in categories]
    description = _strip_html(item.get("description"))
    short = _strip_html(item.get("short_description"))
    # Nosaukumos ir HTML entītijas: "Camlock blīve DN25 NBR (1&#8243;)".
    name = _strip_html(item.get("name"))

    fields = normalize.normalize_attributes(name, attributes, f"{short} {description}")

    # Pārejas otrais diametrs + tipa kods + meklēšanas aliasi.
    # Profiliem nosaukuma izmērus par diametru NEPĀRVĒRŠAM (skat. is_profile);
    # atribūtos norādīts DN paliek spēkā, ja tāds tiešām ir.
    dn_first, dn_second = normalize.parse_dn_pair(name)
    if normalize.is_profile(name):
        dn_first = dn_second = None
    if fields["dn_mm"] is None:
        fields["dn_mm"] = dn_first
    type_code = normalize.parse_type_code(name, attributes)
    category_path = categories[0] if categories else ""
    stock_text = normalize.clean_text(
        (item.get("stock_availability") or {}).get("text")
    )

    sku = (item.get("sku") or "").strip()
    # Mērvienība: vispirms tas, kas tiešām rakstīts nosaukumā/atribūtos, un
    # tikai tad kategorijas likums. Veikala dati to nesatur, skat. `units.py`.
    unit = normalize.detect_unit(name, attributes) or units.resolve_unit(
        sku, category_path, unit_overrides
    )

    return Product(
        id=int(item["id"]),
        sku=sku,
        name=name,
        permalink=item.get("permalink") or "",
        image_url=_first_image(item.get("images")),
        description=description,
        short_description=short,
        price_excl_vat=_price_excl_vat(item, incl),
        price_incl_vat=incl,
        currency=prices.get("currency_code") or "EUR",
        unit=unit,
        is_in_stock=bool(item.get("is_in_stock")),
        is_purchasable=bool(item.get("is_purchasable")),
        stock_text=stock_text,
        stock_qty=_stock_qty(stock_text),
        dn_mm_2=dn_second,
        type_code=type_code,
        aliases=normalize.build_aliases(
            name, category_path, type_code,
            fields["material"], fields["thickness_mm"], fields["color"],
        ),
        category=category_path,
        category_root=roots[0] if roots else "",
        categories_json=normalize.dumps(categories),
        attributes_json=normalize.dumps(attributes),
        synced_at=_now(),
        **fields,
    )


def iter_store_products(
    client: httpx.Client,
    progress=None,
    category_paths: dict[int, list[str]] | None = None,
    unit_overrides: dict[str, str] | None = None,
) -> Iterator[Product]:
    page = 1
    total_pages = 1
    while page <= total_pages:
        resp = client.get(
            f"{STORE_API}/products",
            params={"page": page, "per_page": PER_PAGE, "orderby": "id", "order": "asc"},
        )
        resp.raise_for_status()
        if page == 1:
            total_pages = int(resp.headers.get("X-WP-TotalPages") or 1)
            total = int(resp.headers.get("X-WP-Total") or 0)
            if progress:
                progress(f"Store API: {total} produkti, {total_pages} lapas")
        items = resp.json()
        if not items:
            break
        for item in items:
            yield parse_store_product(item, category_paths, unit_overrides)
        if progress:
            progress(f"  lapa {page}/{total_pages}")
        page += 1


# ---------------------------------------------------------------------------
# Rezerves variants: sitemap + JSON-LD scrape
# ---------------------------------------------------------------------------
def _cache_path(url: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", url.lower())[-120:]
    return CACHE_DIR / f"{slug}.html"


def _fetch_cached(client: httpx.Client, url: str) -> str:
    path = _cache_path(url)
    if path.exists():
        return path.read_text(encoding="utf-8")
    resp = client.get(url, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    path.write_text(resp.text, encoding="utf-8")
    time.sleep(SCRAPE_DELAY_S)
    return resp.text


def _sitemap_product_urls(client: httpx.Client) -> list[str]:
    index = client.get(SITEMAP_URL, headers={"User-Agent": USER_AGENT}).text
    maps = [u for u in re.findall(r"<loc>(.*?)</loc>", index) if "product" in u]
    urls: list[str] = []
    for sm in maps:
        body = client.get(sm, headers={"User-Agent": USER_AGENT}).text
        urls += [html.unescape(u) for u in re.findall(r"<loc>(.*?)</loc>", body)]
        time.sleep(SCRAPE_DELAY_S)
    return [u for u in urls if "/produkts/" in u]


def _jsonld_products(page: str) -> Iterator[dict[str, Any]]:
    for block in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S
    ):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                types = node.get("@type")
                types = types if isinstance(types, list) else [types]
                if "Product" in types:
                    yield node
                stack.extend(v for v in node.values() if isinstance(v, (list, dict)))


def _scrape_attributes(page: str) -> dict[str, str]:
    m = re.search(
        r'<table[^>]*class="[^"]*woocommerce-product-attributes[^"]*".*?</table>',
        page,
        re.S | re.I,
    )
    if not m:
        return {}
    out: dict[str, str] = {}
    for row in re.findall(r"<tr\b.*?</tr>", m.group(0), re.S | re.I):
        cells = re.findall(r"<t[hd]\b.*?>(.*?)</t[hd]>", row, re.S | re.I)
        if len(cells) >= 2:
            key = _strip_html(cells[0]).rstrip(":")
            value = _strip_html(cells[1])
            if key and value:
                out[key] = value
    return out


def _jsonld_image(node: dict[str, Any]) -> str:
    """JSON-LD `image` ir vai nu virkne, vai saraksts, vai ImageObject."""
    raw = node.get("image")
    for candidate in raw if isinstance(raw, list) else [raw]:
        if isinstance(candidate, dict):
            candidate = candidate.get("url") or candidate.get("contentUrl")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def parse_scraped_product(url: str, page: str) -> Product | None:
    node = next(_jsonld_products(page), None)
    if not node:
        return None

    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    offers = offers or {}

    try:
        incl = float(str(offers.get("price")).replace(",", "."))
    except (TypeError, ValueError):
        incl = None

    availability = str(offers.get("availability") or "")
    in_stock = "InStock" in availability

    attributes = _scrape_attributes(page)
    name = _strip_html(node.get("name")) or ""
    description = _strip_html(node.get("description"))
    fields = normalize.normalize_attributes(name, attributes, description)

    pid = node.get("productID") or node.get("@id") or url
    digits = re.findall(r"\d+", str(pid))
    product_id = int(digits[-1]) if digits else abs(hash(url)) % (10**9)

    return Product(
        id=product_id,
        sku=str(node.get("sku") or "").strip(),
        name=name,
        permalink=url,
        image_url=_jsonld_image(node),
        description=description,
        price_excl_vat=round(incl / (1 + VAT_RATE), 2) if incl else None,
        price_incl_vat=incl,
        is_in_stock=in_stock,
        is_purchasable=in_stock,
        stock_text="Ir noliktavā" if in_stock else "Nav noliktavā",
        attributes_json=normalize.dumps(attributes),
        synced_at=_now(),
        **fields,
    )


def iter_scraped_products(client: httpx.Client, progress=None) -> Iterator[Product]:
    urls = _sitemap_product_urls(client)
    if progress:
        progress(f"Sitemap: {len(urls)} produktu lapas")
    for i, url in enumerate(urls, 1):
        try:
            product = parse_scraped_product(url, _fetch_cached(client, url))
        except httpx.HTTPError as exc:
            if progress:
                progress(f"  ! {url}: {exc}")
            continue
        if product:
            yield product
        if progress and i % 50 == 0:
            progress(f"  {i}/{len(urls)}")


# ---------------------------------------------------------------------------
# Kategorijas
# ---------------------------------------------------------------------------
def fetch_categories(client: httpx.Client) -> list[dict[str, Any]]:
    resp = client.get(f"{STORE_API}/products/categories", params={"per_page": PER_PAGE})
    resp.raise_for_status()
    out = list(resp.json())
    total_pages = int(resp.headers.get("X-WP-TotalPages") or 1)
    for page in range(2, total_pages + 1):
        resp = client.get(
            f"{STORE_API}/products/categories",
            params={"per_page": PER_PAGE, "page": page},
        )
        resp.raise_for_status()
        out += resp.json()
    return out


# ---------------------------------------------------------------------------
# Ieejas punkts
# ---------------------------------------------------------------------------
def run_sync(source: str = "api", full: bool = False, progress=None, batch: int = 200) -> int:
    say = progress or (lambda msg: None)
    with db.session() as conn, httpx.Client(
        headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
    ) as client:
        if full:
            say("--full: dzēšu esošos ierakstus")
            db.clear_products(conn)

        # Kategorijas vispirms: produktos ir tikai lapas nosaukums ("EPDM"),
        # pilno ceļu var uzbūvēt tikai no kategoriju koka.
        category_paths: dict[int, list[str]] = {}
        try:
            categories = fetch_categories(client)
            category_paths = build_category_paths(categories)
            db.set_meta(conn, "categories", normalize.dumps(categories))
            say(f"Kategorijas: {len(categories)}")
        except httpx.HTTPError as exc:
            say(f"Kategorijas neizdevās ielādēt: {exc}")

        # Mērvienību izņēmumus nolasām vienreiz — fails ir mazs, bet to
        # lasīt pie katra no 3568 produktiem nav ko.
        unit_overrides = units.load_overrides()
        if unit_overrides:
            say(f"Mērvienību izņēmumi: {len(unit_overrides)} artikuli")

        stream = (
            iter_store_products(client, say, category_paths, unit_overrides)
            if source == "api"
            else iter_scraped_products(client, say)
        )

        buffer: list[Product] = []
        count = 0
        for product in stream:
            buffer.append(product)
            if len(buffer) >= batch:
                count += db.upsert_products(conn, buffer)
                buffer.clear()
        if buffer:
            count += db.upsert_products(conn, buffer)

        # Scrape ceļš kategorijas nezina, un likumi var būt mainīti kopš
        # pēdējās reizes — pārrēķinām mērvienības visam katalogam.
        by_unit = units.apply_units(conn, unit_overrides)
        say("Mērvienības: " + ", ".join(f"{u}={n}" for u, n in sorted(by_unit.items())))

        db.rebuild_fts(conn)
        db.set_meta(conn, "synced_at", _now())
        db.set_meta(conn, "source", source)
        return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sinhronizē e-supplier.lv katalogu")
    parser.add_argument("--source", choices=("api", "scrape"), default="api")
    parser.add_argument("--full", action="store_true", help="dzēst un ievilkt no nulles")
    args = parser.parse_args(argv)

    started = time.monotonic()
    try:
        count = run_sync(args.source, args.full, progress=lambda m: print(m, flush=True))
    except httpx.HTTPStatusError as exc:
        print(f"Kļūda: {exc}", file=sys.stderr)
        if args.source == "api":
            print("Mēģini: uv run sync --source=scrape", file=sys.stderr)
        return 1

    print(f"\nGatavs: {count} produkti, {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
