"""Capture d'écran avec repli en cascade et régions d'intérêt.

Ordre de préférence : ``dxcam`` (DXGI, très rapide) → ``mss`` (BitBlt rapide) →
``PIL.ImageGrab`` (lent mais toujours disponible). Un backend indisponible est
ignoré au profit du suivant.
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from typing_extensions import Self

from .config import CaptureConfig

log = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    """Capture impossible (aucun backend, écran introuvable...)."""


@dataclass(slots=True)
class Frame:
    """Image BGR capturée + position dans l'espace écran virtuel."""

    image: np.ndarray
    left: int
    top: int
    scale: float
    ts: float

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Convertit une coordonnée interne de frame en coordonnée écran."""
        return round(self.left + x / self.scale), round(self.top + y / self.scale)

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])


def _virtual_screen() -> tuple[int, int, int, int]:
    """Rectangle de l'écran virtuel complet (peut être négatif)."""
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78)
    height = user32.GetSystemMetrics(79)
    if width <= 0 or height <= 0:  # pragma: no cover - environnement exotique
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        left = top = 0
    return left, top, width, height


def monitor_bounds(index: int) -> tuple[int, int, int, int]:
    """Rectangle (left, top, width, height) du moniteur ``index`` (0 = tous)."""
    try:
        import mss  # noqa: PLC0415

        with mss.mss() as sct:
            monitors = sct.monitors
            if index >= len(monitors):
                raise CaptureError(f"moniteur {index} demandé, {len(monitors) - 1} disponible(s)")
            mon = monitors[index]
            return mon["left"], mon["top"], mon["width"], mon["height"]
    except ImportError:
        if index > 1:
            raise CaptureError("mss absent : seul le moniteur principal est adressable") from None
        return _virtual_screen()


class _Backend(Protocol):
    name: str

    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray: ...

    def close(self) -> None: ...


class _PilBackend:
    name = "pil"

    def __init__(self) -> None:
        from PIL import ImageGrab  # noqa: PLC0415

        self._grab = ImageGrab.grab

    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray:
        left, top, right, bottom = region
        image = self._grab(bbox=(left, top, right, bottom), all_screens=True)
        return np.asarray(image.convert("RGB"))[:, :, ::-1].copy()

    def close(self) -> None:
        pass


class _MssBackend:
    name = "mss"

    def __init__(self) -> None:
        import mss  # noqa: PLC0415

        self._sct = mss.mss()

    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray:
        left, top, right, bottom = region
        shot = self._sct.grab(
            {"left": left, "top": top, "width": right - left, "height": bottom - top}
        )
        # mss renvoie du BGRA : on garde BGR.
        return (
            np.frombuffer(shot.bgra, dtype=np.uint8)
            .reshape(shot.height, shot.width, 4)[:, :, :3]
            .copy()
        )

    def close(self) -> None:
        self._sct.close()


class _DxcamBackend:
    """Capture DXGI. La région est découpée côté numpy : le ``grab(region=...)``
    de dxcam 0.0.5 plante avec numpy 2, le grab plein écran fonctionne."""

    name = "dxcam"

    def __init__(self, monitor_rect: tuple[int, int, int, int], *, cfg_index: int = 0) -> None:
        import dxcam  # noqa: PLC0415 - dépendance optionnelle

        self._origin = (monitor_rect[0], monitor_rect[1])
        self._camera = dxcam.create(output_idx=max(0, cfg_index - 1), output_color="BGR")
        if self._camera is None:  # pragma: no cover - pas de sortie DXGI
            raise CaptureError("dxcam n'a pas pu créer de sortie")

    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray:
        frame: Any = None
        for _ in range(3):
            frame = self._camera.grab()
            if frame is not None:
                break
            time.sleep(0.005)
        if frame is None:
            raise CaptureError("dxcam : aucune frame disponible")

        left = max(0, region[0] - self._origin[0])
        top = max(0, region[1] - self._origin[1])
        right = min(frame.shape[1], region[2] - self._origin[0])
        bottom = min(frame.shape[0], region[3] - self._origin[1])
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            raise CaptureError("dxcam : région hors écran")
        return np.ascontiguousarray(crop)

    def close(self) -> None:
        # ``release()`` de dxcam 0.0.5 plante (access violation comtypes) sur
        # Python 3.13 : on laisse le processus se terminer dessus.
        log.debug("dxcam non libéré volontairement (release instable)")


_FACTORIES: dict[str, type] = {
    "pil": _PilBackend,
    "mss": _MssBackend,
    "dxcam": _DxcamBackend,
}
_ORDER = ("dxcam", "mss", "pil")


def _build_chain(requested: str) -> list[str]:
    if requested == "auto":
        return list(_ORDER)
    if requested not in _FACTORIES:
        raise CaptureError(f"backend de capture inconnu : {requested!r}")
    return [requested] + [name for name in _ORDER if name != requested]


class ScreenCapture:
    """Capture continue d'une région, avec repli automatique de backend."""

    def __init__(self, cfg: CaptureConfig) -> None:
        self._cfg = cfg
        self._backend: _Backend | None = None
        self._monitor_rect = monitor_bounds(cfg.monitor)
        self._region = self._resolve_region(cfg)
        self._backend = self._open_backend(cfg.backend)

    def _resolve_region(self, cfg: CaptureConfig) -> tuple[int, int, int, int]:
        left, top, width, height = self._monitor_rect
        if cfg.roi:
            rx, ry, rw, rh = cfg.roi
            left, top, width, height = left + rx, top + ry, rw, rh
        return left, top, left + width, top + height

    def _open_backend(self, requested: str) -> _Backend:
        errors: list[str] = []
        for name in _build_chain(requested):
            factory = _FACTORIES[name]
            try:
                if name == "dxcam":
                    # output_idx est l'index DXGI (0 = premier écran), pas l'index mss.
                    backend = factory(self._monitor_rect, cfg_index=self._cfg.monitor or 0)
                else:
                    backend = factory()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                log.debug("backend de capture %s indisponible (%s)", name, exc)
                continue
            log.info("backend de capture : %s", name)
            return backend
        raise CaptureError("aucun backend de capture disponible -> " + " | ".join(errors))

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend else "none"

    @property
    def region(self) -> tuple[int, int, int, int]:
        return self._region

    def grab(self) -> Frame:
        assert self._backend is not None
        image = self._backend.grab(self._region)
        scale = 1.0
        if self._cfg.scale < 1.0:
            from .vision import resize  # noqa: PLC0415 - évite la dépendance circulaire

            image = resize(image, self._cfg.scale)
            scale = self._cfg.scale
        return Frame(
            image=image, left=self._region[0], top=self._region[1], scale=scale, ts=time.time()
        )

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
