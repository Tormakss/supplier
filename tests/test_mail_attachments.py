"""Pielikumu lasīšana.

Ko šeit sargājam: pielikums, kas NETIKA izlasīts, nedrīkst izskatīties pēc
izlasīta. Skenēts rasējums, CAD fails un arhīvs teksta nesatur, un modelim
par tiem jāzina tikai tas, ka tie ir un ka tos atvērs cilvēks.
"""

from __future__ import annotations

from email.message import EmailMessage

from esupplier.mail import attachments as mod
from esupplier.mail.attachments import extract_attachments, extract_text
from helpers_files import (
    make_docx,
    make_dxf,
    make_empty_pdf,
    make_pdf,
    make_png,
    make_pptx,
    make_rtf,
    make_xls,
    make_xlsx,
    make_zip,
)


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


def test_pptx_slides_are_numbered() -> None:
    data = make_pptx([["EPDM profils D", "358 gab."], ["Piegāde jūlijā"]])
    text, note = extract_text("prezentacija.pptx", "", data)
    assert note == ""
    assert "[slaids 1]" in text and "[slaids 2]" in text
    assert "358 gab." in text


def test_old_binary_excel_is_read() -> None:
    """`.xls` no grāmatvedības programmas. Līdz šim tas palika aiz durvīm."""
    text, note = extract_text("tame.xls", "application/vnd.ms-excel", make_xls(
        [["Prece", "Skaits"], ["EPDM D12", 358]]
    ))
    assert note == ""
    assert "EPDM D12 | 358" in text


def test_rtf_keeps_the_text_and_drops_the_font_table() -> None:
    text, note = extract_text("vestule.rtf", "", make_rtf(["Vajag EPDM 12 mm.", "358 gab."]))
    assert note == ""
    assert "Vajag EPDM 12 mm." in text
    assert "358 gab." in text
    # Fontu tabula un ģeneratora birka ir dokumenta iekšas, ne pieprasījums.
    assert "Arial" not in text
    assert "Riched20" not in text


def test_dxf_labels_are_read_without_the_geometry() -> None:
    """Uzraksts rasējuma stūrī ir pats pieprasījums; koordinātas nav."""
    text, note = extract_text("detala.dxf", "", make_dxf(["EPDM 12x20", "358 gab."]))
    assert note == ""
    assert "EPDM 12x20" in text
    assert "358 gab." in text
    assert "125.5" not in text


def test_dxf_repeats_are_not_sent_twice() -> None:
    """Rāmja uzraksts rasējumā atkārtojas katrā izkārtojumā."""
    text, _ = extract_text("detala.dxf", "", make_dxf(["EPDM 12x20", "EPDM 12x20"]))
    assert text.count("EPDM 12x20") == 1


def test_binary_dxf_is_named_not_guessed() -> None:
    text, note = extract_text("detala.dxf", "", b"AutoCAD Binary DXF\r\n\x1a\x00" + b"\x00" * 40)
    assert text == ""
    assert "binārs" in note


def test_docx_renamed_to_doc_is_still_read() -> None:
    """Klients savu `.docx` nosauc par `.doc`. Baiti nemelo, nosaukums mēdz."""
    text, note = extract_text("pieprasijums.doc", "application/msword", make_docx(["Vajag EPDM."]))
    assert note == ""
    assert "Vajag EPDM." in text


def test_old_word_without_libreoffice_says_why(monkeypatch) -> None:
    """Bez LibreOffice `.doc` izlasīt nevar. Menedžerim to jāpasaka tieši."""
    monkeypatch.setattr(mod, "_soffice_bin", lambda: "")
    text, note = extract_text("vecais.doc", "", b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
    assert text == ""
    assert "vecais Word formāts" in note
    assert "LibreOffice" in note


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


# --- arhīvi ----------------------------------------------------------------
def test_zip_is_unpacked_into_its_files() -> None:
    """Rasējumu komplekts nāk ZIP failā. Pats arhīvs nav pieprasījums, bet
    katrs fails tajā var būt."""
    data = make_zip([
        ("spec.xlsx", make_xlsx([["Prece", "Skaits"], ["EPDM D12", 358]])),
        ("piezimes.txt", "Steidzami".encode("utf-8")),
    ])
    found = extract_attachments(build([("rasejumi.zip", data)]))

    assert [item.name for item in found] == [
        "rasejumi.zip → spec.xlsx",
        "rasejumi.zip → piezimes.txt",
    ]
    assert "EPDM D12 | 358" in found[0].text
    assert found[1].text == "Steidzami"


def test_office_files_are_not_unpacked_as_archives() -> None:
    """`.xlsx` arī ir ZIP. Izpakots tas modelim atdotu `[Content_Types].xml`."""
    found = extract_attachments(build([("spec.xlsx", make_xlsx([["Prece", "Skaits"]]))]))
    assert [item.name for item in found] == ["spec.xlsx"]
    assert found[0].read


def test_archive_says_when_it_had_more_files(monkeypatch) -> None:
    monkeypatch.setattr(mod, "MAIL_ARCHIVE_MAX_FILES", 2)
    data = make_zip([(f"fails{n}.txt", b"dati") for n in range(5)])
    found = extract_attachments(build([("daudz.zip", data)]))

    assert len(found) == 3  # divi faili un viena piezīme par pārējiem
    assert "pārējie netika atvērti" in found[-1].note


def test_empty_archive_is_flagged() -> None:
    found = extract_attachments(build([("tukss.zip", make_zip([]))]))
    assert found[0].note == "arhīvs ir tukšs"


# --- ko vēl var izlasīt, paskatoties ---------------------------------------
def test_scanned_pdf_keeps_its_bytes_for_transcription() -> None:
    """Neizlasīts vēl nenozīmē neizlasāms: uz PDF bez teksta var paskatīties."""
    found = extract_attachments(build([("skens.pdf", make_empty_pdf())]))
    assert not found[0].read
    assert found[0].can_transcribe
    assert found[0].image_mime == "application/pdf"


def test_image_keeps_its_bytes_for_transcription() -> None:
    found = extract_attachments(build([("foto.png", make_png())]))
    assert found[0].can_transcribe


def test_read_file_keeps_no_bytes() -> None:
    """Uz izlasītu Excel failu skatīties nav ko, un megabaiti atmiņā nav par velti."""
    found = extract_attachments(build([("spec.xlsx", make_xlsx([["Prece", "Skaits"]]))]))
    assert not found[0].can_transcribe
    assert found[0].data == b""


def test_cad_file_stays_unreadable() -> None:
    """`.dwg` nav ne teksts, ne attēls. Godīgs "jāatver ar roku" paliek."""
    found = extract_attachments(build([("detala.dwg", b"AC1027" + b"\x00" * 50)]))
    assert not found[0].can_transcribe
    assert "AutoCAD" in found[0].note


def test_locked_entry_in_archive_is_not_an_empty_file() -> None:
    """Parolēts ieraksts un tukšs fails menedžerim nozīmē dažādas lietas."""
    item = mod._make_attachment("rasejumi.zip → slepens.pdf", "", None, "")
    assert not item.read
    assert "parolēts" in item.note


def test_sevenzip_without_the_reader_says_so() -> None:
    text, note = extract_text("rasejumi.7z", "", b"7z\xbc\xaf\x27\x1c" + b"\x00" * 40)
    assert text == ""
    assert "py7zr" in note
