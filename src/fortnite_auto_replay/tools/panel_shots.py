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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import mss
import mss.tools

from .. import config as config_mod
from ..chests import Summary
from ..engine import ClickRecord, Phase, Status
from ..panel import ChestsState, Panel, Win32, counters_from
from ..window import ensure_dpi_awareness

NBSP = "\u00a0"


@dataclass(slots=True)
class Scene:
    name: str
    phase: Phase
    collapsed: bool
    detail: str = ""
    actions: int = 0
    per_state: dict[str, int] = field(default_factory=dict)
    recent: int = 0
    still_here: str = "ready"
    dry_run: bool = False
    started: bool = True
    hover: str = ""  # "primary", "chevron", "quit" ou une réponse du dialogue
    answers: tuple[str, ...] = ("ready", "not_yet", "ignore")
    chests: str = "counted"  # état de la section des coffres (voir CHESTS)


_BUSY = {"back_to_lobby": 31, "continue": 12, "pass_battle": 9, "ready": 2, "still_here_ready": 1}
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
CHESTS = {
    "off": ChestsState(Summary(0, 0, None, None, recording=False, replays=False, failed=False)),
    "reading": ChestsState(None),
    "none": ChestsState(Summary(0, 0, None, None, recording=False, replays=True, failed=False)),
    "counted": ChestsState(
        Summary(137, 18, 9, _SINCE, recording=False, replays=True, failed=False)
    ),
    "recording": ChestsState(
        Summary(137, 18, 9, _SINCE, recording=True, replays=True, failed=False)
    ),
    "failed": ChestsState(Summary(137, 18, 9, _SINCE, recording=False, replays=True, failed=True)),
    "down": ChestsState(None, "lecteur de replays introuvable"),
}

SCENES = [
    Scene("collapsed-first", "paused", True, started=False),
    Scene("collapsed-active", "active", True, actions=55, per_state=_BUSY),
    Scene("collapsed-paused", "paused", True, actions=55, per_state=_BUSY),
    Scene("collapsed-waiting", "no_game", True),
    Scene(
        "collapsed-stuck",
        "stuck",
        True,
        detail=f"«{NBSP}Retour au salon{NBSP}» toujours visible après 3 clics",
    ),
    Scene("expanded-first", "paused", False, started=False, chests="off"),
    Scene("expanded-first-chests", "paused", False, started=False),
    *(
        Scene(f"expanded-chests-{state}", "paused", False, started=False, chests=state)
        for state in ("reading", "none", "recording", "failed", "down")
    ),
    Scene("expanded-active", "active", False, actions=55, per_state=_BUSY, recent=5),
    Scene(
        "expanded-two-clicks", "active", False, actions=2, per_state={"back_to_lobby": 2}, recent=2
    ),
    Scene(
        "expanded-stuck",
        "stuck",
        False,
        detail=f"«{NBSP}Retour au salon{NBSP}» toujours visible après 3 clics",
        actions=55,
        per_state=_BUSY,
        recent=5,
    ),
    Scene(
        "expanded-covered",
        "covered",
        False,
        detail=f"Le panneau couvre le bouton «{NBSP}Retour au salon{NBSP}»",
        actions=55,
        per_state=_BUSY,
        recent=5,
    ),
    Scene("expanded-background", "background", False, actions=55, per_state=_BUSY, recent=5),
    Scene("expanded-hidden", "hidden", False, actions=55, per_state=_BUSY, recent=5),
    Scene(
        "expanded-mouse",
        "mouse",
        False,
        detail=f"Clic sur «{NBSP}Retour au salon{NBSP}» en attente",
        actions=55,
        per_state=_BUSY,
        recent=5,
    ),
    Scene("expanded-capture", "capture", False, detail="aucun écran à capturer", recent=0),
    Scene("expanded-stopped", "stopped", False, detail="ScreenCapture indisponible"),
    Scene(
        "expanded-dry-run",
        "active",
        False,
        dry_run=True,
        actions=3,
        recent=3,
        per_state={"back_to_lobby": 3},
    ),
    Scene(
        "expanded-not-yet",
        "active",
        False,
        still_here="not_yet",
        answers=("not_yet", "ignore"),
        hover="ignore",
    ),
    Scene("expanded-hover-primary", "active", False, hover="primary"),
    Scene("expanded-hover-quit", "paused", False, hover="quit"),
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
            actions=scene.actions,
            per_state=dict(scene.per_state),
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

    def totals(self, status: Status) -> tuple[int, int, float]:
        if status.dry_run:
            return 36, 1229, 21 * 3600.0
        return 37, 1229 + status.actions, 21 * 3600.0 + time.time() - status.started_at

    def counters(self) -> list[tuple[str, tuple[str, ...]]]:
        return counters_from(self.cfg)

    def chests(self) -> ChestsState | None:
        return CHESTS.get(self.scene.chests)


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
        "header": panel.header,
        "dot": panel.dot,
        "title": panel.title,
        "detail": panel.detail,
        "primary": panel.primary,
        "chevron": panel.chevron,
        "divider": panel.divider,
        "body": panel.body,
        "notice": panel.notice,
        "s_dialog": panel.s_dialog,
        "segmented": panel.segmented,
        "s_session": panel.s_session,
        "grid": panel.grid,
        "s_recent": panel.s_recent,
        "empty": panel.empty,
        "s_totals": panel.s_totals,
        "totals": panel.totals,
        "s_chests": panel.s_chests,
        "chests": panel.chests,
        "chests_text": panel.chests_text,
        "chests_note": panel.chests_note,
        "chests_notice": panel.chests_notice,
        "footer_rule": panel.footer_rule,
        "footer": panel.footer,
        "quit": panel.quit_button,
        "hint": panel.hint,
    }
    boxes = {name: _box(widget, origin) for name, widget in named.items()}
    sections = (panel.s_dialog, panel.s_session, panel.s_recent, panel.s_totals, panel.s_chests)
    boxes["headings"] = [_box(section.winfo_children()[0], origin) for section in sections]
    boxes["recent_rows"] = [_box(row, origin) for row, _, _ in panel.recent]
    boxes["cells"] = [_box(name.master, origin) for name, _ in panel.cells]
    return {
        "window": [panel.root.winfo_width(), panel.root.winfo_height()],
        "position": list(panel._pos),
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
    cfg.panel.collapsed = True
    monitor = _target_monitor(args.monitor)
    home = (monitor["left"] + 48, monitor["top"] + 48)

    controller = DemoController(cfg)
    root = tk.Tk()
    panel = Panel(root, controller, cfg.panel, home=home, persist=False, win32=win32)
    scenes = [s for s in SCENES if not args.only or args.only in s.name]
    report: dict[str, object] = {
        "before": before,
        "panel": panel.hwnd,
        "ex_style": hex(win32.ex_style(panel.hwnd)),
        "noactivate": bool(win32.ex_style(panel.hwnd) & 0x08000000),
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
        panel._started = scene.started
        panel.collapsed = scene.collapsed
        panel._last = ()
        panel._render(controller.status())
        panel._apply_layout()
        for button in (panel.primary, panel.chevron, panel.quit_button):
            button._set(hover=False, pressed=False)
        panel.segmented._set_hover("")
        if scene.hover == "primary":
            panel.primary._set(hover=True)
        elif scene.hover == "quit":
            panel.quit_button._set(hover=True)
        elif scene.hover:
            panel.segmented._set_hover(scene.hover)
        root.update()
        root.after(350, lambda: capture(index))

    def capture(index: int) -> None:
        scene = scenes[index]
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        region = {"left": x, "top": y, "width": root.winfo_width(), "height": root.winfo_height()}
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
