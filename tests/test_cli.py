"""Ievades testi.

Galvenā kļūda, ko šeit sargājam: ielīmēta e-pasta vēstule tika apstrādāta pa
rindai. Uz "Здравствуйте!" aizgāja viena atbilde, uz parakstu — nākamā, un
klients par vienu pieprasījumu būtu saņēmis piecas vēstules.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from esupplier.cli import _read_message

VESTULE = """\
Здравствуйте!

Нужен U-образный профиль, база 3 мм, головка ~11,5 мм.
Требуется 25 метров.

С уважением,
Андрей"""


class FakeConsole(Console):
    """Console, kas `input()` vietā atdod sagatavotas rindas."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__(quiet=True)
        self._lines = list(lines)

    def input(self, *args, **kwargs) -> str:
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


def test_ielimeta_vestule_ir_viena_zina() -> None:
    console = FakeConsole(VESTULE.splitlines() + ["."])
    assert _read_message(console) == VESTULE


def test_tuksa_rinda_nebeidz_ievadi() -> None:
    """E-pastā tukšās rindas ir starp sveicienu, tekstu un parakstu."""
    console = FakeConsole(["Labdien!", "", "Vajag blīvi.", "."])
    assert _read_message(console) == "Labdien!\n\nVajag blīvi."


def test_eof_nosuta_iesakto_ievadi() -> None:
    console = FakeConsole(["Labdien!", "Vajag blīvi."])
    assert _read_message(console) == "Labdien!\nVajag blīvi."


def test_eof_tuksa_ievade_nozime_iziet() -> None:
    assert _read_message(FakeConsole([])) is None


def test_komanda_aiziet_uzreiz_bez_atdalitaja() -> None:
    console = FakeConsole(["/tools", "nekad nenolasīts"])
    assert _read_message(console) == "/tools"


@pytest.mark.parametrize("terminator", [".", "/send", "/suti"])
def test_atdalitaji(terminator: str) -> None:
    console = FakeConsole(["Vajag blīvi.", terminator])
    assert _read_message(console) == "Vajag blīvi."
