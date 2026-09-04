"""Ienākošās vēstules parsēšana.

Galvenā kļūda, ko šeit sargājam: citēta sarakste aiziet modelim kā aktuāls
pieprasījums. Prompts prasa atbildēt uz KATRU pieminēto pozīciju, tāpēc vecā
sarakstē minētā prece nonāk jaunajā piedāvājumā kā tikko pasūtīta.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from esupplier.mail.message import (
    as_prompt,
    clean_body,
    extract_body,
    parse_message,
    skip_reason,
)


def build(
    *,
    body: str = "Labdien! Vajag EPDM profilu 12 mm, 25 metrus.",
    subject: str = "Pieprasījums",
    sender: str = "Jānis Bērziņš <janis@klients.lv>",
    html: str = "",
    headers: dict[str, str] | None = None,
    attachments: list[tuple[str, bytes]] | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "ai.0001@trialine.lv"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc123@klients.lv>"
    msg["Date"] = "Wed, 03 Sep 2026 10:15:00 +0300"
    for key, value in (headers or {}).items():
        msg[key] = value
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    for name, data in attachments or []:
        msg.add_attachment(
            data, maintype="application", subtype="octet-stream", filename=name
        )
    return msg.as_bytes()


# --- ķermenis --------------------------------------------------------------
def test_plain_body_survives_utf8() -> None:
    incoming = parse_message(build(body="Vajag blīvgumiju ar cietību 60 Sh."))
    assert "blīvgumiju" in incoming.body
    assert incoming.subject == "Pieprasījums"
    assert incoming.sender == "janis@klients.lv"
    assert incoming.sender_name == "Jānis Bērziņš"


def test_encoded_subject_is_decoded() -> None:
    raw = build(subject="Запрос на уплотнитель")
    assert parse_message(raw).subject == "Запрос на уплотнитель"


def test_html_only_letter_falls_back_to_stripped_html() -> None:
    msg = EmailMessage()
    msg["From"] = "a@b.lv"
    msg["Subject"] = "x"
    msg.set_content("<p>Vajag <b>EPDM</b> profilu</p>", subtype="html")
    body = extract_body(
        __import__("email").message_from_bytes(
            msg.as_bytes(), policy=__import__("email").policy.default
        )
    )
    assert "Vajag EPDM profilu" in body
    assert "<b>" not in body


def test_plain_part_wins_over_html() -> None:
    incoming = parse_message(build(body="TEKSTS", html="<p>HTML</p>"))
    assert "TEKSTS" in incoming.body


# --- citāti un paraksts ----------------------------------------------------
QUOTED = """\
Labdien!

Vajag D veida profilu 12 mm, 358 gab.

2026. gada 1. septembrī plkst. 10:00 Anna <anna@klients.lv> rakstīja:
> Iepriekšējais pieprasījums bija par PTFE stieņiem 40 mm
> un par filca loksnēm.
"""


def test_quoted_history_is_cut() -> None:
    body = clean_body(QUOTED)
    assert "D veida profilu" in body
    assert "PTFE" not in body
    assert "filca" not in body


def test_outlook_header_block_is_cut() -> None:
    text = (
        "Vajag silikona šļūteni DN25.\n\n"
        "-----Original Message-----\n"
        "From: Anna\nSubject: vecs pieprasījums\n"
        "Vajag camlock savienojumus.\n"
    )
    body = clean_body(text)
    assert "silikona šļūteni" in body
    assert "camlock" not in body


def test_russian_attribution_is_cut() -> None:
    text = "Нужен профиль 12 мм.\n\nОт: Анна\nКому: мы\nНужны хомуты.\n"
    body = clean_body(text)
    assert "профиль" in body
    assert "хомуты" not in body


def test_signature_is_cut() -> None:
    text = "Vajag 25 m šļūtenes.\n\n-- \nJānis Bērziņš\nSIA Klients\n+371 20000000\n"
    body = clean_body(text)
    assert "šļūtenes" in body
    assert "20000000" not in body


def test_overzealous_cut_falls_back_to_full_text() -> None:
    """Ja heiristika nogriež gandrīz visu, labāk pilns teksts ar citātu.

    Tukša ziņa modelim ir sliktāka par lieku citātu: uz tukšu ievadi tas
    izdomā pieprasījumu no temata.
    """
    text = "No: klients\nVajag EPDM profilu 12 mm, 358 gabalus, ar līmi.\n"
    body = clean_body(text)
    assert "EPDM profilu" in body


# --- filtri ----------------------------------------------------------------
@pytest.mark.parametrize(
    "headers",
    [
        {"List-Id": "<news.e-supplier.lv>"},
        {"Auto-Submitted": "auto-replied"},
        {"Precedence": "bulk"},
    ],
)
def test_bulk_mail_is_skipped(headers: dict[str, str]) -> None:
    from email import policy
    from email.parser import BytesParser

    raw = build(headers=headers)
    mime = BytesParser(policy=policy.default).parsebytes(raw)
    assert skip_reason(mime, parse_message(raw).body)


def test_noreply_sender_is_skipped() -> None:
    from email import policy
    from email.parser import BytesParser

    raw = build(sender="no-reply@piegadatajs.lv")
    mime = BytesParser(policy=policy.default).parsebytes(raw)
    assert "no-reply" in skip_reason(mime, parse_message(raw).body)


def test_our_own_message_is_skipped() -> None:
    from email import policy
    from email.parser import BytesParser

    raw = build(sender="ai.0001@trialine.lv")
    mime = BytesParser(policy=policy.default).parsebytes(raw)
    assert skip_reason(mime, parse_message(raw).body, own_address="ai.0001@trialine.lv")


def test_empty_body_is_skipped() -> None:
    from email import policy
    from email.parser import BytesParser

    raw = build(body="ok")
    mime = BytesParser(policy=policy.default).parsebytes(raw)
    assert skip_reason(mime, "ok") == "tukšs ķermenis"


def test_real_inquiry_is_not_skipped() -> None:
    from email import policy
    from email.parser import BytesParser

    raw = build()
    mime = BytesParser(policy=policy.default).parsebytes(raw)
    assert skip_reason(mime, parse_message(raw).body) == ""


# --- pielikumi -------------------------------------------------------------
def test_attachments_are_named_not_read() -> None:
    incoming = parse_message(build(attachments=[("rasejums.pdf", b"%PDF-1.4 fake")]))
    assert incoming.attachments == ["rasejums.pdf"]
    assert "%PDF" not in incoming.body


def test_prompt_warns_model_about_attachments() -> None:
    """Klusēt par pielikumu nedrīkst: piedāvājums uz pusi pieprasījuma
    izskatās pēc pilnas atbildes."""
    incoming = parse_message(build(attachments=[("specifikacija.xlsx", b"x")]))
    prompt = as_prompt(incoming)
    assert "specifikacija.xlsx" in prompt
    assert "SATURU TU NEREDZI" in prompt


def test_prompt_carries_subject_and_sender() -> None:
    prompt = as_prompt(parse_message(build(subject="Pieprasījums: EPDM 12mm")))
    assert "EPDM 12mm" in prompt
    assert "Jānis Bērziņš" in prompt


# --- adresāts --------------------------------------------------------------
def test_reply_to_wins_over_from() -> None:
    incoming = parse_message(build(headers={"Reply-To": "iepirkumi@klients.lv"}))
    assert incoming.recipient == "iepirkumi@klients.lv"


def test_without_reply_to_we_answer_the_sender() -> None:
    assert parse_message(build()).recipient == "janis@klients.lv"
