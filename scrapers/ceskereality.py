"""
Scraper pro České reality – HTML scraping.

Důležité (z 2026-05-15):
- HTML struktura: <article class="i-estate"> s vnořeným <a class="i-estate__image-link">
- Dispozice je třeba mít přímo v URL path: /byty/byty-3-kk/.../
  Query parametr ?d_subtyp= sám o sobě nestačí, URL se redirectuje.
- Procházíme každou dispozici zvlášť přes její vlastní URL.
- Cena: <span class="i-estate__footer-price-value">
- Lokalita: vytáhneme z URL slugu nebo z img alt textu
"""
import re, time, logging
import httpx
from bs4 import BeautifulSoup

from . import sestav_nazev

log = logging.getLogger(__name__)


def _cena(t):
    t = re.sub(r"[^\d]", "", t or "")
    return int(t) if len(t) >= 4 else None


def _plocha(t):
    m = re.search(r"(\d+)\s*m[²2]", t or "")
    return float(m.group(1)) if m else None


def _disp(t):
    m = re.search(r"(\d\+(?:kk|\d))", t or "", re.I)
    return m.group(1).lower() if m else None


LOK_PATH = {
    "praha":   "hlavni-mesto-praha/praha-hlavni-mesto",
    "brno":    "jihomoravsky-kraj/brno-mesto",
    "ostrava": "moravskoslezsky-kraj/ostrava-mesto",
}

# Mapování dispozic na URL path segment (byty-X-Y)
DISP_URL_SEG = {
    "1+kk": "byty-1-kk",
    "1+1":  "byty-1-1",
    "2+kk": "byty-2-kk",
    "2+1":  "byty-2-1",
    "3+kk": "byty-3-kk",
    "3+1":  "byty-3-1",
    "4+kk": "byty-4-kk",
    "4+1":  "byty-4-1",
    "5+kk": "byty-5-kk",
    "5+1":  "byty-5-1",
    "6+":   "byty-6-a-vice",
}


def _vyssi_rozliseni(url):
    """320x320 -> 640x640, malé thumbs -> větší."""
    if not url:
        return url
    out = url
    out = re.sub(r'/\d{2,4}x\d{2,4}_(webp|jpg|jpeg|png)/', r'/640x640_\1/', out)
    out = re.sub(r'/\d{2,4}x\d{2,4}/', '/640x640/', out)
    out = re.sub(r'-\d{2,4}x\d{2,4}(\.\w+)$', r'-640x640\1', out)
    out = re.sub(r'([?&])w=\d+', r'\g<1>w=640', out)
    out = re.sub(r'([?&])h=\d+', r'\g<1>h=640', out)
    out = re.sub(r'_(small|thumb|mini|tn|s)(\.\w+)$', r'_big\2', out, flags=re.I)
    out = out.replace('/thumbs/', '/').replace('/thumb/', '/')
    return out


def _vytahni_obrazky(item):
    """Najde nejlepší dostupné obrázky z <article class='i-estate'>."""
    imgs = []
    # Preferuj <source srcset="... 1x, ... 2x"> – vezmeme 2x variantu
    for source in item.select("picture source[srcset]"):
        ss = source.get("srcset", "")
        if not ss:
            continue
        # parse "url1 1x, url2 2x"
        candidates = []
        for chunk in ss.split(","):
            parts = chunk.strip().split()
            if parts:
                candidates.append((parts[0], parts[1] if len(parts) > 1 else ""))
        # vezmi 2x pokud existuje, jinak poslední
        chosen = next((u for u, d in candidates if d == "2x"), None)
        if not chosen and candidates:
            chosen = candidates[-1][0]
        if chosen and chosen not in imgs:
            imgs.append(_vyssi_rozliseni(chosen))
        if len(imgs) >= 3:
            break

    # Fallback: <img src>
    if not imgs:
        for img_tag in item.select("img")[:3]:
            src = (img_tag.get("data-src")
                   or img_tag.get("src", "")
                   or "")
            if src and not src.endswith((".gif", ".svg")) and len(src) > 10:
                if not src.startswith("http"):
                    src = "https://www.ceskereality.cz" + src
                imgs.append(_vyssi_rozliseni(src))
    return imgs[:3]


def _lokalita_z_url(url):
    """Z URL /prodej/byty/byty-3-kk/praha/prodej-bytu-3-kk-60-m2-hnevkovskeho-3772627.html
    vrátí např. 'Hněvkovského' (jen pár tokenů, aby to nebylo dlouhé)."""
    if not url:
        return ""
    m = re.search(r"prodej-bytu-[\d+]+-(?:kk|\d)-\d+-m2-([a-z0-9-]+?)-\d+\.html", url, re.I)
    if not m:
        return ""
    slug = m.group(1)
    parts = [p.capitalize() for p in slug.split("-") if p]
    return ", ".join(parts) if parts else ""


def _lokalita_z_nazvu(nazev):
    """Z 'Prodej bytu 3+kk 60 m² Praha Chodov, Hněvkovského' vrátí 'Praha Chodov, Hněvkovského'."""
    if not nazev:
        return ""
    # Najdi část za "m²"
    m = re.search(r"m[²2\u00b2]\s+(.+?)$", nazev)
    if m:
        return m.group(1).strip()
    return ""


def _scrape_dispozice(cl, base_url, max_stranek, delay):
    """Stáhne všechny stránky pro jednu dispozici. Vrátí list inzerátů."""
    inzeraty = []
    seen_urls = set()
    for strana in range(1, max_stranek + 1):
        url = base_url
        if strana > 1:
            url = f"{base_url}?strana={strana}"
        try:
            r = cl.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
        except Exception as e:
            log.error(f"České reality {base_url} strana {strana}: {e}")
            break

        items = soup.select("article.i-estate")
        if not items:
            # Konec – nejsou už další stránky
            break

        nova = 0
        for item in items:
            a = item.select_one("a.i-estate__image-link[href]") \
                or item.select_one("a[href*='prodej-bytu-']") \
                or item.select_one("a[href]")
            if not a:
                continue
            href = a.get("href", "")
            if not href or href == "#":
                continue
            inz_url = href if href.startswith("http") else "https://www.ceskereality.cz" + href
            # Vyřaď "oblíbené", "favorite" apod.
            if "/oblibene/" in inz_url or "/muj-profil/" in inz_url:
                continue
            if inz_url in seen_urls:
                continue
            seen_urls.add(inz_url)

            # Název: nejprve alt obrázku ("Prodej bytu 3+kk 60 m² Praha Chodov, Hněvkovského")
            nazev = ""
            img_tag = item.select_one("img[alt]")
            if img_tag:
                alt = img_tag.get("alt", "").strip()
                if alt and "obrázek" not in alt.lower() and "foto" not in alt.lower():
                    nazev = alt
            # Fallback z header title
            if not nazev:
                title_el = item.select_one(".i-estate__header-title, h2, h3")
                if title_el:
                    nazev = title_el.get_text(strip=True)

            # Cena
            cena_el = item.select_one(".i-estate__footer-price-value, [class*='price']")
            cena_val = _cena(cena_el.get_text() if cena_el else "")

            # Lokalita – nejprve z názvu (má diakritiku), pak z URL slugu
            lok_val = _lokalita_z_nazvu(nazev) or _lokalita_z_url(inz_url)

            # Plocha a dispozice z názvu
            plocha_val = _plocha(nazev)
            disp_val = _disp(nazev)

            imgs = _vytahni_obrazky(item)

            inzeraty.append({
                "portal": "ceskereality", "url": inz_url, "nazev": nazev or "Inzerát ČR",
                "cena": cena_val,
                "plocha": plocha_val,
                "lokalita": lok_val,
                "dispozice": disp_val,
                "popis": None, "obrazky": imgs,
            })
            nova += 1

        log.info(f"České reality {base_url.split('/byty-')[-1].split('/')[0]} "
                 f"strana {strana}: {nova} nových ({len(items)} celkem)")
        if nova == 0:
            break
        time.sleep(delay)

    return inzeraty


def scrape(cfg, headers):
    h = cfg["hledani"]
    typ = "prodej" if h.get("typ") == "prodej" else "pronajem"
    lok = LOK_PATH.get(h.get("lokalita", "").lower(),
                       "hlavni-mesto-praha/praha-hlavni-mesto")
    cmin = h.get("cena_min", 0) or 100000
    cmax = h.get("cena_max", 0) or 99999999
    max_stranek = cfg.get("scraper", {}).get("max_stranek", 5)
    delay = cfg["scraper"]["delay_sekund"]

    # Pokud nejsou dispozice nastavené, vezmeme generický URL bez dispozice v path
    dispozice_list = h.get("dispozice") or []
    url_segs = [DISP_URL_SEG[d] for d in dispozice_list if d in DISP_URL_SEG]
    if not url_segs:
        url_segs = [None]  # bez konkrétní dispozice

    inzeraty = []
    seen_urls = set()

    with httpx.Client(headers=headers, timeout=cfg["scraper"]["timeout_sekund"],
                      follow_redirects=True) as cl:
        for seg in url_segs:
            if seg:
                base = f"https://www.ceskereality.cz/{typ}/byty/{seg}/{lok}/od-{cmin}/do-{cmax}/"
            else:
                base = f"https://www.ceskereality.cz/{typ}/byty/{lok}/od-{cmin}/do-{cmax}/"

            new = _scrape_dispozice(cl, base, max_stranek, delay)
            # Deduplikace (mezi dispozicemi by se duplicity stávat neměly, ale pro jistotu)
            for inz in new:
                if inz["url"] not in seen_urls:
                    seen_urls.add(inz["url"])
                    inzeraty.append(inz)

    log.info(f"České reality: {len(inzeraty)} celkem")
    return inzeraty
