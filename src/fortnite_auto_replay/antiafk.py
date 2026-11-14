"""Anti-AFK : une touche au hasard de temps en temps en partie, pour ne pas être exclu.

Quand ? En partie seulement, d'après l'un de ces deux signes :

- un replay « en direct » écrit depuis moins de ``anti_afk.live_replay_s``
  (le jeu le complète toutes les 25 s environ ; rediffusions activées) ;
- le jeu au premier plan qui tient le curseur (caché, ou bloqué dans sa
  fenêtre par ``ClipCursor``) : au salon, il le montre.

Jeu au premier plan, l'appui n'a lieu que si clavier et souris n'ont pas servi
depuis ``anti_afk.idle_s`` : le joueur qui joue n'est jamais gêné. Au second
plan, seul le replay dit qu'une partie est en cours.

Comment ? ``WM_KEYDOWN`` puis ``WM_KEYUP`` postés à la fenêtre du jeu, jamais
une entrée réelle : elle partirait dans la fenêtre active si le jeu perdait le
premier plan au mauvais moment. Au second plan, l'appui est encadré du même
réveil que les clics (``WM_ACTIVATEAPP``, voir ``actions``). Les messages
postés ne comptent pas comme une activité de l'utilisateur : le délai
d'inactivité reste celui du joueur.
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


class KeyPresser(Protocol):
    def press_key(self, hwnd: int, vk: int, hold_s: float, *, wake: bool) -> None: ...


class UserInput(Protocol):
    def idle_s(self) -> float:
        """Secondes depuis la dernière entrée réelle (clavier, souris) de la session."""
        ...

    def cursor_held(self) -> bool:
        """Curseur caché ou bloqué dans une zone : un jeu le tient."""
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
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetTickCount.restype = wintypes.DWORD
        self._user32, self._kernel32 = user32, kernel32

    def idle_s(self) -> float:
        info = _LastInputInfo(cbSize=ctypes.sizeof(_LastInputInfo))
        if not self._user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0  # inconnu : comme si le joueur venait de jouer
        return ((self._kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000

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


class AntiAfk:
    """Appuie sur une touche au hasard en partie, dans son propre thread.

    :meth:`step` fait un tour et rend le délai avant le suivant (testable sans
    thread) ; :meth:`set_enabled` l'active ou le coupe en marche.
    """

    def __init__(
        self,
        cfg: AntiAfkConfig,
        demos_dir: Path,
        window: GameSource,
        keys: KeyPresser | None,
        user: UserInput,
        *,
        background: bool = True,
        dry_run: bool = False,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.cfg = cfg
        self._demos = demos_dir
        self._window = window
        self._keys = keys
        self._user = user
        self._background = background  # faux avec window.require_foreground
        self.dry_run = dry_run
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

    def _match(self, front: bool) -> bool:
        """En partie ? Note le passage d'un état à l'autre dans le journal."""
        playing = (front and self._user.cursor_held()) or self._live_replay()
        if playing != self._in_match:
            self._in_match = playing
            log.info("anti-AFK : %s", "partie en cours" if playing else "hors partie")
        return playing

    def step(self) -> float:
        """Un tour : appuie si c'est le moment ; rend le délai avant le prochain tour."""
        if not self._enabled:
            return _POLL_S
        now = self._clock()
        if now < self._next_at:
            return min(_POLL_S, self._next_at - now)
        target = self._target()
        if target is not None:
            self._press(now, *target)
        return _POLL_S

    def _target(self) -> tuple[int, bool] | None:
        """(fenêtre du jeu, au premier plan ?) s'il faut appuyer maintenant, sinon ``None``."""
        focus = self._window.focus()
        front = focus == "foreground"
        if not front and not (focus == "background" and self._background):
            return None
        if front and self._user.idle_s() < self.cfg.idle_s:
            return None  # le joueur joue
        if not self._match(front):
            return None
        hwnd = self._window.hwnd()
        return (hwnd, front) if hwnd else None

    def _press(self, now: float, hwnd: int, front: bool) -> None:
        name = self._rng.choice(self.cfg.keys)
        hold = self._rng.uniform(*self.cfg.hold_s)
        self._next_at = now + self._rng.uniform(*self.cfg.interval_s)
        where = "" if front else " (jeu au second plan)"
        if self.dry_run or self._keys is None:
            log.info("[dry-run] anti-AFK : touche %s, %.2f s%s", name, hold, where)
            return
        self._keys.press_key(hwnd, key_code(name), hold, wake=not front)
        log.debug("anti-AFK : touche %s, %.2f s%s", name, hold, where)

    def _run(self) -> None:
        delay = _POLL_S
        while not self._stop.wait(delay):
            try:
                delay = self.step()
            except Exception as exc:  # fenêtre fermée, envoi refusé : on réessaie plus tard
                now = self._clock()
                if now - self._error_logged_at >= _ERROR_LOG_S:
                    log.warning("anti-AFK : appui impossible (%s)", exc)
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
