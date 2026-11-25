"""PASSO 20 — «Aiuto AI» del digitalizzatore (spento di base: lo attiva l'utente quando il metodo classico non basta).

DeepSeek vision non dà coordinate: gli si manda l'immagine con le zone NUMERATE (solo le zone piene, non le linee:
il numero sulle ciglia faceva sbagliare il contorno) e risponde con un JSON {numero: etichetta}.
Le etichette tornano come punti [{"x", "y", "etichetta"}] in frazioni dell'immagine (il punto più interno della
zona): così restano valide quando la bozza si ricalcola con altre opzioni (vedi digitalizza.applica_semantica).

La chiave sta in <progetto>/.env come DEEPSEEK_API_KEY=... (.env è in .gitignore, mai in git).
"""
import base64
import io
import json
import os
import urllib.error
import urllib.request

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

PROGETTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://api.deepseek.com/chat/completions"
MODELLO = "deepseek-flash"
ETICHETTE = ["pelle_viso", "pelle_corpo", "capelli", "occhio_bianco", "iride", "pupilla", "riflesso_occhio",
             "sopracciglio", "bocca", "denti", "guancia", "vestito", "decorazione", "scritta",
             "sfondo_da_non_ricamare", "altro"]
NOMI_IT = {"pelle_viso": "viso", "pelle_corpo": "pelle", "capelli": "capelli", "occhio_bianco": "bianco occhio",
           "iride": "iride", "pupilla": "pupilla", "riflesso_occhio": "riflesso", "sopracciglio": "sopracciglio",
           "bocca": "bocca", "denti": "denti", "guancia": "guancia", "vestito": "vestito", "decorazione": "decorazione",
           "scritta": "scritta", "sfondo_da_non_ricamare": "sfondo (non ricamare)", "altro": "altro"}


def chiave():
    try:                                                # OpenHoop: chiave scritta dall'utente nelle Impostazioni
        import config
        k = config.leggi().get("chiave_deepseek")
        if k:
            return k
    except Exception:
        pass
    try:
        for riga in open(os.path.join(PROGETTO, ".env"), encoding="utf-8"):
            if riga.strip().startswith("DEEPSEEK_API_KEY="):
                k = riga.split("=", 1)[1].strip().strip('"').strip("'")
                if k:
                    return k
    except OSError:
        pass
    return os.environ.get("DEEPSEEK_API_KEY") or None


def _punto_interno(m):
    dt = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1]
    y, x = np.unravel_index(dt.argmax(), dt.shape)
    return int(y), int(x)


def tavola(A, lato=1280, min_mm2=1.0):
    """Immagine originale con bordi delle zone e un numero (1..N) sul punto più interno di ogni zona piena."""
    rgb, z = A["rgb"], A["zone"]
    H, W = z.shape
    k = lato / max(H, W)
    im = Image.fromarray(rgb).resize((int(W * k), int(H * k)), Image.LANCZOS).convert("RGB")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    bordo = ((z != np.roll(z, 1, 0)) | (z != np.roll(z, 1, 1))).astype(np.uint8) * 90
    im.paste((255, 0, 255), mask=Image.fromarray(bordo).resize(im.size, Image.NEAREST))
    zone = []                                            # (numero, id zona, y, x) in pixel dell'analisi
    for o in sorted(A["info"], key=lambda o: -o["area_mm2"]):
        if o["area_mm2"] < min_mm2 or o["punto"] in ("satin", "filza3"):
            continue
        y, x = _punto_interno(z == o["id"])
        n = len(zone) + 1
        zone.append((n, o["id"], y, x))
        t, X, Y = str(n), x * k, y * k
        d.rectangle([X - 9, Y - 8, X - 9 + 9 * len(t) + 8, Y + 9], fill=(255, 255, 255), outline=(0, 0, 0))
        d.text((X - 6, Y - 8), t, fill=(0, 0, 0), font=font)
    return im, zone


def chiedi(im, numeri, timeout=240):
    k = chiave()
    if not k:
        raise ValueError("manca la chiave DeepSeek: aggiungila in Impostazioni → Servizi facoltativi")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    testo = ("Questa è un'immagine da trasformare in ricamo. Ogni zona colorata ha un numero in un riquadro bianco "
             "(i bordi viola separano le zone). "
             f"Numeri presenti: {numeri}. Per OGNI numero dimmi cosa rappresenta, scegliendo tra: {ETICHETTE}. "
             "Usa sfondo_da_non_ricamare solo per zone che chiaramente NON fanno parte del soggetto (cielo, "
             "riquadro, fondo chiuso tra braccio e corpo). "
             "Rispondi SOLO con un oggetto JSON {\"numero\": \"etichetta\"}.")
    corpo = {"model": MODELLO, "messages": [{"role": "user", "content": [
        {"type": "text", "text": testo},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}}]}],
        "response_format": {"type": "json_object"}, "temperature": 0, "reasoning_effort": "low"}
    req = urllib.request.Request(URL, data=json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + k})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read()[:300].decode("utf-8", "replace")
        raise ValueError(f"DeepSeek ha risposto {e.code}: {msg}") from None
    except urllib.error.URLError as e:
        raise ValueError(f"DeepSeek non raggiungibile: {e.reason}") from None
    return json.loads(out["choices"][0]["message"]["content"]), out.get("usage") or {}


def etichetta(A):
    """-> (semantica [{"x","y","etichetta"}], uso dei token). Una chiamata (circa un minuto)."""
    im, zone = tavola(A)
    if not zone:
        return [], {}
    ris, uso = chiedi(im, [n for n, *_ in zone])
    H, W = A["zone"].shape
    sem = []
    for n, _, y, x in zone:
        e = ris.get(str(n))
        if e in ETICHETTE and e != "altro":
            rit = A.get("ritaglio", (0.0, 0.0, 1.0, 1.0))      # frazioni dell'immagine ORIGINALE
            sem.append({"x": round(rit[0] + (x + 0.5) / W * rit[2], 4), "y": round(rit[1] + (y + 0.5) / H * rit[3], 4),
                        "etichetta": e})
    return sem, uso
