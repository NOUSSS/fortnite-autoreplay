"""Captures du panneau dans chaque état, pour la relecture du design.

Le panneau tourne sur un moteur simulé (aucune capture, aucun clic), sur un
écran secondaire s'il y en a un, visible des captures et sans rien
enregistrer dans ``config/local.json``. Pour chaque scène : une image PNG et
la position des blocs (JSON) pour mesurer les écarts.

À lancer par WMI, console masquée (recette dans REVIEW.md), quand personne ne
joue : la création de la fenêtre prend le premier plan un instant, et la garde
du panneau le rend aussitôt.

``foreground.json`` dit si la fenêtre au premier plan a changé pendant les captures.
"""

from __future__ import annotations

import argparse
import json
import time
import tkinter as tk
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import mss
import mss.tools

from .. import config as config_mod
from ..chests import Summary
from ..engine import ClickRecord, Phase, Status
from ..panel import ChestsState, Panel, Win32
from ..updater import UpdateState
from ..window import ensure_dpi_awareness

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


class DemoUpdates:
    """Mises à jour simulées : la scène dit la phase, rien ne se télécharge."""

    def __init__(self, controller: DemoController) -> None:
        self.controller = controller

    def state(self) -> UpdateState:
        error = "signature invalide" if self.controller.scene.update == "failed" else ""
        return UpdateState(self.controller.scene.update, "0.6.0", error=error)  # type: ignore[arg-type]

    def download(self) -> None:
        pass


def _box(widget: tk.Misc, origin: tuple[int, int]) -> list[int] | None:
    if not widget.winfo_ismapped():
        return None
    return [
        widget.winfo_rootx() - origin[0],
        widget.winfo_rooty() - origin[1],
        widget.winfo_width(),
        widget.winfo_height(),
    ]


def _geometry(panel: Panel) -> dict[str, object]:
    origin = (panel.root.winfo_rootx(), panel.root.winfo_rooty())
    named: dict[str, tk.Misc] = {
        "pill": panel.pill,
        "pill_inner": panel.pill_inner,
        "pill_dot": panel.pill_dot,
        "pill_text": panel.pill_text,
        "pill_sep": panel.pill_sep,
        "pill_icon": panel.pill_icon,
        "pill_total": panel.pill_total,
        "header": panel.header,
        "dot": panel.dot,
        "title": panel.title,
        "state": panel.state,
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
        "popover": panel.settings,
        "s_rest": panel.s_rest,
        "rest_choice": panel.rest_choice,
        "s_opacity": panel.s_opacity,
        "opacity": panel.opacity,
        "s_dialog": panel.s_dialog,
        "segmented": panel.segmented,
        "dialog_hint": panel.dialog_hint,
        "hint": panel.hint,
    }
    boxes = {name: _box(widget, origin) for name, widget in named.items()}
    sections = (panel.s_recent, panel.s_games, panel.s_rest, panel.s_dialog)
    boxes["headings"] = [_box(section.winfo_children()[0], origin) for section in sections]
    boxes["recent_rows"] = [_box(row, origin) for row, _, _ in panel.recent]
    boxes["game_rows"] = [_box(row, origin) for row, _, _ in panel.games]
    return {
        "window": [panel.root.winfo_width(), panel.root.winfo_height()],
        "position": list(panel._at),
        "boxes": boxes,
    }


def _target_monitor(index: int | None) -> dict[str, int]:
    with mss.MSS() as screens:
        monitors = screens.monitors[1:]
    if index is not None:
        return monitors[index]
    secondary = [m for m in monitors if not m.get("is_primary")]
    return (secondary or monitors)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(".impeccable/review"))
    parser.add_argument("--monitor", type=int, help="index de l'écran (0 = premier)")
    parser.add_argument("--only", help="ne capturer que les scènes dont le nom contient ce texte")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        return _shoot_all(args)
    except Exception:  # lancé par pythonw : l'erreur doit rester lisible
        (args.out / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise


def _shoot_all(args: argparse.Namespace) -> int:
    ensure_dpi_awareness()
    win32 = Win32()
    before = win32.foreground()
    cfg = config_mod.load_config(None, use_local=False)
    cfg.panel.exclude_from_capture = False  # les captures doivent le voir
    cfg.panel.rest_opacity = 100  # captures opaques : l'opacité se voit au survol réel
    monitor = _target_monitor(args.monitor)
    home = (monitor["left"] + 48, monitor["top"] + 48)

    controller = DemoController(cfg)
    root = tk.Tk()
    updates = DemoUpdates(controller)
    panel = Panel(
        root,
        controller,
        cfg.panel,
        home=home,
        persist=False,
        win32=win32,
        updates=updates,  # type: ignore[arg-type]
        follow_cursor=False,
    )
    scenes = [s for s in SCENES if not args.only or args.only in s.name]
    report: dict[str, object] = {
        "before": before,
        "panel": panel.hwnd,
        "ex_style": hex(win32.ex_style(panel.hwnd)),
        "noactivate": bool(win32.ex_style(panel.hwnd) & 0x08000000),
        "settings": panel.settings_hwnd,
        "settings_noactivate": bool(win32.ex_style(panel.settings_hwnd) & 0x08000000),
    }

    def shoot(index: int) -> None:
        if index >= len(scenes):
            report["after"] = win32.foreground()
            report["foreground_changed"] = report["after"] != before
            report["panel_was_foreground"] = report["after"] == panel.hwnd
            (args.out / "foreground.json").write_text(json.dumps(report, indent=2))
            root.quit()
            return
        scene = scenes[index]
        controller.scene = scene
        panel.rest = "pill"
        panel.expanded = scene.view != "pill"
        panel.rest_opacity = scene.opacity
        panel._close_settings()
        panel._shape = ()
        panel._settings_shape = ()
        panel._render(controller.status())
        if scene.view == "settings":
            panel._toggle_settings()
        buttons = {
            "action": panel.action,
            "settings": panel.settings_button,
            "quit": panel.quit_button,
        }
        for button in buttons.values():
            button._set(hover=False, pressed=False)
        panel.segmented._set_hover("")
        if scene.hover in buttons:
            buttons[scene.hover]._set(hover=True)
        elif scene.hover:
            panel.segmented._set_hover(scene.hover)
        root.update()
        root.after(350, lambda: capture(index))

    def capture(index: int) -> None:
        scene = scenes[index]
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        right, bottom = x + root.winfo_width(), y + root.winfo_height()
        if panel.settings_open:  # la carte et ses réglages sur la même image
            pop = panel.settings
            pop_x, pop_y = pop.winfo_rootx(), pop.winfo_rooty()
            x, y = min(x, pop_x), min(y, pop_y)
            right = max(right, pop_x + pop.winfo_width())
            bottom = max(bottom, pop_y + pop.winfo_height())
        region = {"left": x, "top": y, "width": right - x, "height": bottom - y}
        with mss.MSS() as screens:
            shot = screens.grab(region)
        mss.tools.to_png(shot.rgb, shot.size, output=str(args.out / f"{scene.name}.png"))
        (args.out / f"{scene.name}.json").write_text(
            json.dumps(_geometry(panel), indent=2), encoding="utf-8"
        )
        report[f"foreground_{scene.name}"] = win32.foreground()
        shoot(index + 1)

    root.after(400, lambda: shoot(0))
    root.mainloop()
    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
