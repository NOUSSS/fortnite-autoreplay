---
version: 1
slug: "src-fortnite-auto-replay-panel-py"
primary_target: "src/fortnite_auto_replay/panel.py"
related_targets: []
---

## Scope

Panneau de contrôle du bot (`fnreplay panel`), mode Operate. Une fenêtre tkinter
sans bordure, toujours au-dessus de Fortnite, qui ne prend jamais le focus et
reste invisible pour la capture d'écran du bot.

## Audience and task

Le joueur, Fortnite ouvert en permanence. Il veut savoir d'un coup d'œil si le
bot travaille, le lancer ou le mettre en pause, choisir la réponse au dialogue
« Vous êtes toujours là ? », garder une trace des derniers clics, et voir ses
coffres comptés, qui ne dépendent pas de la pause. Le panneau ne doit pas
prendre de place sur le jeu : repli et dépli soignés.

## Direction contract

THESIS: Le standard de la catégorie, exécuté avec soin : un widget sombre façon
Xbox Game Bar et réglages de Windows 11. Refuse la carte décorative et le
tableau de bord : une ligne par fonction, deux lignes qui se suffisent repliées.

OWN-WORLD: Fond #202020, couche #2B2B2B, filet #3A3A3A, texte #FFFFFF, secondaire
#C5C5C5, accent #60CDFF sur texte noir ; succès #6CCB5F, attention #FCE100,
critique #FF99A4, neutre #9E9E9E. Segoe UI Variable, icônes Segoe Fluent
Icons. Rayons 8 (fenêtre) et 4 (contrôles). Grille de 4 px.

STORY: Le joueur voit l'état de la relance et le total de ses coffres, appuie
sur Pause ou F8 (les coffres restent comptés), déplie pour régler la réponse au
dialogue ou lire les clics, replie.

FIRST VIEWPORT: Deux lignes de 52 px, séparées par un filet. Relance auto :
pastille 8 px, nom en 14 semi-gras, état en 12 secondaire dessous, bouton
d'accent Pause/Lancer et chevron de repli à droite. Coffres : même forme, total
en 14 semi-gras sur la ligne du nom avant son propre chevron, dernière partie
dessous. Chaque ligne se déplie à part :
sous la relance, bandeau d'alerte si bloqué, segment Prêt / Pas encore /
Ignorer, derniers clics ; sous les coffres, parties comptées, bandeau et
dernières parties ; puis
Quitter.

FORM: canon (standard de la catégorie), choisi par le joueur. seed:946c8c0fe0ffcd94

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
