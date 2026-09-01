"""Konstantes un vides mainīgie."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DB_PATH = Path(os.getenv("ESUPPLIER_DB") or PROJECT_ROOT / "data" / "catalog.db")
CACHE_DIR = PROJECT_ROOT / ".cache"
#: Kur nonāk `/save` HTML faili, ko menedžeris ielīmē e-pastā.
ANSWERS_DIR = Path(os.getenv("ESUPPLIER_ANSWERS") or PROJECT_ROOT / "atbildes")

# --- Datu avots -----------------------------------------------------------
SITE_URL = "https://e-supplier.lv"
STORE_API = f"{SITE_URL}/wp-json/wc/store/v1"
SITEMAP_URL = f"{SITE_URL}/wp-sitemap.xml"

USER_AGENT = "esupplier-agent/0.1 (katalogs sinhronizacijai)"
PER_PAGE = 100
REQUEST_TIMEOUT = 30.0
SCRAPE_DELAY_S = 0.7

# PVN likme Latvijā. Store API `prices.price` nāk AR PVN; cenu bez PVN
# rēķinām atpakaļ, ja to neizdodas nolasīt no `price_html` data-no-tax.
VAT_RATE = 0.21

# --- Modelis --------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("ESUPPLIER_MODEL") or "gpt-5.6-luna"
# Uzmanību: `max_completion_tokens` ierobežo domāšanu UN atbildi kopā —
# gpt-5.x domā pēc noklusējuma, tāpēc vajag rezervi.
#
# 8000 bija par maz. Piedāvājums ar produktu tabulu, salīdzinājuma tabulu un
# daudzuma teikumiem ir 2-3k tokenu, domāšana ar `medium` ap 3-5k, un tas, kas
# nepaspēja iznākt, bija atbildes BEIGAS — t.i. iekšējais bloks. Menedžerim
# tas izskatījās pēc "modelim nebija ko piebilst", nevis pēc apcirstas
# atbildes.
MAX_TOKENS = 16000
# Cik dziļi modelis domā pirms atbildes. `medium` ir laba cenas/kvalitātes
# robeža šim uzdevumam; ar `minimal` modelis retāk ķeras pie rīkiem.
REASONING_EFFORT = os.getenv("ESUPPLIER_EFFORT") or "medium"
# Cik reizes modelis drīkst iet pēc datiem viena jautājuma laikā. Pieci bija
# par maz: pieprasījumam ar vairākām pozīcijām gājieni beidzās pusceļā, un
# atbilde tika salikta no tā, kas pagadījās — parasti viena neprecīza prece.
MAX_TOOL_ITERATIONS = 8
REQUEST_TIMEOUT_LLM = 120.0

CONTACT_EMAIL = "office@supplier.lv"

# --- Cenas izmaksu aprēķinam ---------------------------------------------
#: USD par 1M tokenu: (ievade, izvade).
#:
#: ŠIE SKAITĻI NAV MŪŽĪGI un tos NEDRĪKST uzskatīt par pārbaudītiem — cenas
#: jāsalīdzina ar pakalpojuma oficiālo lapu. Modelis, kam šeit ieraksta nav,
#: eval izvadā rāda izmaksas kā "n/a", nevis izdomātu skaitli.
PRICING: dict[str, tuple[float, float]] = {
    # Anthropic (no sākotnējās specifikācijas; Sonnet 5 ievades akcija
    # beidzas 2026-08-31, pēc tam $3/$15)
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    # OpenAI — aizpildi pēc platform.openai.com/pricing
    # "gpt-5.6-luna": (?, ?),
}
#: Kešotā ievade OpenAI pusē maksā lētāk; ja modeļa cena nav zināma, tas
#: nemainās neko.
CACHED_INPUT_DISCOUNT = 0.1
