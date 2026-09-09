"""IMAP savienojums: lasām ienākošās, rakstām melnrakstus.

SMTP šeit APZINĀTI nav: neviena vēstule klientam neaiziet bez cilvēka klikšķa.
"""

from __future__ import annotations

import imaplib
import re
import ssl
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

#: `LIST` rinda: (\HasNoChildren \Drafts) "." "INBOX.Drafts"
_LIST_LINE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+"?(?P<sep>[^"\s]*)"?\s+(?P<name>.+)$')

#: Melnrakstu mapes, ja serveris special-use karogu nedod.
_DRAFT_NAMES = (
    "Drafts",
    "INBOX.Drafts",
    "Melnraksti",
    "Черновики",
    "[Gmail]/Drafts",
    "[Gmail]/Melnraksti",
    "[Google Mail]/Drafts",
)

_GMAIL_HOSTS = ("imap.gmail.com", "imap.googlemail.com")


def login_hint(host: str, error: object) -> str:
    """Ko cilvēkam darīt ar šo pieslēgšanās kļūdu."""
    text = str(error).lower()
    gmail = host.lower() in _GMAIL_HOSTS
    if "application-specific password" in text or "web login required" in text:
        return (
            "\nGmail parastu konta paroli IMAP pieslēgumiem nepieņem kopš 2022. gada. "
            "Ieslēdz kontam divpakāpju verifikāciju, izveido App Password un ieliec "
            "to ESUPPLIER_IMAP_PASSWORD vietā."
        )
    if "imap access is disabled" in text or "imap is disabled" in text:
        return (
            "\nPastkastītes iestatījumos IMAP ir izslēgts. Gmail: Settings -> "
            "Forwarding and POP/IMAP -> Enable IMAP. Workspace domēnā to var būt "
            "aizliedzis administrators."
        )
    if "authenticationfailed" in text or "invalid credentials" in text or "login failed" in text:
        if gmail:
            return (
                "\nPārbaudi lietotājvārdu un to, vai parole ir App Password, nevis "
                "konta parole."
            )
        return "\nPārbaudi ESUPPLIER_IMAP_USER un ESUPPLIER_IMAP_PASSWORD."
    return ""


class MailError(RuntimeError):
    """IMAP kļūda, ko ir jēga parādīt cilvēkam bez stacktrace."""


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
            raise MailError(
                f"Neizdevās pieslēgties {self.host}: {exc}{login_hint(self.host, exc)}"
            ) from exc

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
        """Kur likt melnrakstu: vispirms `\\Drafts` karogs, tad uzminēts nosaukums."""
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

        Ne katrs serveris atbalsta lietotāja atslēgvārdus, tāpēc `UNKEYWORD`
        krītot atkāpjamies; dublēšanos tāpat notur SQLite žurnāls.
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
            result.reverse()  # jaunākās pirmās
            return result[:limit] if limit else result
        raise MailError("IMAP meklēšana neizdevās visos veidos.")

    def unmark(self, uid: str, keyword: str = "") -> bool:
        """Noņem atslēgvārdu, lai vēstule atkal iekrīt `search_new` tvērienā."""
        keyword = keyword or IMAP_KEYWORD
        try:
            status, _ = self.conn.uid("STORE", uid, "-FLAGS", f"({keyword})")
        except imaplib.IMAP4.error:
            return False
        return status == "OK"

    def fetch(self, uid: str) -> bytes:
        status, data = self.conn.uid("FETCH", uid, "(BODY.PEEK[])")
        self._ok(status, data, f"Vēstules {uid} lasīšana")
        for item in data or []:
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
                return item[1]
        raise MailError(f"Vēstulei {uid} nav ķermeņa.")

    # -- rakstīšana ---------------------------------------------------------
    def mark(self, uid: str, keyword: str = "") -> bool:
        """Uzliek atslēgvārdu. `False`, ja serveris to neatļauj — tā nav kļūda."""
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
