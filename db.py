"""
Databázová vrstva – SQLite s WAL journalem.
"""
import sqlite3, hashlib, json
from datetime import datetime, timedelta
from pathlib import Path


def init(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    # DELETE mode (default): vše se zapisuje do jediného .db souboru.
    # WAL by vyžadoval commitovat i .db-wal / .db-shm což je composit křehký.
    c.execute("PRAGMA journal_mode=DELETE")
    c.executescript("""
        CREATE TABLE IF NOT EXISTS inzeraty (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            hash_id     TEXT UNIQUE NOT NULL,
            portal      TEXT NOT NULL,
            url         TEXT NOT NULL,
            nazev       TEXT,
            cena        INTEGER,
            plocha      REAL,
            lokalita    TEXT,
            dispozice   TEXT,
            popis       TEXT,
            obrazky     TEXT,
            lat         REAL,
            lon         REAL,
            geo_status  TEXT DEFAULT 'pending',
            datum       TEXT NOT NULL,
            datum_aktu  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hash  ON inzeraty(hash_id);
        CREATE INDEX IF NOT EXISTS idx_datum ON inzeraty(datum);
        CREATE INDEX IF NOT EXISTS idx_geo   ON inzeraty(geo_status);
        CREATE TABLE IF NOT EXISTS zmeny_cen (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            hash_id    TEXT NOT NULL,
            stara_cena INTEGER,
            nova_cena  INTEGER,
            datum      TEXT NOT NULL
        );
    """)
    # Migrace: přidej sloupce pokud DB existuje bez nich
    cols = {r[1] for r in c.execute("PRAGMA table_info(inzeraty)").fetchall()}
    for col, typ in [("lat", "REAL"), ("lon", "REAL"), ("geo_status", "TEXT DEFAULT 'pending'")]:
        if col not in cols:
            c.execute(f"ALTER TABLE inzeraty ADD COLUMN {col} {typ}")
    c.commit()
    return c


def _hash(portal, url):
    return hashlib.sha256(f"{portal}:{url}".encode()).hexdigest()[:16]


def uloz(c, inz):
    hid = _hash(inz["portal"], inz["url"])
    now = datetime.now().isoformat()
    row = c.execute("SELECT id, cena FROM inzeraty WHERE hash_id=?", (hid,)).fetchone()
    if row is None:
        lat = inz.get("lat")
        lon = inz.get("lon")
        geo_status = "ok" if (lat and lon) else "pending"
        c.execute("""INSERT INTO inzeraty
            (hash_id,portal,url,nazev,cena,plocha,lokalita,dispozice,popis,obrazky,
             lat,lon,geo_status,datum,datum_aktu)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, inz["portal"], inz["url"], inz.get("nazev"), inz.get("cena"),
             inz.get("plocha"), inz.get("lokalita"), inz.get("dispozice"),
             inz.get("popis"), json.dumps(inz.get("obrazky") or []),
             lat, lon, geo_status, now, now))
        c.commit()
        return True
    else:
        nova = inz.get("cena")
        if nova and row["cena"] and nova != row["cena"]:
            c.execute("INSERT INTO zmeny_cen (hash_id,stara_cena,nova_cena,datum) VALUES (?,?,?,?)",
                      (hid, row["cena"], nova, now))
            c.execute("UPDATE inzeraty SET cena=?,datum_aktu=? WHERE hash_id=?", (nova, now, hid))
            c.commit()
        return False


def nacti_vse(c, razeni="datum DESC", limit=None, offset=0, portal=None, dispozice=None,
              cena_min=None, cena_max=None):
    where, params = "WHERE 1=1", []
    if portal:
        where += " AND portal=?"; params.append(portal)
    if dispozice:
        where += " AND dispozice=?"; params.append(dispozice)
    if cena_min:
        where += " AND cena>=?"; params.append(cena_min)
    if cena_max:
        where += " AND cena<=?"; params.append(cena_max)
    sql = f"SELECT * FROM inzeraty {where} ORDER BY {razeni}"
    if limit:
        sql += f" LIMIT {limit} OFFSET {offset}"
    return [dict(r) for r in c.execute(sql, params).fetchall()]


def nacti_s_gps(c):
    return [dict(r) for r in c.execute(
        "SELECT * FROM inzeraty WHERE lat IS NOT NULL AND lon IS NOT NULL ORDER BY datum DESC"
    ).fetchall()]


def pending_geo(c, limit=50):
    return [dict(r) for r in c.execute(
        "SELECT hash_id, lokalita, nazev FROM inzeraty "
        "WHERE geo_status='pending' AND lokalita IS NOT NULL LIMIT ?", (limit,)
    ).fetchall()]


def uloz_gps(c, hash_id, lat, lon):
    c.execute("UPDATE inzeraty SET lat=?,lon=?,geo_status='ok' WHERE hash_id=?", (lat, lon, hash_id))
    c.commit()


def oznac_geo_failed(c, hash_id):
    c.execute("UPDATE inzeraty SET geo_status='failed' WHERE hash_id=?", (hash_id,))
    c.commit()


def statistiky(c):
    celkem    = c.execute("SELECT COUNT(*) FROM inzeraty").fetchone()[0]
    portaly   = c.execute("SELECT portal, COUNT(*) n FROM inzeraty GROUP BY portal ORDER BY n DESC").fetchall()
    nove_dnes = c.execute("SELECT COUNT(*) FROM inzeraty WHERE date(datum)=date('now')").fetchone()[0]
    na_mape   = c.execute("SELECT COUNT(*) FROM inzeraty WHERE lat IS NOT NULL").fetchone()[0]
    return {"celkem": celkem, "portaly": [dict(p) for p in portaly],
            "nove_dnes": nove_dnes, "na_mape": na_mape}


def vycisti(c, dni):
    hranice = (datetime.now() - timedelta(days=dni)).isoformat()
    c.execute("DELETE FROM inzeraty WHERE datum_aktu<?", (hranice,))
    c.commit()
