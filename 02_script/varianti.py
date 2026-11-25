"""Varianti di misura dello stesso disegno (Hdsmm4a/4b/4c, Butterfly/Butterflylg, Rose_5x7/Rose_4x4…).

Due file sono varianti solo se TUTTE queste cose sono vere:
  1. stessa raccolta d'origine (stessa cartella, tema escluso);
  2. stesso nome "base" dopo aver tolto un suffisso di misura riconosciuto
     (lettera finale a-f dopo una cifra o un separatore, s/m/l/lg/sm/med/big/small/large, 4x4, 5x7, 130x180…);
     il suffisso è obbligatorio per almeno uno dei due: "123" e "124" non vengono mai uniti;
  3. le immagini si somigliano davvero: SigLIP E pixel sovrapposti d'accordo (vedi decidi());
  4. proporzioni simili (lato lungo/lato corto entro ±20%).
Uso come modulo: gruppi(DATI, EMB, n_emb) -> {indice: id_gruppo} solo per i disegni che hanno varianti.
Uso da riga di comando: python varianti.py <Ricami_ordinati>  => statistiche + foglio di controllo.
"""
import os
import re
from collections import defaultdict

import numpy as np

_FORME = {}


def forma(cat, i, N=32):
    """Il disegno in scala di grigi, ritagliato sul filo e portato a NxN (sfumato): sovrapponendo due misure
    dello stesso disegno i pixel coincidono. Il filo è ciò che differisce dallo sfondo (angolo)."""
    if i in _FORME:
        return _FORME[i]
    from PIL import Image
    from scipy import ndimage
    try:
        im = np.asarray(Image.open(os.path.join(cat, "img", f"{i}.webp")).convert("RGB"), dtype=np.float32)
    except OSError:
        _FORME[i] = None
        return None
    m = np.abs(im - im[2, 2]).sum(2) > 30
    ys, xs = np.where(m)
    if len(xs) < 20:
        _FORME[i] = None
        return None
    c = im[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8)
    g = np.asarray(Image.fromarray(c).convert("L").resize((N, N), Image.BILINEAR), dtype=np.float32)
    g = ndimage.gaussian_filter(g, 1.0)
    g -= g.mean()
    _FORME[i] = g / (np.linalg.norm(g) + 1e-6)
    return _FORME[i]


def pixel(cat, i, j):
    """Correlazione dei pixel sovrapposti (1 = identici, 0 = niente a che vedere)."""
    a, b = forma(cat, i), forma(cat, j)
    return float((a * b).sum()) if a is not None and b is not None else 0.0


def decidi(c, pr, px, taglia=False):
    """PRUDENTE: unisce solo se immagine (SigLIP) e pixel sono d'accordo con margine.
    Tarato su coppie vere (Hdsmm 4a/4c, 8a/8b, 12a/12b, 6a/6c, orsetto 105x7/104x4) e false
    (lettere A/B in riquadri uguali, vetrate gatto/uccello, fata/ballerina Schiaccianoci cos 0.98 pixel 0.45,
    scene di Halloween diverse cos 0.898 pixel 0.98). Una variante persa = una scheda in più; un'unione
    sbagliata nasconderebbe un disegno => meglio perdere."""
    if not 0.8 <= pr <= 1.25:
        return False
    if taglia and c >= 0.93 and px >= 0.5:      # il nome dice la misura (Lg/Sm, 5x7, S/M/L): è già una prova
        return True
    return (c >= 0.92 and px >= 0.85) or (c >= 0.97 and px >= 0.65)


_MIS = r"(?:\d{2,3}\s*x\s*\d{2,3}(?:mm)?|\d(?:\.\d)?\s*x\s*\d{1,2}(?:\.\d)?)"     # 130x180, 5x7, 4x4, 6.5x10
_TAGLIE = r"(?:xs|s|m|l|xl|xxl|sm|sml|med|lg|lrg|big|small|medium|large|mini|grande|piccolo|medio)"
RX_SUFF = [
    re.compile(r"^(.*?)[\s_\-.]*" + _MIS + r"$", re.I),                        # rose_5x7
    re.compile(r"^(.*?)[\s_\-.]+" + _TAGLIE + r"$", re.I),                     # rose_lg, rose-small
    re.compile(r"^(.*?[a-z])(?:lg|lrg|sm|sml|med|big|small|large)$", re.I),    # butterflylg (solo parole lunghe)
    re.compile(r"^(.*\d)([a-f])$", re.I),                                      # hdsmm4a
    re.compile(r"^(.*?)[\s_\-.]+([a-f])$", re.I),                              # rose_a
]


def base_variante(nome):
    """(base, tipo): tipo "taglia" se il suffisso dice la misura (lg, 5x7, s/m/l), "lettera" se è a-f, "" se niente."""
    n = os.path.splitext(nome)[0].lower().strip()
    for k, rx in enumerate(RX_SUFF):
        m = rx.match(n)
        if m and len(m.group(1)) >= 2:
            return m.group(1).rstrip(" _-."), ("taglia" if k < 3 else "lettera")
    return n, ""


def _raccolta(percorso):
    parti = percorso.replace("/", "\\").split("\\")
    return "\\".join(parti[1:-1]).lower()


def _prop(d):
    a, b = sorted((max(d[2], 0.1), max(d[3], 0.1)))
    return b / a


def gruppi(DATI, EMB, n_emb, cat, diag=None):
    cand = defaultdict(list)
    for i, d in enumerate(DATI):
        base, suff = base_variante(os.path.basename(d[0]))
        cand[(_raccolta(d[0]), base)].append((i, suff))
    out, gid = {}, 0
    for (_, _), membri in cand.items():
        if len(membri) < 2 or not any(s for _, s in membri) or len(membri) > 12:
            continue
        tipo = dict(membri)
        ii = [i for i, _ in membri if EMB is not None and i < n_emb]    # senza impronta non rischio
        capo = {i: i for i in ii}

        def radice(x):
            while capo[x] != x:
                capo[x] = capo[capo[x]]
                x = capo[x]
            return x
        for x in range(len(ii)):
            for y in range(x + 1, len(ii)):
                i, j = ii[x], ii[y]
                c = float(EMB[i] @ EMB[j])
                pr = _prop(DATI[i]) / _prop(DATI[j])
                taglia = "lettera" not in (tipo[i], tipo[j]) and "taglia" in (tipo[i], tipo[j])
                px = pixel(cat, i, j) if c >= 0.92 and 0.8 <= pr <= 1.25 else 0.0
                ok = decidi(c, pr, px, taglia)
                if diag is not None:
                    diag.append((c, ok, i, j, px, pr))
                if ok:
                    capo[radice(i)] = radice(j)
        comp = defaultdict(list)
        for i in ii:
            comp[radice(i)].append(i)
        for tenuti in comp.values():
            if len(tenuti) > 1:
                for i in tenuti:
                    out[i] = gid
                gid += 1
    return out


if __name__ == "__main__":
    import json
    import random
    import sys
    OUT = sys.argv[1]
    CAT = os.path.join(OUT, "_catalogo")
    t = open(os.path.join(CAT, "dati.js"), encoding="utf-8").read()
    DATI = json.loads(t.split("const DATI=", 1)[1].rstrip().rstrip(";"))
    n = int(open(os.path.join(CAT, "clip_n.txt")).read())
    E = np.load(os.path.join(CAT, "clip.npy"))[:n].astype(np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-8
    diag = []
    g = gruppi(DATI, E, n, CAT, diag=diag)
    print("coppie candidate", len(diag), "unite", sum(1 for x in diag if x[1]))
    if "--json" in sys.argv:                    # OpenHoop: solo il risultato (dopo l'analisi delle immagini)
        json.dump({str(k): v for k, v in g.items()}, open(os.path.join(CAT, "varianti.json"), "w"))
        print("disegni in gruppi", len(g), "gruppi", len(set(g.values())))
        sys.exit(0)
    from PIL import Image, ImageDraw
    rr = random.Random(1)
    S = 110
    righe = [("UNITE al limite: cos 0.92-0.94", [x for x in diag if x[1] and x[0] < 0.94]),
             ("UNITE al limite: pixel 0.65-0.85", [x for x in diag if x[1] and x[4] < 0.85]),
             ("NON unite: cos >= 0.95, pixel 0.5-0.65", [x for x in diag if not x[1] and x[0] >= 0.95 and 0.5 <= x[4] < 0.65])]
    img = Image.new("RGB", (8 * 2 * S + 7 * 10, len(righe) * 3 * (S + 18) + 40), "white")
    dr = ImageDraw.Draw(img)
    y = 0
    for tit, xs in righe:
        dr.text((4, y + 2), tit, fill="red"); y += 16
        for r in range(3):
            for c in range(8):
                if not xs:
                    break
                cos_, _, a, b, det_, _ = xs.pop(rr.randrange(len(xs)))
                x0 = c * (2 * S + 10)
                for k, i in enumerate((a, b)):
                    f = os.path.join(CAT, "img", f"{i}.webp")
                    if os.path.exists(f):
                        im = Image.open(f).convert("RGB"); im.thumbnail((S - 2, S - 2)); img.paste(im, (x0 + k * S, y))
                dr.text((x0, y + S), f"{cos_:.2f} px{det_:.2f} {os.path.basename(DATI[a][0])[:10]}/{os.path.basename(DATI[b][0])[:10]}", fill="black")
            y += S + 18
    ante = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "04_anteprima", "varianti")
    os.makedirs(ante, exist_ok=True)
    img.save(os.path.join(ante, "soglia_forma.jpg"), quality=85)
    print("disegni in gruppi", len(g), "gruppi", len(set(g.values())))
    json.dump({str(k): v for k, v in g.items()}, open(os.path.join(CAT, "varianti.json"), "w"))
