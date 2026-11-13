# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

Note : le seul écran est un panneau de bureau Windows (tkinter), pas une page
web. La valeur `web` est celle par défaut de l'outil ; aucune convention iOS ni
Android ne s'applique.

## Users

Une seule personne : le joueur, sur son propre PC Windows, Fortnite ouvert en
permanence sur l'écran principal (1920 × 1080). Il lance le bot puis joue ou
s'absente ; il doit savoir d'un coup d'œil si le bot travaille.

## Product Purpose

Bot de rejeu automatique : il reconnaît à l'écran les boutons de fin de partie
et de salon (Retourner au salon, Continuer, Passer, Prêt ou Jouer) et clique dessus, pour enchaîner
les parties sans intervention. Il répond aussi au dialogue « Vous êtes toujours
là ? » d'un groupe (Prêt ou Pas encore, au choix). Réussite : les parties
s'enchaînent, et le joueur sait à tout moment si la relance est active, désactivée,
en attente du jeu ou bloquée.

## Operating Context

- Le panneau se pose par-dessus Fortnite, toujours au-dessus (réponse du
  joueur). Il ne doit jamais prendre le focus. Le bot clique tant que le jeu
  est visible, au premier plan ou non : le joueur peut travailler sur un autre
  écran pendant ce temps.
- Le panneau ne doit pas prendre de place sur le jeu : au repos, une pastille
  (état de la relance, total des coffres), la carte entière au survol, et une
  opacité réglable quand la souris est ailleurs.
- Raccourcis en jeu : F8 active ou désactive la relance, F9 quitte, seulement quand
  Fortnite est au premier plan. Le joueur peut les utiliser sans regarder le
  panneau.
- Le bot capture l'écran du jeu : le panneau doit rester invisible pour cette
  capture et ne pas couvrir un bouton que le bot doit cliquer.

## Capabilities and Constraints

- États de la relance : Actif, Désactivé, En attente du jeu (fenêtre absente ou
  réduite), Clic en attente (souris utilisée), Bloqué (clic sans effet ou
  erreurs répétées), arrêté.
- Informations demandées : deux fonctions, chacune sur sa ligne avec sa
  pastille et son état. La relance, avec le journal des trois derniers clics
  pour garder une trace. Les coffres ouverts, cassés compris, comptés dans
  les replays du jeu (rediffusions activées dans Fortnite) : ils se comptent
  aussi quand la relance est désactivée, le panneau ne doit pas laisser croire
  le contraire. Ni compteurs de session, ni nombre de parties depuis une date,
  ni totaux de toutes les sessions :
  décoratifs (l'historique reste écrit dans `logs/sessions.jsonl`).
- Réponse au dialogue « Vous êtes toujours là ? » : Prêt (par défaut), Pas
  encore, ou ne rien faire ; modifiable dans le panneau.
- Python 3.10+, tkinter de la bibliothèque standard, Windows uniquement.

## Brand Commitments

Nom : Fortnite AutoReplay. Aucun logo. Texte en français : vouvoiement,
espace insécable avant « : ; ? ! », « … », apostrophe typographique ; pas de
capitales espacées, pas de ton marketing, pas de point d'exclamation.

## Evidence on Hand

Aucune donnée inventée : les derniers clics viennent du moteur, les coffres
des replays réels.

## Product Principles

- Un coup d'œil suffit : l'état se lit avant tout le reste.
- Le jeu d'abord : le panneau ne vole ni le focus ni un clic au jeu.
- Honnête : un état bloqué ou une erreur se montre, avec la marche à suivre.
