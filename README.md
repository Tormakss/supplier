# e-supplier agent

Konsoles asistents, kas no klienta e-pasta sagatavo **gatavu piedāvājuma
vēstuli** pēc e-supplier.lv kataloga.

Menedžeris ielīmē klienta vēstuli — vai aģents to izlasa pastkastītē pats —
un saņem divas daļas: vēstuli, ko var nosūtīt neko nepārrakstot, un iekšējo
bloku ar to, kas jāizdara ar roku. Klientam aiziet tikai pirmā daļa, un tikai
tad, kad cilvēks nospiež "Sūtīt".

```
klienta vēstule  ──►  konsole (ielīmē)   vai   IMAP pastkastīte
      │
      ▼
  aģenta cikls  ──►  search_products / get_product / browse_category
  (Responses API)         │
      │                   ▼
      │            data/catalog.db  (SQLite + FTS5, 3568 produkti)
      ▼
  vēstule klientam  ───►  atbildes/piedavajums-*.html   (bildes, tabulas)
                    ───►  melnraksts pastkastītes mapē Drafts
  ⚑ IEKŠĒJI         ───►  konsolē un atbildes/*-IEKSEJI.txt; melnrakstā nekad
```

## Sākums

```bash
uv sync                       # atkarības
cp .env.example .env          # ieliec OPENAI_API_KEY
uv run sync                   # ievelk katalogu (~3600 produkti, ~85 s)
uv run chat                   # saruna
```

Bez `uv run sync` katalogs ir tukšs un `chat` atsakās startēt.

Pilna uzstādīšana no tukšas mašīnas, pārbaudes soļi un kļūdu ceļi —
**[SETUP.md](SETUP.md)**.

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

## Pastkastīte

Aģents var lasīt klientu vēstules pats un atstāt atbildi kā **melnrakstu** tajā
pašā pastkastītē. SMTP šeit nav un nebūs: vēstule klientam aiziet tikai tad, kad
cilvēks melnrakstu atver, izlasa iekšējās piezīmes, izdzēš tās un nospiež
"Sūtīt".

```bash
uv run mail                 # seko pastkastītei, līdz nospiež Ctrl+C
uv run mail --once          # viens gājiens un ārā (cron, pārbaudes)
uv run mail --dry-run       # sagatavo atbildes, bet pastkastītē neko neraksta
uv run mail --retry-failed  # atkārto tās, kas iepriekš krita
uv run mail --log           # ko jau esam apstrādājuši
```

Palaists bez argumentiem, aģents **paliek strādāt** un ik pēc minūtes pārbauda,
vai nav atnākušas jaunas vēstules. Kamēr nekas nav atnācis, tas klusē: ekrāna
apakšā ir viena rinda ar pēdējās pārbaudes laiku, un tā pārrakstās pati.

```
Sekoju pastkastītei, pārbaude ik pēc 60s. Ctrl+C, lai apstātos.
INBOX: 42 vēstules, 0 neapstrādātas · melnraksti -> Drafts
⠹ Gaidu jaunas vēstules · pēdējā pārbaude 09:14:02 · 3 melnraksti, 7 izlaisti, 0 krita
```

Ritmu maina `--interval` vai `ESUPPLIER_MAIL_POLL`. Ja serveris nokrīt vai
pazūd tīkls, aģents neapstājas: pauze dubultojas līdz desmit minūtēm un
atgriežas parastajā ritmā, tiklīdz savienojums atjaunojas.

Pieeja nāk no `.env` (`ESUPPLIER_IMAP_HOST`, `_USER`, `_PASSWORD`). Melnrakstu
mapi meklējam pēc servera `\Drafts` karoga; ja serveris to nedod, ejam pēc
nosaukuma (`Drafts`, `INBOX.Drafts`, `Melnraksti`). Var norādīt ar roku:
`ESUPPLIER_IMAP_DRAFTS`.

### Ko aģents ar vēstuli izdara

| Solis | Kas notiek |
|---|---|
| Citāti un paraksts | nogriezti — citādi pārsūtītā sarakstē minētā vecā prece nonāk jaunajā piedāvājumā |
| Pielikumi | saturu nelasām, bet nosaukumi aiziet modelim un iekšējā blokā |
| Jaunumi, auto-atbildes, `no-reply` | izlaisti pirms modeļa, ne pēc |
| Adresāts | `Reply-To`, ja tāds ir; citādi `From` |
| Ķēde | `In-Reply-To` un `References`, lai atbilde nesadala sarunu divās vietās |

### Kur paliek iekšējais bloks

**Melnrakstā tā nav.** Melnraksts ir domāts nosūtīšanai bez labošanas, un
bloks, kas pirms tam jāizdzēš ar roku, agri vai vēlu paliek neizdzēsts.

Uzdevumi menedžerim aiziet divās vietās: konsolē gājiena laikā un failā
`atbildes/piedavajums-*-IEKSEJI.txt` blakus vēstulei. Fails ir `.txt`, ne
`.html`, tieši tāpēc, ka `.html` failu menedžeris atver un kopē.

Turpat nonāk brīdinājumi, ko interaktīvajā režīmā izdrukā konsole: izmestās
bildes, iekšējās adreses noplūde, sasniegts rīku limits, neizlasīti pielikumi.
Ilgā sekošanā konsolē neviens neskatās.

**Apcirsta atbilde melnrakstā nenonāk vispār.** Iekšējais bloks ir pēdējais, ko
modelis raksta, tāpēc apcirsta atbilde izskatās pēc pilnas vēstules, kurai nav
ko piebilst. Tāda vēstule paliek pastkastītē neapstrādāta, un `--log` rāda
`KRITA`.

### Divreiz uz vienu vēstuli neatbildam

Apstrādātās vēstules glabājas `processed_messages` tabulā (atslēga —
`Message-ID`) un papildus tiek iezīmētas ar IMAP atslēgvārdu `$AiDrafted`.
Atslēgvārds vien nepietiktu: `\Seen` pazūd, tiklīdz cilvēks pastkastīti atver
savā pasta klientā.

Kritušās vēstules atslēgvārdu **nedabū** — cilvēks tās pastkastītē redz kā
neapstrādātas, un `--retry-failed` tās atgriež ciklā.

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
uv run pytest                                    # 320 testi, bez API izsaukumiem
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
  mail/
    run.py            pastkastītes gājiens: vēstule → melnraksts
    imap.py           savienojums, mapes, APPEND
    message.py        MIME → teksts; citāti, paraksti, filtri
    draft.py          vēstule → MIME melnraksts ar ķēdes galvenēm
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
| `ESUPPLIER_IMAP_HOST` | — | pastkastītes serveris (`uv run mail`) |
| `ESUPPLIER_IMAP_USER` | — | lietotājvārds |
| `ESUPPLIER_IMAP_PASSWORD` | — | parole |
| `ESUPPLIER_IMAP_PORT` | `993` | IMAPS; ar `_SSL=0` STARTTLS uz 143 |
| `ESUPPLIER_IMAP_FOLDER` | `INBOX` | ko lasīt |
| `ESUPPLIER_IMAP_DRAFTS` | — | melnrakstu mape; tukšs = atrodam paši |
| `ESUPPLIER_MAIL_BATCH` | `10` | cik vēstules vienā gājienā |
| `ESUPPLIER_MAIL_POLL` | `60` | pauze sekundēs starp pārbaudēm |

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
