# e-supplier agent

Konsoles asistents, kas no klienta e-pasta sagatavo **gatavu piedāvājuma
vēstuli** pēc e-supplier.lv kataloga.

Menedžeris ielīmē klienta vēstuli, aģents sameklē preces vietējā kataloga kopijā
un atdod divas daļas: vēstuli, ko var nosūtīt neko nepārrakstot, un iekšējo
bloku ar to, kas jāizdara ar roku. Uz e-pastu aiziet tikai pirmā daļa.

```
klienta vēstule
      │
      ▼
  aģenta cikls  ──►  search_products / get_product / browse_category
  (Responses API)         │
      │                   ▼
      │            data/catalog.db  (SQLite + FTS5, 3568 produkti)
      ▼
  vēstule klientam  ───►  atbildes/piedavajums-*.html   (bildes, tabulas)
  ⚑ IEKŠĒJI         ───►  tikai konsolē, failā nekad
```

## Sākums

```bash
uv sync                       # atkarības
cp .env.example .env          # ieliec OPENAI_API_KEY
uv run sync                   # ievelk katalogu (~3600 produkti)
uv run chat                   # saruna
```

Bez `uv run sync` katalogs ir tukšs un `chat` atsakās startēt.

## Lietošana

Ievade ir **daudzrindu**: ielīmē visu vēstuli un pabeidz ar rindu `.` vai
Ctrl+D. Tukša rinda ievadi nebeidz — e-pastā tukšas rindas ir starp sveicienu,
tekstu un parakstu.

```
> Labdien! Vajadzīgs D veida pašlīmējošs EPDM profils, melns,
… apmēram 12 mm. Vajadzīgi 358 gab. Cena?
… .
```

Katra atbilde uzreiz nonāk `atbildes/` kā HTML ar bildēm un īstām tabulām —
konsolē no bildes paliek klikšķināma ikona, un tabulas Rich lauž pēc ekrāna
platuma. **Kopē no HTML faila, ne no termināļa.**

### Konsoles komandas

| Komanda | Ko dara |
|---|---|
| `/save [fails]` | saglabā vēstuli vēl vienā vietā un atver pārlūkā |
| `/tools` | pēdējā gājiena rīku izsaukumi ar parametriem un rezultātiem |
| `/verbose` | pilni rīku inputi un outputi |
| `/reset` | notīra sarunas vēsturi |
| `/sync` | pārsinhronizē katalogu |
| `/units` | pārrēķina mērvienības pēc `data/units.csv` labošanas |
| `/help`, `/exit` | — |

### Bez interaktīvās sesijas

```bash
uv run chat --ask "Cik maksā silikona gumija 2mm biezumā?"
cat vestule.txt | uv run chat          # viss ķermenis = viena ziņa
uv run chat --ask - < vestule.txt
```

## Atbildes formāts

Atbilde vienmēr ir divās daļās, starp tām rinda ar `---`:

1. **Vēstule klientam** — sveiciens, produktu tabula ar foto, cena bez PVN *un*
   ar PVN, daudzuma teikums par katru pozīciju, salīdzinājuma tabula, ja
   piedāvātais atšķiras no prasītā, un ne vairāk kā 4 precizējošie jautājumi.
2. **`⚑ IEKŠĒJI (klientam nesūtīt)`** — obligāts. Divas sadaļas:
   - `JĀIZDARA` — uzdevumi cilvēkam: rezervācija, piegādes termiņš, rēķins,
     mērvienības pārrēķins, iztrūkums pret atlikumu.
   - `NEAPSTIPRINĀTS` — ko no datiem nevarēja apstiprināt un kur meklēšana bija
     nedroša.

Aģents pats **nesola** rēķinu, rezervāciju, piegādes termiņu, apmaksas
nosacījumus, atlaidi vai transportu — tie ir menedžera lēmumi un iet iekšējā
blokā. Klientam tas skan "precizēs kolēģis".

Ja bloka nav vai atbilde tika apcirsta, konsole to pasaka atsevišķi. Bloka
trūkums menedžerim izskatās pēc "nekas nav jādara", un tas ir bīstamākais
klusējums, kāds šeit iespējams.

## Mērvienības

Veikala datos mērvienību **nav** — ne Store API, ne produkta lapā. Tā ir biznesa
patiesība, un tā dzīvo repozitorijā:

- `src/esupplier/catalog/units.py` — kategoriju likumi (pirmā sakritība uzvar).
  Profili, šļūtenes, blīvauklas un lentes → `m`; loksnes, tehniskā gumija un
  segumi → `m2`; pārējais → `gab`.
- `data/units.csv` — SKU izņēmumi ar roku. Uzvar pār likumiem.

```bash
$EDITOR data/units.csv
uv run chat            # tad konsolē:  /units
```

`/units` pārrēķina visu katalogu dažās sekundēs — pilna sinhronizācija tam nav
vajadzīga. Pašreizējais sadalījums: **636 m · 356 m² · 2576 gab.**

Kategorijas, par kurām nav skaidrības (PTFE stieņi, TBK/TBF plāksnes, filcs,
brīdinājuma lentes), likumos apzināti **nav** — tās paliek `gab`, un aģentam
liek prasīt apstiprinājumu iekšējā blokā, nevis klusi rēķināt gabalos. Saraksts
ir `data/units.csv` galvenē.

## Katalogs

```bash
uv run sync                     # pārvelk visu, ieraksta ar upsert
uv run sync --full              # vispirms iztukšo tabulu
uv run sync --source=scrape     # rezerves ceļš: sitemap + JSON-LD
```

Abos gadījumos tiek ievilkts viss katalogs; `--full` atšķiras ar to, ka noņem
arī ierakstus, kuru veikalā vairs nav.

Sinhronizācija normalizē to, ko veikals glabā kā brīvu tekstu: materiālu,
temperatūras diapazonu, DN (pārejām — abus), spiedienu, cietību, krāsu, tipa
kodu un pārtikas sertifikātu. Meklēšanai tiek uzbūvēts `aliases` lauks ar
klienta vārdiem (`pāreja`, `серый`, `DN100`), kuru nosaukumā nav.

Meklēšana ir FTS5 pilnteksts ar trim stingruma pakāpēm (`exact` → `stem` →
`any`) plus strukturētie filtri. Vaļīgākajā pakāpē **izmērs sver vairāk par
apzīmētāju**: skaitliskās grupas šķiro pirmās, un, ja neviens rezultāts nesedz
visus klienta nosauktos skaitļus, rīks to pasaka `notes` laukā.

## Testi un evals

```bash
uv run pytest                                    # 269 testi, bez API izsaukumiem
uv run evals                                     # visi gadījumi (maksā tokenus)
uv run evals --case vienkarsais                  # viens
uv run evals --compare green-7of7.json           # pret iepriekšēju rezultātu
```

`tests/` ir ātri un bez tīkla; daļa meklēšanas testu skrien pret īsto
`data/catalog.db` un tiek izlaisti, ja tā nav.

`evals/cases.jsonl` ir gadījumi ar substring pārbaudēm, rīku izsaukumu limitiem
un LLM-as-judge kritērijiem. Rezultāti krīt `evals/results/`, izejas kods 1, ja
kāds krita — var likt CI.

## Uzbūve

```
src/esupplier/
  cli.py              konsole, komandas, auto-saglabāšana
  config.py           vides mainīgie, limiti, PVN likme
  report.py           atbildes sadalīšana, HTML e-pastam, attēlu pārbaude
  agent/
    prompts.py        sistēmas prompts (nozares zināšanas + atbildes formāts)
    tools.py          rīku definīcijas un izpilde
    loop.py           modelis → rīki → modelis
  catalog/
    sync.py           Store API / scrape → SQLite
    normalize.py      brīvs teksts → tipizēti lauki
    units.py          mērvienības: kategoriju likumi + SKU izņēmumi
    search.py         FTS5 + filtri + atkāpšanās ceļi
    db.py             shēma, savienojums, upsert
    models.py         Product; attēlojumi modelim
  evals/              gadījumi, palaidējs, LLM tiesnesis
data/catalog.db       kataloga kopija (sinhronizēta, nav repozitorijā)
data/units.csv        mērvienību izņēmumi (repozitorijā)
atbildes/*.html       sagatavotās vēstules
```

## Vides mainīgie

| Mainīgais | Noklusējums | Ko dara |
|---|---|---|
| `OPENAI_API_KEY` | — | obligāts |
| `ESUPPLIER_MODEL` | `gpt-5.6-luna` | modelis |
| `ESUPPLIER_EFFORT` | `medium` | domāšanas dziļums; ar `minimal` retāk ķeras pie rīkiem |
| `ESUPPLIER_DB` | `data/catalog.db` | kataloga ceļš |
| `ESUPPLIER_ANSWERS` | `atbildes/` | kur krīt HTML |

## Zināmās robežas

- **D profilu izmēru secība nav apstiprināta.** Divu skaitļu nosaukumā
  ("10×13 mm") nav garantēts, kurš ir platums un kurš augstums, un aģents to
  pieņem. U profiliem promptā ir atsevišķa nomenklatūras sadaļa; D ģimenei tādas
  vēl nav.
- **Divi eval gadījumi krīt uz rīku izsaukumu limitu** (`camlock-ar-4x6`,
  `din-11851-piens`) — daudzpozīciju pieprasījumam `MAX_TOOL_ITERATIONS = 8` ir
  uz robežas.
- `tukss-rezultats` krīt uz pretrunu par toni: tiesnesis grib vēstulē skaidru
  "neatradām", prompts vēstulē vārdu "neatradu" aizliedz. Jāizšķir, kurš ir
  pareizais.
- Cenas `config.PRICING` sarakstā ir tikai Anthropic modeļiem; citiem eval izvadē
  izmaksas rāda `n/a`, nevis izdomātu skaitli.
