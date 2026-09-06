"""Pielikumu lasīšana.

Ko šeit sargājam: pielikums, kas NETIKA izlasīts, nedrīkst izskatīties pēc
izlasīta. Skenēts rasējums, CAD fails un arhīvs teksta nesatur, un modelim
par tiem jāzina tikai tas, ka tie ir un ka tos atvērs cilvēks.
"""

from __future__ import annotations

from email.message import EmailMessage

from esupplier.mail.attachments import extract_attachments, extract_text
from helpers_files import make_docx, make_empty_pdf, make_pdf, make_xlsx


def build(files: list[tuple[str, bytes]], maintype: str = "application") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Jānis <janis@klients.lv>"
    msg["Subject"] = "Pieprasījums"
    msg.set_content("Labdien! Skat. pielikumā.")
    for name, data in files:
        msg.add_attachment(data, maintype=maintype, subtype="octet-stream", filename=name)
    return msg


# --- formāti ---------------------------------------------------------------
def test_pdf_text_is_extracted() -> None:
    text, note = extract_text(
        "rasejums.pdf", "application/pdf", make_pdf(["EPDM profils D 12 mm", "358 gab."])
    )
    assert "EPDM profils D 12 mm" in text
    assert "358 gab." in text
    assert note == ""


def test_scanned_pdf_is_honestly_reported_as_unread() -> None:
    """Bilde PDF iepakojumā. Tukšs teksts te ir bīstamāks par kļūdu: modelis
    "izlasītu" tukšumu un klusētu par pusi pieprasījuma."""
    text, note = extract_text("skens.pdf", "application/pdf", make_empty_pdf())
    assert text == ""
    assert "skenēts" in note


def test_broken_pdf_does_not_raise() -> None:
    text, note = extract_text("bojats.pdf", "application/pdf", b"%PDF-1.4 nav nekas")
    assert text == ""
    assert note


def test_docx_paragraphs_and_table() -> None:
    data = make_docx(["Vajag blīvgumiju."], table=[["Prece", "Skaits"], ["EPDM D12", "358"]])
    text, note = extract_text("pieprasijums.docx", "", data)
    assert note == ""
    assert "Vajag blīvgumiju." in text
    assert "EPDM D12 | 358" in text


def test_xlsx_rows_keep_the_sheet_name() -> None:
    data = make_xlsx([["Prece", "Skaits"], ["EPDM D12", 358]], sheet_name="Specifikācija")
    text, note = extract_text("spec.xlsx", "", data)
    assert note == ""
    assert "[lapa: Specifikācija]" in text
    assert "EPDM D12 | 358" in text


def test_plain_text_survives_cp1257() -> None:
    text, note = extract_text("piezimes.txt", "text/plain", "Blīvgumija 60 Sh".encode("cp1257"))
    assert text == "Blīvgumija 60 Sh"
    assert note == ""


def test_image_is_named_but_not_read() -> None:
    text, note = extract_text("skice.jpg", "image/jpeg", b"\xff\xd8\xff\xe0 binary")
    assert text == ""
    assert "attēls" in note


def test_cad_file_is_named_by_what_it_is() -> None:
    """Menedžerim "AutoCAD rasējums" pasaka, ko darīt; "nezināms fails" — ne."""
    _, note = extract_text("detala.dwg", "application/octet-stream", b"AC1027")
    assert "AutoCAD" in note


# --- limiti ----------------------------------------------------------------
def test_huge_attachment_is_not_opened(monkeypatch) -> None:
    from esupplier.mail import attachments as mod

    monkeypatch.setattr(mod, "MAIL_ATTACHMENT_MAX_BYTES", 100)
    found = extract_attachments(build([("katalogs.txt", b"x" * 500)]))
    assert found[0].text == ""
    assert "liels" in found[0].note


def test_one_attachment_is_cut_at_the_per_file_limit(monkeypatch) -> None:
    from esupplier.mail import attachments as mod

    monkeypatch.setattr(mod, "MAIL_ATTACHMENT_TEXT_LIMIT", 50)
    found = extract_attachments(build([("garš.txt", ("rinda\n" * 200).encode())]))
    assert "apcirsts" in found[0].text
    assert len(found[0].text) < 120


def test_total_budget_stops_the_second_attachment(monkeypatch) -> None:
    """Divi gari faili citādi izspiestu no konteksta pašu vēstuli."""
    from esupplier.mail import attachments as mod

    monkeypatch.setattr(mod, "MAIL_ATTACHMENTS_TEXT_LIMIT", 40)
    found = extract_attachments(
        build([("pirmais.txt", b"a" * 100), ("otrais.txt", b"b" * 100)])
    )
    assert found[0].read
    assert not found[1].read
    assert found[1].note


# --- vēstules līmenis ------------------------------------------------------
def test_attachment_names_are_decoded() -> None:
    msg = build([])
    msg.add_attachment(
        make_pdf(["dati"]), maintype="application", subtype="pdf", filename="rasējums.pdf"
    )
    found = extract_attachments(msg)
    assert found[0].name == "rasējums.pdf"
    assert found[0].size > 0


def test_empty_attachment_is_flagged() -> None:
    found = extract_attachments(build([("tukss.pdf", b"")]))
    assert found[0].text == ""
    assert found[0].note
