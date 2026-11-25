"""Correzione per i PES scritti da pyembroidery, da importare prima di ogni write_pes.

Nella sezione PEC ogni colore prende un numero della tabella Brother da 64 voci: è quello che la Innov-is usa per
mostrare «515 Embroidery». pyembroidery può dare anche il n. 64, che NON è un filo ma «Applique» (rosa 255,200,200):
la macchina allora mostra il simbolo dell'applicazione (zig-zag su linea) al posto del numero (bimba, 23/11/2026).
Qui il 64 viene tolto dalla tabella: i rosa/pelle vanno sul filo vero più vicino (57 Flesh Pink, ecc.).
"""
import pyembroidery.EmbThreadPec as _tp
import pyembroidery.PecWriter as _pw
import pyembroidery.PesWriter as _psw

VIETATI = (64,)                      # 64 = Applique


def _tabella_senza_applique(_orig=_tp.get_thread_set):
    s = _orig()
    for i in VIETATI:
        if i < len(s):
            s[i] = None
    return s


if not getattr(_pw, "_senza_applique", False):
    _pw.get_thread_set = _tabella_senza_applique
    _psw.get_thread_set = _tabella_senza_applique
    _pw._senza_applique = True


def indici_pec(percorso):
    """Numeri della tabella Brother scritti nella sezione PEC di un .pes (per controllo)."""
    import struct
    b = open(percorso, "rb").read()
    o = struct.unpack("<I", b[8:12])[0]
    n = b[o + 48] + 1
    return list(b[o + 49:o + 49 + n])
