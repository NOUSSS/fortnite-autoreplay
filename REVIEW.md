# Relecture

## Exécuter le code sans risque

- Tests : `python -m pytest -q` (capture, clics et fenêtre factices, aucune entrée réelle).
- Lint : `python -m ruff check .` puis `python -m ruff format --check .`.
- Sur la machine réelle, Fortnite peut être ouvert et au premier plan : ne jamais
  lancer `run` sans `--dry-run`, ni l'outil `capture_templates`, ni rien qui
  clique, tape, bouge le curseur ou change le focus. `window` ne fait que lire ; `check` capture
  une frame (envoyer `engine.debug_dir` dans un dossier temporaire).
- Panneau : à sa création, la fenêtre Tk prend le premier plan un instant et
  la garde le rend aussitôt (voir ARCHITECTURE) ; ne l'ouvrir que si personne
  ne joue. Les captures de relecture passent par l'outil `panel_shots` (moteur
  simulé, aucun clic, rien d'enregistré), lancé par WMI pour n'ouvrir aucune
  console :

  ```powershell
  $cmd = "cmd.exe /c start `"`" /wait pythonw -c `"import sys; sys.path.insert(0, 'src'); " +
         "from fortnite_auto_replay.tools.panel_shots import main; main(['--out', '.impeccable/review'])`""
  $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
      CommandLine = $cmd; CurrentDirectory = (Get-Location).Path; ProcessStartupInformation = $startup }
  Wait-Process -Id $r.ProcessId
  ```

  `.impeccable/review/foreground.json` doit dire `"foreground_changed": false`
  (premier plan rendu à la fin de la création, et jamais repris ensuite).
- Mise à jour réelle : `python packaging/update_e2e.py <ancien.zip> <nouveau.zip>`
  (panneau sur un bureau Windows caché, qui ne voit pas le jeu). Le nouveau zip
  se construit en montant la version le temps d'un `python packaging/build.py`.
  Aucun panneau ne doit tourner (mutex commun à la session).
- `release.py` : ne jamais afficher ni écrire ailleurs la clé privée ; `publish`
  écrit sur le VPS et se demande avant. Les tests de `export-key` et
  `import-key` passent `--key` vers `tmp_path` et remplacent `KEY_FILE` et
  `getpass` : jamais la vraie clé, jamais de saisie. Ne lancer `export-key` sur
  la vraie clé qu'à la demande de l'utilisateur, qui tape lui-même la phrase.

## Zones à relire de près

- `engine.py` : ordre des garde-fous dans `_step` (confirmation, cooldown, clic
  sans effet, clic), remise à zéro des séries, état du jeu revérifié dans `_act`.
- Clic au second plan (`actions.BackgroundClicker`, `Engine._act`) : aucun
  chemin ne doit retomber sur une entrée réelle (`SendInput` partirait dans la
  fenêtre active), handle nul compris ; le curseur est remis en place même
  quand l'envoi échoue ; les coordonnées client sont signées dans `lParam`.
  Jamais `WM_ACTIVATE` (peut voler le focus) : seulement `WM_ACTIVATEAPP`, et
  le 0 qui remet le jeu en veille part sur tous les chemins.
- Panneau (`panel.py`) : `set_mouse_through` ne doit jamais rendre le panneau
  traversant quand le jeu n'est pas au premier plan (il deviendrait
  inutilisable) ; `replace_other_panels` ne ferme que des fenêtres Tk de ce
  titre (ou de l'ancien, « Fortnite Auto Replay », `_PANEL_TITLES`) dans un autre processus, et le mutex des panneaux n'est rendu qu'après
  l'arrêt du moteur (sa session écrite), échec de lancement compris.
- Coordonnées : frame → écran (`Frame.to_screen`, `capture.scale`), région
  rognée à l'écran capturé (l'origine de la frame doit suivre), zone connue
  rognée au bord de la frame, `capture.roi` relatif à la zone client du jeu.
- Zones attendues : `templates.prepare` (échelle par la hauteur de fenêtre,
  zone en fraction de frame) et l'ordre de recherche dans `Engine._search`.
- Le second écran de la machine est à gauche (x négatif) : tout calcul qui
  suppose des coordonnées positives est suspect.
- `panel.py` : seul le thread principal touche à Tk ; le moteur ne s'en sert
  pas. Toute fenêtre ou tout appel Win32 ajouté ne doit ni activer le panneau
  (`SetForegroundWindow` sur lui, `focus_force`) ni le rendre visible des
  captures. Un attribut de widget nommé comme une méthode de tkinter
  (`_options`, `_configure`…) casse le widget.
- Garde du clic couvert (`Engine._click_covered`, `GameWindow.covering`) :
  un clic n'est envoyé que si le point visé appartient au processus du jeu.
- `window.py` : handles 64 bits (instances `WinDLL` privées avec `argtypes`),
  fenêtres annexes « Fortnite » (anti-triche, rapport de plantage) à écarter.
- `updater.py` : rien du manifeste n'est utilisé avant `verify_manifest` ;
  `install` remet tout en place à la moindre erreur et ne touche jamais aux
  fichiers de l'utilisateur (`USER_PATHS`) ; l'installation par le panneau ne
  part qu'après l'arrêt du moteur et la destruction de Tk ; `cleanup` ne doit
  effacer que `.update/`.
- `release.py` : l'ordre d'envoi (archives, page, manifeste en dernier) évite
  qu'une application lise un manifeste dont l'archive manque encore ; le
  manifeste écrit est revérifié avec la première clé de `updater.PUBLIC_KEYS`,
  celle des applications déjà distribuées (`verify_for_installed`).
- Sauvegarde de la clé (`seal_key`, `read_backup`, `open_key`) : tout le
  fichier hors `ciphertext` est authentifié par AES-GCM ; `export-key` et
  `import-key` ne remplacent jamais un fichier existant et effacent ce qu'ils
  ont écrit au moindre échec.
