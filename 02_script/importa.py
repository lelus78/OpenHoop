"""Importazione di disegni nuovi nel catalogo (file trascinati, una cartella, un link).

Legge zip (anche uno dentro l'altro), progetti xTool Studio (.xs, vedi xs_in_pes.py) e tutti i formati da ricamo
che legge pyembroidery (stessa preferenza di riordina.py: se lo stesso disegno c'è in più formati vince il PES).
Per ogni disegno: PES con l'angolo in alto a sinistra a 0,0 (Innov-is), misure, colori, punti, sequenza dei fili e
«firma» (densità dei punti su una griglia 8×8, la stessa di riordina.py per riconoscere i doppioni).

Il server (catalogo_server.py) tiene i disegni in attesa in _catalogo/importa/<id>/ finché non si confermano.
"""
import hashlib
import io
import ipaddress
import os
import re
import socket
import tempfile
import urllib.parse
import urllib.request
import zipfile

import pyembroidery as pe

import pes_brother  # noqa: F401  -- niente n. 64 «Applique» nella tabella colori PEC
import riordina as RI
import xs_in_pes as XS

FORMATI = set(RI.FORMATI) | {".xs"}
ACCETTATI = FORMATI | {".zip"}
MAX_FILE = 3000                      # file da ricamo per importazione (una cartella enorme non blocca il server)
MAX_BYTE = 400_000_000               # zip scompattati in tutto
UA = {"User-Agent": "archivio-ricami/1.0 (uso personale)"}


def _zip(nome, raw, out, prof=0, tot=None):
    """Scompatta in memoria (anche zip dentro zip, fino a 3 livelli). out: [(percorso_relativo, byte)]."""
    tot = tot if tot is not None else [0]
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return
    base = os.path.splitext(os.path.basename(nome))[0]
    for i in z.infolist():
        if i.is_dir() or len(out) >= MAX_FILE:
            continue
        e = os.path.splitext(i.filename)[1].lower()
        if e not in ACCETTATI or "__macosx" in i.filename.lower():
            continue
        tot[0] += i.file_size
        if tot[0] > MAX_BYTE:
            raise ValueError("archivio troppo grande")
        dati = z.read(i)
        rel = base + "/" + i.filename
        if e == ".zip":
            if prof < 3:
                _zip(rel, dati, out, prof + 1, tot)
        else:
            out.append((rel, dati))


def espandi(nome, raw):
    """Un file (o uno zip) -> [(percorso_relativo, byte)] dei soli file da ricamo."""
    e = os.path.splitext(nome)[1].lower()
    if e == ".zip":
        out = []
        _zip(nome, raw, out)
        return out
    return [(nome, raw)] if e in FORMATI else []


def raggruppa(files):
    """Stesso disegno in più formati (cartella + nome uguali, anche in sottocartelle «PES», «DST»…) -> uno solo.
    Restituisce [{"nome", "raccolta", "file": {ext: (percorso, byte)}}]."""
    disegni = {}
    for rel, raw in files:
        parti = [p for p in re.split(r"[\\/]", rel) if p]
        b, e = os.path.splitext(parti[-1])
        cart = [p for p in parti[:-1] if not RI.tecnica(p)]
        k = ("/".join(cart).lower(), b.lower())
        d = disegni.setdefault(k, {"nome": b, "raccolta": " - ".join(cart[-2:]) if cart else "", "file": {}})
        d["file"][e.lower()] = (rel, raw)
    return list(disegni.values())


def _leggi(ext, raw):
    """Byte di un file -> (EmbPattern, extra). pyembroidery legge da file: passa da un file temporaneo.
    extra (solo .xs): {"avviso": testo, "stampa": PNG da stampare (progetti ricamo + stampa DTG)}."""
    fd, tmp = tempfile.mkstemp(suffix=ext)
    extra = {}
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        if ext == ".xs":
            blocchi, info = XS.leggi_xs(tmp)
            for _ in range(5):                               # meno cambi di filo, come in xs_in_pes.converti
                nuovi = XS.raggruppa_colori(blocchi)
                if len(nuovi) == len(blocchi) and _cambi(nuovi) >= _cambi(blocchi):
                    break
                blocchi = nuovi
            png = tmp + ".stampa.png"
            try:
                st = XS.stampa_xs(tmp, png)
                if st:
                    extra["stampa"] = open(png, "rb").read()
                    bianco = len({b["colore"] for b in blocchi}) == 1 and \
                        min(int(blocchi[0]["colore"][k:k + 2], 16) for k in (1, 3, 5)) > 200
                    if bianco:                               # progetti DT (solo base bianca): si scartano
                        raise ValueError("progetto ricamo + stampa (xTool DT): il ricamo è solo la base bianca sotto "
                                         "la stampa a colori, sulla Brother da solo non serve. Non importato.")
                    else:
                        extra["avviso"] = (f"Contiene anche l'immagine a colori del disegno ({st[0]:g} × {st[1]:g} mm, "
                                           f"es. quella del generatore Atomm), salvata accanto al PES; il ricamo a "
                                           f"colori è completo da solo.")
            finally:
                try:
                    os.remove(png)
                except OSError:
                    pass
            if info.get("modo", "").startswith("forme"):
                extra["avviso"] = (extra.get("avviso", "") + " Punti calcolati qui dalle forme di xTool: "
                                   "fai una prova su un ritaglio.").strip()
            return XS.in_pattern(blocchi), extra
        return pe.read(tmp), extra
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _cambi(bl):
    return sum(1 for a, b in zip(bl, bl[1:]) if a["colore"] != b["colore"])


def firma(pat):
    """Densità dei punti su una griglia 8×8 (come riordina.py)."""
    x0, y0, x1, y1 = pat.bounds()
    g = [0] * 64
    gx, gy = max(x1 - x0, 1) / 8, max(y1 - y0, 1) / 8
    n = 0
    for x, y, c in pat.stitches:
        if c & pe.COMMAND_MASK == pe.STITCH:
            g[min(7, int((y - y0) / gy)) * 8 + min(7, int((x - x0) / gx))] += 1
            n += 1
    return [round(v / max(1, n), 4) for v in g]


def prepara(d):
    """Un disegno raggruppato -> (EmbPattern pronto per la Brother, info) o eccezione se nessun formato si legge."""
    errore = None
    for ext in sorted(d["file"], key=lambda e: RI.PRIO.get(e, -1) if e != ".xs" else 99):
        rel, raw = d["file"][ext]
        try:
            pat, extra = _leggi(ext, raw)
            if pat is None or not any(s[2] & pe.COMMAND_MASK == pe.STITCH for s in pat.stitches):
                raise ValueError("nessun punto")
            break
        except Exception as ex:                              # un formato rotto: provo gli altri
            errore = (str(ex) if isinstance(ex, ValueError) else f"{ext}: {type(ex).__name__} {ex}")[:300]
    else:
        raise ValueError(errore or "illeggibile")
    for th in pat.threadlist:                                # fili senza colore fanno cadere write_pes
        if th.color is None:
            th.color = 0x333333
    x0, y0, x1, y1 = pat.bounds()
    pat.translate(-x0, -y0)                                  # angolo in alto a sinistra a 0,0 (Innov-is)
    seq, colori = [], 0
    for _, filo in pat.get_as_colorblocks():
        colori += 1
        if len(seq) < 40:
            seq.append(filo.hex_color() if filo is not None and filo.color is not None else "#333333")
    info = {"nome": d["nome"], "raccolta": d["raccolta"], "origine": rel, "formato": ext,
            "larg": round((x1 - x0) / 10, 1), "alt": round((y1 - y0) / 10, 1), "colori": colori,
            "punti": sum(1 for s in pat.stitches if s[2] & pe.COMMAND_MASK == pe.STITCH),
            "sequenza": seq, "firma": firma(pat), "sha1": hashlib.sha1(raw).hexdigest()}
    if extra.get("avviso"):
        info["avviso"] = extra["avviso"]
    if extra.get("stampa"):
        info["_stampa"] = extra["stampa"]                    # byte del PNG: il server lo salva accanto, non nel JSON
    return pat, info


def miniatura_semplice(pat, S=160):
    """Come le miniature di riordina.py (linee dei punti su bianco): le impronte SigLIP del catalogo sono fatte su
    queste, quindi per confrontare un disegno nuovo con i 260k serve lo stesso tipo d'immagine."""
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = pat.bounds()
    k = (S - 8) / max(x1 - x0, y1 - y0, 1)
    img = Image.new("RGB", (S, S), "white")
    dr = ImageDraw.Draw(img)
    for blocco, filo in pat.get_as_colorblocks():
        col = filo.hex_color() if filo is not None and filo.color is not None else "#333333"
        prev = None
        for st in blocco:
            c = st[2] & 0xFF
            p = (4 + (st[0] - x0) * k, 4 + (st[1] - y0) * k)
            if c == 0 and prev is not None:
                dr.line([prev, p], fill=col, width=1)
            prev = p if c in (0, 1) else None
    return img


def pes_bytes(pat):
    b = io.BytesIO()
    pe.write_pes(pat, b, {"pes version": 6})
    return b.getvalue()


def file_in_cartella(cartella, giorni=None):
    """File da ricamo/zip in una cartella (e sottocartelle); `giorni`: solo quelli scaricati di recente."""
    import time
    if not os.path.isdir(cartella):
        raise ValueError("cartella non trovata")
    limite = time.time() - giorni * 86400 if giorni else 0
    out = []
    for dp, dn, fn in os.walk(cartella):
        dn[:] = [x for x in dn if not x.startswith((".", "$"))]
        for f in fn:
            p = os.path.join(dp, f)
            if os.path.splitext(f)[1].lower() in ACCETTATI:
                try:
                    if os.path.getmtime(p) >= limite and os.path.getsize(p) < MAX_BYTE:
                        out.append(p)
                except OSError:
                    pass
            if len(out) >= MAX_FILE:
                return out
    return out


def scarica_link(url):
    """Scarica un file da un link diretto (https, mai indirizzi della rete di casa). -> (nome_file, byte)."""
    u = urllib.parse.urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("serve un link http(s)")
    for info in socket.getaddrinfo(u.hostname, 443):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("indirizzo non ammesso")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        raw = r.read(MAX_BYTE + 1)
        cd = r.headers.get("Content-Disposition") or ""
        ct = (r.headers.get("Content-Type") or "").lower()
        finale = r.geturl()
    if len(raw) > MAX_BYTE:
        raise ValueError("file troppo grande")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
    nome = urllib.parse.unquote(m.group(1)) if m else os.path.basename(urllib.parse.urlparse(finale).path)
    nome = nome or "scaricato"
    if os.path.splitext(nome)[1].lower() not in ACCETTATI:
        if raw[:2] == b"PK":
            nome += ".zip"
        elif "html" in ct:
            raise ValueError("il link porta a una pagina, non a un file: su siti come Atomm scarica il file a mano "
                             "(serve il tuo accesso) e trascinalo qui, o usa la cartella Download")
        else:
            raise ValueError("il file scaricato non è un formato da ricamo")
    return nome, raw
