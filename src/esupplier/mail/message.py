"""Ienākošā vēstule: MIME -> teksts, ko var padot modelim.

E-pasts nav tas pats, kas konsolē ielīmēta vēstule. Tajā ir citēta sarakste,
paraksti, atrunas un pielikumi, un sistēmas prompts prasa atbildēt uz KATRU
pieminēto pozīciju. Ja modelim aiziet arī vecā sarakste, tas godprātīgi
atbild arī uz to, ko klients prasīja pirms mēneša un jau saņēma.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from ..config import MAIL_BODY_LIMIT

#: Rindas, aiz kurām sākas CITĒTĀ sarakste. Pirmā sakritība nogriež asti.
#: Valodas ir trīs, jo tādā valodā raksta klienti, un pasta klienti attribūciju
#: tulko (Outlook LV "No:", Gmail RU "Кому:", Thunderbird EN "On ... wrote:").
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

#: Paraksta atdalītājs. `-- ` ar atstarpi ir standarts (RFC 3676), pārējie —
#: tas, ko cilvēki raksta ar roku.
_SIGNATURE = re.compile(r"^\s*(--\s*$|-{2,}\s*$|—{2,}\s*$)")

#: Automātiskās vēstules, uz kurām atbildēt nedrīkst: atvaļinājuma
#: paziņojumi, jaunumu izsūtnes, piegādes kļūdas. Katra no tām maksā vienu
#: pilnu aģenta ciklu un beidzas ar melnrakstu, kas nevienam nav vajadzīgs.
_BULK_HEADERS = ("list-id", "list-unsubscribe", "auto-submitted", "x-auto-response-suppress")
_NOREPLY = re.compile(r"(no[-._]?reply|do[-._]?not[-._]?reply|mailer-daemon|postmaster)@", re.I)


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
    attachments: list[str] = field(default_factory=list)
    references: str = ""

    @property
    def recipient(self) -> str:
        """Kam adresējam atbildi. `Reply-To` uzvar pār `From` — tā ir tā adrese,
        ko klients pats norādīja atbildēm."""
        return self.reply_to or self.sender

    @property
    def display(self) -> str:
        who = self.sender_name or self.sender or "?"
        return f"{who} — {self.subject or '(bez temata)'}"


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return value.strip()


def _strip_html(html_text: str) -> str:
    """Ļoti vienkāršs HTML -> teksts. Pietiek: tas ir tikai atkāpšanās ceļš,
    kad vēstulē nav `text/plain` daļas."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


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
        return _strip_html("\n".join(html_parts))
    return ""


def clean_body(text: str) -> str:
    """Nogriež citēto saraksti un parakstu.

    Citāts modelim ir bīstamāks nekā tā trūkums: promptā ir "NEKAD neizlaid
    pozīciju klusējot", tāpēc pārsūtītā sarakstē pieminētā vecā prece nonāk
    jaunajā piedāvājumā kā aktuāla pozīcija.
    """
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
    # Ja apgriešana atstāja TUKŠUMU, bet oriģinālā bija teksts, heiristika
    # nostrādāja pārāk cieši — labāk pilns ķermenis ar citātu nekā tukša ziņa
    # modelim: uz tukšu ievadi tas izdomā pieprasījumu no temata.
    #
    # Slieksnis ir tas pats, ko `skip_reason` sauc par tukšu ķermeni. Augstāks
    # slieksnis atgrieza citātu arī tad, kad īstais pieprasījums bija viena
    # īsa rinda ("Vajag silikona šļūteni DN25.") — un tieši tā klienti raksta.
    if len(body) < 15 and len(text.strip()) > 15:
        return text.strip()
    return body


def skip_reason(msg: EmailMessage, body: str, own_address: str = "") -> str:
    """Kāpēc uz šo vēstuli NEATBILDAM. Tukša virkne = atbildam.

    Katra apstrādātā vēstule maksā vienu pilnu aģenta ciklu, tāpēc jaunumu
    izsūtnes un atvaļinājuma auto-atbildes filtrējam pirms modeļa, ne pēc.
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
    if msg.get_content_type() in ("multipart/report", "message/delivery-status"):
        return "piegādes atskaite"
    if own_address and own_address.lower() in sender:
        return "mūsu pašu vēstule"
    if len(body.strip()) < 15:
        return "tukšs ķermenis"
    return ""


def attachment_names(msg: EmailMessage) -> list[str]:
    """Pielikumu nosaukumi.

    Saturu nelasām — rasējumu un specifikāciju aģents nesaprot. Bet KLUSĒT
    par tiem nedrīkst: piedāvājums, kas uzbūvēts uz pusi no pieprasījuma,
    izskatās pēc pilnas atbildes.
    """
    names = []
    for part in msg.walk():
        name = part.get_filename()
        if name:
            names.append(_decode(name))
    return names


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
        date = _decode(msg.get("date"))

    raw_body = extract_body(msg)
    body = clean_body(raw_body)
    if len(body) > MAIL_BODY_LIMIT:
        body = body[:MAIL_BODY_LIMIT] + "\n[… vēstule apcirsta]"

    return Incoming(
        uid=uid,
        message_id=(msg.get("message-id") or "").strip(),
        sender=sender,
        sender_name=_decode(sender_name),
        reply_to=reply_to,
        subject=_decode(msg.get("subject")),
        date=date,
        body=body,
        raw_body=raw_body,
        attachments=attachment_names(msg),
        references=(msg.get("references") or "").strip(),
    )


def as_prompt(incoming: Incoming) -> str:
    """Vēstule tādā formā, kādā to redz modelis.

    Sūtītāju un tematu pievienojam apzināti: temats bieži satur preces
    nosaukumu ("Pieprasījums: EPDM profils 12mm"), un vārds ir vajadzīgs
    uzrunai vēstules sākumā.
    """
    head = [f"Klienta vēstule no: {incoming.sender_name or incoming.sender}"]
    if incoming.subject:
        head.append(f"Temats: {incoming.subject}")
    if incoming.attachments:
        head.append(
            "Pielikumi (SATURU TU NEREDZI — uzraksti iekšējā blokā, ka cilvēkam "
            "tie jāatver): " + ", ".join(incoming.attachments)
        )
    return "\n".join(head) + "\n\n" + incoming.body
