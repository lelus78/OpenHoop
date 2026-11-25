"""Autore / ditta di un disegno, ricavato dal nome della raccolta (i file non lo contengono).

Ordine = priorità: la prima marca trovata nel percorso vince. Aggiungere qui nuove marche.
"""
import re

MARCHE = [
    ("Anita Goodesign", r"anita"),
    ("OESD", r"oesd"),
    ("Dakota Collectibles", r"dakota"),
    ("Designs by Sick", r"designs ?by ?sick"),
    ("A Stitch A Half", r"\basah\b|stitch a half"),
    ("Bunnycup", r"bunnycup"),
    ("Embroiders Eye", r"embroiders eye"),
    ("Embroidery Library", r"embroidery library|emblibrary"),
    ("Urban Threads", r"urban threads"),
    ("Great Notions", r"great notions"),
    ("Cactus Punch", r"cactus punch"),
    ("Amazing Designs", r"amazing ?designs?"),
    ("Sudberry House", r"sudberry"),
    ("Kimberbell", r"kimberbell"),
    ("Designs by JuJu", r"juju"),
    ("Lindee G", r"lindee"),
    ("Zundt", r"zundt"),
    ("Floriani", r"floriani"),
    ("Martha Pullen", r"martha pullen"),
    ("Embroidery Garden", r"embroidery garden"),
    ("Balboa Threadworks", r"balboa"),
    ("Smartneedle", r"smartneedle"),
    ("Sweet Pea", r"sweet ?pea"),
    ("Kreative Kiwi", r"kreative ?kiwi"),
    ("Planet Applique", r"planet ?appliqu"),
    ("Lunch Box Quilts", r"lunch ?box ?quilts"),
    ("Hatched in Africa", r"hatched in africa"),
    ("Applique Market", r"applique ?market"),
    ("Ann the Gran", r"ann ?the ?gran"),
    ("Stitch Delight", r"stitch ?delight"),
    ("Oregon Patchworks", r"oregon ?patch ?works"),
    ("Embroidery Designs", r"embroiderydesigns"),
    ("Big Dreams Embroidery", r"big ?dreams"),
    ("Emily's Embroidery", r"emil(y|ie)'?s ?embroidery"),
    ("Hoopsisters", r"hoop ?sisters"),
    ("Pickle Pie Designs", r"pickle ?pie"),
    ("Marjorie Busby", r"busby"),
    ("Starbird", r"starbird"),
    ("Atomm / xTool", r"atomm|xtool"),
    ("Husqvarna Viking", r"husqvarna|viking|hus ?cards?|\bhus\b"),
    ("Janome", r"janome"),
    ("Brother", r"brother"),
    ("Bernina", r"bernina"),
    ("Pfaff", r"pfaff"),
    ("Baby Lock", r"baby ?lock"),
    ("Singer", r"singer"),
    ("Elna", r"\belna\b"),
]
_RX = [(n, re.compile(r, re.I)) for n, r in MARCHE]


def raccolta(percorso):
    """Nome della raccolta d'origine (percorso senza la cartella del tema e senza il file)."""
    parti = percorso.replace("/", "\\").split("\\")
    return " / ".join(parti[1:-1])


def autore_di(percorso):
    r = raccolta(percorso)
    for n, rx in _RX:
        if rx.search(r):
            return n
    return ""
