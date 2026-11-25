"""Patch automatiche: attorno a un disegno aggiunge il bordo da toppa (satin) e, se si vuole, il fondo ricamato.

Forme: contorno (segue il disegno a distanza fissa), cerchio, ovale, rettangolo, capsula, scudo, esagono, cuore.
Tipi:
  tessuto  = su feltro/twill: linea di piazzamento, STOP (si appoggia il tessuto), fissaggio, disegno, bordo satin.
  ricamato = fondo pieno a punto tatami, disegno sopra, bordo satin.
  bordo    = solo disegno + bordo (tessuto già intelaiato, es. feltro).
Unità interne: mm (pyembroidery usa 0,1 mm). Il bordo satin finisce esattamente sul taglio della patch.

uso: crea(pat, opzioni) -> (EmbPattern, info)   ;   anteprima(pat_patch, info, size) -> PIL.Image
"""
import math

import pyembroidery as pe
import pes_brother  # noqa: F401,E402  -- PEC senza il n. 64 «Applique» (vedi pes_brother.py)
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

FORME = ["contorno", "cerchio", "ovale", "rettangolo", "capsula", "scudo", "esagono", "cuore"]
TIPI = ["tessuto", "ricamato", "bordo"]


# ---------------- forma del disegno ----------------
def sagoma(pat):
    """Silhouette del disegno (mm, piena: senza buchi) dai punti veri."""
    import numpy as np
    from PIL import Image, ImageDraw
    from scipy import ndimage
    from shapely.geometry import box
    P = 0.4                                                    # griglia 0,4 mm (veloce anche con 50k punti)
    st = [(x / 10, y / 10, c & 0xFF) for x, y, c in pat.stitches]
    xy = [(x, y) for x, y, c in st if c == 0]
    if len(xy) < 2:
        raise ValueError("disegno vuoto")
    x0, y0 = min(p[0] for p in xy) - 2, min(p[1] for p in xy) - 2
    W = int((max(p[0] for p in xy) - x0 + 2) / P) + 2
    H = int((max(p[1] for p in xy) - y0 + 2) / P) + 2
    im = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(im)
    prev = None
    for x, y, c in st:
        p = ((x - x0) / P, (y - y0) / P)
        if c == 0 and prev is not None and math.dist(prev, p) * P < 15:
            dr.line([prev, p], fill=255, width=2)
        prev = p if c in (0, 1) else None
    m = ndimage.binary_fill_holes(ndimage.binary_dilation(np.asarray(im) > 0, iterations=1))
    scatole = []
    for r in range(H):                                         # righe di pixel pieni -> rettangoli -> unione
        riga = np.flatnonzero(np.diff(np.concatenate(([0], m[r].astype(np.int8), [0]))))
        for a, b in zip(riga[::2], riga[1::2]):
            scatole.append(box(x0 + a * P, y0 + r * P, x0 + b * P, y0 + (r + 1) * P))
    g = unary_union(scatole).simplify(0.15)
    parti = [g] if g.geom_type == "Polygon" else list(g.geoms)
    return unary_union([Polygon(p.exterior) for p in parti if p.area > 0.5])


def _contorno(sil, dist):
    """Contorno a distanza `dist` che unisce le parti staccate (chiusura morfologica crescente)."""
    for chiudi in (3, 6, 10, 16):
        g = sil.buffer(dist + chiudi, quad_segs=8).buffer(-chiudi, quad_segs=8)
        if g.geom_type == "Polygon":
            return Polygon(g.exterior)
    return sil.convex_hull.buffer(dist, quad_segs=8)


def _modello(forma):
    """Forma unitaria (larga ~2, alta ~2, centrata in 0) — y verso il basso come nei file da ricamo."""
    if forma in ("cerchio", "ovale"):
        return Point(0, 0).buffer(1, quad_segs=32)
    if forma == "rettangolo":
        return Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1)])
    if forma == "capsula":
        return LineString([(-0.5, 0), (0.5, 0)]).buffer(0.5, quad_segs=24)        # stadio 2 x 1
    if forma == "esagono":
        return Polygon([(math.cos(a), math.sin(a)) for a in [math.pi / 6 + k * math.pi / 3 for k in range(6)]])
    if forma == "scudo":                                       # lati dritti, fondo a punta (curva di Bézier)
        dx = [(1 - t) ** 2 * 1 + 2 * (1 - t) * t * 1 + t * t * 0 for t in [k / 16 for k in range(17)]]
        dy = [(1 - t) ** 2 * 0.2 + 2 * (1 - t) * t * 0.75 + t * t * 1 for t in [k / 16 for k in range(17)]]
        destra = list(zip(dx, dy))
        return Polygon([(-1, -1), (1, -1)] + destra + [(-x, y) for x, y in reversed(destra[:-1])])
    if forma == "cuore":
        pts = []
        for k in range(120):
            t = 2 * math.pi * k / 120
            x = 16 * math.sin(t) ** 3
            y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
            pts.append((x / 16, y / 16))
        return Polygon(pts).buffer(0)
    raise ValueError("forma sconosciuta")


def _adatta(modello, bersaglio, cx, cy, sx, sy):
    """Ingrandisce il modello (proporzioni sx:sy) finché contiene il bersaglio."""
    lo, hi = 0.1, 2000.0                                       # mm: il modello è largo 2 unità
    for _ in range(45):
        m = (lo + hi) / 2
        g = affinity.translate(affinity.scale(modello, m * sx, m * sy, origin=(0, 0)), cx, cy)
        if g.contains(bersaglio):
            hi = m
        else:
            lo = m
    return affinity.translate(affinity.scale(modello, hi * sx, hi * sy, origin=(0, 0)), cx, cy)


def _arrotonda(g, r):
    """Smussa gli spigoli (il satin non gira bene sugli angoli vivi)."""
    g = g.buffer(r, quad_segs=8).buffer(-r, quad_segs=8)       # angoli rientranti
    g = g.buffer(-r, quad_segs=8).buffer(r, quad_segs=8)       # angoli sporgenti
    if g.geom_type != "Polygon":
        g = max(g.geoms, key=lambda p: p.area)
    return Polygon(g.exterior)


def forma_patch(sil, forma, margine, bordo, larg=None, alt=None):
    """Bordo esterno della patch (= linea di taglio). margine = spazio libero tra disegno e bordo satin."""
    d = margine + bordo
    x0, y0, x1, y1 = sil.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w, h = x1 - x0, y1 - y0
    if forma == "contorno":
        g = _contorno(sil, d)
    else:
        bers = sil.convex_hull.buffer(d, quad_segs=8) if forma != "cuore" else sil.buffer(d, quad_segs=4).simplify(0.3)
        mod = _modello(forma)
        if forma in ("cerchio", "esagono"):
            sx = sy = 1.0
        elif forma == "capsula":                               # stadio con le proporzioni del disegno
            sx = sy = 1.0
            a, b = max(w, h), max(min(w, h), 1)
            mod = LineString([(-(a - b) / a, 0), ((a - b) / a, 0)]).buffer(b / a, quad_segs=24)
            if h > w:
                mod = affinity.rotate(mod, 90, origin=(0, 0))
        else:
            sx, sy = max(w, 1) / max(w, h, 1), max(h, 1) / max(w, h, 1)
            if forma == "scudo":
                sy *= 1.15
        # cuore e scudo: il disegno sta meglio più in basso (in alto c'è la rientranza / in basso la punta stringe)
        prove = [k * 0.04 for k in range(-6, 7)] if forma in ("cuore", "scudo") else [0]
        g = min((_adatta(mod, bers, cx, cy - dy * max(w, h), sx, sy) for dy in prove), key=lambda p: p.area)
    r = max(1.5, bordo * 0.75) if forma in ("rettangolo", "esagono", "scudo") else max(1.0, bordo * 0.5)
    g = _arrotonda(g, r)
    if larg or alt:                                            # misura scelta a mano (mm): deformo la forma
        gx0, gy0, gx1, gy1 = g.bounds
        fx = (larg / (gx1 - gx0)) if larg else None
        fy = (alt / (gy1 - gy0)) if alt else None
        fx = fx or fy
        fy = fy or fx
        g = affinity.scale(g, fx, fy, origin=(cx, cy))
    return orient(g.simplify(0.05), 1.0)


# ---------------- punti ----------------
def _ricampiona(coords, passo):
    """Punti lungo una spezzata chiusa ogni `passo` mm."""
    ln = LineString(coords)
    n = max(8, int(ln.length / passo))
    return [ln.interpolate(ln.length * k / n).coords[0] for k in range(n + 1)]


def corsa(poligono, passo=2.5, inset=0.0):
    g = poligono.buffer(-inset, quad_segs=8) if inset else poligono
    if g.geom_type != "Polygon":
        g = max(g.geoms, key=lambda p: p.area)
    return _ricampiona(list(orient(g, 1.0).exterior.coords), passo)


def satin(poligono, larg, passo=0.4):
    """Bordo satin largo `larg` col bordo esterno sul perimetro. Restituisce (sottofondo, satin)."""
    centro = poligono.buffer(-larg / 2, quad_segs=16)
    if centro.geom_type != "Polygon":
        centro = max(centro.geoms, key=lambda p: p.area)
    centro = orient(centro, 1.0)
    fine = _ricampiona(list(centro.exterior.coords), 0.1)
    n = len(fine)

    def normale(k):
        a, b = fine[(k - 8) % (n - 1)], fine[(k + 8) % (n - 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        L = math.hypot(tx, ty) or 1
        return -ty / L, tx / L                                  # verso l'interno (anello antiorario)

    def lati(k, w):
        nx, ny = normale(k)
        x, y = fine[k]
        return (x - nx * w / 2, y - ny * w / 2), (x + nx * w / 2, y + ny * w / 2)   # esterno, interno
    passo_i = max(1, round(passo / 2 / 0.1))                   # un lato ogni passo/2 => stessa parte ogni `passo`
    sotto = [fine[k] for k in range(0, n, 25)]                 # corsa centrale 2,5 mm
    zig = []
    for j, k in enumerate(range(0, n, 20)):                   # zigzag di sottofondo, largo 70%, ogni 2 mm
        zig.append(lati(k, larg * 0.7)[j % 2])
    sop = []
    for j, k in enumerate(range(0, n, passo_i)):
        e, i = lati(k, larg)
        sop.append(e if j % 2 == 0 else i)
    return sotto + zig, sop


def tatami(poligono, angolo=45, riga=0.42, punto=3.5):
    """Riempimento a file parallele con punti sfalsati. Divide le zone concave in regioni ricamate una per volta.
    Restituisce una lista di regioni, ognuna = lista di punti (mm). Tra le regioni si taglia il filo."""
    rot = affinity.rotate(poligono, -angolo, origin=(0, 0))
    mx0, my0, mx1, my1 = rot.bounds
    righe = []
    y = my0 + riga / 2
    while y < my1:
        inter = rot.intersection(LineString([(mx0 - 1, y), (mx1 + 1, y)]))
        pezzi = [inter] if inter.geom_type == "LineString" else [g for g in getattr(inter, "geoms", []) if g.geom_type == "LineString"]
        seg = sorted((min(p.coords[0][0], p.coords[-1][0]), max(p.coords[0][0], p.coords[-1][0])) for p in pezzi if p.length > 0.3)
        righe.append((y, seg))
        y += riga
    regioni, aperte = [], []                                   # regione = [(y, x0, x1), ...]
    for y, seg in righe:
        nuove = []
        for s in seg:
            ov = [r for r in aperte if r[-1][1] < s[1] and r[-1][2] > s[0]]
            if len(ov) == 1 and sum(1 for t in seg if ov[0][-1][1] < t[1] and ov[0][-1][2] > t[0]) == 1:
                ov[0].append((y, s[0], s[1]))
                nuove.append(ov[0])
            else:
                r = [(y, s[0], s[1])]
                regioni.append(r)
                nuove.append(r)
        aperte = nuove
    out = []
    for r in regioni:
        pts = []
        for k, (y, a, b) in enumerate(r):
            sf = ((k * 3) % 5) / 5 * punto                       # sfalsamento: niente "solchi" allineati
            xs = [a] + [x for x in [a + sf + punto * j for j in range(int((b - a) / punto) + 2)] if a + 0.8 < x < b - 0.8] + [b]
            if k % 2:
                xs = xs[::-1]
            # passo tra una fila e la successiva: sui bordi poco inclinati (sottofondo a 2 mm) la fine di una fila e
            # l'inizio della seguente distano anche 10-25 mm -> un punto lunghissimo che si impiglia. Spezzato.
            if pts:
                x0_, y0_ = pts[-1]
                d = math.hypot(xs[0] - x0_, y - y0_)
                n = int(d // punto)
                pts += [(x0_ + (xs[0] - x0_) * i / (n + 1), y0_ + (y - y0_) * i / (n + 1)) for i in range(1, n + 1)]
            pts += [(x, y) for x in xs]
        ca, sa = math.cos(math.radians(angolo)), math.sin(math.radians(angolo))
        out.append([(x * ca - y * sa, x * sa + y * ca) for x, y in pts])
    # ordine: la regione più vicina alla fine della precedente
    ordinate, pos = [], (0, -1e9)
    while out:
        j = min(range(len(out)), key=lambda j: math.dist(pos, out[j][0]))
        ordinate.append(out.pop(j))
        pos = ordinate[-1][-1]
    return ordinate


def _filo(hexc, nome="", num=""):
    t = pe.EmbThread()
    t.set_hex_color(hexc)
    t.description = nome or hexc
    t.catalog_number = num
    return t


# ---------------- patch completa ----------------
def crea(pat, o):
    """o: forma, tipo, margine (mm), bordo (mm), larg/alt (mm, opzionali),
    col_bordo/col_fondo = {"hex","nome","num"}. Restituisce (EmbPattern, info)."""
    forma = o.get("forma", "contorno")
    tipo = o.get("tipo", "tessuto")
    margine = max(0.0, min(30.0, float(o.get("margine", 3))))
    bordo = max(2.0, min(8.0, float(o.get("bordo", 4))))
    sil = sagoma(pat)
    g = forma_patch(sil, forma, margine, bordo, o.get("larg") or None, o.get("alt") or None)
    x0, y0, x1, y1 = sil.bounds
    cb = o.get("col_bordo") or {"hex": "#1c2a4a", "nome": "Blu scuro"}
    cf = o.get("col_fondo") or {"hex": "#f2efe6", "nome": "Avorio"}
    fb, ff = _filo(cb["hex"], cb.get("nome", ""), cb.get("num", "")), _filo(cf["hex"], cf.get("nome", ""), cf.get("num", ""))

    out = pe.EmbPattern()
    stato = {"primo": True}

    def nuovo_colore(th):
        if not stato["primo"]:
            out.add_command(pe.COLOR_CHANGE)
        out.add_thread(th)
        stato["primo"] = False

    def cuci(pts, salta=True):
        if not pts:
            return
        x, y = pts[0]
        if salta:
            out.add_stitch_absolute(pe.TRIM, x * 10, y * 10)
            out.add_stitch_absolute(pe.JUMP, x * 10, y * 10)
        # punto di fermo: avanti e indietro di poco
        x2, y2 = pts[min(1, len(pts) - 1)]
        L = math.dist((x, y), (x2, y2)) or 1
        dx, dy = (x2 - x) / L * 0.5, (y2 - y) / L * 0.5
        for fx, fy in ((x, y), (x + dx, y + dy), (x, y), (x + dx, y + dy), (x, y)):
            out.add_stitch_absolute(pe.STITCH, fx * 10, fy * 10)
        for x, y in pts[1:]:
            out.add_stitch_absolute(pe.STITCH, x * 10, y * 10)
        x, y = pts[-1]
        xa, ya = pts[-2] if len(pts) > 1 else pts[-1]
        L = math.dist((x, y), (xa, ya)) or 1
        dx, dy = (xa - x) / L * 0.5, (ya - y) / L * 0.5
        for fx, fy in ((x + dx, y + dy), (x, y), (x + dx, y + dy), (x, y)):
            out.add_stitch_absolute(pe.STITCH, fx * 10, fy * 10)

    linea = corsa(g, 2.5, inset=bordo / 2)                     # sotto il centro del satin: il satin la copre
    if tipo == "tessuto":
        nuovo_colore(fb)                                       # 1) piazzamento: dove appoggiare il tessuto
        cuci(linea)
        nuovo_colore(_filo(cb["hex"], (cb.get("nome", "") + " (fissaggio)").strip(), cb.get("num", "")))
        cuci(linea)                                            # 2) fissaggio del tessuto (doppio passaggio)
        cuci(linea[::-1], salta=False)
    elif tipo == "ricamato":
        nuovo_colore(ff)
        area = g.buffer(-(bordo - 1.0), quad_segs=8)           # il fondo entra 1 mm sotto il satin
        if area.geom_type != "Polygon":
            area = max(area.geoms, key=lambda p: p.area)
        cuci(corsa(area, 2.5, inset=0.8))                      # contorno di sottofondo
        for reg in tatami(area.buffer(-0.8), 135, 2.5, 4.0):   # sottofondo rado, incrociato
            cuci(reg)
        for reg in tatami(area, 45, 0.42, 3.5):
            cuci(reg)
    # 3) il disegno, spostato in modo che resti dov'è rispetto alla forma
    for blocco, th in pat.get_as_colorblocks():
        nuovo_colore(th if th is not None else _filo("#333333"))
        for x, y, c in blocco:
            c &= 0xFF
            if c in (pe.STITCH, pe.JUMP, pe.TRIM):
                out.add_stitch_absolute(c, x, y)
    # 4) bordo satin
    nuovo_colore(fb)
    sotto, sop = satin(g, bordo)
    cuci(sotto)
    cuci(sop, salta=False)
    out.add_command(pe.END)

    gx0, gy0, gx1, gy1 = g.bounds
    punti = sum(1 for s in out.stitches if (s[2] & 0xFF) == pe.STITCH)
    info = {"larg": round(gx1 - gx0, 1), "alt": round(gy1 - gy0, 1), "punti": punti,
            "colori": len(out.threadlist), "forma": forma, "tipo": tipo,
            "dis_larg": round(x1 - x0, 1), "dis_alt": round(y1 - y0, 1),
            "esce": not g.buffer(-bordo + 0.01).contains(sil.buffer(-0.6)),
            "contorno": [(round(x, 2), round(y, 2)) for x, y in g.exterior.coords]}
    return out, info


def anteprima(pat, info, size=900, tessuto="#2b2f36"):
    """Patch disegnata col filo vero; col tipo «tessuto» sotto c'è la stoffa tagliata sulla forma."""
    from PIL import Image, ImageDraw
    from anteprima_bella import render, _tela
    import random
    ss = 2
    img = render(pat, size, ss=ss, sfondo=None)
    x0, y0, x1, y1 = pat.bounds()
    S = size * ss
    m = 0.06 * S
    k = (S - 2 * m) / max(x1 - x0, y1 - y0, 1)
    ox = m + ((S - 2 * m) - (x1 - x0) * k) / 2
    oy = m + ((S - 2 * m) - (y1 - y0) * k) / 2
    base = _tela(S, random.Random(3)).convert("RGBA")
    if info["tipo"] != "ricamato":
        lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dr = ImageDraw.Draw(lay)
        poly = [(ox + (x * 10 - x0) * k, oy + (y * 10 - y0) * k) for x, y in info["contorno"]]
        dr.polygon([(p[0] + 4 * ss, p[1] + 4 * ss) for p in poly], fill=(0, 0, 0, 60))
        c = int(tessuto[1:], 16)
        dr.polygon(poly, fill=((c >> 16) & 255, (c >> 8) & 255, c & 255, 255))
        base.alpha_composite(lay)
    base = base.resize((size, size), Image.LANCZOS)
    base.alpha_composite(img)
    return base.convert("RGB")
