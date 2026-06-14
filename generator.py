"""
Generátor statických HTML stránek z SQLite databáze.
Generuje:
  - index.html      – přehled inzerátů (paginace)
  - statické HTML stránky s přehledem
  - data/stats.json – statistiky pro AJAX refresh
"""
import json, math
from datetime import datetime
from pathlib import Path
import db as db_mod

PORTAL_LABEL = {
    "sreality":    "Sreality",
    "bezrealitky": "Bezrealitky",
    "idnes":       "iDnes Reality",
    "ceskereality":"České reality",
}
PORTAL_COLOR = {
    "sreality":    "#1a56db",
    "bezrealitky": "#0e9f6e",
    "idnes":       "#dc2626",
    "ceskereality":"#15803d",
}


def fmt_cena(cena):
    if not cena:
        return "Cena neuvedena"
    if cena >= 1_000_000:
        return f"{cena/1_000_000:.1f} mil. Kč".replace(".", ",")
    return f"{cena:,} Kč".replace(",", "\u00a0")


def fmt_datum(iso):
    try:
        d = datetime.fromisoformat(iso)
        return d.strftime("%-d. %-m. %Y %H:%M")
    except Exception:
        return iso or ""


def badge(portal):
    label = PORTAL_LABEL.get(portal, portal)
    color = PORTAL_COLOR.get(portal, "#6b7280")
    return (f'<span class="badge" style="background:{color}20;color:{color};'
            f'border-color:{color}40">{label}</span>')


def card_html(inz):
    imgs = json.loads(inz.get("obrazky") or "[]")
    if imgs:
        img_html = (f'<div class="card-img">'
                    f'<img src="{imgs[0]}" alt="" loading="lazy" '
                    f'onerror="this.closest(\'.card-img\').style.display=\'none\'">'
                    f'</div>')
    else:
        img_html = '<div class="card-img card-img--empty"><span>🏠</span></div>'

    # Vždy zobrazit všechny 3 atributy v jedné řadě (chybějící označit pomlčkou)
    disp = inz.get("dispozice") or "—"
    plocha = f"{int(inz['plocha'])} m²" if inz.get("plocha") else "—"
    lok = inz.get("lokalita") or "—"
    # Lokalitu zkrátit na rozumnou délku
    if len(lok) > 38:
        lok = lok[:36] + "…"
    meta = [
        f'<span>🚪 {disp}</span>',
        f'<span>📐 {plocha}</span>',
        f'<span title="{inz.get("lokalita") or ""}">📍 {lok}</span>',
    ]

    popis_html = ""
    if inz.get("popis"):
        popis_html = f'<p class="card-popis">{inz["popis"][:160]}…</p>'

    map_btn = ""

    return f"""
<a class="card" href="{inz['url']}" target="_blank" rel="noopener">
  {img_html}
  <div class="card-body">
    <div class="card-top">
      {badge(inz['portal'])}
      <span class="card-date">{fmt_datum(inz['datum'])}</span>
      {map_btn}
    </div>
    <h2 class="card-title">{inz.get('nazev') or 'Bez názvu'}</h2>
    <div class="card-price">{fmt_cena(inz.get('cena'))}</div>
    <div class="card-meta">{''.join(meta)}</div>
    {popis_html}
  </div>
</a>"""


CSS = """
:root {
  --bg: #0f1117; --surface: #1a1d27; --surface2: #22263a;
  --border: #2e3350; --text: #e8eaf6; --muted: #7986cb;
  --accent: #5c6bc0; --accent2: #7c4dff; --green: #66bb6a;
  --font: 'Inter', 'Segoe UI', system-ui, sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: var(--font);
       min-height: 100vh; }
a { color: inherit; text-decoration: none; }

/* NAV */
nav { background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 0 24px; display: flex; align-items: center; gap: 24px;
      height: 56px; position: sticky; top: 0; z-index: 100; }
.nav-brand { font-size: 1.1rem; font-weight: 700; color: var(--accent2);
             letter-spacing: -0.02em; }
.nav-links { display: flex; gap: 4px; }
.nav-links a { padding: 6px 14px; border-radius: 8px; font-size: 0.85rem;
               color: var(--muted); transition: all .2s; }
.nav-links a:hover, .nav-links a.active { background: var(--surface2);
                                          color: var(--text); }
.nav-stats { margin-left: auto; font-size: 0.8rem; color: var(--muted); }

/* FILTERS */
.filters { padding: 16px 24px; background: var(--surface);
           border-bottom: 1px solid var(--border);
           display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.filter-group { display: flex; gap: 6px; align-items: center; }
.filter-group label { font-size: 0.8rem; color: var(--muted); white-space: nowrap; }
.filter-group select, .filter-group input {
  background: var(--surface2); border: 1px solid var(--border);
  color: var(--text); padding: 6px 10px; border-radius: 8px; font-size: 0.85rem; }
.filter-btn { background: var(--accent); color: #fff; border: none;
              padding: 7px 16px; border-radius: 8px; font-size: 0.85rem;
              cursor: pointer; }

/* GRID */
.container { max-width: 1400px; margin: 0 auto; padding: 24px; }
.grid { display: grid;
        grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
        gap: 16px; }

/* CARD */
.card { background: var(--surface); border: 1px solid var(--border);
        border-radius: 14px; overflow: hidden; display: flex;
        flex-direction: column; transition: all .25s; cursor: pointer; }
.card:hover { border-color: var(--accent); transform: translateY(-2px);
              box-shadow: 0 8px 32px rgba(92,107,192,.15); }
.card-img { height: 180px; overflow: hidden; }
.card-img img { width: 100%; height: 100%; object-fit: cover;
                transition: transform .3s; }
.card:hover .card-img img { transform: scale(1.03); }
.card-img--empty { background: var(--surface2); display: flex;
                   align-items: center; justify-content: center;
                   font-size: 2.5rem; }
.card-body { padding: 14px; flex: 1; display: flex; flex-direction: column; gap: 8px; }
.card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.badge { font-size: 0.72rem; font-weight: 600; padding: 3px 8px;
         border-radius: 6px; border: 1px solid; white-space: nowrap; }
.card-date { font-size: 0.75rem; color: var(--muted); margin-left: auto; }
.card-title { font-size: 0.9rem; font-weight: 600; line-height: 1.4;
              display: -webkit-box; -webkit-line-clamp: 2;
              -webkit-box-orient: vertical; overflow: hidden; }
.card-price { font-size: 1.15rem; font-weight: 700; color: var(--green); }
.card-meta { display: flex; gap: 10px; flex-wrap: wrap; }
.card-meta span { font-size: 0.78rem; color: var(--muted); }
.card-popis { font-size: 0.8rem; color: var(--muted); line-height: 1.5;
              display: -webkit-box; -webkit-line-clamp: 2;
              -webkit-box-orient: vertical; overflow: hidden; }

/* PAGINATION */
.pagination { display: flex; justify-content: center; gap: 8px;
              padding: 32px 0; flex-wrap: wrap; }
.pagination a, .pagination span {
  padding: 8px 14px; border-radius: 8px; font-size: 0.85rem;
  border: 1px solid var(--border); }
.pagination a { color: var(--muted); transition: all .2s; }
.pagination a:hover { background: var(--surface2); color: var(--text); }
.pagination .current { background: var(--accent); color: #fff; border-color: var(--accent); }
.pagination .dots { color: var(--muted); border-color: transparent; }

/* HEADER */
.page-header { padding: 24px 24px 0; display: flex; align-items: center;
               justify-content: space-between; flex-wrap: wrap; gap: 12px; }
.page-header h1 { font-size: 1.3rem; font-weight: 700; }
.page-header .count { font-size: 0.85rem; color: var(--muted); }

/* EMPTY */
.empty { text-align: center; padding: 64px 24px; color: var(--muted); }
.empty .icon { font-size: 3rem; margin-bottom: 16px; }

/* PORTAL STATS */
.portal-stats { display: flex; gap: 8px; flex-wrap: wrap;
                padding: 16px 24px; }
.portal-stat { display: flex; align-items: center; gap: 6px;
               background: var(--surface); border: 1px solid var(--border);
               padding: 5px 12px; border-radius: 20px; font-size: 0.8rem; }
.portal-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
"""

NAV_HTML = """<nav>
  <span class="nav-brand">🏠 Reality Hlídač</span>
  <span class="nav-stats" id="nav-stats">{stats_text}</span>
</nav>"""


def _portal_stats_html(portaly):
    parts = []
    for p in portaly:
        color = PORTAL_COLOR.get(p["portal"], "#6b7280")
        label = PORTAL_LABEL.get(p["portal"], p["portal"])
        parts.append(
            f'<div class="portal-stat">'
            f'<div class="portal-dot" style="background:{color}"></div>'
            f'{label} <strong>{p["n"]}</strong></div>'
        )
    return '<div class="portal-stats">' + "".join(parts) + "</div>"


def _paginace(strana, celkem_stran, prefix="index"):
    if celkem_stran <= 1:
        return ""
    parts = []

    def link(p, label, css=""):
        fname = "index.html" if p == 1 else f"{prefix}{p}.html"
        return f'<a href="{fname}" class="{css}">{label}</a>'

    if strana > 1:
        parts.append(link(strana - 1, "‹ Předchozí"))

    for p in range(1, celkem_stran + 1):
        if p == strana:
            parts.append(f'<span class="current">{p}</span>')
        elif p == 1 or p == celkem_stran or abs(p - strana) <= 2:
            parts.append(link(p, str(p)))
        elif abs(p - strana) == 3:
            parts.append('<span class="dots">…</span>')

    if strana < celkem_stran:
        parts.append(link(strana + 1, "Další ›"))

    return '<div class="pagination">' + "".join(parts) + "</div>"


def generuj(conn, cfg):
    out = Path(cfg["web"]["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)

    na_stranu = cfg["web"].get("inzeratu_na_stranu", 24)
    nazev     = cfg["web"].get("nazev_stranky", "Reality Hlídač")
    vsechny   = db_mod.nacti_vse(conn)
    celkem    = len(vsechny)
    stats     = db_mod.statistiky(conn)
    aktu      = datetime.now().strftime("%-d. %-m. %Y %H:%M")

    # Uložit stats JSON pro live refresh
    with open(out / "data" / "stats.json", "w", encoding="utf-8") as f:
        json.dump({**stats, "aktualizovano": aktu}, f, ensure_ascii=False)

    # --- Stránky s přehledem inzerátů ---
    stranky = math.ceil(celkem / na_stranu) or 1
    for s in range(1, stranky + 1):
        offset = (s - 1) * na_stranu
        items = vsechny[offset:offset + na_stranu]
        cards = "\n".join(card_html(i) for i in items)
        fname = "index.html" if s == 1 else f"index{s}.html"
        stats_text = f"{celkem} inzerátů · aktualizováno {aktu}"
        nav = NAV_HTML.format(stats_text=stats_text)
        pag = _paginace(s, stranky)
        pstat = _portal_stats_html(stats["portaly"])
        empty = ('<div class="empty"><div class="icon">🔍</div>'
                 '<p>Žádné inzeráty nenalezeny. Zkontrolujte config nebo počkejte na první scraping.</p></div>'
                 if not items else "")

        html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{nazev} – strana {s}</title>
  <style>{CSS}</style>
</head>
<body>
{nav}
{pstat}
<div class="page-header">
  <h1>{nazev}</h1>
  <span class="count">Zobrazeno {len(items)} z {celkem} inzerátů · strana {s}/{stranky}</span>
</div>
<div class="container">
  <div class="grid">{cards}</div>
  {empty}
  {pag}
</div>
</body>
</html>"""
        (out / fname).write_text(html, encoding="utf-8")

    return stranky, celkem

