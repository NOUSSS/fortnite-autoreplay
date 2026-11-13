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
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol

from . import diagnostics
from .actions import Actions
from .capture import CaptureError, Frame, ScreenCapture
from .chests import ChestTracker, Reader, Summary
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
    """Encre vert sombre, or pour ce qui agit ou est choisi, quatre couleurs d'état."""

    bg: str = "#11201E"
    layer: str = "#1A2C29"
    layer_hover: str = "#22372F"
    layer_pressed: str = "#162622"
    stroke: str = "#2A403C"
    text: str = "#F4F0E6"
    text_2: str = "#B8C3BB"
    text_3: str = "#7D8D86"
    accent: str = "#F0B93A"
    accent_hover: str = "#F5C65A"
    accent_pressed: str = "#E0A82C"
    on_accent: str = "#1E1604"
    success: str = "#7BD88F"
    caution: str = "#FF9A4D"
    critical: str = "#FF7B7B"
    neutral: str = "#7D8D86"
    caution_bg: str = "#3A2A17"
    critical_bg: str = "#351C1C"


C = Palette()

# grille de 4 px, en pixels à 96 ppp (multipliés par l'échelle d'affichage)
WIDTH = 320  # carte dépliée
FEATURE_H = 52  # ligne d'une fonction : nom et état sur deux lignes
PILL_H = 32  # pastille du repos
PAD = 12  # marge intérieure de la carte, du popover et des bandeaux (côtés)
GAP = 8  # entre éléments voisins d'une ligne
HEADING_GAP = 4  # sous un intertitre
DETAIL_GAP = 4  # entre la ligne d'une fonction et son détail
SECTION_GAP = 12  # entre bandeau et liste d'une fonction
SETTING_GAP = 16  # entre réglages du popover
NOTICE_PAD_Y = 10
ROW_H = 24
CONTROL_H = 32  # boutons et sélecteurs, en pilule
ACTION_MIN_W = 104  # « Activer » et « Désactiver » à la même largeur
BUTTON_PAD = 14
SEGMENT_INSET = 3
SLIDER_H = 20
TRACK_H = 6
THUMB = 18
NOTICE_RADIUS = 8
POPOVER_W = 272
POPOVER_GAP = 8
DOT = 8
RECENT_ROWS = 3

# opacité au repos (pastille ou carte sans la souris), en pour cent
OPACITY_MIN, OPACITY_MAX, OPACITY_STEP = 20, 100, 5
_HOVER_MS = 50  # suivi du curseur : ouverture au survol
LEAVE_S = 0.4  # la carte se referme après ce délai hors du panneau
PREVIEW_S = 0.7  # aperçu de l'opacité après le dernier mouvement du curseur

# glyphes de Segoe Fluent Icons
ICON_INFO = ""
ICON_WARNING = ""
ICON_ERROR = ""
ICON_DOT = ""  # disque plein, lissé (un ovale du canevas ne l'est pas)
ICON_SETTINGS = ""  # curseurs de réglage
ICON_CHEST = ""  # boîte

FONTS = Path(__file__).with_name("fonts")  # Figtree et Bricolage Grotesque (OFL)
_FR_PRIVATE = 0x10


@dataclass(frozen=True, slots=True)
class Look:
    title: str  # sous « Relance auto »
    short: str  # dans la pastille du repos
    dot: str


LOOKS: dict[Phase, Look] = {
    "active": Look("Actif", "Relance active", C.success),
    "paused": Look("Désactivé", "Relance désactivée", C.neutral),
    "stuck": Look("Bloqué", "Relance bloquée", C.critical),
    "errors": Look("Bloqué", "Relance bloquée", C.critical),
    "no_game": Look("En attente du jeu", "En attente du jeu", C.caution),
    "hidden": Look("En attente du jeu", "En attente du jeu", C.caution),
    "background": Look("En attente du jeu", "En attente du jeu", C.caution),
    "capture": Look("Capture impossible", "Capture impossible", C.critical),
    "covered": Look("Clic annulé", "Clic annulé", C.caution),
    "mouse": Look("Clic en attente", "Clic en attente", C.caution),
    "stopped": Look("Arrêté", "Relance arrêtée", C.neutral),
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
NO_MATCH_YET = "Aucune partie comptée pour l\u2019instant"
RECORDING = f"Partie en cours{NBSP}: comptée à la sortie"  # replay fermé en quittant
COUNTING = "Comptage de votre dernière partie…"
INTERRUPTED_SHORT = f"Partie interrompue{NBSP}: jeu fermé"
INTERRUPTED = f"Le jeu s\u2019est fermé pendant la partie{NBSP}: ses coffres ne sont pas comptés."
REPLAYS_OFF_SHORT = "Rediffusions désactivées dans Fortnite"
READER_DOWN_SHORT = "Lecteur de replays indisponible"
READ_FAILED_SHORT = "Dernier replay illisible"

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
    state: str  # sous « Coffres », une ligne
    value: str = ""  # total à droite, vide tant qu'il n'y en a pas
    notice: tuple[str, str] | None = None  # gravité, message
    games: tuple[tuple[str, str], ...] = ()  # dernières parties : (début, coffres)


def chests_view(state: ChestsState, *, game_open: bool = True) -> ChestsView:
    """Ce que montre la ligne « Coffres », indépendante de la relance (désactivée comprise).

    L'état dit d'abord ce qui est en train de se passer : partie quittée en
    cours de comptage, partie en cours, partie interrompue (jeu fermé : son
    replay reste « en direct »), replay illisible ; sinon la dernière partie.
    """
    if state.unavailable:
        return ChestsView(C.caution, READER_DOWN_SHORT, notice=("caution", READER_DOWN))
    summary = state.summary
    if summary is None:
        return ChestsView(C.neutral, READING_REPLAYS)
    if not summary.replays:
        return ChestsView(C.caution, REPLAYS_OFF_SHORT, notice=("caution", REPLAYS_OFF))
    interrupted = summary.interrupted or (summary.recording and not game_open)
    dot, notice = C.success, None
    if interrupted:
        dot, notice = C.caution, ("caution", INTERRUPTED)
    elif summary.failed:
        dot, notice = C.caution, ("caution", READ_FAILED)
    if summary.counting:
        shown = COUNTING
    elif summary.recording and game_open:
        shown = RECORDING
    elif interrupted:
        shown = INTERRUPTED_SHORT
    elif summary.failed:
        shown = READ_FAILED_SHORT
    elif summary.last is not None:
        shown = f"{plural(summary.last, 'coffre')} à la dernière partie"
    else:
        shown = NO_MATCH_YET
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

    def set_alpha(self, hwnd: int, alpha: int) -> None:
        """Opacité de la fenêtre (0 à 255), en couches dès le premier appel."""
        user32 = self.user32
        extended = self.ex_style(hwnd)
        if not extended & _WS_EX_LAYERED:
            user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, extended | _WS_EX_LAYERED)
        user32.SetLayeredWindowAttributes(hwnd, 0, max(0, min(255, alpha)), _LWA_ALPHA)
        user32.RedrawWindow(hwnd, None, None, _RDW_INVALIDATE_ALL)

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


def mix(color: str, under: str, amount: float) -> str:
    """``color`` posée à ``amount`` d'opacité sur ``under`` (bouton désactivé)."""
    top = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    low = [int(under[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(
        f"{round(t * amount + b * (1 - amount)):02X}" for t, b in zip(top, low, strict=True)
    )


@dataclass(frozen=True, slots=True)
class _Fill:
    rest: str
    hover: str
    pressed: str
    text: str
    stroke: str = ""
    hover_text: str = ""


_KINDS = {
    "accent": _Fill(C.accent, C.accent_hover, C.accent_pressed, C.on_accent),
    "standard": _Fill(C.layer, C.layer_hover, C.layer_pressed, C.text, C.stroke),
    "subtle": _Fill(C.bg, C.layer, C.layer_pressed, C.text_2, hover_text=C.text),
}
_DISABLED = 0.4  # opacité d'un bouton désactivé


class Button(tk.Canvas):
    """Bouton en pilule : le survol et l'appui changent sa surface, sans halo.

    Le clic part au relâchement, si le pointeur est resté dessus. ``selected``
    garde l'aspect survolé (réglages ouverts) ; désactivé, il s'efface à 40 %.
    """

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
        icon: tuple[str, tkfont.Font, int] | None = None,  # glyphe, police, écart au texte
        bg: str = C.bg,  # fond derrière les coins arrondis (celui d'un bandeau, par exemple)
    ) -> None:
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
        self._kind = kind
        self._bg = bg
        self._command = command
        self._hover = self._pressed = self._selected = False
        self._enabled = True
        self._shape = _round_rect(self, (0, 0, width - 1, height - 1), height / 2, outline="")
        self._font = font
        self._icon = icon
        self._glyph = self.create_text(0, height / 2, text=icon[0] if icon else "", anchor="w")
        if icon is not None:
            self.itemconfigure(self._glyph, font=icon[1])
        self._text = self.create_text(0, height / 2, text=text, font=font, anchor="w")
        self._center()
        self._paint()
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)

    def _center(self) -> None:
        """Icône puis libellé, centrés ensemble."""
        width = int(self.cget("width"))
        text_w = self._font.measure(self.itemcget(self._text, "text"))
        icon_w = 0
        if self._icon is not None:
            icon_w = self._icon[1].measure(self._icon[0]) + self._icon[2]
        left = (width - icon_w - text_w) / 2
        self.coords(self._glyph, left, int(self.cget("height")) / 2)
        self.coords(self._text, left + icon_w, int(self.cget("height")) / 2)

    def _set(self, *, hover: bool | None = None, pressed: bool | None = None) -> None:
        if hover is not None:
            self._hover = hover
        if pressed is not None:
            self._pressed = pressed
        self._paint()

    def _paint(self) -> None:
        fill = _KINDS[self._kind]
        live = self._enabled
        if live and self._pressed:
            color, ink = fill.pressed, fill.hover_text or fill.text
        elif live and (self._hover or self._selected):
            color, ink = fill.hover, fill.hover_text or fill.text
        else:
            color, ink = fill.rest, fill.text
        stroke = fill.stroke or color
        if not live:
            color, ink, stroke = (mix(c, self._bg, _DISABLED) for c in (color, ink, stroke))
        self.itemconfigure(self._shape, fill=color, outline=stroke)
        self.itemconfigure(self._text, fill=ink)
        self.itemconfigure(self._glyph, fill=ink)

    def _release(self, event: tk.Event) -> None:
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._set(pressed=False)
        if inside and self._enabled:
            self._command()

    def set_text(self, text: str) -> None:
        if self.itemcget(self._text, "text") != text:
            self.itemconfigure(self._text, text=text)
            self._center()

    def set_kind(self, kind: str) -> None:
        if kind != self._kind:
            self._kind = kind
            self._paint()

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self._enabled:
            self._enabled = enabled
            self.configure(cursor="" if enabled else "no")
            self._paint()

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self._paint()


class Segmented(tk.Canvas):
    """Choix exclusif en pilule ; le segment choisi prend l'or."""

    def __init__(
        self,
        parent: tk.Misc,
        options: Sequence[tuple[str, str]],
        command: Callable[[str], None],
        *,
        width: int,
        height: int,
        inset: int,
        font: tkfont.Font,
        strong: tkfont.Font,
    ) -> None:
        super().__init__(parent, width=width, height=height, bg=C.bg, highlightthickness=0, bd=0)
        self._choices = list(options)
        self._command = command
        self._fonts = (font, strong)
        self._size = (width, height, inset)
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

    def set(self, selected: str, disabled: frozenset[str] = frozenset()) -> None:
        if (selected, disabled) != (self._selected, self._disabled):
            self._selected, self._disabled = selected, disabled
            self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width, height, inset = self._size
        font, strong = self._fonts
        _round_rect(self, (0, 0, width - 1, height - 1), height / 2, fill=C.layer, outline=C.stroke)
        step = width / len(self._choices)
        inner_r = height / 2 - inset
        for index, (value, label) in enumerate(self._choices):
            left, right = index * step, (index + 1) * step
            inner = (left + inset, inset, right - inset - 1, height - inset - 1)
            color, face = C.text_2, font
            if value == self._selected:
                _round_rect(self, inner, inner_r, fill=C.accent, outline=C.accent)
                color, face = C.on_accent, strong
            elif value in self._disabled:
                color = C.text_3
            elif value == self._pressed:
                _round_rect(self, inner, inner_r, fill=C.layer_pressed, outline=C.layer_pressed)
                color = C.text
            elif value == self._hover:
                _round_rect(self, inner, inner_r, fill=C.layer_hover, outline=C.layer_hover)
                color = C.text
            self.create_text((left + right) / 2, height / 2, text=label, font=face, fill=color)


class Slider(tk.Canvas):
    """Curseur horizontal : piste en pilule, part remplie en or, poignée claire.

    ``command(valeur, fini)`` suit chaque mouvement ; ``fini`` au relâchement.
    """

    def __init__(
        self,
        parent: tk.Misc,
        command: Callable[[int, bool], None],
        *,
        width: int,
        height: int,
        track: int,
        thumb_font: tkfont.Font,
        thumb: int,
        snap: Callable[[float], int],
        bounds: tuple[int, int],
    ) -> None:
        super().__init__(parent, width=width, height=height, bg=C.bg, highlightthickness=0, bd=0)
        self._command = command
        self._size = (width, height, track, thumb)
        self._thumb_font = thumb_font
        self._snap = snap
        self._bounds = bounds
        self._value = bounds[1]
        self.bind("<ButtonPress-1>", lambda e: self._move(e.x, final=False))
        self.bind("<B1-Motion>", lambda e: self._move(e.x, final=False))
        self.bind("<ButtonRelease-1>", lambda e: self._move(e.x, final=True))
        self._draw()

    def _span(self) -> tuple[float, float]:
        width, _, _, thumb = self._size
        return thumb / 2, width - thumb / 2

    def _move(self, x: float, *, final: bool) -> None:
        start, end = self._span()
        low, high = self._bounds
        ratio = min(1.0, max(0.0, (x - start) / (end - start)))
        value = self._snap(low + ratio * (high - low))
        if value != self._value:
            self._value = value
            self._draw()
        self._command(value, final)

    def set(self, value: int) -> None:
        if value != self._value:
            self._value = value
            self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width, height, track, _ = self._size
        start, end = self._span()
        low, high = self._bounds
        x = start + (self._value - low) / (high - low) * (end - start)
        top, bottom = (height - track) / 2, (height + track) / 2 - 1
        _round_rect(self, (0, top, width - 1, bottom), track / 2, fill=C.layer_hover, outline="")
        _round_rect(self, (0, top, x, bottom), track / 2, fill=C.accent, outline="")
        self.create_text(x, height / 2, text=ICON_DOT, font=self._thumb_font, fill=C.text)


ButtonFactory = Callable[[tk.Misc, str, Callable[[], None], str], Button]


class Notice(tk.Canvas):
    """Bandeau : icône de gravité et message sur fond teinté, coins de 8 px.

    Un bouton d'action facultatif se place sous le message, aligné sur lui.
    """

    _STYLES = {  # noqa: RUF012 - table de constantes
        "critical": (C.critical_bg, C.critical, ICON_ERROR),
        "caution": (C.caution_bg, C.caution, ICON_WARNING),
        "info": (C.layer, C.text_2, ICON_INFO),
    }

    def __init__(
        self,
        parent: tk.Misc,
        *,
        width: int,
        pad: tuple[int, int],  # côtés, haut et bas
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
        pad_x, pad_y = self._pad
        text_x = pad_x + self._icon_font.measure(glyph) + self._gap
        text = self.create_text(
            text_x,
            pad_y,
            text=message,
            font=self._font,
            fill=C.text,
            anchor="nw",
            width=self._width - text_x - pad_x,
        )
        bottom = self.bbox(text)[3]
        bottom = self._place_button(label, background, text_x, bottom)
        line = self._font.metrics("linespace")
        self.create_text(
            pad_x, pad_y + line / 2, text=glyph, font=self._icon_font, fill=tint, anchor="w"
        )
        height = bottom + pad_y
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


_fonts_loaded = False


def load_private_fonts(folder: Path = FONTS) -> int:
    """Charge les polices livrées pour ce seul processus (``FR_PRIVATE``), une fois.

    Rien n'est installé dans Windows ; sans elles, le panneau retombe sur Segoe UI.
    """
    global _fonts_loaded  # noqa: PLW0603 - chargement unique pour le processus
    if _fonts_loaded:
        return 0
    _fonts_loaded = True
    try:
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    except OSError:
        return 0
    gdi32.AddFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    gdi32.AddFontResourceExW.restype = ctypes.c_int
    loaded = 0
    for path in sorted(folder.glob("*.ttf")):
        if gdi32.AddFontResourceExW(str(path), _FR_PRIVATE, None):
            loaded += 1
        else:
            log.warning("police non chargée : %s", path.name)
    return loaded


# --- panneau --------------------------------------------------------------------
Journal = list[tuple[tk.Frame, tk.Label, tk.Label]]


class Panel:
    """Pastille au repos, carte complète au survol, réglages dans un popover à côté.

    Au repos (« Pastille ») ne reste que l'état de la relance et le total des
    coffres ; le curseur posé dessus ouvre la carte, qui se referme 0,4 s après
    son départ. Réglé sur « Panneau », la carte reste ouverte. Sans la souris,
    la fenêtre prend l'opacité au repos choisie ; au survol, elle redevient opaque.
    """

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
        follow_cursor: bool = True,  # faux pour les captures : la scène dit ce qui est ouvert
    ) -> None:
        self.root = root
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
        self.follow_cursor = follow_cursor
        self.expanded = False  # curseur sur le panneau (ou sorti depuis moins de LEAVE_S)
        self._left_at: float | None = None
        self.settings_open = False
        self._preview_until = 0.0  # opacité au repos montrée pendant son réglage
        self._alpha = -1
        self.width = self.px(WIDTH)
        self._drag: tuple[int, int] | None = None
        self._shape: tuple[object, ...] = ()  # disposition affichée (ce qui change la hauteur)
        self._settings_shape: tuple[object, ...] = ()
        start = tuple(cfg.position) if cfg.position else home
        self._pos = (int(start[0]), int(start[1]))  # coin haut gauche voulu
        self._at = self._pos  # coin réellement affiché (gardé entier sur l'écran)

        root.report_callback_exception = self._report_error  # type: ignore[method-assign]
        root.protocol("WM_DELETE_WINDOW", self._closed_by_system)
        root.withdraw()
        root.overrideredirect(True)
        root.configure(bg=C.bg)
        root.attributes("-topmost", cfg.topmost)
        root.title(TITLE)
        self._fonts()
        self._build()
        self._build_settings(cfg.topmost)
        self._render(controller.status())
        root.update_idletasks()
        self.hwnd = self.win32.top_level(root.winfo_id())
        self.win32.style(self.hwnd, exclude_from_capture=cfg.exclude_from_capture)
        self.settings_hwnd = self.win32.top_level(self.settings.winfo_id())
        self.win32.style(self.settings_hwnd, exclude_from_capture=cfg.exclude_from_capture)
        self._apply_alpha(time.monotonic())
        root.deiconify()
        self._guard_focus()
        root.after(_POLL_MS, self._poll)
        if follow_cursor:
            root.after(_HOVER_MS, self._follow)

    def px(self, value: float) -> int:
        return round(value * self.scale)

    # --- construction -----------------------------------------------------
    def _fonts(self) -> None:
        load_private_fonts()
        families = set(tkfont.families(self.root))

        def pick(*names: str) -> str:
            return next((n for n in names if n in families), "Segoe UI")

        def font(family: str, size: int, weight: str = "normal") -> tkfont.Font:
            return tkfont.Font(self.root, family=family, size=-self.px(size), weight=weight)

        text = pick("Figtree", "Segoe UI Variable Text", "Segoe UI")
        strong = pick("Figtree SemiBold", "Segoe UI Variable Text Semibold", "Segoe UI Semibold")
        self.f_body = font(text, 14)
        self.f_strong = font(strong, 14)
        self.f_caption = font(text, 12)
        self.f_heading = font(strong, 12)
        title = pick("Bricolage Grotesque 14pt")
        self.f_title = font(title, 14, "bold") if title != "Segoe UI" else self.f_strong
        icons = pick("Segoe Fluent Icons", "Segoe MDL2 Assets")
        self.f_icon = font(icons, 12)
        self.f_icon_body = font(icons, 14)
        self.f_dot = font(icons, DOT)
        self.f_thumb = font(icons, THUMB)

    def _label(self, parent: tk.Misc, font: tkfont.Font, color: str, **kw: object) -> tk.Label:
        return tk.Label(parent, font=font, fg=color, bg=C.bg, bd=0, padx=0, pady=0, **kw)

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        kind: str,
        width: int,
        *,
        icon: str = "",
        strong: bool = False,
    ) -> Button:
        return Button(
            parent,
            text,
            command,
            kind=kind,
            font=self.f_strong if strong else self.f_body,
            width=width,
            height=self.px(CONTROL_H),
            icon=(icon, self.f_icon_body, self.px(GAP)) if icon else None,
        )

    def _build(self) -> None:
        root, px = self.root, self.px
        inner = self.width - 2 * px(PAD)

        # au repos : pastille de l'état de la relance et total des coffres
        self.pill = tk.Frame(root, bg=C.bg, height=px(PILL_H))
        self.pill.pack_propagate(False)
        self.pill_inner = tk.Frame(self.pill, bg=C.bg)
        self.pill_inner.place(x=px(PAD), rely=0.5, anchor="w")
        self.pill_dot = self._label(self.pill_inner, self.f_dot, C.neutral, text=ICON_DOT)
        self.pill_dot.pack(side="left", padx=(0, px(GAP)))
        self.pill_text = self._label(self.pill_inner, self.f_body, C.text)
        self.pill_text.pack(side="left")
        self.pill_sep = tk.Frame(self.pill_inner, bg=C.stroke, width=1, height=px(12))
        self.pill_icon = self._label(self.pill_inner, self.f_icon, C.text_2, text=ICON_CHEST)
        self.pill_total = self._label(self.pill_inner, self.f_strong, C.text)
        self._draggable(self.pill, self.pill_inner, self.pill_dot, self.pill_text)
        self._draggable(self.pill_icon, self.pill_total)

        # au survol : la carte entière
        self.card = tk.Frame(root, bg=C.bg)
        self._action_w = max(
            px(ACTION_MIN_W),
            *(self.f_strong.measure(t) + 2 * px(BUTTON_PAD) for t in (ACTIVATE, DEACTIVATE)),
        )
        self.header, self.dot, self.title, self.state = self._feature_row(self.card, "Relance auto")
        self.action = self._button(
            self.header, DEACTIVATE, self._toggle, "standard", self._action_w, strong=True
        )
        self.action.grid(row=1, column=2, rowspan=2, padx=(px(GAP), px(PAD)))

        self.bot_body = tk.Frame(self.card, bg=C.bg, padx=px(PAD))
        self.notice = self._notice(self.bot_body, inner)
        self.s_recent = self._section(self.bot_body, "Derniers clics")
        # chiffres proportionnels : la colonne de l'heure a la largeur de la plus large
        clock_w = max(self.f_body.measure(f"{d}{d}:{d}{d}") for d in range(10))
        self.recent = self._journal(self.s_recent, clock_w)
        self.empty = tk.Frame(self.s_recent, bg=C.bg, height=px(ROW_H))
        self.empty.pack_propagate(False)
        empty = self._label(self.empty, self.f_body, C.text_3, text=NO_CLICK)
        empty.pack(side="left")

        # coffres : comptés à chaque fin de partie, que la relance soit active ou non
        self.chests_rule = tk.Frame(self.card, bg=C.stroke, height=1)
        self.chests_block = tk.Frame(self.card, bg=C.bg)
        self.chests_row, self.chests_dot, _, self.chests_state = self._feature_row(
            self.chests_block, "Coffres"
        )
        # le total sur la ligne du nom, aligné sur le bord droit du bouton de la relance
        self.chests_value = self._label(self.chests_row, self.f_title, C.text, anchor="e")
        self.chests_value.grid(row=1, column=2, sticky="e", padx=(px(GAP), px(PAD)))
        self._draggable(self.chests_value)
        self.chests_body = tk.Frame(self.chests_block, bg=C.bg, padx=px(PAD))
        self.chests_notice = self._notice(self.chests_body, inner)
        self.s_games = self._section(self.chests_body, "Dernières parties")
        date_w = max(
            self.f_body.measure(f"{d}{d}{NBSP}{month} {d}{d}:{d}{d}")
            for d in range(10)
            for month in SHORT_MONTHS
        )
        self.games = self._journal(self.s_games, date_w)

        self.footer_rule = tk.Frame(self.card, bg=C.stroke, height=1)
        self.update_box = tk.Frame(self.card, bg=C.bg, padx=px(PAD))
        self.update_notice = self._notice(self.update_box, inner, make_button=self._notice_button)
        self.update_notice.pack(fill="x")
        self.footer = tk.Frame(self.card, bg=C.bg, padx=px(PAD))
        settings_w = (
            self.f_icon_body.measure(ICON_SETTINGS)
            + px(GAP)
            + self.f_body.measure("Réglages")
            + 2 * px(PAD)
        )
        self.settings_button = self._button(
            self.footer, "Réglages", self._toggle_settings, "subtle", settings_w, icon=ICON_SETTINGS
        )
        self.settings_button.pack(side="left")
        quit_w = self.f_body.measure("Quitter") + 2 * px(BUTTON_PAD)
        self.quit_button = self._button(self.footer, "Quitter", self._quit, "standard", quit_w)
        self.quit_button.pack(side="right")

    def _build_settings(self, topmost: bool) -> None:
        """Popover des réglages, fenêtre à part posée contre la carte ; caché au départ."""
        px = self.px
        inner = px(POPOVER_W) - 2 * px(PAD)
        self.settings = tk.Toplevel(self.root, bg=C.bg)
        self.settings.withdraw()
        self.settings.overrideredirect(True)
        self.settings.attributes("-topmost", topmost)
        self.settings.title(SETTINGS_TITLE)
        self.settings.protocol("WM_DELETE_WINDOW", self._closed_by_system)
        box = tk.Frame(self.settings, bg=C.bg, padx=px(PAD), pady=px(PAD))
        box.pack(fill="both")

        self.s_rest = self._section(box, "Au repos")
        self.rest_choice = self._segmented(self.s_rest, REST_MODES, self._choose_rest, inner)
        self.s_rest.pack(fill="x")

        self.s_opacity = tk.Frame(box, bg=C.bg)
        heading = tk.Frame(self.s_opacity, bg=C.bg)
        heading.pack(fill="x", pady=(0, px(HEADING_GAP)))
        self._label(heading, self.f_heading, C.text_3, text="Opacité au repos").pack(side="left")
        self.opacity_value = self._label(heading, self.f_heading, C.text_3)
        self.opacity_value.pack(side="right")
        self.opacity = Slider(
            self.s_opacity,
            self._choose_opacity,
            width=inner,
            height=px(SLIDER_H),
            track=px(TRACK_H),
            thumb_font=self.f_thumb,
            thumb=px(THUMB),
            snap=snap_opacity,
            bounds=(OPACITY_MIN, OPACITY_MAX),
        )
        self.opacity.pack(anchor="w")
        self.s_opacity.pack(fill="x", pady=(px(SETTING_GAP), 0))

        self.s_dialog = self._section(box, "Vous êtes toujours là\u00a0?")
        self.segmented = self._segmented(self.s_dialog, ANSWERS, self._choose, inner)
        self.dialog_hint = self._label(
            self.s_dialog, self.f_caption, C.text_3, anchor="w", justify="left", wraplength=inner
        )
        self.s_dialog.pack(fill="x", pady=(px(SETTING_GAP), 0))

        toggle, stop = self.hotkeys
        self.hint = self._label(
            box,
            self.f_caption,
            C.text_3,
            text=shortcuts_hint(toggle, stop),
            anchor="w",
            justify="left",
            wraplength=inner,
        )
        self.hint.pack(fill="x", pady=(px(SETTING_GAP), 0))

    def _segmented(
        self,
        parent: tk.Misc,
        options: Sequence[tuple[str, str]],
        command: Callable[[str], None],
        width: int,
    ) -> Segmented:
        segmented = Segmented(
            parent,
            options,
            command,
            width=width,
            height=self.px(CONTROL_H),
            inset=self.px(SEGMENT_INSET),
            font=self.f_body,
            strong=self.f_strong,
        )
        segmented.pack(anchor="w")
        return segmented

    def _notice(
        self, parent: tk.Misc, width: int, make_button: ButtonFactory | None = None
    ) -> Notice:
        return Notice(
            parent,
            width=width,
            pad=(self.px(PAD), self.px(NOTICE_PAD_Y)),
            gap=self.px(GAP),
            radius=self.px(NOTICE_RADIUS),
            font=self.f_body,
            icon_font=self.f_icon,
            make_button=make_button,
        )

    def _feature_row(
        self, parent: tk.Misc, name: str
    ) -> tuple[tk.Frame, tk.Label, tk.Label, tk.Label]:
        """Ligne d'une fonction : pastille et nom, son état dessous ; colonne 2 libre."""
        px = self.px
        row = tk.Frame(parent, bg=C.bg, height=px(FEATURE_H), width=self.width)
        row.grid_propagate(False)
        row.rowconfigure((0, 3), weight=1)  # le bloc de texte reste centré en hauteur
        row.columnconfigure(1, weight=1)
        dot = self._label(row, self.f_dot, C.neutral, text=ICON_DOT)
        dot.grid(row=1, column=0, padx=(px(PAD), px(GAP)))
        title = self._label(row, self.f_title, C.text, text=name, anchor="w")
        title.grid(row=1, column=1, sticky="w")
        state = self._label(row, self.f_caption, C.text_2, anchor="w")
        state.grid(row=2, column=1, sticky="w")
        row.pack(fill="x")
        self._draggable(row, dot, title, state)
        return row, dot, title, state

    def _draggable(self, *widgets: tk.Misc) -> None:
        for widget in widgets:
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)

    def _journal(self, parent: tk.Misc, when_w: int) -> Journal:
        """Rangées de 24 px : moment en texte tertiaire, 12 px, libellé ; cachées tant que vides."""
        px = self.px
        rows: Journal = []
        for _ in range(RECENT_ROWS):
            row = tk.Frame(parent, bg=C.bg, height=px(ROW_H))
            row.grid_propagate(False)
            row.rowconfigure(0, weight=1)
            row.columnconfigure(0, minsize=when_w)
            when = self._label(row, self.f_body, C.text_3, anchor="w")
            when.grid(row=0, column=0, sticky="w")
            what = self._label(row, self.f_body, C.text, anchor="w")
            what.grid(row=0, column=1, sticky="w", padx=(px(PAD), 0))
            rows.append((row, when, what))
        return rows

    def _notice_button(
        self, parent: tk.Misc, text: str, command: Callable[[], None], bg: str
    ) -> Button:
        width = self.f_body.measure(text) + 2 * self.px(BUTTON_PAD)
        return Button(
            parent,
            text,
            command,
            kind="standard",
            font=self.f_body,
            width=width,
            height=self.px(CONTROL_H),
            bg=bg,
        )

    def _section(self, parent: tk.Misc, title: str) -> tk.Frame:
        """Liste ou réglage : intertitre en légende semi-grasse, tertiaire."""
        frame = tk.Frame(parent, bg=C.bg)
        heading = self._label(frame, self.f_heading, C.text_3, text=title, anchor="w")
        heading.pack(fill="x", pady=(0, self.px(HEADING_GAP)))
        return frame

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
        """Pastille, ou carte : relance, coffres, mise à jour, pied (Réglages, Quitter)."""
        px = self.px
        for widget in (
            self.pill,
            self.card,
            self.bot_body,
            self.notice,
            self.s_recent,
            self.chests_rule,
            self.chests_block,
            self.chests_body,
            self.chests_notice,
            self.s_games,
            self.update_box,
        ):
            widget.pack_forget()
        for widget in (self.pill_sep, self.pill_icon, self.pill_total):
            widget.pack_forget()
        if not self.card_shown:
            if chests is not None and chests.value:
                self.pill_sep.pack(side="left", padx=px(GAP))
                self.pill_icon.pack(side="left", padx=(0, px(HEADING_GAP)))
                self.pill_total.pack(side="left")
            self.pill_inner.update_idletasks()
            width = self.pill_inner.winfo_reqwidth() + 2 * px(PAD)
            self.pill.configure(width=width)
            self.pill.pack()
            self._place()
            return
        if notice is not None:
            self.notice.show(*notice)
            self.notice.pack(fill="x", pady=(0, px(SECTION_GAP)))
        self.s_recent.pack(fill="x")
        self.bot_body.pack(fill="x", pady=(px(DETAIL_GAP), px(PAD)))
        if chests is not None:  # absente si le compteur est désactivé
            self.chests_rule.pack(fill="x", padx=px(PAD))
            self.chests_block.pack(fill="x")
            self._layout_chests(chests)
        self.footer_rule.pack_forget()
        self.footer.pack_forget()
        self.footer_rule.pack(fill="x", padx=px(PAD))
        if update is not None:
            self._show_update(update)
            self.update_box.pack(fill="x", pady=(px(PAD), 0))
        self.footer.pack(fill="x", pady=(px(GAP), px(PAD)))
        self.card.pack()
        self._place()

    def _layout_chests(self, chests: ChestsView) -> None:
        """Sous la ligne des coffres : bandeau, puis dernières parties."""
        px = self.px
        if chests.notice is not None:
            self.chests_notice.show(*chests.notice)
            self.chests_notice.pack(fill="x")
        if chests.games:
            top = px(SECTION_GAP) if chests.notice else 0
            self.s_games.pack(fill="x", pady=(top, 0))
        if chests.notice is not None or chests.games:
            self.chests_body.pack(fill="x", pady=(px(DETAIL_GAP), px(PAD)))

    def _place(self) -> None:
        """Pose la fenêtre à sa place, taille au contenu, entière sur son écran.

        Pastille et carte partagent le coin haut gauche : la carte s'ouvre sous le curseur.
        """
        self.root.update_idletasks()
        width, height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x, y = self._pos
        area = self.win32.work_area(x + width // 2, y + self.px(PILL_H) // 2)
        self._at = clamp(x, y, width, height, area)
        self.root.geometry(f"{width}x{height}+{self._at[0]}+{self._at[1]}")
        if self.settings_open:
            self._place_settings()

    def _place_settings(self) -> None:
        """Popover contre la carte, bas alignés : à droite, à gauche s'il n'y a pas la place."""
        px = self.px
        self.settings.update_idletasks()
        width, height = self.settings.winfo_reqwidth(), self.settings.winfo_reqheight()
        self.root.update_idletasks()
        card_w, card_h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x, y = self._at
        area = self.win32.work_area(x + card_w // 2, y + card_h // 2)
        right = x + card_w + px(POPOVER_GAP)
        left = x - px(POPOVER_GAP) - width
        spot_x = right if right + width <= area[2] or left < area[0] else left
        spot = clamp(spot_x, y + card_h - height, width, height, area)
        self.settings.geometry(f"{width}x{height}+{spot[0]}+{spot[1]}")

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
        self._settings_shape = ()
        self._render(self.controller.status())
        self._place_settings()
        self.settings.deiconify()

    def _close_settings(self) -> None:
        if not self.settings_open:
            return
        self.settings_open = False
        self.settings_button.set_selected(False)
        self.settings.withdraw()

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
        self._shape = ()
        self._render(self.controller.status())
        self._save({"panel": {"rest": rest}})

    def _choose_opacity(self, value: int, final: bool) -> None:
        """Aperçu sur la carte pendant le réglage ; enregistré au relâchement."""
        self.rest_opacity = value
        self._text(self.opacity_value, f"{value}{NBSP}%")
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

    def _drag_start(self, event: tk.Event) -> None:
        self._drag = (event.x_root - self._at[0], event.y_root - self._at[1])

    def _drag_move(self, event: tk.Event) -> None:
        if self._drag is not None:
            self._pos = self._at = (event.x_root - self._drag[0], event.y_root - self._drag[1])
            self.root.geometry(f"+{self._at[0]}+{self._at[1]}")
            if self.settings_open:
                self._place_settings()

    def _drag_end(self, _event: tk.Event) -> None:
        if self._drag is None:
            return
        self._drag = None
        self._place()
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
        finally:
            self.root.after(_HOVER_MS, self._follow)

    def _cursor_inside(self) -> bool:
        """Curseur sur la carte ou ses réglages, et à l'utilisateur (pas tenu par le jeu)."""
        if self._drag is not None:
            return True
        if self._through:
            return False
        x, y, _, _ = self.win32.cursor_state()
        windows = [self.hwnd, self.settings_hwnd] if self.settings_open else [self.hwnd]
        for hwnd in windows:
            left, top, right, bottom = self.win32.window_rect(hwnd)
            if left <= x < right and top <= y < bottom:
                return True
        return False

    def _apply_alpha(self, now: float) -> None:
        """Opaque au survol ; sinon (ou pendant son réglage) l'opacité au repos."""
        rest = round(self.rest_opacity * 255 / 100)
        alpha = rest if now < self._preview_until or not self.expanded else 255
        if alpha != self._alpha:
            self.win32.set_alpha(self.hwnd, alpha)
            self._alpha = alpha

    # --- rendu --------------------------------------------------------------
    def _poll(self) -> None:
        if self.controller.closing:
            self.root.quit()
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
        for dot in (self.dot, self.pill_dot):
            self._color(dot, look.dot)
        self._text(self.state, look.title)
        self._text(self.pill_text, look.short)
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
            self._color(self.chests_dot, chests.dot)
            self._text(self.chests_state, chests.state)
            self._text(self.chests_value, chests.value)
            self._text(self.pill_total, chests.value)
            self._fill_journal(self.games, chests.games[:RECENT_ROWS])

        notice = explain(status)
        recent = tuple(
            (time.strftime("%H:%M", time.localtime(c.ts)), c.label)
            for c in status.recent[:RECENT_ROWS]
        )
        self._fill_recent(recent)
        self._render_settings(status)
        update = None
        if self.updates is not None:
            update = update_notice(self.updates.state())
        # ce qui change la taille ; le texte du bandeau de mise à jour suit le téléchargement
        shape = (
            self.card_shown,
            notice if self.card_shown else None,
            None if update is None else (update[0], update[2]),
            len(recent),
            None
            if chests is None
            else (bool(chests.value), chests.notice, min(len(chests.games), RECENT_ROWS)),
            look.short if not self.card_shown else "",
            chests.value if chests is not None and not self.card_shown else "",
        )
        if shape != self._shape:
            self._shape = shape
            self._layout(notice, update, chests)
        elif update is not None and self.card_shown and self._show_update(update):
            self._place()  # message passé à la ligne

    def _render_settings(self, status: Status) -> None:
        """Réglages : mode et opacité au repos, réponse au dialogue et ses boutons sans modèle."""
        self.rest_choice.set(self.rest)
        self.opacity.set(self.rest_opacity)
        self._text(self.opacity_value, f"{self.rest_opacity}{NBSP}%")
        answers = set(self.controller.still_here_answers())
        disabled = frozenset(a for a, _ in ANSWERS) - answers
        self.segmented.set(status.still_here, disabled)
        hint = missing_hint(disabled)
        if self._settings_shape == (hint,):
            return
        self._settings_shape = (hint,)
        self._text(self.dialog_hint, hint)
        if hint:
            self.dialog_hint.pack(fill="x", pady=(self.px(GAP), 0))
        else:
            self.dialog_hint.pack_forget()
        if self.settings_open:
            self._place_settings()

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

    @staticmethod
    def _color(label: tk.Label, color: str) -> None:
        if label.cget("fg") != color:
            label.configure(fg=color)

    def _fill_journal(self, journal: Journal, rows: tuple[tuple[str, str], ...]) -> None:
        for index, (row, when, what) in enumerate(journal):
            if index < len(rows):
                self._text(when, rows[index][0])
                self._text(what, rows[index][1])
                if not row.winfo_manager():
                    row.pack(fill="x")
            elif row.winfo_manager():
                row.pack_forget()

    def _fill_recent(self, rows: tuple[tuple[str, str], ...]) -> None:
        self._fill_journal(self.recent, rows)
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
