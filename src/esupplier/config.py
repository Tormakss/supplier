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

# --- Pastkastīte ----------------------------------------------------------
# Aģents lasa klientu vēstules pa IMAP un raksta atbildi ATPAKAĻ tajā pašā
# pastkastītē kā melnrakstu. Sūtīšanas ceļa (SMTP) šeit APZINĀTI nav: pilota
# versijā neviena vēstule klientam neaiziet bez cilvēka klikšķa.
IMAP_HOST = os.getenv("ESUPPLIER_IMAP_HOST") or ""
IMAP_PORT = int(os.getenv("ESUPPLIER_IMAP_PORT") or 993)
IMAP_USER = os.getenv("ESUPPLIER_IMAP_USER") or ""
IMAP_PASSWORD = os.getenv("ESUPPLIER_IMAP_PASSWORD") or ""
#: `1` = IMAPS (993). `0` = STARTTLS uz 143. Nešifrētu savienojumu nav.
IMAP_SSL = (os.getenv("ESUPPLIER_IMAP_SSL") or "1").strip().lower() not in ("0", "false", "no")
IMAP_FOLDER = os.getenv("ESUPPLIER_IMAP_FOLDER") or "INBOX"
#: Tukšs = atrodam pēc `\Drafts` special-use karoga, ar atkāpšanos uz "Drafts".
#: Mapes nosaukums serveriem atšķiras (`Drafts`, `INBOX.Drafts`, `Melnraksti`).
IMAP_DRAFTS = os.getenv("ESUPPLIER_IMAP_DRAFTS") or ""
#: IMAP atslēgvārds, ar ko iezīmējam apstrādātās vēstules. Atšķirībā no `\Seen`
#: tas nepazūd, kad cilvēks vēstuli atver savā pasta klientā.
IMAP_KEYWORD = os.getenv("ESUPPLIER_IMAP_KEYWORD") or "$AiDrafted"
#: Cik vēstules apstrādājam vienā gājienā. Katra maksā vienu pilnu aģenta ciklu.
MAIL_BATCH = int(os.getenv("ESUPPLIER_MAIL_BATCH") or 10)
#: Pauze sekundēs starp pastkastītes pārbaudēm. Viena pārbaude ir viens IMAP
#: `SEARCH` — tas nemaksā ne tokenus, ne manāmu laiku, tāpēc minūte ir droša.
MAIL_POLL_S = int(os.getenv("ESUPPLIER_MAIL_POLL") or 60)
#: Cik zīmju no vēstules ķermeņa aiziet modelim. Garāka vēstule parasti ir
#: pārsūtīta sarakste, un tās aste modelim tikai maldina.
MAIL_BODY_LIMIT = 12000
IMAP_TIMEOUT = 30.0

# --- Pielikumi ------------------------------------------------------------
# Rasējums, specifikācija un cenu tabula ir puse pieprasījuma. Aģents no tiem
# lasa TEKSTU; skenēts rasējums teksta nesatur un paliek cilvēkam.
#
#: Cik lielu pielikumu vispār atveram. Produktu katalogs PDF formātā mēdz būt
#: desmitiem MB, un tā saturs nav pieprasījums.
MAIL_ATTACHMENT_MAX_BYTES = int(os.getenv("ESUPPLIER_ATTACHMENT_MAX_BYTES") or 10_000_000)
#: Cik zīmju no VIENA pielikuma aiziet modelim.
MAIL_ATTACHMENT_TEXT_LIMIT = int(os.getenv("ESUPPLIER_ATTACHMENT_CHARS") or 4000)
#: Cik zīmju kopā no visiem pielikumiem. Bez šī limita divi gari PDF izspiestu
#: no konteksta pašu vēstuli, kuras dēļ viss notiek.
MAIL_ATTACHMENTS_TEXT_LIMIT = int(os.getenv("ESUPPLIER_ATTACHMENTS_CHARS") or 12000)
#: Cik PDF lapu lasām. Pieprasījums ir pirmajās; tālāk sākas tipveida noteikumi.
MAIL_ATTACHMENT_PDF_PAGES = int(os.getenv("ESUPPLIER_ATTACHMENT_PDF_PAGES") or 20)
#: Cik failu atveram no viena arhīva. Rasējumu ZIP mēdz būt ar simtiem lapu, un
#: pieprasījums ir pirmajās; pārējie tikai izspiestu vēstuli no konteksta.
MAIL_ARCHIVE_MAX_FILES = int(os.getenv("ESUPPLIER_ARCHIVE_MAX_FILES") or 12)
#: LibreOffice vecajiem binārajiem formātiem (`.doc`, `.xls`, `.ppt`). Tukšs =
#: meklējam `soffice` PATH. Ja tā nav, tādi pielikumi paliek cilvēkam.
SOFFICE_BIN = os.getenv("ESUPPLIER_SOFFICE") or ""
#: Cik ilgi gaidām vienu LibreOffice konvertāciju.
SOFFICE_TIMEOUT_S = int(os.getenv("ESUPPLIER_SOFFICE_TIMEOUT") or 60)

# --- Attēlu atšifrēšana ---------------------------------------------------
# Skenēts rasējums un telefonā nofotografēta specifikācija teksta slāni nesatur.
# Vienīgais, kas tos izlasa, ir modelis, kurš redz attēlu. Atšifrējums NAV
# oriģināls: tas aiziet modelim atzīmēts kā atšifrējums, un menedžeris par to
# saņem atsevišķu brīdinājumu.
MAIL_ATTACHMENT_VISION = (
    os.getenv("ESUPPLIER_ATTACHMENT_VISION") or "1"
).strip().lower() not in ("0", "false", "no")
#: Modelis, kas redz attēlus. Tukšs = tas pats, kas raksta vēstuli.
VISION_MODEL = os.getenv("ESUPPLIER_VISION_MODEL") or MODEL
#: Cik PDF lapu attēlojam un sūtām modelim, kad teksta slāņa nav. Rasējums
#: parasti ir viena lapa; pieci ir rezerve daudzlapu specifikācijai.
MAIL_ATTACHMENT_VISION_PAGES = int(os.getenv("ESUPPLIER_VISION_PAGES") or 5)
#: Cik lielu attēlu vispār sūtām. Lielāku pirms tam samazina `pypdfium2`
#: nevar — tas ir tikai PDF ceļš —, tāpēc telefona foto virs šī paliek cilvēkam.
MAIL_ATTACHMENT_VISION_MAX_BYTES = int(os.getenv("ESUPPLIER_VISION_MAX_BYTES") or 8_000_000)
#: Ar kādu izšķirtspēju attēlojam PDF lapu. 150 DPI ir robeža, aiz kuras
#: rasējuma izmēru atzīmes vairs nesalasās.
MAIL_ATTACHMENT_VISION_DPI = int(os.getenv("ESUPPLIER_VISION_DPI") or 150)
#: Cik zīmju paturam no viena attēla atšifrējuma.
MAIL_ATTACHMENT_VISION_CHARS = int(os.getenv("ESUPPLIER_VISION_CHARS") or 4000)
#: Cik attēlu vienā vēstulē atšifrējam. Katrs maksā savu izsaukumu, un ZIP ar
#: divdesmit skenējumiem citādi kļūtu par divdesmit izsaukumiem uz vienu
#: vēstuli. Pārējie paliek menedžerim ar tieši šo iemeslu.
MAIL_ATTACHMENT_VISION_MAX_FILES = int(os.getenv("ESUPPLIER_VISION_MAX_FILES") or 5)


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
