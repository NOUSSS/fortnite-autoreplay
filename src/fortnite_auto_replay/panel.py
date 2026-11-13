"""Panneau de contrôle du bot : relance (activer, désactiver), coffres, réglages.

Une petite fenêtre sans bordure, toujours au-dessus du jeu :

- au repos, une pastille : l'état de la relance et le total des coffres. Le
  curseur posé dessus ouvre la carte entière, qui se referme quand il s'en va ;
  sans la souris, la fenêtre prend l'opacité choisie dans les réglages ;

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
- une ligne par fonction, chacune avec son état : la relance (bouton Activer
  ou Désactiver) et les coffres, comptés même relance désactivée.

Le moteur tourne dans un thread à part ; seul le thread principal touche à Qt.
Toute fermeture est journalisée avec sa cause ; un moteur qui ne répond plus
voit la pile de ses threads écrite dans ``logs/crash.log``.
"""

from __future__ import annotations

import ctypes
import dataclasses
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QCloseEvent,
    QFont,
    QFontMetricsF,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

from . import diagnostics
from .actions import Actions
from .capture import CaptureError, Frame, ScreenCapture
from .chests import ChestTracker, Reader, Summary
from .config import Config, PanelConfig, update_local_config
from .engine import Engine, Phase, Status
from .panel_glass import GlassFeed, GlassWorker
from .panel_style import (
    ACTION_MIN_W,
    BUTTON_PAD,
    CARD_RADIUS,
    CLOCK_W,
    CLOSE_MS,
    CONTROL_H,
    DATE_W,
    DOT,
    FADE_MS,
    FEATURE_H,
    FOOTER_TOP,
    GAP,
    GLASS_MS,
    GLASS_TINT,
    HEADING_GAP,
    ICON,
    JOURNAL_GAP,
    LINE,
    LINE_SMALL,
    NOTICE_ICON,
    NOTICE_PAD_Y,
    NOTICE_RADIUS,
    OPEN_MS,
    OPEN_SCALE,
    PAD,
    PILL_H,
    POPOVER_GAP,
    POPOVER_W,
    RECENT_ROWS,
    ROW_H,
    SECTION_GAP,
    SEGMENT_INSET,
    SETTING_GAP,
    SLIDER_H,
    SUBTLE_PAD,
    THUMB,
    TRACK_H,
    WIDTH,
    C,
    draw_chest,
    draw_sliders,
    fill_round,
    make_fonts,
    qcolor,
    tabular,
)
from .panel_widgets import (
    Button,
    Dot,
    Icon,
    IconPainter,
    Notice,
    Rule,
    Segmented,
    Slider,
    Text,
    Wrapped,
)
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


# opacité au repos (pastille ou carte sans la souris), en pour cent
OPACITY_MIN, OPACITY_MAX, OPACITY_STEP = 20, 100, 5
_HOVER_MS = 50  # suivi du curseur : ouverture au survol
LEAVE_S = 0.4  # la carte se referme après ce délai hors du panneau
PREVIEW_S = 0.7  # aperçu de l'opacité après le dernier mouvement du curseur


@dataclass(frozen=True, slots=True)
class Look:
    short: str  # dans la pastille du repos
    dot: str


LOOKS: dict[Phase, Look] = {
    "active": Look("Relance active", C.success),
    "paused": Look("Relance désactivée", C.neutral),
    "stuck": Look("Relance bloquée", C.critical),
    "errors": Look("Relance bloquée", C.critical),
    "no_game": Look("En attente du jeu", C.caution),
    "hidden": Look("En attente du jeu", C.caution),
    "background": Look("En attente du jeu", C.caution),
    "capture": Look("Capture impossible", C.critical),
    "covered": Look("Clic annulé", C.caution),
    "mouse": Look("Clic en attente", C.caution),
    "stopped": Look("Relance arrêtée", C.neutral),
}

ANSWERS = (("ready", "Prêt"), ("not_yet", "Pas encore"), ("ignore", "Ignorer"))
REST_MODES = (("pill", "Pastille"), ("panel", "Panneau"))
ACTIVATE, DEACTIVATE = "Activer", "Désactiver"
NO_CLICK = "Aucun clic pour l\u2019instant."


# --- textes -------------------------------------------------------------------
def number(value: int) -> str:
    """Séparateur de milliers à la française (espace insécable)."""
    return f"{value:,}".replace(",", NBSP)


def plural(count: int, word: str) -> str:
    """« 0 clic », « 1 clic », « 2 clics » : le pluriel français commence à 2."""
    return f"{number(count)}{NBSP}{word}{'s' if count > 1 else ''}"


def explain(status: Status) -> tuple[str, str] | None:  # noqa: PLR0911 - une branche par phase
    """Bandeau sous la relance : (gravité, message), ou rien quand tout va bien."""
    phase, detail = status.phase, status.detail
    if phase in ("stuck", "errors"):
        return "critical", f"{detail}. La relance s\u2019est désactivée."
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
# phases d'un moteur qui tourne (relance active) : relancé ainsi après une mise à jour
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


def action_label(phase: Phase) -> str:
    """Le bouton de la relance dit ce qu'il va faire : l'activer ou la désactiver."""
    return ACTIVATE if phase in ("paused", "stuck", "errors", "stopped") else DEACTIVATE


def shortcuts_hint(toggle: str, stop: str) -> str:
    """Rappel des raccourcis, qui ne comptent que jeu au premier plan (voir hotkeys)."""
    return f"En jeu, {toggle}{NBSP}: activer ou désactiver, {stop}{NBSP}: quitter"


def snap_opacity(value: float) -> int:
    """Opacité au repos ramenée à la grille du curseur (20 à 100 %, de 5 en 5)."""
    snapped = round(value / OPACITY_STEP) * OPACITY_STEP
    return int(min(OPACITY_MAX, max(OPACITY_MIN, snapped)))


def hover_step(
    inside: bool, expanded: bool, left_at: float | None, now: float, delay: float = LEAVE_S
) -> tuple[bool, float | None]:
    """Carte ouverte au survol : (ouverte, moment où le curseur est sorti).

    Elle s'ouvre dès que le curseur entre et ne se referme qu'après ``delay``
    hors du panneau : traverser l'espace entre la carte et ses réglages, ou
    déborder d'un pixel, ne la fait pas clignoter.
    """
    if inside:
        return True, None
    if not expanded:
        return False, None
    if left_at is None:
        return True, now
    if now - left_at >= delay:
        return False, None
    return True, left_at


REPLAYS_OFF = (
    "Pour compter vos coffres, activez l\u2019enregistrement des rediffusions dans Fortnite"
    f"{NBSP}: Paramètres, Jeu, Rediffusions."
)
READER_DOWN = "Le lecteur de replays ne démarre pas, vos coffres ne sont pas comptés."
READ_FAILED = "Le replay de votre dernière partie n\u2019a pas pu être lu."
READING_REPLAYS = "Lecture des replays…"
COUNTING = "Comptage de votre dernière partie…"
INTERRUPTED = f"Le jeu s\u2019est fermé pendant la partie{NBSP}: ses coffres ne sont pas comptés."

SHORT_MONTHS = (
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
)


def match_time(when: datetime) -> str:
    """« 13 nov. 18:12 », à l'heure locale : le début d'une partie du journal des coffres."""
    local = when.astimezone()
    return f"{local.day}{NBSP}{SHORT_MONTHS[local.month - 1]} {local:%H:%M}"


@dataclass(frozen=True, slots=True)
class ChestsState:
    """Ce que le panneau sait des coffres : résumé du compteur, lecteur indisponible."""

    summary: Summary | None  # None : premier tour pas encore fini
    unavailable: str = ""  # lecteur de replays absent ou en échec


@dataclass(frozen=True, slots=True)
class ChestsView:
    """Ligne « Coffres » : pastille, état, total ; dessous, bandeau et dernières parties."""

    dot: str  # couleur de la pastille
    state: str = ""  # sous « Coffres » : ce qui est en cours, rien sinon
    value: str = ""  # total à droite, vide tant qu'il n'y en a pas
    notice: tuple[str, str] | None = None  # gravité, message
    games: tuple[tuple[str, str], ...] = ()  # dernières parties : (début, coffres)


def chests_view(state: ChestsState, *, game_open: bool = True) -> ChestsView:
    """Ce que montre la ligne « Coffres », indépendante de la relance (désactivée comprise).

    Une ligne d'état seulement pour ce qui passe vite : premier tour des
    replays, comptage de la partie qui vient de finir (pas la partie en cours,
    qui durerait toute la partie). Ce qui
    empêche de compter (partie interrompue, jeu fermé : son replay reste « en
    direct » ; replay illisible ; lecteur absent) a son bandeau.
    """
    if state.unavailable:
        return ChestsView(C.caution, notice=("caution", READER_DOWN))
    summary = state.summary
    if summary is None:
        return ChestsView(C.neutral, READING_REPLAYS)
    if not summary.replays:
        return ChestsView(C.caution, notice=("caution", REPLAYS_OFF))
    interrupted = summary.interrupted or (summary.recording and not game_open)
    dot, notice = C.success, None
    if interrupted:
        dot, notice = C.caution, ("caution", INTERRUPTED)
    elif summary.failed:
        dot, notice = C.caution, ("caution", READ_FAILED)
    shown = COUNTING if summary.counting else ""
    if summary.since is None or summary.last is None:
        return ChestsView(dot, shown, value="0", notice=notice)
    games = tuple((match_time(when), plural(count, "coffre")) for when, count in summary.recent)
    return ChestsView(dot, shown, value=number(summary.total), notice=notice, games=games)


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

    Une fenêtre peut prendre le premier plan malgré ``WS_EX_NOACTIVATE`` (création,
    clic d'un pilote) : le panneau le rend aussitôt à la fenêtre d'avant (le jeu,
    en général).
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

    def chests(self) -> ChestsState | None:
        """Coffres ouverts comptés ; None si le compteur est désactivé."""
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
        self._chests = (
            ChestTracker(cfg.chests, cfg.resolved_chests_file(), Reader(cfg.resolved_reader()))
            if cfg.chests.enabled
            else None
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

    def chests(self) -> ChestsState | None:
        if self._chests is None:
            return None
        return ChestsState(self._chests.summary(), self._chests.unavailable)


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
# hors de _PANEL_TITLES : un nouveau panneau ne doit pas la prendre pour un autre panneau
SETTINGS_TITLE = f"{TITLE} - réglages"
_TK_CLASS = "TkTopLevel"  # panneaux des versions Tk (0.5 et avant)
_WDA_EXCLUDEFROMCAPTURE = 0x11
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


_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _panel_class(name: str) -> bool:
    """Classe d'une fenêtre de panneau : Qt (« Qt6120QWindowTool… ») ou Tk (anciennes versions)."""
    return name == _TK_CLASS or (name.startswith("Qt") and "QWindow" in name)


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
        """La fenêtre appartient-elle à ce processus (panneau, réglages) ?"""
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
            if self.user32.GetClassNameW(hwnd, name, 64) and _panel_class(name.value):
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
        """Jamais activée, hors barre des tâches, invisible aux captures."""
        extended = self.ex_style(hwnd) | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
        self.user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, extended)
        if exclude_from_capture and not self.user32.SetWindowDisplayAffinity(
            hwnd, _WDA_EXCLUDEFROMCAPTURE
        ):
            log.warning("le panneau reste visible des captures (Windows 10 2004 ou plus requis)")

    def work_area(self, x: int, y: int) -> Area:
        """Zone de travail (sans barre des tâches) de l'écran le plus proche du point."""
        monitor = self.user32.MonitorFromPoint(wintypes.POINT(x, y), _MONITOR_DEFAULTTONEAREST)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not monitor or not self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return x, y, x + 1920, y + 1080
        work = info.rcWork
        return work.left, work.top, work.right, work.bottom


# --- fenêtres -------------------------------------------------------------------------
class Surface(QWidget):
    """Fond du panneau : encre, coins arrondis, filet d'un pixel à l'intérieur.

    En verre, le jeu flouté (``backdrop``, petite image agrandie en douceur)
    passe sous une encre translucide ; sans lui, l'encre est pleine.
    """

    def __init__(self, radius: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._radius = radius
        self.backdrop: QImage | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self.backdrop is None:
            fill_round(painter, rect, self._radius, C.bg, C.stroke)
            return
        radius = min(self._radius, rect.height() / 2, rect.width() / 2)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(path)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(rect, self.backdrop)
        painter.restore()
        fill_round(painter, rect, self._radius, qcolor(C.bg, GLASS_TINT), C.stroke)


class Ghost(QWidget):
    """Image de la carte (ou des réglages) qui s'ouvre ou se ferme : fondu et échelle.

    Qt ne met pas à l'échelle des widgets vivants : l'animation joue sur une
    capture, puis rend la place aux vrais widgets.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._pixmap = QPixmap()
        self._origin = QPointF()
        self._progress = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._step)
        self._done: Callable[[], None] = _nothing
        self._anim.finished.connect(self._finish)
        self.hide()

    @property
    def running(self) -> bool:
        return self._anim.state() == QAbstractAnimation.State.Running

    def play(
        self,
        pixmap: QPixmap,
        origin: QPointF,
        *,
        opening: bool,
        ms: int,
        done: Callable[[], None],
    ) -> None:
        self._anim.stop()
        self._pixmap, self._origin, self._done = pixmap, origin, done
        self.setGeometry(0, 0, pixmap.width(), pixmap.height())
        start = self._progress if self.isVisible() else (0.0 if opening else 1.0)
        self._anim.setStartValue(start)
        self._anim.setEndValue(1.0 if opening else 0.0)
        curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
        if opening:  # cubic-bezier(.23, 1, .32, 1) : rapide puis doux
            curve.addCubicBezierSegment(QPointF(0.23, 1.0), QPointF(0.32, 1.0), QPointF(1, 1))
        else:  # ease-out
            curve.addCubicBezierSegment(QPointF(0.0, 0.0), QPointF(0.58, 1.0), QPointF(1, 1))
        self._anim.setEasingCurve(curve)
        self._anim.setDuration(ms)
        self._progress = start
        self.show()
        self.raise_()
        self._anim.start()

    def stop(self) -> None:
        if self.running:
            self._anim.stop()
        self.hide()

    def _step(self, value: float) -> None:
        self._progress = float(value)
        self.update()

    def _finish(self) -> None:
        self.hide()
        done, self._done = self._done, _nothing
        done()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(self._progress)
        scale = OPEN_SCALE + (1 - OPEN_SCALE) * self._progress
        painter.translate(self._origin)
        painter.scale(scale, scale)
        painter.translate(-self._origin)
        painter.drawPixmap(0, 0, self._pixmap)


class PanelWindow(QWidget):
    """Fenêtre sans bordure ni fond, au-dessus du jeu, jamais activée par un clic."""

    def __init__(self, title: str, *, topmost: bool, on_close: Callable[[], None]) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        if topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle(title)
        self._on_close = on_close

    def closeEvent(self, event: QCloseEvent) -> None:
        """WM_CLOSE (Alt+F4, fermeture de session, autre panneau) : arrêt propre, journalisé."""
        event.ignore()
        self._on_close()


Journal = list[tuple[QWidget, Text, Text]]


class Panel:
    """Pastille au repos, carte complète au survol, réglages dans un popover à côté.

    Au repos (« Pastille ») ne reste que l'état de la relance et le total des
    coffres ; le curseur posé dessus ouvre la carte, qui se referme 0,4 s après
    son départ. Réglé sur « Panneau », la carte reste ouverte. Sans la souris,
    la fenêtre prend l'opacité au repos choisie ; au survol, elle redevient opaque.
    """

    def __init__(
        self,
        controller: Controller,
        cfg: PanelConfig,
        *,
        hotkeys: tuple[str, str] = ("F8", "F9"),
        home: tuple[int, int] = (0, 0),
        persist: bool = True,
        win32: Win32 | None = None,
        game: Callable[[], int] | None = None,
        updates: Updates | None = None,
        follow_cursor: bool = True,  # faux pour les captures : la scène dit ce qui est ouvert
        show: bool = True,  # faux pour les captures : rien n'est affiché à l'écran
    ) -> None:
        self.controller = controller
        self.updates = updates
        self._installing = False  # mise à jour prête : le panneau se ferme pour l'installer
        self.persist = persist
        self.hotkeys = hotkeys
        self.win32 = win32 or Win32()
        self._game = game or _no_game  # fenêtre du jeu, 0 si absente
        self._game_known = game is not None  # sans suivi de fenêtre, le jeu est supposé ouvert
        # fenêtre active avant le panneau, à qui rendre le premier plan s'il le prend
        _, self._other = focus_step(self.win32.foreground(), False, 0)
        self._holding = False  # premier plan gardé faute de pouvoir le rendre (déjà signalé)
        self._through = False  # la souris traverse le panneau (partie en cours)
        self.scale = self.win32.scale()
        self.rest = cfg.rest  # "pill" ou "panel" : ce qui reste affiché sans la souris
        self.rest_opacity = snap_opacity(cfg.rest_opacity)
        self._glass = GlassFeed()  # vue qui s'ouvre : tout de suite, dans ce thread
        self._glass_worker: GlassWorker | None = None  # rafraîchissement continu
        self.follow_cursor = follow_cursor
        self.animate = show  # pas d'animation pour les captures : l'état final directement
        self.expanded = False  # curseur sur le panneau (ou sorti depuis moins de LEAVE_S)
        self._left_at: float | None = None
        self.settings_open = False
        self._preview_until = 0.0  # opacité au repos montrée pendant son réglage
        self._opacity_target = -1.0
        self._drag: QPoint | None = None
        self._card_view = False  # carte affichée (sinon la pastille)
        start = tuple(cfg.position) if cfg.position else home
        self._pos = (int(start[0]), int(start[1]))  # coin haut gauche voulu
        self._at = self._pos  # coin réellement affiché (gardé entier sur l'écran)
        self.fonts = make_fonts(self.scale)

        self.window = PanelWindow(TITLE, topmost=cfg.topmost, on_close=self._closed_by_system)
        self.settings = PanelWindow(
            SETTINGS_TITLE, topmost=cfg.topmost, on_close=self._closed_by_system
        )
        self._fade = QVariantAnimation(self.window)
        self._fade.setDuration(FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(lambda v: self.window.setWindowOpacity(float(v)))
        self._dragger = _Dragger(self)
        self._build()
        self._build_settings()
        self._render(controller.status())
        self.hwnd = int(self.window.winId())
        self.settings_hwnd = int(self.settings.winId())
        if show:
            self.win32.style(self.hwnd, exclude_from_capture=cfg.exclude_from_capture)
            self.win32.style(self.settings_hwnd, exclude_from_capture=cfg.exclude_from_capture)
        self._apply_alpha(time.monotonic())
        self._timers: list[QTimer] = []
        if show:
            self.window.show()
            self._guard_focus()
            self._every(_POLL_MS, self._poll)
            if follow_cursor:
                self._every(_HOVER_MS, self._follow)
            self._glass_worker = GlassWorker()
            self._glass_worker.ready.connect(self._glass_ready)
            self._every(GLASS_MS, self._glass_tick)

    def px(self, value: float) -> int:
        return round(value * self.scale)

    def _every(self, ms: int, callback: Callable[[], None]) -> None:
        timer = QTimer(self.window)
        timer.setInterval(ms)
        timer.timeout.connect(callback)
        timer.start()
        self._timers.append(timer)

    # --- construction -----------------------------------------------------
    def _text(self, font: QFont, color: str, text: str = "", **kw: object) -> Text:
        line = self.px(LINE_SMALL) if font.pixelSize() <= self.px(12) else self.px(LINE)
        return Text(font, color, line, text, **kw)  # type: ignore[arg-type]

    def _button(
        self,
        text: str,
        command: Callable[[], None],
        kind: str,
        *,
        strong: bool = False,
        min_width: int = 0,
        icon: IconPainter | None = None,
    ) -> Button:
        px = self.px
        if icon is not None:
            pad = (px(SUBTLE_PAD[0]), px(SUBTLE_PAD[1]))
        else:
            pad = (px(BUTTON_PAD), px(BUTTON_PAD))
        return Button(
            text,
            command,
            kind=kind,
            font=self.fonts.strong if strong else self.fonts.body,
            height=px(CONTROL_H),
            pad=pad,
            min_width=min_width,
            icon=(icon, px(ICON), px(GAP)) if icon is not None else None,
        )

    def _box(
        self, parent: QWidget, margins: tuple[int, int, int, int], spacing: int = 0
    ) -> QVBoxLayout:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(*(self.px(m) for m in margins))
        layout.setSpacing(self.px(spacing))
        return layout

    def _build(self) -> None:
        px, fonts = self.px, self.fonts

        # au repos : pastille de l'état de la relance et total des coffres
        self.pill = Surface(px(PILL_H) / 2, self.window)
        self.pill.setFixedHeight(px(PILL_H))
        row = QHBoxLayout(self.pill)
        row.setContentsMargins(px(PAD), 0, px(PAD), 0)
        row.setSpacing(px(GAP))
        self.pill_dot = Dot(px(DOT), C.neutral)
        self.pill_text = self._text(fonts.body, C.text)
        row.addWidget(self.pill_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.pill_text, 0, Qt.AlignmentFlag.AlignVCenter)
        self.pill_chests = QWidget()
        self.pill_chests.setFixedHeight(px(PILL_H))  # icône et total centrés sur la pastille
        group = QHBoxLayout(self.pill_chests)
        group.setContentsMargins(0, 0, 0, 0)
        group.setSpacing(0)
        self.pill_sep = Rule(vertical=px(12))
        self.pill_icon = Icon(draw_chest, px(ICON), C.text_2)
        self.pill_total = self._text(fonts.figures, C.text)
        group.addWidget(self.pill_sep, 0, Qt.AlignmentFlag.AlignVCenter)
        group.addSpacing(px(GAP))
        group.addWidget(self.pill_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        group.addSpacing(px(HEADING_GAP))  # total collé à l'icône (4 px)
        group.addWidget(self.pill_total, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.pill_chests, 0, Qt.AlignmentFlag.AlignVCenter)
        self._draggable(self.pill)

        # au survol : la carte entière
        self.card = Surface(px(CARD_RADIUS), self.window)
        column = self._box(self.card, (0, 0, 0, 0))
        inner = px(WIDTH) - 2 * px(PAD)

        action_w = max(
            px(ACTION_MIN_W),
            *(
                round(QFontMetricsF(fonts.strong).horizontalAdvance(t)) + 2 * px(BUTTON_PAD)
                for t in (ACTIVATE, DEACTIVATE)
            ),
        )
        self.action = self._button(
            DEACTIVATE, self._toggle, "standard", strong=True, min_width=action_w
        )
        # l'état de la relance se lit à la pastille et au bouton : pas de ligne dessous
        self.header, self.dot, self.title, _ = self._feature_row("Relance auto", side=self.action)
        column.addWidget(self.header)

        self.bot_body = QWidget()
        body = self._box(self.bot_body, (PAD, 0, PAD, PAD), SECTION_GAP)
        self.notice = self._notice(inner)
        body.addWidget(self.notice)
        self.s_recent, recent = self._section("Derniers clics")
        self.recent = self._journal(recent, px(CLOCK_W))
        self.empty = self._text(fonts.body, C.text_3, NO_CLICK)
        self.empty.setFixedHeight(px(ROW_H))
        recent.addWidget(self.empty)
        body.addWidget(self.s_recent)
        column.addWidget(self.bot_body)

        # coffres : comptés à chaque fin de partie, que la relance soit active ou non
        self.chests_rule = self._rule()
        column.addWidget(self.chests_rule)
        self.chests_block = QWidget()
        block = self._box(self.chests_block, (0, 0, 0, 0))
        # le total sur la ligne du nom, aligné sur le bord droit du bouton de la relance
        self.chests_value = self._text(fonts.title, C.text, align=Qt.AlignmentFlag.AlignRight)
        self.chests_row, self.chests_dot, _, self.chests_state = self._feature_row(
            "Coffres", value=self.chests_value
        )
        block.addWidget(self.chests_row)
        self.chests_body = QWidget()
        chests = self._box(self.chests_body, (PAD, 0, PAD, PAD), SECTION_GAP)
        self.chests_notice = self._notice(inner)
        chests.addWidget(self.chests_notice)
        self.s_games, games = self._section("Dernières parties")
        self.games = self._journal(games, px(DATE_W))
        chests.addWidget(self.s_games)
        block.addWidget(self.chests_body)
        column.addWidget(self.chests_block)

        self.footer_rule = self._rule()
        column.addWidget(self.footer_rule)
        self.update_box = QWidget()
        update = self._box(self.update_box, (PAD, PAD, PAD, 0))
        self.update_notice = self._notice(inner, make_button=self._notice_button)
        update.addWidget(self.update_notice)
        column.addWidget(self.update_box)
        self.footer = QWidget()
        foot = QHBoxLayout(self.footer)
        foot.setContentsMargins(px(PAD), px(FOOTER_TOP), px(PAD), px(PAD))
        foot.setSpacing(0)
        self.settings_button = self._button(
            "Réglages", self._toggle_settings, "subtle", icon=self._sliders_icon
        )
        self.quit_button = self._button("Quitter", self._quit, "standard")
        foot.addWidget(self.settings_button)
        foot.addStretch(1)
        foot.addWidget(self.quit_button)
        column.addWidget(self.footer)
        self.card.hide()
        self.ghost = Ghost(self.window)

    @staticmethod
    def _sliders_icon(painter: QPainter, x: float, y: float, size: float, color: str) -> None:
        draw_sliders(painter, x, y, size, color, C.bg)

    def _rule(self) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(self.px(PAD), 0, self.px(PAD), 0)
        layout.addWidget(Rule())
        holder.setFixedHeight(1)
        return holder

    def _feature_row(
        self, name: str, *, side: QWidget | None = None, value: QWidget | None = None
    ) -> tuple[QWidget, Dot, Text, Text]:
        """Ligne d'une fonction : pastille et nom, ``value`` au bout de la ligne du nom ;
        dessous, une ligne d'état cachée tant qu'elle est vide ; à droite, ``side``."""
        px, fonts = self.px, self.fonts
        row = QWidget()
        row.setFixedHeight(px(FEATURE_H))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(px(PAD), 0, px(PAD), 0)
        layout.setSpacing(px(GAP))
        names = QWidget()
        lines = QVBoxLayout(names)
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(0)
        title_line = QHBoxLayout()
        title_line.setSpacing(px(GAP))
        dot = Dot(px(DOT), C.neutral)
        title = self._text(fonts.title, C.text, name)
        title_line.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        title_line.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        title_line.addStretch(1)
        if value is not None:
            title_line.addWidget(value, 0, Qt.AlignmentFlag.AlignVCenter)
        lines.addLayout(title_line)
        state = self._text(fonts.caption, C.text_2, elide=True)
        state.hide()
        state_line = QHBoxLayout()
        state_line.setContentsMargins(px(DOT) + px(GAP), 0, 0, 0)
        state_line.addWidget(state)
        lines.addLayout(state_line)
        layout.addWidget(names, 1, Qt.AlignmentFlag.AlignVCenter)
        if side is not None:
            layout.addWidget(side, 0, Qt.AlignmentFlag.AlignVCenter)
        self._draggable(row, names, dot, title, state)
        if value is not None:
            self._draggable(value)
        return row, dot, title, state

    def _section(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        """Liste ou réglage : intertitre en légende semi-grasse, tertiaire."""
        frame = QWidget()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        heading = self._text(self.fonts.heading, C.text_3, title)
        layout.addWidget(heading)
        layout.addSpacing(self.px(HEADING_GAP))
        return frame, layout

    def _journal(self, layout: QVBoxLayout, when_w: int) -> Journal:
        """Rangées de 24 px : moment en texte tertiaire, 12 px, libellé ; cachées tant que vides."""
        px = self.px
        rows: Journal = []
        for _ in range(RECENT_ROWS):
            row = QWidget()
            row.setFixedHeight(px(ROW_H))
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(px(JOURNAL_GAP))
            when = self._text(tabular(self.fonts.body), C.text_3)
            when.setFixedWidth(when_w)
            what = self._text(self.fonts.body, C.text, elide=True)
            line.addWidget(when, 0, Qt.AlignmentFlag.AlignVCenter)
            line.addWidget(what, 1, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(row)
            row.hide()
            rows.append((row, when, what))
        return rows

    def _notice(
        self, width: int, make_button: Callable[[str, Callable[[], None]], Button] | None = None
    ) -> Notice:
        px = self.px
        notice = Notice(
            width=width,
            pad=(px(PAD), px(NOTICE_PAD_Y)),
            gap=px(GAP),
            radius=px(NOTICE_RADIUS),
            icon=px(NOTICE_ICON),
            font=self.fonts.body,
            badge=self.fonts.badge,
            line=px(LINE),
            make_button=make_button,
        )
        notice.hide()
        return notice

    def _notice_button(self, text: str, command: Callable[[], None]) -> Button:
        return self._button(text, command, "standard")

    def _draggable(self, *widgets: QWidget) -> None:
        for widget in widgets:
            widget.installEventFilter(self._dragger)

    def _build_settings(self) -> None:
        """Popover des réglages, fenêtre à part posée contre la carte ; caché au départ."""
        px, fonts = self.px, self.fonts
        inner = px(POPOVER_W) - 2 * px(PAD)
        self.popover = Surface(px(CARD_RADIUS), self.settings)
        box = self._box(self.popover, (PAD, PAD, PAD, PAD), SETTING_GAP)

        self.s_rest, rest = self._section("Au repos")
        self.rest_choice = self._segmented(REST_MODES, self._choose_rest, inner)
        rest.addWidget(self.rest_choice)
        box.addWidget(self.s_rest)

        self.s_opacity = QWidget()
        opacity = QVBoxLayout(self.s_opacity)
        opacity.setContentsMargins(0, 0, 0, 0)
        opacity.setSpacing(px(HEADING_GAP))
        heading = QHBoxLayout()
        heading.addWidget(self._text(fonts.heading, C.text_3, "Opacité au repos"))
        heading.addStretch(1)
        self.opacity_value = self._text(tabular(fonts.heading), C.text_3)
        heading.addWidget(self.opacity_value)
        opacity.addLayout(heading)
        self.opacity = Slider(
            self._choose_opacity,
            width=inner,
            height=px(SLIDER_H),
            track=px(TRACK_H),
            thumb=px(THUMB),
            snap=snap_opacity,
            bounds=(OPACITY_MIN, OPACITY_MAX),
        )
        opacity.addWidget(self.opacity)
        box.addWidget(self.s_opacity)

        self.s_dialog, dialog = self._section(f"Vous êtes toujours là{NBSP}?")
        self.segmented = self._segmented(ANSWERS, self._choose, inner)
        dialog.addWidget(self.segmented)
        self.dialog_gap = QWidget()
        self.dialog_gap.setFixedHeight(px(GAP))
        dialog.addWidget(self.dialog_gap)
        self.dialog_hint = Wrapped(fonts.caption, C.text_3, px(LINE_SMALL), inner)
        dialog.addWidget(self.dialog_hint)
        box.addWidget(self.s_dialog)

        toggle, stop = self.hotkeys
        self.hint = Wrapped(fonts.caption, C.text_3, px(LINE_SMALL), inner)
        self.hint.set_text(shortcuts_hint(toggle, stop))
        box.addWidget(self.hint)
        self.pop_ghost = Ghost(self.settings)

    def _segmented(
        self, options: Sequence[tuple[str, str]], command: Callable[[str], None], width: int
    ) -> Segmented:
        return Segmented(
            options,
            command,
            width=width,
            height=self.px(CONTROL_H),
            inset=self.px(SEGMENT_INSET),
            font=self.fonts.body,
            strong=self.fonts.strong,
        )

    # --- disposition --------------------------------------------------------
    @property
    def card_shown(self) -> bool:
        return self.rest == "panel" or self.expanded

    def _layout(
        self,
        notice: tuple[str, str] | None,
        update: tuple[str, str, str] | None,
        chests: ChestsView | None,
    ) -> None:
        """Carte : relance, coffres, mise à jour, pied (Réglages, Quitter)."""
        if notice is not None:
            self.notice.show_message(*notice)
        self.notice.setVisible(notice is not None)
        self.chests_rule.setVisible(chests is not None)  # absente si le compteur est désactivé
        self.chests_block.setVisible(chests is not None)
        if chests is not None:
            if chests.notice is not None:
                self.chests_notice.show_message(*chests.notice)
            self.chests_notice.setVisible(chests.notice is not None)
            self.s_games.setVisible(bool(chests.games))
            self.chests_body.setVisible(chests.notice is not None or bool(chests.games))
        if update is not None:
            self._show_update(update)
        self.update_notice.setVisible(update is not None)  # caché à sa création
        self.update_box.setVisible(update is not None)
        self.pill_chests.setVisible(chests is not None and bool(chests.value))
        self._fit()

    @staticmethod
    def _size(widget: QWidget, width: int = 0) -> None:
        """Taille au contenu (les widgets cachés ne comptent pas) ; largeur fixe si donnée."""
        layout = widget.layout()
        if layout is None:
            return
        # textes et lignes changés à l'instant : tailles recalculées de bas en haut,
        # puis positions de haut en bas (Qt ne le ferait qu'au prochain tour de boucle)
        children = widget.findChildren(QWidget)
        nested = [c.layout() for c in children if c.layout() is not None]
        for child in reversed(children):
            child.updateGeometry()  # vide le cache de taille que garde la mise en page parente
            if child.layout() is not None:
                child.layout().invalidate()
        layout.invalidate()
        hint = layout.sizeHint()
        widget.resize(width or hint.width(), hint.height())
        layout.activate()
        for child in nested:
            child.activate()

    def _fit(self) -> None:
        """Taille de la fenêtre : celle de la vue affichée, sauf pendant une animation."""
        self._size(self.pill)
        self._size(self.card, self.px(WIDTH))
        if self.ghost.running:
            return
        view = self.card if self._card_view else self.pill
        self._place(view.width(), view.height())

    def _place(self, width: int, height: int) -> None:
        """Pose la fenêtre à sa place, entière sur son écran.

        Pastille et carte partagent le coin haut gauche : la carte s'ouvre sous le curseur.
        """
        x, y = self._pos
        area = self.win32.work_area(x + width // 2, y + self.px(PILL_H) // 2)
        self._at = clamp(x, y, width, height, area)
        self.window.setGeometry(self._at[0], self._at[1], width, height)
        if self.settings_open:
            self._place_settings()

    def _place_settings(self) -> bool:
        """Popover contre la carte, bas alignés : à droite, à gauche s'il n'y a pas la place.

        Vrai s'il est à droite.
        """
        px = self.px
        self._size(self.popover, px(POPOVER_W))
        width, height = self.popover.width(), self.popover.height()
        card_w, card_h = self.card.width(), self.card.height()
        x, y = self._at
        area = self.win32.work_area(x + card_w // 2, y + card_h // 2)
        right = x + card_w + px(POPOVER_GAP)
        left = x - px(POPOVER_GAP) - width
        on_right = right + width <= area[2] or left < area[0]
        spot = clamp(right if on_right else left, y + card_h - height, width, height, area)
        self.settings.setGeometry(spot[0], spot[1], width, height)
        return on_right

    def _set_view(self, card: bool) -> None:
        """Pastille ou carte, l'une s'effaçant dans l'autre (180 ms, 120 à la fermeture)."""
        if card == self._card_view:
            return
        self._card_view = card
        origin = QPointF(self.px(16), self.px(16))
        if not self.animate or not self.window.isVisible():
            self.ghost.stop()
            self.card.setVisible(card)
            self.pill.setVisible(not card)
            self._fit()
            return
        self._size(self.card, self.px(WIDTH))
        self._glass_now(self.card)
        snapshot = self.card.grab()
        self.card.hide()
        if card:
            self.pill.hide()
            self._place(self.card.width(), self.card.height())
            self.ghost.play(snapshot, origin, opening=True, ms=OPEN_MS, done=self._view_done)
        else:
            self.pill.show()
            self.ghost.play(snapshot, origin, opening=False, ms=CLOSE_MS, done=self._view_done)

    def _view_done(self) -> None:
        self.card.setVisible(self._card_view)
        self.pill.setVisible(not self._card_view)
        self._fit()

    # --- actions ------------------------------------------------------------
    def _quit(self) -> None:
        log.info("fermeture : bouton Quitter")
        self.controller.quit()

    def _closed_by_system(self) -> None:
        """WM_CLOSE (Alt+F4, fermeture de session, autre logiciel) : arrêt propre, journalisé."""
        log.warning("fermeture demandée par Windows (WM_CLOSE)")
        self.controller.quit()

    def _toggle(self) -> None:
        """Activer ou désactiver la relance (comme F8) ; rien si le moteur s'est arrêté."""
        if self.controller.status().phase == "stopped":
            return
        self.controller.toggle()
        self._render(self.controller.status())

    def _toggle_settings(self) -> None:
        if self.settings_open:
            self._close_settings()
            return
        self.settings_open = True
        self.settings_button.set_selected(True)
        self._render(self.controller.status())
        on_right = self._place_settings()
        if not self.animate:
            return
        self.popover.hide()
        self.settings.show()
        self._glass_now(self.popover)
        snapshot = self.popover.grab()
        width, height = self.popover.width(), self.popover.height()
        origin = QPointF(0 if on_right else width, height)  # depuis le coin contre la carte
        self.pop_ghost.play(snapshot, origin, opening=True, ms=OPEN_MS, done=self.popover.show)

    def _close_settings(self) -> None:
        if not self.settings_open:
            return
        self.settings_open = False
        self.settings_button.set_selected(False)
        self.pop_ghost.stop()
        self.settings.hide()

    def _choose(self, answer: str) -> None:
        try:
            self.controller.set_still_here(answer)
        except ValueError as exc:
            log.warning("%s", exc)
            return
        self._render(self.controller.status())
        self._save({"engine": {"still_here": answer}})

    def _choose_rest(self, rest: str) -> None:
        self.rest = rest
        self.rest_choice.set(rest)
        self._render(self.controller.status())
        self._save({"panel": {"rest": rest}})

    def _glass_now(self, surface: Surface) -> None:
        """Le jeu flouté sous cette surface, tout de suite (vue qui s'ouvre)."""
        if self._glass_worker is None:  # captures de relecture : rien n'est à l'écran
            return
        at = surface.mapToGlobal(QPoint(0, 0))
        surface.backdrop = self._glass.grab(at.x(), at.y(), surface.width(), surface.height())
        surface.update()

    def _surfaces(self) -> dict[str, Surface]:
        return {"pill": self.pill, "card": self.card, "popover": self.popover}

    def _glass_tick(self) -> None:
        """Verre : le jeu flouté sous chaque surface visible, une quinzaine de fois par seconde."""
        if self._glass_worker is None:
            return
        zones = []
        for key, surface in self._surfaces().items():
            if surface.isVisible():
                at = surface.mapToGlobal(QPoint(0, 0))
                zones.append((key, (at.x(), at.y(), surface.width(), surface.height())))
        if zones:
            self._glass_worker.request(zones)

    def _glass_ready(self, results: list[tuple[str, QImage | None]]) -> None:
        surfaces = self._surfaces()
        for key, image in results:
            surfaces[key].backdrop = image
            surfaces[key].update()

    def close_glass(self) -> None:
        if self._glass_worker is not None:
            self._glass_worker.stop()

    def _choose_opacity(self, value: int, final: bool) -> None:
        """Aperçu sur la carte pendant le réglage ; enregistré au relâchement."""
        self.rest_opacity = value
        self.opacity_value.set_text(f"{value}{NBSP}%")
        now = time.monotonic()
        self._preview_until = now + PREVIEW_S
        self._apply_alpha(now)
        if final:
            self._save({"panel": {"rest_opacity": value}})

    def _save(self, changes: dict[str, object]) -> None:
        if not self.persist:
            return
        try:
            update_local_config(changes)
        except (OSError, ValueError) as exc:
            log.warning("réglage du panneau non enregistré (%s)", exc)

    def drag_start(self, at: QPoint) -> None:
        self._drag = at - QPoint(*self._at)

    def drag_move(self, at: QPoint) -> None:
        if self._drag is None:
            return
        spot = at - self._drag
        self._pos = self._at = (spot.x(), spot.y())
        self.window.move(spot)
        if self.settings_open:
            self._place_settings()

    def drag_end(self) -> None:
        if self._drag is None:
            return
        self._drag = None
        self._fit()
        self._pos = self._at
        self._save({"panel": {"position": list(self._pos)}})

    # --- survol et opacité ----------------------------------------------------
    def _follow(self) -> None:
        """Toutes les 50 ms : souris traversante en partie, carte ouverte au survol."""
        if self.controller.closing:
            return
        try:
            self._guard_mouse()
            now = time.monotonic()
            expanded, self._left_at = hover_step(
                self._cursor_inside(), self.expanded, self._left_at, now
            )
            if expanded != self.expanded:
                self.expanded = expanded
                if not expanded:
                    self._close_settings()
                self._render(self.controller.status())
            self._apply_alpha(now)
        except Exception:  # le suivi ne doit pas s'arrêter pour de bon
            log.exception("erreur pendant le suivi du curseur")

    def _cursor_inside(self) -> bool:
        """Curseur sur la vue affichée ou ses réglages, et à l'utilisateur (pas tenu par le jeu)."""
        if self._drag is not None:
            return True
        if self._through:
            return False
        x, y, _, _ = self.win32.cursor_state()
        left, top, right, bottom = self.win32.window_rect(self.hwnd)
        if not self._card_view:  # la carte qui se referme ne retient pas le curseur
            right = min(right, left + self.pill.width())
            bottom = min(bottom, top + self.pill.height())
        if left <= x < right and top <= y < bottom:
            return True
        if self.settings_open:
            left, top, right, bottom = self.win32.window_rect(self.settings_hwnd)
            return left <= x < right and top <= y < bottom
        return False

    def _apply_alpha(self, now: float) -> None:
        """Opaque au survol ; sinon (ou pendant son réglage) l'opacité au repos, en fondu."""
        rest = self.rest_opacity / 100
        target = rest if now < self._preview_until or not self.expanded else 1.0
        if target == self._opacity_target:
            return
        self._opacity_target = target
        self._fade.stop()
        if not self.animate:
            self.window.setWindowOpacity(target)
            return
        self._fade.setStartValue(self.window.windowOpacity())
        self._fade.setEndValue(target)
        self._fade.start()

    # --- rendu --------------------------------------------------------------
    def _poll(self) -> None:
        if self.controller.closing:
            QApplication.exit(0)  # quit() redemanderait la fermeture des fenêtres, refusée
            return
        try:
            self._guard_focus()
            if not self.follow_cursor:
                self._guard_mouse()
            self.controller.check_stall()
            status = self.controller.status()
            self._render(status)
            self._watch_update(status)
        except Exception:  # un rendu raté ne doit pas figer le panneau pour de bon
            log.exception("erreur pendant la mise à jour du panneau")

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
        self.dot.set_color(look.dot)
        self.pill_dot.set_color(look.dot)
        self.pill_text.set_text(look.short)
        label = action_label(status.phase)
        self.action.set_text(label)
        self.action.set_kind("accent" if label == ACTIVATE else "standard")
        self.action.set_enabled(status.phase != "stopped")
        chests_state = self.controller.chests()
        chests = None
        if chests_state is not None:
            # un replay « en direct » sans fenêtre du jeu : partie interrompue, pas en cours
            game_open = not self._game_known or bool(self._game())
            chests = chests_view(chests_state, game_open=game_open)
            self.chests_dot.set_color(chests.dot)
            self.chests_state.set_text(chests.state)
            self.chests_state.setVisible(bool(chests.state))
            self.chests_value.set_text(chests.value)
            self.pill_total.set_text(chests.value)
            self._fill_journal(self.games, chests.games[:RECENT_ROWS])
        recent = tuple(
            (time.strftime("%H:%M", time.localtime(c.ts)), c.label)
            for c in status.recent[:RECENT_ROWS]
        )
        self._fill_journal(self.recent, recent)
        self.empty.setVisible(not recent)
        self._render_settings(status)
        update = None
        if self.updates is not None:
            update = update_notice(self.updates.state())
        self._layout(explain(status), update, chests)
        self._set_view(self.card_shown)

    def _render_settings(self, status: Status) -> None:
        """Réglages : mode et opacité au repos, réponse au dialogue et ses boutons sans modèle."""
        self.rest_choice.set(self.rest)
        self.opacity.set(self.rest_opacity)
        self.opacity_value.set_text(f"{self.rest_opacity}{NBSP}%")
        answers = set(self.controller.still_here_answers())
        disabled = frozenset(a for a, _ in ANSWERS) - answers
        self.segmented.set(status.still_here, disabled)
        hint = missing_hint(disabled)
        self.dialog_hint.set_text(hint)
        self.dialog_hint.setVisible(bool(hint))
        self.dialog_gap.setVisible(bool(hint))
        if self.settings_open:
            self._place_settings()

    def _show_update(self, update: tuple[str, str, str]) -> bool:
        severity, message, label = update
        action = (label, self._update_action) if label else None
        return self.update_notice.show_message(severity, message, action)

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
    def _fill_journal(journal: Journal, rows: tuple[tuple[str, str], ...]) -> None:
        for index, (row, when, what) in enumerate(journal):
            if index < len(rows):
                when.set_text(rows[index][0])
                what.set_text(rows[index][1])
            row.setVisible(index < len(rows))


class _Dragger(QObject):
    """Déplace le panneau quand on le tire par sa pastille ou ses lignes."""

    def __init__(self, panel: Panel) -> None:
        super().__init__(panel.window)
        self._panel = panel

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if not isinstance(event, QMouseEvent):
            return False
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._panel.drag_start(event.globalPosition().toPoint())
            return True
        if kind == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
            self._panel.drag_move(event.globalPosition().toPoint())
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self._panel.drag_end()
            return True
        return False


# --- lancement ------------------------------------------------------------------
def qt_app() -> QApplication:
    """L'application Qt du processus, en pixels d'écran (mise à l'échelle de Qt coupée)."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    return app


def _log_qt_error(
    kind: type[BaseException], value: BaseException, tb: TracebackType | None
) -> None:
    """Erreur dans un rappel Qt : sous pythonw, elle n'irait nulle part."""
    log.error("erreur dans le panneau", exc_info=(kind, value, tb))


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

    app = qt_app()
    previous_hook = sys.excepthook
    sys.excepthook = _log_qt_error
    panel = Panel(
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
        app.exec()
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
        panel.close_glass()
        panel.settings.hide()
        panel.window.hide()
        sys.excepthook = previous_hook
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
