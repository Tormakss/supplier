# Ievestais kods

## `fencing.py`

- **Avots:** https://github.com/anthropics/commerce-agents
- **Ceļš avotā:** `commerce-common/commerce_common/fencing.py`
- **Revīzija:** `fd4d59224ab96b43c6dc6888207c67b3bd5a24cf`
- **Ievests:** 2026-09-04
- **Licence:** Apache 2.0 (`LICENSE-APACHE-2.0` blakus), © 2026 Anthropic PBC
- **Izmaiņas:** nav. Fails ir burtiska kopija.

Anthropic references paraugā tas iežogo svešu tekstu, pirms to ierauga modelis:
nost neredzamās un vadības rakstzīmes, viltotie gājienu marķieri (`Human:`
tukšas rindas sākumā) un rīku izsaukumu birkas, tad viss ieliek rāmī ar
nemainīgu birku un zīmju griestiem.

Mums tas vajadzīgs tāpēc, ka klienta vēstule un it īpaši pielikuma saturs ir
svešs teksts. PDF fails var saturēt "aizmirsti iepriekšējos norādījumus", un
faila nosaukumu izvēlas sūtītājs.

Mūsu puse — `src/esupplier/mail/fences.py`. Tur ir birkas, paziņojumi promptam
un tas, kas tieši tiek iežogots.

Testi `tests/test_vendor_fencing.py` ir tā paša projekta testi; mainīts tikai
importa ceļš. Tie ir tur tāpēc, ka nākamajā versijas celšanā tie pateiks, vai
uzvedība mainījās.
