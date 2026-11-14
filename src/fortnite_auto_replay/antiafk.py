"""Anti-AFK : un geste au hasard de temps en temps en partie, pour ne pas être exclu.

Gestes (``anti_afk.gestures``) : une touche, un clic gauche ou droit au centre
du jeu, ou une rotation de la caméra.

Quand ? En partie seulement, d'après l'un de ces deux signes :

- un replay « en direct » écrit depuis moins de ``anti_afk.live_replay_s``
  (le jeu le complète toutes les 25 s environ ; rediffusions activées) ;
- le jeu au premier plan qui tient le curseur (caché, ou bloqué dans sa
  fenêtre par ``ClipCursor``) : au salon, il le montre.

Jeu au premier plan, rien n'a lieu si clavier et souris ont servi depuis
``anti_afk.idle_s`` : le joueur qui joue n'est jamais gêné. Au second plan,
seul le replay dit qu'une partie est en cours.

Comment ? Touches et clics : messages postés à la fenêtre du jeu, jamais une
entrée réelle, qui partirait dans la fenêtre active si le jeu perdait le
premier plan au mauvais moment. Au second plan, ils sont encadrés du même
réveil que les clics du moteur (``WM_ACTIVATEAPP``, voir ``actions``). Jeu au
premier plan, un clic n'a lieu que curseur tenu (vue de jeu, pas une carte ou
un inventaire ouverts).

La caméra, elle, ne tourne qu'avec la vraie souris : le jeu lit ses
déplacements en entrée brute, qu'aucun message ne remplace. Elle ne tourne
donc qu'au premier plan, curseur tenu, par petits pas relatifs (``SendInput``)
avant chacun desquels le premier plan, le curseur et l'inactivité du joueur
sont revérifiés. Ces pas comptent pour Windows comme une activité : le délai
d'inactivité ne les prend pas pour ceux du joueur.
"""

from __future__ import annotations

import ctypes
import logging
import random
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .chests import read_header
from .config import AntiAfkConfig

if TYPE_CHECKING:
    from .window import Focus

log = logging.getLogger(__name__)

_POLL_S = 1.0
_REPLAY_CHECK_S = 5.0  # le dossier des replays n'est relu qu'à cet intervalle
_ERROR_LOG_S = 60.0
_CURSOR_SHOWING = 0x1
_SM_VIRTUAL_SCREEN = (76, 77, 78, 79)  # x, y, largeur, hauteur
_MOUSEEVENTF_MOVE = 0x0001
_TURN_STEPS = 12  # une rotation en petits pas, sur 0,3 s environ
_TURN_STEP_S = 0.025
# une entrée datée jusqu'à ce délai après un pas de souris est la nôtre (Windows la
# date à son traitement, un peu après SendInput) ; une rotation entière y passe
_OWN_MARGIN_MS = 150
_OWN_GAP_MS = 1000  # au-delà depuis le dernier pas, une nouvelle rotation commence
_TICK = 0xFFFFFFFF

# touches sans effet hors de la partie (jamais Échap, qui ouvre le menu)
_NAMED = {"SPACE": 0x20, "SHIFT": 0x10, "CTRL": 0x11}


def key_code(name: str) -> int:
    """Code de touche virtuelle : une lettre, un chiffre, ``SPACE``, ``SHIFT`` ou ``CTRL``."""
    key = name.strip().upper()
    if key in _NAMED:
        return _NAMED[key]
    if len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
        return ord(key)
    raise ValueError(f"touche inconnue : {name!r} (lettre, chiffre, SPACE, SHIFT ou CTRL)")


def live_replay(demos: Path, now: float, fresh_s: float) -> bool:
    """Le replay le plus récent est-il « en direct » et écrit depuis moins de ``fresh_s`` ?"""
    newest: tuple[float, Path] | None = None
    try:
        for path in demos.glob("*.replay"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, path)
    except OSError:
        return False
    if newest is None or now - newest[0] > fresh_s:
        return False
    header = read_header(newest[1])
    return header is not None and header.live


class GameSource(Protocol):
    def focus(self) -> Focus: ...

    def hwnd(self) -> int: ...

    def region(self) -> tuple[int, int, int, int] | None:
        """Zone client du jeu en coordonnées écran ; ``None`` si absente ou réduite."""
        ...


class GameInput(Protocol):
    """Messages postés à la fenêtre du jeu (``actions.BackgroundClicker``)."""

    def press_key(self, hwnd: int, vk: int, hold_s: float, *, wake: bool) -> None: ...

    def press_button(
        self, hwnd: int, x: int, y: int, button: str, hold_s: float, *, wake: bool
    ) -> None: ...


class UserInput(Protocol):
    def idle_s(self) -> float:
        """Secondes depuis la dernière entrée du joueur (clavier, souris), hors :meth:`nudge`."""
        ...

    def cursor_held(self) -> bool:
        """Curseur caché ou bloqué dans une zone : un jeu le tient."""
        ...

    def nudge(self, dx: int, dy: int) -> None:
        """Déplacement relatif de la vraie souris (jeu au premier plan seulement)."""
        ...


class _LastInputInfo(ctypes.Structure):
    _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))


class _CursorInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    )


class Win32UserInput:
    """``UserInput`` réel, sur une instance privée de user32."""

    def __init__(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LastInputInfo)]
        user32.GetLastInputInfo.restype = wintypes.BOOL
        user32.GetCursorInfo.argtypes = [ctypes.POINTER(_CursorInfo)]
        user32.GetCursorInfo.restype = wintypes.BOOL
        user32.GetClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]
        user32.GetClipCursor.restype = wintypes.BOOL
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetTickCount.restype = wintypes.DWORD
        self._user32, self._kernel32 = user32, kernel32
        self._real: int | None = None  # dernière entrée du joueur (tick)
        self._own: tuple[int, int] | None = None  # ticks de la rotation en cours (début, fin)

    def _last_input(self) -> int | None:
        info = _LastInputInfo(cbSize=ctypes.sizeof(_LastInputInfo))
        if not self._user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return int(info.dwTime)

    def idle_s(self) -> float:
        tick = self._last_input()
        if tick is None:
            return 0.0  # inconnu : comme si le joueur venait de jouer
        if self._real is None or not self._ours(tick):
            self._real = tick
        return ((self._kernel32.GetTickCount() - self._real) & _TICK) / 1000

    def _ours(self, tick: int) -> bool:
        if self._own is None:
            return False
        start, end = self._own
        return (tick - start) & _TICK <= (end - start) & _TICK

    def cursor_held(self) -> bool:
        info = _CursorInfo(cbSize=ctypes.sizeof(_CursorInfo))
        if not self._user32.GetCursorInfo(ctypes.byref(info)):
            return False
        if not info.flags & _CURSOR_SHOWING or not info.hCursor:
            return True
        clip = wintypes.RECT()
        if not self._user32.GetClipCursor(ctypes.byref(clip)):
            return False
        x, y, width, height = (self._user32.GetSystemMetrics(i) for i in _SM_VIRTUAL_SCREEN)
        return (clip.left, clip.top, clip.right, clip.bottom) != (x, y, x + width, y + height)

    def nudge(self, dx: int, dy: int) -> None:
        from .actions import INPUT, MOUSEINPUT, InputError  # noqa: PLC0415 - Windows uniquement

        self.idle_s()  # note la dernière entrée du joueur avant d'y mêler la nôtre
        now = self._kernel32.GetTickCount()
        if self._own is None or (now - self._own[1]) & _TICK > _OWN_GAP_MS:
            self._own = (now, now)
        event = INPUT()
        event.type = 0  # INPUT_MOUSE
        event.union.mi = MOUSEINPUT(
            dx=dx, dy=dy, mouseData=0, dwFlags=_MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0
        )
        if self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise InputError(f"SendInput a échoué (erreur {ctypes.get_last_error()})")
        self._own = (self._own[0], (self._kernel32.GetTickCount() + _OWN_MARGIN_MS) & _TICK)


class AntiAfk:
    """Fait un geste au hasard en partie, dans son propre thread.

    :meth:`step` fait un tour et rend le délai avant le suivant (testable sans
    thread) ; :meth:`set_enabled` l'active ou le coupe en marche.
    """

    def __init__(
        self,
        cfg: AntiAfkConfig,
        demos_dir: Path,
        window: GameSource,
        game: GameInput | None,
        user: UserInput,
        *,
        background: bool = True,
        dry_run: bool = False,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        self.cfg = cfg
        self._demos = demos_dir
        self._window = window
        self._game = game
        self._user = user
        self._background = background  # faux avec window.require_foreground
        self.dry_run = dry_run or game is None
        self._clock = clock
        self._wall = wall
        self._rng = rng or random.Random()
        self._enabled = cfg.enabled
        self._next_at = float("-inf")
        self._replay_at = float("-inf")
        self._replay_live = False
        self._in_match = False
        self._error_logged_at = float("-inf")
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait  # interrompu par stop()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.cfg.enabled = enabled
        self._in_match = False
        log.info("anti-AFK %s", "activé" if enabled else "désactivé")

    def _live_replay(self) -> bool:
        now = self._clock()
        if now - self._replay_at >= _REPLAY_CHECK_S:
            self._replay_at = now
            self._replay_live = live_replay(self._demos, self._wall(), self.cfg.live_replay_s)
        return self._replay_live

    def _match(self, held: bool) -> bool:
        """En partie ? Note le passage d'un état à l'autre dans le journal."""
        playing = held or self._live_replay()
        if playing != self._in_match:
            self._in_match = playing
            log.info("anti-AFK : %s", "partie en cours" if playing else "hors partie")
        return playing

    def step(self) -> float:
        """Un tour : fait un geste si c'est le moment ; rend le délai avant le prochain tour."""
        if not self._enabled:
            return _POLL_S
        now = self._clock()
        if now < self._next_at:
            return min(_POLL_S, self._next_at - now)
        target = self._target()
        if target is None:
            return _POLL_S
        hwnd, front, held = target
        gestures = self._gestures(front, held)
        if gestures:
            self._next_at = now + self._rng.uniform(*self.cfg.interval_s)
            self._do(self._rng.choice(gestures), hwnd, front)
        return _POLL_S

    def _target(self) -> tuple[int, bool, bool] | None:
        """(fenêtre du jeu, premier plan ?, curseur tenu ?) s'il faut agir, sinon ``None``."""
        focus = self._window.focus()
        front = focus == "foreground"
        if not front and not (focus == "background" and self._background):
            return None
        if front and self._user.idle_s() < self.cfg.idle_s:
            return None  # le joueur joue
        # au second plan, le jeu rend le curseur : seul le replay compte
        held = front and self._user.cursor_held()
        if not self._match(held):
            return None
        hwnd = self._window.hwnd()
        return (hwnd, front, held) if hwnd else None

    def _gestures(self, front: bool, held: bool) -> list[str]:
        """Gestes possibles : clic en vue de jeu, caméra au premier plan seulement."""
        allowed = []
        for gesture in self.cfg.gestures:
            if gesture == "clicks" and front and not held:
                continue  # carte ou inventaire ouverts : le clic toucherait un menu
            if gesture == "camera" and not held:
                continue  # au second plan, ou curseur visible
            allowed.append(gesture)
        return allowed

    def _do(self, gesture: str, hwnd: int, front: bool) -> None:
        if gesture == "keys":
            what = self._key(hwnd, front)
        elif gesture == "clicks":
            what = self._click(hwnd, front)
        else:
            what = self._camera()
        if what:
            where = "" if front else " (jeu au second plan)"
            if self.dry_run:
                log.info("[dry-run] anti-AFK : %s%s", what, where)
            else:
                log.debug("anti-AFK : %s%s", what, where)

    def _key(self, hwnd: int, front: bool) -> str:
        name = self._rng.choice(self.cfg.keys)
        hold = self._rng.uniform(*self.cfg.hold_s)
        if self._game is not None and not self.dry_run:
            self._game.press_key(hwnd, key_code(name), hold, wake=not front)
        return f"touche {name}, {hold:.2f} s"

    def _click(self, hwnd: int, front: bool) -> str:
        region = self._window.region()
        if region is None:
            return ""
        button = self._rng.choice(("left", "right"))
        hold = self._rng.uniform(*self.cfg.click_hold_s)
        x, y = (region[0] + region[2]) // 2, (region[1] + region[3]) // 2
        if self._game is not None and not self.dry_run:
            self._game.press_button(hwnd, x, y, button, hold, wake=not front)
        return f"clic {'gauche' if button == 'left' else 'droit'}, {hold:.2f} s"

    def _camera(self) -> str:
        dx = round(self._rng.choice((-1, 1)) * self._rng.uniform(*self.cfg.turn_px))
        dy = round(dx * self._rng.uniform(-0.15, 0.15))
        what = f"caméra {dx:+d} x {dy:+d}"
        if not self.dry_run and not self._turn(dx, dy):
            what += " (interrompue)"
        return what

    def _turn(self, dx: int, dy: int) -> bool:
        """Rotation en petits pas ; faux dès que le jeu ou le joueur reprend la main."""
        done_x = done_y = 0
        for index in range(1, _TURN_STEPS + 1):
            if (
                self._stop.is_set()
                or self._window.focus() != "foreground"
                or not self._user.cursor_held()
                or self._user.idle_s() < self.cfg.idle_s
            ):
                return False
            step_x = round(dx * index / _TURN_STEPS) - done_x
            step_y = round(dy * index / _TURN_STEPS) - done_y
            self._user.nudge(step_x, step_y)
            done_x, done_y = done_x + step_x, done_y + step_y
            self._sleep(_TURN_STEP_S)
        return True

    def _run(self) -> None:
        delay = _POLL_S
        while not self._stop.wait(delay):
            try:
                delay = self.step()
            except Exception as exc:  # fenêtre fermée, envoi refusé : on réessaie plus tard
                now = self._clock()
                if now - self._error_logged_at >= _ERROR_LOG_S:
                    log.warning("anti-AFK : geste impossible (%s)", exc)
                    self._error_logged_at = now
                self._next_at = now + self.cfg.interval_s[0]
                delay = _POLL_S

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="anti-afk", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float) -> bool:
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)
        return self._thread is None or not self._thread.is_alive()
