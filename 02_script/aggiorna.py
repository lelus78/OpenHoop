"""OpenHoop: aggiornamenti dalla pagina delle release di GitHub.

- controlla()  legge l'ultima release pubblica (una richiesta GET, nessun dato dell'utente) e dice se è più nuova
- installa()   scarica lo zip, controlla l'impronta SHA-256 pubblicata nella release (file «<zip>.sha256»),
               mette da parte la versione attuale in «_versione_precedente», copia i file nuovi sopra quelli del
               programma (mai .venv, configurazione, archivio dei disegni, catalogo), installa le librerie nuove
- ripristina() rimette la versione messa da parte

La release deve avere due file: OpenHoop-<versione>.zip (git archive --prefix=OpenHoop/) e
OpenHoop-<versione>.zip.sha256 (l'impronta, prima parola della riga).
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

QUI = os.path.dirname(os.path.abspath(__file__))
RADICE = os.path.dirname(QUI)                         # cartella del programma (quella con OpenHoop.exe)
REPO = "lelus78/OpenHoop"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PRECEDENTE = os.path.join(RADICE, "_versione_precedente")
# cosa l'aggiornamento può scrivere: i file del programma, mai i dati
NON_TOCCARE = {".venv", "_versione_precedente", ".git"}
MAX_ZIP = 300_000_000


def versione():
    try:
        return open(os.path.join(QUI, "versione.txt"), encoding="utf-8").read().strip()
    except OSError:
        return "1.0"


def _numeri(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def piu_nuova(a, b):
    """a è più nuova di b?"""
    return _numeri(a) > _numeri(b)


def _get(url, accetta="application/json", limite=MAX_ZIP):
    req = urllib.request.Request(url, headers={"User-Agent": f"OpenHoop/{versione()}", "Accept": accetta})
    with urllib.request.urlopen(req, timeout=60) as r:
        dati = r.read(limite + 1)
    if len(dati) > limite:
        raise ValueError("file troppo grande")
    return dati


def controlla():
    """-> {attuale, nuova, note, data, zip, sha, pagina} (nuova = None se si è già all'ultima)."""
    r = json.loads(_get(API).decode("utf-8"))
    tag = str(r.get("tag_name") or "").lstrip("vV")
    zip_url = sha_url = None
    for a in r.get("assets") or []:
        n = str(a.get("name") or "")
        if n.lower().endswith(".zip"):
            zip_url = a.get("browser_download_url")
        elif n.lower().endswith(".zip.sha256"):
            sha_url = a.get("browser_download_url")
    att = versione()
    nuova = tag if tag and piu_nuova(tag, att) and zip_url and sha_url else None
    return {"attuale": att, "nuova": nuova, "note": str(r.get("body") or "")[:6000], "data": r.get("published_at"),
            "zip": zip_url, "sha": sha_url, "pagina": r.get("html_url"), "controllato": time.time()}


def _sicuro(nome):
    """Percorso dentro lo zip -> relativo alla cartella del programma, o None se non si deve scrivere."""
    p = nome.replace("\\", "/")
    if not p.startswith("OpenHoop/") or p.endswith("/"):
        return None
    rel = p[len("OpenHoop/"):]
    parti = [x for x in rel.split("/") if x]
    if not parti or any(x in ("..", ".") or ":" in x for x in parti) or parti[0] in NON_TOCCARE:
        return None
    return os.path.join(*parti)


def installa(info, avanza=print):
    """Scarica e installa la release descritta da info (da controlla()). Non riavvia: lo fa chi chiama."""
    if not info.get("nuova"):
        raise ValueError("nessuna versione nuova")
    if not str(info.get("zip", "")).startswith("https://github.com/") or \
            not str(info.get("sha", "")).startswith("https://github.com/"):
        raise ValueError("indirizzo di scaricamento non valido")
    avanza("scarico la versione " + info["nuova"])
    raw = _get(info["zip"], "application/octet-stream")
    atteso = _get(info["sha"], "text/plain", 10_000).decode("utf-8", "replace").split()[0].lower()
    if hashlib.sha256(raw).hexdigest() != atteso:
        raise ValueError("il file scaricato non corrisponde all'impronta della release: aggiornamento annullato")
    z = zipfile.ZipFile(io.BytesIO(raw))
    voci = [(x, _sicuro(x.filename)) for x in z.infolist() if not x.is_dir()]
    voci = [(x, r) for x, r in voci if r]
    if not any(r.replace("\\", "/") == "02_script/catalogo_server.py" for _x, r in voci):
        raise ValueError("lo zip non sembra OpenHoop")
    # la versione attuale da parte (solo i file che l'aggiornamento sostituisce, più versione.txt)
    avanza("metto da parte la versione attuale")
    shutil.rmtree(PRECEDENTE, ignore_errors=True)
    os.makedirs(PRECEDENTE)
    nuovi = []
    for _x, rel in voci:
        dst = os.path.join(RADICE, rel)
        if os.path.exists(dst):
            os.makedirs(os.path.dirname(os.path.join(PRECEDENTE, rel)), exist_ok=True)
            shutil.copy2(dst, os.path.join(PRECEDENTE, rel))
        else:
            nuovi.append(rel)
    with open(os.path.join(PRECEDENTE, "aggiornamento.json"), "w", encoding="utf-8") as fh:
        json.dump({"da": versione(), "a": info["nuova"], "data": time.strftime("%Y-%m-%d %H:%M"), "nuovi": nuovi},
                  fh, ensure_ascii=False)
    req_prima = _leggi(os.path.join(RADICE, "requirements.txt"))
    avanza("copio i file nuovi")
    for x, rel in voci:
        _scrivi(os.path.join(RADICE, rel), z.read(x))
    if _leggi(os.path.join(RADICE, "requirements.txt")) != req_prima:
        avanza("installo le librerie nuove")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", os.path.join(RADICE, "requirements.txt"),
                        "--quiet", "--disable-pip-version-check"], check=False, timeout=1800)
    avanza("fatto: riavvio")
    return info["nuova"]


def _leggi(f):
    try:
        return open(f, "rb").read()
    except OSError:
        return b""


def _scrivi(dst, dati):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".nuovo"
    with open(tmp, "wb") as fh:
        fh.write(dati)
    try:
        os.replace(tmp, dst)
    except PermissionError:                           # OpenHoop.exe è in uso: si sposta, il nuovo vale dal prossimo avvio
        vecchio = dst + ".vecchio"
        try:
            os.remove(vecchio)
        except OSError:
            pass
        os.rename(dst, vecchio)
        os.replace(tmp, dst)


def precedente():
    """-> {da, a, data} dell'ultimo aggiornamento, se si può tornare indietro."""
    try:
        return json.load(open(os.path.join(PRECEDENTE, "aggiornamento.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ripristina(avanza=print):
    p = precedente()
    if not p:
        raise ValueError("non c'è una versione precedente")
    avanza("rimetto la versione " + p["da"])
    for dp, _dn, fn in os.walk(PRECEDENTE):
        for f in fn:
            src = os.path.join(dp, f)
            rel = os.path.relpath(src, PRECEDENTE)
            if rel == "aggiornamento.json":
                continue
            _scrivi(os.path.join(RADICE, rel), open(src, "rb").read())
    for rel in p.get("nuovi", []):                    # file che la versione nuova aveva aggiunto
        if _sicuro("OpenHoop/" + rel.replace("\\", "/")):
            try:
                os.remove(os.path.join(RADICE, rel))
            except OSError:
                pass
    shutil.rmtree(PRECEDENTE, ignore_errors=True)
    return p["da"]
