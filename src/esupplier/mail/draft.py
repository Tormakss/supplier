"""Melnraksta salikšana: aģenta atbilde -> MIME vēstule.

Melnrakstā ir TIKAI vēstule klientam. Iekšējās piezīmes tajā nenonāk nemaz:
melnraksts ir domāts nosūtīšanai bez labošanas, un katrs bloks, kas pirms tam
jāizdzēš ar roku, agri vai vēlu paliek neizdzēsts.

Piezīmes aiziet konsolē un blakus failā `atbildes/*-IEKSEJI.txt`.
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from datetime import datetime

from .. import report
from ..config import IMAP_USER
from .message import Incoming

#: Uzruna melnraksta priekšmetā, kad oriģinālam temata nav.
_NO_SUBJECT = "Jūsu pieprasījums"


def reply_subject(subject: str) -> str:
    """`Re:` bez dublēšanās. "Re: Re: Re:" izskatās pēc robota, un tas te ir."""
    clean = (subject or "").strip()
    if not clean:
        return f"Re: {_NO_SUBJECT}"
    lowered = clean.lower()
    for prefix in ("re:", "re :", "atb:", "ответ:", "aw:", "sv:"):
        if lowered.startswith(prefix):
            return "Re: " + clean[len(prefix):].strip()
    return f"Re: {clean}"


def _references(incoming: Incoming) -> str:
    """`References` ķēde. Bez tās klienta pasta klients atbildi rāda kā jaunu
    sarunu, un sarakste sadalās divās vietās."""
    chain = [ref for ref in incoming.references.split() if ref]
    if incoming.message_id and incoming.message_id not in chain:
        chain.append(incoming.message_id)
    return " ".join(chain)


def build_draft(
    incoming: Incoming,
    letter: str,
    *,
    sender: str = "",
    sender_name: str = "Tehnisko Materiālu Sagāde",
) -> EmailMessage:
    """Melnraksts kā atbilde uz `incoming`.

    `letter` ir TIKAI klientam sūtāmā daļa (skat. `report.split_answer`) ar jau
    pārbaudītām bildēm. Šī funkcija atbildi vairs nešķiro — ja iekšējais teksts
    atnāk `letter` argumentā, tas aizies klientam.
    """
    msg = EmailMessage()
    from_address = sender or IMAP_USER
    if from_address:
        msg["From"] = formataddr((sender_name, from_address))
    if incoming.recipient:
        msg["To"] = incoming.recipient
    msg["Subject"] = reply_subject(incoming.subject)
    msg["Date"] = format_datetime(datetime.now().astimezone())
    msg["Message-ID"] = make_msgid(domain=(from_address.split("@")[-1] or None) if from_address else None)
    if incoming.message_id:
        msg["In-Reply-To"] = incoming.message_id
    chain = _references(incoming)
    if chain:
        msg["References"] = chain
    # Melnraksts, ko uzrakstīja aģents. Pasta klientos šī galvene neredzas, bet
    # pastkastītes revīzijā tā ir vienīgā pēda, kas atšķir cilvēka rakstīto.
    msg["X-Esupplier-Agent"] = "draft"

    msg.set_content(letter)
    msg.add_alternative(
        report.render_html(letter, title=reply_subject(incoming.subject)),
        subtype="html",
    )
    return msg
