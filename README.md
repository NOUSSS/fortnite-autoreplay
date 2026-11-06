# fortnite-auto-replay

Bot de rejeu automatique pour Fortnite : regarde l'écran en continu, reconnaît les
boutons du jeu par *template matching* et clique dessus.

Boucle visée : **Rejouer → Continuer → Prêt → (en jeu) → Rejouer → ...**

> Statut : **fondations**. La structure, la capture, la vision, les clics, la
> machine à états, le CLI et les tests sont en place. Il reste à capturer les
> templates réels (voir `docs/TEMPLATES.md`) et à affiner les seuils.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

Fortnite doit tourner en **mode fenêtré sans bordure** (ou plein écran) et être au
premier plan. Lance le bot en administrateur si les clics ne passent pas.

## Utilisation

```powershell
# 1. capturer les templates (boutons Rejouer / Continuer / Prêt)
python -m fortnite_auto_replay.tools.capture_templates

# 2. vérifier la détection sans cliquer
python -m fortnite_auto_replay run --dry-run --debug

# 3. lancer pour de vrai
python -m fortnite_auto_replay run
```

Raccourcis globaux : `F8` = démarrer / mettre en pause, `F9` = quitter.
Le bot démarre en pause par sécurité.

## Comment ça marche

```
capture.py ──► vision.py ──► engine.py ──► actions.py
 (DXGI/mss)    (matching)    (états+décisions)   (clic)
```

- `capture.py` : capture d'écran rapide (dxcam → mss → PIL), avec ROI et FPS cible.
- `vision.py` : matching multi-échelle + `matchTemplate`, retourne score et position.
- `engine.py` : machine à états, confirmations anti-faux-positifs, cooldowns, stats.
- `actions.py` : clics (`pydirectinput` → SendInput via ctypes → pyautogui).

Rien n'est codé en dur : seuils, ROI, délais et zones de clic sont dans
`config/default.json` (surchargé par `config/local.json`).

## Sécurité / limites connues

- Ne joue pas : il faut lancer la partie à la main la première fois.
- Fortnite doit rester au premier plan ; sinon les clics partent ailleurs.
- Si un écran de résultat diffère (nouvel événement, nouvelle saison), il faut
  refaire les templates.
- Anti-cheat : uniquement des entrées souris clavier classiques, rien d'injecté.
- Le backend `dxcam` ne libère pas sa sortie DXGI en fin de processus
  (`release()` plante sur Python 3.13) ; la sortie est volontairement dure
  (`os._exit`) dans `__main__.py`.

## Documentation

- `docs/ARCHITECTURE.md` — découpage des modules et flux d'exécution
- `docs/TEMPLATES.md` — comment capturer / valider les templates
- `docs/ROADMAP.md` — ce qui est fait, ce qui reste à faire
