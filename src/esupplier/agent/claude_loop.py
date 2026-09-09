"""Tas pats gājiens, bet caur Claude Agent SDK.

Atšķirība no `loop.py` ir tikai dzinējs; `AgentResult` un atbildes formāts ir
tie paši. Atslēgas nav: SDK maksā no abonementa un prasa pieteiktu Claude Code.

Rīki iet PROCESA IEKŠIENĒ (`create_sdk_mcp_server`), ne caur `mcp_server.py`:
otrs process nozīmētu otru SQLite savienojumu un otru kopiju tā paša koda.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLINotFoundError,
    ResultError,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from ..catalog import db
from ..config import CLAUDE_MODEL, MAX_TOOL_ITERATIONS, REASONING_EFFORT
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SPECS, execute_tool

#: Servera vārds MCP telpā. Rīki modelim redzami kā `mcp__katalogs__<vārds>`.
SERVER_NAME = "katalogs"

#: Ko modelim atļaujam. Ar failiem, čaulu un internetu aģentam nav darīšanas.
ALLOWED_TOOLS = [f"mcp__{SERVER_NAME}__{spec['name']}" for spec in TOOL_SPECS]

#: `allowed_tools` to jau nosaka, bet aizliegums paliek spēkā arī tad, ja
#: kāds sarakstu paplašina, neapdomājot, ko tas atver pastkastītes dēmonam.
DISALLOWED_TOOLS = [
    "Bash", "Write", "Edit", "NotebookEdit", "Read", "Glob", "Grep",
    "Task", "WebFetch", "WebSearch",
]

CONTACT_HINT = "Pamēģini pārformulēt jautājumu vai pārbaudi savienojumu."

_MISSING_CLI = (
    "Neatradu Claude Code. `claude` dzinējam tas ir jāuzstāda un jāpiesakās "
    "(`npm i -g @anthropic-ai/claude-code`, tad `claude`). Ja Claude Code nav, "
    "liec ESUPPLIER_ENGINE=anthropic (Claude API ar atslēgu) vai "
    "ESUPPLIER_ENGINE=openai."
)


def build_tools(conn: sqlite3.Connection, calls: list[Any]) -> list[Any]:
    """Kataloga rīki kā SDK rīki, katrs ar izsaukuma pierakstu.

    Uz pieraksta balstās izcelsmes pārbaude; bez tā tā klusē un izskatās pēc
    nostrādājušas.
    """
    wrapped = []
    for spec in TOOL_SPECS:
        name = spec["name"]

        def make(name: str = name, spec: dict[str, Any] = spec):
            @tool(name, spec["description"], spec["parameters"])
            async def run(args: dict[str, Any]) -> dict[str, Any]:
                content, record = execute_tool(name, dict(args or {}), conn)
                calls.append(record)
                return {
                    "content": [{"type": "text", "text": content}],
                    "is_error": record.is_error,
                }

            return run

        wrapped.append(make())
    return wrapped


def _tool_server(conn: sqlite3.Connection, calls: list[Any]) -> Any:
    """Rīki kā MCP serveris procesa iekšienē.

    Būvēts KATRAM gājienam no jauna: vajag konkrēto savienojumu un pierakstu.
    """
    return create_sdk_mcp_server(SERVER_NAME, tools=build_tools(conn, calls))


_EFFORTS = ("low", "medium", "high", "xhigh", "max")


NUDGE = (
    "Vairāk rīku izsaukumu ŠAJĀ atbildē nav pieejams. Atbildi ar to informāciju, "
    "kas jau ir, ievērojot parasto atbildes formātu, un iekšējā blokā pasaki, kas "
    "palika neapstiprināts."
)


def _options(
    conn: sqlite3.Connection,
    calls: list[Any],
    *,
    max_turns: int,
    resume: str,
    with_tools: bool = True,
) -> Any:
    return ClaudeAgentOptions(
        model=CLAUDE_MODEL,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={SERVER_NAME: _tool_server(conn, calls)} if with_tools else {},
        # Bez šī SDK paņem projekta `.mcp.json`: tie paši rīki, bet bez
        # izsaukumu pieraksta, un izcelsmes pārbaudei nav ko salīdzināt.
        strict_mcp_config=True,
        allowed_tools=ALLOWED_TOOLS if with_tools else [],
        disallowed_tools=DISALLOWED_TOOLS,
        max_turns=max_turns,
        effort=REASONING_EFFORT if REASONING_EFFORT in _EFFORTS else "medium",
        # `CLAUDE.md` un `.claude/settings.json` NEIELĀDĒJAM: izstrādātāja
        # iestatījumi klientam sūtāmā vēstulē neko nemeklē.
        setting_sources=None,
        permission_mode="bypassPermissions",
        **({"resume": resume} if resume else {}),
    )


async def _collect(stream: Any, out: dict[str, Any], chunks: list[str]) -> None:
    async for message in stream:
        session = getattr(message, "session_id", None)
        if session:
            out["session_id"] = session
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text:
                    chunks.append(block.text)
        elif isinstance(message, ResultMessage):
            out["result"] = message


async def _drive(
    prompt: str,
    conn: sqlite3.Connection,
    calls: list[Any],
    *,
    max_turns: int,
    resume: str,
) -> dict[str, Any]:
    """Viens gājiens. Atgriež to, kas vajadzīgs `AgentResult` aizpildīšanai."""
    out: dict[str, Any] = {
        "text": "", "session_id": resume, "result": None, "max_turns": False
    }
    chunks: list[str] = []

    try:
        await _collect(
            query(
                prompt=prompt,
                options=_options(conn, calls, max_turns=max_turns, resume=resume),
            ),
            out,
            chunks,
        )
    except ResultError as exc:
        if exc.subtype != "error_max_turns" and exc.terminal_reason != "max_turns":
            raise
        # Gājieni beidzās. Bez šī menedžeris dabūtu izņēmumu tur, kur agrāk
        # bija nepilnīga, bet lietojama vēstule.
        out["max_turns"] = True
        out["session_id"] = exc.session_id or out["session_id"]

    if out["max_turns"]:
        chunks.clear()
        followup: dict[str, Any] = {"session_id": out["session_id"], "result": None}
        await _collect(
            query(
                prompt=NUDGE,
                options=_options(
                    conn, calls, max_turns=1,
                    resume=out["session_id"], with_tools=False,
                ),
            ),
            followup,
            chunks,
        )
        out["session_id"] = followup["session_id"]
        out["result"] = followup["result"] or out["result"]

    result_message = out["result"]
    # `ResultMessage.result` ir pēdējais teksts; savāktie gabali ir rezerve.
    text = getattr(result_message, "result", None) or "\n".join(chunks)
    out["text"] = (text or "").strip()
    return out


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for item in reversed(messages):
        if item.get("role") == "user":
            content = item.get("content")
            return content if isinstance(content, str) else str(content)
    return ""


def _last_session(messages: list[dict[str, Any]]) -> str:
    """Sarunas turpinājums.

    Vēsturi tur Claude Code sesija, mums paliek tikai tās numurs: `messages`
    tā nekļūst par otru kopiju, kas ar pirmo sāktu nesakrist.
    """
    for item in reversed(messages):
        if item.get("session_id"):
            return str(item["session_id"])
    return ""


def _account(result: Any, message: Any) -> None:
    usage = getattr(message, "usage", None) or {}
    if not isinstance(usage, dict):
        usage = getattr(usage, "__dict__", {}) or {}
    result.input_tokens += int(usage.get("input_tokens") or 0)
    result.output_tokens += int(usage.get("output_tokens") or 0)
    result.cached_tokens += int(usage.get("cache_read_input_tokens") or 0)


def run_turn(
    messages: list[dict[str, Any]],
    max_iterations: int = MAX_TOOL_ITERATIONS,
    conn: sqlite3.Connection | None = None,
    client: Any = None,
) -> Any:
    """Tā pati saskarne, kas `loop.run_turn`. `client` netiek lietots."""
    from .loop import AgentResult  # cikla dēļ: `loop` zina par šo moduli

    if conn is None:
        with db.session() as owned:
            return run_turn(messages, max_iterations, conn=owned)

    result = AgentResult()
    started = time.monotonic()
    prompt = _latest_user_text(messages)
    if not prompt.strip():
        result.text = f"Tukšs jautājums. {CONTACT_HINT}"
        return result

    try:
        out = asyncio.run(
            _drive(
                prompt,
                conn,
                result.tool_calls,
                # SDK skaita gājienus, ne rīku izsaukumus: katram rīkam vajag
                # arī atbildes gājienu.
                max_turns=max(2, max_iterations * 2),
                resume=_last_session(messages),
            )
        )
    except CLINotFoundError:
        result.text = _MISSING_CLI
        result.status = "error"
        result.duration_s = time.monotonic() - started
        return result
    except (ClaudeSDKError, OSError) as exc:
        result.text = f"Neizdevās sazināties ar modeli: {exc}"
        result.status = "error"
        result.duration_s = time.monotonic() - started
        return result

    message = out["result"]
    if message is not None:
        _account(result, message)
        result.status = getattr(message, "subtype", None)
        result.truncated = getattr(message, "stop_reason", None) == "max_tokens"
    # Tas pats, ko vecajā ceļā rīku limits: atbilde var būt nepilnīga.
    result.hit_iteration_limit = bool(out["max_turns"])

    result.text = out["text"]
    if not result.text:
        result.text = f"Neizdevās sagatavot atbildi. {CONTACT_HINT}"
    # Lai nākamais jautājums turpina to pašu sarunu, ne sāk jaunu.
    messages.append(
        {"role": "assistant", "content": result.text, "session_id": out["session_id"]}
    )
    result.duration_s = time.monotonic() - started
    return result
