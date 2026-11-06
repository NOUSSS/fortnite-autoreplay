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

## À faire

- [ ] Capturer les templates réels sur l'écran de l'utilisateur
- [ ] Calibrer `vision.threshold` et les cooldowns sur des parties réelles
- [ ] Ajouter les états manquants (écran de fin, « Revanche », popups)
- [ ] Watchdog : détecter « bloqué sur un écran inconnu » et sauver la frame
- [ ] Sauvegarde automatique d'une frame quand une détection est douteuse
- [ ] Détection de la zone de jeu (fenêtre Fortnite) au lieu d'un ROI manuel
- [ ] Support multi-résolution (dossiers de templates par résolution)
- [ ] Compteur de parties / statistiques persistantes
- [ ] Notification (son / Discord) quand le bot s'arrête ou bloque
- [ ] Packaging en exécutable (`pyinstaller`) et démarrage en administrateur
- [ ] Interface de configuration (TUI ou fenêtre)

## Idées long terme

- Reprise après crash du client
- Mode « AFK XP » vs « farm de parties »
- OCR sur les écrans pour vérifier l'état réel de la partie
