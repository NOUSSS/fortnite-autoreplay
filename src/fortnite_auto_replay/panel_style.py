"""Jetons du panneau (variante « Discret ») : couleurs, grille, polices, icônes.

Mesures en pixels à 96 ppp, multipliées par l'échelle d'affichage (``px``) :
la mise à l'échelle automatique de Qt est coupée, pour que fenêtres, curseur
et positions enregistrées parlent tous en pixels d'écran.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPen

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Palette:
    """Verre d'encre vert sombre, monochrome ; quatre couleurs d'état, pour l'état seul.

    Le fond est le jeu flouté sous l'encre (``GLASS_TINT``) ; les couches des
    contrôles sont des voiles blancs (``#AARRGGBB``), le choix un voile plus
    marqué, l'action principale (Activer) un blanc plein. Les bandeaux
    d'alerte teintent le verre de leur couleur.
    """

    bg: str = "#0B1715"
    layer: str = "#12FFFFFF"
    layer_hover: str = "#1CFFFFFF"
    layer_pressed: str = "#0DFFFFFF"
    layer_selected: str = "#38FFFFFF"
    stroke: str = "#1FFFFFFF"
    text: str = "#F4F0E6"
    text_2: str = "#B8C3BB"
    text_3: str = "#7D8D86"
    accent: str = "#F4F0E6"
    accent_hover: str = "#FFFFFF"
    accent_pressed: str = "#D9D5CC"
    on_accent: str = "#0B1715"
    success: str = "#7BD88F"
    caution: str = "#FF9A4D"
    critical: str = "#FF7B7B"
    neutral: str = "#7D8D86"
    caution_bg: str = "#2EFF9A4D"
    critical_bg: str = "#2EFF7B7B"


C = Palette()

# verre : le jeu flouté derrière le panneau, sous l'encre ; sans capture, l'encre pleine
GLASS_TINT = 0.70  # opacité de l'encre posée sur le flou
GLASS_MS = 66  # flou rafraîchi environ 15 fois par seconde
GLASS_SHRINK = 8  # le flou se calcule sur une image 8 fois plus petite
GLASS_BLUR = 3.0  # rayon du flou sur la petite image (environ 24 px à l'écran)
GLASS_SATURATION = 1.6


# grille de 4 px
WIDTH = 320  # carte
FEATURE_H = 52  # ligne d'une fonction : nom et état sur deux lignes
PILL_H = 32  # pastille du repos
PAD = 12  # marge intérieure de la carte, du popover et des bandeaux (côtés)
GAP = 8  # entre éléments voisins d'une ligne
HEADING_GAP = 4  # sous un intertitre
SECTION_GAP = 12  # entre bandeau et liste d'une fonction
SETTING_GAP = 16  # entre réglages du popover
NOTICE_PAD_Y = 10
FOOTER_TOP = 10
ROW_H = 24
LINE = 19  # hauteur de ligne du texte de 14 px
LINE_SMALL = 16  # hauteur de ligne du texte de 12 px
CONTROL_H = 32  # boutons et sélecteurs, en pilule
ACTION_MIN_W = 104  # « Activer » et « Désactiver » à la même largeur
BUTTON_PAD = 14
SUBTLE_PAD = (10, 12)  # bouton discret : gauche (côté icône), droite
SEGMENT_INSET = 3
SLIDER_H = 20
TRACK_H = 6
THUMB = 18
CARD_RADIUS = 12
NOTICE_RADIUS = 8
POPOVER_W = 272
POPOVER_GAP = 8
DOT = 8
ICON = 14
NOTICE_ICON = 14
CLOCK_W = 40  # colonne de l'heure des derniers clics
DATE_W = 104  # colonne du début des dernières parties
JOURNAL_GAP = 12
RECENT_ROWS = 3

# mouvements (millisecondes), repris du prototype
OPEN_MS = 180  # carte et réglages qui s'ouvrent : rapide puis doux
CLOSE_MS = 120  # carte qui se referme
FADE_MS = 150  # opacité au repos, couleurs au survol
PRESS_MS = 100  # bouton enfoncé
OPEN_SCALE = 0.96
PRESS_SCALE = 0.97
THUMB_PRESS_SCALE = 0.9
DISABLED = 0.4  # opacité d'un bouton désactivé

FONTS = Path(__file__).with_name("fonts")  # Figtree et Bricolage Grotesque (OFL)
TEXT_FAMILY = "Figtree"
TITLE_FAMILY = "Bricolage Grotesque"
_FALLBACK = "Segoe UI"


def qcolor(color: str, alpha: float = 1.0) -> QColor:
    """Couleur (``#RRGGBB`` ou ``#AARRGGBB``), sa transparence multipliée par ``alpha``."""
    result = QColor(color)
    result.setAlphaF(result.alphaF() * max(0.0, min(1.0, alpha)))
    return result


_loaded: set[str] | None = None


def load_fonts(folder: Path = FONTS) -> set[str]:
    """Polices livrées, pour ce seul processus ; rien n'est installé dans Windows."""
    global _loaded  # noqa: PLW0603 - chargement unique pour le processus
    if _loaded is None:
        _loaded = set()
        for path in sorted(folder.glob("*.ttf")):
            index = QFontDatabase.addApplicationFont(str(path))
            if index < 0:
                log.warning("police non chargée : %s", path.name)
                continue
            _loaded.update(QFontDatabase.applicationFontFamilies(index))
    return _loaded


@dataclass(frozen=True, slots=True)
class Fonts:
    body: QFont  # 14 px
    strong: QFont  # 14 px semi-gras
    caption: QFont  # 12 px
    heading: QFont  # 12 px semi-gras
    title: QFont  # noms des fonctions et total : Bricolage Grotesque gras
    figures: QFont  # chiffres alignés (heures, total de la pastille)
    badge: QFont  # « i » et « ! » des bandeaux


def make_fonts(scale: float) -> Fonts:
    families = load_fonts()
    text = TEXT_FAMILY if TEXT_FAMILY in families else _FALLBACK
    title = TITLE_FAMILY if TITLE_FAMILY in families else text

    def font(family: str, size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        result = QFont(family)
        result.setPixelSize(round(size * scale))
        result.setWeight(weight)
        result.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        result.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return result

    title_font = font(title, 14, QFont.Weight.Bold)
    title_font.setVariableAxis(QFont.Tag("opsz"), 14.0)  # taille optique du prototype
    title_font.setFeature(QFont.Tag("tnum"), 1)
    figures = font(text, 14, QFont.Weight.DemiBold)
    figures.setFeature(QFont.Tag("tnum"), 1)
    return Fonts(
        body=font(text, 14),
        strong=font(text, 14, QFont.Weight.DemiBold),
        caption=font(text, 12),
        heading=font(text, 12, QFont.Weight.DemiBold),
        title=title_font,
        figures=figures,
        badge=font(text, 10, QFont.Weight.Bold),
    )


def tabular(font: QFont) -> QFont:
    """Même police, chiffres de largeur fixe (heures, pourcentages)."""
    result = QFont(font)
    result.setFeature(QFont.Tag("tnum"), 1)
    return result


# --- icônes du prototype (grille de 16, trait de 1,2) ------------------------------
def _icon_pen(painter: QPainter, color: str, size: float) -> None:
    pen = QPen(QColor(color), 1.2 * size / 16)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)


def draw_chest(painter: QPainter, x: float, y: float, size: float, color: str) -> None:
    """Coffre : boîte, couvercle arrondi, serrure."""
    k = size / 16
    painter.save()
    painter.translate(x, y)
    painter.scale(k, k)
    _icon_pen(painter, color, 16)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    body = QPainterPath()
    body.moveTo(2.5, 7)
    body.lineTo(13.5, 7)
    body.lineTo(13.5, 12.5)
    body.arcTo(QRectF(11.5, 11.5, 2, 2), 0, -90)
    body.lineTo(3.5, 13.5)
    body.arcTo(QRectF(2.5, 11.5, 2, 2), 270, -90)
    body.closeSubpath()
    painter.drawPath(body)
    lid = QPainterPath()
    lid.moveTo(2.5, 7)
    lid.lineTo(2.5, 5.5)
    lid.arcTo(QRectF(2.5, 3, 5, 5), 180, -90)
    lid.lineTo(11, 3)
    lid.arcTo(QRectF(8.5, 3, 5, 5), 90, -90)
    lid.lineTo(13.5, 7)
    painter.drawPath(lid)
    lock = QPainterPath()
    lock.moveTo(7, 7)
    lock.lineTo(7, 9)
    lock.lineTo(9, 9)
    lock.lineTo(9, 7)
    painter.drawPath(lock)
    painter.restore()


def draw_sliders(  # noqa: PLR0917 - même signature que les autres icônes, plus le fond
    painter: QPainter, x: float, y: float, size: float, color: str, bg: str
) -> None:
    """Réglages : deux traits, deux boutons ronds."""
    k = size / 16
    painter.save()
    painter.translate(x, y)
    painter.scale(k, k)
    _icon_pen(painter, color, 16)
    painter.drawLine(QPointF(2, 5), QPointF(14, 5))
    painter.drawLine(QPointF(2, 11), QPointF(14, 11))
    painter.setBrush(QColor(bg))
    painter.drawEllipse(QPointF(6, 5), 1.9, 1.9)
    painter.drawEllipse(QPointF(10.5, 11), 1.9, 1.9)
    painter.restore()


def draw_dot(painter: QPainter, cx: float, cy: float, diameter: float, color: str) -> None:
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(QPointF(cx, cy), diameter / 2, diameter / 2)
    painter.restore()


def fill_round(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    fill: QColor | str,
    stroke: QColor | str | None = None,
) -> None:
    """Surface arrondie, filet d'un pixel à l'intérieur (``box-shadow: inset``)."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(fill))
    radius = min(radius, rect.height() / 2, rect.width() / 2)
    painter.drawRoundedRect(rect, radius, radius)
    if stroke is not None:
        pen = QPen(QColor(stroke), 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inner = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        r = max(0.0, radius - 0.5)
        painter.drawRoundedRect(inner, r, r)
    painter.restore()
