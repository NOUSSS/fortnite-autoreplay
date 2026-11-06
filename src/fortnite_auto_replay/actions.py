"""Envoi d'entrées souris vers le jeu.

Backends, par ordre de préférence : ``pydirectinput`` (DirectInput/ScanCode,
le mieux accepté par les jeux) → ``win32`` (SendInput natif via ctypes, sans
dépendance) → ``pyautogui``.

Le bot ne nécessite aucune injection mémoire : ce sont des entrées réelles.
"""

from __future__ import annotations

import ctypes
import logging
import random
import time
from ctypes import wintypes

from .config import PROJECT_ROOT, ActionConfig

log = logging.getLogger(__name__)

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class InputError(RuntimeError):
    """Backend d'entrée indisponible."""


if hasattr(ctypes, "windll"):  # Windows uniquement

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        )

    class _INPUTUNION(ctypes.Union):
        _fields_ = (("mi", MOUSEINPUT),)

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", _INPUTUNION))


def _virtual_screen() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78) or user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(79) or user32.GetSystemMetrics(1)
    return left, top, width, height


class _Win32Backend:
    name = "win32"

    def __init__(self) -> None:
        if not hasattr(ctypes, "windll"):  # pragma: no cover
            raise InputError("SendInput indisponible hors Windows")
        self._user32 = ctypes.windll.user32

    def _send(self, flags: int, x: int | None = None, y: int | None = None) -> None:
        inputs = INPUT()
        inputs.type = 0  # INPUT_MOUSE
        inputs.union.mi = MOUSEINPUT(
            dx=x or 0, dy=y or 0, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0
        )
        sent = self._user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(INPUT))
        if sent != 1:  # pragma: no cover
            raise InputError(f"SendInput a échoué (flags={flags:#x})")

    def _absolute(self, x: int, y: int) -> tuple[int, int]:
        left, top, width, height = _virtual_screen()
        nx = round((x - left) * 65535 / max(1, width - 1))
        ny = round((y - top) * 65535 / max(1, height - 1))
        return max(0, min(65535, nx)), max(0, min(65535, ny))

    def move(self, x: int, y: int) -> None:
        nx, ny = self._absolute(x, y)
        self._send(_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK, nx, ny)

    def down(self) -> None:
        self._send(_MOUSEEVENTF_LEFTDOWN)

    def up(self) -> None:
        self._send(_MOUSEEVENTF_LEFTUP)

    def position(self) -> tuple[int, int]:
        point = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        return int(point.x), int(point.y)


class _PyAutoGuiBackend(_Win32Backend):
    name = "pyautogui"

    def __init__(self) -> None:  # pragma: no cover - dépendance optionnelle
        import pyautogui  # noqa: PLC0415

        super().__init__()
        self._gui = pyautogui
        pyautogui.FAILSAFE = False

    def move(self, x: int, y: int) -> None:
        self._gui.moveTo(x, y, duration=0)

    def down(self) -> None:
        self._gui.mouseDown()

    def up(self) -> None:
        self._gui.mouseUp()


class _PyDirectInputBackend(_Win32Backend):
    name = "pydirectinput"

    def __init__(self) -> None:  # pragma: no cover - dépendance optionnelle
        import pydirectinput  # noqa: PLC0415

        super().__init__()
        self._pdi = pydirectinput
        pydirectinput.PAUSE = 0
        pydirectinput.FAILSAFE = False

    def move(self, x: int, y: int) -> None:
        self._pdi.moveTo(x, y)

    def down(self) -> None:
        self._pdi.mouseDown()

    def up(self) -> None:
        self._pdi.mouseUp()


_FACTORIES = {
    "pydirectinput": _PyDirectInputBackend,
    "win32": _Win32Backend,
    "pyautogui": _PyAutoGuiBackend,
}
_ORDER = ("pydirectinput", "win32", "pyautogui")


def _build_chain(requested: str) -> list[str]:
    if requested == "auto":
        return list(_ORDER)
    if requested not in _FACTORIES:
        raise InputError(f"backend d'entrée inconnu : {requested!r}")
    return [requested] + [name for name in _ORDER if name != requested]


class Actions:
    """Clique et déplace la souris selon la configuration."""

    def __init__(self, cfg: ActionConfig, *, dry_run: bool = False) -> None:
        self._cfg = cfg
        self.dry_run = dry_run
        self._backend: _Win32Backend | None = None
        if not dry_run:
            self._backend = self._open(cfg.backend)
            log.info("backend d'entrée : %s", self._backend.name)
        else:
            log.info("mode dry-run : aucun clic réel")

    def _open(self, requested: str) -> _Win32Backend:
        errors: list[str] = []
        for name in _build_chain(requested):
            try:
                backend = _FACTORIES[name]()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                continue
            return backend
        raise InputError("aucun backend d'entrée disponible -> " + " | ".join(errors))

    def position(self) -> tuple[int, int]:
        if self._backend is None:
            return (0, 0)
        return self._backend.position()

    def move(self, x: int, y: int) -> None:
        if self._backend is not None:
            self._backend.move(x, y)

    def _sleep_jitter(self, base: float) -> None:
        time.sleep(base * random.uniform(0.8, 1.25))

    def click(self, x: int, y: int, *, clicks: int = 1) -> tuple[int, int]:
        """Clique en (x, y) coordonnées écran absolues. Retourne le point visé."""
        ox, oy = self._cfg.click_offset
        tx, ty = int(x + ox), int(y + oy)
        if self.dry_run:
            log.info("[dry-run] clic %sx%s (x%d)", clicks, (tx, ty), clicks)
            return tx, ty

        assert self._backend is not None
        saved = self._backend.position() if self._cfg.restore_cursor else (0, 0)

        self._backend.move(tx, ty)
        self._sleep_jitter(self._cfg.move_duration_s)
        for index in range(clicks):
            self._backend.down()
            self._sleep_jitter(self._cfg.click_hold_s)
            self._backend.up()
            if index + 1 < clicks:
                self._sleep_jitter(0.12)

        if self._cfg.restore_cursor:
            self._backend.move(*saved)
        return tx, ty


def debug_click_marker(path_prefix: str, x: int, y: int) -> str:  # pragma: no cover - utilitaire
    """Trace un fichier texte à côté des captures, pour déboguer les clics."""
    target = PROJECT_ROOT / f"{path_prefix}_{x}_{y}.txt"
    target.write_text(f"{x},{y}\n", encoding="utf-8")
    return str(target)
