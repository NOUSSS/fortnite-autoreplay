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
Le bot démarre en pause par sécurité. `F8` joue deux notes : montantes quand
le bot repart, descendantes quand il se met en pause (`engine.sound_on_toggle`).

## Panneau de contrôle

Double-cliquer sur **`Lancer le panneau.cmd`** (ou
`python -m fortnite_auto_replay panel`, avec `--dry-run` pour une simulation).
Une petite fenêtre s'ouvre au-dessus du jeu et lance le moteur :

- en-tête : pastille et état (Actif, En pause, En attente du jeu, Bloqué,
  Clic annulé…), clics de la session, bouton Lancer / Pause / Reprendre, et le
  chevron qui replie le panneau sur cette seule ligne ;
- déplié : la réponse au dialogue « Vous êtes toujours là ? », les compteurs de
  la session, les cinq derniers clics, les totaux de toutes les sessions et
  Quitter. Un bandeau explique tout état anormal (bouton qui ne part pas, jeu au
  second plan, capture en échec, clic annulé).

Le panneau se déplace en le tirant par son en-tête ; sa position, son état
replié et la réponse au dialogue sont gardés dans `config/local.json`. Il ne
garde jamais le focus : s'il le prend (un instant à son ouverture), il le rend
aussitôt à la fenêtre d'avant, le jeu en général. Il est invisible pour les
captures d'écran (le bot ne le voit pas), et si un bouton à cliquer se trouve
sous lui, le clic est annulé et le bandeau demande de le déplacer. Il reste
au-dessus du jeu en **plein écran fenêtré** ; en plein écran exclusif, Windows
ne montre aucune fenêtre par-dessus.

### Dialogue « Vous êtes toujours là ? »

Quand Fortnite demande « Vous êtes toujours là ? » avant une partie de groupe,
le bot clique **Prêt** par défaut. Le panneau permet de choisir Prêt, Pas
encore ou Ignorer (ne rien cliquer), aussi dans `config/local.json` :

```json
{ "engine": { "still_here": "ready" } }
```

Valeurs : `ready`, `not_yet`, `ignore`.

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
- `panel.py` : panneau de contrôle (tkinter) ; le moteur tourne dans un thread.

Rien n'est codé en dur : seuils, ROI, délais et zones de clic sont dans
`config/default.json` (surchargé par `config/local.json`).

## Sécurité / limites connues

- Ne joue pas : il faut lancer la partie à la main la première fois.
- Tant que Fortnite n'est pas au premier plan, le bot attend : il ne clique
  jamais dans une autre fenêtre. `--no-window` retire ce garde-fou.
- Avant chaque clic, le bot vérifie que le point visé est bien sur le jeu : si
  une autre fenêtre le couvre (panneau, notification, overlay), il n'y clique
  pas.
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
