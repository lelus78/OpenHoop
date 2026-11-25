"""Converte un progetto xTool Studio (.xs) con un ricamo dentro in PES per la Brother.

Un .xs è uno zip di JSON. I ricami (macchina xTool CF001) stanno come blocchi «LINE_SVG» con isSTITCH: ogni blocco
è un colore, `dPath` (o `vectorRef` -> vectors/svg/data-*.json) è la sequenza dei punti dell'ago: «M x y» = salto,
«L x y» = punto, in decimi di mm (unità del file da ricamo d'origine, es. VP3). `transform` è come xTool l'ha messo
nel telaio (spesso ruotato e ingrandito per riempirlo): qui si usa la MISURA ORIGINALE, perché ingrandire i punti
così come sono li allunga e li dirada. Colori: `layerColor`, con le sostituzioni scelte in xTool (`patches`).
Studiato sul file «Christmas Embroidery.xs» (25/11/2026): 52 blocchi, 9 colori, 101,6 × 35,6 mm.

Uso:  python xs_in_pes.py file.xs [uscita.pes] [--come-nel-progetto] [--ordine-originale] [--anteprima immagine.png]
      --ordine-originale: non raggruppa i colori (di base sì, solo dove i blocchi non si toccano)
"""
import io
import json
import os
import re
import sys
import zipfile

import pyembroidery as pe

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pes_brother  # noqa: E402,F401  -- niente n. 64 «Applique» nella tabella colori PEC

SALTO_CON_TAGLIO = 30          # decimi di mm: salti più lunghi di 3 mm dentro un blocco = filo da tagliare


def _anelli(d):
    """dPath (M/L/C/Z, coordinate assolute) -> lista di anelli [(x, y), ...]. Le curve diventano 8 segmenti."""
    anelli, cur, pos = [], [], (0.0, 0.0)
    tok = re.findall(r"[MLCZmlcz]|-?[\d.]+(?:e-?\d+)?", d)
    i, cmd = 0, None
    num = lambda k: float(tok[k])                                             # noqa: E731
    while i < len(tok):
        if tok[i] in "MLCZmlcz":
            cmd = tok[i].upper()
            i += 1
            if cmd == "Z":
                if len(cur) > 2:
                    anelli.append(cur)
                cur = []
                continue
        if cmd == "M":
            if len(cur) > 2:
                anelli.append(cur)
            pos = (num(i), num(i + 1))
            cur = [pos]
            i += 2
            cmd = "L"
        elif cmd == "L":
            pos = (num(i), num(i + 1))
            cur.append(pos)
            i += 2
        elif cmd == "C":
            p0, p1, p2, p3 = pos, (num(i), num(i + 1)), (num(i + 2), num(i + 3)), (num(i + 4), num(i + 5))
            for k in range(1, 9):
                t = k / 8
                a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
                cur.append((a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]))
            pos = p3
            i += 6
        else:
            i += 1
    if len(cur) > 2:
        anelli.append(cur)
    return anelli


def _poligono(d, transform):
    """Forma piena (regola evenodd: i buchi si tolgono) nelle coordinate del telaio, mm."""
    from shapely.geometry import Polygon
    from shapely.validation import make_valid
    a, b, c, dd, e, f = [float(v) for v in transform]
    g = None
    for r in _anelli(d):
        p = make_valid(Polygon([(a * x + c * y + e, b * x + dd * y + f) for x, y in r]))
        g = p if g is None else g.symmetric_difference(p)
    return g


def _punti_forma(g, prof):
    """Punti (mm) di una forma secondo il profilo xTool: [[(x, y), ...] regione, ...] (tra regioni si salta)."""
    import patch as PA
    from shapely.geometry import MultiPolygon, Polygon
    tipo, v = prof["processingType"], prof["values"]
    poli = [g] if isinstance(g, Polygon) else [p for p in getattr(g, "geoms", []) if isinstance(p, (Polygon, MultiPolygon))]
    poli = [q for p in poli for q in (p.geoms if isinstance(p, MultiPolygon) else [p]) if q.area > 0.3]
    out = []
    for p in poli:
        if tipo == "EMBROIDERY_VECTOR":                                  # linea: filza sul contorno
            for _ in range(max(1, int(v.get("runTimes", 1)))):
                out.append(PA.corsa(p, float(v.get("maxStitchLength", 2.5))))
            continue
        angolo = float(v.get("angle", 45))
        riga = max(0.25, float(v.get("density", 0.4)))
        lungo = float(v.get("maxStitchLength", 4))
        satin = v.get("fillScanMode", v.get("kind")) == "satin"
        comp = float(v.get("pullCompensation", 0)) / 2
        if v.get("underlay", True) and p.area > 4:                       # sottofondo rado, di traverso
            sotto = p.buffer(-0.4)
            if not sotto.is_empty:
                for q in getattr(sotto, "geoms", [sotto]):
                    out += PA.tatami(q, angolo + 90, 2.0, 3.0)
        q = p.buffer(comp) if comp else p
        for qq in getattr(q, "geoms", [q]):
            # «satin» di xTool: un punto da bordo a bordo (spezzato solo oltre maxStitchLength, 7 mm)
            out += PA.tatami(qq, angolo, riga, lungo if not satin else max(lungo, 7.0))
    return _cuci_regioni(out, g)


def _corsa_tra(a, b, passo=2.0):
    import math
    n = max(1, int(math.dist(a, b) / passo))
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]


def _cuci_regioni(regioni, forma):
    """Tra un pezzo e il successivo della stessa forma l'ago viaggia con una filza NASCOSTA dentro la forma
    (verrà coperta dal riempimento) invece di saltare: la Innov-is non taglia i salti, ogni salto è un filo da
    tagliare a mano. Si salta solo se la linea dritta esce dalla forma."""
    from shapely.geometry import LineString
    dentro = forma.buffer(0.3)
    out = []
    for r in regioni:
        if not r:
            continue
        via = None
        if out:
            a, b = out[-1][-1], r[0]
            seg = LineString([a, b])
            if seg.length < 0.3 or dentro.contains(seg):
                via = _corsa_tra(a, b)
            else:
                via = _lungo_il_bordo(forma, dentro, a, b)
        if via is not None:
            out[-1] = out[-1] + via + list(r[1:])
        else:
            out.append(list(r))
    return out


def _lungo_il_bordo(forma, dentro, a, b, passo=2.0):
    """Percorso da a a b lungo il bordo interno (0,4 mm dentro) della stessa parte di forma, dal lato più corto.
    None se non si può (parti staccate, collegamenti che escono)."""
    from shapely.geometry import LineString, Point
    interno = forma.buffer(-0.4)
    for parte in getattr(interno, "geoms", [interno]):
        if parte.is_empty or parte.geom_type != "Polygon":
            continue
        anello = parte.exterior
        if anello.distance(Point(a)) > 1.5 or anello.distance(Point(b)) > 1.5:
            continue
        L = anello.length
        d0, d1 = anello.project(Point(a)), anello.project(Point(b))
        avanti = (d1 - d0) % L
        lungo, segno = (avanti, 1) if avanti <= L / 2 else (L - avanti, -1)
        if lungo > 120:                                              # troppo giro: meglio un taglio
            return None
        n = max(1, int(lungo / passo))
        pts = [anello.interpolate((d0 + segno * lungo * k / n) % L).coords[0] for k in range(n + 1)]
        if not (dentro.contains(LineString([a, pts[0]])) or Point(a).distance(Point(pts[0])) < 0.3):
            return None
        if not (dentro.contains(LineString([pts[-1], b])) or Point(b).distance(Point(pts[-1])) < 0.3):
            return None
        return pts + [b]
    return None


def leggi_xs_forme(z, nomi):
    """Progetti xTool con FORME vettoriali (i punti li calcolava xTool Studio): si calcolano qui con tatami.
    -> blocchi nello stesso formato di leggi_xs (punti in 0,1 mm, «M» = salto)."""
    leggi = lambda n: json.loads(z.read(n))                                   # noqa: E731
    vettori = {}
    for n in nomi:
        if re.fullmatch(r"vectors/svg/data-\d+\.json", n):
            vettori.update(leggi(n).get("entries", {}))
    prof = leggi("profiles.json").get("profiles", {}) if "profiles.json" in nomi else {}
    lega, patch = {}, {}
    for n in nomi:
        if n.startswith("devices/") and n.endswith(".json"):
            for proc in leggi(n).get("processing", {}).values():
                m = (proc.get("modes") or {}).get("EMBROIDERY")
                if not m:
                    continue
                patch.update({k: p.get("overrides", {}) for k, p in (m.get("patches") or {}).items()})
                ign = set(m.get("ignoredDisplayIds") or [])
                for b in m.get("bindings") or []:
                    for di in b.get("displayIds", []):
                        if di not in ign:
                            lega[di] = (b.get("baseProfileId"), b.get("patchIds") or [])
    forme = []
    for n in nomi:
        if re.fullmatch(r"canvases/[^/]+/displays-\d+\.json", n):
            forme += [x for x in leggi(n).get("displays", []) if x.get("type") == "PATH" and x.get("visible", True)
                      and x.get("id") in lega]
    out = []
    for x in sorted(forme, key=lambda x: float(x.get("zOrder", 0))):
        pid, patch_ids = lega[x["id"]]
        p = prof.get(pid)
        if not p or p.get("processingType") not in ("EMBROIDERY_FILL", "EMBROIDERY_VECTOR"):
            continue
        val = dict(p.get("values", {}))
        for k in patch_ids:
            val.update(patch.get(k, {}))
        p = dict(p, values=val)
        d = x.get("dPath") or vettori.get((x.get("vectorRef") or {}).get("vectorHash"), "")
        if not isinstance(d, str):
            d = json.dumps(d)
        g = _poligono(d, x.get("transform") or [1, 0, 0, 1, 0, 0])
        if g is None or g.is_empty:
            continue
        col = (val.get("fillColor") if p["processingType"] == "EMBROIDERY_FILL" else val.get("lineColor")) \
            or x.get("layerColor") or "#000000"
        punti = []
        for reg in _punti_forma(g, p):
            if len(reg) > 1:
                punti += [("M", reg[0][0] * 10, reg[0][1] * 10)] + [("L", a * 10, b * 10) for a, b in reg]
        if punti:
            out.append({"colore": col.lower(), "punti": punti, "transform": None, "nome": x.get("name", ""),
                        "_geo": g})
    # tra due forme vicine dello stesso colore, una dopo l'altra: filza nascosta se la linea resta dentro le due forme
    from shapely.geometry import LineString
    for a, b in zip(out, out[1:]):
        if a["colore"] == b["colore"]:
            p0, p1 = a["punti"][-1][1:], b["punti"][0][1:]
            seg = LineString([(p0[0] / 10, p0[1] / 10), (p1[0] / 10, p1[1] / 10)])
            if a["_geo"].union(b["_geo"]).buffer(0.3).contains(seg):
                b["punti"][0] = ("L",) + b["punti"][0][1:]
                b["punti"][0:0] = [("L", x * 10, y * 10) for x, y in _corsa_tra(seg.coords[0], seg.coords[1])[:-1]]
    for b in out:
        b.pop("_geo", None)
    if not out:
        raise ValueError("nel file non ci sono forme da ricamare")
    return out, {"blocchi": len(out), "colori": len({b["colore"] for b in out}), "modo": "forme (punti calcolati qui)"}


def stampa_xs(percorso, uscita_png):
    """Progetti «ricamo + stampa» (DTG, xTool DT002): salva l'immagine da stampare con i DPI giusti, così stampata
    al 100% ha la misura del progetto. -> (larg_mm, alt_mm, posizione x, y in mm nel telaio) o None."""
    from PIL import Image
    z = zipfile.ZipFile(percorso)
    for n in z.namelist():
        if re.fullmatch(r"canvases/[^/]+/displays-\d+\.json", n):
            for x in json.loads(z.read(n)).get("displays", []):
                if x.get("type") != "BITMAP":
                    continue
                png = [r for r in z.namelist() if r.startswith("resources/")]
                if not png:
                    return None
                im = Image.open(io.BytesIO(z.read(png[0])))
                a = float(x["transform"][0])                         # mm per pixel
                dpi = 25.4 / a
                im.save(uscita_png, dpi=(dpi, dpi))
                return round(im.width * a, 1), round(im.height * a, 1), float(x["transform"][4]), float(x["transform"][5])
    return None


def leggi_xs(percorso):
    """Restituisce (blocchi in ordine di ricamo, info). Blocco = {"colore": "#rrggbb", "punti": [(cmd, x, y)...],
    "transform": [a, b, c, d, e, f]}; cmd "M" salto, "L" punto, coordinate in decimi di mm."""
    z = zipfile.ZipFile(percorso)
    nomi = z.namelist()
    leggi = lambda n: json.loads(z.read(n))                                   # noqa: E731
    vettori = {}
    for n in nomi:
        if re.fullmatch(r"vectors/svg/data-\d+\.json", n):
            vettori.update(leggi(n).get("entries", {}))
    blocchi_raw = []
    for n in nomi:
        if re.fullmatch(r"canvases/[^/]+/displays-\d+\.json", n):
            blocchi_raw += [x for x in leggi(n).get("displays", []) if x.get("isSTITCH") and x.get("visible", True)]
    if not blocchi_raw:                                      # niente punti: forme vettoriali da riempire
        return leggi_xs_forme(z, nomi)
    # colori: sostituzioni fatte in xTool (patch sul profilo legato ai blocchi)
    sost = {}
    for n in nomi:
        if n.startswith("devices/") and n.endswith(".json"):
            for proc in leggi(n).get("processing", {}).values():
                m = (proc.get("modes") or {}).get("EMBROIDERY") or {}
                patch = {p["profileId"]: p.get("overrides", {}).get("lineColor") for p in (m.get("patches") or {}).values()}
                prof = json.loads(z.read("profiles.json")).get("profiles", {}) if "profiles.json" in nomi else {}
                for b in m.get("bindings") or []:
                    for pid in [b.get("baseProfileId")] + list(b.get("profileIds") or []):
                        if patch.get(pid):
                            base = (prof.get(pid, {}).get("values") or {}).get("lineColor", "").lower()
                            if base:
                                sost[base] = patch[pid].lower()
    out = []
    for x in sorted(blocchi_raw, key=lambda x: float(x.get("zOrder", 0))):
        p = x.get("dPath")
        if not p and x.get("vectorRef"):
            p = vettori.get(x["vectorRef"].get("vectorHash"), "")
        if not isinstance(p, str):
            p = json.dumps(p)
        punti = [(c, float(a), float(b)) for c, a, b in re.findall(r"([ML])\s*(-?[\d.]+)[\s,]+(-?[\d.]+)", p)]
        if not punti:
            continue
        col = (x.get("layerColor") or x.get("originColor") or "#000000").lower()
        out.append({"colore": sost.get(col, col), "punti": punti, "transform": x.get("transform"),
                    "nome": x.get("name", "")})
    info = {"blocchi": len(out), "colori": len({b["colore"] for b in out}), "sostituzioni": sost}
    return out, info


def _forma(b, margine=6):
    """Area coperta dai punti del blocco (+0,6 mm), salti esclusi. Tenuta in cache nel blocco."""
    if "_forma" not in b:
        from shapely.geometry import MultiLineString, Point
        tratti, cur = [], []
        for c, x, y in b["punti"]:
            if c == "M" and cur:
                tratti.append(cur)
                cur = []
            cur.append((x, y))
        tratti.append(cur)
        linee = [t for t in tratti if len(t) > 1]
        g = MultiLineString(linee) if linee else Point(b["punti"][0][1:])
        b["_forma"] = g.buffer(margine, resolution=4)
    return b["_forma"]


def _toccano(a, b):
    return _forma(a).intersects(_forma(b))


def raggruppa_colori(blocchi):
    """Meno cambi di filo: ogni blocco si sposta indietro, subito dopo l'ultimo blocco dello stesso colore, SOLO se
    non tocca nessun blocco che sta in mezzo (così sovrapposizioni e strati restano come li ha pensati il
    disegnatore). Il file di xTool alterna i colori (51 cambi su 9 colori): sulla Innov-is ogni cambio è un rinfilo."""
    out = []
    for b in blocchi:
        pos = len(out)
        for k in range(len(out) - 1, -1, -1):
            if out[k]["colore"] == b["colore"]:
                pos = k + 1
                break
            if _toccano(b, out[k]):
                break                                     # sopra a qualcosa di diverso: resta dov'è
        out.insert(pos, b)
    return out


def in_pattern(blocchi, come_nel_progetto=False):
    """Blocchi -> EmbPattern (unità 0,1 mm, angolo in alto a sinistra in 0,0 come vuole la Innov-is)."""
    pat = pe.EmbPattern()
    prec = None
    ultimo = None
    for b in blocchi:
        if come_nel_progetto and b.get("transform"):
            a, bb, c, d, e, f = [float(v) for v in b["transform"]]
            # il progetto xTool è in mm: punto (0,1 mm) -> mm del telaio -> 0,1 mm
            tr = lambda x, y: ((a * x + c * y + e) * 10, (bb * x + d * y + f) * 10)    # noqa: E731
        else:
            tr = lambda x, y: (x, y)                                                     # noqa: E731
        if b["colore"] != prec:
            if prec is not None:
                pat.add_command(pe.TRIM)
                pat.add_command(pe.COLOR_CHANGE)
            t = pe.EmbThread()
            t.set_color(int(b["colore"][1:3], 16), int(b["colore"][3:5], 16), int(b["colore"][5:7], 16))
            t.description = b["colore"]
            pat.add_thread(t)
            prec = b["colore"]
        for cmd, x, y in b["punti"]:
            X, Y = tr(x, y)
            if cmd == "M":
                if ultimo and ((X - ultimo[0]) ** 2 + (Y - ultimo[1]) ** 2) ** 0.5 > SALTO_CON_TAGLIO:
                    pat.add_command(pe.TRIM)
                pat.add_stitch_absolute(pe.JUMP, X, Y)
            else:
                pat.add_stitch_absolute(pe.STITCH, X, Y)
            ultimo = (X, Y)
    pat.add_command(pe.END)
    x0, y0, _, _ = pat.bounds()
    pat.translate(-x0, -y0)
    return pat


def converti(xs, pes=None, come_nel_progetto=False, raggruppa=True):
    blocchi, info = leggi_xs(xs)
    cambi = lambda bl: sum(1 for a, b in zip(bl, bl[1:]) if a["colore"] != b["colore"])   # noqa: E731
    info["cambi_filo_originali"] = cambi(blocchi)
    if raggruppa:
        # più passate: uno spostamento può liberare il successivo
        for _ in range(5):
            nuovi = raggruppa_colori(blocchi)
            if cambi(nuovi) >= cambi(blocchi):
                break
            blocchi = nuovi
    info["cambi_filo"] = cambi(blocchi)
    pat = in_pattern(blocchi, come_nel_progetto)
    pes = pes or os.path.splitext(xs)[0] + ".pes"
    pe.write_pes(pat, pes, {"pes version": 6})
    x0, y0, x1, y1 = pat.bounds()
    info.update(pes=pes, larg_mm=round((x1 - x0) / 10, 1), alt_mm=round((y1 - y0) / 10, 1),
                punti=sum(1 for s in pat.stitches if s[2] == pe.STITCH),
                tagli=sum(1 for s in pat.stitches if s[2] == pe.TRIM))
    return pat, info


if __name__ == "__main__":
    arg = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not arg:
        sys.exit(__doc__)
    ant = None
    if "--anteprima" in sys.argv:
        ant = sys.argv[sys.argv.index("--anteprima") + 1]
        arg = [a for a in arg if a != ant]
    pat, info = converti(arg[0], arg[1] if len(arg) > 1 else None, "--come-nel-progetto" in sys.argv,
                         "--ordine-originale" not in sys.argv)
    print(json.dumps(info, ensure_ascii=False))
    if ant:
        from anteprima_bella import render
        render(pat, 1400, ss=2, sfondo=True).save(ant)
