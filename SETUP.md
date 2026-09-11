# Uzstādīšana

No tukšas mašīnas līdz pirmajai sagatavotajai vēstulei. Ap 5 minūtēm, no kurām
1,5 aiziet kataloga ievilkšanai.

## 1. Prasības

| Kas | Versija | Pārbaude |
|---|---|---|
| Python | 3.12+ | `python3 --version` |
| [uv](https://docs.astral.sh/uv/) | 0.11+ | `uv --version` |
| git | jebkura | `git --version` |
| Claude Code | jebkura | `claude --version` |

Noklusējuma dzinējs ir `claude`: aģents iet caur Claude Agent SDK un maksā no
abonementa, tāpēc API atslēga nav vajadzīga. Jābūt tikai uzstādītam un
pieteiktam Claude Code.

Ja gribi maksāt ar API atslēgu, nevis abonementu, izvēlies vienu no diviem:

```
ESUPPLIER_ENGINE=anthropic      # Claude API
ANTHROPIC_API_KEY=sk-ant-...    # console.anthropic.com/settings/keys
```

```
ESUPPLIER_ENGINE=openai         # ChatGPT
OPENAI_API_KEY=sk-proj-...      # platform.openai.com/api-keys
```

Uz servera un cron darbam der tieši šie divi: Claude Code tur nav jāuzstāda.

Python versiju `uv` uzstādīs pats, ja tās nav — `.python-version` prasa 3.12.

Ja `uv` nav:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. Kods

```bash
git clone git@git.trialine.lv:web/ai-assistance.git e-supplier
cd e-supplier
```

Repozitorijs ir **privāts**, tāpēc vajag SSH atslēgu, kas piesaistīta kontam ar
piekļuvi. Pārbaude:

```bash
ssh -T git@git.trialine.lv     # atbild ar lietotājvārdu, ja atslēga der
```

## 3. Atkarības

```bash
uv sync
```

Izveido `.venv/` un ievelk `httpx`, `openai`, `markdown-it-py`, `rich`,
`python-dotenv`, `pytest` un pielikumu lasītājus (`pypdf`, `pypdfium2`,
`xlrd`, `pillow`). Versijas nāk no `uv.lock` — nekas netiek
atjaunināts pats no sevis.

## 4. Vides mainīgie

```bash
cp .env.example .env
$EDITOR .env
```

Ar noklusējuma dzinēju (`claude`) obligātu mainīgo **nav**: modelis nāk no
abonementa. Abiem API dzinējiem obligāta ir atslēga:

```
ESUPPLIER_ENGINE=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

```
ESUPPLIER_ENGINE=openai
OPENAI_API_KEY=sk-proj-...
```

Pārējie ir neobligāti; noklusējumi ir `src/esupplier/config.py`.

| Mainīgais | Noklusējums | Kad aiztikt |
|---|---|---|
| `ESUPPLIER_ENGINE` | `claude` | `anthropic` vai `openai`, ja gribi maksāt ar atslēgu |
| `ANTHROPIC_API_KEY` | — | obligāts `anthropic` dzinējam |
| `ESUPPLIER_CLAUDE_MODEL` | `claude-opus-5` | cits Claude modelis |
| `ESUPPLIER_MODEL` | `gpt-5.6-luna` | `openai` dzinējam, ja kontam šis nav pieejams |
| `ESUPPLIER_EFFORT` | `medium` | `minimal` ir lētāk, bet retāk ķeras pie rīkiem |
| `ESUPPLIER_DB` | `data/catalog.db` | cits kataloga ceļš |
| `ESUPPLIER_ANSWERS` | `atbildes/` | kur krīt sagatavotās vēstules |
| `ESUPPLIER_IMAP_IGNORE` | — | adreses/domēni, uz kuriem neatbildam nekad, ar komatu |
| `ESUPPLIER_ATTACHMENT_CHARS` | `4000` | cik zīmju no viena pielikuma aiziet modelim |
| `ESUPPLIER_ATTACHMENTS_CHARS` | `12000` | cik zīmju kopā no visiem pielikumiem |
| `ESUPPLIER_ATTACHMENT_VISION` | `1` | `0` izslēdz skenētu rasējumu atšifrēšanu |
| `ESUPPLIER_VISION_MODEL` | = `ESUPPLIER_MODEL` | modelis, kas prot lasīt attēlus |

Pielikumu saturs un klienta vēstule modelim aiziet iežogoti, un artikulus
ar cenām pirms melnraksta salīdzina ar rīku atbildēm. Abu apraksts ir
DOCS.md sadaļā "Svešs teksts un izcelsme".

**Skenēts rasējums un foto** teksta slāni nesatur; tos izlasa modelis, kurš
attēlu redz, un katrs tāds pielikums maksā vienu papildu izsaukumu. Modelim
`ESUPPLIER_VISION_MODEL` mainīgajā jāprot lasīt attēlus. Ja tāda nav vai
izmaksas pilotā negribas, liec `ESUPPLIER_ATTACHMENT_VISION=0` — tad skenētie
rasējumi atkal paliek menedžerim ar piezīmi "jāatver ar roku".

**Vecais `.doc` un `.ppt`** prasa LibreOffice. Ja `soffice` uz mašīnas nav,
viss pārējais strādā tāpat, tikai šie divi formāti paliek cilvēkam:

```bash
soffice --version      # ja atbild, nekas nav jādara
```

Ja gribi, lai aģents lasa pastu pats (`uv run mail`, skat. 6.a sadaļu), vajag
vēl trīs:

```
ESUPPLIER_IMAP_HOST=lima.trialine.lv
ESUPPLIER_IMAP_USER=ai.0001@trialine.lv
ESUPPLIER_IMAP_PASSWORD=...
```

Pārējie pastkastītes mainīgie (`_PORT`, `_SSL`, `_FOLDER`, `_DRAFTS`,
`ESUPPLIER_MAIL_BATCH`, `ESUPPLIER_MAIL_POLL`) ir `.env.example` un vajadzīgi
tikai tad, ja serveris atšķiras no noklusējuma.

**Par modeli:** aģents iet caur OpenAI **Responses API** ar `reasoning.effort`,
nevis Chat Completions. Modelim jābūt tādam, kas to atbalsta, un tam jābūt
pieejamam Tavam kontam. Ja nav — skat. 7. sadaļu.

`.env` ir `.gitignore` sarakstā un repozitorijā nenonāk nekad.

## 5. Katalogs

```bash
uv run sync
```

Ievelk visu veikala katalogu lokālā SQLite failā. Gaidāmā izvade:

```
Kategorijas: 300
Store API: 3568 produkti, 36 lapas
  lapa 1/36
  ...
  lapa 36/36
Mērvienības: gab=2576, m=636, m2=356
Gatavs: 3568 produkti, 83.0s
```

Skaitļi mainās līdz ar katalogu; pārbaudāmas ir pēdējās divas rindas:
produktu skaits nav nulle, un mērvienības sadalījušās trīs grupās. Ja `m` un
`m2` ir 0, kaut kas nav kārtībā ar `data/units.csv` vai kategoriju likumiem —
skat. DOCS.md sadaļu "Mērvienības".

Rezultāts ir ~14 MB fails `data/catalog.db`. Tas repozitorijā nenonāk; katrai
mašīnai savs.

Ja Store API neatbild:

```bash
uv run sync --source=scrape     # rezerves ceļš: sitemap + JSON-LD, lēnāk
```

## 6. Pārbaude

```bash
uv run pytest
```

Gaidāms `466 passed` zem divām sekundēm. Testi neiet tīklā un nemaksā
tokenus. Daļa meklēšanas testu prasa `data/catalog.db` — bez tā tie tiek
izlaisti, ne kritīs.

Tad pirmais īstais jautājums:

```bash
uv run chat --ask "Cik maksā silikona gumija 2mm biezumā?"
```

Ja atbildē ir cena ar PVN un bez, un apakšā rinda `Vēstule ar bildēm:` ar ceļu
uz HTML failu — uzstādīšana ir pabeigta. Atver to failu; tur ir bildes un
tabulas, ko konsole nerāda.

Interaktīvi:

```bash
uv run chat
```

Ievade ir daudzrindu — ielīmē visu klienta vēstuli un pabeidz ar rindu `.`
(vai Ctrl+D). `/help` rāda komandas.

## 6.a Pastkastīte

Šis solis ir neobligāts. Bez tā aģents strādā konsolē; ar to viņš lasa vēstules
pats un atstāj atbildes kā melnrakstus.

Vispirms pārbaudi savienojumu un to, vai melnrakstu mape ir atrasta:

```bash
uv run mail --check
```

Gaidāmā izvade:

```
Savienojums ir. INBOX: 12 vēstules
Neapstrādātas: 3
Mapes: Sent, Junk, Trash, Drafts, INBOX
Melnraksti -> Drafts
```

`--check` modeli neizsauc un tokenus nemaksā. Ja kaut kas nav kārtībā, kļūdas
tekstā ir arī tas, ko ar to darīt.

**Gmail.** Mainās trīs rindas, koda izmaiņas nav vajadzīgas:

```
ESUPPLIER_IMAP_HOST=imap.gmail.com
ESUPPLIER_IMAP_USER=vards@gmail.com
ESUPPLIER_IMAP_PASSWORD=<App Password>
```

Parastā konta parole neder: Google to IMAP pieslēgumiem nepieņem kopš 2022.
gada. Kontam jāieslēdz divpakāpju verifikācija, jāizveido App Password un
Gmail iestatījumos jāieslēdz IMAP. Sīkāk — DOCS.md sadaļa "Gmail".

Kad `--check` ir zaļš, var pārbaudīt visu ceļu:

```bash
uv run mail --dry-run
```

Gaidāmā pirmā rinda:

```
INBOX: 12 vēstules, 3 neapstrādātas · dry-run
```

`--dry-run` atbildes sagatavo un saglabā mapē `atbildes/`, bet pastkastītē
neko neraksta un žurnālā neko neieraksta — to var palaist atkārtoti.

Kad izskatās pareizi:

```bash
uv run mail          # paliek strādāt un seko pastkastītei
uv run mail --once   # viens gājiens un ārā
uv run mail --log    # ko izdarīja
```

Bez argumentiem aģents neapstājas: ik pēc minūtes pārbauda pastkastīti un
klusē, kamēr nekas nav atnācis. Apstādina ar Ctrl+C. `--once` ir tas pats
gājiens vienu reizi, un tas ir tas, ko liek cron.

Melnrakstu atrodi pastkastītes mapē `Drafts`. Tur ir **tikai vēstule
klientam**, sūtāma bez labošanas. Aģents pats neko nesūta: SMTP šajā projektā
nav.

Uzdevumus menedžerim (rezervācija, termiņš, rēķins, mērvienību pārrēķins)
izdrukā konsole, un tie paliek failā blakus vēstulei:

```
atbildes/piedavajums-20260904-081712.html          <- vēstule klientam
atbildes/piedavajums-20260904-081712-IEKSEJI.txt   <- kas jāizdara ar roku
```

**Izlasi `-IEKSEJI.txt` pirms sūti melnrakstu.**

## 7. Kad kaut kas nestrādā

**`Neatradu Claude Code.`** Noklusējuma dzinējs iet caur Claude Agent SDK, un
tam vajag uzstādītu un pieteiktu Claude Code (`claude --version`). Vai arī liec
`.env` failā `ESUPPLIER_ENGINE=anthropic` un Claude API atslēgu (vai
`ESUPPLIER_ENGINE=openai` un OpenAI atslēgu).

**`Trūkst OPENAI_API_KEY. Nokopē .env.example uz .env un ieliec atslēgu.`**
`ESUPPLIER_ENGINE=openai`, bet `.env` nav vai atslēga tukša. Fails jābūt
projekta saknē, ne `src/`.

**`Katalogs tukšs. Palaid: uv run sync`**
Sinhronizācija nav palaista vai `ESUPPLIER_DB` rāda uz citu failu.

**`Neizdevās sazināties ar modeli: Error code: 400 ... 'code': 'model_not_found'`**
Kontam nav piekļuves modelim. Pārbaudi, kas ir pieejams:

```bash
uv run python -c "from openai import OpenAI; print([m.id for m in OpenAI().models.list()])"
```

un ieliec derīgu vārdu `ESUPPLIER_MODEL` mainīgajā.

**Sinhronizācija iet lēni.** `config.SITE_URL` ir `https://e-supplier.lv`, bet
veikals ir pārcelts uz `etms.lv` un atbild ar 301. `httpx` klients seko
pāradresācijai, tāpēc viss strādā, tikai katrs pieprasījums maksā papildu
apriti. Kad būs skaidrs, kurš domēns ir galvenais, `SITE_URL` var pārlikt.

**`uv run chat` prasa apstiprināt Python versiju.** `uv python install 3.12`.

**`Trūkst pastkastītes datu.`** `.env` nav `ESUPPLIER_IMAP_HOST`, `_USER` vai
`_PASSWORD`.

**Iekšējā blokā stāv "atšifrēt neizdevās".** Modelis `ESUPPLIER_VISION_MODEL`
mainīgajā attēlus lasīt neprot vai izsaukums nokrita. Vēstule tiek sagatavota
tāpat, tikai skenētais rasējums paliek cilvēkam. Pārbaudi modeļa nosaukumu vai
izslēdz atšifrēšanu ar `ESUPPLIER_ATTACHMENT_VISION=0`.

**Iekšējā blokā stāv "jāatver ar roku (LibreOffice uz servera nav)".** Vecais
`.doc` vai `.ppt`. Uzstādi LibreOffice vai norādi ceļu `ESUPPLIER_SOFFICE`
mainīgajā.

**`Application-specific password required`** (Gmail). Parastā konta parole.
Ieslēdz divpakāpju verifikāciju, izveido App Password un ieliec to
`ESUPPLIER_IMAP_PASSWORD` vietā.

**`IMAP access is disabled`** (Gmail). Ieslēdz IMAP: Settings -> Forwarding and
POP/IMAP. Workspace domēnā to var būt aizliedzis administrators.

**`Neatradu melnrakstu mapi.`** Serveris nedod `\Drafts` karogu un mape saucas
citādi. Kļūdas tekstā ir visu mapju saraksts — izvēlies pareizo un ieliec to
`ESUPPLIER_IMAP_DRAFTS`.

**Melnraksti neparādās, bet `--log` rāda `MELNRAKSTS`.** Skaties citā mapē:
pirmā rinda pēc palaišanas saka, kur tie iet (`melnraksti -> Drafts`).

**Uz vienu vēstuli divi melnraksti.** Tā nedrīkst notikt: atslēga ir
`Message-ID`. Ja tas atkārtojas, vēstulei `Message-ID` galvenes nav vispār, un
atslēga ir mapes UID — tas mainās, pārvietojot vēstuli citā mapē.

**`--log` rāda `KRITA` ar "atbilde tika apcirsta".** Modelis netika līdz
atbildes beigām. Melnraksts APZINĀTI netiek taisīts: apcirsta atbilde izskatās
pēc pilnas vēstules bez uzdevumiem menedžerim. Atbildi ar roku konsolē vai
palaid `uv run mail --retry-failed`.

## 8. Ikdienas darbs

```bash
uv run chat                    # saruna
uv run mail                    # pastkastīte: vēstules -> melnraksti
uv run sync                    # katalogs (atlikumi un cenas mainās)
uv run pytest                  # pēc koda izmaiņām
uv run evals                   # pēc prompta izmaiņām — maksā tokenus
```

`uv run sync` ir vērts palaist katru rītu: cenas un noliktavas atlikumi
mainās, un aģents runā tikai to, kas ir lokālajā kopijā.

Ja labo `data/units.csv`, pilna sinhronizācija nav vajadzīga — konsolē pietiek
ar `/units`.
