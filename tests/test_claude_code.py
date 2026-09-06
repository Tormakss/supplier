"""Claude Code prasme.

Prasme ir sistēmas prompta ATVASINĀJUMS. Ja fails atpaliek, menedžeris konsolē
un Claude Code sesijā dabū divus dažādus aģentus ar vienu nosaukumu — un tas
neizskatās pēc kļūdas, tas izskatās pēc "aģents šodien atbild savādāk".
"""

from __future__ import annotations

from esupplier.agent.prompts import SYSTEM_PROMPT
from esupplier.claude_code import SKILL_PATH, render, write_skill


def test_skill_on_disk_is_up_to_date() -> None:
    assert SKILL_PATH.exists(), "palaid: uv run skill"
    assert SKILL_PATH.read_text(encoding="utf-8") == render(), "palaid: uv run skill"


def test_skill_carries_the_system_prompt() -> None:
    body = render()
    # Pirmais noteikums ir tas, kas notur piedāvājumu uz kataloga.
    assert "Atbildi TIKAI par produktiem" in body
    assert SYSTEM_PROMPT.rstrip() in body


def test_frontmatter_names_the_skill_and_when_to_use_it() -> None:
    head = render().split("---")[1]
    assert "name: piedavajums" in head
    assert "description:" in head
    # Bez šiem vārdiem prasme netiek ieslēgta tad, kad vajag.
    assert "piedāvājum" in head and "klient" in head


def test_write_skill_is_idempotent(tmp_path) -> None:
    target = tmp_path / "skills" / "piedavajums" / "SKILL.md"
    write_skill(target)
    first = target.read_text(encoding="utf-8")
    write_skill(target)
    assert target.read_text(encoding="utf-8") == first
