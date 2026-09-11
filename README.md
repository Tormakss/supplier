# e-supplier agent

Asistents, kas no klienta vēstules sagatavo gatavu piedāvājuma vēstuli pēc
e-supplier.lv kataloga. Strādā divos veidos: saruna terminālī vai pats lasa
pastkastīti un atstāj atbildi kā **melnrakstu**. Klientam nekas neaiziet, kamēr
cilvēks nav nospiedis "Sūtīt".

## Uzstādīšana

```bash
git clone git@git.trialine.lv:web/ai-assistance.git e-supplier
cd e-supplier
uv sync
cp .env.example .env
uv run sync          # ievelk katalogu, ~3600 produkti, ~85 s
```

Bez `uv run sync` katalogs ir tukšs un aģents nestartē.

## .env — dzinējs

Obligātās rindas ir atkarīgas no dzinēja. Izvēlies **vienu** bloku.

```
ESUPPLIER_ENGINE=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

```
ESUPPLIER_ENGINE=openai
OPENAI_API_KEY=sk-proj-...
```

```
ESUPPLIER_ENGINE=claude
```

`claude` atslēgu neprasa — maksā no Claude Code abonementa, bet Claude Code
jābūt uzstādītam un pieteiktam uz tās pašas mašīnas. Serverim un cron darbam
der `anthropic` vai `openai`: tiem vajag tikai atslēgu.

## .env — pastkastīte

Pastkastītei obligātas ir četras rindas, un tās ir vienādas visiem dzinējiem.

```
ESUPPLIER_IMAP_HOST=imap.gmail.com
ESUPPLIER_IMAP_USER=tormaks17@gmail.com
ESUPPLIER_IMAP_PASSWORD=<16 zīmju App Password>
ESUPPLIER_IMAP_FOLDER=INBOX          # mapes vai Gmail etiķetes nosaukums
```

App Password ņem no <https://myaccount.google.com/apppasswords>. Parastā konta
parole neder — Google to IMAP pieslēgumiem nepieņem kopš 2022. gada, un App
Password izdod tikai kontam ar divpakāpju verifikāciju.

Melnrakstu mapi meklējam paši pēc servera karoga; ar roku to nosaka
`ESUPPLIER_IMAP_DRAFTS`. Pārējie mainīgie ar noklusējumiem ir `.env.example`.

## Komandas

| Komanda | Ko dara |
|---|---|
| `uv run mail --check` | pārbauda savienojumu un mapes; modeli neizsauc, nemaksā neko |
| `uv run mail --once` | viens gājiens, uzraksta melnrakstus |
| `uv run mail` | seko pastkastītei, līdz `Ctrl+C` |
| `uv run mail --log 20` | ko jau apstrādāja |
| `uv run mail --redo 3` | atbild vēlreiz uz trim pēdējām |
| `uv run mail --dry-run` | sagatavo atbildes, bet pastkastītē neko neraksta |
| `uv run chat` | saruna terminālī; ievadi beidz ar punktu atsevišķā rindā |
| `uv run sync` | ievelk katalogu |
| `uv run evals` | palaiž eval gadījumus (maksā tokenus) |
| `uv run skill` | pārraksta prasmes failu no prompta |
| `uv run pytest -q` | testi |

Pieslēdzot jaunu pastkastīti, pirmais vienmēr ir `--check`, tad `--once`.

Sagatavotās vēstules krīt `atbildes/` kā HTML ar bildēm un tabulām. **Kopē no
HTML faila, ne no termināļa.** Uzdevumi menedžerim ir blakus failā
`*-IEKSEJI.txt`; melnrakstā to nav nekad.

## Sīkāk

- **[SETUP.md](SETUP.md)** — uzstādīšana no tukšas mašīnas un kļūdu ceļi.
- **[DOCS.md](DOCS.md)** — kā aģents strādā: dzinēji, pielikumi, rasējumi,
  pārbaudes, katalogs, mērvienības, visi vides mainīgie, zināmās robežas.
- `.env.example` — katrs mainīgais ar komentāru.
