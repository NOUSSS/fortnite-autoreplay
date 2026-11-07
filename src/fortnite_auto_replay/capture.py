"""Capture d'écran avec repli en cascade et régions d'intérêt.

Ordre de préférence : ``dxcam`` (Desktop Duplication, ~0,2 ms par capture) →
``mss`` (BitBlt, ~17 ms) → ``PIL.ImageGrab`` (lent mais toujours disponible).
Un backend indisponible est ignoré au profit du suivant.

Chaque backend annonce la zone d'écran qu'il sait capturer (``bounds`` : un
seul écran pour dxcam, tout le bureau pour mss et PIL). La région demandée y
est rognée avant la capture, et la frame prend l'origine de la région rognée :
la conversion frame → écran reste exacte même si le jeu déborde de l'écran.
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

import numpy as np
from typing_extensions import Self

from .config import CaptureConfig

if TYPE_CHECKING:
    from .window import GameWindow

log = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]  # left, top, right, bottom en coordonnées écran

_REOPEN_AFTER = 3  # échecs de capture consécutifs avant de rouvrir le backend
_FIRST_FRAME_TRIES = 20  # dxcam : attente de la toute première frame (x 10 ms)


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
    # taille de la zone client du jeu quand la frame la couvre en entier, sinon None
    window_size: tuple[int, int] | None = None

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Convertit une coordonnée interne de frame en coordonnée écran."""
        return round(self.left + x / self.scale), round(self.top + y / self.scale)

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])


def intersect(a: Rect, b: Rect) -> Rect | None:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _contains(rect: Rect, x: float, y: float) -> bool:
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def _center(rect: Rect) -> tuple[float, float]:
    return (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2


def _area(rect: Rect | None) -> int:
    return 0 if rect is None else (rect[2] - rect[0]) * (rect[3] - rect[1])


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


def _mss_instance() -> Any:
    import mss  # noqa: PLC0415

    factory = getattr(mss, "MSS", None) or mss.mss  # mss.mss est déprécié depuis mss 10
    return factory()


def monitor_bounds(index: int) -> tuple[int, int, int, int]:
    """Rectangle (left, top, width, height) du moniteur ``index`` (0 = tous)."""
    try:
        sct = _mss_instance()
    except ImportError:
        if index > 1:
            raise CaptureError("mss absent : seul le moniteur principal est adressable") from None
        return _virtual_screen()
    with sct:
        monitors = sct.monitors
        if index >= len(monitors):
            raise CaptureError(f"moniteur {index} demandé, {len(monitors) - 1} disponible(s)")
        mon = monitors[index]
        return mon["left"], mon["top"], mon["width"], mon["height"]


def _desktop_rect() -> Rect:
    left, top, width, height = monitor_bounds(0)
    return left, top, left + width, top + height


class _Backend(Protocol):
    name: str
    bounds: Rect  # zone d'écran que ce backend sait capturer

    def grab(self, region: Rect) -> np.ndarray: ...

    def close(self) -> None: ...


class _PilBackend:
    name = "pil"

    def __init__(self, target: Rect) -> None:
        from PIL import ImageGrab  # noqa: PLC0415

        del target
        self._grab = ImageGrab.grab
        self.bounds = _desktop_rect()

    def grab(self, region: Rect) -> np.ndarray:
        image = self._grab(bbox=region, all_screens=True)
        return np.asarray(image.convert("RGB"))[:, :, ::-1].copy()

    def close(self) -> None:
        pass


class _MssBackend:
    name = "mss"

    def __init__(self, target: Rect) -> None:
        del target
        self._sct = _mss_instance()
        mon = self._sct.monitors[0]
        self.bounds = (
            mon["left"],
            mon["top"],
            mon["left"] + mon["width"],
            mon["top"] + mon["height"],
        )

    def grab(self, region: Rect) -> np.ndarray:
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


@dataclass(frozen=True, slots=True)
class DxgiOutput:
    device: int
    output: int
    rect: Rect


def pick_output(outputs: list[DxgiOutput], target: Rect) -> DxgiOutput:
    """Écran qui contient le centre de ``target``, sinon celui qui en couvre le plus."""
    if not outputs:
        raise CaptureError("dxcam : aucune sortie d'écran")
    cx, cy = _center(target)
    for candidate in outputs:
        if _contains(candidate.rect, cx, cy):
            return candidate
    best = max(outputs, key=lambda o: _area(intersect(o.rect, target)))
    if _area(intersect(best.rect, target)) == 0:
        raise CaptureError(f"aucun écran ne contient la zone {target}")
    return best


def _dxgi_outputs(module: Any) -> list[DxgiOutput]:
    # la fabrique interne évite DXFactory(), qui affiche un message à chaque appel
    factory = vars(module).get("__factory") or module.DXFactory()
    found: list[DxgiOutput] = []
    for device_idx, outputs in enumerate(factory.outputs):
        for output_idx, output in enumerate(outputs):
            output.update_desc()
            c = output.desc.DesktopCoordinates
            rect = (int(c.left), int(c.top), int(c.right), int(c.bottom))
            found.append(DxgiOutput(device_idx, output_idx, rect))
    return found


class _DxcamBackend:
    """Desktop Duplication sur l'écran qui contient la zone visée.

    Desktop Duplication ne livre une image que si l'écran a changé : quand il
    est immobile, ``grab()`` renvoie ``None`` et on réutilise la dernière image,
    qui est alors toujours exacte.
    """

    name = "dxcam"

    def __init__(self, target: Rect, *, camera: Any = None, bounds: Rect | None = None) -> None:
        if camera is None:
            import dxcam  # noqa: PLC0415 - dépendance optionnelle

            chosen = pick_output(_dxgi_outputs(dxcam), target)
            camera = dxcam.create(
                device_idx=chosen.device, output_idx=chosen.output, output_color="BGR"
            )
            if camera is None:  # pragma: no cover - pas de sortie DXGI
                raise CaptureError("dxcam n'a pas pu créer de sortie")
            bounds = chosen.rect
        if bounds is None:
            raise CaptureError("dxcam : zone de l'écran inconnue")
        self._camera = camera
        self.bounds = bounds
        self._last: np.ndarray | None = None

    def _latest(self) -> np.ndarray:
        frame = self._camera.grab()
        if frame is not None:
            self._last = frame
            return frame
        if self._last is not None:
            return self._last  # écran immobile depuis la dernière image
        for _ in range(_FIRST_FRAME_TRIES):
            time.sleep(0.01)
            frame = self._camera.grab()
            if frame is not None:
                self._last = frame
                return frame
        raise CaptureError("dxcam : aucune image reçue de l'écran")

    def grab(self, region: Rect) -> np.ndarray:
        frame = self._latest()
        left, top, right, bottom = self.bounds
        if frame.shape[0] != bottom - top or frame.shape[1] != right - left:
            self._last = None
            raise CaptureError("dxcam : la résolution de l'écran a changé")
        crop = frame[region[1] - top : region[3] - top, region[0] - left : region[2] - left]
        if crop.size == 0:
            raise CaptureError("dxcam : zone visée hors de l'écran capturé")
        return np.ascontiguousarray(crop)

    def close(self) -> None:
        # ``release()`` de dxcam a planté (access violation comtypes) sur
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


def apply_roi(base: Rect, roi: list[int] | None) -> Rect:
    """Région ``(left, top, right, bottom)`` : ``base`` restreinte par ``roi``.

    ``roi`` = ``[left, top, width, height]`` relatif au coin de ``base`` ; il est
    rogné pour ne jamais sortir de ``base``.
    """
    left, top, right, bottom = base
    if not roi:
        return base
    rx, ry, rw, rh = roi
    new_left = min(max(left, left + rx), right)
    new_top = min(max(top, top + ry), bottom)
    new_right = max(new_left, min(right, left + rx + rw))
    new_bottom = max(new_top, min(bottom, top + ry + rh))
    if new_right - new_left <= 0 or new_bottom - new_top <= 0:
        raise CaptureError(f"capture.roi {roi} sort de la zone capturée {base}")
    return new_left, new_top, new_right, new_bottom


class ScreenCapture:
    """Capture continue d'une région, avec repli et réouverture de backend.

    Avec ``window``, la région suit la zone client du jeu à chaque capture
    (``capture.roi`` devient alors relatif à cette zone) ; sinon elle couvre le
    moniteur ``capture.monitor``. Si le jeu passe sur un autre écran, ou après
    plusieurs échecs de suite, le backend est rouvert sur le bon écran.
    """

    def __init__(self, cfg: CaptureConfig, *, window: GameWindow | None = None) -> None:
        self._cfg = cfg
        self._window = window
        self._backend: _Backend | None = None
        self._failures = 0
        left, top, width, height = monitor_bounds(cfg.monitor)
        self._static_region = apply_roi((left, top, left + width, top + height), cfg.roi)
        initial = window.region() if window is not None else None
        self._backend = self._open_backend(initial or self._static_region)

    def _open_backend(self, target: Rect) -> _Backend:
        errors: list[str] = []
        for name in _build_chain(self._cfg.backend):
            try:
                backend = _FACTORIES[name](target)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                log.debug("backend de capture %s indisponible (%s)", name, exc)
                continue
            log.info("backend de capture : %s, écran %s", name, backend.bounds)
            return backend
        raise CaptureError("aucun backend de capture disponible -> " + " | ".join(errors))

    def _reopen(self, target: Rect) -> _Backend:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._backend = self._open_backend(target)
        return self._backend

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend else "none"

    def _target(self) -> tuple[Rect, tuple[int, int] | None]:
        """Zone à capturer et, si elle couvre toute la zone client du jeu, sa taille."""
        if self._window is None:
            return self._static_region, None
        rect = self._window.region()
        if rect is None:
            raise CaptureError("fenêtre du jeu introuvable ou réduite")
        size = (rect[2] - rect[0], rect[3] - rect[1])
        return apply_roi(rect, self._cfg.roi), (None if self._cfg.roi else size)

    def _backend_for(self, target: Rect) -> _Backend:
        backend = self._backend
        if backend is None:
            return self._reopen(target)
        if backend.name == "dxcam" and not _contains(backend.bounds, *_center(target)):
            log.info("le jeu a changé d'écran : capture rouverte")
            return self._reopen(target)
        return backend

    def _grab_failed(self, exc: Exception, target: Rect) -> NoReturn:
        self._failures += 1
        message = str(exc) if isinstance(exc, CaptureError) else f"{type(exc).__name__}: {exc}"
        if self._failures >= _REOPEN_AFTER:
            log.warning("%d captures ratées de suite : réouverture du backend", self._failures)
            self._failures = 0
            try:
                self._reopen(target)
            except CaptureError as reopen_exc:
                message += f" ; réouverture impossible : {reopen_exc}"
        raise CaptureError(message) from exc

    def grab(self) -> Frame:
        target, window_size = self._target()
        backend = self._backend_for(target)
        region = intersect(target, backend.bounds)
        if region is None:
            raise CaptureError(f"la zone visée {target} est hors de l'écran {backend.bounds}")
        if region != target:
            window_size = None  # le jeu déborde de l'écran : la frame n'en montre qu'une partie
        try:
            image = backend.grab(region)
        except Exception as exc:
            self._grab_failed(exc, target)
        self._failures = 0

        scale = 1.0
        if self._cfg.scale < 1.0:
            from .vision import resize  # noqa: PLC0415 - évite la dépendance circulaire

            image = resize(image, self._cfg.scale)
            scale = self._cfg.scale
        return Frame(
            image=image,
            left=region[0],
            top=region[1],
            scale=scale,
            ts=time.time(),
            window_size=window_size,
        )

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
