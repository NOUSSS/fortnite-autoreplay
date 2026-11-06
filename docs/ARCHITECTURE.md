# Architecture

## But

Automatiser une boucle de rejeu : observer l'écran, reconnaître les boutons
demandés par Fortnite, cliquer dessus, recommencer. Aucune lecture mémoire,
aucune injection : uniquement de la vision et des entrées souris réelles.

## Flux

```
        ┌──────────────┐
        │  ScreenCapture│  capture.py  (dxcam → mss → PIL)
        └──────┬───────┘
               │ Frame (image BGR + origine écran)
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
| `capture.py` | Capture rapide, repli de backend, régions, conversion frame→écran |
| `vision.py` | Matching multi-échelle, backends OpenCV/numpy, annotations |
| `templates.py` | Chargement/validation des templates par état |
| `engine.py` | Boucle, priorité, `confirm_frames`, cooldowns, stats, pause/stop |
| `actions.py` | Clics souris, offsets, arières-plans interchangeables |
| `hotkeys.py` | `F8`/`F9` via `GetAsyncKeyState`, sans dépendance |
| `cli.py` | `run`, `check`, `config` + surcharges en ligne de commande |

## Décisions

- **Priorité par état** (`states[].priority`) plutôt que séquence rigide : si
  « Rejouer » et « Prêt » sont visibles en même temps, le plus prioritaire gagne.
- **Confirmation multi-frame** : `confirm_frames=2` évite les clics sur un
  popup transitoire ressemblant à un bouton.
- **Cooldowns** par état *et* global : même détecté en boucle, un bouton n'est
  pas martelé.
- **ROI glissante** : après un hit, la recherche suivante se limite à la zone
  connue ± `vision.padding`, ce qui accélère la boucle.
- **Aucune valeur dans le code** : tout est configuration.

## Points d'extension prévus

- Templates multiples par état : déjà supporté (plusieurs PNG par dossier).
- Nouveaux états (ex. « Quitter », « Revanche », écrans d'événements) :
  ajouter un `StateConfig` + un dossier de templates.
- Détection OCR : remplacer `vision.best_match` par un backend dédié, sans
  toucher au moteur.
- Interface graphique : `Engine` expose déjà `pause/resume/stop/stats`.
