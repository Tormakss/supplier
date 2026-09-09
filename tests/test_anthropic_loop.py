"""Claude API dzinējs (Anthropic Messages API).

Bez tīkla: `client.messages.create` te ir viltus objekts. Pārbaudām to, kas ir
mūsu pusē — dzinēja izvēle, rīku cikls, domāšanas bloku saglabāšana, gājienu
limits un izsaukumu pieraksts, uz kura balstās izcelsmes pārbaude.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from anthropic import APIError

from esupplier.agent import anthropic_loop, loop
from esupplier.catalog import db


@pytest.fixture()
def conn(tmp_path):
    with db.session(tmp_path / "test.db") as connection:
        yield connection


# --- viltus atbildes -------------------------------------------------------
class Block(SimpleNamespace):
    """Satura bloks ar `model_dump`, tāpat kā Anthropic SDK pydantic modelim."""

    def model_dump(self, exclude_none: bool = False) -> dict:
        data = dict(self.__dict__)
        return {k: v for k, v in data.items() if v is not None} if exclude_none else data


def text_block(text: str) -> Block:
    return Block(type="text", text=text, citations=None)


def thinking_block(text: str = "domāju", signature: str = "sig-1") -> Block:
    return Block(type="thinking", thinking=text, signature=signature)


def tool_block(name: str, params: dict, call_id: str = "call-1") -> Block:
    return Block(type="tool_use", id=call_id, name=name, input=params)


def response(blocks: list[Block], stop: str = "end_turn", tokens: tuple = (10, 5, 0)):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            cache_read_input_tokens=tokens[2],
        ),
    )


class FakeClient:
    """Atdod sagatavotās atbildes pēc kārtas un pieraksta katru izsaukumu."""

    def __init__(self, *responses) -> None:
        self.queue = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.queue.pop(0)


# --- dzinēja izvēle --------------------------------------------------------
def test_engine_switch_picks_the_claude_api_path(conn, monkeypatch) -> None:
    """Zaram jābūt `loop.run_turn` iekšā, tāpat kā abiem pārējiem dzinējiem."""
    monkeypatch.setattr(loop, "ENGINE", "anthropic")
    called: list[str] = []
    monkeypatch.setattr(
        anthropic_loop, "run_turn", lambda *a, **k: called.append("anthropic") or "rezultāts"
    )

    out = loop.run_turn([{"role": "user", "content": "Cik maksā?"}], conn=conn)
    assert out == "rezultāts"
    assert called == ["anthropic"]


def test_claude_api_needs_its_own_key(monkeypatch) -> None:
    """Atslēgas trūkums jāatklāj pirms gājiena, ne pēc pirmās vēstules."""
    monkeypatch.setattr(anthropic_loop, "ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        anthropic_loop.build_client()


def test_engine_alias_maps_claude_api_to_anthropic(monkeypatch) -> None:
    """`claude-api` ir tas, ko cilvēks uzraksta pats. Ja tas nokristu uz
    noklusējumu, viņš klusi maksātu no abonementa atslēgas vietā."""
    import importlib

    from esupplier import config

    monkeypatch.setenv("ESUPPLIER_ENGINE", "claude-api")
    assert importlib.reload(config).ENGINE == "anthropic"
    monkeypatch.setenv("ESUPPLIER_ENGINE", "chatgpt")
    assert importlib.reload(config).ENGINE == "openai"
    monkeypatch.delenv("ESUPPLIER_ENGINE")
    importlib.reload(config)


def test_tools_are_translated_to_the_anthropic_shape() -> None:
    """Rīku definīcijas ir vienas; te mainās tikai lauka nosaukums."""
    assert {t["name"] for t in anthropic_loop.TOOLS} == {
        "search_products", "get_product", "browse_category", "list_categories"
    }
    for tool in anthropic_loop.TOOLS:
        assert tool["input_schema"]["type"] == "object"
        assert tool["description"]


# --- rīku cikls ------------------------------------------------------------
def test_tool_call_runs_and_is_recorded(conn) -> None:
    """Izcelsmes pārbaude salīdzina vēstulē nosauktos artikulus ar to, ko rīki
    tiešām atdeva. Bez pieraksta tā klusē, izskatoties pēc nostrādājušas."""
    client = FakeClient(
        response([tool_block("list_categories", {})], stop="tool_use"),
        response([text_block("Katalogā ir šādas kategorijas.")]),
    )
    messages = [{"role": "user", "content": "Kas jums ir?"}]

    result = anthropic_loop.run_turn(messages, conn=conn, client=client)

    assert result.text == "Katalogā ir šādas kategorijas."
    assert [call.name for call in result.tool_calls] == ["list_categories"]
    assert result.tool_calls[0].output
    assert result.input_tokens == 20 and result.output_tokens == 10


def test_tool_result_goes_back_as_a_user_block(conn) -> None:
    client = FakeClient(
        response([tool_block("get_product", {"sku": "nav-tāda"}, "call-9")], stop="tool_use"),
        response([text_block("Tādas preces katalogā nav.")]),
    )
    messages = [{"role": "user", "content": "Artikuls nav-tāda?"}]

    anthropic_loop.run_turn(messages, conn=conn, client=client)

    results = [m for m in messages if anthropic_loop._is_tool_result(m.get("content"))]
    assert len(results) == 1
    block = results[0]["content"][0]
    assert block["tool_use_id"] == "call-9"
    assert isinstance(block["content"], str)


def test_thinking_blocks_are_sent_back_unchanged(conn) -> None:
    """Bez paraksta API rīku ciklu ar ieslēgtu domāšanu noraida."""
    client = FakeClient(
        response(
            [thinking_block(signature="sig-abc"), tool_block("list_categories", {})],
            stop="tool_use",
        ),
        response([text_block("Gatavs.")]),
    )

    anthropic_loop.run_turn([{"role": "user", "content": "Kas ir?"}], conn=conn, client=client)

    sent_back = client.calls[1]["messages"]
    thinking = [
        b for m in sent_back if isinstance(m.get("content"), list)
        for b in m["content"] if isinstance(b, dict) and b.get("type") == "thinking"
    ]
    assert thinking and thinking[0]["signature"] == "sig-abc"


def test_system_prompt_goes_in_its_own_field(conn) -> None:
    """Anthropic API sistēmas promptu tur atsevišķi, ne `messages` sarakstā."""
    client = FakeClient(response([text_block("Labdien.")]))
    anthropic_loop.run_turn([{"role": "user", "content": "Sveiki"}], conn=conn, client=client)

    assert "PIEDĀVĀJUM" in client.calls[0]["system"].upper()
    assert all(m["role"] != "system" for m in client.calls[0]["messages"])


# --- robežas ---------------------------------------------------------------
def test_tool_limit_asks_for_an_answer_without_tools(conn) -> None:
    """Beidzoties gājieniem, modelim jāatbild ar to, kas jau ir — citādi
    menedžeris dabū izņēmumu tur, kur bija nepilnīga, bet lietojama vēstule."""
    client = FakeClient(
        response([tool_block("list_categories", {})], stop="tool_use"),
        response([text_block("Tik, cik paspēju.")]),
    )

    result = anthropic_loop.run_turn(
        [{"role": "user", "content": "Daudz pozīciju"}], max_iterations=1,
        conn=conn, client=client,
    )

    assert result.hit_iteration_limit
    assert result.text == "Tik, cik paspēju."
    assert "tools" not in client.calls[1]
    assert client.calls[1]["messages"][-1]["content"] == anthropic_loop.NUDGE


def test_nudge_never_stays_in_the_history(conn) -> None:
    """Vēsturē palicis norādījums nākamajā jautājumā aizliegtu meklēt ar jau
    atjaunotu limitu."""
    client = FakeClient(
        response([tool_block("list_categories", {})], stop="tool_use"),
        response([text_block("Atbilde")]),
    )
    messages = [{"role": "user", "content": "Jautājums"}]

    anthropic_loop.run_turn(messages, max_iterations=1, conn=conn, client=client)

    assert all(m.get("content") != anthropic_loop.NUDGE for m in messages)


def test_truncated_answer_is_flagged(conn) -> None:
    """Apcirsta atbilde nogriež tieši iekšējo bloku, un melnrakstā tā nedrīkst."""
    client = FakeClient(response([text_block("Sākums…")], stop="max_tokens"))
    result = anthropic_loop.run_turn(
        [{"role": "user", "content": "Garš"}], conn=conn, client=client
    )
    assert result.truncated


def test_api_error_does_not_escape(conn) -> None:
    """Viena kritusi vēstule nedrīkst apstādināt pastkastītes gājienu."""

    class Boom:
        def __init__(self) -> None:
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **_kwargs):
            raise APIError("serveris klusē", request=None, body=None)

    result = anthropic_loop.run_turn(
        [{"role": "user", "content": "Cik maksā?"}], conn=conn, client=Boom()
    )
    assert result.status == "error"
    assert "Neizdevās sazināties" in result.text


# --- domāšana un vēsture ---------------------------------------------------
def test_thinking_budget_leaves_room_for_the_answer(monkeypatch) -> None:
    """Budžets virs `max_tokens` pieprasījumu noraida, un vēstule nesanāk vispār."""
    monkeypatch.setattr(anthropic_loop, "MAX_TOKENS", 16000)
    monkeypatch.setattr(anthropic_loop, "REASONING_EFFORT", "medium")
    assert anthropic_loop.thinking_config()["budget_tokens"] == 4000

    monkeypatch.setattr(anthropic_loop, "MAX_TOKENS", 4000)
    assert anthropic_loop.thinking_config() is None


def test_unknown_effort_falls_back_to_medium(monkeypatch) -> None:
    monkeypatch.setattr(anthropic_loop, "REASONING_EFFORT", "nezināms")
    assert anthropic_loop.thinking_config()["budget_tokens"] == 4000


def test_old_tool_results_are_shortened_but_the_last_stays(conn) -> None:
    """Katrs izsaukums pārsūta visu vēsturi, tāpēc vecas meklēšanas tiek
    apmaksātas atkārtoti. Pēdējā gājiena atbildes paliek pilnas."""
    long = "x" * 3000
    messages = [
        {"role": "user", "content": "Pirmais jautājums"},
        {"role": "assistant", "content": [{"type": "text", "text": "-"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": long}]},
        {"role": "user", "content": "Otrais jautājums"},
        {"role": "assistant", "content": [{"type": "text", "text": "-"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b", "content": long}]},
    ]

    saved = anthropic_loop.compact_history(messages)

    assert saved > 0
    assert len(messages[2]["content"][0]["content"]) < len(long)
    assert messages[5]["content"][0]["content"] == long


def test_typo_in_the_engine_name_is_not_silently_chatgpt(conn, monkeypatch) -> None:
    """`antropic` bez `h` agrāk aizgāja uz ChatGPT: cilvēks maksāja svešai
    atslēgai, domādams, ka izvēlējās Claude."""
    monkeypatch.setattr(loop, "ENGINE", "antropic")
    monkeypatch.setattr(
        loop, "run_turn_openai", lambda *a, **k: pytest.fail("nedrīkst nokļūt ChatGPT")
    )

    result = loop.run_turn([{"role": "user", "content": "Cik maksā?"}], conn=conn)
    assert result.status == "error"
    assert "ESUPPLIER_ENGINE" in result.text

    with pytest.raises(RuntimeError, match="Nezināms ESUPPLIER_ENGINE"):
        loop.build_client()
