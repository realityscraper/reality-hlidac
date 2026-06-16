"""
Scraper pro Sreality.cz – verze 2026-06-08.

Sreality přepnuli z /api/cs/v2/estates (zrušeno, vrací 404) na Next.js SSR.
Inzeráty čteme přes data endpoint /_next/data/{buildId}/cs/hledani/.../{lokalita}.json

Klíčové poznatky:
- buildId se mění s každým releasem (na 2026-06-08 = "1.0.484") - načítáme jej
  dynamicky z HTML stránky listingu před každým spuštěním.
- Filtry jsou v query string s ČESKÝMI klíči:
    velikost=3+kk,3+1,4+kk,... (PLUS znaky musí být zakódované jako %2B)
    cena-od=1000000
    cena-do=11000000
    strana=2  (stránkování)
- Stránka vrací 22 inzerátů (server limit), strana 1 obsahuje navíc promované.
- Data jsou v pageProps.dehydratedState.queries, queryKey začíná 'estatesSearch'.
"""
import re, time, json, logging
import httpx

from . import sestav_nazev

log = logging.getLogger(__name__)


# Mapování naší dispozice -> sreality "velikost" segment v URL
DISPOSITION_TO_VELIKOST = {
    "1+kk": "1+kk",
    "1+1":  "1+1",
    "2+kk": "2+kk",
    "2+1":  "2+1",
    "3+kk": "3+kk",
    "3+1":  "3+1",
    "4+kk": "4+kk",
    "4+1":  "4+1",
    "5+kk": "5+kk",
    "5+1":  "5+1",
    "6+":   "6-a-vice",
}

LOK_SLUG = {
    "praha":   "praha",
    "brno":    "brno-mesto",
    "ostrava": "ostrava-mesto",
}

BASE = "https://www.sreality.cz"


def _fetch_build_id(client, lokalita_slug):
    """Stáhne HTML listingu, vyparsuje __NEXT_DATA__ a vrátí buildId."""
    url = f"{BASE}/hledani/prodej/byty/{lokalita_slug}"
    r = client.get(url)
    r.raise_for_status()
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                  r.text, re.DOTALL)
    if not m:
        raise RuntimeError("__NEXT_DATA__ not found")
    data = json.loads(m.group(1))
    bid = data.get("buildId")
    if not bid:
        raise RuntimeError("buildId not in __NEXT_DATA__")
    return bid


def _extract_results(payload):
    """Z Next.js data JSON vrátí (results_list, total_count)."""
    pp = payload.get("pageProps", payload.get("props", {}).get("pageProps", {}))
    dh = pp.get("dehydratedState", {})
    for q in dh.get("queries", []):
        key = q.get("queryKey", [])
        if key and key[0] == "estatesSearch":
            data = q.get("state", {}).get("data", {}) or {}
            results = data.get("results", []) or []
            pag = data.get("pagination", {}) or {}
            return results, pag.get("total", 0)
    return [], 0


def _fetch_page(client, build_id, lok_slug, velikosti, cena_od, cena_do, strana):
    """Stáhne jednu stránku přes /_next/data/.../{slug}.json"""
    url = f"{BASE}/_next/data/{build_id}/cs/hledani/prodej/byty/{lok_slug}.json"
    params = []
    if velikosti:
        params.append(("velikost", ",".join(velikosti)))
    if cena_od:
        params.append(("cena-od", str(cena_od)))
    if cena_do:
        params.append(("cena-do", str(cena_do)))
    if strana and strana > 1:
        params.append(("strana", str(strana)))
    r = client.get(url, params=params)
    r.raise_for_status()
    return _extract_results(r.json())


def _norm_img(url):
    """Sreality vrací '//d18-a.sdn.cz/...' – přidáme https: a transformační parametr.
    Bez query parametru CDN vrací 401 Unauthorized."""
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    if "?" not in url:
        url = url + "?fl=res,400,300,3|shr,,20|jpg,90"
    return url

def _lokalita_str(loc):
    """{city, cityPart, street, ...} -> 'Street, City - CityPart'"""
    if not isinstance(loc, dict):
        return ""
    street = (loc.get("street") or "").strip()
    city = (loc.get("city") or "").strip()
    city_part = (loc.get("cityPart") or "").strip()
    parts = []
    if street:
        parts.append(street)
    if city and city_part:
        parts.append(f"{city} - {city_part}")
    elif city:
        parts.append(city)
    elif city_part:
        parts.append(city_part)
    return ", ".join(parts)


def scrape(cfg, headers):
    h = cfg["hledani"]
    inzeraty = []
    seen_ids = set()

    if h.get("typ", "prodej") != "prodej":
        log.warning("Sreality scraper podporuje aktuálně jen 'prodej'")
        return []

    lokalita = (h.get("lokalita") or "praha").lower()
    lok_slug = LOK_SLUG.get(lokalita, lokalita)
    cena_od = h.get("cena_min") or None
    cena_do = h.get("cena_max") or None

    velikosti = [DISPOSITION_TO_VELIKOST[d]
                 for d in (h.get("dispozice") or [])
                 if d in DISPOSITION_TO_VELIKOST]

    max_stranek = cfg.get("scraper", {}).get("max_stranek", 20)
    delay = cfg["scraper"]["delay_sekund"]

    with httpx.Client(
        headers={**headers, "Accept": "application/json, text/html"},
        timeout=cfg["scraper"]["timeout_sekund"],
        follow_redirects=True,
    ) as cl:
        # 1) Zjistit buildId
        try:
            build_id = _fetch_build_id(cl, lok_slug)
            log.info(f"Sreality buildId={build_id}")
        except Exception as e:
            log.error(f"Sreality: nepodařilo se získat buildId: {e}")
            return []

        # 2) Stránkovat
        for strana in range(1, max_stranek + 1):
            try:
                results, total = _fetch_page(
                    cl, build_id, lok_slug, velikosti, cena_od, cena_do, strana
                )
            except Exception as e:
                log.error(f"Sreality strana {strana}: {e}")
                break

            if not results:
                log.info(f"Sreality strana {strana}: prázdná, konec")
                break

            # DEBUG na první straně
            if strana == 1 and results:
                first = results[0]
                log.info(f"Sreality DEBUG první inzerát: "
                         f"name={first.get('name')!r}, "
                         f"locality.city={first.get('locality',{}).get('city')!r}, "
                         f"categorySubCb={first.get('categorySubCb')!r}")

            nova = 0
            for e in results:
                eid = e.get("id")
                if not eid or eid in seen_ids:
                    continue
                seen_ids.add(eid)

                name = e.get("name", "") or ""
                loc = e.get("locality") or {}
                lok_str = _lokalita_str(loc)

                # Sestav název: "Prodej bytu 3+kk 60 m² · Sokolovská, Praha - Libeň"
                if name and lok_str:
                    nazev = f"{name} · {lok_str}"
                elif name:
                    nazev = name
                else:
                    nazev = "Inzerát Sreality"

                # Dispozice z categorySubCb.name (např. '3+kk', '6 a více')
                disp_obj = e.get("categorySubCb") or {}
                disp_raw = disp_obj.get("name", "") if isinstance(disp_obj, dict) else ""
                if disp_raw == "6 a více":
                    dispozice = "6+"
                elif disp_raw:
                    dispozice = disp_raw.lower()
                else:
                    dispozice = None

                # Plocha z name "Prodej bytu 3+kk 60 m²"
                plocha = None
                pm = re.search(r"(\d+)\s*m[²2\u00b2]", name)
                if pm:
                    plocha = float(pm.group(1))

                cena = e.get("priceCzk") or e.get("priceSummaryCzk")
                lat = loc.get("latitude")
                lon = loc.get("longitude")

                imgs = []
                for img in (e.get("images") or [])[:3]:
                    if isinstance(img, dict):
                        u = _norm_img(img.get("url"))
                        if u:
                            imgs.append(u)

                # URL na detail – sestavíme z slug komponent
                slug_parts = []
                for k in ("citySeoName", "cityPartSeoName", "streetSeoName"):
                    v = loc.get(k)
                    if v:
                        slug_parts.append(v)
                slug = "-".join(slug_parts) or "praha"
                disp_url = disp_raw.replace(" a více", "-a-vice") if disp_raw else "byt"
                url = f"{BASE}/detail/prodej/byt/{disp_url}/{slug}/{eid}"

                inzeraty.append({
                    "portal": "sreality",
                    "url": url,
                    "nazev": nazev,
                    "cena": cena,
                    "plocha": plocha,
                    "lokalita": lok_str,
                    "dispozice": dispozice,
                    "popis": None,
                    "obrazky": imgs,
                    "lat": lat,
                    "lon": lon,
                })
                nova += 1

            log.info(f"Sreality strana {strana}: {nova} nových "
                     f"({len(results)} v odpovědi, {total} total)")

            # Konec – buď jsme dosáhli totalu nebo strana neměla nové
            if total and len(seen_ids) >= total:
                break
            if nova == 0:
                break
            time.sleep(delay)

    log.info(f"Sreality: {len(inzeraty)} celkem")
    return inzeraty
