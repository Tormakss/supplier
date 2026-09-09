"""Abonementa dzinējs: Claude Agent SDK.

Bez tīkla. `_drive` te ir aizstāts — pārbaudām to, kas ir mūsu pusē: dzinēja
izvēle, sarunas turpinājums, gājienu limits un rīku izsaukumu pieraksts, uz kura
balstās izcelsmes pārbaude.
"""

from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import CLINotFoundError

from esupplier.agent import claude_loop, loop
from esupplier.catalog import db


@pytest.fixture()
def conn(tmp_path):
    with db.session(tmp_path / "test.db") as connection:
        yield connection


def fake_drive(text: str = "Atbilde", session: str = "sess-1", max_turns: bool = False):
    """`_drive` vietā. Pieraksta, ar ko to sauca, un atdod gatavu rezultātu."""

    async def _drive(prompt, conn, calls, *, max_turns=0, resume=""):
        _drive.seen = {"prompt": prompt, "resume": resume, "max_turns": max_turns}
        return {
            "text": text,
            "session_id": session,
            "result": None,
            "max_turns": _drive.limit_hit,
        }

    _drive.limit_hit = max_turns
    return _drive


# --- dzinēja izvēle --------------------------------------------------------
def test_engine_switch_picks_the_subscription_path(conn, monkeypatch) -> None:
    """Zaram jābūt `loop.run_turn` iekšā. Ja tas nokļūtu izsaukuma vietās,
    `mail/run.py` un `cli.py` sāktu zināt, kurš modelis ir zem apakšas."""
    monkeypatch.setattr(loop, "ENGINE", "claude")
    called: list[str] = []
    monkeypatch.setattr(
        claude_loop, "run_turn", lambda *a, **k: called.append("claude") or "rezultāts"
    )

    assert loop.run_turn([{"role": "user", "content": "Cik maksā?"}], conn=conn) == "rezultāts"
    assert called == ["claude"]


def test_openai_engine_still_reachable(conn, monkeypatch) -> None:
    monkeypatch.setattr(loop, "ENGINE", "openai")
    called: list[str] = []
    monkeypatch.setattr(
        loop, "run_turn_openai", lambda *a, **k: called.append("openai") or "rezultāts"
    )

    loop.run_turn([{"role": "user", "content": "Cik maksā?"}], conn=conn)
    assert called == ["openai"]


def test_subscription_needs_no_api_key(monkeypatch) -> None:
    """`mail/run.py` klientu būvē pirms gājiena, lai trūkstoša atslēga atklātos
    uzreiz. Ar abonementu pārbaudāmās atslēgas nav, un tas nav kļūda."""
    monkeypatch.setattr(loop, "ENGINE", "claude")
    assert loop.build_client() is None

    monkeypatch.setattr(loop, "ENGINE", "openai")
    monkeypatch.setattr(loop, "OPENAI_API_KEY", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        loop.build_client()


# --- saruna ----------------------------------------------------------------
def test_answer_carries_the_session_forward(conn, monkeypatch) -> None:
    """Vēsturi tur Claude Code sesija, mums paliek tās numurs. Bez tā katrs
    papildjautājums sāktu jaunu sarunu ar to pašu cilvēku."""
    monkeypatch.setattr(claude_loop, "_drive", fake_drive(session="sess-42"))
    messages = [{"role": "user", "content": "Cik maksā EPDM?"}]
    result = claude_loop.run_turn(messages, conn=conn)

    assert result.text == "Atbilde"
    assert not result.hit_iteration_limit
    assert messages[-1]["session_id"] == "sess-42"
    assert claude_loop._last_session(messages) == "sess-42"


def test_followup_resumes_the_same_session(conn, monkeypatch) -> None:
    drive = fake_drive(session="sess-42")
    monkeypatch.setattr(claude_loop, "_drive", drive)
    messages = [{"role": "user", "content": "Cik maksā EPDM?"}]
    claude_loop.run_turn(messages, conn=conn)

    messages.append({"role": "user", "content": "Un cik tā pati DN32?"})
    claude_loop.run_turn(messages, conn=conn)

    assert drive.seen["resume"] == "sess-42"
    assert drive.seen["prompt"] == "Un cik tā pati DN32?"


def test_empty_question_does_not_reach_the_model(conn, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("tukšs jautājums nedrīkst maksāt izsaukumu")

    monkeypatch.setattr(claude_loop, "_drive", boom)
    result = claude_loop.run_turn([{"role": "user", "content": "   "}], conn=conn)
    assert "Tukšs jautājums" in result.text


# --- robežas ---------------------------------------------------------------
def test_turn_limit_is_reported_to_the_manager(conn, monkeypatch) -> None:
    """Gājienu limits nozīmē to pašu, ko vecajā ceļā rīku limits: atbilde var
    būt nepilnīga, un iekšējā blokā par to jāparādās brīdinājumam."""
    monkeypatch.setattr(claude_loop, "_drive", fake_drive(max_turns=True))
    result = claude_loop.run_turn([{"role": "user", "content": "Daudz pozīciju"}], conn=conn)

    assert result.hit_iteration_limit
    assert result.text == "Atbilde"


def test_missing_claude_code_says_what_to_install(conn, monkeypatch) -> None:
    async def missing(*_a, **_k):
        raise CLINotFoundError("nav")

    monkeypatch.setattr(claude_loop, "_drive", missing)
    result = claude_loop.run_turn([{"role": "user", "content": "Cik maksā?"}], conn=conn)

    assert "Claude Code" in result.text
    assert "ESUPPLIER_ENGINE=openai" in result.text
    assert result.status == "error"


# --- rīki ------------------------------------------------------------------
def test_tool_call_is_recorded_when_the_model_uses_it(conn) -> None:
    """Izcelsmes pārbaude salīdzina vēstulē nosauktos artikulus ar to, ko rīki
    tiešām atdeva. Bez pieraksta tā klusē, izskatoties pēc nostrādājušas."""
    calls: list = []
    tools = {t.name: t for t in claude_loop.build_tools(conn, calls)}
    assert set(tools) == {
        "search_products", "get_product", "browse_category", "list_categories"
    }

    answer = asyncio.run(tools["list_categories"].handler({}))

    assert answer["content"][0]["text"]
    assert [call.name for call in calls] == ["list_categories"]
    assert calls[0].output


def test_failed_tool_is_recorded_too(conn) -> None:
    """Kļūdains izsaukums arī ir izsaukums. Ja tas pieraksta netiktu, modelis
    varētu atsaukties uz preci, kuru neviens rīks neatdeva, un pārbaude to
    nepamanītu."""
    calls: list = []
    tools = {t.name: t for t in claude_loop.build_tools(conn, calls)}

    answer = asyncio.run(tools["get_product"].handler({"sku": "nav-tāda"}))

    assert len(calls) == 1
    assert answer["content"][0]["text"]


def test_allowed_tools_are_only_ours() -> None:
    """Pastkastītes dēmonam nav darīšanas ne ar failiem, ne čaulu, ne internetu."""
    assert all(name.startswith("mcp__katalogs__") for name in claude_loop.ALLOWED_TOOLS)
    for built_in in ("Bash", "Write", "Read", "WebFetch"):
        assert built_in in claude_loop.DISALLOWED_TOOLS


def test_options_ignore_the_projects_own_mcp_config(conn) -> None:
    """Bez `strict_mcp_config` modelis aizgāja uz projekta `.mcp.json` serveri:
    tie paši rīki, bet bez izsaukumu pieraksta, un pārbaude klusi izslēdzās."""
    options = claude_loop._options(conn, [], max_turns=4, resume="")
    assert options.strict_mcp_config is True
    assert options.setting_sources is None
    assert list(options.mcp_servers) == [claude_loop.SERVER_NAME]
