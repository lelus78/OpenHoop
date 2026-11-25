"""Impostazioni di OpenHoop per l'utente: %APPDATA%\\OpenHoop\\config.json (o OPENHOOP_CONFIG per le prove).

Campi:
  lingua        "it" | "en" | "fr" | "es"
  sorgenti      [cartelle dove stanno i ricami di partenza]   (non si modificano mai)
  archivio      cartella dove OpenHoop mette i disegni riordinati (+ _catalogo con i dati)
  marca         nome in macchine.MARCHE
  formato       ".pes", ".jef", ".vp3", ".exp", ".xxx", ".dst"… (file per la macchina)
  telai         [[larghezza, altezza] mm]
  taglia_salti  True se la macchina taglia da sola i fili dei salti
  unita         "mm" | "in"
  fili          marca di fili predefinita (fili.marche())
  pronto        True quando il primo riordino è finito
"""
import json
import os

CARTELLA = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "OpenHoop")
FILE = os.environ.get("OPENHOOP_CONFIG") or os.path.join(CARTELLA, "config.json")

PREDEFINITE = {"lingua": "it", "sorgenti": [], "archivio": "", "marca": "Brother", "formato": ".pes",
               "telai": [[100, 100], [130, 180], [160, 260]], "taglia_salti": False, "unita": "mm",
               "fili": "Brothread 80", "pronto": False}


def leggi():
    try:
        c = json.load(open(FILE, encoding="utf-8"))
    except (OSError, ValueError):
        c = {}
    return dict(PREDEFINITE, **c)


def esiste():
    return os.path.exists(FILE)


def salva(c):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(c, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)
