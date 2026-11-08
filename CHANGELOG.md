# Nouveautés

Une section par version, la plus récente en haut, au format
`## X.Y.Z (AAAA-MM-JJ)` puis une note par ligne `- …`. La landing les affiche
toutes, et le manifeste de mise à jour reprend celles de la dernière version
(`packaging/release.py`).

## 0.3.0 (2026-11-08)

- Le panneau estime les coffres que vous avez ouverts, au-delà des 1 000 de la quête de la chasse au trésor : il lit vos parties dans les journaux du jeu et les compare aux paliers de la quête, à noter dans `config/local.json` (voir le README).

## 0.2.0 (2026-11-08)

- L’application se met à jour d’elle-même : elle vérifie au lancement, puis toutes les six heures, si une version plus récente existe. Un clic sur « Mettre à jour » dans le panneau la télécharge et l’installe, sans toucher à vos réglages.
- Chaque version est signée : une archive modifiée ou venue d’ailleurs est refusée.

## 0.1.1 (2026-11-08)

- Le bot clique aussi sur « Jouer », le bouton du salon quand vous jouez seul ou menez le groupe.

## 0.1.0 (2026-11-08)

- Première version : retour au salon, « Continuer » et « Prêt » en fin de partie, réponse au dialogue « Vous êtes toujours là ? », panneau de contrôle au-dessus du jeu.
