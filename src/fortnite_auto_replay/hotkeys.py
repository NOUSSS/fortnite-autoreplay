"""Raccourcis clavier globaux sans dépendance (``GetAsyncKeyState``).

Le thread de surveillance tourne en tâche de fond et appelle les callbacks
fournis. Utilisé pour ``F8`` (pause/reprise) et ``F9`` (arrêt).

``GetAsyncKeyState`` voit les touches de toutes les applications : avec
``active``, un appui n'est pris en compte que si la condition est vraie (jeu
au premier plan), pour que F8 ou F9 tapé dans un autre logiciel pendant que
le bot tourne ne le mette pas en pause ni ne le ferme.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

_VK = {
    "F1": 0x70,
    "F2": 0x71,
    "F3": 0x72,
    "F4": 0x73,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "PAUSE": 0x13,
    "SCROLLLOCK": 0x91,
    "INSERT": 0x2D,
}


def resolve_key(name: str) -> int:
    key = name.strip().upper()
    if key in _VK:
        return _VK[key]
    if len(key) == 1:
        return ord(key)
    raise ValueError(f"raccourci inconnu : {name!r}")


class HotkeyListener:
    def __init__(
        self, poll_interval_s: float = 0.05, *, active: Callable[[], bool] | None = None
    ) -> None:
        self._interval = poll_interval_s
        self._active = active
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._bindings: dict[int, Callable[[], None]] = {}

    def bind(self, key: str, callback: Callable[[], None]) -> None:
        self._bindings[resolve_key(key)] = callback

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="hotkeys", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        user32 = ctypes.windll.user32
        pressed: set[int] = set()
        log.info("raccourcis actifs : %s", ", ".join(f"{vk:#x}" for vk in self._bindings))
        while not self._stop.is_set():
            for vk, callback in self._bindings.items():
                down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                if down and vk not in pressed:
                    pressed.add(vk)
                    try:
                        if self._active is None or self._active():
                            callback()
                        else:
                            log.info("raccourci %#x ignoré : le jeu n'est pas au premier plan", vk)
                    except Exception:
                        log.exception("erreur dans le callback du raccourci")
                elif not down and vk in pressed:
                    pressed.discard(vk)
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
