"""Verre simulé : le jeu derrière le panneau, flouté par le panneau lui-même.

Windows ne floute pas une fenêtre qui ne prend jamais le focus (essayé : accent
Acrylic, flou d'accent, fond système de Windows 11, avec Qt comme en natif).
Le panneau étant exclu des captures, une capture de la zone qu'il couvre donne
le jeu sans lui : réduite, floutée, saturée, elle sert de fond, teintée d'encre.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from .panel_style import GLASS_BLUR, GLASS_SATURATION, GLASS_SHRINK

log = logging.getLogger(__name__)


def blur(image: Image.Image) -> Image.Image:
    """Flou fort et couleurs ravivées, sur une image réduite (agrandie au dessin)."""
    small = image.reduce(GLASS_SHRINK) if min(image.size) >= GLASS_SHRINK else image
    small = small.filter(ImageFilter.GaussianBlur(GLASS_BLUR))
    return ImageEnhance.Color(small).enhance(GLASS_SATURATION)


def to_qimage(image: Image.Image) -> QImage:
    rgb = image.convert("RGB")
    data = rgb.tobytes()
    result = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return result.copy()  # détachée de ``data``


class GlassFeed:
    """Captures de petites zones de l'écran (mss), dans le thread qui l'utilise."""

    def __init__(self) -> None:
        self._sct: Any = None
        self._missing = False  # mss absent : plus la peine d'essayer
        self._failing = False  # captures en échec (session verrouillée, UAC…)

    def grab(self, x: int, y: int, width: int, height: int) -> QImage | None:
        """La zone floutée, ou ``None`` (capture impossible : fond opaque)."""
        if self._missing or width <= 0 or height <= 0:
            return None
        try:
            if self._sct is None:
                import mss  # noqa: PLC0415 - optionnel, comme pour le bot

                factory = getattr(mss, "MSS", None) or mss.mss
                self._sct = factory()
            shot = self._sct.grab({"left": x, "top": y, "width": width, "height": height})
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except ImportError as exc:
            log.warning("verre indisponible, fond opaque (%s)", exc)
            self._missing = True
            return None
        except Exception as exc:  # passager le plus souvent : nouvel essai au tour suivant
            if not self._failing:
                log.warning("capture du verre en échec, fond opaque (%s)", exc)
                self._failing = True
            self._reset()
            return None
        if self._failing:
            log.info("capture du verre rétablie")
            self._failing = False
        return to_qimage(blur(image))

    def _reset(self) -> None:
        sct, self._sct = self._sct, None
        if sct is not None:
            with contextlib.suppress(Exception):
                sct.close()


Zone = tuple[str, tuple[int, int, int, int]]  # surface, (x, y, largeur, hauteur) à l'écran


class GlassWorker(QObject):
    """Captures dans leur propre thread : une capture d'écran prend ~17 ms (synchronisation
    avec la composition de Windows), le panneau ne doit pas attendre.

    Une demande à la fois : si la précédente n'est pas finie, la nouvelle est sautée.
    """

    ready = Signal(object)  # list[tuple[str, QImage | None]], reçu dans le thread du panneau

    def __init__(self) -> None:
        super().__init__()
        self._feed = GlassFeed()  # mss vit dans le thread qui s'en sert
        self._requests: queue.Queue[list[Zone] | None] = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._run, name="verre", daemon=True)
        self._thread.start()

    def request(self, zones: list[Zone]) -> bool:
        try:
            self._requests.put_nowait(zones)
        except queue.Full:
            return False
        return True

    def stop(self) -> None:
        with contextlib.suppress(queue.Full):
            self._requests.put(None, timeout=1)
        self._thread.join(timeout=1)

    def _run(self) -> None:
        while (zones := self._requests.get()) is not None:
            self.ready.emit([(key, self._feed.grab(*rect)) for key, rect in zones])
