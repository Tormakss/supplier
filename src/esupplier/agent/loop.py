"""Tool calling cikls (OpenAI Responses API).

Responses API, ne Chat Completions: gpt-5.x tur neatļauj rīkus kopā ar
`reasoning_effort`, un bez domāšanas modelis retāk ķeras pie rīkiem.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI, OpenAIError

from ..catalog import db
from ..config import (
    ENGINE,
    KNOWN_ENGINES,
    MAX_TOKENS,
    MAX_TOOL_ITERATIONS,
    MODEL,
    OPENAI_API_KEY,
    REASONING_EFFORT,
    REQUEST_TIMEOUT_LLM,
)
from .prompts import SYSTEM_PROMPT
from .tools import TOOLS, ToolCall, execute_tool

CONTACT_HINT = "Pamēģini pārformulēt jautājumu vai pārbaudi savienojumu."
_UNKNOWN_ENGINE = (
    f"Nezināms ESUPPLIER_ENGINE=\"{ENGINE}\". Der: "
    "`anthropic` (Claude API ar ANTHROPIC_API_KEY), "
    "`openai` (ChatGPT ar OPENAI_API_KEY) vai "
    "`claude` (Claude Agent SDK no abonementa)."
)
#: Cik gara paliek IEPRIEKŠĒJO gājienu rīku atbilde, kad tā jāpārsūta vēlreiz.
HISTORY_OUTPUT_LIMIT = 700


@dataclass(slots=True)
class AgentResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    #: Cik no `input_tokens` nāca no kešatmiņas. Bez tā kopsumma izskatās
    #: sliktāk, nekā maksā patiesībā.
    cached_tokens: int = 0
    duration_s: float = 0.0
    status: str | None = None
    hit_iteration_limit: bool = False
    #: True, ja modelis netika līdz beigām. Apcirpšana nogriež tieši iekšējo
    #: bloku, un ar neiztukšu teksta daļu tas notiek klusi.
    truncated: bool = False

    @property
    def products_used(self) -> int:
        return sum(c.result_count for c in self.tool_calls if c.name != "list_categories")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def build_client() -> Any:
    """Klients tam dzinējam, kas ieslēgts.

    `claude` dzinējam klienta nav, un `None` te nav izlaidums: Agent SDK iet no
    abonementa, un pārbaudāmās atslēgas vienkārši nav.
    """
    if ENGINE not in KNOWN_ENGINES:
        raise RuntimeError(_UNKNOWN_ENGINE)
    if ENGINE == "claude":
        return None
    if ENGINE == "anthropic":
        from .anthropic_loop import build_client as anthropic_client

        return anthropic_client()
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "Trūkst OPENAI_API_KEY. Nokopē .env.example uz .env un ieliec atslēgu."
        )
    return OpenAI(api_key=OPENAI_API_KEY, timeout=REQUEST_TIMEOUT_LLM)


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    """Rīka argumenti nāk kā JSON teksts — bojāts JSON nedrīkst nogāzt sarunu."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _refusal_text(output: list[Any]) -> str | None:
    for item in output:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                return getattr(part, "refusal", None) or "Pieprasījums noraidīts."
    return None


def compact_history(messages: list[dict[str, Any]]) -> int:
    """Saīsina IEPRIEKŠĒJO gājienu rīku atbildes. Atgriež ietaupīto zīmju skaitu.

    Katrs izsaukums pārsūta visu vēsturi, tāpēc vecas meklēšanas tiek
    apmaksātas atkārtoti. Pēdējā gājiena atbildes paliek pilnas — uz tām
    balstās papildjautājumi ("un cik tā pati DN32?").
    """
    last_user = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1
    )
    saved = 0
    for item in messages[:last_user]:
        if item.get("type") != "function_call_output":
            continue
        output = item.get("output") or ""
        if len(output) <= HISTORY_OUTPUT_LIMIT:
            continue
        item["output"] = (
            output[:HISTORY_OUTPUT_LIMIT]
            + "… [saīsināts — iepriekšēja gājiena rezultāts. "
            "Ja vajag pilnu sarakstu, izsauc rīku vēlreiz.]"
        )
        saved += len(output) - len(item["output"])
    return saved


def run_turn(
    messages: list[dict[str, Any]],
    max_iterations: int = MAX_TOOL_ITERATIONS,
    conn: sqlite3.Connection | None = None,
    client: OpenAI | None = None,
) -> AgentResult:
    """Viens gājiens ar to dzinēju, kas ieslēgts `ESUPPLIER_ENGINE` mainīgajā.

    Zars ir ŠEIT, ne katrā izsaukuma vietā: divi ceļi ar vienu uzvedību ir
    vērtīgi tikai tad, kamēr izsaukuma puse ir viena.
    """
    if ENGINE == "claude":
        from .claude_loop import run_turn as claude_run_turn

        return claude_run_turn(messages, max_iterations, conn=conn)
    if ENGINE == "anthropic":
        from .anthropic_loop import run_turn as anthropic_run_turn

        return anthropic_run_turn(messages, max_iterations, conn=conn, client=client)
    if ENGINE != "openai":
        result = AgentResult()
        result.text = _UNKNOWN_ENGINE
        result.status = "error"
        return result
    return run_turn_openai(messages, max_iterations, conn=conn, client=client)


def run_turn_openai(
    messages: list[dict[str, Any]],
    max_iterations: int = MAX_TOOL_ITERATIONS,
    conn: sqlite3.Connection | None = None,
    client: OpenAI | None = None,
) -> AgentResult:
    """Vecais ceļš: OpenAI Responses API, modelis -> rīki -> modelis.

    `messages` tiek papildināts uz vietas, tāpēc vēsture saglabājas. Sistēmas
    promptu tur neglabājam: to var mainīt bez vēstures pārrakstīšanas.
    """
    if conn is None:
        with db.session() as owned:
            return run_turn_openai(messages, max_iterations, conn=owned, client=client)

    client = client or build_client()
    result = AgentResult()
    started = time.monotonic()
    compact_history(messages)

    def call_model(with_tools: bool, extra: list[dict[str, Any]] | None = None) -> Any:
        kwargs: dict[str, Any] = {
            "model": MODEL,
            "max_output_tokens": MAX_TOKENS,
            "reasoning": {"effort": REASONING_EFFORT},
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                *messages,
                *(extra or []),
            ],
        }
        if with_tools:
            kwargs["tools"] = TOOLS
        return client.responses.create(**kwargs)

    def account(response: Any) -> None:
        if not response.usage:
            return
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens
        details = getattr(response.usage, "output_tokens_details", None)
        if details:
            result.reasoning_tokens += getattr(details, "reasoning_tokens", 0) or 0
        in_details = getattr(response.usage, "input_tokens_details", None)
        if in_details:
            result.cached_tokens += getattr(in_details, "cached_tokens", 0) or 0

    try:
        for iteration in range(max_iterations):
            response = call_model(with_tools=True)
            account(response)
            result.status = response.status

            refusal = _refusal_text(response.output)
            if refusal:
                result.text = f"{refusal} {CONTACT_HINT}"
                break

            # Nemainītu, arī reasoning blokus: citādi pazūd saite uz call_id.
            messages.extend(item.model_dump(exclude_none=True) for item in response.output)

            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                result.text = (response.output_text or "").strip()
                result.truncated = response.status == "incomplete"
                if not result.text and result.truncated:
                    result.text = (
                        "Atbilde bija pārāk gara un tika apcirsta. "
                        "Uzdod šaurāku jautājumu."
                    )
                break

            for call in calls:
                content, record = execute_tool(
                    call.name, _parse_arguments(call.arguments), conn
                )
                result.tool_calls.append(record)
                messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": content,
                    }
                )

            if iteration == max_iterations - 1:
                # Beidzās gājieni — lūdzam atbildēt ar to, kas jau ir. Šo
                # norādījumu vēsturē NEGLABĀJAM: tur tas paliktu spēkā arī
                # nākamajam jautājumam ar jau atjaunotu limitu.
                result.hit_iteration_limit = True
                nudge = {
                    "role": "user",
                    "content": (
                        "Vairāk rīku izsaukumu ŠAJĀ atbildē nav pieejams. Atbildi "
                        "lietotājam ar to informāciju, kas jau ir, un pasaki, kas "
                        "palika neskaidrs."
                    ),
                }
                final = call_model(with_tools=False, extra=[nudge])
                account(final)
                result.status = final.status
                result.truncated = final.status == "incomplete"
                messages.extend(
                    item.model_dump(exclude_none=True) for item in final.output
                )
                result.text = (final.output_text or "").strip()
    except OpenAIError as exc:
        result.text = f"Neizdevās sazināties ar modeli: {exc}"
        result.status = "error"

    result.duration_s = time.monotonic() - started
    if not result.text:
        result.text = f"Neizdevās sagatavot atbildi. {CONTACT_HINT}"
    return result
