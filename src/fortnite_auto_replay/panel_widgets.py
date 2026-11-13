"""Composants dessinés du panneau : texte, pastille, boutons en pilule, sélecteur, curseur,
bandeau. Tout est lissé (Qt), les couleurs glissent au survol comme dans le prototype.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from .panel_style import (
    DISABLED,
    FADE_MS,
    PRESS_MS,
    PRESS_SCALE,
    THUMB_PRESS_SCALE,
    C,
    draw_dot,
    fill_round,
    qcolor,
)

IconPainter = Callable[[QPainter, float, float, float, str], None]  # x, y, taille, couleur


def _painter(widget: QWidget) -> QPainter:
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    return painter


class Tween(QObject):
    """Valeur qui glisse vers sa cible (couleur ou nombre), ``changed`` à chaque pas."""

    changed = Signal()

    def __init__(self, value: QColor | float, ms: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.value = value
        self._target = value
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)

    def _step(self, value: QColor | float) -> None:
        self.value = value
        self.changed.emit()

    def to(self, target: QColor | float, *, now: bool = False) -> None:
        if target == self._target and not now:
            return
        self._target = target
        self._anim.stop()
        if now:
            self.value = target
            self.changed.emit()
            return
        self._anim.setStartValue(self.value)
        self._anim.setEndValue(target)
        self._anim.start()


# --- texte -----------------------------------------------------------------------------
class Text(QWidget):
    """Une ligne de texte à hauteur de ligne fixe (19 ou 16 px), coupée par « … » si besoin."""

    def __init__(
        self,
        font: QFont,
        color: str,
        line: int,
        text: str = "",
        *,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
        elide: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._font, self._color, self._line = font, color, line
        self._text, self._align, self._elide = text, align, elide
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        policy = QSizePolicy.Policy.Ignored if elide else QSizePolicy.Policy.Fixed
        self.setSizePolicy(policy, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(line)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.updateGeometry()
            self.update()

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def sizeHint(self) -> QSize:
        width = QFontMetricsF(self._font).horizontalAdvance(self._text)
        return QSize(int(width + 0.999), self._line)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self._line) if self._elide else self.sizeHint()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = _painter(self)
        painter.setFont(self._font)
        painter.setPen(QColor(self._color))
        text = self._text
        if self._elide:
            metrics = QFontMetricsF(self._font)
            text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, self.width())
        rect = QRectF(self.rect())
        painter.drawText(rect, int(self._align | Qt.AlignmentFlag.AlignVCenter), text)


class Wrapped(QWidget):
    """Paragraphe à la ligne, hauteur de ligne fixe (celle du prototype, pas celle de Qt)."""

    def __init__(
        self, font: QFont, color: str, line: int, width: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._font, self._color, self._line = font, color, line
        self._text = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(width)
        self._lines: list[tuple[str, float]] = []
        self.setFixedHeight(0)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> bool:
        """Vrai si la hauteur a changé."""
        if text == self._text:
            return False
        self._text = text
        self._lines = self._layout(self.width())
        height = len(self._lines) * self._line
        changed = height != self.height()
        self.setFixedHeight(height)
        self.update()
        return changed

    def _layout(self, width: int) -> list[tuple[str, float]]:
        if not self._text:
            return []
        layout = QTextLayout(self._text, self._font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        layout.setTextOption(option)
        lines: list[tuple[str, float]] = []
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            start, length = line.textStart(), line.textLength()
            lines.append((self._text[start : start + length].rstrip(), line.naturalTextWidth()))
        layout.endLayout()
        return lines

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = _painter(self)
        painter.setFont(self._font)
        painter.setPen(QColor(self._color))
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for index, (text, _) in enumerate(self._lines):
            rect = QRectF(0, index * self._line, self.width(), self._line)
            painter.drawText(rect, flags, text)


class Dot(QWidget):
    """Pastille d'état, disque lissé."""

    def __init__(self, diameter: int, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(diameter, diameter)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = _painter(self)
        draw_dot(painter, self.width() / 2, self.height() / 2, self.width(), self._color)


class Rule(QWidget):
    """Filet d'un pixel."""

    def __init__(self, parent: QWidget | None = None, *, vertical: int = 0) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if vertical:
            self.setFixedSize(1, vertical)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(C.stroke))


class Icon(QWidget):
    """Icône dessinée (coffre, réglages)."""

    def __init__(
        self, paint: IconPainter, size: int, color: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._paint, self._color = paint, color
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(size, size)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = _painter(self)
        self._paint(painter, 0, 0, self.width(), self._color)


# --- boutons ---------------------------------------------------------------------------
_KINDS = {  # fond au repos, survolé, enfoncé ; texte ; texte survolé ; filet
    "accent": (C.accent, C.accent_hover, C.accent_pressed, C.on_accent, C.on_accent, None),
    "standard": (C.layer, C.layer_hover, C.layer_pressed, C.text, C.text, C.stroke),
    "subtle": ("", C.layer, C.layer_pressed, C.text_2, C.text, None),
}


class Button(QWidget):
    """Bouton en pilule : couleur qui glisse au survol, léger enfoncement au clic.

    Le clic part au relâchement, si le pointeur est resté dessus. ``selected``
    garde l'aspect survolé (réglages ouverts) ; désactivé, il s'efface à 40 %.
    """

    def __init__(
        self,
        text: str,
        command: Callable[[], None],
        *,
        kind: str,
        font: QFont,
        height: int,
        pad: tuple[int, int],
        min_width: int = 0,
        icon: tuple[IconPainter, int, int] | None = None,  # dessin, taille, écart au texte
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text, self._command, self._kind = text, command, kind
        self._font, self._pad, self._min_width, self._icon = font, pad, min_width, icon
        self._hover = self._pressed = self._selected = False
        self._enabled = True
        self._fill = Tween(self._colors()[0], FADE_MS, self)
        self._ink = Tween(self._colors()[1], FADE_MS, self)
        self._scale = Tween(1.0, PRESS_MS, self)
        for tween in (self._fill, self._ink, self._scale):
            tween.changed.connect(self.update)
        self.setFixedHeight(height)
        self._resize()

    def text(self) -> str:
        return self._text

    def kind(self) -> str:
        return self._kind

    def enabled(self) -> bool:
        return self._enabled

    def _content_width(self) -> float:
        width = QFontMetricsF(self._font).horizontalAdvance(self._text)
        if self._icon is not None:
            width += self._icon[1] + self._icon[2]
        return width

    def _resize(self) -> None:
        width = round(self._content_width()) + self._pad[0] + self._pad[1]
        self.setFixedWidth(max(self._min_width, width))

    def _colors(self) -> tuple[QColor, QColor]:
        rest, hover, pressed, ink, ink_hover, _ = _KINDS[self._kind]
        live = self._enabled
        if live and self._pressed:
            fill, text = pressed, ink_hover
        elif live and (self._hover or self._selected):
            fill, text = hover, ink_hover
        else:
            fill, text = rest, ink
        return (qcolor(fill) if fill else qcolor(C.bg, 0.0)), qcolor(text)

    def _refresh(self, *, now: bool = False) -> None:
        fill, ink = self._colors()
        self._fill.to(fill, now=now)
        self._ink.to(ink, now=now)
        pressed = self._pressed and self._enabled
        self._scale.to(PRESS_SCALE if pressed else 1.0, now=now)

    def set_state(self, *, hover: bool | None = None, pressed: bool | None = None) -> None:
        """Survol ou appui imposés (captures de relecture)."""
        if hover is not None:
            self._hover = hover
        if pressed is not None:
            self._pressed = pressed
        self._refresh(now=True)

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._resize()
            self.update()

    def set_kind(self, kind: str) -> None:
        if kind != self._kind:
            self._kind = kind
            self._refresh(now=True)

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self._enabled:
            self._enabled = enabled
            shape = Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ForbiddenCursor
            self.setCursor(shape)
            self._refresh(now=True)

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self._refresh()

    def enterEvent(self, _event: QEnterEvent) -> None:
        self._hover = True
        self._refresh()

    def leaveEvent(self, _event: object) -> None:
        self._hover = self._pressed = False
        self._refresh()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._refresh()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        inside = self.rect().contains(event.position().toPoint())
        self._pressed = False
        self._refresh()
        if inside and self._enabled:
            self._command()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = _painter(self)
        if not self._enabled:
            painter.setOpacity(DISABLED)
        scale = float(self._scale.value)
        center = QPointF(self.width() / 2, self.height() / 2)
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)
        rect = QRectF(self.rect())
        stroke = _KINDS[self._kind][5]
        fill = self._fill.value
        assert isinstance(fill, QColor)
        if fill.alpha() or stroke:
            fill_round(painter, rect, rect.height() / 2, fill, stroke)
        ink = self._ink.value
        assert isinstance(ink, QColor)
        left = (
            self._pad[0] if self._icon is not None else (rect.width() - self._content_width()) / 2
        )
        if self._icon is not None:
            paint, size, gap = self._icon
            paint(painter, left, (rect.height() - size) / 2, size, ink.name())
            left += size + gap
        painter.setFont(self._font)
        painter.setPen(ink)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.drawText(QRectF(left, 0, rect.width() - left, rect.height()), flags, self._text)


class Segmented(QWidget):
    """Choix exclusif en pilule ; le segment choisi prend l'or."""

    def __init__(
        self,
        options: Sequence[tuple[str, str]],
        command: Callable[[str], None],
        *,
        width: int,
        height: int,
        inset: int,
        font: QFont,
        strong: QFont,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(width, height)
        self._choices = list(options)
        self._command = command
        self._inset = inset
        self._fonts = (font, strong)
        self._selected = ""
        self._disabled: frozenset[str] = frozenset()
        self._hover = self._pressed = ""
        self._fills = {
            value: Tween(qcolor(C.layer_hover, 0.0), FADE_MS, self) for value, _ in options
        }
        for tween in self._fills.values():
            tween.changed.connect(self.update)

    def selected(self) -> str:
        return self._selected

    def _at(self, x: float) -> str:
        step = self.width() / len(self._choices)
        index = min(len(self._choices) - 1, max(0, int(x // step)))
        return self._choices[index][0]

    def _refresh(self, *, now: bool = False) -> None:
        for value, tween in self._fills.items():
            if value == self._selected:
                target = qcolor(C.accent)
            elif value in self._disabled:
                target = qcolor(C.layer_hover, 0.0)
            elif value == self._pressed:
                target = qcolor(C.layer_pressed)
            elif value == self._hover:
                target = qcolor(C.layer_hover)
            else:
                target = qcolor(C.layer_hover, 0.0)
            tween.to(target, now=now or value == self._selected)
        self.update()

    def set_hover(self, value: str) -> None:
        """Survol imposé (captures de relecture)."""
        self._hover = value
        self._refresh(now=True)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        value = self._at(event.position().x())
        if value != self._hover:
            self._hover = value
            self._refresh()

    def leaveEvent(self, _event: object) -> None:
        self._hover = self._pressed = ""
        self._refresh()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        value = self._at(event.position().x())
        if value not in self._disabled and value != self._selected:
            self._pressed = value
            self._refresh()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._pressed = ""
        if not self.rect().contains(event.position().toPoint()):
            self._refresh()
            return
        value = self._at(event.position().x())
        if value not in self._disabled and value != self._selected:
            self._command(value)
        self._refresh()

    def set(self, selected: str, disabled: frozenset[str] = frozenset()) -> None:
        if (selected, disabled) != (self._selected, self._disabled):
            self._selected, self._disabled = selected, disabled
            self._refresh(now=True)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = _painter(self)
        rect = QRectF(self.rect())
        fill_round(painter, rect, rect.height() / 2, C.layer, C.stroke)
        inset = self._inset
        step = (rect.width() - 2 * inset) / len(self._choices)
        font, strong = self._fonts
        for index, (value, label) in enumerate(self._choices):
            cell = QRectF(inset + index * step, inset, step, rect.height() - 2 * inset)
            color = self._fills[value].value
            assert isinstance(color, QColor)
            if color.alpha():
                fill_round(painter, cell, cell.height() / 2, color)
            if value == self._selected:
                painter.setFont(strong)
                painter.setPen(QColor(C.on_accent))
            else:
                painter.setFont(font)
                live = value not in self._disabled
                hot = live and value in (self._hover, self._pressed)
                painter.setPen(QColor(C.text if hot else (C.text_2 if live else C.text_3)))
            painter.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), label)


class Slider(QWidget):
    """Curseur horizontal : piste en pilule, part remplie en or, poignée claire.

    ``command(valeur, fini)`` suit chaque mouvement ; ``fini`` au relâchement.
    """

    def __init__(
        self,
        command: Callable[[int, bool], None],
        *,
        width: int,
        height: int,
        track: int,
        thumb: int,
        snap: Callable[[float], int],
        bounds: tuple[int, int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(width, height)
        self._command, self._track, self._thumb = command, track, thumb
        self._snap, self._bounds = snap, bounds
        self._value = bounds[1]
        self._press = Tween(1.0, PRESS_MS, self)
        self._press.changed.connect(self.update)

    def value(self) -> int:
        return self._value

    def _span(self) -> tuple[float, float]:
        return self._thumb / 2, self.width() - self._thumb / 2

    def _move(self, x: float, *, final: bool) -> None:
        start, end = self._span()
        low, high = self._bounds
        ratio = min(1.0, max(0.0, (x - start) / (end - start)))
        value = self._snap(low + ratio * (high - low))
        if value != self._value:
            self._value = value
            self.update()
        self._command(value, final)

    def set(self, value: int) -> None:
        if value != self._value:
            self._value = value
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press.to(THUMB_PRESS_SCALE)
            self._move(event.position().x(), final=False)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._move(event.position().x(), final=False)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press.to(1.0)
            self._move(event.position().x(), final=True)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = _painter(self)
        width, height = self.width(), self.height()
        low, high = self._bounds
        ratio = (self._value - low) / (high - low)
        top = (height - self._track) / 2
        track = QRectF(0, top, width, self._track)
        fill_round(painter, track, self._track / 2, C.layer_hover)
        filled = QRectF(0, top, width * ratio, self._track)
        if filled.width() > 0:
            painter.save()
            painter.setClipRect(filled)
            fill_round(painter, track, self._track / 2, C.accent)
            painter.restore()
        start, end = self._span()
        x = start + ratio * (end - start)
        radius = self._thumb / 2 * float(self._press.value)
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((2.0, 0.10), (1.0, 0.16)):  # ombre douce sous la poignée
            painter.setBrush(qcolor("#000000", alpha))
            painter.drawEllipse(QPointF(x, height / 2 + 1), radius + spread, radius + spread)
        painter.setBrush(QColor(C.text))
        painter.drawEllipse(QPointF(x, height / 2), radius, radius)


_NOTICE_STYLES = {  # fond, pastille, signe
    "critical": (C.critical_bg, C.critical, "!"),
    "caution": (C.caution_bg, C.caution, "!"),
    "info": (C.layer, C.text_2, "i"),
}


class Notice(QWidget):
    """Bandeau : pastille « i » ou « ! », message à la ligne, bouton d'action facultatif."""

    def __init__(
        self,
        *,
        width: int,
        pad: tuple[int, int],
        gap: int,
        radius: int,
        icon: int,
        font: QFont,
        badge: QFont,
        line: int,
        make_button: Callable[[str, Callable[[], None]], Button] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(width)
        self._pad, self._gap, self._radius, self._icon = pad, gap, radius, icon
        self._badge, self._line = badge, line
        self._severity = "info"
        self._label = ""
        self._command: Callable[[], None] = lambda: None
        self._make_button = make_button
        text_x = pad[0] + icon + gap
        self.message = Wrapped(font, C.text, line, width - text_x - pad[0], self)
        self.message.move(text_x, pad[1])
        self.button: Button | None = None

    def severity(self) -> str:
        return self._severity

    def show_message(
        self, severity: str, message: str, action: tuple[str, Callable[[], None]] | None = None
    ) -> bool:
        """Remplit le bandeau ; vrai si sa hauteur a changé."""
        label = action[0] if action is not None else ""
        if action is not None:
            self._command = action[1]
        old = self.height()
        if severity != self._severity:
            self._severity = severity
            self.update()
        self.message.set_text(message)
        bottom = self._pad[1] + self.message.height()
        if label != self._label:
            self._label = label
            if self.button is not None:
                self.button.hide()  # détruit seulement au prochain tour de boucle
                self.button.deleteLater()
                self.button = None
            if label and self._make_button is not None:
                self.button = self._make_button(label, self._run)
                self.button.setParent(self)
                self.button.show()
        if self.button is not None:
            self.button.move(self.message.x(), bottom + self._gap)
            bottom += self._gap + self.button.height()
        self.setFixedHeight(bottom + self._pad[1])
        return self.height() != old

    def _run(self) -> None:
        self._command()  # le dernier rappel donné, pas celui de la création du bouton

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = _painter(self)
        background, tint, sign = _NOTICE_STYLES[self._severity]
        fill_round(painter, QRectF(self.rect()), self._radius, background)
        size = self._icon
        top = self._pad[1] + (self._line - size) / 2
        badge = QRectF(self._pad[0], top, size, size)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tint))
        painter.drawEllipse(badge)
        painter.setFont(self._badge)
        painter.setPen(QColor(C.bg))
        painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), sign)
