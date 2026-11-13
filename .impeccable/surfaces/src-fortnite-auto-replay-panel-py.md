---
version: 1
slug: "src-fortnite-auto-replay-panel-py"
primary_target: "src/fortnite_auto_replay/panel.py"
related_targets: []
---

## Scope

Panneau de contrôle du bot (`fnreplay panel`), mode Operate. Une fenêtre Qt (PySide6)
sans bordure, toujours au-dessus de Fortnite, qui ne prend jamais le focus et
reste invisible pour la capture d'écran du bot.

## Audience and task

Le joueur, Fortnite ouvert en permanence. Il veut savoir d'un coup d'œil si le
bot travaille, activer ou désactiver la relance, choisir la réponse au dialogue
« Vous êtes toujours là ? », garder une trace des derniers clics, et voir ses
coffres comptés, qui ne dépendent pas de la relance. Le panneau ne doit pas
prendre de place sur le jeu : une pastille au repos, la carte au survol.

## Direction contract

THESIS: Discret. Au repos, une pastille qui dit l'état de la relance et le total
des coffres ; au survol, la carte entière ; les réglages à côté. Refuse le
tableau de bord et le chiffre décoratif.

OWN-WORLD: Verre : le jeu flouté sous l'encre #0B1715 à 70 %, contrôles et
filets en voiles blancs (7 à 12 %) ; texte #F4F0E6,
secondaire #B8C3BB, Activer en blanc #F4F0E6 sur encre, segment choisi en voile ; succès #7BD88F, attention
#FF9A4D, critique #FF7B7B, neutre #7D8D86. Figtree et Bricolage Grotesque
(livrées, OFL), icônes dessinées. Carte et popover à 12 px, bandeaux à 8 px,
pastille et contrôles en pilule de 32 px. Grille de 4 px.

STORY: Le joueur lit la pastille du coin de l'œil, la survole pour la carte,
clique Activer ou Désactiver (ou F8 ; les coffres restent comptés), ouvre les
Réglages pour l'opacité, le mode au repos ou la réponse au dialogue.

FIRST VIEWPORT: Une pastille de 32 px : pastille d'état, « Relance active », filet,
boîte et total. Au survol, la carte de 320 px : Relance auto (nom en Bricolage,
bouton Activer en blanc ou Désactiver), bandeau éventuel, trois
derniers clics ; filet ; Coffres (total à droite, ce qui
est en cours dessous), bandeau, trois dernières
parties ; filet ; Réglages et Quitter. Réglages dans un popover à côté : au
repos, opacité au repos, réponse au dialogue, raccourcis.

FORM: Discret, choisi par le joueur parmi trois prototypes (Natif, Instrument, Discret). seed:946c8c0fe0ffcd94

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
