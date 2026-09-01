"""Sarunas gājiena testi — kas paliek vēsturē un kas ne.

Divas kļūdas, kas tika atrastas testēšanā ar īstu vēstuli:
(1) rīku limita norādījums palika vēsturē un nogrieza rīkus nākamajam
    jautājumam ("nevaru apstiprināt" bez neviena meklējuma);
(2) vecu meklējumu pilnie rezultāti tika pārsūtīti pie katra nākamā
    izsaukuma un veidoja lielāko daļu no 99k tokeniem.
"""

from __future__ import annotations

from esupplier.agent.loop import HISTORY_OUTPUT_LIMIT, compact_history


def _output(size: int) -> dict:
    return {"type": "function_call_output", "call_id": "c1", "output": "x" * size}


def test_compact_history_saisina_tikai_ieprieksejos_gajienus() -> None:
    old = _output(HISTORY_OUTPUT_LIMIT + 5000)
    fresh = _output(HISTORY_OUTPUT_LIMIT + 5000)
    messages = [
        {"role": "user", "content": "pirmais jautājums"},
        old,
        {"role": "user", "content": "otrais jautājums"},
        fresh,
    ]

    saved = compact_history(messages)

    assert saved > 4000
    assert len(old["output"]) < HISTORY_OUTPUT_LIMIT + 200
    assert "saīsināts" in old["output"]
    # Pēdējā gājiena rezultāts paliek pilns — uz tā balstās papildjautājumi.
    assert len(fresh["output"]) == HISTORY_OUTPUT_LIMIT + 5000


def test_compact_history_neaiztiek_isus_rezultatus() -> None:
    short = _output(50)
    messages = [{"role": "user", "content": "a"}, short, {"role": "user", "content": "b"}]

    assert compact_history(messages) == 0
    assert short["output"] == "x" * 50


def test_compact_history_bez_vestures() -> None:
    assert compact_history([]) == 0
    assert compact_history([{"role": "user", "content": "a"}]) == 0


def test_rikulimita_noradijums_nepaliek_vesture() -> None:
    """Norādījumam "vairāk rīku nav" jāaiziet tikai uz vienu izsaukumu.

    Kamēr tas tika pievienots `messages`, nākamais jautājums sākās ar spēkā
    esošu aizliegumu meklēt: 0 rīku izsaukumu un atbilde no gaisa.
    """
    import inspect

    from esupplier.agent import loop

    source = inspect.getsource(loop.run_turn)
    limit_block = source.split("hit_iteration_limit = True", 1)[1]
    # Pirms `call_model` ar norādījumu vēsturē neko nepievienojam.
    before_final = limit_block.split("final = call_model", 1)[0]
    assert "messages.append" not in before_final
    assert "extra=[nudge]" in limit_block
