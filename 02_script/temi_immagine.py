"""OpenHoop: i disegni rimasti in «VARIE (da smistare)» (il nome non diceva il soggetto) vanno nel tema giusto
guardando l'IMMAGINE, con SigLIP 2 "zero-shot": impronta della miniatura (clip.npy, già calcolata) contro la
descrizione di ogni tema. Conta anche la raccolta: i disegni della stessa cartella di solito hanno lo stesso
soggetto. Si sposta solo se è abbastanza sicuro; i file si spostano davvero (l'archivio è di OpenHoop) e dati.js
si aggiorna. Gira dopo indicizza_clip.py, prima del riavvio del server.

uso: python temi_immagine.py <archivio>
"""
import json
import os
import re
import sys
from collections import defaultdict

import numpy as np

QUI = os.path.dirname(os.path.abspath(__file__))
MODELLO = "google/siglip2-so400m-patch16-384"
# tema (nome italiano, come in riordina.TEMI) -> cosa raffigura, in inglese (SigLIP capisce meglio)
DESCR = {
    "ANIMALI": "animals, pets, birds, wild animals, cute animal",
    "FIORI & PIANTE": "flowers, roses, leaves, plants, trees, floral",
    "CIBO": "food, fruit, cake, sweets, coffee cup, kitchen",
    "CORNICI & GRECHE": "decorative frame, border, ornament, corner flourish",
    "LETTERE-NUMERI": "alphabet letter, monogram, number",
    "FESTE": "christmas, easter, halloween, birthday, holiday celebration",
    "DISNEY-CARTONI": "cartoon character",
    "VEICOLI": "car, truck, train, airplane, boat, vehicle",
    "MESTIERI": "profession, nurse, doctor, firefighter, tools",
    "SCUOLA": "school, pencil, book, graduation",
    "MUSICALI": "music, musical notes, guitar, instrument",
    "DANZA": "dance, ballet, ballerina",
    "HOBBY": "sport, ball, fishing, hobby",
    "RELIGIONE": "religious, cross, angel, church",
    "METEO": "sun, moon, stars, clouds, rain, snowflake",
    "VESTITI": "clothes, dress, shoes, hat, fashion",
    "FIOCCHI": "ribbon bow",
    "LUOGHI": "house, landscape, city, lighthouse, beach",
    "FANTASIA": "fairy, mermaid, unicorn, castle, magic fantasy",
    "HORROR": "skull, horror, gothic",
    "QUADRI": "realistic picture, scene, portrait",
    "FRASI": "text, words, quote, lettering phrase",
    "PARTI DEL CORPO": "hand, foot, lips, eye",
}
SOGLIA, MARGINE = 0.42, 0.12          # probabilità minima del primo tema e distacco dal secondo


def proposte(out, DATI, nome=None):
    """[(indice, tema)] per i disegni di VARIE abbastanza sicuri (non sposta niente). nome: tema -> nome cartella."""
    cat = os.path.join(out, "_catalogo")
    n = int(open(os.path.join(cat, "clip_n.txt")).read())
    E = np.load(os.path.join(cat, "clip.npy"))[:n].astype(np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-8
    if nome is None:
        try:                                          # nomi dei temi nella lingua dell'utente (come riordina)
            import config
            lingua = config.leggi().get("lingua", "it")
            diz = json.load(open(os.path.join(QUI, "lingue", lingua + ".json"), encoding="utf-8")) if lingua != "it" else {}
        except Exception:
            diz = {}
        nome = lambda s: re.sub(r'[<>:"/\\|?*]', "_", diz.get(s, s))  # noqa: E731
    varie = nome("VARIE (da smistare)")
    da_fare = [i for i in range(min(n, len(DATI))) if DATI[i][1] == varie]
    if not da_fare:
        return []
    import torch
    from transformers import AutoModel, AutoProcessor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    m = AutoModel.from_pretrained(MODELLO, torch_dtype=torch.float16 if dev == "cuda" else torch.float32)
    m = m.to(dev, dtype=torch.float16 if dev == "cuda" else torch.float32).eval()
    p = AutoProcessor.from_pretrained(MODELLO)
    temi = list(DESCR)
    T = []
    for k in temi:
        frasi = [f"an embroidery design of {d.strip()}" for d in DESCR[k].split(",")]
        x = p(text=frasi, padding="max_length", max_length=64, return_tensors="pt").to(dev)
        with torch.no_grad():
            o = m.get_text_features(**x)
        o = o if torch.is_tensor(o) else (o.pooler_output if getattr(o, "pooler_output", None) is not None else o[0])
        v = o.float().cpu().numpy()
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        T.append(v.mean(0) / np.linalg.norm(v.mean(0)))
    T = np.stack(T)

    def prob(v):
        z = 100 * (T @ v)
        z = np.exp(z - z.max())
        return z / z.sum()

    gruppi = defaultdict(list)                        # stessa cartella = stessa raccolta
    for i in da_fare:
        gruppi[os.path.dirname(DATI[i][0])].append(i)
    out_ = []
    for cart, idx in gruppi.items():
        media = E[idx].mean(0)
        pc = prob(media / (np.linalg.norm(media) + 1e-8))
        for i in idx:
            pr = 0.5 * prob(E[i]) + 0.5 * pc
            o = np.argsort(-pr)
            if pr[o[0]] >= SOGLIA and pr[o[0]] - pr[o[1]] >= MARGINE:
                out_.append((i, nome(temi[o[0]])))
    return out_


def main():
    """Dopo l'indicizzazione: sposta i VARIE riconosciuti e aggiorna tutti i riferimenti (sistema_libreria)."""
    import sistema_libreria as SL
    out = sys.argv[1]
    L = SL.Libreria(out)
    prop = proposte(out, L.dati)
    for i, tema in prop:
        vecchio = L.dati[i][0]
        parti = vecchio.replace("/", "\\").split("\\")
        L.sposta(i, os.path.join(tema, *parti[1:]), tema=tema)
    L.salva()
    print(f"FINE: {len(prop)} disegni di VARIE messi in un tema guardando l'immagine")


if __name__ == "__main__":
    main()
