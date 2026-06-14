# Reality Hlídač

Scraper pro nemovitosti z Sreality, Bezrealitky, iDnes Reality a České reality.
Běží na GitHub Actions každých 30 minut, výsledné HTML hostuje GitHub Pages
s ochranou heslem (JS šifrování).

## Setup

### 1. Fork / nahrání do GitHub

Vytvoř nový **soukromý** repozitář (aby DB nebyla veřejně viditelná) a nahraj
obsah tohoto adresáře.

### 2. Nastavit Secret pro heslo

V repu **Settings → Secrets and variables → Actions → New repository secret**:

| Name              | Value                                     |
| ----------------- | ----------------------------------------- |
| `SITE_PASSWORD`   | dlouhé heslo (16+ znaků, neslovníkové)    |

> 💡 **Bezpečnost**: JS šifrování chrání před náhodnými návštěvníky, není to
> kryptografická ochrana. Útočník, který získá HTML, může brute-forcovat heslo.
> PBKDF2 100k iterací zpomaluje útok na ~10 hesel/s, ale slabá hesla jsou
> stále zranitelná.

### 3. Povolit GitHub Pages

V repu **Settings → Pages**:
- **Source**: GitHub Actions
- (žádný branch / folder)

### 4. Upravit `config.yaml`

Pokud chceš jinou lokalitu / cenové rozpětí / dispozice, edituj `config.yaml`
a commitni. Workflow se spustí znovu automaticky.

### 5. Spustit workflow

Buď počkej na cron (každých 30 min), nebo manuálně:
**Actions → Scrape and deploy → Run workflow**

### 6. Otevřít web

Po prvním úspěšném běhu uvidíš URL v **Actions → Deploy → page_url**.
Typicky: `https://<username>.github.io/<repo-name>/`

## Struktura

```
.
├── .github/workflows/
│   └── scrape.yml          # GitHub Actions cron + deploy
├── scrapers/
│   ├── __init__.py
│   ├── sreality.py         # Next.js _next/data endpoint
│   ├── bezrealitky.py      # GraphQL API
│   ├── idnes.py            # HTML scraping
│   └── ceskereality.py     # HTML scraping
├── data/
│   └── reality.db          # SQLite, commitovaná do repa
├── public/                  # Vygenerované HTML (zašifrované, na Pages)
├── config.yaml             # Konfigurace
├── main.py                 # Hlavní skript (běží jednou per workflow)
├── db.py                   # SQLite vrstva
├── generator.py            # HTML generátor
├── encrypt_html.py         # Šifruje public/*.html pomocí AES-GCM
└── requirements.txt
```

## Lokální vývoj

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Bez šifrování (HTML bude čitelné)
python main.py

# Se šifrováním
SITE_PASSWORD=tajneheslo python main.py
SITE_PASSWORD=tajneheslo python encrypt_html.py public

# Výsledné HTML
python -m http.server -d public 8080
# → http://localhost:8080
```

## Pozor na limity GitHub Actions

- **Free tier**: 2000 minut/měsíc pro privátní repo (public neomezeně)
- Jeden běh trvá cca 2 minuty → cca **1500 běhů/měsíc** = OK pro 30 min interval
- Cron může být zpožděn nebo přeskočen při vysoké zátěži runnerů
- Pokud chceš jistější interval, lze přepnout na zaplacený runner nebo VPS

## Změna hesla

Změň `SITE_PASSWORD` v repo secrets. Při dalším běhu se HTML zašifruje
novým heslem (staré tabletu z prohlížeče vymaž — heslo je v `sessionStorage`).

## Reset databáze

Smaž `data/reality.db` z repa, commitni, spusť workflow. Začne nasbírat
od nuly.
