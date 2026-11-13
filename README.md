# fortnite-auto-replay

Bot de rejeu automatique pour Fortnite : regarde l'écran en continu, reconnaît les
boutons du jeu par *template matching* et clique dessus.

Boucle visée : **Retourner au salon → Continuer → Prêt ou Jouer → (en jeu) → Retourner au salon → ...**

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
repère sa fenêtre et n'analyse que sa zone client. Il agit tant que le jeu est
visible à l'écran, même si l'on travaille sur un autre écran : au second plan, ses
clics sont envoyés à la fenêtre du jeu sans lui donner le focus. Lance le bot en
administrateur si les clics ne passent pas.

## Utilisation

```powershell
# 1. capturer les templates (boutons Retourner au salon / Continuer / Prêt / Jouer)
python -m fortnite_auto_replay.tools.capture_templates

# 2. vérifier la détection sans cliquer
python -m fortnite_auto_replay run --dry-run --debug

# 3. lancer pour de vrai
python -m fortnite_auto_replay run

# diagnostic
python -m fortnite_auto_replay window   # fenêtre du jeu repérée et premier plan
python -m fortnite_auto_replay stats    # totaux des sessions passées
python -m fortnite_auto_replay chests   # coffres ouverts comptés (voir plus bas)
```

Raccourcis en jeu : `F8` = démarrer / mettre en pause, `F9` = quitter. Ils ne
comptent que quand Fortnite est au premier plan : ailleurs, `F8` et `F9` restent
à l'application active. Le bot démarre en pause par sécurité. `F8` joue deux notes : montantes quand
le bot repart, descendantes quand il se met en pause (`engine.sound_on_toggle`).

## Panneau de contrôle

Double-cliquer sur **`Lancer le panneau.cmd`** (ou
`python -m fortnite_auto_replay panel`, avec `--dry-run` pour une simulation).
Une petite fenêtre s'ouvre au-dessus du jeu et lance le moteur :

- une ligne par fonction, chacune avec sa pastille et son état. **Relance
  auto** : Actif, En pause, En attente du jeu, Bloqué, Clic annulé…, avec le
  bouton Lancer / Pause / Reprendre. **Coffres** : le total compté et la
  dernière partie. Le compteur de coffres ne dépend pas de la relance : il
  compte aussi en pause. Chaque ligne a son chevron, qui la déplie ou la
  replie sans toucher à l'autre ;
- déplié : sous la relance, la réponse au dialogue « Vous êtes toujours là ? »
  et les cinq derniers clics ; sous les coffres, le nombre de parties
  comptées et les cinq dernières, avec leurs coffres ; puis Quitter, dès
  qu'une ligne est dépliée. Un bandeau explique tout état anormal (bouton qui
  ne part pas, jeu réduit, clic en attente, capture en échec, clic annulé,
  rediffusions désactivées).

Le panneau se déplace en le tirant par l'une de ses lignes ; sa position, ce
qui est replié et la réponse au dialogue sont gardés dans `config/local.json`. Il ne
garde jamais le focus : s'il le prend (un instant à son ouverture), il le rend
aussitôt à la fenêtre d'avant, ou au jeu si elle n'existe plus. Pendant une
partie (jeu au premier plan, curseur masqué ou retenu dans sa fenêtre), la
souris le traverse : un clic ou un geste de visée passé dessus va au jeu et ne
peut ni l'activer ni le déplacer. Un seul panneau tourne à la fois : en lancer
un nouveau ferme l'ancien, même caché hors écran ; si l'ancien refuse (lancé en
administrateur, par exemple), un message le dit. Il est invisible pour les
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

### Coffres ouverts

Le panneau compte les coffres que vous ouvrez, partie après partie, que la
relance tourne ou soit en pause : sa ligne « Coffres » donne le total et les
coffres de la dernière partie ; dépliée, le nombre de parties comptées, leur
date de début et les cinq dernières parties (début et coffres). Les coffres cassés (support détruit, explosion)
comptent aussi ; les caisses de munitions, de nourriture et les canons à
butin non.

Il les lit dans les replays de Fortnite, qu'il faut donc activer dans le jeu :
**Paramètres, Jeu, Rediffusions, Enregistrer les rediffusions : Activé**. Le
jeu écrit un replay par partie dans `%LOCALAPPDATA%\FortniteGame\Saved\Demos`
et n'écrit sa clé de déchiffrement qu'à la fin : une partie se compte quelques
secondes après sa fin, pas pendant. Seul le joueur qui enregistre est compté,
et seulement en Battle Royale.

La lecture des replays est celle de
[ChestCounter](https://github.com/LED-N/Fortnite-ChestCounter) (licence MIT),
reprise dans `replay-reader/` (voir son README) : un petit programme à part,
`replay-reader.exe`, livré avec l'application. Il ne touche ni au jeu ni à sa
mémoire et n'envoie rien.

Les parties comptées sont gardées dans `logs/chests.json` : le jeu efface ses
vieux replays, le compteur continue. Une partie jouée application fermée est
comptée au lancement suivant, tant que son replay existe encore.
`python -m fortnite_auto_replay chests` lit les replays terminés et affiche
le total.

## Comment ça marche

```
window.py ──► capture.py ──► vision.py ──► engine.py ──► actions.py
 (fenêtre,     (DXGI/mss)     (matching)    (états+décisions)   (clic)
  état)
```

- `window.py` : repère la fenêtre du jeu (exécutable + classe), donne sa zone
  client et dit si elle est au premier plan, au second plan ou réduite.
- `capture.py` : capture d'écran rapide (dxcam → mss → PIL), calée sur la zone
  client du jeu (ou sur le moniteur avec `--no-window`), à demi-échelle.
- `templates.py` : templates par état ; leur JSON (écrit par l'outil de capture)
  donne la zone où chercher et l'échelle à appliquer.
- `vision.py` : matching multi-échelle + `matchTemplate`, retourne score et position.
- `engine.py` : machine à états, confirmations anti-faux-positifs, cooldowns,
  garde de fenêtre, pause sur clic sans effet, frames douteuses, historique.
- `actions.py` : clics au premier plan (`pydirectinput` → SendInput via ctypes →
  pyautogui), clics postés à la fenêtre du jeu au second plan.
- `diagnostics.py` : traces des arrêts inattendus (`logs/crash.log`).
- `panel.py` : panneau de contrôle (tkinter) ; le moteur tourne dans un thread.
- `chests.py` : coffres ouverts, comptés dans les replays du jeu par
  `replay-reader.exe` (C#, `replay-reader/`).

Rien n'est codé en dur : seuils, ROI, délais et zones de clic sont dans
`config/default.json` (surchargé par `config/local.json`).

## Sécurité / limites connues

- Ne joue pas : il faut lancer la partie à la main la première fois.
- Tant que la fenêtre de Fortnite est absente ou réduite, le bot attend. Au
  second plan, il poste ses clics à la fenêtre du jeu (`PostMessage`) et
  n'envoie jamais de vraie entrée, qui partirait dans la fenêtre active.
  `window.require_foreground: true` le fait attendre le premier plan, comme
  avant ; `--no-window` retire ce garde-fou.
- Au second plan, Fortnite ne voit un bouton survolé que si le vrai curseur est
  dessus. En mode `actions.background: "cursor"` (défaut), le bot pose le
  curseur sur le bouton le temps du clic (environ 0,25 s) puis le remet au même
  pixel, en gardant le déplacement si la souris a bougé entre-temps ; il attend
  pour cela que la souris reste immobile
  `actions.mouse_idle_s` et qu'aucun bouton ne soit enfoncé (« Clic en
  attente » sinon). Le mode `"message"` ne touche jamais au curseur, au risque
  que le jeu ignore le clic.
- Au second plan, Fortnite se met en veille (30 images par seconde) et peut
  ignorer les clics. Le temps du clic, le bot lui annonce qu'il est l'application
  active (`WM_ACTIVATEAPP`), sans activer sa fenêtre ni prendre le focus, puis
  le remet en veille. `actions.background_activate: false` retire ce réveil.
- Avant chaque clic, le bot vérifie que le point visé est bien sur le jeu : si
  une autre fenêtre le couvre (panneau, notification, overlay), il n'y clique
  pas.
- Si un bouton reste affiché après `engine.max_ineffective_clicks` clics, le bot
  se met en pause (bip, frame `captures/stuck-*.png`) : `F8` pour reprendre.
- Un score juste sous le seuil sauve une frame `captures/doubt-*.jpg` : c'est le
  signe qu'un template est à recadrer ou que `vision.threshold` est trop haut.
- Si un écran de résultat diffère (nouvel événement, nouvelle saison), il faut
  refaire les templates.
- Anti-cheat : entrées souris classiques au premier plan, messages de fenêtre
  standard (`WM_LBUTTONDOWN`…) au second plan ; rien n'est injecté dans le
  processus du jeu.
- Arrêt inattendu : `logs/bot.log` dit pourquoi le bot s'est arrêté (Quitter,
  `F9`, fermeture par Windows, exception) et `logs/crash.log` garde la pile des
  threads lors d'un plantage natif ou d'un moteur bloqué.
- Le backend `dxcam` ne libère pas sa sortie DXGI en fin de processus
  (`release()` plante sur Python 3.13) ; la sortie est volontairement dure
  (`os._exit`) dans `__main__.py`.

## Documentation

- `docs/ARCHITECTURE.md` — découpage des modules et flux d'exécution
- `docs/TEMPLATES.md` — comment capturer / valider les templates
- `docs/ROADMAP.md` — ce qui est fait, ce qui reste à faire
- `CHANGELOG.md` — notes de chaque version, reprises par la landing et le manifeste

## Empaquetage

```powershell
python packaging/build.py
```

Construit `dist/Fortnite AutoReplay/` (PyInstaller, sans console) puis
`dist/FortniteAutoReplay-<version>.zip`, à distribuer tel quel : l'exécutable,
`config/default.json`, `templates/` et `Lisez-moi.txt`. Lancé sans commande,
`Fortnite AutoReplay.exe` ouvre le panneau ; les commandes de la CLI restent
possibles (`"Fortnite AutoReplay.exe" check`). Depuis l'exécutable, config,
logs et captures se lisent et s'écrivent à côté de lui.

## Mises à jour

L'exécutable vérifie au lancement, puis toutes les six heures,
`https://autoreplay.feitanx.com/latest.json` (section `update` de la config ;
`"enabled": false` coupe la vérification). Ce manifeste donne la version, les
notes, l'adresse, la taille et le sha256 de l'archive. Il est signé en Ed25519
et seules les clés de `updater.PUBLIC_KEYS` sont acceptées (une seule, sauf le
temps d'un changement de clé).

Un clic sur « Mettre à jour » dans le panneau télécharge et vérifie l'archive
dans `.update/`, ferme le panneau, remplace le programme (exécutable,
`_internal`, `templates`, `config/default.json`, `Lisez-moi.txt`) en gardant
`config/local.json`, `logs` et `captures`, puis relance la nouvelle version, qui
efface `.update/`. En cas d'échec, l'ancienne version est remise en place et
relancée. En ligne de commande : `"Fortnite AutoReplay.exe" update [--install]`.

## Publier une version

1. Monter la version (`src/fortnite_auto_replay/__init__.py` et
   `pyproject.toml`) et ajouter sa section en haut de `CHANGELOG.md`.
2. `python packaging/release.py build` : zip, landing (`site/` rendu dans
   `dist/site/`) et `latest.json` signé.
3. `python packaging/update_e2e.py dist/FortniteAutoReplay-<précédente>.zip
   dist/FortniteAutoReplay-<nouvelle>.zip` : vraie mise à jour, sur un bureau
   Windows caché (aucun panneau ne doit tourner).
4. `python packaging/release.py publish` : envoie `dist/site/` sur le VPS
   (`/data/autoreplay`), l'archive d'abord et le manifeste en dernier.

Sur le VPS, un conteneur nginx sert `/data/autoreplay` derrière Traefik
(Coolify) et Cloudflare, avec `packaging/nginx.conf` : `latest.json` et la
page jamais mis en cache, archives et feuille de style (versionnées) gardées
un an.

## Clé de signature

La clé privée de signature est chiffrée par DPAPI dans
`%USERPROFILE%\.secrets\fortnite-autoreplay-release.dpapi` (créée une fois par
`release.py keygen`), lisible par ce compte Windows sur ce PC seulement. Sans
elle, aucune nouvelle version ne peut plus atteindre les applications déjà
distribuées : elle se sauvegarde hors de ce PC. Aucune commande n'affiche la
clé privée ni ne l'écrit en clair.

### Sauvegarder et restaurer

```powershell
python packaging/release.py export-key D:\fortnite-autoreplay-cle.json
```

demande deux fois une phrase de passe (12 caractères au moins, rien ne
s'affiche à la frappe) et écrit la clé chiffrée par elle : scrypt (128 Mio,
environ 0,3 s par essai, coûteux à deviner en masse) puis AES-256-GCM. Le
fichier est relu et déchiffré avant la fin de la commande, et un fichier qui
existe déjà n'est jamais remplacé. Il donne aussi la clé publique en clair,
pour savoir quelle clé il contient sans le déchiffrer. Le garder hors de ce PC
(clé USB, stockage en ligne), et la phrase de passe ailleurs que lui
(gestionnaire de mots de passe).

Après une réinstallation de Windows, ou sur un autre PC :

```powershell
python packaging/release.py import-key D:\fortnite-autoreplay-cle.json
```

remet la clé sous DPAPI pour ce compte, s'il n'y en a pas déjà une. Une phrase
de passe fausse, ou un fichier modifié, est refusé. La commande dit si la clé
publique importée est bien dans `updater.PUBLIC_KEYS`.

### Changer de clé

Une application installée ne fait confiance qu'aux clés de sa propre
`updater.PUBLIC_KEYS`. Pour qu'elle passe à une nouvelle clé, il faut une
version signée par l'ancienne qui lui apprend la nouvelle, puis une version
signée par la nouvelle. `release.py build` vérifie toujours le manifeste avec
la **première** clé de la liste, celle que connaissent les applications déjà
distribuées.

1. Créer la nouvelle clé à côté de l'actuelle, puis la sauvegarder :

   ```powershell
   python packaging/release.py keygen --key "$env:USERPROFILE\.secrets\fortnite-autoreplay-release-2.dpapi"
   python packaging/release.py export-key --key "$env:USERPROFILE\.secrets\fortnite-autoreplay-release-2.dpapi" D:\fortnite-autoreplay-cle-2.json
   ```

2. Version de passage : ajouter la clé publique affichée **après** l'actuelle,
   `PUBLIC_KEYS = ("<actuelle>", "<nouvelle>")`, puis publier comme
   d'habitude. `build` signe avec l'actuelle (clé par défaut) : toutes les
   installations l'acceptent, et celles qui s'y mettent à jour acceptent
   ensuite les deux clés.
3. Laisser aux installations le temps de passer à cette version : elles la
   voient au lancement ou dans les six heures, mais ne l'installent qu'au clic
   sur « Mettre à jour ».
4. Version de bascule : `PUBLIC_KEYS = ("<nouvelle>",)`, construite avec la
   nouvelle clé (`release.py build --key "$env:USERPROFILE\.secrets\fortnite-autoreplay-release-2.dpapi"`),
   puis publiée. Une installation restée avant la version de passage refuse
   désormais toute mise à jour : il faut la remplacer à la main depuis le site.
5. Faire de la nouvelle clé la clé par défaut, l'ancienne ne servant plus :

   ```powershell
   Move-Item -Force "$env:USERPROFILE\.secrets\fortnite-autoreplay-release-2.dpapi" "$env:USERPROFILE\.secrets\fortnite-autoreplay-release.dpapi"
   ```

   La sauvegarde `fortnite-autoreplay-cle-2.json` devient la seule à garder.

Si l'ancienne clé a fuité, publier la bascule juste après la version de
passage : tant
qu'une installation accepte l'ancienne clé, quelqu'un qui la détient et
contrôle aussi le site peut lui faire installer autre chose. Si l'ancienne clé
est perdue (ni DPAPI ni sauvegarde), aucune version de passage n'est possible :
la nouvelle version, signée par une nouvelle clé, s'installe à la main sur
chaque PC.
