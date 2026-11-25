"""Scritte da ricamo con i font di Ink/Stitch (github.com/inkstitch/embroidery-fonts, licenze nei LICENSE dei font).

Ogni font = font.json + ltr.svg: una «GlyphLayer-X» per lettera, fatta di
  - colonne satin (inkstitch:satin_column): 2 binari + traversine, con sottofondi e compensazione;
  - punti dritti (tratteggiati): percorsi e passaggi nascosti;
  - riempimenti (path con fill): punto tatami.
Qui li trasformo in punti macchina (mm). Non uso Ink/Stitch: solo i suoi file dei font.

testo(font_id, "Sofia", altezza_mm) -> {"blocchi": [(hex, [tratti])], "larg", "alt"}  (tratto = lista di punti mm;
tra un tratto e l'altro si taglia il filo)
"""
import json
import math
import os
import xml.etree.ElementTree as ET
from functools import lru_cache

import svgelements as se
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

QUI = os.path.dirname(os.path.abspath(__file__))
CARTELLA = os.path.join(os.path.dirname(QUI), "01_riferimenti", "inkstitch-fonts", "src")
PX = 25.4 / 96                                  # 1 px SVG = 0,2646 mm
NS = {"svg": "http://www.w3.org/2000/svg", "inkscape": "http://www.inkscape.org/namespaces/inkscape",
      "inkstitch": "http://inkstitch.org/namespace", "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"}
_IK = "{%s}" % NS["inkstitch"]
_LABEL = "{%s}label" % NS["inkscape"]


# ---------------- lettura del font ----------------
ELENCO = os.path.join(CARTELLA, "..", "elenco_font.json")       # calcolato una volta (leggere 140 font è lento)


def elenco():
    """Font disponibili: [{id, nome, descr, parole, min, max, maiuscole, altezza_mm (lettere maiuscole al 100%)}]."""
    try:
        return json.load(open(ELENCO, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    out = _elenco()
    json.dump(out, open(ELENCO, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def _elenco():
    out = []
    if not os.path.isdir(CARTELLA):
        return out
    for d in sorted(os.listdir(CARTELLA)):
        f = os.path.join(CARTELLA, d, "font.json")
        if not os.path.exists(f) or not os.path.exists(os.path.join(CARTELLA, d, "ltr.svg")):
            continue
        try:
            m = json.load(open(f, encoding="utf-8"))
        except ValueError:
            continue
        try:
            alt = metriche(d)["maiusc_mm"]
        except Exception:
            continue
        out.append({"id": d, "nome": m.get("name", d), "descr": m.get("description", ""),
                    "parole": m.get("keywords", []), "min": m.get("min_scale", 1), "max": m.get("max_scale", 1),
                    "maiuscole": m.get("letter_case", "") == "upper", "altezza_mm": round(alt, 1),
                    "glifi": "".join(g for g in m.get("glyphs", []) if len(g) == 1)})
    return out


def _bool(v, predef=False):
    if v is None:
        return predef
    return str(v).strip().lower() in ("true", "1", "yes")


def _num(v, predef):
    try:
        return float(v)
    except (TypeError, ValueError):
        return predef


def _stile(el):
    s = {}
    for parte in (el.get("style") or "").split(";"):
        if ":" in parte:
            k, v = parte.split(":", 1)
            s[k.strip()] = v.strip()
    for k in ("fill", "stroke", "stroke-dasharray", "display"):
        if el.get(k) is not None and k not in s:
            s[k] = el.get(k)
    return s


def _sottopercorsi(d, matrice):
    """Path SVG -> lista di spezzate (mm), curve campionate."""
    p = se.Path(d) * matrice
    p.reify()
    out = []
    for sp in p.as_subpaths():
        pts = []
        for seg in sp:
            if isinstance(seg, se.Move):
                pts.append((seg.end.x, seg.end.y))
            elif isinstance(seg, (se.Line, se.Close)):
                if seg.end is not None:
                    pts.append((seg.end.x, seg.end.y))
            else:                                               # curva: lunghezza stimata dal poligono di controllo
                ctrl = [q for q in (getattr(seg, a, None) for a in ("start", "control", "control1", "control2", "end")) if q is not None]
                lc = sum(math.dist((a.x, a.y), (b.x, b.y)) for a, b in zip(ctrl, ctrl[1:])) if len(ctrl) > 1 else 10
                n = max(3, min(24, int(lc / 1.5) + 1))
                for k in range(1, n + 1):
                    q = seg.point(k / n)
                    pts.append((q.x, q.y))
        pts = [(float(x) * PX, float(y) * PX) for x, y in pts]
        pulite = [pts[0]] if pts else []
        for q in pts[1:]:
            if math.dist(q, pulite[-1]) > 1e-4:
                pulite.append(q)
        if len(pulite) > 1:
            out.append(pulite)
    return out


@lru_cache(maxsize=24)
def carica(font_id):
    """-> (meta, {carattere: [elementi]}). elemento = dict(tipo=satin|corsa|riempi, colore, linee, parametri)."""
    cart = os.path.join(CARTELLA, font_id)
    meta = json.load(open(os.path.join(cart, "font.json"), encoding="utf-8"))
    radice = ET.parse(os.path.join(cart, "ltr.svg")).getroot()
    glifi = {}

    def visita(el, matrice, glifo):
        tag = el.tag.split("}")[-1]
        if tag in ("defs", "metadata", "namedview", "symbol", "clipPath", "mask", "title", "desc"):
            return
        t = el.get("transform")
        if t:
            matrice = se.Matrix(t) * matrice
        lab = el.get(_LABEL) or ""
        if tag == "g" and lab.startswith("GlyphLayer-"):
            glifo = lab[len("GlyphLayer-"):]
            glifi.setdefault(glifo, [])
        if tag == "g" and el.get(_IK + "command") is not None:
            return
        if tag == "use":                                        # simboli dei comandi (taglio, stop…): li ignoro
            return
        if tag == "path" and glifo is not None and el.get("d") and not el.get(_IK + "connection-end"):
            st = _stile(el)
            par = {k[len(_IK):]: v for k, v in el.attrib.items() if k.startswith(_IK)}
            try:
                linee = _sottopercorsi(el.get("d"), matrice)
            except Exception:
                linee = []
            if not linee:
                return
            riempito = st.get("fill") not in (None, "none", "") and st.get("fill-opacity", "1") not in ("0",)
            traccia = st.get("stroke") not in (None, "none", "")
            if _bool(par.get("satin_column")):
                tipo, col = "satin", st.get("stroke", "#000000")
            elif riempito and not traccia:
                tipo, col = "riempi", st.get("fill", "#000000")
            elif traccia:
                tipo, col = "corsa", st.get("stroke", "#000000")
            else:
                return
            glifi[glifo].append({"tipo": tipo, "colore": col if col.startswith("#") else "#000000", "linee": linee, "par": par})
        for figlio in el:
            visita(figlio, matrice, glifo)

    visita(radice, se.Matrix(), None)
    return meta, {k: v for k, v in glifi.items() if v}


@lru_cache(maxsize=64)
def metriche(font_id):
    """Linea di base e altezza delle maiuscole (mm) misurate sulle lettere stesse."""
    meta, gl = carica(font_id)

    def bbox(c):
        xs = [x for e in gl[c] for ln in e["linee"] for x, _ in ln]
        ys = [y for e in gl[c] for ln in e["linee"] for _, y in ln]
        return min(xs), min(ys), max(xs), max(ys)
    rif = [c for c in "EFHILTNMKXZ" if c in gl] or [c for c in "efhiltnmkxz" if c in gl] or [c for c in gl if c.isalnum()]
    if not rif:
        raise ValueError("font senza lettere")
    bb = sorted(bbox(c) for c in rif)
    fondi = sorted(b[3] for b in bb)
    cime = sorted(b[1] for b in bb)
    base = fondi[len(fondi) // 2]
    alto = cime[len(cime) // 2]
    return {"base": base, "maiusc_mm": base - alto, "bbox": {c: bbox(c) for c in gl}}


# ---------------- punti ----------------
def _ricampiona(pts, passo):
    ln = LineString(pts)
    if ln.length < 1e-6:
        return [pts[0]]
    n = max(1, math.ceil(ln.length / passo))
    out = []
    # tieni gli spigoli: ricampiono ogni segmento
    for a, b in zip(pts, pts[1:]):
        L = math.dist(a, b)
        k = max(1, math.ceil(L / passo))
        for j in range(k):
            out.append((a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k))
    out.append(pts[-1])
    del n
    return out


def corsa(el, scala):
    par = el["par"]
    passo = _num(par.get("running_stitch_length_mm"), 2.5) / max(scala, 0.3)   # il passo resta in mm veri
    ripeti = int(_num(par.get("repeats"), 1))
    fagiolo = par.get("bean_stitch_repeats", "0")
    try:
        fagiolo = int(str(fagiolo).split()[0])
    except ValueError:
        fagiolo = 0
    tratti = []
    for ln in el["linee"]:
        pts = _ricampiona(ln, passo)
        for r in range(1, ripeti):
            pts += _ricampiona(ln[::-1] if r % 2 else ln, passo)[1:]
        if fagiolo:
            b = [pts[0]]
            for p in pts[1:]:
                for _ in range(fagiolo):
                    b += [p, b[-1]]
                b.append(p)
            pts = b
        tratti.append(pts)
    return tratti


def _binari_e_traversine(linee):
    """Come Ink/Stitch: le traversine toccano esattamente 2 altri tratti; se ambiguo, i binari sono i più lunghi."""
    L = [LineString(x) for x in linee]
    n = len(L)
    if n <= 2:
        return [0, 1][:n], []
    inc = [sum(L[i].intersects(L[j]) for j in range(n) if j != i) for i in range(n)]
    if n == 3:
        pos = [i for i in range(n) if inc[i] == 1 and L[i].length > 0.03]
    else:
        pos = [i for i in range(n) if inc[i] > 2 and L[i].length > 0.03]
    if len(pos) != 2:
        pos = sorted(range(n), key=lambda i: -L[i].length)[:2]
    return pos, [i for i in range(n) if i not in pos]


def _sezioni(a, b, traversine):
    """Taglia i due binari dove passano le traversine e li accoppia a pezzi."""
    ta, tb = [], []
    for r in traversine:
        inter = r.intersection(MultiLineString([a, b]))
        if inter.geom_type == "MultiPoint" and len(inter.geoms) > 2:
            continue
        ta.append(a.project(nearest_points(r, a)[1]))
        tb.append(b.project(nearest_points(r, b)[1]))
    ta, tb = sorted(set(ta)), sorted(set(tb))
    if len(ta) != len(tb):
        ta, tb = [], []

    def taglia(ln, tt):
        cuts = [0] + [t for t in tt if 1e-3 < t < ln.length - 1e-3] + [ln.length]
        return [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1)]
    sa, sb = taglia(a, ta), taglia(b, tb)
    if len(sa) != len(sb):
        sa, sb = [(0, a.length)], [(0, b.length)]
    return list(zip(sa, sb))


def satin(el, scala):
    par = el["par"]
    linee = el["linee"]
    idx, trav = _binari_e_traversine(linee)
    if len(idx) < 2:
        return corsa(el, scala)
    a, b = LineString(linee[idx[0]]), LineString(linee[idx[1]])
    # stessa direzione dei due binari (come "automatic" di Ink/Stitch), poi le scelte del font
    if math.dist(a.coords[0], b.coords[0]) + math.dist(a.coords[-1], b.coords[-1]) > \
            math.dist(a.coords[0], b.coords[-1]) + math.dist(a.coords[-1], b.coords[0]):
        b = LineString(list(b.coords)[::-1])
    rr = par.get("reverse_rails", "automatic")
    if rr in ("first", "both"):
        a = LineString(list(a.coords)[::-1])
    if rr in ("second", "both"):
        b = LineString(list(b.coords)[::-1])
    if _bool(par.get("swap_satin_rails")):
        a, b = b, a
    trav = [LineString(linee[i]) for i in trav]
    # le misure del font sono in mm "veri" sul font al 100%: le densità devono restare in mm dopo la scala
    k = 1 / max(scala, 0.3)
    passo = _num(par.get("zigzag_spacing_mm"), 0.4) * k
    comp = _num(par.get("pull_compensation_mm"), 0.0) * k
    comp_pc = _num(par.get("pull_compensation_percent"), 0.0) / 100
    coppie = []                                                     # (pa, pb) lungo tutta la colonna
    sez = _sezioni(a, b, trav)
    for q, ((a0, a1), (b0, b1)) in enumerate(sez):
        La, Lb = a1 - a0, b1 - b0
        n = max(1, math.ceil(max(La, Lb) / passo))
        for j in range(n + (1 if q == len(sez) - 1 else 0)):
            t = j / n
            pa, pb = a.interpolate(a0 + La * t), b.interpolate(b0 + Lb * t)
            coppie.append(((pa.x, pa.y), (pb.x, pb.y)))
    if not coppie:
        return []

    def larga(pa, pb, extra):
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        L = math.hypot(dx, dy) or 1
        e = (extra + comp_pc * L) / 2
        return (pa[0] - dx / L * e, pa[1] - dy / L * e), (pb[0] + dx / L * e, pb[1] + dy / L * e)

    def stretta(pa, pb, ins):
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        L = math.hypot(dx, dy) or 1
        ins = min(ins, L * 0.45)
        return (pa[0] + dx / L * ins, pa[1] + dy / L * ins), (pb[0] - dx / L * ins, pb[1] - dy / L * ins)

    tratto = []
    lung = sum(math.dist(c[0], c[1]) for c in coppie) / len(coppie)
    if _bool(par.get("center_walk_underlay")):
        centro = [((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2) for pa, pb in coppie]
        cw = _ricampiona(centro, 2.5 * k)
        tratto += cw + cw[::-1]
    if _bool(par.get("contour_underlay")):
        ins = _num(par.get("contour_underlay_inset_mm"), 0.4) * k
        s = [stretta(pa, pb, ins) for pa, pb in coppie]
        tratto += _ricampiona([x[0] for x in s], 2.5 * k) + _ricampiona([x[1] for x in s][::-1], 2.5 * k)
    if _bool(par.get("zigzag_underlay")) and lung > 0.8:
        ins = _num(par.get("zigzag_underlay_inset_mm"), comp / 2 + 0.2 * k)
        sp = _num(par.get("zigzag_underlay_spacing_mm"), 3.0) * k
        passo_c = max(1, round(sp / 2 / max(passo, 1e-3)))
        s = [stretta(pa, pb, ins) for pa, pb in coppie[::passo_c]]
        andata = [x[j % 2] for j, x in enumerate(s)]
        ritorno = [x[(j + 1) % 2] for j, x in enumerate(s)][::-1]
        tratto += andata + ritorno
    sop = []
    for j, (pa, pb) in enumerate(coppie):
        e, i = larga(pa, pb, comp)
        sop.append(e if j % 2 == 0 else i)
    tratto += sop
    return [tratto]


def riempi(el, scala):
    import patch as P
    par = el["par"]
    g = unary_union([Polygon(ln).buffer(0) for ln in el["linee"] if len(ln) > 2])
    # i buchi (es. la O) sono sottopercorsi interni: differenza simmetrica come la regola evenodd
    poli = [Polygon(ln).buffer(0) for ln in el["linee"] if len(ln) > 2]
    if len(poli) > 1:
        g = poli[0]
        for p in poli[1:]:
            g = g.symmetric_difference(p)
    k = 1 / max(scala, 0.3)
    ang = -_num(par.get("angle"), 0)
    riga = _num(par.get("row_spacing_mm"), 0.25) * k
    punto = _num(par.get("max_stitch_length_mm"), 3.0) * k
    out = []
    parti = [g] if g.geom_type == "Polygon" else [x for x in getattr(g, "geoms", []) if x.geom_type == "Polygon"]
    for pz in parti:
        if pz.area < 0.05:
            continue
        if _bool(par.get("fill_underlay"), True):
            for reg in P.tatami(pz.buffer(-0.3 * k) if pz.buffer(-0.3 * k).area > 0 else pz, ang + 90, 2.0 * k, 3.0 * k):
                out.append(reg)
        out += P.tatami(pz, ang, riga, punto)
    return out


# ---------------- scritta ----------------
PUNTI = ["satin", "riempimento", "contorno", "triplo"]


def _forma_glifo(elementi):
    """Superficie della lettera (mm del font): colonne satin (binari) + riempimenti."""
    poli = []
    for e in elementi:
        if e["tipo"] == "satin":
            idx, _ = _binari_e_traversine(e["linee"])
            if len(idx) == 2:
                a, b = e["linee"][idx[0]], e["linee"][idx[1]]
                if math.dist(a[0], b[0]) + math.dist(a[-1], b[-1]) > math.dist(a[0], b[-1]) + math.dist(a[-1], b[0]):
                    b = b[::-1]
                p = Polygon(list(a) + list(b[::-1])).buffer(0)
                if not p.is_empty:
                    poli.append(p)
        elif e["tipo"] == "riempi":
            poli += [Polygon(ln).buffer(0) for ln in e["linee"] if len(ln) > 2]
    return unary_union(poli) if poli else None


def _parti(g):
    return [g] if g.geom_type == "Polygon" else [x for x in getattr(g, "geoms", []) if x.geom_type == "Polygon"]


def glifo_punti(elementi, scala, punto="satin"):
    """[(colore, tratto)] di una lettera col tipo di punto scelto. Le misure dei punti restano in mm veri
    (le coordinate qui sono quelle del font, poi moltiplicate per `scala`)."""
    out = []
    forma = _forma_glifo(elementi) if punto != "satin" else None
    if forma is None or forma.is_empty:                    # font fatti solo di filza: si usano così
        for e in elementi:
            fn = satin if e["tipo"] == "satin" else (riempi if e["tipo"] == "riempi" else corsa)
            out += [(e["colore"], tr) for tr in fn(e, scala)]
        return out
    col = next((e["colore"] for e in elementi if e["tipo"] in ("satin", "riempi")), "#000000")
    k = 1 / max(scala, 0.3)
    if punto == "riempimento":
        import patch as P
        for pz in _parti(forma):
            if pz.area * scala * scala < 0.3:
                continue
            bordo = pz.buffer(-0.35 * k)
            for b in _parti(bordo) if not bordo.is_empty else []:      # filza lungo il bordo: margini netti
                out.append((col, _ricampiona(list(b.exterior.coords), 2.0 * k)))
            for reg in P.tatami(pz.buffer(-0.3 * k) if not pz.buffer(-0.3 * k).is_empty else pz, 135, 1.8 * k, 3.0 * k):
                out.append((col, reg))                                  # sottopunto rado incrociato
            for reg in P.tatami(pz, 45, 0.42 * k, 3.5 * k):
                out.append((col, reg))
    else:                                                               # contorno a filza o triplo
        passo = (2.0 if punto == "contorno" else 2.5) * k
        for pz in _parti(forma):
            for anello in [pz.exterior] + list(pz.interiors):
                pts = _ricampiona(list(anello.coords), passo)
                if punto == "triplo":                                   # avanti-indietro-avanti su ogni punto
                    b = [pts[0]]
                    for p in pts[1:]:
                        b += [p, b[-1], p]
                    pts = b
                out.append((col, pts))
    return out


def _arco(x, y, R):
    """Piega il punto (x lungo la riga, y verso il basso dalla linea di base) su un cerchio di raggio R (mm).
    R > 0 = arco verso l'alto (come sopra un disegno tondo), R < 0 = verso il basso (sorriso)."""
    h = -y
    th = x / R
    r = R + h
    return r * math.sin(th), R - r * math.cos(th)


def testo(font_id, scritta, altezza_mm=None, spaziatura_mm=0.0, interlinea=1.0, allinea="centro", colori_font=False,
          punto="satin", arco=0.0):
    """Restituisce {"blocchi": [(hex, [tratti])], "larg", "alt", "mancano", "scala"}. Coordinate mm, centro del testo in 0,0."""
    meta, gl = carica(font_id)
    met = metriche(font_id)
    scala = (altezza_mm / met["maiusc_mm"]) if altezza_mm else 1.0
    if meta.get("letter_case") == "upper":
        scritta = scritta.upper()
    elif meta.get("letter_case") == "lower":
        scritta = scritta.lower()
    adv = meta.get("horiz_adv_x") or {}
    adv_def = meta.get("horiz_adv_x_default")
    spazio = meta.get("horiz_adv_x_space", 20)
    kern = meta.get("kerning_pairs") or {}
    leading = meta.get("leading", 100)
    righe, mancano = [], set()
    y = 0.0
    origine = min(b[0] for b in met["bbox"].values())               # x=0 del disegno del font (circa)
    for riga in scritta.split("\n"):
        # come Ink/Stitch (font.py _render_glyph): la lettera è spostata di -min_x; prima di ogni lettera che
        # segue un'altra nella stessa parola si aggiunge min_x - crenatura, dopo si avanza di horiz_adv_x - min_x
        x, prec, el_riga = 0.0, None, []
        for c in riga:
            if c == " ":
                x += spazio * PX + spaziatura_mm / scala
                prec = None
                continue
            if c not in gl:
                if c.swapcase() in gl:
                    c = c.swapcase()
                else:
                    mancano.add(c)
                    continue
            minx = met["bbox"][c][0] - origine * 0                    # mm, nel sistema del font
            if prec is not None:
                kk = kern.get(f"{prec} {c}", kern.get(prec + c, 0))
                x += minx - kk * PX + spaziatura_mm / scala
            el_riga.append((c, x - minx, y))                           # la lettera parte a x
            larg = met["bbox"][c][2] - minx
            av = adv.get(c, adv_def)
            x += (av * PX if av is not None else larg + minx) - minx
            prec = c
        righe.append(el_riga)
        y += leading * PX * interlinea
    # posa i punti
    blocchi = []                                                     # (colore, tratti) nell'ordine del ricamo
    xs_tot = []
    righe_pts = []
    for el_riga in righe:
        r = []
        for c, dx, yy in el_riga:
            for colore, tr in glifo_punti(gl[c], scala, punto if punto in PUNTI else "satin"):
                r.append((colore, [(px_ + dx, py_ + yy - met["base"]) for px_, py_ in tr], c))
        righe_pts.append(r)
    # allinea le righe e scala
    for r in righe_pts:
        if not r:
            continue
        x0 = min(p[0] for _, tr, _ in r for p in tr)
        x1 = max(p[0] for _, tr, _ in r for p in tr)
        xs_tot.append((x0, x1))
    largh = max((b - a for a, b in xs_tot), default=0)
    tutti = []
    for r, (x0, x1) in zip([r for r in righe_pts if r], xs_tot):
        sp = {"centro": (largh - (x1 - x0)) / 2, "destra": largh - (x1 - x0)}.get(allinea, 0) - x0
        for col, tr, c in r:
            tutti.append((col, [(float((x + sp) * scala), float(y * scala)) for x, y in tr]))
    if arco and abs(arco) >= 5 and tutti:                                # scritta ad arco (raggio in mm)
        xc = largh * scala / 2
        tutti = [(col, [tuple(float(v) for v in _arco(x - xc, y, arco)) for x, y in tr]) for col, tr in tutti]
    if not tutti:
        return {"blocchi": [], "larg": 0, "alt": 0, "mancano": sorted(mancano), "scala": scala}
    X = [p[0] for _, tr in tutti for p in tr]
    Y = [p[1] for _, tr in tutti for p in tr]
    cx, cy = (min(X) + max(X)) / 2, (min(Y) + max(Y)) / 2
    for col, tr in tutti:
        tr2 = [(x - cx, y - cy) for x, y in tr]
        col = col if colori_font else "#000000"
        if blocchi and blocchi[-1][0] == col:
            blocchi[-1][1].append(tr2)
        else:
            blocchi.append((col, [tr2]))
    return {"blocchi": blocchi, "larg": round(max(X) - min(X), 1), "alt": round(max(Y) - min(Y), 1),
            "mancano": sorted(mancano), "scala": round(scala, 3), "min": meta.get("min_scale", 1), "max": meta.get("max_scale", 1)}
