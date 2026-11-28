"""Stile «redwork» del digitalizzatore: solo le linee del disegno, un filo solo (rosso, blu…), punto filza.

Come i redwork commerciali dell'archivio (misurati il 27/11/2026: 600MIL Baby «… Redwork», 24 disegni):
- un colore, punto filza SINGOLO, mediana 1,8 mm (p10 0,9 mm nelle curve strette, p90 3 mm);
- per non saltare l'ago torna indietro sulla linea già fatta, negli stessi buchi (punto doppio sui rami);
- 2-19 salti e 0-2 tagli per disegno: quasi tutto in un percorso continuo;
- occhi e nasini pieni, piccoli, nello stesso colore.
Le linee vengono dal disegno analizzato (digitalizza.analizza): le linee sottili danno la loro linea centrale,
le zone colorate il loro bordo (dove cambia davvero colore), i pallini scuri piccoli restano pieni.
"""
import heapq
import math
from collections import defaultdict

import numpy as np
from scipy import ndimage

import digitalizza as DG

PASSO = 1.8           # mm: punto filza sul dritto
DEV = 0.12            # mm: nelle curve il punto si accorcia finché la corda resta entro questa distanza dalla linea
LARG_LINEA = 2.0      # mm: zone-linea più strette -> linea centrale; più larghe -> il loro bordo
PONTE = 1.0           # mm: due pezzi di tratto più vicini di così si uniscono (schizzi interrotti, tratteggi)
STACCATO_MM = 4.0     # mm: pezzi isolati più piccoli (diagonale) si possono togliere per risparmiare tagli
PALLINO_MM2 = 12.0    # mm²: pallini scuri e pieni più piccoli (pupille, nasini) -> pieni
CONTRASTO = 60        # differenza RGB (somma) sotto cui un bordo tra due colori non si ricama (sfumature)
COLORI = {"rosso": "#b3121f", "blu": "#1f3f8f", "nero": "#1a1a1a", "marrone": "#5a3a22", "verde": "#1f6b3a"}


def _e_linea(o, col_contorno):
    """Zona da ricamare come linea centrale: linee sottili, e il contorno principale anche se spesso (un contorno
    nero da 3 mm non deve diventare due linee parallele)."""
    return o["punto"] in ("filza3", "satin") and (o.get("larg_tipica", 9) <= LARG_LINEA or o["colore"] == col_contorno)


def inchiostro(A, soglia=28.0):
    """Pixel che si staccano dal fondo (ΔE dal colore più comune dell'immagine): il tratto di un disegno a linee."""
    from skimage.color import rgb2lab
    lab = rgb2lab(A["rgb"][..., :3].astype(np.float32) / 255)
    fondo = np.median(lab.reshape(-1, 3), 0)
    return np.linalg.norm(lab - fondo, axis=-1) > soglia


def a_tratto(A, ink=None):
    """Disegno a linee (redwork, pagina da colorare): poco inchiostro (< 25 %) fatto di tratti sottili (mezzo
    spessore mediano ≤ 0,45 mm). Misurato: redwork a tratto 13 % e 0,27 mm; cartoni colorati 40-85 % e ≥ 0,7 mm."""
    from skimage.morphology import skeletonize
    ink = inchiostro(A) if ink is None else ink
    if not ink.any() or ink.mean() >= 0.25:
        return False
    sk = skeletonize(ink)
    if not sk.any():
        return False
    dt = ndimage.distance_transform_edt(ink) / A["ppm"]
    return float(np.median(dt[sk])) <= 0.45


def maschere_tratto(A, ink):
    """Disegno a linee: si ricama lo scheletro del tratto così com'è; le macchie piene piccole (pupille) pieni."""
    ppm = A["ppm"]
    lab, n = ndimage.label(ink, np.ones((3, 3), bool))
    if n:                                       # briciole (meno di 0,15 mm²): sporco della scansione
        area = ndimage.sum(ink, lab, np.arange(1, n + 1)) / ppm ** 2
        ink = ink & np.isin(lab, np.nonzero(area >= 0.15)[0] + 1)
    ink = _ponti(ink, ppm)
    dt = ndimage.distance_transform_edt(ink) / ppm
    from skimage.morphology import skeletonize
    sk = skeletonize(ink)
    tipico = float(np.median(dt[sk])) if sk.any() else 0.3
    solido = dt > max(0.5, 2.2 * tipico)          # dove il tratto è molto più spesso del solito: una macchia piena
    pallini = []
    if solido.any():
        macchie = ndimage.binary_dilation(solido, iterations=max(1, int(round(0.6 * ppm)))) & ink
        lm, nm = ndimage.label(macchie)
        for k in range(1, nm + 1):
            m = lm == k
            if m.sum() / ppm ** 2 <= PALLINO_MM2 and _pieno_davvero(m):
                pallini.append(m)
                ink = ink & ~m
    return ink, pallini


def _ponti(ink, ppm, gap=PONTE):
    """Unisce i pezzi di tratto quasi attaccati (≤ gap mm) con un trattino nel punto più vicino: ogni pezzo
    staccato è un salto, cioè un filo da tagliare a mano. I redwork commerciali hanno 0-4 salti perché le linee si
    toccano; negli schizzi i tratti sono spesso interrotti di pochi decimi di millimetro."""
    from PIL import Image as _Im, ImageDraw as _Dr
    st = np.ones((3, 3), bool)
    lab, n = ndimage.label(ink, st)
    if n < 2:
        return ink
    g = gap * ppm
    pad = int(math.ceil(g)) + 1
    H, W = ink.shape
    sl = ndimage.find_objects(lab)
    tela = _Im.new("1", (W, H), 0)
    dr = _Dr.Draw(tela)
    larg = max(1, int(round(0.35 * ppm)))
    uniti = 0
    for k in range(1, n + 1):
        s_ = sl[k - 1]
        y0, y1 = max(0, s_[0].start - pad), min(H, s_[0].stop + pad)
        x0, x1 = max(0, s_[1].start - pad), min(W, s_[1].stop + pad)
        sub = lab[y0:y1, x0:x1]
        altri = (sub > 0) & (sub != k)
        if not altri.any():
            continue
        d, (iy, ix) = ndimage.distance_transform_edt(~altri, return_indices=True)
        mio = sub == k
        dm = np.where(mio, d, np.inf)
        a = np.unravel_index(np.argmin(dm), dm.shape)
        if dm[a] > g:
            continue
        b = (iy[a], ix[a])
        dr.line([(x0 + a[1], y0 + a[0]), (x0 + b[1], y0 + b[0])], fill=1, width=larg)
        uniti += 1
    return ink | np.array(tela, bool)


def _pieno_davvero(m):
    """Pallino vero (pupilla, nasino): occupa quasi tutto il suo inviluppo convesso; un telaio di finestra o un
    anello no."""
    from skimage.morphology import convex_hull_image
    ys, xs = np.nonzero(m)
    sub = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return sub.sum() >= 0.85 * convex_hull_image(sub).sum()


def maschere(A):
    """-> (maschera delle linee da ricamare HxW, [maschere dei pallini pieni])."""
    z, ppm = A["zone"], A["ppm"]
    H, W = z.shape
    pal = np.asarray(A["palette"], float)[:, :3]
    lab = np.full((H, W), -1, np.int32)          # id della zona-area, -1 stoffa, -3 linea o pallino
    col = {-1: -1}
    lin = np.zeros((H, W), bool)
    pallini = []
    area_lin = defaultdict(float)                # colore del contorno: il colore-linea con più area
    for o in A["info"]:
        if o["punto"] in ("filza3", "satin"):
            area_lin[o["colore"]] += o["area_mm2"]
    col_contorno = max(area_lin, key=area_lin.get) if area_lin else None
    for o in A["info"]:
        m = z == o["id"]
        if not m.any():
            continue
        if _e_linea(o, col_contorno):
            lin |= m
            lab[m] = -3
        elif o["area_mm2"] <= PALLINO_MM2 and pal[o["colore"]].max() <= 110 and DG._compatto(m) and _pieno_davvero(m):
            pallini.append(m)
            lab[m] = -3
        else:
            lab[m] = o["id"]
            col[o["id"]] = o["colore"]
    # bordi: dove due zone-area (o zona e stoffa) di colore DIVERSO si toccano
    bordo = np.zeros((H, W), bool)
    fondo = np.array([255.0, 255.0, 255.0])
    ids = np.array(sorted(col))
    mappa = np.zeros(ids.max() + 2, np.int32) - 1
    for k in ids:
        mappa[k + 1] = col[k]                    # +1: -1 (stoffa) va all'indice 0
    for a, b in ((lab[:, :-1], lab[:, 1:]), (lab[:-1, :], lab[1:, :])):
        ok = (a != b) & (a != -3) & (b != -3)
        ca, cb = mappa[np.maximum(a, -1) + 1], mappa[np.maximum(b, -1) + 1]
        ok &= ca != cb
        ra = np.where((ca >= 0)[..., None], pal[np.maximum(ca, 0)], fondo)
        rb = np.where((cb >= 0)[..., None], pal[np.maximum(cb, 0)], fondo)
        ok &= np.abs(ra - rb).sum(-1) >= CONTRASTO
        if a.shape[1] < W:
            bordo[:, :-1] |= ok
        else:
            bordo[:-1, :] |= ok
    # zone tagliate dal bordo dell'immagine: lì il contorno lo fa il bordo
    cornice = np.zeros((H, W), bool)
    cornice[0, :] = cornice[-1, :] = cornice[:, 0] = cornice[:, -1] = True
    bordo |= cornice & (lab >= 0)
    r = max(1, int(round(0.15 * ppm)))
    tutto = lin | ndimage.binary_dilation(bordo, iterations=r)
    return tutto, pallini


def _liscia(P, chiuso, sig_mm, ppm):
    """Media mobile gaussiana lungo il percorso (pixel dello scheletro -> mm), capi fermi (salvo anelli)."""
    P = np.asarray(P, float)
    if len(P) < 5:
        return P
    s = max(1.0, sig_mm * ppm)
    Q = np.stack([ndimage.gaussian_filter1d(P[:, k], s, mode="wrap" if chiuso else "nearest") for k in range(2)], 1)
    if not chiuso:
        # i capi restano dove sono (si attaccano agli incroci), il resto scivola piano verso la curva liscia
        n = len(P)
        w = np.minimum(np.arange(n), np.arange(n)[::-1]) / max(1.0, s)
        w = np.clip(w, 0, 1)[:, None]
        Q = P * (1 - w) + Q * w
    return Q


def _punti(P, passo=PASSO, dev=DEV):
    """Ricampiona la linea (mm) in punti filza: lunghi `passo` sul dritto, più corti nelle curve."""
    P = np.asarray(P, float)
    if len(P) < 2:
        return [tuple(p) for p in P]
    # linea fitta (0,1 mm)
    seg = np.hypot(*np.diff(P, axis=0).T)
    L = np.concatenate([[0], np.cumsum(seg)])
    if L[-1] <= 1e-6:
        return [tuple(P[0])]
    t = np.linspace(0, L[-1], max(2, int(L[-1] / 0.1) + 1))
    F = np.stack([np.interp(t, L, P[:, 0]), np.interp(t, L, P[:, 1])], 1)
    out = [tuple(F[0])]
    i = 0
    n = len(F)
    while i < n - 1:
        j = i + 1
        best = j
        while j < n and t[j] - t[i] <= passo + 1e-9:
            a, b = F[i], F[j]
            d = b - a
            ln = math.hypot(*d) or 1e-9
            mezzo = F[i + 1:j]
            if len(mezzo):
                dist = np.abs(d[0] * (mezzo[:, 1] - a[1]) - d[1] * (mezzo[:, 0] - a[0])) / ln
                if dist.max() > dev:
                    break
            best = j
            j += 1
        # ultimo pezzo cortissimo: si allunga il precedente invece di fare un punto da 0,2 mm
        if n - 1 - best <= 3 and best != n - 1 and t[n - 1] - t[i] <= passo * 1.25:
            best = n - 1
        out.append(tuple(F[best]))
        i = best
    return out


def linee(A):
    """-> (lati [(nodo_a, nodo_b, [punti mm])], nodi {id: (x, y) mm}, pallini [maschere])."""
    ppm = A["ppm"]
    ink = inchiostro(A)
    M, pallini = maschere_tratto(A, ink) if a_tratto(A, ink) else maschere(A)
    if not M.any():
        return [], {}, pallini
    rami, _ = DG._percorsi_scheletro(M)
    rami = [r for r in rami if len(r) >= 2]

    # potatura: codini dello scheletro (corti, un capo libero) e briciole isolate. Negli angoli di un contorno
    # spesso lo scheletro fa un codino lungo quanto mezzo spessore: la soglia cresce con lo spessore all'incrocio
    spess = ndimage.distance_transform_edt(M) / ppm          # mezzo spessore della linea in ogni punto
    for _ in range(2):
        capi = defaultdict(int)
        for r in rami:
            for p in (r[0], r[-1]):
                capi[(p[0] // 3, p[1] // 3)] += 1

        def libero(p):
            return sum(capi.get((p[0] // 3 + a, p[1] // 3 + b), 0) for a in (-1, 0, 1) for b in (-1, 0, 1)) <= 1
        tieni = []
        for r in rami:
            lung = len(r) / ppm
            la, lb = libero(r[0]), libero(r[-1])
            chiuso = r[0] == r[-1] or math.dist(r[0], r[-1]) <= 1.5
            if not chiuso and la and lb and lung < 1.5:
                continue
            if not chiuso and (la != lb):
                inc = r[-1] if la else r[0]              # capo attaccato all'incrocio
                if lung < max(1.0, 2.2 * spess[inc] + 0.3):
                    continue
            tieni.append(r)
        rami = tieni

    # nodi: capi vicini (0,4 mm) = stesso incrocio
    capi = []
    for r in rami:
        capi += [(r[0][1] / ppm, r[0][0] / ppm), (r[-1][1] / ppm, r[-1][0] / ppm)]
    pad = list(range(len(capi)))

    def rad(k):
        while pad[k] != k:
            pad[k] = pad[pad[k]]
            k = pad[k]
        return k
    if capi:
        C = np.array(capi)
        from scipy.spatial import cKDTree
        for a, b in cKDTree(C).query_pairs(0.4):
            pad[rad(a)] = rad(b)
    gruppi = defaultdict(list)
    for k in range(len(capi)):
        gruppi[rad(k)].append(k)
    nodo_di, nodi = {}, {}
    for g, ks in gruppi.items():
        nodi[g] = tuple(np.mean([capi[k] for k in ks], 0))
        for k in ks:
            nodo_di[k] = g
    lati = []
    for i, r in enumerate(rami):
        a, b = nodo_di[2 * i], nodo_di[2 * i + 1]
        P = [(x / ppm, y / ppm) for y, x in r]
        chiuso = a == b and len(P) > 4
        Q = _liscia(P, chiuso, 0.35, ppm)
        Q[0], Q[-1] = nodi[a], nodi[b]
        pts = _punti(Q)
        if len(pts) < 2:
            continue
        lati.append((a, b, pts))
    return lati, nodi, pallini


def _lung(pts):
    return sum(math.dist(pts[k], pts[k + 1]) for k in range(len(pts) - 1))


def _percorso(lati, nodi, comp, inizio_vicino):
    """Percorso continuo su una rete di linee: i lati con capi dispari si accoppiano lungo il cammino più corto e
    quei tratti si ripassano (stessi buchi, all'indietro); poi Hierholzer. -> lista di punti mm."""
    adj = defaultdict(list)
    for e in comp:
        a, b, p = lati[e]
        adj[a].append(e)
        adj[b].append(e)
    peso = {e: _lung(lati[e][2]) for e in comp}

    def dijkstra(s):
        d, pr = {s: 0.0}, {}
        h = [(0.0, s)]
        while h:
            dd, u = heapq.heappop(h)
            if dd > d.get(u, 1e18):
                continue
            for e in adj[u]:
                a, b, _ = lati[e]
                v = b if a == u else a
                nd = dd + peso[e]
                if nd < d.get(v, 1e18):
                    d[v], pr[v] = nd, (u, e)
                    heapq.heappush(h, (nd, v))
        return d, pr
    grado = {u: len(es) for u, es in adj.items()}
    dispari = [u for u, g in grado.items() if g % 2]
    extra = []                                   # lati da ripassare
    if len(dispari) > 2:
        tab = {u: dijkstra(u) for u in dispari}
        coppie = sorted((tab[u][0].get(v, 1e18), u, v) for i, u in enumerate(dispari) for v in dispari[i + 1:])
        liberi = set(dispari)
        # restano spaiati i 2 dispari più lontani tra loro: diventano inizio e fine
        for d_, u, v in coppie:
            if len(liberi) <= 2:
                break
            if u in liberi and v in liberi and d_ < 1e17:
                liberi -= {u, v}
                x = v
                while x != u:
                    px, e = tab[u][1][x]
                    extra.append(e)
                    x = px
        dispari = list(liberi)
    # multigrafo: lati originali + ripassi
    archi = [(e, False) for e in comp] + [(e, True) for e in extra]
    madj = defaultdict(list)
    for k, (e, _r) in enumerate(archi):
        a, b, _ = lati[e]
        madj[a].append(k)
        madj[b].append(k)
    cand = dispari if dispari else list(madj)
    s = min(cand, key=lambda u: math.dist(nodi[u], inizio_vicino))
    usato = [False] * len(archi)
    pos = defaultdict(int)
    pila, giro = [(s, None)], []
    while pila:
        u, k_in = pila[-1]
        while pos[u] < len(madj[u]) and usato[madj[u][pos[u]]]:
            pos[u] += 1
        if pos[u] == len(madj[u]):
            giro.append(pila.pop())
            continue
        k = madj[u][pos[u]]
        usato[k] = True
        a, b, _ = lati[archi[k][0]]
        pila.append((b if a == u else a, k))
    giro.reverse()
    out = [nodi[s]]
    u = s
    for v, k in giro[1:]:
        e = archi[k][0]
        a, b, p = lati[e]
        pts = p if a == u else p[::-1]
        out += pts[1:]
        u = v
    return out


def _pieno(m, ppm, passo=0.38, ang=45.0):
    """Pallino pieno: righe parallele a `ang` gradi, avanti e indietro (punti ≤ 2,5 mm). -> punti mm."""
    ys, xs = np.nonzero(m)
    if len(xs) < 3:
        return []
    X, Y = (xs + 0.5) / ppm, (ys + 0.5) / ppm
    a = math.radians(ang)
    u = X * math.cos(a) + Y * math.sin(a)        # lungo la riga
    v = -X * math.sin(a) + Y * math.cos(a)       # tra le righe
    out = []
    righe = np.arange(v.min() + passo / 2, v.max(), passo)
    for k, vv in enumerate(righe):
        sel = np.abs(v - vv) <= 0.5 / ppm + 1e-9
        if sel.sum() < 2:
            continue
        u0, u1 = u[sel].min() + 0.05, u[sel].max() - 0.05
        if u1 <= u0:
            continue
        if k % 2:
            u0, u1 = u1, u0
        n = max(1, int(math.ceil(abs(u1 - u0) / 2.5)))
        for t in range(n + 1):
            uu = u0 + (u1 - u0) * t / n
            out.append((uu * math.cos(a) - vv * math.sin(a), uu * math.sin(a) + vv * math.cos(a)))
    return out


def filo(colore):
    """Filo più vicino al colore scelto (nome in COLORI o «#rrggbb»), tra tutte le marche."""
    import fili as F
    from skimage.color import deltaE_ciede2000
    h = COLORI.get(colore, colore if isinstance(colore, str) and colore.startswith("#") else COLORI["rosso"])
    fl, labf = F.tabella_tutte()
    c = F.lab_hex([h])[0]
    d = deltaE_ciede2000(np.repeat(c[None], len(labf), 0), labf)
    k = int(np.argmin(d))
    return dict(fl[k], originale=h, de=round(float(d[k]), 1))


def digitalizza_redwork(A, colore="rosso", passate=1, staccati=True):
    """-> (EmbPattern, info). passate=1 filza singola (come i commerciali), 3 = filza tripla (linea marcata).
    staccati=False: via i pezzetti isolati più piccoli di STACCATO_MM (fiocchi di neve, puntini): ognuno è un
    salto, cioè un filo da tagliare a mano."""
    import pyembroidery as pe
    from componi import _fermo
    ppm = A["ppm"]
    lati, nodi, pallini = linee(A)
    # reti separate (pezzi di disegno non collegati)
    pad = {u: u for u in nodi}

    def rad(u):
        while pad[u] != u:
            pad[u] = pad[pad[u]]
            u = pad[u]
        return u
    for a, b, _ in lati:
        pad[rad(a)] = rad(b)
    comps = defaultdict(list)
    for e, (a, b, _) in enumerate(lati):
        comps[rad(a)].append(e)
    comps = list(comps.values())
    pieni = [p for p in (_pieno(m, ppm) for m in pallini) if len(p) >= 2]
    tolti = 0
    if not staccati:
        def piccolo(P):
            P = np.asarray(P)
            return math.hypot(*(P.max(0) - P.min(0))) < STACCATO_MM
        prima = len(comps) + len(pieni)
        comps = [c for c in comps if not piccolo([q for e in c for q in lati[e][2]])]
        # un pallino pieno resta se è attaccato a una linea (pupilla nell'occhio), va via se è isolato
        vicino = [nodi[u] for c in comps for e in c for u in lati[e][:2]] + [q for c in comps for e in c for q in lati[e][2][::3]]
        if vicino:
            from scipy.spatial import cKDTree
            albero = cKDTree(np.asarray(vicino))
            pieni = [p for p in pieni if albero.query(np.mean(p, 0))[0] <= STACCATO_MM]
        else:
            pieni = []
        tolti = prima - len(comps) - len(pieni)
    # ordine: sempre la rete più vicina a dove si è (si parte in alto a sinistra)
    qui = (0.0, 0.0)
    corse = []
    da_fare = list(comps)
    while da_fare:
        k = min(range(len(da_fare)), key=lambda i: min(math.dist(qui, nodi[u]) for e in da_fare[i] for u in lati[e][:2]))
        pts = _percorso(lati, nodi, da_fare.pop(k), qui)
        if passate >= 3:                         # filza tripla: avanti, indietro, avanti
            t = [pts[0]]
            for q in pts[1:]:
                t += [q, t[-1], q]
            pts = t
        if len(pts) >= 2:
            corse.append(pts)
            qui = pts[-1]
    # pallini pieni: se una linea ci passa accanto (≤ 1,5 mm) si fanno in deviazione da quella linea (si entra, si
    # riempie, si torna sulla linea: niente salto); gli altri da soli, dopo
    from scipy.spatial import cKDTree
    soli, deviazioni = [], defaultdict(list)
    if corse and pieni:
        tutti = np.concatenate([np.asarray(c) for c in corse])
        chi = np.concatenate([np.stack([np.full(len(c), k), np.arange(len(c))], 1) for k, c in enumerate(corse)])
        albero = cKDTree(tutti)
        for P in pieni:
            d, j = albero.query(np.asarray(P))
            i_ = int(np.argmin(d))
            if d[i_] <= 1.5:
                k, idx = chi[j[i_]]
                rif = corse[k][idx]
                P = P if math.dist(rif, P[0]) <= math.dist(rif, P[-1]) else P[::-1]
                deviazioni[int(k)].append((int(idx), P))
            else:
                soli.append(P)
    else:
        soli = list(pieni)
    for k, dev in deviazioni.items():
        c = corse[k]
        for idx, P in sorted(dev, key=lambda t: -t[0]):
            c = c[:idx + 1] + list(P) + [c[idx]] + c[idx + 1:]
        corse[k] = c
    while soli:
        k = min(range(len(soli)), key=lambda i: min(math.dist(qui, soli[i][0]), math.dist(qui, soli[i][-1])))
        P = soli.pop(k)
        P = P if math.dist(qui, P[0]) <= math.dist(qui, P[-1]) else P[::-1]
        corse.append(list(P))
        qui = P[-1]
    # catene: corse vicinissime (≤ 1 mm) si collegano con un punto, le altre col taglio
    catene = []
    for c in corse:
        if catene and math.dist(catene[-1][-1], c[0]) <= 1.0:
            catene[-1] = catene[-1] + c
        else:
            catene.append(list(c))
    f = filo(colore)
    out = pe.EmbPattern()
    th = pe.EmbThread()
    th.set_hex_color(f["hex"])
    th.description, th.catalog_number = f.get("nome", ""), f.get("num", "")
    out.add_thread(th)
    tagli = 0
    for k, c in enumerate(catene):
        if k:
            tagli += 1
            out.add_stitch_absolute(pe.TRIM, c[0][0] * 10, c[0][1] * 10)
        out.add_stitch_absolute(pe.JUMP, c[0][0] * 10, c[0][1] * 10)
        for x, y in _fermo(c):
            out.add_stitch_absolute(pe.STITCH, x * 10, y * 10)
    out.add_command(pe.END)
    n = sum(1 for st in out.stitches if (st[2] & 0xFF) == pe.STITCH)
    if n:
        x0, y0, x1, y1 = out.bounds()
    else:
        x0 = y0 = x1 = y1 = 0
    return out, {"punti": n, "colori": 1 if n else 0, "tagli": tagli, "reti": len(comps), "pieni": len(pieni), "tolti": tolti,
                 "lati": len(lati), "larg": round((x1 - x0) / 10, 1), "alt": round((y1 - y0) / 10, 1), "filo": f}
