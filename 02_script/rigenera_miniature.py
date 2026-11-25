"""Miniature 'belle' (filo realistico, stile fancy di pyembroidery) per il catalogo.

Scrive _catalogo/img_hd/<n>.webp (240 px). Le miniature semplici in _catalogo/img restano per SigLIP.
Riprende da dove era rimasto (salta i file già fatti). Gira a bassa priorità.

Uso: python rigenera_miniature.py [cartella_Ricami_ordinati]
"""
import io
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

OUT = sys.argv[1] if len(sys.argv) > 1 else ""      # importato dal server: OUT non serve (le funzioni ricevono la cartella)
CAT = os.path.join(OUT, "_catalogo")
HD = os.path.join(CAT, "img_hd")
LATO_RENDER, LATO_MINI = 512, 240


def fancy(pat, lato):
    """Disegno realistico: scala il ricamo a `lato` pixel e usa il render 'fancy' di pyembroidery."""
    import pyembroidery
    from PIL import Image
    x0, y0, x1, y1 = pat.bounds()
    s = lato / max(x1 - x0, y1 - y0, 1)
    p = pyembroidery.EmbPattern()
    p.threadlist = pat.threadlist
    p.stitches = [[st[0] * s, st[1] * s, st[2]] for st in pat.stitches]
    b = io.BytesIO()
    pyembroidery.write_png(p, b, {"fancy": True})
    im = Image.open(b).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    return bg.convert("RGB")


def lavora(args):
    """args = (n, percorso relativo del PES, cartella Ricami_ordinati). Usata anche dal server, al volo."""
    i, rel, out = args
    hd = os.path.join(out, "_catalogo", "img_hd")
    dest = os.path.join(hd, f"{i}.webp")
    if os.path.exists(dest):
        return 0
    try:
        import pyembroidery
        from PIL import Image
        os.makedirs(hd, exist_ok=True)
        im = fancy(pyembroidery.read(os.path.join(out, rel)), LATO_RENDER)
        im.thumbnail((LATO_MINI, LATO_MINI), Image.LANCZOS)
        quad = Image.new("RGB", (LATO_MINI, LATO_MINI), "white")
        quad.paste(im, ((LATO_MINI - im.width) // 2, (LATO_MINI - im.height) // 2))
        tmp = f"{dest}.{os.getpid()}.tmp"
        quad.save(tmp, "WEBP", quality=78)
        os.replace(tmp, dest)
        return 1
    except Exception:
        return -1


def _bassa_priorita():
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
    except Exception:
        pass


def main():
    with open(os.path.join(CAT, "dati.js"), encoding="utf-8") as fh:
        dati = json.loads(fh.read().split("const DATI=", 1)[1].rstrip().rstrip(";"))
    os.makedirs(HD, exist_ok=True)
    _bassa_priorita()
    t0, fatti, err = time.time(), 0, 0
    lavori = [(i, d[0], OUT) for i, d in enumerate(dati)]
    with ProcessPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 2), initializer=_bassa_priorita) as ex:
        for n, r in enumerate(ex.map(lavora, lavori, chunksize=32)):
            fatti += r == 1
            err += r == -1
            if n % 5000 == 0:
                vel = fatti / max(1, time.time() - t0)
                print(f"{n}/{len(dati)}  nuove {fatti}  errori {err}  {vel:.1f}/s  resto ~{(len(dati) - n) / max(vel, 0.1) / 3600:.1f} h", flush=True)
    print(f"FINE: {fatti} miniature nuove, {err} errori, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    if not OUT:
        sys.exit("uso: python rigenera_miniature.py <cartella dell'archivio>")
    main()
