"""Attēlu atšifrēšana ar viltus modeli.

Bez tīkla. Šeit sargājam divas lietas: skenēts rasējums tiešām nonāk pie
modeļa, un neizdevies atšifrējums NEIZLIEKAS par izlasītu pielikumu.
"""

from __future__ import annotations

import pytest

from esupplier.mail import vision
from esupplier.mail.attachments import Attachment
from esupplier.mail.message import attachments_prompt
from helpers_files import make_empty_pdf, make_png

TRANSCRIPT = "EPDM profils D 12 mm\n358 gab."


class FakeClient:
    """Tik daudz no OpenAI klienta, cik `vision._ask` tiešām izmanto."""

    def __init__(self, text: str = TRANSCRIPT, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict] = []
        outer = self

        class _Responses:
            @staticmethod
            def create(**kwargs):
                outer.calls.append(kwargs)
                if outer.error:
                    raise outer.error
                return type("Response", (), {"output_text": outer.text})()

        self.responses = _Responses()

    @property
    def images(self) -> int:
        return sum(
            1
            for call in self.calls
            for part in call["input"][0]["content"]
            if part["type"] == "input_image"
        )


def scan(name: str = "skens.pdf") -> Attachment:
    return Attachment(
        name=name,
        note="PDF bez teksta (skenēts vai rasējums) — jāatver ar roku",
        data=make_empty_pdf(),
        image_mime="application/pdf",
    )


def photo(name: str = "foto.png") -> Attachment:
    return Attachment(
        name=name,
        note="attēls — teksta tajā nav, jāatver ar roku",
        data=make_png(),
        image_mime="image/png",
    )


@pytest.fixture(autouse=True)
def vision_on(monkeypatch):
    monkeypatch.setattr(vision, "MAIL_ATTACHMENT_VISION", True)
    # Dzinēju piesienam apzināti. Bez tā `_ask` aizietu pa `claude` ceļu, kur
    # viltus klientu neviens neskatās, un testi klusi sāktu iet tīklā.
    monkeypatch.setattr(vision, "ENGINE", "openai")


# --- attēla sagatavošana ---------------------------------------------------
def test_pdf_pages_are_rendered() -> None:
    """`pypdf` no skenēta faila neizvelk neko; lapa jāattēlo pašiem."""
    pages = vision.image_pages(scan())
    assert len(pages) == 1
    data, mime = pages[0]
    assert mime in ("image/png", "image/jpeg")
    assert len(data) > 100


def test_image_is_re_encoded() -> None:
    pages = vision.image_pages(photo())
    assert len(pages) == 1
    assert pages[0][1] == "image/png"


def test_broken_image_yields_nothing() -> None:
    item = Attachment(name="bojats.png", data=b"sis nav attels", image_mime="image/png")
    assert vision.image_pages(item) == []


def test_oversized_page_is_scaled_down(monkeypatch) -> None:
    """Rasējums 300 DPI izšķirtspējā maksā tokenus, ne salasāmību."""
    monkeypatch.setattr(vision, "_MAX_EDGE", 100)
    from PIL import Image

    data, _ = vision._encode(Image.new("RGB", (1000, 500), "white"))
    with Image.open(__import__("io").BytesIO(data)) as scaled:
        assert max(scaled.size) == 100


# --- atšifrēšana -----------------------------------------------------------
def test_scanned_drawing_becomes_text() -> None:
    items = [scan()]
    client = FakeClient()
    vision.transcribe(items, client)

    assert items[0].text == TRANSCRIPT
    assert items[0].read
    assert items[0].transcribed
    assert items[0].note == ""
    assert client.images == 1


def test_bytes_are_released_after_transcription() -> None:
    """Desmit megabaiti uz pielikumu citādi paliktu līdz gājiena beigām."""
    items = [scan()]
    vision.transcribe(items, FakeClient())
    assert items[0].data == b""
    assert not items[0].can_transcribe


def test_blank_image_stays_unread() -> None:
    """Tukšs atšifrējums, kas izliktos par izlasītu, būtu sliktāks par godīgu
    "atver pats": modelis klusētu par pusi pieprasījuma."""
    items = [photo()]
    vision.transcribe(items, FakeClient(text="NAV_TEKSTA"))

    assert not items[0].read
    assert not items[0].transcribed
    assert items[0].note


def test_failed_call_leaves_the_attachment_to_the_manager() -> None:
    items = [scan()]
    vision.transcribe(items, FakeClient(error=RuntimeError("tīkls nokrita")))

    assert not items[0].read
    assert "atšifrēt neizdevās" in items[0].note


def test_disabled_vision_calls_nobody(monkeypatch) -> None:
    monkeypatch.setattr(vision, "MAIL_ATTACHMENT_VISION", False)
    items = [scan()]
    client = FakeClient()
    vision.transcribe(items, client)

    assert client.calls == []
    assert not items[0].read


def test_already_read_attachment_is_left_alone() -> None:
    item = Attachment(name="spec.xlsx", text="Prece | Skaits")
    client = FakeClient()
    vision.transcribe([item], client)

    assert client.calls == []
    assert not item.transcribed


def test_transcripts_share_the_same_budget(monkeypatch) -> None:
    """Divi skenēti rasējumi citādi izspiestu no konteksta pašu vēstuli."""
    monkeypatch.setattr(vision, "MAIL_ATTACHMENT_VISION_CHARS", 40)
    from esupplier.mail import attachments as attachments_mod

    monkeypatch.setattr(attachments_mod, "MAIL_ATTACHMENTS_TEXT_LIMIT", 40)
    items = [scan("viens.pdf"), scan("divi.pdf")]
    vision.transcribe(items, FakeClient(text="x" * 40))

    assert items[0].read
    assert not items[1].read
    assert "budžetā" in items[1].note


# --- ko par to zina modelis ------------------------------------------------
def test_prompt_marks_a_transcript_as_a_transcript() -> None:
    """Atšifrējums nav oriģināls. Modelim tas jāzina: no rasējuma nolasīts
    "12 mm" var būt "1,2 mm", un tad jautājums klientam ir vērtīgāks par
    pārliecinātu piedāvājumu."""
    items = [scan(), Attachment(name="spec.xlsx", text="Prece | Skaits")]
    vision.transcribe(items, FakeClient())
    payload = attachments_prompt(items)

    assert "attēla atšifrējums" in payload
    # Excel aile ir tas, kas failā rakstīts — tai birkas nav.
    assert payload.count("atšifrējums") == 1


def test_only_a_few_images_per_letter_are_transcribed(monkeypatch) -> None:
    """ZIP ar divdesmit skenējumiem citādi kļūtu par divdesmit izsaukumiem."""
    monkeypatch.setattr(vision, "MAIL_ATTACHMENT_VISION_MAX_FILES", 2)
    items = [photo(f"foto{n}.png") for n in range(4)]
    client = FakeClient()
    vision.transcribe(items, client)

    assert sum(1 for item in items if item.transcribed) == 2
    assert len(client.calls) == 2
    assert "vairāk nekā 2 attēli" in items[2].note


def test_engine_decides_who_transcribes(monkeypatch) -> None:
    """Viens `transcribe`, divi dzinēji. Ja izvēle nokļūtu izsaukuma vietā,
    `mail/run.py` sāktu zināt, kurš modelis tur ir zem apakšas."""
    seen: list[str] = []
    monkeypatch.setattr(vision, "_ask_claude", lambda images: seen.append("claude") or "teksts")
    monkeypatch.setattr(
        vision, "_ask_openai", lambda client, images: seen.append("openai") or "teksts"
    )

    monkeypatch.setattr(vision, "ENGINE", "claude")
    vision.transcribe([scan()], client=None)
    monkeypatch.setattr(vision, "ENGINE", "openai")
    vision.transcribe([scan()], FakeClient())

    assert seen == ["claude", "openai"]


def test_claude_path_needs_no_client() -> None:
    """Abonementa ceļā klienta objekta nav vispār, un `None` nedrīkst nokrist
    kā kļūda pielikuma piezīmē."""
    item = scan()
    assert item.can_transcribe
