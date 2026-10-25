# From Prescel (https://github.com/fhoudebert/prescel, dictionnaires/pluriels_ants.py,
# commit c91149f, MIT licence, by the same author), unchanged below.
# Pluriels anciens en -ans / -ens (le t tombait devant le s du pluriel) et leur
# forme moderne. Complète la règle « -ans → -ants », « -ens → -ents »
# d'epub_modernise.py --mode ants, pour les mots qu'elle ne peut pas deviner
# (accent, s long, autre graphie).

PLURIELS_ANTS = {
    "charmans": "charmants",
    "pressans": "pressants",
    "presens": "présents",
    "prefens": "présents",
    "parens": "parents",
    "differens": "différents",
    "élemens": "éléments",
    "elemens": "éléments",
    "sentimens": "sentiments",
    "commandemens": "commandements",
    "établissemens": "établissements",
    "etablissemens": "établissements",
    "evenemens": "événements",
    "événemens": "événements",
    "negocians": "négociants",
    "négocians": "négociants",
    "penitens": "pénitents",
    "pénitens": "pénitents",
    "residens": "résidents",
    "résidens": "résidents",
}
