"""LLM-as-judge: kritēriji, ko ar apakšvirknēm pārbaudīt nevar."""

from __future__ import annotations

import json
from dataclasses import dataclass

from openai import OpenAI, OpenAIError

from ..config import MODEL

JUDGE_SYSTEM = (
    "Tu esi stingrs vērtētājs. Atbildi TIKAI ar JSON: "
    '{"pass": true/false, "reason": "viens teikums"}. '
    "Neesi izpalīdzīgs — ja kritērijs nav skaidri izpildīts, tas ir neizdevies."
)


@dataclass(slots=True)
class Verdict:
    passed: bool
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0


def judge_answer(
    client: OpenAI,
    email: str,
    answer: str,
    criterion: str,
    model: str | None = None,
) -> Verdict:
    """Novērtē atbildi pēc brīvi formulēta kritērija.

    Bez rīkiem un bez domāšanas: vērtētājam jāspriež par tekstu, kas tam
    padots, nevis jāiet meklēt katalogā.
    """
    prompt = (
        f"KLIENTA E-PASTS:\n{email}\n\n"
        f"AĢENTA ATBILDE:\n{answer}\n\n"
        f"KRITĒRIJS:\n{criterion}"
    )
    try:
        response = client.responses.create(
            model=model or MODEL,
            input=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            reasoning={"effort": "low"},
            max_output_tokens=2000,
        )
    except OpenAIError as exc:
        return Verdict(False, f"vērtētājs neizdevās: {exc}")

    text = (response.output_text or "").strip()
    usage = response.usage
    tokens = (usage.input_tokens, usage.output_tokens) if usage else (0, 0)

    try:
        # Modelis dažreiz ietin JSON koda blokā.
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        data = json.loads(cleaned.strip())
        return Verdict(bool(data["pass"]), str(data.get("reason", "")), *tokens)
    except (ValueError, KeyError, TypeError):
        return Verdict(False, f"vērtētāja atbilde nav derīgs JSON: {text[:120]}", *tokens)
