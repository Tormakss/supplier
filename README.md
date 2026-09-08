# e-supplier agent

Konsoles asistents, kas no klienta e-pasta sagatavo **gatavu piedāvājuma
vēstuli** pēc e-supplier.lv kataloga.

Menedžeris ielīmē klienta vēstuli — vai aģents to izlasa pastkastītē pats —
un saņem divas daļas: vēstuli, ko var nosūtīt neko nepārrakstot, un iekšējo
bloku ar to, kas jāizdara ar roku. Klientam aiziet tikai pirmā daļa, un tikai
tad, kad cilvēks nospiež "Sūtīt".

```
klienta vēstule  ──►  konsole (ielīmē)  ·  Claude Code  ·  IMAP pastkastīte
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

## Claude Code

Katalogu var lietot arī no Claude Code sesijas, bez OpenAI atslēgas: Claude Code
iet uz abonementa. Repozitorijā ir divi faili, kas to savieno.

`.mcp.json` pieslēdz MCP serveri ar tiem PAŠIEM četriem rīkiem, ko lieto
`uv run chat` — `search_products`, `get_product`, `browse_category`,
`list_categories`. Rīku definīcijas nāk no `agent/tools.py`; otras kopijas nav.

`.claude/skills/piedavajums/` ir prasme ar uzvedības noteikumiem: cenas abās PVN
pusēs, mērvienība no datiem, ko nedrīkst apsolīt. Tā ir sistēmas prompta
**atvasinājums**, ne otra kopija:

```bash
uv run skill        # pārraksta prasmi no src/esupplier/agent/prompts.py
```

Labo promptu un palaid `uv run skill`. Ja aizmirsti, `uv run pytest` krīt —
citādi konsole un Claude Code sesija kļūtu par diviem dažādiem aģentiem ar vienu
nosaukumu, un tas neizskatītos pēc kļūdas, tikai pēc "šodien atbild savādāk".

Serveri ar roku palaist nevajag; to dara Claude Code. Pārbaudei:

```bash
uv run mcp-katalogs      # stdio; klusē, līdz klients kaut ko pajautā
```

**Pastkastītes dēmons uz abonementa neiet.** `uv run mail` visu diennakti pats
aptaujā `INBOX`, un tā nav interaktīva sesija. Anthropic Agent SDK dokumentācija
to pasaka tieši: bez iepriekšējas atļaujas claude.ai pieteikšanos un tās limitus
trešo pušu produktos lietot nedrīkst. Dēmons paliek uz sava ceļa ar savu atslēgu.

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
| Pielikumi | PDF, Word, Excel, PowerPoint, RTF, CSV un DXF tiek atvērti, arhīvi izpakoti; teksts aiziet modelim atsevišķā, iežogotā blokā |
| Skenēti rasējumi un foto | attēloti un atšifrēti ar modeli; atšifrējums aiziet atzīmēts kā atšifrējums |
| Pielikumi, ko neizlasa | `.dwg`, 3D modeļi, parolēti faili — nosaukums un iemesls aiziet modelim un iekšējā blokā |
| Jaunumi, auto-atbildes, `no-reply` | izlaisti pirms modeļa, ne pēc |
| Adresāts | `Reply-To`, ja tāds ir; citādi `From` |
| Ķēde | `In-Reply-To` un `References`, lai atbilde nesadala sarunu divās vietās |
| Svešs teksts | vēstule un pielikumi aiziet rāmī kā dati (skat. [Svešs teksts un izcelsme](#svešs-teksts-un-izcelsme)) |
| Artikuli un cenas | salīdzināti ar rīku atbildēm; kas nesakrīt, aiziet menedžerim |

### Ko aģents izlasa pielikumā

| Formāts | Kas notiek |
|---|---|
| `.pdf` | teksts no pirmajām 20 lapām; PDF ar paroli mēģinām atvērt ar tukšu paroli |
| `.docx`, `.doc` | rindkopas un tabulas (šūnas atdalītas ar `\|`); vecais `.doc` — caur LibreOffice |
| `.xlsx`, `.xlsm`, `.xls` | rindas pa lapām, ar lapas nosaukumu |
| `.pptx`, `.ppt` | teksts pa slaidiem; vecais `.ppt` — caur LibreOffice |
| `.rtf` | teksts bez fontu tabulas un stiliem |
| `.txt`, `.csv`, `.md`, `.xml`, `.json` | teksts; bez kodējuma galvenē mēģinām `utf-8`, tad `cp1257` |
| `.html` | teksts bez iezīmēm |
| `.dxf` | uzraksti un izmēru atzīmes no rasējuma; ģeometrija ne |
| `.zip`, `.7z` | izpakots; katrs fails iekšā iet pa šo pašu tabulu |
| attēli, skenēts PDF | **atšifrēti ar modeli** — skat. zemāk |
| `.dwg`, 3D modeļi, `.rar` bez `unrar` | **netiek lasīti** — nosaukums un iemesls aiziet iekšējā blokā |

Arhīvs pats par sevi pieprasījums nav, tāpēc tas pazūd un tā vietā parādās tas,
kas bija iekšā: `rasejumi.zip → skice-3.pdf`. Salikto vārdu menedžeris redz
iekšējā blokā, lai zinātu, kurā failā meklēt.

**Vecais `.doc` un `.ppt` prasa LibreOffice.** Cita ceļa tiem nav. Ja `soffice`
uz servera nav, tādi pielikumi paliek cilvēkam, un iekšējā blokā stāv tieši tas
iemesls, ne "nezināms formāts".

### Skenēts rasējums

**Skenēts PDF ir bilde, un bildē teksta nav.** Vienīgais, kas to izlasa, ir
modelis, kurš attēlu redz. PDF lapu bez teksta slāņa aģents attēlo pats
(`pypdfium2`, 150 DPI) un kopā ar foto, skenējumiem un ekrānšāviņiem sūta uz
atsevišķu izsaukumu, kura viss uzdevums ir **pārrakstīt**, ne interpretēt.

Atšifrējums nav oriģināls, un tā visur arī tiek uzskatīts:

- modelim tas aiziet ar lauku `avots`, un sistēmas prompts liek tādu izmēru
  neuzskatīt par apstiprinātu;
- menedžeris par katru tādu pielikumu saņem atsevišķu, stiprāku brīdinājumu
  nekā par nolasītu Excel faili — kļūda atšifrējumā ir tieši izmērā;
- ja modelis atbild `NAV_TEKSTA` vai izsaukums krīt, pielikums paliek
  neizlasīts ar godīgu iemeslu. Tukšs izvilkums, kas izliktos par izlasītu,
  būtu sliktāks par neizlasīšanu vispār: modelis klusētu par pusi pieprasījuma.

Katrs attēls maksā vienu papildu izsaukumu, un vienā vēstulē to ir ne vairāk
kā pieci: ZIP ar divdesmit skenējumiem citādi kļūtu par divdesmit izsaukumiem
uz vienu vēstuli. Pārējie paliek menedžerim ar tieši šo iemeslu. Visu ceļu
izslēdz `ESUPPLIER_ATTACHMENT_VISION=0`.

Pielikuma teksts modelim iet **atsevišķi no vēstules ķermeņa**, savā rāmī.
Citādi specifikācijas rinda "EPDM 12 mm — 358 gab." lasās kā paša klienta
rakstīts teikums, arī tad, kad tā bija vecas tāmes aile.

Tāpēc, ka pieprasījums var būt tikai pielikumā, vēstule ar īsu ķermeni
("Sk. pielikumā") vairs netiek izlaista kā tukša, ja pielikumā ir teksts — vai
attēls, ko vēl var atšifrēt.

### Kur paliek iekšējais bloks

**Melnrakstā tā nav.** Melnraksts ir domāts nosūtīšanai bez labošanas, un
bloks, kas pirms tam jāizdzēš ar roku, agri vai vēlu paliek neizdzēsts.

Uzdevumi menedžerim aiziet divās vietās: konsolē gājiena laikā un failā
`atbildes/piedavajums-*-IEKSEJI.txt` blakus vēstulei. Fails ir `.txt`, ne
`.html`, tieši tāpēc, ka `.html` failu menedžeris atver un kopē.

Turpat nonāk brīdinājumi, ko interaktīvajā režīmā izdrukā konsole: izmestās
bildes, iekšējās adreses noplūde, sasniegts rīku limits, neizlasīti pielikumi
un tie, kuru saturs modelim aizgāja automātiski.
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

## Svešs teksts un izcelsme

Klienta vēstule un pielikuma saturs ir teksts, ko rakstīja kāds cits. PDF failā
var būt "aizmirsti iepriekšējos norādījumus", un faila nosaukumu izvēlas
sūtītājs. Divas pārbaudes to notur.

**Iežogošana.** Viss svešais teksts modelim aiziet rāmī ar nemainīgu birku:
vēstule `<klienta_vestule>`, pielikumi `<klienta_pielikumi>` kā JSON. Pirms
rāmja tas tiek attīrīts — nost neredzamās un vadības rakstzīmes, viltotie
gājienu marķieri (`Human:` aiz tukšas rindas) un rīku izsaukumu birkas. Birka ir
avota literālis, nekad no ienākošiem datiem, tāpēc svešs teksts robežu atkārtot
nevar. Sistēmas prompts par abiem rāmjiem pasaka, ka tie ir dati, ne norādījumi.

Pielikumi iet kā JSON, ne kā mūsu pašu rakstīti atdalītāji. Ar rindu
`--- PIELIKUMS x ---` pielikums varētu tādu rindu uzrakstīt pats un izlikties
par nākamo failu; JSON pēdiņās tas ir tikai teksts.

Pašu iežogošanu dara `src/esupplier/vendor/fencing.py` — Anthropic
[`commerce-agents`](https://github.com/anthropics/commerce-agents) modulis,
ievests nemainīts ar Apache 2.0 licenci. Izcelsme, revīzija un iemesls —
`src/esupplier/vendor/README.md`; kopsavilkums — `NOTICE`. Mūsu puse ar birkām
un paziņojumiem ir `src/esupplier/fences.py`.

**Izcelsme.** Vēstulē drīkst būt tikai tas, ko rīks šajā gājienā tiešām atdeva.
Pirms melnraksta katrs artikuls un katra cena tiek salīdzināta ar rīku atbildēm,
un tas, kas nesakrīt, aiziet menedžerim tāpat kā izmestās bildes. Promptā šis
aizliegums ir pirmais noteikums, bet prompts nav pārbaude.

Cenu pārbaude pieņem plaši un apzināti: kataloga cena, tā reizināta ar jebkuru
vēstulē minētu skaitli, tas pats ar PVN, un vairāku pozīciju kopsumma. Kļūda uz
"atzīstam" pusi maksā palaistu garām skaitli; kļūda uz otru pusi maksā
brīdinājumu pie katras vēstules, un tādus pēc nedēļas vairs neviens nelasa.

Bez rīku atbildes abas pārbaudes klusē: salīdzināt nav ar ko.

## Atbildes formāts

Atbilde vienmēr ir divās daļās, starp tām rinda ar `---`:

1. **Vēstule klientam** — sveiciens, produktu tabula (foto, artikuls, nosaukums,
   cena bez PVN *un* ar PVN, `Ir`/`Nav`, saite uz veikalu), daudzuma teikums par
   katru pozīciju, salīdzinājuma tabula, ja piedāvātais atšķiras no prasītā, un
   ne vairāk kā 4 precizējošie jautājumi.
2. **`⚑ IEKŠĒJI (klientam nesūtīt)`** — obligāts. Divas sadaļas plus atlikums:
   - `JĀIZDARA` — uzdevumi cilvēkam: rezervācija, piegādes termiņš, rēķins,
     mērvienības pārrēķins, atlikuma pārbaude.
   - `NEAPSTIPRINĀTS` — ko no datiem nevarēja apstiprināt un kur meklēšana bija
     nedroša.
   - `ATLIKUMS NOLIKTAVĀ` — pa rindai uz katru vēstulē nosaukto artikulu. To
     raksta programma, ne modelis.

Aģents pats **nesola** rēķinu, rezervāciju, piegādes termiņu, apmaksas
nosacījumus, atlaidi vai transportu — tie ir menedžera lēmumi un iet iekšējā
blokā. Klientam tas skan "precizēs kolēģis".

Ja bloka nav vai atbilde tika apcirsta, konsole to pasaka atsevišķi. Bloka
trūkums menedžerim izskatās pēc "nekas nav jādara", un tas ir bīstamākais
klusējums, kāds šeit iespējams.

## Noliktava un saites

**Klientam pieejamība ir `Ir` vai `Nav`.** Precīzs atlikuma skaitlis vēstulē
nenonāk, un tas nav prompta lūgums — modelim tā vienkārši nav. `stock_qty` un
`stock_text` no rīku atbildēm ir izņemti (`Product.to_search_dict`), tāpēc
skaitli nokopēt nav no kurienes. Atlikums mainās ātrāk, nekā vēstule aiziet, un
skaitlis, ko klients izlasīja, kļūst par solījumu.

Menedžerim skaitlis ir vajadzīgs, un tas ir katrā vēstulē: `report.stock_notes`
paņem to no datubāzes par katru vēstulē nosaukto artikulu un ieliek iekšējā
blokā. To dara programma, tāpēc tas ir tur vienmēr, ne tikai tad, kad modelis
atcerējās pajautāt.

```
ATLIKUMS NOLIKTAVĀ (klientam nesūtīt)
- art. 000015202 — noliktavā 121 gab.
- art. 000029023 — noliktavā NAV
```

Ja skaitlis tomēr parādās vēstulē, tas ir izdomāts, un `report.stock_leaks` to
noķer: skaitlis blakus vārdam "noliktavā", kā arī vārdi "atlikums" un "krājums".
Brīdinājums aiziet menedžerim.

**Katrai pozīcijai ir saite uz veikalu.** Rīki atdod `url`, un modelis to kopē
tabulas ailē kā `[Skatīt](url)`. Pirms melnraksta `report.verify_links` salīdzina
katru saiti ar kataloga adresēm; kas nesakrīt, tiek izmesta, tāpat kā bildes.
Adrese, salikta no artikula, klientam atveras kā 404, un tas ir sliktāk nekā
saites trūkums.

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
uv run pytest                                    # 443 testi, bez API izsaukumiem
uv run evals                                     # visi gadījumi (maksā tokenus)
uv run evals --case vienkarsais                  # viens
uv run evals --compare green-7of7.json           # pret iepriekšēju rezultātu
```

`tests/` ir ātri un bez tīkla; daļa meklēšanas testu skrien pret īsto
`data/catalog.db` un tiek izlaisti, ja tā nav.

`tests/test_vendor_fencing.py` ir ievestā `commerce-agents` moduļa paša testi,
mainīts tikai importa ceļš. Tie ir tur tāpēc, ka nākamajā versijas celšanā
pateiks, vai uzvedība mainījās. `tests/helpers_files.py` ģenerē īstus PDF, DOCX,
XLSX, PPTX, XLS, RTF, DXF, ZIP un PNG failus — pielikumu lasīšanu ar izliktiem
baitiem pārbaudīt nevar.

`evals/cases.jsonl` ir gadījumi ar substring pārbaudēm, rīku izsaukumu limitiem
un LLM-as-judge kritērijiem. Rezultāti krīt `evals/results/`, izejas kods 1, ja
kāds krita — var likt CI.

## Uzbūve

```
src/esupplier/
  cli.py              konsole, komandas, auto-saglabāšana
  config.py           vides mainīgie, limiti, PVN likme
  fences.py           rāmju birkas svešam tekstam; paziņojumi promptam
  mcp_server.py       kataloga rīki Claude Code sesijai (MCP, stdio)
  claude_code.py      prasme, atvasināta no sistēmas prompta
  report.py           atbildes sadalīšana, HTML, attēlu/saišu/izcelsmes pārbaude
  vendor/             ievests svešs kods; NEPĀRRAKSTĀM (skat. vendor/README.md)
  agent/
    prompts.py        sistēmas prompts (nozares zināšanas + atbildes formāts)
    tools.py          rīku definīcijas un izpilde
    loop.py           modelis → rīki → modelis
  mail/
    run.py            pastkastītes gājiens: vēstule → melnraksts
    imap.py           savienojums, mapes, APPEND
    message.py        MIME → teksts; citāti, paraksti, filtri
    attachments.py    pielikums → teksts (PDF, Office, RTF, DXF, arhīvi)
    vision.py         attēls → teksts: skenēts rasējums, foto, PDF bez teksta
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
.mcp.json             MCP pieslēgums Claude Code sesijai
.claude/skills/       prasme (ģenerēta ar `uv run skill`, repozitorijā ir)
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
| `ESUPPLIER_ATTACHMENT_MAX_BYTES` | `10000000` | lielāku pielikumu neatveram |
| `ESUPPLIER_ATTACHMENT_CHARS` | `4000` | cik zīmju no viena pielikuma aiziet modelim |
| `ESUPPLIER_ATTACHMENTS_CHARS` | `12000` | cik zīmju kopā no visiem pielikumiem |
| `ESUPPLIER_ATTACHMENT_PDF_PAGES` | `20` | cik PDF lapu lasām |
| `ESUPPLIER_ARCHIVE_MAX_FILES` | `12` | cik failu atveram no viena arhīva |
| `ESUPPLIER_SOFFICE` | — | LibreOffice ceļš vecajam `.doc`; tukšs = meklējam PATH |
| `ESUPPLIER_ATTACHMENT_VISION` | `1` | `0` izslēdz attēlu atšifrēšanu |
| `ESUPPLIER_VISION_MODEL` | = `ESUPPLIER_MODEL` | modelis, kas redz attēlus |
| `ESUPPLIER_VISION_PAGES` | `5` | cik PDF lapu attēlojam un sūtām |
| `ESUPPLIER_VISION_DPI` | `150` | izšķirtspēja, ar kādu attēlojam lapu |
| `ESUPPLIER_VISION_MAX_FILES` | `5` | cik attēlu vienā vēstulē atšifrējam |

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
- **Izcelsmes pārbaude ir pēc fakta, ne vārti.** Vēstule jau ir uzrakstīta, kad
  to pārbauda; aizturēt to nevar, var tikai pateikt menedžerim. Sagatavotais
  melnraksts paliek pastkastītē arī tad, kad brīdinājums iedegās.
- **Cenu pārbaude nav stingra.** Tā pieņem visu, kas no kataloga cenas izriet ar
  reizināšanu vai saskaitīšanu, tāpēc izdomāta cena, kas nejauši sakrīt ar kādu
  no tiem skaitļiem, tiek palaista cauri. Artikulu pārbaude ir stingra.
- **Konsolē klienta vēstule rāmī neiet.** Tur ievadi raksta menedžeris, un tā ir
  gan pielīmēta klienta vēstule, gan norādījumi aģentam ("uzraksti īsāk"). Rāmis
  padarītu par datiem arī tos. Pastkastītes ceļā šī neskaidrība nepastāv.
