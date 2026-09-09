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

# --- Dzinējs --------------------------------------------------------------
# Trīs ceļi pie modeļa, viena uzvedība:
#   `anthropic` — Claude API ar ANTHROPIC_API_KEY.
#   `openai`    — ChatGPT (Responses API) ar OPENAI_API_KEY.
#   `claude`    — Claude Agent SDK; maksā no abonementa, atslēga nav vajadzīga,
#                 bet prasa uzstādītu un pieteiktu Claude Code.
_ENGINE_ALIASES = {
    "claude-api": "anthropic",
    "claude_api": "anthropic",
    "chatgpt": "openai",
    "sdk": "claude",
}
_engine_raw = (os.getenv("ESUPPLIER_ENGINE") or "claude").strip().lower()
ENGINE = _ENGINE_ALIASES.get(_engine_raw, _engine_raw)
#: Dzinēji, kas iet caur API atslēgu. `claude` te NAV: tam atslēgas nav.
API_ENGINES = ("anthropic", "openai")
#: Nezināmu vērtību NEPIEŅEMAM klusi: `antropic` bez `h` citādi aizietu uz
#: ChatGPT, un cilvēks maksātu svešai atslēgai, domādams, ka izvēlējās Claude.
KNOWN_ENGINES = ("claude", "anthropic", "openai")

# --- Modelis --------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("ESUPPLIER_MODEL") or "gpt-5.6-luna"
#: Atsevišķs no `MODEL`: viens lauks abiem nozīmētu, ka dzinēja maiņa klusi
#: paņem otram neesošu modeli. Kopīgs `anthropic` un `claude` dzinējiem —
#: modeļa nosaukums abos ir viens un tas pats.
CLAUDE_MODEL = os.getenv("ESUPPLIER_CLAUDE_MODEL") or "claude-opus-5"
#: Modelis, ko tiešām lieto ieslēgtais dzinējs. Bez šī evals rāda `MODEL` arī
#: tad, kad atbildi rakstīja Claude, un izmaksas tiek rēķinātas pēc svešas cenas.
ACTIVE_MODEL = MODEL if ENGINE == "openai" else CLAUDE_MODEL
#: Cik tokenu Claude API drīkst notērēt domāšanai. Jāpaliek zem `MAX_TOKENS`,
#: citādi atbildei vietas nepaliek; `0` domāšanu izslēdz.
THINKING_BUDGET = {"low": 2000, "medium": 4000, "high": 8000, "xhigh": 10000, "max": 10000}
# `max_completion_tokens` ierobežo domāšanu UN atbildi kopā. Ar 8000 pietrūka
# tieši atbildes BEIGĀM, t.i. iekšējam blokam.
MAX_TOKENS = 16000
# Ar `minimal` modelis retāk ķeras pie rīkiem.
REASONING_EFFORT = os.getenv("ESUPPLIER_EFFORT") or "medium"
# Cik reizes modelis drīkst iet pēc datiem. Ar pieciem daudzpozīciju
# pieprasījumi beidzās pusceļā.
MAX_TOOL_ITERATIONS = 8
REQUEST_TIMEOUT_LLM = 120.0

CONTACT_EMAIL = "office@supplier.lv"

# --- Pastkastīte ----------------------------------------------------------
# SMTP šeit APZINĀTI nav: neviena vēstule neaiziet bez cilvēka klikšķa.
IMAP_HOST = os.getenv("ESUPPLIER_IMAP_HOST") or ""
IMAP_PORT = int(os.getenv("ESUPPLIER_IMAP_PORT") or 993)
IMAP_USER = os.getenv("ESUPPLIER_IMAP_USER") or ""
IMAP_PASSWORD = os.getenv("ESUPPLIER_IMAP_PASSWORD") or ""
#: `1` = IMAPS (993). `0` = STARTTLS uz 143. Nešifrētu savienojumu nav.
IMAP_SSL = (os.getenv("ESUPPLIER_IMAP_SSL") or "1").strip().lower() not in ("0", "false", "no")
IMAP_FOLDER = os.getenv("ESUPPLIER_IMAP_FOLDER") or "INBOX"
#: Tukšs = atrodam pēc `\Drafts` karoga, ar atkāpšanos uz uzminētu nosaukumu.
IMAP_DRAFTS = os.getenv("ESUPPLIER_IMAP_DRAFTS") or ""
#: Apstrādāto vēstuļu atslēgvārds. Atšķirībā no `\Seen` nepazūd, kad cilvēks
#: vēstuli atver savā pasta klientā.
IMAP_KEYWORD = os.getenv("ESUPPLIER_IMAP_KEYWORD") or "$AiDrafted"
#: Cik vēstules vienā gājienā. Katra maksā pilnu aģenta ciklu.
MAIL_BATCH = int(os.getenv("ESUPPLIER_MAIL_BATCH") or 10)
#: Pauze starp pārbaudēm. Viena pārbaude ir viens `SEARCH` — tokenus nemaksā.
MAIL_POLL_S = int(os.getenv("ESUPPLIER_MAIL_POLL") or 60)
#: Cik zīmju no ķermeņa aiziet modelim. Garākais parasti ir pārsūtīta sarakste.
MAIL_BODY_LIMIT = 12000
#: Adreses vai domēni, uz kuriem neatbildam nekad. Ar komatu. Domāts paša
#: sistēmu paziņojumiem, kas nāk no īstas adreses un izsūtņu filtros neiekrīt.
MAIL_IGNORE_SENDERS = tuple(
    part.strip().lower()
    for part in (os.getenv("ESUPPLIER_IMAP_IGNORE") or "").split(",")
    if part.strip()
)
IMAP_TIMEOUT = 30.0

# --- Pielikumi ------------------------------------------------------------
#: Cik lielu pielikumu vispār atveram. PDF katalogs mēdz būt desmitiem MB, un
#: tā saturs nav pieprasījums.
MAIL_ATTACHMENT_MAX_BYTES = int(os.getenv("ESUPPLIER_ATTACHMENT_MAX_BYTES") or 10_000_000)
#: Cik zīmju no VIENA pielikuma aiziet modelim.
MAIL_ATTACHMENT_TEXT_LIMIT = int(os.getenv("ESUPPLIER_ATTACHMENT_CHARS") or 4000)
#: Cik zīmju kopā. Bez tā divi gari PDF izspiež no konteksta pašu vēstuli.
MAIL_ATTACHMENTS_TEXT_LIMIT = int(os.getenv("ESUPPLIER_ATTACHMENTS_CHARS") or 12000)
#: Cik PDF lapu lasām. Tālāk sākas tipveida noteikumi.
MAIL_ATTACHMENT_PDF_PAGES = int(os.getenv("ESUPPLIER_ATTACHMENT_PDF_PAGES") or 20)
#: Cik failu atveram no viena arhīva; pieprasījums ir pirmajos.
MAIL_ARCHIVE_MAX_FILES = int(os.getenv("ESUPPLIER_ARCHIVE_MAX_FILES") or 12)
#: LibreOffice vecajiem `.doc`, `.xls`, `.ppt`. Tukšs = meklējam `soffice` PATH.
SOFFICE_BIN = os.getenv("ESUPPLIER_SOFFICE") or ""
#: Cik ilgi gaidām vienu LibreOffice konvertāciju.
SOFFICE_TIMEOUT_S = int(os.getenv("ESUPPLIER_SOFFICE_TIMEOUT") or 60)

# --- Attēlu atšifrēšana ---------------------------------------------------
# Atšifrējums NAV oriģināls: tas aiziet modelim atzīmēts, un menedžeris par to
# saņem atsevišķu brīdinājumu.
MAIL_ATTACHMENT_VISION = (
    os.getenv("ESUPPLIER_ATTACHMENT_VISION") or "1"
).strip().lower() not in ("0", "false", "no")
#: Modelis, kas redz attēlus. Tukšs = tas pats, kas raksta vēstuli.
VISION_MODEL = os.getenv("ESUPPLIER_VISION_MODEL") or MODEL
#: Cik PDF lapu attēlojam, kad teksta slāņa nav.
MAIL_ATTACHMENT_VISION_PAGES = int(os.getenv("ESUPPLIER_VISION_PAGES") or 5)
#: Cik lielu attēlu vispār sūtām; virs tā telefona foto paliek cilvēkam.
MAIL_ATTACHMENT_VISION_MAX_BYTES = int(os.getenv("ESUPPLIER_VISION_MAX_BYTES") or 8_000_000)
#: PDF lapas izšķirtspēja. Zem 150 DPI izmēru atzīmes vairs nesalasās.
MAIL_ATTACHMENT_VISION_DPI = int(os.getenv("ESUPPLIER_VISION_DPI") or 150)
#: Cik zīmju paturam no viena attēla atšifrējuma.
MAIL_ATTACHMENT_VISION_CHARS = int(os.getenv("ESUPPLIER_VISION_CHARS") or 4000)
#: Cik attēlu vienā vēstulē atšifrējam. Katrs maksā savu izsaukumu.
MAIL_ATTACHMENT_VISION_MAX_FILES = int(os.getenv("ESUPPLIER_VISION_MAX_FILES") or 5)


# --- Cenas izmaksu aprēķinam ---------------------------------------------
#: USD par 1M tokenu: (ievade, izvade). ŠIE SKAITĻI NAV PĀRBAUDĪTI — salīdzini
#: ar pakalpojuma oficiālo lapu. Bez ieraksta eval rāda "n/a", ne izdomātu.
PRICING: dict[str, tuple[float, float]] = {
    # Anthropic (no sākotnējās specifikācijas; Sonnet 5 ievades akcija
    # beidzas 2026-08-31, pēc tam $3/$15)
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    # OpenAI — aizpildi pēc platform.openai.com/pricing
    # "gpt-5.6-luna": (?, ?),
}
#: Kešotā ievade OpenAI pusē maksā lētāk.
CACHED_INPUT_DISCOUNT = 0.1
