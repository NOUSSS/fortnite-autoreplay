# Architecture

## But

Automatiser une boucle de rejeu : observer l'écran, reconnaître les boutons
demandés par Fortnite, cliquer dessus, recommencer. Aucune lecture mémoire,
aucune injection : uniquement de la vision et des entrées souris réelles.

## Flux

```
        ┌──────────────┐
        │  GameWindow   │  window.py  (zone client du jeu, focus)
        └──────┬───────┘
               │ région écran + « au premier plan ? »
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
 │  vision.py  │  │  actions.py    │  (pydirectinput → SendInput → pyautogui)
 │ + templates │  └───────────────┘
 └─────────────┘
```

## Modules

| Module | Rôle |
| --- | --- |
| `config.py` | Schéma typé + validation + fusion `default.json`/`local.json` |
| `window.py` | Fenêtre du jeu par exécutable + classe, zone client, focus, DPI |
| `capture.py` | Capture rapide, repli de backend, région suivant la fenêtre, frame→écran |
| `vision.py` | Matching multi-échelle, backends OpenCV/numpy, annotations |
| `templates.py` | Templates par état, JSON de zone, mise à l'échelle de la frame |
| `engine.py` | Boucle, priorité, `confirm_frames`, cooldowns, garde-fous, pause/stop |
| `history.py` | Historique des sessions (`logs/sessions.jsonl`) et totaux |
| `actions.py` | Clics souris, offsets, arières-plans interchangeables |
| `hotkeys.py` | `F8`/`F9` via `GetAsyncKeyState`, sans dépendance |
| `sounds.py` | Deux notes sur `F8` (reprise, pause) |
| `panel.py` | Panneau de contrôle tkinter, moteur dans un thread |
| `cli.py` | `run`, `check`, `config`, `window`, `stats`, `panel` + surcharges en ligne de commande |

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
- **Focus avant tout** : sans focus, la boucle ne capture rien et oublie ses
  confirmations ; le focus est revérifié juste avant chaque clic.
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
- **Panneau** : tkinter dans le thread principal, moteur dans un thread ; le
  panneau lit `Engine.status()` (instantané figé, compteurs sous verrou) toutes
  les 250 ms. La capture s'ouvre dans le thread du moteur (mss n'est utilisable
  que dans le thread qui l'a créé). La fenêtre est `WS_EX_NOACTIVATE`, au-dessus
  de tout et exclue des captures (`WDA_EXCLUDEFROMCAPTURE`) : le bot ne la voit
  pas, et un clic dessus ne retire pas le focus au jeu.
- **Premier plan rendu** : à la création de sa première fenêtre, Tk prend le
  premier plan (constaté lancé depuis un terminal comme par WMI), avant qu'on
  puisse poser `WS_EX_NOACTIVATE`. Le panneau note la fenêtre active avant de se
  créer et lui rend le premier plan s'il l'a pris (`focus_step`), puis le
  vérifie à chaque tour de 250 ms.
- **Aucune valeur dans le code** : tout est configuration.

## Points d'extension prévus

- Templates multiples par état : déjà supporté (plusieurs PNG par dossier).
- Nouveaux états (ex. « Quitter », « Revanche », écrans d'événements) :
  ajouter un `StateConfig` + un dossier de templates.
- Détection OCR : remplacer `vision.best_match` par un backend dédié, sans
  toucher au moteur.
- Autre interface : `Engine` expose `pause/resume/toggle/stop/status` et
  `set_still_here` ; le panneau n'utilise rien d'autre.
