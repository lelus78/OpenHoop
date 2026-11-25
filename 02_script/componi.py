"""Composizione: più disegni del catalogo + scritte (lettering.py) messi insieme in un telaio -> un solo PES.

spec = {"elementi": [
   {"tipo": "disegno", "percorso": "...pes", "x": 0, "y": -20, "scala": 1, "rot": 0, "specchia": false},
   {"tipo": "testo", "testo": "Sofia", "font": "pacificlo", "altezza": 15, "spaz": 0, "allinea": "centro",
    "x": 0, "y": 40, "rot": 0, "colore": {"hex", "nome", "num"}, "colori_font": false}]}
Coordinate in mm, centro del telaio = (0, 0), y verso il basso. L'ordine degli elementi = ordine di ricamo.
"""
import math

import pyembroidery as pe
import pes_brother  # noqa: F401,E402  -- PEC senza il n. 64 «Applique» (vedi pes_brother.py)

import lettering as LET


def _trasforma(x, y, el):
    s = float(el.get("scala", 1) or 1)
    x, y = x * s, y * s
    if el.get("specchia"):
        x = -x
    r = math.radians(float(el.get("rot", 0) or 0))
    if r:
        x, y = x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)
    return x + float(el.get("x", 0)), y + float(el.get("y", 0))


def _filo(hexc, nome="", num=""):
    t = pe.EmbThread()
    t.set_hex_color(hexc)
    t.description = nome or hexc
    t.catalog_number = num
    return t


def blocchi_disegno(pat, el):
    """[(thread, [(cmd, x, y)])] del disegno, centrato sul suo riquadro e trasformato (mm)."""
    x0, y0, x1, y1 = pat.bounds()
    cx, cy = (x0 + x1) / 20, (y0 + y1) / 20
    out = []
    for blocco, th in pat.get_as_colorblocks():
        punti = []
        for x, y, c in blocco:
            c &= 0xFF
            if c in (pe.STITCH, pe.JUMP, pe.TRIM):
                punti.append((c,) + _trasforma(x / 10 - cx, y / 10 - cy, el))
        if punti:
            out.append((th if th is not None else _filo("#333333"), punti))
    return out


def blocchi_testo(el):
    r = LET.testo(el["font"], el.get("testo", ""), float(el.get("altezza", 15)), float(el.get("spaz", 0) or 0),
                  allinea=el.get("allinea", "centro"), colori_font=bool(el.get("colori_font")),
                  punto=el.get("punto", "satin"), arco=float(el.get("arco", 0) or 0))
    col = el.get("colore") or {"hex": "#1c2a4a", "nome": "Blu scuro", "num": ""}
    out = []
    e2 = dict(el, scala=1)                                     # la scala del testo è già nell'altezza
    for hexc, tratti in r["blocchi"]:
        th = _filo(hexc, hexc) if el.get("colori_font") else _filo(col["hex"], col.get("nome", ""), col.get("num", ""))
        punti = []
        for tr in tratti:
            x, y = _trasforma(tr[0][0], tr[0][1], e2)
            punti += [(pe.TRIM, x, y), (pe.JUMP, x, y)]
            # punto di fermo all'inizio e alla fine di ogni tratto (dopo un taglio il filo deve tenere)
            pts = [_trasforma(px, py, e2) for px, py in tr]
            punti += [(pe.STITCH,) + p for p in _fermo(pts)]
        out.append((th, punti))
    return out, r


def _fermo(pts):
    if len(pts) < 2:
        return pts
    def piccolo(a, b, d=0.4):
        L = math.dist(a, b) or 1
        return (a[0] + (b[0] - a[0]) / L * d, a[1] + (b[1] - a[1]) / L * d)
    a, b = pts[0], pts[1]
    z, w = pts[-1], pts[-2]
    return [a, piccolo(a, b), a, piccolo(a, b), a] + pts[1:] + [piccolo(z, w), z, piccolo(z, w), z]


def usa_ricalcolo(el, decidi=None):
    """«auto» (intelligente): ricalcola i punti solo se la scala semplice non va bene per QUESTO disegno
    (decidi(percorso, scala) dal server); senza decidi: oltre ±10%."""
    s = float(el.get("scala", 1) or 1)
    modo = el.get("ric", "auto")
    if abs(s - 1) < 0.02 or modo == "no":
        return False
    if modo == "si":
        return True
    return decidi(el["percorso"], s) if decidi else abs(s - 1) > 0.10


def costruisci(spec, leggi_pes, ricalcolato=None, decidi=None):
    """leggi_pes(percorso) -> EmbPattern; ricalcolato(percorso, scala) -> EmbPattern già alla misura nuova
    con i punti rigenerati (ricalcola.py). Restituisce (EmbPattern, info)."""
    out = pe.EmbPattern()
    ultimo = None
    info_el = []
    for el in spec.get("elementi", []):
        if el.get("tipo") == "disegno":
            if ricalcolato is not None and usa_ricalcolo(el, decidi):
                bl = blocchi_disegno(ricalcolato(el["percorso"], float(el["scala"])), dict(el, scala=1))
                extra = {"ricalcolato": True}
            else:
                bl = blocchi_disegno(leggi_pes(el["percorso"]), el)
                extra = {"ricalcolato": False}
        elif el.get("tipo") == "testo" and el.get("testo", "").strip():
            bl, r = blocchi_testo(el)
            extra = {"mancano": r["mancano"], "scala": r["scala"], "min": r["min"], "max": r["max"]}
        else:
            continue
        xs = [p[1] for _, pts in bl for p in pts if p[0] == pe.STITCH]
        ys = [p[2] for _, pts in bl for p in pts if p[0] == pe.STITCH]
        info_el.append(dict(extra, bbox=[round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)] if xs else None))
        for th, pts in bl:
            chiave = (th.hex_color(), th.catalog_number or "")
            if ultimo is None:
                out.add_thread(th)
            elif chiave != ultimo:                             # stesso filo di prima: niente cambio colore
                out.add_command(pe.COLOR_CHANGE)
                out.add_thread(th)
            else:
                out.add_stitch_absolute(pe.TRIM, pts[0][1] * 10, pts[0][2] * 10)
            ultimo = chiave
            for c, x, y in pts:
                out.add_stitch_absolute(c, x * 10, y * 10)
    out.add_command(pe.END)
    punti = sum(1 for s in out.stitches if (s[2] & 0xFF) == pe.STITCH)
    if punti:
        x0, y0, x1, y1 = out.bounds()
        box = [x0 / 10, y0 / 10, x1 / 10, y1 / 10]
    else:
        box = [0, 0, 0, 0]
    return out, {"punti": punti, "colori": len(out.threadlist), "bbox": [round(v, 1) for v in box],
                 "larg": round(box[2] - box[0], 1), "alt": round(box[3] - box[1], 1), "elementi": info_el}
