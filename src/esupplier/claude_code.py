"""Claude Code prasme no sistēmas prompta.

Prasmes fails ir `agent/prompts.py` ATVASINĀJUMS, ne otra kopija: ar roku
pārrakstīts, tas klusi atpaliktu.

    uv run skill        # pārraksta .claude/skills/piedavajums/SKILL.md

`tests/test_claude_code.py` krīt, ja fails atpalicis no prompta.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .agent.prompts import SYSTEM_PROMPT
from .config import PROJECT_ROOT

SKILL_PATH = PROJECT_ROOT / ".claude" / "skills" / "piedavajums" / "SKILL.md"

#: Apraksts izšķir, VAI prasme tiks ieslēgta, tāpēc tajā ir menedžera vārdi.
_FRONTMATTER = """\
---
name: piedavajums
description: >-
  Sagatavo piedāvājuma vēstuli klientam pēc e-supplier.lv kataloga. Lieto, kad
  menedžeris ielīmē klienta pieprasījumu vai vēstuli, jautā par preci, cenu,
  mērvienību vai pieejamību, vai lūdz sagatavot piedāvājumu, atbildi klientam
  vai preču izvēli blīvēšanas materiāliem, šļūtenēm, profiliem un gumijas
  izstrādājumiem.
---
"""

_PREAMBLE = """\
# Piedāvājums pēc e-supplier.lv kataloga

Katalogs nāk no MCP servera `e-supplier-katalogs` (`search_products`,
`get_product`, `browse_category`, `list_categories`). Ja rīku sesijā nav,
katalogs nav sinhronizēts vai serveris nav pieslēgts — pasaki to un apstājies,
nevis atbildi no atmiņas.

Zemāk ir tie paši noteikumi, pēc kuriem strādā `uv run chat`. Šis fails ir
ATVASINĀTS no `src/esupplier/agent/prompts.py`; labo tur un palaid `uv run skill`.

---

"""


def render() -> str:
    return _FRONTMATTER + "\n" + _PREAMBLE + SYSTEM_PROMPT.rstrip() + "\n"


def write_skill(path: Path | None = None) -> Path:
    target = path or SKILL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(), encoding="utf-8")
    return target


def main() -> int:
    written = write_skill()
    try:
        shown = written.relative_to(Path.cwd())
    except ValueError:
        shown = written
    print(f"Prasme uzrakstīta: {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
