---
name: Fortnite AutoReplay
description: Panneau de contrôle de bureau du bot de rejeu, au thème sombre de Windows 11.
colors:
  bg: "#202020"
  layer: "#2B2B2B"
  layer-hover: "#323232"
  layer-pressed: "#272727"
  stroke: "#3A3A3A"
  text: "#FFFFFF"
  text-secondary: "#C5C5C5"
  text-tertiary: "#9E9E9E"
  accent: "#60CDFF"
  accent-hover: "#5BBAE8"
  accent-pressed: "#56A8D1"
  on-accent: "#000000"
  success: "#6CCB5F"
  caution: "#FCE100"
  critical: "#FF99A4"
  neutral: "#9E9E9E"
  caution-bg: "#433519"
  critical-bg: "#442726"
typography:
  title:
    fontFamily: "Segoe UI Variable Text Semibold, Segoe UI Semibold, Segoe UI"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: "19px"
  body:
    fontFamily: "Segoe UI Variable Text, Segoe UI"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "19px"
  caption:
    fontFamily: "Segoe UI Variable Small, Segoe UI"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: "16px"
  icon:
    fontFamily: "Segoe Fluent Icons, Segoe MDL2 Assets"
    fontSize: "12px"
rounded:
  control: "4px"
  segment-inner: "3px"
  window: "8px"
spacing:
  inset: "2px"
  gap: "8px"
  pad: "12px"
  section: "16px"
components:
  button-accent:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "28px"
  button-accent-hover:
    backgroundColor: "{colors.accent-hover}"
  button-accent-pressed:
    backgroundColor: "{colors.accent-pressed}"
  button-standard:
    backgroundColor: "{colors.layer}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "28px"
  button-standard-hover:
    backgroundColor: "{colors.layer-hover}"
  button-standard-pressed:
    backgroundColor: "{colors.layer-pressed}"
  button-subtle:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.icon}"
    rounded: "{rounded.control}"
    size: "28px"
  button-subtle-hover:
    backgroundColor: "{colors.layer-hover}"
  button-subtle-pressed:
    backgroundColor: "{colors.layer-pressed}"
  segmented:
    backgroundColor: "{colors.layer}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "2px"
    height: "32px"
  segmented-selected:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.title}"
    rounded: "{rounded.segment-inner}"
  segmented-disabled:
    textColor: "{colors.text-tertiary}"
  notice-critical:
    backgroundColor: "{colors.critical-bg}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "12px"
  notice-caution:
    backgroundColor: "{colors.caution-bg}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "12px"
  notice-info:
    backgroundColor: "{colors.layer}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "12px"
  header:
    backgroundColor: "{colors.bg}"
    height: "44px"
    width: "320px"
    padding: "0 12px"
  data-row:
    textColor: "{colors.text}"
    typography: "{typography.body}"
    height: "24px"
---

# Design System: Fortnite AutoReplay

## Overview

**Creative North Star: "Le widget de la Game Bar"**

Un seul écran, une petite fenêtre sans bordure de 320 px posée sur le jeu. Le système est celui du thème sombre de Windows 11, exécuté avec soin plutôt que réinventé : couches grises à peine décalées, un filet d'un pixel, un accent bleu clair sur texte noir, Segoe UI Variable et les icônes Segoe Fluent. Il s'agit du standard de la catégorie, choisi tel quel.

La densité est celle d'un outil qu'on regarde du coin de l'œil. Tout part d'une ligne d'en-tête de 44 px qui se suffit repliée : pastille d'état, mot d'état, détail court, bouton d'action principal, chevron de repli. Déplié, le corps empile des sections plates séparées par l'espace, jamais par des cartes. La profondeur vient des couches de couleur, pas des ombres.

Le rendu est mesuré à 96 ppp et chaque valeur de grille est multipliée par l'échelle d'affichage du système (`px()`), sur une grille de 4 px.

**Key Characteristics:**
- Thème sombre de Windows 11 : fond, couche, filet, accent sur texte noir.
- Une ligne d'en-tête de 44 px qui tient seule, repliée.
- Sections plates, séparées par 16 px d'espace et non par des cadres.
- Deux tailles de texte (14 et 12 px) ; la hiérarchie passe par la graisse et la couleur.
- Aucune ombre, aucune animation : le survol et l'appui changent la surface.

## Colors

Une palette neutre presque monochrome, un seul accent bleu clair, et quatre couleurs d'état réservées à l'état.

### Primary
- **Bleu Fluent clair** (accent) : remplissage du bouton principal (Pause, Lancer, Reprendre, Quitter selon l'état), du segment choisi et teinte de l'icône du bandeau d'information. Le texte posé dessus est toujours noir (on-accent). Survol et appui foncent le bleu par paliers (accent-hover, accent-pressed).

### Tertiary
- **Vert succès** (success) : pastille de l'état Actif, et rien d'autre.
- **Jaune attention** (caution) : pastille des états En attente du jeu et Clic annulé, icône du bandeau d'attention.
- **Rose critique** (critical) : pastille des états Bloqué et Capture impossible, icône du bandeau critique.
- **Gris neutre** (neutral) : pastille des états En pause et Arrêté. Même valeur que le texte tertiaire, rôle distinct.
- **Ocre sombre** (caution-bg) et **Brique sombre** (critical-bg) : fonds teintés des bandeaux d'attention et critique, seuls fonds colorés du panneau.

### Neutral
- **Gris fenêtre** (bg) : fond de la fenêtre et de toutes les sections ; fond au repos des boutons discrets.
- **Gris couche** (layer) : surface des contrôles au repos (bouton standard, piste du sélecteur à segments) et fond du bandeau d'information.
- **Couche survolée** (layer-hover) et **couche appuyée** (layer-pressed) : états de survol et d'appui des contrôles neutres ; l'appui est plus sombre que le repos.
- **Filet** (stroke) : séparateurs d'un pixel sous l'en-tête et au-dessus du pied, contour des contrôles neutres, bordure de la fenêtre posée par DWM.
- **Texte** (text) : mots d'état, intertitres, valeurs, libellés des contrôles.
- **Texte secondaire** (text-secondary) : libellés des compteurs, détail de l'en-tête, ligne des totaux, chevron.
- **Texte tertiaire** (text-tertiary) : heures du journal, état vide, aide sous le sélecteur, rappel des raccourcis, segment désactivé.

### Named Rules
**La règle de la pastille.** Les couleurs d'état ne colorent qu'une pastille de 8 px ou l'icône d'un bandeau. Jamais un texte, un bouton ni un fond de section.

**La règle de l'accent qui agit.** L'accent marque ce qui agit ou ce qui est choisi : le bouton principal et le segment sélectionné. Un seul bouton d'accent par fenêtre.

## Typography

**Police de texte :** Segoe UI Variable Text (repli Segoe UI)
**Police de légende :** Segoe UI Variable Small (repli Segoe UI)
**Icônes :** Segoe Fluent Icons (repli Segoe MDL2 Assets)

**Character :** La pile du système Windows 11, sans police d'affichage : le panneau se lit comme une fenêtre native.

### Hierarchy
- **Title** (semi-gras, 14 px) : mot d'état de l'en-tête, intertitres de section, libellé du segment choisi.
- **Body** (normal, 14 px) : libellés de boutons et de segments, compteurs, journal, totaux, message des bandeaux.
- **Caption** (normal, 12 px) : détail de l'en-tête (nombre de clics), aide sous le sélecteur, rappel des raccourcis.
- **Icon** (12 px) : chevron de repli et icône de gravité des bandeaux. La pastille d'état est le disque plein de la même police, à 8 px, pour un bord lissé.

### Named Rules
**La règle des deux tailles.** 14 et 12 px, rien d'autre. Un intertitre se distingue par la graisse, un texte secondaire par la couleur, jamais par une taille de plus.

**La règle du texte français.** Espace insécable avant « : ; ? ! » et dans les guillemets, séparateur de milliers en espace insécable, apostrophe typographique, ni capitales ni lettres espacées.

## Layout

Fenêtre de 320 px de large, hauteur ajustée au contenu, gardée entière dans la zone de travail de l'écran.

- **En-tête** : 44 px de haut, marges latérales de 12 px (pad). Pastille, 8 px (gap), mot d'état, 8 px, détail ; à droite le bouton principal, 8 px (gap), puis le chevron, à 12 px du bord. Les contrôles de 28 px sont centrés (8 px au-dessus et au-dessous).
- **Corps** : marge intérieure de 12 px, largeur utile de 296 px. Premier bloc à 12 px sous le filet ; 16 px (section) entre bandeau et sections et entre sections ; 8 px entre un intertitre et son contenu ou entre le sélecteur et son aide.
- **Lignes de données** : 24 px de haut pour chaque compteur, ligne du journal, ligne des totaux et état vide.
- **Compteurs** : grille de deux colonnes égales, libellé à gauche, valeur alignée à droite, gouttière de 24 px (12 px de chaque côté).
- **Journal** : colonne de l'heure à la largeur de « 00:00 » la plus large de la police, puis 12 px avant le libellé ; cinq lignes au plus.
- **Pied** : filet à 12 px sous la dernière section, puis 12 px de marge autour de Quitter (à gauche) et du rappel des raccourcis (à droite).
- **Replié** : seul l'en-tête reste, la fenêtre fait 320 × 44 px.

### Named Rules
**La règle de la ligne qui se suffit.** L'en-tête porte seul l'état et l'action principale ; replié, il ne manque rien pour piloter le bot.

## Elevation & Depth

Le système est plat. Aucune ombre n'est dessinée : la profondeur vient de l'écart de luminosité entre le fond de la fenêtre et la couche des contrôles, et de filets d'un pixel. La fenêtre elle-même reçoit de Windows (DWM) ses coins arrondis et une bordure à la couleur du filet.

### Named Rules
**La règle de la surface qui change.** Le survol et l'appui changent la couleur de surface du contrôle (couche survolée, couche appuyée, paliers de l'accent). Jamais de halo, d'ombre ni de déplacement.

## Shapes

Coins doucement arrondis partout, à deux niveaux : 8 px pour la fenêtre (arrondi standard de Windows 11 demandé à DWM), 4 px pour les contrôles et les bandeaux. Dans le sélecteur à segments, le segment choisi est inscrit à 2 px de la piste avec un rayon de 3 px, pour rester concentrique. Les seules lignes sont les filets d'un pixel et le contour des contrôles neutres.

## Components

### Buttons
Contrôles natifs de Windows 11, sobres et plats.
- **Shape :** coins arrondis de 4 px, 28 px de haut, 12 px de marge latérale autour du libellé.
- **Accent :** fond accent, texte noir. Le bouton principal prend la largeur de son plus long libellé possible (Pause, Lancer, Reprendre, Quitter) pour ne jamais changer de taille.
- **Standard :** fond couche, contour filet, texte blanc. Sert à Quitter dans le pied.
- **Discret :** carré de 28 px sur le fond de la fenêtre, glyphe en texte secondaire. Sert au chevron de repli.
- **Survol / appui :** changement de surface seul ; le clic part au relâchement, si le pointeur est resté sur le bouton.

### Sélecteur à segments
- **Style :** piste couche avec contour filet, 32 px de haut, segments de largeur égale.
- **État :** segment choisi en accent avec libellé semi-gras noir ; survol et appui en couche survolée et appuyée ; segment sans modèle en texte tertiaire, non cliquable, expliqué par une aide en légende dessous.

### Bandeau (InfoBar)
- **Style :** pleine largeur utile, coins de 4 px, 12 px de marge intérieure, icône de gravité de 12 px puis 8 px avant le message, message en Body blanc, retour à la ligne.
- **Gravités :** critique (fond brique, icône rose), attention (fond ocre, icône jaune), information (fond couche, icône accent).
- **Présence :** il n'existe que s'il a quelque chose à dire ; sinon le corps commence directement par le sélecteur.

### Ligne d'état (signature)
Pastille de 8 px à la couleur de l'état, mot d'état en Title, détail en Caption secondaire (le nombre de clics, quand l'état est Actif ou En pause). Toute la ligne, sauf ses boutons, sert de poignée pour déplacer la fenêtre.

### Lignes de données
Rangées de 24 px sans fond ni séparateur : libellé en texte secondaire ou tertiaire, valeur en texte. L'alignement à droite des valeurs et la hauteur constante tiennent la grille.

## Do's and Don'ts

### Do:
- **Do** tirer chaque couleur, taille et écart des jetons ci-dessus, multipliés par l'échelle d'affichage.
- **Do** séparer les sections par 16 px d'espace et un intertitre semi-gras, sans cadre.
- **Do** réserver les couleurs d'état à la pastille et à l'icône des bandeaux.
- **Do** donner aux contrôles voisins la même hauteur (28 px) et au bouton principal une largeur fixe sur tous ses libellés.
- **Do** dire un état bloqué ou une erreur dans un bandeau, avec la marche à suivre.

### Don't:
- **Don't** ajouter d'ombre, de halo ou de lueur à un bouton, au repos comme au survol.
- **Don't** poser des cartes ou des fonds de section : le corps reste sur le fond de la fenêtre.
- **Don't** ajouter une troisième taille de texte ni une police d'affichage.
- **Don't** écrire un libellé en capitales ou en lettres espacées, ni ajouter « → » à un bouton.
- **Don't** poser une icône colorée sur un carré de sa couleur assombrie.
