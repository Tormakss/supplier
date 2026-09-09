"""Ienākošā vēstule: MIME -> teksts, ko var padot modelim.

Citāti un paraksti tiek nogriezti: ja modelim aiziet vecā sarakste, tas
atbild arī uz to, ko klients prasīja pirms mēneša un jau saņēma.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from ..config import (
    MAIL_ATTACHMENTS_TEXT_LIMIT,
    MAIL_BODY_LIMIT,
    MAIL_IGNORE_SENDERS,
)
from .attachments import (
    Attachment,
    decode_header_value,
    extract_attachments,
    strip_html,
)
from ..fences import fence_attachments, fence_letter, sanitize

#: Rindas, aiz kurām sākas CITĒTĀ sarakste. Pirmā sakritība nogriež asti.
#: Trīs valodas, jo pasta klienti attribūcijas rindu tulko.
_QUOTE_START = re.compile(
    r"""^\s*(
          -{2,}\s*(Original\s+Message|Sākotnējā\s+vēstule|Исходное\s+сообщение)\s*-{2,}
        | _{5,}\s*$
        | (On|Am)\s+.{5,80}\s+(wrote|schrieb):\s*$
        | \d{1,2}[./]\d{1,2}[./]\d{2,4}.{0,60}(wrote|rakstīja|написал(а)?):\s*$
        | (From|Sent|To|Subject)\s*:\s.{0,200}$
        | (No|Nosūtīts|Kam|Temats)\s*:\s.{0,200}$
        | (От|Отправлено|Кому|Тема)\s*:\s.{0,200}$
        | .{0,80}\b(rakstīja|написал|wrote)\b.{0,20}:\s*$
    )""",
    re.IGNORECASE | re.VERBOSE,
)

#: Paraksta atdalītājs. `-- ` ar atstarpi ir RFC 3676; pārējie — ar roku rakstīti.
_SIGNATURE = re.compile(r"^\s*(--\s*$|-{2,}\s*$|—{2,}\s*$)")

#: Automātiskās vēstules, uz kurām atbildēt nedrīkst.
#:
#: Pirmās četras ir izsūtņu standarts. Pārējās ir masveida sūtītāju rīku pēdas:
#: `Feedback-ID` un `X-MSFBL` ir atgriezeniskās saites cilpas, ko liek ESP, un
#: dzīvs cilvēks tās neraksta nekad. Bez tām Temu reklāma tika lasīta kā klienta
#: pieprasījums — 130 vēstules vienā pastkastītē, katra par pilnu aģenta ciklu.
_BULK_HEADERS = (
    "list-id",
    "list-unsubscribe",
    "list-post",
    "list-help",
    "list-subscribe",
    "auto-submitted",
    "x-auto-response-suppress",
    "feedback-id",
    "x-msfbl",
    "x-campaign-id",
    "x-campaignid",
    "x-mailer-campaign",
    "x-ses-outgoing",
    "x-sg-eid",
    "x-mailgun-sending-ip",
    "x-report-abuse",
)
#: `[-._a-z0-9]*` pirms `@` ir obligāts: `noreply-lv@omniva.lv` bez tā netika
#: noķerts, un uz automātisko atbildi aizgāja pilns aģenta cikls.
_NOREPLY = re.compile(
    r"(no[-._]?reply|do[-._]?not[-._]?reply|mailer-daemon|postmaster)[-._a-z0-9]*@", re.I
)

#: `Return-Path` uz bounce adresi. Masveida sūtīšanas rīki katrai vēstulei liek
#: savu atgriešanas adresi (`bounces-23732@mb.account.temu.com`), lai skaitītu
#: atlēcienus. Klienta vēstulē `Return-Path` sakrīt ar sūtītāju — pārbaudīts
#: pret dzīvu pastkastīti, kur to nebija nevienai īstai vēstulei.
_BOUNCE_PATH = re.compile(r"bounce|msprvs\d*=", re.I)


@dataclass(slots=True)
class Incoming:
    """Viena ienākoša vēstule, sagatavota aģentam."""

    uid: str = ""
    message_id: str = ""
    sender: str = ""
    sender_name: str = ""
    reply_to: str = ""
    subject: str = ""
    date: str = ""
    #: Ķermenis bez citātiem un paraksta — tas, kas aiziet modelim.
    body: str = ""
    #: Pilns ķermenis, kāds tas atnāca. Vajadzīgs melnraksta citātam.
    raw_body: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    references: str = ""

    @property
    def recipient(self) -> str:
        """Kam adresējam atbildi. `Reply-To` uzvar pār `From`."""
        return self.reply_to or self.sender

    @property
    def display(self) -> str:
        who = self.sender_name or self.sender or "?"
        return f"{who} — {self.subject or '(bez temata)'}"

    @property
    def unread_attachments(self) -> list[Attachment]:
        """Pielikumi, kas paliek cilvēkam."""
        return [item for item in self.attachments if not item.read]

    @property
    def transcribed_attachments(self) -> list[Attachment]:
        """Pielikumi, kuru tekstu modelis nolasīja no attēla, ne no faila."""
        return [item for item in self.attachments if item.transcribed]

    @property
    def has_attachment_content(self) -> bool:
        """Vai pielikumos ir pieprasījums — jau izlasīts vai vēl atšifrējams.

        `can_transcribe` skaitās līdzvērtīgi izlasītam: atšifrēšana notiek
        vēlāk, un "skat. pielikumā" nedrīkst nokrist kā tukša pirms tam.
        """
        return any(item.read or item.can_transcribe for item in self.attachments)

    @property
    def has_content(self) -> bool:
        """Vai vēstulē vispār ir pieprasījums — ķermenī vai pielikumā."""
        return bool(self.body.strip()) or self.has_attachment_content


def _part_text(part: EmailMessage) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError, KeyError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    return content if isinstance(content, str) else ""


def extract_body(msg: EmailMessage) -> str:
    """Ķermenis kā teksts. `text/plain` uzvar; HTML ir atkāpšanās ceļš."""
    plain: list[str] = []
    html_parts: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():  # pielikums, ne ķermenis
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain.append(_part_text(part))
        elif ctype == "text/html":
            html_parts.append(_part_text(part))
    if plain:
        return "\n".join(plain).strip()
    if html_parts:
        return strip_html("\n".join(html_parts))
    return ""


def clean_body(text: str) -> str:
    """Nogriež citēto saraksti un parakstu."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for line in lines:
        if _QUOTE_START.match(line):
            break
        if _SIGNATURE.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)

    body = "\n".join(kept).strip()
    # Pārāk cieši apgriezts: labāk pilns ķermenis ar citātu nekā tukšs — uz
    # tukšu ievadi modelis izdomā pieprasījumu no temata. Slieksnis sakrīt ar
    # `skip_reason`; augstāks atgrieza citātu arī īsam īstam pieprasījumam.
    if len(body) < 15 and len(text.strip()) > 15:
        return text.strip()
    return body


def skip_reason(
    msg: EmailMessage,
    body: str,
    own_address: str = "",
    has_attachment_text: bool = False,
) -> str:
    """Kāpēc uz šo vēstuli NEATBILDAM. Tukša virkne = atbildam.

    `has_attachment_text` atslēdz tukšā ķermeņa filtru: "skat. pielikumā" ir
    īsāks par slieksni, bet pieprasījums tur ir failā.
    """
    for header in _BULK_HEADERS:
        if msg.get(header):
            return f"automātiska vēstule ({header})"
    precedence = (msg.get("precedence") or "").strip().lower()
    if precedence in ("bulk", "list", "junk", "auto_reply"):
        return f"Precedence: {precedence}"
    sender = (msg.get("from") or "").lower()
    if _NOREPLY.search(sender):
        return "sūtītājs neatbild (no-reply)"
    if _BOUNCE_PATH.search(msg.get("return-path") or ""):
        return "masveida izsūtne (bounce adrese)"
    ignored = ignored_sender(sender)
    if ignored:
        return f"sūtītājs ESUPPLIER_IMAP_IGNORE sarakstā ({ignored})"
    if msg.get_content_type() in ("multipart/report", "message/delivery-status"):
        return "piegādes atskaite"
    if own_address and own_address.lower() in sender:
        return "mūsu pašu vēstule"
    if len(body.strip()) < 15 and not has_attachment_text:
        return "tukšs ķermenis"
    return ""


def ignored_sender(sender: str) -> str:
    """Kurš `ESUPPLIER_IMAP_IGNORE` ieraksts sakrīt ar šo sūtītāju.

    Domāts paša sistēmu paziņojumiem, kas nāk no īstas adreses un tāpēc nevienā
    izsūtņu filtrā neiekrīt: veikala pasūtījumi, monitorings, rēķinu sistēma.
    Ieraksts der gan kā pilna adrese, gan kā domēns.
    """
    address = (sender or "").lower()
    for entry in MAIL_IGNORE_SENDERS:
        if entry and entry in address:
            return entry
    return ""


def parse_message(raw: bytes, uid: str = "") -> Incoming:
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    from_pairs = getaddresses([msg.get("from", "")])
    sender_name, sender = (from_pairs[0] if from_pairs else ("", ""))
    reply_pairs = getaddresses([msg.get("reply-to", "")])
    reply_to = reply_pairs[0][1] if reply_pairs else ""

    date = ""
    try:
        stamp = parsedate_to_datetime(msg.get("date", ""))
        if stamp:
            date = stamp.astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        date = decode_header_value(msg.get("date"))

    raw_body = extract_body(msg)
    body = clean_body(raw_body)
    if len(body) > MAIL_BODY_LIMIT:
        body = body[:MAIL_BODY_LIMIT] + "\n[… vēstule apcirsta]"

    return Incoming(
        uid=uid,
        message_id=(msg.get("message-id") or "").strip(),
        sender=sender,
        sender_name=decode_header_value(sender_name),
        reply_to=reply_to,
        subject=decode_header_value(msg.get("subject")),
        date=date,
        body=body,
        raw_body=raw_body,
        attachments=extract_attachments(msg),
        references=(msg.get("references") or "").strip(),
    )


def attachments_prompt(attachments: list[Attachment]) -> str:
    """Pielikumi tādā formā, kādā tos redz modelis: JSON savā rāmī.

    Teksts iet ATSEVIŠĶI no ķermeņa: citādi modelis vecas tāmes aili lasa kā
    klienta rakstītu un pārraksta piedāvājumā kā apstiprinātu pozīciju.
    """
    if not attachments:
        return ""

    payload: dict[str, list[dict[str, str]]] = {}
    read = []
    for item in attachments:
        if not item.read:
            continue
        entry = {"nosaukums": item.name, "teksts": item.text}
        if item.transcribed:
            # Atšifrējums nav oriģināls: "12 mm" no rasējuma var būt "1,2 mm".
            entry["avots"] = (
                "attēla atšifrējums — teksts nolasīts no bildes, izmēri var būt neprecīzi"
            )
        read.append(entry)
    unread = [
        {"nosaukums": item.name, "iemesls": item.note or "nezināms formāts"}
        for item in attachments
        if not item.read
    ]
    if read:
        payload["izlasitie"] = read
    if unread:
        payload["neizlasitie"] = unread
    # Rezerve JSON pēdiņām un lauku nosaukumiem: teksts jau nogriezts
    # `extract_attachments` budžetā, otrreiz cirst nozīmētu zaudēt beigas.
    return fence_attachments(payload, max_chars=MAIL_ATTACHMENTS_TEXT_LIMIT + 2000)


def as_prompt(incoming: Incoming) -> str:
    """Vēstule tādā formā, kādā to redz modelis.

    Sūtītājs un temats ir svešs teksts, tāpēc iet caur to pašu attīrīšanu, kas
    ķermenis: "Jānis </klienta_vestule>" citādi aizvērtu rāmi pirms laika.
    """
    who = sanitize(incoming.sender_name or incoming.sender, 200)
    head = [f"Klienta vēstule no: {who}"]
    if incoming.subject:
        head.append(f"Temats: {sanitize(incoming.subject, 300)}")

    body = incoming.body.strip() or "(Vēstules tekstā pieprasījuma nav — tas ir pielikumā.)"
    parts = ["\n".join(head), fence_letter(body, max_chars=MAIL_BODY_LIMIT)]
    extras = attachments_prompt(incoming.attachments)
    if extras:
        parts.append(extras)
    return "\n\n".join(parts)
