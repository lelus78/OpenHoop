"""Fili da ricamo: tabelle colori per marca (da Ink/Stitch, 01_riferimenti/palette_inkstitch/*.gpl)
e abbinamento del filo più vicino (CIE Lab, come lo vede l'occhio).

Predefinita: "New Brothread 80 Colors" (Brothread 80, poliestere, numeri Janome) -> MARCA_PREDEFINITA.
"""
import glob
import os
import re

import numpy as np

QUI = os.path.dirname(os.path.abspath(__file__))
CARTELLA = os.path.join(os.path.dirname(QUI), "01_riferimenti", "palette_inkstitch")
MARCA_PREDEFINITA = "Brothread 80"

# nomi italiani dei colori più comuni (il resto resta come sul rocchetto)
IT = {"Black": "Nero", "White": "Bianco", "Red": "Rosso", "Pink": "Rosa", "Blue": "Blu", "Yellow": "Giallo",
      "Orange": "Arancione", "Brown": "Marrone", "Light Blue": "Azzurro", "Sky Blue": "Celeste", "Silver": "Argento",
      "Gray": "Grigio", "Grey": "Grigio", "Purple": "Viola", "Violet": "Violetto", "Lemon Yellow": "Giallo limone",
      "Dark Gray": "Grigio scuro", "Dark Grey": "Grigio scuro", "Light Gray": "Grigio chiaro", "Cream Brown": "Marrone crema",
      "Leaf Green": "Verde foglia", "Emerald Green": "Verde smeraldo", "Lime Green": "Verde lime", "Moss Green": "Verde muschio",
      "Dark Brown": "Marrone scuro", "Light Brown": "Marrone chiaro", "Salmon Pink": "Rosa salmone", "Deep Gold": "Oro scuro",
      "Gold": "Oro", "Beige": "Beige", "Flesh Pink": "Rosa carne", "Carmine": "Carminio", "Lilac": "Lilla",
      "Light Lilac": "Lilla chiaro", "Magenta": "Magenta", "Teal Green": "Verde acqua", "Navy Blue": "Blu marina",
      "Royal Purple": "Viola reale", "Pumpkin": "Zucca", "Vermillion": "Vermiglio", "Vermilion": "Vermiglio",
      "Clay Brown": "Marrone argilla", "Khaki": "Kaki", "Linen": "Lino", "Pewter": "Peltro", "Green": "Verde",
      "Dark Green": "Verde scuro", "Light Green": "Verde chiaro", "Canary Yellow": "Giallo canarino",
      "Tangerine": "Mandarino", "Honeydew": "Melata", "Cream": "Crema", "Ivory": "Avorio", "Wine Red": "Rosso vino",
      "Burgundy": "Bordeaux", "Turquoise": "Turchese", "Aqua": "Acqua", "Peach": "Pesca", "Coral": "Corallo",
      "Lavender": "Lavanda", "Mauve": "Malva", "Rust": "Ruggine", "Tan": "Cuoio", "Olive Green": "Verde oliva"}

try:                                                    # OpenHoop: nomi italiani solo con l'interfaccia in italiano
    import config as _config
    ITALIANO = _config.leggi().get("lingua", "it") == "it"
except Exception:
    ITALIANO = True


def marche():
    return sorted(os.path.splitext(os.path.basename(f))[0] for f in glob.glob(os.path.join(CARTELLA, "*.gpl")))


_CACHE = {}


def tabella(marca=MARCA_PREDEFINITA):
    """-> (lista fili [{num, nome, en, hex}], array Lab)."""
    if marca not in _CACHE:
        fili, visti = [], set()
        for riga in open(os.path.join(CARTELLA, marca + ".gpl"), encoding="utf-8", errors="replace"):
            m = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*\S)\s+(\S+)\s*$", riga)
            if not m:
                continue
            r, g, b, nome, num = int(m[1]), int(m[2]), int(m[3]), m[4].strip(), m[5]
            if marca.startswith("Brothread") and num.isdigit():
                num = "N" + num                                 # come sui rocchetti New Brothread (N001, N201…)
            if num in visti:
                continue
            visti.add(num)
            fili.append({"num": num, "nome": IT.get(nome, nome) if ITALIANO else nome, "en": nome,
                         "hex": f"#{r:02x}{g:02x}{b:02x}"})
        lab = _lab([[int(f["hex"][i:i + 2], 16) for i in (1, 3, 5)] for f in fili])
        _CACHE[marca] = (fili, lab)
    return _CACHE[marca]


def tabella_tutte():
    """Tutti i fili di tutte le marche (per avvicinarsi il più possibile a un colore, che io l'abbia o no):
    -> (lista fili con "marca", array Lab). «num» è «marca num» per non confondere marche diverse."""
    if "*" not in _CACHE:
        fili, labs = [], []
        for m in marche():
            fl, lab = tabella(m)
            fili += [dict(f, marca=m, num=f"{m} {f['num']}", num_marca=f["num"]) for f in fl]
            labs.append(lab)
        _CACHE["*"] = (fili, np.concatenate(labs) if labs else np.zeros((0, 3)))
    return _CACHE["*"]


def _lab(rgb):
    """rgb: array (...,3) 0-255 -> CIE Lab."""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.2126, 0.0193], [0.3576, 0.7152, 0.1192], [0.1805, 0.0722, 0.9505]])
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def piu_vicino(hexes, marca=MARCA_PREDEFINITA):
    """Lista di '#rrggbb' -> lista di numeri del filo più vicino della marca (stessa lunghezza)."""
    if not hexes:
        return []
    fili, lab = tabella(marca)
    rgb = [[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in hexes]
    out = []
    L = _lab(rgb)
    for k in range(0, len(L), 4096):                         # a blocchi per non usare troppa memoria
        d = ((L[k:k + 4096, None, :] - lab[None, :, :]) ** 2).sum(-1)
        out += [fili[j]["num"] for j in d.argmin(1)]
    return out


def lab_hex(hexes):
    return _lab([[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in hexes])


def posseduti(chiavi):
    """chiavi = insieme di 'Marca|num' -> (lista fili [{marca,num,nome,hex}], array Lab) dei fili che ho, di tutti i kit."""
    out = []
    per_marca = {}
    for k in chiavi:
        m, _, n = k.partition("|")
        per_marca.setdefault(m, set()).add(n)
    for m, nums in sorted(per_marca.items()):
        try:
            fl, _ = tabella(m)
        except OSError:
            continue
        out += [dict(f, marca=m) for f in fl if f["num"] in nums]
    lab = lab_hex([f["hex"] for f in out]) if out else np.zeros((0, 3))
    return out, lab


def vicini(lab_colori, lab_fili):
    """Per ogni colore: indice del filo più vicino e distanza ΔE (CIE76). Vuoto -> (-1, inf)."""
    n = len(lab_colori)
    if not len(lab_fili):
        return np.full(n, -1), np.full(n, np.inf)
    idx, de = np.empty(n, dtype=np.int64), np.empty(n)
    for k in range(0, n, 4096):
        d = ((lab_colori[k:k + 4096, None, :] - lab_fili[None, :, :]) ** 2).sum(-1)
        idx[k:k + 4096] = d.argmin(1)
        de[k:k + 4096] = np.sqrt(d.min(1))
    return idx, de


def mappa_tutti(seq_per_disegno, marca=MARCA_PREDEFINITA):
    """Per tutto il catalogo: liste di hex -> liste di numeri filo (calcola solo i colori unici)."""
    unici = sorted({h for s in seq_per_disegno for h in s if isinstance(h, str) and len(h) == 7})
    num = dict(zip(unici, piu_vicino(unici, marca)))
    nero = tabella(marca)[0][0]["num"]
    return [[num.get(h, nero) for h in s] for s in seq_per_disegno]
