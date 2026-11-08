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
- [x] Aucune analyse ni clic tant que le jeu est absent ou réduit
- [x] Clic au second plan : messages postés à la fenêtre du jeu, curseur remis en place
- [x] Traces des arrêts inattendus (`logs/crash.log`, cause de sortie au journal)
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

## Fait (panneau et dialogue)

- [x] Réponse automatique au dialogue « Vous êtes toujours là ? » (Prêt par défaut, Pas encore, Ignorer)
- [x] Panneau de contrôle au-dessus du jeu : état, Lancer / Pause, réponse au dialogue, compteurs, derniers clics, totaux
- [x] Panneau repliable sur une ligne, déplaçable, réglages gardés dans `config/local.json`
- [x] Panneau sans prise de focus et invisible aux captures ; clic annulé si une fenêtre couvre le bouton
- [x] Deux notes sur `F8` (reprise, pause)

## Fait (distribution)

- [x] Exécutable PyInstaller et zip à distribuer, `Lisez-moi.txt`
- [x] Mises à jour signées (Ed25519), installées depuis le panneau, avec retour arrière
- [x] Landing `autoreplay.feitanx.com` et outil de publication (`packaging/release.py`)
- [x] Essai de mise à jour de bout en bout sur un bureau caché (`packaging/update_e2e.py`)
- [x] Sauvegarde chiffrée de la clé de signature (`release.py export-key`, `import-key`)
      et procédure de changement de clé (README)

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
- [ ] Démarrage en administrateur (manifeste UAC de l'exécutable)
- [ ] Sauvegarder la vraie clé de signature hors de ce PC (`release.py export-key`)
- [ ] Raccourci sur le Bureau pour le panneau (à créer à la demande)
- [ ] Panneau : vérifier en partie qu'un clic dessus laisse le focus au jeu, et que
  la souris le traverse (Fortnite masque-t-il ou retient-il le curseur en partie ?)
- [ ] Vérifier en partie le clic au second plan (modes `cursor` et `message`), avec
  le réveil `WM_ACTIVATEAPP` : sans lui, le clic sur « Rejouer » n'a pas pris

## Idées long terme

- Reprise après crash du client
- Mode « AFK XP » vs « farm de parties »
- OCR sur les écrans pour vérifier l'état réel de la partie
