"""Tas pats gājiens caur Claude API (Anthropic Messages API).

Atšķirība no `claude_loop.py` ir maksāšanas ceļš, ne uzvedība: šeit iet
ANTHROPIC_API_KEY, tur — abonements caur Claude Code. Rīki, sistēmas prompts un
`AgentResult` visiem trim dzinējiem ir vieni.

Vēsture dzīvo `messages` sarakstā Anthropic formātā, tāpat kā `loop.py` tā
dzīvo Responses API formātā. Domāšanas blokus atdodam atpakaļ nemainītus —
bez tiem API rīku ciklu ar ieslēgtu domāšanu noraida.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from anthropic import Anthropic, AnthropicError

from ..catalog import db
from ..config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    MAX_TOKENS,
    MAX_TOOL_ITERATIONS,
    REASONING_EFFORT,
    REQUEST_TIMEOUT_LLM,
    THINKING_BUDGET,
)
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SPECS, ToolCall, execute_tool

CONTACT_HINT = "Pamēģini pārformulēt jautājumu vai pārbaudi savienojumu."
#: Cik gara paliek IEPRIEKŠĒJO gājienu rīku atbilde, kad tā jāpārsūta vēlreiz.
HISTORY_OUTPUT_LIMIT = 700

#: Rīki Anthropic formātā: tas pats, kas `TOOL_SPECS`, tikai `input_schema`.
TOOLS: list[dict[str, Any]] = [
    {
        "name": spec["name"],
        "description": spec["description"],
        "input_schema": spec["parameters"],
    }
    for spec in TOOL_SPECS
]

NUDGE = (
    "Vairāk rīku izsaukumu ŠAJĀ atbildē nav pieejams. Atbildi ar to informāciju, "
    "kas jau ir, ievērojot parasto atbildes formātu, un iekšējā blokā pasaki, kas "
    "palika neapstiprināts."
)


def build_client() -> Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "Trūkst ANTHROPIC_API_KEY. Nokopē .env.example uz .env un ieliec "
            "atslēgu no console.anthropic.com."
        )
    return Anthropic(api_key=ANTHROPIC_API_KEY, timeout=REQUEST_TIMEOUT_LLM)


def thinking_config() -> dict[str, Any] | None:
    """Domāšanas budžets no `ESUPPLIER_EFFORT`. `None` = domāšana izslēgta.

    Budžetam jāpaliek zem `max_tokens`, citādi API pieprasījumu noraida. Rezervi
    atstājam pašai atbildei: piedāvājums ar tabulām ir 2-3k tokenu.
    """
    budget = THINKING_BUDGET.get(REASONING_EFFORT, THINKING_BUDGET["medium"])
    if budget <= 0 or budget >= MAX_TOKENS - 2000:
        return None
    return {"type": "enabled", "budget_tokens": budget}


def compact_history(messages: list[dict[str, Any]]) -> int:
    """Saīsina IEPRIEKŠĒJO gājienu rīku atbildes. Atgriež ietaupīto zīmju skaitu.

    Tas pats iemesls, kas `loop.compact_history`: katrs izsaukums pārsūta visu
    vēsturi, tāpēc vecas meklēšanas tiek apmaksātas atkārtoti.
    """
    last_user_text = -1
    for index, item in enumerate(messages):
        if item.get("role") != "user":
            continue
        content = item.get("content")
        # Rīku rezultāts arī ir `user` loma, bet tas nav cilvēka jautājums.
        if isinstance(content, str) or not _is_tool_result(content):
            last_user_text = index

    saved = 0
    for item in messages[:last_user_text]:
        content = item.get("content")
        if not _is_tool_result(content):
            continue
        for block in content:
            text = block.get("content")
            if not isinstance(text, str) or len(text) <= HISTORY_OUTPUT_LIMIT:
                continue
            block["content"] = (
                text[:HISTORY_OUTPUT_LIMIT]
                + "… [saīsināts — iepriekšēja gājiena rezultāts. "
                "Ja vajag pilnu sarakstu, izsauc rīku vēlreiz.]"
            )
            saved += len(text) - len(block["content"])
    return saved


def _is_tool_result(content: Any) -> bool:
    return (
        isinstance(content, list)
        and bool(content)
        and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    )


def _text_of(blocks: list[Any]) -> str:
    return "\n".join(
        block.text for block in blocks
        if getattr(block, "type", None) == "text" and getattr(block, "text", "")
    ).strip()


def run_turn(
    messages: list[dict[str, Any]],
    max_iterations: int = MAX_TOOL_ITERATIONS,
    conn: sqlite3.Connection | None = None,
    client: Any = None,
) -> Any:
    """Claude API: modelis -> rīki -> modelis. Tā pati saskarne, kas `loop.run_turn`."""
    from .loop import AgentResult  # cikla dēļ: `loop` zina par šo moduli

    if conn is None:
        with db.session() as owned:
            return run_turn(messages, max_iterations, conn=owned, client=client)

    client = client or build_client()
    result = AgentResult()
    started = time.monotonic()
    compact_history(messages)
    thinking = thinking_config()

    def call_model(with_tools: bool, extra: list[dict[str, Any]] | None = None) -> Any:
        kwargs: dict[str, Any] = {
            "model": CLAUDE_MODEL,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [*messages, *(extra or [])],
        }
        if with_tools:
            kwargs["tools"] = TOOLS
        if thinking:
            kwargs["thinking"] = thinking
        return client.messages.create(**kwargs)

    def account(response: Any) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        result.input_tokens += getattr(usage, "input_tokens", 0) or 0
        result.output_tokens += getattr(usage, "output_tokens", 0) or 0
        result.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

    try:
        for iteration in range(max_iterations):
            response = call_model(with_tools=True)
            account(response)
            result.status = response.stop_reason

            # Domāšanas blokus atdodam nemainītus: bez paraksta API nākamo
            # pieprasījumu ar rīku rezultātiem noraida.
            messages.append(
                {
                    "role": "assistant",
                    "content": [b.model_dump(exclude_none=True) for b in response.content],
                }
            )

            calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not calls:
                result.text = _text_of(response.content)
                result.truncated = response.stop_reason == "max_tokens"
                if not result.text and result.truncated:
                    result.text = (
                        "Atbilde bija pārāk gara un tika apcirsta. "
                        "Uzdod šaurāku jautājumu."
                    )
                break

            outputs: list[dict[str, Any]] = []
            for call in calls:
                content, record = execute_tool(call.name, dict(call.input or {}), conn)
                result.tool_calls.append(record)
                outputs.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": content,
                        "is_error": record.is_error,
                    }
                )
            messages.append({"role": "user", "content": outputs})

            if iteration == max_iterations - 1:
                # Beidzās gājieni — lūdzam atbildēt ar to, kas jau ir. Šo
                # norādījumu vēsturē NEGLABĀJAM: tur tas paliktu spēkā arī
                # nākamajam jautājumam ar jau atjaunotu limitu.
                result.hit_iteration_limit = True
                final = call_model(
                    with_tools=False, extra=[{"role": "user", "content": NUDGE}]
                )
                account(final)
                result.status = final.stop_reason
                result.truncated = final.stop_reason == "max_tokens"
                messages.append(
                    {
                        "role": "assistant",
                        "content": [b.model_dump(exclude_none=True) for b in final.content],
                    }
                )
                result.text = _text_of(final.content)
    except AnthropicError as exc:
        result.text = f"Neizdevās sazināties ar modeli: {exc}"
        result.status = "error"

    result.duration_s = time.monotonic() - started
    if not result.text:
        result.text = f"Neizdevās sagatavot atbildi. {CONTACT_HINT}"
    return result


__all__ = ["TOOLS", "ToolCall", "build_client", "compact_history", "run_turn", "thinking_config"]
