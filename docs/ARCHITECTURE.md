# Architecture

## But

Automatiser une boucle de rejeu : observer l'écran, reconnaître les boutons
demandés par Fortnite, cliquer dessus, recommencer. Aucune lecture mémoire,
aucune injection : uniquement de la vision, des entrées souris réelles au
premier plan et des messages de fenêtre au second plan.

## Flux

```
        ┌──────────────┐
        │  GameWindow   │  window.py  (zone client du jeu, état)
        └──────┬───────┘
               │ région écran + « visible ? au premier plan ? »
        ┌──────▼───────┐
        │  ScreenCapture│  capture.py  (dxcam → mss → PIL)
        └──────┬───────┘
               │ Frame (image BGR à demi-échelle + origine écran)
        ┌──────▼───────┐
        │    Engine     │  engine.py  (machine à états, cooldowns, confirmations)
        │  _detect()    │
        └───┬───────┬──┘
   Vision   │       │  Actions
 ┌──────────▼──┐  ┌─▼─────────────┐
 │  vision.py  │  │  actions.py    │  (SendInput au premier plan, PostMessage sinon)
 │ + templates │  └───────────────┘
 └─────────────┘
```

## Modules

| Module | Rôle |
| --- | --- |
| `config.py` | Schéma typé + validation + fusion `default.json`/`local.json` |
| `window.py` | Fenêtre du jeu par exécutable + classe, zone client, état (`focus`), DPI |
| `capture.py` | Capture rapide, repli de backend, région suivant la fenêtre, frame→écran |
| `vision.py` | Matching multi-échelle, backends OpenCV/numpy, annotations |
| `templates.py` | Templates par état, JSON de zone, mise à l'échelle de la frame |
| `engine.py` | Boucle, priorité, `confirm_frames`, cooldowns, garde-fous, pause/stop |
| `history.py` | Historique des sessions (`logs/sessions.jsonl`) et totaux |
| `actions.py` | Clics souris, offsets, backends interchangeables, clic posté (`BackgroundClicker`) |
| `hotkeys.py` | `F8`/`F9` via `GetAsyncKeyState`, seulement quand le jeu est au premier plan |
| `diagnostics.py` | Exceptions non rattrapées au journal, piles des threads dans `logs/crash.log` |
| `panel.py` | Panneau de contrôle Qt (PySide6), moteur dans un thread, bandeau de mise à jour |
| `panel_style.py` | Jetons du panneau : couleurs, grille, polices, icônes |
| `panel_widgets.py` | Widgets dessinés du panneau : texte, boutons, sélecteur, curseur, bandeau |
| `panel_glass.py` | Verre du panneau : l'écran sous lui (il est exclu des captures), capturé dans un thread puis flouté |
| `chests.py` | Coffres comptés dans les replays du jeu par `replay-reader.exe` (`logs/chests.json`) |
| `updater.py` | Manifeste signé, téléchargement vérifié, installation tout ou rien, relance |
| `cli.py` | `run`, `check`, `config`, `window`, `stats`, `panel`, `update` + surcharges en ligne de commande |

## Performance

Mesuré sur 1080p, 8 templates (4 états x 2), écran sans bouton (cas courant en
pleine partie), même machine et même charge :

| Analyse d'une frame | Coût |
| --- | --- |
| Avant : pleine échelle, frame entière | 600 à 1 000 ms (300 ms machine au repos) |
| Demi-échelle, frame entière (balayage complet, toutes les 2 s) | ~130 ms |
| Demi-échelle, zones attendues (frame courante) | ~11 ms |

La capture dxcam coûte ~0,2 ms : la boucle reste synchrone, un thread de
capture n'apporterait rien. Tout le gain vient du matching.

## Décisions

- **Priorité par état** (`states[].priority`) plutôt que séquence rigide : si
  « Rejouer » et « Prêt » sont visibles en même temps, le plus prioritaire gagne.
- **Confirmation multi-frame** : `confirm_frames=2` évite les clics sur un
  popup transitoire ressemblant à un bouton.
- **Cooldowns** par état *et* global : même détecté en boucle, un bouton n'est
  pas martelé.
- **Où chercher**, dans l'ordre : là où le bouton vient d'être vu
  (± `vision.padding`), puis la zone attendue de chaque template (son JSON),
  puis toute la frame pour les templates sans JSON. Un balayage complet a lieu
  toutes les `vision.full_scan_interval_s` secondes. Un bouton absent de sa
  position connue la fait oublier sur la même frame.
- **Demi-échelle** (`capture.scale = 0.5`) : les boutons de Fortnite restent
  très lisibles et le matching coûte ~5 fois moins. Gris calculé une fois par
  frame, templates préparés une fois par taille de frame.
- **Image en cache** : Desktop Duplication ne livre une image que si l'écran a
  changé. Sur un écran immobile, la dernière image est réutilisée (avant, la
  capture échouait et le bot devenait aveugle).
- **Bon écran, bonne origine** : dxcam capture l'écran qui contient le jeu
  (choisi par coordonnées, pas par index). La région est rognée à cet écran et
  la frame prend l'origine rognée : si le jeu déborde, les clics restent justes.
  Le backend est rouvert si le jeu change d'écran ou après 3 échecs de suite.
- **Erreurs survivables** : une exception pendant l'analyse est journalisée et
  la boucle continue ; après `engine.max_step_errors` de suite, pause et bip.
- **Fenêtre par exécutable, pas par titre** : l'anti-triche
  (`…_EAC_EOS.exe`) ouvre aussi une fenêtre « Fortnite », invisible. On cherche
  `FortniteClient-Win64-Shipping.exe` de classe `UnrealWindow`, visible.
- **Jeu visible avant tout** : fenêtre absente ou réduite (origine à
  -32000), la boucle ne capture rien et oublie ses confirmations ; l'état est
  revérifié juste avant chaque clic.
- **Clic au second plan** : au premier plan, entrée réelle (`SendInput`). Au
  second plan, le clic est posté à la fenêtre du jeu (`WM_MOUSEMOVE`,
  `WM_LBUTTONDOWN`, `WM_LBUTTONUP`, coordonnées client) : il n'active pas le
  jeu et ne vole pas le focus. Jamais de repli sur une entrée réelle, qui
  partirait dans la fenêtre active. Unreal calcule le survol d'un bouton
  d'après le vrai curseur (`GetCursorPos`), pas d'après le message : en mode
  `cursor`, le curseur est posé sur le bouton le temps du clic puis remis au
  même pixel (plus le geste fait pendant le clic, pour ne pas reculer),
  seulement si la souris est immobile et sans bouton enfoncé. Sinon le clic est
  reporté à l'image suivante, sans nouvelle confirmation (« Clic en attente »).
- **Réveil du jeu** : au second plan, Fortnite se dit inactif (`AppBackgroundUtils`
  « Active State False ») et peut ignorer les clics. Le clic est encadré par
  `WM_ACTIVATEAPP` (1, puis 0 dans un `finally`, même si l'envoi échoue) : le
  jeu se croit actif sans que sa fenêtre le soit. Jamais `WM_ACTIVATE` : sa
  procédure par défaut appelle `SetFocus`, et la fenêtre peut passer au premier
  plan si son processus en a le droit (constaté sur une fenêtre de test).
- **Traces d'arrêt** : sous `pythonw`, rien ne s'affiche. Chaque sortie est
  journalisée avec sa cause ; `faulthandler` écrit la pile de tous les threads
  dans `logs/crash.log` lors d'un plantage natif ; le panneau y écrit aussi la
  pile quand le moteur ne bat plus depuis 20 s ou ne s'arrête pas en 3 s.
- **Clic sans effet** : une série de clics sur le même bouton s'arrête dès que
  le bouton disparaît d'une frame. Au-delà de `max_ineffective_clicks`, pause.
- **DPI** : le processus se déclare *per-monitor aware* au démarrage, pour que
  capture, fenêtres et clics parlent en pixels physiques (affichage à 125 %…).
- **Dialogue « Vous êtes toujours là ? »** : deux états, un par bouton
  (`still_here_ready`, `still_here_not_yet`). Seul celui de la réponse choisie
  (`engine.still_here`) est recherché ; les deux templates sont chargés pour
  pouvoir changer de réponse bot en marche (`Engine.set_still_here`).
- **Clic couvert** : juste avant de cliquer, `GameWindow.covering` regarde
  quelle fenêtre recevrait le clic (`WindowFromPoint`). Si ce n'est pas le jeu
  (panneau, notification), le clic est annulé et l'état passe à « Clic annulé ».
- **Panneau** : Qt (PySide6) dans le thread principal, moteur dans un thread ; le
  panneau lit `Engine.status()` (instantané figé, compteurs sous verrou) toutes
  les 250 ms. La capture s'ouvre dans le thread du moteur (mss n'est utilisable
  que dans le thread qui l'a créé). La fenêtre est `WS_EX_NOACTIVATE`, au-dessus
  de tout et exclue des captures (`WDA_EXCLUDEFROMCAPTURE`) : le bot ne la voit
  pas, et un clic dessus ne retire pas le focus au jeu.
- **Pastille et survol** : au repos, la fenêtre ne montre qu'une pastille
  (`panel.rest` : `pill`) ou la carte entière (`panel`). Toutes les 50 ms, le
  panneau lit la position du curseur (`GetCursorInfo`, `GetWindowRect`) : dessus
  (ou sur le popover des réglages), la carte s'ouvre ; dehors depuis 0,4 s, elle
  se referme (`hover_step`). Jamais quand la souris le traverse : en partie, le
  curseur caché passe dessous sans rien ouvrir. Sans la souris, la fenêtre prend
  l'opacité au repos en 150 ms (`setWindowOpacity`, `panel.rest_opacity`).
  Le popover des réglages est une seconde fenêtre, aux mêmes drapeaux.
- **Dessin** : fenêtres sans cadre à fond transparent (`WA_TranslucentBackground`,
  donc `WS_EX_LAYERED` mis à jour par `UpdateLayeredWindow`), widgets peints à
  la main avec anticrénelage. Pas d'ombre extérieure : ses pixels prendraient
  les clics au-dessus du jeu. La mise à l'échelle automatique de Qt est coupée
  (`QT_ENABLE_HIGHDPI_SCALING=0`) : fenêtres, curseur et positions enregistrées
  parlent en pixels d'écran, et `px()` applique l'échelle de Windows.
- **Animations** (reprises du prototype) : la carte qui s'ouvre ou se referme et
  le popover qui apparaît sont joués par `Ghost`, une image de la vue d'arrivée
  (`grab()`) qui passe de 0,96 à 1 en échelle et de 0 à 1 en opacité (180 ms à
  l'ouverture, 120 ms à la fermeture) ; boutons : couleur en 150 ms, enfoncés à
  0,97.
- **Polices** : Figtree et Bricolage Grotesque (OFL), dans
  `src/fortnite_auto_replay/fonts` et dans `_internal` une fois empaqueté, sont
  chargées par `QFontDatabase.addApplicationFont` : visibles du seul processus,
  rien n'est installé. Une mise à jour remplace `_internal` entier, polices
  comprises.
- **Premier plan rendu** : les fenêtres se montrent sans s'activer
  (`WA_ShowWithoutActivating`, `WindowDoesNotAcceptFocus`, `WS_EX_NOACTIVATE`).
  Par sécurité, le panneau note la fenêtre active avant de se créer et lui rend
  le premier plan s'il l'a pris (`focus_step`), puis le vérifie à chaque tour
  de 250 ms. Si cette fenêtre n'existe plus, il le rend au
  jeu (`give_back_targets`) ; s'il ne peut le rendre à personne, il le dit une
  fois par épisode, pas à chaque tour.
- **Souris traversante** : pendant une partie (jeu au premier plan, curseur
  masqué ou retenu par `ClipCursor`), le panneau ajoute `WS_EX_TRANSPARENT` (sur
  une fenêtre `WS_EX_LAYERED`) : les clics passent au jeu. Curseur posé
  sur le panneau, l'état ne change pas, pour qu'il ne bascule pas sous la visée
  (`mouse_through`).
- **Panneau unique** : au lancement, avant de lire l'historique,
  `replace_other_panels` ferme les autres fenêtres « Fortnite AutoReplay » (Qt, ou Tk
  des versions 0.5 et avant)
  (ou « Fortnite Auto Replay », titre de la version 0.1.0 lancée depuis les sources)
  (`WM_CLOSE`, puis attente de la fin du processus) et prend un mutex nommé que
  chaque panneau tient jusqu'à l'arrêt de son moteur, session enregistrée. Deux
  lancements rapprochés ne passent donc pas tous les deux : le second ferme le
  premier dès que sa fenêtre paraît. Si un panneau ne se ferme pas en 8 s (droits
  plus élevés, panneau figé), un message le dit et le nouveau ne s'ouvre pas.
- **Mise à jour sans programme d'aide** : Windows permet de renommer un
  exécutable en cours d'exécution et son dossier `_internal`. Le programme
  installé part dans `.update/old/`, la nouvelle version prend sa place, et
  tout est remis en place si un renommage échoue. L'installation n'a lieu
  qu'une fois le moteur arrêté (session écrite) et les fenêtres Qt fermées,
  juste avant `os._exit` : l'ancien processus lit encore des modules dans
  `_internal` tant qu'il tourne. La nouvelle version, relancée détachée (avec
  `--no-pause` si le bot tournait), efface `.update/` au démarrage.
- **Confiance dans le manifeste** : signature Ed25519 sur le JSON canonique
  (clés triées, sans espaces, sans le champ `signature`) par l'une des clés de
  `updater.PUBLIC_KEYS` (deux le temps d'un changement de clé), version strictement
  plus récente, https seulement (http vers la machine elle-même pour les
  essais), archive vérifiée par taille et sha256 avant extraction, chemins du
  zip qui sortiraient du dossier refusés.
- **Coffres comptés dans les replays** : la quête de la chasse au trésor cesse
  de compter à 1 000 et les journaux du jeu ne notent aucun coffre. Le replay
  d'une partie contient l'état de chaque coffre (`bAlreadySearched`,
  `bDestroyed`) et le pion qui l'a fouillé ou cassé (`SearchingPawn`). La
  lecture vient de ChestCounter (C#, lui-même parti de
  FortniteReplayDecompressor), reprise telle quelle dans `replay-reader/vendor`
  et appelée par `replay-reader.exe`, une ligne JSON par replay : la réécrire
  en Python serait long et suivrait mal les changements de format du jeu.
  Exécutable autonome (.NET embarqué, 38 Mo), lancé sans fenêtre et en
  priorité basse, un replay à la fois (1 à 2 s).
- **Quand lire un replay** : le jeu le complète toutes les 25 s environ en
  partie, drapeau « en direct » levé et clé de déchiffrement à zéro ; il écrit
  la clé et baisse le drapeau à la fin. L'en-tête est lu en Python
  (`read_header`) : un replay terminé se lit une fois sa taille stable sur deux
  tours (4 s de calme), un replay encore « en direct » mais intouché depuis
  10 minutes est un enregistrement coupé (jeu fermé), noté incomplet sans être
  lu. Mêmes délais que ChestCounter.
- **Ce que le panneau dit du comptage** : le jeu ne termine le replay qu'à la
  sortie de la partie. Un replay terminé mais pas encore lu met le résumé en
  « comptage » (`Summary.counting`), publié aussi pendant la lecture, pour ne
  jamais présenter l'avant-dernière partie comme la dernière. Un replay
  encore « en direct » sans fenêtre du jeu est une partie interrompue : le
  panneau le dit tout de suite, sans attendre les 10 minutes, puis
  `Summary.interrupted` le retient jusqu'à la partie suivante (seulement pour
  une partie jouée panneau ouvert).
- **Une partie, une fois** : un replay est reconnu par nom, taille et date ; une
  partie par son `session_id`, si bien qu'un replay renommé ou copié ne compte
  pas deux fois. Une nouvelle version du lecteur (`--version`, version de
  l'analyse de ChestCounter) relit les replays encore présents et remplace
  leurs parties ; celles dont le replay a disparu restent.
- **Lecteur arrêté avec le panneau** : `ChestTracker.stop` tue la lecture en
  cours, qui tiendrait le dossier `replay-reader/` pendant l'installation
  d'une mise à jour, et rien n'est retenu de ce replay (relu au lancement
  suivant).
- **Aucune valeur dans le code** : tout est configuration.

## Points d'extension prévus

- Templates multiples par état : déjà supporté (plusieurs PNG par dossier).
- Nouveaux états (ex. « Quitter », « Revanche », écrans d'événements) :
  ajouter un `StateConfig` + un dossier de templates.
- Détection OCR : remplacer `vision.best_match` par un backend dédié, sans
  toucher au moteur.
- Autre interface : `Engine` expose `pause/resume/toggle/stop/status` et
  `set_still_here` ; le panneau n'utilise rien d'autre.
