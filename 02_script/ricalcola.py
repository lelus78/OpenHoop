"""Ridimensionamento con RICALCOLO dei punti (quello che fanno TrueSizer / Embrilliance), fatto in casa.

Idea: un file PES/DST contiene solo punti, non gli "oggetti" del disegnatore. Li ricostruiamo:
  1. ogni tratto cucito (tra un salto e l'altro) viene diviso in pezzi di tipo
       - SATIN  (punto pieno: zig-zag, ogni punto torna indietro rispetto al precedente)
       - FILL   (riempimento tatami: file parallele di punti, che girano alla fine della fila)
       - RUN    (punto filza / contorni / sottopunto lineare)
  2. di ogni pezzo si ricava la forma (binari del satin, poligono + angolo + passo delle file del fill,
     linea del run) e i suoi parametri di cucitura (passo tra le file, lunghezza punto).
  3. si scala la FORMA e si rigenerano i punti con gli STESSI parametri di cucitura dell'originale
     => la densità resta quella pensata dal digitalizzatore, cambia solo la misura.

Uso:  python ricalcola.py file.pes 1.3 uscita.pes        (scala 130%)
      da codice: ricalcola(pattern, 1.3) -> (pattern_nuovo, rapporto)
"""
import math
import sys

import numpy as np
import pyembroidery as E
import pes_brother  # noqa: F401,E402  -- PEC senza il n. 64 «Applique» (vedi pes_brother.py)

SATIN, FILL, RUN = "satin", "fill", "run"


# ---------------------------------------------------------------- lettura
def tratti(pat):
    """-> lista di blocchi colore: (filo, [tratti]); tratto = array Nx2 in mm di punti cuciti consecutivi.
    trim_prima[i] = True se prima del tratto c'era un taglio filo."""
    out = []
    for blocco, filo in pat.get_as_colorblocks():
        lista, cur, taglio, t_cur = [], [], False, False
        for x, y, c in blocco:
            c &= 0xFF
            if c == E.STITCH:
                if not cur:
                    t_cur = taglio
                    taglio = False
                cur.append((x / 10.0, y / 10.0))
            else:
                if c == E.TRIM:
                    taglio = True
                if len(cur) > 1:
                    lista.append((np.array(cur), t_cur))
                # dopo un salto il primo punto cucito riparte dalla posizione del salto
                cur = [(x / 10.0, y / 10.0)] if c == E.JUMP else []
        if len(cur) > 1:
            lista.append((np.array(cur), t_cur))
        out.append((filo, lista))
    return out


# ---------------------------------------------------------------- classificazione
def _mediane_mobili(L, prima, dopo):
    """Mediana di L[k-prima : k+dopo] (tagliata ai bordi) per ogni k, come np.median sulla finestra."""
    n = len(L)
    w = prima + dopo
    pad = np.full(n + w, np.nan)
    pad[prima:prima + n] = L
    fin = np.lib.stride_tricks.sliding_window_view(pad, w)[:n]
    return np.nanmedian(fin, axis=1)


def classifica(P):
    """Etichetta ogni punto (segmento P[k]->P[k+1]) come SATIN / FILL / RUN, poi unisce i pezzetti troppo corti."""
    V = np.diff(P, axis=0)
    L = np.hypot(V[:, 0], V[:, 1])
    n = len(V)
    if n < 4:
        return [(0, n, RUN)]
    U = V / np.maximum(L, 1e-9)[:, None]
    dot_next = np.r_[(U[:-1] * U[1:]).sum(1), 1.0]
    rev = dot_next < -0.7                                             # il punto dopo torna indietro
    ang2 = np.arctan2(V[:, 1], V[:, 0]) * 2                           # angolo "doppio": direzione senza verso
    c, s = np.cos(ang2) * L, np.sin(ang2) * L
    # finestre [k-4, k+5) e [k-8, k+9) tagliate ai bordi: somme con le cumulate (vettoriale, prima era un ciclo)
    def somma(v, a, b):
        cs = np.r_[0.0, np.cumsum(v)]
        return cs[b] - cs[a]
    k = np.arange(n)
    a, b = np.maximum(0, k - 4), np.minimum(n, k + 5)
    A, B = np.maximum(0, k - 8), np.minimum(n, k + 9)
    revf = rev.astype(float)
    rf = somma(revf, a, b) / (b - a)
    rv = somma(revf, A, B) / (B - A)
    R = np.hypot(somma(c, A, B), somma(s, A, B)) / np.maximum(somma(L, A, B), 1e-9)     # 1 = tutti paralleli
    med9, med17 = _mediane_mobili(L, 4, 5), _mediane_mobili(L, 8, 9)
    satin = (rf > 0.75) & (med9 > 0.6)
    fill = ~satin & (R > 0.88) & (rv > 0.04) & (med17 > 0.8)
    lab = np.where(satin, SATIN, np.where(fill, FILL, RUN)).tolist()
    # gruppi contigui
    gr = []
    for k, t in enumerate(lab):
        if gr and gr[-1][2] == t:
            gr[-1][1] = k + 1
        else:
            gr.append([k, k + 1, t])
    # pezzi troppo corti -> si uniscono al precedente (o al successivo)
    cambiato = True
    while cambiato and len(gr) > 1:
        cambiato = False
        for j, g in enumerate(gr):
            if g[1] - g[0] < 6:
                if j > 0:
                    gr[j - 1][1] = g[1]
                else:
                    gr[j + 1][0] = g[0]
                gr.pop(j)
                cambiato = True
                break
        # riunisci vicini dello stesso tipo
        k = 1
        while k < len(gr):
            if gr[k][2] == gr[k - 1][2]:
                gr[k - 1][1] = gr[k][1]
                gr.pop(k)
            else:
                k += 1
    return [tuple(g) for g in gr]


# ---------------------------------------------------------------- utilità geometriche
def _ricampiona(linea, passi):
    """Punti a distanze 'passi' (array 0..1) lungo una polilinea."""
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(linea, axis=0).T))]
    if d[-1] < 1e-9:
        return np.repeat(linea[:1], len(passi), 0)
    t = np.asarray(passi) * d[-1]
    return np.stack([np.interp(t, d, linea[:, 0]), np.interp(t, d, linea[:, 1])], 1)


def _lung(linea):
    return float(np.hypot(*np.diff(linea, axis=0).T).sum()) if len(linea) > 1 else 0.0


# ---------------------------------------------------------------- rigenerazione per tipo
def _passo_file(Y):
    """Passo tra le file del riempimento: differenze tra le quote (ruotate) delle file."""
    y = np.sort(Y)
    gruppi = [[y[0]]]
    for v in y[1:]:
        if v - gruppi[-1][-1] < 0.12:
            gruppi[-1].append(v)
        else:
            gruppi.append([v])
    q = np.array([np.mean(g) for g in gruppi])
    d = np.diff(q)
    d = d[(d > 0.12) & (d < 3)]
    return float(np.median(d)) if len(d) else 0.4


def rifai_satin(Q, s, info):
    """Q = punti del zig-zag (estremi alternati sui due bordi). Si scalano i bordi e si rimette lo stesso passo.
    Colonne più larghe di MAX_SATIN: 'split satin' (punto spezzato con giunte sfalsate, come fanno i programmi pro)."""
    A, B = Q[0::2] * s, Q[1::2] * s
    if len(A) < 2 or len(B) < 2:
        return [tuple(p) for p in Q * s]
    passo = np.median(np.hypot(*np.diff(Q[0::2], axis=0).T))         # passo originale su un bordo
    passo = max(passo, 0.25)
    lun = max(_lung(A), _lung(B))
    n = max(2, int(round(lun / passo)))
    ta = np.linspace(0, 1, n + 1)
    tb = np.clip(ta + 0.5 / n, 0, 1)[:-1]
    pa, pb = _ricampiona(A, ta), _ricampiona(B, tb)
    seq = np.empty((len(pa) + len(pb), 2))
    seq[0::2], seq[1::2] = pa, pb
    lim = max(MAX_SATIN, float(np.hypot(*np.diff(Q, axis=0).T).max()) * 1.02)   # punti lunghi voluti dal disegnatore restano
    out = [tuple(seq[0])]
    for k in range(1, len(seq)):
        a, b = seq[k - 1], seq[k]
        d = math.dist(a, b)
        if d > lim:
            info["satin_spezzati"] += 1
            m = int(math.ceil(d / SPLIT_SATIN))
            sf = (0.18 if (k // 2) % 2 else -0.18) / m                # giunte alternate: niente "riga" visibile
            for t in np.arange(1, m) / m + sf:
                out.append(tuple(a + (b - a) * t))
        out.append(tuple(b))
    return out


MIN_PUNTO = 0.4        # mm: punti più corti si eliminano
MAX_SATIN = 7.0          # mm: oltre si spezza
SPLIT_SATIN = 4.5        # mm: lunghezza dei pezzi


def _passo_file_seq(R, VR, allineati):
    """Passo tra le file seguendo l'ordine di cucitura: fila = punti allineati consecutivi nello stesso verso;
    passo = mediana della distanza (perpendicolare) tra una fila e la successiva."""
    file, cur, verso = [], [], 0
    for k in range(len(VR)):
        if allineati[k]:
            v = 1 if VR[k, 0] > 0 else -1
            if cur and v != verso:
                file.append(cur)
                cur = []
            verso = v
            cur.append((R[k, 1] + R[k + 1, 1]) / 2)
        elif cur:
            file.append(cur)
            cur, verso = [], 0
    if cur:
        file.append(cur)
    y = np.array([np.mean(f) for f in file])
    d = np.abs(np.diff(y))
    d = d[(d > 0.1) & (d < 3)]
    return float(np.median(d)) if len(d) else 0.4


def _maschera(R, passo, ris=0.05):
    """Copertura del riempimento (coordinate ruotate) come immagine a 0,05 mm, buchi piccoli chiusi."""
    from PIL import Image, ImageDraw
    from scipy import ndimage
    x0, y0 = R.min(0) - 1
    x1, y1 = R.max(0) + 1
    W, H = int((x1 - x0) / ris) + 1, int((y1 - y0) / ris) + 1
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    pts = [((x - x0) / ris, (y - y0) / ris) for x, y in R]
    d.line(pts, fill=255, width=max(1, int(passo * 1.25 / ris)))
    m = np.asarray(im) > 0
    r = max(1, int(passo * 0.8 / ris))
    m = ndimage.binary_closing(m, structure=np.ones((2 * r + 1, 2 * r + 1)), iterations=1)
    m = ndimage.binary_opening(m, structure=np.ones((3, 3)))
    return m, x0, y0, ris


def rifai_fill(Q, s, info):
    V = np.diff(Q, axis=0)
    L = np.hypot(V[:, 0], V[:, 1])
    ang2 = np.arctan2(V[:, 1], V[:, 0]) * 2
    th = math.atan2((np.sin(ang2) * L).sum(), (np.cos(ang2) * L).sum()) / 2     # direzione delle file
    co, si = math.cos(-th), math.sin(-th)
    R = Q @ np.array([[co, si], [-si, co]])                           # file orizzontali
    VR = np.diff(R, axis=0)
    lr = np.hypot(*VR.T)
    allineati = (np.abs(VR[:, 1]) < 0.25 * np.maximum(lr, 1e-9)) & (lr > 0.3)
    if allineati.sum() < 3:
        return rifai_run(Q, s, info)
    passo = min(max(0.2, _passo_file_seq(R, VR, allineati)), 1.5)
    lpunto = min(max(float(np.median(lr[allineati])), 1.0), 7.0)
    # forma = immagine della copertura; le file nuove si leggono direttamente dall'immagine (ritorno alle coord. originali / s)
    m, mx0, my0, ris = _maschera(R, passo)
    Hm = m.shape[0]
    righe = []
    y = (my0 + 1) * s + passo / 2
    yfine = (my0 + Hm * ris - 1) * s
    k = 0
    while y < yfine:
        riga = int((y / s - my0) / ris)
        if 0 <= riga < Hm:
            r = m[riga]
            dif = np.diff(np.r_[0, r.astype(np.int8), 0])
            ini, fin = np.nonzero(dif == 1)[0], np.nonzero(dif == -1)[0]
            pezzi = [((mx0 + a * ris) * s, (mx0 + b * ris) * s) for a, b in zip(ini, fin)]
            righe.append([(a, b, y, k) for a, b in pezzi if b - a > 0.3])
        else:
            righe.append([])
        y += passo
        k += 1
    usati = [set() for _ in righe]
    out, salti = [], 0
    for r0 in range(len(righe)):
        for j0 in range(len(righe[r0])):
            if j0 in usati[r0]:
                continue
            r, j, verso = r0, j0, 1
            colonna = []
            while True:
                usati[r].add(j)
                a, b, yy, kk = righe[r][j]
                off = (kk % 3) / 3.0 * lpunto                         # sfalsamento tatami (1/3)
                xs = np.arange(math.floor((a - off) / lpunto) * lpunto + off, b, lpunto)
                xs = np.r_[a, xs[(xs > a + 0.3) & (xs < b - 0.3)], b]
                if verso < 0:
                    xs = xs[::-1]
                colonna += [(x, yy) for x in xs]
                verso = -verso
                if r + 1 >= len(righe):
                    break
                # pezzo della riga sotto che si sovrappone, vicino a dove sono arrivato
                xa = xs[-1]
                cand = [jj for jj, (aa, bb, _, _) in enumerate(righe[r + 1])
                        if jj not in usati[r + 1] and aa < b and bb > a]
                if not cand:
                    break
                j = min(cand, key=lambda jj: min(abs(righe[r + 1][jj][0] - xa), abs(righe[r + 1][jj][1] - xa)))
                r += 1
                # la fila nuova deve partire dal lato dove sono arrivato
                aa, bb = righe[r][j][0], righe[r][j][1]
                verso = 1 if abs(aa - xa) <= abs(bb - xa) else -1
            if out:
                salti += 1
                out.append(None)                                      # salto tra due colonne
            out += colonna
    info["salti"] += salti
    co, si = math.cos(th), math.sin(th)
    M = np.array([[co, si], [-si, co]])
    return [None if p is None else tuple(np.array(p) @ M) for p in out]


def rifai_run(Q, s, info):
    P = Q * s
    L = np.hypot(*np.diff(Q, axis=0).T)
    lp = float(np.median(L)) if len(L) else 2.5
    lp = min(max(lp, 0.8), 6.0)
    out = [P[0]]
    for k in range(1, len(P)):
        a, b = out[-1], P[k]
        d = math.dist(a, b)
        ultimo = k == len(P) - 1
        if d < 0.6 * lp and not ultimo:
            # con il rimpicciolimento i punti diventano troppo corti: si saltano i vertici quasi allineati
            if k + 1 < len(P):
                v1, v2 = b - a, P[k + 1] - b
                if np.dot(v1, v2) > 0.9 * np.linalg.norm(v1) * np.linalg.norm(v2):
                    continue
        n = max(1, int(round(d / lp))) if d > 1.4 * lp else 1
        for t in np.linspace(0, 1, n + 1)[1:]:
            out.append(a + (b - a) * t)
    return [tuple(p) for p in out]


# ---------------------------------------------------------------- tutto il disegno
FILL_PARALLELO = 0.8   # sotto: le file del riempimento non sono parallele in tutto il pezzo


def _parallelismo(Q):
    """1 = tutti i punti del pezzo nella stessa direzione (riempimento tatami vero), 0 = direzioni a caso."""
    V = np.diff(Q, axis=0)
    L = np.hypot(V[:, 0], V[:, 1])
    a2 = np.arctan2(V[:, 1], V[:, 0]) * 2
    lunghi = L > 0.8                                   # i punti di passaggio corti non contano
    if lunghi.sum() < 4:
        return 1.0
    L, a2 = L[lunghi], a2[lunghi]
    return float(math.hypot((np.cos(a2) * L).sum(), (np.sin(a2) * L).sum()) / L.sum())


def ricalcola(pat, s, colori_debug=False):
    """-> (EmbPattern nuovo, info). colori_debug=True: un colore per tipo (rosso satin, blu fill, verde run)."""
    info = {"satin": 0, "fill": 0, "run": 0, "satin_spezzati": 0, "salti": 0}
    nuovo = E.EmbPattern()
    DBG = {SATIN: 0xD03030, FILL: 0x3050D0, RUN: 0x30A040}
    primo_blocco = True
    for filo, lista in tratti(pat):
        if not lista:
            continue
        pezzi = []                                                    # (tipo, punti o None=salto)
        for P, taglio in lista:
            for a, b, t in classifica(P):
                Q = P[a:b + 1]
                info[t] += 1
                if t == FILL and _parallelismo(Q) < FILL_PARALLELO:
                    # riempimento «artistico» (raggi a ventaglio, pelo, punti liberi): rifarlo a file parallele
                    # ne cancella l'aspetto (orecchie dell'orsetto 44fuzzy310) => si scala e basta
                    info["fill_liberi"] = info.get("fill_liberi", 0) + 1
                    pts = [tuple(p) for p in Q * s]
                else:
                    pts = rifai_satin(Q, s, info) if t == SATIN else rifai_fill(Q, s, info) if t == FILL else rifai_run(Q, s, info)
                pezzi.append((t, pts, taglio and a == 0))
        if colori_debug:
            for t, pts, tg in pezzi:
                th = E.EmbThread()
                th.color = DBG[t]
                if not primo_blocco:
                    nuovo.add_command(E.COLOR_CHANGE)
                nuovo.add_thread(th)
                primo_blocco = False
                _emetti(nuovo, pts, True)
            continue
        if not primo_blocco:
            nuovo.add_command(E.COLOR_CHANGE)
        nuovo.add_thread(filo if filo is not None else E.EmbThread())
        primo_blocco = False
        for t, pts, tg in pezzi:
            _emetti(nuovo, pts, tg)
    nuovo.add_command(E.END)
    return nuovo, info


def _emetti(pat, pts, taglio):
    """Aggiunge una sequenza di punti (None = salto). Se il pezzo inizia lontano dall'ultimo punto: taglio + salto."""
    ultimo = pat.stitches[-1][:2] if pat.stitches else None
    salta = True
    n = len(pts)
    for j, p in enumerate(pts):
        if p is None:
            salta = True
            continue
        x, y = float(p[0]) * 10, float(p[1]) * 10
        fine_pezzo = j == n - 1 or pts[j + 1] is None
        if not salta and not fine_pezzo and math.dist(ultimo, (x, y)) < MIN_PUNTO * 10:
            continue                                                  # punto troppo corto: la macchina lo ammassa
        if salta:
            if ultimo is None or math.dist(ultimo, (x, y)) > 15 or taglio:   # > 1,5 mm
                if ultimo is not None:
                    pat.add_stitch_absolute(E.TRIM, *ultimo)
                pat.add_stitch_absolute(E.JUMP, x, y)
            salta, taglio = False, False
        pat.add_stitch_absolute(E.STITCH, x, y)
        ultimo = (x, y)


if __name__ == "__main__":
    src, sc, dst = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    p2, inf = ricalcola(E.read(src), sc)
    E.write(p2, dst)
    print(inf)
