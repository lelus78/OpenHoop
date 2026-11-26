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
# istruzioni, tabelle dei colori, PDF di montaggio: vanno con i disegni della loro cartella (o di quelle sotto)
ALLEGATI = {".pdf", ".txt", ".rtf", ".doc", ".docx", ".odt", ".htm", ".html", ".jpg", ".jpeg", ".png", ".gif", ".bmp"}
IMMAGINI = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
MAX_ALLEGATO = 20_000_000
MAX_ALLEGATI = 150_000_000           # in tutto, per importazione


PREFERITO = ".pes"                  # formato della macchina dell'utente (config «formato»): vince sugli altri


def prio(e):
    """Ordine dei formati quando lo stesso disegno c'è in più formati: prima quello della macchina dell'utente
    (i suoi colori sono quelli pensati per i suoi fili), poi l'ordine solito di riordina."""
    return 0 if e == PREFERITO else RI.PRIO.get(e, 98) + 1


def nome_zip(i):
    """Nome di un file nello zip. Senza il segno «UTF-8» Python lo legge come DOS occidentale (cp437): gli zip fatti
    su Windows russi (cp866) diventano «╜½·½Γ┐╗δ» invece di «логотипы». Si prova UTF-8, poi il cirillico DOS se
    dà parole intere in cirillico (almeno 3 lettere di fila), altrimenti resta com'è (accenti occidentali)."""
    if i.flag_bits & 0x800 or i.filename.isascii():
        return i.filename
    try:
        b = i.filename.encode("cp437")
    except UnicodeEncodeError:
        return i.filename
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        pass
    ru = b.decode("cp866", "replace")
    alti = [c for c in ru if not c.isascii()]
    if alti and all("Ѐ" <= c <= "ӿ" or c == "№" for c in alti) and re.search("[Ѐ-ӿ]{3}", ru):
        return ru
    return i.filename


def _nomi_zip(z):
    for i in z.infolist():
        i.filename = nome_zip(i)    # si legge sempre con l'oggetto ZipInfo: l'intestazione usa orig_filename
    return z


def _zip(nome, raw, out, prof=0, tot=None, allegati=None):
    """Scompatta in memoria (anche zip dentro zip, fino a 3 livelli). out: [(percorso_relativo, byte)].
    allegati (lista o None): ci finiscono [(percorso_relativo, byte)] di PDF, istruzioni, tabelle dei colori."""
    tot = tot if tot is not None else [0, 0]
    try:
        z = _nomi_zip(zipfile.ZipFile(io.BytesIO(raw)))
    except zipfile.BadZipFile:
        if prof == 0:                   # prima si scartava in silenzio: l'utente non capiva perché non entrava niente
            raise ValueError("zip rovinato o incompleto (si sta ancora scaricando?)")
        return
    base = os.path.splitext(os.path.basename(nome))[0]
    if allegati is not None:
        for i in z.infolist():
            e = os.path.splitext(i.filename)[1].lower()
            if not i.is_dir() and e in ALLEGATI and 0 < i.file_size <= MAX_ALLEGATO and "__macosx" not in i.filename.lower()                     and tot[1] + i.file_size <= MAX_ALLEGATI:
                tot[1] += i.file_size
                allegati.append((base + "/" + i.filename, z.read(i)))
    # dello stesso disegno in più formati (raccolte vendute con PES, DST, JEF, VP3… di ognuno) si legge solo il
    # formato migliore: lo zip pesa un settimo e non sfora il limite
    migliore = {}
    for i in z.infolist():
        e = os.path.splitext(i.filename)[1].lower()
        if i.is_dir() or e not in ACCETTATI or "__macosx" in i.filename.lower():
            continue
        k = i.filename if e in (".zip", ".xs") else _chiave(i.filename)
        if k not in migliore or prio(e) < prio(os.path.splitext(migliore[k].filename)[1].lower()):
            migliore[k] = i
    for i in migliore.values():
        if len(out) >= MAX_FILE:
            raise ValueError(f"più di {MAX_FILE} disegni: importa una parte alla volta")
        e = os.path.splitext(i.filename)[1].lower()
        tot[0] += i.file_size
        if tot[0] > MAX_BYTE:
            raise ValueError("archivio troppo grande")
        dati = z.read(i)
        rel = base + "/" + i.filename
        if e == ".zip":
            if prof < 3:
                _zip(rel, dati, out, prof + 1, tot, allegati)
        else:
            out.append((rel, dati))


_SUFF_FORMATO = re.compile(r"(?:[\s_.\-]+in)?[\s_.\-]+(?:" + "|".join(sorted((e.strip(".") for e in FORMATI), key=len, reverse=True))
                           + r")(?:[\s_\-]*(?:format|file|files))?$", re.I)


def _nome_pulito(b):
    """«4 in pes» / «rose_dst» / «cat-PES» -> «4» / «rose» / «cat» (il formato scritto nel nome non conta)."""
    p = _SUFF_FORMATO.sub("", b).strip(" _-.")
    return p or b


def _chiave(rel):
    """Stesso disegno = stessa cartella (senza le cartelle «PES», «DST»…) + stesso nome pulito."""
    parti = [p for p in re.split(r"[\\/]", rel) if p]
    cart = [p for p in parti[:-1] if not RI.tecnica(p)]
    return ("/".join(cart).lower(), _nome_pulito(os.path.splitext(parti[-1])[0]).lower())


def espandi(nome, raw, allegati=None):
    """Un file (o uno zip) -> [(percorso_relativo, byte)] dei soli file da ricamo (allegati: vedi _zip)."""
    e = os.path.splitext(nome)[1].lower()
    if e == ".zip":
        out = []
        _zip(nome, raw, out, allegati=allegati)
        return out
    return [(nome, raw)] if e in FORMATI else []


def _dir(rel):
    return "/".join(p for p in re.split(r"[\\/]", rel)[:-1] if p).lower()


def allegati_vicini(percorso, cache=None):
    """Disegno preso da una cartella del disco: gli allegati della sua cartella (o di quella sopra, se nella sua
    non ce ne sono: «Baby Dinosaur/istruzioni.pdf» per «Baby Dinosaur/1/4 in.pes»). -> [(percorso_rel, byte)]"""
    cache = {} if cache is None else cache
    d = os.path.dirname(os.path.abspath(percorso))
    for cart in (d, os.path.dirname(d)):
        if cart not in cache:
            trovati = []
            try:
                for f in sorted(os.listdir(cart)):
                    p = os.path.join(cart, f)
                    if os.path.splitext(f)[1].lower() in ALLEGATI and os.path.isfile(p) and 0 < os.path.getsize(p) <= MAX_ALLEGATO:
                        trovati.append((f, p))
            except OSError:
                pass
            cache[cart] = trovati
        if cache[cart]:
            return [(os.path.basename(d) + "/" + f, open(p, "rb").read()) for f, p in cache[cart]]
    return []


def assegna_allegati(gruppi, allegati):
    """Mette in ogni disegno (d["allegati"] = [(nome, byte)]) gli allegati della sua cartella o, se non ce ne sono,
    di quella sopra (PDF di una raccolta con i disegni divisi in sottocartelle per misura o per formato).
    Le immagini che sono solo le anteprime dei disegni (stesso nome) o troppe (una per disegno) si scartano."""
    per_cart = {}
    for rel, raw in allegati:
        per_cart.setdefault(_dir(rel), []).append((re.split(r"[\\/]", rel)[-1], raw))
    nomi_per_cart = {}
    for d in gruppi:
        for rel, _raw in d["file"].values():
            nomi_per_cart.setdefault(_dir(rel), set()).add(_nome_pulito(os.path.splitext(re.split(r"[\\/]", rel)[-1])[0]).lower())
    for d in gruppi:
        rel = next(iter(d["file"].values()))[0]
        c = _dir(rel)
        scelti = []
        for _ in range(3):                                   # la sua cartella, poi su di due livelli al massimo
            cand = per_cart.get(c)
            if cand:
                imm = [n for n, _r in cand if os.path.splitext(n)[1].lower() in IMMAGINI]
                nomi = nomi_per_cart.get(c, set())
                scelti = [(n, r) for n, r in cand if os.path.splitext(n)[1].lower() not in IMMAGINI
                          or (len(imm) <= 10 and _nome_pulito(os.path.splitext(n)[0]).lower() not in nomi)]
                break
            if "/" not in c:
                break
            c = c.rsplit("/", 1)[0]
        d["allegati"] = scelti


def raggruppa(files):
    """Stesso disegno in più formati (cartella + nome uguali, anche in sottocartelle «PES», «DST»…) -> uno solo.
    Restituisce [{"nome", "raccolta", "file": {ext: (percorso, byte)}}]."""
    disegni = {}
    for rel, raw in files:
        parti = [p for p in re.split(r"[\\/]", rel) if p]
        b, e = os.path.splitext(parti[-1])
        cart = [p for p in parti[:-1] if not RI.tecnica(p)]
        k = _chiave(rel)
        nb = _nome_pulito(b)
        # «4», «4 in», «5x7», «10 cm» da soli non dicono niente: «Baby Dinosaur 4 in»
        if len(nb) <= 16 and re.fullmatch(r"[\d\s_.\-x×]*(?:in|inch|inches|cm|mm|\")?[\d\s_.\-x×]*", nb, re.I) and re.search(r"\d", nb):
            parola = next((c for c in reversed(cart) if not re.fullmatch(r"[\d\s_.\-]+", c)), "")
            nb = f"{parola} {nb}".strip()
        d = disegni.setdefault(k, {"nome": nb, "raccolta": " - ".join(cart[-2:]) if cart else "", "file": {}})
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
    for ext in sorted(d["file"], key=lambda e: prio(e) if e != ".xs" else 999):
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
    # stesso disegno in più formati (cartella estratta da una raccolta PES+DST+JEF…): solo il formato migliore
    migliore = {}
    for p in out:
        e = os.path.splitext(p)[1].lower()
        k = p if e in (".zip", ".xs") else _chiave(os.path.relpath(p, cartella))
        if k not in migliore or prio(e) < prio(os.path.splitext(migliore[k])[1].lower()):
            migliore[k] = p
    out = list(migliore.values())
    if len(out) > MAX_FILE:
        raise ValueError(f"più di {MAX_FILE} disegni in questa cartella: importa una sottocartella alla volta")
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
