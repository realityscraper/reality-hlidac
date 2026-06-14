"""
Scraper pro iDnes Reality – HTML scraping.
URL struktura:
https://reality.idnes.cz/s/byty/cena-nad-100000-do-11000000/praha/?s-qc[subtypeFlat]=3k|31|4k|41|5k|51|6k
"""
import re, time, logging
import httpx
from bs4 import BeautifulSoup

from . import sestav_nazev

log = logging.getLogger(__name__)

# Mapování dispozic na iDnes kódy
DISP_KOD = {
    "3+kk": "3k", "3+1": "31", "4+kk": "4k", "4+1": "41",
    "5+kk": "5k", "5+1": "51", "6+": "6k",
}


def _cena(t):
    t = re.sub(r"[^\d]", "", t or "")
    return int(t) if len(t) >= 4 else None


def _plocha(t):
    m = re.search(r"(\d+)\s*m[²2]", t or "")
    return float(m.group(1)) if m else None


def _disp(t):
    m = re.search(r"(\d\+(?:kk|\d))", t or "", re.I)
    return m.group(1).lower() if m else None


def _slugify(s):
    repl = {"á": "a", "č": "c", "ď": "d", "é": "e", "ě": "e", "í": "i",
            "ň": "n", "ó": "o", "ř": "r", "š": "s", "ť": "t", "ú": "u",
            "ů": "u", "ý": "y", "ž": "z", " ": "-"}
    s = (s or "").lower().strip()
    for k, v in repl.items():
        s = s.replace(k, v)
    return s


def _parse_items(soup):
    selectors = [
        "article.c-products__item",
        "article[class*='c-products']",
        "div.c-products__item",
        "article.b-real-estate",
        "article[class*='real-estate']",
        "article",
    ]
    for sel in selectors:
        items = soup.select(sel)
        if items:
            log.debug(f"iDnes selektor '{sel}': {len(items)} prvků")
            return items
    return []


def _extrahuj_lokalitu(item):
    """
    Zkusíme různé selektory; pokud nic, hledáme regex Praha X - Čtvrť v textu karty.
    """
    selectors = [
        ".c-products__locality", ".c-products__info", ".b-detail__info",
        "[class*='locality']", "[class*='location']",
        ".c-products__address", "[class*='address']",
    ]
    for sel in selectors:
        el = item.select_one(sel)
        if el:
            txt = el.get_text(strip=True)
            # Zajímá nás něco s názvem města; přeskočíme čistá čísla nebo prázdno
            if txt and len(txt) > 3 and not txt.replace(" ", "").isdigit():
                return txt
    # Fallback: hledej v plain textu karty patrný vzor "Praha X - Čtvrť"
    full_text = item.get_text(" ", strip=True)
    m = re.search(r"(Praha\s+\d+\s*[-–]\s*[ZŠČŘŽÝÁÍÉÚŮÓzscrzyaieuuo\wáčďéěíňóřšťúůýž]+(?:\s*,\s*[\w\sáčďéěíňóřšťúůýž]+)?)",
                  full_text, re.UNICODE)
    if m:
        return m.group(1).strip()
    # Druhý fallback: něco jako "Praha-Hloubětín" nebo "Praha Smíchov"
    m = re.search(r"(Praha\s*[-–]?\s*[A-ZŠČŘŽÝÁÍÉÚŮÓ][\wáčďéěíňóřšťúůýž]+)", full_text)
    if m:
        return m.group(1).strip()
    return ""


def _parse_one(item):
    a = (item.select_one("a.c-products__link") or
         item.select_one("a[href*='/detail/']") or
         item.select_one("h2 a") or
         item.select_one("h3 a") or
         item.select_one("a[href]"))
    if not a:
        return None
    href = a.get("href", "")
    if not href or href == "#":
        return None
    url = href if href.startswith("http") else "https://reality.idnes.cz" + href
    if "/detail/" not in url and "reality.idnes.cz" not in url:
        return None

    nazev_el = (item.select_one(".c-products__title") or
                item.select_one("h2") or item.select_one("h3"))
    nazev_raw = nazev_el.get_text(strip=True) if nazev_el else a.get_text(strip=True)

    cena_el = (item.select_one(".c-products__price") or
               item.select_one("[class*='price']"))
    cena = _cena(cena_el.get_text() if cena_el else "")

    lokalita = _extrahuj_lokalitu(item)

    # Pokud listing lokalitu nemá, zkusíme ji vytáhnout z URL slugu
    # např. /detail/prodej/byt/3-kk/63m2/praha-hlubocepy-gollove/3057984/
    if not lokalita and "/detail/" in url:
        m = re.search(r"/detail/[^/]+/[^/]+(?:/[^/]+)*/(praha[a-z0-9-]+)/", url, re.I)
        if m:
            slug = m.group(1)
            # praha-hlubocepy-gollove → "Praha Hlubočepy, Gollové"
            parts = slug.split("-")
            if len(parts) >= 2:
                cap = [p.capitalize() for p in parts]
                if len(cap) >= 3:
                    lokalita = f"{cap[0]} {cap[1]}, {' '.join(cap[2:])}"
                else:
                    lokalita = " ".join(cap)

    imgs = []
    for img_tag in item.select("img")[:3]:
        src = img_tag.get("src") or img_tag.get("data-src") or ""
        if src and not src.endswith((".gif", ".svg")) and len(src) > 10:
            imgs.append(src)
    if not imgs:
        for el in item.select("[style*='background-image']")[:2]:
            style = el.get("style", "")
            m = re.search(r"url\(['\"]?([^'\")\s]+)['\"]?\)", style)
            if m:
                imgs.append(m.group(1))

    plocha = _plocha(nazev_raw)
    dispozice = _disp(nazev_raw)

    # Společný formát: "3+1 · 69 m² · Praha 4 - Kamýk"
    nazev = sestav_nazev(dispozice, plocha, lokalita,
                         fallback=(nazev_raw or "Inzerát iDnes"))

    return {
        "portal": "idnes", "url": url, "nazev": nazev,
        "cena": cena, "plocha": plocha,
        "lokalita": lokalita, "dispozice": dispozice,
        "popis": None, "obrazky": imgs,
    }


def scrape(cfg, headers):
    h = cfg["hledani"]
    lok_slug = _slugify(h.get("lokalita", "Praha"))

    # Sestavení URL ve správném formátu iDnes
    # https://reality.idnes.cz/s/byty/cena-nad-100000-do-11000000/praha/
    cmin = h.get("cena_min", 0) or 0
    cmax = h.get("cena_max", 0) or 0
    if cmin and cmax:
        cena_seg = f"cena-nad-{cmin}-do-{cmax}"
    elif cmax:
        cena_seg = f"cena-do-{cmax}"
    elif cmin:
        cena_seg = f"cena-nad-{cmin}"
    else:
        cena_seg = ""

    if cena_seg:
        base = f"https://reality.idnes.cz/s/byty/{cena_seg}/{lok_slug}/"
    else:
        base = f"https://reality.idnes.cz/s/prodej/byty/{lok_slug}/"

    # Dispozice filtr
    params = {}
    disp_cfg = h.get("dispozice", [])
    if disp_cfg:
        kody = [DISP_KOD[d] for d in disp_cfg if d in DISP_KOD]
        if kody:
            params["s-qc[subtypeFlat]"] = "|".join(kody)

    inzeraty = []
    seen_urls = set()

    max_stranek = cfg.get("scraper", {}).get("max_stranek", 5)

    with httpx.Client(headers=headers, timeout=cfg["scraper"]["timeout_sekund"],
                      follow_redirects=True) as cl:
        for strana in range(1, max_stranek + 1):
            p = dict(params)
            if strana > 1:
                p["page"] = strana - 1
            try:
                r = cl.get(base, params=p)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
            except Exception as e:
                log.error(f"iDnes strana {strana}: {e}")
                break

            items = _parse_items(soup)
            if not items:
                log.warning(f"iDnes strana {strana}: žádné inzeráty")
                arts = soup.find_all("article")
                if arts:
                    classes = [" ".join(a.get("class", [])) for a in arts[:3]]
                    log.warning(f"iDnes article třídy: {classes}")
                break

            nova = 0
            for item in items:
                parsed = _parse_one(item)
                if parsed and parsed["url"] not in seen_urls:
                    seen_urls.add(parsed["url"])
                    inzeraty.append(parsed)
                    nova += 1

            log.info(f"iDnes strana {strana}: {nova} nových ({len(items)} celkem)")
            if nova == 0:
                break
            time.sleep(cfg["scraper"]["delay_sekund"])

    log.info(f"iDnes: {len(inzeraty)} celkem")
    return inzeraty
