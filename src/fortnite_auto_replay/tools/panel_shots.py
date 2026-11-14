"""Captures du panneau dans chaque état, pour la relecture du design.

Le panneau tourne sur un moteur simulé (aucune capture, aucun clic) et rien
n'est affiché : chaque vue (pastille, carte, réglages) est dessinée hors écran
par Qt, fond transparent compris, sans rien enregistrer dans
``config/local.json``. Pour chaque scène : une image PNG et la position des
blocs (JSON) pour mesurer les écarts. Aucune fenêtre ne s'ouvre : on peut
capturer pendant une partie.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .. import config as config_mod
from ..chests import Summary
from ..engine import ClickRecord, Phase, Status
from ..panel import POPOVER_GAP, ChestsState, Panel, qt_app
from ..panel_glass import blur, to_qimage
from ..updater import UpdateState

NBSP = "\u00a0"


@dataclass(slots=True)
class Scene:
    name: str
    phase: Phase
    view: str  # "pill" (repos), "card" (survol) ou "settings" (carte et réglages)
    detail: str = ""
    recent: int = 0
    still_here: str = "ready"
    dry_run: bool = False
    hover: str = ""  # "action", "settings", "quit" ou une réponse du dialogue
    answers: tuple[str, ...] = ("ready", "not_yet", "ignore")
    update: str = "idle"  # phase de la mise à jour (bandeau au-dessus du pied)
    chests: str = "counted"  # état de la ligne des coffres (voir CHESTS ; absent : compteur coupé)
    opacity: int = 80
    anti_afk: bool | None = False  # None : anti-AFK indisponible (fenêtre du jeu non suivie)


_LABELS = {
    "back_to_lobby": "Retour au salon",
    "continue": "Continuer",
    "pass_battle": "Passer",
    "ready": "Prêt",
    "still_here_ready": f"Toujours là{NBSP}: Prêt",
}
_ORDER = (
    "back_to_lobby",
    "continue",
    "still_here_ready",
    "back_to_lobby",
    "pass_battle",
    "back_to_lobby",
)

_SINCE = datetime(2026, 11, 6, 10, 33, tzinfo=timezone.utc)
_GAMES = (
    (datetime(2026, 11, 13, 17, 12, tzinfo=timezone.utc), 9),
    (datetime(2026, 11, 13, 16, 48, tzinfo=timezone.utc), 12),
    (datetime(2026, 11, 13, 16, 21, tzinfo=timezone.utc), 1),
    (datetime(2026, 11, 12, 21, 5, tzinfo=timezone.utc), 0),
    (datetime(2026, 11, 9, 20, 40, tzinfo=timezone.utc), 7),
)
CHESTS = {
    "off": ChestsState(Summary(0, 0, None, None, recording=False, replays=False, failed=False)),
    "reading": ChestsState(None),
    "none": ChestsState(Summary(0, 0, None, None, recording=False, replays=True, failed=False)),
    "counted": ChestsState(
        Summary(137, 18, 9, _SINCE, recording=False, replays=True, failed=False, recent=_GAMES)
    ),
    "recording": ChestsState(
        Summary(137, 18, 9, _SINCE, recording=True, replays=True, failed=False, recent=_GAMES)
    ),
    "failed": ChestsState(
        Summary(137, 18, 9, _SINCE, recording=False, replays=True, failed=True, recent=_GAMES)
    ),
    "first-game": ChestsState(
        Summary(9, 1, 9, _GAMES[0][0], False, replays=True, failed=False, recent=_GAMES[:1])
    ),
    "counting": ChestsState(
        Summary(137, 18, 9, _SINCE, False, replays=True, failed=False, recent=_GAMES, counting=True)
    ),
    "interrupted": ChestsState(
        Summary(137, 18, 9, _SINCE, False, True, failed=False, recent=_GAMES, interrupted=True)
    ),
    "down": ChestsState(None, "lecteur de replays introuvable"),
    "big": ChestsState(
        Summary(12840, 412, 9, _SINCE, recording=False, replays=True, failed=False, recent=_GAMES)
    ),
}

_STUCK = f"«{NBSP}Retour au salon{NBSP}» toujours visible après 3 clics"

SCENES = [
    Scene("pill-active", "active", "pill"),
    Scene("pill-paused", "paused", "pill"),
    Scene("pill-waiting", "no_game", "pill"),
    Scene("pill-stuck", "stuck", "pill", detail=_STUCK),
    Scene("pill-stopped", "stopped", "pill", detail="ScreenCapture indisponible"),
    Scene("pill-big-total", "active", "pill", chests="big"),
    Scene("pill-reading", "active", "pill", chests="reading"),
    Scene("pill-no-chests", "active", "pill", chests="disabled"),
    Scene("card-first", "paused", "card", chests="off"),
    Scene("card-paused", "paused", "card"),
    Scene("card-active", "active", "card", recent=5),
    Scene("card-two-clicks", "active", "card", recent=2),
    *(
        Scene(f"card-chests-{state}", "paused", "card", chests=state)
        for state in (
            "reading",
            "none",
            "first-game",
            "recording",
            "counting",
            "interrupted",
            "failed",
            "down",
        )
    ),
    Scene("card-stuck", "stuck", "card", detail=_STUCK, recent=5),
    Scene(
        "card-covered",
        "covered",
        "card",
        detail=f"Le panneau couvre le bouton «{NBSP}Retour au salon{NBSP}»",
        recent=5,
    ),
    Scene("card-background", "background", "card", recent=5),
    Scene(
        "card-mouse",
        "mouse",
        "card",
        detail=f"Clic sur «{NBSP}Retour au salon{NBSP}» en attente",
        recent=5,
    ),
    Scene("card-capture", "capture", "card", detail="aucun écran à capturer"),
    Scene("card-stopped", "stopped", "card", detail="ScreenCapture indisponible"),
    Scene("card-dry-run", "active", "card", dry_run=True, recent=3),
    Scene("card-hover-action", "active", "card", hover="action"),
    Scene("card-hover-activate", "paused", "card", hover="action"),
    Scene("card-hover-quit", "paused", "card", hover="quit"),
    Scene("card-hover-settings", "active", "card", hover="settings"),
    Scene("card-update", "active", "card", recent=2, update="available"),
    Scene("card-update-failed", "active", "card", update="failed"),
    Scene("card-no-chests", "active", "card", recent=2, chests="disabled"),
    Scene("settings", "active", "settings", recent=3),
    Scene("settings-opacity-40", "active", "settings", opacity=40),
    Scene("settings-anti-afk", "active", "settings", anti_afk=True),
    Scene("settings-no-anti-afk", "active", "settings", anti_afk=None),
    Scene(
        "settings-not-yet",
        "active",
        "settings",
        still_here="not_yet",
        answers=("not_yet", "ignore"),
        hover="ignore",
    ),
    Scene("settings-short-card", "paused", "settings", chests="disabled"),
]


class DemoController:
    """Controller du panneau sans moteur : la scène courante dit tout."""

    def __init__(self, cfg: config_mod.Config) -> None:
        self.cfg = cfg
        self.scene = SCENES[0]
        self.started_at = time.time() - 47 * 60
        self.closing = False

    def status(self) -> Status:
        scene = self.scene
        now = time.time()
        recent = tuple(
            ClickRecord(now - 95 * i, slug, _LABELS[slug])
            for i, slug in enumerate(_ORDER[: scene.recent])
        )
        return Status(
            phase=scene.phase,
            detail=scene.detail,
            started_at=self.started_at,
            actions=len(recent),
            per_state={},
            recent=recent,
            still_here=scene.still_here,
            dry_run=scene.dry_run,
        )

    def toggle(self) -> None:
        pass

    def quit(self) -> None:
        self.closing = True

    def check_stall(self) -> None:
        pass

    def still_here_answers(self) -> list[str]:
        return list(self.scene.answers)

    def set_still_here(self, answer: str) -> None:
        self.scene.still_here = answer

    def chests(self) -> ChestsState | None:
        return CHESTS.get(self.scene.chests)

    def anti_afk(self) -> bool | None:
        return self.scene.anti_afk

    def set_anti_afk(self, enabled: bool) -> None:
        self.scene.anti_afk = enabled


class DemoUpdates:
    """Mises à jour simulées : la scène dit la phase, rien ne se télécharge."""

    def __init__(self, controller: DemoController) -> None:
        self.controller = controller

    def state(self) -> UpdateState:
        error = "signature invalide" if self.controller.scene.update == "failed" else ""
        return UpdateState(self.controller.scene.update, "0.6.0", error=error)  # type: ignore[arg-type]

    def download(self) -> None:
        pass


def _box(widget: QWidget, view: QWidget, offset: QPoint) -> list[int] | None:
    if not widget.isVisibleTo(view) and widget is not view:
        return None
    at = widget.mapTo(view, QPoint(0, 0)) + offset if widget is not view else offset
    return [at.x(), at.y(), widget.width(), widget.height()]


def _geometry(panel: Panel, offset: QPoint) -> dict[str, object]:
    view = panel.card if panel.card_shown else panel.pill
    named: dict[str, QWidget] = {
        "pill": panel.pill,
        "pill_dot": panel.pill_dot,
        "pill_text": panel.pill_text,
        "pill_sep": panel.pill_sep,
        "pill_icon": panel.pill_icon,
        "pill_total": panel.pill_total,
        "card": panel.card,
        "header": panel.header,
        "dot": panel.dot,
        "title": panel.title,
        "action": panel.action,
        "bot_body": panel.bot_body,
        "notice": panel.notice,
        "s_recent": panel.s_recent,
        "empty": panel.empty,
        "chests_rule": panel.chests_rule,
        "chests_row": panel.chests_row,
        "chests_dot": panel.chests_dot,
        "chests_state": panel.chests_state,
        "chests_value": panel.chests_value,
        "chests_body": panel.chests_body,
        "chests_notice": panel.chests_notice,
        "s_games": panel.s_games,
        "footer_rule": panel.footer_rule,
        "update_box": panel.update_box,
        "update_notice": panel.update_notice,
        "footer": panel.footer,
        "settings_button": panel.settings_button,
        "quit": panel.quit_button,
    }
    boxes: dict[str, object] = {}
    for name, widget in named.items():
        inside = widget is view or view.isAncestorOf(widget)
        boxes[name] = _box(widget, view, QPoint(0, 0)) if inside else None
    boxes["recent_rows"] = [_box(row, view, QPoint(0, 0)) for row, _, _ in panel.recent]
    boxes["game_rows"] = [_box(row, view, QPoint(0, 0)) for row, _, _ in panel.games]
    if panel.settings_open:
        pop = panel.popover
        settings: dict[str, QWidget] = {
            "popover": pop,
            "s_rest": panel.s_rest,
            "rest_choice": panel.rest_choice,
            "s_opacity": panel.s_opacity,
            "opacity": panel.opacity,
            "s_dialog": panel.s_dialog,
            "segmented": panel.segmented,
            "dialog_hint": panel.dialog_hint,
            "hint": panel.hint,
        }
        for name, widget in settings.items():
            boxes[name] = _box(widget, pop, offset)
    return {"view": [view.width(), view.height()], "boxes": boxes}


BACKDROP_AT = (24, 120)  # coin du panneau sur l'image de fond (son coin par défaut)


def _glass(panel: Panel, backdrop: Image.Image) -> None:
    """Le verre de chaque surface, flouté depuis l'image de fond, à sa place dans la capture."""
    view = panel.card if panel.card_shown else panel.pill
    surfaces = [(view, 0, 0)]
    if panel.settings_open:
        pop = panel.popover
        height = max(view.height(), pop.height())
        surfaces = [
            (view, 0, height - view.height()),
            (pop, view.width() + panel.px(POPOVER_GAP), height - pop.height()),
        ]
    x0, y0 = BACKDROP_AT
    for surface, dx, dy in surfaces:
        x, y = x0 + dx, y0 + dy
        zone = backdrop.crop((x, y, x + surface.width(), y + surface.height()))
        surface.backdrop = to_qimage(blur(zone))


def _on_backdrop(image: QPixmap, backdrop: Image.Image, margin: int) -> Image.Image:
    """La capture posée sur l'image de fond, avec une marge autour."""
    x0, y0 = BACKDROP_AT
    box = (x0 - margin, y0 - margin, x0 + image.width() + margin, y0 + image.height() + margin)
    canvas = backdrop.crop(box).convert("RGBA")
    shot = image.toImage().convertToFormat(image.toImage().Format.Format_RGBA8888)
    raw = bytes(shot.constBits())
    layer = Image.frombuffer("RGBA", (shot.width(), shot.height()), raw, "raw", "RGBA", 0, 1)
    canvas.alpha_composite(layer, (margin, margin))
    return canvas


def _shot(panel: Panel) -> tuple[QPixmap, QPoint]:
    """La vue affichée ; avec les réglages, posés à droite de la carte, bas alignés."""
    view = panel.card if panel.card_shown else panel.pill
    image = view.grab()
    if not panel.settings_open:
        return image, QPoint()
    pop = panel.popover.grab()
    gap = panel.px(POPOVER_GAP)
    width = image.width() + gap + pop.width()
    height = max(image.height(), pop.height())
    canvas = QPixmap(width, height)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap(0, height - image.height(), image)
    offset = QPoint(image.width() + gap, height - pop.height())
    painter.drawPixmap(offset, pop)
    painter.end()
    return canvas, offset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(".impeccable/review"))
    parser.add_argument("--only", help="ne capturer que les scènes dont le nom contient ce texte")
    parser.add_argument(
        "--backdrop",
        type=Path,
        help="image de jeu en 1920 sur 1080 : le panneau y est posé, son verre flouté depuis elle",
    )
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        return _shoot_all(args)
    except Exception:  # lancé par pythonw : l'erreur doit rester lisible
        (args.out / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise


def _shoot_all(args: argparse.Namespace) -> int:
    qt_app()
    backdrop = Image.open(args.backdrop).convert("RGB") if args.backdrop else None
    cfg = config_mod.load_config(None, use_local=False)
    controller = DemoController(cfg)
    updates = DemoUpdates(controller)
    panel = Panel(
        controller,
        cfg.panel,
        persist=False,
        updates=updates,  # type: ignore[arg-type]
        follow_cursor=False,
        show=False,
    )
    buttons = {
        "action": panel.action,
        "settings": panel.settings_button,
        "quit": panel.quit_button,
    }
    for scene in (s for s in SCENES if not args.only or args.only in s.name):
        controller.scene = scene
        panel.rest = "pill"
        panel.expanded = scene.view != "pill"
        panel.rest_opacity = scene.opacity
        panel._close_settings()
        for button in buttons.values():
            button.set_state(hover=False, pressed=False)
        panel.segmented.set_hover("")
        panel._render(controller.status())
        if scene.view == "settings":
            panel._toggle_settings()
        if scene.hover in buttons:
            buttons[scene.hover].set_state(hover=True)
        elif scene.hover:
            panel.segmented.set_hover(scene.hover)
        if backdrop is not None:
            _glass(panel, backdrop)
        image, offset = _shot(panel)
        if backdrop is not None:
            _on_backdrop(image, backdrop, panel.px(24)).save(args.out / f"{scene.name}.png")
        else:
            image.save(str(args.out / f"{scene.name}.png"))
        (args.out / f"{scene.name}.json").write_text(
            json.dumps(_geometry(panel, offset), indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
