"""SQLite shēma, savienojums un upsert."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..config import DB_PATH
from .models import Product

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id                 INTEGER PRIMARY KEY,
    sku                TEXT,
    name               TEXT NOT NULL,
    permalink          TEXT,
    -- Galvenās bildes adrese; atbildē klientam foto ir obligāts.
    image_url          TEXT DEFAULT '',
    description        TEXT DEFAULT '',
    short_description  TEXT DEFAULT '',

    price_excl_vat     REAL,
    price_incl_vat     REAL,
    currency           TEXT DEFAULT 'EUR',
    unit               TEXT DEFAULT 'gab',

    is_in_stock        INTEGER DEFAULT 0,
    is_purchasable     INTEGER DEFAULT 0,
    stock_text         TEXT DEFAULT '',
    stock_qty          INTEGER,

    -- `category` ir pilnais ceļš ("Šļūtenu savienojumi > Camlock > EPDM"),
    -- `category_root` — augšējā līmeņa kategorija grupēšanai.
    category           TEXT DEFAULT '',
    category_root      TEXT DEFAULT '',
    categories_json    TEXT DEFAULT '[]',

    temp_min_c         REAL,
    temp_max_c         REAL,
    thickness_mm       REAL,
    -- Pārejai ir DIVI diametri; klients var jautāt pēc jebkura no tiem.
    dn_mm              INTEGER,
    dn_mm_2            INTEGER,
    pressure_bar       REAL,
    material           TEXT,
    length_m           REAL,
    hardness_sha       REAL,
    -- Krāsa no atribūtiem; klients to nosauc pirmo, katalogs to slēpj.
    color              TEXT,
    standard           TEXT,
    -- Savienojuma tipa kods (AR, DAR, C, ...). Kolonna, nevis teksts, jo
    -- "AR" kā FTS marķieris sakrīt ar latviešu vārdu "ar".
    type_code          TEXT,
    -- Ģenerēts meklēšanas teksts: klienta vārdi -> kataloga kodi.
    aliases            TEXT DEFAULT '',

    food_grade         INTEGER DEFAULT 0,
    oil_resistant      INTEGER DEFAULT 0,
    chemical_resistant INTEGER DEFAULT 0,

    attributes_json    TEXT DEFAULT '{}',
    synced_at          TEXT
);

-- SKU nav unikāls: katalogā ir produkti, kas dala vienu artikulu.
CREATE INDEX IF NOT EXISTS idx_products_sku       ON products(sku);
CREATE INDEX IF NOT EXISTS idx_products_dn        ON products(dn_mm);
CREATE INDEX IF NOT EXISTS idx_products_material  ON products(material);
CREATE INDEX IF NOT EXISTS idx_products_food      ON products(food_grade);
CREATE INDEX IF NOT EXISTS idx_products_stock     ON products(is_in_stock);
CREATE INDEX IF NOT EXISTS idx_products_catroot   ON products(category_root);
CREATE INDEX IF NOT EXISTS idx_products_dn2       ON products(dn_mm_2);
CREATE INDEX IF NOT EXISTS idx_products_type      ON products(type_code);
CREATE INDEX IF NOT EXISTS idx_products_temp      ON products(temp_min_c, temp_max_c);

CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
    name, aliases, description, category, material,
    content='products', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# FTS sinhronizācija ar `products` (external content table).
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS products_ai AFTER INSERT ON products BEGIN
    INSERT INTO products_fts(rowid, name, aliases, description, category, material)
    VALUES (new.id, new.name, new.aliases, new.description, new.category, new.material);
END;

CREATE TRIGGER IF NOT EXISTS products_ad AFTER DELETE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, name, aliases, description, category, material)
    VALUES ('delete', old.id, old.name, old.aliases, old.description, old.category, old.material);
END;

CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, name, aliases, description, category, material)
    VALUES ('delete', old.id, old.name, old.aliases, old.description, old.category, old.material);
    INSERT INTO products_fts(rowid, name, aliases, description, category, material)
    VALUES (new.id, new.name, new.aliases, new.description, new.category, new.material);
END;
"""

#: Kolonnas, kas pievienotas pēc pirmās versijas. `CREATE TABLE IF NOT EXISTS`
#: esošu tabulu nepapildina, tāpēc jaunas kolonnas jāpieliek ar ALTER — citādi
#: vecs `catalog.db` pēc atjauninājuma krīt ar "no such column".
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("image_url", "TEXT DEFAULT ''"),
    ("color", "TEXT"),
)

_COLUMNS = (
    "id", "sku", "name", "permalink", "image_url", "description", "short_description",
    "price_excl_vat", "price_incl_vat", "currency", "unit",
    "is_in_stock", "is_purchasable", "stock_text", "stock_qty",
    "category", "category_root", "categories_json",
    "temp_min_c", "temp_max_c", "thickness_mm", "dn_mm", "dn_mm_2",
    "pressure_bar", "material", "length_m", "hardness_sha", "color", "standard",
    "type_code", "aliases",
    "food_grade", "oil_resistant", "chemical_resistant",
    "attributes_json", "synced_at",
)

_UPSERT = (
    f"INSERT INTO products ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))}) "
    f"ON CONFLICT(id) DO UPDATE SET "
    + ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "id")
)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    db_path = Path(path or DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Pieliek trūkstošās kolonnas esošai tabulai. Atgriež pievienoto sarakstu."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
    if not existing:  # tabulas nav — SCHEMA to izveidos jau pilnā formā
        return []
    added = []
    for column, ddl in _ADDED_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE products ADD COLUMN {column} {ddl}")
            added.append(column)
    if added:
        conn.commit()
    return added


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.executescript(TRIGGERS)
    conn.commit()


@contextmanager
def session(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()


def upsert_products(conn: sqlite3.Connection, products: list[Product]) -> int:
    rows = [
        tuple(
            int(v) if isinstance(v, bool) else v
            for v in (getattr(p, c) for c in _COLUMNS)
        )
        for p in products
    ]
    conn.executemany(_UPSERT, rows)
    conn.commit()
    return len(rows)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def product_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO products_fts(products_fts) VALUES('rebuild')")
    conn.commit()


def clear_products(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM products")
    conn.execute("DELETE FROM products_fts")
    conn.commit()
