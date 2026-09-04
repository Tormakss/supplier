"""IMAP savienojums: lasām ienākošās, rakstām melnrakstus.

SMTP šeit nav un nebūs, kamēr tas ir pilots. Aģents raksta TIKAI melnrakstus:
vienīgais ceļš pie klienta iet caur cilvēku, kas nospiež "Sūtīt".
"""

from __future__ import annotations

import imaplib
import re
import ssl
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from email.message import EmailMessage
from time import time

from ..config import (
    IMAP_DRAFTS,
    IMAP_FOLDER,
    IMAP_HOST,
    IMAP_KEYWORD,
    IMAP_PASSWORD,
    IMAP_PORT,
    IMAP_SSL,
    IMAP_TIMEOUT,
    IMAP_USER,
)

#: `LIST` atbildes rinda: (\HasNoChildren \Drafts) "." "INBOX.Drafts"
_LIST_LINE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+"?(?P<sep>[^"\s]*)"?\s+(?P<name>.+)$')

#: Mapes, ko pieņemam par melnrakstiem, ja serveris special-use karogu nedod.
_DRAFT_NAMES = ("Drafts", "INBOX.Drafts", "Melnraksti", "Черновики", "[Gmail]/Drafts")


class MailError(RuntimeError):
    """IMAP kļūda, ko ir jēga parādīt cilvēkam bez stacktrace."""


@dataclass(slots=True)
class RawMessage:
    uid: str
    raw: bytes


def _unquote(name: bytes) -> str:
    text = name.decode("utf-8", errors="replace").strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return text


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Mailbox:
    """Plāns apvalks ap `imaplib`, kas runā šī projekta valodā."""

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        user: str = "",
        password: str = "",
        *,
        use_ssl: bool | None = None,
        folder: str = "",
    ) -> None:
        self.host = host or IMAP_HOST
        self.port = port or IMAP_PORT
        self.user = user or IMAP_USER
        self.password = password or IMAP_PASSWORD
        self.use_ssl = IMAP_SSL if use_ssl is None else use_ssl
        self.folder = folder or IMAP_FOLDER
        self._conn: imaplib.IMAP4 | None = None

    # -- savienojums --------------------------------------------------------
    def connect(self) -> None:
        if not self.host or not self.user or not self.password:
            raise MailError(
                "Trūkst pastkastītes datu. Ieliec .env: ESUPPLIER_IMAP_HOST, "
                "ESUPPLIER_IMAP_USER, ESUPPLIER_IMAP_PASSWORD."
            )
        try:
            if self.use_ssl:
                self._conn = imaplib.IMAP4_SSL(
                    self.host, self.port, timeout=IMAP_TIMEOUT
                )
            else:
                self._conn = imaplib.IMAP4(self.host, self.port, timeout=IMAP_TIMEOUT)
                self._conn.starttls(ssl.create_default_context())
            self._conn.login(self.user, self.password)
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
            raise MailError(f"Neizdevās pieslēgties {self.host}: {exc}") from exc

    def close(self) -> None:
        if not self._conn:
            return
        try:
            self._conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
        finally:
            self._conn = None

    @property
    def conn(self) -> imaplib.IMAP4:
        if not self._conn:
            raise MailError("Savienojums nav atvērts.")
        return self._conn

    def _ok(self, status: str, data: object, what: str) -> None:
        if status != "OK":
            raise MailError(f"{what} neizdevās: {data!r}")

    # -- mapes --------------------------------------------------------------
    def folders(self) -> list[tuple[str, set[str]]]:
        status, data = self.conn.list()
        if status != "OK":
            return []
        found: list[tuple[str, set[str]]] = []
        for line in data:
            if not isinstance(line, bytes):
                continue
            match = _LIST_LINE.match(line.strip())
            if not match:
                continue
            flags = {
                f.decode("ascii", "replace").lower()
                for f in match.group("flags").split()
            }
            found.append((_unquote(match.group("name")), flags))
        return found

    def drafts_folder(self) -> str:
        """Kur likt melnrakstu.

        Nosaukums serveriem atšķiras (`Drafts`, `INBOX.Drafts`, `Melnraksti`),
        tāpēc pirmais ceļš ir `\\Drafts` special-use karogs, ko Dovecot un
        pārējie mūsdienās atdod paši. Uzminēts nosaukums ir pēdējais variants:
        `APPEND` neesošā mapē krīt, un atbilde pazūd bez pēdām.
        """
        if IMAP_DRAFTS:
            return IMAP_DRAFTS
        listing = self.folders()
        for name, flags in listing:
            if "\\drafts" in flags:
                return name
        known = {name.lower(): name for name, _ in listing}
        for candidate in _DRAFT_NAMES:
            if candidate.lower() in known:
                return known[candidate.lower()]
        raise MailError(
            "Neatradu melnrakstu mapi. Norādi to ar ESUPPLIER_IMAP_DRAFTS "
            f"(pieejamās: {', '.join(name for name, _ in listing) or 'nav'})."
        )

    def select(self, folder: str = "", readonly: bool = False) -> int:
        status, data = self.conn.select(_quote(folder or self.folder), readonly=readonly)
        self._ok(status, data, f"Mapes {folder or self.folder} atvēršana")
        return int(data[0]) if data and data[0] else 0

    # -- lasīšana -----------------------------------------------------------
    def search_new(self, keyword: str = "", limit: int = 0) -> list[str]:
        """Vēstuļu UID, kam vēl NAV mūsu atslēgvārda.

        `UNKEYWORD` ir precīzākais ceļš, bet ne katrs serveris atbalsta
        lietotāja atslēgvārdus. Ja tas krīt, atkāpjamies uz `UNSEEN` — sliktāks
        kritērijs, taču SQLite žurnāls tāpat neļauj atbildēt divreiz.
        """
        keyword = keyword or IMAP_KEYWORD
        for criteria in (f'(UNKEYWORD "{keyword}")', "(UNSEEN)", "(ALL)"):
            try:
                status, data = self.conn.uid("SEARCH", None, criteria)
            except imaplib.IMAP4.error:
                continue
            if status != "OK":
                continue
            uids = (data[0] or b"").split()
            result = [u.decode("ascii") for u in uids]
            # Jaunākās ir svarīgākās: ja pastkastītē krājas simts vēstuļu,
            # menedžerim vajag šodienas, ne pagājušā gada.
            result.reverse()
            return result[:limit] if limit else result
        raise MailError("IMAP meklēšana neizdevās visos veidos.")

    def fetch(self, uid: str) -> bytes:
        status, data = self.conn.uid("FETCH", uid, "(BODY.PEEK[])")
        self._ok(status, data, f"Vēstules {uid} lasīšana")
        for item in data or []:
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
                return item[1]
        raise MailError(f"Vēstulei {uid} nav ķermeņa.")

    def fetch_new(self, keyword: str = "", limit: int = 0) -> Iterator[RawMessage]:
        for uid in self.search_new(keyword, limit):
            yield RawMessage(uid=uid, raw=self.fetch(uid))

    # -- rakstīšana ---------------------------------------------------------
    def mark(self, uid: str, keyword: str = "") -> bool:
        """Uzliek atslēgvārdu. `False`, ja serveris to neatļauj.

        Neveiksme nav kļūda: dublēšanos novērš SQLite žurnāls, un atslēgvārds
        ir tikai ērtība cilvēkam, kas skatās pastkastītē.
        """
        keyword = keyword or IMAP_KEYWORD
        try:
            status, _ = self.conn.uid("STORE", uid, "+FLAGS", f"({keyword})")
        except imaplib.IMAP4.error:
            return False
        return status == "OK"

    def append_draft(self, msg: EmailMessage, folder: str = "") -> str:
        """Ieliek melnrakstu mapē. Atgriež mapes nosaukumu, kur tas nonāca."""
        target = folder or self.drafts_folder()
        try:
            status, data = self.conn.append(
                _quote(target),
                r"(\Draft \Seen)",
                imaplib.Time2Internaldate(time()),
                msg.as_bytes(),
            )
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"Melnraksta ierakstīšana mapē {target} krita: {exc}") from exc
        self._ok(status, data, f"Melnraksta ierakstīšana mapē {target}")
        return target


@contextmanager
def mailbox(**kwargs: object) -> Iterator[Mailbox]:
    box = Mailbox(**kwargs)  # type: ignore[arg-type]
    box.connect()
    try:
        yield box
    finally:
        box.close()
