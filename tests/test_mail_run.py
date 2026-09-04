"""Pastkastītes gājiens ar viltus IMAP un viltus modeli.

Bez tīkla un bez API izsaukumiem. Šeit pārbaudām lēmumus, kas notiek BEZ
cilvēka: uz ko atbildam, uz ko ne, un kad melnraksts netiek taisīts vispār.
"""

from __future__ import annotations

import pathlib
from dataclasses import replace
from email.message import EmailMessage

import pytest

from esupplier import report
from esupplier.agent.loop import AgentResult
from esupplier.catalog import db
from esupplier.mail import run as mail_run
from esupplier.mail.message import Incoming, parse_message

ANSWER = """\
Labdien! Paldies par pieprasījumu.

EPDM profils 12 mm — 4.10 € bez PVN (4.96 € ar PVN) / m.

Ar cieņu

---

⚑ IEKŠĒJI (klientam nesūtīt)

JĀIZDARA
- Apstiprināt piegādes termiņu.
"""


class FakeBox:
    """Tik daudz no `Mailbox`, cik `run_once` tiešām izmanto."""

    def __init__(self, letters: dict[str, bytes]) -> None:
        self.letters = letters
        self.folder = "INBOX"
        self.appended: list[EmailMessage] = []
        self.marked: list[str] = []
        self.closed = False

    def connect(self) -> None: ...
    def select(self, folder: str = "", readonly: bool = False) -> int:
        return len(self.letters)

    def drafts_folder(self) -> str:
        return "INBOX.Drafts"

    def search_new(self, keyword: str = "", limit: int = 0) -> list[str]:
        return list(self.letters)[: limit or None]

    def fetch(self, uid: str) -> bytes:
        return self.letters[uid]

    def append_draft(self, msg: EmailMessage, folder: str = "") -> str:
        self.appended.append(msg)
        return folder or "INBOX.Drafts"

    def mark(self, uid: str, keyword: str = "") -> bool:
        self.marked.append(uid)
        return True

    def close(self) -> None:
        self.closed = True


def letter_bytes(
    *,
    body: str = "Labdien! Vajag EPDM profilu 12 mm, 25 metrus. Cena?",
    sender: str = "Jānis <janis@klients.lv>",
    message_id: str = "<one@klients.lv>",
    headers: dict[str, str] | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "ai.0001@trialine.lv"
    msg["Subject"] = "Pieprasījums"
    msg["Message-ID"] = message_id
    for key, value in (headers or {}).items():
        msg[key] = value
    msg.set_content(body)
    return msg.as_bytes()


@pytest.fixture()
def conn(tmp_path):
    with db.session(tmp_path / "test.db") as connection:
        yield connection


@pytest.fixture(autouse=True)
def answers_dir(tmp_path, monkeypatch):
    """`atbildes/` uz laiku pārceļam, lai testi neaugļo īsto mapi."""
    monkeypatch.setattr(report, "ANSWERS_DIR", tmp_path / "atbildes")
    return tmp_path / "atbildes"


def fake_turn(text: str = ANSWER, **kwargs):
    def _turn(messages, conn=None, client=None, **_):
        return AgentResult(text=text, **kwargs)

    return _turn


CLIENT = Incoming(
    uid="1",
    message_id="<one@klients.lv>",
    sender="janis@klients.lv",
    sender_name="Jānis",
    subject="Pieprasījums",
    body="Vajag EPDM profilu 12 mm.",
)


# --- process_one -----------------------------------------------------------
def test_normal_inquiry_becomes_a_draft(conn, monkeypatch) -> None:
    monkeypatch.setattr(mail_run, "run_turn", fake_turn())
    box = FakeBox({})
    outcome = mail_run.process_one(CLIENT, conn, client=None, box=box)

    assert outcome.status == "drafted"
    assert len(box.appended) == 1
    plain = box.appended[0].get_body(preferencelist=("plain",)).get_content()
    assert "Labdien!" in plain


def test_draft_carries_only_the_letter(conn, monkeypatch) -> None:
    """Melnraksts ir nosūtāms bez labošanas. Uzdevumi menedžerim tajā nav."""
    monkeypatch.setattr(mail_run, "run_turn", fake_turn())
    box = FakeBox({})
    mail_run.process_one(CLIENT, conn, client=None, box=box)

    plain = box.appended[0].get_body(preferencelist=("plain",)).get_content()
    html = box.appended[0].get_body(preferencelist=("html",)).get_content()
    assert "Apstiprināt piegādes termiņu" not in plain
    assert "Apstiprināt piegādes termiņu" not in html
    assert "IEKŠĒJI" not in plain


def test_internal_notes_land_in_console_and_file(conn, monkeypatch, answers_dir) -> None:
    """Melnrakstā tos vairs nav, tāpēc vienīgā vieta ir `Outcome` un blakus
    fails. Uzdevums, ko neviens neredz, ir tas pats, kas neuzrakstīts."""
    monkeypatch.setattr(mail_run, "run_turn", fake_turn())
    outcome = mail_run.process_one(CLIENT, conn, client=None, box=FakeBox({}))

    assert "Apstiprināt piegādes termiņu" in outcome.internal
    assert outcome.internal_path.endswith("-IEKSEJI.txt")
    notes = pathlib.Path(outcome.internal_path).read_text(encoding="utf-8")
    assert "Apstiprināt piegādes termiņu" in notes


def test_truncated_answer_never_becomes_a_draft(conn, monkeypatch) -> None:
    """Iekšējais bloks ir pēdējais, ko modelis raksta. Apcirsta atbilde tāpēc
    izskatās pēc pilnas vēstules, kurai nav ko piebilst — un tieši tā ir
    bīstamākā melnraksta forma."""
    monkeypatch.setattr(mail_run, "run_turn", fake_turn(truncated=True))
    box = FakeBox({})
    outcome = mail_run.process_one(CLIENT, conn, client=None, box=box)

    assert outcome.status == "failed"
    assert "apcirst" in outcome.reason
    assert box.appended == []


def test_agent_crash_does_not_stop_the_run(conn, monkeypatch) -> None:
    def boom(messages, conn=None, client=None, **_):
        raise RuntimeError("tīkls nokrita")

    monkeypatch.setattr(mail_run, "run_turn", boom)
    outcome = mail_run.process_one(CLIENT, conn, client=None, box=FakeBox({}))
    assert outcome.status == "failed"
    assert "tīkls nokrita" in outcome.reason


def test_answer_without_letter_part_fails(conn, monkeypatch) -> None:
    monkeypatch.setattr(mail_run, "run_turn", fake_turn("⚑ IEKŠĒJI\nTikai piezīmes."))
    outcome = mail_run.process_one(CLIENT, conn, client=None, box=FakeBox({}))
    assert outcome.status == "failed"


def test_missing_internal_block_becomes_a_visible_warning(conn, monkeypatch) -> None:
    """Bloka trūkums menedžerim izskatās pēc "nekas nav jādara", tāpēc par to
    jāpasaka atsevišķi — konsolē un blakus failā."""
    monkeypatch.setattr(mail_run, "run_turn", fake_turn("Labdien! Piedāvājums seko."))
    box = FakeBox({})
    outcome = mail_run.process_one(CLIENT, conn, client=None, box=box)

    assert outcome.status == "drafted"
    assert any("NEUZRAKSTĪJA" in w for w in outcome.warnings)
    assert "NEUZRAKSTĪJA" in outcome.internal


def test_attachments_are_flagged_to_the_manager(conn, monkeypatch) -> None:
    monkeypatch.setattr(mail_run, "run_turn", fake_turn())
    incoming = replace(CLIENT, attachments=["rasejums.pdf"])
    box = FakeBox({})
    outcome = mail_run.process_one(incoming, conn, client=None, box=box)
    assert any("rasejums.pdf" in w for w in outcome.warnings)


def test_tool_limit_is_flagged(conn, monkeypatch) -> None:
    monkeypatch.setattr(mail_run, "run_turn", fake_turn(hit_iteration_limit=True))
    outcome = mail_run.process_one(CLIENT, conn, client=None, box=FakeBox({}))
    assert any("limits" in w for w in outcome.warnings)


def test_dry_run_leaves_the_mailbox_untouched(conn, monkeypatch) -> None:
    monkeypatch.setattr(mail_run, "run_turn", fake_turn())
    box = FakeBox({})
    outcome = mail_run.process_one(CLIENT, conn, client=None, box=box, dry_run=True)
    assert outcome.status == "drafted"
    assert box.appended == []


def test_html_copy_is_written_next_to_the_draft(conn, monkeypatch, answers_dir) -> None:
    monkeypatch.setattr(mail_run, "run_turn", fake_turn())
    outcome = mail_run.process_one(CLIENT, conn, client=None, box=FakeBox({}))
    assert outcome.answer_path
    content = next(answers_dir.glob("*.html")).read_text(encoding="utf-8")
    # HTML fails ir vēstule klientam. Piezīmes ir blakus, `.txt` failā.
    assert "Apstiprināt piegādes termiņu" not in content
    assert "Labdien!" in content


# --- run_once --------------------------------------------------------------
def test_run_once_drafts_and_records(conn, monkeypatch) -> None:
    from rich.console import Console

    box = FakeBox({"1": letter_bytes()})
    monkeypatch.setattr(mail_run, "Mailbox", lambda **kw: box)
    monkeypatch.setattr(mail_run, "run_turn", fake_turn())

    outcomes = mail_run.run_once(Console(quiet=True), conn, client=None)

    assert [o.status for o in outcomes] == ["drafted"]
    assert len(box.appended) == 1
    assert box.marked == ["1"]
    assert db.is_processed(conn, "<one@klients.lv>")


def test_the_same_letter_is_not_answered_twice(conn, monkeypatch) -> None:
    """Bez šī katrs nākamais gājiens uzrakstītu vēl vienu melnrakstu uz to pašu
    vēstuli, un menedžeris tos šķirotu ar roku."""
    from rich.console import Console

    box = FakeBox({"1": letter_bytes()})
    monkeypatch.setattr(mail_run, "Mailbox", lambda **kw: box)
    monkeypatch.setattr(mail_run, "run_turn", fake_turn())

    mail_run.run_once(Console(quiet=True), conn, client=None)
    second = mail_run.run_once(Console(quiet=True), conn, client=None)

    assert second == []
    assert len(box.appended) == 1


def test_newsletter_is_skipped_before_the_model(conn, monkeypatch) -> None:
    from rich.console import Console

    called = []

    def spy(messages, conn=None, client=None, **_):
        called.append(messages)
        return AgentResult(text=ANSWER)

    box = FakeBox({"1": letter_bytes(headers={"List-Id": "<news.piegadatajs.lv>"})})
    monkeypatch.setattr(mail_run, "Mailbox", lambda **kw: box)
    monkeypatch.setattr(mail_run, "run_turn", spy)

    outcomes = mail_run.run_once(Console(quiet=True), conn, client=None)

    assert [o.status for o in outcomes] == ["skipped"]
    assert called == []          # modelis netika izsaukts vispār
    assert box.appended == []


def test_failed_letter_keeps_its_keyword_off(conn, monkeypatch) -> None:
    """Kritušu vēstuli neiezīmējam: cilvēks to pastkastītē atradīs kā
    neapstrādātu, un `--retry-failed` to atgriež ciklā."""
    from rich.console import Console

    box = FakeBox({"1": letter_bytes()})
    monkeypatch.setattr(mail_run, "Mailbox", lambda **kw: box)
    monkeypatch.setattr(mail_run, "run_turn", fake_turn(truncated=True))

    mail_run.run_once(Console(quiet=True), conn, client=None)

    assert box.marked == []
    assert db.is_processed(conn, "<one@klients.lv>")
    assert db.forget_failed(conn) == 1
    assert not db.is_processed(conn, "<one@klients.lv>")


def test_each_letter_gets_its_own_history(conn, monkeypatch) -> None:
    """Kopīga vēsture nozīmētu, ka otrā klienta piedāvājumā parādās pirmā
    klienta preces un cenas."""
    from rich.console import Console

    seen: list[int] = []

    def spy(messages, conn=None, client=None, **_):
        seen.append(len(messages))
        return AgentResult(text=ANSWER)

    box = FakeBox({
        "1": letter_bytes(message_id="<one@klients.lv>"),
        "2": letter_bytes(message_id="<two@klients.lv>", sender="Anna <anna@cits.lv>"),
    })
    monkeypatch.setattr(mail_run, "Mailbox", lambda **kw: box)
    monkeypatch.setattr(mail_run, "run_turn", spy)

    mail_run.run_once(Console(quiet=True), conn, client=None)

    assert seen == [1, 1]


def test_mailbox_is_closed_even_when_a_letter_explodes(conn, monkeypatch) -> None:
    from rich.console import Console

    box = FakeBox({"1": letter_bytes()})
    monkeypatch.setattr(mail_run, "Mailbox", lambda **kw: box)
    monkeypatch.setattr(mail_run, "run_turn", fake_turn())

    mail_run.run_once(Console(quiet=True), conn, client=None)
    assert box.closed


def test_idle_check_stays_silent(conn, monkeypatch) -> None:
    """Sekošanas režīmā tukšs gājiens notiek katru minūti. Ja katrs no tiem
    atstātu rindu, īstie melnraksti ekrānā tajos pazustu."""
    from rich.console import Console

    box = FakeBox({})
    monkeypatch.setattr(mail_run, "Mailbox", lambda **kw: box)
    console = Console(record=True, width=100)

    outcomes = mail_run.run_once(console, conn, client=None, announce=False)

    assert outcomes == []
    assert console.export_text().strip() == ""


def test_first_check_still_announces(conn, monkeypatch) -> None:
    """Pirmajā gājienā rinda ir vajadzīga: tā ir vienīgā apstiprinājums, ka
    savienojums ir un melnraksti ies pareizajā mapē."""
    from rich.console import Console

    box = FakeBox({})
    monkeypatch.setattr(mail_run, "Mailbox", lambda **kw: box)
    console = Console(record=True, width=100)

    mail_run.run_once(console, conn, client=None, announce=True)

    # `export_text` pēc noklusējuma buferi iztukšo — nolasām vienu reizi.
    printed = console.export_text()
    assert "INBOX" in printed
    assert "Drafts" in printed

