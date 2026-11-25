"""Impronta SigLIP 2 di ogni miniatura del catalogo (ricerca 'alla Immich', in italiano).
Scelto dopo il confronto del 22/11/2026 (04_anteprima/confronto_*.png): molto meglio del CLIP B-32.

Uso: .venv\\Scripts\\python.exe indicizza_clip.py [cartella_Ricami_ordinati]
Riprende da dove era arrivato: salva ogni ~5000 immagini in _catalogo/clip.npy (+ clip_n.txt, clip_modello.txt).
"""
import json
import os
import sys
import time

import numpy as np
import torch
from PIL import Image

OUT = sys.argv[1] if len(sys.argv) > 1 else sys.exit("uso: indica la cartella dell'archivio")
CAT = os.path.join(OUT, "_catalogo")
MODELLO = "google/siglip2-so400m-patch16-384"


def vett(o):
    return o if torch.is_tensor(o) else (o.pooler_output if getattr(o, "pooler_output", None) is not None else o[0])


def main():
    from transformers import AutoModel, AutoProcessor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print("dispositivo:", torch.cuda.get_device_name(0) if dev == "cuda" else "CPU", flush=True)
    with open(os.path.join(CAT, "dati.js"), encoding="utf-8") as fh:
        n_tot = len(json.loads(fh.read().split("const DATI=", 1)[1].rstrip().rstrip(";")))
    m = AutoModel.from_pretrained(MODELLO, torch_dtype=torch.float16 if dev == "cuda" else torch.float32).to(dev).eval()
    p = AutoProcessor.from_pretrained(MODELLO)
    f_npy, f_n, f_mod = (os.path.join(CAT, x) for x in ("clip.npy", "clip_n.txt", "clip_modello.txt"))
    stesso = os.path.exists(f_mod) and open(f_mod).read().strip() == MODELLO
    fatti = int(open(f_n).read()) if stesso and os.path.exists(f_n) and os.path.exists(f_npy) else 0
    emb = np.load(f_npy) if fatti else None
    t0, B = time.time(), (128 if dev == "cuda" else 16)
    for i in range(fatti, n_tot, B):
        imgs = [Image.open(os.path.join(CAT, "img", f"{j}.webp")).convert("RGB") for j in range(i, min(i + B, n_tot))]
        x = p(images=imgs, return_tensors="pt")
        with torch.no_grad():
            v = vett(m.get_image_features(pixel_values=x["pixel_values"].to(dev, m.dtype))).float().cpu().numpy()
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        if emb is None:
            emb = np.zeros((n_tot, v.shape[1]), dtype=np.float16)
        elif emb.shape[0] < n_tot:
            emb = np.vstack([emb, np.zeros((n_tot - emb.shape[0], emb.shape[1]), dtype=np.float16)])
        emb[i:i + len(imgs)] = v.astype(np.float16)
        if (i // B) % max(1, 5000 // B) == 0 or i + B >= n_tot:
            np.save(f_npy, emb)
            with open(f_n, "w") as fh:
                fh.write(str(i + len(imgs)))
            with open(f_mod, "w") as fh:
                fh.write(MODELLO)
            ora = i + len(imgs)
            vel = (ora - fatti) / max(1, time.time() - t0)
            print(f"{ora}/{n_tot}  {vel:.0f} img/s  resto ~{(n_tot - ora) / max(vel, 1) / 60:.0f} min", flush=True)
    print("FINE", flush=True)


if __name__ == "__main__":
    main()
