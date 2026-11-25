"""Ricerca di disegni gratuiti (SVG e immagini) per il digitalizzatore.

Fonti (provate il 25/11/2026, vedi NOTE.md):
- Iconify, solo raccolte di emoji/illustrazioni a colori (o tutte, con «anche icone e loghi»): veloce, pulito.
- Pixabay (API ufficiale, chiave PIXABAY_API_KEY in .env): illustrazioni vettoriali carine, cerca in italiano.
  Regole: risultati in cache 24 h, citare Pixabay, scaricare l'immagine prima di usarla (niente hotlink fisso).
- SVG Repo: legge la pagina pubblica di ricerca (filtro «multicolor»); blocca (429) se si cerca troppo in fretta:
  una richiesta ogni 3 s e cache 24 h.
- OpenClipart: clipart CC0; non ha API, si legge la pagina di ricerca (se cambia il sito questa parte si rompe).
- Wikimedia Commons: solo su richiesta («altre fonti»), molti loghi/mappe da filtrare.
Scartati: Openverse (lento), FreeSVG/PublicDomainVectors (copie di OpenClipart), motori di ricerca immagini (non
sono fatti per i programmi).

Ogni immagine raster riceve un voto «adatta al ricamo» (0/1/2) guardando la miniatura: sfondo bianco o trasparente
e pochi colori piatti = 2; foto o sfondi pieni = 0.

Le ricerche sono in inglese: `traduci()` usa prima un piccolo dizionario, poi la cache, poi MyMemory (gratuito, senza chiave).
"""
import hashlib
import html
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "archivio-ricami/1.0 (uso personale)"}
ICONIFY_SET = ["noto", "fluent-emoji-flat", "twemoji", "openmoji", "emojione", "fxemoji",
               "streamline-kameleon-color", "flat-color-icons"]
ICONIFY_LIC = {"noto": ("Apache 2.0", "Google"), "fluent-emoji-flat": ("MIT", "Microsoft"),
               "twemoji": ("CC BY 4.0", "Twitter"), "openmoji": ("CC BY-SA 4.0", "OpenMoji"),
               "emojione": ("CC BY 4.0", "Emoji One"), "fxemoji": ("Apache 2.0", "Mozilla"),
               "streamline-kameleon-color": ("CC BY 4.0", "Streamline"), "flat-color-icons": ("MIT", "Icons8")}
HOST_OK = {"api.iconify.design", "openclipart.org", "upload.wikimedia.org", "www.svgrepo.com"}   # SVG da qui
HOST_IMG = {"cdn.pixabay.com", "pixabay.com"}                                                    # immagini da qui
UA_SITO = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130 Safari/537.36"}                     # SVG Repo risponde solo ai browser
PROGETTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMONS_NO = re.compile(r"logo|map|mappa|locator|chart|diagram|graph|population|distribution|range|flag of|wordmark|"
                        r"icon set|structure|formula|blank|template|\.svg\.svg", re.I)

# Nomi italiani dei personaggi: nessun modello di traduzione sa che «Topolino» è «Mickey Mouse» (lo traduce «mouse»).
# Solo nomi propri: tutto il resto lo traduce il modello opus-mt-it-en.
NOMI = {
    "topolino": "mickey mouse", "minni": "minnie mouse", "paperino": "donald duck", "paperina": "daisy duck",
    "pippo": "goofy", "pluto": "pluto", "zio paperone": "scrooge mcduck", "qui quo qua": "huey dewey louie",
    "biancaneve": "snow white", "cenerentola": "cinderella", "la sirenetta": "ariel", "sirenetta": "ariel",
    "la bella addormentata": "aurora", "winnie the pooh": "winnie the pooh", "pooh": "winnie the pooh",
    "uomo ragno": "spider-man", "spiderman": "spider-man", "l'uomo ragno": "spider-man", "spider man": "spider-man",
    "babbo natale": "santa claus", "puffi": "smurf", "puffo": "smurf", "puffetta": "smurfette",
    "pimpa": "pimpa", "peppa": "peppa pig", "masha e orso": "masha and the bear", "bing": "bing bunny",
    "gli incredibili": "the incredibles", "il re leone": "simba", "re leone": "simba", "dumbo": "dumbo",
    "bambi": "bambi", "pinocchio": "pinocchio", "trilli": "tinker bell", "campanellino": "tinker bell",
    "capitan uncino": "captain hook", "lilli e il vagabondo": "lady and the tramp", "carica dei 101": "dalmatian",
}
_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCK = threading.Lock()
_CACHE_RIC = {}                                         # (q, pagina, altre) -> risultati, finché il server è acceso


def _get(url, params=None, timeout=12, headers=UA):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


CACHE_TRAD = os.path.join(_DIR, "traduzioni.json")      # il server lo sposta in _catalogo con imposta_cache()
CACHE_DIR = None                                        # risultati delle fonti, 24 h (obbligatorio per Pixabay)


def imposta_cache(percorso):
    global CACHE_TRAD, CACHE_DIR
    CACHE_TRAD = percorso
    CACHE_DIR = os.path.join(os.path.dirname(percorso), "cerca_cache")


def _in_cache(chiave, fn, ore=24):
    """Risultato di fn() tenuto su disco per `ore` (una fonte, una ricerca, una pagina)."""
    if not CACHE_DIR:
        return fn()
    f = os.path.join(CACHE_DIR, hashlib.sha1(json.dumps(chiave).encode()).hexdigest()[:20] + ".json")
    try:
        if time.time() - os.path.getmtime(f) < ore * 3600:
            return json.load(open(f, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    v = fn()
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(v, fh, ensure_ascii=False)
    return v


def _chiave(nome):
    try:                                                # OpenHoop: chiavi scritte dall'utente nelle Impostazioni
        import config
        k = config.leggi().get({"PIXABAY_API_KEY": "chiave_pixabay", "BRAVE_API_KEY": "chiave_brave"}.get(nome, ""))
        if k:
            return k
    except Exception:
        pass
    try:
        for riga in open(os.path.join(PROGETTO, ".env"), encoding="utf-8"):
            if riga.strip().startswith(nome + "="):
                return riga.split("=", 1)[1].strip().strip('"').strip("'") or None
    except OSError:
        pass
    return os.environ.get(nome) or None


def chiave_pixabay():
    return _chiave("PIXABAY_API_KEY")


def chiave_brave():
    return _chiave("BRAVE_API_KEY")


def _trad_cache():
    try:
        return json.load(open(CACHE_TRAD, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


try:                                                    # OpenHoop: lingua di chi cerca (dalle impostazioni)
    import config as _config
    LINGUA = _config.leggi().get("lingua", "it")
except Exception:
    LINGUA = "it"
# un modello piccolo per lingua (~300 MB, solo traduzione verso l'inglese, 0,1-0,2 s su CPU); in inglese niente
MODELLI_TRAD = {"it": "Helsinki-NLP/opus-mt-it-en", "fr": "Helsinki-NLP/opus-mt-fr-en", "es": "Helsinki-NLP/opus-mt-es-en"}
MODELLO_TRAD = MODELLI_TRAD.get(LINGUA)
_TRAD = {}
_TRAD_LOCK = threading.Lock()


def _modello():
    """Carica (una volta) il modello di traduzione. Su CPU: la GPU resta a SigLIP."""
    with _TRAD_LOCK:
        if "m" not in _TRAD:
            from transformers import MarianMTModel, MarianTokenizer
            _TRAD["t"] = MarianTokenizer.from_pretrained(MODELLO_TRAD)
            _TRAD["m"] = MarianMTModel.from_pretrained(MODELLO_TRAD).eval()
        return _TRAD["t"], _TRAD["m"]


def precarica():
    """Carica il modello in sottofondo all'avvio, così la prima ricerca non aspetta ~10-30 s."""
    if not MODELLO_TRAD:
        return
    threading.Thread(target=lambda: _prova(lambda _: _modello(), None), daemon=True).start()


def _traduci_modello(testo):
    import torch
    tok, m = _modello()
    with _TRAD_LOCK, torch.no_grad():
        out = m.generate(**tok([testo], return_tensors="pt"), max_new_tokens=32, num_beams=4)
    return tok.decode(out[0], skip_special_tokens=True)


def _traduci_mymemory(testo):
    r = json.loads(_get("https://api.mymemory.translated.net/get", {"q": testo, "langpair": LINGUA + "|en"}, timeout=8))
    t = html.unescape((r.get("responseData") or {}).get("translatedText") or "")
    if not t or "mymemory" in t.lower() or "quota" in t.lower() or len(t) > 80:
        raise ValueError(t[:60])
    return t


def traduci(q):
    """Italiano -> inglese per le ricerche, col modello opus-mt-it-en (locale, gratis). I nomi dei personaggi (NOMI)
    si sostituiscono prima. Restituisce (testo inglese, come: 'modello'|'nomi'|'cache'|'mymemory'|'nessuna')."""
    q = " ".join(q.lower().split())
    if not q:
        return "", "nessuna"
    if not MODELLO_TRAD:                                 # chi scrive già in inglese
        return q, "nessuna"
    if q in NOMI:
        return NOMI[q], "nomi"
    testo = q
    for it in sorted(NOMI, key=len, reverse=True):       # «topolino con palloncino» -> «mickey mouse con palloncino»
        testo = re.sub(rf"(?<!\w){re.escape(it)}(?!\w)", NOMI[it], testo)
    chiave = "m:" + testo if LINGUA == "it" else f"m:{LINGUA}:{testo}"
    with _LOCK:
        c = _trad_cache()
    if chiave in c:
        return c[chiave], "cache"
    t, come = None, "nessuna"
    for fn, nome in ((_traduci_modello, "modello"), (_traduci_mymemory, "mymemory")):   # MyMemory solo se il modello manca
        try:
            t, come = fn(testo), nome
            break
        except Exception:
            continue
    if t is None:
        return testo, "nessuna"                          # niente modello né rete: si cerca così com'è
    t = re.sub(r"[^\w\s'&-]", "", t.lower()).strip(" -'") or testo
    with _LOCK:
        c = _trad_cache()
        c[chiave] = t
        try:
            with open(CACHE_TRAD, "w", encoding="utf-8") as fh:
                json.dump(c, fh, ensure_ascii=False, indent=0)
        except OSError:
            pass
    return t, come


def iconify(q, pagina=0, tutte=False):
    if pagina:                                           # l'API dà al massimo ~100 risultati in un colpo solo
        return []
    par = {"query": q, "limit": 96}
    if not tutte:                                        # di base solo emoji e illustrazioni a colori
        par["prefixes"] = ",".join(ICONIFY_SET)
    r = json.loads(_get("https://api.iconify.design/search", par))
    out = []
    for ic in r.get("icons", []):
        pre, nome = ic.split(":", 1)
        lic, aut = ICONIFY_LIC.get(pre, ("vedi raccolta", pre))
        out.append({"fonte": "Iconify", "id": ic, "titolo": nome.replace("-", " "), "licenza": lic, "autore": aut,
                    "mini": f"https://api.iconify.design/{pre}/{nome}.svg?height=96",
                    "svg": f"https://api.iconify.design/{pre}/{nome}.svg?height=1200",
                    "pagina": f"https://icon-sets.iconify.design/{pre}/{nome}/"})
    # prima le raccolte più «carine» (ordine di ICONIFY_SET)
    out.sort(key=lambda x: ICONIFY_SET.index(x["id"].split(":")[0]) if x["id"].split(":")[0] in ICONIFY_SET else 99)
    return out


def openclipart(q, pagina=0):
    t = _get("https://openclipart.org/search/", {"query": q, "p": pagina + 1}).decode("utf-8", "replace")
    out, visti = [], set()
    for m in re.finditer(r'<a href="/detail/(\d+)/[^"]*">\s*<picture[^>]*>.*?<img[^>]+alt="([^"]*)"', t, re.S):
        i, tit = m.group(1), html.unescape(m.group(2))
        if i in visti:
            continue
        visti.add(i)
        out.append({"fonte": "OpenClipart", "id": i, "titolo": tit, "licenza": "CC0 (libero)", "autore": "",
                    "mini": f"https://openclipart.org/image/200px/{i}", "svg": f"https://openclipart.org/download/{i}",
                    "pagina": f"https://openclipart.org/detail/{i}"})
    return out


_SVGREPO_LOCK = threading.Lock()
_SVGREPO_ULTIMA = [0.0]


def svgrepo(q, pagina=0):
    """SVG Repo, solo disegni a più colori. Una richiesta ogni 3 s al massimo: oltre, il sito risponde 429."""
    slug = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
    if not slug:
        return []
    url = f"https://www.svgrepo.com/vectors/{urllib.parse.quote(slug)}/multicolor/" + (f"{pagina + 1}/" if pagina else "")
    with _SVGREPO_LOCK:
        attesa = 3 - (time.time() - _SVGREPO_ULTIMA[0])
        if attesa > 0:
            time.sleep(attesa)
        try:
            t = _get(url, headers=UA_SITO, timeout=15).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code == 429:
                raise ValueError("troppe ricerche di fila, riprova fra un minuto")
            raise
        finally:
            _SVGREPO_ULTIMA[0] = time.time()
    out = []
    for i, nome in dict.fromkeys(re.findall(r'src="/show/(\d+)/([^"/]+)\.svg"', t)):
        out.append({"fonte": "SVG Repo", "id": i, "titolo": nome.replace("-", " "), "licenza": "vedi pagina (SVG Repo)",
                    "autore": "", "mini": f"https://www.svgrepo.com/show/{i}/{nome}.svg",
                    "svg": f"https://www.svgrepo.com/show/{i}/{nome}.svg", "pagina": f"https://www.svgrepo.com/svg/{i}/{nome}"})
    return out


def pixabay(q, pagina=0):
    """Pixabay, illustrazioni vettoriali (arrivano come PNG/JPG fino a 1280 px). Cerca direttamente in italiano."""
    k = chiave_pixabay()
    if not k:
        return []
    r = json.loads(_get("https://pixabay.com/api/", {"key": k, "q": q[:100], "lang": LINGUA if LINGUA in ("it", "fr", "es", "en") else "en", "image_type": "vector",
                                                     "safesearch": "true", "per_page": 60, "page": pagina + 1}))
    out = []
    for h in r.get("hits", []):
        tag = (h.get("tags") or "").split(",")[0].strip()
        out.append({"fonte": "Pixabay", "id": str(h["id"]), "titolo": tag or "pixabay", "tipo": "img",
                    "licenza": "Pixabay (uso libero)", "autore": h.get("user", ""), "mini": h.get("previewURL"),
                    "grande": h.get("largeImageURL") or h.get("webformatURL"), "pagina": h.get("pageURL", ""),
                    "ai": bool(h.get("isAiGenerated"))})
    return out


_BRAVE_OK = set()          # immagini trovate da Brave o dai wiki dei fan: solo queste si possono scaricare (oltre a HOST_*)

# Wiki dei fan con API MediaWiki aperta (gratis, senza chiave; provati il 25/11/2026). I wiki su Fandom (Disney,
# Hello Kitty, Bluey…) bloccano i programmi: non usarli. Parole chiave -> quel wiki si interroga anche senza la casella.
WIKI_FAN = [
    ("Minecraft Wiki", "https://minecraft.wiki/api.php", "minecraft creeper steve enderman villager ghast"),
    ("Bulbapedia", "https://archives.bulbagarden.net/w/api.php", "pokemon pokémon pikachu eevee charmander bulbasaur squirtle"),
    ("Super Mario Wiki", "https://www.mariowiki.com/api.php", "mario luigi peach yoshi toad bowser"),
    ("SmashWiki", "https://www.ssbwiki.com/api.php", "smash nintendo"),
    ("Zelda Wiki", "https://zeldawiki.wiki/w/api.php", "zelda"),
    ("Sonic Retro", "https://info.sonicretro.org/api.php", "sonic tails knuckles"),
    ("Nookipedia", "https://nookipedia.com/w/api.php", "animal crossing"),
    ("Terraria Wiki", "https://terraria.wiki.gg/api.php", "terraria"),
    ("Pikmin Wiki", "https://www.pikminwiki.com/api.php", "pikmin"),
]


def _wiki(nome, base, q):
    r = json.loads(_get(base, {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                               "gsrlimit": 50, "gsrsearch": q, "prop": "imageinfo", "iiprop": "url|mime|size",
                               "iiurlwidth": 160}, timeout=12))
    out = []
    pagine = (r.get("query") or {}).get("pages", {}).values()
    # le immagini ufficiali sono grandi, gli sprite dei giochi minuscoli (e danno decine di tagli): prima le grandi
    for p in sorted(pagine, key=lambda p: -max((p.get("imageinfo") or [{}])[0].get(k, 0) for k in ("width", "height"))):
        ii = (p.get("imageinfo") or [{}])[0]
        mime, url = ii.get("mime", ""), ii.get("url", "")
        if mime not in ("image/png", "image/svg+xml", "image/gif", "image/webp") or not url.startswith("https://"):
            continue                                     # niente foto JPG (quasi sempre screenshot o foto)
        svg = mime == "image/svg+xml"
        if not svg and max(ii.get("width", 0), ii.get("height", 0)) < 300:
            continue
        out.append({"fonte": nome, "id": p.get("title", url), "tipo": "svg" if svg else "img", "proxy": True,
                    "titolo": re.sub(r"^[^:]+:|\.\w+$", "", p.get("title", "")).replace("_", " ")[:80],
                    "licenza": "personaggio protetto: solo uso personale", "autore": nome,
                    "mini": ii.get("thumburl") or url, "svg": url if svg else None, "grande": None if svg else url,
                    "pagina": ii.get("descriptionurl", "")})
    return out


def _safebooru_tag(q):
    """Parole -> il tag di Safebooru più usato che le contiene (es. «stitch» -> «stitch_(lilo_&_stitch)»)."""
    import xml.etree.ElementTree as ET
    parole = [p for p in re.findall(r"[a-z0-9]+", q.lower()) if p not in VUOTE]
    if not parole:
        return None
    unito = "_".join(parole)
    # prima i nomi esatti («mario», «creeper_(minecraft)»), poi «contiene» (name_pattern è sempre %…%)
    esatti = [unito]
    if len(parole) == 2:
        a, b = parole
        esatti += [f"{b}_({a})", f"{a}_({b})"]
    esatti += [unito + "_(series)", "-".join(parole)]                 # «spider-man»
    for n in esatti:
        t = _get("https://safebooru.org/index.php", {"page": "dapi", "s": "tag", "q": "index", "name": n}, timeout=12)
        for x in ET.fromstring(t).iter("tag"):
            if int(x.get("count", 0)) >= 5:
                return x.get("name")
    schemi = [unito + "_(", "%".join(parole)]
    if len(parole) > 1:
        schemi += ["%".join(reversed(parole))] + parole
    for s in schemi:
        t = _get("https://safebooru.org/index.php", {"page": "dapi", "s": "tag", "q": "index", "name_pattern": s,
                                                     "limit": 200}, timeout=12)
        tag = [(int(x.get("count", 0)), x.get("name", "")) for x in ET.fromstring(t).iter("tag")]
        # ogni parola deve essere intera nel tag (stitch sì in stitch_(lilo_&_stitch), no in stitches)
        tag = [(c, n) for c, n in tag if c >= 5 and any(re.search(rf"(^|[_(&]){p}($|[_)])", n) for p in parole)]
        if tag:
            return max(tag)[1]
    return None


def safebooru(q_en, q_it=""):
    """Disegni dei fan (Safebooru: solo immagini adatte a tutti). Prima quelli su sfondo trasparente o bianco.
    Il tag si cerca prima col nome scritto (i personaggi hanno spesso lo stesso nome in ogni lingua), poi tradotto."""
    tag = (_safebooru_tag(q_it) if q_it and q_it != q_en else None) or _safebooru_tag(q_en)
    if not tag:
        return []
    out, visti = [], set()
    for extra in ("transparent_background", "white_background", ""):
        if len(out) >= 40:
            break
        js = json.loads(_get("https://safebooru.org/index.php", {"page": "dapi", "s": "post", "q": "index", "json": 1,
                                                                 "limit": 60, "tags": f"{tag} {extra}".strip()},
                             timeout=12) or b"[]")
        for p in js or []:
            if p["id"] in visti or p.get("rating") not in ("general", "safe"):
                continue
            visti.add(p["id"])
            img = f"https://safebooru.org/images/{p['directory']}/{p['image']}"
            if not img.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                continue
            out.append({"fonte": "Safebooru", "id": str(p["id"]), "tipo": "img", "titolo": tag.replace("_", " "),
                        "licenza": "personaggio/fan art: solo uso personale", "autore": p.get("owner", ""),
                        "mini": p.get("preview_url") or img, "grande": img,
                        "pagina": f"https://safebooru.org/index.php?page=post&s=view&id={p['id']}"})
    return out


def wikifan(q_en, q_it, tutte):
    """Personaggi famosi dai wiki dei fan: i wiki nominati dalle parole della ricerca, o tutti se `tutte`."""
    parole = set(re.findall(r"[\wé]+", (q_en + " " + q_it).lower()))
    scelti = [w for w in WIKI_FAN if tutte or parole & set(w[2].split())]
    out = []
    with ThreadPoolExecutor(max(1, len(scelti))) as ex:
        def una(w):
            # «minecraft creeper» su Minecraft Wiki -> «creeper» (il nome della serie sul suo wiki non serve)
            qq = " ".join(p for p in q_en.split() if p not in w[2].split()[:1]) or q_en
            return _prova(lambda _: _wiki(w[0], w[1], qq), None)
        for w, r in zip(scelti, ex.map(una, scelti)):
            if not parole & set(w[2].split()):
                # wiki non nominato (casella «personaggi»): solo le immagini col nome cercato nel titolo
                chiavi = [p for p in re.findall(r"[a-z0-9]+", q_en.lower()) if len(p) >= 4 and p not in VUOTE]
                r = [x for x in r if chiavi and any(k in x["titolo"].lower() for k in chiavi)]
            out += r
    return out


def brave(q, pagina=0):
    """«Cerca ovunque»: ricerca immagini ufficiale di Brave (chiave BRAVE_API_KEY in .env, credito gratis mensile).
    Trova anche personaggi famosi: solo per uso personale."""
    k = chiave_brave()
    if not k or pagina:
        return []
    qq = q if re.search(r"clip ?art|disegno|cartoon|png|svg", q, re.I) else q + " clipart"
    req = urllib.request.Request("https://api.search.brave.com/res/v1/images/search?" + urllib.parse.urlencode(
        {"q": qq[:200], "count": 100, "safesearch": "strict", "search_lang": "it", "country": "IT"}),
        headers={"Accept": "application/json", "X-Subscription-Token": k, "User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=15) as r:
        j = json.loads(r.read())
    out = []
    for i, h in enumerate(j.get("results") or []):
        grande = (h.get("properties") or {}).get("url")
        mini = (h.get("thumbnail") or {}).get("src")
        if not grande or not mini or not grande.startswith("https://"):
            continue
        out.append({"fonte": "Ovunque", "id": grande, "titolo": (h.get("title") or "immagine")[:80], "tipo": "img",
                    "licenza": "sconosciuta: solo uso personale", "autore": h.get("source") or "",
                    "mini": mini, "grande": grande, "pagina": h.get("url") or ""})
    return out


def voto(raw):
    """Quanto una miniatura sembra «da ricamo»: 2 = sfondo libero e colori piatti, 1 = forse, 0 = foto o sfondo pieno."""
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im.thumbnail((96, 96))
    a = np.asarray(im.convert("RGBA")).astype(np.int16)
    bg = (a[..., 3] < 30) | (a[..., :3].min(-1) > 232)
    bordo = np.concatenate([bg[:3].ravel(), bg[-3:].ravel(), bg[:, :3].ravel(), bg[:, -3:].ravel()])
    fondo = bordo.mean()
    fg = a[~bg][:, :3]
    pieno = len(fg) / bg.size
    if pieno < 0.03:
        return 0
    q = (fg[:, 0] >> 4) * 256 + (fg[:, 1] >> 4) * 16 + (fg[:, 2] >> 4)
    conta = np.sort(np.bincount(q))[::-1]
    piatti = conta[:12].sum() / len(fg)                  # parte del disegno coperta dai 12 colori principali
    # verde solo se TUTTE e due le cose vanno bene (prima la media lasciava passare sfumature su fondo bianco)
    v = 2 if fondo >= 0.8 and piatti >= 0.8 else 1 if fondo >= 0.5 and piatti >= 0.55 else 0
    if pieno < 0.12:                                     # solo linee sottili o disegno pallido: si ricama, ma è un contorno
        v = min(v, 1)
    return v


_VOTI = {}


def _scarica_qualsiasi(url, timeout=12):
    """Alcuni siti vogliono un browser (SVG Repo), altri rifiutano i finti browser (Bulbapedia): provo tutti e due."""
    try:
        return _get(url, timeout=timeout)
    except Exception:
        return _get(url, timeout=timeout, headers=UA_SITO)


def _voto_url(url):
    if url not in _VOTI:
        try:
            _VOTI[url] = voto(_scarica_qualsiasi(url, 8))
        except Exception:
            _VOTI[url] = 1
    return _VOTI[url]


def commons(q, pagina=0):
    r = json.loads(_get("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 40,
        "gsroffset": pagina * 40, "gsrsearch": f"{q} filemime:image/svg+xml", "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": 160}))
    out = []
    for p in sorted((r.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        tit = p.get("title", "")
        if COMMONS_NO.search(tit):
            continue
        ii = (p.get("imageinfo") or [{}])[0]
        m = ii.get("extmetadata") or {}
        aut = re.sub(r"<[^>]+>", "", html.unescape((m.get("Artist") or {}).get("value", ""))).strip()[:80]
        if not ii.get("url"):
            continue
        out.append({"fonte": "Wikimedia", "id": tit, "titolo": tit.removeprefix("File:").removesuffix(".svg"),
                    "licenza": (m.get("LicenseShortName") or {}).get("value", "?"), "autore": aut,
                    "mini": ii.get("thumburl") or ii["url"], "svg": ii["url"], "pagina": ii.get("descriptionurl", "")})
    return out


VUOTE = {"a", "an", "the", "with", "of", "and", "in", "on", "for", "to", "at", "by", "from", "his", "her", "its"}


def _prova(f, q):
    try:
        return f(q)
    except Exception:
        return []


def cerca(q, pagina=0, altre=False, tutte=False, ovunque=False):
    """Cerca nelle fonti in parallelo. Restituisce {"q", "en", "trad", "risultati": [...], "errori": {fonte: msg},
    "pixabay": bool}. Ogni risultato ha "voto" 0/1/2 (adatta al ricamo) e "tipo" "svg" o "img"."""
    en, come = traduci(q)
    qi = " ".join(q.lower().split())
    chiave = (en, qi, pagina, altre, tutte, ovunque)
    if chiave in _CACHE_RIC:
        return dict(_CACHE_RIC[chiave], q=q, trad=come)
    fonti = {"Iconify": lambda: iconify(en, pagina, tutte), "Pixabay": lambda: pixabay(qi, pagina),
             "SVG Repo": lambda: svgrepo(en, pagina), "OpenClipart": lambda: openclipart(en, pagina)}
    parole = set(re.findall(r"[\wé]+", (en + " " + qi).lower()))
    if pagina == 0 and (ovunque or any(parole & set(w[2].split()) for w in WIKI_FAN)):
        fonti = {"Wiki dei fan": lambda: wikifan(en, qi, ovunque), **fonti}
    if pagina == 0 and ovunque:
        fonti = {"Safebooru": lambda: safebooru(en, qi if come in ("modello", "mymemory") else ""), **fonti}
    if ovunque and chiave_brave():
        fonti = {"Ovunque": lambda: brave(qi, pagina), **fonti}
    if altre:
        fonti["Wikimedia"] = lambda: commons(en, pagina)
    ris, err = {}, {}
    with ThreadPoolExecutor(len(fonti)) as ex:
        fut = {n: ex.submit(_in_cache, [n, en, qi, pagina, tutte], f) for n, f in fonti.items()}
        for n, f in fut.items():
            try:
                ris[n] = f.result()
            except Exception as e:
                ris[n], err[n] = [], str(e)[:120]
    lista = [x for n in fonti for x in ris[n]]
    for x in lista:                                      # solo questi indirizzi si potranno scaricare dal server
        if x["fonte"] == "Ovunque" or x.get("licenza", "").startswith("personaggio"):
            _BRAVE_OK.update(u for u in (x.get("grande"), x.get("svg"), x.get("mini")) if u)
    parole = [p for p in re.split(r"\s+", en) if p not in VUOTE]
    if pagina == 0 and len(lista) < 24 and len(parole) > 1:
        # frase troppo precisa («teddy bear with balloon»): cerco anche le singole parole e le coppie
        pezzi = [" ".join(parole[i:i + 2]) for i in range(len(parole) - 1)] + parole
        visti = {(x["fonte"], x["id"]) for x in lista}
        with ThreadPoolExecutor(4) as ex:
            for extra in ex.map(lambda s: _prova(iconify, s) + _prova(openclipart, s), pezzi):
                for x in extra:
                    if (x["fonte"], x["id"]) not in visti:
                        visti.add((x["fonte"], x["id"]))
                        lista.append(x)
    # voto «adatta al ricamo» dalle miniature raster (le miniature SVG di Iconify/SVG Repo sono già disegni piatti)
    raster = [x for x in lista if not x["mini"].split("?")[0].endswith(".svg")]
    with ThreadPoolExecutor(12) as ex:
        for x, v in zip(raster, ex.map(lambda x: _voto_url(x["mini"]), raster)):
            x["voto"] = v
    ordine = list(fonti)
    pos = {}
    for x in lista:
        x.setdefault("voto", 2)
        x.setdefault("tipo", "svg")
        pos[id(x)] = pos.setdefault(x["fonte"], -1) + 1
        pos[x["fonte"]] = pos[id(x)]
    # prima i verdi, poi i gialli, poi i rossi; dentro ogni gruppo le fonti si alternano
    lista.sort(key=lambda x: (-x["voto"], pos[id(x)], ordine.index(x["fonte"]) if x["fonte"] in ordine else 9))
    out = {"q": q, "en": en, "trad": come, "risultati": lista[:400], "errori": err,
           "pixabay": any(x["fonte"] == "Pixabay" for x in lista), "ovunque_ok": bool(chiave_brave())}
    if not err:
        _CACHE_RIC[chiave] = out
    return out


def scarica(url):
    """Scarica un SVG da una delle fonti ammesse (per la pagina: stessa origine, così il canvas non è «sporco»)."""
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or (u.hostname not in HOST_OK and url not in _BRAVE_OK):
        raise ValueError("fonte non ammessa")
    raw = _scarica_qualsiasi(url, 25)
    if len(raw) > 15_000_000:
        raise ValueError("SVG troppo grande")
    if b"<svg" not in raw[:5000].lower() and b"<svg" not in raw.lower():
        raise ValueError("il file scaricato non è un SVG")
    # niente script né gestori di eventi: l'SVG viene solo disegnato
    raw = re.sub(rb"<script\b.*?</script\s*>", b"", raw, flags=re.S | re.I)
    raw = re.sub(rb"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", b"", raw, flags=re.I)
    return raw


def scarica_img(url):
    """Scarica un'immagine (Pixabay) sul nostro server prima di usarla: (byte, content-type)."""
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or (u.hostname not in HOST_IMG and url not in _BRAVE_OK):
        raise ValueError("fonte non ammessa")
    if url in _BRAVE_OK:                                 # sito qualsiasi: mai indirizzi della rete di casa
        import ipaddress
        import socket
        for info in socket.getaddrinfo(u.hostname, 443):
            if not ipaddress.ip_address(info[4][0]).is_global:
                raise ValueError("indirizzo non ammesso")
    for intest in (UA, UA_SITO):                         # vedi _scarica_qualsiasi
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=intest), timeout=25) as r:
                ct = (r.headers.get("Content-Type") or "").split(";")[0].strip()
                raw = r.read(25_000_001)
            break
        except urllib.error.HTTPError:
            if intest is UA_SITO:
                raise
    if len(raw) > 25_000_000:
        raise ValueError("immagine troppo grande")
    if ct not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        raise ValueError("il file scaricato non è un'immagine")
    return raw, ct


if __name__ == "__main__":
    import sys
    r = cerca(" ".join(sys.argv[1:]) or "volpe", altre=True)
    print(r["q"], "->", r["en"], f"({r['trad']})", r["errori"])
    from collections import Counter
    print(Counter(x["fonte"] for x in r["risultati"]))
    for x in r["risultati"][:5]:
        print(" ", x["fonte"], x["titolo"], x["licenza"])
