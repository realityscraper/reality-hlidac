"""
Scraper pro Bezrealitky.cz – GraphQL API.

Schéma se mění tak často, že posíláme query INLINE (bez GraphQL proměnných),
to obejde problémy s typováním ($regionOsmIds: [String] vs [ID] atd.).

Strategie:
1. Postavíme query čistě jako string (žádné GraphQL proměnné).
2. Z konfigu sestavíme inline argumenty.
3. Postupně testujeme list pole (s address / bez, s gps / bez, ...).
4. Lokalita: address(locale: CS) když projde, jinak fallback ze slugu uri.
5. Název: vždy sestavíme '3+kk · 70 m² · Praha 5, Smíchov' (společná utility).
"""
import time, logging, random
import httpx

from . import sestav_nazev

log = logging.getLogger(__name__)

URL = "https://api.bezrealitky.cz/graphql/"

OFFER  = {"prodej": "PRODEJ", "pronajem": "PRONAJEM"}
ESTATE = {"byt": "BYT", "dum": "DUM", "pozemek": "POZEMEK"}
DISP   = {
    "1+kk": "DISP_1_KK", "1+1": "DISP_1_1",
    "2+kk": "DISP_2_KK", "2+1": "DISP_2_1",
    "3+kk": "DISP_3_KK", "3+1": "DISP_3_1",
    "4+kk": "DISP_4_KK", "4+1": "DISP_4_1",
    "5+kk": "DISP_5_KK", "5+1": "DISP_5_1",
    "6+":   "DISP_6_KK",
}
OSM = {
    "praha":   "r435514",
    "brno":    "r439857",
    "ostrava": "r442185",
    "plzen":   "r442169",
}
DISP_LABEL = {
    "DISP_1_KK": "1+kk", "DISP_1_1": "1+1",
    "DISP_2_KK": "2+kk", "DISP_2_1": "2+1",
    "DISP_3_KK": "3+kk", "DISP_3_1": "3+1",
    "DISP_4_KK": "4+kk", "DISP_4_1": "4+1",
    "DISP_5_KK": "5+kk", "DISP_5_1": "5+1",
    "DISP_6_KK": "6+",
}

_SLUG_STOPWORDS = {
    "nabidka", "prodej", "pronajem", "bytu", "domu", "pozemku",
    "byt", "dum", "pozemek", "kk", "garsoniera",
}


def _lokalita_z_uri(uri: str) -> str:
    """
    Z uri jako '1023689-nabidka-prodej-bytu-praha-5-smichov-stroupeznickeho'
    sestavíme 'Praha 5, Smichov, Stroupeznickeho'.
    """
    if not uri:
        return ""
    parts = uri.split("-")
    if parts and parts[0].isdigit():
        parts = parts[1:]
    cleaned = []
    skip_next = False
    for i, p in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        pl = p.lower()
        if pl in _SLUG_STOPWORDS:
            continue
        if pl.isdigit() and len(pl) == 1 and i + 1 < len(parts):
            nxt = parts[i + 1].lower()
            if nxt == "kk" or (nxt.isdigit() and len(nxt) == 1):
                skip_next = True
                continue
        cleaned.append(p)
    if not cleaned:
        return ""
    cap = [w.capitalize() if not w.isdigit() else w for w in cleaned]
    # Sloučíme "Praha 5" do jednoho tokenu
    if len(cap) >= 2 and cap[1].isdigit():
        mesto = f"{cap[0]} {cap[1]}"
        zbytek = cap[2:]
    else:
        mesto = cap[0]
        zbytek = cap[1:]
    if not zbytek:
        return mesto
    return f"{mesto}, " + ", ".join(zbytek)


def _sestav_nazev(disp_label: str, plocha, lokalita: str) -> str:
    return sestav_nazev(disp_label, plocha, lokalita, fallback="Inzerát Bezrealitky")


def _build_args(h, limit, offset, with_region=True, region_format="r_prefix"):
    """Sestaví GraphQL argumenty jako INLINE string (žádné $proměnné).

    region_format:
      'r_prefix'  -> ["r435514"]  (osm node prefix)
      'numeric'   -> ["435514"]   (jen číslo)
      'osm_url'   -> ["relation/435514"]
    """
    a = []
    a.append(f"offerType:[{OFFER.get(h.get('typ','prodej'),'PRODEJ')}]")
    a.append(f"estateType:[{ESTATE.get(h.get('nemovitost','byt'),'BYT')}]")
    if h.get("cena_max"): a.append(f"priceTo:{int(h['cena_max'])}")
    if h.get("cena_min"): a.append(f"priceFrom:{int(h['cena_min'])}")
    disp = h.get("dispozice", [])
    if disp:
        kody = [DISP[d] for d in disp if d in DISP]
        if kody:
            a.append(f"disposition:[{','.join(kody)}]")
    if with_region:
        osm_raw = OSM.get(h.get("lokalita", "").lower())
        if osm_raw:
            if region_format == "r_prefix":
                osm_val = osm_raw  # 'r435514'
            elif region_format == "numeric":
                osm_val = osm_raw.lstrip("r")  # '435514'
            elif region_format == "osm_url":
                osm_val = f"relation/{osm_raw.lstrip('r')}"
            else:
                osm_val = osm_raw
            a.append(f'regionOsmIds:["{osm_val}"]')
    a.append(f"limit:{limit}")
    a.append(f"offset:{offset}")
    a.append("order:TIMEORDER_DESC")
    return ", ".join(a)


# Pole "list { ... }" – varianty od nejbohatší po nejhubenější.
# (label, fields, has_address, with_region, region_format, image_variables)
# Schéma vyžaduje pro pole publicImages.url argument filter: ImageFilter!

def _detect_image_strategy(fields):
    """Vrátí True/False jestli query obsahuje publicImages pole."""
    return "publicImages" in fields


# (label, fields, has_address, with_region, region_format)
# Z introspekce schématu (15.5.2026):
#   - ImageFilter je ENUM s hodnotami RECORD_THUMB (600x400), RECORD_MAIN (1800x1200), ...
#   - regionOsmIds je [ID] ale evidentně neakceptuje "r435514" – proto klientský filter
LIST_FIELDS_VARIANTS = [
    # === Plná query: obrázky + adresa + gps (region zkusíme různě) ===
    ("img+region_r+address+gps",
     "id uri price surface disposition address(locale: CS) gps { lat lng } "
     "publicImages(limit: 3) { url(filter: RECORD_THUMB) }",
     True, True, "r_prefix"),
    ("img+region_num+address+gps",
     "id uri price surface disposition address(locale: CS) gps { lat lng } "
     "publicImages(limit: 3) { url(filter: RECORD_THUMB) }",
     True, True, "numeric"),
    ("img+noregion+address+gps",
     "id uri price surface disposition address(locale: CS) gps { lat lng } "
     "publicImages(limit: 3) { url(filter: RECORD_THUMB) }",
     True, False, "r_prefix"),

    # === Stejné bez gps ===
    ("img+region_r+address",
     "id uri price surface disposition address(locale: CS) "
     "publicImages(limit: 3) { url(filter: RECORD_THUMB) }",
     True, True, "r_prefix"),
    ("img+noregion+address",
     "id uri price surface disposition address(locale: CS) "
     "publicImages(limit: 3) { url(filter: RECORD_THUMB) }",
     True, False, "r_prefix"),

    # === Záchrana – bez obrázků ===
    ("region_r+address",
     "id uri price surface disposition address(locale: CS) gps { lat lng }",
     True, True, "r_prefix"),
    ("noregion+address",
     "id uri price surface disposition address(locale: CS)",
     True, False, "r_prefix"),
    ("noregion",
     "id uri price surface disposition",
     False, False, "r_prefix"),
]


def _post(client, query, headers, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    return client.post(URL, json=payload, headers=headers)


def _try_get_full_error(body):
    errs = []
    for err in body.get("errors", [])[:5]:
        msg = err.get("message", "?")
        locs = err.get("locations", [])
        if locs:
            msg += f" @line {locs[0].get('line', '?')}"
        errs.append(msg)
    return errs


def scrape(cfg, headers):
    h = cfg["hledani"]
    # Menší batch (20 místo 40) - bezrealitky API je pomalé s publicImages
    inzeraty, offset, limit = [], 0, 20

    gql_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
        "Origin": "https://www.bezrealitky.cz",
        "Referer": "https://www.bezrealitky.cz/",
        "User-Agent": headers.get(
            "User-Agent",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        ),
    }

    def build_query(list_fields, args):
        """Sestaví GraphQL query – vše inline, žádné proměnné."""
        return f"{{ listAdverts({args}) {{ list {{ {list_fields} }} totalCount }} }}"

    # Bezrealitky API je pomalé – delší timeout než globální
    timeout = max(cfg["scraper"]["timeout_sekund"], 60)
    with httpx.Client(timeout=timeout) as cl:
        active_fields = None
        has_address = False
        active_with_region = True
        active_region_format = "r_prefix"

        for label, fields, addr, with_region, region_format in LIST_FIELDS_VARIANTS:
            test_args = _build_args(h, limit=1, offset=0,
                                    with_region=with_region,
                                    region_format=region_format)
            q = build_query(fields, test_args)
            try:
                r = _post(cl, q, gql_headers)
                body = r.json()
                if not body.get("errors") and body.get("data"):
                    total = body["data"].get("listAdverts", {}).get("totalCount", 0)
                    if with_region and total == 0:
                        log.info(f"Bezrealitky varianta='{label}': totalCount=0, zkouším další")
                        continue
                    active_fields = fields
                    has_address = addr
                    active_with_region = with_region
                    active_region_format = region_format
                    log.info(f"Bezrealitky: použitá varianta='{label}' (total={total})")
                    break
                else:
                    for err in _try_get_full_error(body):
                        log.info(f"Bezrealitky varianta='{label}' chyba: {err}")
            except Exception as e:
                log.info(f"Bezrealitky probing '{label}': {e}")

        if not active_fields:
            log.error("Bezrealitky: žádná z variant query neprošla – viz výše")
            try:
                r = _post(cl, "{ __typename }", gql_headers)
                log.info(f"Bezrealitky diag __typename: status={r.status_code} body={r.text[:200]}")
            except Exception as e:
                log.info(f"Bezrealitky diag error: {e}")
            return []

        # Klientský filter pokud serverový region nesedí
        client_filter = (not active_with_region) and h.get("lokalita")
        if client_filter:
            log.info(f"Bezrealitky: bez serverového regionu, filtruji klientsky podle '{h['lokalita']}'")
        lokalita_lower = (h.get("lokalita") or "").lower().strip()
        has_images = _detect_image_strategy(active_fields)
        if has_images:
            log.info("Bezrealitky: obrázky z publicImages (RECORD_THUMB 600x400)")

        # Hlavní stránkování
        while True:
            args = _build_args(h, limit=limit, offset=offset,
                               with_region=active_with_region,
                               region_format=active_region_format)
            q = build_query(active_fields, args)
            # 3× retry s exponenciálním backoffem (2s, 5s, 10s) - API občas timeoutuje
            body = None
            for pokus in range(3):
                try:
                    r = _post(cl, q, gql_headers)
                    r.raise_for_status()
                    body = r.json()
                    break
                except Exception as e:
                    wait = (2, 5, 10)[pokus]
                    if pokus < 2:
                        log.warning(f"Bezrealitky offset {offset} pokus {pokus+1}/3: {e}, čekám {wait}s")
                        time.sleep(wait)
                    else:
                        log.error(f"Bezrealitky offset {offset}: vzdávám se po 3 pokusech ({e})")

            if body is None:
                # Nemůžeme získat data, ale zkusíme pokračovat dalším offsetem
                offset += limit
                if offset >= 500:
                    break
                continue

            if body.get("errors"):
                log.warning(f"Bezrealitky chyba: {body['errors'][0].get('message','?')}")
                break

            result = body.get("data", {}).get("listAdverts", {})
            items  = result.get("list", [])
            total  = result.get("totalCount", 0)
            if not items:
                break

            for item in items:
                uri  = item.get("uri", "")
                url  = f"https://www.bezrealitky.cz/nemovitosti-byty-domy/{uri}" if uri else ""
                gps  = item.get("gps") or {}

                adresa = (item.get("address") or "").strip() if has_address else ""
                if not adresa:
                    adresa = _lokalita_z_uri(uri)

                # Klientský filter – když nemáme server-side region, zahodíme co nesedí
                if client_filter and lokalita_lower:
                    haystack = f"{adresa} {uri}".lower()
                    if lokalita_lower not in haystack:
                        continue

                disp_raw   = item.get("disposition", "")
                disp_label = DISP_LABEL.get(disp_raw, disp_raw)
                plocha     = item.get("surface")
                nazev      = _sestav_nazev(disp_label, plocha, adresa)

                # Obrázky z publicImages
                imgs = []
                if has_images:
                    arr = item.get("publicImages") or []
                    if isinstance(arr, list):
                        for im in arr[:3]:
                            if isinstance(im, dict) and im.get("url"):
                                imgs.append(im["url"])

                inzeraty.append({
                    "portal": "bezrealitky", "url": url, "nazev": nazev,
                    "cena": item.get("price"), "plocha": plocha,
                    "lokalita": adresa, "dispozice": disp_label,
                    "popis": None, "obrazky": imgs,
                    "lat": gps.get("lat") if isinstance(gps, dict) else None,
                    "lon": gps.get("lng") if isinstance(gps, dict) else None,
                })

            offset += limit
            log.info(f"Bezrealitky: {len(inzeraty)} ulozeno / offset {offset}/{total}")
            if offset >= total or offset >= 500:
                break
            base = cfg["scraper"]["delay_sekund"]
            time.sleep(base + random.uniform(0, base * 0.6))

    log.info(f"Bezrealitky: {len(inzeraty)} celkem")
    return inzeraty
