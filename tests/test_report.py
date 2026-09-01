"""Atbildes sagatavošanas testi.

Divas lietas, kuras nedrīkst salūzt klusi: (1) iekšējās piezīmes nedrīkst
nonākt klientam paredzētajā HTML, (2) attēls, kura nav katalogā, nedrīkst
aizceļot uz e-pastu — nepareiza bilde ir sliktāka par bildes trūkumu.
"""

from __future__ import annotations

import sqlite3

import pytest

from esupplier import report
from esupplier.catalog import db
from esupplier.catalog.models import Product

IMG = "https://e-supplier.lv/wp-content/uploads/2026/08/89073.png"

ANSWER = f"""\
Labdien! Paldies par pieprasījumu.

| Foto | Artikuls | Nosaukums | Cena | Noliktavā |
|---|---|---|---|---|
| ![AO Gs](image) | 48 | AO Gs Armēts EPDM | 4.10 € bez PVN | 100 m |

Jums vajadzīgi 25 m, pašlaik mūsu noliktavā ir pieejami 19 metri.

Ar cieņu,
Tehnisko Materiālu Sagāde

---

⚑ IEKŠĒJI (klientam nesūtīt)
- krāsa inventārā nav norādīta
""".replace("image", IMG)


# ---------------------------------------------------------------------------
# Sadalīšana
# ---------------------------------------------------------------------------
def test_split_removes_internal_block():
    letter, internal = report.split_answer(ANSWER)
    assert "IEKŠĒJI" not in letter
    assert "krāsa inventārā" not in letter
    assert "krāsa inventārā" in internal
    assert letter.startswith("Labdien!")


def test_split_drops_the_separator_rule():
    letter, _ = report.split_answer(ANSWER)
    assert not letter.rstrip().endswith("---")


@pytest.mark.parametrize(
    "heading",
    [
        "⚑ IEKŠĒJI (klientam nesūtīt)",
        "## ⚑ Iekšēji",
        "**ВНУТРЕННЕЕ (клиенту не отправлять)**",
        "INTERNAL NOTES",
        "IEKSEJI",
    ],
)
def test_split_recognises_heading_variants(heading):
    """Virsraksta valoda mainās līdz ar menedžera valodu, marķieris — nē."""
    letter, internal = report.split_answer(f"Labdien!\n\n---\n\n{heading}\n- piezīme")
    assert letter == "Labdien!"
    assert "piezīme" in internal


def test_split_without_internal_block_keeps_everything():
    letter, internal = report.split_answer("Labdien! Cena 4.10 €.")
    assert letter == "Labdien! Cena 4.10 €."
    assert internal == ""


def test_split_keeps_a_rule_that_is_not_a_separator():
    """`---` atbildes vidū ir noformējums, nevis iekšējās daļas sākums."""
    letter, internal = report.split_answer("Pirmā daļa\n\n---\n\nOtrā daļa")
    assert "Otrā daļa" in letter
    assert internal == ""


# ---------------------------------------------------------------------------
# Attēlu pārbaude
# ---------------------------------------------------------------------------
def test_unknown_image_is_dropped():
    text, dropped = report.verify_images(f"![x]({IMG}) un ![y](https://cits.lv/a.png)", {IMG})
    assert IMG in text
    assert "cits.lv" not in text
    assert dropped == ["https://cits.lv/a.png"]


def test_empty_catalog_skips_verification():
    """Bez sinhronizēta kataloga pārbaudei nav pret ko salīdzināt."""
    text, dropped = report.verify_images(f"![x]({IMG})", set())
    assert IMG in text
    assert dropped == []


def test_console_turns_images_into_clickable_icons():
    """Konsolē attēls ir saite aiz ikonas, ne izplests URL.

    Ikona vien bija par maz: menedžeris redzēja 📷 bez adreses un bez
    pielikuma. URL tabulā izstieptu kolonnu pāri ekrānam, tāpēc tas paliek
    aiz Markdown saites — Rich to atdod kā termināļa hipersaiti.
    """
    line = report.for_console(f"| ![AO Gs]({IMG}) | 48 |")
    assert line == f"| [📷]({IMG}) | 48 |"
    assert "![" not in line


def test_internal_block_detection():
    with_block = "Labdien!\n\n---\n\n⚑ IEKŠĒJI (klientam nesūtīt)\n- rezervēt 358 m"
    assert report.has_internal(with_block)
    # Apcirsta atbilde beidzas ar vēstuli un bloka nav — tieši to menedžeris
    # nolasa kā "nekas nav jādara".
    assert not report.has_internal("Labdien! Cena 4.10 € bez PVN / m.")


def test_known_image_urls_reads_the_catalog(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    db.upsert_products(
        conn,
        [
            Product(id=1, sku="48", name="Ar bildi", permalink="", image_url=IMG),
            Product(id=2, sku="52", name="Bez bildes", permalink=""),
        ],
    )
    assert report.known_image_urls(conn) == {IMG}
    conn.close()


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def test_html_has_inline_styles_for_email_clients():
    """Outlook izmet <style> bloku, tāpēc stiliem jābūt pie katra taga."""
    out = report.render_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table style=" in out
    assert "<th style=" in out
    assert "<td style=" in out


def test_html_renders_images_and_links():
    out = report.render_html(f"![AO Gs]({IMG})\n\n[saite](https://e-supplier.lv/p/48)")
    assert f'src="{IMG}"' in out
    assert 'href="https://e-supplier.lv/p/48"' in out


def test_saved_file_contains_no_internal_notes(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    db.upsert_products(
        conn, [Product(id=1, sku="48", name="AO Gs", permalink="", image_url=IMG)]
    )
    path, dropped = report.save_answer(ANSWER, path=tmp_path / "vestule", conn=conn)
    body = path.read_text(encoding="utf-8")
    conn.close()

    assert path.suffix == ".html"
    assert dropped == []
    assert "IEKŠĒJI" not in body
    assert "krāsa inventārā" not in body
    assert "Jums vajadzīgi 25 m" in body
    assert f'src="{IMG}"' in body


def test_saved_file_drops_a_hallucinated_image(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    db.upsert_products(
        conn, [Product(id=1, sku="48", name="AO Gs", permalink="", image_url=IMG)]
    )
    answer = "Labdien!\n\n![izdomāts](https://e-supplier.lv/nav-tada.png)"
    path, dropped = report.save_answer(answer, path=tmp_path / "v.html", conn=conn)
    conn.close()

    assert dropped == ["https://e-supplier.lv/nav-tada.png"]
    assert "nav-tada.png" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Migrācija
# ---------------------------------------------------------------------------
def test_migration_adds_image_url_to_an_old_database(tmp_path):
    """Vecs catalog.db bez `image_url` nedrīkst krist ar "no such column"."""
    path = tmp_path / "vecs.db"
    old = sqlite3.connect(path)
    # Iepriekšējā shēma = tagadējā bez jaunajām kolonnām.
    previous = "\n".join(
        line
        for line in db.SCHEMA.splitlines()
        if not any(line.strip().startswith(column) for column, _ in db._ADDED_COLUMNS)
    )
    old.executescript(previous)
    old.commit()
    old.close()

    conn = db.connect(path)
    db.init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
    conn.close()
    assert "image_url" in columns


def test_migration_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "jauns.db")
    db.init_db(conn)
    assert db.migrate(conn) == []
    conn.close()


# ---------------------------------------------------------------------------
# Iekšējā adrese vēstulē
# ---------------------------------------------------------------------------
def test_contact_leaks_atrod_adresi_vestule() -> None:
    """Klients uz šo adresi tikko rakstīja — atpakaļ to sūtīt nedrīkst."""
    text = (
        "Labdien!\n"
        "Пожалуйста, направьте этот запрос на office@supplier.lv\n"
        "\n---\n⚑ IEKŠĒJI (klientam nesūtīt)\n- nav\n"
    )
    leaks = report.contact_leaks(text)
    assert len(leaks) == 1
    assert "office@supplier.lv" in leaks[0]


def test_contact_leaks_neuzskata_ieksejo_dalu() -> None:
    """Iekšējā daļā eskalācijas adrese ir tieši tur, kur tai jābūt."""
    text = (
        "Labdien! Piedāvājums sagatavots.\n"
        "\n---\n⚑ IEKŠĒJI (klientam nesūtīt)\n"
        "- MOQ jāapstiprina, raksti office@supplier.lv\n"
    )
    assert report.contact_leaks(text) == []


def test_contact_leaks_tira_vestule() -> None:
    assert report.contact_leaks(ANSWER) == []

