"""Disegno 'realistico' di un ricamo: filo spesso con ombra e riflesso, su tela, con antialiasing.

render(pat, size) -> PIL.Image   (usato da rigenera_miniature.py e dal server per l'anteprima grande)
"""
import math
import random

from PIL import Image, ImageDraw, ImageFilter

FILO_MM = 0.42                     # spessore del filo da ricamo (40 wt) in mm


def _tela(size, rnd):
    """Sfondo tipo lino chiaro: rumore leggero + trama orizzontale/verticale."""
    base = Image.new("RGB", (size, size), (246, 242, 234))
    d = ImageDraw.Draw(base)
    passo = max(2, size // 160)
    for y in range(0, size, passo):
        c = 236 + rnd.randint(-4, 4)
        d.line([(0, y), (size, y)], fill=(c, c - 3, c - 10))
    for x in range(0, size, passo * 2):
        c = 240 + rnd.randint(-4, 4)
        d.line([(x, 0), (x, size)], fill=(c, c - 3, c - 9))
    return base.filter(ImageFilter.GaussianBlur(0.6))


def _schiarisci(rgb, k):
    return tuple(min(255, int(c + (255 - c) * k)) for c in rgb)


def _scurisci(rgb, k):
    return tuple(max(0, int(c * (1 - k))) for c in rgb)


def render(pat, size=320, ss=3, sfondo=True):
    """pat = pyembroidery.EmbPattern. ss = supersampling (disegna ss volte più grande poi riduce)."""
    x0, y0, x1, y1 = (float(v) for v in pat.bounds())          # anche se i punti sono numeri numpy
    S = size * ss
    margine = 0.06 * S
    k = (S - 2 * margine) / max(x1 - x0, y1 - y0, 1)          # pixel per 0.1 mm
    ox = margine + ((S - 2 * margine) - (x1 - x0) * k) / 2
    oy = margine + ((S - 2 * margine) - (y1 - y0) * k) / 2
    spess = max(ss, FILO_MM * 10 * k)                           # spessore filo in pixel
    rnd = random.Random(7)
    # sfondo=True tela, False bianco, None trasparente (per metterlo dentro il telaio disegnato dalla pagina)
    if sfondo:
        img = _tela(S, rnd).convert("RGBA")
    else:
        img = Image.new("RGBA", (S, S), (255, 255, 255, 255 if sfondo is False else 0))
    ombra = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dom = ImageDraw.Draw(ombra)
    filo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dfi = ImageDraw.Draw(filo)
    off = max(1, spess * 0.35)
    for blocco, th in pat.get_as_colorblocks():
        if th is not None and th.color is not None:
            c = th.color & 0xFFFFFF
            rgb = ((c >> 16) & 255, (c >> 8) & 255, c & 255)
        else:
            rgb = (60, 60, 60)
        scuro, chiaro = _scurisci(rgb, 0.28), _schiarisci(rgb, 0.45)
        prev = None
        for st in blocco:
            cmd = st[2] & 0xFF
            p = (ox + (st[0] - x0) * k, oy + (st[1] - y0) * k)
            if cmd == 0 and prev is not None:
                lung = math.dist(prev, p)
                if lung > 0.2:
                    dom.line([(prev[0] + off, prev[1] + off), (p[0] + off, p[1] + off)], fill=(0, 0, 0, 70), width=int(spess))
                    dfi.line([prev, p], fill=scuro + (255,), width=int(spess))              # bordo del filo
                    dfi.line([prev, p], fill=rgb + (255,), width=max(1, int(spess * 0.7)))  # corpo
                    # riflesso lucido spostato verso l'alto-sinistra
                    hx, hy = -spess * 0.12, -spess * 0.12
                    dfi.line([(prev[0] + hx, prev[1] + hy), (p[0] + hx, p[1] + hy)],
                             fill=chiaro + (200,), width=max(1, int(spess * 0.22)))
            prev = p if cmd in (0, 1) else None
    ombra = ombra.filter(ImageFilter.GaussianBlur(max(1, spess * 0.5)))
    img.alpha_composite(ombra)
    img.alpha_composite(filo)
    img = img.resize((size, size), Image.LANCZOS)
    return img if sfondo is None else img.convert("RGB")


MARGINE = 0.06          # il disegno occupa (1 - 2*MARGINE) del lato: serve alla pagina per la scala nel telaio
