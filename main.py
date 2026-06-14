#!/usr/bin/env python3
"""
Reality Hlídač – hlavní skript.

Hledá config v tomto pořadí:
  1. --config CESTA
  2. /etc/reality/config.yaml
  3. ./config.yaml (relativně k main.py)

Logy: /var/log/reality/scraper.log (fallback: ./scraper.log)
"""
import argparse, logging, sys, time, os
from pathlib import Path

import yaml
import db
import generator
import geocoding
from scrapers import sreality, bezrealitky, idnes, ceskereality


def _setup_logging():
    handlers = [logging.StreamHandler(sys.stdout)]
    # Zkus napsat log do souboru, pokud lze (lokálně), jinak jen stdout (GitHub Actions)
    for log_dir in [os.getcwd(), "/var/log/reality"]:
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            log_path = Path(log_dir) / "scraper.log"
            log_path.touch(exist_ok=True)
            handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
            break
        except (PermissionError, OSError):
            continue
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def _najdi_config(arg_cesta=None):
    if arg_cesta:
        p = Path(arg_cesta)
        if not p.exists():
            sys.exit(f"❌ Config nenalezen: {p}")
        return p
    # Priorita: ./config.yaml (vedle main.py) > /etc/reality/config.yaml > ./config.yaml (cwd)
    for p in [Path(__file__).parent / "config.yaml",
              Path("/etc/reality/config.yaml"),
              Path("config.yaml")]:
        if p.exists():
            return p
    sys.exit(
        "❌ Config nenalezen. Vytvořte config.yaml vedle main.py "
        "nebo použijte --config CESTA"
    )


def nacti_cfg(cesta):
    with open(cesta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _headers(cfg):
    return {
        "User-Agent": cfg["scraper"]["user_agent"],
        "Accept-Language": "cs-CZ,cs;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }


def _pasuje(inz, h):
    """Ověří zda inzerát splňuje filtry."""
    cena  = inz.get("cena")
    disp  = (inz.get("dispozice") or "").lower()

    if h.get("cena_max") and cena and cena > h["cena_max"]:
        return False
    if h.get("cena_min") and cena and cena < h["cena_min"]:
        return False
    if h.get("dispozice") and disp:
        povolene = [d.lower() for d in h["dispozice"]]
        if not any(p in disp for p in povolene):
            return False
    return True


def _vypis_souhrn(log, vysledky):
    ok      = [(p, n) for p, n, e in vysledky if not e and n > 0]
    prazdne = [(p, n) for p, n, e in vysledky if not e and n == 0]
    chyby   = [(p, e) for p, n, e in vysledky if e]
    sep = "─" * 52
    log.info(sep)
    log.info("SOUHRN SCRAPOVÁNÍ")
    log.info(sep)
    if ok:
        log.info(f"✅ Úspěšné ({len(ok)}):")
        for portal, pocet in ok:
            log.info(f"   {portal:<20} {pocet} inzerátů")
    if prazdne:
        log.warning(f"⚠️  Nulový výsledek ({len(prazdne)}):")
        for portal, _ in prazdne:
            log.warning(f"   {portal}")
    if chyby:
        log.error(f"❌ Chyby ({len(chyby)}):")
        for portal, err in chyby:
            log.error(f"   {portal:<20} {err}")
    log.info(sep)


def iterace(cfg, log):
    conn = db.init(cfg["databaze"]["soubor"])
    p    = cfg["portaly"]
    hd   = _headers(cfg)
    h    = cfg["hledani"]
    nove = 0

    SCRAPERY = [
        ("sreality",     p.get("sreality"),     lambda: sreality.scrape(cfg, hd)),
        ("bezrealitky",  p.get("bezrealitky"),  lambda: bezrealitky.scrape(cfg, hd)),
        ("idnes",        p.get("idnes"),        lambda: idnes.scrape(cfg, hd)),
        ("ceskereality", p.get("ceskereality"), lambda: ceskereality.scrape(cfg, hd)),
    ]

    vysledky = []
    for klic, aktivni, fn in SCRAPERY:
        if not aktivni:
            continue
        log.info(f"=== {klic} ===")
        chyba, pocet = "", 0
        try:
            items = fn()
            pocet = len(items)
            for inz in items:
                if inz.get("url") and _pasuje(inz, h) and db.uloz(conn, inz):
                    nove += 1
        except Exception as e:
            chyba = str(e)
            log.error(f"{klic} selhalo: {e}", exc_info=True)
        vysledky.append((klic, pocet, chyba))

    _vypis_souhrn(log, vysledky)

    # Geocoding pouze pokud je v configu povolený (default vypnuto)
    if cfg.get("geocoding", {}).get("zapnuto", False):
        geocoding.geocode_batch(conn, limit=40)
    else:
        log.info("Geocoding přeskočen (v configu vypnutý)")

    # Vyčistit staré inzeráty
    db.vycisti(conn, cfg["databaze"]["uchovavat_dni"])

    # Generovat HTML
    log.info(f"Nových inzerátů: {nove} – generuji HTML…")
    stranky, celkem = generator.generuj(conn, cfg)
    log.info(f"Hotovo – {stranky} stránek, {celkem} inzerátů → {cfg['web']['output_dir']}")
    conn.close()
    return nove


def main():
    ap = argparse.ArgumentParser(description="Reality Hlídač")
    ap.add_argument("--config", default=None, help="Cesta ke config.yaml")
    ap.add_argument("--daemon", action="store_true",
                    help="Opakuj každých N minut (dle config)")
    args = ap.parse_args()

    _setup_logging()
    log = logging.getLogger("main")

    cfg_path = _najdi_config(args.config)
    log.info(f"Config: {cfg_path}")
    cfg = nacti_cfg(cfg_path)

    # Vytvoř datovou složku pokud DB cesta není absolutní
    db_path = Path(cfg["databaze"]["soubor"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if args.daemon:
        interval = cfg["scraper"]["interval_minut"] * 60
        log.info(f"Daemon – interval {cfg['scraper']['interval_minut']} min")
        while True:
            try:
                iterace(cfg, log)
            except Exception as e:
                log.error(f"Chyba iterace: {e}", exc_info=True)
            log.info(f"Čekám {cfg['scraper']['interval_minut']} minut…")
            time.sleep(interval)
    else:
        n = iterace(cfg, log)
        print(f"\n✅ Hotovo – {n} nových inzerátů")


if __name__ == "__main__":
    main()
