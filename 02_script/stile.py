"""«Profilo di stile» di un ricamo, misurato dai punti veri (l'idea «LoRA» fatta con la statistica):
dai disegni d'esempio di uno stile si ricavano i numeri che lo distinguono (lunghezza dei punti e quanto varia,
avanti-indietro del satin/tatami contro tratti liberi, quanto cambia la direzione da un punto all'altro, quanti
strati di filo si sovrappongono); il digitalizzatore userà questi numeri per ricamare «in quello stile».

    python stile.py file.pes [altri...]      -> profilo di ogni file e media
"""
import os
import sys

import numpy as np


def punti(pat):
    """Segmenti cuciti consecutivi (mm) per blocco di colore."""
    import pyembroidery as pe
    blocchi, cur, prev = [], [], None
    for x, y, c in pat.stitches:
        c &= pe.COMMAND_MASK
        if c == pe.STITCH:
            if prev is not None:
                cur.append((prev[0] / 10, prev[1] / 10, x / 10, y / 10))
            prev = (x, y)
        elif c in (pe.JUMP, pe.TRIM):
            prev = (x, y) if c == pe.JUMP else None
        elif c in (pe.COLOR_CHANGE, pe.STOP, pe.END):
            if cur:
                blocchi.append(cur)
            cur, prev = [], None
    if cur:
        blocchi.append(cur)
    return blocchi


def profilo(pat, px_mm=10):
    blocchi = punti(pat)
    S = np.array([s for b in blocchi for s in b], float)
    if len(S) < 50:
        raise ValueError("troppo pochi punti")
    d = S[:, 2:] - S[:, :2]
    L = np.hypot(d[:, 0], d[:, 1])
    ok = L > 0.05
    # coppie consecutive nello stesso blocco
    giro, dth = [], []
    for b in blocchi:
        if len(b) < 3:
            continue
        v = np.array([(s[2] - s[0], s[3] - s[1]) for s in b])
        n = np.hypot(v[:, 0], v[:, 1])
        k = (n[:-1] > 0.3) & (n[1:] > 0.3)
        cosv = (v[:-1] * v[1:]).sum(1)[k] / (n[:-1] * n[1:])[k]
        giro += list(np.degrees(np.arccos(np.clip(cosv, -1, 1))))
        a = np.degrees(np.arctan2(v[:, 1], v[:, 0])) % 180
        da = np.abs(np.diff(a))[k]
        dth += list(np.minimum(da, 180 - da))
    giro, dth = np.array(giro), np.array(dth)
    # strati: quante volte il filo passa sullo stesso punto (griglia 0,1 mm)
    from PIL import Image, ImageDraw
    x0, y0 = S[:, [0, 2]].min(), S[:, [1, 3]].min()
    W = int((S[:, [0, 2]].max() - x0) * px_mm) + 3
    H = int((S[:, [1, 3]].max() - y0) * px_mm) + 3
    acc = np.zeros((H, W), np.uint16)
    for b in blocchi:
        im = Image.new("L", (W, H), 0)
        dr = ImageDraw.Draw(im)
        for s in b:
            dr.line([((s[0] - x0) * px_mm, (s[1] - y0) * px_mm), ((s[2] - x0) * px_mm, (s[3] - y0) * px_mm)], fill=1, width=4)
        acc += np.asarray(im, np.uint16)          # strati di COLORI diversi sovrapposti
    cop = acc > 0
    Lk = L[ok]
    return {
        "punti": int(len(S)),
        "colori": len(blocchi),
        "lung_p10": round(float(np.percentile(Lk, 10)), 2),
        "lung_p50": round(float(np.percentile(Lk, 50)), 2),
        "lung_p90": round(float(np.percentile(Lk, 90)), 2),
        "lung_cv": round(float(Lk.std() / Lk.mean()), 2),                 # quanto variano le lunghezze
        "avanti_indietro": round(float((giro > 150).mean()), 2),          # satin/tatami: il filo torna indietro
        "dritto": round(float((giro < 30).mean()), 2),                     # filze, contorni
        "svolta_media": round(float(np.median(dth)), 1),                   # quanto cambia direzione (0 = parallelo)
        "strati_colore": round(float(acc[cop].mean()), 2),                 # colori sovrapposti nello stesso punto
        "densita_mm": round(float(Lk.sum() / max(1, cop.sum() / px_mm ** 2)), 2),   # mm di filo per mm² coperto
    }


if __name__ == "__main__":
    import pyembroidery as pe
    righe = []
    for f in sys.argv[1:]:
        p = profilo(pe.read(f))
        righe.append(p)
        print(os.path.basename(f)[:30].ljust(30), p)
