# IA-K en bref

IA-K est un assistant documentaire **personnel, hors ligne et portable**. Il
lit vos documents, les rend interrogeables par mots ou par sens, répond à vos
questions en citant ses sources, et traduit textes et livres entiers. Tout se
passe sur votre ordinateur, avec des modèles de langue locaux : aucun document
ne part sur Internet.

Installation : [README.md](README.md) ; en anglais : [FEATURES.md](FEATURES.md).

## Où et comment il fonctionne

- **Portable** : IA-K tient dans un dossier, sur une clé USB ou un disque, et
  fonctionne sous **Windows, Linux et macOS**, y compris depuis la même clé
  exFAT, avec une bibliothèque commune.
- **Sans installation** : on décompresse un seul fichier (`ia-k.zip`, tous
  systèmes) sur la clé, Python, moteur et OCR compris ; rien n'est écrit sur
  l'ordinateur. Seul un Mac Intel demande une fois `setup_python.sh` (et
  Tesseract par Homebrew).
- **Modèles locaux** au format GGUF, servis par llama.cpp (`llama-server`) :
  Gemma 4 pour les réponses, EmbeddingGemma pour la recherche par le sens,
  TranslateGemma pour la traduction, Qwen3-VL pour la lecture des pages.
- **Un fichier par usage** lance les serveurs nécessaires puis IA-K, et les
  arrête à la fermeture : `serveurs-baseConnaissance` (questions),
  `serveurs-traduction`, `serveurs-vl` (relecture des scans).
- **Plusieurs ordinateurs** : chaque ordinateur a sa section de réglages,
  choisie par son nom ; la même clé sert partout.
- Interface en **français et en anglais**. Logiciel libre, licence MIT.

## Bibliothèque

- Import de **PDF, scans, images, captures d'écran, EPUB, DOCX, HTML, TXT et
  Markdown**, par bouton ou glisser-déposer ; copie dans la bibliothèque ou
  simple référence au fichier.
- **OCR** (Tesseract, fourni sur la clé avec le français, l'anglais, le
  moyen français et le latin) des pages scannées, avec la confiance de
  lecture de chaque page ; langues réglables (`fra+eng`, `fra+frm` pour le
  moyen français…).
- **Dossiers** : « Importer un dossier… » (ou déposer un dossier sur la
  fenêtre) référence tous ses documents, sous-dossiers compris, dans une
  collection du nom du dossier. Un dossier **surveillé** est examiné chaque
  minute : les fichiers ajoutés ou modifiés y sont importés tout seuls. Un
  dossier situé sur la clé est retrouvé d'un ordinateur à l'autre, quelle que
  soit la lettre du lecteur.
- **Collections** : des groupes de documents nommés (par sujet, projet,
  fonds…) ; un document peut appartenir à plusieurs collections, passer de
  l'une à l'autre (« Déplacer vers la collection »), ou être rangé depuis la
  visionneuse ; une collection peut limiter la recherche et les questions.
- Visionneuse : la page à gauche, son texte à droite, **correction du texte**
  conservée même après une nouvelle extraction.
- Sauvegarde automatique de la base avant chaque suppression ou mise à jour ;
  les fichiers d'origine ne sont jamais modifiés.

## Recherche et questions

- **Recherche par mots, par sens ou les deux** : « Qui est Geoffroy Martel ? » trouve « C’est le fils de Foulques Nerra, il est né en 1005 … ».
- **Réponses sourcées** : chaque affirmation renvoie au document, à la page
  et au passage ; un clic ouvre la page et **surligne le passage**.
- Réponse écrite en direct ; **questions de suivi** (« et quand l'a-t-il
  rendu ? ») ; filtres par collection, par type ou par document.
- Si les documents ne contiennent pas la réponse, IA-K **le dit** au lieu
  d'inventer.
- **Export en Markdown** : « Copier en Markdown » sur chaque réponse,
  « Exporter la conversation… » pour toute la séance ; chaque réponse garde
  ses renvois [S1] et la liste de ses sources (document, page, extrait du
  passage).

## Analyses

- Analyse d'une page par le modèle de vision : décrire, transcrire, expliquer
  une erreur sur une capture d'écran, extraire noms, dates et montants,
  répondre à une question.
- **Résumé** d'un document avec renvoi aux pages ; **comparaison** de
  plusieurs documents (points communs, différences, contradictions).
- Résumés, comparaisons et analyses se **copient ou s'exportent en
  Markdown** : pages citées, documents comparés (D1, D2…), modèle et date.

## Texte intégral

- « Texte intégral… » transforme un **PDF scanné** (ou une image) en document
  léger fait de vrai texte, que la liseuse remet en page : le texte de la
  bibliothèque (OCR, corrections, relectures) remplace les images des pages,
  les titres sont retrouvés, en-têtes et numéros de page retirés, les
  **paragraphes reconstitués** d'après la page (alinéa, dernière ligne
  courte, espace ; plus de coupures à chaque ligne mal alignée) et recollés
  d'une page à l'autre, les **notes** de bas de page reliées à leurs appels
  (notes EPUB, liens en HTML, liste en fin de chapitre ailleurs), et les
  **illustrations** (photos, gravures, cartes) sont découpées et gardées à
  leur place. En EPUB, PDF A5, HTML, Word, RTF ou Markdown, bien plus léger
  que le scan (selon les illustrations gardées).
- Pour un **EPUB fait de pages scannées**, chaque image de texte est lue et
  remplacée par son texte ; illustrations et couverture restent. Au choix :
  Tesseract seul (sans serveur), Tesseract puis **vision ciblée** sur les
  mots douteux (`serveurs-vl`), Tesseract puis modèle de texte
  (`serveurs-baseConnaissance`), ou le modèle de vision seul. Les élisions
  mal lues sont rétablies (« quon », « Lanxiété » → « qu'on »,
  « L'anxiété »), les notes reliées à leurs appels, et chaque correction,
  gardée ou refusée, est listée dans un fichier `.corrections.tsv`.
- **Finition du livre entier** (reprise de Prescel) : **typographie
  française** (espaces insécables avant `; : ! ?`, dans les guillemets,
  points de suspension, tirets de dialogue ; pour les livres en français),
  **mots coupés recollés** (« mar eschal » → « mareschal » quand le livre
  écrit le mot entier ailleurs) et **mots collés séparés**, et pour un
  **imprimé ancien** le **s long** lu « f » (« eft », « auffi » → « est »,
  « aussi »), jamais là où la forme en f est aussi un mot (« fait »,
  « font »). Pour un livre des XVIIᵉ-XVIIIᵉ siècles, l'**orthographe
  moderne** (« estoit », « luy », « bontez », « charmans » → « était »,
  « lui », « bontés », « charmants ») : les formes sûres sont changées,
  celles qui sont aussi des mots modernes (« teste ») seulement listées.
  Chaque changement est listé dans le `.corrections.tsv`.
- **Export Project Gutenberg** : un zip au format demandé par Project
  Gutenberg (texte sur 72 colonnes et page HTML, notes en fin de chapitre,
  images), pour un livre du domaine public, relu.

## Relecture des scans difficiles

- Le modèle de vision **corrige le texte de Tesseract** à partir de l'image,
  ou relit l'image seule : livres anciens, colonnes, tableaux (rendus en
  Markdown).
- **Mots douteux** : sous le texte d'une page OCR, les mots lus avec peu
  de confiance, inconnus de la bibliothèque (son propre vocabulaire, ancien
  français compris, et des listes de mots ajoutées) ou aux lettres suspectes
  (`at1as`) ; un clic les montre dans le texte et sur l'image, et les pages
  qui en ont beaucoup sont signalées. On peut **corriger les seuls mots
  douteux**, bien plus vite qu'en relisant toute la page : par le modèle de
  texte (chaque mot dans sa phrase, avec les mots proches de vos livres), ou
  par la **vision ciblée** (les seules lignes concernées, découpées dans
  l'image). Chaque mot proposé est vérifié ; les corrections gardées et les
  propositions refusées sont affichées.
- Chaque lecture est une **proposition** : taux de concordance avec le texte
  actuel, **différences mot à mot**, puis « Utiliser comme texte de la page »
  ou retour au texte extrait.
- Relecture d'un **document entier** (pages faibles, toutes les pages OCR ou
  toutes les pages), en remplaçant directement si on le souhaite, sans jamais
  toucher aux pages corrigées à la main ; annulable et reprise là où elle
  s'est arrêtée.

## Traduction
![Traduction](assets/traduire.png)
- **Texte libre** : coller un texte, choisir les langues (ou détection
  automatique), traduire ; 18 langues proposées, dont le latin, l'occitan
  et le breton.
- **Document de la bibliothèque, page par page** : traductions conservées,
  pages corrigées depuis signalées, export Markdown ou texte (avec
  l'original si souhaité).
- **Livre entier, mise en forme conservée** : EPUB → EPUB (italiques, notes,
  liens, table des matières), DOCX → DOCX, HTML → HTML. Un PDF (titres,
  paragraphes recollés, en-têtes et numéros de page retirés) revient en PDF
  au format A5 des liseuses, ou au choix en EPUB, HTML, Word, RTF, Markdown
  ou texte ; de même pour les scans, TXT et Markdown. Une traduction finie
  peut être réenregistrée dans un autre format sans rien retraduire.
  - 1 à 3 passes : traduction, révision bilingue, relecture finale ;
  - glossaire facultatif (« terme = traduction ») ;
  - essai sur les premiers blocs ;
  - **pause et reprise**, même après l'arrêt de l'ordinateur ;
  - le livre traduit peut être enregistré ou ajouté à la bibliothèque.
- TranslateGemma est utilisé quand son serveur tourne (avec sa consigne
  d'entraînement exacte), sinon le modèle de chat traduit.

## Pour aller plus loin

- **Ligne de commande** (`iak.bat`, `./iak.sh`) pour tout faire sans
  l'interface : import, recherche, questions, résumés, relecture, traduction
  de livres ([BACKEND.md](BACKEND.md)).
- **API locale** sécurisée (127.0.0.1, jeton) utilisée par l'interface.
- Serveurs de modèles : réglages, mémoire graphique, mesures
  ([SERVERS.md](SERVERS.md)) ; développement et intégration continue
  ([DEVELOPERS.md](DEVELOPERS.md)).
