"""
Geocoding lokality přes Nominatim (OpenStreetMap) – bez API klíče.
Zpracovává max. limit inzerátů najednou.
"""
import time, logging, re
import httpx
import db as db_mod

log = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "RealityHlidac/1.0 (osobni-projekt)"}

# Regex pro detekci smysluplné lokality (musí obsahovat název ulice nebo čtvrti)
_RE_SMYSLUPLNA = re.compile(r'[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]{2,}', re.UNICODE)


def _je_smysluplna(text):
    """Vrátí True pokud text vypadá jako skutečná lokalita, ne jen čísla/metráž."""
    if not text or len(text) < 5:
        return False
    # Odmítni texty které jsou jen název bytu (obsahují m² nebo jsou bez písmen)
    if re.search(r'\d+\s*m[²2]', text):
        return False
    if re.search(r'prodej\s*bytu', text, re.I):
        return False
    # Musí obsahovat aspoň jedno slovo začínající velkým písmenem
    return bool(_RE_SMYSLUPLNA.search(text))


def _geocode_text(cl, text):
    """Geokóduje text. Vrací (lat, lon) nebo (None, None)."""
    # Zkrať na první smysluplnou část
    query = re.sub(r'\s+\d+\s*$', '', text).strip()
    if not _je_smysluplna(query):
        return None, None
    try:
        r = cl.get(NOMINATIM, params={
            "q": query + ", Praha, Česká republika",
            "format": "json",
            "limit": 1,
            "countrycodes": "cz",
        })
        r.raise_for_status()
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        log.debug(f"Geocoding '{query}': {e}")
    return None, None


def geocode_batch(conn, limit=40):
    """Geokóduje pending inzeráty. Max limit požadavků za běh."""
    pending = db_mod.pending_geo(conn, limit=limit)
    if not pending:
        return
    # Filtruj jen ty co mají smysluplnou lokalitu
    k_geocodovani = [r for r in pending if _je_smysluplna(r.get("lokalita", ""))]
    k_oznaceni_failed = [r for r in pending if not _je_smysluplna(r.get("lokalita", ""))]

    # Inzeráty bez lokality rovnou označ jako failed (přeskočíme API volání)
    for row in k_oznaceni_failed:
        db_mod.oznac_geo_failed(conn, row["hash_id"])

    if not k_geocodovani:
        log.info(f"Geocoding: 0 inzerátů k geolokaci ({len(k_oznaceni_failed)} bez lokality)")
        return

    log.info(f"Geocoding: {len(k_geocodovani)} inzerátů čeká na geolokaci")
    ok = 0
    with httpx.Client(headers=HEADERS, timeout=10, follow_redirects=True) as cl:
        for row in k_geocodovani:
            lat, lon = _geocode_text(cl, row.get("lokalita", ""))
            if lat and lon:
                db_mod.uloz_gps(conn, row["hash_id"], lat, lon)
                ok += 1
            else:
                db_mod.oznac_geo_failed(conn, row["hash_id"])
            time.sleep(1.1)
    log.info(f"Geocoding: {ok}/{len(k_geocodovani)} úspěšně geokódováno")
