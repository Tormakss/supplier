"""Melnraksta salikšana.

Melnraksts ir domāts nosūtīšanai bez labošanas. Tāpēc tajā ir TIKAI vēstule
klientam: katrs bloks, kas pirms sūtīšanas jāizdzēš ar roku, agri vai vēlu
paliek neizdzēsts. Iekšējās piezīmes dzīvo konsolē un `-IEKSEJI.txt` failā.
"""

from __future__ import annotations

from esupplier import report
from esupplier.mail.draft import build_draft, reply_subject
from esupplier.mail.message import Incoming

ANSWER = """\
Labdien! Paldies par pieprasījumu.

| Prece | Cena |
|---|---|
| EPDM profils 12 mm | 4.10 € bez PVN (4.96 € ar PVN) / m |

Ar cieņu

---

⚑ IEKŠĒJI (klientam nesūtīt)

JĀIZDARA
- Rezervēt 25 m, apstiprināt piegādes termiņu.

NEAPSTIPRINĀTS
- Atlikums noliktavā nav pārbaudīts.
"""

CLIENT = Incoming(
    uid="42",
    message_id="<abc123@klients.lv>",
    sender="janis@klients.lv",
    sender_name="Jānis Bērziņš",
    subject="Pieprasījums: EPDM profils",
    body="Vajag EPDM profilu 12 mm.",
    references="<vecais@klients.lv>",
)


def parts(msg) -> tuple[str, str]:
    plain = msg.get_body(preferencelist=("plain",)).get_content()
    html = msg.get_body(preferencelist=("html",)).get_content()
    return plain, html


# --- temats un ķēde --------------------------------------------------------
def test_subject_gets_re() -> None:
    assert reply_subject("Pieprasījums") == "Re: Pieprasījums"


def test_re_is_not_doubled() -> None:
    assert reply_subject("Re: Pieprasījums") == "Re: Pieprasījums"
    assert reply_subject("RE: Pieprasījums") == "Re: Pieprasījums"
    assert reply_subject("Ответ: Запрос") == "Re: Запрос"


def test_missing_subject_gets_a_name() -> None:
    assert reply_subject("") == "Re: Jūsu pieprasījums"


def test_threading_headers_point_at_the_original() -> None:
    letter, _internal = report.split_answer(ANSWER)
    msg = build_draft(CLIENT, letter, sender="ai.0001@trialine.lv")
    assert msg["In-Reply-To"] == "<abc123@klients.lv>"
    assert "<vecais@klients.lv>" in msg["References"]
    assert "<abc123@klients.lv>" in msg["References"]
    assert msg["To"] == "janis@klients.lv"
    assert "ai.0001@trialine.lv" in msg["From"]


def test_draft_has_its_own_message_id() -> None:
    letter, _internal = report.split_answer(ANSWER)
    msg = build_draft(CLIENT, letter, sender="ai.0001@trialine.lv")
    assert msg["Message-ID"]
    assert msg["Message-ID"] != CLIENT.message_id


# --- iekšējais bloks -------------------------------------------------------
def test_internal_notes_never_reach_the_draft() -> None:
    """Melnraksts ir nosūtāms bez labošanas. Viss, kas pirms tam jāizdzēš,
    kādreiz paliks neizdzēsts."""
    letter, _internal = report.split_answer(ANSWER)
    plain, html = parts(build_draft(CLIENT, letter))
    assert "Rezervēt 25 m" not in plain
    assert "Rezervēt 25 m" not in html
    assert "IEKŠĒJI" not in plain
    assert "IEKŠĒJI" not in html


def test_draft_starts_with_the_letter() -> None:
    letter, _internal = report.split_answer(ANSWER)
    plain, _ = parts(build_draft(CLIENT, letter))
    assert plain.lstrip().startswith("Labdien!")


def test_answers_file_never_contains_internal_notes(tmp_path) -> None:
    """Failu mapē `atbildes/` menedžeris atver un kopē visu. Iekšējām
    piezīmēm tur nav ko darīt."""
    path, _dropped = report.save_answer(ANSWER, path=tmp_path / "x.html")
    content = path.read_text(encoding="utf-8")
    assert "Rezervēt 25 m" not in content
    assert "EPDM profils 12 mm" in content


def test_internal_notes_go_to_a_txt_file_beside_the_letter(tmp_path) -> None:
    """`.txt`, ne `.html`: fails, ko var atvērt un ielīmēt, agri vai vēlu tiek
    ielīmēts."""
    _letter, internal = report.split_answer(ANSWER)
    path, _dropped = report.save_answer(ANSWER, path=tmp_path / "x.html")
    notes = report.save_internal(internal, answer_path=path)

    assert notes.name == "x-IEKSEJI.txt"
    assert notes.parent == path.parent
    assert "Rezervēt 25 m" in notes.read_text(encoding="utf-8")


# --- saturs ----------------------------------------------------------------
def test_html_part_carries_the_table() -> None:
    letter, _internal = report.split_answer(ANSWER)
    _plain, html = parts(build_draft(CLIENT, letter))
    assert "<table" in html
    assert "border-collapse" in html  # stili ir inline, citādi Gmail tos nomet


def test_utf8_survives_the_round_trip() -> None:
    """Melnraksts uz serveri aiziet kā baiti. Ja kodējums salūzt tur, latviešu
    un krievu teksts pastkastītē kļūst par jautājumzīmēm."""
    from email import policy
    from email.parser import BytesParser

    letter = "Labdien! Šļūtene ar blīvgumiju — 25 m. Здравствуйте!"
    raw = build_draft(CLIENT, letter).as_bytes()
    again = BytesParser(policy=policy.default).parsebytes(raw)
    plain, _html = parts(again)
    assert "Šļūtene" in plain
    assert "Здравствуйте" in plain
