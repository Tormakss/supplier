"""Konsoles saskarne — jautā un atbild."""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule

from . import report
from .agent.loop import AgentResult, build_client, run_turn
from .agent.tools import ToolCall
from .catalog import db
from .catalog.search import catalog_stats

HELP = """\
Ievade ir DAUDZRINDU: ielīmē visu vēstuli un pabeidz ar rindu `.` vai Ctrl+D.
Komanda (rinda, kas sākas ar /) tiek izpildīta uzreiz, bez atdalītāja.

Komandas:
  /save      saglabā pēdējo vēstuli kā HTML ar bildēm un atver pārlūkā
  /tools     pēdējā gājiena rīku izsaukumi ar parametriem un rezultātiem
  /reset     notīra sarunas vēsturi
  /sync      pārsinhronizē katalogu no e-supplier.lv
  /units     pārrēķina mērvienības pēc data/units.csv labošanas
  /verbose   ieslēdz/izslēdz pilnus rīku inputus un outputus
  /help      šis saraksts
  /exit      iziet (arī Ctrl+D tukšā ievadē)\
"""

#: Rindas, kas nobeidz daudzrindu ievadi. `.` ir mail(1) mantojums.
_SUBMIT = {".", "/send", "/suti", "/sūti"}


def _local_time(iso: str | None) -> str:
    if not iso:
        return "nezināms"
    try:
        stamp = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return stamp.astimezone().strftime("%Y-%m-%d %H:%M")


def _print_tool_calls(console: Console, calls: list[ToolCall], verbose: bool) -> None:
    if not calls:
        console.print("[dim]Pēdējā gājienā rīki netika izsaukti.[/dim]")
        return
    for i, call in enumerate(calls, 1):
        # Aizpildītos noklusējumus (0, "", false) nerādām — tie tāpat tiek atmesti.
        shown = {k: v for k, v in call.input.items() if v not in ("", 0, False, None)}
        colour = "red" if call.is_error else "cyan"
        console.print(
            f"[{colour}]{i}. {call.name}[/{colour}]"
            f"([dim]{json.dumps(shown, ensure_ascii=False)}[/dim])"
            f" -> [bold]{call.result_count}[/bold]"
            + (" [red]KĻŪDA[/red]" if call.is_error else "")
        )
        for note in call.notes:
            console.print(f"   [yellow]! {note}[/yellow]")
        if verbose:
            body = call.output
            if len(body) > 4000:
                body = body[:4000] + f"… (+{len(call.output) - 4000} rakstzīmes)"
            console.print(f"   [dim]{body}[/dim]")


def _print_footer(console: Console, result: AgentResult) -> None:
    bits = [
        f"{result.products_used} produkti izmantoti",
        f"{len(result.tool_calls)} rīku izsaukumi",
        f"{result.duration_s:.1f}s",
        f"{result.total_tokens} tokeni",
    ]
    if result.cached_tokens:
        bits.append(f"{result.cached_tokens} kešoti")
    if result.reasoning_tokens:
        bits.append(f"{result.reasoning_tokens} domāšanai")
    if result.hit_iteration_limit:
        bits.append("[yellow]sasniegts rīku limits[/yellow]")
    if result.truncated:
        bits.append("[red]ATBILDE APCIRSTA[/red]")
    console.print(f"[dim]\\[{' · '.join(bits)}][/dim]")


def _save_answer(
    console: Console, text: str, conn: Any, target: str | None, open_browser: bool
) -> None:
    if not text:
        console.print("[yellow]Nav ko saglabāt — vispirms uzdod jautājumu.[/yellow]")
        return
    letter, internal = report.split_answer(text)
    if not letter.strip():
        console.print("[yellow]Atbildē nav klientam sūtāmās daļas.[/yellow]")
        return
    try:
        path, dropped = report.save_answer(text, path=target, conn=conn)
    except OSError as exc:
        console.print(f"[red]Neizdevās saglabāt: {exc}[/red]")
        return

    console.print(f"[green]Saglabāts:[/green] {path}")
    for leak in report.contact_leaks(text):
        console.print(
            f"[yellow]! Failā palika mūsu iekšējā adrese: {leak}[/yellow]"
        )
    if dropped:
        console.print(
            f"[yellow]! {len(dropped)} attēls(i) nebija katalogā un tika izmesti "
            f"— pārbaudi bildes pirms sūtīšanas.[/yellow]"
        )
    if internal:
        console.print("[dim]Iekšējās piezīmes failā nav iekļautas.[/dim]")
    if open_browser:
        webbrowser.open(path.resolve().as_uri())


def _autosave(console: Console, text: str, conn: Any) -> None:
    """Klusi saglabā vēstuli kā HTML un izdrukā ceļu.

    Foto un tabulu robežas dzīvo tikai HTML failā. Kamēr tas prasīja atsevišķu
    `/save`, menedžeris kopēja no termināļa un bildes neaizgāja vispār.
    """
    letter, _internal = report.split_answer(text)
    if not letter.strip():
        return
    try:
        path, dropped = report.save_answer(text, conn=conn)
    except OSError as exc:
        console.print(f"[yellow]! Neizdevās saglabāt HTML: {exc}[/yellow]")
        return

    # file:// saite ir terminālī klikšķināma.
    uri = path.resolve().as_uri()
    console.print(f"[green]Vēstule ar bildēm:[/green] [link={uri}]{path}[/link]")
    if dropped:
        console.print(
            f"[yellow]! {len(dropped)} attēls(i) nebija katalogā un tika izmesti "
            f"— pārbaudi bildes pirms sūtīšanas.[/yellow]"
        )


def _read_message(console: Console) -> str | None:
    """Nolasa VIENU ziņu, kas var būt vairākas rindas. None = jāiziet.

    Ar `input()` katra ielīmētās vēstules rinda kļūtu par atsevišķu gājienu.
    Tukša rinda ievadi NEBEIDZ: e-pastā tādas ir starp sveicienu un parakstu.
    """
    lines: list[str] = []
    while True:
        prompt = "[bold green]> [/bold green]" if not lines else "[dim]… [/dim]"
        try:
            line = console.input(prompt)
        except EOFError:
            console.print()
            if not lines:
                return None
            break
        except KeyboardInterrupt:
            if not lines:
                console.print("[dim](Ctrl+D lai izietu)[/dim]")
                continue
            console.print("[dim]Ievade atcelta.[/dim]")
            lines = []
            continue

        stripped = line.strip()
        if stripped.lower() in _SUBMIT:
            break
        # Komandu nav jēgas gaidīt līdz atdalītājam — tā vienmēr ir viena rinda.
        if not lines and stripped.startswith("/"):
            return stripped
        if not lines and not stripped:
            continue
        lines.append(line)

    return "\n".join(lines).strip()


def _run_units(console: Console, conn: Any) -> None:
    """Pārrēķina `unit` visiem produktiem pēc likumu vai CSV labošanas.

    Atsevišķi no `/sync`: mērvienība ir mūsu dati, ne veikala.
    """
    from .catalog import units

    overrides = units.load_overrides()
    counts = units.apply_units(conn, overrides)
    console.print(
        "[green]Mērvienības pārrēķinātas:[/green] "
        + ", ".join(f"{units.LABELS.get(u, u)}={n}" for u, n in sorted(counts.items()))
        + (f" [dim](izņēmumi: {len(overrides)})[/dim]" if overrides else "")
    )


def _run_sync(console: Console) -> None:
    from .catalog.sync import run_sync

    try:
        with console.status("Sinhronizēju katalogu…", spinner="dots"):
            count = run_sync(progress=lambda m: None)
        console.print(f"[green]Gatavs: {count} produkti.[/green]")
    except Exception as exc:
        console.print(f"[red]Sinhronizācija neizdevās: {exc}[/red]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="e-supplier.lv produktu asistents")
    parser.add_argument(
        "--verbose", action="store_true", help="rādīt pilnus rīku inputus un outputus"
    )
    parser.add_argument("--ask", help="uzdod vienu jautājumu un iziet")
    parser.add_argument(
        "--save",
        nargs="?",
        const="",
        metavar="FAILS",
        help="saglabā vēstuli arī norādītajā failā (HTML mapē atbildes/ top vienmēr)",
    )
    args = parser.parse_args(argv)

    console = Console()
    verbose = args.verbose

    with db.session() as conn:
        stats = catalog_stats(conn=conn)
        if not stats["total"]:
            console.print("[red]Katalogs tukšs. Palaid: uv run sync[/red]")
            return 1

        try:
            client = build_client()
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            return 1

        console.print(
            f"[bold]Katalogā: {stats['total']} produkti[/bold] "
            f"[dim]({stats['in_stock']} noliktavā · sinhronizēts "
            f"{_local_time(stats['synced_at'])})[/dim]"
        )

        messages: list[dict[str, Any]] = []
        last_calls: list[ToolCall] = []
        last_answer = ""

        def ask(question: str) -> None:
            nonlocal last_calls, last_answer
            messages.append({"role": "user", "content": question})
            with console.status("Meklēju katalogā…", spinner="dots"):
                result = run_turn(messages, conn=conn, client=client)
            last_calls = result.tool_calls
            last_answer = result.text
            if verbose:
                _print_tool_calls(console, result.tool_calls, verbose=True)
            console.print()
            # Bilžu URL izstieptu tabulu pāri ekrānam; 📷 ir saite.
            console.print(Markdown(report.for_console(result.text)))
            console.print()
            for leak in report.contact_leaks(result.text):
                console.print(
                    f"[yellow]! Vēstulē klientam ir mūsu iekšējā adrese — "
                    f"izņem pirms sūtīšanas: {leak}[/yellow]"
                )
            # Artikuls vai cena, ko neviens rīks neatdeva.
            seen = report.tool_provenance(result.tool_calls)
            letter_part, _ = report.split_answer(result.text)
            for sku in report.unbacked_skus(letter_part, seen.skus):
                console.print(
                    f"[yellow]! Artikuls {sku} nenāk no rīka atbildes — "
                    f"pārbaudi katalogā.[/yellow]"
                )
            for price in report.unbacked_prices(letter_part, seen.prices):
                console.print(
                    f"[yellow]! Cena {price} € nav no kataloga un no tā "
                    f"neizriet — pārbaudi.[/yellow]"
                )
            # Modelis atlikumu neredz: katrs tāds skaitlis ir izdomāts solījums.
            for line in report.stock_leaks(letter_part):
                console.print(
                    f"[yellow]! Vēstulē ir atlikuma skaitlis vai vārds — "
                    f"izņem: {line}[/yellow]"
                )
            _, bad_links = report.verify_links(
                letter_part, report.known_product_urls(conn)
            )
            for url in bad_links:
                console.print(
                    f"[yellow]! Saite nav katalogā — pārbaudi: {url}[/yellow]"
                )
            # Atlikums menedžerim. Vēstulē tā nav un nedrīkst būt.
            for note in report.stock_notes(conn, report.cited_skus(letter_part)):
                console.print(f"   [cyan]{note}[/cyan]")
            if result.truncated:
                console.print(
                    "[red]! Atbilde tika apcirsta pusvārdā — beigas (arī "
                    "iekšējais bloks) var trūkt. Pārjautā šaurāk.[/red]"
                )
            elif not report.has_internal(result.text):
                # Bloka trūkums izskatās pēc "nekas nav jādara", un rezervācija
                # ar termiņu paliek neizdarīti.
                console.print(
                    "[yellow]! Atbildē NAV iekšējā bloka (⚑ IEKŠĒJI). "
                    "Pārbaudi, vai tiešām nekas nav jāizdara ar roku.[/yellow]"
                )
            # VIENMĒR: `/save` atcerēšanās nedrīkst būt priekšnoteikums tam,
            # lai menedžeris vispār ieraudzītu foto.
            _autosave(console, result.text, conn)
            _print_footer(console, result)

        # `--ask -` un caurule ir tas pats: viss ķermenis ir VIENA ziņa, un
        # tam jāstrādā bez termināļa un bez atdalītāja.
        question = args.ask
        if question == "-" or (question is None and not sys.stdin.isatty()):
            question = sys.stdin.read().strip()

        if question:
            ask(question)
            # `ask` jau saglabāja mapē atbildes/; ar ceļu saglabājam vēlreiz.
            if args.save:
                _save_answer(console, last_answer, conn, args.save, open_browser=False)
            return 0
        if args.ask is not None or not sys.stdin.isatty():
            console.print("[yellow]Tukšs pieprasījums.[/yellow]")
            return 1

        console.print(
            "[dim]Ielīmē vēstuli un pabeidz ar rindu `.` (vai Ctrl+D). "
            "/help komandām.[/dim]"
        )

        while True:
            console.print()
            line = _read_message(console)
            if line is None:
                console.print("[dim]Uz redzēšanos.[/dim]")
                break

            if not line:
                continue

            if line.startswith("/"):
                command = line.split()[0].lower()
                if command in ("/exit", "/quit"):
                    break
                if command == "/help":
                    console.print(HELP)
                elif command == "/save":
                    target = line.split(maxsplit=1)[1].strip() if len(line.split()) > 1 else None
                    _save_answer(console, last_answer, conn, target, open_browser=True)
                elif command == "/reset":
                    messages.clear()
                    last_calls = []
                    last_answer = ""
                    console.print("[dim]Sarunas vēsture notīrīta.[/dim]")
                elif command == "/tools":
                    # Pilnos outputus rāda tikai --verbose / /verbose.
                    _print_tool_calls(console, last_calls, verbose=verbose)
                elif command == "/verbose":
                    verbose = not verbose
                    console.print(f"[dim]verbose: {'ieslēgts' if verbose else 'izslēgts'}[/dim]")
                elif command == "/units":
                    _run_units(console, conn)
                elif command == "/sync":
                    _run_sync(console)
                    stats = catalog_stats(conn=conn)
                    console.print(f"[dim]Katalogā: {stats['total']} produkti.[/dim]")
                else:
                    console.print(f"[red]Nezināma komanda: {command}[/red]")
                    console.print(HELP)
                continue

            try:
                ask(line)
            except KeyboardInterrupt:
                console.print("\n[yellow]Pārtraukts.[/yellow]")
            except Exception as exc:
                console.print(f"[red]Kļūda: {exc}[/red]")

        console.print(Rule(style="dim"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
