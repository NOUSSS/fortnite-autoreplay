# fortnite-auto-replay

Bot de rejeu automatique pour Fortnite : regarde l'écran en continu, reconnaît les
boutons du jeu par *template matching* et clique dessus.

Boucle visée : **Rejouer → Continuer → Prêt → (en jeu) → Rejouer → ...**

> Statut : **fondations, garde-fous, performance**. Capture, vision, clics,
> machine à états, suivi de la fenêtre du jeu, zones de recherche par template,
> pause sur clic sans effet, historique et tests sont en place. Il reste à capturer les templates réels (voir
> `docs/TEMPLATES.md`) et à affiner les seuils.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

Fortnite peut tourner en **fenêtré sans bordure**, fenêtré ou plein écran : le bot
repère sa fenêtre et n'analyse que sa zone client. Il n'agit que lorsque le jeu
est au premier plan. Lance le bot en administrateur si les clics ne passent pas.

## Utilisation

```powershell
# 1. capturer les templates (boutons Rejouer / Continuer / Prêt)
python -m fortnite_auto_replay.tools.capture_templates

# 2. vérifier la détection sans cliquer
python -m fortnite_auto_replay run --dry-run --debug

# 3. lancer pour de vrai
python -m fortnite_auto_replay run

# diagnostic
python -m fortnite_auto_replay window   # fenêtre du jeu repérée et focus
python -m fortnite_auto_replay stats    # totaux des sessions passées
```

Raccourcis globaux : `F8` = démarrer / mettre en pause, `F9` = quitter.
Le bot démarre en pause par sécurité.

## Comment ça marche

```
window.py ──► capture.py ──► vision.py ──► engine.py ──► actions.py
 (fenêtre,     (DXGI/mss)     (matching)    (états+décisions)   (clic)
  focus)
```

- `window.py` : repère la fenêtre du jeu (exécutable + classe), donne sa zone
  client et dit si elle a le focus.
- `capture.py` : capture d'écran rapide (dxcam → mss → PIL), calée sur la zone
  client du jeu (ou sur le moniteur avec `--no-window`), à demi-échelle.
- `templates.py` : templates par état ; leur JSON (écrit par l'outil de capture)
  donne la zone où chercher et l'échelle à appliquer.
- `vision.py` : matching multi-échelle + `matchTemplate`, retourne score et position.
- `engine.py` : machine à états, confirmations anti-faux-positifs, cooldowns,
  garde de focus, pause sur clic sans effet, frames douteuses, historique.
- `actions.py` : clics (`pydirectinput` → SendInput via ctypes → pyautogui).

Rien n'est codé en dur : seuils, ROI, délais et zones de clic sont dans
`config/default.json` (surchargé par `config/local.json`).

## Sécurité / limites connues

- Ne joue pas : il faut lancer la partie à la main la première fois.
- Tant que Fortnite n'est pas au premier plan, le bot attend : il ne clique
  jamais dans une autre fenêtre. `--no-window` retire ce garde-fou.
- Si un bouton reste affiché après `engine.max_ineffective_clicks` clics, le bot
  se met en pause (bip, frame `captures/stuck-*.png`) : `F8` pour reprendre.
- Un score juste sous le seuil sauve une frame `captures/doubt-*.jpg` : c'est le
  signe qu'un template est à recadrer ou que `vision.threshold` est trop haut.
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
