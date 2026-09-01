# Uzstādīšana

No tukšas mašīnas līdz pirmajai sagatavotajai vēstulei. Ap 5 minūtēm, no kurām
1,5 aiziet kataloga ievilkšanai.

## 1. Prasības

| Kas | Versija | Pārbaude |
|---|---|---|
| Python | 3.12+ | `python3 --version` |
| [uv](https://docs.astral.sh/uv/) | 0.11+ | `uv --version` |
| git | jebkura | `git --version` |
| OpenAI API atslēga | — | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |

Python versiju `uv` uzstādīs pats, ja tās nav — `.python-version` prasa 3.12.

Ja `uv` nav:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. Kods

```bash
git clone git@github.com:Tormakss/supplier.git e-supplier
cd e-supplier
```

Repozitorijs ir **privāts**, tāpēc vajag SSH atslēgu, kas piesaistīta kontam ar
piekļuvi. Pārbaude:

```bash
ssh -T git@github.com          # "Hi <lietotājs>! You've successfully authenticated"
```

## 3. Atkarības

```bash
uv sync
```

Izveido `.venv/` un ievelk `httpx`, `openai`, `markdown-it-py`, `rich`,
`python-dotenv` un `pytest`. Versijas nāk no `uv.lock` — nekas netiek
atjaunināts pats no sevis.

## 4. Vides mainīgie

```bash
cp .env.example .env
$EDITOR .env
```

Obligāts ir viens:

```
OPENAI_API_KEY=sk-proj-...
```

Pārējie ir neobligāti; noklusējumi ir `src/esupplier/config.py`.

| Mainīgais | Noklusējums | Kad aiztikt |
|---|---|---|
| `ESUPPLIER_MODEL` | `gpt-5.6-luna` | ja kontam šis modelis nav pieejams |
| `ESUPPLIER_EFFORT` | `medium` | `minimal` ir lētāk, bet retāk ķeras pie rīkiem |
| `ESUPPLIER_DB` | `data/catalog.db` | cits kataloga ceļš |
| `ESUPPLIER_ANSWERS` | `atbildes/` | kur krīt sagatavotās vēstules |

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
skat. README sadaļu "Mērvienības".

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

Gaidāms `269 passed` zem sekundes. Testi neiet tīklā un nemaksā tokenus. Daļa
meklēšanas testu prasa `data/catalog.db` — bez tā tie tiek izlaisti, ne kritīs.

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

## 7. Kad kaut kas nestrādā

**`Trūkst OPENAI_API_KEY. Nokopē .env.example uz .env un ieliec atslēgu.`**
`.env` nav vai atslēga tukša. Fails jābūt projekta saknē, ne `src/`.

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

## 8. Ikdienas darbs

```bash
uv run chat                    # saruna
uv run sync                    # katalogs (atlikumi un cenas mainās)
uv run pytest                  # pēc koda izmaiņām
uv run evals                   # pēc prompta izmaiņām — maksā tokenus
```

`uv run sync` ir vērts palaist katru rītu: cenas un noliktavas atlikumi
mainās, un aģents runā tikai to, kas ir lokālajā kopijā.

Ja labo `data/units.csv`, pilna sinhronizācija nav vajadzīga — konsolē pietiek
ar `/units`.
