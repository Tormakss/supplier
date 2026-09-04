"""Pastkastītes gājiens: izlasi jaunās vēstules, atstāj melnrakstus.

Palaišana:

    uv run mail                 # seko pastkastītei, līdz nospiež Ctrl+C
    uv run mail --once          # viens gājiens un ārā (cron, pārbaudes)
    uv run mail --dry-run       # viss tas pats, bet melnraksts pastkastītē neaiziet
    uv run mail --retry-failed  # atkārto tās, kas iepriekš krita
    uv run mail --log           # ko jau esam apstrādājuši

Melnrakstā aiziet TIKAI vēstule klientam. Menedžera uzdevumi paliek konsolē
un failā `atbildes/*-IEKSEJI.txt`: melnraksts ir domāts nosūtīšanai bez
labošanas, un bloks, kas pirms tam jāizdzēš ar roku, kādreiz paliks neizdzēsts.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.parser import BytesParser
from typing import Any

from rich.console import Console

from .. import report
from ..agent.loop import build_client, run_turn
from ..catalog import db
from ..catalog.search import catalog_stats
from ..config import IMAP_USER, MAIL_BATCH, MAIL_POLL_S
from . import draft as draft_mod
from .imap import MailError, Mailbox
from .message import Incoming, as_prompt, parse_message, skip_reason


@dataclass(slots=True)
class Outcome:
    """Ko izdarījām ar vienu vēstuli."""

    incoming: Incoming
    status: str  # drafted | skipped | failed
    reason: str = ""
    answer_path: str = ""
    #: Menedžera uzdevumi. Melnrakstā to NAV — tikai konsolē un blakus failā.
    internal: str = ""
    internal_path: str = ""
    warnings: list[str] = field(default_factory=list)


def _augment_internal(internal: str, warnings: list[str]) -> str:
    """Pieliek iekšējam blokam to, ko konsolē būtu pateikusi programma.

    Izmestas bildes, iekšējās adreses noplūde, sasniegts rīku limits — konsolē
    par to brīdina programma. `--watch` režīmā konsolē neviens neskatās, tāpēc
    brīdinājumam jāpaliek arī failā blakus vēstulei.
    """
    if not warnings:
        return internal
    block = "\n".join(f"- {w}" for w in warnings)
    head = "**⚑ AUTOMĀTISKĀS PĀRBAUDES**\n" + block
    return f"{head}\n\n{internal}".strip() if internal.strip() else head


def process_one(
    incoming: Incoming,
    conn: sqlite3.Connection,
    client: Any,
    box: Mailbox | None,
    *,
    dry_run: bool = False,
    drafts_folder: str = "",
) -> Outcome:
    """Viena vēstule: modelis -> melnraksts. Nekad nemet izņēmumu uz augšu."""
    reason = skip_reason_for(incoming)
    if reason:
        return Outcome(incoming, "skipped", reason)

    # Katrai vēstulei SAVA vēsture. Kopīgs saraksts nozīmētu, ka otrā klienta
    # pieprasījumam modelis redz pirmā klienta preces un cenas.
    messages: list[dict[str, Any]] = [{"role": "user", "content": as_prompt(incoming)}]
    try:
        result = run_turn(messages, conn=conn, client=client)
    except Exception as exc:  # tīkls, API, kas vien — viena vēstule neapstādina gājienu
        return Outcome(incoming, "failed", f"aģenta kļūda: {exc}")

    if result.truncated:
        # Apcirsta atbilde ir tieši tā, ko nedrīkst likt melnrakstā: iekšējais
        # bloks ir pēdējais, ko modelis raksta, tāpēc apcirpta atbilde izskatās
        # pēc pilnas vēstules, kurai vienkārši "nav ko piebilst".
        return Outcome(incoming, "failed", "atbilde tika apcirsta — melnraksts netaisīts")

    letter, internal = report.split_answer(result.text)
    if not letter.strip():
        return Outcome(incoming, "failed", "atbildē nav klientam sūtāmās daļas")

    warnings: list[str] = []
    letter, dropped = report.verify_images(letter, report.known_image_urls(conn))
    if dropped:
        warnings.append(
            f"{len(dropped)} attēls(i) nebija katalogā un tika izmesti — "
            "pārbaudi bildes pirms sūtīšanas."
        )
    leaks = report.contact_leaks(letter)
    for leak in leaks:
        warnings.append(f"Vēstulē palika mūsu iekšējā adrese, izņem to: {leak}")
    if not internal.strip():
        warnings.append(
            "Modelis iekšējo bloku NEUZRAKSTĪJA. Tas nenozīmē, ka nekas nav "
            "jādara — pārbaudi rezervāciju, termiņu un rēķinu ar roku."
        )
    if incoming.attachments:
        warnings.append(
            "Vēstulei ir pielikumi, kurus aģents nelasa: "
            + ", ".join(incoming.attachments)
        )
    if result.hit_iteration_limit:
        warnings.append("Sasniegts rīku izsaukumu limits — atbilde var būt nepilnīga.")

    internal = _augment_internal(internal, warnings)

    answer_path = ""
    internal_path = ""
    try:
        # HTML fails mapē `atbildes/` ir vēstule klientam — bez piezīmēm, tāpat
        # kā līdz šim. Tas ir arī vienīgais, kas paliek, ja melnraksts pazuda
        # vai serveris `APPEND` noraidīja.
        path, _dropped = report.save_answer(result.text, conn=conn)
        answer_path = str(path)
        # Piezīmes — blakus, atsevišķā .txt. Melnrakstā to nav: melnraksts ir
        # domāts nosūtīšanai bez labošanas, un bloks, kas pirms tam jāizdzēš ar
        # roku, agri vai vēlu paliek neizdzēsts.
        if internal.strip():
            internal_path = str(report.save_internal(internal, answer_path=path))
    except OSError:
        warnings.append("Kopiju mapē atbildes/ saglabāt neizdevās.")

    if dry_run or box is None:
        return Outcome(
            incoming, "drafted", "dry-run", answer_path, internal, internal_path, warnings
        )

    message = draft_mod.build_draft(incoming, letter, sender=IMAP_USER)
    try:
        folder = box.append_draft(message, drafts_folder)
    except MailError as exc:
        return Outcome(
            incoming, "failed", str(exc), answer_path, internal, internal_path, warnings
        )
    return Outcome(
        incoming,
        "drafted",
        f"melnraksts mapē {folder}",
        answer_path,
        internal,
        internal_path,
        warnings,
    )


def skip_reason_for(incoming: Incoming) -> str:
    """`skip_reason` uz jau izparsētas vēstules.

    Galvenes pārbaudi veic `message.skip_reason`; šeit paliek tas, ko var
    pateikt bez MIME objekta.
    """
    if not incoming.body.strip():
        return "tukšs ķermenis"
    if not incoming.recipient:
        return "nav adreses, uz kuru atbildēt"
    return ""


def run_once(
    console: Console,
    conn: sqlite3.Connection,
    client: Any,
    *,
    limit: int = MAIL_BATCH,
    dry_run: bool = False,
    folder: str = "",
    announce: bool = True,
) -> list[Outcome]:
    """Viens gājiens: savienojums, jaunās vēstules, melnraksti, savienojums ciet.

    Savienojumu katram gājienam veram no jauna. Sekošanas režīmā sesija stāv
    atvērta stundām, un IMAP serveri neaktīvu savienojumu kādā brīdī nomet —
    tad nākamais gājiens kristu tur, kur iepriekšējais strādāja.

    `announce=False` klusē, kad jaunu vēstuļu nav. Sekošanas režīmā tas ir
    vienīgais, kas notiek 99% gājienu, un rinda par to katru minūti aizber
    ekrānu tā, ka īstie melnraksti tajā pazūd.
    """
    outcomes: list[Outcome] = []
    box = Mailbox(folder=folder)
    box.connect()
    try:
        total = box.select()
        drafts = "" if dry_run else box.drafts_folder()
        uids = box.search_new(limit=limit)
        if announce or uids:
            console.print(
                f"[dim]{box.folder}: {total} vēstules, {len(uids)} neapstrādātas"
                + (f" · melnraksti -> {drafts}" if drafts else " · dry-run")
                + "[/dim]"
            )

        for uid in uids:
            try:
                raw = box.fetch(uid)
            except MailError as exc:
                console.print(f"[red]{uid}: {exc}[/red]")
                continue

            incoming = parse_message(raw, uid=uid)
            key = incoming.message_id or f"uid:{box.folder}:{uid}"

            if db.is_processed(conn, key):
                box.mark(uid)
                continue

            # Galvenes filtru palaižam uz MIME objekta, jo `List-Id` un
            # `Auto-Submitted` izparsētajā `Incoming` vairs nav.
            mime = BytesParser(policy=policy.default).parsebytes(raw)
            header_reason = skip_reason(mime, incoming.body, own_address=IMAP_USER)
            if header_reason:
                outcome = Outcome(incoming, "skipped", header_reason)
            else:
                with console.status(f"[dim]{incoming.display}[/dim]", spinner="dots"):
                    outcome = process_one(
                        incoming, conn, client, box,
                        dry_run=dry_run, drafts_folder=drafts,
                    )

            outcomes.append(outcome)
            _report_outcome(console, outcome)
            if not dry_run:
                db.mark_processed(
                    conn,
                    key,
                    status=outcome.status,
                    uid=uid,
                    sender=incoming.sender,
                    subject=incoming.subject,
                    reason=outcome.reason,
                    answer_path=outcome.answer_path,
                )
                if outcome.status != "failed":
                    box.mark(uid)
    finally:
        box.close()
    return outcomes


#: Cik ilgi pauze drīkst augt, kad serveris neatbild. Desmit minūtes ir robeža,
#: aiz kuras atgriešanās vairs nav "tūlīt", bet ekrāns ar kļūdām neaizbirst.
_MAX_BACKOFF_S = 600

_COLOURS = {"drafted": "green", "skipped": "dim", "failed": "red"}
_LABELS = {"drafted": "MELNRAKSTS", "skipped": "izlaists", "failed": "KRITA"}


def _report_outcome(console: Console, outcome: Outcome) -> None:
    colour = _COLOURS[outcome.status]
    console.print(
        f"[{colour}]{_LABELS[outcome.status]}[/{colour}] {outcome.incoming.display}"
        + (f" [dim]({outcome.reason})[/dim]" if outcome.reason else "")
    )
    # Iekšējais bloks melnrakstā vairs neiet, tāpēc konsole ir vieta, kur
    # menedžeris to redz. Rādām VISU: uzdevums, ko neviens neizlasīja, nav
    # labāks par uzdevumu, kas nekad netika uzrakstīts.
    for line in outcome.internal.splitlines():
        if line.strip():
            console.print(f"   [cyan]{line.rstrip()}[/cyan]")
    for warning in outcome.warnings:
        console.print(f"   [yellow]! {warning}[/yellow]")
    if outcome.answer_path:
        console.print(f"   [dim]{outcome.answer_path}[/dim]")
    if outcome.internal_path:
        console.print(f"   [dim]{outcome.internal_path}[/dim]")


def _wait(console: Console, delay: int, totals: dict[str, int]) -> None:
    """Pauze starp pārbaudēm ar dzīvu rindu ekrāna apakšā.

    Rinda pārrakstās pati un vēsturē nepaliek. Bez tās sekošanas režīms
    izskatās pēc pakārušās programmas: pēdējais izvades gabals var būt vairākas
    stundas vecs, un nav kā pateikt, vai tā vēl skatās pastkastītē.
    """
    stamp = datetime.now().strftime("%H:%M:%S")
    line = (
        f"[dim]Gaidu jaunas vēstules · pēdējā pārbaude {stamp} · "
        f"{totals['drafted']} melnraksti, {totals['skipped']} izlaisti, "
        f"{totals['failed']} krita[/dim]"
    )
    with console.status(line, spinner="dots"):
        time.sleep(delay)


def _print_log(console: Console, conn: sqlite3.Connection, limit: int) -> None:
    rows = db.processed_log(conn, limit)
    if not rows:
        console.print("[dim]Žurnāls ir tukšs.[/dim]")
        return
    for row in rows:
        colour = _COLOURS.get(row["status"], "dim")
        console.print(
            f"[dim]{row['processed_at']}[/dim] "
            f"[{colour}]{_LABELS.get(row['status'], row['status'])}[/{colour}] "
            f"{row['sender']} — {row['subject'] or '(bez temata)'}"
            + (f" [dim]({row['reason']})[/dim]" if row["reason"] else "")
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seko pastkastītei un atstāj atbildes uz klientu vēstulēm kā melnrakstus"
        )
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="viens gājiens un ārā (noklusējumā seko pastkastītei bez apstājas)",
    )
    parser.add_argument(
        "--interval", type=int, default=MAIL_POLL_S, help="pauze sekundēs starp pārbaudēm"
    )
    parser.add_argument(
        "--limit", type=int, default=MAIL_BATCH, help="cik vēstules vienā gājienā"
    )
    parser.add_argument("--folder", default="", help="mape, ko lasīt (noklusējums INBOX)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="sagatavo atbildes, bet melnrakstu pastkastītē neieraksta",
    )
    parser.add_argument(
        "--retry-failed", action="store_true", help="atkārto vēstules, kas iepriekš krita"
    )
    parser.add_argument(
        "--log", nargs="?", type=int, const=20, help="parāda apstrādes žurnālu un iziet"
    )
    args = parser.parse_args(argv)

    console = Console()

    with db.session() as conn:
        if args.log is not None:
            _print_log(console, conn, args.log)
            return 0

        if args.retry_failed:
            forgotten = db.forget_failed(conn)
            console.print(f"[dim]Aizmirstas {forgotten} kritušās vēstules.[/dim]")

        stats = catalog_stats(conn=conn)
        if not stats["total"]:
            console.print("[red]Katalogs tukšs. Palaid: uv run sync[/red]")
            return 1

        try:
            client = build_client()
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            return 1

        interval = max(10, args.interval)
        if not args.once:
            console.print(
                f"[dim]Sekoju pastkastītei, pārbaude ik pēc {interval}s. "
                f"Ctrl+C, lai apstātos.[/dim]"
            )

        totals = {"drafted": 0, "skipped": 0, "failed": 0}
        delay = interval
        first = True

        while True:
            try:
                outcomes = run_once(
                    console, conn, client,
                    limit=args.limit, dry_run=args.dry_run, folder=args.folder,
                    announce=first or args.once,
                )
                delay = interval
            except MailError as exc:
                if args.once:
                    console.print(f"[red]{exc}[/red]")
                    return 1
                # Serveris nokrita vai tīkls pazuda. Sekošanas režīmā tas nav
                # iemesls apstāties, bet mēģināt katru minūti nozīmē aizbērt
                # ekrānu ar to pašu kļūdu — tāpēc pauze aug līdz `_MAX_BACKOFF_S`.
                delay = min(delay * 2, _MAX_BACKOFF_S)
                console.print(
                    f"[yellow]! {exc}[/yellow] [dim](mēģināšu vēlreiz pēc {delay}s)[/dim]"
                )
                outcomes = []
            except KeyboardInterrupt:
                console.print("\n[dim]Pārtraukts.[/dim]")
                return 0

            first = False
            for outcome in outcomes:
                totals[outcome.status] = totals.get(outcome.status, 0) + 1
            failed = sum(1 for o in outcomes if o.status == "failed")

            if args.once:
                console.print(
                    f"[dim]Gājiens beidzies: {totals['drafted']} melnraksti, "
                    f"{totals['skipped']} izlaisti, {totals['failed']} krita.[/dim]"
                )
                # Kritusi vēstule ir izejas kods 1: cron par to jāpaziņo. Otrreiz
                # tā pati vēstule nekritīs — `failed` ieraksts to notur ārpus
                # cikla, līdz kāds palaiž `--retry-failed`.
                return 1 if failed else 0

            try:
                _wait(console, delay, totals)
            except KeyboardInterrupt:
                console.print(
                    f"\n[dim]Apstājos. Kopā: {totals['drafted']} melnraksti, "
                    f"{totals['skipped']} izlaisti, {totals['failed']} krita.[/dim]"
                )
                return 0


if __name__ == "__main__":
    sys.exit(main())
