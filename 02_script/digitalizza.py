"""Da un disegno semplice (clipart: colori pieni, contorni netti) a un ricamo. Tappa 1 dell'auto-digitalizzazione.

Passi:
  1. sfondo: chiaro e collegato ai bordi (il bianco DENTRO un contorno resta: pupazzo di neve);
  2. colori: k-means in Lab, colori vicini uniti, sfumature dei bordi riassegnate; ogni colore -> filo più vicino;
  3. zone: componenti connesse, pezzetti minuscoli assorbiti dal vicino;
  4. punto per zona dallo SPESSORE (regole emerse dai 5.000 ricami di scuola.py):
       linea sottile (< 1,2 mm)        -> filza tripla lungo lo scheletro
       fascia/linea (1,2 - 7 mm)       -> satin che segue lo scheletro (contorni, glassa, sorriso)
       zona larga                      -> riempimento tatami, file lungo l'asse lungo se la forma è allungata
  5. ordine: riempimenti (dal più grande), poi dettagli, poi le linee; i riempimenti entrano sotto le zone
     ricamate dopo (niente tela scoperta tra un colore e l'altro).

uso: python digitalizza.py immagine.png [altezza_mm=100] [cartella_uscita]
"""
import math
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage
import pes_brother  # noqa: F401,E402  -- PEC senza il n. 64 «Applique» (vedi pes_brother.py)

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)

LINEA_MAX = 5.0        # mm: fasce più larghe diventano riempimento (dati: satin p90 = 4.1 mm; oltre 6 mm il satin fa fili lenti)
FILZA_MAX = 0.8        # mm: linee più sottili -> filza tripla; da qui in su satin largo almeno SATIN_MIN (guide)
SATIN_MIN = 1.0        # mm: satin più stretto non copre (ago da 1 mm); le linee anime da 0,8-1,2 mm vanno a 1 mm
MIN_ZONA = 3.0         # mm² reali: zone più piccole si uniscono alla vicina (riempimento più piccolo nei disegni veri: p5 ~3 mm²)
MIN_ZONA_CONTRASTO = 0.8   # mm²: i dettagli molto contrastati (occhi, naso) restano fino a qui
CONTRASTO_DETTAGLIO = 35.0  # ΔE Lab oltre cui una zonetta è un dettaglio (nero su bianco ~100, guancia su pelle ~20)
MIN_COLORE_MM2 = 4.0   # mm² reali: un colore che in tutto il disegno occupa meno di così sparisce (si fonde col più simile)


# ---------------------------------------------------------------- 1-3: analisi dell'immagine
PX_MM_MIN = 10.0       # px/mm: immagini più piccole si ingrandiscono prima dell'analisi (contorni lisci)


def carica(percorso, altezza_mm):
    """percorso = file o oggetto aperto; altezza_mm = misura del lato lungo del ricamo."""
    rgb, ppm, _, _ = carica_con_linee(percorso, altezza_mm)
    return rgb, ppm


def _ritaglio(im):
    """PASSO 26: riquadro del DISEGNO dentro l'immagine (senza il bianco attorno), in pixel dell'originale. Nota:
    «la parte bianca non deve essere contata»: il «lato lungo» chiesto deve essere quello del ricamo, non del foglio.
    Contano solo le macchie non bianche di almeno lo 0,2% dell'immagine (non la polvere né una firma minuscola)."""
    rgb = _su_bianco(im)
    m = rgb.min(2) < 232
    if not m.any():
        return None
    lab, n = ndimage.label(m, structure=np.ones((3, 3)))
    dim = np.bincount(lab.ravel())
    dim[0] = 0
    tieni = np.nonzero(dim >= 0.002 * m.size)[0]
    if not len(tieni):
        return None
    ys, xs = np.nonzero(np.isin(lab, tieni))
    H, W = m.shape
    # pezzetti piccoli vicino al disegno (trattini di un tappeto, fiocchi di neve dei disegni a linee): contano se
    # insieme fanno qualcosa (≥ 0,1% dell'immagine), non un granello di polvere da solo
    piccoli = np.nonzero((dim > 0) & (dim < 0.002 * m.size) & (dim >= max(20, 0.00002 * m.size)))[0]
    if len(piccoli):
        x0c, y0c, x1c, y1c = xs.min(), ys.min(), xs.max(), ys.max()
        d = int(0.08 * max(H, W))
        sl = ndimage.find_objects(lab)
        vicini = [k for k in piccoli if sl[k - 1] is not None
                  and sl[k - 1][0].start <= y1c + d and sl[k - 1][0].stop >= y0c - d
                  and sl[k - 1][1].start <= x1c + d and sl[k - 1][1].stop >= x0c - d]
        if vicini and dim[vicini].sum() >= 0.001 * m.size:
            ys, xs = np.nonzero(np.isin(lab, np.concatenate([tieni, vicini])))
    mg = max(2, int(0.005 * max(H, W)))
    x0, y0, x1, y1 = max(0, xs.min() - mg), max(0, ys.min() - mg), min(W, xs.max() + 1 + mg), min(H, ys.max() + 1 + mg)
    return (x0, y0, x1, y1)


def a_ritaglio(rit, fx, fy):
    """Frazioni dell'immagine ORIGINALE -> frazioni del disegno ritagliato."""
    return (fx - rit[0]) / rit[2], (fy - rit[1]) / rit[3]


def a_originale(rit, fx, fy):
    """Frazioni del disegno ritagliato -> frazioni dell'immagine originale (pagina, zone escluse, etichette)."""
    return rit[0] + fx * rit[2], rit[1] + fy * rit[3]


def carica_con_linee(percorso, altezza_mm):
    """Come carica, più la maschera delle linee scure sottili ridisegnate (passo 19) o None."""
    im = Image.open(percorso).convert("RGBA")
    W0, H0 = im.size
    box = _ritaglio(im)
    rit = (0.0, 0.0, 1.0, 1.0)
    if box:
        im = im.crop(box)
        rit = (box[0] / W0, box[1] / H0, (box[2] - box[0]) / W0, (box[3] - box[1]) / H0)
    lato_px = max(im.size)
    ppm = max(im.size) / altezza_mm
    linee = None
    # PASSO 37 (28/11/2026): sotto 10 px/mm si ingrandisce (Lanczos) prima dell'analisi: il cerbiatto ridisegnato
    # (1189 px a 179 mm = 6,6 px/mm) dava contorni seghettati, lo scheletro seguiva gli scalini dei pixel
    if not PX_MM_MIN <= ppm <= 14:                           # 0.07-0.1 mm per pixel: abbastanza fine, non troppo lento
        k = min(14, max(PX_MM_MIN, ppm)) / ppm
        dim = (max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k)))
        if k < 0.45:                                         # solo se le linee si dimezzano (Elsa 2400 px a 50 mm)
            linee = _linee_scure(_su_bianco(im), dim)
        im = im.resize(dim, Image.LANCZOS)
    rgb = _su_bianco(im)
    maschera = None
    if linee is not None:
        rgb, maschera = _rimetti_linee(rgb, *linee)
    ppm = max(rgb.shape[:2]) / altezza_mm                                     # pixel per mm
    return rgb.astype(np.uint8), ppm, maschera, (rit, lato_px)


def _su_bianco(im):
    a = np.asarray(im).astype(np.float32)
    return a[..., :3] * (a[..., 3:4] / 255) + 255 * (1 - a[..., 3:4] / 255)    # trasparenza -> bianco


# PASSO 19: rimpicciolendo per i 50 mm il contorno nero di Elsa (6 px su 2400) diventava 1,7 px grigio e il filtro
# dei colori (5x5 px) lo cancellava. Le linee scure si riconoscono sull'immagine ORIGINALE e, dove dopo il
# rimpicciolimento sono più sottili di 3 px, si ridisegnano larghe 3 px col loro colore (come i disegni veri da 50 mm:
# il contorno c'è sempre, portato poi a satin largo SATIN_MIN dal passo 3).
def _linee_scure(rgb, dim, soglia_L=50.0):
    from skimage.color import rgb2lab
    lab = rgb2lab(rgb / 255.0)
    # solo scuri NEUTRI (contorni neri/grigi): pupille e iridi blu scure non sono linee da ridisegnare
    D = ((lab[..., 0] < soglia_L) & (np.hypot(lab[..., 1], lab[..., 2]) < 15)).astype(np.float32)
    if not D.any():
        return None
    # solo se le linee rimpicciolite scenderebbero sotto 2 px (Elsa test: 6 px -> 1,7). Quelle più spesse
    # (cute elsa: 8 px -> 2,3) passano già da sole: riscriverle le spezzava e il contorno spariva
    from skimage.morphology import skeletonize
    sk = skeletonize(D > 0)
    if not sk.any() or np.median(2 * ndimage.distance_transform_edt(D)[sk]) * dim[0] / rgb.shape[1] >= 2.0:
        return None
    cop = np.asarray(Image.fromarray(D).resize(dim, Image.BOX))
    col = np.stack([np.asarray(Image.fromarray(rgb[..., i] * D).resize(dim, Image.BOX)) for i in range(3)], -1)
    return cop, col / np.maximum(cop, 1e-6)[..., None]


def _rimetti_linee(rgb, cop, col, min_cop=0.25):
    linea = cop >= min_cop
    spessa = ndimage.binary_opening(linea, structure=np.ones((3, 3)))
    sottile = linea & ~spessa
    if not sottile.any():
        return rgb, None
    dest = ndimage.binary_dilation(sottile, structure=np.ones((3, 3))) & ~spessa
    _, (iy, ix) = ndimage.distance_transform_edt(~linea, return_indices=True)
    out = rgb.copy()
    out[dest] = col[iy[dest], ix[dest]]
    return out, dest


def sfondo(rgb, soglia=232):
    """Pixel chiari collegati al bordo dell'immagine."""
    chiaro = (rgb.min(2) >= soglia)
    lab, n = ndimage.label(chiaro)
    bordo = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    return np.isin(lab, list(bordo))


def colori(rgb, fondo, max_k=12, unisci_de=14.0, minimo=0.004, ppm=None, min_mm2=None):
    """-> (etichette HxW con -1 = sfondo, lista colori rgb). k-means in Lab sui pixel del disegno."""
    from scipy.cluster.vq import kmeans2
    from skimage.color import rgb2lab
    lisc = ndimage.median_filter(rgb, size=(5, 5, 1))
    lab = rgb2lab(lisc / 255.0)
    fg = ~fondo
    X = lab[fg]
    rnd = np.random.default_rng(1)
    camp = X[rnd.choice(len(X), min(60000, len(X)), replace=False)]
    # PASSO 8: nei disegni a colori piatti i colori veri sono i PICCHI dell'istogramma (grandi aree identiche).
    # Il k-means spendeva i centri sulle sfumature (capelli di Elsa) e metteva la pelle chiara nel gruppo dei
    # capelli. Prima i picchi distinti (ΔE > 6), poi il k-means parte da lì.
    q = np.round(camp / 3.0).astype(np.int32)
    chiavi, inv, cnt = np.unique(q, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    picchi = []
    for k in np.argsort(-cnt):
        if cnt[k] < max(30, 0.004 * len(camp)) or len(picchi) >= max_k:
            break
        c = camp[inv == k].mean(0)
        if all(np.linalg.norm(c - p) > 6.0 for p in picchi):
            picchi.append(c)
    if len(picchi) >= 2:
        C, _ = kmeans2(camp, np.array(picchi), minit="matrix", iter=5)
    else:
        C, _ = kmeans2(camp, max_k, seed=1, minit="++")
    # PASSO 30 (27/11/2026): colori SFUMATI senza picco (guance rosa, lingua del cagnolino in «Bimba e
    # cucciolo»): nessun rosa identico copre abbastanza pixel per fare un picco e finiva nel color pelle.
    # Nelle zone uniformi (non sui bordi, dove le sfumature tra due colori sono normali) i pixel lontani da
    # tutti i colori scelti, se sono abbastanza, diventano un colore in più
    if len(C) < max_k:
        gr = sum(np.abs(np.gradient(lab[..., k], axis=a)) for k in range(3) for a in (0, 1))
        piatti = lab[fg & (gr < 4.0)]
        if len(piatti):
            piatti = piatti[rnd.choice(len(piatti), min(60000, len(piatti)), replace=False)]
            dmin = np.sqrt(((piatti[:, None] - C[None]) ** 2).sum(-1)).min(1)
            lontani = piatti[dmin > 18.0]
            serve = max(30, 0.003 * len(piatti))
            if ppm and min_mm2:
                serve = max(serve, min_mm2 * ppm ** 2 * len(piatti) / max(1, fg.sum()))
            for _ in range(3):
                if len(lontani) < serve or len(C) >= max_k:
                    break
                c, _ = kmeans2(lontani, 1, seed=1, minit="++")
                lontani = lontani[np.sqrt(((lontani - c[0]) ** 2).sum(-1)) > 18.0]
                # solo colori chiari o medi (rosa, pesca): un colore scuro aggiunto così (iride blu della ragazza di prova)
                # si prendeva pezzi del contorno scuro e il contorno a 10 cm usciva più sottile
                if c[0][0] >= 40:
                    C = np.vstack([C, c])
    # unisci centri vicini (stesso colore spezzato in due) e togli quelli rari (sfumature dei bordi)
    while True:
        d = np.sqrt(((C[:, None] - C[None]) ** 2).sum(-1)) + np.eye(len(C)) * 1e9
        i, j = np.unravel_index(d.argmin(), d.shape)
        if d[i, j] > unisci_de:
            break
        C[i] = (C[i] + C[j]) / 2
        C = np.delete(C, j, 0)
    for _ in range(3):
        idx = ((X[:, None] - C[None]) ** 2).sum(-1).argmin(1)
        quota = np.bincount(idx, minlength=len(C)) / len(X)
        tieni = quota >= minimo
        if ppm and min_mm2:                              # PASSO 4: un colore che in tutto occupa pochi mm² REALI non
            tieni &= quota * len(X) / ppm ** 2 >= min_mm2   # vale un cambio di filo: rimpicciolendo il disegno si semplifica
        C = np.array([X[idx == k].mean(0) for k in range(len(C)) if tieni[k] and (idx == k).any()])
    idx = ((X[:, None] - C[None]) ** 2).sum(-1).argmin(1)
    et = np.full(fondo.shape, -1, np.int32)
    et[fg] = idx
    from skimage.color import lab2rgb
    rgbc = (lab2rgb(C[None])[0] * 255).round().astype(int)
    return et, [tuple(c) for c in rgbc]


def pulisci(et, ppm):
    """Bordi a scaletta e sfumature: filtro di maggioranza (le etichette restano quelle)."""
    from skimage.filters.rank import majority
    from skimage.morphology import disk
    e = (et + 1).astype(np.uint8)
    e = majority(e, disk(max(1, int(round(0.15 * ppm)))))
    return e.astype(np.int32) - 1


def _compatto(m):
    """Pallino (pupilla, riflesso, nasino), non pezzo di linea: area < 3 volte il cerchio inscritto."""
    ys, xs = np.nonzero(m)
    sub = np.pad(m[ys.min():ys.max() + 1, xs.min():xs.max() + 1], 1)
    r = ndimage.distance_transform_edt(sub).max()
    return m.sum() < 3.0 * np.pi * r * r


def _ospite_grande(et, anello, v, minimo):
    """Il fondo su cui ingrandire è una zona grande (viso, occhio bianco), non un altro dettaglio."""
    lab, _ = ndimage.label(et == v, structure=np.ones((3, 3)))
    ids = np.unique(lab[anello & (et == v)])
    ids = ids[ids > 0]
    return len(ids) > 0 and max((lab == i).sum() for i in ids) >= minimo


def zone(et, ppm, pal=None, ingrandisci=False, registro=None):
    """Componenti connesse per colore; quelle sotto MIN_ZONA passano al colore vicino più frequente.
    PASSO 4 corretto: si assorbono le zonette POCO contrastate (sfumature, ombre, guance); quelle molto
    contrastate (occhi, naso, bocca) restano fino a MIN_ZONA_CONTRASTO: i digitalizzatori le tengono sempre."""
    min_px = MIN_ZONA * ppm * ppm
    min_px_c = MIN_ZONA_CONTRASTO * ppm * ppm
    labp = None
    if pal is not None:
        from skimage.color import rgb2lab
        labp = rgb2lab(np.array(pal, float)[None] / 255.0)[0]
    # 1) assorbi PRIMA tutti i pezzetti (sfumature attorno a occhi e glassa) nel colore vicino; NON nello sfondo
    for _ in range(4):
        cambiati = 0
        for c in sorted(set(np.unique(et)) - {-1}):
            lab, n = ndimage.label(et == c, structure=np.ones((3, 3)))
            if not n:
                continue
            dim = np.bincount(lab.ravel())
            for k in np.nonzero(dim[1:] < min_px)[0] + 1:
                m = lab == k
                anello = ndimage.binary_dilation(m, iterations=2) & ~m
                vic = et[anello]
                vic = vic[(vic != c) & (vic >= 0)]
                if len(vic):
                    v = np.bincount(vic).argmax()
                    if labp is not None and dim[k] >= min_px_c and np.linalg.norm(labp[c] - labp[v]) > CONTRASTO_DETTAGLIO:
                        continue                              # dettaglio contrastato: resta
                    # PASSO 23a (semplifica per la misura): un dettaglio contrastato troppo piccolo (riflesso
                    # dell'occhio, pupilla, nasino a 50 mm) non sparisce: si INGRANDISCE sul suo fondo fino alla
                    # soglia, come fa il digitalizzatore che «esagera» gli occhi
                    if ingrandisci and labp is not None and dim[k] >= 0.2 * ppm * ppm                             and np.linalg.norm(labp[c] - labp[v]) > CONTRASTO_DETTAGLIO                             and _compatto(m) and _ospite_grande(et, anello, v, 6 * min_px_c):
                        g = m.copy()
                        for _ in range(int(0.6 * ppm) + 1):
                            if g.sum() >= 1.3 * min_px_c:
                                break
                            g |= ndimage.binary_dilation(g) & (et == v)
                        if g.sum() >= min_px_c:
                            et[g] = c
                            if registro is not None:
                                ys, xs = np.nonzero(m)
                                registro.append({"tipo": "ingrandito", "y": float(ys.mean()), "x": float(xs.mean())})
                            cambiati += 1
                            continue
                    et[m] = v
                    cambiati += 1
        if not cambiati:
            break
    # 2) poi numera le zone sull'immagine pulita (prima i buchi rimasti a -1 sembravano «sfondo»)
    out = np.full(et.shape, -1, np.int32)
    info = []
    for c in sorted(set(np.unique(et)) - {-1}):
        lab, n = ndimage.label(et == c, structure=np.ones((3, 3)))
        for k in range(1, n + 1):
            m = lab == k
            out[m] = len(info)
            info.append({"id": len(info), "colore": int(c), "px": int(m.sum())})
    return out, info


def separa_macchie(z, info, ppm, pal=None, spess_mm=0.6, min_mm2=10.0):
    """PASSO 35 (28/11/2026): una zona fatta di linee sottili con dentro una MACCHIA piena dello stesso colore
    (pupilla nera attaccata al contorno nero dell'occhio nel cerbiatto ridisegnato: una zona sola «linea» -> il satin
    seguiva lo scheletro e una riga passava in mezzo all'occhio). La macchia (spessore > 2×spess_mm) diventa una
    zona sua, STESSO colore: si riempie, e il contorno resta linea.
    (Una prima versione le dava anche un colore suo: nell'occhio della ragazza di prova pupilla e iride diventavano un disco
    azzurro. Il colore non si tocca.)"""
    from skimage.morphology import skeletonize
    sl = ndimage.find_objects(z + 1)
    r = max(1, int(round(spess_mm * ppm)))
    # solo il colore del CONTORNO (il più scuro della tavolozza): pupilla nera attaccata al contorno nero. Su altri
    # colori (anello azzurro dell'iride della ragazza di prova) la «macchia» riempita copriva la pupilla
    scuro = int(np.argmin([sum(c[:3]) for c in pal])) if pal else None
    for o in list(info):
        k = o["id"]
        if scuro is not None and o["colore"] != scuro:
            continue
        if k >= len(sl) or sl[k] is None or o["px"] < 3 * min_mm2 * ppm * ppm:
            continue
        s_ = sl[k]
        m = z[s_] == k
        dt = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1]
        sk = skeletonize(m)
        if not sk.any() or np.median(dt[sk]) / ppm > 0.45:   # la zona non è fatta di linee sottili
            continue
        nucleo, n = ndimage.label(dt > r)
        for j in range(1, n + 1):
            # 2r: la macchia si prende anche i trattini sottili rimasti attaccati (attorno al riflesso), che
            # altrimenti diventavano una linea dentro la pupilla
            blob = ndimage.binary_dilation(nucleo == j, iterations=2 * r) & m
            npx = int(blob.sum())
            if npx < min_mm2 * ppm * ppm or npx > 0.7 * m.sum():
                continue
            # solo macchie TONDE e piene (pupille): i pezzi di ciglia spesse della ragazza di prova (allungati, 2-5 mm²) staccati
            # facevano diventare filza il resto del contorno dell'occhio e la pupilla spariva
            ys_, xs_ = np.nonzero(blob)
            sub_b = blob[ys_.min():ys_.max() + 1, xs_.min():xs_.max() + 1]
            from skimage.morphology import convex_hull_image
            if not _compatto(blob) or sub_b.sum() < 0.8 * convex_hull_image(sub_b).sum():
                continue
            nid = len(info)
            sub = z[s_]
            sub[blob] = nid
            info.append({"id": nid, "colore": o["colore"], "px": npx, "macchia": True, "padre": k})
            o["px"] -= npx
            o["ha_macchia"] = True
    return z, info


def dividi_zone(rgb, z, info, ppm, min_mm2=60.0, soglia=3.0, parte_mm2=20.0, spess_mm=1.0):
    """PASSO 18: due colori vicini che si TOCCANO senza contorno (viso 255,234,215 e capelli 252,243,213 di Elsa,
    uniti dove la linea nera è aperta) finiscono nello stesso gruppo E nella stessa zona: nessuna regola per zona
    li separa. Qui una zona grande si divide in due se dentro ha due colori (ΔE2000 > soglia), la parte più
    piccola è un'area compatta (≥ parte_mm2, spessa ≥ 2 mm) e il confine tra i due è NETTO (disegno a tinte
    piatte), non una sfumatura (cielo, ombre morbide: il colore cambia piano piano e resta una zona sola)."""
    from scipy.cluster.vq import kmeans2
    from skimage.color import deltaE_ciede2000, rgb2lab
    lab = rgb2lab(ndimage.median_filter(rgb, size=(3, 3, 1)) / 255.0)
    grad = np.sqrt(sum(ndimage.sobel(lab[..., k], 0) ** 2 + ndimage.sobel(lab[..., k], 1) ** 2 for k in range(3))) / 8
    r = max(1, int(round(0.4 * ppm)))
    rng = np.random.default_rng(0)
    for o in list(info):
        if o["px"] / ppm ** 2 < min_mm2:
            continue
        m = z == o["id"]
        dentro = ndimage.binary_erosion(m, iterations=2)
        pix = lab[dentro]
        # via le linee sottili inglobate nella zona (a 50 mm i tratti dei capelli): il gruppo «scuro» vinceva
        pix = pix[np.linalg.norm(pix - np.median(pix, 0), axis=1) < 15]
        if len(pix) < 500:
            continue
        camp = pix[rng.choice(len(pix), min(len(pix), 20000), replace=False)]
        cen, _ = kmeans2(camp, 2, minit="++", seed=0)
        s = soglia if min(cen[0][0], cen[1][0]) >= 35 else soglia * 3   # scuri: l'antialiasing inventa sfumature
        if deltaE_ciede2000(cen[0][None], cen[1][None])[0] <= s:
            continue
        ys, xs = np.nonzero(m)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        ms, ls = m[y0:y1, x0:x1], lab[y0:y1, x0:x1]
        part = ms & (((ls - cen[1]) ** 2).sum(-1) < ((ls - cen[0]) ** 2).sum(-1))
        part = ndimage.binary_closing(ndimage.binary_opening(part, iterations=r), iterations=r) & ms
        altra = ms & ~part
        if part.sum() > altra.sum():
            part, altra = altra, part
        salto = np.linalg.norm(cen[0] - cen[1])
        lb, n = ndimage.label(part)
        for k in range(1, n + 1):
            c = lb == k
            if c.sum() < parte_mm2 * ppm ** 2 or ndimage.distance_transform_edt(c).max() < spess_mm * ppm:
                continue
            confine = ndimage.binary_dilation(c, iterations=1) & altra
            if confine.sum() < 3 * ppm or np.median(grad[y0:y1, x0:x1][confine]) < 0.25 * salto:
                continue                                     # sfumatura, non due tinte piatte
            nid = len(info)
            z[y0:y1, x0:x1][c] = nid
            info.append({"id": nid, "colore": o["colore"], "px": int(c.sum())})
            o["px"] -= int(c.sum())
    return z, info


def separa_linee_esterne(z, info, fondo, linee, ppm, min_mm2=1.0):
    """PASSO 19c: il contorno ridisegnato è collegato alle linee interne (ciocche dei capelli): una zona sola che tocca
    lo sfondo -> tutta satin largo 1 mm (passo 3), ciocche nere grosse. Come nei disegni veri da 50 mm: satin solo
    lungo lo sfondo, i rami interni diventano zone a parte (filza)."""
    vicino = ndimage.binary_dilation(fondo, iterations=4)
    for o in list(info):
        m = z == o["id"]
        if not (m & vicino).any() or (m & linee).sum() < 0.5 * o["px"]:
            continue
        lab, n = ndimage.label(m & ~vicino, structure=np.ones((3, 3)))
        if not n:
            continue
        dim = np.bincount(lab.ravel())
        for k in range(1, n + 1):
            if dim[k] < min_mm2 * ppm * ppm:
                continue
            z[lab == k] = len(info)
            info.append({"id": len(info), "colore": o["colore"], "px": int(dim[k])})
            o["px"] -= int(dim[k])
    return z, info


def _rinumera(z, info, ppm=None, toccate=()):
    """id = posizione nell'elenco dopo togli/pota (le zone sparite escono). Nelle zone TOCCATE i pezzetti rimasti
    sotto 0,3 mm² vanno alla zona vicina. Le zone fatte di più pezzi restano intere (spezzarle faceva un buco
    sotto il fiocco di neve di Elsa)."""
    for zid in toccate:
        m = z == zid
        if not m.any():
            continue
        lab, n = ndimage.label(m, structure=np.ones((3, 3)))
        dim = np.bincount(lab.ravel())
        for k in range(1, n + 1):
            if dim[k] < 0.3 * ppm * ppm:
                _riassegna(z, lab == k, zid)
    out = np.full(z.shape, -1, np.int32)
    nuovo = []
    for o in info:
        m = z == o["id"]
        if not m.any():
            continue
        d = dict(o, id=len(nuovo), px=int(m.sum()))
        out[m] = d["id"]
        nuovo.append(d)
    return out, nuovo


def _riassegna(z, togli, zid):
    """I pixel tolti a una zona vanno alla zona vicina più prossima (non allo sfondo, se ce n'è un'altra)."""
    altri = (z != zid) & (z >= 0) & ~togli
    if not altri.any():
        return
    _, (iy, ix) = ndimage.distance_transform_edt(~altri, return_indices=True)
    z[togli] = z[iy[togli], ix[togli]]


def _rami(sk):
    """Rami dello scheletro che partono da una punta: [(pixel del ramo, finisce_in_un_incrocio, lunghezza px)]."""
    vic = ndimage.convolve(sk.astype(np.uint8), np.ones((3, 3), np.uint8), mode="constant") - 1
    vic[~sk] = 0
    punte = list(zip(*np.nonzero(sk & (vic == 1))))
    H, W = sk.shape
    out = []
    for p in punte:
        path, prev, cur, lung = [p], None, p, 0.0
        incrocio = False
        while True:
            y, x = cur
            nb = [(y + dy, x + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy or dx)
                  and 0 <= y + dy < H and 0 <= x + dx < W and sk[y + dy, x + dx] and (y + dy, x + dx) != prev
                  and (y + dy, x + dx) not in path[-3:]]
            if cur != p and vic[cur] >= 3:
                incrocio = True
                break
            if not nb:
                break
            nxt = nb[0]
            lung += 1.4142 if (nxt[0] != y and nxt[1] != x) else 1.0
            prev, cur = cur, nxt
            path.append(cur)
            if len(path) > 4000:
                break
        out.append((path, incrocio, lung))
    return out


def togli_tratti_corti(z, info, fondo, ppm, pal, lmin=1.5, lpota=1.0, min_rami=4, larg_max=1.2, registro=None):
    """PASSO 23b (semplifica per la misura): un digitalizzatore a 50 mm non ricama le ciglia una per una né le
    ciocche lunghe 1 mm. Linee INTERNE (non toccano lo sfondo, larghe <= larg_max mm):
      - tratto isolato più corto di lmin mm (e allungato: non un pallino) -> tolto, ci va il riempimento sotto;
      - rametti più corti di lpota mm attaccati a un incrocio, solo se la linea ne ha almeno min_rami (frangia di
        ciglia sul contorno dell'occhio) -> potati.
    Prima versione (2,5 mm, ogni rametto) rovinava le lettere della bimba («t», «N») e gli occhi chiusi.
    Solo linee SCURE (L < 50: ciglia, tratti a china): il fiocco di neve bianco, potato, diventava un buco."""
    from skimage.morphology import skeletonize
    from skimage.color import rgb2lab
    Lp = rgb2lab(np.array(pal, float)[None] / 255.0)[0][:, 0]
    vicino_fondo = ndimage.binary_dilation(fondo, iterations=2)
    lmin_px = lmin * ppm
    toccate = set()
    for o in list(info):
        m = z == o["id"]
        if not m.any() or Lp[o["colore"]] >= 50 or (m & vicino_fondo).any() or o["px"] > 40 * ppm * ppm:
            continue
        ys, xs = np.nonzero(m)
        y0, x0 = ys.min() - 1, xs.min() - 1
        sub = np.pad(m[ys.min():ys.max() + 1, xs.min():xs.max() + 1], 1)
        dt = ndimage.distance_transform_edt(sub)
        sk = skeletonize(sub)
        if not sk.any():
            continue
        larg = 2 * np.median(dt[sk])
        if larg > larg_max * ppm:
            continue
        lung = sk.sum()
        if lung < lmin_px:
            if lung >= 2.0 * larg:                           # tratto corto (non un pallino)
                tg = np.zeros_like(m)
                tg[m] = True
                _riassegna(z, tg, o["id"])
                toccate.update(np.unique(z[ndimage.binary_dilation(tg)]).tolist())
                if registro is not None:
                    registro.append({"tipo": "tolto", "y": float(ys.mean()), "x": float(xs.mean())})
            continue
        # potatura dei rametti corti
        rami = [r for r in _rami(sk) if r[1] and r[2] < lpota * ppm]
        if len(rami) < min_rami:
            continue
        sp = np.zeros_like(sk)
        for path, _, _ in rami:
            for (py, px_) in path[:-max(1, int(round(larg)))]:   # non tagliare l'incrocio
                sp[py, px_] = True
        if not sp.any():
            continue
        _, (iy, ix) = ndimage.distance_transform_edt(~sk, return_indices=True)
        togli_sub = sub & sp[iy, ix]
        tg = np.zeros_like(m)
        hh, ww = togli_sub.shape
        Y0, X0 = y0, x0
        yy, xx = np.nonzero(togli_sub)
        yy, xx = yy + Y0, xx + X0
        ok = (yy >= 0) & (xx >= 0) & (yy < m.shape[0]) & (xx < m.shape[1])
        tg[yy[ok], xx[ok]] = True
        tg &= m
        if tg.any():
            _riassegna(z, tg, o["id"])
            toccate.add(o["id"])
            if registro is not None:
                for path, _, _ in rami:
                    registro.append({"tipo": "potato", "y": float(path[0][0] + Y0), "x": float(path[0][1] + X0)})
    return z, info, toccate


def spiana_punte(z, info, ppm, r_mm=0.35, registro=None):
    """PASSO 23c (semplifica per la misura): le punte dei riempimenti più sottili di 2·r_mm (0,7 mm) non si
    ricamano bene (punti corti, satin storti): passano alla zona accanto che le circonda. Le linee (larghezza
    tipica <= 1,2 mm) e le punte verso lo sfondo restano (il contorno le copre)."""
    from skimage.morphology import disk, skeletonize
    r = max(1, int(round(r_mm * ppm)))
    toccate = set()
    for o in info:
        if o["px"] < 5 * ppm * ppm:
            continue
        m = z == o["id"]
        ys, xs = np.nonzero(m)
        y0, y1, x0, x1 = max(0, ys.min() - r - 1), ys.max() + r + 2, max(0, xs.min() - r - 1), xs.max() + r + 2
        sub = m[y0:y1, x0:x1]
        sk = skeletonize(sub)
        if sk.any() and 2 * np.median(ndimage.distance_transform_edt(sub)[sk]) <= 1.2 * ppm:
            continue                                          # è una linea
        punte = sub & ~ndimage.binary_opening(sub, structure=disk(r))
        if not punte.any():
            continue
        zs = z[y0:y1, x0:x1]
        _, (iy, ix) = ndimage.distance_transform_edt(zs == o["id"], return_indices=True)
        vicino = zs[iy, ix]                                   # zona (o sfondo) più vicina fuori da me
        tg = punte & (vicino >= 0)
        lab, n = ndimage.label(tg, structure=np.ones((3, 3)))
        for k in range(1, n + 1):
            p = lab == k
            if p.sum() < 2:
                continue
            zs[p] = vicino[p]
            toccate.add(o["id"])
            if registro is not None:
                py, px_ = np.nonzero(p)
                registro.append({"tipo": "spianato", "y": float(py.mean() + y0), "x": float(px_.mean() + x0)})
    return z, info, toccate


def maschera_zona(z, o):
    """Pixel della zona + i suoi buchi piccoli tappati (passo 6)."""
    m = z == o["id"]
    if o.get("tappo") is not None:
        y0, x0, tp = o["tappo"]
        m[y0:y0 + tp.shape[0], x0:x0 + tp.shape[1]] |= tp
    return m


def separa_per_zona(rgb, z, info, pal, ppm, soglia=3.0, min_mm2=20.0, massimo=None):
    """PASSO 8b: due colori quasi uguali in zone diverse (pelle #ffe9d7 e capelli #fdf0d8 di Elsa, ΔE ~4) finiscono
    nello stesso gruppo e nessuna soglia sui soli colori li separa senza separare anche le sfumature. Qui si
    guarda il colore MEDIO di ogni zona grande: se è lontano (CIEDE2000 > soglia) dal colore del suo gruppo,
    la zona (e le altre zone simili a lei) prende un colore suo. Le sfumature DENTRO una zona non contano."""
    from skimage.color import deltaE_ciede2000, rgb2lab
    lab = rgb2lab(ndimage.median_filter(rgb, size=(3, 3, 1)) / 255.0)
    pal = list(pal)
    labp = [rgb2lab(np.array(p, float)[None, None] / 255.0)[0, 0] for p in pal]
    medie = {}
    for o in info:
        if o["px"] / ppm ** 2 >= min_mm2:
            medie[o["id"]] = lab[z == o["id"]].mean(0)
    nuovi = []                                           # (lab, indice palette)
    famiglia = {}                                        # colore di partenza -> colori che hanno preso le sue zone grandi
    for o in info:
        famiglia.setdefault(o["colore"], {o["colore"]})
    # PASSO 15b: il colore del gruppo è una media e cade A METÀ tra due colori vicini (pelle e capelli di Elsa:
    # ΔE ~2 da entrambi, ~4 tra loro) -> nessuno dei due si separava. Il riferimento del gruppo diventa il colore
    # della sua zona più grande: le altre si confrontano con quella.
    piu_grande = {}
    for o in info:
        if o["id"] in medie and o["px"] > piu_grande.get(o["colore"], (0, None))[0]:
            piu_grande[o["colore"]] = (o["px"], o["id"])
    for c, (_, i) in piu_grande.items():
        mz = medie[i]
        if deltaE_ciede2000(mz[None], labp[c][None])[0] <= soglia * 2:   # solo se è davvero lo stesso colore
            labp[c] = mz
            from skimage.color import lab2rgb
            pal[c] = tuple(int(v) for v in (lab2rgb(mz[None, None])[0, 0] * 255).round())
    for o in sorted(info, key=lambda o: -o["px"]):
        if o["id"] not in medie:
            continue
        mz = medie[o["id"]]
        c0 = o["colore"]
        s = soglia if mz[0] >= 35 else soglia * 3          # i colori scuri (contorni) variano per l'antialiasing
        if deltaE_ciede2000(mz[None], labp[o["colore"]][None])[0] <= s:
            continue
        for lb, k in nuovi:
            if deltaE_ciede2000(mz[None], lb[None])[0] <= soglia:
                o["colore"] = k
                break
        else:
            d_ = [deltaE_ciede2000(mz[None], lb_[None])[0] for lb_ in labp] if massimo is not None else [0]
            # oltre il tetto solo i colori DAVVERO diversi (iride azzurra della puzzola, ΔE > 15 da tutti): le
            # sfumature di un disegno dipinto si fondono
            if massimo is not None and len(pal) >= massimo and min(d_) <= 15:
                # PASSO 34 (27/11/2026): disegni DIPINTI (cerbiatto dipinto: ogni zona ha una media diversa) facevano
                # 52 colori con «Colori max 12» = 82 cambi di filo. Raggiunto il tetto, la zona prende il colore
                # già in tavolozza più vicino alla sua media (spesso il suo)
                o["colore"] = int(np.argmin(d_))
                famiglia[c0].add(o["colore"])
                continue
            from skimage.color import lab2rgb
            pal.append(tuple(int(v) for v in (lab2rgb(mz[None, None])[0, 0] * 255).round()))
            labp.append(mz)
            nuovi.append((mz, len(pal) - 1))
            o["colore"] = len(pal) - 1
        famiglia[c0].add(o["colore"])
    # PASSO 15: le zone PICCOLE (sotto min_mm2) restavano sul colore di partenza anche quando le zone grandi
    # dello stesso gruppo ne avevano preso uno nuovo: nella bimba la fascia diventava panna e gli occhielli delle
    # lettere (B, o, a: stesso panna nell'immagine) restavano bianchi = due fili diversi. Ora ogni zona piccola
    # prende, nella sua famiglia, il colore più vicino al SUO colore interno (senza il bordo sfumato).
    for o in info:
        fam = famiglia.get(o["colore"])
        if o["id"] in medie or not fam or len(fam) < 2:
            continue
        m = z == o["id"]
        dentro = ndimage.binary_erosion(m)
        pix = lab[dentro] if dentro.sum() >= 4 else lab[m]
        mz = np.median(pix, 0)
        o["colore"] = min(fam, key=lambda k: deltaE_ciede2000(mz[None], labp[k][None])[0])
    return pal


def _misura(o, m, y0, y1, x0, x1, ppm):
    """Misure della forma m (riquadro y0:y1, x0:x1) e tipo di punto. -> True se è una linea."""
    from skimage.morphology import skeletonize
    ys, xs = np.nonzero(m)
    sub = np.pad(m[y0:y1, x0:x1], 2)
    dt = ndimage.distance_transform_edt(sub)
    sk = skeletonize(sub)
    larg = 2 * dt[sk] / ppm if sk.any() else np.array([2 * dt.max() / ppm])
    o["bbox_px"] = [int(x0) - 2, int(y0) - 2, int(x1) + 2, int(y1) + 2]
    o["spessore_max"] = float(2 * dt.max() / ppm)
    o["larg_tipica"] = float(np.median(larg))
    o["area_mm2"] = float(m.sum() / ppm / ppm)
    o["lung_scheletro"] = float(sk.sum() / ppm)
    c = np.stack([xs, ys], 1).astype(float)
    c -= c.mean(0)
    val, vec = np.linalg.eigh(np.cov(c.T))
    o["asse"] = float(np.degrees(np.arctan2(vec[1, 1], vec[0, 1])) % 180)
    o["allung"] = float(np.sqrt(val[1] / max(val[0], 1e-9)))
    # linea = fascia stretta e lunga (contorni, glassa, sorriso); macchia = zona piena
    lineare = o["larg_tipica"] <= LINEA_MAX and o["lung_scheletro"] >= 2.5 * o["larg_tipica"] \
        and o["spessore_max"] <= LINEA_MAX * 1.3
    if lineare:
        # una zona da cui è stata staccata una macchia (PASSO 35) resta satin: senza la macchia la larghezza tipica
        # scende e la filza perdeva la pupilla della ragazza di prova (che sta ancora in questa zona)
        o["punto"] = "filza3" if o["larg_tipica"] < FILZA_MAX and not o.get("ha_macchia") else "satin"
    elif o["spessore_max"] <= PUNTINO_MAX:
        o["punto"] = "satin_pieno"          # occhi, bottoni piccoli: satin di traverso (dati: 4-6 mm -> satin)
    else:
        o["punto"] = "fill"
    return lineare


def misura_zone(z, info, ppm):
    """Spessore (2×distanza dal bordo), larghezza tipica lungo lo scheletro, allungamento, tipo di punto."""
    buco_px = BUCO_MAX_MM2 * ppm * ppm

    def misura(o, m, y0, y1, x0, x1):
        return _misura(o, m, y0, y1, x0, x1, ppm)

    for o in info:
        m = z == o["id"]
        ys, xs = np.nonzero(m)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        o["tappo"] = None
        lineare = misura(o, m, y0, y1, x0, x1)
        # PASSO 6: buchi piccoli (riflessi negli occhi, nel naso) = la forma si ricama PIENA e quello che c'è nel
        # buco si ricama sopra (come Wilcom/Hatch). Solo per le forme piene con pochi buchi: le linee (lettere
        # con l'occhiello, contorni a rete con decine di buchi) restano come sono, se no il contorno ricamato per
        # ultimo copriva i piccoli spazi chiusi (la faccia del cagnolino) e le lettere perdevano la forma.
        # Mai i buchi di sfondo vero o le zone escluse a mano.
        if lineare:
            continue
        piena = ndimage.binary_fill_holes(m[y0:y1, x0:x1])
        buchi = piena & ~m[y0:y1, x0:x1]
        if not buchi.any():
            continue
        lb, nb = ndimage.label(buchi)
        if nb > BUCHI_MAX:
            continue
        dim = np.bincount(lb.ravel())
        zz = z[y0:y1, x0:x1]
        tappo = np.zeros_like(buchi)
        for k in range(1, nb + 1):
            if dim[k] <= buco_px and not (zz[lb == k] < 0).any():
                tappo |= lb == k
        if tappo.any():
            o["tappo"] = (int(y0), int(x0), tappo)
            m = m.copy()
            m[y0:y1, x0:x1] |= tappo
            misura(o, m, y0, y1, x0, x1)
    return info


SORMONTO_MM = 0.5          # sormonto tra colori vicini: 0,5 mm = predefinito Wilcom (Shaping > Overlap)
SORMONTO_GRANDE_MM = 0.6   # sotto un riempimento grande il riempimento di sotto entra solo di tanto
GRANDE_MM2 = 40.0          # oltre quest'area una zona di riempimento sopra un'altra «buca» quella sotto
PUNTINO_MAX = 6.0      # mm: macchie più strette di così si fanno a satin pieno
BUCO_MAX_MM2 = 15.0    # mm²: buchi più piccoli si tappano (la forma sotto è piena, il dettaglio va sopra)
BUCHI_MAX = 4          # forme con più buchi di così sono reti di contorni: non si tappano


def strati(z, info):
    """Chi sta sopra chi. Zone che toccano lo sfondo = contorno esterno (ultime). Le altre stanno «sopra» il colore
    che le circonda di più: quel colore continua SOTTO di loro (il corpo dell'omino resta un pezzo solo
    sotto la glassa). Restituisce per ogni zona: sotto_di (id zona base) e livello."""
    H, W = z.shape
    for o in info:
        m = z == o["id"]
        anello = ndimage.binary_dilation(m, iterations=2) & ~m
        vic = z[anello]
        o["tocca_sfondo"] = bool((vic == -1).mean() > 0.02)
        vic = vic[vic >= 0]
        o["vicini"] = {int(k): int(v) for k, v in zip(*np.unique(vic, return_counts=True))}
    per_id = {o["id"]: o for o in info}
    for o in info:
        o["sopra_a"] = None
        if o["tocca_sfondo"]:
            continue
        # colore più presente intorno (escluse le zone-contorno, che sono linee sopra tutto)
        cand = {}
        for k, n in o["vicini"].items():
            v = per_id[k]
            if v["colore"] == o["colore"]:
                continue
            if v["tocca_sfondo"] and v["punto"] in ("satin", "filza3"):
                continue
            cand[v["colore"]] = cand.get(v["colore"], 0) + n
        if cand:
            c = max(cand, key=cand.get)                     # colore su cui poggia
            base = sum(per_id[k]["px"] for k in o["vicini"] if per_id[k]["colore"] == c
                       and not (per_id[k]["tocca_sfondo"] and per_id[k]["punto"] in ("satin", "filza3")))
            if o["px"] < base:                              # il corpo non può stare «sopra» un fiocco più piccolo
                o["sopra_a"] = c
    # pezzi di corpo tagliati da ciò che ci sta sopra (calotta della testa sopra la glassa): stesso colore di un
    # riempimento più grande e con zone appoggiate su quel colore accanto -> è riempimento anche lui, non una linea
    fill_col = {}
    for o in info:
        if o["punto"] == "fill":
            fill_col[o["colore"]] = max(fill_col.get(o["colore"], 0), o["px"])
    for o in info:
        if o["punto"] in ("satin", "satin_pieno") and not o["tocca_sfondo"] and fill_col.get(o["colore"], 0) > o["px"]:
            if any(per_id[k]["sopra_a"] == o["colore"] for k in o["vicini"]):
                o["punto"], o["sopra_a"] = "fill", None
    return info


def oggetti(z, info, ppm):
    """PASSO 16 — ricamo a OGGETTI, come un digitalizzatore: la fascia della bimba sotto «Buon Natale» è UNA forma
    piena con le lettere sopra, non tante strisce larghe 1,3 mm ritagliate attorno alle lettere (a 50 mm
    diventavano satin spezzati e buchi). Per ogni zona su cui poggiano altre zone:
      - la forma si misura PIENA (lei + chi ci sta sopra + gli occhielli) e il tipo di punto viene da lì;
      - gli occhielli (zone del suo stesso colore chiuse dentro chi ci sta sopra: dentro di B, o, a) sono buchi che
        mostrano la base: si «assorbono» nella base, che passa sotto la lettera, invece di essere pezzetti sopra."""
    per_id = {o["id"]: o for o in info}
    for o in info:
        o.setdefault("assorbite", [])
    for o in info:
        if o.get("assorbita_in") is not None or (o["tocca_sfondo"] and o["punto"] in ("satin", "filza3")):
            continue
        figli = [per_id[k] for k in o["vicini"] if per_id[k]["sopra_a"] == o["colore"] and not per_id[k]["tocca_sfondo"]]
        if not figli:
            continue
        occh = {}
        for f in figli:
            for k in f["vicini"]:
                g = per_id[k]
                if g["id"] != o["id"] and g["colore"] == o["colore"] and g["sopra_a"] == f["colore"] \
                        and not g["tocca_sfondo"] and g.get("assorbita_in") is None and g["px"] < o["px"] \
                        and g.get("etichetta") not in SOPRA:
                    occh[g["id"]] = g
        for g in occh.values():
            g["assorbita_in"] = o["id"]
            o["assorbite"].append(g["id"])
        # PASSO 17: niente soglia sull'area dei dettagli: il viso della bimba a 50 mm, tagliato a strisce dalle
        # linee degli occhi (12 mm² su 99), era «una linea» e il satin lasciava buchi attorno agli occhi. Come fa
        # come si fa a mano: si copre tutto il viso e gli occhi si ricamano sopra
        if o["punto"] not in ("satin", "filza3"):
            continue
        m = z == o["id"]
        for k in [f["id"] for f in figli] + o["assorbite"]:
            m |= z == k
        ys, xs = np.nonzero(m)
        prova = dict(o)
        if not _misura(prova, m, ys.min(), ys.max() + 1, xs.min(), xs.max() + 1, ppm):
            for k in ("bbox_px", "spessore_max", "larg_tipica", "area_mm2", "lung_scheletro", "asse", "allung", "punto"):
                o[k] = prova[k]
            o["oggetto"] = True
    return info


def misura_consigliata(A, R, lato_px):
    """PASSO 22 — misura consigliata (come un digitalizzatore che guarda il disegno prima di accettare la misura).
    A = analisi a R mm. Ogni zona sopravvive finché la sua area, scalata (S/R)², resta sopra il limite sotto cui il
    programma la fonde (MIN_ZONA_CONTRASTO per i dettagli contrastati: occhi, pupille, bocca; MIN_ZONA per gli
    altri); ogni colore finché occupa almeno MIN_COLORE_MM2. Minimo = la misura sotto cui si perde un dettaglio
    contrastato o un colore. Massimo = risoluzione dell'immagine (almeno 6 pixel per mm).
    -> {"min", "max", "dettagli": [{"x","y","mm","tipo","hex","etichetta"}]} (mm = misura sotto cui si perde)."""
    from skimage.color import deltaE_ciede2000, rgb2lab
    z, info = A["zone"], A["info"]
    H, W = z.shape
    labp = rgb2lab(np.array(A["palette"], float)[None] / 255.0)[0]
    bianco = rgb2lab(np.ones((1, 1, 3)))[0, 0]
    per_id = {o["id"]: o for o in info}
    det, area_col = [], {}
    for o in info:
        area_col[o["colore"]] = area_col.get(o["colore"], 0.0) + o["area_mm2"]
        vic = {k: n for k, n in (o.get("vicini") or {}).items() if per_id[k]["colore"] != o["colore"]}
        ref = labp[per_id[max(vic, key=vic.get)]["colore"]] if vic else bianco
        contr = float(deltaE_ciede2000(labp[o["colore"]][None], ref[None])[0])
        dettaglio = contr > CONTRASTO_DETTAGLIO or o.get("etichetta") in SOPRA
        soglia = MIN_ZONA_CONTRASTO if dettaglio else MIN_ZONA
        if not dettaglio or o["area_mm2"] < 1.2 * soglia:      # già al limite anche a R: rumore, non un dettaglio
            continue
        x0, y0, x1, y1 = [max(0, v) for v in o["bbox_px"]]
        dt = ndimage.distance_transform_edt(z[y0:y1, x0:x1] == o["id"])
        y, x = np.unravel_index(dt.argmax(), dt.shape)
        ox, oy = a_originale(A.get("ritaglio", (0, 0, 1, 1)), (x + x0 + 0.5) / W, (y + y0 + 0.5) / H)
        det.append({"x": round(ox, 4), "y": round(oy, 4),
                    "mm": round(R * float(np.sqrt(soglia / o["area_mm2"]))), "tipo": "dettaglio",
                    "hex": A["fili"][o["colore"]]["originale"], "etichetta": o.get("etichetta")})
    for c, a in area_col.items():
        det.append({"x": None, "y": None, "mm": round(R * float(np.sqrt(MIN_COLORE_MM2 / a))), "tipo": "colore",
                    "hex": A["fili"][c]["originale"], "etichetta": None})
    det.sort(key=lambda d: -d["mm"])
    minimo = max([d["mm"] for d in det], default=20)
    minimo = int(min(R, max(20, 5 * np.ceil(minimo / 5))))
    massimo = int(max(minimo, 10 * np.floor(A.get("lato_px", lato_px) / 6 / 10)))
    return {"min": minimo, "max": massimo, "rif_mm": R, "dettagli": det}


OCCHI = {"occhio_bianco": 1, "iride": 2, "pupilla": 3, "riflesso_occhio": 4}   # ordine: dal fondo dell'occhio in su
# passo 32: anche bocca, denti, guance, sopracciglia etichettati stanno SOPRA la pelle e non spariscono dentro
# di lei (denti sopra la bocca); come gli occhi: mai assorbiti, tenuti anche piccoli
SOPRA = dict(OCCHI, bocca=1, guancia=1, sopracciglio=1, denti=2)
PELLE = ("pelle_viso", "pelle_corpo")


def applica_semantica(rgb, z, info, pal, semantica):
    """PASSO 20 — etichette dell'«Aiuto AI» (o corrette a mano): [{"x", "y", "etichetta"}] in frazioni dell'immagine,
    un punto dentro ogni zona (sopravvive ai ricalcoli, come le zone escluse). Effetti:
      - capelli e pelle dello stesso colore -> i capelli prendono un filo loro (il loro colore mediano);
      - occhi (bianco, iride, pupilla, riflesso) mai assorbiti e ricamati uno sopra l'altro in quest'ordine.
    «sfondo_da_non_ricamare» qui non fa niente: è solo una proposta di esclusione che conferma l'utente."""
    H, W = z.shape
    per_id = {o["id"]: o for o in info}
    for s in semantica or []:
        r, c = int(min(max(s["y"], 0), 0.9999) * H), int(min(max(s["x"], 0), 0.9999) * W)
        if z[r, c] >= 0:
            per_id[int(z[r, c])]["etichetta"] = s["etichetta"]
    pal = list(pal)
    for c in sorted({o["colore"] for o in info}):
        cap = [o for o in info if o["colore"] == c and o.get("etichetta") == "capelli"]
        if not cap or not any(o["colore"] == c and o.get("etichetta") in PELLE for o in info):
            continue
        m = np.zeros(z.shape, bool)
        for o in cap:
            m |= z == o["id"]
        d = ndimage.binary_erosion(m, iterations=2)
        pal.append(tuple(int(v) for v in np.median(rgb[d if d.sum() >= 20 else m], 0)))
        for o in cap:
            o["colore"] = len(pal) - 1
    return pal


def analizza(percorso, altezza_mm=100, max_colori=12, forzati=None, marca=None, esclusi=None, semantica=None,
             semplifica=False):
    """forzati = {colore dell'immagine "#rrggbb": numero del filo} scelti a mano nella pagina."""
    rgb, ppm, linee, (rit, lato_px) = carica_con_linee(percorso, altezza_mm)
    # zone escluse ed etichette arrivano in frazioni dell'immagine ORIGINALE: si portano sul disegno ritagliato
    esclusi = [p for p in (a_ritaglio(rit, fx, fy) for fx, fy in (esclusi or [])) if 0 <= p[0] < 1 and 0 <= p[1] < 1]
    if semantica:
        semantica = [dict(s, x=p[0], y=p[1]) for s in semantica for p in [a_ritaglio(rit, s["x"], s["y"])]
                     if 0 <= p[0] < 1 and 0 <= p[1] < 1]
    fondo = sfondo(rgb)
    # PASSO 23d: sotto la misura minima un colore deve valere di più per meritare un cambio di filo (8 mm² invece di 4)
    et, pal = colori(rgb, fondo, max_k=max_colori, ppm=ppm, min_mm2=MIN_COLORE_MM2 * (2 if semplifica else 1))
    et = pulisci(et, ppm)
    et[fondo] = -1
    # PASSO 19b: i filtri dei colori (mediano 5x5, maggioranza) spezzavano le linee ridisegnate larghe 3 px in
    # 20 pezzetti, poi assorbiti dai capelli (sparito il contorno del viso a 50 mm): si riscrivono dopo i filtri,
    # col colore scuro della palette più vicino
    if linee is not None and linee.any():
        from skimage.color import rgb2lab
        labp = rgb2lab(np.array(pal, float)[None] / 255.0)[0]
        cl = rgb2lab(np.median(rgb[linee], 0)[None, None] / 255.0)[0, 0]
        k = int(np.argmin(((labp - cl) ** 2).sum(1)))
        if labp[k][0] < 50:
            et[linee] = k
            fondo = fondo & ~linee
    # mappa delle zone cliccabili nella pagina (prima delle esclusioni, così anche quelle escluse restano
    # riconoscibili): 0 = sfondo, poi un numero per ogni pezzo di colore
    regioni = np.zeros(et.shape, np.int32)
    base = 0
    for c in sorted(set(np.unique(et)) - {-1}):
        lab_r, n_r = ndimage.label(et == c, structure=np.ones((3, 3)))
        regioni[lab_r > 0] = lab_r[lab_r > 0] + base
        base += n_r
    # zone escluse a mano nella pagina (clic sull'originale, coordinate 0..1): il pezzo di colore cliccato diventa
    # sfondo, come il bianco chiuso tra i capelli e il collo. Il contorno vicino diventa «esterno».
    for fx, fy in (esclusi or []):
        r, c = int(min(max(fy, 0), 0.9999) * et.shape[0]), int(min(max(fx, 0), 0.9999) * et.shape[1])
        if et[r, c] >= 0:
            lab_e, _ = ndimage.label(et == et[r, c], structure=np.ones((3, 3)))
            m_e = lab_e == lab_e[r, c]
            et[m_e] = -1
            fondo = fondo | m_e
    registro = []                                   # PASSO 23: cosa ha cambiato la semplificazione (per la pagina)
    z, info = zone(et, ppm, pal, ingrandisci=semplifica, registro=registro)
    z, info = dividi_zone(rgb, z, info, ppm)
    if linee is not None:
        z, info = separa_linee_esterne(z, info, fondo, linee, ppm)
    pal = separa_per_zona(rgb, z, info, pal, ppm, massimo=max_colori + 2)   # tetto: pochi colori in più del chiesto
    z, info = separa_macchie(z, info, ppm, pal)         # dopo i colori: la macchia tiene quello della sua zona
    if semantica:
        pal = applica_semantica(rgb, z, info, pal, semantica)
    if semplifica:
        z, info, toccate = togli_tratti_corti(z, info, fondo, ppm, pal, registro=registro)
        z, info, t2 = spiana_punte(z, info, ppm, registro=registro)
        z, info = _rinumera(z, info, ppm, (toccate | t2) - {-1})
    info = misura_zone(z, info, ppm)
    info = strati(z, info)
    info = oggetti(z, info, ppm)
    # PASSO 16b: un riempimento non «poggia» su una linea: il cuscinetto rosa chiuso dall'anello marrone poggia su
    # quello su cui poggia l'anello (il panna della zampa); la linea va sopra a tutti e due (vedi _livelli)
    per_id = {o["id"]: o for o in info}
    col_linea = {}
    for o in info:
        if o["punto"] in ("satin", "filza3") and not o["tocca_sfondo"]:
            col_linea.setdefault(o["colore"], []).append(o)
    for o in info:
        if o["punto"] not in ("fill", "satin_pieno") or o["tocca_sfondo"] or not o["vicini"]:
            continue
        if o.get("sopra_a") in col_linea:
            linee = [per_id[k] for k in o["vicini"] if per_id[k]["colore"] == o["sopra_a"]
                     and per_id[k]["punto"] in ("satin", "filza3")]
        elif o.get("sopra_a") is None:
            # cuscinetto PIÙ GRANDE del suo anello: strati() non lo mette sopra a niente. Se è chiuso da linee
            # (la linea con più contatto è un anello interno) poggia comunque su quello su cui poggia l'anello
            k = max(o["vicini"], key=o["vicini"].get)
            linee = [per_id[k]] if per_id[k]["punto"] in ("satin", "filza3") and not per_id[k]["tocca_sfondo"] else []
        else:
            linee = []
        if linee:
            l = max(linee, key=lambda v: v["px"])
            o["sopra_a"] = l["sopra_a"] if l["sopra_a"] != o["colore"] else None
    # PASSO 33 (27/11/2026): una «linea» satin larga (≥ 2,5 mm) che non tocca il fondo è una FORMA chiusa dal
    # contorno (corno della renna a 7 cm: spessore 6,47 mm contro la soglia 6,5 -> satin, l'altro corno 6,54 ->
    # riempimento). Da satin non entrava sotto il contorno e alla base, dove le linee si incrociano, restava
    # tela scoperta: diventa riempimento come il corno gemello
    for o in info:
        if o["punto"] == "satin" and not o["tocca_sfondo"] and o.get("larg_tipica", 0) >= 2.5:
            o["punto"] = "fill"
    # PASSO 3: il contorno esterno non è mai una filza. I disegni veri da 50 mm del catalogo hanno satin larghi
    # almeno ~1 mm come quelli da 100 mm (p5 0,97 mm): la linea resta satin (portato a SATIN_MIN), se no la metà
    # esterna della linea resta tela scoperta. Le linee interne sottili restano filza: il riempimento va sotto.
    for o in info:
        if o["punto"] == "filza3" and o["tocca_sfondo"]:
            o["punto"] = "satin"
    # PASSO 9: un dettaglio sottile PIÙ CHIARO del fondo su cui sta (fiocco di neve bianco sull'abito azzurro,
    # motivi bianchi) come filza sparisce: diventa satin largo almeno SATIN_MIN. Le linee sottili scure restano filza.
    from skimage.color import rgb2lab
    Lp = [float(rgb2lab(np.array(p, float)[None, None] / 255.0)[0, 0, 0]) for p in pal]
    for o in info:
        if o["punto"] == "filza3" and o.get("sopra_a") is not None and Lp[o["colore"]] > Lp[o["sopra_a"]] + 12 \
                and o.get("larg_tipica", 0) >= 0.9 * SATIN_MIN:
            o["punto"] = "satin"
    # PASSO 31 (27/11/2026): una linea MOLTO più sottile del satin minimo (bracci dei cristalli di ghiaccio della
    # ragazza di prova: 0,4-0,6 mm a 7 cm) portata a satin da 1 mm + compensazione si gonfiava e i bracci, distanti meno di
    # 1 mm, si fondevano in una macchia. Diventa filza tripla (larga più o meno come la linea vera). Resta satin
    # solo se fa da bordo a un riempimento di un altro colore (lì il riempimento finisce sotto la linea)
    per_id_ = {o["id"]: o for o in info}
    for o in info:
        if o["punto"] not in ("satin", "filza3") or o.get("larg_tipica", 9) >= 0.9 * SATIN_MIN or o.get("ha_macchia"):
            continue
        # fino a 0,9 mm solo le forme RAMIFICATE (cristalli: tanti bracci); fasce e onde colorate (bordino rosso
        # del tetto della casetta, glassa dell'omino) solo sotto 0,7 mm, se no da fascia diventano un filo
        if o.get("larg_tipica", 9) >= 0.7 * SATIN_MIN and o["punto"] == "satin":
            rami_, _ = _percorsi_scheletro(maschera_zona(z, o))
            if sum(1 for r_ in rami_ if len(r_) >= 3) < 5:
                continue
        vic = o.get("vicini") or {}
        tot = sum(vic.values()) or 1
        # (contatti con TUTTE le zone piene sommati: il contorno della ragazza di prova tocca capelli, viso, vestito...)
        bordo_riemp = o["tocca_sfondo"] and sum(
            n_ for k, n_ in vic.items() if k in per_id_ and per_id_[k]["punto"] != "filza3"   # anche ciocche satin
            and per_id_[k]["colore"] != o["colore"]
            and per_id_[k]["px"] / ppm ** 2 >= 15.0) > 0.3 * tot   # non i pezzettini (1-2 mm²) del cristallo
        if not bordo_riemp:
            o["punto"] = "filza3"
            # riga sottile ma «piena» (lettere di una scritta piccola): ripassata 5 volte, resta marcata
            if o.get("larg_tipica", 0) >= 0.55:
                o["passate"] = 5
    import fili as F
    from skimage.color import deltaE_ciede2000
    hexes = ["#%02x%02x%02x" % tuple(c) for c in pal]
    # marca=None: tutte le marche. Conta avvicinarsi al colore dell'immagine, non avere il filo nel kit
    fl, labf = F.tabella(marca) if marca else F.tabella_tutte()
    per_num = {x["num"]: k for k, x in enumerate(fl)}
    filo = []
    for h in hexes:
        c = F.lab_hex([h])[0]
        # CIEDE2000 (ΔE76 sbagliava sui marroni scuri). Con 20.000 fili di 75 marche il più vicino è quasi
        # identico; con un kit solo (Brothread 80) un pesca chiaro diventava giallino o nocciola.
        d = deltaE_ciede2000(np.repeat(c[None], len(labf), 0), labf)
        ordine = np.argsort(d)
        k = int(ordine[0])
        scelto = False
        if forzati and forzati.get(h) in per_num:
            k, scelto = per_num[forzati[h]], True
        filo.append(dict(fl[k], originale=h, de=round(float(d[k]), 1), a_mano=scelto,
                         alternative=[dict(fl[int(j)], de=round(float(d[j]), 1)) for j in ordine[:10]]))
    # due colori dell'immagine sullo stesso filo (due marroni scuri nella casetta) = un colore solo: niente
    # cambi di filo inutili; le zone si uniscono come se fossero dello stesso colore fin dall'inizio
    primo = {}
    canon = [primo.setdefault(f["num"], k) for k, f in enumerate(filo)]
    if canon != list(range(len(filo))):
        for o in info:
            o["colore"] = canon[o["colore"]]
            if o.get("sopra_a") is not None:
                o["sopra_a"] = canon[o["sopra_a"]]
                if o["sopra_a"] == o["colore"]:
                    o["sopra_a"] = None
    H, W = z.shape
    semplificato = []
    for r in registro:
        ox, oy = a_originale(rit, (r["x"] + 0.5) / W, (r["y"] + 0.5) / H)
        semplificato.append({"tipo": r["tipo"], "x": round(ox, 4), "y": round(oy, 4)})
    # mappa delle zone cliccabili nella cornice dell'immagine ORIGINALE (la pagina la sovrappone all'originale)
    Hf, Wf = max(H, int(round(H / rit[3]))), max(W, int(round(W / rit[2])))
    piena = np.zeros((Hf, Wf), np.int32)
    oy, ox = min(Hf - H, int(round(rit[1] * Hf))), min(Wf - W, int(round(rit[0] * Wf)))
    piena[oy:oy + H, ox:ox + W] = regioni
    return {"rgb": rgb, "ppm": ppm, "fondo": fondo, "etichette": et, "zone": z, "info": info, "palette": pal, "fili": filo,
            "regioni": piena, "semplificato": semplificato, "ritaglio": rit, "lato_px": lato_px}


# ---------------------------------------------------------------- 4-5: punti
def maschera_poligono(m, ppm):
    """Maschera (pixel) -> poligono shapely in mm, con bordi LISCI (contorni a mezzo livello di una maschera
    sfumata). Prima erano righe di pixel unite: i bordi a scalini spezzavano il tatami in decine di pezzetti
    (ognuno un taglio del filo)."""
    from functools import reduce
    from shapely.geometry import Polygon
    from skimage.measure import find_contours
    pad = 3
    g = ndimage.gaussian_filter(np.pad(m, pad).astype(np.float32), sigma=max(0.8, 0.1 * ppm))
    poli = []
    for c in find_contours(g, 0.5):
        if len(c) < 4:
            continue
        P = Polygon((c[:, ::-1] - pad) / ppm).buffer(0)
        if P.area > 0.05:
            poli.append(P)
    if not poli:
        return Polygon()
    out = reduce(lambda a, b: a.symmetric_difference(b), poli)   # pari/dispari: i contorni dentro sono buchi
    return out.simplify(0.05).buffer(0)


def _livelli(info):
    """Ordine di ricamo: chi sta sotto prima. livello = 1 + livello della zona su cui poggia; contorni esterni ultimi."""
    per_id = {o["id"]: o for o in info}
    liv = {}

    def calcola(o, visti=()):
        if o["id"] in liv:
            return liv[o["id"]]
        if o["sopra_a"] is None or o["id"] in visti:
            liv[o["id"]] = 0
            return 0
        # chi poggia su di me non può stare sotto di me (lettera <-> occhiello dello stesso colore della fascia:
        # il ciclo dava livelli a caso e la fascia finiva ricamata sopra le lettere)
        sotto = [per_id[k] for k in o["vicini"] if per_id[k]["colore"] == o["sopra_a"]
                 and per_id[k]["sopra_a"] != o["colore"]]
        l = 1 + max((calcola(s, visti + (o["id"],)) for s in sotto), default=0)
        liv[o["id"]] = l
        return l
    for o in info:
        calcola(o)
    # PASSO 20: occhi etichettati: bianco < iride < pupilla < riflesso, tutti sopra i riempimenti su cui stanno
    for _ in range(4):
        for o in info:
            r = SOPRA.get(o.get("etichetta"))
            if not r:
                continue
            sotto_me = [liv[k] for k in o["vicini"] if SOPRA.get(per_id[k].get("etichetta"), 0) < r
                        and per_id[k]["punto"] in ("fill", "satin_pieno") and not per_id[k]["tocca_sfondo"]]
            if sotto_me and liv[o["id"]] <= max(sotto_me):
                liv[o["id"]] = max(sotto_me) + 1
    # PASSO 16b: una linea interna (anello attorno ai cuscinetti dell'impronta, contorno dell'occhio) si ricama
    # DOPO i riempimenti che tocca: così il riempimento passa sotto la linea e non resta la base a vista in mezzo
    for _ in range(3):
        for o in info:
            if o["punto"] in ("satin", "filza3") and not o["tocca_sfondo"]:
                pieni = [liv[k] for k in o["vicini"] if per_id[k]["punto"] in ("fill", "satin_pieno")
                         and per_id[k].get("assorbita_in") is None]
                if pieni and liv[o["id"]] <= max(pieni):
                    liv[o["id"]] = max(pieni) + 1
    # PASSO 35b: le zone piccole attaccate a una macchia separata dal contorno (il riflesso bianco sulla pupilla,
    # che tocca anche l'iride) si ricamano DOPO: il riflesso ha lo stesso bianco del bulbo e i livelli lo mettevano
    # sotto la pupilla
    for o in info:
        if not o.get("macchia"):
            continue
        for q in info:
            tot = sum(v for k, v in q["vicini"].items())
            if (q["px"] < 0.125 * o["px"] and q["punto"] == "satin_pieno" and q["colore"] != o["colore"] and tot
                    and q["vicini"].get(o["id"], 0) >= 0.25 * tot and liv[q["id"]] <= liv[o["id"]]):
                liv[q["id"]] = liv[o["id"]] + 1
    top = max(liv.values(), default=0) + 1
    for o in info:
        o["livello"] = top if (o["tocca_sfondo"] and o["punto"] in ("satin", "filza3")) else liv[o["id"]]
    return info


def unita_di_ricamo(A):
    """Pezzi da ricamare: i riempimenti si allargano SOTTO le zone che poggiano su di loro (ricorsivo) e sotto il
    contorno esterno per metà della sua larghezza (niente tela scoperta)."""
    z, info, ppm = A["zone"], A["info"], A["ppm"]
    per_id = {o["id"]: o for o in info}
    figli = {o["id"]: [] for o in info}
    for o in info:
        if o["sopra_a"] is None:
            continue
        for k in o["vicini"]:
            if per_id[k]["colore"] == o["sopra_a"] and per_id[k]["punto"] in ("fill", "satin_pieno"):
                figli[k].append(o["id"])

    sorm = max(1, int(round(SORMONTO_MM * ppm)))
    sorm_grande = max(1, int(round(SORMONTO_GRANDE_MM * ppm)))

    def sotto(o, visti):
        """Maschera del riempimento o: continua sotto i dettagli che gli stanno sopra (occhi, bocca, linee,
        piccole zone: niente buchi), ma sotto un riempimento GRANDE entra solo per il sormonto (regola
        «remove overlaps»: due riempimenti pieni uno sull'altro = ricamo rigido, aghi rotti)."""
        m = maschera_zona(z, o)
        for a in o.get("assorbite", ()):                 # occhielli delle lettere: la base passa sotto (passo 16)
            m |= maschera_zona(z, per_id[a])
        grandi = []
        for f in figli[o["id"]]:
            if f not in visti:
                visti.add(f)
                mf = sotto(per_id[f], visti)
                if per_id[f]["punto"] == "fill" and per_id[f]["px"] / ppm ** 2 > GRANDE_MM2:
                    grandi.append(mf)
                else:
                    m |= mf
        if grandi:
            bordo = ndimage.binary_dilation(m, iterations=sorm_grande)
            for mf in grandi:
                m |= bordo & mf
        return m


    # PASSO 32 (27/11/2026): dove più linee si incontrano (base del corno sulla testa della renna a 7 cm) la
    # linea fa una macchia più larga della sua larghezza tipica: il satin copre solo attorno al centro e il
    # riempimento entrava sotto solo per mezza larghezza tipica -> tela scoperta. Ogni pixel di linea va al
    # riempimento più vicino (come nel punto croce): sotto le macchie degli incroci c'è sempre il colore giusto
    # (solo le linee del CONTORNO principale, il colore di linea più usato: una fascia colorata come il bordino
    # rosso del tetto della casetta deve restare coperta solo dal suo satin, se no la neve le entrava sotto)
    area_col = {}
    for p in info:
        if p["punto"] in ("satin", "filza3"):
            area_col[p["colore"]] = area_col.get(p["colore"], 0) + p["px"]
    col_cont = max(area_col, key=area_col.get) if area_col else None
    ids_linea = [p["id"] for p in info if p["punto"] in ("satin", "filza3") and p["colore"] == col_cont]
    px_linea = np.isin(z, ids_linea)
    if px_linea.any():
        _, (iy_l, ix_l) = ndimage.distance_transform_edt(px_linea, return_indices=True)
    unita = []
    for o in info:
        if o.get("assorbita_in") is not None and per_id[o["assorbita_in"]]["punto"] in ("fill", "satin_pieno"):
            continue                                     # è dentro la base (passo 16)
        if o["punto"] in ("fill", "satin_pieno"):
            m = sotto(o, {o["id"]})
            # PASSO 1: il riempimento va sotto TUTTE le linee che lo toccano e vengono ricamate dopo (contorno
            # esterno, dita delle zampe, croce della finestra, venature, ciocche) per mezza larghezza della linea
            # + 1 px: come Wilcom, le zone chiuse da un contorno sono una regione sola e la linea è un dettaglio
            # sopra. Prima solo il contorno esterno: lungo le linee interne restavano fessure di tela.
            bordo = ndimage.binary_dilation(m, iterations=2) & ~m
            sotto_linee = set()
            for L in np.unique(z[bordo]):
                if L < 0 or L not in per_id:
                    continue
                pl = per_id[L]
                if pl["punto"] not in ("satin", "filza3") or pl["livello"] <= o["livello"]:
                    continue
                ys, xs = np.nonzero(z == L)
                hw = max(1, int(round(pl["larg_tipica"] / 2 * ppm)) + 1)
                y0, y1 = max(0, ys.min() - hw - 2), ys.max() + hw + 3
                x0, x1 = max(0, xs.min() - hw - 2), xs.max() + hw + 3
                sub = ndimage.binary_dilation(m[y0:y1, x0:x1], iterations=hw) & (z[y0:y1, x0:x1] == L)
                # + i pixel della linea più vicini a questo riempimento che a ogni altra cosa (passo 32)
                if pl["colore"] == col_cont:
                    sub |= (z[y0:y1, x0:x1] == L) & m[iy_l[y0:y1, x0:x1], ix_l[y0:y1, x0:x1]]
                m[y0:y1, x0:x1] |= sub
                sotto_linee.add(int(L))
            # sormonto verso i colori vicini ricamati dopo o insieme (non lo sfondo): niente fessure di tela
            vicini_dopo = [p["id"] for p in info if p["id"] != o["id"] and p["livello"] >= o["livello"]]
            prima = m.copy()
            m |= ndimage.binary_dilation(m, iterations=sorm) & np.isin(z, vicini_dopo)
            # PASSO 16d: dentro una linea sottile (anello da 1 mm a 50 mm) mezza linea + sormonto arrivavano
            # all'ALTRO bordo e il rosa del cuscinetto spuntava fuori dall'anello: il riempimento si ferma a
            # 0,25 mm dal bordo opposto della linea
            marg = max(1, int(round(0.25 * ppm)))
            for L in sotto_linee:
                ys, xs = np.nonzero(z == L)
                y0, y1 = max(0, ys.min() - marg - 2), ys.max() + marg + 3
                x0, x1 = max(0, xs.min() - marg - 2), xs.max() + marg + 3
                zl = z[y0:y1, x0:x1]
                fuori = (zl != L) & ~prima[y0:y1, x0:x1] & ~(zl == o["id"])
                m[y0:y1, x0:x1] &= ~(ndimage.binary_dilation(fuori, iterations=marg) & (zl == L))
            unita.append({"zona": o, "maschera": m})
        else:
            unita.append({"zona": o, "maschera": maschera_zona(z, o)})
    # riempimenti dello stesso colore che si toccano (pezzi del corpo separati dalla glassa) diventano uno solo
    fuse, fatto = [], set()
    for k, u in enumerate(unita):
        if k in fatto:
            continue
        if u["zona"]["punto"] == "fill":
            for j in range(k + 1, len(unita)):
                v = unita[j]
                # stesso colore E stesso livello: la testa della renna (sopra il muso) unita alle corna (sotto)
                # finiva ricamata prima del muso, che la copriva tutta
                if j not in fatto and v["zona"]["punto"] == "fill" and v["zona"]["colore"] == u["zona"]["colore"] \
                        and v["zona"]["livello"] == u["zona"]["livello"] \
                        and (u["maschera"] & ndimage.binary_dilation(v["maschera"], iterations=4)).any():
                    u = {"zona": u["zona"] if u["zona"]["livello"] <= v["zona"]["livello"] else v["zona"],
                         "maschera": u["maschera"] | v["maschera"]}
                    fatto.add(j)
        fuse.append(u)
    fuse.sort(key=lambda u: (u["zona"]["livello"], u["zona"]["colore"], -u["maschera"].sum()))
    return _ordina(fuse)


def _ordina(unita):
    """Ordine di ricamo con meno cambi di filo: un pezzo aspetta solo i pezzi di livello più basso che tocca
    davvero (gli stanno sotto); tra quelli pronti sceglie prima il filo già montato. Le corna della renna
    (sotto, marrone) non toccano il muso: si ricamano insieme alla testa (sopra, stesso marrone)."""
    n = len(unita)
    box = []
    for u in unita:
        ys, xs = np.nonzero(u["maschera"])
        box.append((ys.min(), ys.max(), xs.min(), xs.max()) if len(ys) else (0, -1, 0, -1))
    dip = [set() for _ in range(n)]
    for b in range(n):
        for a in range(n):
            if unita[a]["zona"]["livello"] >= unita[b]["zona"]["livello"]:
                continue
            A_, B_ = box[a], box[b]
            y0, y1, x0, x1 = max(A_[0], B_[0]) - 3, min(A_[1], B_[1]) + 3, max(A_[2], B_[2]) - 3, min(A_[3], B_[3]) + 3
            if y0 > y1 or x0 > x1:
                continue
            y0, x0 = max(0, y0), max(0, x0)
            ma = unita[a]["maschera"][y0:y1 + 1, x0:x1 + 1]
            mb = ndimage.binary_dilation(unita[b]["maschera"][y0:y1 + 1, x0:x1 + 1], iterations=3)
            if (ma & mb).any():
                dip[b].add(a)
    fatti, out, col, out_k = set(), [], None, []
    while len(out) < n:
        pronti = [k for k in range(n) if k not in fatti and dip[k] <= fatti]
        if not pronti:                                   # non dovrebbe succedere (livelli = ordine parziale)
            pronti = [k for k in range(n) if k not in fatti]
        stesso = [k for k in pronti if unita[k]["zona"]["colore"] == col]
        if stesso:
            # PASSO 12: tra i pezzi pronti dello stesso filo, il più VICINO all'ultimo fatto (distanza tra i
            # riquadri): l'ago passa da un pezzo a quello accanto invece di attraversare il disegno (meno tagli)
            def gap(q):
                A_, B_ = box[out_k[-1]], box[q]
                dy = max(0, max(A_[0], B_[0]) - min(A_[1], B_[1]))
                dx = max(0, max(A_[2], B_[2]) - min(A_[3], B_[3]))
                return dx * dx + dy * dy
            k = min(stesso, key=gap) if out_k else stesso[0]
        else:                                            # nuovo filo: quello con più pezzi pronti, poi livello basso
            conta = {}
            for q in pronti:
                conta[unita[q]["zona"]["colore"]] = conta.get(unita[q]["zona"]["colore"], 0) + 1
            k = min(pronti, key=lambda q: (unita[q]["zona"]["livello"], -conta[unita[q]["zona"]["colore"]], q))
        fatti.add(k)
        out_k.append(k)
        out.append(unita[k])
        col = unita[k]["zona"]["colore"]
    return out


def _percorsi_scheletro(m):
    """Scheletro della linea -> lista di percorsi [(riga, col), …] da un incrocio/estremo al successivo."""
    from skimage.morphology import skeletonize
    sk = skeletonize(m)
    pts = set(zip(*np.nonzero(sk)))
    D = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    def vic(p):
        return [(p[0] + a, p[1] + b) for a, b in D if (p[0] + a, p[1] + b) in pts]
    grado = {p: len(vic(p)) for p in pts}
    nodi = {p for p in pts if grado[p] != 2}
    usati, out = set(), []
    for n in nodi:
        for v in vic(n):
            if (n, v) in usati:
                continue
            path, prev, cur = [n, v], n, v
            usati |= {(n, v), (v, n)}
            while cur not in nodi:
                nx = [w for w in vic(cur) if w != prev and (cur, w) not in usati]
                if not nx:
                    break
                w = nx[0]
                usati |= {(cur, w), (w, cur)}
                path.append(w)
                prev, cur = cur, w
            out.append(path)
    resto = pts - {p for q in out for p in q}
    while resto:                                         # anelli chiusi senza incroci (cerchio intorno al bottone)
        s = next(iter(resto))
        path, prev, cur = [s], None, s
        while True:
            nx = [w for w in vic(cur) if w != prev and w in resto and w not in path[-3:]]
            if not nx:
                break
            prev, cur = cur, nx[0]
            if cur == s:
                break
            path.append(cur)
        resto -= set(path)
        if len(path) > 3:
            out.append(path + [path[0]])
    return out, grado


def _ricampiona(P, passo):
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(P, axis=0).T))]
    if d[-1] < 1e-6:
        return P[:1], np.array([0.0])
    t = np.arange(0, d[-1] + 1e-9, passo)
    return np.stack([np.interp(t, d, P[:, 0]), np.interp(t, d, P[:, 1])], 1), t


def _prolunga(P, W, fine, m, ppm, oltre):
    """Allunga il percorso da un estremo fino al bordo vero del colore. Lo scheletro finisce circa mezza larghezza
    prima (e sulle estremità piatte si biforca): senza questo la glassa e la tesa del cappello restano corte.
    La larghezza di ogni punto aggiunto è la corda del colore misurata di traverso (estremità piatte = piene,
    estremità tonde = si stringono). Se oltre il bordo c'è un'altra zona (es. il contorno, ricamato dopo) va sotto 0,4 mm."""
    H, Wd = m.shape

    def dentro(q, mask):
        r, c = int(round(q[1] * ppm)), int(round(q[0] * ppm))
        return 0 <= r < H and 0 <= c < Wd and mask[r, c]
    dietro = min(len(P) - 1, max(3, int(round(1.0 * ppm))))     # direzione presa su ~1 mm di percorso
    p0, p1 = (P[0], P[dietro]) if fine == 0 else (P[-1], P[-1 - dietro])
    d = (p0 - p1) / max(np.hypot(*(p0 - p1)), 1e-9)
    nr = np.array([-d[1], d[0]])
    w_end = W[0] if fine == 0 else W[-1]
    passo = 0.5 / ppm
    nuovi, larg = [], []
    q, fuori = p0.copy(), 0.0
    for _ in range(int((w_end * 0.8 + 0.6) / passo)):
        q = q + d * passo
        if dentro(q, m):
            a = b = 0.0                                          # corda di traverso dentro il colore
            while a < 2 * w_end and dentro(q + nr * (a + passo), m):
                a += passo
            while b < 2 * w_end and dentro(q - nr * (b + passo), m):
                b += passo
            if a + b < 0.3:
                break
            nuovi.append(q + nr * (a - b) / 2)
            larg.append(min(a + b, w_end * 1.15))
        elif oltre is not None and dentro(q, oltre) and fuori < 0.4 and larg:
            fuori += passo                                       # sotto il contorno vicino, stessa larghezza
            nuovi.append(q.copy())
            larg.append(larg[-1])
        else:
            break
    if not nuovi:
        return P, W
    N, L = np.array(nuovi), np.array(larg)
    if fine == 0:
        return np.vstack([N[::-1], P]), np.r_[L[::-1], W]
    return np.vstack([P, N]), np.r_[W, L]


def _anello(C):
    """Contorno chiuso (N,2) in mm -> (punti, distanze cumulate, lunghezza) per campionarlo a una distanza qualsiasi."""
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(C, axis=0).T))]
    return C, d, d[-1]


def _sul_anello(A, s):
    C, d, L = A
    s = np.mod(s, L)
    return np.stack([np.interp(s, d, C[:, 0]), np.interp(s, d, C[:, 1])], -1)


def passo_auto(passo, larg):
    """PASSO 11a «auto spacing» (Wilcom): nelle colonne strette troppi buchi rovinano il tessuto; sotto 2 mm la
    distanza tra i punti sullo stesso lato cresce fino a +30% a 1 mm."""
    if larg >= 2.0:
        return passo
    return passo * (1 + 0.3 * min(1.0, (2.0 - max(larg, 1.0)) / 1.0))


def _e_anello(tenuti, ppm):
    """Vero anello (bordo del bottone): lo scheletro è un giro chiuso + al più rametti corti. Le lettere attaccate
    con un occhiello («uon» di Buon Natale) hanno un buco ma NON sono anelli: a rotaie facevano una raggiera."""
    if not tenuti:
        return False
    lun = [np.hypot(*np.diff(x[1], axis=0).T).sum() / ppm for x in tenuti]
    j = int(np.argmax(lun))
    return tenuti[j][0][0] == tenuti[j][0][-1] and all(l < min(4.0, 0.2 * lun[j]) for k, l in enumerate(lun) if k != j)


def satin_rotaie(m, ppm, tenuti, passo=0.42, comp=0.25):
    """Satin «a rotaie» come nei programmi professionali: i due bordi del colore sono le rotaie e ogni punto va da
    una all'altra. Copre esattamente la forma, punte comprese. Vale per
      - una striscia semplice (bocca, glassa, tesa): un solo ramo di scheletro, nessun buco; il bordo si divide
        nelle due rotaie nei due punti dove il ramo, prolungato, esce dalla forma;
      - un anello (cerchio intorno al bottone): rotaia esterna e rotaia del buco.
    Le forme con incroci (contorno con rami) -> None: ci pensa satin_linea."""
    from shapely.geometry import LineString, Point, Polygon
    from skimage.measure import find_contours
    pad = 3
    g = ndimage.gaussian_filter(np.pad(m, pad).astype(np.float32), sigma=max(0.8, 0.12 * ppm))
    anelli = []
    for c in find_contours(g, 0.5):
        if len(c) < 8 or np.hypot(*(c[0] - c[-1])) > 1.5:
            continue
        C = (c[:, ::-1] - pad) / ppm                     # (x, y) in mm
        P = Polygon(C)
        if P.is_valid and P.area > 0.3:
            anelli.append((P.area, C, P))
    if not anelli:
        return None
    anelli.sort(key=lambda a: -a[0])
    _, Ce, Pe = anelli[0]
    buchi = [a for a in anelli[1:] if Pe.contains(a[2].representative_point()) and a[0] > 0.5]
    E = _anello(Ce)
    # striscia semplice: un ramo lungo e al massimo rametti corti (dove la linea tocca un'altra in diagonale)
    principale = None
    if not buchi and tenuti:
        lun = [np.hypot(*np.diff(x[1], axis=0).T).sum() / ppm for x in tenuti]
        j = int(np.argmax(lun))
        if tenuti[j][0][0] != tenuti[j][0][-1] and all(l < min(4.0, 0.2 * lun[j]) for k, l in enumerate(lun) if k != j):
            principale = tenuti[j]
    if principale is not None:
        r = principale[1][:, ::-1] / ppm
        anello = LineString(Ce)
        cap = []
        for fine in (0, 1):
            dietro = min(len(r) - 1, max(2, int(round(1.5 * ppm))))
            p0, p1 = (r[0], r[dietro]) if fine == 0 else (r[-1], r[-1 - dietro])
            d = (p0 - p1) / max(np.hypot(*(p0 - p1)), 1e-9)
            raggio = LineString([p0, p0 + d * 50])
            x = raggio.intersection(anello)
            pts = [x] if x.geom_type == "Point" else list(getattr(x, "geoms", []))
            pts = [q for q in pts if q.geom_type == "Point"]
            q = min(pts, key=lambda q: q.distance(Point(p0))) if pts else anello.interpolate(anello.project(Point(p0)))
            cap.append(anello.project(q))
        s0, s1 = cap
        la = (s1 - s0) % E[2]
        lb = E[2] - la
        if min(la, lb) < 0.5:
            return None
        lung = (la + lb) / 2
        A = lambda u: _sul_anello(E, s0 + la * u)
        B = lambda u: _sul_anello(E, s0 - lb * u)
        chiuso = False
    elif len(buchi) == 1 and _e_anello(tenuti, ppm):
        Ch = buchi[0][1]
        Pe_o, Ph_o = Polygon(Ce), Polygon(Ch)            # stessi giri: entrambi antiorari
        if Pe_o.exterior.is_ccw is False:
            Ce = Ce[::-1]
        if Ph_o.exterior.is_ccw is False:
            Ch = Ch[::-1]
        E, Hh = _anello(Ce), _anello(Ch)
        sh = LineString(Ch).project(Point(Ce[0]))
        lung = (E[2] + Hh[2]) / 2
        A = lambda u: _sul_anello(E, E[2] * u)
        B = lambda u: _sul_anello(Hh, sh + Hh[2] * u)
        chiuso = True
    else:
        return None
    u0 = np.linspace(0, 1, 60)
    passo = passo_auto(passo, float(np.median(np.hypot(*(B(u0) - A(u0)).T))))
    n = max(4, int(lung / (passo / 2)))
    u = np.linspace(0, 1, n + 1)
    a, b = A(u), B(u)
    v = b - a
    w = np.hypot(*v.T)
    if np.median(w) > 9:                                 # troppo larga per un satin: meglio riempimento
        return None
    vn = v / np.maximum(w, 1e-9)[:, None]
    # compensazione + larghezza minima: lontano dalle punte il satin non scende sotto SATIN_MIN
    k = np.where(w > 0.3, np.maximum(comp / 2, (SATIN_MIN - w) / 2), 0)[:, None]
    a, b = a - vn * k, b + vn * k                        # compensazione: il filo tira e stringe la colonna
    zig = []
    for i in range(n + 1):                               # punti oltre 7 mm: spezzati a metà (satin diviso)
        p = a[i] if i % 2 == 0 else b[i]
        q = b[i] if i % 2 == 0 else a[i]
        # PASSO 11c «short stitch»: nel lato interno di una curva stretta i punti si ammucchiano (stesso lato
        # più vicini di metà passo): uno ogni due si accorcia del 35% verso l'altro lato
        lato = a if i % 2 == 0 else b
        if i >= 2 and (i // 2) % 2 == 1 and np.hypot(*(lato[i] - lato[i - 2])) < 0.5 * passo and w[i] > 1.2:
            p = p + (q - p) * 0.35
        if zig and np.hypot(*(p - np.array(zig[-1]))) > 7.0:
            m_ = (np.array(zig[-1]) + p) / 2 + vn[i][::-1] * np.array([1, -1]) * 0.15 * (1 if i % 4 < 2 else -1)
            zig.append(tuple(m_))
        zig.append(tuple(p))
    mezzo = (a + b) / 2
    passo_u = max(1, int(round(2.0 / (lung / n))))       # sottopunto: filza al centro ogni ~2 mm, all'andata
    cammino = [tuple(p) for p in mezzo[::passo_u]] + [tuple(mezzo[-1])]
    if np.median(w) > 3.5:                               # PASSO 11b: colonne larghe: filza sui due bordi (0,5 mm
        ai, bi = a + vn * 0.5, b - vn * 0.5              # dentro) + filza al centro, poi il satin (guide Wilcom)
        bordo = [tuple(p) for p in ai[::passo_u]] + [tuple(ai[-1])] + [tuple(p) for p in bi[::-passo_u]] + [tuple(bi[0])]
        return [bordo + cammino + zig[::-1]]
    if np.median(w) > 1.6:
        return [cammino + zig[::-1]]
    return [zig]


def satin_linea(m, ppm, passo=0.42, comp=0.25, oltre=None):
    """Satin che segue la linea: per ogni ramo dello scheletro, zig-zag largo quanto la linea in quel punto.
    oltre = pixel dove si può sconfinare un poco alle estremità (le altre zone, non lo sfondo)."""
    dt = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1]
    rami, grado = _percorsi_scheletro(m)
    tenuti = []
    for r in rami:
        if len(r) < 3:
            continue
        a = np.array(r, float)
        lung = np.hypot(*np.diff(a, axis=0).T).sum()
        w_px = np.array([dt[p] for p in r]) * 2
        libero = [grado.get(r[0], 2) == 1, grado.get(r[-1], 2) == 1]
        if (libero[0] or libero[1]) and lung < 1.2 * np.median(w_px) and len(rami) > 1:
            continue                                     # rametto spurio dello scheletro
        tenuti.append((r, a, w_px))
    rot = satin_rotaie(m, ppm, tenuti, passo, comp)      # striscia semplice o anello: rotaie, copre tutto
    if rot is not None:
        return rot
    # un estremo è libero se nessun altro ramo tenuto finisce lì vicino (un incrocio dello scheletro è un grumo di
    # pixel: 3 px di tolleranza). I rametti tolti non contano: così le estremità piatte (biforcate) sono libere.
    estremi = [(j, e) for j, (r, _, _) in enumerate(tenuti) for e in (r[0], r[-1])]

    def e_libero(j, e):
        if tenuti[j][0][0] == tenuti[j][0][-1]:
            return False                                 # anello chiuso
        return not any(k != j and abs(e[0] - f[0]) <= 3 and abs(e[1] - f[1]) <= 3 for k, f in estremi)
    # PASSO 10: tratti continui. Un digitalizzatore a un incrocio non ferma la colonna: prosegue nel ramo che
    # continua più dritto (fiocco di neve: ogni braccio è un tratto; contorni: la linea passa oltre l'incrocio).
    # Si legano a coppie gli estremi vicini con le tangenti opposte (deviazione < ~40°), poi si concatenano.
    def tangente(j, e):
        aa = tenuti[j][1]
        k = min(len(aa) - 1, max(2, int(round(1.0 * ppm))))
        p, q = (aa[0], aa[k]) if e == 0 else (aa[-1], aa[-1 - k])
        d = p - q
        return d / max(np.hypot(*d), 1e-9)
    capi = [(j, e) for j, (r, _, _) in enumerate(tenuti) if r[0] != r[-1] for e in (0, 1)]
    pos_c = {c: np.array(tenuti[c[0]][0][0 if c[1] == 0 else -1], float) for c in capi}
    rag_c = {c: max(3.0, 0.6 * float(tenuti[c[0]][2][0 if c[1] == 0 else -1])) for c in capi}
    coppie = []
    for i1 in range(len(capi)):
        for i2 in range(i1 + 1, len(capi)):
            c1, c2 = capi[i1], capi[i2]
            if c1[0] == c2[0] or np.hypot(*(pos_c[c1] - pos_c[c2])) > max(rag_c[c1], rag_c[c2]) + 2:
                continue
            dritto = -float(np.dot(tangente(*c1), tangente(*c2)))
            if dritto > 0.77:
                coppie.append((dritto, c1, c2))
    legame = {}
    for _, c1, c2 in sorted(coppie, reverse=True):
        if c1 not in legame and c2 not in legame:
            legame[c1], legame[c2] = c2, c1
    usati, catene = set(), []

    def cammina(j, e_in):
        pts = []
        while j not in usati:
            usati.add(j)
            r = tenuti[j][0]
            seq = r if e_in == 0 else r[::-1]
            pts += [p for p in seq if not pts or p != pts[-1]]
            nxt = legame.get((j, 1 - e_in))
            if nxt is None:
                return pts, (j, 1 - e_in)
            j, e_in = nxt
        return pts, None                                 # giro chiuso
    for (j, e) in capi:
        if j not in usati and (j, e) not in legame:
            pts, fine = cammina(j, e)
            catene.append((pts, (j, e), fine))
    for j in range(len(tenuti)):
        if j not in usati:
            pts, fine = cammina(j, 0)
            catene.append((pts, (j, 0), None))
    incroci = np.array([pos_c[c] for c in capi if c in legame or not e_libero(c[0], tuple(pos_c[c].astype(int)))])         if capi else np.zeros((0, 2))
    tratti = []
    # tappo sugli incroci: il nodo dove si incontrano tre linee è più largo della linea; un piccolo pieno a stella
    # (diametri incrociati) lo copre, le colonne ci passano sopra
    visti_i = []
    for p in incroci:
        if any(np.hypot(*(p - q)) < 4 for q in visti_i):
            continue
        visti_i.append(p)
        rr = float(dt[int(p[0]), int(p[1])]) / ppm
        if rr < 0.35:
            continue
        rr = min(rr * 1.1, 1.6)
        cx, cy = p[1] / ppm, p[0] / ppm
        tappo = []                                        # piccolo satin a righe parallele (a 45°) dentro il cerchio:
        c45, s45 = np.cos(np.pi / 4), np.sin(np.pi / 4)   # punti lunghi quanto la corda, mai sotto 0,5 mm
        for i, yy in enumerate(np.arange(-rr + 0.15, rr, 0.35)):
            meta = np.sqrt(max(rr * rr - yy * yy, 0.0))
            if meta < 0.25:
                continue
            xx = -meta if i % 2 == 0 else meta
            tappo.append((cx + xx * c45 - yy * s45, cy + xx * s45 + yy * c45))
            tappo.append((cx - xx * c45 - yy * s45, cy - xx * s45 + yy * c45))
        if len(tappo) >= 4:
            tratti.append(tappo)
    for pts, inizio, fine in catene:
        r = pts
        if len(r) < 3:
            continue
        a = np.array(r, float)
        w_px = np.array([dt[p] for p in r]) * 2
        if len(incroci):                                 # SOLO vicino agli incroci la distanza dal bordo si gonfia:
            dmin = np.sqrt(((a[:, None, :] - incroci[None]) ** 2).sum(-1)).min(1)   # altrove le linee anime
            w_med = np.median(w_px)                                                  # cambiano davvero larghezza
            w_px = np.where(dmin < 1.2 * w_med, np.minimum(w_px, 1.35 * w_med), w_px)
        if fine is None:
            libero = [False, False]
        else:
            libero = [e_libero(inizio[0], tenuti[inizio[0]][0][0 if inizio[1] == 0 else -1]),
                      e_libero(fine[0], tenuti[fine[0]][0][0 if fine[1] == 0 else -1])]
        P = a[:, ::-1] / ppm                             # (x, y) in mm
        W = w_px / ppm
        k = max(2, int(round(0.75 * ppm)))              # liscia il percorso a scalini: finestra ~1,5 mm
        kw = max(3, int(round(1.25 * ppm)))             # e la larghezza: ~2,5 mm (bordi netti, non «pelosi»)
        if len(P) > 2 * k:
            P = np.stack([np.convolve(np.pad(P[:, j], k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), "valid") for j in (0, 1)], 1)
        if len(W) > 2 * kw:
            W = np.convolve(np.pad(W, kw, mode="edge"), np.ones(2 * kw + 1) / (2 * kw + 1), "valid")
        # incroci: vicino all'incrocio lo scheletro si «gonfia» (la distanza dal bordo cresce) e il ramo si ferma
        # al centro dell'incrocio senza toccare gli altri -> bozze e tacche. Larghezza bloccata a quella tipica del
        # ramo e prolungamento di mezza larghezza oltre il centro: i rami si sovrappongono e l'incrocio è pieno.
        w_tip = float(np.median(W)) if len(W) else 0.0
        for fine in (0, 1):
            if len(P) < 4:
                continue
            if libero[fine]:
                P, W = _prolunga(P, W, fine, m, ppm, oltre)
            elif r[0] != r[-1]:
                d_end = np.hypot(*(P - (P[0] if fine == 0 else P[-1])).T)
                vicino = d_end < w_tip * 1.2
                W = np.where(vicino, np.minimum(W, w_tip * 1.1), W)
                dietro = min(len(P) - 1, max(3, int(round(1.0 * ppm))))
                p0, p1 = (P[0], P[dietro]) if fine == 0 else (P[-1], P[-1 - dietro])
                dv = (p0 - p1) / max(np.hypot(*(p0 - p1)), 1e-9)
                q = p0 + dv * w_tip * 0.5
                P = np.vstack([q, P]) if fine == 0 else np.vstack([P, q])
                W = np.r_[w_tip, W] if fine == 0 else np.r_[W, w_tip]
        F, t = _ricampiona(P, 0.1)
        d0 = np.r_[0, np.cumsum(np.hypot(*np.diff(P, axis=0).T))]
        Wf = np.maximum(np.interp(t, d0, W) + comp, SATIN_MIN)
        if len(F) < 3:
            continue
        # passo 31: bordi lisci. Prima la normale veniva dalla linea centrale con ancora gli scalini dei pixel e le
        # punte del satin entravano e uscivano di 0,2-0,3 mm (contorno «peloso» della renna su stoffa, 23/11/2026).
        # Linea, normale e larghezza lisciate su ~1 mm (anello chiuso: lisciatura circolare).
        from scipy.ndimage import gaussian_filter1d
        modo = "wrap" if r[0] == r[-1] and len(F) > 20 else "nearest"
        if len(F) > 12:
            F = gaussian_filter1d(F, 4.0, axis=0, mode=modo)
            Wf = gaussian_filter1d(Wf, 12.0, mode=modo)          # larghezza quasi costante (~1,2 mm di finestra)
        tg = np.gradient(gaussian_filter1d(F, 6.0, axis=0, mode=modo) if len(F) > 12 else F, axis=0)
        tg /= np.maximum(np.hypot(*tg.T), 1e-9)[:, None]
        nr = np.stack([-tg[:, 1], tg[:, 0]], 1)
        cammino = [tuple(p) for p in F[::20]] + [tuple(F[-1])]         # sottopunto: filza al centro, andata
        zig = []
        step = max(1, int(round(passo_auto(passo, float(np.median(Wf))) / 2 / 0.1)))
        for j, i in enumerate(range(len(F) - 1, -1, -step)):           # satin al ritorno, sopra il sottopunto
            s = 1 if j % 2 == 0 else -1
            zig.append(tuple(F[i] + nr[i] * s * Wf[i] / 2))
        tratti.append(cammino + zig if Wf.mean() > 1.6 else zig)
    return tratti


def filza3_linea(m, ppm, passate=3):
    """Filza ripassata (3 volte; 5 per le righe sottili ma «piene» dei disegni piccoli: passo 31)."""
    rami, _ = _percorsi_scheletro(m)
    out = []
    for r in rami:
        if len(r) < 3:
            continue
        Q = np.array(r, float)[:, ::-1] / ppm
        lung = float(np.sum(np.hypot(*np.diff(Q, axis=0).T))) if len(Q) > 1 else 0.0
        # punti da 2 mm; sui rami corti (bracci di un cristallo piccolo) più corti, fino a 1 mm: niente spigoli
        P, _ = _ricampiona(Q, max(1.0, min(2.0, lung / 4)))
        b = [tuple(P[0])]
        for p in P[1:]:
            q = b[-1]
            b += [tuple(p), q, tuple(p)] if passate == 3 else [tuple(p), q, tuple(p), q, tuple(p)]
        out.append(b)
    return out


# passo 29: densità del riempimento secondo la stoffa. Prima prova su tela verde (renna, 23/11/2026): a 0,42 mm con
# sottofondo a 2,2 mm il verde si vedeva tra i punti (filo 6,0 mm per mm² contro 6,5-8 dei disegni commerciali)
DENSITA = {"normale": {"file": 0.40, "sotto": 2.0, "nome": "stoffa chiara"},
           "fitta": {"file": 0.36, "sotto": 1.5, "nome": "stoffa colorata o scura"}}
_dens = DENSITA["normale"]


def riempi(poli, angolo, fondo=True):
    import patch as P
    out = []
    parti = [poli] if poli.geom_type == "Polygon" else [g for g in poli.geoms if g.geom_type == "Polygon"]
    for pz in parti:
        if pz.area < 0.5:
            continue
        if fondo:
            bordo = pz.buffer(-0.5)
            for b in ([bordo] if bordo.geom_type == "Polygon" else list(getattr(bordo, "geoms", []))):
                if not b.is_empty:
                    out.append(P.corsa(b, 2.0))
            inn = pz.buffer(-0.4)
            if not inn.is_empty:
                out += P.tatami(inn, angolo + 90, _dens["sotto"], 3.5)
        out += P.tatami(pz, angolo, _dens["file"], 3.5)
    return out


def riempi_cerchi(poli, fondo=True):
    """Passo 30 — riempimento a cerchi concentrici («contour fill»): anelli che seguono il bordo, uno dentro l'altro.
    Per le forme tonde (naso della renna, palline, bottoni grandi): luce morbida, bordo pulito, niente righe dritte."""
    import patch as P
    out = []
    parti = [poli] if poli.geom_type == "Polygon" else [g for g in poli.geoms if g.geom_type == "Polygon"]
    for pz in parti:
        if pz.area < 0.5:
            continue
        if fondo:                                                     # sottofondo rado, come il riempimento
            inn = pz.buffer(-0.5)
            if not inn.is_empty:
                out += P.tatami(inn, 45, max(_dens["sotto"], 1.8), 3.5)
        d, pos = 0.2, None
        while True:
            g = pz.buffer(-d, quad_segs=8)
            if g.is_empty or g.area < 0.05:
                break
            for anello in ([g] if g.geom_type == "Polygon" else list(g.geoms)):
                if anello.length < 1.0:
                    continue
                pts = P.corsa(anello, 2.2)
                if len(pts) < 3:
                    continue
                if pos is not None:                                   # parto dal punto più vicino all'anello prima
                    k = int(np.argmin([np.hypot(x - pos[0], y - pos[1]) for x, y in pts[:-1]]))
                    pts = pts[k:-1] + pts[:k + 1]
                out.append(pts)
                pos = pts[-1]
            d += _dens["file"]
    return out


def solo_contorno(poli):
    """Passo 30 — «solo contorno»: filza tripla sul bordo della zona (la zona resta vuota)."""
    import patch as P
    out = []
    parti = [poli] if poli.geom_type == "Polygon" else [g for g in poli.geoms if g.geom_type == "Polygon"]
    for pz in parti:
        if pz.length < 1.0:
            continue
        r = P.corsa(pz, 2.0, inset=0.2) if pz.area > 1 else P.corsa(pz, 2.0)
        out.append(r + r[::-1][1:] + r[1:])
    return out


def applica_scelte(A, scelte):
    """Passo 30 — scelte a mano per zona: [{"x", "y", "punto", "angolo", "densita"}] in frazioni dell'immagine
    ORIGINALE (come le etichette: sopravvivono ai ricalcoli). punto: riempimento | satin | cerchi | contorno | salta."""
    z, info = A["zone"], A["info"]
    H, W = z.shape
    per_id = {o["id"]: o for o in info}
    for o in info:
        o.pop("scelta", None)
    rit = A.get("ritaglio", (0.0, 0.0, 1.0, 1.0))
    for s in scelte or []:
        fx, fy = a_ritaglio(rit, s["x"], s["y"])
        if not (0 <= fx < 1 and 0 <= fy < 1):
            continue
        k = int(z[int(fy * H), int(fx * W)])
        if k < 0 or k not in per_id:
            continue
        o = per_id[k]
        while o.get("assorbita_in") is not None and o["assorbita_in"] in per_id:
            o = per_id[o["assorbita_in"]]
        o["scelta"] = {c: s[c] for c in ("punto", "angolo", "densita") if s.get(c) not in (None, "", "auto")}


def satin_pieno(poli, asse):
    """Puntini (occhi, bottoni): un punto unico da bordo a bordo, di traverso sulla forma = satin."""
    import patch as P
    # zona di un pixel (disegni a tratto): l'asse dalla covarianza viene NaN e shapely si rompe ruotando
    asse = float(asse) if asse is not None and np.isfinite(asse) else 0.0
    parti = [poli] if poli.geom_type == "Polygon" else [g for g in poli.geoms if g.geom_type == "Polygon"]
    out = []
    for pz in parti:
        if pz.is_empty:
            continue
        pz = pz.buffer(0.12)                                            # compensazione del tiro
        out += P.tatami(pz, asse + 90, 0.38, 6.5)            # Auto Split (Wilcom): mai punti oltre ~7 mm
    return out


def percorso_interno(dentro, griglia, cella, a, b, max_mm=250.0, coperto=None, stesso=None):
    """Strada per spostare l'ago da a a b senza uscire dalle zone permesse (niente taglio): dritta se resta tutta
    sotto zone ricamate DOPO, altrimenti il percorso più breve su una griglia di mezzo millimetro. Passare sopra
    la zona che sto ricamando (già fatta in parte) costa di più: la strada preferisce andare sotto il contorno.
    -> lista di punti (b compreso) o None se bisogna tagliare."""
    from shapely.geometry import LineString
    from skimage.graph import route_through_array
    if dentro is not None and dentro.contains(LineString([a, b])):
        return [b]
    if griglia is None or not griglia.any():
        return None
    H, W = griglia.shape

    def cella_di(p):
        r, c = int(p[1] / cella), int(p[0] / cella)
        r, c = min(max(r, 0), H - 1), min(max(c, 0), W - 1)
        if griglia[r, c]:
            return r, c
        rr, cc = np.nonzero(griglia[max(0, r - 3):r + 4, max(0, c - 3):c + 4])   # cella permessa più vicina
        if not len(rr):
            return None
        k = int(np.argmin((rr + max(0, r - 3) - r) ** 2 + (cc + max(0, c - 3) - c) ** 2))
        return rr[k] + max(0, r - 3), cc[k] + max(0, c - 3)
    s, e = cella_di(a), cella_di(b)
    if s is None or e is None:
        return None
    costo = np.where(griglia, 6.0, 1e5)
    if stesso is not None:
        costo[stesso] = 2.0                 # sopra punti GIÀ fatti dello stesso colore (in cima): non si vede
    if coperto is not None:
        costo[coperto] = 1.0
    via, tot = route_through_array(costo, s, e, fully_connected=True, geometric=True)
    if tot >= 1e5:
        return None
    pts = [((c + 0.5) * cella, (r + 0.5) * cella) for r, c in via]
    linea = LineString([a] + pts + [b]).simplify(0.3)
    # passo 28: molte ricamatrici NON tagliano i salti (si tagliano a mano): meglio un giro lungo nascosto
    if linea.length > max_mm or linea.length > 6 * np.hypot(b[0] - a[0], b[1] - a[1]) + 30:
        return None
    return list(linea.coords)[1:]


def _griglia(m, ppm, passo=0.5):
    """Maschera in pixel -> griglia booleana a celle di ~passo mm (cella permessa solo se tutta dentro)."""
    f = max(1, int(round(passo * ppm)))
    H, W = m.shape
    h, w = H // f, W // f
    # cella permessa se è dentro per almeno metà: con «tutta dentro» le linee da ~1 mm (contorni anime) sparivano
    # dalla griglia e l'ago tagliava invece di scorrere lungo il contorno (passo 12b)
    return m[:h * f, :w * f].reshape(h, f, w, f).mean(axis=(1, 3)) >= 0.5, f / ppm


def _scoperto(tratti, m, ppm):
    """Frazione della zona m (pixel) che i punti (mm, filo 0,4 mm) non coprono."""
    from PIL import Image as _I, ImageDraw as _D
    ys, xs = np.nonzero(m)
    if not len(ys):
        return 0.0
    y0, x0 = ys.min(), xs.min()
    sub = m[y0:ys.max() + 1, x0:xs.max() + 1]
    im = _I.new("L", (sub.shape[1], sub.shape[0]), 0)
    d = _D.Draw(im)
    lw = max(1, int(round(0.4 * ppm)))
    for tr in tratti:
        pts = [(x * ppm - x0, y * ppm - y0) for x, y in tr]
        if len(pts) >= 2:
            d.line(pts, fill=255, width=lw)
    cop = np.asarray(im) > 0
    dentro = ndimage.binary_erosion(sub, iterations=max(1, int(round(0.25 * ppm))))
    if not dentro.any():
        return 0.0
    return float((dentro & ~cop).sum() / dentro.sum())


def _tensore(gx, gy, w):
    jxx, jyy, jxy = (gx * gx * w).sum(), (gy * gy * w).sum(), (gx * gy * w).sum()
    tr = jxx + jyy
    if tr <= 0:
        return 0.0, 0.0, None
    coer = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / tr
    grad = 0.5 * np.degrees(np.arctan2(2 * jxy, jxx - jyy))     # direzione media del gradiente
    return tr / w.sum(), coer, float((grad + 90) % 180)           # tratti = perpendicolari al gradiente


def angolo_fibre(A, m, etichetta=None):
    """PASSO 21: direzione delle file del riempimento come la sceglierebbe un digitalizzatore: lungo le ciocche dei
    capelli, lungo le pieghe del vestito. Due indizi (tensore di struttura):
      - la trama DENTRO la zona (sfumature, tratti disegnati senza contorno);
      - le LINEE disegnate che corrono dentro o lungo la zona (nei disegni a tinte piatte le ciocche sono zone-linea
        a parte, attaccate al contorno: fuori dalla maschera del riempimento).
    Vince l'indizio più coerente; se nessuno ha una direzione chiara -> None (resta la regola dell'asse)."""
    ppm = A["ppm"]
    if "_L" not in A:
        from skimage.color import rgb2lab
        A["_L"] = rgb2lab(A["rgb"] / 255.0)[..., 0]
        lin = np.zeros(A["zone"].shape, bool)
        for o in A["info"]:
            if o["punto"] in ("satin", "filza3"):
                lin |= A["zone"] == o["id"]
        A["_linee"] = lin
    ys, xs = np.nonzero(m)
    if len(ys) < 50:
        return None
    pad = int(ppm)
    y0, y1, x0, x1 = max(0, ys.min() - pad), ys.max() + pad + 1, max(0, xs.min() - pad), xs.max() + pad + 1
    mm = m[y0:y1, x0:x1]
    soglia = 0.25 if etichetta == "capelli" else 0.4
    scelte = []
    # 1) trama dentro (bordo esterno escluso)
    dentro = ndimage.binary_erosion(ndimage.binary_fill_holes(mm), iterations=max(1, int(round(0.4 * ppm))))
    if dentro.sum() >= 4 * ppm * ppm:
        L = ndimage.gaussian_filter(A["_L"][y0:y1, x0:x1], 0.6)
        en, co, an = _tensore(ndimage.sobel(L, 1), ndimage.sobel(L, 0), dentro.astype(float))
        if en >= 20 and co >= soglia:
            scelte.append((co, an))
    # 2) linee disegnate dentro / lungo la zona (almeno ~ 3 volte il lato della zona in lunghezza di tratti)
    vic = ndimage.binary_dilation(mm, iterations=max(1, int(round(0.6 * ppm))))
    lm = A["_linee"][y0:y1, x0:x1] & vic
    lato = np.sqrt(mm.sum())
    if lm.sum() >= 3 * lato * 0.3 * ppm:
        g = ndimage.gaussian_filter(lm.astype(float), 0.3 * ppm)
        en, co, an = _tensore(ndimage.sobel(g, 1), ndimage.sobel(g, 0), vic.astype(float))
        if co >= soglia:
            scelte.append((co, an))
    return max(scelte)[1] if scelte else None


def sezioni_fibre(A, m, min_mm2=60.0, sez_mm2=40.0, passo=45, max_sez=6):
    """PASSO 24: un riempimento grande con ciocche che girano (ciuffo di Elsa: coerenza 0,19 su tutta la zona) si
    divide in SEZIONI, ognuna con il suo angolo, come fa il digitalizzatore a mano. Campo di direzioni locale
    (tensore di struttura sulla luminosità, linee comprese, media su ~1,5 mm) quantizzato a passi di 30°;
    sezioni sotto sez_mm2 si uniscono alla vicina. -> [(maschera, angolo)] o None (una sezione sola)."""
    from skimage.morphology import disk
    ppm = A["ppm"]
    if m.sum() < min_mm2 * ppm * ppm:
        return None
    if "_L" not in A:
        angolo_fibre(A, m)
    ys, xs = np.nonzero(m)
    pad = int(2 * ppm)
    y0, y1, x0, x1 = max(0, ys.min() - pad), ys.max() + pad + 1, max(0, xs.min() - pad), xs.max() + pad + 1
    mm = m[y0:y1, x0:x1]
    L = ndimage.gaussian_filter(A["_L"][y0:y1, x0:x1], 0.6)
    gx, gy = ndimage.sobel(L, 1), ndimage.sobel(L, 0)
    s = 1.5 * ppm
    w = ndimage.binary_dilation(mm, iterations=max(1, int(0.6 * ppm))).astype(float)
    jxx = ndimage.gaussian_filter(gx * gx * w, s)
    jyy = ndimage.gaussian_filter(gy * gy * w, s)
    jxy = ndimage.gaussian_filter(gx * gy * w, s)
    tr = jxx + jyy + 1e-9
    coer = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / tr
    theta = (0.5 * np.degrees(np.arctan2(2 * jxy, jxx - jyy)) + 90) % 180
    # solo dove DENTRO ci sono davvero tratti (capelli di Elsa ~3000, pelo della volpe ~2000): sulle zone piatte
    # (pancia del pupazzo ~100, vestito di Babbo ~300, viso ~1200) le «direzioni» venivano solo dai bordi
    dentro = ndimage.binary_erosion(mm, iterations=max(1, int(0.6 * ppm)))
    if not dentro.any() or np.median(ndimage.gaussian_filter(gx * gx + gy * gy, s)[dentro]) < 1500:
        return None
    if np.median(coer[mm]) < 0.3:                       # niente direzioni chiare neanche localmente
        return None
    nb = 180 // passo
    q = (np.round(theta / passo).astype(int) % nb) + 1
    q[~mm] = 0
    from skimage.filters.rank import majority
    q = majority(q.astype(np.uint8), disk(max(1, int(round(1.0 * ppm))))).astype(int)
    q[~mm] = 0
    q[mm & (q == 0)] = 1
    # sezioni connesse; le piccole si uniscono alla vicina con più contatto (soglia che cresce finché sono <= max_sez)
    soglia_px = sez_mm2 * ppm * ppm
    for _ in range(14):
        lab = np.zeros_like(q)
        n = 0
        for b in range(1, nb + 1):
            l, k = ndimage.label(q == b)
            lab[l > 0] = l[l > 0] + n
            n += k
        dim = np.bincount(lab.ravel(), minlength=n + 1)
        piccole = [i for i in range(1, n + 1) if dim[i] < soglia_px]
        if not piccole:
            if n <= max_sez:
                break
            soglia_px *= 1.5
            continue
        for i in sorted(piccole, key=lambda i: dim[i]):
            p = lab == i
            anello = ndimage.binary_dilation(p) & ~p & mm
            vic = q[anello]
            vic = vic[vic > 0]
            if len(vic):
                q[p] = np.bincount(vic).argmax()
            else:                                         # pezzetto staccato: va alla sezione più vicina
                altri = (q > 0) & ~p
                if altri.any():
                    _, (iy, ix) = ndimage.distance_transform_edt(~altri, return_indices=True)
                    q[p] = q[iy[p], ix[p]]
    sezioni = []
    for b in range(1, nb + 1):
        l, k = ndimage.label(q == b)
        for i in range(1, k + 1):
            p = l == i
            if p.sum() < 4:
                continue
            a2 = np.radians(2 * theta[p])
            ang = float((np.degrees(np.arctan2(np.sin(a2).mean(), np.cos(a2).mean())) / 2) % 180)
            full = np.zeros(m.shape, bool)
            full[y0:y1, x0:x1] = p
            sezioni.append((full, ang))
    return sezioni if 1 < len(sezioni) <= max_sez else None


# PASSO 25 — STILI. «pulito» = quello di sempre (tatami a file avanti-indietro). «schizzo» = pittura ad ago come la
# volpe 40420 del catalogo, misurata con stile.py: 70% dei punti prosegue DRITTO (tratti, non file avanti-indietro),
# svolta media 4° (tratti morbidi che seguono il pelo), punti 0,7-3,8 mm (mediana 1,7), ~1,6 colori sovrapposti.
STILI = {
    "pulito": None,
    "schizzo": {"passo": (1.2, 3.0), "tratto": (5.0, 12.0), "spazio": 0.45, "collega": 3.0, "passate": 2, "sfasa": 12.0,
                "base": 0.8},
    # «pelo» = koala Fuzzy Wuzzy del catalogo (44fuzzy301/205, stile.py): punti LUNGHI (mediana 3,4 mm, p90 5,7),
    # metà avanti-indietro (frangia dal dentro al bordo e ritorno) e metà dritti, svolta 4°, 1,5 colori sovrapposti
    "pelo": {"passo": (2.0, 5.0), "tratto": (6.0, 14.0), "spazio": 0.5, "collega": 3.0, "passate": 1, "sfasa": 0.0,
             "base": 0.8, "frangia": (1.5, 4.5), "fuori": 0.35},
}


def campo_direzioni(A, m, angolo_base):
    """Direzione delle fibre in ogni pixel (gradi, mod 180) nel riquadro della maschera: dove il disegno ha tratti
    segue quelli (tensore di struttura), dove è piatto tende all'angolo della zona. -> (theta, y0, x0)."""
    ppm = A["ppm"]
    if "_L" not in A:
        angolo_fibre(A, m)
    ys, xs = np.nonzero(m)
    pad = int(2 * ppm)
    y0, y1, x0, x1 = max(0, ys.min() - pad), ys.max() + pad + 1, max(0, xs.min() - pad), xs.max() + pad + 1
    L = ndimage.gaussian_filter(A["_L"][y0:y1, x0:x1], 0.6)
    gx, gy = ndimage.sobel(L, 1), ndimage.sobel(L, 0)
    s = 1.2 * ppm
    jxx, jyy, jxy = (ndimage.gaussian_filter(v, s) for v in (gx * gx, gy * gy, gx * gy))
    # doppio angolo del GRADIENTE; i tratti sono perpendicolari -> si aggiunge 180° al doppio angolo
    c2, s2 = -(jxx - jyy), -2 * jxy
    peso = np.sqrt(c2 ** 2 + s2 ** 2)
    base = np.radians(2 * angolo_base)
    k = 0.15 * np.median(peso[m[y0:y1, x0:x1]]) + 200.0          # quanto conta l'angolo della zona dove è piatto
    c2 = c2 + k * np.cos(base)
    s2 = s2 + k * np.sin(base)
    c2, s2 = ndimage.gaussian_filter(c2, 0.5 * ppm), ndimage.gaussian_filter(s2, 0.5 * ppm)
    return (np.degrees(np.arctan2(s2, c2)) / 2) % 180, y0, x0


def riempi_schizzo(A, m, angolo_base, par, seme=0):
    """Più passate sovrapposte (pittura ad ago: strati), ognuna con le direzioni un po' sfasate. Sotto, uno strato
    di base rado dello stesso colore (tatami a righe di par["base"] mm): i tratti da soli lasciavano 100-180 mm² di
    tela scoperta su un disegno da 100 mm."""
    out = []
    if par.get("base"):
        import patch as P
        poli = maschera_poligono(m, A["ppm"]).buffer(-0.2)
        parti = [poli] if poli.geom_type == "Polygon" else [g for g in getattr(poli, "geoms", []) if g.geom_type == "Polygon"]
        for pz in parti:
            if pz.area >= 0.5:
                out += P.tatami(pz, angolo_base + 90, par["base"], 3.5)
    for k in range(par.get("passate", 1)):
        out += _schizzo_passata(A, m, angolo_base, par, seme * 7 + k, (-1) ** k * par.get("sfasa", 0) * (k > 0))
    if par.get("frangia"):
        out += _frangia(A, m, par, seme)
    return out


def _frangia(A, m, par, seme=0):
    """Bordo «spettinato» (stile pelo): lungo il contorno, ogni 0,45 mm un punto che parte da dentro (profondità a
    caso in par["frangia"] mm) e arriva un po' FUORI dal bordo (par["fuori"] mm), poi il successivo: il filo va
    avanti e indietro tra dentro e bordo come la pelliccia dei koala Fuzzy Wuzzy."""
    from skimage.measure import find_contours
    ppm = A["ppm"]
    rng = np.random.default_rng(seme + 991)
    ys, xs = np.nonzero(m)
    pad = int(6 * ppm)
    y0, x0 = max(0, ys.min() - pad), max(0, xs.min() - pad)
    sub = m[y0:ys.max() + pad + 1, x0:xs.max() + pad + 1]
    dt = ndimage.distance_transform_edt(sub)
    gy, gx = np.gradient(ndimage.gaussian_filter(dt, 1.0))
    H, W = sub.shape
    out = []
    for c in find_contours(sub.astype(float), 0.5):
        if len(c) < 8:
            continue
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(c, axis=0).T))])
        if s[-1] < 2 * ppm:
            continue
        tr = []
        for u in np.arange(0, s[-1], 0.45 * ppm):
            py, px_ = c[min(np.searchsorted(s, u), len(c) - 1)]
            iy, ix = min(max(int(round(py)), 0), H - 1), min(max(int(round(px_)), 0), W - 1)
            n = np.array([gy[iy, ix], gx[iy, ix]])          # verso l'interno
            if np.hypot(*n) < 1e-6:
                continue
            n /= np.hypot(*n)
            prof = rng.uniform(*par["frangia"]) * ppm
            jy, jx = min(max(int(round(py + n[0] * 2)), 0), H - 1), min(max(int(round(px_ + n[1] * 2)), 0), W - 1)
            prof = min(prof, 2 * dt[jy, jx] + 2)            # non oltre la metà della forma
            tr += [(py + n[0] * prof, px_ + n[1] * prof), (py - n[0] * par["fuori"] * ppm, px_ - n[1] * par["fuori"] * ppm)]
        if len(tr) >= 2:
            out.append([((x + x0) / ppm, (y + y0) / ppm) for y, x in tr])
    return out


def _schizzo_passata(A, m, angolo_base, par, seme=0, sfasa=0.0):
    """Riempimento «schizzo»: tratti che scorrono lungo il campo di direzioni (punti di lunghezza variabile), uno
    dopo l'altro; il passaggio al tratto seguente è un punto dritto se resta dentro la zona (come a mano)."""
    ppm = A["ppm"]
    rng = np.random.default_rng(seme)
    theta, y0, x0 = campo_direzioni(A, m, angolo_base)
    theta = (theta + sfasa) % 180
    H, W = theta.shape
    dentro = ndimage.binary_erosion(m, iterations=max(1, int(round(0.15 * ppm))))[y0:y0 + H, x0:x0 + W]
    if dentro.sum() < 4:
        return []
    cop = np.zeros_like(dentro)
    sp = par["spazio"] * ppm
    # semi su una griglia sfalsata, in ordine a serpentina
    yy, xx = np.nonzero(dentro)
    semi = []
    for r, y in enumerate(np.arange(yy.min(), yy.max() + 1, sp)):
        riga = np.arange(xx.min() + (sp / 2 if r % 2 else 0), xx.max() + 1, sp)
        riga = riga if r % 2 == 0 else riga[::-1]
        for x in riga:
            py, px_ = y + rng.uniform(-0.3, 0.3) * sp, x + rng.uniform(-0.3, 0.3) * sp
            iy, ix = int(round(py)), int(round(px_))
            if 0 <= iy < H and 0 <= ix < W and dentro[iy, ix]:
                semi.append((py, px_))
    semi = np.array(semi) if semi else np.zeros((0, 2))
    usati = np.zeros(len(semi), bool)

    def libero(p):
        iy, ix = int(round(p[0])), int(round(p[1]))
        return 0 <= iy < H and 0 <= ix < W and dentro[iy, ix]

    def segna(a, b):
        n = max(2, int(np.hypot(b[0] - a[0], b[1] - a[1])))
        r = max(1, int(round(0.55 * sp)))
        for t in np.linspace(0, 1, n):
            iy, ix = int(round(a[0] + (b[0] - a[0]) * t)), int(round(a[1] + (b[1] - a[1]) * t))
            cop[max(0, iy - r):iy + r + 1, max(0, ix - r):ix + r + 1] = True

    def dentro_seg(a, b):
        n = max(2, int(np.hypot(b[0] - a[0], b[1] - a[1])))
        return all(libero((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)) for t in np.linspace(0, 1, n))

    def traccia(p):
        """Un tratto da p lungo il campo, in avanti."""
        pts = [p]
        lmax = rng.uniform(*par["tratto"]) * ppm
        lung, d = 0.0, None
        while lung < lmax:
            iy, ix = int(round(p[0])), int(round(p[1]))
            a = np.radians(theta[min(max(iy, 0), H - 1), min(max(ix, 0), W - 1)])
            v = np.array([np.sin(a), np.cos(a)])               # (dy, dx)
            lp = rng.uniform(*par["passo"]) * ppm
            if d is None:                                   # primo passo: il verso che va nel NON coperto
                versi = [v, -v] if rng.random() < 0.5 else [-v, v]
                d = None
                for w in versi:
                    q = (p[0] + w[0] * lp, p[1] + w[1] * lp)
                    mq = (int(round((p[0] + q[0]) / 2)), int(round((p[1] + q[1]) / 2)))
                    if libero(q) and not cop[int(round(q[0])), int(round(q[1]))] and not cop[mq]:
                        d = w
                        break
                if d is None:
                    break
                v = d
            elif v @ d < 0:
                v = -v
            d = v
            q = (p[0] + d[0] * lp, p[1] + d[1] * lp)
            if not dentro_seg(p, q):
                break
            ciq = int(round(q[0])), int(round(q[1]))
            mid = int(round((p[0] + q[0]) / 2)), int(round((p[1] + q[1]) / 2))
            if len(pts) >= 2 and (cop[ciq] or cop[mid]):
                break
            pts.append(q)
            lung += np.hypot(q[0] - p[0], q[1] - p[1])
            p = q
        return pts

    tracce, cur = [], []
    pos = None
    while True:
        liberi = np.nonzero(~usati)[0]
        if not len(liberi):
            break
        # salta i semi già coperti
        cop_semi = cop[np.clip(np.round(semi[liberi, 0]).astype(int), 0, H - 1), np.clip(np.round(semi[liberi, 1]).astype(int), 0, W - 1)]
        usati[liberi[cop_semi]] = True
        liberi = liberi[~cop_semi]
        if not len(liberi):
            break
        if pos is None:
            j = liberi[0]
        else:
            dd = np.hypot(semi[liberi, 0] - pos[0], semi[liberi, 1] - pos[1])
            j = liberi[int(dd.argmin())]
        usati[j] = True
        tr = traccia(tuple(semi[j]))
        if len(tr) < 2:
            continue
        for a, b in zip(tr[:-1], tr[1:]):
            segna(a, b)
        if cur and np.hypot(tr[0][0] - cur[-1][0], tr[0][1] - cur[-1][1]) <= par["collega"] * ppm and dentro_seg(cur[-1], tr[0]):
            cur += tr
        else:
            if len(cur) >= 2:
                tracce.append(cur)
            cur = list(tr)
        pos = cur[-1]
    if len(cur) >= 2:
        tracce.append(cur)
    # pixel (riga, colonna) del riquadro -> mm (x = colonna / ppm, y = riga / ppm)
    return [[((x + x0) / ppm, (y + y0) / ppm) for y, x in tr] for tr in tracce]


def digitalizza(A, stile="pulito", densita="normale", scelte=None):
    """-> (EmbPattern, info) con l'ordine: sotto -> sopra, contorni esterni per ultimi.
    densita: "normale" | "fitta" (stoffe colorate o scure); scelte: punto/angolo/densità scelti a mano per zona."""
    global _dens
    applica_scelte(A, scelte)
    try:
        return _digitalizza(A, stile, DENSITA.get(densita, DENSITA["normale"]))
    finally:
        _dens = DENSITA["normale"]


def _digitalizza(A, stile, dens_base):
    global _dens
    import pyembroidery as pe
    from componi import _fermo
    _livelli(A["info"])
    unita = unita_di_ricamo(A)
    ppm = A["ppm"]
    out = pe.EmbPattern()
    ultimo_col, pos = None, None
    pezzi = []
    for u in unita:
        o = u["zona"]
        par = STILI.get(stile)
        sc = o.get("scelta") or {}
        _dens = DENSITA.get(sc.get("densita"), dens_base)
        p_sc = sc.get("punto")
        ang_sc = float(sc["angolo"]) % 180 if isinstance(sc.get("angolo"), (int, float)) else None
        if p_sc == "salta":
            o["angolo"] = "saltata"
            tr = []
        elif p_sc == "cerchi":
            tr = riempi_cerchi(maschera_poligono(u["maschera"], ppm))
            o["angolo"] = "cerchi"
        elif p_sc == "contorno":
            tr = solo_contorno(maschera_poligono(u["maschera"], ppm))
            o["angolo"] = "contorno"
        elif p_sc == "satin" and o["punto"] not in ("satin", "filza3"):
            # satin di traverso sulla forma (spezzato oltre ~7 mm: Auto Split); la direzione scelta è quella dei fili
            asse = (ang_sc - 90) if ang_sc is not None else o["asse"]
            tr = satin_pieno(maschera_poligono(u["maschera"], ppm), asse)
            o["angolo"] = round((asse + 90) % 180)
        elif p_sc == "riempimento" or (ang_sc is not None and o["punto"] in ("fill", "satin_pieno")):
            ang = ang_sc
            if ang is None:
                ang = angolo_fibre(A, u["maschera"], o.get("etichetta"))
            if ang is None:
                ang = o["asse"] if o["allung"] >= 1.5 else 45.0
            o["angolo"] = round(ang)
            tr = riempi(maschera_poligono(u["maschera"], ppm), ang)
        elif par and o["punto"] in ("fill", "satin_pieno") and o["area_mm2"] >= 4:
            ang = angolo_fibre(A, u["maschera"], o.get("etichetta"))
            if ang is None:
                ang = o["asse"] if o["allung"] >= 1.5 else 45.0
            tr = riempi_schizzo(A, u["maschera"], ang, par, seme=o["id"])
            o["angolo"] = "schizzo"
        elif o["punto"] == "fill":
            poli = maschera_poligono(u["maschera"], ppm)
            # sull'UNITÀ intera (pezzi dello stesso colore fusi: il ciuffo di Elsa è due zone, una sola unità)
            ang = angolo_fibre(A, u["maschera"], o.get("etichetta"))
            sez = sezioni_fibre(A, u["maschera"]) if ang is None else None
            if sez:
                # ogni sezione col suo angolo; le sezioni si sovrappongono di un pixel (niente fessure tra le file)
                tr = []
                asse = np.radians(o["asse"])
                for p, a_s in sorted(sez, key=lambda s: (np.nonzero(s[0])[1].mean() * np.cos(asse)
                                                          + np.nonzero(s[0])[0].mean() * np.sin(asse))):
                    pp = ndimage.binary_dilation(p) & u["maschera"]
                    tr += riempi(maschera_poligono(pp, ppm), a_s)
                o["angolo"] = [round(a) for _, a in sez]
            else:
                if ang is None:
                    ang = o["asse"] if o["allung"] >= 1.5 else 45.0
                o["angolo"] = round(ang)
                tr = riempi(poli, ang)
        elif o["punto"] == "satin_pieno":
            tr = satin_pieno(maschera_poligono(u["maschera"], ppm), o["asse"])
        elif o["punto"] == "satin":
            tr = satin_linea(u["maschera"], ppm, oltre=~A["fondo"] & (A["zone"] != o["id"]))
        else:
            tr = filza3_linea(u["maschera"], ppm, o.get("passate", 3))
        # PASSO 27: una zona non deve MAI sparire in silenzio. Il dente sinistro del draghetto (4,4 mm², largo 1,3 mm,
        # gemello del destro) era «satin da linea» per un soffio e satin_linea su una forma così tozza dava zero punti
        if not [x for x in tr if len(x) >= 2] and o["punto"] in ("satin", "filza3") and p_sc != "salta":
            poli = maschera_poligono(u["maschera"], ppm)
            tr = satin_pieno(poli, o["asse"]) if o["area_mm2"] <= 12 else riempi(poli, o["asse"] if o["allung"] >= 1.5 else 45.0)
            o["ripiego"] = True
        # PASSO 13: controllo da digitalizzatore: se il satin lascia scoperta una parte della zona (muso della
        # renna a 50 mm: le guance gonfie fuori dalle «rotaie») si passa al riempimento
        if o["punto"] == "satin" and not p_sc and o["area_mm2"] > 10 and _scoperto(tr, u["maschera"], ppm) > 0.08:
            poli = maschera_poligono(u["maschera"], ppm)
            tr = riempi(poli, o["asse"] if o["allung"] >= 1.5 else 45.0)
        reg = maschera_poligono(u["maschera"], ppm).buffer(-0.25)   # dove posso spostarmi senza tagliare
        pezzi.append((o, [t for t in tr if len(t) >= 2], reg))
    from shapely.prepared import prep
    # dove l'ago può passare senza tagliare: la zona che sto ricamando + tutte quelle che verranno ricamate DOPO
    # (le copriranno: «branching» di Wilcom, «underpath» di Ink/Stitch). Il contorno, ultimo, collega quasi tutto.
    futuri, acc = [None] * len(pezzi), None
    m_acc = np.zeros(A["zone"].shape, bool)
    stringi = max(1, int(round(0.15 * ppm)))
    for k in range(len(pezzi) - 1, -1, -1):
        # «altri» = solo le zone ricamate dopo (coprono davvero); «tutti» = anche quella che sto ricamando
        g_altri, _ = _griglia(ndimage.binary_erosion(m_acc, iterations=stringi), ppm) if m_acc.any() else (None, None)
        reg_altri = acc
        r = pezzi[k][2]
        acc = r if acc is None else (acc if r.is_empty else acc.union(r))
        m_acc |= unita[k]["maschera"]
        futuri[k] = (reg_altri, _griglia(ndimage.binary_erosion(m_acc, iterations=stringi), ppm), g_altri)
    tagli = viaggi = 0
    cambio = True
    cima = np.full(A["zone"].shape, -1, np.int16)          # colore del filo in cima, punto per punto
    for k_p, (o, tratti, _) in enumerate(pezzi):
        reg, (griglia, cella), g_altri = futuri[k_p]
        dentro = prep(reg) if reg is not None and not reg.is_empty else None
        if not tratti:
            continue
        # passo 28: l'ago può scorrere anche sopra i punti già fatti dello STESSO colore che sono ancora in cima
        # (Wilcom «travel on top»): i contorni staccati dello stesso colore si collegano senza taglio
        g_col = None
        fatto = cima == o["colore"]
        if fatto.any():
            g_col, _ = _griglia(ndimage.binary_erosion(fatto, iterations=stringi), ppm)
            griglia = g_col if griglia is None else (griglia | g_col)
        f = A["fili"][o["colore"]]
        if ultimo_col != o["colore"]:
            if ultimo_col is not None:
                out.add_command(pe.COLOR_CHANGE)
            th = pe.EmbThread()
            th.set_hex_color(f["hex"])
            th.description, th.catalog_number = f["nome"], f["num"]
            out.add_thread(th)
            ultimo_col = o["colore"]
            cambio = True                  # il primo salto dopo il cambio colore non lascia fili: non è un taglio a mano
        # ordine dei tratti: il più vicino a dove sono
        rest = list(tratti)
        while rest:
            if pos is None:
                j = 0
            else:
                j = min(range(len(rest)), key=lambda q: min(np.hypot(*(np.array(rest[q][0]) - pos)), np.hypot(*(np.array(rest[q][-1]) - pos))))
            t = rest.pop(j)
            if pos is not None and np.hypot(*(np.array(t[-1]) - pos)) < np.hypot(*(np.array(t[0]) - pos)) and o["punto"] in ("satin", "filza3"):
                t = t[::-1]
            x, y = t[0]
            dist = np.inf if pos is None else np.hypot(x - pos[0], y - pos[1])
            if dist <= 3.0:                               # salto corto: un punto solo, niente taglio (come Ink/Stitch)
                pts = t
            elif pos is not None and (via := percorso_interno(dentro, griglia, cella, tuple(pos), (x, y), coperto=g_altri, stesso=g_col)):
                # percorso nascosto dentro la stessa zona (dritto o lungo il bordo): lo coprono i punti dopo
                prec = tuple(pos)
                for q in via:
                    d_q = np.hypot(q[0] - prec[0], q[1] - prec[1])
                    n_v = max(1, int(np.ceil(d_q / 2.5)))
                    for s in range(1, n_v + 1):
                        out.add_stitch_absolute(pe.STITCH, (prec[0] + (q[0] - prec[0]) * s / n_v) * 10, (prec[1] + (q[1] - prec[1]) * s / n_v) * 10)
                    prec = q
                pts = t[1:]
                viaggi += 1
            else:
                out.add_stitch_absolute(pe.TRIM, x * 10, y * 10)
                out.add_stitch_absolute(pe.JUMP, x * 10, y * 10)
                pts = _fermo(t)
                tagli += 0 if cambio else 1
            for x, y in pts:
                out.add_stitch_absolute(pe.STITCH, x * 10, y * 10)
            pos = np.array(t[-1])
            cambio = False
        cima[unita[k_p]["maschera"]] = o["colore"]
    out.add_command(pe.END)
    n = sum(1 for s in out.stitches if (s[2] & 0xFF) == pe.STITCH)
    x0, y0, x1, y1 = out.bounds()
    return out, {"punti": n, "colori": len(out.threadlist), "tagli": tagli, "viaggi": viaggi, "larg": round((x1 - x0) / 10, 1), "alt": round((y1 - y0) / 10, 1),
                 "unita": [(o["id"], o["punto"], o["livello"], A["fili"][o["colore"]]["num"]) for o, _, _ in pezzi]}


# ---------------------------------------------------------------- foglio di controllo dell'analisi
def foglio_analisi(A, titolo):
    from PIL import ImageDraw
    rgb, z, info, pal = A["rgb"], A["zone"], A["info"], A["palette"]
    H, W = z.shape
    piatta = np.full((H, W, 3), 255, np.uint8)
    for o in info:
        piatta[z == o["id"]] = pal[o["colore"]]
    tipo = np.full((H, W, 3), 255, np.uint8)
    T = {"fill": (70, 110, 230), "satin": (230, 60, 60), "filza3": (40, 160, 60), "satin_pieno": (240, 150, 30)}
    for o in info:
        tipo[z == o["id"]] = T[o["punto"]]
    S = 520
    tav = Image.new("RGB", (S * 3 + 40, S + 230), "white")
    for k, a in enumerate((rgb, piatta, tipo)):
        im = Image.fromarray(a)
        im.thumbnail((S, S))
        tav.paste(im, (10 + k * (S + 10), 40))
    d = ImageDraw.Draw(tav)
    d.text((10, 10), f"{titolo}   originale | colori ridotti ({len(pal)}) | punto: blu riempimento, rosso satin, arancio satin pieno, verde filza tripla", fill="black")
    y = S + 50
    for k, (c, f) in enumerate(zip(pal, A["fili"])):
        d.rectangle([10, y, 34, y + 16], fill=tuple(c))
        d.rectangle([40, y, 64, y + 16], fill=f["hex"])
        n = sum(1 for o in info if o["colore"] == k)
        d.text((72, y + 2), f"{f['originale']} -> filo {f['num']} {f['nome']}  ({n} zone)", fill="black")
        y += 22
    x = 560
    y = S + 50
    for o in sorted(info, key=lambda o: -o["area_mm2"])[:9]:
        d.text((x, y), f"zona {o['id']}: {o['punto']:6s} area {o['area_mm2']:.0f} mm², spessore max {o['spessore_max']:.1f}, "
                       f"larg tipica {o['larg_tipica']:.1f}, scheletro {o['lung_scheletro']:.0f} mm", fill="black")
        y += 18
    return tav


if __name__ == "__main__":
    src = sys.argv[1]
    alt = float(sys.argv[2]) if len(sys.argv) > 2 else 100
    dest = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(QUI), "04_anteprima", "digitalizza")
    os.makedirs(dest, exist_ok=True)
    A = analizza(src, alt)
    nome = os.path.splitext(os.path.basename(src))[0]
    foglio_analisi(A, f"{nome}  {alt:.0f} mm").save(os.path.join(dest, f"{nome}_analisi.png"))
    print(nome, "colori", len(A["palette"]), "zone", len(A["info"]),
          {p: sum(1 for o in A["info"] if o["punto"] == p) for p in ("fill", "satin", "satin_pieno", "filza3")})
    import pyembroidery as pe
    from anteprima_bella import render
    pat, inf = digitalizza(A)
    x0, y0, _, _ = pat.bounds()
    pat.translate(-x0, -y0)                                   # angolo in alto a sinistra a 0,0 (Brother)
    pe.write_pes(pat, os.path.join(dest, f"{nome}.pes"), {"pes version": 6})
    S = 900
    tav = Image.new("RGB", (S * 2 + 30, S + 50), "white")
    im = Image.fromarray(A["rgb"])
    im.thumbnail((S, S))
    tav.paste(im, (10, 40))
    tav.paste(render(pat, S, ss=2), (S + 20, 40))
    from PIL import ImageDraw
    ImageDraw.Draw(tav).text((10, 10), f"{nome}: {inf['larg']} x {inf['alt']} mm, {inf['punti']} punti, {inf['colori']} colori", fill="black")
    tav.save(os.path.join(dest, f"{nome}_ricamo.png"))
    print(inf["larg"], "x", inf["alt"], "mm", inf["punti"], "punti", inf["colori"], "colori")
    for u in inf["unita"]:
        print("  ", u)
