# Relecture

## Exécuter le code sans risque

- Tests : `python -m pytest -q` (capture, clics et fenêtre factices, aucune entrée réelle).
- Lint : `python -m ruff check .` puis `python -m ruff format --check .`.
- Sur la machine réelle, Fortnite peut être ouvert et au premier plan : ne jamais
  lancer `run` sans `--dry-run`, ni l'outil `capture_templates`, ni rien qui
  clique, tape ou change le focus. `window` ne fait que lire ; `check` capture
  une frame (envoyer `engine.debug_dir` dans un dossier temporaire).

## Zones à relire de près

- `engine.py` : ordre des garde-fous dans `_step` (confirmation, cooldown, clic
  sans effet, clic), remise à zéro des séries, focus revérifié dans `_act`.
- Coordonnées : frame → écran (`Frame.to_screen`, `capture.scale`), région
  rognée à l'écran capturé (l'origine de la frame doit suivre), zone connue
  rognée au bord de la frame, `capture.roi` relatif à la zone client du jeu.
- Zones attendues : `templates.prepare` (échelle par la hauteur de fenêtre,
  zone en fraction de frame) et l'ordre de recherche dans `Engine._search`.
- Le second écran de la machine est à gauche (x négatif) : tout calcul qui
  suppose des coordonnées positives est suspect.
- `window.py` : handles 64 bits (instances `WinDLL` privées avec `argtypes`),
  fenêtres annexes « Fortnite » (anti-triche, rapport de plantage) à écarter.
