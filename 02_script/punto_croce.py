"""Stile «punto croce» del digitalizzatore: griglia di croci + schema A4 da stampare per il ricamo a mano.

Come i punto croce commerciali dell'archivio (misurati il 27/11/2026: PONTO-CRUZ BABY/FLORAIS/CASAS, Hus 171000):
- quadretto 1,6-2,5 mm (tipico 1,8-2,0), solo diagonali a 45°, ogni gamba ripassata 3 volte, nessuna imbottitura;
- l'ago entra da un angolo, fa la prima gamba, torna al CENTRO (mezza diagonale nascosta sotto la croce), va
  all'angolo di partenza della seconda gamba e la finisce nell'angolo che il quadretto ha in comune col prossimo:
  una riga dello stesso colore è una catena senza salti;
- linee sottili e contorno: punto filza triplo sopra le croci («backstitch»).
"""
import math
from collections import deque

import numpy as np
from scipy import ndimage

import digitalizza as DG

# angoli del quadretto: 0=TL 1=TR 2=BR 3=BL (in unità di griglia: (dx, dy))
ANG = {0: (0, 0), 1: (1, 0), 2: (1, 1), 3: (0, 1)}
OPP = {0: 2, 1: 3, 2: 0, 3: 1}
LATO = {0: (1, 3), 1: (0, 2), 2: (1, 3), 3: (0, 2)}     # angoli sullo stesso lato (né uguale né opposto)


def _rdp(P, eps):
    """Douglas-Peucker: tiene solo i punti che servono a restare entro eps dalla linea."""
    if len(P) < 3:
        return list(P)
    (x1, y1), (x2, y2) = P[0], P[-1]
    L = math.hypot(x2 - x1, y2 - y1)
    if L < 1e-6:                                 # anello chiuso: distanza dal punto di partenza
        d = [math.hypot(x - x1, y - y1) for x, y in P[1:-1]]
    else:
        d = [abs((x2 - x1) * (y1 - y) - (x1 - x) * (y2 - y1)) / L for x, y in P[1:-1]]
    k = int(np.argmax(d)) + 1
    if d[k - 1] <= eps:
        return [P[0], P[-1]]
    return _rdp(P[:k + 1], eps)[:-1] + _rdp(P[k:], eps)


def _linea_morbida(P, ox, oy, s, passo=1.2):
    """Linea in mm -> curva morbida (in quadretti, con la virgola): via il seghettato dei pixel, angoli
    arrotondati, punti ogni ~passo mm. Per la macchina la linea non deve passare dai buchi della tela."""
    Q = _rdp([((x - ox) / s, (y - oy) / s) for x, y in P], 0.07)
    chiuso = len(Q) > 3 and math.dist(Q[0], Q[-1]) < 1e-6
    for _ in range(2):                           # Chaikin: tagli gli angoli a 1/4 e 3/4
        if len(Q) < 3:
            break
        N = [Q[0]]
        for a, b in zip(Q, Q[1:]):
            N += [(0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]),
                  (0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1])]
        N.append(Q[-1])
        Q = N
    if chiuso:
        Q[-1] = Q[0]
    out = [Q[0]]                                 # ricampiona: punti ~passo mm (né troppo corti né lunghi)
    acc = 0.0
    for a, b in zip(Q, Q[1:]):
        L = math.dist(a, b) * s
        acc += L
        if acc >= passo:
            out.append(b)
            acc = 0.0
    if out[-1] != Q[-1]:
        if len(out) > 1 and math.dist(out[-1], Q[-1]) * s < passo / 2:
            out[-1] = Q[-1]
        else:
            out.append(Q[-1])
    return out


def _linea_liscia(P, ox, oy, s, max_q=3):
    """Linea in mm -> punto indietro da buco a buco della tela (angoli dei quadretti) a tratti LUNGHI, come nelle
    schede dei negozi: si semplifica la curva, si aggancia ai buchi, si tolgono i punti inutili. Tratti al massimo
    di max_q quadretti (a mano un punto più lungo si allenta). -> [nodi (colonna, riga)]"""
    Q = [((x - ox) / s, (y - oy) / s) for x, y in P]
    Q = _rdp(Q, 0.35)
    nodi = []
    for u, v in Q:
        n = (round(u), round(v))
        if not nodi or n != nodi[-1]:
            nodi.append(n)
    if len(nodi) >= 3:                           # i buchi agganciati: di nuovo solo quelli che servono
        nodi = [tuple(n) for n in _rdp([tuple(map(float, n)) for n in nodi], 0.5)]
        nodi = [(int(u), int(v)) for u, v in nodi]
    # come i punto croce commerciali (Reindeer-18 della raccolta): solo tratti dritti e a 45°. Una pendenza
    # qualsiasi diventa mezzo tratto dritto + diagonale + mezzo tratto dritto
    a45 = nodi[:1]
    for n in nodi[1:]:
        a = a45[-1]
        dx, dy = n[0] - a[0], n[1] - a[1]
        dg = min(abs(dx), abs(dy))
        sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
        rx, ry = dx - sx * dg, dy - sy * dg     # resto dritto (uno dei due è 0)
        h = (rx // 2 if rx >= 0 else -((-rx) // 2), ry // 2 if ry >= 0 else -((-ry) // 2))
        for p in ((a[0] + h[0], a[1] + h[1]), (a[0] + h[0] + sx * dg, a[1] + h[1] + sy * dg), n):
            if p != a45[-1]:
                a45.append(p)
    nodi = [a45[0]]                              # via i nodi in mezzo a un tratto dritto
    for k in range(1, len(a45)):
        if len(nodi) >= 2 and k < len(a45):
            p, q = nodi[-2], nodi[-1]
            d1 = (q[0] - p[0], q[1] - p[1])
            d2 = (a45[k][0] - q[0], a45[k][1] - q[1])
            if d1[0] * d2[1] == d1[1] * d2[0] and d1[0] * d2[0] + d1[1] * d2[1] > 0:
                nodi[-1] = a45[k]
                continue
        nodi.append(a45[k])
    out = nodi[:1]
    for n in nodi[1:]:                           # tratti troppo lunghi: un buco intermedio sulla retta
        a = out[-1]
        k = math.ceil(max(abs(n[0] - a[0]), abs(n[1] - a[1])) / max_q)
        for t in range(1, k):
            out.append((round(a[0] + (n[0] - a[0]) * t / k), round(a[1] + (n[1] - a[1]) * t / k)))
        out.append(n)
    return out


def griglia(A, cella=2.0, scelte=None, bordo="croci"):
    """-> dict: G (righe×colonne, indice colore o -1), origine in mm, cella, linee [(colore, tratti mm)], ordine colori.
    bordo="croci": i contorni spessi diventano una fila di croci scure; "indietro": punto indietro fine sul contorno."""
    DG.applica_scelte(A, scelte)
    DG._livelli(A["info"])
    ppm = A["ppm"]
    H, W = A["zone"].shape
    # colore VISIBILE di ogni pixel (non le maschere di ricamo, che si allargano sotto le zone sopra):
    # così i quadretti seguono il disegno e non escono dal contorno
    vis = np.full((H, W), -1, np.int32)
    spessa = np.zeros((H, W), bool)             # contorni spessi (≥ ~mezzo quadretto): diventano croci scure
    linee, ordine = [], []
    piene = set()                               # zone fatte a croci (non le linee)

    def _piena(o):
        return o["punto"] in ("fill", "satin_pieno") or (o["punto"] == "satin" and not o["tocca_sfondo"]
                                                         and o.get("larg_tipica", 0) >= 1.2 * cella)
    # colore del contorno principale: quello delle zone-linea con più area (il «nero» del disegno)
    area_lin = {}
    for o in A["info"]:
        if not _piena(o):
            area_lin[o["colore"]] = area_lin.get(o["colore"], 0) + int((A["zone"] == o["id"]).sum())
    col_contorno = max(area_lin, key=area_lin.get) if area_lin else None
    for o in sorted(A["info"], key=lambda o: o.get("livello", 0)):
        if (o.get("scelta") or {}).get("punto") == "salta":
            continue
        m = DG.maschera_zona(A["zone"], o)
        if not m.any():
            continue
        piena = _piena(o)
        # striscia larga quasi un quadretto che non è il contorno (ciuffo di capelli): a croci, non una riga
        # (ma una scritta o un disegnino sopra un colore solo, come «Buon Natale» sul nastro, resta una riga)
        # pallino (pupilla, nasino) di un colore che non è il contorno: sempre un'area, anche piccolissimo
        if not piena and bordo != "croci" and o["colore"] != col_contorno and DG._compatto(m):
            piena = True
        if not piena and bordo != "croci" and o["colore"] != col_contorno:
            anello = ndimage.binary_dilation(m, iterations=max(2, int(0.3 * ppm))) & ~m
            tutto = A["etichette"][anello]
            zz = tutto[(tutto >= 0) & (tutto != col_contorno) & (tutto != o["colore"])]
            # chiusa tutt'intorno dal contorno: è un'area anche se stretta (triangolino di maglione tra le linee)
            if zz.size < 0.1 * max(1, tutto.size):
                piena = True
            elif zz.size and o.get("larg_tipica", 0) >= 0.9 * cella:
                quote = np.bincount(zz) / zz.size
                if (quote >= 0.15).sum() >= 2:
                    piena = True
        if piena or (bordo == "croci" and o.get("larg_tipica", 0) >= 0.55 * cella):
            piene.add(o["id"])
            vis[m] = o["colore"]
            if not piena:
                spessa |= m
        else:                                   # linea sottile: punto indietro sopra, sotto continua il fondo
            vis[m] = -2
            linee.append((o["colore"], m, o["tocca_sfondo"]))
    if not (vis != -1).any():
        return None
    era_linea = vis == -2                        # pixel del contorno/linee nel disegno
    if (vis == -2).any() and (vis >= 0).any():
        # sotto le linee: quel che c'è di fianco (colore o fondo). Metà linea va al pieno, metà al fondo:
        # il bordo delle croci cade sul centro del contorno, dove passa il punto indietro
        _, (iy, ix) = ndimage.distance_transform_edt(vis == -2, return_indices=True)
        vis = np.where(vis == -2, vis[iy, ix], vis)
    # luci piccole quasi del colore del fondo (puntino bianco negli occhi): restano stoffa libera
    cp = cella * ppm
    rgb_fondo = A["rgb"][A["fondo"]].mean(axis=0) if A["fondo"].any() else np.array([255, 255, 255])
    luci = []
    for o in A["info"]:
        m = DG.maschera_zona(A["zone"], o)
        media = A["rgb"][m].mean(axis=0) if m.any() else None
        # riflesso = quasi del colore del fondo E più vicino al fondo che al suo colore (l'interno panna di una
        # «a» è panna vero, non un riflesso)
        if 0 < m.sum() < 4 * cp * cp and np.abs(media - rgb_fondo).max() < 40                 and np.abs(media - rgb_fondo).sum() < np.abs(media - np.asarray(A["palette"][o["colore"]], float)).sum():
            vis[m] = -1
            luci.append(o["id"])
    buchi = ndimage.binary_fill_holes(vis != -1) & (vis == -1)
    ys, xs = np.nonzero(vis != -1)
    x0, y0 = xs.min(), ys.min()
    nx = int(math.ceil((xs.max() + 1 - x0) / cp))
    ny = int(math.ceil((ys.max() + 1 - y0) / cp))
    ox, oy = x0 / ppm, y0 / ppm
    # punto indietro: prima la linea definitiva (tratti lunghi da buco a buco), poi le croci solo dentro
    percorsi = []                                # [(colore, [nodi])]
    pal = np.asarray(A["palette"], float)
    for c, m, bordo_est in linee:
        # linea quasi del colore della zona su cui sta (sfumature tra due colori, ciuffi chiari): non si vede,
        # ricamata fa solo sporco
        sotto = vis[m & (vis >= 0)]
        if not bordo_est and sotto.size:
            cs = int(np.bincount(sotto).argmax())
            if np.abs(pal[c] - pal[cs]).sum() < 120:
                continue
        rami, _ = DG._percorsi_scheletro(m)     # rami interi (con i capi agli incroci): la linea resta chiusa
        capi = [tuple(r[0]) for r in rami if len(r) >= 2] + [tuple(r[-1]) for r in rami if len(r) >= 2]

        def libero(pt):                          # capo che non tocca nessun altro ramo
            return sum(1 for q in capi if abs(q[0] - pt[0]) <= 3 and abs(q[1] - pt[1]) <= 3) <= 1
        for r in rami:
            if len(r) < 2:
                continue
            # codini dello scheletro dove il contorno fa angolo (incroci delle corna): rami corti con un capo
            # libero e l'altro attaccato a un incrocio
            # (solo sui contorni esterni: nelle scritte un ramo corto è la stanghetta della «t»)
            if bordo_est and len(r) < 4.0 * ppm and (libero(tuple(r[0])) != libero(tuple(r[-1]))):
                continue
            Pm = [(x / ppm, y / ppm) for y, x in r]
            nodi = _linea_morbida(Pm, ox, oy, cella) if bordo == "morbido" else _linea_liscia(Pm, ox, oy, cella)
            if len(nodi) >= 2:
                percorsi.append((c, nodi))
    fuori = np.zeros((H, W), bool)               # fondo fuori dalle linee (collegato al bordo dell'immagine)
    if percorsi:
        from PIL import Image as _Im, ImageDraw as _Dr
        tela = _Im.new("1", (W, H), 0)
        dr = _Dr.Draw(tela)
        for _, nodi in percorsi:
            dr.line([(x0 + u * cp, y0 + v * cp) for u, v in nodi], fill=1, width=max(2, int(ppm * 0.25)))
        # pezzi di stoffa separati dalle linee: «fuori» quelli fatti per lo più di fondo (se una linea non chiude,
        # il pezzo resta misto e non si toglie niente)
        # la linea si ingrossa di ~mezzo quadretto solo per chiudere i piccoli buchi agli incroci (agganciando
        # ai buchi della tela i rami non si toccano sempre)
        lin = np.array(tela)
        cornice = np.zeros_like(lin)            # il bordo dell'immagine chiude il disegno tagliato
        cornice[:2, :] = cornice[-2:, :] = cornice[:, :2] = cornice[:, -2:] = True
        dlin = ndimage.distance_transform_edt(~lin) / ppm   # mm dalla linea: lì sotto i collegamenti non si vedono
        lin_g = ndimage.binary_dilation(lin, iterations=max(1, int(cp * 0.5)))
        lab, n = ndimage.label(~(lin_g | cornice))
        tot = np.bincount(lab.ravel(), minlength=n + 1)
        bg = np.bincount(lab.ravel(), weights=(vis == -1).ravel(), minlength=n + 1)
        es = (bg > 0.6 * np.maximum(tot, 1))
        # disegno tagliato sul bordo dell'immagine (contorno che tocca il bordo): la striscetta tra la linea e il
        # bordo è fuori anche se non ha fondo
        sul_bordo = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
        es[sul_bordo[tot[sul_bordo] < 30 * cp * cp]] = True
        # pezzo sul bordo fatto quasi solo di contorno (la striscetta sotto il mento): è fuori
        vicino_bordo = np.unique(lab[ndimage.binary_dilation(cornice, iterations=3) & (lab > 0)])
        cont = np.bincount(lab.ravel(), weights=era_linea.ravel(), minlength=n + 1)
        for L in vicino_bordo:
            if (bg[L] + cont[L]) > 0.6 * tot[L]:
                es[L] = True
        es[0] = False
        _, (iy, ix) = ndimage.distance_transform_edt(lab == 0, return_indices=True)
        lab = lab[iy, ix]                        # sotto la linea ingrossata: il pezzo più vicino
        fuori = es[lab] & ~buchi & ~lin
        # striscetta tra la linea e il bordo dell'immagine (disegno tagliato): collegata alla cornice senza
        # passare la linea, vicina al bordo e fatta di contorno o fondo
        lab2, _ = ndimage.label(~lin)
        fuori_c = np.isin(lab2, np.unique(lab2[cornice & ~lin]))
        vic_b = ~ndimage.binary_erosion(np.ones_like(lin), iterations=max(2, int(1.2 * cp)), border_value=0)
        fuori |= fuori_c & vic_b & (era_linea | (vis == -1)) & ~lin
    G = np.full((ny, nx), -1, np.int32)
    tq = []                                      # punti 3/4 dove la diagonale della linea taglia il quadretto
    parz = []                                    # bordo morbido: croci con le gambe accorciate fino alla linea
    if (vis >= 0).any():                         # per i quadretti dentro la linea ma bianchi nel disegno
        dist, (iy, ix) = ndimage.distance_transform_edt(vis < 0, return_indices=True)
        vic = vis[iy, ix]
    for i in range(ny):
        for j in range(nx):
            a, b = int(round(y0 + i * cp)), int(round(y0 + (i + 1) * cp))
            c, d = int(round(x0 + j * cp)), int(round(x0 + (j + 1) * cp))
            blocco = vis[a:b, c:d]
            v = blocco[blocco >= 0]
            if buchi[a:b, c:d].sum() >= 0.35 * blocco.size:
                continue
            fu = fuori[a:b, c:d].sum() / blocco.size

            def colore():
                if not v.size:
                    return int(np.bincount(vic[a:b, c:d].ravel()).argmax())
                sp = spessa[a:b, c:d] & (blocco >= 0)
                if sp.sum() >= 0.3 * blocco.size:   # il contorno vince: resta continuo anche se sottile
                    return int(np.bincount(blocco[sp]).argmax())
                return int(np.bincount(v).argmax())
            if not percorsi:
                if v.size >= 0.5 * blocco.size:  # croce se il quadretto è disegnato almeno per metà
                    G[i, j] = colore()
                continue
            if bordo == "morbido":
                # quadretto che esce dall'immagine o è per metà fuori dalla linea: niente
                area_c = cp * cp
                fu_c = (fuori[a:b, c:d].sum() + max(0.0, area_c - blocco.size)) / area_c
                if fu_c > 0.5:
                    continue
                # le linee dividono la stoffa in pezzi (lab). Quadretto in un pezzo solo: croce intera (o niente
                # se è fuori). Quadretto tagliato da una linea (esterna o interna): ogni gamba si ferma sulla linea
                # e ogni pezzetto prende il colore del suo lato
                labs = lab[a:b, c:d]
                pezzi = np.unique(labs)
                if len(pezzi) == 1 and fu_c <= 0.1:
                    if not es[pezzi[0]] and (v.size >= 0.5 * blocco.size or dist[a:b, c:d].min() <= 0.8 * cp):
                        G[i, j] = colore()
                    continue
                col_di, area = {}, {}
                for L in pezzi:
                    if es[L]:
                        continue
                    sel = labs == L
                    vv_ = blocco[sel & (blocco >= 0)]
                    cc = int(np.bincount(vv_).argmax()) if vv_.size else int(np.bincount(vic[a:b, c:d][sel]).argmax())
                    col_di[L] = cc
                    area[cc] = area.get(cc, 0) + sel.sum() / blocco.size
                if not col_di or not (v.size or dist[a:b, c:d].min() <= 0.8 * cp):
                    continue
                per_col = {}
                N = 24
                for k0, k1 in ((0, 2), (1, 3)):
                    A0 = (j + ANG[k0][0], i + ANG[k0][1])
                    A1 = (j + ANG[k1][0], i + ANG[k1][1])
                    pt = [(A0[0] + (A1[0] - A0[0]) * t / N, A0[1] + (A1[1] - A0[1]) * t / N) for t in range(N + 1)]
                    cols = []
                    for uu, vv in pt:
                        py_, px_ = int(y0 + vv * cp), int(x0 + uu * cp)
                        if not (0 <= py_ < H and 0 <= px_ < W) or fuori[py_, px_]:
                            cols.append(-1)                # fuori dall'immagine o oltre la linea
                            continue
                        cols.append(col_di.get(lab[py_, px_], -1))
                    t0 = 0
                    for t in range(1, N + 2):    # pezzi di gamba dello stesso colore
                        if t == N + 1 or cols[t] != cols[t0]:
                            if cols[t0] >= 0 and t - 1 - t0 >= 0.25 * N:
                                per_col.setdefault(cols[t0], []).append((pt[t0], pt[t - 1]))
                            t0 = t
                for cc, segs in per_col.items():
                    parz.append((i, j, cc, segs, area.get(cc, 0) >= 0.5))
                continue
            if fu <= 0.08:
                # dentro la linea: croce anche se il disegno lì è bianco (la linea agganciata ai buchi può
                # stare un po' più in fuori del disegno), ma solo vicino al disegno
                if v.size >= 0.5 * blocco.size or dist[a:b, c:d].min() <= 0.8 * cp:
                    G[i, j] = colore()
                continue
            if fu > 0.65:
                continue
            # tagliato dalla linea: se il «fuori» sta nell'angolo opposto, punto 3/4 verso l'angolo dentro
            hm, wm = (a + b) // 2, (c + d) // 2
            q = [fuori[a:hm, c:wm].mean(), fuori[a:hm, wm:d].mean(), fuori[hm:b, wm:d].mean(), fuori[hm:b, c:wm].mean()]
            k = int(np.argmin(q))
            if q[k] < 0.08 and q[OPP[k]] > 0.5:
                tq.append((i, j, colore(), k))
            elif fu <= 0.15:
                G[i, j] = colore()
    for c in [o["colore"] for o in sorted(A["info"], key=lambda o: o.get("livello", 0))]:   # contorno per ultimo
        if c not in ordine and (G == c).any():
            ordine.append(c)
    # i dettagli più piccoli di un quadretto (occhi, naso) non devono sparire: almeno una croce al loro centro
    for o in A["info"]:
        luce = o["id"] in luci
        if not luce and o["id"] not in piene:    # le linee non diventano croci (es. il contorno del naso)
            continue
        if (o["colore"] not in ordine and not luce) or (o.get("scelta") or {}).get("punto") == "salta":
            continue
        m = DG.maschera_zona(A["zone"], o)
        if not m.any():
            continue
        yy, xx = np.nonzero(m)
        i0, i1 = int((yy.min() - y0) // cp), int((yy.max() - y0) // cp)
        j0, j1 = int((xx.min() - x0) // cp), int((xx.max() - x0) // cp)
        if (G[max(0, i0):i1 + 1, max(0, j0):j1 + 1] == (-1 if luce else o["colore"])).any():
            continue
        i, j = int((yy.mean() - y0) // cp), int((xx.mean() - x0) // cp)
        if 0 <= i < ny and 0 <= j < nx:
            G[i, j] = -1 if luce else o["colore"]
    # croci che sporgono sul bordo esterno (attaccate al disegno da un lato solo): via, il bordo resta pulito
    for _ in range(2):
        g = G >= 0
        for i, j, _c, _k in tq:
            g[i, j] = True
        for i, j, *_r in parz:
            g[i, j] = True
        pieno = np.pad(g, 1)
        vicini = pieno[:-2, 1:-1].astype(int) + pieno[2:, 1:-1] + pieno[1:-1, :-2] + pieno[1:-1, 2:]
        G[(G >= 0) & (vicini <= 1)] = -1
    per_c = {}
    for c, nodi in percorsi:
        P = [(ox + u * cella, oy + v * cella) for u, v in nodi]
        fitti = [P[0]]                           # per la macchina: punti lunghi divisi (max 3 mm), stessa retta
        for p in P[1:]:
            q = fitti[-1]
            k = max(1, math.ceil(math.dist(q, p) / 3.0))
            fitti += [(q[0] + (p[0] - q[0]) * t / k, q[1] + (p[1] - q[1]) * t / k) for t in range(1, k + 1)]
        b = [fitti[0]]                           # filza tripla avanti-indietro-avanti
        for p in fitti[1:]:
            b += [p, b[-1], p]
        per_c.setdefault(c, []).append(b)
    tratti = list(per_c.items())
    tq = [t for t in tq if G[t[0], t[1]] < 0]
    for _i, _j, c, _k in tq:
        if c not in ordine:
            ordine.append(c)
    for _i, _j, c, *_r in parz:
        if c not in ordine:
            ordine.append(c)
    usati = {c for c in ordine if (G == c).any() or any(t[2] == c for t in tq + parz)}
    return {"G": G, "ox": x0 / ppm, "oy": y0 / ppm, "cella": cella, "linee": tratti, "tq": tq, "parz": parz,
            "dlin": dlin if percorsi else None, "x0px": x0, "y0px": y0, "ppm": ppm, "era_linea": era_linea,
            # i colori che hanno anche linee per ultimi: croci e linee di quel colore senza cambiare filo due volte
            "ordine": sorted([c for c in ordine if c in usati], key=lambda c: any(cc == c for cc, _ in tratti))}


def _isole(gr):
    """Isole di un colore (quadretti che si toccano anche solo d'angolo: lì l'ago passa senza salti)."""
    lab, n = ndimage.label(gr, structure=np.ones((3, 3)))
    return [set(zip(*(v.tolist() for v in np.nonzero(lab == k)))) for k in range(1, n + 1)]


def croci(R, A, passate=3):
    """Griglia -> EmbPattern (croci per colore + linee a filza tripla sopra)."""
    import pyembroidery as pe
    from componi import _fermo
    G, ox, oy, s = R["G"], R["ox"], R["oy"], R["cella"]

    def mm(j, i, k):                    # angolo k del quadretto (riga i, colonna j) in mm
        dx, dy = ANG[k]
        return (ox + (j + dx) * s, oy + (i + dy) * s)

    def centro(j, i):
        return (ox + (j + 0.5) * s, oy + (i + 0.5) * s)

    def angoli_di(i, j):
        return {(j + ANG[k][0], i + ANG[k][1]): k for k in range(4)}

    def celle_attorno(nodo):
        x, y = nodo
        return ((y - 1, x - 1), (y - 1, x), (y, x - 1), (y, x))

    out = pe.EmbPattern()
    pos = None                          # angolo dove sta l'ago (nodo di griglia)
    corse = []                          # [(colore, [punti mm])]: tra una corsa e l'altra si taglia

    for c in R["ordine"]:
        fatti = set()

        def viaggio(da, arrivo):
            """Percorso nascosto sopra le croci già fatte di questo colore (angolo -> centro -> angolo, sempre
            sulle loro diagonali) fino al primo angolo per cui arrivo(angolo) è vero."""
            prec = {da: None}
            q = deque([da])
            while q:
                n = q.popleft()
                if arrivo(n):
                    via = []
                    while prec[n] is not None:
                        via.append((n, prec[n][1]))
                        n = prec[n][0]
                    return via[::-1]
                for cel in celle_attorno(n):
                    if cel in fatti:
                        for nn in angoli_di(*cel):
                            if nn not in prec:
                                prec[nn] = (n, cel)
                                q.append(nn)
            return None

        isole = _isole(G == c)
        while isole:
            if pos is not None:
                isole.sort(key=lambda U: min(abs(j - pos[0]) + abs(i - pos[1]) for i, j in U))
            U = isole.pop(0)
            corsa = (c, [])
            corse.append(corsa)
            p = corsa[1]

            def liberi_vicini(cel):     # Warnsdorff: prima le croci con meno vicine da fare (niente buchi isolati)
                i, j = cel
                return sum((i + a, j + b) in U for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b)

            i, j = min(U)               # dall'alto a sinistra
            e, verso = None, 1
            while True:
                U.discard((i, j))
                # prossima croce: una da fare che tocca uno degli angoli d'uscita possibili (stesso lato
                # dell'ingresso); prima la stessa riga nello stesso verso, poi la stessa riga, poi le altre
                migliore = None
                for x in (LATO[e] if e is not None else (0, 1, 2, 3)):
                    nodo = (j + ANG[x][0], i + ANG[x][1])
                    for cel in celle_attorno(nodo):
                        if cel in U:
                            ni, nj = cel
                            sc = (0 if (ni == i and nj - j == verso) else 1 if ni == i else 2, liberi_vicini(cel))
                            if migliore is None or sc < migliore[0]:
                                migliore = (sc, x, cel)
                if e is None:
                    x = migliore[1] if migliore else 1
                    e = LATO[x][0]
                    p.append(mm(j, i, e))
                else:
                    x = migliore[1] if migliore else LATO[e][0]
                a, b = e, OPP[e]
                for t in range(passate):                       # prima gamba
                    p.append(mm(j, i, b if t % 2 == 0 else a))
                p.append(centro(j, i))                         # mezza diagonale nascosta sotto la croce
                a, b = (OPP[x], x) if passate % 2 else (x, OPP[x])
                p.append(mm(j, i, a))
                for t in range(passate):                       # seconda gamba, finisce sull'uscita
                    p.append(mm(j, i, b if t % 2 == 0 else a))
                fatti.add((i, j))
                pos = (j + ANG[x][0], i + ANG[x][1])
                if migliore:
                    ni, nj = migliore[2]
                    verso = (1 if nj > j else -1) if ni == i else -verso
                    i, j = ni, nj
                    e = angoli_di(i, j)[pos]
                    continue
                if not U:
                    break
                via = viaggio(pos, lambda n: any(cel in U for cel in celle_attorno(n)))
                if via is None:                                # non dovrebbe capitare in un'isola: nuova corsa
                    isole.append(U)
                    break
                for n, (ci, cj) in via:
                    p.append(centro(cj, ci))
                    p.append((ox + n[0] * s, oy + n[1] * s))
                    pos = n
                i, j = min((cel for cel in celle_attorno(pos) if cel in U), key=liberi_vicini)
                e = angoli_di(i, j)[pos]
                verso = -verso
        # punti 3/4 di questo colore: mezza croce lungo la linea (×3) + quarto verso l'angolo dentro (×3)
        resto = [t for t in R.get("tq", []) if t[2] == c]
        while resto:
            qui = pos if pos is not None else (resto[0][1], resto[0][0])
            resto.sort(key=lambda t: abs(t[1] - qui[0]) + abs(t[0] - qui[1]))
            i, j, _c, k = resto.pop(0)
            ang_n = {(j + ANG[n][0], i + ANG[n][1]): n for n in range(4)}
            # ci si arriva passando nascosti sulle croci già fatte di questo colore (niente taglio)
            via = viaggio(pos, lambda n: n in ang_n) if pos is not None and fatti else None
            if via is not None and corse and corse[-1][0] == c:
                p = corse[-1][1]
                for n, (ci, cj) in via:
                    p.append(centro(cj, ci))
                    p.append((ox + n[0] * s, oy + n[1] * s))
                    pos = n
                da = ang_n[pos]
            else:
                p = []
                corse.append((c, p))
                da = None
            s1, s2 = LATO[k]
            if da == k:                          # dall'angolo dentro: prima il quarto, poi la mezza croce
                p += [mm(j, i, k), centro(j, i), mm(j, i, k), centro(j, i),
                      mm(j, i, s1), mm(j, i, s2), mm(j, i, s1), mm(j, i, s2)]
                pos = (j + ANG[s2][0], i + ANG[s2][1])
            else:
                if da == s2:
                    s1, s2 = s2, s1
                p += [mm(j, i, s1), mm(j, i, s2), mm(j, i, s1), mm(j, i, s2), centro(j, i),
                      mm(j, i, k), centro(j, i), mm(j, i, k)]
                pos = (j + ANG[k][0], i + ANG[k][1])
        # croci accorciate (bordo morbido): ogni gamba solo nel pezzo dentro la linea, ×3; dalla croce prima
        # ci si sposta dritti (corto, sopra le croci dello stesso colore) o con salto/taglio se lontano
        # niente collegamenti a vista: si entra da un angolo arrivando sotto le croci già fatte, si passa da una
        # gamba all'altra dal centro (sopra la gamba appena fatta) e si esce da un angolo
        resto = [t for t in R.get("parz", []) if t[2] == c]

        def g2mm(pt):
            return (ox + pt[0] * s, oy + pt[1] * s)

        def contiene(sg, pt):
            (x1, y1), (x2, y2) = sg
            cr = (x2 - x1) * (pt[1] - y1) - (y2 - y1) * (pt[0] - x1)
            dt = (pt[0] - x1) * (x2 - x1) + (pt[1] - y1) * (y2 - y1)
            return abs(cr) < 1e-6 and -1e-6 <= dt <= (x2 - x1) ** 2 + (y2 - y1) ** 2 + 1e-6

        dl = R.get("dlin")

        def sotto_linea(p0, p1):
            """Il collegamento p0->p1 (quadretti) corre sotto la linea che si ricama dopo: non si vedrà."""
            if dl is None or p0 is None or math.dist(p0, p1) > 2.6:
                return False
            cpx = s * R["ppm"]
            n = max(2, int(math.dist(p0, p1) * s / 0.2))
            for t in range(n + 1):
                u = p0[0] + (p1[0] - p0[0]) * t / n
                v = p0[1] + (p1[1] - p0[1]) * t / n
                py = min(dl.shape[0] - 1, max(0, int(R["y0px"] + v * cpx)))
                px = min(dl.shape[1] - 1, max(0, int(R["x0px"] + u * cpx)))
                if dl[py, px] > 0.4:
                    return False
            return True

        ritorno = None                           # angolo raggiungibile di nascosto (sopra l'ultima gamba)
        while resto:
            qui = pos if pos is not None else resto[0][3][0][0]
            resto.sort(key=lambda t: abs(t[1] + 0.5 - qui[0]) + abs(t[0] + 0.5 - qui[1]))
            stesso = corse and corse[-1][0] == c and pos is not None
            scelta, clip_in = 0, None            # prima i vicini raggiungibili lungo la linea
            if stesso:
                for k_, t_ in enumerate(resto[:8]):
                    for sg in t_[3]:
                        for e_ in sg:
                            if (round(e_[0]), round(e_[1])) != e_ or True:
                                if sotto_linea(pos, e_):
                                    scelta, clip_in = k_, e_
                                    break
                        if clip_in:
                            break
                    if clip_in:
                        break
            i, j, _c, segs, _pieno = resto.pop(scelta)
            cen = (j + 0.5, i + 0.5)
            angoli = {(j + ANG[n][0], i + ANG[n][1]) for n in range(4)}

            def ang_di(pt):
                r = (round(pt[0]), round(pt[1]))
                return r if r in angoli and math.dist(r, pt) < 1e-6 else None
            uso = [sg for sg in segs if contiene(sg, cen)][:2]
            if not uso:                          # gambe che non si incrociano: solo la più lunga
                uso = [max(segs, key=lambda sg: math.dist(*sg))]
            capi = [e_ for sg in uso for e_ in sg]
            if clip_in is not None and clip_in not in capi:
                clip_in = min(capi, key=lambda e_: math.dist(pos, e_))
                if not sotto_linea(pos, clip_in):
                    clip_in = None
            entrate = {ang_di(e_) for e_ in capi} - {None}
            if clip_in is not None:              # lungo la linea: coperto dal contorno
                p = corse[-1][1]
                p.append(g2mm(clip_in))
                start = clip_in
            else:
                via = None
                if stesso and entrate and fatti:
                    if not isinstance(pos[0], int) and ritorno is not None:
                        corse[-1][1].append(g2mm(ritorno))           # torna all'angolo sopra la gamba
                        pos = ritorno
                    if isinstance(pos[0], int):
                        via = viaggio(pos, lambda n: n in entrate)
                if via is not None:
                    p = corse[-1][1]
                    for n, (ci, cj) in via:
                        p.append(centro(cj, ci))
                        p.append((ox + n[0] * s, oy + n[1] * s))
                        pos = n
                    start = pos
                else:
                    p = []
                    corse.append((c, p, "taglia"))  # non raggiungibile di nascosto: un taglio, non un filo a vista
                    start = next(iter(entrate)) if entrate else uso[0][0]
                    p.append(g2mm(start))
            S1 = min(uso, key=lambda sg: min(math.dist(sg[0], start), math.dist(sg[1], start)))
            o = S1[1] if math.dist(S1[0], start) <= math.dist(S1[1], start) else S1[0]
            p += [g2mm(o), g2mm(start), g2mm(o)]                     # prima gamba ×3, finisce in o
            fine, ritorno = o, ang_di(start) or ang_di(o)
            S2 = next((sg for sg in uso if sg is not S1), None)
            if S2 is not None:
                p.append(g2mm(cen))                                # al centro, sopra la gamba appena fatta
                # si esce sulla linea (per proseguire lungo il contorno) se si può, se no da un angolo
                e = next((x for x in S2 if not ang_di(x)), None) or next((x for x in S2 if ang_di(x)), S2[1])
                f = S2[0] if e is S2[1] else S2[1]
                p += [g2mm(f), g2mm(e), g2mm(f), g2mm(e)]          # seconda gamba ×3, finisce in e
                fine = e
                ritorno = ang_di(f) or ang_di(e) or ritorno
            pos = ang_di(fine) or fine
    n_croci = len(corse)                         # da qui in poi solo linee
    # linee (backstitch) sopra le croci
    # (come il digitalizzatore: ogni volta il tratto più vicino, girato se conviene)
    per_col = {}
    for c, tratti in R["linee"]:
        per_col.setdefault(c, []).extend(list(t) for t in tratti)
    qui = corse[-1][1][-1] if corse and corse[-1][1] else None
    for c, rest in per_col.items():
        # i rami che si toccano formano una rete: la si percorre tutta in una corsa (visita in profondità).
        # Ogni ramo nuovo a punto triplo; per tornare a un incrocio si ripassa una volta sopra un ramo già
        # fatto (non si vede: è sotto la stessa linea), così niente salti e tagli tra un ramo e l'altro
        P = [t[0::3] for t in rest if len(t) >= 2]
        P = [p for p in P if len(p) >= 2]
        nodi = []                                # capi dei rami, uniti se a meno di 0,35 mm

        def nodo(pt):
            for k, q in enumerate(nodi):
                if math.dist(pt, q) < 0.35:
                    return k
            nodi.append(pt)
            return len(nodi) - 1
        lati = [(nodo(p[0]), nodo(p[-1]), p) for p in P]
        adj = {}
        for e, (u, v, _p) in enumerate(lati):
            adj.setdefault(u, []).append(e)
            adj.setdefault(v, []).append(e)
        fatti_l = set()
        while len(fatti_l) < len(lati):
            liberi = [e for e in range(len(lati)) if e not in fatti_l]
            # si parte dal capo libero più vicino (meglio un capo con un ramo solo: meno ritorni)
            cand = {u for e in liberi for u in lati[e][:2]}
            rif = qui if qui is not None else nodi[next(iter(cand))]
            u = min(cand, key=lambda n: (math.dist(rif, nodi[n]) - (1.0 if len(adj[n]) == 1 else 0)))
            corsa = [nodi[u]]
            corse.append((c, corsa))
            pila = []                                    # [(nodo, lato da cui si è arrivati)]
            rimasti, coda = set(), [u]                   # i rami di questa rete
            visti = {u}
            while coda:
                n_ = coda.pop()
                for e in adj[n_]:
                    if e not in fatti_l:
                        rimasti.add(e)
                    for m_ in lati[e][:2]:
                        if m_ not in visti:
                            visti.add(m_)
                            coda.append(m_)
            while True:
                aperti = [e for e in adj[u] if e not in fatti_l]
                if aperti:
                    e = aperti[0]
                    a, b, p = lati[e]
                    pts = p if a == u else p[::-1]
                    for q in pts[1:]:                   # triplo: avanti, indietro, avanti
                        corsa += [q, corsa[-1], q]
                    fatti_l.add(e)
                    rimasti.discard(e)
                    pila.append((u, e))
                    u = b if a == u else a
                    continue
                # ramo finito: se nella rete c'è ancora qualcosa da fare, si torna indietro sopra i rami fatti
                if not pila or not rimasti:
                    break
                v, e = pila.pop()
                a, b, p = lati[e]
                pts = p if a == v else p[::-1]           # da v a u: ora si ripercorre da u a v (una passata)
                for q in pts[::-1][1:]:
                    corsa.append(q)
                u = v
            qui = corsa[-1]

    # scrittura: cambio filo quando cambia il colore, taglio tra una corsa e l'altra (salvo vicinissime)
    ultimo, prec, tagli = None, None, 0
    # collegamento senza taglio solo se non si vedrà: sotto croci di un colore che si ricama DOPO, o sotto
    # la linea del contorno (per le croci; le linee vengono per ultime). Il filo scoperto si taglia sempre.
    primo = {}
    for k_, co in enumerate(corse[:n_croci]):
        primo.setdefault(co[0], k_)
    celle = {}
    for i_ in range(G.shape[0]):
        for j_ in range(G.shape[1]):
            if G[i_, j_] >= 0:
                celle[(i_, j_)] = {int(G[i_, j_])}
    for t_ in R.get("parz", []):
        celle.setdefault((t_[0], t_[1]), set()).add(t_[2])
    for t_ in R.get("tq", []):
        celle.setdefault((t_[0], t_[1]), set()).add(t_[2])
    dl = R.get("dlin")

    def nascosto(k_, a, b):
        n = max(2, int(math.dist(a, b) / 0.25))
        for t in range(n + 1):
            x_ = a[0] + (b[0] - a[0]) * t / n
            y_ = a[1] + (b[1] - a[1]) * t / n
            cel = (int((y_ - oy) // s), int((x_ - ox) // s))
            if any(primo.get(d, -1) > k_ for d in celle.get(cel, ())):
                continue
            if k_ < n_croci and dl is not None:
                py = min(dl.shape[0] - 1, max(0, int(R["y0px"] + (y_ - oy) * R["ppm"])))
                px = min(dl.shape[1] - 1, max(0, int(R["x0px"] + (x_ - ox) * R["ppm"])))
                if dl[py, px] <= 0.4:
                    continue
            return False
        return True

    for k_corsa, corsa_ in enumerate(corse):
        c, pts = corsa_[0], corsa_[1]
        if len(pts) < 2:
            continue
        if c != ultimo:
            if ultimo is not None:
                out.add_command(pe.COLOR_CHANGE)
            f = A["fili"][c]
            th = pe.EmbThread()
            th.set_hex_color(f["hex"])
            th.description, th.catalog_number = f["nome"], f["num"]
            out.add_thread(th)
            ultimo, prec = c, None
        if prec is not None and (math.dist(prec, pts[0]) <= 0.8
                                 or (math.dist(prec, pts[0]) <= 5.0 and nascosto(k_corsa, prec, pts[0]))):
            seq = pts
        else:
            if prec is not None:
                tagli += 1
                out.add_stitch_absolute(pe.TRIM, pts[0][0] * 10, pts[0][1] * 10)
            out.add_stitch_absolute(pe.JUMP, pts[0][0] * 10, pts[0][1] * 10)
            seq = _fermo(pts)
        for x_, y_ in seq:
            out.add_stitch_absolute(pe.STITCH, x_ * 10, y_ * 10)
        prec = pts[-1]
    out.add_command(pe.END)
    n = sum(1 for st in out.stitches if (st[2] & 0xFF) == pe.STITCH)
    x0, y0, x1, y1 = out.bounds()
    return out, {"punti": n, "colori": len(out.threadlist), "tagli": tagli, "croci": int((G >= 0).sum()),
                 "larg": round((x1 - x0) / 10, 1), "alt": round((y1 - y0) / 10, 1)}


def digitalizza_croce(A, cella=2.0, passate=3, scelte=None, bordo="croci"):
    R = griglia(A, cella, scelte, bordo)
    if R is None:
        import pyembroidery as pe
        return pe.EmbPattern(), {"punti": 0, "colori": 0, "tagli": 0}
    pat, info = croci(R, A, passate)
    info["griglia"] = R
    return pat, info


def resa(A, R, linee=True):
    """Come si vede il disegno a punto croce, pixel per pixel nella cornice di A: indice colore o -1 (stoffa).
    linee=False: solo le croci."""
    from PIL import Image as _Im, ImageDraw as _Dr
    H, W = A["zone"].shape
    ppm, cp = A["ppm"], R["cella"] * A["ppm"]
    x0, y0 = R["x0px"], R["y0px"]
    out = np.full((H, W), -1, np.int32)
    disegno = A["zone"] >= 0

    def blocco(i, j):
        a, b = int(round(y0 + i * cp)), int(round(y0 + (i + 1) * cp))
        c, d = int(round(x0 + j * cp)), int(round(x0 + (j + 1) * cp))
        return slice(max(0, a), max(0, b)), slice(max(0, c), max(0, d))
    G = R["G"]
    for i, j in zip(*np.nonzero(G >= 0)):
        out[blocco(i, j)] = G[i, j]
    # croci tagliate dalla linea e 3/4: coprono solo la parte dentro il disegno
    for i, j, c, *_r in list(R["parz"]) + list(R["tq"]):
        s = blocco(i, j)
        out[s] = np.where(disegno[s], c, out[s])
    if not linee:
        return out
    tela = _Im.new("I", (W, H), 0)
    dr = _Dr.Draw(tela)
    for c, tratti in R["linee"]:
        for t in tratti:
            dr.line([(x * ppm, y * ppm) for x, y in t], fill=int(c) + 1, width=max(1, int(round(0.45 * ppm))))
    lin = np.array(tela, np.int32) - 1
    out[lin >= 0] = lin[lin >= 0]
    return out


def idoneita(A, R):
    """Quanto il disegno si presta al punto croce, 0-100 %, confrontando il disegno con la sua resa a croci
    (con la tolleranza di mezzo quadretto: lo scalino della griglia non conta, i pezzi persi sì):
    - zone: ogni zona colorata c'è ancora? (peso √area: un occhio conta quasi come un cappello);
    - sbavature: croci di un colore dove il disegno quel colore non l'ha;
    - linee (contorno, scritte): c'è ancora un filo sopra?;
    - sfumature: pixel lontani dal loro filo (disegni ombreggiati: il punto croce li fa a gradini).
    -> {"perc", "livello", "motivi": [(chiave, valore)], ...}"""
    from skimage.color import rgb2lab
    ppm, cella = A["ppm"], R["cella"]
    z = A["zone"]
    cp = cella * ppm
    tol = max(1, int(round(0.5 * cp)))
    col = np.full(z.shape, -1, np.int32)
    for o in A["info"]:
        col[z == o["id"]] = o["colore"]
    el = R.get("era_linea")
    el = np.zeros(z.shape, bool) if el is None else el
    col_r = col                                  # il disegno senza linee: al loro posto quel che c'è di fianco
    if el.any() and (~el).any():
        _, (iy, ix) = ndimage.distance_transform_edt(el, return_indices=True)
        col_r = col[iy, ix]
    r = resa(A, R, linee=False)
    st = np.ones((3, 3), bool)
    # zone ritrovate
    num = den = 0.0
    persi, spezzate, sottili = [], [], []
    for o in A["info"]:
        mz = (z == o["id"]) & ~el
        n = int(mz.sum())
        if n < 0.15 * cp * cp:                  # briciole (meno di un sesto di quadretto)
            continue
        rc = r == o["colore"]
        ys, xs = np.nonzero(mz)
        a, b = max(0, ys.min() - tol), ys.max() + tol + 1
        c, d = max(0, xs.min() - tol), xs.max() + tol + 1
        vicino = ndimage.binary_dilation(rc[a:b, c:d], st, iterations=tol)
        rec = float(vicino[mz[a:b, c:d]].mean())
        q = min(1.0, rec / 0.8)
        # spezzata: un pezzo solo nel disegno, più pezzi a croci (onde della glassa, rametti)
        intorno = ndimage.binary_dilation(mz[a:b, c:d], st, iterations=tol)
        n_d = ndimage.label(mz[a:b, c:d], st)[1]
        n_r = ndimage.label(rc[a:b, c:d] & intorno, st)[1]
        if n_r > n_d and n >= cp * cp:
            q *= 0.5
            spezzate.append(o["id"])
        # stretta (meno di un quadretto e mezzo) ma fatta a croci: a gradini, si legge male (croce della
        # finestra, onde della glassa); più è stretta più perde
        elif o.get("larg_tipica", 9) < 1.5 * cella and n >= cp * cp:
            q *= min(1.0, 0.4 + 0.4 * o["larg_tipica"] / cella)
            if o["larg_tipica"] < cella:
                sottili.append(o["id"])
        w = math.sqrt(n)
        num += w * q
        den += w
        if rec < 0.4:
            persi.append(o["id"])
    zone = num / den if den else 1.0
    # sbavature
    fuori = 0
    tot = int((r >= 0).sum())
    for c in np.unique(r[r >= 0]):
        orig = ndimage.binary_dilation((col_r == c) | ((col == c) & el), st, iterations=tol)
        fuori += int(((r == c) & ~orig).sum())
    sbav = fuori / tot if tot else 0.0
    # linee
    linee_ok = 1.0
    if el.any():
        from skimage.morphology import skeletonize
        sk = skeletonize(el)
        rl = resa(A, R)
        disegnate = rl != r
        if sk.any():
            dist = ndimage.distance_transform_edt(~disegnate) / ppm if disegnate.any() else np.full(z.shape, 99.0)
            linee_ok = float((dist[sk] <= 0.6).mean())
    # sfumature
    m = col >= 0
    rgb = A["rgb"][..., :3].astype(np.float32) / 255
    pal = np.asarray(A["palette"], np.float32)[:, :3] / 255
    sfumato = float((np.linalg.norm(rgb2lab(rgb[m]) - rgb2lab(pal[None])[0][col[m]], axis=1) > 12).mean()) if m.any() else 0.0
    celle = int(round(m.sum() / cp ** 2))
    v = zone * (1 - min(1.0, 3 * sbav)) * (0.5 + 0.5 * linee_ok) * (1 - min(0.6, max(0.0, sfumato - 0.03) * 4))
    perc = int(round(100 * v))
    livello = "ottimo" if perc >= 85 else "buono" if perc >= 70 else "difficile" if perc >= 50 else "sconsigliato"
    motivi = []
    if persi:
        motivi.append(("dettagli_persi", len(persi)))
    if spezzate:
        motivi.append(("spezzate", len(spezzate)))
    if sottili:
        motivi.append(("sottili", len(sottili)))
    if sbav >= 0.02:
        motivi.append(("sbavature", round(100 * sbav)))
    if linee_ok < 0.9:
        motivi.append(("linee_perse", round(100 * (1 - linee_ok))))
    if sfumato > 0.05:
        motivi.append(("sfumature", round(100 * sfumato)))
    if celle < 700:
        motivi.append(("pochi_quadretti", celle))
    return {"perc": perc, "livello": livello, "motivi": motivi, "celle": celle, "zone_ok": round(zone, 3),
            "sbavature": round(sbav, 3), "linee_ok": round(linee_ok, 3), "sfumato": round(sfumato, 3), "persi": persi,
            "spezzate": spezzate, "sottili": sottili}


def anteprima_aida(R, A, px=18, bordo=3):
    """Come verrà ricamato a mano: croci di filo (gamba sotto «/», sopra «\\») su tela Aida, linee a punto
    indietro. Disegnato 2× e rimpicciolito per i bordi morbidi. -> PIL.Image"""
    from PIL import Image, ImageDraw
    G = R["G"]
    ny, nx = G.shape
    ss = 2
    s = px * ss
    W, H = (nx + 2 * bordo) * s, (ny + 2 * bordo) * s
    im = Image.new("RGB", (W, H), (243, 239, 230))
    d = ImageDraw.Draw(im)
    # trama Aida: fili chiari in rilievo, un buchino a ogni angolo dei quadretti
    for k in range(0, nx + 2 * bordo + 1):
        d.line((k * s, 0, k * s, H), fill=(228, 223, 212), width=max(2, s // 6))
    for k in range(0, ny + 2 * bordo + 1):
        d.line((0, k * s, W, k * s), fill=(228, 223, 212), width=max(2, s // 6))
    r = max(2, s // 9)
    for i in range(ny + 2 * bordo + 1):
        for j in range(nx + 2 * bordo + 1):
            d.ellipse((j * s - r, i * s - r, j * s + r, i * s + r), fill=(196, 188, 174))
    pal = A["palette"]

    def tono(c, f):
        return tuple(int(max(0, min(255, v * f if f < 1 else v + (255 - v) * (f - 1)))) for v in pal[c])

    w = max(3, int(s * 0.30))
    m = max(1, s // 10)
    for gamba in (0, 1):                         # prima tutte le gambe sotto, poi quelle sopra (come a mano)
        for i in range(ny):
            for j in range(nx):
                c = G[i, j]
                if c < 0:
                    continue
                x, y = (j + bordo) * s, (i + bordo) * s
                a, b = ((x + m, y + s - m), (x + s - m, y + m)) if gamba == 0 else ((x + m, y + m), (x + s - m, y + s - m))
                d.line((a, b), fill=tono(c, 0.62), width=w + 2)          # ombra del filo
                d.line((a, b), fill=tono(c, 1.0), width=w)
                h = w // 4                                                 # riflesso lucido
                d.line(((a[0] + h, a[1] - h if gamba == 0 else a[1] + h), (b[0] + h, b[1] - h if gamba == 0 else b[1] + h)),
                       fill=tono(c, 1.35), width=max(1, w // 4))
    for i, j, c, k in R.get("tq", []):           # punti 3/4: mezza croce + quarto
        x, y = (j + bordo) * s, (i + bordo) * s

        def ang(n, ins=m):
            dx, dy = ANG[n]
            return (x + (ins if dx == 0 else s - ins), y + (ins if dy == 0 else s - ins))
        s1, s2 = LATO[k]
        for a, b in ((ang(s1), ang(s2)), (ang(k), (x + s / 2, y + s / 2))):
            d.line((a, b), fill=tono(c, 0.62), width=w + 2)
            d.line((a, b), fill=tono(c, 1.0), width=w)
    for gamba in (0, 1):                         # croci accorciate fino alla linea (bordo morbido)
        for i, j, c, segs, _p in R.get("parz", []):
            for (a0, a1) in segs:
                diag = (a1[0] - a0[0]) * (a1[1] - a0[1]) < 0      # «/» = gamba sotto
                if diag != (gamba == 0):
                    continue
                A0 = ((a0[0] + bordo) * s, (a0[1] + bordo) * s)
                A1 = ((a1[0] + bordo) * s, (a1[1] + bordo) * s)
                d.line((A0, A1), fill=tono(c, 0.62), width=w + 2)
                d.line((A0, A1), fill=tono(c, 1.0), width=w)
    for c, tratti in R["linee"]:
        for t in tratti:
            P = [((x - R["ox"]) / R["cella"] + bordo, (y - R["oy"]) / R["cella"] + bordo) for x, y in t[::3]]
            if len(P) >= 2:
                P = [(u * s, v * s) for u, v in P]
                d.line(P, fill=tono(c, 0.7), width=max(3, int(s * 0.22)) + 2, joint="curve")
                d.line(P, fill=tono(c, 1.0), width=max(3, int(s * 0.22)), joint="curve")
                h = max(1, int(s * 0.05))                                  # riflesso del filo
                d.line([(u + h, v - h) for u, v in P], fill=tono(c, 1.4), width=max(1, int(s * 0.05)))
    return im.resize((W // ss, H // ss), Image.LANCZOS)


# ---------------------------------------------------------------- schema A4 per il ricamo a mano
SIMBOLI ="●■▲◆★✚✖○□△◇☆♥♦♣♠◐◑▼◀▶⬟⬢✿❖✦✪"


def schema_a4(R, A, percorso_pdf, titolo="", marca="DMC", dpi=200, png=None):
    """Schema classico da stampare: griglia con un simbolo per colore, righe spesse ogni 10, numeri sui bordi,
    frecce al centro, linee (backstitch) disegnate, legenda con il filo DMC più vicino e il numero di croci.
    Se il disegno non sta in una pagina si divide in più pagine (con 2 quadretti ripetuti per riattaccare)."""
    from PIL import Image, ImageDraw, ImageFont
    import fili as F

    def font(sz, sim=False, b=False):
        for nome in (("seguisym.ttf",) if sim else ()) + (("segoeuib.ttf",) if b else ("segoeui.ttf",)) + ("DejaVuSans.ttf",):
            try:
                return ImageFont.truetype(nome, sz)
            except OSError:
                continue
        return ImageFont.load_default()

    G = R["G"]
    ny, nx = G.shape
    colori = list(R["ordine"])
    for c, _ in R["linee"]:
        if c not in colori:
            colori.append(c)
    pal = A["palette"]
    hexes = ["#%02x%02x%02x" % tuple(int(v) for v in pal[c]) for c in colori]
    tab = {f["num"]: f for f in F.tabella(marca)[0]}
    nums = F.piu_vicino(hexes, marca)
    sim = {c: SIMBOLI[k % len(SIMBOLI)] for k, c in enumerate(colori)}
    mmpx = dpi / 25.4
    PW, PH = int(210 * mmpx), int(297 * mmpx)
    marg = int(12 * mmpx)
    testa = int(18 * mmpx)
    # lato del quadretto sulla carta: 3,2 mm; scende fino a 2,6 mm se così il disegno sta in una pagina
    lato = min((210 - 24 - 8) / nx, (297 - 24 - 18 - 8 - 70) / ny)
    q = int((3.2 if lato >= 3.2 else 2.6 if lato < 2.6 else lato) * mmpx)
    per_x = (PW - 2 * marg - int(8 * mmpx)) // q
    per_y = (PH - 2 * marg - testa - int(8 * mmpx)) // q
    sovr = 2 if (nx > per_x or ny > per_y) else 0
    passo_x, passo_y = per_x - sovr, per_y - sovr
    pagine = []
    f_num, f_sim, f_tit = font(int(2.2 * mmpx)), font(int(2.6 * mmpx), sim=True), font(int(5 * mmpx), b=True)
    f_sim3 = font(int(1.8 * mmpx), sim=True)

    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.299 * r + 0.587 * g + 0.114 * b

    blocchi = [(i0, j0) for i0 in range(0, ny, passo_y) for j0 in range(0, nx, passo_x)]
    for n_p, (i0, j0) in enumerate(blocchi):
        im = Image.new("RGB", (PW, PH), "white")
        d = ImageDraw.Draw(im)
        d.text((marg, marg), titolo or "Punto croce", font=f_tit, fill=(31, 42, 38))
        sotto = f"{nx} × {ny} croci  ·  pagina {n_p + 1}/{len(blocchi)}  ·  righe {i0 + 1}-{min(ny, i0 + per_y)}, colonne {j0 + 1}-{min(nx, j0 + per_x)}"
        d.text((marg, marg + int(7 * mmpx)), sotto, font=font(int(2.8 * mmpx)), fill=(90, 100, 95))
        X0, Y0 = marg + int(8 * mmpx), marg + testa + int(6 * mmpx)
        bi, bj = min(per_y, ny - i0), min(per_x, nx - j0)
        for i in range(bi):
            for j in range(bj):
                c = G[i0 + i, j0 + j]
                if c < 0:
                    continue
                k = colori.index(c)
                x, y = X0 + j * q, Y0 + i * q
                d.rectangle((x, y, x + q, y + q), fill=hexes[k])
                d.text((x + q / 2, y + q / 2), sim[c], font=f_sim, anchor="mm",
                       fill="white" if lum(hexes[k]) < 110 else "black")
        for ti, tj, c, kk in R.get("tq", []):    # punto 3/4: triangolo colorato verso l'angolo dentro
            i, j = ti - i0, tj - j0
            if not (0 <= i < bi and 0 <= j < bj):
                continue
            x, y = X0 + j * q, Y0 + i * q
            P = [(x + ANG[n][0] * q, y + ANG[n][1] * q) for n in (LATO[kk][0], kk, LATO[kk][1])]
            d.polygon(P, fill=hexes[colori.index(c)])
            cx, cy = sum(p[0] for p in P) / 3, sum(p[1] for p in P) / 3
            d.text((cx, cy), sim[c], font=f_sim3, anchor="mm",
                   fill="white" if lum(hexes[colori.index(c)]) < 110 else "black")
        for ti, tj, c, _segs, pieno in R.get("parz", []):   # tagliati dalla linea: a mano, croce intera se
            i, j = ti - i0, tj - j0                          # il quadretto è dentro almeno per metà
            if not pieno or not (0 <= i < bi and 0 <= j < bj):
                continue
            x, y = X0 + j * q, Y0 + i * q
            k = colori.index(c)
            d.rectangle((x, y, x + q, y + q), fill=hexes[k])
            d.text((x + q / 2, y + q / 2), sim[c], font=f_sim, anchor="mm",
                   fill="white" if lum(hexes[k]) < 110 else "black")
        for i in range(bi + 1):
            g = (i0 + i) % 10 == 0
            d.line((X0, Y0 + i * q, X0 + bj * q, Y0 + i * q), fill=(40, 40, 40) if g else (175, 175, 175), width=3 if g else 1)
        for j in range(bj + 1):
            g = (j0 + j) % 10 == 0
            d.line((X0 + j * q, Y0, X0 + j * q, Y0 + bi * q), fill=(40, 40, 40) if g else (175, 175, 175), width=3 if g else 1)
        for j in range(0, bj + 1):
            if (j0 + j) % 10 == 0:
                d.text((X0 + j * q, Y0 - 4), str(j0 + j), font=f_num, anchor="mb", fill=(60, 60, 60))
        for i in range(0, bi + 1):
            if (i0 + i) % 10 == 0:
                d.text((X0 - 6, Y0 + i * q), str(i0 + i), font=f_num, anchor="rm", fill=(60, 60, 60))
        # frecce al centro del disegno (per partire dal centro della stoffa)
        cj, ci = nx // 2 - j0, ny // 2 - i0
        if 0 <= cj <= bj:
            x = X0 + cj * q
            d.polygon([(x - 8, Y0 - int(4 * mmpx)), (x + 8, Y0 - int(4 * mmpx)), (x, Y0 - int(2.8 * mmpx))], fill=(200, 40, 40))
        if 0 <= ci <= bi:
            y = Y0 + ci * q
            d.polygon([(X0 - int(6 * mmpx), y - 8), (X0 - int(6 * mmpx), y + 8), (X0 - int(4.6 * mmpx), y)], fill=(200, 40, 40))
        # linee (backstitch): una passata sola del tratto, non le tre
        for c, tratti in R["linee"]:
            k = colori.index(c)
            for t in tratti:
                P = [((x - R["ox"]) / R["cella"] - j0, (y - R["oy"]) / R["cella"] - i0) for x, y in t[::3]]
                P = [(X0 + u * q, Y0 + v * q) for u, v in P if -0.5 <= u <= bj + 0.5 and -0.5 <= v <= bi + 0.5]
                if len(P) >= 2:
                    d.line(P, fill=hexes[k], width=max(3, q // 4), joint="curve")
        pagine.append(im)
    # legenda (sull'ultima pagina se c'è posto, altrimenti su una pagina nuova)
    righe = len(colori)
    alt_leg = int((40 + 7 * righe) * mmpx)
    ultima = pagine[-1]
    y_occ = Y0 + min(per_y, ny - blocchi[-1][0]) * q + int(10 * mmpx)
    pag_nuova = y_occ + max(alt_leg, int(45 * mmpx)) > PH - marg
    if pag_nuova:
        ultima = Image.new("RGB", (PW, PH), "white")
        pagine.append(ultima)
        y_occ = marg
    d = ImageDraw.Draw(ultima)
    f_l, f_lb = font(int(3 * mmpx)), font(int(3 * mmpx), b=True)
    d.text((marg, y_occ), "Legenda", font=font(int(4 * mmpx), b=True), fill=(31, 42, 38))
    y = y_occ + int(8 * mmpx)
    cont = {c: int((G == c).sum()) + sum(1 for t in R.get("tq", []) + [p for p in R.get("parz", []) if p[4]] if t[2] == c) for c in colori}
    for k, c in enumerate(colori):
        f = tab.get(nums[k], {})
        d.rectangle((marg, y, marg + int(5 * mmpx), y + int(5 * mmpx)), fill=hexes[k], outline=(120, 120, 120))
        d.text((marg + int(2.5 * mmpx), y + int(2.5 * mmpx)), sim[c], font=f_sim, anchor="mm",
               fill="white" if lum(hexes[k]) < 110 else "black")
        tipo = f"{cont[c]} croci" if cont[c] else "solo linea (punto indietro)"
        if cont[c] and any(cc == c for cc, _ in R["linee"]):
            tipo += " + linea"
        d.text((marg + int(8 * mmpx), y + int(0.5 * mmpx)), f"{marca} {nums[k]}", font=f_lb, fill=(31, 42, 38))
        d.text((marg + int(26 * mmpx), y + int(0.5 * mmpx)), f"{f.get('en', '')}"[:24], font=f_l, fill=(60, 60, 60))
        d.text((marg + int(66 * mmpx), y + int(0.5 * mmpx)), tipo if cont[c] else "linea", font=f_l, fill=(60, 60, 60))
        y += int(7 * mmpx)
    y += int(3 * mmpx)
    for k in (0, 2):
        d.text((marg, y), "   ".join(f"Aida {ct}: {nx / (ct / 2.54):.1f} × {ny / (ct / 2.54):.1f} cm"
                                     for ct in (11, 14, 16, 18)[k:k + 2]), font=f_l, fill=(60, 60, 60))
        y += int(5.5 * mmpx)
    d.text((marg, y), f"Ricamatrice: {nx * R['cella']:.0f} × {ny * R['cella']:.0f} mm (quadretto {R['cella']} mm)",
           font=f_l, fill=(60, 60, 60))
    d.text((marg, y + int(5.5 * mmpx)), "Croci: 2 fili" + (".  Linee: punto indietro, 1 filo." if R["linee"] else "."),
           font=f_l, fill=(60, 60, 60))
    # a destra della legenda: come verrà ricamato (come nelle schede dei negozi)
    if pag_nuova:                                # pagina tutta per la legenda: anteprima grande sotto
        xa, ya = marg, y + int(16 * mmpx)
    else:
        xa, ya = marg + int(92 * mmpx), y_occ
    wa, ha = PW - marg - xa, PH - marg - int(6 * mmpx) - (ya + int(8 * mmpx))
    if wa > 30 * mmpx and ha > 30 * mmpx:
        d.text((xa, ya), "Come verrà", font=font(int(4 * mmpx), b=True), fill=(31, 42, 38))
        foto = anteprima_aida(R, A, px=24, bordo=2)
        k = min(wa / foto.width, ha / foto.height)
        foto = foto.resize((int(foto.width * k), int(foto.height * k)), Image.LANCZOS)
        ultima.paste(foto, (xa, ya + int(8 * mmpx)))
    for im in pagine:
        ImageDraw.Draw(im).text((PW - marg, PH - marg // 2), "OpenHoop", font=f_num, anchor="rb", fill=(150, 150, 150))
    if png:
        for k, im in enumerate(pagine):
            im.save(f"{png}_{k + 1}.png")
    pagine[0].save(percorso_pdf, "PDF", resolution=dpi, save_all=True, append_images=pagine[1:])
    return len(pagine)
