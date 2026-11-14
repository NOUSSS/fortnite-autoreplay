# Nouveautés

Une section par version, la plus récente en haut, au format
`## X.Y.Z (AAAA-MM-JJ)` puis une note par ligne `- …`. La landing les affiche
toutes, et le manifeste de mise à jour reprend celles de la dernière version
(`packaging/release.py`).

## 0.7.0 (2026-11-14)

- Anti-AFK, à activer dans les réglages du panneau : en partie, un geste au hasard toutes les 25 à 50 secondes (touche, clic, coup d’œil autour de vous) pour que le jeu ne vous exclue pas pour inactivité. Tant que vous jouez, il ne fait rien. Jeu en arrière-plan, il a besoin des rediffusions activées (Paramètres, Jeu, Rediffusions) pour savoir qu’une partie est en cours.

## 0.6.0 (2026-11-13)

- Nouveau panneau : au repos, une pastille discrète qui donne l’état de la relance et le total de vos coffres ; le curseur posé dessus ouvre la carte complète. Son fond en verre laisse deviner le jeu, flouté.
- Chaque fonction a sa ligne : désactiver la relance n’arrête pas le compteur de coffres. Sous les coffres, vos dernières parties, et le comptage de celle qui vient de finir.
- F8 ne fait plus de son.

## 0.5.0 (2026-11-10)

- Le panneau compte vraiment vos coffres, partie après partie, au lieu de les estimer : il lit les replays du jeu quelques secondes après chaque partie, coffres cassés compris. Activez l’enregistrement des rediffusions dans Fortnite (Paramètres, Jeu, Rediffusions).
- La connexion au compte Epic et l’estimation disparaissent.

## 0.4.0 (2026-11-08)

- L’estimation des coffres marche pour tout le monde : un clic sur « Se connecter à Epic » dans le panneau suffit à lire les paliers de votre quête, sans rien noter à la main. L’application ne garde ni jeton ni mot de passe.
- L’estimation affiche une fourchette, et un minimum quand les journaux du jeu ne remontent pas jusqu’à votre dernier palier.

## 0.3.0 (2026-11-08)

- Le panneau estime les coffres que vous avez ouverts, au-delà des 1 000 de la quête de la chasse au trésor : il lit vos parties dans les journaux du jeu et les compare aux paliers de la quête, à noter dans `config/local.json` (voir le README).

## 0.2.0 (2026-11-08)

- L’application se met à jour d’elle-même : elle vérifie au lancement, puis toutes les six heures, si une version plus récente existe. Un clic sur « Mettre à jour » dans le panneau la télécharge et l’installe, sans toucher à vos réglages.
- Chaque version est signée : une archive modifiée ou venue d’ailleurs est refusée.

## 0.1.1 (2026-11-08)

- Le bot clique aussi sur « Jouer », le bouton du salon quand vous jouez seul ou menez le groupe.

## 0.1.0 (2026-11-08)

- Première version : retour au salon, « Continuer » et « Prêt » en fin de partie, réponse au dialogue « Vous êtes toujours là ? », panneau de contrôle au-dessus du jeu.
