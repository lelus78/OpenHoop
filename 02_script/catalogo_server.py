"""OpenHoop (catalogo ricami): server locale (http://127.0.0.1:8766) con ricerca 'alla Immich' (CLIP multilingue),
selezione multipla, preferiti / già ricamati, simili, esportazione su cartella o chiavetta USB.

Uso: python catalogo_server.py [cartella_Ricami_ordinati] [--no-browser]
"""
import base64
import ctypes
import io
import json
import math
import os
import re
import shutil
import string
import sys
import threading
import time
import pes_brother  # noqa: F401,E402  -- PEC senza il n. 64 «Applique» (vedi pes_brother.py)
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

import config as CONFIG  # noqa: E402  -- impostazioni dell'utente (%APPDATA%\OpenHoop\config.json)
import macchine as MACCHINE  # noqa: E402
CFG = CONFIG.leggi()
LINGUE = ("it", "en", "fr", "es")


def diz(lingua=None):
    """Dizionario frase italiana -> traduzione (lingue/<lingua>.json); vuoto per l'italiano."""
    l = lingua or CFG.get("lingua") or "it"
    if l == "it" or l not in LINGUE:
        return {}
    try:
        return json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lingue", l + ".json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def tr(s):
    """Nomi di cartelle e temi creati dal server, nella lingua scelta all'avvio."""
    return _DIZ.get(s, s)


_DIZ = diz()


def _hf_offline():
    """Modelli già scaricati -> niente richieste a internet a ogni avvio (si parte prima e funziona anche senza
    connessione). Se ne manca uno (prima installazione, lingua nuova) si lascia scaricare."""
    if os.environ.get("HF_HUB_OFFLINE"):
        return
    base = os.path.join(os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface"), "hub")
    serve = ["google/siglip2-so400m-patch16-384"]
    trad = {"it": "Helsinki-NLP/opus-mt-it-en", "fr": "Helsinki-NLP/opus-mt-fr-en",
            "es": "Helsinki-NLP/opus-mt-es-en"}.get(CFG.get("lingua") or "it")
    if trad:
        serve.append(trad)
    if all(os.path.isdir(os.path.join(base, "models--" + m.replace("/", "--"), "snapshots")) for m in serve):
        os.environ["HF_HUB_OFFLINE"] = "1"


_hf_offline()


AVVIO = int(time.time())


def lingua_js():
    l = CFG.get("lingua") or "it"
    return (f"window.AVVIO={AVVIO};window.LINGUA={json.dumps(l)};window.UNITA={json.dumps(CFG.get('unita') or 'mm')};window.DIZ={json.dumps(diz(l), ensure_ascii=False)};\n"
            + open(os.path.join(QUI, "lingue", "i18n.js"), encoding="utf-8").read())
# archivio: dalla riga di comando, se no dalle impostazioni. Senza archivio pronto -> PRIMO AVVIO (catalogo vuoto,
# la pagina mostra la procedura guidata: lingua, cartelle, macchina, poi il riordino)
OUT = next((a for a in sys.argv[1:] if not a.startswith("--")), None) or CFG.get("archivio") or ""
if not OUT:
    OUT = os.path.join(CONFIG.CARTELLA, "archivio-vuoto")
CAT = os.path.join(OUT, "_catalogo")
PRIMO_AVVIO = not os.path.exists(os.path.join(CAT, "dati.js"))
if __name__ != "__mp_main__":
    os.makedirs(CAT, exist_ok=True)
QUI = os.path.dirname(os.path.abspath(__file__))
PORTA = int(os.environ.get("PORTA_CATALOGO") or 8766)    # OpenHoop.exe usa la stessa (PORTA_CATALOGO per le prove)
PUNTI_AL_MINUTO = 650          # velocità media macchina Brother domestica
CAMBIO_COLORE_S = 30

# i processi che disegnano le miniature (spawn su Windows) reimportano questo file: non devono caricare il catalogo
FIGLIO = __name__ == "__mp_main__"
TELAIO, DATI, LOW, NOMI = (130, 180), [], [], []
PATCH_DI, PATCH_ORIG = {}, {}                           # toppe in libreria: origine -> [toppe], toppa -> origine
TOLTI = set()                                           # indici eliminati (cestino) o con il file sparito
N_DATIJS = 0
if not FIGLIO:
    if PRIMO_AVVIO:                                         # archivio ancora da fare: catalogo vuoto
        _t = "const TELAIO=[130,180];\nconst DATI=[];"
    else:
        with open(os.path.join(CAT, "dati.js"), encoding="utf-8") as fh:
            _t = fh.read()
    TELAIO = json.loads(_t.split("const TELAIO=", 1)[1].split(";", 1)[0])
    DATI = json.loads(_t.split("const DATI=", 1)[1].rstrip().rstrip(";"))
    N_DATIJS = len(DATI)                                    # le impronte di clip.npy coprono solo questi
    # temi assegnati guardando l'immagine (smista_varie.py): solo nel catalogo, i file restano in VARIE
    try:
        _auto = json.load(open(os.path.join(CAT, "temi_auto.json"), encoding="utf-8"))
        for d in DATI:
            if d[0] in _auto:
                d[1] = _auto[d[0]][0]
    except (OSError, ValueError):
        pass
    # disegni digitalizzati e confermati dal digitalizzatore, e disegni importati (se dati.js viene rifatto possono
    # già esserci)
    _gia = {d[0] for d in DATI}
    for _f in ("digitalizzati.json", "importati.json"):
        try:
            _nuovi = [d for d in json.load(open(os.path.join(CAT, _f), encoding="utf-8")) if d[0] not in _gia]
            DATI += _nuovi
            _gia.update(d[0] for d in _nuovi)
        except (OSError, ValueError):
            pass
    # toppe salvate in libreria accanto al disegno d'origine (patch.json: [{voce, origine}])
    try:
        for _p in json.load(open(os.path.join(CAT, "patch.json"), encoding="utf-8")):
            if _p["voce"][0] not in _gia and os.path.exists(os.path.join(OUT, _p["voce"][0])):
                DATI.append(_p["voce"])
                _gia.add(_p["voce"][0])
                PATCH_DI.setdefault(_p["origine"], []).append(_p["voce"][0])
                PATCH_ORIG[_p["voce"][0]] = _p["origine"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    # versioni dei colori di un disegno («Scarta ma prendi i suoi colori», poi si sceglie nella scheda): il file
    # ha già i colori di quella attiva, qui solo la sequenza per miniature e fili (dati.js non si riscrive)
    try:
        _cv = json.load(open(os.path.join(CAT, "colori_varianti.json"), encoding="utf-8"))
        for d in DATI:
            if d[0] in _cv and len(d) > 7:
                _v = _cv[d[0]]
                d[7] = [f[0] for f in _v["var"][_v["attiva"]]["fili"]][:40]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    LOW = [d[0].lower() for d in DATI]
    NOMI = [os.path.splitext(os.path.basename(d[0]))[0] for d in DATI]
IDX = {d[0]: n for n, d in enumerate(DATI)}


# ---------------- serie: stessa raccolta d'origine + stesso nome senza il numero finale (Des22_01..Des22_75) ----------------
def chiave_serie(percorso):
    parti = percorso.replace("/", "\\").split("\\")
    racc = "\\".join(parti[1:-1]).lower()          # senza la cartella del tema: una serie può stare in più temi
    base = re.sub(r"[\s_\-.]*\d+[a-z]?$", "", os.path.splitext(parti[-1])[0].lower())
    # es. "Des22_01" -> "des22", "IG01" -> "ig", "Rosa2" -> "rosa"
    return racc, base


import autori as AUT  # noqa: E402
AUTORE = [AUT.autore_di(d[0]) for d in DATI]

# varianti di misura (Hdsmm4a/4b/4c): calcolate da varianti.py (nome + immagine + sagoma) -> _catalogo/varianti.json
VAR, GRUPPO = {}, {}
try:
    if FIGLIO:                      # nei processi delle miniature DATI è vuoto: niente varianti (prima li faceva morire)
        raise OSError
    for _k, _g in json.load(open(os.path.join(CAT, "varianti.json"))).items():
        VAR[int(_k)] = _g
        GRUPPO.setdefault(_g, []).append(int(_k))
    for _g in GRUPPO.values():
        _g.sort(key=lambda i: -DATI[i][2] * DATI[i][3])           # dal più grande al più piccolo
except (OSError, ValueError):
    pass


def raggruppa(risultati):
    """Una sola scheda per gruppo di varianti: il più grande tra quelli rimasti dopo i filtri, al posto del primo."""
    presenti = {}
    for i, _ in risultati:
        if i in VAR:
            presenti.setdefault(VAR[i], []).append(i)
    out, visti = [], set()
    tutti = {i for i, _ in risultati}
    for i, sc in risultati:
        o = PATCH_ORIG.get(DATI[i][0])                 # toppa: resta col disegno d'origine (se c'è anche lui)
        if o is not None and IDX.get(o) in tutti:
            continue
        g = VAR.get(i)
        if g is None:
            out.append((i, sc))
        elif g not in visti:
            visti.add(g)
            out.append((max(presenti[g], key=lambda j: DATI[j][2] * DATI[j][3]), sc))
    return out


SERIE, RACC_ORIG = {}, {}
for _n, _d in enumerate(DATI):
    _k = chiave_serie(_d[0])
    SERIE.setdefault(_k, []).append(_n)
    RACC_ORIG.setdefault(_k[0], []).append(_n)


def serie_di(i):
    """La serie del disegno; se è da solo (nomi tutti diversi) tutta la sua raccolta d'origine."""
    k = chiave_serie(DATI[i][0])
    g = SERIE.get(k, [i])
    return g if len(g) > 1 or not k[0] else RACC_ORIG.get(k[0], [i])

# ---------------- SigLIP (facoltativo: se clip.npy non c'è funziona la ricerca per nome) ----------------
EMB = None
_n_emb = 0
_emb_firma = None


def carica_emb():
    """(Ri)legge clip.npy se è cambiato: mentre indicizza_clip.py lavora la ricerca per significato
    funziona già sulla parte pronta e cresce da sola (controllo ogni 2 minuti)."""
    global EMB, _n_emb, _emb_firma
    f_npy, f_n = os.path.join(CAT, "clip.npy"), os.path.join(CAT, "clip_n.txt")
    try:
        firma = (os.path.getmtime(f_npy), os.path.getmtime(f_n) if os.path.exists(f_n) else 0)
        if firma == _emb_firma:
            return
        n = int(open(f_n).read()) if os.path.exists(f_n) else None
        e = np.load(f_npy).astype(np.float32)
    except (OSError, ValueError, EOFError):
        return                                     # file in scrittura: riprovo al giro dopo
    n = len(e) if n is None else min(n, len(e))
    # impronte dei disegni importati dopo (clip_extra.npy + clip_extra.json = percorsi): in coda, alla loro posizione
    try:
        extra = np.load(os.path.join(CAT, "clip_extra.npy")).astype(np.float32)
        percorsi = json.load(open(os.path.join(CAT, "clip_extra.json"), encoding="utf-8"))
        pos = [(IDX.get(p), k) for k, p in enumerate(percorsi) if IDX.get(p) is not None and IDX.get(p) >= n]
        if pos and n >= N_DATIJS:                           # solo a indicizzazione di dati.js finita
            e = np.vstack([e[:n], np.zeros((len(DATI) - n, e.shape[1]), np.float32)])
            for i, k in pos:
                e[i] = extra[k]
            n = len(DATI)
    except (OSError, ValueError):
        pass
    EMB, _n_emb, _emb_firma = e, n, firma


def _sorveglia_emb():
    while True:
        time.sleep(120)
        carica_emb()


if not FIGLIO:
    carica_emb()
_txt_model = None
_txt_lock = threading.Lock()


MODELLO = "google/siglip2-so400m-patch16-384"


def emb_testo(q):
    """Impronta della domanda con lo stesso modello usato per le immagini (SigLIP 2, multilingue)."""
    global _txt_model
    import torch
    with _txt_lock:
        if _txt_model is None:
            from transformers import AutoModel, AutoProcessor
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            m = AutoModel.from_pretrained(MODELLO, torch_dtype=torch.float16 if dev == "cuda" else torch.float32)
            # tutto il modello nello stesso formato: se un altro modello (traduzione) si carica nello stesso momento,
            # transformers può lasciarne una parte in float32 («mat1 and mat2 must have the same dtype»)
            m = m.to(dev, dtype=torch.float16 if dev == "cuda" else torch.float32)
            _txt_model = (m.eval(), AutoProcessor.from_pretrained(MODELLO), dev)
        m, p, dev = _txt_model
        x = p(text=[q.lower()], padding="max_length", max_length=64, return_tensors="pt").to(dev)
        with torch.no_grad():
            o = m.get_text_features(**x)
        o = o if torch.is_tensor(o) else (o.pooler_output if getattr(o, "pooler_output", None) is not None else o[0])
        v = o.float().cpu().numpy()[0]
    return (v / np.linalg.norm(v)).astype(np.float32)


def emb_immagine(img):
    """Impronta SigLIP di un'immagine PIL (per i disegni importati: tema proposto e ricerca per significato)."""
    import torch
    emb_testo("disegno")                                    # carica il modello, se non c'è ancora
    with _txt_lock:
        m, p, dev = _txt_model
        x = p(images=[img.convert("RGB")], return_tensors="pt")
        with torch.no_grad():
            o = m.get_image_features(pixel_values=x["pixel_values"].to(dev, m.dtype))
        o = o if torch.is_tensor(o) else (o.pooler_output if getattr(o, "pooler_output", None) is not None else o[0])
        v = o.float().cpu().numpy()[0]
    return (v / np.linalg.norm(v)).astype(np.float32)


# ---------------- stato persistente: preferiti / già ricamati (per percorso, sopravvive a un nuovo riordino) ----------------
F_STATO = os.path.join(CAT, "stato.json")
DIARIO_DIR = os.path.join(CAT, "diario")
try:
    STATO = json.load(open(F_STATO, encoding="utf-8"))
except (OSError, ValueError):
    STATO = {}
SET = {k: set(STATO.get(k, [])) for k in ("pref", "ric")}             # percorsi
RACCOLTE = {k: list(v) for k, v in STATO.get("raccolte", {}).items()}  # nome -> [percorsi]
DIARIO = STATO.get("diario", [])                                      # [{id, percorso, data, su, note, foto}]
_stato_lock = threading.Lock()

# ---------------- fili: più kit, ogni colore del disegno -> filo più vicino tra TUTTI quelli che ho ----------------
import fili as FILI  # noqa: E402
SOGLIA_UGUALE, SOGLIA_OK = 5.0, 10.0          # ΔE: <5 praticamente uguale, <10 simile e accettabile, oltre "manca"
FILI_BASE = CFG.get("fili") if CFG.get("fili") in FILI.marche() else FILI.MARCA_PREDEFINITA
KIT = STATO.get("kit") or [FILI_BASE]                                 # marche/kit che ho; il primo = per la spesa
# telai (mm, larghezza × altezza): montati in verticale => la larghezza è sempre il lato corto. Di base quelli
# della marca scelta al primo avvio (macchine.py), poi quelli che l'utente modifica
TELAI = sorted(([min(t), max(t)] for t in (STATO.get("telai") or CFG.get("telai") or [[100, 100], [130, 180], [160, 260]])),
               key=lambda t: t[0] * t[1])
POSS = set(STATO.get("posseduti", []))                                # 'Marca|num' dei fili che ho
if "posseduti" not in STATO:                                          # prima volta: il kit della marca scelta completo
    POSS = {f"{FILI_BASE}|{f['num']}" for f in FILI.tabella(FILI_BASE)[0]}
COL_HEX, COL_LAB, D_IDS, D_PTR = [], None, None, None                 # colori unici del catalogo e indice per disegno
OWN, OWN_IDX, OWN_DE, PRIM_IDX, MANC = [], None, None, None, None


def prepara_colori():
    """Una volta all'avvio: colori unici del catalogo e, per ogni disegno, i suoi colori distinti (formato CSR)."""
    global COL_HEX, COL_LAB, D_IDS, D_PTR
    pos = {}
    ids, ptr = [], [0]
    for d in DATI:
        seq = d[7] if len(d) > 7 else []
        for h in dict.fromkeys(h for h in seq if isinstance(h, str) and len(h) == 7):
            ids.append(pos.setdefault(h, len(pos)))
        ptr.append(len(ids))
    COL_HEX = list(pos)
    COL_LAB = FILI.lab_hex(COL_HEX) if COL_HEX else np.zeros((0, 3))
    D_IDS, D_PTR = np.array(ids, dtype=np.int64), np.array(ptr, dtype=np.int64)
    COL_POS.clear()
    COL_POS.update(pos)


COL_POS = {}


def calcola_fili():
    """Ricalcola gli abbinamenti con i fili che ho (veloce: lavora sui colori unici, non sui disegni)."""
    global OWN, OWN_IDX, OWN_DE, PRIM_IDX, MANC
    OWN, lab = FILI.posseduti(POSS)
    OWN_IDX, OWN_DE = FILI.vicini(COL_LAB, lab)
    PRIM_IDX, _ = FILI.vicini(COL_LAB, FILI.tabella(KIT[0])[1])
    manca_col = (OWN_DE > SOGLIA_OK).astype(np.int32)
    cs = np.concatenate([[0], np.cumsum(manca_col[D_IDS])]) if len(D_IDS) else np.zeros(1)
    MANC = cs[D_PTR[1:]] - cs[D_PTR[:-1]]                             # colori mancanti per disegno


def mancano(i):
    return int(MANC[i]) if MANC is not None and i < len(MANC) else 0


def salva_stato():
    with _stato_lock:
        dati = {k: sorted(v) for k, v in SET.items()}
        dati.update(kit=KIT, posseduti=sorted(POSS), raccolte=RACCOLTE, diario=DIARIO, telai=TELAI)
        tmp = F_STATO + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(dati, fh, ensure_ascii=False)
        os.replace(tmp, F_STATO)


def info_fili(i):
    """Fili del disegno nell'ordine di ricamo: filo mio più vicino (con quanto è simile) o 'manca' + cosa comprare."""
    orig = []
    try:
        import pyembroidery
        orig = [(t.brand or "", t.catalog_number or "", t.description or "")
                for t in pyembroidery.read(os.path.join(OUT, DATI[i][0])).threadlist]
    except Exception:
        pass
    prim = FILI.tabella(KIT[0])[0]
    out = []
    for n, h in enumerate(DATI[i][7] if len(DATI[i]) > 7 else []):
        c = COL_POS.get(h)
        o = orig[n] if n < len(orig) else ("", "", "")
        r = {"hex": h, "orig": " ".join(x for x in o if x).strip()}
        if c is not None and OWN_IDX[c] >= 0 and OWN_DE[c] <= SOGLIA_OK:
            f = OWN[OWN_IDX[c]]
            r.update(ho=True, marca=f["marca"], num=f["num"], nome=f["nome"], filo_hex=f["hex"],
                     qualita="uguale" if OWN_DE[c] <= SOGLIA_UGUALE else "simile")
        else:
            f = prim[PRIM_IDX[c]] if c is not None else {"num": "?", "nome": "", "hex": h}
            r.update(ho=False, marca=KIT[0], num=f["num"], nome=f["nome"], filo_hex=f["hex"], qualita="manca")
        out.append(r)
    return out


def spesa(ids):
    """Fili da comprare (marca principale) per un gruppo di disegni, con quanti disegni li usano."""
    prim = FILI.tabella(KIT[0])[0]
    conta = {}
    for i in ids:
        for k in {int(PRIM_IDX[c]) for c in D_IDS[D_PTR[i]:D_PTR[i + 1]] if OWN_DE[c] > SOGLIA_OK}:
            conta[k] = conta.get(k, 0) + 1
    return [{"marca": KIT[0], "num": prim[k]["num"], "nome": prim[k]["nome"], "hex": prim[k]["hex"], "disegni": n}
            for k, n in sorted(conta.items(), key=lambda x: -x[1])]


def sta(w, h, t):
    """0 = entra così, 1 = entra solo girato di 90°, None = non entra."""
    if w <= t[0] and h <= t[1]:
        return 0
    if h <= t[0] and w <= t[1]:
        return 1
    return None


def telaio_min(w, h):
    """Indice del telaio più piccolo in cui entra (dritto o girato), -1 se in nessuno."""
    for k, t in enumerate(TELAI):
        if sta(w, h, t) is not None:
            return k
    return -1


def entra(d, k=None):
    if k is None:
        return telaio_min(d[2], d[3]) >= 0
    return 0 <= k < len(TELAI) and sta(d[2], d[3], TELAI[k]) is not None


_CT = {}


def conta_telai():
    """Per ogni telaio quanti disegni ci entrano (dritti o girati) + quanti non entrano in nessuno."""
    chiave = json.dumps(TELAI)
    if chiave not in _CT:
        c = [0] * len(TELAI)
        fuori = 0
        for n_, d in enumerate(DATI):
            if n_ in TOLTI:
                continue
            ok = False
            for k, t in enumerate(TELAI):
                if sta(d[2], d[3], t) is not None:
                    c[k] += 1
                    ok = True
            fuori += not ok
        _CT.clear()
        _CT[chiave] = {"entra": c, "fuori": fuori}
    return _CT[chiave]


_CONTEGGI = {}


def conteggi(tel, tema):
    """Numeri della barra laterale: temi contati col filtro telaio attuale, telai contati col tema attuale."""
    chiave = (tel, tema, json.dumps(TELAI))
    if chiave not in _CONTEGGI:
        temi, aut, ent, fuori = {}, {}, [0] * len(TELAI), 0
        for n_, d in enumerate(DATI):
            if n_ in TOLTI:
                continue
            k_min = telaio_min(d[2], d[3])
            ok_tel = (tel == "x" and k_min >= 0) or (tel == "f" and k_min < 0) or                      (tel.startswith("t") and entra(d, int(tel[1:] or -1))) or tel not in ("x", "f") and not tel.startswith("t")
            if ok_tel:
                temi[d[1]] = temi.get(d[1], 0) + 1
                if AUTORE[n_]:
                    aut[AUTORE[n_]] = aut.get(AUTORE[n_], 0) + 1
            if not tema or d[1] == tema:
                if k_min < 0:
                    fuori += 1
                else:
                    for k, t in enumerate(TELAI):
                        if sta(d[2], d[3], t) is not None:
                            ent[k] += 1
        if len(_CONTEGGI) > 64:
            _CONTEGGI.clear()
        _CONTEGGI[chiave] = {"temi": temi, "autori": aut, "telai": {"entra": ent, "fuori": fuori}}
    return _CONTEGGI[chiave]


def minuti(d):
    return round(d[5] / PUNTI_AL_MINUTO + d[4] * CAMBIO_COLORE_S / 60)


def voce(i, score=None):
    d = DATI[i]
    return {"i": i, "nome": NOMI[i], "tema": d[1], "autore": AUTORE[i], "raccolta": AUT.raccolta(d[0]), "w": d[2], "h": d[3], "col": d[4], "punti": d[5],
            "fmt": d[6], "entra": entra(d), "min": minuti(d), "pref": d[0] in SET["pref"],
            "ric": d[0] in SET["ric"], "score": score, "tel": telaio_min(d[2], d[3]),
            "mancano": mancano(i), "nvar": len(GRUPPO[VAR[i]]) if i in VAR else 0, "foto": foto_diario(d[0]),
            "ntoppe": sum(1 for r in PATCH_DI.get(d[0], []) if IDX.get(r) not in TOLTI)}


def foto_diario(percorso):
    """Foto del ricamo fatto (la più recente nel diario), per mostrarla passando sopra la scheda."""
    foto = [e for e in DIARIO if e.get("percorso") == percorso and e.get("foto")]
    return max(foto, key=lambda e: e.get("data", ""))["foto"] if foto else ""


# ---------------- ricerca ----------------
COS_MIN = (15.93 + math.log(0.01 / 0.99)) / 109.9      # ≈ 0.103: coseno con probabilità SigLIP 1%
RX_MISURA = re.compile(r"(?<![\d.])(\d{2,3}(?:[.,]\d)?)\s*(?:mm)?\s*[x×*]\s*(\d{2,3}(?:[.,]\d)?)\s*(?:mm)?(?![\d.])", re.I)


def leggi_misura(testo):
    m = RX_MISURA.search(testo or "")
    if not m:
        return None, testo
    w, h = (float(x.replace(",", ".")) for x in m.groups())
    return (w, h), (testo[:m.start()] + testo[m.end():]).strip()


def cerca(p):
    q = p.get("q", "").strip()
    # misura: scritta nella ricerca ("trilli 130x160") o nel campo apposito; tolleranza in % (dritto o girato)
    mis, q = leggi_misura(q)
    if p.get("mis"):
        m1 = leggi_misura(p["mis"])[0]
        solo_n = re.fullmatch(r"\s*(\d{2,3}(?:[.,]\d)?)\s*(?:mm)?\s*", p["mis"])
        # un numero solo ("130") = lato più lungo di circa 130 mm
        mis = m1 or ((float(solo_n.group(1).replace(",", ".")), None) if solo_n else mis)
    tol = max(2, min(60, float(p.get("tol", "15") or 15))) / 100

    def vicino(d):
        W, Hh = mis
        if Hh is None:
            return abs(max(d[2], d[3]) - W) <= W * tol
        return any(abs(a - W) <= W * tol and abs(b - Hh) <= Hh * tol for a, b in ((d[2], d[3]), (d[3], d[2])))

    def distanza(d):
        W, Hh = mis
        if Hh is None:
            return abs(max(d[2], d[3]) - W) / W
        return min(abs(a - W) / W + abs(b - Hh) / Hh for a, b in ((d[2], d[3]), (d[3], d[2])))
    tema, solo = p.get("tema", ""), p.get("solo", "")
    tel = p.get("telaio", "x")                       # "" tutti, "x" in un mio telaio, "t2" nel telaio 2
    colmax = int(p.get("colmax", "0") or 0)
    ids_sel = {int(x) for x in p.get("ids", "").split(",") if x.isdigit()}
    simili = p.get("simili", "")
    serie = p.get("serie", "")
    autore = p.get("autore", "")
    conifili = p.get("conifili") == "1"
    racc = set(RACCOLTE.get(p.get("raccolta", ""), [])) if p.get("raccolta") else None

    def ok(i, tutto=True):
        """tutto=False (vista serie): solo i filtri di misura/telaio/colori/fili, non tema/autore/raccolta/libreria."""
        if i in TOLTI:
            return False
        d = DATI[i]
        if mis and not vicino(d):
            return False
        if not tutto:
            return not (conifili and mancano(i)) and not (colmax and d[4] > colmax) and (
                tel not in ("x", "f") and not tel.startswith("t") or
                (tel == "x" and entra(d)) or (tel == "f" and not entra(d)) or
                (tel.startswith("t") and entra(d, int(tel[1:] or -1))))
        if tema and d[1] != tema:
            return False
        if autore and AUTORE[i] != autore:
            return False
        if racc is not None and d[0] not in racc:
            return False
        if conifili and mancano(i):
            return False
        if tel == "x" and not entra(d):
            return False
        if tel == "f" and entra(d):
            return False
        if tel.startswith("t") and not entra(d, int(tel[1:] or -1)):
            return False
        if colmax and d[4] > colmax:
            return False
        if solo == "pref" and d[0] not in SET["pref"]:
            return False
        if solo == "ric" and d[0] not in SET["ric"]:
            return False
        if solo == "sel" and i not in ids_sel:
            return False
        if solo == "nuovi" and i < N_DATIJS:                # importati e digitalizzati, dopo il riordino
            return False
        return True

    punteggi = None
    vaghi = 0
    if serie.isdigit() and int(serie) < len(DATI):
        # tutta la serie (anche fuori dal tema/autore scelto), ma rispettando telaio, misura e colori
        risultati = [(i, None) for i in sorted(serie_di(int(serie)), key=lambda i: LOW[i]) if ok(i, False)]
    elif simili.isdigit() and EMB is not None and int(simili) < _n_emb:
        s = EMB[:_n_emb] @ EMB[int(simili)]
        ordine = np.argsort(-s)[:3000]
        risultati = [(int(i), float(s[i])) for i in ordine if ok(int(i))]
    elif q:
        # per nome: parole intere (così "rana" non trova "zebrana")
        rx = [re.compile(r"(?<![a-zà-ü0-9])" + re.escape(w)) for w in q.lower().split()]
        per_nome = [i for i in range(len(DATI)) if all(r.search(LOW[i]) for r in rx) and ok(i)]
        risultati = [(i, None) for i in per_nome]
        if EMB is not None and _n_emb:
            s = EMB[:_n_emb] @ emb_testo(q)
            ordine = np.argsort(-s)[:3000]
            gia = set(per_nome)
            # soglia adattiva: tieni chi è nettamente sopra la media (SigLIP ha punteggi bassi e ravvicinati)
            top, med = float(s[ordine[0]]), float(np.median(s))
            soglia = med + 0.6 * (top - med)
            # minimo assoluto: probabilità SigLIP >= 1% (logit = cos*109.9 - 15.93). Misurato sul campione:
            # soggetti presenti 0.28-0.89, assenti (disney, trattore…) <= 0.01 => sotto sono "a caso"
            if p.get("vaghi") != "1":
                vaghi = sum(1 for i in ordine if soglia <= s[i] < COS_MIN and int(i) not in gia)
                soglia = max(soglia, COS_MIN)
            risultati += [(int(i), float(s[i])) for i in ordine if s[i] >= soglia and int(i) not in gia and ok(int(i))]
    else:
        risultati = [(i, None) for i in range(len(DATI)) if ok(i)]
        o = p.get("ordina", "n")
        if o == "s":
            risultati.sort(key=lambda r: DATI[r[0]][2] * DATI[r[0]][3])
        elif o == "b":
            risultati.sort(key=lambda r: -DATI[r[0]][2] * DATI[r[0]][3])
        elif o == "p":
            risultati.sort(key=lambda r: DATI[r[0]][5])
        else:
            risultati.sort(key=lambda r: LOW[r[0]])
        if mis and o == "n":
            risultati.sort(key=lambda r: distanza(DATI[r[0]]))          # solo misura: prima i più vicini
        if solo == "nuovi" and o == "n" and not mis:
            risultati.sort(key=lambda r: -r[0])                         # aggiunti di recente: l'ultimo per primo
    n_file = len(risultati)
    # aggiunti di recente nascosti dal filtro dei telai (es. appena importato ma troppo grande): la pagina lo dice
    fuori_tel = 0
    if solo == "nuovi" and tel == "x" and not (q or mis or colmax or conifili or tema or autore or racc is not None):
        fuori_tel = sum(1 for i in range(N_DATIJS, len(DATI)) if i not in TOLTI and not entra(DATI[i]))
    if p.get("raggruppa", "1") == "1" and (VAR or PATCH_ORIG):
        risultati = raggruppa(risultati)
    pag, per = int(p.get("pagina", "0")), int(p.get("per", "120"))
    return {"tot": len(risultati), "n_file": n_file, "items": [voce(i, sc) for i, sc in risultati[pag * per:(pag + 1) * per]],
            "clip": EMB is not None, "clip_n": _n_emb, "vaghi": vaghi, "conteggi": conteggi(tel, tema),
            "n_pref": sum(1 for x in SET["pref"] if x in IDX), "n_ric": sum(1 for x in SET["ric"] if x in IDX),
            "misura": mis, "tol": round(tol * 100), "fuori_tel": max(0, fuori_tel)}


# ---------------- anteprima grande disegnata dal PES ----------------
def leggi_scalato(i, scala=1.0):
    import pyembroidery
    pat = pyembroidery.read(os.path.join(OUT, DATI[i][0]))
    if abs(scala - 1) > 1e-3:
        pat.stitches = [[x * scala, y * scala, c] for x, y, c in pat.stitches]
    return pat


def anteprima(i, S=1800, tel=False, scala=1.0, ric=False):
    """Anteprima grande realistica (filo spesso 0,42 mm con ombra e riflesso, su tela) per dettaglio e zoom.
    tel=True: sfondo trasparente, per metterla nel telaio disegnato dalla pagina. Salvata in _catalogo/img_xl.
    scala != 1: disegno ridimensionato ma filo sempre 0,42 mm veri => si vede se i punti si aprono o si ammassano."""
    pc = round(scala * 100)
    ric = ric and pc != 100 and serve_ricalcolo(i, scala)
    f = os.path.join(CAT, "img_xl", f"{i}{'_t' if tel else ''}{'' if pc == 100 else (f'_r{pc}' if ric else f'_s{pc}')}.webp")
    if os.path.exists(f):
        return open(f, "rb").read()
    from anteprima_bella import render
    pat = ricalcolato(DATI[i][0], scala) if ric else leggi_scalato(i, scala)
    im = render(pat, 900 if tel else S, ss=2, sfondo=None if tel else True)
    b = io.BytesIO()
    im.save(b, "WEBP", quality=90)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f + ".tmp", "wb") as fh:
        fh.write(b.getvalue())
    os.replace(f + ".tmp", f)
    return b.getvalue()


_ANALISI = {}


def analisi_punti(i):
    """Misure del disegno originale per valutare un ridimensionamento:
    lunghezze dei punti (mm) e superficie ricamata (mm², griglia 0,5 mm) -> distanza media tra le file = area / lunghezza filo."""
    if i in _ANALISI:
        return _ANALISI[i]
    r = analizza(leggi_scalato(i))
    _ANALISI[i] = r
    if len(_ANALISI) > 200:
        _ANALISI.pop(next(iter(_ANALISI)))
    return r


def analizza(pat):
    import numpy as np
    seg, prev = [], None
    for x, y, c in pat.stitches:
        c &= 0xFF
        p = (x / 10, y / 10)                                   # pyembroidery: unità 0,1 mm
        if c == 0 and prev is not None:
            seg.append((prev[0], prev[1], p[0], p[1]))
        prev = p if c in (0, 1) else None
    if not seg:
        r = {"lung": [], "area": 0, "filo": 0}
    else:
        a = np.array(seg)
        lung = np.hypot(a[:, 2] - a[:, 0], a[:, 3] - a[:, 1])
        # campiona ogni segmento ogni 0,25 mm e segna le celle da 0,5 mm toccate dal filo
        n = np.maximum(1, np.ceil(lung / 0.25)).astype(int)
        rip = np.repeat(np.arange(len(a)), n)
        t = (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)) / np.repeat(n, n)
        xs = a[rip, 0] + (a[rip, 2] - a[rip, 0]) * t
        ys = a[rip, 1] + (a[rip, 3] - a[rip, 1]) * t
        celle = np.unique(np.stack([np.floor(xs / 0.5), np.floor(ys / 0.5)], 1), axis=0)
        r = {"lung": lung, "area": len(celle) * 0.25, "filo": float(lung.sum())}
    return r


def serve_ricalcolo(i, scala):
    """Ridimensionamento intelligente: si ricalcolano i punti solo se la scala semplice NON va bene per questo
    disegno (anche un 6% può bastare a ammassare i punti corti; spesso un 15% va benissimo così)."""
    if abs(scala - 1) < 0.02:
        return False
    return valuta_scala(i, scala).get("giudizio", "ok") != "ok"


def valuta_scala(i, scala, ric=False):
    """Cosa succede ai punti con questo ridimensionamento (lo scalare allunga i punti ma non ne aggiunge).
    ric=True (intelligente): se la scala semplice non basta, misura il disegno coi punti RICALCOLATI."""
    import numpy as np
    if ric and serve_ricalcolo(i, scala):
        return valuta_ricalcolo(i, scala)
    r = analisi_punti(i)
    d = DATI[i]
    w, h = round(d[2] * scala, 1), round(d[3] * scala, 1)
    k = telaio_min(w, h)
    out = {"scala": round(scala * 100), "w": w, "h": h, "tel": k,
           "entra": k >= 0, "punti": int(len(r["lung"]))}
    if not len(r["lung"]):
        return out
    L = r["lung"] * scala
    passo0 = r["area"] / r["filo"] if r["filo"] else 0           # mm tra una fila e l'altra (stima)
    out.update(passo_orig=round(passo0, 2), passo=round(passo0 * scala, 2),
               densita=round(100 / scala),                         # % di filo per mm² rispetto all'originale
               corti=round(float((L < 1.0).mean()) * 100, 1),      # < 1 mm: si ammassano, rischio rotture
               corti_orig=round(float((r["lung"] < 1.0).mean()) * 100, 1),
               lunghi=round(float((L > 7.0).mean()) * 100, 1),     # > 7 mm: punti pieni lenti, si impigliano
               lunghi_orig=round(float((r["lung"] > 7.0).mean()) * 100, 1),
               lung_media=round(float(np.median(L)), 2))
    v = abs(scala - 1)
    peggio = (out["corti"] - out["corti_orig"] > 5) or (out["lunghi"] - out["lunghi_orig"] > 5)
    out["giudizio"] = "ok" if v <= 0.1 and not peggio else ("limite" if v <= 0.2 and not peggio else "no")
    out["intelligente"] = bool(ric)
    return out


def valuta_ricalcolo(i, scala):
    """Densità e punti del disegno ricalcolato, confrontati con l'originale."""
    import numpy as np
    r0, r1 = analisi_punti(i), analizza(ricalcolato(DATI[i][0], scala))
    d = DATI[i]
    w, h = round(d[2] * scala, 1), round(d[3] * scala, 1)
    k = telaio_min(w, h)
    out = {"scala": round(scala * 100), "w": w, "h": h, "tel": k, "entra": k >= 0, "ricalcolo": True,
           "punti": int(len(r1["lung"])), "punti_orig": int(len(r0["lung"]))}
    if not len(r1["lung"]) or not r0["filo"]:
        return out
    p0, p1 = r0["area"] / r0["filo"], r1["area"] / r1["filo"]
    out.update(passo_orig=round(p0, 2), passo=round(p1, 2), densita=round(100 * p0 / p1),
               corti=round(float((r1["lung"] < 1.0).mean()) * 100, 1), corti_orig=round(float((r0["lung"] < 1.0).mean()) * 100, 1),
               lunghi=round(float((r1["lung"] > 7.0).mean()) * 100, 1), lunghi_orig=round(float((r0["lung"] > 7.0).mean()) * 100, 1),
               lung_media=round(float(np.median(r1["lung"])), 2))
    dd = abs(out["densita"] - 100)
    peggio = (out["corti"] - out["corti_orig"] > 8) or (out["lunghi"] - out["lunghi_orig"] > 5)
    out["giudizio"] = "ok" if dd <= 12 and not peggio and abs(scala - 1) <= 0.6 else ("limite" if dd <= 25 else "no")
    return out


sys.path.insert(0, QUI)
from rigenera_miniature import lavora  # noqa: E402  (render 'fancy' delle miniature, in processi separati)
POOL = None                             # ProcessPoolExecutor creato in __main__ (Windows: spawn)
_IN_CORSO = {}                          # n -> future: una miniatura si disegna una volta sola
_ic_lock = threading.Lock()


_SOSPETTI = {}                          # n -> quante volte era in corso quando un processo è morto


def _nuovo_pool():
    from concurrent.futures import ProcessPoolExecutor
    return ProcessPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 4))


def miniatura_hd(i):
    """Avvia (se serve) il disegno 'bello' della miniatura n e restituisce il future (o None se già pronta).
    Non solleva MAI: se un processo muore (file PES malato) il pool si ricrea; un file che fa morire
    i processi 2 volte resta con la miniatura semplice. Prima un pool rotto bloccava tutte le ricerche."""
    global POOL
    if POOL is None or _SOSPETTI.get(i, 0) >= 2 or os.path.exists(os.path.join(CAT, "img_hd", f"{i}.webp")):
        return None
    from concurrent.futures.process import BrokenProcessPool
    with _ic_lock:
        f = _IN_CORSO.get(i)
        if f is not None:
            return f
        for _ in range(2):
            try:
                f = POOL.submit(lavora, (i, DATI[i][0], OUT))
                break
            except (BrokenProcessPool, RuntimeError):
                for k in list(_IN_CORSO):
                    _SOSPETTI[k] = _SOSPETTI.get(k, 0) + 1
                _IN_CORSO.clear()
                try:
                    POOL.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
                print("miniature: processo morto, ricreo il pool", flush=True)
                POOL = _nuovo_pool()
        else:
            return None
        _IN_CORSO[i] = f
        f.add_done_callback(lambda _f, i=i: _IN_CORSO.pop(i, None))
    return f


# ---------------- destinazioni ed esportazione ----------------
def _lettere_usb():
    """Lettere dei dischi collegati via USB (anche SSD/HDD esterni, che Windows vede come 'fissi')."""
    import subprocess
    cmd = ("Get-Partition | Where-Object DriveLetter | ForEach-Object { "
           "'{0}|{1}' -f $_.DriveLetter, (Get-Disk -Number $_.DiskNumber).BusType }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=20,
                           creationflags=0x08000000)
        return {l.split("|")[0] for l in r.stdout.split() if l.endswith("|USB")}
    except Exception:
        return set()


_DEST = {"t": 0.0, "v": None, "in_corso": False}


def destinazioni():
    """Dischi dove salvare (USB per primi). Leggerli sveglia i dischi in standby (fino a 20 s): il risultato resta
    in memoria e si rinfresca in sottofondo ogni minuto, così la pagina non aspetta."""
    if _DEST["v"] is not None:
        if time.time() - _DEST["t"] > 60 and not _DEST["in_corso"]:
            _DEST["in_corso"] = True
            threading.Thread(target=_aggiorna_dest, daemon=True).start()
        return _DEST["v"]
    return _aggiorna_dest()


def _dest_ok(root):
    """La destinazione esiste? Se non è nell'elenco in memoria (chiavetta appena inserita) si rilegge."""
    return root in {d["root"] for d in destinazioni()} or root in {d["root"] for d in _aggiorna_dest()}


def _aggiorna_dest():
    try:
        v = _leggi_destinazioni()
        _DEST.update(t=time.time(), v=v)
        return v
    finally:
        _DEST["in_corso"] = False


def _leggi_destinazioni():
    out = []
    usb = _lettere_usb()
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    for n, lettera in enumerate(string.ascii_uppercase):
        if not mask & (1 << n):
            continue
        root = f"{lettera}:\\"
        tipo = k32.GetDriveTypeW(root)
        if tipo not in (2, 3):                                       # 2 = rimovibile, 3 = fisso
            continue
        nome = ctypes.create_unicode_buffer(261)
        k32.GetVolumeInformationW(root, nome, 261, None, None, None, None, 0)
        try:
            libero = shutil.disk_usage(root).free
        except OSError:
            continue
        out.append({"root": root, "nome": nome.value, "usb": tipo == 2 or lettera in usb,
                    "libero_gb": round(libero / 2**30, 1)})
    out.sort(key=lambda d: (not d["usb"], d["root"]))
    return out


def pulisci(n):
    return re.sub(r"[<>:\"/\\|?*]", "_", n).strip(" .")[:80] or "ricami"


def scheda_html(ids):
    righe = []
    for i in ids:
        d = DATI[i]
        img = base64.b64encode(open(os.path.join(CAT, "img", f"{i}.webp"), "rb").read()).decode()
        fili = "".join(f'<span class="f" style="background:{f["filo_hex"]}" title="{f["nome"]}"></span>'
                       f'<small>{f["num"]}</small> ' for f in info_fili(i))
        righe.append(f'<div class="s"><img src="data:image/webp;base64,{img}"><div><b>{NOMI[i]}.pes</b><br>'
                     f'{d[2]} × {d[3]} mm{"" if entra(d) else " <b class=no>NON entra nel telaio</b>"}<br>'
                     f'{d[4]} colori · {d[5]:,} punti · ~{minuti(d)} min<br><small>{d[1]}</small><br>{fili}</div></div>')
    return ("<!doctype html><meta charset=utf-8><title>Scheda ricami</title><style>body{font-family:sans-serif;margin:16px}"
            ".s{display:flex;gap:14px;border-bottom:1px solid #ddd;padding:10px 0;break-inside:avoid}"
            ".s img{width:150px;height:150px;object-fit:contain;border:1px solid #eee}"
            ".f{display:inline-block;width:22px;height:22px;border:1px solid #999;margin:4px 3px 0 0;border-radius:4px}"
            ".no{color:#c0392b}</style><h2>Scheda ricami</h2>" + "".join(righe))


def pes_centrato(pat, f):
    """Scrive il PES (v6) come quelli dei programmi Brother: ANGOLO IN ALTO A SINISTRA in 0,0
    (tutti i PES originali letti da pyembroidery hanno min = 0,0). La Innov-is mostra e ricama solo
    la parte con coordinate positive: centrato in 0,0 restava un quarto di disegno, col centro
    spostato si tagliavano strisce in alto/a sinistra (prove del 22/11/2026).
    Il pattern torna com'era dopo la scrittura (è in cache)."""
    import pyembroidery
    x0, y0, _, _ = pat.bounds()
    dx, dy = -x0, -y0
    pat.translate(dx, dy)
    try:
        pyembroidery.write_pes(pat, f, {"pes version": 6})
    finally:
        pat.translate(-dx, -dy)


FORMATO = CFG.get("formato") if CFG.get("formato") in MACCHINE.FORMATI else ".pes"   # file per la macchina


def scrivi_formato(pat, f, formato=None):
    """Scrive il disegno nel formato della macchina scelta nelle impostazioni. PES: angolo in alto a sinistra a
    0,0 (regola Brother, vedi pes_centrato). Gli altri formati: disegno centrato in 0,0, come li fanno i
    programmi di quelle marche."""
    import pyembroidery
    formato = formato or FORMATO
    if formato == ".pes":
        return pes_centrato(pat, f)
    for th in pat.threadlist:                               # fili senza colore fanno cadere alcuni scrittori
        if th.color is None:
            th.color = 0x333333
    x0, y0, x1, y1 = pat.bounds()
    dx, dy = -(x0 + x1) / 2, -(y0 + y1) / 2
    pat.translate(dx, dy)
    try:
        getattr(pyembroidery, "write_" + formato[1:])(pat, f)
    finally:
        pat.translate(-dx, -dy)


def trasforma(i, ruota_auto, specchia, scala, ric=True):
    """Legge il PES e applica: rotazione di 90° se entra nel telaio solo girato, specchio, scala (0.8-1.2).
    Restituisce i byte del file nuovo nel formato della macchina, oppure None se non serve cambiare niente (si
    copia l'originale: solo PES)."""
    d = DATI[i]
    w, h = d[2] * scala, d[3] * scala
    k = telaio_min(w, h)
    ruota = ruota_auto and k >= 0 and sta(w, h, TELAI[k]) == 1      # nel telaio più piccolo entra solo girato
    # i PES originali si copiano così come sono; quelli CONVERTITI dal riordino (da .jef, .sew…) sono
    # centrati in 0,0 e la macchina li taglierebbe: si riscrivono sempre con l'angolo in 0,0
    if not (ruota or specchia or abs(scala - 1) > 1e-3) and d[6] == ".pes" and FORMATO == ".pes":
        return None
    import pyembroidery
    # ridimensionamento intelligente: oltre ±10% i punti si RICALCOLANO (densità come l'originale)
    if ric and serve_ricalcolo(i, scala):
        base = ricalcolato(d[0], scala)
        pat = pyembroidery.EmbPattern()
        pat.stitches = [list(s) for s in base.stitches]
        pat.threadlist = base.threadlist
        scala = 1.0
    else:
        pat = pyembroidery.read(os.path.join(OUT, d[0]))
    nuovi = []
    for x, y, c in pat.stitches:
        x, y = x * scala, y * scala
        if specchia:
            x = -x
        if ruota:
            x, y = -y, x
        nuovi.append([x, y, c])
    pat.stitches = nuovi
    b = io.BytesIO()
    scrivi_formato(pat, b)
    return b.getvalue()


def esporta(body):
    ids = [int(x) for x in body.get("ids", [])]
    ruota_auto = bool(body.get("ruota", True))
    specchia = bool(body.get("specchia"))
    scala_g = max(0.5, min(2.0, float(body.get("scala", 100)) / 100))   # oltre ±10% si ricalcolano i punti
    per_dis = {int(k): max(0.3, min(3.0, float(v) / 100)) for k, v in (body.get("scale") or {}).items()}  # scelta nel dettaglio
    noric = {int(x) for x in body.get("noric", [])}                     # disegni per cui si vuole la scala semplice
    root = body.get("dest", "")
    if not ids or not re.match(r"^[A-Z]:\\$", root) or not _dest_ok(root):
        raise ValueError("destinazione non valida")
    radice = bool(body.get("radice"))                       # per la macchina: file direttamente nella chiavetta
    cart = root if radice else os.path.join(root, pulisci(body.get("cartella") or time.strftime("Ricami %Y-%m-%d")))
    os.makedirs(cart, exist_ok=True)
    copiati, fuori = 0, []
    for i in ids:
        d = DATI[i]
        sub = os.path.join(cart, pulisci(d[1])) if body.get("per_tema") and not radice else cart
        os.makedirs(sub, exist_ok=True)
        dest = os.path.splitext(os.path.join(sub, os.path.basename(d[0])))[0] + FORMATO   # formato della macchina
        b, e = os.path.splitext(dest)
        n = 2
        while os.path.exists(dest):
            dest = f"{b} ({n}){e}"
            n += 1
        scala = per_dis.get(i, scala_g)
        nuovo = trasforma(i, ruota_auto, specchia, scala, ric=i not in noric)
        if nuovo is None:
            shutil.copy2(os.path.join(OUT, d[0]), dest)
        else:
            with open(dest, "wb") as fh:
                fh.write(nuovo)
        copiati += 1
        w, h = d[2] * scala, d[3] * scala
        if telaio_min(w, h) < 0:
            fuori.append(NOMI[i])
    if body.get("scheda") and not radice:
        with open(os.path.join(cart, "Scheda ricami.html"), "w", encoding="utf-8") as fh:
            fh.write(scheda_html(ids))
    return {"copiati": copiati, "cartella": cart, "fuori_telaio": fuori}


# ---------------- patch: bordo da toppa attorno al disegno (patch.py) ----------------
import patch as PATCH  # noqa: E402
_PATCH = {}
_patch_lock = threading.Lock()


def _filo_kit(chiave, predef):
    """'Marca|num' -> {hex, nome, num}; se manca o è sbagliato usa il colore predefinito."""
    try:
        marca, num = chiave.split("|", 1)
        f = next(f for f in FILI.tabella(marca)[0] if f["num"] == num)
        return {"hex": f["hex"], "nome": f["nome"], "num": f["num"]}
    except (AttributeError, ValueError, StopIteration, OSError):
        return predef


def opz_patch(p):
    def num(k, d):
        try:
            return float(p.get(k) or d)
        except (TypeError, ValueError):
            return d
    return {"forma": p.get("forma") if p.get("forma") in PATCH.FORME else "contorno",
            "tipo": p.get("tipo") if p.get("tipo") in PATCH.TIPI else "tessuto",
            "margine": num("margine", 3), "bordo": num("bordo", 4),
            "larg": num("larg", 0) or None, "alt": num("alt", 0) or None,
            "col_bordo": _filo_kit(p.get("cb"), {"hex": "#1c2a4a", "nome": "Blu scuro", "num": ""}),
            "col_fondo": _filo_kit(p.get("cf"), {"hex": "#f2efe6", "nome": "Avorio", "num": ""}),
            "togli": sorted({int(x) for x in str(p.get("togli") or "").split(",") if x.strip().isdigit()})}


def blocchi_colore(pat):
    """[(punti [(x, y, cmd)], filo)] del disegno, un blocco per cambio di filo."""
    import pyembroidery as pe
    out = []
    for blocco, filo in pat.get_as_colorblocks():
        st = [s for s in blocco if (s[2] & pe.COMMAND_MASK) not in (pe.COLOR_CHANGE, pe.END, pe.STOP)]
        if st:
            out.append((st, filo))
    return out


def senza_colori(pat, togli):
    """Il disegno senza i blocchi di colore indicati (es. il riempimento dello sfondo), per la toppa."""
    import pyembroidery as pe
    nuovo = pe.EmbPattern()
    tenuti = [b for k, b in enumerate(blocchi_colore(pat)) if k not in togli]
    for n, (st, filo) in enumerate(tenuti):
        if n:
            nuovo.add_command(pe.COLOR_CHANGE)
        nuovo.add_thread(filo)
        x, y = st[0][0], st[0][1]
        nuovo.add_stitch_absolute(pe.JUMP, x, y)
        for sx, sy, c in st:
            nuovo.add_stitch_absolute(c & pe.COMMAND_MASK, sx, sy)
    nuovo.add_command(pe.END)
    return nuovo if tenuti else pat


def colori_patch(i):
    """Colori del disegno per il creatore di toppe. sfondo=True: un blocco grande che copre quasi tutto il disegno
    (di solito il riempimento dello sfondo, da togliere in una toppa)."""
    import pyembroidery
    pat = pyembroidery.read(os.path.join(OUT, DATI[i][0]))
    bl = blocchi_colore(pat)
    tot = sum(len(st) for st, _ in bl) or 1
    x0, y0, x1, y1 = pat.bounds()
    area = max((x1 - x0) * (y1 - y0), 1)
    out = []
    for k, (st, filo) in enumerate(bl):
        xs, ys = [s[0] for s in st], [s[1] for s in st]
        cop = (max(xs) - min(xs)) * (max(ys) - min(ys)) / area
        out.append({"k": k, "hex": filo.hex_color() if filo is not None and filo.color is not None else "#333333",
                    "nome": (filo.description or "") if filo is not None else "", "punti": len(st),
                    "sfondo": cop > 0.85 and len(st) > 0.2 * tot and k < 2})
    return out


def fai_patch(i, o):
    k = (i, json.dumps(o, sort_keys=True))
    with _patch_lock:
        if k in _PATCH:
            return _PATCH[k]
    import pyembroidery
    pat = pyembroidery.read(os.path.join(OUT, DATI[i][0]))
    if o.get("togli"):
        pat = senza_colori(pat, set(o["togli"]))
    r = PATCH.crea(pat, o)
    with _patch_lock:
        if len(_PATCH) > 40:
            _PATCH.clear()
        _PATCH[k] = r
    return r


def info_patch(i, o):
    pat, inf = fai_patch(i, o)
    out = {x: v for x, v in inf.items() if x != "contorno"}
    out["minuti"] = round(inf["punti"] / PUNTI_AL_MINUTO + inf["colori"] * CAMBIO_COLORE_S / 60)
    out["tel"] = telaio_min(inf["larg"], inf["alt"])
    out["fili"] = [{"hex": t.hex_color(), "nome": t.description or ""} for t in pat.threadlist]
    return out


# ---------------- composizione: disegni + scritte (componi.py, lettering.py) ----------------
import componi as COMP  # noqa: E402
import lettering as LET  # noqa: E402
_PES = {}


def leggi_pes_cache(percorso):
    import pyembroidery
    if percorso not in _PES:
        if len(_PES) > 30:
            _PES.clear()
        _PES[percorso] = pyembroidery.read(os.path.join(OUT, percorso))
    return _PES[percorso]


def spec_comp(body):
    """Dal browser: elementi con 'i' (disegno) o testo; colori come 'Marca|num'."""
    els = []
    for e in body.get("elementi", [])[:60]:
        e = dict(e)
        if e.get("tipo") == "disegno":
            i = int(e["i"])
            if not 0 <= i < len(DATI):
                continue
            e["percorso"] = DATI[i][0]
        elif e.get("tipo") == "testo":
            e["colore"] = _filo_kit(e.get("cb"), {"hex": "#1c2a4a", "nome": "Blu scuro", "num": ""})
            if not os.path.isdir(os.path.join(LET.CARTELLA, os.path.basename(e.get("font", "")))):
                continue
        else:
            continue
        els.append(e)
    return {"elementi": els}


_RIC = {}
_ric_lock = threading.Lock()


def ricalcolato(percorso, scala):
    """Disegno alla misura nuova con i punti RIGENERATI (ricalcola.py: densità come l'originale). In cache."""
    k = (percorso, round(scala, 2))
    with _ric_lock:
        if k in _RIC:
            return _RIC[k]
    from ricalcola import ricalcola
    pat, _ = ricalcola(leggi_pes_cache(percorso), round(scala, 2))
    with _ric_lock:
        if len(_RIC) > 24:
            _RIC.pop(next(iter(_RIC)))
        _RIC[k] = pat
    return pat


def fai_comp(body):
    pat, inf = COMP.costruisci(spec_comp(body), leggi_pes_cache, ricalcolato,
                               decidi=lambda percorso, s: serve_ricalcolo(IDX[percorso], s))
    inf["minuti"] = round(inf["punti"] / PUNTI_AL_MINUTO + inf["colori"] * CAMBIO_COLORE_S / 60)
    inf["tel"] = telaio_min(inf["larg"], inf["alt"])
    return pat, inf


_TESTI = {}


def testo_el(p):
    """Un testo da solo, centrato (per l'editor): (pattern, info con larghezza/altezza e centro)."""
    el = {"tipo": "testo", "testo": p.get("testo", "")[:200], "font": os.path.basename(p.get("font", "")),
          "altezza": max(3.0, min(150.0, float(p.get("altezza", 15) or 15))), "spaz": float(p.get("spaz", 0) or 0),
          "allinea": p.get("allinea", "centro"), "colori_font": p.get("cf") == "1", "cb": p.get("cb", ""), "x": 0, "y": 0,
          "punto": p.get("punto", "satin"), "arco": max(-1000.0, min(1000.0, float(p.get("arco", 0) or 0)))}
    k = json.dumps(el, sort_keys=True)
    if k not in _TESTI:
        if len(_TESTI) > 60:
            _TESTI.clear()
        _TESTI[k] = fai_comp({"elementi": [el]})
    return _TESTI[k]


def adatta_in_cornice(body):
    """Mette la scritta nello spazio vuoto dentro una cornice: buco del disegno (raster 0,5 mm), punto più
    lontano dai bordi (polylabel), altezza massima che ci sta con 2,5 mm di margine (entro i limiti del font)."""
    import numpy as np
    from PIL import Image, ImageDraw
    from scipy import ndimage
    from shapely.geometry import box as sbox
    from shapely.ops import polylabel, unary_union
    sp = spec_comp(body)
    els = body.get("elementi", [])
    it, ic = int(body["testo"]), int(body["cornice"])
    te, co = els[it], els[ic]
    if te.get("tipo") != "testo" or co.get("tipo") != "disegno":
        raise ValueError("serve una scritta e un disegno")
    cor = next(e for e in sp["elementi"] if e.get("tipo") == "disegno" and int(e["i"]) == int(co["i"]))
    cor = dict(cor, x=co.get("x", 0), y=co.get("y", 0), scala=co.get("scala", 1), rot=co.get("rot", 0), specchia=co.get("specchia"))
    punti = [(x, y) for _, pts in COMP.blocchi_disegno(leggi_pes_cache(cor["percorso"]), cor) for c, x, y in pts]
    xs, ys = [p[0] for p in punti], [p[1] for p in punti]
    P = 0.5
    x0, y0 = min(xs) - 2, min(ys) - 2
    W, H = int((max(xs) - x0 + 2) / P) + 2, int((max(ys) - y0 + 2) / P) + 2
    im = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(im)
    prev = None
    for _, pts in COMP.blocchi_disegno(leggi_pes_cache(cor["percorso"]), cor):
        for c, x, y in pts:
            q = ((x - x0) / P, (y - y0) / P)
            if c == 0 and prev is not None and math.dist(prev, q) * P < 12:
                dr.line([prev, q], fill=255, width=2)
            prev = q if c in (0, 1) else None
    pieno = ndimage.binary_dilation(np.asarray(im) > 0, iterations=1)
    lab, n = ndimage.label(~pieno)
    bordo = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    cand = sorted(((int((lab == k).sum()), k) for k in range(1, n + 1) if k not in bordo), reverse=True)[:8]
    if not cand:
        raise ValueError("questo disegno non ha uno spazio chiuso dentro")
    # proporzioni della scritta (dritta) a un'altezza di prova
    h0 = 20.0
    r = LET.testo(os.path.basename(te["font"]), te.get("testo", ""), h0, float(te.get("spaz", 0) or 0), allinea=te.get("allinea", "centro"),
                  punto=te.get("punto", "satin"))
    if not r["larg"]:
        raise ValueError("scritta vuota")
    kw, kh = r["larg"] / h0, r["alt"] / h0
    # si prova OGNI spazio chiuso (le cornici da applicazione hanno anche l'anello tra cornice e linea di
    # piazzamento): vince quello dove la scritta viene più grande
    migliore = None
    for area, k in cand:
        if area * P * P < 40:
            continue
        m = lab == k
        scatole = []
        for rr in range(H):
            riga = np.flatnonzero(np.diff(np.concatenate(([0], m[rr].astype(np.int8), [0]))))
            for a_, b_ in zip(riga[::2], riga[1::2]):
                scatole.append(sbox(x0 + a_ * P, y0 + rr * P, x0 + b_ * P, y0 + (rr + 1) * P))
        buco = unary_union(scatole).buffer(-2.0)
        if buco.is_empty:
            continue
        if buco.geom_type != "Polygon":
            buco = max(buco.geoms, key=lambda g: g.area)
        # posizione: griglia di centri possibili + polylabel; vince la scritta più grande, a pari misura la
        # più vicina al centro del buco (es. fiori che sporgono in alto: la scritta scende un po', non va di lato)
        from shapely.geometry import Point as SPt
        bx0, by0, bx1, by1 = buco.bounds
        mx, my = (bx0 + bx1) / 2, (by0 + by1) / 2
        centri = [polylabel(buco, tolerance=0.3)] + [SPt(bx0 + (bx1 - bx0) * i / 16, by0 + (by1 - by0) * j / 10)
                                                     for i in range(1, 16) for j in range(1, 10)]

        def massima(c):
            if not buco.contains(c):
                return 0.0
            lo, hi = 0.0, 300.0
            for _ in range(30):
                h = (lo + hi) / 2
                if buco.contains(sbox(c.x - kw * h / 2, c.y - kh * h / 2, c.x + kw * h / 2, c.y + kh * h / 2)):
                    lo = h
                else:
                    hi = h
            return lo
        prove = [(massima(c), c) for c in centri]
        top = max(p[0] for p in prove)
        lo, c = min((p for p in prove if p[0] >= top * 0.85), key=lambda p: (round(abs(p[1].x - mx) / 3), -p[0]))
        if migliore is None or lo > migliore[0]:
            migliore = (lo, c)
    if migliore is None or migliore[0] < 1:
        raise ValueError("lo spazio dentro la cornice è troppo piccolo")
    lo, c = migliore
    f = next((x for x in LET.elenco() if x["id"] == os.path.basename(te["font"])), None)
    alt = lo
    nota = ""
    if f:
        amax, amin = f["altezza_mm"] * f["max"], f["altezza_mm"] * f["min"]
        if alt > amax:
            alt, nota = amax, "limitata alla misura massima del font"
        elif alt < amin:
            nota = "lo spazio è piccolo per questo font: i punti verranno fitti"
    return {"x": round(c.x, 1), "y": round(c.y, 1), "altezza": round(alt * 2) / 2, "nota": nota}


def salva_comp(body):
    root = body.get("dest", "")
    if not re.match(r"^[A-Z]:\\$", root) or not _dest_ok(root):
        raise ValueError("destinazione non valida")
    cart = root if body.get("radice") else os.path.join(root, pulisci(body.get("cartella") or "Composizioni"))
    os.makedirs(cart, exist_ok=True)
    import pyembroidery
    pat, inf = fai_comp(body)
    if not inf["punti"]:
        raise ValueError("composizione vuota")
    dest = os.path.join(cart, pulisci(body.get("nome") or "Composizione") + FORMATO)
    b, e = os.path.splitext(dest)
    n = 2
    while os.path.exists(dest):
        dest = f"{b} ({n}){e}"
        n += 1
    buf = io.BytesIO()
    scrivi_formato(pat, buf)
    with open(dest, "wb") as fh:
        fh.write(buf.getvalue())
    return {"file": dest, "larg": inf["larg"], "alt": inf["alt"]}


def salva_patch(body):
    """Toppa: nella chiavetta (formato della macchina) e/o in libreria accanto al disegno d'origine (PES, voce del
    catalogo collegata all'originale: la scheda del disegno mostra le sue toppe)."""
    i = int(body["i"])
    o = opz_patch(body.get("opz") or {})
    root = body.get("dest", "")
    libreria = body.get("libreria", True)
    chiavetta = bool(re.match(r"^[A-Z]:\\$", root)) and _dest_ok(root)
    if not libreria and not chiavetta:
        raise ValueError("destinazione non valida")
    pat, inf = fai_patch(i, o)
    nome = pulisci(f"{NOMI[i][:50]} patch {o['forma']}")
    out = {"larg": inf["larg"], "alt": inf["alt"]}
    if chiavetta:
        cart = root if body.get("radice") else os.path.join(root, pulisci(body.get("cartella") or "Patch"))
        os.makedirs(cart, exist_ok=True)
        dest = os.path.join(cart, nome + FORMATO)
        b, e = os.path.splitext(dest)
        n = 2
        while os.path.exists(dest):
            dest = f"{b} ({n}){e}"
            n += 1
        buf = io.BytesIO()
        scrivi_formato(pat, buf)
        with open(dest, "wb") as fh:
            fh.write(buf.getvalue())
        out["file"] = dest
    if libreria:
        tess = str((body.get("opz") or {}).get("tess", ""))
        out.update(patch_in_libreria(i, pat, nome, inf, tess if re.fullmatch(r"#[0-9a-fA-F]{6}", tess) else "#2b2f36"))
    return out


F_ELIMINATI = os.path.join(CAT, "eliminati.json") if not FIGLIO else ""


def carica_tolti():
    """All'avvio: disegni eliminati da OpenHoop (eliminati.json) e file non più sul disco (cancellati a mano)."""
    try:
        via = set(json.load(open(F_ELIMINATI, encoding="utf-8")))
    except (OSError, ValueError):
        via = set()
    for n, d in enumerate(DATI):
        if d[0] in via or not os.path.exists(os.path.join(OUT, d[0])):
            TOLTI.add(n)
    _CT.clear()
    _CONTEGGI.clear()


def elimina(ids):
    """Toglie i disegni dalla libreria: il file va in <archivio>/_cestino (stesso percorso, si può rimettere a
    mano). I file nelle cartelle dei ricami dell'utente non si toccano mai (l'archivio ne ha copie o collegamenti)."""
    fatti = []
    with _stato_lock:
        try:
            via = json.load(open(F_ELIMINATI, encoding="utf-8"))
        except (OSError, ValueError):
            via = []
        for i in {int(x) for x in ids if str(x).lstrip("-").isdigit()}:
            if not 0 <= i < len(DATI) or i in TOLTI:
                continue
            rel = DATI[i][0]
            src = os.path.join(OUT, rel)
            if os.path.normcase(os.path.abspath(src)).startswith(os.path.normcase(os.path.abspath(OUT)) + os.sep) and os.path.exists(src):
                dest = os.path.join(OUT, "_cestino", rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.exists(dest):
                    b, e = os.path.splitext(dest)
                    dest = f"{b} ({int(time.time())}){e}"
                os.replace(src, dest)
            TOLTI.add(i)
            via.append(rel)
            fatti.append(i)
            for k in ("pref", "ric"):
                SET[k].discard(rel)
        with open(F_ELIMINATI + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(sorted(set(via)), fh, ensure_ascii=False)
        os.replace(F_ELIMINATI + ".tmp", F_ELIMINATI)
    _CT.clear()
    _CONTEGGI.clear()
    return {"eliminati": len(fatti), "cestino": os.path.join(OUT, "_cestino")}


def patch_in_libreria(i, pat, nome, inf, tess="#2b2f36"):
    """La toppa entra in libreria nella cartella del disegno d'origine, con lo stesso tema."""
    orig = DATI[i][0]
    rel = os.path.join(os.path.dirname(orig), nome + ".pes")
    b, e = os.path.splitext(rel)
    n = 2
    while os.path.exists(os.path.join(OUT, rel)):
        rel = f"{b} ({n}){e}"
        n += 1
    buf = io.BytesIO()
    pes_centrato(pat, buf)
    with open(os.path.join(OUT, rel), "wb") as fh:
        fh.write(buf.getvalue())
    seq = [th.hex_color() if th.color is not None else "#333333" for th in pat.threadlist][:40]
    voce_d = [rel, DATI[i][1], round(inf["larg"], 1), round(inf["alt"], 1), len(pat.threadlist), inf["punti"], ".pes", seq]
    with _DIGIT_LOCK:
        j = _aggiungi_al_catalogo(voce_d)
        PATCH_DI.setdefault(orig, []).append(rel)
        PATCH_ORIG[rel] = orig
        f = os.path.join(CAT, "patch.json")
        try:
            elenco = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            elenco = []
        elenco.append({"voce": voce_d, "origine": orig})
        with open(f + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(elenco, fh, ensure_ascii=False)
        os.replace(f + ".tmp", f)
    _miniature_nuove(j, rel)
    if inf.get("tipo") != "ricamato":
        # su feltro/twill la stoffa non è nel file: le anteprime in libreria la mostrano come nel creatore di toppe
        try:
            from PIL import Image
            im = PATCH.anteprima(pat, inf, 1800, tessuto=tess)
            for sub, s in (("img_xl", 1800), ("img_hd", 240), ("img", 240)):
                os.makedirs(os.path.join(CAT, sub), exist_ok=True)
                (im if s == 1800 else im.resize((s, s), Image.LANCZOS)).save(os.path.join(CAT, sub, f"{j}.webp"), "WEBP", quality=88)
        except Exception as e:
            print("anteprima toppa:", e)
    return {"i_libreria": j, "percorso": rel}


# ---------------- digitalizzatore: immagine -> ricamo (digitalizza.py) ----------------
# Le prove stanno in una «zona d'attesa» (_catalogo/digitalizza/bozze/<id>/: immagine, opzioni, PES, anteprime) e
# sopravvivono ai riavvii. Solo «Conferma» le porta in libreria: PES in Ricami_ordinati/DIGITALIZZATI, voce nel
# catalogo (salvata in _catalogo/digitalizzati.json, ricaricata all'avvio dopo dati.js).
DIG_DIR = os.path.join(CAT, "digitalizza")
DIG_BOZZE = os.path.join(DIG_DIR, "bozze")
# cartella dei digitalizzati: se ne esiste già una (creata con un'altra lingua) si usa quella, niente doppioni
DIG_TEMA = next((n for n in ["DIGITALIZZATI"] + [diz(l).get("DIGITALIZZATI", "") for l in LINGUE]
                 if n and os.path.isdir(os.path.join(OUT, n))), tr("DIGITALIZZATI"))
_DIGIT_LOCK = threading.Lock()


def _bozza_dir(did):
    if not re.fullmatch(r"[0-9a-f]{6,20}", did or ""):
        raise ValueError("bozza sconosciuta")
    d = os.path.join(DIG_BOZZE, did)
    if not os.path.isdir(d):
        raise ValueError("bozza sconosciuta")
    return d


def _bozza_info(did):
    d = os.path.join(DIG_BOZZE, did)
    info = dict(json.load(open(os.path.join(d, "info.json"), encoding="utf-8")), id=did)
    try:                                                    # disegno preso da «Cerca disegni gratis»: fonte e licenza
        info["fonte"] = json.load(open(os.path.join(d, "fonte.json"), encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return info


def digit_elenco():
    if not os.path.isdir(DIG_BOZZE):
        return []
    out = []
    for did in os.listdir(DIG_BOZZE):
        try:
            out.append(_bozza_info(did))
        except (OSError, ValueError):
            pass
    return sorted(out, key=lambda b: b.get("data", ""), reverse=True)


def _calcola_bozza(d, nome, alt, ncol, forzati=None, esclusi=None, semantica=None, ai=None, semplifica=None,
                   stile="pulito", densita="normale", scelte=None):
    """Digitalizza l'immagine della bozza con le opzioni date e riscrive PES, anteprime e info."""
    import digitalizza as DG
    from anteprima_bella import render
    import pyembroidery
    from PIL import Image
    sorg = next(os.path.join(d, f) for f in os.listdir(d) if f.startswith("immagine."))
    with _DIGIT_LOCK:                                       # un calcolo alla volta: usa molta memoria
        opz = dict(max_colori=ncol, forzati=forzati or {}, marca=None, esclusi=esclusi or [], semantica=semantica or [])
        # PASSO 22: misura consigliata, dall'analisi a 100 mm (si rifà solo se cambiano esclusioni/colori/etichette)
        chiave_m = json.dumps([esclusi or [], ncol, semantica or [], 3], sort_keys=True)
        f_m = os.path.join(d, "misura.json")
        try:
            cache = json.load(open(f_m, encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        A100 = None
        if cache.get("chiave") == chiave_m:
            misura = cache["m"]
        else:
            A100 = DG.analizza(sorg, 100, **opz)
            misura = DG.misura_consigliata(A100, 100, max(Image.open(sorg).size))
            with open(f_m, "w", encoding="utf-8") as fh:
                json.dump({"chiave": chiave_m, "m": misura}, fh)
        # PASSO 23: semplificazione per la misura: automatica sotto il minimo consigliato, o scelta a mano
        semp = (alt < misura["min"]) if semplifica is None else bool(semplifica)
        if A100 is not None and abs(alt - 100) < 0.5 and not semp:
            A = A100
        else:
            A = DG.analizza(sorg, alt, semplifica=semp, **opz)
        stile = stile if stile in DG.STILI else "pulito"
        densita = densita if densita in DG.DENSITA else "normale"
        pat, inf = DG.digitalizza(A, stile=stile, densita=densita, scelte=scelte or [])
        x0, y0, _, _ = pat.bounds()
        pat.translate(-x0, -y0)                             # angolo in alto a sinistra a 0,0 (Brother)
        pyembroidery.write_pes(pat, os.path.join(d, "ricamo.pes"), {"pes version": 6})
        render(pat, 2400, ss=1, sfondo=True).save(os.path.join(d, "ricamo.webp"), "WEBP", quality=90)   # grande: per lo zoom
        rg = A["regioni"]                               # zone cliccabili per «Escludi zone»: id in R+G
        Image.fromarray(np.stack([rg & 255, (rg >> 8) & 255, (rg >> 16) & 255], -1).astype(np.uint8)).save(os.path.join(d, "regioni.png"))
        DG.foglio_analisi(A, f"{nome}  {alt:.0f} mm").convert("RGB").save(os.path.join(d, "analisi.webp"), "WEBP", quality=88)
    conta = {}
    for o in A["info"]:
        conta[o["punto"]] = conta.get(o["punto"], 0) + 1
    larg, altz = float(inf["larg"]), float(inf["alt"])
    info = {"nome": nome, "alt_mm": alt, "max_colori": ncol, "data": time.strftime("%Y-%m-%d %H:%M:%S"), "forzati": forzati or {}, "esclusi": esclusi or [],
            "semantica": semantica or [], "ai": ai, "misura": misura,
            "semplifica": semplifica, "semplificata": semp, "semplificato": A.get("semplificato", []), "stile": stile,
            "densita": densita, "scelte": scelte or [],
            "tipi_zona": _tipi_zona(A, DG),
            "tagli": inf.get("tagli"),
            "larg": larg, "alt": altz, "punti": int(inf["punti"]), "colori": int(inf["colori"]),
            "minuti": round(inf["punti"] / PUNTI_AL_MINUTO + inf["colori"] * CAMBIO_COLORE_S / 60),
            "tel": telaio_min(larg, altz), "zone": len(A["info"]), "tipi": conta,
            "sequenza": ["#%06x" % (t.color & 0xFFFFFF) for t in pat.threadlist],
            "fili": [{"hex": f.get("hex"), "nome": f.get("nome"), "num": f.get("num"), "originale": f["originale"], "de": f["de"],
                      "a_mano": f.get("a_mano", False),
                      "alternative": [{"hex": x.get("hex"), "nome": x.get("nome"), "num": x.get("num"), "de": x["de"]} for x in f["alternative"]]}
                     for f in A["fili"]]}
    with open(os.path.join(d, "info.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False)
    return info


def _tipi_zona(A, DG):
    """Per la pagina: un punto dentro ogni zona (frazioni dell'immagine ORIGINALE) col punto usato, l'angolo e
    la scelta a mano: serve al menu «punto per zona» (passo 30)."""
    from scipy import ndimage
    z = A["zone"]
    H, W = z.shape
    rit = A.get("ritaglio", (0, 0, 1, 1))
    sl = ndimage.find_objects(z + 1)
    out = []
    for o in A["info"]:
        s = sl[o["id"]] if o["id"] < len(sl) else None
        if s is None:
            continue
        m = z[s] == o["id"]
        dt = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1]
        y, x = np.unravel_index(dt.argmax(), dt.shape)
        fx, fy = DG.a_originale(rit, (x + s[1].start + 0.5) / W, (y + s[0].start + 0.5) / H)
        ang = o.get("angolo")
        out.append({"x": round(fx, 4), "y": round(fy, 4), "punto": o["punto"], "mm2": round(o["area_mm2"], 1),
                    "angolo": ang if isinstance(ang, (int, float, str)) else None, "scelta": o.get("scelta") or {},
                    "assorbita": o.get("assorbita_in") is not None})
    return out


def digit_nuovo(body):
    """Nuova bozza da un'immagine (data URL base64)."""
    dati = body.get("img", "")
    m = re.match(r"data:image/(png|jpeg|jpg|webp|gif|bmp);base64,", dati)
    if not m:
        raise ValueError("serve un'immagine PNG, JPG, WEBP, GIF o BMP")
    raw = base64.b64decode(dati[m.end():])
    if len(raw) > 40_000_000:
        raise ValueError("immagine troppo grande")
    did = f"{int(time.time() * 1000):x}"
    d = os.path.join(DIG_BOZZE, did)
    os.makedirs(d)
    with open(os.path.join(d, "immagine." + m.group(1).replace("jpeg", "jpg")), "wb") as fh:
        fh.write(raw)
    if isinstance(body.get("fonte"), dict):                 # da «Cerca disegni gratis»: chi l'ha fatto e con che licenza
        f = {k: str(body["fonte"].get(k, ""))[:300] for k in ("fonte", "titolo", "licenza", "autore", "pagina", "svg")}
        with open(os.path.join(d, "fonte.json"), "w", encoding="utf-8") as fh:
            json.dump(f, fh, ensure_ascii=False)
    nome = pulisci(os.path.splitext(body.get("nome") or "immagine")[0])[:60]
    try:
        info = _calcola_bozza(d, nome, max(20.0, min(400.0, float(body.get("alt") or 100))),
                              max(2, min(16, int(body.get("colori") or 12))))
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return dict(info, id=did)


def digit_rifai(body):
    """Rifà una bozza con altre opzioni (misura, colori, nome)."""
    d = _bozza_dir(body.get("id"))
    vec = _bozza_info(body["id"])
    nome = pulisci(body.get("nome") or vec["nome"])[:60]
    forzati = dict(vec.get("forzati") or {})
    if isinstance(body.get("fili"), dict):              # filo scelto a mano: {"#rrggbb": "N212"}, "" = torna automatico
        for h, num in body["fili"].items():
            if re.fullmatch(r"#[0-9a-f]{6}", h):
                if num:
                    forzati[h] = str(num)
                else:
                    forzati.pop(h, None)
    esclusi = vec.get("esclusi") or []
    if isinstance(body.get("esclusi"), list):           # zone escluse a mano: [[x, y], ...] in frazioni dell'immagine
        esclusi = [[min(max(float(p[0]), 0), 1), min(max(float(p[1]), 0), 1)] for p in body["esclusi"][:200]]
    semantica = vec.get("semantica") or []
    if isinstance(body.get("semantica"), list):         # etichette corrette a mano: [{"x", "y", "etichetta"}]
        import aiuto_ai as AI
        semantica = [{"x": min(max(float(s["x"]), 0), 1), "y": min(max(float(s["y"]), 0), 1), "etichetta": s["etichetta"]}
                     for s in body["semantica"][:400]
                     # etichette dell'AI o personalizzate (testo libero, corto): quelle personalizzate servono a chi usa il programma
                     # per orientarsi, sul ricamo agiscono solo quelle note (occhi, capelli/pelle, sfondo)
                     if isinstance(s.get("etichetta"), str) and (s["etichetta"] in AI.ETICHETTE
                                                                 or re.fullmatch(r"[\w àèéìòùç'-]{1,30}", s["etichetta"]))]
    semplifica = vec.get("semplifica")
    if "semplifica" in body:                            # "auto" | "si" | "no"
        semplifica = {"si": True, "no": False}.get(body["semplifica"])
    stile = body.get("stile") or vec.get("stile") or "pulito"
    densita = body.get("densita") or vec.get("densita") or "normale"
    scelte = vec.get("scelte") or []
    if isinstance(body.get("scelte"), list):            # punto/angolo/densità scelti a mano per zona (passo 30)
        scelte = []
        for s in body["scelte"][:400]:
            v = {"x": min(max(float(s["x"]), 0), 1), "y": min(max(float(s["y"]), 0), 1)}
            if s.get("punto") in ("riempimento", "satin", "cerchi", "contorno", "salta"):
                v["punto"] = s["punto"]
            if isinstance(s.get("angolo"), (int, float)):
                v["angolo"] = float(s["angolo"]) % 180
            if s.get("densita") in ("normale", "fitta"):
                v["densita"] = s["densita"]
            if len(v) > 2:
                scelte.append(v)
    info = _calcola_bozza(d, nome, max(20.0, min(400.0, float(body.get("alt") or vec["alt_mm"]))),
                          max(2, min(16, int(body.get("colori") or vec["max_colori"]))), forzati, esclusi,
                          semantica, vec.get("ai"), semplifica, stile, densita, scelte)
    return dict(info, id=body["id"])


def digit_ai(body):
    """PASSO 20 — «Aiuto AI»: DeepSeek etichetta le zone (una chiamata, ~1 minuto), poi la bozza si ricalcola con le
    etichette. Le zone che l'AI considera sfondo tornano come PROPOSTE di esclusione: le conferma l'utente."""
    import digitalizza as DG
    import aiuto_ai as AI
    d = _bozza_dir(body.get("id"))
    vec = _bozza_info(body["id"])
    if not AI.chiave():
        raise ValueError("manca la chiave DeepSeek: aggiungila in Impostazioni → Servizi facoltativi")
    sorg = next(os.path.join(d, f) for f in os.listdir(d) if f.startswith("immagine."))
    with _DIGIT_LOCK:
        A = DG.analizza(sorg, vec["alt_mm"], max_colori=vec["max_colori"], forzati=vec.get("forzati") or {},
                        marca=None, esclusi=vec.get("esclusi") or [])
    sem, uso = AI.etichetta(A)                          # fuori dal lock: la chiamata dura un minuto
    ai = {"data": time.strftime("%Y-%m-%d %H:%M:%S"), "token": uso.get("total_tokens"), "zone": len(sem)}
    info = _calcola_bozza(d, vec["nome"], vec["alt_mm"], vec["max_colori"], vec.get("forzati"), vec.get("esclusi"), sem, ai,
                          vec.get("semplifica"), vec.get("stile") or "pulito", vec.get("densita") or "normale",
                          vec.get("scelte") or [])
    proposte = [[s["x"], s["y"]] for s in sem if s["etichetta"] == "sfondo_da_non_ricamare"]
    return dict(info, id=body["id"], proposte=proposte)


def punti_sim(percorso):
    """Punti per la simulazione nella pagina: {"fili": [...], "p": [x, y, cmd, ...]} in decimi di mm.
    cmd: 0 punto, 1 salto, 2 taglio, 3 cambio colore."""
    import pyembroidery as pe
    pat = pe.read(percorso)
    cod = {pe.STITCH: 0, pe.JUMP: 1, pe.TRIM: 2, pe.COLOR_CHANGE: 3, pe.STOP: 3}
    p = []
    for x, y, c in pat.stitches:
        k = cod.get(c & pe.COMMAND_MASK)
        if k is not None:
            p += [int(round(x)), int(round(y)), k]
    fili = [{"hex": "#%06x" % (t.color & 0xFFFFFF), "nome": t.description or "", "num": t.catalog_number or ""}
            for t in pat.threadlist]
    return {"fili": fili, "p": p}


def digit_scarta(body):
    shutil.rmtree(_bozza_dir(body.get("id")))
    return {"ok": True}


def digit_salva(body):
    """Copia il PES della bozza su chiavetta/cartella (per provarlo in macchina senza confermarlo)."""
    d = _bozza_dir(body.get("id"))
    nome = _bozza_info(body["id"])["nome"]
    root = body.get("dest", "")
    if not re.match(r"^[A-Z]:\\$", root) or not _dest_ok(root):
        raise ValueError("destinazione non valida")
    cart = root if body.get("radice") else os.path.join(root, pulisci(body.get("cartella") or "Digitalizzati"))
    os.makedirs(cart, exist_ok=True)
    dest = os.path.join(cart, nome + FORMATO)
    b, e = os.path.splitext(dest)
    n = 2
    while os.path.exists(dest):
        dest = f"{b} ({n}){e}"
        n += 1
    if FORMATO == ".pes":
        shutil.copyfile(os.path.join(d, "ricamo.pes"), dest)
    else:                                                  # OpenHoop: nel formato della macchina
        import pyembroidery
        with open(dest, "wb") as fh:
            scrivi_formato(pyembroidery.read(os.path.join(d, "ricamo.pes")), fh)
    return {"file": dest}


def _aggiungi_al_catalogo(voce_d, ricalcola=True):
    """Aggiunge una voce [percorso, tema, w, h, colori, punti, ext, sequenza] a tutte le strutture in memoria."""
    i = len(DATI)
    DATI.append(voce_d)
    LOW.append(voce_d[0].lower())
    NOMI.append(os.path.splitext(os.path.basename(voce_d[0]))[0])
    IDX[voce_d[0]] = i
    AUTORE.append(AUT.autore_di(voce_d[0]))
    k = chiave_serie(voce_d[0])
    SERIE.setdefault(k, []).append(i)
    RACC_ORIG.setdefault(k[0], []).append(i)
    if ricalcola:                                       # (le importazioni grandi lo fanno una volta per blocco)
        prepara_colori()                                # colori nuovi (toppe, digitalizzati): abbinati ai fili che ho
        calcola_fili()
    _CT.clear()
    _CONTEGGI.clear()
    return i


_SALTI = {}


def salti_tagli(i):
    """Salti del disegno i letti dal PES: (salti, tagli_a_mano). Molte macchine (es. Brother Innov-is) NON tagliano i salti: ogni salto
    dentro lo stesso colore più lungo di 2 mm è un filo da tagliare a mano. Il salto subito dopo un cambio di filo
    non conta (lì il filo si taglia comunque). Stessa regola del banco di prova del digitalizzatore."""
    if i in _SALTI:
        return _SALTI[i]
    import pyembroidery as pe
    salti = tagli = 0
    try:
        pat = pe.read(os.path.join(OUT, DATI[i][0]))
        dopo_cambio, in_salto, da = True, False, None
        x0 = y0 = None
        for x, y, c in pat.stitches:
            c &= pe.COMMAND_MASK
            if c in (pe.COLOR_CHANGE, pe.STOP):
                dopo_cambio, in_salto = True, False
            elif c in (pe.JUMP, pe.TRIM):
                if not in_salto:
                    in_salto, da = True, (x0, y0)
            elif c == pe.STITCH:
                if in_salto and not dopo_cambio and da[0] is not None:
                    salti += 1
                    if ((x - da[0]) ** 2 + (y - da[1]) ** 2) ** 0.5 > 20:      # decimi di mm
                        tagli += 1
                in_salto, dopo_cambio = False, False
                x0, y0 = x, y
    except Exception:
        return None, None
    _SALTI[i] = (salti, tagli)
    return salti, tagli


def _miniature_nuove(i, rel):
    """Miniature di un disegno appena entrato nel catalogo (via quelle di un vecchio disegno con lo stesso numero)."""
    for sub in ("img", "img_hd", "img_xl"):
        for g in ([os.path.join(CAT, sub, f"{i}.webp")] if sub != "img_xl" else
                  [os.path.join(CAT, sub, x) for x in os.listdir(os.path.join(CAT, sub)) if re.match(rf"{i}[_.]", x)]
                  if os.path.isdir(os.path.join(CAT, sub)) else []):
            try:
                os.remove(g)
            except OSError:
                pass
    lavora((i, rel, OUT))                                   # miniatura «bella» subito
    hd = os.path.join(CAT, "img_hd", f"{i}.webp")
    if os.path.exists(hd):
        shutil.copyfile(hd, os.path.join(CAT, "img", f"{i}.webp"))


_VELOCI = set()                                           # appena messi in libreria: miniatura veloce già pronta


def _miniature_veloci(i, rel, d):
    """Come _miniature_nuove ma senza aspettare: subito la miniatura dall'anteprima già fatta durante l'analisi,
    quella «bella» la rifanno i processi in sottofondo (prima: fino a 1 s a disegno in «Metti in libreria»)."""
    for sub in ("img", "img_hd"):
        try:
            os.remove(os.path.join(CAT, sub, f"{i}.webp"))
        except OSError:
            pass
    xl = os.path.join(CAT, "img_xl")
    if os.path.isdir(xl):
        for x in os.listdir(xl):
            if re.match(rf"{i}[_.]", x):
                try:
                    os.remove(os.path.join(xl, x))
                except OSError:
                    pass
    try:
        from PIL import Image
        im = Image.open(os.path.join(d, "anteprima.webp")).convert("RGB")
        im.thumbnail((240, 240), Image.LANCZOS)
        quad = Image.new("RGB", (240, 240), "white")
        quad.paste(im, ((240 - im.width) // 2, (240 - im.height) // 2))
        os.makedirs(os.path.join(CAT, "img"), exist_ok=True)
        quad.save(os.path.join(CAT, "img", f"{i}.webp"), "WEBP", quality=78)
        _VELOCI.add(i)
    except Exception:
        pass
    miniatura_hd(i)                                       # in coda ai processi: non si aspetta


def digit_conferma(body):
    """La bozza entra in libreria: PES in Ricami_ordinati/DIGITALIZZATI + voce nel catalogo + miniatura."""
    d = _bozza_dir(body.get("id"))
    info = _bozza_info(body["id"])
    cart = os.path.join(OUT, DIG_TEMA)
    os.makedirs(cart, exist_ok=True)
    base = info["nome"]
    rel = os.path.join(DIG_TEMA, base + ".pes")
    n = 2
    while os.path.exists(os.path.join(OUT, rel)):
        rel = os.path.join(DIG_TEMA, f"{base} ({n}).pes")
        n += 1
    shutil.copyfile(os.path.join(d, "ricamo.pes"), os.path.join(OUT, rel))
    voce_d = [rel, DIG_TEMA, round(info["larg"], 1), round(info["alt"], 1), info["colori"], info["punti"], ".pes",
              info.get("sequenza", [])]
    with _DIGIT_LOCK:
        i = _aggiungi_al_catalogo(voce_d)
        f = os.path.join(CAT, "digitalizzati.json")
        try:
            elenco = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            elenco = []
        elenco.append(voce_d)
        with open(f + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(elenco, fh, ensure_ascii=False)
        os.replace(f + ".tmp", f)
    _miniature_nuove(i, rel)
    # l'immagine d'origine e le opzioni restano accanto, in confermati/ (servono alla scuola)
    os.makedirs(os.path.join(DIG_DIR, "confermati"), exist_ok=True)
    shutil.move(d, os.path.join(DIG_DIR, "confermati", body["id"]))
    return {"i": i, "percorso": rel}


# ---------------- importazione di disegni nuovi (trascinati, da una cartella, da un link) ----------------
# Ogni disegno resta in attesa in _catalogo/importa/<id>/ (disegno.pes, anteprima.webp, emb.npy, info.json) finché
# non lo metti in libreria: allora va in Ricami_ordinati/<tema>/<raccolta>/ e nel catalogo (importati.json).
# I file d'origine già visti (sha1) non si ripropongono: importati_sorgenti.json {sha1: percorso | "scartato"}.
IMP_DIR = os.path.join(CAT, "importa")
F_IMP_SORG = os.path.join(CAT, "importati_sorgenti.json")
_IMP_LOCK = threading.Lock()
_IMP_CODA = []                                              # [(tipo, valore, fonte)] tipo: file | percorso | link
_IMP_CONTA = __import__("itertools").count()
_IMP_STATO = {"attivo": False, "fatti": 0, "nuovi": 0, "doppi": 0, "gia": 0, "errori": [], "ora": ""}
# la coda sopravvive ai riavvii: i file caricati dalla pagina si salvano in importa_coda/, l'elenco in coda.json
IMP_CODA_DIR = os.path.join(CAT, "importa_coda")
F_IMP_CODA = os.path.join(IMP_CODA_DIR, "coda.json")
_IMP_CORRENTE = [None]
_IMP_ALL_CACHE = {}                                         # cartella del disco -> allegati trovati (importazione)


def _imp_coda_salva():
    """Da chiamare con _IMP_LOCK preso. Solo voci «percorso» e «link» (i byte stanno nei file)."""
    voci = ([_IMP_CORRENTE[0]] if _IMP_CORRENTE[0] else []) + _IMP_CODA
    try:
        os.makedirs(IMP_CODA_DIR, exist_ok=True)
        with open(F_IMP_CODA + ".tmp", "w", encoding="utf-8") as fh:
            json.dump([list(v) for v in voci if v[0] in ("percorso", "link")], fh, ensure_ascii=False)
        os.replace(F_IMP_CODA + ".tmp", F_IMP_CODA)
    except OSError as e:
        print("coda non salvata:", e)


def imp_file_su_disco(nome, raw):
    """File arrivato dalla pagina -> importa_coda/<n>_<nome> e voce «percorso» (riprende dopo un riavvio)."""
    os.makedirs(IMP_CODA_DIR, exist_ok=True)
    nome = os.path.basename(nome) or "file"
    f = os.path.join(IMP_CODA_DIR, f"{int(time.time() * 1000):x}_{nome}")
    with open(f, "wb") as fh:
        fh.write(raw)
    return ("percorso", (f, nome), nome)


def imp_riprendi():
    """All'avvio: quello che era in coda (o a metà) quando OpenHoop si è chiuso riparte da solo.
    I disegni già analizzati si riconoscono e si saltano."""
    try:
        voci = [tuple(v) for v in json.load(open(F_IMP_CODA, encoding="utf-8"))]
    except (OSError, ValueError):
        return
    voci = [(a, tuple(b) if isinstance(b, list) else b, c) for a, b, c in voci
            if a == "link" or (a == "percorso" and os.path.exists(b[0]))]
    if voci:
        print(f"importazione ripresa: {len(voci)} file in coda")
        imp_accoda(voci)
_PER_MISURA = {}                                            # (larg, alt arrotondate) -> [indici]: per i doppioni
_FIRME = {}
TEMA_VARIE = tr("VARIE (da smistare)")
TEMI_NO_PROPOSTA = {TEMA_VARIE, "VARIE (da smistare)", DIG_TEMA, "DIGITALIZZATI", "GIA RICAMATI", tr("GIA RICAMATI")}


def _imp_sorgenti():
    try:
        return json.load(open(F_IMP_SORG, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _imp_segna_sorgente(sha1, valore):
    with _IMP_LOCK:
        s = _imp_sorgenti()
        s[sha1] = valore
        with open(F_IMP_SORG + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(s, fh, ensure_ascii=False)
        os.replace(F_IMP_SORG + ".tmp", F_IMP_SORG)


def _doppione(info):
    """Lo stesso disegno è già nel catalogo? Stessa regola del riordino: misure ±1 mm, punti ±3%, firma 8×8
    quasi uguale (le firme del catalogo si calcolano solo per i pochi candidati). -> indice o None."""
    import importa as IM
    import pyembroidery
    if _PER_MISURA.get("_n") != len(DATI):                  # rifatto se il catalogo è cresciuto
        _PER_MISURA.clear()
        for j, d in enumerate(DATI):
            _PER_MISURA.setdefault((round(d[2]), round(d[3])), []).append(j)
        _PER_MISURA["_n"] = len(DATI)
    kw, kh = round(info["larg"]), round(info["alt"])
    for dw in (-1, 0, 1):
        for dh in (-1, 0, 1):
            for j in _PER_MISURA.get((kw + dw, kh + dh), ()):
                p2 = DATI[j][5]
                if abs(p2 - info["punti"]) > 0.03 * max(p2, info["punti"]):
                    continue
                if j not in _FIRME:
                    try:
                        _FIRME[j] = IM.firma(pyembroidery.read(os.path.join(OUT, DATI[j][0])))
                    except Exception:
                        _FIRME[j] = None
                if _FIRME[j] and sum(abs(a - b) for a, b in zip(_FIRME[j], info["firma"])) < 0.08:
                    return j
    return None


def _tema_proposto(info, v):
    """Dalle parole (nome + raccolta, come il riordino); se non bastano, dai 30 disegni più simili del catalogo."""
    import riordina as RI
    t = RI.tema_di(f"{info['raccolta']} {info['nome']}")
    if t == TEMA_VARIE and v is not None and EMB is not None and _n_emb:
        from collections import Counter
        s = EMB[:_n_emb] @ v
        top = np.argpartition(-s, 30)[:30]
        conta = Counter(DATI[i][1] for i in top if DATI[i][1] not in TEMI_NO_PROPOSTA)
        if conta:
            t2, n = conta.most_common(1)[0]
            if n >= 9:
                t = t2
    return t


def _imp_uno(d, fonte, sorgenti, in_attesa=None):
    import importa as IM
    from anteprima_bella import render
    pat, info = IM.prepara(d)
    if info["sha1"] in sorgenti:
        _IMP_STATO["gia"] += 1
        return
    if in_attesa is None:
        in_attesa = _Attesa()
    if info["sha1"] in in_attesa:                            # già in attesa (stesso file trascinato due volte)
        _IMP_STATO["gia"] += 1
        return
    in_attesa.add(info["sha1"])
    iid =f"{int(time.time() * 1000):x}{next(_IMP_CONTA) % 65536:04x}"
    cart = os.path.join(IMP_DIR, iid)
    os.makedirs(cart, exist_ok=True)
    with open(os.path.join(cart, "disegno.pes"), "wb") as fh:
        fh.write(IM.pes_bytes(pat))
    stampa = info.pop("_stampa", None)                      # progetti ricamo + stampa: l'immagine da stampare
    if stampa:
        with open(os.path.join(cart, "stampa.png"), "wb") as fh:
            fh.write(stampa)
        info["stampa"] = True
    render(pat, 600, ss=1, sfondo=True).save(os.path.join(cart, "anteprima.webp"), "WEBP", quality=85)
    v = None
    try:
        v = emb_immagine(IM.miniatura_semplice(pat))
        np.save(os.path.join(cart, "emb.npy"), v)
    except Exception as e:
        print("impronta non calcolata:", e)
    dop = _doppione(info)
    # allegati: una copia sola per contenuto in importa/_allegati (lo stesso PDF vale per tutta la raccolta)
    elenco_all = []
    for n, raw in d.get("allegati", []):
        h = __import__("hashlib").sha1(raw).hexdigest()[:20] + os.path.splitext(n)[1].lower()
        fa = os.path.join(IMP_DIR, "_allegati", h)
        if not os.path.exists(fa):
            os.makedirs(os.path.dirname(fa), exist_ok=True)
            with open(fa, "wb") as fh:
                fh.write(raw)
        elenco_all.append([n, h])
    info["allegati"] = elenco_all
    info.update(id=iid, fonte=fonte, data=time.strftime("%Y-%m-%d %H:%M:%S"), tema=_tema_proposto(info, v),
                doppione=None if dop is None else {"i": dop, "percorso": DATI[dop][0]})
    if dop is None and isinstance(in_attesa, _Attesa):
        # stesso disegno già in attesa (altro formato o altro nome): resta «nuovo» quello nel formato della
        # macchina dell'utente, l'altro diventa «uguale a un altro in attesa»
        altro = in_attesa.cerca(info)
        if altro is not None:
            if IM.prio(info.get("formato")) < IM.prio(altro.get("formato")):
                for b in _imp_carica():                       # chi puntava al vecchio ora punta al nuovo
                    if b.get("doppio_di") == altro["id"] or b is altro:
                        b["doppio_di"] = iid
                        try:
                            _imp_scrivi_info(b)
                        except OSError:
                            pass
                in_attesa.togli(altro)
            else:
                info["doppio_di"] = altro["id"]
    if not info.get("doppio_di") and dop is None and isinstance(in_attesa, _Attesa):
        in_attesa.metti(info)
    with open(os.path.join(cart, "info.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False)
    _IMP_STATO["doppi" if dop is not None or info.get("doppio_di") else "nuovi"] += 1


def _imp_lavoratore():
    """Un'importazione alla volta, in sottofondo: la pagina chiede lo stato ogni secondo."""
    import importa as IM
    IM.PREFERITO = FORMATO                                  # formato della macchina: vince sugli altri
    while True:
        with _IMP_LOCK:
            if not _IMP_CODA:
                _IMP_STATO["attivo"] = False
                return
            tipo, val, fonte = _IMP_CODA.pop(0)
            _IMP_CORRENTE[0] = (tipo, val, fonte)
            _imp_coda_salva()
        try:
            allegati = []                                    # PDF, istruzioni, tabelle dei colori
            if tipo == "file":
                nome, raw = val
                files = IM.espandi(nome, raw, allegati)
            elif tipo == "percorso":                         # (file, percorso relativo alla cartella scelta)
                files = IM.espandi(val[1], open(val[0], "rb").read(), allegati)
                if not val[0].lower().endswith(".zip") and                         os.path.dirname(os.path.abspath(val[0])) != os.path.abspath(IMP_CODA_DIR):
                    # disegno preso da una cartella: allegati della sua cartella (o di quella sopra), con lo stesso
                    # percorso relativo del disegno così assegna_allegati li abbina
                    pref = "/".join(x for x in val[1].replace("\\", "/").split("/")[:-1] if x)
                    allegati = [((pref + "/" if pref else "") + n.split("/")[-1], r)
                                for n, r in IM.allegati_vicini(val[0], _IMP_ALL_CACHE)]
            else:
                _IMP_STATO["ora"] = "scarico " + val[:80]
                nome, raw = IM.scarica_link(val)
                files = IM.espandi(nome, raw, allegati)
            sorgenti = _imp_sorgenti()
            gruppi = IM.raggruppa(files)
            IM.assegna_allegati(gruppi, allegati)
            in_attesa = _Attesa()
            _IMP_STATO["dis_tot"] = _IMP_STATO.get("dis_tot", 0) + len(gruppi)
            for d in gruppi:
                _IMP_STATO["ora"] = d["nome"]
                _IMP_STATO["dis_fatti"] = _IMP_STATO.get("dis_fatti", 0) + 1
                try:
                    _imp_uno(d, fonte, sorgenti, in_attesa)
                except Exception as e:
                    _IMP_STATO["errori"].append(f"{d['nome']}: {e}"[:200])
        except Exception as e:
            _IMP_STATO["errori"].append(f"{fonte}: {e}"[:300])
        _IMP_STATO["fatti"] += 1
        _IMP_STATO["ora"] = ""
        with _IMP_LOCK:
            _IMP_CORRENTE[0] = None
            _imp_coda_salva()
        if tipo == "percorso" and os.path.dirname(os.path.abspath(val[0])) == os.path.abspath(IMP_CODA_DIR):
            try:
                os.remove(val[0])                            # file caricato dalla pagina: ormai è in attesa
            except OSError:
                pass


ALLEGATI = {".pdf", ".txt", ".rtf", ".doc", ".docx", ".odt", ".htm", ".html", ".jpg", ".jpeg", ".png", ".gif", ".bmp"}


def allegati_di(i):
    """Istruzioni, tabelle colori, PDF nella cartella della raccolta del disegno (messi lì dall'importazione o da
    «Sistema la libreria»). L'immagine da stampare di un altro disegno non conta."""
    cart = os.path.dirname(os.path.join(OUT, DATI[i][0]))
    mio = os.path.splitext(os.path.basename(DATI[i][0]))[0] + " - stampa.png"
    try:
        nomi = sorted(os.listdir(cart), key=lambda f: (os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png", ".gif", ".bmp"), f.lower()))
    except OSError:
        return []
    return [f for f in nomi if os.path.splitext(f)[1].lower() in ALLEGATI
            and (not f.endswith(" - stampa.png") or f == mio)][:30]


def _sistema_in_corso():
    return _LAVORO.get("tipo") == "sistema" and not _LAVORO.get("finito") and _LAVORO.get("modo") != "anteprima"


def imp_accoda(voci):
    """voci: [(tipo, valore, fonte)]. Parte il lavoratore se è fermo."""
    if _sistema_in_corso():
        raise ValueError("sto sistemando la libreria: importa quando ho finito")
    with _IMP_LOCK:
        if not _IMP_STATO["attivo"]:
            _IMP_STATO.update(fatti=0, nuovi=0, doppi=0, gia=0, errori=[], ora="", totale=0, dis_tot=0, dis_fatti=0)
            _IMP_ALL_CACHE.clear()
        _IMP_CODA.extend(voci)
        _IMP_STATO["totale"] = _IMP_STATO.get("totale", 0) + len(voci)
        _imp_coda_salva()
        if not _IMP_STATO["attivo"]:
            _IMP_STATO["attivo"] = True
            threading.Thread(target=_imp_lavoratore, daemon=True).start()
    return imp_stato()


def imp_stato():
    return dict(_IMP_STATO, in_coda=len(_IMP_CODA), conferma=dict(_CONF))


def imp_barra():
    """Per la barra in alto (tutte le pagine): analisi dei disegni importati e «Metti in libreria»."""
    a, c = _IMP_STATO, _CONF
    return {"analisi": {"attivo": a["attivo"], "fatti": a.get("dis_fatti", 0), "totale": a.get("dis_tot", 0)},
            "libreria": {"attivo": c["attivo"], "fatti": c["fatti"], "totale": c["totale"], "messi": c["messi"]},
            "attesa": sum(1 for x in os.listdir(IMP_DIR) if not x.startswith("_")) if os.path.isdir(IMP_DIR) else 0}


_IMP_CACHE = {}                                            # id -> (data di info.json, dati): niente 3.000 letture a ogni giro


def _imp_carica():
    """Disegni in attesa così come sono in info.json (anche la firma). Gli oggetti sono quelli della cache."""
    if not os.path.isdir(IMP_DIR):
        return []
    out, visti = [], set()
    for e in os.scandir(IMP_DIR):
        if e.name.startswith("_") or not e.is_dir():
            continue
        f = os.path.join(e.path, "info.json")
        try:
            mt = os.stat(f).st_mtime_ns
            c = _IMP_CACHE.get(e.name)
            if c is None or c[0] != mt:
                c = _IMP_CACHE[e.name] = (mt, json.load(open(f, encoding="utf-8")))
            out.append(c[1])
            visti.add(e.name)
        except (OSError, ValueError):
            pass
    for k in [k for k in _IMP_CACHE if k not in visti]:
        del _IMP_CACHE[k]
    return out


def _imp_scrivi_info(b):
    f = os.path.join(IMP_DIR, b["id"], "info.json")
    with open(f + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(b, fh, ensure_ascii=False)
    os.replace(f + ".tmp", f)


def imp_elenco():
    tutti = _imp_carica()
    ids = {b.get("id") for b in tutti}
    orfani = {}                                          # il disegno di riferimento è andato in libreria o scartato
    for b in tutti:
        if b.get("doppio_di") and b["doppio_di"] not in ids:
            orfani.setdefault(b["doppio_di"], []).append(b)
    if orfani:
        import importa as IM
        IM.PREFERITO = FORMATO
        for gruppo in orfani.values():
            gruppo.sort(key=lambda x: IM.prio(x.get("formato")))
            capo = gruppo[0]                             # il migliore rimasto diventa il riferimento
            capo.pop("doppio_di", None)
            if capo.get("firma") and capo.get("doppione") is None:
                try:
                    j = _doppione(capo)
                    if j is not None:
                        capo["doppione"] = {"i": j, "percorso": DATI[j][0]}
                except Exception:
                    pass
            for x in gruppo[1:]:
                x["doppio_di"] = capo["id"]
            for x in gruppo:
                try:
                    _imp_scrivi_info(x)
                except OSError:
                    pass
    out = []
    for b in tutti:
        c = {k: v for k, v in b.items() if k != "firma"}
        dp = c.get("doppione")
        if isinstance(dp, dict) and isinstance(dp.get("i"), int) and 0 <= dp["i"] < len(DATI):
            d = DATI[dp["i"]]
            c["doppione"] = dict(dp, w=d[2], h=d[3], col=d[4], punti=d[5], seq=d[7] if len(d) > 7 else [])
        out.append(c)
    return sorted(out, key=lambda b: b.get("data", ""), reverse=True)


class _Attesa:
    """Disegni già in attesa, per non farne entrare due uguali nella stessa importazione: stesso file (sha1) o
    stesso disegno in un altro formato / con un altro nome (misure ±1 mm, punti ±3%, firma come _doppione)."""

    def __init__(self):
        self.sha, self.mis = set(), {}
        for b in _imp_carica():
            self.sha.add(b.get("sha1"))
            if not b.get("doppione") and not b.get("doppio_di"):
                self.metti(b)

    def __contains__(self, sha):
        return sha in self.sha

    def add(self, sha):
        self.sha.add(sha)

    def metti(self, b):
        if b.get("firma"):
            self.mis.setdefault((round(b["larg"]), round(b["alt"])), []).append(b)

    def togli(self, b):
        lst = self.mis.get((round(b["larg"]), round(b["alt"])), [])
        if b in lst:
            lst.remove(b)

    def cerca(self, info):
        kw, kh = round(info["larg"]), round(info["alt"])
        for dw in (-1, 0, 1):
            for dh in (-1, 0, 1):
                for b in self.mis.get((kw + dw, kh + dh), ()):
                    if abs(b["larg"] - info["larg"]) > 1 or abs(b["alt"] - info["alt"]) > 1:
                        continue
                    if abs(b["punti"] - info["punti"]) > 0.03 * max(b["punti"], info["punti"]):
                        continue
                    if sum(abs(x - y) for x, y in zip(b["firma"], info["firma"])) < 0.08:
                        return b
        return None


def _imp_dir(iid):
    if not re.fullmatch(r"[0-9a-f]+", str(iid or "")):
        raise ValueError("id non valido")
    d = os.path.join(IMP_DIR, iid)
    if not os.path.isdir(d):
        raise ValueError("disegno non trovato (già messo in libreria o scartato?)")
    return d


_CONF = {"attivo": False, "fatti": 0, "totale": 0, "errori": [], "messi": 0}       # «Metti in libreria» in sottofondo


def imp_conferma_avvia(body):
    """Parte in sottofondo e risponde subito: la pagina segue l'avanzamento (barra in alto)."""
    voci = [v for v in body.get("voci", []) if isinstance(v, dict)]
    if _sistema_in_corso():
        raise ValueError("sto sistemando la libreria: aspetta che finisca")
    with _IMP_LOCK:
        if _CONF["attivo"]:
            raise ValueError("sto già mettendo in libreria altri disegni: aspetta che finisca")
        _CONF.update(attivo=True, fatti=0, totale=len(voci), errori=[], messi=0)
    threading.Thread(target=_imp_conferma_lavora, args=(voci,), daemon=True).start()
    return dict(_CONF)


def _imp_conferma_lavora(voci):
    try:
        imp_conferma({"voci": voci})
    except Exception as e:
        _CONF["errori"].append(str(e)[:300])
    finally:
        _CONF["attivo"] = False


def imp_conferma(body):
    """Mette in libreria i disegni scelti: [{id, tema, raccolta, nome}] (i campi vuoti restano quelli proposti).
    A blocchi: i calcoli su tutto il catalogo (colori, indice di ricerca, file json) si fanno una volta ogni 50
    disegni e alla fine, non per ogni disegno (con 1.700 disegni prima ci voleva un'ora)."""
    global EMB, _n_emb
    fatti, blocco = [], []                      # blocco: [(i, rel, voce, emb|None, sha1, cartella)] non ancora salvati

    def salva_blocco():
        global EMB, _n_emb
        if not blocco:
            return
        with _IMP_LOCK:
            f = os.path.join(CAT, "importati.json")
            try:
                elenco = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                elenco = []
            elenco += [x[2] for x in blocco]
            with open(f + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(elenco, fh, ensure_ascii=False)
            os.replace(f + ".tmp", f)
            # impronte SigLIP: in coda a clip_extra (sul disco) e subito nella ricerca per significato
            con = [x for x in blocco if x[3] is not None]
            if con:
                fx, fj = os.path.join(CAT, "clip_extra.npy"), os.path.join(CAT, "clip_extra.json")
                try:
                    ex, px = np.load(fx), json.load(open(fj, encoding="utf-8"))
                except (OSError, ValueError):
                    ex, px = np.zeros((0, len(con[0][3])), np.float16), []
                np.save(fx, np.vstack([ex] + [x[3][None].astype(np.float16) for x in con]))
                with open(fj, "w", encoding="utf-8") as fh:
                    json.dump(px + [x[1] for x in con], fh, ensure_ascii=False)
                if EMB is not None and _n_emb >= N_DATIJS:
                    if len(EMB) < len(DATI):
                        EMB = np.vstack([EMB[:_n_emb], np.zeros((len(DATI) - _n_emb, EMB.shape[1]), np.float32)])
                    for x in con:
                        EMB[x[0]] = x[3]
                    _n_emb = len(DATI)
            s = _imp_sorgenti()
            s.update({x[4]: x[1] for x in blocco})
            with open(F_IMP_SORG + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(s, fh, ensure_ascii=False)
            os.replace(F_IMP_SORG + ".tmp", F_IMP_SORG)
        for x in blocco:                         # tolti dall'attesa solo ora che sono registrati nel catalogo
            shutil.rmtree(x[5], ignore_errors=True)
        prepara_colori()                         # colori nuovi abbinati ai fili che ho (una volta per blocco)
        calcola_fili()
        blocco.clear()

    for v in body.get("voci", []):
        try:
            d = _imp_dir(v.get("id"))
            info = json.load(open(os.path.join(d, "info.json"), encoding="utf-8"))
            tema = pulisci(v.get("tema") or info["tema"])[:60]
            racc = pulisci(v.get("raccolta") or info.get("raccolta") or tr("Importati"))[:60]
            nome = pulisci(v.get("nome") or info["nome"])[:50]
            # Windows non apre percorsi oltre 260 caratteri: raccolta e nome si accorciano (spazio per « (n)» e
            # « - stampa.png»)
            resto = 245 - len(os.path.join(OUT, tema)) - len(" - stampa.png") - 6
            if len(racc) + len(nome) + 2 > resto:
                racc = racc[:max(12, resto // 2)].rstrip(" .-_")
                nome = nome[:max(12, resto - len(racc) - 2)].rstrip(" .-_")
            os.makedirs(os.path.join(OUT, tema, racc), exist_ok=True)
            rel = os.path.join(tema, racc, nome + ".pes")
            n = 2
            while os.path.exists(os.path.join(OUT, rel)):
                rel = os.path.join(tema, racc, f"{nome} ({n}).pes")
                n += 1
            shutil.copyfile(os.path.join(d, "disegno.pes"), os.path.join(OUT, rel))
            if os.path.exists(os.path.join(d, "stampa.png")):    # accanto al PES: «<nome> - stampa.png»
                shutil.copyfile(os.path.join(d, "stampa.png"), os.path.join(OUT, rel[:-4] + " - stampa.png"))
            for n_all, h_all in info.get("allegati", []):        # istruzioni, PDF: nella cartella della raccolta
                src_all = os.path.join(IMP_DIR, "_allegati", h_all)
                if not os.path.exists(src_all):
                    continue
                dest_all = os.path.join(OUT, tema, racc, pulisci(n_all)[:80])
                if os.path.exists(dest_all) and os.path.getsize(dest_all) == os.path.getsize(src_all):
                    continue                                 # c'è già (altro disegno della stessa raccolta)
                b_all, e_all = os.path.splitext(dest_all)
                k_all = 2
                while os.path.exists(dest_all):
                    dest_all = f"{b_all} ({k_all}){e_all}"
                    k_all += 1
                shutil.copyfile(src_all, dest_all)
            voce = [rel, tema, info["larg"], info["alt"], info["colori"], info["punti"], info.get("formato", ".pes"),
                    info.get("sequenza", [])]
            fe = os.path.join(d, "emb.npy")
            ve = np.load(fe).astype(np.float32) if os.path.exists(fe) else None
            if ve is None:                       # impronta mancata durante l'analisi: si calcola adesso
                try:
                    import importa as IM
                    import pyembroidery
                    ve = emb_immagine(IM.miniatura_semplice(pyembroidery.read(os.path.join(d, "disegno.pes"))))
                except Exception as e:
                    print("impronta non calcolata:", e)
            with _IMP_LOCK:
                i = _aggiungi_al_catalogo(voce, ricalcola=False)
            _miniature_veloci(i, rel, d)
            blocco.append((i, rel, voce, ve, info["sha1"], d))
            fatti.append({"i": i, "percorso": rel})
            _CONF["messi"] += 1
        except Exception as e:
            _CONF["errori"].append(f"{v.get('id')}: {e}"[:200])
        _CONF["fatti"] += 1
        if len(blocco) >= 50:
            salva_blocco()
    salva_blocco()
    _imp_pulisci_allegati()
    return {"fatti": fatti}


def _imp_pulisci_allegati():
    """Allegati in attesa che nessun disegno in attesa usa più (messi in libreria o scartati)."""
    cart = os.path.join(IMP_DIR, "_allegati")
    if not os.path.isdir(cart):
        return
    usati = {h for b in imp_elenco() for _n, h in b.get("allegati", [])}
    for f in os.listdir(cart):
        if f not in usati:
            try:
                os.remove(os.path.join(cart, f))
            except OSError:
                pass


COL_VAR_F = os.path.join(CAT, "colori_varianti.json")
COL_SCRIVIBILI = (".pes", ".jef", ".vp3", ".xxx", ".u01")


def _col_var():
    try:
        return json.load(open(COL_VAR_F, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _col_var_salva(cv):
    with open(COL_VAR_F + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(cv, fh, ensure_ascii=False)
    os.replace(COL_VAR_F + ".tmp", COL_VAR_F)


def _fili_di(pat):
    """[[#colore, descrizione, numero di catalogo]] uno per blocco di colore."""
    out = []
    for _s, t in pat.get_as_colorblocks():
        c = (t.color if t is not None and t.color is not None else 0x333333) & 0xFFFFFF
        out.append(["#%06x" % c, (getattr(t, "description", None) or "") if t is not None else "",
                    (getattr(t, "catalog_number", None) or "") if t is not None else ""])
    return out


def _scrivi_fili(i, fili):
    """Riscrive il file del disegno i con questi fili (stessi punti) e aggiorna miniature e abbinamenti."""
    import pyembroidery
    rel = DATI[i][0]
    full = os.path.join(OUT, rel)
    ext = os.path.splitext(rel)[1].lower()
    pat = pyembroidery.read(full)
    if pat is None:
        raise ValueError("non riesco a leggere il file")
    n = sum(1 for _ in pat.get_as_colorblocks())
    if n != len(fili):
        raise ValueError(f"hanno un numero di colori diverso ({len(fili)} contro {n})")
    nuovi = []
    for h, desc, cat in fili:
        f = pyembroidery.EmbThread()
        f.color = int(h[1:], 16)
        if desc:
            f.description = desc
        if cat:
            f.catalog_number = cat
        nuovi.append(f)
    pat.threadlist = nuovi
    bk = os.path.join(CAT, "colori_backup")
    os.makedirs(bk, exist_ok=True)
    copia = os.path.join(bk, f"{i}_{os.path.basename(rel)}")
    if not os.path.exists(copia):                           # la prima versione del file resta sempre
        shutil.copy2(full, copia)
    tmp = full + ".tmp" + ext
    if ext == ".pes":
        pyembroidery.write_pes(pat, tmp, {"pes version": 6})
    else:
        pyembroidery.write(pat, tmp)
    os.replace(tmp, full)
    DATI[i][7] = [f[0] for f in fili][:40]
    prepara_colori()
    calcola_fili()
    _miniature_nuove(i, rel)


def colori_di(i):
    """Le versioni dei colori di un disegno (per la scheda), o None se ne ha una sola."""
    v = _col_var().get(DATI[i][0])
    if not v or len(v.get("var", [])) < 2:
        return None
    return {"attiva": v["attiva"], "var": [{"nome": x["nome"], "seq": [f[0] for f in x["fili"]]} for x in v["var"]]}


def colori_scegli(body):
    i, k = int(body.get("i", -1)), int(body.get("k", -1))
    if not 0 <= i < len(DATI) or i in TOLTI:
        raise ValueError("disegno non trovato")
    cv = _col_var()
    v = cv.get(DATI[i][0])
    if not v or not 0 <= k < len(v["var"]):
        raise ValueError("versione dei colori non trovata")
    with _COL_LOCK:
        _scrivi_fili(i, v["var"][k]["fili"])
        v["attiva"] = k
        _col_var_salva(cv)
    return colori_di(i)


_COL_LOCK = threading.Lock()


def imp_prendi_colori(body):
    """Il disegno importato è un doppione con altri colori: il file del catalogo prende i suoi colori (stesse forme,
    cambia solo la tabella dei fili). Le versioni restano tutte: nella scheda si passa dall'una all'altra."""
    import pyembroidery
    d = _imp_dir(body.get("id"))
    info = json.load(open(os.path.join(d, "info.json"), encoding="utf-8"))
    i = int((info.get("doppione") or {}).get("i", -1))
    if not 0 <= i < len(DATI) or i in TOLTI:
        raise ValueError("il disegno del catalogo non c'è più")
    rel = DATI[i][0]
    ext = os.path.splitext(rel)[1].lower()
    if ext not in COL_SCRIVIBILI:
        raise ValueError(f"i file {ext} non tengono i colori o non so riscriverli")
    nuovo, vecchio = pyembroidery.read(os.path.join(d, "disegno.pes")), pyembroidery.read(os.path.join(OUT, rel))
    if nuovo is None or vecchio is None:
        raise ValueError("non riesco a leggere uno dei due file")
    fili = _fili_di(nuovo)
    with _COL_LOCK:
        cv = _col_var()
        v = cv.get(rel) or {"attiva": 0, "var": [{"nome": "Originali", "fili": _fili_di(vecchio)}]}
        nome = "da " + os.path.basename(info.get("origine") or info.get("fonte") or "importato")
        k = next((n for n, x in enumerate(v["var"]) if [f[0] for f in x["fili"]] == [f[0] for f in fili]), None)
        if k is None:
            v["var"].append({"nome": nome, "fili": fili})
            k = len(v["var"]) - 1
        _scrivi_fili(i, v["var"][k]["fili"])
        v["attiva"] = k
        cv[rel] = v
        _col_var_salva(cv)
    imp_scarta({"ids": [body.get("id")]})
    return {"ok": True, "i": i, "percorso": rel}


def imp_scarta(body):
    n = 0
    for iid in body.get("ids", [])[:5000]:
        try:
            d = _imp_dir(iid)
        except ValueError:
            continue
        try:
            sha = json.load(open(os.path.join(d, "info.json"), encoding="utf-8")).get("sha1")
            if sha and body.get("ricorda", True):           # non riproporlo alla prossima scansione dei Download
                _imp_segna_sorgente(sha, "scartato")
        except (OSError, ValueError):
            pass
        shutil.rmtree(d, ignore_errors=True)
        n += 1
    _imp_pulisci_allegati()
    return {"scartati": n}


def imp_cartella(body):
    import importa as IM
    IM.PREFERITO = FORMATO
    cart = os.path.expandvars(os.path.expanduser(str(body.get("percorso") or "").strip().strip('"')))
    giorni = body.get("giorni")
    giorni = float(giorni) if giorni not in (None, "", 0, "0") else None
    files = IM.file_in_cartella(cart, giorni)
    if not files:
        raise ValueError("nessun file da ricamo o zip in quella cartella" + (f" negli ultimi {giorni:g} giorni" if giorni else ""))
    # le sottocartelle diventano la raccolta (D:\Nuovi\Natale 2026\renna.pes -> raccolta «Natale 2026»)
    voci = [("percorso", (f, os.path.relpath(f, cart)), os.path.relpath(f, cart)) for f in files]
    return dict(imp_accoda(voci), trovati=len(files))


# ---------------- OpenHoop: primo avvio, impostazioni, riordino e indicizzazione in sottofondo ----------------
_LAVORO = {"tipo": "", "fatti": 0, "totale": 0, "msg": "", "finito": False, "errore": ""}
_LAV_PROC = [None]
ESTENSIONI_RICAMO = {e.lower() for e in __import__("riordina").FORMATI} | {".zip", ".xs"}


def setup_info():
    cfg = {k: v for k, v in CFG.items() if not k.startswith("chiave_")}      # le chiavi non tornano alla pagina
    return {"primo_avvio": PRIMO_AVVIO, "config": cfg,
            "chiavi": {"pixabay": bool(CFG.get("chiave_pixabay")), "deepseek": bool(CFG.get("chiave_deepseek"))}, "config_file": CONFIG.FILE, "macchine": MACCHINE.elenco(),
            "fili": FILI.marche(), "lavoro": dict(_LAVORO), "disegni": len(DATI), "avvio": AVVIO,
            "importa": imp_barra()}


def cartelle(p):
    """Navigatore di cartelle per la pagina (il browser non può aprire la finestra di Windows sul server)."""
    if not p:
        casa = os.path.expanduser("~")
        voci = [{"nome": f"{u}:\\", "percorso": f"{u}:\\"} for u in string.ascii_uppercase if os.path.exists(f"{u}:\\")]
        voci += [{"nome": n, "percorso": os.path.join(casa, n)} for n in ("Desktop", "Documents", "Downloads")
                 if os.path.isdir(os.path.join(casa, n))]
        return {"percorso": "", "su": None, "cartelle": voci, "file_ricamo": 0}
    p = os.path.abspath(p)
    if not os.path.isdir(p):
        raise ValueError("cartella non trovata")
    sotto, n = [], 0
    try:
        for x in sorted(os.scandir(p), key=lambda x: x.name.lower()):
            if x.is_dir() and not x.name.startswith(("$", ".")):
                sotto.append({"nome": x.name, "percorso": x.path})
            elif os.path.splitext(x.name)[1].lower() in ESTENSIONI_RICAMO:
                n += 1
    except PermissionError:
        pass
    su = os.path.dirname(p) if os.path.dirname(p) != p else ""
    return {"percorso": p, "su": su, "cartelle": sotto[:500], "file_ricamo": n}


def setup_salva(body):
    """Salva le impostazioni scelte nella pagina (primo avvio o «Impostazioni»)."""
    global FORMATO
    c = dict(CFG)
    if "lingua" in body and body["lingua"] in ("it", "en", "fr", "es"):
        c["lingua"] = body["lingua"]
    if "sorgenti" in body:
        src = [os.path.abspath(s) for s in body["sorgenti"] if str(s).strip()]
        if any(not os.path.isdir(s) for s in src):
            raise ValueError("una delle cartelle dei ricami non esiste")
        c["sorgenti"] = src
    if body.get("archivio"):
        arc = os.path.abspath(body["archivio"])
        if any(os.path.normcase(arc) == os.path.normcase(s) for s in c["sorgenti"]):
            raise ValueError("l'archivio deve essere una cartella diversa da quelle dei ricami")
        c["archivio"] = arc
    if body.get("marca") in MACCHINE.MARCHE:
        c["marca"] = body["marca"]
    if body.get("formato") in MACCHINE.FORMATI:
        c["formato"] = body["formato"]
    if isinstance(body.get("telai"), list):
        c["telai"] = [[float(a), float(b)] for a, b in body["telai"] if 10 <= float(a) <= 600 and 10 <= float(b) <= 600][:12]
    for k in ("taglia_salti",):
        if k in body:
            c[k] = bool(body[k])
    if body.get("unita") in ("mm", "in"):
        c["unita"] = body["unita"]
    if body.get("fili") in FILI.marche():
        c["fili"] = body["fili"]
    for k in ("chiave_pixabay", "chiave_deepseek"):       # vuoto = lascia com'è, "-" = togli
        v = str(body.get(k) or "").strip()
        if v == "-":
            c.pop(k, None)
        elif v and len(v) < 200 and re.fullmatch(r"[\w.:-]+", v):
            c[k] = v
    cambia_lingua = c.get("lingua") != CFG.get("lingua")
    CONFIG.salva(c)
    CFG.clear()
    CFG.update(c)
    if cambia_lingua and (not _LAVORO["tipo"] or _LAVORO["finito"]):
        riavvia()                                   # nomi di temi, cartelle e fili si leggono all'avvio
        return dict(setup_info(), riavvio=True)
    FORMATO = c["formato"]
    if c.get("telai") and not STATO.get("telai"):
        TELAI[:] = sorted(([min(t), max(t)] for t in c["telai"]), key=lambda t: t[0] * t[1])
    return setup_info()


def _segui(proc, log, tipo, al_termine):
    """Legge il registro del processo (riordina / indicizza_clip) e aggiorna l'avanzamento."""
    import subprocess  # noqa: F401
    rx_tot = re.compile(r"disegni trovati: (\d+)")
    rx_av = re.compile(r"^\s*(\d+)/(\d+)")
    while proc.poll() is None:
        time.sleep(1)
        try:
            righe = open(log, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for r in righe[-30:]:
            m = rx_tot.search(r)
            if m and tipo == "riordino":
                _LAVORO["totale"] = int(m.group(1))
            m = rx_av.match(r)
            if m:
                _LAVORO["fatti"], _LAVORO["totale"] = int(m.group(1)), int(m.group(2))
        if righe:
            _LAVORO["msg"] = righe[-1][-160:]
    try:
        coda = open(log, encoding="utf-8", errors="replace").read()
    except OSError:
        coda = ""
    if proc.returncode != 0 or "FINE" not in coda:
        _LAVORO.update(finito=True, errore=(coda.strip().splitlines() or ["errore"])[-1][-300:])
        return
    _LAVORO.update(fatti=_LAVORO["totale"], fase="finale")     # finito lo mette al_termine (può riavviare)
    al_termine()


def avvia_lavoro(tipo, extra=None):
    """tipo: «riordino» (cartelle dell'utente -> archivio, poi riavvio del server) o «clip» (ricerca per
    significato: impronte SigLIP di tutte le miniature, riprende da dove era)."""
    import subprocess
    if _LAV_PROC[0] is not None and _LAV_PROC[0].poll() is None:
        raise ValueError("c'è già un lavoro in corso")
    if tipo == "riordino" and (not CFG.get("sorgenti") or not CFG.get("archivio")):
        raise ValueError("scegli prima le cartelle")
    arc = CFG["archivio"] if tipo == "riordino" else OUT
    os.makedirs(os.path.join(arc, "_catalogo"), exist_ok=True)
    log = os.path.join(arc, "_catalogo", f"{tipo}.log")
    if tipo == "sistema" and (_IMP_STATO["attivo"] or _CONF["attivo"]):
        raise ValueError("aspetta che finisca l'importazione in corso")
    script = {"riordino": "riordina.py", "clip": "indicizza_clip.py", "sistema": "sistema_libreria.py"}[tipo]
    args = [sys.executable, "-u", os.path.join(QUI, script)] + (["config"] if tipo == "riordino" else [OUT]) + (extra or [])
    fh = open(log, "w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen(args, stdout=fh, stderr=subprocess.STDOUT, cwd=QUI, env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _LAV_PROC[0] = p
    _LAVORO.update(tipo=tipo, fatti=0, totale=0, msg="", finito=False, errore="", fase="", riavvio=False,
                   modo=(extra or [""])[0])

    def fine():
        if tipo == "sistema":                    # anteprima: solo il rapporto; applica/annulla: catalogo nuovo
            _LAVORO["finito"] = True
            if (extra or [""])[0] in ("applica", "annulla"):
                riavvia()
            return
        if tipo == "riordino":
            c = dict(CFG, pronto=True)
            CONFIG.salva(c)
            _LAVORO["finito"] = True
            riavvia()
        elif tipo == "clip" and not _LAVORO.get("errore"):
            for script, argx in (("temi_immagine.py", []), ("varianti.py", ["--json"])):
                try:                            # VARIE smistati guardando l'immagine, poi stesso disegno in più misure
                    subprocess.run([sys.executable, os.path.join(QUI, script), OUT] + argx, cwd=QUI, timeout=1800,
                                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except Exception:
                    pass
            try:                                # riavvio solo se ci sono impronte nuove (niente giri a vuoto)
                nuove = int(open(os.path.join(CAT, "clip_n.txt")).read()) > _n_emb
            except (OSError, ValueError):
                nuove = False
            _LAVORO["finito"] = True
            if nuove:
                riavvia()                       # rilegge impronte e varianti

    threading.Thread(target=_segui, args=(p, log, tipo, fine), daemon=True).start()
    return dict(_LAVORO)


# ---------------- aggiornamenti (aggiorna.py): controllo all'avvio, installazione con un clic ----------------
AGG = {"info": None, "attivo": False, "stato": "", "errore": ""}
AGG_F = os.path.join(CONFIG.CARTELLA, "aggiornamento.json")


def _occupato():
    """Un lavoro che un aggiornamento (o il suo riavvio) romperebbe."""
    if _IMP_STATO.get("attivo") or _CONF.get("attivo"):
        return "c'è un'importazione o un «Metti in libreria» in corso"
    if _LAVORO.get("tipo") and not _LAVORO.get("finito") and not _LAVORO.get("errore"):
        return "c'è un lavoro in corso (riordino, analisi o sistemazione)"
    return None


def agg_controlla(forza=False):
    import aggiorna as AG
    if not forza:
        try:
            c = json.load(open(AGG_F, encoding="utf-8"))
            if time.time() - c.get("controllato", 0) < 20 * 3600 and c.get("attuale") == AG.versione():
                AGG["info"] = c
                return c
        except (OSError, ValueError):
            pass
    c = AG.controlla()
    AGG["info"] = c
    try:
        os.makedirs(os.path.dirname(AGG_F), exist_ok=True)
        with open(AGG_F, "w", encoding="utf-8") as fh:
            json.dump(c, fh, ensure_ascii=False)
    except OSError:
        pass
    return c


def agg_info():
    import aggiorna as AG
    c = AGG["info"] or {}
    nuova = c.get("nuova") if c.get("nuova") and AG.piu_nuova(c["nuova"], AG.versione()) else None
    return {"attuale": AG.versione(), "nuova": nuova, "note": c.get("note", "") if nuova else "",
            "pagina": c.get("pagina"), "auto": CFG.get("aggiornamenti", True), "precedente": AG.precedente(),
            "attivo": AGG["attivo"], "stato": AGG["stato"], "errore": AGG["errore"]}


def agg_azione(body):
    import aggiorna as AG
    az = body.get("azione")
    if az == "auto":
        CFG["aggiornamenti"] = bool(body.get("valore"))
        c = CONFIG.leggi()
        c["aggiornamenti"] = CFG["aggiornamenti"]
        CONFIG.salva(c)
        return agg_info()
    if az == "controlla":
        agg_controlla(forza=True)
        return agg_info()
    if az not in ("installa", "ripristina"):
        raise ValueError("azione non valida")
    if AGG["attivo"]:
        raise ValueError("aggiornamento già in corso")
    perche = _occupato()
    if perche:
        raise ValueError(perche + ": aspetta che finisca e riprova")
    info = AGG["info"] or {}
    if az == "installa" and not info.get("nuova"):
        raise ValueError("nessuna versione nuova")

    def lavora():
        AGG.update(attivo=True, errore="", stato="")
        try:
            def avanza(m):
                AGG["stato"] = m
                print("aggiornamento:", m, flush=True)
            if az == "installa":
                AG.installa(info, avanza)
            else:
                AG.ripristina(avanza)
            try:
                os.remove(AGG_F)                         # versione cambiata: al riavvio si ricontrolla
            except OSError:
                pass
            riavvia()
        except Exception as e:
            AGG["errore"] = str(e)
            AGG["attivo"] = False
    threading.Thread(target=lavora, daemon=True).start()
    return agg_info()


def riavvia():
    """Riavvia il server (dopo il riordino: legge il catalogo nuovo). Su Windows execv apre un processo nuovo."""
    _LAVORO["riavvio"] = True                   # la pagina lo vede e si ricarica quando il server torna

    def dopo():
        time.sleep(1.5)
        args = [a for a in sys.argv if a != "--no-browser"] + ["--no-browser"]
        if CFG.get("archivio") and CFG["archivio"] not in args:
            args = [args[0]] + [a for a in args[1:] if a.startswith("--")]
        os.execv(sys.executable, [sys.executable] + args)
    threading.Thread(target=dopo, daemon=True).start()


def clip_da_fare():
    """Impronte SigLIP mancanti (archivio nuovo o cresciuto): si preparano in sottofondo all'avvio."""
    return CFG.get("pronto") and not PRIMO_AVVIO and _n_emb < N_DATIJS


# ---------------- HTTP ----------------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        try:
            if cache == "rev":
                # immagini per numero di disegno: il numero può passare a un altro disegno (toppe, eliminati, riordino)
                # o l'anteprima può cambiare => il browser ricontrolla sempre (304 se uguale, quasi gratis)
                import hashlib
                etag = '"' + hashlib.md5(body).hexdigest() + '"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    return
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if cache == "rev":
                self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache" if cache == "rev" else "max-age=86400" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True   # il browser ha chiuso prima (F5, cambio pagina): niente da fare

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path == "/":
                return self._send(200, open(os.path.join(QUI, "app.html"), encoding="utf-8").read(), "text/html; charset=utf-8")
            m = re.fullmatch(r"/brand/([\w-]+\.(png|ico))", u.path)        # OpenHoop: logo e icone
            if m and os.path.exists(os.path.join(QUI, "brand", m.group(1))):
                return self._send(200, open(os.path.join(QUI, "brand", m.group(1)), "rb").read(),
                                  "image/png" if m.group(2) == "png" else "image/x-icon")
            if u.path == "/lingua.js":                               # OpenHoop: lingua dell'interfaccia
                return self._send(200, lingua_js(), "application/javascript; charset=utf-8")
            m = re.fullmatch(r"/img/(\d+)\.webp", u.path)
            if m and int(m.group(1)) < len(DATI):
                i = int(m.group(1))
                hd = os.path.join(CAT, "img_hd", f"{i}.webp")
                f = miniatura_hd(i)                                   # miniatura 'bella': generata la prima volta
                if f is not None and i not in _VELOCI:
                    try:                                              # appena importati: c'è già quella veloce
                        f.result(timeout=60)
                    except Exception:
                        pass
                f = hd if os.path.exists(hd) else os.path.join(CAT, "img", f"{i}.webp")
                return self._send(200, open(f, "rb").read(), "image/webp", cache="rev" if os.path.exists(hd) else False)
            if u.path == "/api/info":
                temi, aut = {}, {}
                for n_, d in enumerate(DATI):
                    if n_ in TOLTI:
                        continue
                    temi[d[1]] = temi.get(d[1], 0) + 1
                    if AUTORE[n_]:
                        aut[AUTORE[n_]] = aut.get(AUTORE[n_], 0) + 1
                return self._send(200, {"tot": len(DATI) - len(TOLTI), "temi": temi, "autori": aut, "telaio": TELAI[-1], "telai": TELAI,
                                        "conta_telai": conta_telai(),
                                        "clip": EMB is not None, "clip_n": _n_emb})
            if u.path == "/api/cerca":
                r = cerca(p)
                for v in r["items"][:240]:                            # comincia subito a disegnare la pagina
                    miniatura_hd(v["i"])
                return self._send(200, r)
            if u.path == "/api/dettaglio":
                i = int(p["i"])
                v = voce(i)
                v["salti"], v["tagli"] = salti_tagli(i)
                v["taglia_salti"] = bool(CFG.get("taglia_salti"))  # la macchina li taglia: niente «tagli a mano»
                v["patch"] = [{"i": IDX[r], "nome": NOMI[IDX[r]]} for r in PATCH_DI.get(DATI[i][0], [])
                              if r in IDX and IDX[r] not in TOLTI]
                _o = PATCH_ORIG.get(DATI[i][0])
                v["patch_di"] = {"i": IDX[_o], "nome": NOMI[IDX[_o]]} if _o in IDX else None
                v["allegati"] = allegati_di(i)
                v["colori_var"] = colori_di(i)
                v.update(percorso=DATI[i][0], seq=DATI[i][7] if len(DATI[i]) > 7 else [], simili=EMB is not None and i < _n_emb, n_serie=len(serie_di(i)),
                         varianti=[{"i": j, "nome": NOMI[j], "w": DATI[j][2], "h": DATI[j][3], "tel": telaio_min(DATI[j][2], DATI[j][3])}
                                   for j in GRUPPO.get(VAR.get(i), [])],
                         fili=info_fili(i), kit=KIT,
                         raccolte=[n for n, l in RACCOLTE.items() if DATI[i][0] in l],
                         diario=[e for e in DIARIO if e["percorso"] == DATI[i][0]])
                return self._send(200, v)
            if u.path == "/api/fili":                              # tabella della marca + quelli che ho
                m = p.get("marca") or KIT[0]                       # fili di un kit, con quelli che ho
                fl, _ = FILI.tabella(m)
                return self._send(200, {"marca": m, "kit": KIT, "marche": FILI.marche(),
                                        "conta": {k: sum(1 for x in POSS if x.startswith(k + "|")) for k in KIT},
                                        "fili": [dict(f, ho=f"{m}|{f['num']}" in POSS) for f in fl]})
            if u.path == "/api/spesa":
                return self._send(200, spesa([int(x) for x in p.get("ids", "").split(",") if x.isdigit()]))
            if u.path == "/api/raccolte":
                return self._send(200, {n: len(l) for n, l in sorted(RACCOLTE.items())})
            if u.path == "/api/diario":
                out = []
                for e in sorted(DIARIO, key=lambda e: e.get("data", ""), reverse=True):
                    out.append(dict(e, i=IDX.get(e["percorso"]), nome=os.path.splitext(os.path.basename(e["percorso"]))[0]))
                return self._send(200, out)
            m = re.fullmatch(r"/diario/([\w.-]+\.(jpg|jpeg|png|webp))", u.path)
            if m and os.path.exists(os.path.join(DIARIO_DIR, m.group(1))):
                return self._send(200, open(os.path.join(DIARIO_DIR, m.group(1)), "rb").read(), "image/jpeg", cache=True)
            if u.path == "/api/anteprima":                        # ric=1: punti ricalcolati (ridimensionamento intelligente)
                sc = max(0.3, min(3.0, float(p.get("scala", 100)) / 100))
                return self._send(200, anteprima(int(p["i"]), tel=p.get("tel") == "1", scala=sc, ric=p.get("ric") == "1"),
                                  "image/webp", cache="rev")
            if u.path == "/api/scala":
                return self._send(200, valuta_scala(int(p["i"]), max(0.3, min(3.0, float(p.get("scala", 100)) / 100)),
                                                    ric=p.get("ric") == "1"))
            if u.path == "/api/destinazioni":
                return self._send(200, destinazioni())
            if u.path == "/api/font":
                return self._send(200, LET.elenco())
            m = re.fullmatch(r"/font/([\w()\-]+)/preview\.png", u.path)
            if m and os.path.exists(os.path.join(LET.CARTELLA, m.group(1), "preview.png")):
                return self._send(200, open(os.path.join(LET.CARTELLA, m.group(1), "preview.png"), "rb").read(), "image/png", cache=True)
            if u.path == "/api/testo":
                pat, inf = testo_el(p)
                e = inf["elementi"][0] if inf["elementi"] else {}
                return self._send(200, {"larg": inf["larg"], "alt": inf["alt"], "bbox": inf["bbox"], "punti": inf["punti"],
                                        "mancano": e.get("mancano", []), "scala": e.get("scala"), "min": e.get("min"), "max": e.get("max")})
            if u.path == "/api/testo.webp":
                pat, inf = testo_el(p)
                from anteprima_bella import render
                b = io.BytesIO()
                if inf["punti"]:
                    render(pat, 900, ss=2, sfondo=None).save(b, "WEBP", quality=88)
                else:
                    from PIL import Image
                    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(b, "WEBP")
                return self._send(200, b.getvalue(), "image/webp", cache=True)
            if u.path == "/api/patch":
                return self._send(200, info_patch(int(p["i"]), opz_patch(p)))
            if u.path == "/api/patch/colori":                   # colori del disegno (per togliere lo sfondo)
                return self._send(200, colori_patch(int(p["i"])))
            if u.path == "/api/patch.webp":
                o = opz_patch(p)
                pat, inf = fai_patch(int(p["i"]), o)
                tess = p.get("tess", "#2b2f36") if re.fullmatch(r"#[0-9a-fA-F]{6}", p.get("tess", "")) else "#2b2f36"
                b = io.BytesIO()
                PATCH.anteprima(pat, inf, 900, tessuto=tess).save(b, "WEBP", quality=88)
                return self._send(200, b.getvalue(), "image/webp")
            if u.path == "/api/sistema":                          # «Sistema la libreria»: ultimo rapporto
                r = {}
                try:
                    r = json.load(open(os.path.join(CAT, "sistema_rapporto.json"), encoding="utf-8"))
                except (OSError, ValueError):
                    pass
                try:
                    dia = json.load(open(os.path.join(CAT, "sistema_diario.json"), encoding="utf-8"))
                    r["annullabile"] = dia.get("data")
                except (OSError, ValueError):
                    r["annullabile"] = None
                return self._send(200, r)
            if u.path == "/api/allegato":                          # apre un allegato (PDF, istruzioni) del disegno
                i = int(p["i"])
                cart = os.path.dirname(os.path.join(OUT, DATI[i][0]))
                f = os.path.join(cart, os.path.basename(p.get("f", "")))
                if os.path.splitext(f)[1].lower() not in ALLEGATI or not os.path.isfile(f):
                    return self._send(404, {"errore": "allegato non trovato"})
                os.startfile(f)
                return self._send(200, {"ok": True})
            if u.path == "/api/digit":
                return self._send(200, digit_elenco())
            if u.path == "/api/aggiornamento":
                return self._send(200, agg_info())
            if u.path == "/api/setup":                              # OpenHoop: primo avvio e impostazioni
                return self._send(200, setup_info())
            if u.path == "/api/cartelle":
                return self._send(200, cartelle(p.get("p", "")))
            if u.path == "/api/importa":                            # disegni in attesa + stato dell'importazione
                temi = sorted({d[1] for d in DATI} - {DIG_TEMA})
                return self._send(200, {"attesa": imp_elenco(), "stato": imp_stato(), "temi": temi,
                                        "recenti": len(DATI) - N_DATIJS,
                                        "download": os.path.join(os.path.expanduser("~"), "Downloads")})
            m = re.fullmatch(r"/api/importa/([0-9a-f]+)/anteprima\.webp", u.path)
            if m:
                return self._send(200, open(os.path.join(_imp_dir(m.group(1)), "anteprima.webp"), "rb").read(),
                                  "image/webp", cache=True)
            if u.path == "/api/svg/cerca":                          # «Cerca disegni gratis» (Iconify, OpenClipart, Wikimedia)
                import cerca_svg as CS
                CS.imposta_cache(os.path.join(CAT, "traduzioni.json"))
                if not p.get("q", "").strip():                      # solo «quali fonti ci sono» (all'apertura)
                    return self._send(200, {"risultati": [], "ovunque_ok": bool(CS.chiave_brave())})
                return self._send(200, CS.cerca(p.get("q", "")[:100], int(p.get("pagina") or 0), p.get("altre") == "1",
                                                p.get("tutte") == "1", p.get("ovunque") == "1"))
            if u.path == "/api/svg/file":                           # l'SVG scelto, servito da qui: il canvas resta pulito
                import cerca_svg as CS
                return self._send(200, CS.scarica(p.get("u", "")), "image/svg+xml", cache=True)
            if u.path == "/api/img/file":                           # immagine scelta (Pixabay, «ovunque»): scaricata qui
                import cerca_svg as CS
                raw, ct = CS.scarica_img(p.get("u", ""))
                return self._send(200, raw, ct, cache=True)
            m = re.fullmatch(r"/api/digit/([0-9a-f]+)/(ricamo|analisi)\.webp", u.path)
            if m:
                return self._send(200, open(os.path.join(_bozza_dir(m.group(1)), m.group(2) + ".webp"), "rb").read(), "image/webp")
            m = re.fullmatch(r"/api/digit/([0-9a-f]+)/punti", u.path)
            if m:
                return self._send(200, punti_sim(os.path.join(_bozza_dir(m.group(1)), "ricamo.pes")))
            if u.path == "/api/punti":                              # simulazione di un disegno del catalogo
                return self._send(200, punti_sim(os.path.join(OUT, DATI[int(p["i"])][0])))
            m = re.fullmatch(r"/api/digit/([0-9a-f]+)/regioni\.png", u.path)
            if m:
                f_r = os.path.join(_bozza_dir(m.group(1)), "regioni.png")
                if not os.path.exists(f_r):
                    raise ValueError("ricalcola la bozza per avere le zone")
                return self._send(200, open(f_r, "rb").read(), "image/png")
            m = re.fullmatch(r"/api/digit/([0-9a-f]+)/immagine", u.path)
            if m:
                d = _bozza_dir(m.group(1))
                f = next(x for x in os.listdir(d) if x.startswith("immagine."))
                return self._send(200, open(os.path.join(d, f), "rb").read(), "image/" + f.rsplit(".", 1)[1].replace("jpg", "jpeg"))
            if u.path == "/api/apri":                               # apre la cartella del disegno in Esplora file
                i = int(p["i"])
                os.startfile(os.path.dirname(os.path.join(OUT, DATI[i][0])))
                return self._send(200, {"ok": True})
            if u.path == "/api/cestino":                            # apre il cestino di OpenHoop in Esplora file
                c = os.path.join(OUT, "_cestino")
                os.makedirs(c, exist_ok=True)
                os.startfile(c)
                return self._send(200, {"ok": True, "cartella": c})
            return self._send(404, {"errore": "non trovato"})
        except Exception as e:
            return self._send(500, {"errore": str(e)})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if self.path.startswith("/api/importa/grezzo"):     # file scelto o trascinato, byte così come sono
            # (in base64 dentro un JSON uno zip di 600 MB supera la lunghezza massima di una stringa del browser)
            try:
                nome = os.path.basename(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("nome", ["file"])[0])
                raw = self.rfile.read(n)
                if len(raw) < n:
                    raise ValueError("caricamento interrotto")
                return self._send(200, imp_accoda([imp_file_su_disco(nome, raw)]))
            except Exception as e:
                return self._send(500, {"errore": str(e)})
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path == "/api/esci":                     # «Esci» dall'icona di OpenHoop.exe
                self._send(200, {"ok": True})
                threading.Timer(0.3, lambda: os._exit(0)).start()
                return
            if self.path == "/api/elimina":                   # nel cestino di OpenHoop (recuperabile)
                return self._send(200, elimina(body.get("ids") or []))
            if self.path == "/api/segna":
                i, tipo = int(body["i"]), body["tipo"]
                if tipo not in SET:
                    raise ValueError("tipo")
                (SET[tipo].add if body.get("valore") else SET[tipo].discard)(DATI[i][0])
                salva_stato()
                return self._send(200, {"ok": True})
            if self.path == "/api/esporta":
                return self._send(200, esporta(body))
            if self.path == "/api/patch/salva":
                return self._send(200, salva_patch(body))
            if self.path == "/api/digit":
                return self._send(200, digit_nuovo(body))
            if self.path == "/api/setup":
                return self._send(200, setup_salva(body))
            if self.path == "/api/aggiornamento":
                return self._send(200, agg_azione(body))
            if self.path == "/api/setup/avvia":                     # «riordino» o «clip»
                return self._send(200, avvia_lavoro(body.get("tipo", "")))
            if self.path == "/api/importa/file":                    # file trascinato o scelto (base64)
                raw = base64.b64decode(str(body.get("dati", "")).split(",", 1)[-1])
                nome = os.path.basename(str(body.get("nome") or "file"))
                return self._send(200, imp_accoda([imp_file_su_disco(nome, raw)]))
            if self.path == "/api/importa/cartella":
                return self._send(200, imp_cartella(body))
            if self.path == "/api/importa/link":
                links = [x.strip() for x in re.split(r"[\s,]+", str(body.get("url", ""))) if x.strip()][:50]
                if not links:
                    raise ValueError("scrivi un link")
                return self._send(200, imp_accoda([("link", x, x) for x in links]))
            if self.path == "/api/sistema":                        # {modo: anteprima|applica|annulla, passi: [...]}
                modo = body.get("modo")
                if modo not in ("anteprima", "applica", "annulla"):
                    raise ValueError("modo non valido")
                passi = [x for x in body.get("passi", []) if x in ("allegati", "nomi", "temi", "doppioni")]
                if modo != "annulla" and not passi:
                    raise ValueError("scegli almeno una correzione")
                return self._send(200, avvia_lavoro("sistema", [modo] + (["passi=" + ",".join(passi)] if passi else [])))
            if self.path == "/api/importa/conferma":
                return self._send(200, imp_conferma_avvia(body))
            if self.path == "/api/importa/scarta":
                return self._send(200, imp_scarta(body))
            if self.path == "/api/importa/colori":
                return self._send(200, imp_prendi_colori(body))
            if self.path == "/api/colori/scegli":
                return self._send(200, colori_scegli(body))
            if self.path in ("/api/digit/rifai", "/api/digit/scarta", "/api/digit/salva", "/api/digit/conferma", "/api/digit/ai"):
                fn = {"rifai": digit_rifai, "scarta": digit_scarta, "salva": digit_salva, "conferma": digit_conferma, "ai": digit_ai}
                return self._send(200, fn[self.path.rsplit("/", 1)[1]](body))
            if self.path == "/api/componi/adatta":
                return self._send(200, adatta_in_cornice(body))
            if self.path == "/api/componi/info":
                return self._send(200, fai_comp(body)[1])
            if self.path == "/api/componi/anteprima":
                pat, inf = fai_comp(body)
                from anteprima_bella import render
                b = io.BytesIO()
                render(pat, 1400, ss=2, sfondo=True).save(b, "WEBP", quality=88)
                return self._send(200, b.getvalue(), "image/webp")
            if self.path == "/api/componi/salva":
                return self._send(200, salva_comp(body))
            if self.path == "/api/fili":                            # segna i fili che ho di un kit (uno o tutti)
                m = body.get("marca") or KIT[0]
                if m not in FILI.marche():
                    raise ValueError("marca sconosciuta")
                if "tutti" in body:
                    POSS.difference_update({x for x in POSS if x.startswith(m + "|")})
                    POSS.update(f"{m}|{n}" for n in body["tutti"])
                else:
                    (POSS.add if body.get("valore") else POSS.discard)(f"{m}|{body['num']}")
                calcola_fili()
                salva_stato()
                return self._send(200, {"ok": True})
            if self.path == "/api/kit":                             # aggiungi / togli un kit, scegli il principale
                m, az = body.get("marca"), body.get("azione")
                if m not in FILI.marche():
                    raise ValueError("marca sconosciuta")
                if az == "aggiungi" and m not in KIT:
                    KIT.append(m)
                    POSS.update(f"{m}|{f['num']}" for f in FILI.tabella(m)[0])   # un kit nuovo = tutti i suoi fili
                elif az == "togli" and m in KIT and len(KIT) > 1:
                    KIT.remove(m)
                    POSS.difference_update({x for x in POSS if x.startswith(m + "|")})
                elif az == "principale" and m in KIT:
                    KIT.remove(m)
                    KIT.insert(0, m)
                calcola_fili()
                salva_stato()
                return self._send(200, {"ok": True, "kit": KIT})
            if self.path == "/api/telai":                          # [[larg, alt], ...] in mm
                nuovi = [[float(a), float(b)] for a, b in body.get("telai", []) if 5 <= float(a) <= 600 and 5 <= float(b) <= 600]
                if nuovi:
                    TELAI[:] = sorted(([min(t), max(t)] for t in nuovi), key=lambda t: t[0] * t[1])
                    salva_stato()
                return self._send(200, {"telai": TELAI, "conta_telai": conta_telai()})
            if self.path == "/api/raccolta":                        # crea / aggiungi / togli / elimina / rinomina
                az, nome = body.get("azione"), (body.get("nome") or "").strip()[:60]
                if not nome:
                    raise ValueError("nome mancante")
                percorsi = [DATI[int(x)][0] for x in body.get("ids", [])]
                if az in ("crea", "aggiungi"):
                    l = RACCOLTE.setdefault(nome, [])
                    l += [x for x in percorsi if x not in l]
                elif az == "togli":
                    RACCOLTE[nome] = [x for x in RACCOLTE.get(nome, []) if x not in percorsi]
                elif az == "elimina":
                    RACCOLTE.pop(nome, None)
                elif az == "rinomina":
                    nuovo = (body.get("nuovo") or "").strip()[:60]
                    if nuovo and nome in RACCOLTE:
                        RACCOLTE[nuovo] = RACCOLTE.pop(nome)
                salva_stato()
                return self._send(200, {"ok": True})
            if self.path == "/api/diario":                          # nuova voce: data, su cosa, note, foto (data URL)
                i = int(body["i"])
                voce_d = {"id": f"{int(time.time() * 1000)}", "percorso": DATI[i][0],
                          "data": (body.get("data") or time.strftime("%Y-%m-%d"))[:10],
                          "su": (body.get("su") or "")[:120], "note": (body.get("note") or "")[:2000], "foto": None}
                foto = body.get("foto") or ""
                m = re.match(r"data:image/(jpeg|jpg|png|webp);base64,(.+)", foto, re.S)
                if m:
                    os.makedirs(DIARIO_DIR, exist_ok=True)
                    from PIL import Image
                    im = Image.open(io.BytesIO(base64.b64decode(m.group(2)))).convert("RGB")
                    im.thumbnail((1600, 1600))
                    nome_f = f"{voce_d['id']}.jpg"
                    im.save(os.path.join(DIARIO_DIR, nome_f), "JPEG", quality=85)
                    voce_d["foto"] = nome_f
                DIARIO.append(voce_d)
                SET["ric"].add(DATI[i][0])                          # nel diario = già ricamato
                salva_stato()
                return self._send(200, voce_d)
            if self.path == "/api/diario/elimina":
                DIARIO[:] = [e for e in DIARIO if e["id"] != body.get("id")]
                salva_stato()
                return self._send(200, {"ok": True})
            return self._send(404, {"errore": "non trovato"})
        except Exception as e:
            return self._send(400, {"errore": str(e)})


if __name__ == "__main__":
    POOL = _nuovo_pool()
    prepara_colori()
    calcola_fili()
    try:                                                    # modello di traduzione per «Cerca disegni gratis»
        import cerca_svg
        cerca_svg.precarica()
    except Exception as e:
        print("traduzione non disponibile:", e)
    for _prova_porta in range(40):                          # dopo un riavvio la porta può essere ancora occupata
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", PORTA), H)
            break
        except OSError:
            time.sleep(0.5)
    else:
        sys.exit(f"porta {PORTA} occupata: OpenHoop è già aperto?")
    srv.request_queue_size = 256
    url = f"http://127.0.0.1:{PORTA}/"
    threading.Thread(target=_sorveglia_emb, daemon=True).start()
    if CFG.get("aggiornamenti", True) and not os.environ.get("OPENHOOP_NO_AGG"):
        def _agg_avvio():
            time.sleep(20)                                  # dopo l'avvio, senza rallentarlo
            try:
                agg_controlla()
            except Exception as e:
                print("controllo aggiornamenti non riuscito:", e, flush=True)
        threading.Thread(target=_agg_avvio, daemon=True).start()
    print(f"OpenHoop: {url}  ({len(DATI)} disegni, CLIP {'sì' if EMB is not None else 'no'}"
          f"{', primo avvio' if PRIMO_AVVIO else ''})", flush=True)
    if not PRIMO_AVVIO:
        threading.Thread(target=carica_tolti, daemon=True).start()
        imp_riprendi()                                      # importazione interrotta da un riavvio: riparte
    if clip_da_fare():                                      # ricerca per significato: si prepara da sola
        try:
            avvia_lavoro("clip")
        except Exception as e:
            print("ricerca per significato non preparata:", e)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    threading.Thread(target=_aggiorna_dest, daemon=True).start()     # dischi pronti prima che servano
    srv.serve_forever()
