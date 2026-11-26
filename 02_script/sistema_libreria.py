"""OpenHoop: «Sistema la libreria» — corregge l'archivio già fatto senza rifare il riordino né l'importazione.

passi:
  allegati  PDF, istruzioni, tabelle colori (pdf, txt, doc, immagini) rimasti nelle cartelle e negli zip d'origine
            -> copiati nella cartella della raccolta, accanto ai disegni (la scheda li mostra)
  nomi      nomi fatti solo di numeri/misure («4», «45 inch 10») o col formato nel nome («rose_dst», «4 in pes»)
            -> «Baby Dinosaur 4», «rose»
  temi      VARIE (da smistare) messi nel tema giusto guardando l'immagine; raccolte con la misura nel nome
            («Panel kangaroo 5x7» e «… 6x10») unite in una sola. I temi scelti dall'utente non si cambiano.
  doppioni  stesso disegno due volte (misure, punti e distribuzione dei punti uguali): le copie nel cestino di
            OpenHoop (_cestino, recuperabili), si tiene quella nei preferiti/raccolte/diario o fuori da VARIE.

Le posizioni dei disegni nel catalogo non cambiano mai (impronte, miniature e varianti sono per posizione): cambiano
solo percorsi e temi, aggiornati in tutti i file del catalogo. «applica» tiene una copia dei file del catalogo e un
diario delle mosse: «annulla» rimette tutto com'era.

uso: python sistema_libreria.py <archivio> anteprima|applica [passi=allegati,nomi,temi,doppioni]
     python sistema_libreria.py <archivio> annulla
"""
import json
import os
import re
import shutil
import sys
import time
import zipfile
from collections import Counter, defaultdict

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)
import riordina as RI  # noqa: E402

ALLEGATI = {".pdf", ".txt", ".rtf", ".doc", ".docx", ".odt", ".htm", ".html", ".jpg", ".jpeg", ".png", ".gif", ".bmp"}
IMMAGINI = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
MAX_ALLEGATO = 20_000_000
FILE_CAT = ("dati.js", "digitalizzati.json", "importati.json", "patch.json", "stato.json", "clip_extra.json",
            "eliminati.json", "importati_sorgenti.json", "temi_auto.json", "colori_varianti.json")
_RX_NOME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def pulisci(nome, maxlen=60):
    return RI.pulisci(nome, maxlen)


def avanza(n, tot, cosa):
    print(f"  {n}/{tot}  {cosa}", flush=True)


def _json(f, vuoto):
    try:
        return json.load(open(f, encoding="utf-8"))
    except (OSError, ValueError):
        return vuoto


def _scrivi_json(f, dati):
    with open(f + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(dati, fh, ensure_ascii=False)
    os.replace(f + ".tmp", f)


class Libreria:
    """Il catalogo come lo vede OpenHoop (dati.js + digitalizzati + importati + toppe) e le mosse sui file."""

    def __init__(self, out, applica=True):
        self.out, self.cat, self.applica = out, os.path.join(out, "_catalogo"), applica
        t = open(os.path.join(self.cat, "dati.js"), encoding="utf-8").read()
        self.testa, resto = t.split("const DATI=", 1)
        self.base = json.loads(resto.rstrip().rstrip(";"))
        self.dati = list(self.base)
        self.extra = {}
        gia = {d[0] for d in self.base}
        for f in ("digitalizzati.json", "importati.json"):
            self.extra[f] = _json(os.path.join(self.cat, f), [])
            for d in self.extra[f]:
                if d[0] not in gia:
                    self.dati.append(d)
                    gia.add(d[0])
        self.patch = _json(os.path.join(self.cat, "patch.json"), [])
        self.toppe = set()
        for p in self.patch:
            if p["voce"][0] not in gia and os.path.exists(os.path.join(out, p["voce"][0])):
                self.dati.append(p["voce"])
                gia.add(p["voce"][0])
                self.toppe.add(len(self.dati) - 1)
        self.eliminati = set(_json(os.path.join(self.cat, "eliminati.json"), []))
        self.stato = _json(os.path.join(self.cat, "stato.json"), {})
        self.mappa = {}                                  # percorso vecchio -> nuovo
        self.occupati, self.liberati = set(), set()      # percorsi (normcase) presi / lasciati dalle mosse
        self.mosse = []                                  # diario per «annulla»
        self.contati = Counter()
        self.esempi = defaultdict(list)
        self.piano = []                                  # anteprima: le mosse da rifare tali e quali con «applica»
        self.errori = []                                 # [passo, cosa, errore]: saltati senza fermarsi

    def errore(self, passo, cosa, e):
        self.errori.append([passo, str(cosa), f"{type(e).__name__}: {e}"])
        if len(self.errori) <= 200:
            print(f"  ERRORE ({passo}) {cosa}: {e}", flush=True)

    # -------- utilità --------
    def vivo(self, i):
        d = self.dati[i]
        return d[0] not in self.eliminati and os.path.exists(os.path.join(self.out, d[0]))

    def importanti(self):
        """Percorsi che l'utente ha toccato: preferiti, già ricamati, raccolte, diario, origini delle toppe."""
        s = set(self.stato.get("pref", [])) | set(self.stato.get("ric", []))
        for v in (self.stato.get("raccolte") or {}).values():
            s |= set(v)
        s |= {e.get("percorso") for e in self.stato.get("diario", []) if isinstance(e, dict)}
        s |= {p.get("origine") for p in self.patch}
        return s

    def occupato(self, rel):
        k = os.path.normcase(rel)
        return k in self.occupati or (os.path.exists(os.path.join(self.out, rel)) and k not in self.liberati)

    def libero(self, rel):
        if not self.occupato(rel):
            return rel
        b, e = os.path.splitext(rel)
        n = 2
        while self.occupato(f"{b} ({n}){e}"):
            n += 1
        return f"{b} ({n}){e}"

    def esempio(self, passo, testo):
        self.contati[passo] += 1
        if len(self.esempi[passo]) < 25:
            self.esempi[passo].append(testo)

    # -------- mosse --------
    def sposta(self, i, nuovo, tema=None, passo=None):
        vecchio = self.dati[i][0]
        if os.path.normcase(nuovo) == os.path.normcase(vecchio):
            if tema:
                self.dati[i][1] = tema
            return vecchio
        nuovo = self.libero(nuovo)
        if self.applica:
            src, dst = os.path.join(self.out, vecchio), os.path.join(self.out, nuovo)
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
            except OSError as e:                                     # file aperto, sparito…: si salta
                self.errore(passo or "sposta", vecchio, e)
                return vecchio
            self.mosse.append(["sposta", vecchio, nuovo])
            st = os.path.splitext(src)[0] + " - stampa.png"          # immagine da stampare del progetto (xTool)
            if os.path.exists(st):
                try:
                    os.replace(st, os.path.splitext(dst)[0] + " - stampa.png")
                    self.mosse.append(["sposta", os.path.splitext(vecchio)[0] + " - stampa.png",
                                       os.path.splitext(nuovo)[0] + " - stampa.png"])
                except OSError as e:
                    self.errore(passo or "sposta", st, e)
            self._via_se_vuota(os.path.dirname(src))
        else:
            self.piano.append([passo or "", i, vecchio, nuovo, tema])
        self.occupati.add(os.path.normcase(nuovo))
        self.liberati.add(os.path.normcase(vecchio))
        self.mappa[vecchio] = nuovo
        self.dati[i][0] = nuovo
        if tema:
            self.dati[i][1] = tema
        if passo:
            self.esempio(passo, [vecchio, nuovo])
        return nuovo

    def _via_se_vuota(self, d):
        radice = os.path.normcase(os.path.abspath(self.out))
        while os.path.normcase(os.path.abspath(d)) != radice:
            try:
                os.rmdir(d)
            except OSError:
                return
            d = os.path.dirname(d)

    def elimina(self, i, motivo=""):
        rel = self.dati[i][0]
        if self.applica:
            dest = os.path.join(self.out, "_cestino", rel)
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.exists(dest):
                    b, e = os.path.splitext(dest)
                    dest = f"{b} ({int(time.time())}){e}"
                os.replace(os.path.join(self.out, rel), dest)
            except OSError as e:
                self.errore("doppioni", rel, e)
                return
            self.mosse.append(["elimina", rel, os.path.relpath(dest, self.out)])
        else:
            self.piano.append(["doppioni", i, rel, motivo])
        self.eliminati.add(rel)
        self.esempio("doppioni", [rel, motivo])

    def allegato(self, leggi, nome, cart_rel, dim, fonte=None):
        """Copia un allegato nella cartella (relativa all'archivio). leggi: percorso di un file o funzione -> byte.
        fonte: [zip, voce] per il piano (se leggi è una funzione)."""
        nome_orig, nome = nome, pulisci(nome, 80)
        dest = os.path.join(cart_rel, nome)
        full = os.path.join(self.out, dest)
        if os.path.exists(full) and os.path.getsize(full) == dim:
            return False                                    # c'è già (sistemazione rifatta)
        dati = None
        if self.applica and not isinstance(leggi, str):
            try:
                dati = leggi()                              # prima di creare il file: zip rovinato = si salta
            except Exception as e:
                self.errore("allegati", fonte and " › ".join(fonte) or nome, e)
                return False
        dest = self.libero(dest)
        self.occupati.add(os.path.normcase(dest))
        if self.applica:
            full = os.path.join(self.out, dest)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if isinstance(leggi, str):
                try:
                    os.link(leggi, full)                    # stesso disco: non occupa spazio
                except OSError:
                    try:
                        shutil.copy2(leggi, full)
                    except OSError as e:
                        self.errore("allegati", leggi, e)
                        return False
            else:
                with open(full, "wb") as fh:
                    fh.write(dati)
            self.mosse.append(["allegato", dest])
        else:
            self.piano.append(["allegati", leggi if isinstance(leggi, str) else fonte, nome_orig, cart_rel, dim])
        return True

    # -------- salvataggio --------
    def copia_sicurezza(self):
        d = os.path.join(self.cat, "sistema_backup")
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for f in FILE_CAT:
            if os.path.exists(os.path.join(self.cat, f)):
                shutil.copy2(os.path.join(self.cat, f), os.path.join(d, f))
        return d

    def salva(self):
        """Percorsi nuovi in tutti i file del catalogo (le posizioni non cambiano)."""
        m = self.mappa
        nuovo = lambda p: m.get(p, p)  # noqa: E731
        temi = {d[0]: d[1] for d in self.dati}           # tema aggiornato (per percorso già nuovo)
        for d in self.base:
            d[0] = nuovo(d[0])
            d[1] = temi.get(d[0], d[1])
        with open(os.path.join(self.cat, "dati.js.tmp"), "w", encoding="utf-8") as fh:
            fh.write(self.testa + "const DATI=" + json.dumps(self.base, ensure_ascii=False, separators=(",", ":")) + ";\n")
        os.replace(os.path.join(self.cat, "dati.js.tmp"), os.path.join(self.cat, "dati.js"))
        for f, lst in self.extra.items():
            if lst:
                for d in lst:
                    d[0] = nuovo(d[0])
                    d[1] = temi.get(d[0], d[1])
                _scrivi_json(os.path.join(self.cat, f), lst)
        if self.patch:
            for p in self.patch:
                p["voce"][0], p["origine"] = nuovo(p["voce"][0]), nuovo(p["origine"])
            _scrivi_json(os.path.join(self.cat, "patch.json"), self.patch)
        if self.stato:
            s = self.stato
            for k in ("pref", "ric"):
                if k in s:
                    s[k] = [nuovo(x) for x in s[k]]
            if "raccolte" in s:
                s["raccolte"] = {k: [nuovo(x) for x in v] for k, v in s["raccolte"].items()}
            for e in s.get("diario", []):
                if isinstance(e, dict) and e.get("percorso"):
                    e["percorso"] = nuovo(e["percorso"])
            _scrivi_json(os.path.join(self.cat, "stato.json"), s)
        f = os.path.join(self.cat, "clip_extra.json")
        if os.path.exists(f):
            _scrivi_json(f, [nuovo(x) for x in _json(f, [])])
        _scrivi_json(os.path.join(self.cat, "eliminati.json"), sorted(nuovo(x) for x in self.eliminati))
        f = os.path.join(self.cat, "importati_sorgenti.json")
        if os.path.exists(f):
            _scrivi_json(f, {k: nuovo(v) for k, v in _json(f, {}).items()})
        for f in ("temi_auto.json", "colori_varianti.json"):
            f = os.path.join(self.cat, f)
            if os.path.exists(f):
                _scrivi_json(f, {nuovo(k): v for k, v in _json(f, {}).items()})
        _scrivi_json(os.path.join(self.cat, "sistema_diario.json"),
                     {"data": time.strftime("%Y-%m-%d %H:%M"), "mosse": self.mosse, "contati": dict(self.contati)})


# ---------------- passo: nomi ----------------
def _etichetta(cart):
    """Cartella della raccolta -> parola utile per i nomi numerici («A - Christmas-Cat» -> «Christmas-Cat»)."""
    for p in reversed(os.path.basename(cart).split(" - ")):
        p = RI.senza_misura(p).strip()
        if p and not re.fullmatch(r"[\d\s_.\-x×()]+", p) and not RI.tecnica(p):
            return p
    return ""


def nome_chiaro(base, cart):
    import importa as IM
    b = re.sub(r"\s\(\d+\)$", "", base)                    # «nome (2)»: il numero di copia resta fuori
    n = IM._nome_pulito(b)
    if len(n) <= 16 and re.fullmatch(r"[\d\s_.\-x×]*(?:in|inch|inches|cm|mm|\")?[\d\s_.\-x×]*", n, re.I) \
            and re.search(r"\d", n):
        e = _etichetta(cart)
        if e:
            n = f"{e} {n}"
    return pulisci(n, 50)


def passo_nomi(L):
    tot = len(L.dati)
    for i, d in enumerate(L.dati):
        if i % 20000 == 0:
            avanza(i, tot, "nomi")
        if i in L.toppe or not L.vivo(i):
            continue
        cart, f = os.path.split(d[0])
        base, ext = os.path.splitext(f)
        nuovo = nome_chiaro(base, cart)
        if nuovo.lower() != re.sub(r"\s\(\d+\)$", "", base).lower():
            L.sposta(i, os.path.join(cart, nuovo + ext), passo="nomi")


# ---------------- passo: temi e raccolte ----------------
def raccolta_pulita(r):
    """«Panel 2 kangaroo 5x7» -> «Panel 2 kangaroo»; «Coll X - PES» -> «Coll X»; vuota se resta solo rumore."""
    parti = []
    for p in r.split(" - "):
        q = RI.senza_misura(p).strip()
        if q and not RI.RUMORE_DIR.match(q) and not RI.tecnica(q) and (not parti or parti[-1].lower() != q.lower()):
            parti.append(q)
    return pulisci(" - ".join(parti)) if parti else ""


def passo_temi(L):
    import temi_immagine as TI
    print("smisto i VARIE guardando l'immagine…", flush=True)
    try:
        prop = TI.proposte(L.out, L.dati)
    except Exception as e:                               # niente impronte: si salta solo questa parte
        print("VARIE non smistati:", e, flush=True)
        prop = []
    for k, (i, tema) in enumerate(prop):
        if k % 2000 == 0:
            avanza(k, len(prop), "VARIE")
        if i in L.toppe or not L.vivo(i):
            continue
        parti = L.dati[i][0].replace("/", "\\").split("\\")
        L.sposta(i, os.path.join(tema, *parti[1:]), tema=tema, passo="temi")
    tot = len(L.dati)
    for i, d in enumerate(L.dati):
        if i % 20000 == 0:
            avanza(i, tot, "raccolte")
        if i in L.toppe or not L.vivo(i):
            continue
        parti = d[0].replace("/", "\\").split("\\")
        if len(parti) != 3:
            continue
        tema, racc, f = parti
        nuova = raccolta_pulita(racc)
        if not nuova or nuova.lower() == racc.lower():
            continue
        dest = os.path.join(tema, nuova, f)
        if L.occupato(dest):                                # stesso nome in due misure: la misura va nel nome
            m = RI.MISURA.search(racc)
            if m:
                b, e = os.path.splitext(f)
                dest = os.path.join(tema, nuova, pulisci(f"{b} {m.group(0).strip(' -_().')}", 50) + e)
        L.sposta(i, dest, passo="raccolte")


# ---------------- passo: doppioni ----------------
def _firma(rel_out):
    """(firma grossa 8×8 del riordino, griglia fine 32×32 dei punti): la seconda distingue disegni con lo stesso
    impaginato ma contenuto diverso (scritte diverse della stessa serie, «Christmas Wishes 1…16»)."""
    import pyembroidery
    import importa as IM
    try:
        pat = pyembroidery.read(rel_out)
        pts = [(s[0], s[1]) for s in pat.stitches if (s[2] & 0xFF) == 0]
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, y0 = min(xs), min(ys)
        w, h = max(max(xs) - x0, 1), max(max(ys) - y0, 1)
        celle = {(min(31, int(32 * (x - x0) / w)), min(31, int(32 * (y - y0) / h))) for x, y in pts}
        return IM.firma(pat), frozenset(celle)
    except Exception:
        return None


def stessi_colori(a, b):
    """Stessa sequenza di fili (±piccole differenze di tabella colori tra formati). Disegni diversi della stessa
    misura standard (es. tutti alti 7 pollici) hanno spesso punti e sagoma simili ma colori diversi; anche le
    varianti di colore dello stesso disegno non sono doppioni."""
    sa, sb = (a[7] if len(a) > 7 else []), (b[7] if len(b) > 7 else [])
    if not sa or not sb:
        return True                                     # niente sequenza nel catalogo: decide la firma
    if len(sa) != len(sb):
        return False

    def rgb(h):
        return [int(h[k:k + 2], 16) for k in (1, 3, 5)] if isinstance(h, str) and len(h) == 7 else [0, 0, 0]
    vicini = sum(1 for x, y in zip(sa, sb) if sum((p - q) ** 2 for p, q in zip(rgb(x), rgb(y))) ** 0.5 < 80)
    return vicini >= 0.8 * len(sa)


def passo_doppioni(L):
    from concurrent.futures import ProcessPoolExecutor
    vivi = [i for i in range(len(L.dati)) if i not in L.toppe and L.vivo(i)]
    per_misura = defaultdict(list)
    for i in vivi:
        d = L.dati[i]
        per_misura[(round(d[2]), round(d[3]))].append(i)
    # candidati: stesso riquadro (±1 mm) e punti entro il 3% di un altro disegno
    cand = set()
    for (w, h), idx in per_misura.items():
        vicini = [j for dw in (-1, 0, 1) for dh in (-1, 0, 1) for j in per_misura.get((w + dw, h + dh), ())]
        if len(vicini) < 2:
            continue
        for i in idx:
            pi = L.dati[i][5]
            if any(j != i and abs(L.dati[j][5] - pi) <= 0.03 * max(pi, L.dati[j][5]) for j in vicini):
                cand.add(i)
    cand = sorted(cand)
    print(f"doppioni: {len(cand)} disegni da confrontare", flush=True)
    firme = {}
    with ProcessPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 2)) as ex:
        for k, (i, f) in enumerate(zip(cand, ex.map(_firma, [os.path.join(L.out, L.dati[i][0]) for i in cand],
                                                    chunksize=64))):
            if k % 2000 == 0:
                avanza(k, len(cand), "confronto dei doppioni")
            if f:
                firme[i] = f
    imp = L.importanti()
    varie = {"VARIE (da smistare)"}

    try:
        import config
        pref = config.leggi().get("formato") or ".pes"
    except Exception:
        pref = ".pes"

    def punteggio(i):
        d = L.dati[i]
        # tieni: quella che l'utente usa, fuori da VARIE, nata nel formato della sua macchina (colori pensati per i
        # suoi fili; le convertite da DST hanno spesso i salti visibili), nata in PES, dell'archivio di partenza,
        # col percorso più corto
        f = d[6] if len(d) > 6 else ".pes"
        return (d[0] in imp, d[1] not in varie and "VARIE" not in d[1].upper(), f == pref, f == ".pes",
                i < len(L.base), -len(d[0]), -i)
    padre = {}

    def radice(i):
        while padre.get(i, i) != i:
            i = padre[i]
        return i
    for (w, h), idx in per_misura.items():
        vicini = [j for dw in (-1, 0, 1) for dh in (-1, 0, 1) for j in per_misura.get((w + dw, h + dh), ()) if j in firme]
        for i in idx:
            if i not in firme:
                continue
            for j in vicini:
                if j <= i:
                    continue
                a, b = L.dati[i], L.dati[j]
                fi, fj = firme[i], firme[j]
                if abs(a[5] - b[5]) <= 0.03 * max(a[5], b[5]) and stessi_colori(a, b) and \
                        sum(abs(x - y) for x, y in zip(fi[0], fj[0])) < 0.08 and \
                        len(fi[1] & fj[1]) >= 0.9 * len(fi[1] | fj[1]):
                    padre[radice(j)] = radice(i)
    gruppi = defaultdict(list)
    for i in firme:
        gruppi[radice(i)].append(i)
    for g in gruppi.values():
        if len(g) < 2:
            continue
        tieni = max(g, key=punteggio)
        for i in g:
            if i != tieni:
                L.elimina(i, L.dati[tieni][0])


# ---------------- passo: allegati ----------------
def _chiave_nome(b):
    b = re.sub(r"\s\(\d+\)$", "", os.path.splitext(b)[0])
    return re.sub(r"\s+", " ", _RX_NOME.sub("_", b))[:50].strip(" .").lower()


def _bersagli(nomi, indice, parti=None, per_cart=None):
    """Nomi dei disegni di una cartella d'origine -> cartelle dell'archivio dove sono finiti (voto a maggioranza).
    Se i nomi non bastano (disegni importati e rinominati, «4 in pes» -> «Baby Dinosaur 4 in»): la cartella
    dell'archivio col nome della raccolta, fatto come nel riordino e nell'importazione (ultime 2 cartelle)."""
    v = _voto(nomi, indice)
    if v or not parti or per_cart is None:
        return v
    utili = [p for p in parti if p and not RI.tecnica(p)]
    for cand in (" - ".join(utili[-2:]), utili[-1] if utili else "",
                 " - ".join(RI.senza_misura(p) for p in utili[-2:])):
        c = per_cart.get(pulisci(cand).lower()) if cand else None
        if c and len(c) <= 3:
            return sorted(c)
    if utili:                                             # PDF nella cartella sopra le raccolte («Baby Dinosaur»
        pre = pulisci(utili[-1]).lower() + " - "          # -> «Baby Dinosaur - 1», «Baby Dinosaur - 2»…)
        c = sorted(x for k, v in per_cart.items() if k.startswith(pre) for x in v)
        if 0 < len(c) <= 12:
            return c
    return []


def _voto(nomi, indice):
    conta = Counter()
    for n in nomi:
        for c in indice.get(_chiave_nome(n), ()):
            conta[c] += 1
    if not conta:
        return []
    soglia = max(1, 0.3 * len(nomi)) if len(nomi) > 1 else 1
    return [c for c, k in conta.most_common(3) if k >= soglia and (len(nomi) > 1 or len(indice.get(_chiave_nome(nomi[0]), ())) == 1)]


def _scegli_allegati(files, nomi_disegni):
    """files: [(nome, dimensione)] -> quelli da tenere (PDF/testi sempre; immagini solo se poche e non anteprime)."""
    disegni = {_chiave_nome(n) for n in nomi_disegni}
    imm = [f for f in files if os.path.splitext(f[0])[1].lower() in IMMAGINI]
    out = []
    for n, dim in files:
        e = os.path.splitext(n)[1].lower()
        if e not in ALLEGATI or dim > MAX_ALLEGATO or dim == 0:
            continue
        if e in IMMAGINI and (len(imm) > 10 or _chiave_nome(n) in disegni):
            continue                                      # anteprime di ogni disegno: non sono istruzioni
        out.append((n, dim))
    return out


def passo_allegati(L, sorgenti):
    indice = defaultdict(set)                             # nome del disegno -> cartelle dell'archivio
    for i, d in enumerate(L.dati):
        if i not in L.toppe:
            cart, f = os.path.split(d[0])
            indice[_chiave_nome(f)].add(cart)
    cartelle, zips, parti_di = {}, [], {}
    per_cart = defaultdict(set)                           # nome della cartella della raccolta -> cartelle archivio
    for i, d in enumerate(L.dati):
        if i not in L.toppe:
            c = os.path.dirname(d[0])
            per_cart[os.path.basename(c).lower()].add(c)
    n_file = 0
    for base in sorgenti:
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if not RI.SALTA_DIR.match(x)]
            dis, all_ = [], []
            for f in fn:
                n_file += 1
                if n_file % 50000 == 0:
                    print(f"  letti {n_file} file delle cartelle d'origine…", flush=True)
                e = os.path.splitext(f)[1].lower()
                if e in RI.PRIO:
                    dis.append(f)
                elif e == ".zip":
                    zips.append(os.path.join(dp, f))
                elif e in ALLEGATI:
                    try:
                        all_.append((f, os.path.getsize(os.path.join(dp, f))))
                    except OSError:
                        pass
            cartelle[dp] = (dis, all_)
            parti_di[dp] = os.path.relpath(dp, os.path.dirname(os.path.abspath(base))).split(os.sep)[1:]

    def disegni_vicini(dp):
        """Cartella senza disegni (es. «Istruzioni»): si guardano la cartella sopra e le sue sottocartelle."""
        dis, _ = cartelle[dp]
        if dis:
            return dis
        su = os.path.dirname(dp)
        tutti = []
        for c, (d2, _a) in cartelle.items():
            if c == su or os.path.dirname(c) == su:
                tutti += d2
            if len(tutti) > 500:
                break
        return tutti
    con = [c for c, (_d, a) in cartelle.items() if a]
    for k, dp in enumerate(con):
        if k % 500 == 0:
            avanza(k, len(con) + len(zips), "allegati nelle cartelle")
        nomi = disegni_vicini(dp)
        scelti = _scegli_allegati(cartelle[dp][1], nomi)
        if not scelti or not nomi:
            continue
        for cart in _bersagli(nomi, indice, parti_di.get(dp), per_cart):
            for n, dim in scelti:
                if L.allegato(os.path.join(dp, n), n, cart, dim):
                    L.esempio("allegati", [os.path.join(cart, pulisci(n, 80)), os.path.join(dp, n)])
    for k, zp in enumerate(zips):
        if k % 200 == 0:
            avanza(len(con) + k, len(con) + len(zips), "allegati negli zip")
        try:
            z = zipfile.ZipFile(zp)
            voci = [x for x in z.infolist() if not x.is_dir() and "__macosx" not in x.filename.lower()]
        except (OSError, zipfile.BadZipFile, ValueError):
            continue
        per_int = defaultdict(lambda: ([], []))          # cartella dentro lo zip -> (disegni, allegati)
        for x in voci:
            c, f = os.path.split(x.filename.replace("\\", "/"))
            e = os.path.splitext(f)[1].lower()
            if e in RI.PRIO:
                per_int[c][0].append(f)
            elif e in ALLEGATI:
                per_int[c][1].append((f, x.file_size, x))
        tutti_dis = [f for d, _a in per_int.values() for f in d]
        for c, (dis, all_) in per_int.items():
            if not all_:
                continue
            nomi = dis or tutti_dis
            scelti = {n for n, _ in _scegli_allegati([(n, s) for n, s, _ in all_], nomi)}
            if not scelti or not nomi:
                continue
            parti = [os.path.splitext(os.path.basename(zp))[0]] + [p for p in c.split("/") if p]
            for cart in _bersagli(nomi, indice, parti, per_cart):
                for n, dim, x in all_:
                    if n in scelti and L.allegato(lambda x=x: z.read(x), n, cart, dim, [zp, x.filename]):
                        L.esempio("allegati", [os.path.join(cart, pulisci(n, 80)), f"{zp} › {x.filename}"])


# ---------------- annulla ----------------
def annulla(out):
    cat = os.path.join(out, "_catalogo")
    diario = _json(os.path.join(cat, "sistema_diario.json"), None)
    if not diario:
        print("FINE: niente da annullare")
        return
    mosse = diario["mosse"]
    for k, m in enumerate(reversed(mosse)):
        if k % 5000 == 0:
            avanza(k, len(mosse), "annullo")
        try:
            if m[0] == "sposta":
                src, dst = os.path.join(out, m[2]), os.path.join(out, m[1])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
            elif m[0] == "elimina":
                src, dst = os.path.join(out, m[2]), os.path.join(out, m[1])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
            elif m[0] == "allegato":
                os.remove(os.path.join(out, m[1]))
        except OSError as e:
            print("non annullato:", m, e, flush=True)
    radice = os.path.normcase(os.path.abspath(out))
    for m in mosse:                                       # cartelle create dalla sistemazione e rimaste vuote
        d = os.path.dirname(os.path.join(out, m[2] if m[0] != "allegato" else m[1]))
        while os.path.normcase(os.path.abspath(d)) != radice:
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)
    bk = os.path.join(cat, "sistema_backup")
    for f in FILE_CAT:
        if os.path.exists(os.path.join(bk, f)):
            shutil.copy2(os.path.join(bk, f), os.path.join(cat, f))
    os.replace(os.path.join(cat, "sistema_diario.json"), os.path.join(cat, "sistema_diario_annullato.json"))
    print(f"FINE: annullate {len(mosse)} modifiche, catalogo rimesso com'era il {diario.get('data', '?')}")


# ---------------- main ----------------
def _impronta(cat):
    """Dimensione e data dei file del catalogo: se cambiano, l'elenco dell'anteprima non vale più."""
    imp = []
    for f in ("dati.js", "digitalizzati.json", "importati.json", "patch.json", "eliminati.json"):
        try:
            st = os.stat(os.path.join(cat, f))
            imp.append([f, st.st_size, int(st.st_mtime)])
        except OSError:
            imp.append([f, 0, 0])
    return imp


def esegui_piano(L, ops, passi):
    """Rifà le mosse dell'anteprima. Una mossa che non si può più fare si salta e finisce negli errori."""
    z_aperto = [None, None]                                  # [percorso, ZipFile]: gli zip sono in fila
    tot = len(ops)
    for k, op in enumerate(ops):
        if k % 2000 == 0:
            avanza(k, tot, "mosse")
        passo = op[0]
        if ("temi" if passo == "raccolte" else passo) not in passi:
            continue
        try:
            if passo == "allegati":
                _, fonte, nome, cart, dim = op
                if isinstance(fonte, str):
                    leggi = fonte
                else:
                    if z_aperto[0] != fonte[0]:
                        z_aperto[:] = [fonte[0], zipfile.ZipFile(fonte[0])]
                    leggi = lambda z=z_aperto[1], m=fonte[1]: z.read(m)  # noqa: E731
                if L.allegato(leggi, nome, cart, dim, fonte if not isinstance(fonte, str) else None):
                    L.esempio("allegati", [os.path.join(cart, pulisci(nome, 80)),
                                           fonte if isinstance(fonte, str) else " › ".join(fonte)])
            elif passo == "doppioni":
                _, i, rel, motivo = op
                if L.dati[i][0] != rel or not L.vivo(i):
                    raise FileNotFoundError("non c'è più")
                L.elimina(i, motivo)
            else:
                _, i, vecchio, nuovo, tema = op
                cur = L.dati[i][0]
                if passo == "nomi":                          # solo il nome: vale anche se i temi non si fanno
                    nuovo = os.path.join(os.path.dirname(cur), os.path.basename(nuovo))
                elif cur != vecchio:
                    raise FileNotFoundError("spostato nel frattempo")
                if not L.vivo(i):
                    raise FileNotFoundError("non c'è più")
                L.sposta(i, nuovo, tema=tema, passo=passo)
        except Exception as e:
            L.errore(passo, op[2] if passo != "allegati" else op[2], e)


def main():
    out, modo = sys.argv[1], sys.argv[2]
    passi = next((a.split("=", 1)[1].split(",") for a in sys.argv[3:] if a.startswith("passi=")),
                 ["allegati", "nomi", "temi", "doppioni"])
    if modo == "annulla":
        annulla(out)
        return
    applica = modo == "applica"
    cat = os.path.join(out, "_catalogo")
    f_piano = os.path.join(cat, "sistema_piano.json")
    impronta = _impronta(cat)
    piano = _json(f_piano, None) if applica else None
    if piano and (piano.get("impronta") != impronta or not set(passi) <= set(piano.get("passi", []))):
        print("la libreria è cambiata dopo l'anteprima (o ci sono passi nuovi): ricalcolo da capo", flush=True)
        piano = None
    L = Libreria(out, applica=applica)
    if applica:
        L.copia_sicurezza()
    t0 = time.time()
    try:
        if piano:
            print(f"uso l'elenco dell'anteprima del {piano.get('data', '?')}", flush=True)
            esegui_piano(L, piano["ops"], passi)
        else:
            # ordine: allegati (cercati coi nomi d'origine) -> doppioni -> temi -> nomi
            for nome, fn in (("allegati", None), ("doppioni", passo_doppioni), ("temi", passo_temi), ("nomi", passo_nomi)):
                if nome not in passi:
                    continue
                try:
                    if fn is None:
                        import config
                        passo_allegati(L, [s for s in config.leggi().get("sorgenti", []) if os.path.isdir(s)])
                    else:
                        fn(L)
                except Exception as e:                       # un passo che si rompe non ferma gli altri
                    import traceback
                    traceback.print_exc()
                    L.errore(nome, "passo interrotto", e)
    finally:
        if applica:
            L.salva()                                        # sempre: i file già spostati restano nel catalogo
    rapporto = {"modo": modo, "data": time.strftime("%Y-%m-%d %H:%M"), "passi": passi,
                "contati": dict(L.contati), "esempi": dict(L.esempi), "secondi": round(time.time() - t0),
                "errori": len(L.errori), "errori_esempi": L.errori[:100], "dal_piano": bool(piano)}
    _scrivi_json(os.path.join(L.cat, "sistema_rapporto.json"), rapporto)
    if applica:
        try:
            os.remove(f_piano)                               # dopo «applica» l'elenco non vale più
        except OSError:
            pass
    else:
        _scrivi_json(f_piano, {"data": rapporto["data"], "impronta": impronta, "passi": passi, "ops": L.piano})
    c = L.contati
    err = f", {len(L.errori)} errori (saltati)" if L.errori else ""
    print(f"FINE: allegati {c['allegati']}, doppioni {c['doppioni']}, temi {c['temi']}, raccolte {c['raccolte']}, "
          f"nomi {c['nomi']}{err} ({'applicato' if applica else 'anteprima'}, {time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
