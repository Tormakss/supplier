"""Datu struktūras."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from typing import Any

from . import units


@dataclass(slots=True)
class Attribute:
    """Neapstrādāts produkta atribūts no veikala (nosaukums + vērtības)."""

    name: str
    values: list[str] = field(default_factory=list)

    @property
    def value(self) -> str:
        return ", ".join(self.values)


@dataclass(slots=True)
class Product:
    """Produkts ar normalizētajām kolonnām.

    Cenas: `price_excl_vat` ir cena BEZ PVN, `price_incl_vat` — ar PVN.
    Store API atgriež cenu ar PVN, tāpēc abas glabājam atsevišķi.
    """

    id: int
    sku: str
    name: str
    permalink: str
    #: Galvenā produkta bilde. Klients atbildi bez foto neatpazīst — blīves un
    #: savienojumi izskatās vienādi, kamēr tos neredz.
    image_url: str = ""
    description: str = ""
    short_description: str = ""

    price_excl_vat: float | None = None
    price_incl_vat: float | None = None
    currency: str = "EUR"
    #: Kā prece tiek tirgota: `m`, `m2` vai `gab`. Veikala datos šī lauka NAV —
    #: to pieliek `catalog/units.py` pēc kategorijas un SKU izņēmumiem.
    unit: str = "gab"

    is_in_stock: bool = False
    is_purchasable: bool = False
    stock_text: str = ""
    stock_qty: int | None = None

    #: Pilnais kategorijas ceļš, piem. "Šļūtenu savienojumi > Camlock > EPDM".
    category: str = ""
    #: Augšējā līmeņa kategorija — pēc tās grupējam katalogu.
    category_root: str = ""
    categories_json: str = "[]"

    # --- normalizētie tehniskie lauki ---
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    thickness_mm: float | None = None
    #: Pārejai ir divi diametri — `dn_mm` ir pirmais, `dn_mm_2` otrais.
    dn_mm: int | None = None
    dn_mm_2: int | None = None
    pressure_bar: float | None = None
    material: str | None = None
    length_m: float | None = None
    hardness_sha: float | None = None
    #: Krāsa no atribūtiem, tāda, kāda tā ir katalogā ("Melna", "Pelēka").
    color: str | None = None
    standard: str | None = None
    #: Savienojuma tipa kods: AR, DAR, C, E, ...
    type_code: str | None = None
    #: Ģenerēts meklēšanas teksts (indeksēts FTS, lietotājam nerādām).
    aliases: str = ""

    food_grade: bool = False
    oil_resistant: bool = False
    chemical_resistant: bool = False

    attributes_json: str = "{}"
    synced_at: str = ""

    # ------------------------------------------------------------------
    @property
    def categories(self) -> list[str]:
        try:
            return json.loads(self.categories_json)
        except (ValueError, TypeError):
            return []

    @property
    def attributes(self) -> dict[str, str]:
        try:
            return json.loads(self.attributes_json)
        except (ValueError, TypeError):
            return {}

    @property
    def price(self) -> float | None:
        """Noklusējuma cena sarunā — bez PVN."""
        return self.price_excl_vat

    @classmethod
    def from_row(cls, row: Any) -> Product:
        names = {f.name for f in fields(cls)}
        data = {k: row[k] for k in row.keys() if k in names}
        for flag in ("is_in_stock", "is_purchasable", "food_grade", "oil_resistant", "chemical_resistant"):
            if flag in data:
                data[flag] = bool(data[flag])
        return cls(**data)

    def to_search_dict(self) -> dict[str, Any]:
        """Kompakts attēlojums modelim.

        Apzināti šaurs: aprakstu šeit NAV nekad (Camlock produktiem tie ir
        identiski ~900 zīmju bloki, kas noēd kontekstu), un tehniskie lauki,
        kas vajadzīgi retāk, nāk tikai caur `get_product`.
        """
        out = {
            "sku": self.sku,
            "name": self.name,
            "price_eur_excl_vat": self.price_excl_vat,
            "price_eur_incl_vat": self.price_incl_vat,
            # Modelim atdodam gatavu apzīmējumu ("m²", nevis "m2") — to tas
            # kopē vēstulē, un pārrakstīšana pa ceļam nav vajadzīga.
            "unit": units.LABELS.get(self.unit, self.unit),
            "in_stock": self.is_in_stock,
            "url": self.permalink,
        }
        # Tikai tad, kad ir ko teikt — citādi tērē kontekstu ar null. Sešos
        # rezultātos katrs tukšais lauks maksā seškārt, un tas atkārtojas pie
        # katra nākamā rīka izsaukuma, kad vēsture tiek pārsūtīta no jauna.
        if self.material:
            out["material"] = self.material
        if self.dn_mm is not None:
            out["dn_mm"] = self.dn_mm
        if self.food_grade:
            out["food_grade"] = True
        if self.image_url:
            out["image_url"] = self.image_url
        # Krāsu un cietību klients nosauc pirmajā teikumā ("pelēks, 70 Shore"),
        # tāpēc tās vajag jau meklēšanas rezultātā, nevis pēc get_product.
        if self.color:
            out["color"] = self.color
        if self.hardness_sha is not None:
            out["hardness_sha"] = self.hardness_sha
        if self.stock_qty is not None:
            out["stock_qty"] = self.stock_qty
        if self.dn_mm_2 is not None:
            out["dn_mm_2"] = self.dn_mm_2
        if self.type_code:
            out["type_code"] = self.type_code
        return out

    def to_browse_dict(self) -> dict[str, Any]:
        """Vēl šaurāks attēlojums pārlūkošanai.

        `browse_category` atdod desmitiem produktu vienā izsaukumā, tāpēc katrs
        lieks lauks reizinās ar 30. Šeit ir tikai tas, kas vajadzīgs, lai
        ATPAZĪTU kandidātu; detaļas pēc tam paņem ar get_product.
        """
        out = {
            "sku": self.sku,
            "name": self.name,
            "price": self.price_excl_vat,
            "in_stock": self.is_in_stock,
        }
        if self.dn_mm is not None:
            out["dn"] = self.dn_mm
        if self.dn_mm_2 is not None:
            out["dn2"] = self.dn_mm_2
        if self.type_code:
            out["type"] = self.type_code
        return out
