"""Marche di macchine da ricamo: formato del file che leggono e telai più comuni (mm, larghezza × altezza).

I telai sono un punto di partenza: ognuno li corregge nelle impostazioni (modelli diversi della stessa marca
hanno telai diversi). Il catalogo tiene sempre i disegni in PES (formato di lavoro); la marca decide solo il
formato con cui escono verso la macchina (esportazione su chiavetta o cartella).

Formati che pyembroidery sa SCRIVERE: pes, jef, vp3, exp, xxx, dst, u01, tbf. HUS non si scrive: le Husqvarna
moderne leggono VP3.
"""

MARCHE = {
    "Brother": {"formato": ".pes", "telai": [[100, 100], [130, 180], [160, 260], [200, 300]],
                "nota": "Innov-is, PE, NV, Stellaire…"},
    "Baby Lock": {"formato": ".pes", "telai": [[100, 100], [130, 180], [160, 260], [200, 300]]},
    "Janome": {"formato": ".jef", "telai": [[50, 50], [110, 126], [140, 140], [140, 200], [200, 280]],
               "nota": "Memory Craft, Skyline…"},
    "Elna": {"formato": ".jef", "telai": [[50, 50], [110, 126], [140, 140], [140, 200], [200, 280]]},
    "Husqvarna Viking": {"formato": ".vp3", "telai": [[100, 100], [150, 150], [150, 240], [200, 260], [200, 360]]},
    "Pfaff": {"formato": ".vp3", "telai": [[100, 100], [115, 120], [150, 150], [200, 260], [200, 360]]},
    "Bernina": {"formato": ".exp", "telai": [[50, 72], [100, 130], [145, 255], [260, 400]],
                "nota": "file .exp (il formato .art non si può scrivere)"},
    "Bernette": {"formato": ".exp", "telai": [[100, 100], [150, 240], [170, 260]]},
    "Singer": {"formato": ".xxx", "telai": [[100, 100], [150, 150], [170, 260]]},
    "Multiago / professionale (Tajima, Ricoma, Happy…)": {"formato": ".dst",
                                                         "telai": [[100, 100], [150, 150], [200, 200], [300, 300]]},
    "Altro": {"formato": ".dst", "telai": [[100, 100], [130, 180], [160, 260]]},
}

FORMATI = {".pes": "PES (Brother, Baby Lock)", ".jef": "JEF (Janome, Elna)", ".vp3": "VP3 (Husqvarna, Pfaff)",
           ".exp": "EXP (Bernina, Bernette)", ".xxx": "XXX (Singer)", ".dst": "DST (Tajima e quasi tutte le macchine)",
           ".u01": "U01 (Barudan)"}


def elenco():
    """Per la pagina delle impostazioni."""
    return {"marche": [dict(v, nome=k) for k, v in MARCHE.items()], "formati": FORMATI}
