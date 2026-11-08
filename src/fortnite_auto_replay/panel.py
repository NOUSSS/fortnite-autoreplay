"""Panneau de contrôle du bot : état, pause, réponse au dialogue, compteurs.

Une petite fenêtre sans bordure, toujours au-dessus du jeu :

- elle ne prend jamais le focus (``WS_EX_NOACTIVATE``) : un clic sur le
  panneau ne doit pas le retirer au jeu, ni à la fenêtre où l'on travaille.
  Si elle le prend quand même, elle le rend à la fenêtre d'avant, ou au jeu ;
- en pleine partie (jeu au premier plan, curseur caché ou bloqué dans le jeu),
  la souris la traverse : un tir dont le curseur caché passe sur elle reste au
  jeu, au lieu de cliquer ou de déplacer le panneau ;
- un seul panneau à la fois : en lancer un ferme celui qui tournait ;
- les captures d'écran ne la voient pas (``WDA_EXCLUDEFROMCAPTURE``) : posée sur
  le jeu, elle ne cache aucun bouton au bot. Un clic qui tomberait sur elle est
  annulé par le moteur (``GameWindow.covering``) ;
- repliée, elle tient sur une ligne : pastille, état, bouton Pause.

Le moteur tourne dans un thread à part ; seul le thread principal touche à Tk.
Toute fermeture est journalisée avec sa cause ; un moteur qui ne répond plus
voit la pile de ses threads écrite dans ``logs/crash.log``.
"""

from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import logging
import os
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol

from . import diagnostics, history
from .actions import Actions
from .capture import CaptureError, Frame, ScreenCapture
from .chests import ChestTracker, Estimate
from .config import Config, PanelConfig, update_local_config
from .engine import Engine, Phase, Status
from .templates import TemplateLibrary
from .updater import Updates, UpdateState
from .window import GameWindow

log = logging.getLogger(__name__)

NBSP = "\u00a0"
_POLL_MS = 250
_STALL_S = 20.0  # moteur sans tour de boucle depuis ce délai : bloqué
_JOIN_S = 3.0  # attente de l'arrêt du moteur à la fermeture
_REPLACE_S = 8.0  # attente de la fermeture d'un panneau déjà ouvert (moteur compris)
_LOCK_POLL_S = 0.25  # entre deux essais du verrou des panneaux

Area = tuple[int, int, int, int]  # left, top, right, bottom en pixels écran


# --- jetons -----------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Palette:
    """Thème sombre de Windows 11 : couches, filets, accent, états."""

    bg: str = "#202020"
    layer: str = "#2B2B2B"
    layer_hover: str = "#323232"
    layer_pressed: str = "#272727"
    stroke: str = "#3A3A3A"
    text: str = "#FFFFFF"
    text_2: str = "#C5C5C5"
    text_3: str = "#9E9E9E"
    accent: str = "#60CDFF"
    accent_hover: str = "#5BBAE8"
    accent_pressed: str = "#56A8D1"
    on_accent: str = "#000000"
    success: str = "#6CCB5F"
    caution: str = "#FCE100"
    critical: str = "#FF99A4"
    neutral: str = "#9E9E9E"
    caution_bg: str = "#433519"
    critical_bg: str = "#442726"


C = Palette()

# grille de 4 px, en pixels à 96 ppp (multipliés par l'échelle d'affichage)
WIDTH = 320
HEADER_H = 44
PAD = 12  # marge intérieure de la fenêtre et des bandeaux
GAP = 8  # entre éléments voisins d'une ligne, sous un intertitre
SECTION_GAP = 16
ROW_H = 24
CONTROL_H = 28
SEGMENT_H = 32
RADIUS = 4
DOT = 8
RECENT_ROWS = 5

# glyphes de Segoe Fluent Icons
ICON_UP = "\ue70e"
ICON_DOWN = "\ue70d"
ICON_INFO = "\ue946"
ICON_WARNING = "\ue7ba"
ICON_ERROR = "\uea39"
ICON_DOT = "\ue91f"  # disque plein, lissé (un ovale du canevas ne l'est pas)


@dataclass(frozen=True, slots=True)
class Look:
    title: str
    dot: str


LOOKS: dict[Phase, Look] = {
    "active": Look("Actif", C.success),
    "paused": Look("En pause", C.neutral),
    "stuck": Look("Bloqué", C.critical),
    "errors": Look("Bloqué", C.critical),
    "no_game": Look("En attente du jeu", C.caution),
    "hidden": Look("En attente du jeu", C.caution),
    "background": Look("En attente du jeu", C.caution),
    "capture": Look("Capture impossible", C.critical),
    "covered": Look("Clic annulé", C.caution),
    "mouse": Look("Clic en attente", C.caution),
    "stopped": Look("Arrêté", C.neutral),
}

ANSWERS = (("ready", "Prêt"), ("not_yet", "Pas encore"), ("ignore", "Ignorer"))
PRIMARY_LABELS = ("Pause", "Lancer", "Reprendre", "Quitter")
NO_CLICK = "Aucun clic pour l\u2019instant."


# --- textes -------------------------------------------------------------------
def duration(seconds: float) -> str:
    """« 12 min », « 1 h 05 », « 31 h » (au-delà de 10 h, les minutes se taisent)."""
    minutes = max(0, int(seconds // 60))
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{minutes}{NBSP}min"
    if hours >= 10:
        return f"{hours}{NBSP}h"
    return f"{hours}{NBSP}h{NBSP}{rest:02d}"


def number(value: int) -> str:
    """Séparateur de milliers à la française (espace insécable : Segoe UI n'a pas l'espace fine)."""
    return f"{value:,}".replace(",", NBSP)


def plural(count: int, word: str) -> str:
    """« 0 clic », « 1 clic », « 2 clics » : le pluriel français commence à 2."""
    return f"{number(count)}{NBSP}{word}{'s' if count > 1 else ''}"


def explain(status: Status) -> tuple[str, str] | None:  # noqa: PLR0911 - une branche par phase
    """Bandeau sous l'en-tête : (gravité, message), ou rien quand tout va bien."""
    phase, detail = status.phase, status.detail
    if phase in ("stuck", "errors"):
        return "critical", f"{detail}. Le bot s\u2019est mis en pause."
    if phase == "capture":
        return "critical", (
            "La capture d\u2019écran échoue. Le bot réessaie, détail dans logs/bot.log."
        )
    if phase == "covered":
        tail = " Déplacez le panneau." if detail.startswith("Le panneau") else ""
        return "caution", f"{detail}, le clic est annulé.{tail}"
    if phase == "mouse":
        return "info", f"{detail}{NBSP}: le bot attend que la souris reste immobile un instant."
    if phase == "no_game":
        return "caution", "Fortnite n\u2019est pas ouvert. Le bot attend sa fenêtre."
    if phase == "hidden":
        return "caution", "Fortnite est réduit. Le bot reprend dès que sa fenêtre est visible."
    if phase == "background":
        return "caution", (
            "Fortnite n\u2019est pas au premier plan. Le bot reprend dès votre retour en jeu."
        )
    if phase == "stopped":
        reason = "Le moteur s\u2019est arrêté sur une erreur, détail dans logs/bot.log. "
        return "info", (reason if detail else "") + "Quittez le panneau puis relancez-le."
    if status.dry_run:
        return "info", f"Simulation{NBSP}: le bot repère les boutons sans cliquer."
    return None


UPDATE_ACTIONS = {"available": "Mettre à jour", "failed": "Réessayer"}
# phases d'un moteur qui tourne (pas en pause) : relancé sans pause après une mise à jour
RUNNING = frozenset({"active", "no_game", "hidden", "background", "capture", "covered", "mouse"})


def update_notice(state: UpdateState) -> tuple[str, str, str] | None:
    """Bandeau de mise à jour : (gravité, message, libellé du bouton), ou rien."""
    version = state.version
    if state.phase == "available":
        return "info", f"La version {version} est disponible.", UPDATE_ACTIONS["available"]
    if state.phase == "downloading":
        percent = int(state.progress * 100)
        return "info", f"Téléchargement de la version {version}… {percent}{NBSP}%", ""
    if state.phase == "ready":
        return "info", f"Installation de la version {version}…", ""
    if state.phase == "failed":
        reason = state.error.split(" (", 1)[0]  # le détail technique reste dans le journal
        message = f"La mise à jour a échoué{NBSP}: {reason}."
        return "caution", message, UPDATE_ACTIONS["failed"]
    return None


def missing_hint(disabled: frozenset[str]) -> str:
    """Sous le choix de réponse : les boutons sans modèle, et où apprendre à les capturer."""
    names = [label for value, label in ANSWERS if value in disabled]
    if not names:
        return ""
    quoted = " et ".join(f"«{NBSP}{name}{NBSP}»" for name in names)
    return f"Pas de modèle pour {quoted}{NBSP}: voir docs/TEMPLATES.md."


def primary_label(phase: Phase, started: bool) -> str:
    """Le bouton principal dit ce qu'il va faire."""
    if phase == "stopped":
        return "Quitter"
    if phase == "paused":
        return "Reprendre" if started else "Lancer"
    if phase in ("stuck", "errors"):
        return "Reprendre"
    return "Pause"


def header_detail(status: Status) -> str:
    """À côté de l'état, quand il est court : les clics de la session, s'il y en a."""
    if status.actions and status.phase in ("active", "paused"):
        return plural(status.actions, "clic")
    return ""


def chests_line(estimate: Estimate | None) -> str:
    """« Environ 2 770, dont 1 000 confirmés » ; rien sans estimation."""
    if estimate is None:
        return ""
    confirmed = f"{number(estimate.confirmed)}{NBSP}confirmé{'s' if estimate.confirmed > 1 else ''}"
    if estimate.total <= estimate.confirmed:  # aucune partie depuis le dernier palier
        return confirmed
    return f"Environ {number(estimate.total)}, dont {confirmed}"


def counters_from(cfg: Config) -> list[tuple[str, tuple[str, ...]]]:
    """Un compteur par bouton, les deux réponses au dialogue réunies sous « Toujours là »."""
    rows: list[tuple[str, tuple[str, ...]]] = [
        (s.label, (s.slug,)) for s in cfg.states if s.enabled and not s.still_here
    ]
    dialog = tuple(s.slug for s in cfg.states if s.enabled and s.still_here)
    if dialog:
        rows.append(("Toujours là", dialog))
    return rows


# --- géométrie ----------------------------------------------------------------
def clamp(x: int, y: int, width: int, height: int, area: Area) -> tuple[int, int]:
    """Garde la fenêtre entière dans la zone de travail ; le haut l'emporte si elle déborde."""
    left, top, right, bottom = area
    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    return x, y


def default_home(area: Area, scale: float) -> tuple[int, int]:
    """Haut gauche de la zone du jeu, sous la barre du salon : aucun bouton visé n'y est."""
    return area[0] + round(24 * scale), area[1] + round(120 * scale)


def focus_step(foreground: int, ours: bool, other: int) -> tuple[int, int]:
    """Garde du premier plan : (fenêtre à qui le rendre, 0 sinon ; dernière fenêtre hors panneau).

    Tk prend le premier plan en créant sa première fenêtre, avant que
    ``WS_EX_NOACTIVATE`` puisse être posé : le panneau le rend aussitôt à la
    fenêtre d'avant (le jeu, en général).
    """
    if not foreground:
        return 0, other
    if ours:
        return other, other
    return 0, foreground


def give_back_targets(other: int, game: int) -> list[int]:
    """À qui rendre le premier plan : la fenêtre d'avant, sinon le jeu.

    La fenêtre d'avant peut avoir disparu (console du lanceur, par exemple) :
    sans le jeu en second choix, le panneau garderait le premier plan.
    """
    return [hwnd for hwnd in dict.fromkeys((other, game)) if hwnd]


def mouse_through(
    game_front: bool, hidden: bool, clipped: bool, on_panel: bool, previous: bool
) -> bool:
    """Le panneau doit-il laisser passer la souris ? Oui quand le jeu la tient.

    En partie, Fortnite cache le curseur ou le bloque dans sa fenêtre ; le
    curseur caché se promène quand même dessous, et un tir sur le panneau le
    cliquerait. Au-dessus du panneau, c'est lui qui montre son curseur : l'état
    d'avant est gardé tant que la souris y reste.
    """
    if not game_front:
        return False
    if clipped:
        return True
    if on_panel:
        return previous
    return hidden


# --- pilotage -----------------------------------------------------------------
class Controller(Protocol):
    """Ce dont le panneau a besoin : le moteur réel ou une démonstration."""

    @property
    def closing(self) -> bool: ...

    def status(self) -> Status: ...

    def toggle(self) -> None: ...

    def quit(self) -> None: ...

    def check_stall(self) -> None:
        """Signale un moteur figé (appelé à chaque rafraîchissement du panneau)."""
        ...

    def still_here_answers(self) -> list[str]: ...

    def set_still_here(self, answer: str) -> None: ...

    def totals(self, status: Status) -> tuple[int, int, float]:
        """Toutes les sessions, celle-ci comprise : (sessions, clics, secondes)."""
        ...

    def counters(self) -> list[tuple[str, tuple[str, ...]]]:
        """Compteurs de la session : (libellé, slugs additionnés)."""
        ...

    def chests(self) -> Estimate | None:
        """Coffres ouverts estimés d'après les journaux du jeu, s'il y a de quoi estimer."""
        ...


class _NotOpenYet:
    """Capture tant que le thread du moteur n'a pas ouvert la vraie."""

    def grab(self) -> Frame:
        raise CaptureError("capture pas encore ouverte")


class EngineController:
    """Fait tourner le moteur dans un thread et le pilote pour le panneau.

    La capture s'ouvre dans le thread du moteur : certains backends (mss) ne
    s'utilisent que dans le thread qui les a créés.
    """

    def __init__(self, engine: Engine, cfg: Config, window: GameWindow | None) -> None:
        self.engine = engine
        self._cfg = cfg
        self._window = window
        self._fatal = ""
        self._quit = threading.Event()
        self._stall_reported = False
        path = cfg.resolved_stats_file()
        past = history.summarize(history.read_sessions(path)) if path is not None else {}
        self._past = (
            int(past.get("sessions", 0)),
            int(past.get("actions", 0)),
            float(past.get("uptime_h", 0.0)) * 3600,
        )
        self._chests = (
            ChestTracker(cfg.chests, cfg.resolved_matches_file()) if cfg.chests.enabled else None
        )
        self._thread = threading.Thread(target=self._run, name="moteur", daemon=True)

    def start(self) -> None:
        self._thread.start()
        if self._chests is not None:
            self._chests.start()

    def _run(self) -> None:
        roi = self._window if self._cfg.window.use_as_roi else None
        try:
            with ScreenCapture(self._cfg.capture, window=roi) as capture:
                self.engine.capture = capture
                self.engine.run()
        except Exception as exc:  # le panneau doit le dire, pas mourir en silence
            log.exception("le moteur s'est arrêté")
            self._fatal = str(exc) or exc.__class__.__name__

    def join(self, timeout: float) -> bool:
        """Attend la fin du moteur ; faux s'il tourne encore après ``timeout``."""
        if self._thread.is_alive():
            self._thread.join(timeout)
        return not self._thread.is_alive()

    def check_stall(self) -> None:
        """Moteur figé (capture ou pilote bloqué) : la pile de ses threads, une fois par blocage."""
        if not self._thread.is_alive():
            return
        stalled = time.monotonic() - self.engine.last_beat >= _STALL_S
        if stalled and not self._stall_reported:
            diagnostics.dump_threads(f"moteur sans réponse depuis {_STALL_S:.0f}{NBSP}s")
        self._stall_reported = stalled

    @property
    def closing(self) -> bool:
        return self._quit.is_set()

    def status(self) -> Status:
        status = self.engine.status()
        if self._fatal:
            return dataclasses.replace(status, phase="stopped", detail=self._fatal)
        return status

    def toggle(self) -> None:
        self.engine.toggle()

    def quit(self) -> None:
        self._quit.set()
        self.engine.stop()
        if self._chests is not None:
            self._chests.stop()

    def still_here_answers(self) -> list[str]:
        return self.engine.still_here_answers()

    def set_still_here(self, answer: str) -> None:
        self.engine.set_still_here(answer)

    def totals(self, status: Status) -> tuple[int, int, float]:
        sessions, actions, seconds = self._past
        if status.dry_run:  # une simulation n'entre pas dans l'historique
            return sessions, actions, seconds
        return sessions + 1, actions + status.actions, seconds + time.time() - status.started_at

    def counters(self) -> list[tuple[str, tuple[str, ...]]]:
        return counters_from(self._cfg)

    def chests(self) -> Estimate | None:
        return self._chests.estimate() if self._chests is not None else None


# --- Win32 --------------------------------------------------------------------
_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_LWA_ALPHA = 0x2
_RDW_INVALIDATE_ALL = 0x0001 | 0x0080 | 0x0400  # INVALIDATE | ALLCHILDREN | FRAME
_CURSOR_SHOWING = 0x1
_SM_VIRTUAL_SCREEN = (76, 77, 78, 79)  # x, y, largeur, hauteur
_WM_CLOSE = 0x0010
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80  # verrou d'un processus mort : acquis quand même
_LOCK_NAME = "Local\\FortniteAutoReplay.panel"
TITLE = "Fortnite AutoReplay"
# titres des panneaux à remplacer, ancien nom compris (versions 0.1.0 lancées depuis les sources)
_PANEL_TITLES = frozenset({TITLE, "Fortnite Auto Replay"})
_TK_CLASS = "TkTopLevel"
_WDA_EXCLUDEFROMCAPTURE = 0x11
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_BORDER_COLOR = 34
_DWMWCP_ROUND = 2
_MONITOR_DEFAULTTONEAREST = 2
_GA_ROOT = 2


class _CursorInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    )


class _MonitorInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    )


def _colorref(color: str) -> int:
    red, green, blue = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return red | green << 8 | blue << 16


_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class Win32:
    """Appels Win32 du panneau, typés sur des instances privées des DLL."""

    def __init__(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = wintypes.HWND
        user32.GetAncestor.argtypes = [hwnd, wintypes.UINT]
        user32.GetAncestor.restype = hwnd
        user32.GetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowDisplayAffinity.argtypes = [hwnd, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = hwnd
        user32.SetForegroundWindow.argtypes = [hwnd]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [hwnd]
        user32.IsWindow.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [hwnd, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [hwnd]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetCursorInfo.argtypes = [ctypes.POINTER(_CursorInfo)]
        user32.GetCursorInfo.restype = wintypes.BOOL
        user32.GetClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]
        user32.GetClipCursor.restype = wintypes.BOOL
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.GetWindowRect.argtypes = [hwnd, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.SetLayeredWindowAttributes.argtypes = [
            hwnd,
            wintypes.DWORD,
            wintypes.BYTE,
            wintypes.DWORD,
        ]
        user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        user32.RedrawWindow.argtypes = [hwnd, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        user32.RedrawWindow.restype = wintypes.BOOL
        user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetClassNameW.argtypes = [hwnd, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [hwnd, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.PostMessageW.argtypes = [hwnd, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        self.user32 = user32
        self.kernel32 = kernel32
        self._lock: int | None = None  # verrou des panneaux, tenu jusqu'à la fermeture
        self._locked = False
        self.dwm: ctypes.WinDLL | None
        try:
            dwm = ctypes.WinDLL("dwmapi")
            dwm.DwmSetWindowAttribute.argtypes = [
                hwnd,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            dwm.DwmSetWindowAttribute.restype = ctypes.c_long
            self.dwm = dwm
        except OSError:
            self.dwm = None

    def scale(self) -> float:
        try:
            dpi = self.user32.GetDpiForSystem()
        except AttributeError:  # Windows < 10
            return 1.0
        return max(1.0, dpi / 96)

    def top_level(self, child: int) -> int:
        return int(self.user32.GetAncestor(child, _GA_ROOT) or child)

    def foreground(self) -> int:
        return int(self.user32.GetForegroundWindow() or 0)

    def pid(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def ours(self, hwnd: int) -> bool:
        """La fenêtre appartient-elle à ce processus (panneau, racine Tk cachée) ?"""
        return self.pid(hwnd) == os.getpid()

    def give_back(self, hwnd: int) -> bool:
        """Rend le premier plan à une fenêtre visible d'un autre processus."""
        user32 = self.user32
        return bool(
            hwnd
            and user32.IsWindow(hwnd)
            and user32.IsWindowVisible(hwnd)
            and not self.ours(hwnd)
            and user32.SetForegroundWindow(hwnd)
        )

    def cursor_state(self) -> tuple[int, int, bool, bool]:
        """Position du curseur, caché ou non, bloqué ou non dans une zone (``ClipCursor``)."""
        info = _CursorInfo()
        info.cbSize = ctypes.sizeof(_CursorInfo)
        if not self.user32.GetCursorInfo(ctypes.byref(info)):
            return 0, 0, False, False
        hidden = not info.flags & _CURSOR_SHOWING or not info.hCursor
        clip = wintypes.RECT()
        if not self.user32.GetClipCursor(ctypes.byref(clip)):
            return info.ptScreenPos.x, info.ptScreenPos.y, hidden, False
        x, y, width, height = (self.user32.GetSystemMetrics(i) for i in _SM_VIRTUAL_SCREEN)
        clipped = (clip.left, clip.top, clip.right, clip.bottom) != (x, y, x + width, y + height)
        return info.ptScreenPos.x, info.ptScreenPos.y, hidden, clipped

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return 0, 0, 0, 0
        return rect.left, rect.top, rect.right, rect.bottom

    def set_mouse_through(self, hwnd: int, through: bool) -> None:
        """La souris traverse-t-elle la fenêtre (``WS_EX_LAYERED | WS_EX_TRANSPARENT``) ?"""
        user32 = self.user32
        extended = self.ex_style(hwnd)
        if not through:
            user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, extended & ~_WS_EX_TRANSPARENT)
            return
        if not extended & _WS_EX_LAYERED:
            # fenêtre en couches opaque : sans l'alpha posé aussitôt, elle disparaîtrait
            extended |= _WS_EX_LAYERED
            user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, extended)
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, _LWA_ALPHA)
            user32.RedrawWindow(hwnd, None, None, _RDW_INVALIDATE_ALL)
        user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, extended | _WS_EX_TRANSPARENT)

    def other_panels(self) -> list[tuple[int, int]]:
        """Panneaux d'autres processus encore ouverts : (fenêtre, pid)."""
        found: list[tuple[int, int]] = []
        own = os.getpid()
        name = ctypes.create_unicode_buffer(64)

        def visit(hwnd: int, _param: int) -> bool:
            if self.user32.GetClassNameW(hwnd, name, 64) and name.value == _TK_CLASS:
                self.user32.GetWindowTextW(hwnd, name, 64)
                pid = self.pid(hwnd)
                if name.value in _PANEL_TITLES and pid != own:
                    found.append((int(hwnd), pid))
            return True

        self.user32.EnumWindows(_ENUM_PROC(visit), 0)
        return found

    def close_and_wait(self, hwnd: int, pid: int, timeout_s: float) -> bool:
        """Demande à une fenêtre de se fermer (``WM_CLOSE``) ; vrai si son processus s'arrête."""
        process = self.kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not self.user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0):  # droits plus élevés (UIPI)
            if process:
                self.kernel32.CloseHandle(process)
            return False
        if not process:  # processus inaccessible : on suit sa fenêtre à la place
            deadline = time.monotonic() + timeout_s
            while self.user32.IsWindow(hwnd) and time.monotonic() < deadline:
                time.sleep(0.1)
            return not self.user32.IsWindow(hwnd)
        try:
            result = self.kernel32.WaitForSingleObject(process, int(timeout_s * 1000))
            return result == _WAIT_OBJECT_0
        finally:
            self.kernel32.CloseHandle(process)

    def take_lock(self, wait_s: float) -> bool:
        """Prend le verrou des panneaux (un seul à la fois) ; faux s'il est tenu ailleurs."""
        if self._locked:
            return True
        if self._lock is None:
            self._lock = self.kernel32.CreateMutexW(None, False, _LOCK_NAME) or None
            if self._lock is None:  # créé par un panneau aux droits plus élevés
                time.sleep(wait_s)
                return False
        result = self.kernel32.WaitForSingleObject(self._lock, int(wait_s * 1000))
        self._locked = result in {_WAIT_OBJECT_0, _WAIT_ABANDONED}
        return self._locked

    def release_lock(self) -> None:
        """Rend le verrou, une fois le moteur arrêté et sa session enregistrée."""
        if self._lock is None:
            return
        if self._locked:
            self.kernel32.ReleaseMutex(self._lock)
            self._locked = False
        self.kernel32.CloseHandle(self._lock)
        self._lock = None

    def ex_style(self, hwnd: int) -> int:
        return int(self.user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE))

    def style(self, hwnd: int, *, exclude_from_capture: bool) -> None:
        """Jamais activée, hors barre des tâches, invisible aux captures, coins arrondis."""
        extended = self.ex_style(hwnd) | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
        self.user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, extended)
        if exclude_from_capture and not self.user32.SetWindowDisplayAffinity(
            hwnd, _WDA_EXCLUDEFROMCAPTURE
        ):
            log.warning("le panneau reste visible des captures (Windows 10 2004 ou plus requis)")
        if self.dwm is not None:
            corner = ctypes.c_int(_DWMWCP_ROUND)
            self.dwm.DwmSetWindowAttribute(
                hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), 4
            )
            border = wintypes.DWORD(_colorref(C.stroke))
            self.dwm.DwmSetWindowAttribute(hwnd, _DWMWA_BORDER_COLOR, ctypes.byref(border), 4)

    def work_area(self, x: int, y: int) -> Area:
        """Zone de travail (sans barre des tâches) de l'écran le plus proche du point."""
        monitor = self.user32.MonitorFromPoint(wintypes.POINT(x, y), _MONITOR_DEFAULTTONEAREST)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not monitor or not self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return x, y, x + 1920, y + 1080
        work = info.rcWork
        return work.left, work.top, work.right, work.bottom


# --- composants -----------------------------------------------------------------
def _round_rect(
    canvas: tk.Canvas, box: tuple[float, float, float, float], radius: float, **kw: object
) -> int:
    x1, y1, x2, y2 = box
    r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = (
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    )  # fmt: skip
    return canvas.create_polygon(points, smooth=True, **kw)


@dataclass(frozen=True, slots=True)
class _Fill:
    rest: str
    hover: str
    pressed: str
    text: str
    stroke: str = ""


_KINDS = {
    "accent": _Fill(C.accent, C.accent_hover, C.accent_pressed, C.on_accent),
    "standard": _Fill(C.layer, C.layer_hover, C.layer_pressed, C.text, C.stroke),
    "subtle": _Fill(C.bg, C.layer_hover, C.layer_pressed, C.text_2),
}


class Button(tk.Canvas):
    """Bouton de Windows 11 : le survol et l'appui changent sa surface, sans halo."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        kind: str,
        font: tkfont.Font,
        width: int,
        height: int,
        radius: int,
        bg: str = C.bg,  # fond derrière les coins arrondis (celui d'un bandeau, par exemple)
    ) -> None:
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
        self._fill = _KINDS[kind]
        self._command = command
        self._hover = self._pressed = False
        stroke = self._fill.stroke or self._fill.rest
        box = (0, 0, width - 1, height - 1)
        self._shape = _round_rect(self, box, radius, fill=self._fill.rest, outline=stroke)
        self._text = self.create_text(
            width / 2, height / 2, text=text, font=font, fill=self._fill.text
        )
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)

    def _set(self, *, hover: bool | None = None, pressed: bool | None = None) -> None:
        if hover is not None:
            self._hover = hover
        if pressed is not None:
            self._pressed = pressed
        fill = self._fill
        color = fill.pressed if self._pressed else fill.hover if self._hover else fill.rest
        self.itemconfigure(self._shape, fill=color, outline=fill.stroke or color)

    def _release(self, event: tk.Event) -> None:
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._set(pressed=False)
        if inside:
            self._command()

    def set_text(self, text: str) -> None:
        if self.itemcget(self._text, "text") != text:
            self.itemconfigure(self._text, text=text)


class Segmented(tk.Canvas):
    """Choix exclusif en segments ; le segment choisi prend l'accent."""

    def __init__(
        self,
        parent: tk.Misc,
        options: Sequence[tuple[str, str]],
        command: Callable[[str], None],
        *,
        width: int,
        height: int,
        radius: int,
        inset: int,
        font: tkfont.Font,
        strong: tkfont.Font,
    ) -> None:
        super().__init__(parent, width=width, height=height, bg=C.bg, highlightthickness=0, bd=0)
        self._choices = list(options)
        self._command = command
        self._fonts = (font, strong)
        self._size = (width, height, radius, inset)
        self._selected = ""
        self._disabled: frozenset[str] = frozenset()
        self._hover = self._pressed = ""
        self.bind("<Motion>", lambda e: self._set_hover(self._at(e.x)))
        self.bind("<Leave>", lambda _e: self._set_hover(""))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._click)
        self._draw()

    def _at(self, x: float) -> str:
        step = self._size[0] / len(self._choices)
        index = min(len(self._choices) - 1, max(0, int(x // step)))
        return self._choices[index][0]

    def _set_hover(self, value: str) -> None:
        if value != self._hover:
            self._hover = value
            if not value:
                self._pressed = ""
            self._draw()

    def _press(self, event: tk.Event) -> None:
        value = self._at(event.x)
        if value not in self._disabled and value != self._selected:
            self._pressed = value
            self._draw()

    def _click(self, event: tk.Event) -> None:
        if self._pressed:
            self._pressed = ""
            self._draw()
        if not (0 <= event.x < self._size[0] and 0 <= event.y < self._size[1]):
            return
        value = self._at(event.x)
        if value not in self._disabled and value != self._selected:
            self._command(value)

    def set(self, selected: str, disabled: frozenset[str]) -> None:
        if (selected, disabled) != (self._selected, self._disabled):
            self._selected, self._disabled = selected, disabled
            self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width, height, radius, inset = self._size
        font, strong = self._fonts
        _round_rect(self, (0, 0, width - 1, height - 1), radius, fill=C.layer, outline=C.stroke)
        step = width / len(self._choices)
        for index, (value, label) in enumerate(self._choices):
            left, right = index * step, (index + 1) * step
            inner = (left + inset, inset, right - inset - 1, height - inset - 1)
            color, face = C.text, font
            if value == self._selected:
                _round_rect(self, inner, radius - 1, fill=C.accent, outline=C.accent)
                color, face = C.on_accent, strong
            elif value in self._disabled:
                color = C.text_3
            elif value == self._pressed:
                _round_rect(self, inner, radius - 1, fill=C.layer_pressed, outline=C.layer_pressed)
            elif value == self._hover:
                _round_rect(self, inner, radius - 1, fill=C.layer_hover, outline=C.layer_hover)
            self.create_text((left + right) / 2, height / 2, text=label, font=face, fill=color)


ButtonFactory = Callable[[tk.Misc, str, Callable[[], None], str], Button]


class Notice(tk.Canvas):
    """Bandeau d'information (InfoBar) : icône de gravité et message sur fond teinté.

    Un bouton d'action facultatif se place sous le message, aligné sur lui.
    """

    _STYLES = {  # noqa: RUF012 - table de constantes
        "critical": (C.critical_bg, C.critical, ICON_ERROR),
        "caution": (C.caution_bg, C.caution, ICON_WARNING),
        "info": (C.layer, C.accent, ICON_INFO),
    }

    def __init__(
        self,
        parent: tk.Misc,
        *,
        width: int,
        pad: int,
        gap: int,
        radius: int,
        font: tkfont.Font,
        icon_font: tkfont.Font,
        make_button: ButtonFactory | None = None,
    ) -> None:
        super().__init__(parent, width=width, height=1, bg=C.bg, highlightthickness=0, bd=0)
        self._width, self._pad, self._gap, self._radius = width, pad, gap, radius
        self._font, self._icon_font = font, icon_font
        self._shown: tuple[str, str, str] | None = None
        self._make_button = make_button
        self._button: Button | None = None
        self._button_key: tuple[str, str] = ("", "")  # libellé, fond
        self._command: Callable[[], None] = _nothing

    def show(
        self, severity: str, message: str, action: tuple[str, Callable[[], None]] | None = None
    ) -> bool:
        """Dessine le bandeau ; vrai si sa hauteur a changé."""
        label = action[0] if action is not None else ""
        if action is not None:
            self._command = action[1]
        if self._shown == (severity, message, label):
            return False
        self._shown = (severity, message, label)
        background, tint, glyph = self._STYLES[severity]
        self.delete("all")
        pad = self._pad
        text_x = pad + self._icon_font.measure(glyph) + self._gap
        text = self.create_text(
            text_x,
            pad,
            text=message,
            font=self._font,
            fill=C.text,
            anchor="nw",
            width=self._width - text_x - pad,
        )
        bottom = self.bbox(text)[3]
        bottom = self._place_button(label, background, text_x, bottom)
        line = self._font.metrics("linespace")
        self.create_text(
            pad, pad + line / 2, text=glyph, font=self._icon_font, fill=tint, anchor="w"
        )
        height = bottom + pad
        shape = _round_rect(
            self, (0, 0, self._width - 1, height - 1), self._radius, fill=background, outline=""
        )
        self.tag_lower(shape)
        changed = int(self.cget("height")) != height
        self.configure(height=height)
        return changed

    def _place_button(self, label: str, background: str, left: int, bottom: int) -> int:
        """Bouton d'action sous le message ; rend le nouveau bas du contenu."""
        if not label or self._make_button is None:
            if self._button is not None:
                self._button.destroy()
                self._button, self._button_key = None, ("", "")
            return bottom
        if self._button is None or self._button_key != (label, background):
            if self._button is not None:
                self._button.destroy()
            self._button = self._make_button(self, label, self._run_command, background)
            self._button_key = (label, background)
        top = bottom + self._gap
        self.create_window(left, top, window=self._button, anchor="nw")
        return top + int(self._button.cget("height"))

    def _run_command(self) -> None:
        self._command()  # le dernier rappel donné à show, pas celui de la création


# --- panneau --------------------------------------------------------------------
class Panel:
    def __init__(
        self,
        root: tk.Tk,
        controller: Controller,
        cfg: PanelConfig,
        *,
        hotkeys: tuple[str, str] = ("F8", "F9"),
        home: tuple[int, int] = (0, 0),
        persist: bool = True,
        win32: Win32 | None = None,
        game: Callable[[], int] | None = None,
        updates: Updates | None = None,
    ) -> None:
        self.root = root
        self.controller = controller
        self.updates = updates
        self._installing = False  # mise à jour prête : le panneau se ferme pour l'installer
        self.persist = persist
        self.hotkeys = hotkeys
        self.win32 = win32 or Win32()
        self._game = game or _no_game  # fenêtre du jeu, 0 si absente
        # fenêtre active avant le panneau, à qui rendre le premier plan s'il le prend
        _, self._other = focus_step(self.win32.foreground(), False, 0)
        self._holding = False  # premier plan gardé faute de pouvoir le rendre (déjà signalé)
        self._through = False  # la souris traverse le panneau (partie en cours)
        self.scale = self.win32.scale()
        self.collapsed = cfg.collapsed
        self.width = self.px(WIDTH)
        self._started = False  # bot déjà lancé une fois : « Reprendre » plutôt que « Lancer »
        self._drag: tuple[int, int] | None = None
        self._last: tuple[object, ...] = ()
        start = tuple(cfg.position) if cfg.position else home
        self._pos = (int(start[0]), int(start[1]))

        root.report_callback_exception = self._report_error  # type: ignore[method-assign]
        root.protocol("WM_DELETE_WINDOW", self._closed_by_system)
        root.withdraw()
        root.overrideredirect(True)
        root.configure(bg=C.bg)
        root.attributes("-topmost", cfg.topmost)
        root.title(TITLE)
        self._fonts()
        self._build()
        self._render(controller.status())
        self._apply_layout()
        root.update_idletasks()
        self.hwnd = self.win32.top_level(root.winfo_id())
        self.win32.style(self.hwnd, exclude_from_capture=cfg.exclude_from_capture)
        root.deiconify()
        self._guard_focus()
        root.after(_POLL_MS, self._poll)

    def px(self, value: float) -> int:
        return round(value * self.scale)

    # --- construction -----------------------------------------------------
    def _fonts(self) -> None:
        families = set(tkfont.families(self.root))

        def pick(*names: str) -> str:
            return next((n for n in names if n in families), "Segoe UI")

        def font(family: str, size: int) -> tkfont.Font:
            return tkfont.Font(self.root, family=family, size=-self.px(size))

        self.f_body = font(pick("Segoe UI Variable Text", "Segoe UI"), 14)
        self.f_strong = font(pick("Segoe UI Variable Text Semibold", "Segoe UI Semibold"), 14)
        self.f_caption = font(pick("Segoe UI Variable Small", "Segoe UI"), 12)
        icons = pick("Segoe Fluent Icons", "Segoe MDL2 Assets")
        self.f_icon = font(icons, 12)
        self.f_dot = font(icons, DOT)

    def _label(self, parent: tk.Misc, font: tkfont.Font, color: str, **kw: object) -> tk.Label:
        return tk.Label(parent, font=font, fg=color, bg=C.bg, bd=0, padx=0, pady=0, **kw)

    def _button(
        self, parent: tk.Misc, text: str, command: Callable[[], None], kind: str, width: int
    ) -> Button:
        font = self.f_icon if kind == "subtle" else self.f_body
        return Button(
            parent,
            text,
            command,
            kind=kind,
            font=font,
            width=width,
            height=self.px(CONTROL_H),
            radius=self.px(RADIUS),
        )

    def _build(self) -> None:
        root, px = self.root, self.px
        inner = self.width - 2 * px(PAD)

        self.header = tk.Frame(root, bg=C.bg, height=px(HEADER_H), width=self.width)
        self.header.pack_propagate(False)
        self.dot = self._label(self.header, self.f_dot, C.neutral, text=ICON_DOT)
        self.dot.pack(side="left", padx=(px(PAD), px(GAP)))
        self.title = self._label(self.header, self.f_strong, C.text)
        self.title.pack(side="left")
        self.detail = self._label(self.header, self.f_caption, C.text_2)
        self.detail.pack(side="left", padx=(px(GAP), 0))
        self.chevron = self._button(
            self.header, ICON_UP, self._toggle_collapsed, "subtle", px(CONTROL_H)
        )
        self.chevron.pack(side="right", padx=(px(GAP), px(PAD)))
        primary_w = max(self.f_body.measure(t) for t in PRIMARY_LABELS) + 2 * px(PAD)
        self.primary = self._button(self.header, "Lancer", self._primary, "accent", primary_w)
        self.primary.pack(side="right")
        for widget in (self.header, self.dot, self.title, self.detail):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)

        self.divider = tk.Frame(root, bg=C.stroke, height=1)
        self.body = tk.Frame(root, bg=C.bg, padx=px(PAD))
        self.notice = Notice(
            self.body,
            width=inner,
            pad=px(PAD),
            gap=px(GAP),
            radius=px(RADIUS),
            font=self.f_body,
            icon_font=self.f_icon,
        )
        self.update_notice = Notice(
            self.body,
            width=inner,
            pad=px(PAD),
            gap=px(GAP),
            radius=px(RADIUS),
            font=self.f_body,
            icon_font=self.f_icon,
            make_button=self._notice_button,
        )

        self.s_dialog = self._section("Vous êtes toujours là\u00a0?")
        self.segmented = Segmented(
            self.s_dialog,
            ANSWERS,
            self._choose,
            width=inner,
            height=px(SEGMENT_H),
            radius=px(RADIUS),
            inset=px(2),
            font=self.f_body,
            strong=self.f_strong,
        )
        self.segmented.pack(anchor="w")
        self.dialog_hint = self._label(
            self.s_dialog, self.f_caption, C.text_3, anchor="w", justify="left", wraplength=inner
        )

        self.s_session = self._section("Session")
        self.grid = tk.Frame(self.s_session, bg=C.bg)
        self.grid.pack(fill="x")
        self.grid.columnconfigure((0, 1), weight=1, uniform="col")
        self.cells: list[tuple[tk.Label, tk.Label]] = []

        self.s_recent = self._section("Derniers clics")
        self.recent: list[tuple[tk.Frame, tk.Label, tk.Label]] = []
        # chiffres proportionnels : la colonne de l'heure a la largeur de la plus large
        clock_w = max(self.f_body.measure(f"{d}{d}:{d}{d}") for d in range(10))
        for _ in range(RECENT_ROWS):
            row = tk.Frame(self.s_recent, bg=C.bg, height=px(ROW_H))
            row.grid_propagate(False)
            row.rowconfigure(0, weight=1)
            row.columnconfigure(0, minsize=clock_w)
            when = self._label(row, self.f_body, C.text_3, anchor="w")
            when.grid(row=0, column=0, sticky="w")
            what = self._label(row, self.f_body, C.text, anchor="w")
            what.grid(row=0, column=1, sticky="w", padx=(px(PAD), 0))
            self.recent.append((row, when, what))
        self.empty = tk.Frame(self.s_recent, bg=C.bg, height=px(ROW_H))
        self.empty.pack_propagate(False)
        empty = self._label(self.empty, self.f_body, C.text_3, text=NO_CLICK)
        empty.pack(side="left")

        self.s_totals = self._section("Toutes les sessions")
        self.totals = tk.Frame(self.s_totals, bg=C.bg, height=px(ROW_H))
        self.totals.pack_propagate(False)
        self.totals_text = self._label(self.totals, self.f_body, C.text_2, anchor="w")
        self.totals_text.pack(side="left")
        self.totals.pack(fill="x")

        self.s_chests = self._section("Coffres ouverts")
        self.chests = tk.Frame(self.s_chests, bg=C.bg, height=px(ROW_H))
        self.chests.pack_propagate(False)
        self.chests_text = self._label(self.chests, self.f_body, C.text_2, anchor="w")
        self.chests_text.pack(side="left")
        self.chests.pack(fill="x")

        self.footer_rule = tk.Frame(root, bg=C.stroke, height=1)
        self.footer = tk.Frame(root, bg=C.bg, padx=px(PAD), pady=px(PAD))
        quit_w = self.f_body.measure("Quitter") + 2 * px(PAD)
        self.quit_button = self._button(self.footer, "Quitter", self._quit, "standard", quit_w)
        self.quit_button.pack(side="left")
        toggle, stop = self.hotkeys
        # les raccourcis ne comptent que jeu au premier plan (voir hotkeys)
        hint = f"En jeu, {toggle}{NBSP}: pause, {stop}{NBSP}: quitter"
        self.hint = self._label(self.footer, self.f_caption, C.text_3, text=hint)
        self.hint.pack(side="right")

    def _notice_button(
        self, parent: tk.Misc, text: str, command: Callable[[], None], bg: str
    ) -> Button:
        width = self.f_body.measure(text) + 2 * self.px(PAD)
        return Button(
            parent,
            text,
            command,
            kind="standard",
            font=self.f_body,
            width=width,
            height=self.px(CONTROL_H),
            radius=self.px(RADIUS),
            bg=bg,
        )

    def _section(self, title: str) -> tk.Frame:
        frame = tk.Frame(self.body, bg=C.bg)
        heading = self._label(frame, self.f_strong, C.text, text=title, anchor="w")
        heading.pack(fill="x", pady=(0, self.px(GAP)))
        return frame

    # --- disposition --------------------------------------------------------
    def _apply_layout(self) -> None:
        for widget in (self.header, self.divider, self.body, self.footer_rule, self.footer):
            widget.pack_forget()
        self.header.pack(fill="x")
        if not self.collapsed:
            self.divider.pack(fill="x")
            self.body.pack(fill="x")
            self.footer_rule.pack(fill="x", pady=(self.px(PAD), 0))
            self.footer.pack(fill="x")
        self.chevron.set_text(ICON_DOWN if self.collapsed else ICON_UP)
        self._place(*self._pos)

    def _layout_body(
        self, notice: tuple[str, str] | None, update: tuple[str, str, str] | None, chests: bool
    ) -> None:
        sections = (self.s_dialog, self.s_session, self.s_recent, self.s_totals)
        for widget in (self.notice, self.update_notice, *sections, self.s_chests):
            widget.pack_forget()
        if chests:  # seulement s'il y a de quoi estimer (paliers relevés, journaux lus)
            sections = (*sections, self.s_chests)
        top = self.px(PAD)
        if notice is not None:
            self.notice.show(*notice)
            self.notice.pack(fill="x", pady=(top, 0))
            top = self.px(SECTION_GAP)
        if update is not None:
            self._show_update(update)
            self.update_notice.pack(fill="x", pady=(top, 0))
            top = self.px(SECTION_GAP)
        for section in sections:
            section.pack(fill="x", pady=(top, 0))
            top = self.px(SECTION_GAP)

    def _place(self, x: int, y: int) -> None:
        """Pose la fenêtre à (x, y), hauteur au contenu, entière sur son écran."""
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        area = self.win32.work_area(x + self.width // 2, y + self.px(HEADER_H) // 2)
        self._pos = clamp(x, y, self.width, height, area)
        self.root.geometry(f"{self.width}x{height}+{self._pos[0]}+{self._pos[1]}")

    # --- actions ------------------------------------------------------------
    def _quit(self) -> None:
        log.info("fermeture : bouton Quitter")
        self.controller.quit()

    def _closed_by_system(self) -> None:
        """WM_CLOSE (Alt+F4, fermeture de session, autre logiciel) : arrêt propre, journalisé."""
        log.warning("fermeture demandée par Windows (WM_CLOSE)")
        self.controller.quit()

    def _report_error(
        self, kind: type[BaseException], value: BaseException, tb: TracebackType | None
    ) -> None:
        """Erreur dans un rappel Tk : sous pythonw, elle n'irait nulle part."""
        log.error("erreur dans le panneau", exc_info=(kind, value, tb))

    def _primary(self) -> None:
        if self.controller.status().phase == "stopped":
            log.info("fermeture : bouton Quitter (moteur arrêté)")
            self.controller.quit()
            return
        self._started = True
        self.controller.toggle()
        self._render(self.controller.status())

    def _toggle_collapsed(self) -> None:
        self.collapsed = not self.collapsed
        self._last = ()
        self._render(self.controller.status())
        self._apply_layout()
        self._save({"panel": {"collapsed": self.collapsed}})

    def _choose(self, answer: str) -> None:
        try:
            self.controller.set_still_here(answer)
        except ValueError as exc:
            log.warning("%s", exc)
            return
        self._render(self.controller.status())
        self._save({"engine": {"still_here": answer}})

    def _save(self, changes: dict[str, object]) -> None:
        if not self.persist:
            return
        try:
            update_local_config(changes)
        except (OSError, ValueError) as exc:
            log.warning("réglage du panneau non enregistré (%s)", exc)

    def _drag_start(self, event: tk.Event) -> None:
        self._drag = (event.x_root - self._pos[0], event.y_root - self._pos[1])

    def _drag_move(self, event: tk.Event) -> None:
        if self._drag is not None:
            self._pos = (event.x_root - self._drag[0], event.y_root - self._drag[1])
            self.root.geometry(f"+{self._pos[0]}+{self._pos[1]}")

    def _drag_end(self, _event: tk.Event) -> None:
        if self._drag is None:
            return
        self._drag = None
        self._place(*self._pos)
        self._save({"panel": {"position": list(self._pos)}})

    # --- rendu --------------------------------------------------------------
    def _poll(self) -> None:
        if self.controller.closing:
            self.root.quit()
            return
        try:
            self._guard_focus()
            self._guard_mouse()
            self.controller.check_stall()
            status = self.controller.status()
            self._render(status)
            self._watch_update(status)
        except Exception:  # un rendu raté ne doit pas figer le panneau pour de bon
            log.exception("erreur pendant la mise à jour du panneau")
        finally:
            self.root.after(_POLL_MS, self._poll)

    def _guard_focus(self) -> None:
        """Le panneau ne garde jamais le premier plan : rendu à la fenêtre d'avant, ou au jeu."""
        foreground = self.win32.foreground()
        ours = bool(foreground) and self.win32.ours(foreground)
        _, self._other = focus_step(foreground, ours, self._other)
        if not ours:
            self._holding = False
            return
        for target in give_back_targets(self._other, self._game()):
            if self.win32.give_back(target):
                who = "au jeu" if target != self._other else "à la fenêtre précédente"
                log.info("le panneau avait pris le premier plan : rendu %s", who)
                self._other = target
                self._holding = False
                return
        if not self._holding:  # une fois par épisode, pas une ligne toutes les 250 ms
            log.warning("le panneau a le premier plan et ne peut pas le rendre")
            self._holding = True

    def _guard_mouse(self) -> None:
        """En pleine partie, la souris traverse le panneau (voir :func:`mouse_through`)."""
        if self._drag is not None:  # déplacement en cours : la souris est au panneau
            return
        game = self._game()
        through = False
        if game:
            foreground = self.win32.foreground()
            front = bool(foreground) and self.win32.pid(foreground) == self.win32.pid(game)
            x, y, hidden, clipped = self.win32.cursor_state()
            left, top, right, bottom = self.win32.window_rect(self.hwnd)
            on_panel = left <= x < right and top <= y < bottom
            through = mouse_through(front, hidden, clipped, on_panel, self._through)
        if through != self._through:
            self.win32.set_mouse_through(self.hwnd, through)
            self._through = through
            log.debug("panneau %s", "traversé par la souris" if through else "de nouveau cliquable")

    def _render(self, status: Status) -> None:
        look = LOOKS[status.phase]
        if self.dot.cget("fg") != look.dot:
            self.dot.configure(fg=look.dot)
        if status.phase not in ("paused", "stopped"):
            self._started = True
        self._text(self.title, look.title)
        self._text(self.detail, header_detail(status))
        self.primary.set_text(primary_label(status.phase, self._started))
        if self.collapsed:
            return

        notice = explain(status)
        update = update_notice(self.updates.state()) if self.updates is not None else None
        # gravité et bouton du bandeau de mise à jour : sa hauteur (le texte suit le téléchargement)
        update_key = None if update is None else (update[0], update[2])
        counts = [
            (name, number(sum(status.per_state.get(slug, 0) for slug in slugs)))
            for name, slugs in self.controller.counters()
        ]
        counts.append(("Durée", duration(time.time() - status.started_at)))
        sessions, clicks, seconds = self.controller.totals(status)
        totals = f"{plural(sessions, 'session')}, {plural(clicks, 'clic')}, {duration(seconds)}"
        recent = tuple(
            (time.strftime("%H:%M", time.localtime(c.ts)), c.label)
            for c in status.recent[:RECENT_ROWS]
        )
        disabled = frozenset(a for a, _ in ANSWERS) - set(self.controller.still_here_answers())
        hint = missing_hint(disabled)
        chests = chests_line(self.controller.chests())
        state = (
            notice,
            update_key,
            len(recent),
            hint,
            bool(chests),
            tuple(counts),
            recent,
            totals,
            chests,
            status.still_here,
            update,
        )
        if state == self._last:
            return
        reflow = not self._last or self._last[:5] != state[:5]  # hauteur changée
        self._last = state

        self.segmented.set(status.still_here, disabled)
        self._fill_grid(counts)
        self._fill_recent(recent)
        self._text(self.totals_text, totals)
        self._text(self.chests_text, chests)
        if reflow:
            self._text(self.dialog_hint, hint)
            if hint:
                self.dialog_hint.pack(fill="x", pady=(self.px(GAP), 0))
            else:
                self.dialog_hint.pack_forget()
            self._layout_body(notice, update, bool(chests))
            self._place(*self._pos)
        elif update is not None and self._show_update(update):  # message passé à la ligne
            self._place(*self._pos)

    def _show_update(self, update: tuple[str, str, str]) -> bool:
        severity, message, label = update
        action = (label, self._update_action) if label else None
        return self.update_notice.show(severity, message, action)

    def _update_action(self) -> None:
        """« Mettre à jour » ou « Réessayer » : téléchargement en arrière-plan, bot en marche."""
        if self.updates is None:
            return
        log.info("mise à jour : téléchargement demandé")
        self.updates.download()
        self._render(self.controller.status())

    def _watch_update(self, status: Status) -> None:
        """Version prête : le panneau se ferme, l'installe et relance la nouvelle (voir cli)."""
        if self.updates is None or self._installing or self.updates.state().phase != "ready":
            return
        self._installing = True
        log.info("mise à jour prête : fermeture du panneau pour l'installer")
        self.updates.request_install(resume=status.phase in RUNNING)
        self.controller.quit()

    @staticmethod
    def _text(label: tk.Label, text: str) -> None:
        if label.cget("text") != text:
            label.configure(text=text)

    def _fill_grid(self, items: list[tuple[str, str]]) -> None:
        px = self.px
        while len(self.cells) < len(items):
            index = len(self.cells)
            cell = tk.Frame(self.grid, bg=C.bg, height=px(ROW_H))
            cell.pack_propagate(False)
            name = self._label(cell, self.f_body, C.text_2, anchor="w")
            name.pack(side="left")
            value = self._label(cell, self.f_body, C.text, anchor="e")
            value.pack(side="right")
            column = index % 2
            half = px(PAD)  # gouttière de 24 px : une valeur ne colle pas au libellé voisin
            cell.grid(
                row=index // 2,
                column=column,
                sticky="ew",
                padx=(0, half) if column == 0 else (half, 0),
            )
            self.cells.append((name, value))
        for (name, value), (text, shown) in zip(self.cells, items, strict=False):
            self._text(name, text)
            self._text(value, shown)

    def _fill_recent(self, rows: tuple[tuple[str, str], ...]) -> None:
        for index, (row, when, what) in enumerate(self.recent):
            if index < len(rows):
                self._text(when, rows[index][0])
                self._text(what, rows[index][1])
                if not row.winfo_manager():
                    row.pack(fill="x")
            elif row.winfo_manager():
                row.pack_forget()
        if rows and self.empty.winfo_manager():
            self.empty.pack_forget()
        elif not rows and not self.empty.winfo_manager():
            self.empty.pack(fill="x")


# --- lancement ------------------------------------------------------------------
def run_panel(
    cfg: Config,
    library: TemplateLibrary,
    *,
    dry_run: bool = False,
    updates: Updates | None = None,
) -> int:
    """Ouvre le panneau, lance le moteur dans son thread, attend la fermeture.

    Lève ``RuntimeError`` si un autre panneau reste ouvert. Une mise à jour
    demandée depuis le panneau s'installe après le retour (voir ``cli``).
    """
    win32 = Win32()
    try:  # verrou rendu aussi en cas d'échec : le message d'erreur peut rester ouvert
        # avant de lire l'historique : l'ancien panneau y écrit sa session en se fermant
        replace_other_panels(win32)
        return _open_panel(cfg, library, win32, dry_run=dry_run, updates=updates)
    finally:
        win32.release_lock()


def _open_panel(
    cfg: Config,
    library: TemplateLibrary,
    win32: Win32,
    *,
    dry_run: bool,
    updates: Updates | None,
) -> int:
    from .cli import game_in_front  # noqa: PLC0415 - le panneau est lancé par la CLI
    from .hotkeys import HotkeyListener  # noqa: PLC0415 - inutile hors panneau

    window = GameWindow(cfg.window) if cfg.window.enabled else None
    actions = Actions(cfg.actions, dry_run=dry_run)
    engine = Engine(cfg, _NotOpenYet(), actions, library, dry_run=dry_run, window=window)
    controller = EngineController(engine, cfg, window)
    region = window.region() if window is not None else None
    home = default_home(region or win32.work_area(0, 0), win32.scale())

    root = tk.Tk()
    Panel(
        root,
        controller,
        cfg.panel,
        hotkeys=(cfg.hotkeys.toggle, cfg.hotkeys.quit),
        home=home,
        win32=win32,
        game=window.hwnd if window is not None else None,
        updates=updates,
    )
    if updates is not None:
        updates.start()
    listener: HotkeyListener | None = None
    if cfg.hotkeys.enabled:
        listener = HotkeyListener(active=game_in_front(window))
        listener.bind(cfg.hotkeys.toggle, engine.toggle)
        listener.bind(cfg.hotkeys.quit, lambda: _hotkey_quit(controller))
        listener.start()
    controller.start()
    try:
        root.mainloop()
        log.info("panneau fermé")
    finally:
        controller.quit()
        if listener is not None:
            listener.stop()
        if not controller.join(timeout=_JOIN_S):
            diagnostics.dump_threads(f"le moteur ne s'est pas arrêté en {_JOIN_S:.0f}{NBSP}s")
            if updates is not None and updates.install_requested:
                # il lirait encore ses modules dans _internal pendant qu'on le remplace
                log.warning("mise à jour reportée : le moteur tourne encore")
                updates.install_requested = False
        with contextlib.suppress(tk.TclError):  # déjà détruite
            root.destroy()
    return 0


def _no_game() -> int:
    return 0


def _nothing() -> None:
    return None


class _PanelFinder(Protocol):
    def other_panels(self) -> list[tuple[int, int]]: ...

    def close_and_wait(self, hwnd: int, pid: int, timeout_s: float) -> bool: ...

    def take_lock(self, wait_s: float) -> bool: ...


def replace_other_panels(
    win32: _PanelFinder,
    timeout_s: float = _REPLACE_S,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Ferme les panneaux déjà ouverts : deux bots cliqueraient chacun de leur côté.

    Rend la main avec le verrou des panneaux, que l'ancien ne lâche qu'une fois
    son moteur arrêté et sa session enregistrée. Un panneau encore en train de
    démarrer (verrou pris, fenêtre pas encore créée) est fermé dès qu'elle
    paraît. Lève ``RuntimeError`` si l'un d'eux ne se ferme pas à temps.
    """
    deadline = clock() + timeout_s
    asked: set[int] = set()
    while True:
        for hwnd, pid in win32.other_panels():
            if hwnd in asked:
                continue
            asked.add(hwnd)
            if win32.close_and_wait(hwnd, pid, max(0.0, deadline - clock())):
                log.info("panneau déjà ouvert (pid %d) : fermé pour laisser place au nouveau", pid)
        left = win32.other_panels()
        if win32.take_lock(0.0 if left else _LOCK_POLL_S) and not left:
            return
        if clock() >= deadline:
            raise RuntimeError(_still_open(left))
        if left:
            sleep(_LOCK_POLL_S)


def _still_open(left: list[tuple[int, int]]) -> str:
    if not left:
        return "un autre panneau est déjà en train de démarrer"
    pids = ", ".join(str(pid) for _, pid in left)
    return (
        f"un autre panneau reste ouvert (pid {pids}){NBSP}; "
        "fermez-le avec son bouton Quitter, puis relancez"
    )


def _hotkey_quit(controller: EngineController) -> None:
    log.info("fermeture : raccourci clavier")
    controller.quit()
