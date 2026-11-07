"""Envoi d'entrées souris vers le jeu.

Jeu au premier plan : entrées réelles. Backends, par ordre de préférence :
``pydirectinput`` (DirectInput/ScanCode, le mieux accepté par les jeux) →
``win32`` (SendInput natif via ctypes, sans dépendance) → ``pyautogui``.

Jeu visible mais sans le focus : une entrée réelle l'activerait (il volerait
le focus de la fenêtre où l'on travaille). Le clic est alors posté dans la
file de messages de sa fenêtre (``WM_LBUTTONDOWN``/``WM_LBUTTONUP``), ce qui ne
l'active jamais. Au second plan, Fortnite se met en veille et ignore la souris :
il reçoit d'abord ``WM_ACTIVATEAPP`` pour se croire actif, puis l'inverse
après le clic. Jamais ``WM_ACTIVATE`` : traité par défaut, il donne le focus à
la fenêtre, et Windows peut alors la mettre au premier plan.

Unreal calcule le survol des boutons avec le vrai curseur : en mode ``cursor``,
le curseur est posé sur le bouton le temps du clic (environ 0,25 s), seulement
quand la souris est immobile, puis remis à sa place.

Le bot ne nécessite aucune injection mémoire.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import random
import time
from collections.abc import Callable, Iterator
from ctypes import wintypes
from typing import Protocol

from .config import PROJECT_ROOT, ActionConfig

log = logging.getLogger(__name__)

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000

_WM_ACTIVATEAPP = 0x001C
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_MK_LBUTTON = 0x0001
# VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2
_MOUSE_BUTTONS = (0x01, 0x02, 0x04, 0x05, 0x06)
_IDLE_POLL_S = 0.02
_IDLE_TIMEOUT_S = 1.5  # au-delà, le clic est reporté à une prochaine image
_ERROR_ACCESS_DENIED = 5

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

    def __init__(
        self,
        cfg: ActionConfig,
        *,
        dry_run: bool = False,
        background: BackgroundClicker | None = None,
    ) -> None:
        self._cfg = cfg
        self.dry_run = dry_run
        self._backend: _Win32Backend | None = None
        self._background = background
        if not dry_run:
            self._backend = self._open(cfg.backend)
            if self._background is None:
                self._background = BackgroundClicker(cfg)
            log.info(
                "backend d'entrée : %s ; jeu au second plan : clic posté (mode %s)",
                self._backend.name,
                cfg.background,
            )
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

    def click(
        self,
        x: int,
        y: int,
        *,
        clicks: int = 1,
        window: int = 0,
        cancel: Callable[[], bool] | None = None,
    ) -> tuple[int, int] | None:
        """Clique en (x, y) coordonnées écran absolues. Retourne le point visé.

        Avec ``window`` (handle du jeu au second plan), le clic est posté à
        cette fenêtre sans l'activer ; ``None`` s'il est reporté (souris utilisée)
        ou abandonné (``cancel`` devenu vrai pendant l'attente de la souris).
        """
        ox, oy = self._cfg.click_offset
        tx, ty = int(x + ox), int(y + oy)
        if self.dry_run:
            where = " en arrière-plan" if window else ""
            log.info("[dry-run] clic%s %sx%s (x%d)", where, clicks, (tx, ty), clicks)
            return tx, ty

        if window:
            assert self._background is not None
            if not self._background.click(window, tx, ty, clicks=clicks, cancel=cancel):
                return None
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


# --- clic en arrière-plan --------------------------------------------------------
class WindowInput(Protocol):
    """Appels Win32 du clic en arrière-plan (remplaçables dans les tests)."""

    def is_window(self, hwnd: int) -> bool: ...

    def to_client(self, hwnd: int, x: int, y: int) -> tuple[int, int]: ...

    def post(self, hwnd: int, message: int, wparam: int, x: int, y: int) -> None: ...

    def cursor(self) -> tuple[int, int]: ...

    def set_cursor(self, x: int, y: int) -> None: ...

    def buttons_down(self) -> bool: ...


class _Win32WindowInput:
    """``WindowInput`` réel, sur une instance privée de user32 typée en 64 bits."""

    def __init__(self) -> None:
        if not hasattr(ctypes, "WinDLL"):  # pragma: no cover
            raise InputError("clic en arrière-plan disponible sous Windows uniquement")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = wintypes.HWND
        user32.IsWindow.argtypes = [hwnd]
        user32.IsWindow.restype = wintypes.BOOL
        user32.ScreenToClient.argtypes = [hwnd, ctypes.POINTER(wintypes.POINT)]
        user32.ScreenToClient.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [hwnd, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        self._user32 = user32

    def is_window(self, hwnd: int) -> bool:
        return bool(self._user32.IsWindow(hwnd))

    def to_client(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        point = wintypes.POINT(x, y)
        if not self._user32.ScreenToClient(hwnd, ctypes.byref(point)):
            raise InputError(f"ScreenToClient a échoué (erreur {ctypes.get_last_error()})")
        return int(point.x), int(point.y)

    def post(self, hwnd: int, message: int, wparam: int, x: int, y: int) -> None:
        lparam = (y & 0xFFFF) << 16 | (x & 0xFFFF)  # coordonnées signées sur 16 bits
        if self._user32.PostMessageW(hwnd, message, wparam, lparam):
            return
        error = ctypes.get_last_error()
        if error == _ERROR_ACCESS_DENIED:  # UIPI : le jeu tourne avec plus de droits
            raise InputError("Fortnite refuse le clic : lancez le bot en administrateur")
        raise InputError(f"PostMessage a échoué (erreur {error})")

    def cursor(self) -> tuple[int, int]:
        point = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        return int(point.x), int(point.y)

    def set_cursor(self, x: int, y: int) -> None:
        self._user32.SetCursorPos(x, y)

    def buttons_down(self) -> bool:
        return any(self._user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MOUSE_BUTTONS)


class BackgroundClicker:
    """Clique dans une fenêtre sans l'activer : messages postés, curseur rendu."""

    def __init__(
        self,
        cfg: ActionConfig,
        api: WindowInput | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = cfg
        self._api = api if api is not None else _Win32WindowInput()
        self._sleep = sleep
        self._clock = clock

    def _mouse_idle(self, cancel: Callable[[], bool]) -> bool:
        """Attend une souris immobile, sans bouton enfoncé.

        Faux si elle ne l'est pas à temps, ou dès que ``cancel`` devient vrai.
        """
        api = self._api
        start = self._clock()
        still_since = start
        last = api.cursor()
        while True:
            if cancel():
                return False
            now = self._clock()
            position = api.cursor()
            if position != last or api.buttons_down():
                last, still_since = position, now
            elif now - still_since >= self._cfg.mouse_idle_s:
                return True
            if now - start >= _IDLE_TIMEOUT_S:
                return False
            self._sleep(_IDLE_POLL_S)

    @contextlib.contextmanager
    def _awake(self, hwnd: int) -> Iterator[None]:
        """Le jeu se croit actif le temps du bloc (sa fenêtre ne l'est jamais), puis plus.

        Au second plan, Fortnite tourne au ralenti et ignore la souris. En sortant
        du bloc, quoi qu'il arrive, il revient à son état réel ; Unreal relâche au
        passage les boutons et la capture de la souris.
        """
        if not self._cfg.background_activate:
            yield
            return
        self._api.post(hwnd, _WM_ACTIVATEAPP, 1, 0, 0)
        try:
            self._sleep(self._cfg.background_wait_s)  # sortie de veille, plein régime
            yield
        finally:
            with contextlib.suppress(InputError):
                self._api.post(hwnd, _WM_ACTIVATEAPP, 0, 0, 0)

    def _press(self, hwnd: int, cx: int, cy: int, clicks: int) -> None:
        post = self._api.post
        post(hwnd, _WM_MOUSEMOVE, 0, cx, cy)
        self._sleep(self._cfg.background_wait_s)
        for index in range(clicks):
            post(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, cx, cy)
            self._sleep(self._cfg.click_hold_s)
            post(hwnd, _WM_LBUTTONUP, 0, cx, cy)
            if index + 1 < clicks:
                self._sleep(0.12)
        self._sleep(self._cfg.background_wait_s)

    def click(
        self,
        hwnd: int,
        x: int,
        y: int,
        *,
        clicks: int = 1,
        cancel: Callable[[], bool] | None = None,
    ) -> bool:
        """Clique au point écran (x, y) de la fenêtre ``hwnd``.

        Faux quand le clic n'est pas fait : en mode ``cursor``, la souris est en
        mouvement ou un de ses boutons est enfoncé, ou bien ``cancel`` (bot mis
        en pause ou arrêté) est devenu vrai pendant cette attente.
        """
        api = self._api
        if not api.is_window(hwnd):
            raise InputError("fenêtre du jeu fermée avant le clic")
        cx, cy = api.to_client(hwnd, x, y)
        if self._cfg.background == "message":
            with self._awake(hwnd):
                self._press(hwnd, cx, cy, clicks)
            return True
        stop = cancel if cancel is not None else _never
        if not self._mouse_idle(stop) or stop():
            return False
        with self._awake(hwnd):  # réveil avant de prendre le curseur : moins longtemps parti
            if stop() or api.buttons_down():  # un bouton enfoncé pendant le réveil
                return False
            saved = api.cursor()
            api.set_cursor(x, y)
            placed = api.cursor()  # relu : Windows peut rogner la position demandée
            try:
                self._press(hwnd, cx, cy, clicks)
            finally:
                # retour au même pixel ; une souris bougée pendant le clic garde son
                # déplacement, pour que le curseur ne recule pas sous la main
                now_x, now_y = api.cursor()
                api.set_cursor(saved[0] + now_x - placed[0], saved[1] + now_y - placed[1])
        return True


def _never() -> bool:
    return False


def debug_click_marker(path_prefix: str, x: int, y: int) -> str:  # pragma: no cover - utilitaire
    """Trace un fichier texte à côté des captures, pour déboguer les clics."""
    target = PROJECT_ROOT / f"{path_prefix}_{x}_{y}.txt"
    target.write_text(f"{x},{y}\n", encoding="utf-8")
    return str(target)
