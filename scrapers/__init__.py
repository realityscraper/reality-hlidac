"""scrapers package – společné utility."""


def sestav_nazev(dispozice, plocha, lokalita, fallback="Inzerát") -> str:
    """
    Sjednocený formát názvu pro všechny portály:
        '3+kk · 70 m² · Praha 5, Smíchov'
    """
    parts = []
    if dispozice:
        parts.append(str(dispozice))
    if plocha:
        try:
            parts.append(f"{int(plocha)} m²")
        except (TypeError, ValueError):
            pass
    if lokalita:
        lok = str(lokalita).strip()
        if len(lok) > 60:
            lok = lok[:58] + "…"
        parts.append(lok)
    return " · ".join(parts) if parts else fallback
