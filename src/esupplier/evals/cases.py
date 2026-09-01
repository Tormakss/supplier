"""Testa gadījumu ielāde un pārbaudes."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT

CASES_PATH = PROJECT_ROOT / "evals" / "cases.jsonl"
RESULTS_DIR = PROJECT_ROOT / "evals" / "results"


def fold(text: str) -> str:
    """Reģistrnejutīgi un bez diakritikas — "blīv" jāsakrīt ar "Blīves"."""
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@dataclass(slots=True)
class Case:
    id: str
    email: str
    must_find_skus: list[str] = field(default_factory=list)
    must_not_find_skus: list[str] = field(default_factory=list)
    must_mention: list[str] = field(default_factory=list)
    must_not_mention: list[str] = field(default_factory=list)
    min_tool_calls: int | None = None
    max_tool_calls: int | None = None
    judge: str | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Case:
        known = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


def load_cases(path: Path | None = None) -> list[Case]:
    source = Path(path or CASES_PATH)
    if not source.exists():
        raise FileNotFoundError(f"Nav testa gadījumu faila: {source}")
    cases: list[Case] = []
    for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            cases.append(Case.from_dict(json.loads(line)))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{source}:{line_no} — bojāts JSON: {exc}") from exc
    return cases


def evaluate(case: Case, answer: str, tool_output: str, tool_calls: int) -> list[Check]:
    """Visas pārbaudes, izņemot LLM-judge (tas ir atsevišķi, jo maksā)."""
    checks: list[Check] = []
    answer_folded = fold(answer)
    everything_folded = fold(f"{answer}\n{tool_output}")

    for sku in case.must_find_skus:
        # Artikuls skaitās atrasts, ja tas parādās atbildē VAI rīku rezultātos —
        # modelis var to pareizi atrast un aprakstīt bez artikula citēšanas.
        found = fold(sku) in everything_folded
        checks.append(
            Check("must_find_skus", found, "" if found else f"{sku!r} neparādījās nekur")
        )

    for sku in case.must_not_find_skus:
        # Šeit skatāmies TIKAI atbildē: rīks to drīkst atgriezt, bet klientam
        # to piedāvāt nedrīkst.
        offered = fold(sku) in answer_folded
        checks.append(
            Check(
                "must_not_find_skus",
                not offered,
                f"{sku!r} tika piedāvāts klientam" if offered else "",
            )
        )

    for needle in case.must_mention:
        # Saraksts saraksta iekšienē = "jebkurš no šiem der". Bez tā
        # "EPDM vai silikons" būtu jākodē kā stingra EPDM prasība, un
        # pareiza atbilde ar silikonu skaitītos par kļūdu.
        options = needle if isinstance(needle, list) else [needle]
        hit = any(fold(o) in answer_folded for o in options)
        label = " VAI ".join(repr(o) for o in options)
        checks.append(
            Check("must_mention", hit, "" if hit else f"{label} nav atrasts atbildē")
        )

    for needle in case.must_not_mention:
        hit = fold(needle) in answer_folded
        checks.append(
            Check(
                "must_not_mention",
                not hit,
                f"{needle!r} pieminēts, lai gan nedrīkst" if hit else "",
            )
        )

    if case.min_tool_calls is not None:
        ok = tool_calls >= case.min_tool_calls
        checks.append(
            Check(
                "min_tool_calls",
                ok,
                "" if ok else f"{tool_calls} rīku izsaukumi, gaidīti vismaz {case.min_tool_calls}",
            )
        )

    if case.max_tool_calls is not None:
        ok = tool_calls <= case.max_tool_calls
        checks.append(
            Check(
                "max_tool_calls",
                ok,
                "" if ok else f"{tool_calls} rīku izsaukumi, atļauti ne vairāk kā {case.max_tool_calls}",
            )
        )

    return checks
