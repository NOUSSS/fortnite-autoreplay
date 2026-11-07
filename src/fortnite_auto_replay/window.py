"""Repérage de la fenêtre Fortnite : zone de capture, premier plan, handle.

La fenêtre du jeu est trouvée par le nom de son exécutable et sa classe
(``FortniteClient-Win64-Shipping.exe`` / ``UnrealWindow``). On écarte ainsi les
fenêtres annexes qui portent aussi le titre « Fortnite » (lanceur anti-triche
``…_EAC_EOS.exe``, rapporteur de plantage).

Les appels Win32 passent par des instances privées de ``user32``/``kernel32`` :
leurs ``argtypes`` (handles 64 bits) ne touchent pas les autres modules.
"""

from __future__ import annotations

import ctypes
import logging
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import PureWindowsPath
from typing import Literal

from .config import WindowConfig

log = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]  # left, top, right, bottom en coordonnées écran
# absente | réduite (rien à capturer) | visible mais sans le focus | au premier plan
Focus = Literal["missing", "hidden", "background", "foreground"]

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GA_ROOT = 2
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_PROCESS_PER_MONITOR_DPI_AWARE = 2
# position que Windows donne à une fenêtre réduite (parfois avant que IsIconic le dise)
_MINIMIZED_ORIGIN = -32000


class WindowError(RuntimeError):
    """API fenêtres indisponible (hors Windows)."""


@dataclass(frozen=True, slots=True)
class WindowCandidate:
    hwnd: int
    pid: int
    process: str  # nom de l'exécutable, sans chemin
    class_name: str
    title: str
    visible: bool
    rect: Rect | None  # zone client, None si la fenêtre est réduite ou vide

    @property
    def area(self) -> int:
        if self.rect is None:
            return 0
        left, top, right, bottom = self.rect
        return max(0, right - left) * max(0, bottom - top)


def pick_window(candidates: Iterable[WindowCandidate], cfg: WindowConfig) -> WindowCandidate | None:
    """Fenêtre du jeu parmi les candidates : bon exécutable, bonne classe, visible.

    Si plusieurs conviennent, la plus grande zone client gagne.
    """
    wanted = {name.lower() for name in cfg.process_names}
    matching = [
        c
        for c in candidates
        if c.visible
        and c.process.lower() in wanted
        and (not cfg.window_class or c.class_name == cfg.window_class)
    ]
    if not matching:
        return None
    return max(matching, key=lambda c: c.area)


# --- Win32 ------------------------------------------------------------------
class _Win32:
    """Fonctions Win32 typées, chargées une seule fois."""

    def __init__(self) -> None:
        if not hasattr(ctypes, "WinDLL"):
            raise WindowError("repérage de fenêtre disponible sous Windows uniquement")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hwnd = wintypes.HWND

        self.enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, hwnd, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [self.enum_proc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [hwnd, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [hwnd]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [hwnd]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [hwnd]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetClassNameW.argtypes = [hwnd, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [hwnd, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetClientRect.argtypes = [hwnd, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = [hwnd, ctypes.POINTER(wintypes.POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = hwnd
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = hwnd
        user32.GetAncestor.argtypes = [hwnd, wintypes.UINT]
        user32.GetAncestor.restype = hwnd

        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        self.user32 = user32
        self.kernel32 = kernel32

    def window_pid(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def process_name(self, pid: int) -> str:
        handle = self.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return PureWindowsPath(buffer.value).name
        finally:
            self.kernel32.CloseHandle(handle)

    def _text(self, getter: Callable[..., int], hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        getter(hwnd, buffer, len(buffer))
        return buffer.value

    def client_rect(self, hwnd: int) -> Rect | None:
        if self.user32.IsIconic(hwnd):
            return None
        rect = wintypes.RECT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        origin = wintypes.POINT(0, 0)
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        if origin.x <= _MINIMIZED_ORIGIN and origin.y <= _MINIMIZED_ORIGIN:
            return None
        if rect.right <= 0 or rect.bottom <= 0:
            return None
        return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom

    def is_window(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindow(hwnd))

    def foreground_pid(self) -> int:
        hwnd = self.user32.GetForegroundWindow()
        return self.window_pid(hwnd) if hwnd else 0

    def window_at(self, x: int, y: int) -> int:
        """Fenêtre de premier niveau sous le point écran, celle qui recevrait un clic."""
        hwnd = self.user32.WindowFromPoint(wintypes.POINT(x, y))
        if not hwnd:
            return 0
        return int(self.user32.GetAncestor(hwnd, _GA_ROOT) or hwnd)

    def enumerate(self) -> list[WindowCandidate]:
        """Fenêtres de premier niveau visibles (les invisibles sont écartées tôt)."""
        names: dict[int, str] = {}
        found: list[WindowCandidate] = []

        def callback(hwnd: int, _lparam: int) -> bool:
            if not hwnd or not self.user32.IsWindowVisible(hwnd):
                return True
            pid = self.window_pid(hwnd)
            if pid not in names:
                names[pid] = self.process_name(pid)
            found.append(
                WindowCandidate(
                    hwnd=int(hwnd),
                    pid=pid,
                    process=names[pid],
                    class_name=self._text(self.user32.GetClassNameW, hwnd),
                    title=self._text(self.user32.GetWindowTextW, hwnd),
                    visible=True,
                    rect=self.client_rect(hwnd),
                )
            )
            return True

        self.user32.EnumWindows(self.enum_proc(callback), 0)
        return found


_api: _Win32 | None = None


def _win32() -> _Win32:
    global _api  # noqa: PLW0603 - chargement paresseux, une seule fois
    if _api is None:
        _api = _Win32()
    return _api


def ensure_dpi_awareness() -> None:
    """Coordonnées en pixels physiques partout (capture, fenêtres, clics).

    Sans cela, un affichage à 125 % ou 150 % décale les clics. À appeler avant
    toute capture ; les appels suivants sont sans effet.
    """
    if not hasattr(ctypes, "WinDLL"):
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return
    except AttributeError:  # Windows < 10 1703
        pass
    try:
        if ctypes.WinDLL("shcore").SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return
    except (AttributeError, OSError):
        pass
    user32.SetProcessDPIAware()


class GameWindow:
    """Suit la fenêtre du jeu : position de la zone client et premier plan.

    La fenêtre trouvée est gardée en cache ; on ne réénumère les fenêtres
    qu'au plus une fois par ``rescan_s`` quand elle est absente.
    """

    def __init__(
        self,
        cfg: WindowConfig,
        *,
        rescan_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = cfg
        self._api = _win32()
        self._rescan_s = rescan_s
        self._clock = clock
        self._current: WindowCandidate | None = None
        self._last_scan = float("-inf")

    def locate(self) -> WindowCandidate | None:
        current = self._current
        if current is not None:
            api = self._api
            if api.is_window(current.hwnd) and api.window_pid(current.hwnd) == current.pid:
                self._current = replace(current, rect=self._api.client_rect(current.hwnd))
                return self._current
            log.info("fenêtre du jeu fermée")
            self._current = None

        now = self._clock()
        if now - self._last_scan < self._rescan_s:
            return None
        self._last_scan = now
        self._current = pick_window(self._api.enumerate(), self._cfg)
        if self._current is not None:
            log.info(
                "fenêtre du jeu : %s (pid %d), zone client %s",
                self._current.process,
                self._current.pid,
                self._current.rect,
            )
        return self._current

    def region(self) -> Rect | None:
        """Zone client en coordonnées écran, ``None`` si absente ou réduite."""
        found = self.locate()
        return found.rect if found is not None else None

    def focus(self) -> Focus:
        found = self.locate()
        if found is None:
            return "missing"
        if found.rect is None:
            return "hidden"
        return "foreground" if self._api.foreground_pid() == found.pid else "background"

    def hwnd(self) -> int:
        """Handle de la fenêtre du jeu, 0 si elle est absente."""
        found = self.locate()
        return found.hwnd if found is not None else 0

    def covering(self, x: int, y: int) -> tuple[int, str] | None:
        """Ce qui recevrait un clic au point écran (x, y), si ce n'est pas le jeu.

        ``None`` quand le point tombe sur une fenêtre du jeu ; sinon le pid et
        l'exécutable de la fenêtre qui le couvre (panneau, notification…).
        """
        found = self.locate()
        hwnd = self._api.window_at(x, y)
        if not hwnd:
            return 0, ""
        pid = self._api.window_pid(hwnd)
        if found is not None and pid == found.pid:
            return None
        return pid, self._api.process_name(pid)
