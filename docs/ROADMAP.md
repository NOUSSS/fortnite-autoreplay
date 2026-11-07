# Roadmap

## Fait (fondations)

- [x] Capture d'écran multi-backend avec repli (`dxcam` → `mss` → `PIL`)
- [x] Vision par template matching multi-échelle (OpenCV + repli numpy)
- [x] Clics souris multi-backend (`pydirectinput` → `SendInput` → `pyautogui`)
- [x] Machine à états priorisée, confirmations, cooldowns, stats
- [x] Configuration typée, validée, surchargeable (`local.json`, CLI)
- [x] Pause/reprise/arrêt par raccourcis globaux, mode dry-run
- [x] Outil de capture de templates guidé
- [x] Commande `check` pour valider la détection
- [x] Tests unitaires des briques (vision, config, templates, frames)

## Fait (garde-fous)

- [x] Détection de la fenêtre Fortnite (exécutable + classe) au lieu d'un ROI manuel
- [x] Aucune analyse ni clic tant que le jeu n'est pas au premier plan
- [x] Pause + bip + frame sauvée quand un clic reste sans effet
- [x] Sauvegarde d'une frame quand une détection est douteuse (score sous le seuil)
- [x] Historique des sessions (`logs/sessions.jsonl`) et commande `stats`
- [x] Commande `window` (diagnostic de la fenêtre et du focus)
- [x] Coordonnées en pixels physiques (DPI per-monitor)
- [x] Tests du moteur de bout en bout avec capture et clics factices
- [x] Correctif : la ROI glissante ne retombait jamais sur la frame entière

## Fait (performance et fiabilité)

- [x] Zones attendues par template (JSON écrit par l'outil de capture) + balayage complet périodique
- [x] Mise à l'échelle automatique des templates à la résolution de la fenêtre
- [x] Analyse à demi-échelle, gris calculé une fois par frame (~11 ms au lieu de 300 à 1 000)
- [x] dxcam : dernière image réutilisée quand l'écran est immobile
- [x] dxcam : écran choisi par coordonnées, origine de frame juste si le jeu déborde
- [x] Réouverture de la capture si le jeu change d'écran ou après plusieurs échecs
- [x] Une erreur inattendue ne tue plus la boucle (pause après plusieurs de suite)
- [x] Outil de capture : même chaîne que le bot, délai pour écarter la souris, aucun écrasement

## À faire

- [ ] Capturer les templates réels sur l'écran de l'utilisateur
- [ ] Calibrer `vision.threshold` et les cooldowns sur des parties réelles
- [ ] Ajouter les états manquants (écran de fin, « Revanche », popups)
- [ ] Écran inconnu : impossible à distinguer d'une partie en cours sans
      reconnaître l'écran de jeu lui-même (HUD) ; à faire avec un état « en jeu »
- [ ] Écarter le curseur du bouton après le clic (le curseur du jeu peut
      masquer le template et faire baisser le score)
- [ ] Limiter les threads OpenCV pendant les parties, si le balayage complet
      gêne le jeu (à mesurer machine au repos)
- [ ] Compteur de parties (quel clic marque le début d'une partie ?)
- [ ] Notification Discord quand le bot se met en pause
- [ ] Packaging en exécutable (`pyinstaller`) et démarrage en administrateur
- [ ] Interface de configuration (TUI ou fenêtre)

## Idées long terme

- Reprise après crash du client
- Mode « AFK XP » vs « farm de parties »
- OCR sur les écrans pour vérifier l'état réel de la partie
