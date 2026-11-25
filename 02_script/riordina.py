"""Riordino dei file da ricamo (OpenHoop: cartelle e macchina dalle impostazioni, vedi config.py).

Per ogni disegno: un solo file PES (se esiste solo in altri formati lo converte), niente doppioni
(stessi punti = stesso disegno), smistato per TEMA (le categorie di 01_Ricami_PES) e collezione,
con miniatura e misure per il catalogo HTML. Non cancella e non modifica niente nella sorgente.

Uso:
  python riordina.py prova  [cartella_uscita]   -> campione di poche collezioni
  python riordina.py tutto  [cartella_uscita]   -> tutto
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
import pes_brother  # noqa: F401,E402  -- PEC senza il n. 64 «Applique» (vedi pes_brother.py)
from autori import _RX as AUTORI  # noqa: E402  -- nomi dei disegnatori (non sono il soggetto del disegno)

SRC = os.path.expanduser("~")                  # solo per i modi a riga di comando; OpenHoop usa «config»
SORGENTI = ["01_Ricami_PES", "02_Gia_ricamati", "03_Da_riordinare"]
TELAIO = (130, 180)                      # mm

# formati leggibili da pyembroidery, in ordine di preferenza se manca il PES
FORMATI = [".pes", ".dst", ".jef", ".exp", ".vp3", ".xxx", ".hus", ".vip", ".sew", ".pec", ".pcs", ".shv", ".jpx", ".tbf", ".u01", ".10o", ".100", ".pcm", ".pcq", ".dat"]
PRIO = {e: i for i, e in enumerate(FORMATI)}
NOMI_FORMATO = {e.strip(".") for e in FORMATI} | {"edr", "art", "emb", "jef+", "pes files", "dst files", "jef files", "hus files"}
# cartelle "tecniche" con il nome del formato: PES, JEF1, "DST files", "Formato PES", "All Formats"...
FORMATO_DIR = re.compile(r"^(formato?s?\s+)?(pes|jef\+?|dst|exp|hus|vip|vp3|xxx|sew|pec|pcs|shv|art|edr|emb|all)(\s*\d+|\s*files?|\s*formats?|\s*format)?$", re.I)


def tecnica(nome):
    return nome.lower() in NOMI_FORMATO or bool(FORMATO_DIR.match(nome.strip()))

# ---------------- temi: parola chiave (minuscolo, it/en/pt/es) -> categoria di 01_Ricami_PES ----------------
TEMI = {
    "ANIMALI": "animal animali animais animales cat cats gatt gato dog dogs cane cani cachorro perro horse cavall cavalo caballo bird birds uccell passar pajaro owl gufo gufi coruja buho fish pesc peixe pez bear orso urso oso teddy rabbit bunny conigl coelho conejo duck anatra pato farm fattoria fazenda granja safari wild lion leone leao elephant elefant monkey scimm macaco mono frog rana sapo butterfl farfall borboleta mariposa insect bug ape abelha bee cow mucca vaca pig maiale porco cerdo sheep pecora ovelha oveja dragon drago dragão dragones dino flamingo fenicott penguin pinguin turtle tartaruga tortuga snake serpent mouse topo rato raton deer cervo veado reindeer renna giraffe zebra tiger tigre fox volpe raposa zorro wolf lupo lobo squirrel scoiattol unicorn unicorno aquatic marine sea creature kitty puppy pony koala kangaroo canguro joey sloth bradipo panda llama alpaca hedgehog riccio hippo ippopotam rhino rinoceront camel cammell octopus polpo whale balena dolphin delfin shark squal crab granchi ladybug coccinell chick pulcin hen gallina rooster gallo goat capra donkey asin woodland critter critters raccoon procione otter lontra seal foca walrus beaver castoro moose alce hamster bunnies kitten kittens",
    "FIORI & PIANTE": "flower flowers fiore fiori flor flores floral rose rosa roses tulip tulipan daisy margherit leaf leaves foglie folha hoja tree trees albero arvore arbol garden giardin jardim jardin plant pianta planta herb erbe ivy cactus botanic bouquet wreath ghirland lily giglio orchid orchide sunflower girasol poppy papaver vine vite",
    "CIBO": "food cibo comida fruit frutta fruta vegetable verdur legume cake torta bolo pastel cupcake coffee caffe cafe tea tè cha teiera cucina kitchen cozinha cooking cuoco chef wine vino vinho bread pane pao candy dolci doce",
    "CORNICI & GRECHE": "border borders bordur bordure frame frames cornic moldura marco corner angol canto greca greche redwork quilt patchwork motif ornament decor heirloom candlewick swirl scroll",
    "APPLIQUE": "applique appliqué aplique aplicação aplicacion applicaz",
    "LETTERE-NUMERI": "alfabet alphabet alphab alfabeto letter letters lettere letra letras font fonts monogram monogramm number numeri numero abc script",
    "FESTE": "christmas natale natal navidad xmas santa babbo noel easter pasqua pascoa pascua halloween valentin san valentino love amore amor heart cuore coração corazon wedding matrimon casamento boda birthday complean aniversario cumpleaños holiday holidays feste festa fiesta party thanksgiving new year capodanno baby shower occasion",
    "DISNEY-CARTONI": "disney cartoon cartoni cartoons desenho dibujo mickey minnie pooh winnie snow white biancaneve princess principess simpsons pokemon looney tunes hello kitty barbie dessins animès bd character personaggi sesame",
    "VEICOLI": "car cars auto macchina carro coche truck camion caminhao train treno trem tren plane aereo aviao avion airplane boat barca barco nave ship moto motorcycle bike bici bicicleta transport tractor trattore heavy equipment racing motor sport ",
    "MESTIERI": "occupational mestier profession teacher insegnant professor nurse infermier doctor medico police polizia fireman pompier bombeiro chef worker lavoro",
    "SCUOLA": "school scuola escola escuela collegiate college pencil matita book libro livro abc teacher",
    "MUSICALI": "music musica musical musicali note notas guitar chitarra violao guitarra piano instrument strumento",
    "DANZA": "dance danza dança ballet ballerina bailarina",
    "HOBBY": "sport sports baseball basketball soccer calcio futebol futbol football golf tennis bowling cheerleading fishing pesca lures hunting camping hobby sewing cucito costura knitting craft rodeo cowboy",
    "RELIGIONE": "religio church chiesa igreja iglesia angel angeli anjo angeles cross croce cruz jesus gesù madonna saint santo pray bible bibbia christening battesimo batizado communion comunione",
    "METEO": "weather meteo sun sole moon luna lua star stelle estrela estrella rain pioggia chuva lluvia snow neve nieve snowflake fiocco di neve cloud nuvol",
    "VESTITI": "fashion moda dress vestit vestido shoe scarp sapato zapato hat cappell chapeu sombrero bonnet bonnets bag bags borsa bolsa purse jewelry gioiell joia joya earring orecchin facemask mask",
    "FIOCCHI": "bow bows fiocco fiocchi laço lazo ribbon nastro fita cinta",
    "LUOGHI": "house casa home city città cidade ciudad country landscape paesagg paisagem paisaje lighthouse faro beach spiaggia praia playa travel viaggio viagem viaje world mondo map italia italy paris london",
    "FANTASIA": "fantasy fantasia fairy fata fate fada hada magic magia mythical mitic mermaid sirena castle castello dragon unicorn elf elfo gnome folk art celtic tribal aboriginal",
    "HORROR": "horror skull teschio caveira calavera zombie vampire vampir witch strega bruxa bruja ghost fantasma",
    "QUADRI": "picture quadro quadri scene scena cross stitch punto croce ponto cruz photo stitch portrait ritratto",
    "FRASI": "saying sayings frase frasi quote quotes word words parole palavras palabras phrase text",
    "PARTI DEL CORPO": "hand mano mão mano foot piede pé pie lips labbra eye occhi olho ojo face viso rosto cara",
    "SCHIZZI": "sketch schizz sketched outline contorno line art lineart",
}
INTERE = {"pane", "pao", "topo", "rato", "pato", "mono", "cara", "mano", "vite", "rana", "sapo", "cane", "cani", "sole",
          "luna", "lua", "neve", "casa", "faro", "auto", "moda", "fata", "fate", "fada", "rosa", "flor", "pie", "eye", "sea",
          "car", "cars", "bow", "bows", "hat", "sun", "star", "home", "cross", "note", "book", "line", "word", "words", "text"}


def _parola(k):
    return re.escape(k) + (r"s?(?![a-zà-ü])" if len(k) <= 3 or k in INTERE else "")


_TEMI_RE = [(t, re.compile(r"(?<![a-zà-ü])(" + "|".join(_parola(k) for k in sorted(set(v.split()), key=len, reverse=True)) + r")", re.I)) for t, v in TEMI.items()]
# tecniche: se compaiono vincono sul soggetto (un pizzo natalizio va nei PIZZI)
TECNICHE = [
    ("PIZZI (FSL)", re.compile(r"(?<![a-z])(lace|laces|pizz[oi]|renda|rendas|encaje|dentelle|centrin|doil(y|ies)|fsl|free ?standing|cutwork|intaglio|battenberg|crochet|uncinetto|k-lace)", re.I)),
    ("APPLIQUE", re.compile(r"(?<![a-z])(appliqu|aplica|applicaz)", re.I)),
    ("IN THE HOOP (ITH)", re.compile(r"(?<![a-z])(ith|in[ -]?the[ -]?hoop|bags?|purses?|zipper|pouch(es)?|tote|wallet|"
                                     r"key ?fobs?|keychain|coasters?|mug ?rugs?|stuff(ed|ie|ies)|plush|bookmarks?|"
                                     r"borsa|borse|borsell|portachiav|astuccio|sottobicchier|pochette|sac|trousse)(?![a-z])", re.I)),
]
BAMBINI = re.compile(r"(?<![a-z])(baby|babies|bimb|bambin|children|child|kids|kid|infant|nursery|crian|niñ|neonat)", re.I)


def tema_di(testo):
    """Il tema con più parole chiave trovate nel percorso (collezione + nome file)."""
    for t, rx in TECNICHE:
        if rx.search(testo):
            return t
    punti = {}
    for t, rx in _TEMI_RE:
        n = len(rx.findall(testo))
        if n:
            punti[t] = n
    if not punti:
        return "BAMBINI" if BAMBINI.search(testo) else "VARIE (da smistare)"
    return max(punti, key=punti.get)


SALTA_DIR = re.compile(r"^(.*\.part\d+|_?programas.*|software|system(64)?|\$recycle\.bin)$", re.I)


MISURA = re.compile(r"\s*[-_(]*\b\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:mm|cm|in|inch(?:es)?|\")?\)?\s*", re.I)
RUMORE_DIR = re.compile(r"^(other ?formats?|altri formati|all ?formats?|formats?|files?|designs?|embroidery|back|front|"
                        r"(?:hoop|telaio)\s*\w*|size|sizes|misure|tamaños?|tailles?|small|medium|large|big)$", re.I)


def senza_misura(nome):
    """«panel 2 kangaroo 5x7» -> «panel 2 kangaroo»; una cartella fatta solo di misure diventa vuota."""
    return re.sub(r"\s{2,}", " ", MISURA.sub(" ", nome)).strip(" -_.")


def collezione_di(rel_dir):
    """Nome leggibile della collezione: salta cartelle 'tecniche' (formato, partNN, misure, wrapper ripetuti)."""
    parti = [p for p in rel_dir.split(os.sep) if p]
    utili = [senza_misura(p) for p in parti[1:] if not tecnica(p) and not SALTA_DIR.match(p)]
    utili = [p for p in utili if p and not RUMORE_DIR.match(p) and not tecnica(p)]
    # togli i nomi ripetuti uno dentro l'altro (es. "Coleção X\Coleção X")
    out = []
    for p in utili:
        if not out or out[-1].lower() != p.lower():
            out.append(p)
    return out


def tema_per_cartella(disegni, nome_tema):
    """Tema deciso per cartella d'origine con TUTTI i nomi dei file dentro (una raccolta non si spacca in due temi).
    I nomi dei disegnatori non contano (non dicono il soggetto)."""
    def chiave(cart):                                    # «panel 2 kangaroo 5x7» e «… 6x10» = stessa raccolta
        c = collezione_di(cart)[:1]
        return (cart.split(os.sep)[0], c[0].lower()) if c else (cart,)
    nomi = {}
    for d in disegni:
        nomi.setdefault(chiave(d["dir"]), []).append(d["nome"])
    temi = {}
    for k, ns in nomi.items():
        testo = " ".join(k[1:]) + " " + " ".join(ns)
        for _n, _rx in AUTORI:
            testo = _rx.sub(" ", testo)
        temi[k] = nome_tema(tema_di(testo))
    return {d["dir"]: temi[chiave(d["dir"])] for d in disegni}


def pulisci_archivio(out):
    """Rifare il riordino: toglie i file che OpenHoop aveva messo nell'archivio (elencati nel vecchio dati.js) e i
    dati che dipendono dall'ordine dei disegni (miniature, impronte, varianti). I file importati o digitalizzati
    (importati.json, digitalizzati.json) e le cartelle dei ricami dell'utente non si toccano."""
    cat = os.path.join(out, "_catalogo")
    try:
        t = open(os.path.join(cat, "dati.js"), encoding="utf-8").read()
        vecchi = json.loads(t.split("const DATI=", 1)[1].rstrip().rstrip(";"))
    except (OSError, ValueError, IndexError):
        return
    tieni = set()
    for f in ("importati.json", "digitalizzati.json"):
        try:
            tieni |= {v[0] if isinstance(v, list) else v.get("percorso", "") for v in json.load(open(os.path.join(cat, f), encoding="utf-8"))}
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    radice = os.path.normcase(os.path.abspath(out))
    cartelle = set()
    for v in vecchi:
        f = os.path.abspath(os.path.join(out, v[0]))
        if v[0] in tieni or not os.path.normcase(f).startswith(radice + os.sep) or "_catalogo" in v[0]:
            continue
        try:
            os.remove(f)
            cartelle.add(os.path.dirname(f))
        except OSError:
            pass
    for c in sorted(cartelle, key=len, reverse=True):       # cartelle rimaste vuote (anche quella del tema)
        for d in (c, os.path.dirname(c)):
            try:
                if os.path.normcase(d) != radice:
                    os.rmdir(d)
            except OSError:
                pass
    for f in ("clip.npy", "clip_n.txt", "clip_modello.txt", "varianti.json", "temi_auto.json"):
        try:
            os.remove(os.path.join(cat, f))
        except OSError:
            pass
    for d in ("img", "img_hd"):
        shutil.rmtree(os.path.join(cat, d), ignore_errors=True)


def pulisci(nome, maxlen=60):
    # prima accorcia, poi togli spazi/punti finali (Windows non accetta cartelle che finiscono con spazio)
    nome = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", nome)
    nome = re.sub(r"\s+", " ", nome)[:maxlen].strip(" .")
    return nome or "senza nome"


# ---------------- 1) indice: un elemento per disegno con tutti i suoi formati ----------------
def indicizza(filtro=None, radici=None, salta=None):
    """radici: cartelle da leggere (di base le tre cartelle storiche dentro SRC). Il percorso relativo parte dal NOME della
    radice (il primo pezzo poi si scarta in collezione_di). salta: cartella da non leggere (l'archivio stesso)."""
    disegni = {}
    salta = os.path.normcase(os.path.abspath(salta)) if salta else None
    for base in radici or [os.path.join(SRC, s) for s in SORGENTI]:
        sopra = os.path.dirname(os.path.abspath(base))
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if (not SALTA_DIR.match(d) or d.lower().endswith(tuple(f".part{i:02d}" for i in range(100))))
                     and not (salta and os.path.normcase(os.path.abspath(os.path.join(dp, d))) == salta)]
            rel = os.path.relpath(dp, sopra)
            if filtro and not any(f.lower() in rel.lower() for f in filtro):
                continue
            for f in fn:
                b, e = os.path.splitext(f)
                e = e.lower()
                if e not in PRIO:
                    continue
                # i formati possono stare in sottocartelle "PES", "JEF"...: il disegno è della cartella sopra
                d = dp
                while tecnica(os.path.basename(d)):
                    d = os.path.dirname(d)
                chiave = (d.lower(), b.lower())
                disegni.setdefault(chiave, {"dir": os.path.relpath(d, sopra), "nome": b, "file": {}})["file"][e] = os.path.join(dp, f)
    return list(disegni.values())


# ---------------- 2) per ogni disegno: scegli/converti PES, misure, firma, miniatura ----------------
def lavora(args):
    """Un disegno problematico non deve mai fermare tutto il lavoro."""
    try:
        return _lavora(args)
    except Exception as ex:
        d = args[0]
        return {"src": next(iter(d["file"].values())), "err": f"{type(ex).__name__}: {ex}"}


def _lavora(args):
    d, out_img = args
    import pyembroidery
    from PIL import Image, ImageDraw
    fmt = min(d["file"], key=lambda e: PRIO[e])
    src = d["file"][fmt]
    try:
        pat = pyembroidery.read(src)
        if pat is None or not pat.stitches:
            raise ValueError("vuoto")
    except Exception as ex:
        # prova gli altri formati dello stesso disegno
        for e in sorted(d["file"], key=lambda e: PRIO[e]):
            if e == fmt:
                continue
            try:
                pat = pyembroidery.read(d["file"][e])
                if pat is not None and pat.stitches:
                    fmt, src = e, d["file"][e]
                    break
            except Exception:
                pass
        else:
            return {"err": f"{type(ex).__name__}", "src": src}
    x0, y0, x1, y1 = pat.bounds()
    w, h = (x1 - x0) / 10, (y1 - y0) / 10                        # mm
    punti = [(s[0], s[1]) for s in pat.stitches if (s[2] & 0xFF) == 0]
    # impronta "a somiglianza": densità dei punti su una griglia 8x8 (formati diversi spostano i punti di poco)
    griglia = [0] * 64
    gx, gy = max(x1 - x0, 1) / 8, max(y1 - y0, 1) / 8
    for x, y in punti:
        griglia[min(7, int((y - y0) / gy)) * 8 + min(7, int((x - x0) / gx))] += 1
    tot = max(1, len(punti))
    firma = [round(v / tot, 4) for v in griglia]
    # miniatura 160 px
    S = 160
    k = (S - 8) / max(x1 - x0, y1 - y0, 1)
    img = Image.new("RGB", (S, S), "white")
    dr = ImageDraw.Draw(img)
    colori = 0
    sequenza = []                                                # colori del filo nell'ordine di ricamo
    for blocco, filo in pat.get_as_colorblocks():
        colori += 1
        col = filo.hex_color() if filo is not None and filo.color is not None else "#333333"
        if len(sequenza) < 40:
            sequenza.append(col)
        prev = None
        for st in blocco:
            c = st[2] & 0xFF
            p = (4 + (st[0] - x0) * k, 4 + (st[1] - y0) * k)
            if c == 0 and prev is not None:                      # STITCH
                dr.line([prev, p], fill=col, width=1)
            prev = p if c in (0, 1) else None                     # JUMP spezza la linea
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=70)
    # PES da scrivere se l'originale non è PES
    pes = None
    if fmt != ".pes":
        for th in pat.threadlist:                                # fili senza colore fanno cadere write_pes
            if th.color is None:
                th.color = 0x333333
        # angolo in alto a sinistra in 0,0 come i PES Brother: centrato in 0,0 la Innov-is ne mostra/ricama un quarto
        pat.translate(-x0, -y0)
        b = io.BytesIO()
        pyembroidery.write_pes(pat, b, {"pes version": 6})
        pes = b.getvalue()
    return {"firma": firma, "fmt": fmt, "src": src, "w": round(w, 1), "h": round(h, 1),
            "colori": colori, "punti": len(punti), "img": buf.getvalue(), "pes": pes, "seq": sequenza}


def main():
    modo = sys.argv[1] if len(sys.argv) > 1 else "config"
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(SRC, "Ricami_ordinati")
    filtro, radici, generico = None, None, False
    nome_tema = lambda s: s  # noqa: E731
    if modo == "config":                                  # OpenHoop: cartelle scelte al primo avvio
        import config
        cfg = config.leggi()
        OUT, radici, generico = cfg["archivio"], [r for r in cfg["sorgenti"] if os.path.isdir(r)], True
        if not radici or not OUT:
            sys.exit("mancano le cartelle: impostale nella pagina del primo avvio")
        if cfg.get("lingua", "it") != "it":                # cartelle dei temi nella lingua dell'utente
            try:
                _diz = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lingue",
                                                   cfg["lingua"] + ".json"), encoding="utf-8"))
            except (OSError, ValueError):
                _diz = {}
            nome_tema = lambda s: pulisci(_diz.get(s, s))  # noqa: E731
    t0 = time.time()
    if generico:
        pulisci_archivio(OUT)
    disegni = indicizza(filtro, radici, salta=OUT)
    print(f"disegni trovati: {len(disegni)}  ({time.time()-t0:.0f}s)", flush=True)

    os.makedirs(os.path.join(OUT, "_catalogo", "img"), exist_ok=True)
    if generico:                                         # OpenHoop: un tema per raccolta (tutti i nomi insieme)
        TEMA_CART = tema_per_cartella(disegni, nome_tema)
    origine = (radici or [SRC])[0]
    stesso_volume = os.path.splitdrive(OUT)[0].lower() == os.path.splitdrive(origine)[0].lower()
    visti, catalogo, errori, doppi = {}, [], [], 0
    with ProcessPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 2)) as ex:
        for i, (d, r) in enumerate(zip(disegni, ex.map(lavora, [(d, None) for d in disegni], chunksize=64))):
            if i % (2000 if not generico else 50) == 0:
                print(f"  {i}/{len(disegni)}  doppioni {doppi}  errori {len(errori)}  ({time.time()-t0:.0f}s)", flush=True)
            if "err" in r:
                errori.append([r["src"], r["err"]])
                continue
            # stesso disegno già preso? misure entro 1 mm, punti entro il 3%, distribuzione quasi uguale
            kw, kh = round(r["w"]), round(r["h"])
            doppio = False
            for dw in (-1, 0, 1):
                for dh in (-1, 0, 1):
                    for p2, g2 in visti.get((kw + dw, kh + dh), ()):
                        if abs(p2 - r["punti"]) <= 0.03 * max(p2, r["punti"]) and \
                                sum(abs(a - b) for a, b in zip(g2, r["firma"])) < 0.08:
                            doppio = True
                            break
                    if doppio:
                        break
                if doppio:
                    break
            if doppio:
                doppi += 1
                continue
            parti = collezione_di(d["dir"])
            if generico:                                     # OpenHoop: tema dalle parole (raccolta + nome)
                tema, coll = TEMA_CART[d["dir"]], parti[:1]
                if not coll:                                 # file sparsi: «bird bag 6x10 back» -> raccolta «bird bag»
                    base = senza_misura(d["nome"])
                    base = re.sub(r"\b(back|front|retro|fronte|\d+)\b", " ", base, flags=re.I)
                    base = re.sub(r"\s{2,}", " ", base).strip(" -_.")
                    coll = [base] if base and base.lower() != d["nome"].lower().strip() else [nome_tema("(generale)")]
            elif d["dir"].startswith("01_Ricami_PES"):
                tema = pulisci(parti[0]) if parti else "VARIE (da smistare)"
                coll = parti[1:] or ["(generale)"]
            elif d["dir"].startswith("02_Gia_ricamati"):
                tema, coll = "GIA RICAMATI", parti or ["(generale)"]
            else:
                tema = tema_di(" ".join(parti) + " " + d["nome"])
                coll = parti or ["(generale)"]
            coll_dir = pulisci(" - ".join(coll[-2:]) if len(coll) > 1 else coll[0])
            dest_dir = os.path.join(OUT, tema, coll_dir)
            nome = pulisci(d["nome"], 50)
            try:
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, nome + ".pes")
                n = 2
                while os.path.exists(dest):
                    dest = os.path.join(dest_dir, f"{nome} ({n}).pes")
                    n += 1
                if r["pes"] is None:
                    try:
                        os.link(r["src"], dest) if stesso_volume else shutil.copy2(r["src"], dest)
                    except OSError:
                        shutil.copy2(r["src"], dest)
                else:
                    with open(dest, "wb") as fh:
                        fh.write(r["pes"])
            except OSError as ex:                                    # un disegno problematico non ferma tutto
                errori.append([r["src"], f"scrittura: {ex}"])
                continue
            idx = len(catalogo)
            with open(os.path.join(OUT, "_catalogo", "img", f"{idx}.webp"), "wb") as fh:
                fh.write(r["img"])
            visti.setdefault((kw, kh), []).append((r["punti"], r["firma"]))
            catalogo.append([os.path.relpath(dest, OUT), tema, r["w"], r["h"], r["colori"], r["punti"], r["fmt"], r["seq"]])

    with open(os.path.join(OUT, "_catalogo", "dati.js"), "w", encoding="utf-8") as fh:
        fh.write("const TELAIO=" + json.dumps(TELAIO) + ";\nconst DATI=" + json.dumps(catalogo, ensure_ascii=False, separators=(",", ":")) + ";\n")
    with open(os.path.join(OUT, "_catalogo", "errori.json"), "w", encoding="utf-8") as fh:
        json.dump(errori, fh, ensure_ascii=False, indent=0)
    if not generico:
        shutil.copy2(os.path.join(os.path.dirname(__file__), "catalogo.html"), os.path.join(OUT, "Catalogo ricami.html"))
    conv = sum(1 for c in catalogo if c[6] != ".pes")
    print(f"FINE: {len(catalogo)} disegni unici ({conv} convertiti in PES), {doppi} doppioni scartati, "
          f"{len(errori)} illeggibili. {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
