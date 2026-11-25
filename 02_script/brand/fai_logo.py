"""Dal logo sorgente (sorgente.png, fondo crema) tutte le versioni di OpenHoop:
icona trasparente, .ico per Windows, favicon, avatar rotondo (Ko-fi, social), logo largo col nome, immagine social.
Uso: python fai_logo.py   (scrive nella stessa cartella)"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

QUI = os.path.dirname(os.path.abspath(__file__))
CREMA, VERDE, CORALLO = (244, 239, 230), (41, 102, 71), (250, 92, 92)
FONT = next((f for f in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\bahnschrift.ttf") if os.path.exists(f)), None)


def trasparente(im):
    """Fondo crema -> trasparente (alfa morbido sui bordi), ritaglio quadrato attorno al disegno."""
    a = np.asarray(im.convert("RGB")).astype(float)
    fondo = np.median(np.concatenate([a[:8, :8].reshape(-1, 3), a[-8:, -8:].reshape(-1, 3)]), axis=0)
    d = np.sqrt(((a - fondo) ** 2).sum(2))
    alfa = np.clip((d - 18) / 50, 0, 1)
    # colore "senza fondo": toglie la parte di crema mescolata nei pixel di bordo
    al = np.maximum(alfa, 1e-3)[..., None]
    rgb = np.clip((a - fondo * (1 - al)) / al, 0, 255)
    out = Image.fromarray(np.dstack([rgb, alfa * 255]).astype(np.uint8), "RGBA")
    x0, y0, x1, y1 = out.getbbox()
    lato = int(max(x1 - x0, y1 - y0) * 1.06)
    q = Image.new("RGBA", (lato, lato), (0, 0, 0, 0))
    q.paste(out.crop((x0, y0, x1, y1)), ((lato - (x1 - x0)) // 2, (lato - (y1 - y0)) // 2))
    return q


def su_fondo(ic, lato, fondo=CREMA, scala=0.86, tondo=False):
    im = Image.new("RGBA", (lato, lato), (0, 0, 0, 0) if tondo else fondo + (255,))
    if tondo:
        ImageDraw.Draw(im).ellipse((0, 0, lato - 1, lato - 1), fill=fondo + (255,))
    s = int(lato * scala)
    im.alpha_composite(ic.resize((s, s), Image.LANCZOS), ((lato - s) // 2, (lato - s) // 2))
    return im


def logo_largo(ic, alt=400, fondo=None):
    f = ImageFont.truetype(FONT, int(alt * 0.52))
    w_open, w_hoop = f.getlength("Open"), f.getlength("Hoop")
    gap = int(alt * 0.12)
    W = int(alt + gap + w_open + w_hoop + alt * 0.1)
    im = Image.new("RGBA", (W, alt), (fondo + (255,)) if fondo else (0, 0, 0, 0))
    im.alpha_composite(ic.resize((alt, alt), Image.LANCZOS), (0, 0))
    d = ImageDraw.Draw(im)
    y = alt // 2
    d.text((alt + gap, y), "Open", font=f, fill=VERDE, anchor="lm")
    d.text((alt + gap + w_open, y), "Hoop", font=f, fill=CORALLO, anchor="lm")
    return im


def main():
    ic = trasparente(Image.open(os.path.join(QUI, "sorgente.png")))
    ic.resize((1024, 1024), Image.LANCZOS).save(os.path.join(QUI, "icona_1024.png"))
    ic.resize((512, 512), Image.LANCZOS).save(os.path.join(QUI, "icona_512.png"))
    # .ico: fondo crema arrotondato, così si vede anche sulla barra scura di Windows
    base = su_fondo(ic, 256, scala=0.9)
    m = Image.new("L", (256, 256), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, 255, 255), 48, fill=255)
    base.putalpha(m)
    base.save(os.path.join(QUI, "openhoop.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    base.resize((64, 64), Image.LANCZOS).save(os.path.join(QUI, "favicon.png"))
    base.resize((180, 180), Image.LANCZOS).save(os.path.join(QUI, "apple-touch-icon.png"))
    su_fondo(ic, 800, scala=0.78, tondo=True).save(os.path.join(QUI, "avatar_rotondo.png"))
    su_fondo(ic, 1080, scala=0.8).convert("RGB").save(os.path.join(QUI, "social_quadrato.png"))
    logo_largo(ic).save(os.path.join(QUI, "logo_largo.png"))
    logo_largo(ic, fondo=CREMA).convert("RGB").save(os.path.join(QUI, "logo_largo_crema.png"))
    # copertina (Ko-fi, GitHub social preview 1280x640)
    cop = Image.new("RGBA", (1280, 640), CREMA + (255,))
    lg = logo_largo(ic, alt=260)
    cop.alpha_composite(lg, ((1280 - lg.width) // 2, 150))
    f = ImageFont.truetype(FONT, 40)
    ImageDraw.Draw(cop).text((640, 500), "Free embroidery design organizer", font=f, fill=VERDE, anchor="mm")
    cop.convert("RGB").save(os.path.join(QUI, "copertina.png"))
    # copertina larga (Ko-fi, social: 3:1)
    k = Image.new("RGBA", (1500, 500), CREMA + (255,))
    lg = logo_largo(ic, alt=200)
    k.alpha_composite(lg, ((1500 - lg.width) // 2, 95))
    f2 = ImageFont.truetype(FONT, 34)
    ImageDraw.Draw(k).text((750, 375), "Free, open-source embroidery design organizer", font=f2, fill=VERDE, anchor="mm")
    k.convert("RGB").save(os.path.join(QUI, "copertina_larga.png"))
    print("fatto:", sorted(f for f in os.listdir(QUI) if f != "fai_logo.py"))


if __name__ == "__main__":
    main()
