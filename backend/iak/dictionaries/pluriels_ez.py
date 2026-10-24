# From Prescel (https://github.com/fhoudebert/prescel, dictionnaires/pluriels_ez.py,
# commit c91149f, MIT licence, by the same author), unchanged below.
# Pluriels anciens en -ez et leur forme moderne.
# Complète la règle « -ez → -és » d'epub_modernise.py --mode ez : utile quand la
# forme moderne n'est pas en -és (« excez » → « excès »), quand un accent manque
# (« extremitez » → « extrémités ») ou quand l'OCR a lu un s long « f ».
# Les occurrences précédées de « vous » restent gardées (« vous suez »).

PLURIELS_EZ = {
    "curiofitez": "curiosités",
    "curiositez": "curiosités",
    "divinitez": "divinités",
    "excez": "excès",
    "progrez": "progrès",
    "extremitez": "extrémités",
    "suez": "suées",
    "austeritez": "austérités",
    "liberalitez": "libéralités",
    "séveritez": "sévérités",
    "severitez": "sévérités",
    "paffionnez": "passionnés",
    "passionnez": "passionnés",
}
