---
name: Fortnite AutoReplay
description: Panneau de contrôle de bureau du bot de rejeu, une pastille discrète qui s'ouvre au survol.
colors:
  bg: "#11201E"
  layer: "#1A2C29"
  layer-hover: "#22372F"
  layer-pressed: "#162622"
  stroke: "#2A403C"
  text: "#F4F0E6"
  text-secondary: "#B8C3BB"
  text-tertiary: "#7D8D86"
  accent: "#F0B93A"
  accent-hover: "#F5C65A"
  accent-pressed: "#E0A82C"
  on-accent: "#1E1604"
  success: "#7BD88F"
  caution: "#FF9A4D"
  critical: "#FF7B7B"
  neutral: "#7D8D86"
  caution-bg: "#3A2A17"
  critical-bg: "#351C1C"
typography:
  title:
    fontFamily: "Bricolage Grotesque 14pt"
    fontSize: "14px"
    fontWeight: 700
    lineHeight: "19px"
  strong:
    fontFamily: "Figtree SemiBold"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: "19px"
  body:
    fontFamily: "Figtree"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "19px"
  heading:
    fontFamily: "Figtree SemiBold"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: "16px"
  caption:
    fontFamily: "Figtree"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: "16px"
  icon:
    fontFamily: "Segoe Fluent Icons, Segoe MDL2 Assets"
    fontSize: "12px"
rounded:
  window: "8px"
  notice: "8px"
  control: "999px"
spacing:
  heading: "4px"
  gap: "8px"
  pad: "12px"
  section: "12px"
  setting: "16px"
components:
  pill:
    backgroundColor: "{colors.bg}"
    height: "32px"
    padding: "0 14px 0 12px"
  button-accent:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.strong}"
    rounded: "{rounded.control}"
    height: "32px"
    minWidth: "104px"
  button-standard:
    backgroundColor: "{colors.layer}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "0 14px"
  button-subtle:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "0 12px"
  segmented:
    backgroundColor: "{colors.layer}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "3px"
  segmented-selected:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.strong}"
  slider:
    trackHeight: "6px"
    trackColor: "{colors.layer-hover}"
    fillColor: "{colors.accent}"
    thumb: "18px {colors.text}"
  notice:
    rounded: "{rounded.notice}"
    padding: "10px 12px"
  feature-row:
    height: "52px"
    width: "320px"
  popover:
    width: "272px"
    padding: "12px"
  data-row:
    height: "24px"
---

# Design System: Fortnite AutoReplay

## Overview

**Creative North Star : « La pastille discrète »**

Le panneau ne doit pas gêner le jeu. Au repos, il se réduit à une pastille de 32 px de haut : la pastille d'état de la relance, son état en quelques mots (« Relance active »), puis le total des coffres derrière une icône de boîte. Quand le curseur passe dessus, la carte entière s'ouvre à la même place. Elle se referme 0,4 s après le départ du curseur. Sans la souris, la fenêtre prend l'opacité choisie (80 % par défaut, de 20 à 100 %) ; au survol, elle redevient opaque.

La palette est une encre vert sombre, chaude, posée sur le jeu. L'or marque ce qui agit (Activer) ou ce qui est choisi (segment sélectionné, part remplie du curseur). Figtree porte le texte et Bricolage Grotesque, plus affirmée, les noms des fonctions et le total des coffres. Ces deux polices libres (OFL) sont livrées avec l'application et chargées pour elle seule au démarrage. Les icônes et les pastilles viennent de Segoe Fluent Icons, dont les glyphes sont lissés.

Le rendu est mesuré à 96 ppp et chaque valeur de grille est multipliée par l'échelle d'affichage du système (`px()`), sur une grille de 4 px.

**Key Characteristics:**
- Au repos, une pastille ; au survol, la carte entière ; les réglages dans un popover à côté.
- Une ligne de 52 px par fonction, chacune avec son état : désactiver la relance ne touche pas aux coffres.
- Boutons et sélecteurs en pilule de 32 px ; le bouton de la relance dit ce qu'il fera (Activer ou Désactiver).
- Deux tailles de texte (14 et 12 px) ; la hiérarchie passe par la police, la graisse et la couleur.
- Aucune ombre dessinée : le survol et l'appui changent la surface.

## Colors

Une encre vert sombre presque monochrome, un seul accent or, et quatre couleurs d'état réservées à l'état.

### Primary
- **Or** (accent) : remplissage du bouton Activer, du segment choisi et de la part remplie du curseur d'opacité. Le texte posé dessus est toujours l'encre sombre (on-accent). Survol et appui changent l'or par paliers (accent-hover, accent-pressed).

### Tertiary
- **Vert succès** (success) : pastille de la relance Active et des coffres comptés normalement.
- **Orangé attention** (caution) : pastille des états En attente du jeu, Clic annulé et Clic en attente, des coffres quand les rediffusions sont désactivées, le lecteur indisponible, le dernier replay illisible ou la partie interrompue ; icône du bandeau d'attention.
- **Corail critique** (critical) : pastille des états Bloqué et Capture impossible, icône du bandeau critique.
- **Gris neutre** (neutral) : pastille des états Désactivé et Arrêté, et des coffres pendant la première lecture des replays.
- **Brun sombre** (caution-bg) et **brique sombre** (critical-bg) : fonds des bandeaux d'attention et critique, seuls fonds colorés du panneau.

### Neutral
- **Encre** (bg) : fond de la pastille, de la carte et du popover ; fond au repos des boutons discrets.
- **Couche** (layer) : surface des boutons standard, piste des sélecteurs, fond du bandeau d'information, survol des boutons discrets.
- **Couche survolée** (layer-hover) et **couche appuyée** (layer-pressed) : états des contrôles neutres ; la piste vide du curseur prend la couche survolée.
- **Filet** (stroke) : contour des contrôles neutres, séparateurs d'un pixel entre les blocs de la carte (inscrits à 12 px des bords), séparateur de la pastille, bordure de la fenêtre posée par DWM.
- **Texte** (text) : noms des fonctions, total des coffres, libellés du journal, des boutons et des segments survolés, poignée du curseur.
- **Texte secondaire** (text-secondary) : état sous le nom d'une fonction, libellés des segments au repos, bouton Réglages au repos, icône de boîte.
- **Texte tertiaire** (text-tertiary) : intertitres, heures du journal, état vide, aide sous le dialogue, rappel des raccourcis.

### Named Rules
**La règle de la pastille.** Les couleurs d'état ne colorent qu'une pastille de 8 px ou l'icône d'un bandeau. Jamais un texte, un bouton ni un fond de section.

**La règle de l'or qui agit.** L'or marque ce qui agit ou ce qui est choisi : le bouton Activer, le segment sélectionné, la valeur du curseur. Désactiver n'est pas en or : c'est un bouton standard.

## Typography

**Police de texte :** Figtree (repli Segoe UI Variable Text)
**Police des titres :** Bricolage Grotesque, coupe 14 pt, graisse 700 (repli Figtree SemiBold)
**Icônes :** Segoe Fluent Icons (repli Segoe MDL2 Assets)

**Character :** Figtree, ronde et lisible à 12 px, pour tout ce qui se lit ; Bricolage Grotesque, plus dessinée, pour les deux noms de fonction et le total des coffres. Les fichiers (`src/fortnite_auto_replay/fonts`) sont chargés par `AddFontResourceEx` en privé : rien n'est installé dans Windows. Sans eux, le panneau retombe sur Segoe UI.

### Hierarchy
- **Title** (Bricolage 700, 14 px) : nom d'une fonction (Relance auto, Coffres), total des coffres.
- **Strong** (Figtree SemiBold, 14 px) : bouton Activer / Désactiver, segment choisi, total dans la pastille.
- **Body** (Figtree, 14 px) : état dans la pastille, journal, libellés des boutons et segments, message des bandeaux.
- **Heading** (Figtree SemiBold, 12 px, texte tertiaire) : intertitres (Derniers clics, Dernières parties, Au repos, Opacité au repos et sa valeur, Vous êtes toujours là ?).
- **Caption** (Figtree, 12 px) : état sous le nom d'une fonction, aide sous le dialogue, rappel des raccourcis.
- **Icon** (12 à 14 px) : icône des bandeaux, boîte de la pastille, curseurs du bouton Réglages. La pastille d'état est le disque plein de la même police, à 8 px ; la poignée du curseur, le même disque à 18 px.

### Named Rules
**La règle des deux tailles.** 14 et 12 px, rien d'autre. Un nom de fonction se distingue par sa police, un intertitre par la graisse et la couleur, jamais par une taille de plus.

**La règle du texte français.** Espace insécable avant « : ; ? ! » et dans les guillemets, séparateur de milliers en espace insécable, apostrophe typographique, ni capitales ni lettres espacées.

## Layout

### Pastille (repos)
32 px de haut, largeur au contenu. 12 px à gauche, 14 px à droite. Pastille d'état de 8 px, 8 px, état court (Relance active, Relance désactivée, Relance bloquée, En attente du jeu…), puis, si les coffres sont comptés : 8 px, filet vertical de 12 px, 8 px, icône de boîte, 4 px, total en Strong. La fenêtre garde son coin haut gauche quand elle s'ouvre en carte.

### Carte (survol)
320 px de large, hauteur au contenu, gardée entière dans la zone de travail de l'écran.

- **Ligne d'une fonction** : 52 px. Pastille à 12 px du bord, 8 px, puis le nom (19 px) et l'état dessous (16 px), bloc centré en hauteur. À droite, à 12 px du bord : le bouton Activer / Désactiver (32 px, largeur fixe de son plus long libellé, 104 px au moins) pour la relance, le total en Title sur la ligne du nom pour les coffres.
- **Détail d'une fonction** : marges de 12 px, à 4 px sous la ligne. Bandeau, puis 12 px, puis la liste ; 12 px avant le filet suivant. Pas de détail des coffres tant qu'il n'y a ni bandeau ni partie comptée.
- **Journal** : intertitre, 4 px, rangées de 24 px. Colonne du moment à la largeur de sa forme la plus large dans la police (« 00:00 », « 00 sept. 00:00 »), puis 12 px avant le libellé ; trois lignes au plus (derniers clics, dernières parties).
- **Pied** : filet ; le bandeau de mise à jour s'il y en a un, 12 px plus bas ; puis, 8 px sous lui et 12 px au-dessus du bas, Réglages à gauche et Quitter à droite.
- **Filets** : 1 px, inscrits à 12 px des bords, entre relance et coffres et au-dessus du pied.

### Popover des réglages
272 px de large, marge intérieure de 12 px, posé à 8 px de la carte, à droite (à gauche s'il n'y a pas la place), bas aligné sur celui de la carte. Réglages séparés de 16 px : Au repos (Pastille ou Panneau), Opacité au repos (intertitre et valeur sur la même ligne, curseur de 20 px de haut), Vous êtes toujours là ? (sélecteur, aide 8 px dessous s'il manque un modèle), rappel des raccourcis.

### Named Rules
**La règle de la pastille qui suffit.** Au repos, la pastille dit si la relance tourne et combien de coffres sont comptés. Tout le reste attend le survol.

**La règle du survol qui ne clignote pas.** La carte s'ouvre dès l'entrée du curseur et ne se referme que 0,4 s après sa sortie : passer de la carte au popover, ou déborder d'un pixel, ne la ferme pas. En partie, quand le jeu tient le curseur (caché ou bloqué), le panneau ne s'ouvre pas : la souris le traverse.

## Elevation & Depth

Le système est plat. Aucune ombre n'est dessinée : la profondeur vient de l'écart entre l'encre et la couche des contrôles, et de filets d'un pixel. Les fenêtres (pastille, carte, popover) reçoivent de Windows (DWM) leurs coins arrondis de 8 px et une bordure à la couleur du filet. L'opacité au repos est celle de toute la fenêtre (`SetLayeredWindowAttributes`).

### Named Rules
**La règle de la surface qui change.** Le survol et l'appui changent la couleur de surface du contrôle (couche survolée, couche appuyée, paliers de l'or). Jamais de halo, d'ombre ni de déplacement.

## Shapes

Deux familles : la fenêtre et les bandeaux à 8 px (arrondi standard de Windows 11 demandé à DWM, le même dessiné pour les bandeaux), les contrôles en pilule (rayon de la moitié de leur hauteur). Dans un sélecteur, le segment choisi est inscrit à 3 px de la piste, en pilule lui aussi. Les seules lignes sont les filets d'un pixel et le contour des contrôles neutres.

## Components

### Boutons
Pilules de 32 px.
- **Activer** : or, texte encre en Strong. Proposé quand la relance est désactivée, bloquée ou arrêtée.
- **Désactiver** : couche, contour filet, texte en Strong. Même largeur qu'Activer : le bouton ne change jamais de taille.
- **Standard** : couche, contour filet, texte en Body (Quitter, Mettre à jour, Réessayer).
- **Discret** : encre, texte secondaire, icône à 8 px du libellé (Réglages). Survolé ou ouvert : couche et texte.
- **États** : survol et appui changent la surface ; le clic part au relâchement, si le pointeur est resté sur le bouton. Désactivé (moteur arrêté) : tout le bouton à 40 % sur l'encre, curseur interdit.

### Sélecteur à segments
Piste couche avec contour filet, pilule de 32 px, segments de largeur égale. Segment choisi en or avec libellé Strong encre ; survol et appui en couche survolée et appuyée, libellé en texte ; au repos, libellé en texte secondaire ; segment sans modèle en texte tertiaire, non cliquable, expliqué par l'aide dessous.

### Curseur d'opacité
Piste de 6 px en pilule (couche survolée), part remplie en or jusqu'à la poignée, poignée de 18 px en texte. De 20 à 100 %, par pas de 5. Pendant le réglage, la carte prend l'opacité choisie pour la montrer, et la retrouve 0,7 s après le dernier mouvement ; la valeur est enregistrée au relâchement.

### Bandeau
Pleine largeur utile, coins de 8 px, 12 px de marge sur les côtés et 10 px en haut et en bas, icône de gravité de 12 px puis 8 px avant le message en Body texte.
- **Gravités** : critique (fond brique, icône corail), attention (fond brun, icône orangée), information (fond couche, icône texte secondaire).
- **Action** : un bouton standard facultatif sous le message, aligné sur lui, 8 px plus bas (Mettre à jour, Réessayer). Jamais l'or.
- **Présence** : seulement s'il a quelque chose à dire. Celui du moteur sous la ligne de la relance ; celui des coffres sous la leur ; celui de la mise à jour au-dessus du pied.

### Ligne de fonction (signature)
Pastille de 8 px à la couleur de l'état de la fonction, nom en Title, état dessous en Caption secondaire, et à droite ce que la fonction offre. Chaque ligne, la pastille du repos comprise, sert de poignée pour déplacer la fenêtre.

### Journal
Rangées de 24 px sans fond ni séparateur : moment en texte tertiaire, libellé en texte. Derniers clics sous la relance (« Aucun clic pour l'instant. » sinon), dernières parties (début et coffres) sous les coffres, absentes tant qu'aucune partie n'est comptée.

## Do's and Don'ts

### Do:
- **Do** tirer chaque couleur, taille et écart des jetons ci-dessus, multipliés par l'échelle d'affichage.
- **Do** dire avec le bouton ce qu'il va faire : Activer ou Désactiver, jamais Pause, Reprendre ou Lancer.
- **Do** réserver les couleurs d'état à la pastille et à l'icône des bandeaux.
- **Do** donner aux contrôles voisins la même hauteur (32 px) et au bouton de la relance une largeur fixe.
- **Do** dire un état bloqué ou une erreur dans un bandeau, avec la marche à suivre.
- **Do** donner son propre état à chaque fonction : un état global laisserait croire que désactiver la relance arrête aussi les coffres.

### Don't:
- **Don't** ajouter d'ombre, de halo ou de lueur à un bouton, au repos comme au survol.
- **Don't** poser des cartes ou des fonds de section dans la carte : le détail reste sur l'encre.
- **Don't** afficher de chiffre décoratif (clics de la session, nombre de parties depuis une date, totaux de toutes les sessions) : seuls les derniers clics et les coffres servent.
- **Don't** ajouter une troisième taille de texte.
- **Don't** écrire un libellé en capitales ou en lettres espacées, ni ajouter « → » à un bouton.
- **Don't** ouvrir la carte quand le jeu tient le curseur : elle couvrirait la partie.
