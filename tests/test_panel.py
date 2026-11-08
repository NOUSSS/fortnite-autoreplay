"""Logique du panneau sans fenêtre : textes, géométrie, garde du premier plan, totaux."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fortnite_auto_replay import config as config_mod
from fortnite_auto_replay.engine import Status
from fortnite_auto_replay.panel import (
    NBSP,
    EngineController,
    Panel,
    clamp,
    counters_from,
    default_home,
    duration,
    explain,
    focus_step,
    give_back_targets,
    header_detail,
    missing_hint,
    mouse_through,
    number,
    plural,
    primary_label,
    replace_other_panels,
)


def status(phase: str = "active", **changes: Any) -> Status:
    fields: dict[str, Any] = {
        "phase": phase,
        "detail": "",
        "started_at": time.time(),
        "actions": 0,
        "per_state": {},
        "recent": (),
        "still_here": "ready",
        "dry_run": False,
    }
    fields.update(changes)
    return Status(**fields)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (-5, "0 min"),
        (59, "0 min"),
        (61, "1 min"),
        (3600 + 5 * 60, "1 h 05"),
        (9 * 3600 + 59 * 60, "9 h 59"),
        (31 * 3600 + 10, "31 h"),
    ],
)
def test_duration(seconds: float, text: str) -> None:
    assert duration(seconds) == text.replace(" ", NBSP)


def test_number_and_plural() -> None:
    assert number(1284) == f"1{NBSP}284"
    assert plural(0, "clic") == f"0{NBSP}clic"
    assert plural(1, "clic") == f"1{NBSP}clic"
    assert plural(2, "session") == f"2{NBSP}sessions"


@pytest.mark.parametrize(
    ("phase", "started", "label"),
    [
        ("paused", False, "Lancer"),
        ("paused", True, "Reprendre"),
        ("stuck", True, "Reprendre"),
        ("errors", True, "Reprendre"),
        ("active", True, "Pause"),
        ("no_game", True, "Pause"),
        ("covered", True, "Pause"),
        ("stopped", True, "Quitter"),
    ],
)
def test_primary_label_says_what_it_does(phase: str, started: bool, label: str) -> None:
    assert primary_label(phase, started) == label  # type: ignore[arg-type]


def test_header_detail_only_with_clicks() -> None:
    assert header_detail(status("active")) == ""
    assert header_detail(status("active", actions=3)) == f"3{NBSP}clics"
    assert header_detail(status("paused", actions=1)) == f"1{NBSP}clic"
    assert header_detail(status("no_game", actions=3)) == ""


def test_explain_is_quiet_when_all_is_well() -> None:
    assert explain(status("active")) is None
    assert explain(status("paused")) is None


@pytest.mark.parametrize(
    ("phase", "severity"),
    [
        ("stuck", "critical"),
        ("errors", "critical"),
        ("capture", "critical"),
        ("covered", "caution"),
        ("no_game", "caution"),
        ("hidden", "caution"),
        ("background", "caution"),
        ("mouse", "info"),
        ("stopped", "info"),
    ],
)
def test_explain_every_problem(phase: str, severity: str) -> None:
    found = explain(status(phase, detail="détail"))
    assert found is not None
    assert found[0] == severity
    assert found[1].endswith(".")
    assert "'" not in found[1]  # apostrophe typographique dans l'interface


def test_explain_covered_by_the_panel_asks_to_move_it() -> None:
    own = explain(status("covered", detail="Le panneau couvre le bouton « Rejouer »"))
    other = explain(status("covered", detail="Discord.exe couvre le bouton « Rejouer »"))
    assert own is not None and own[1].endswith("Déplacez le panneau.")
    assert other is not None and "Déplacez" not in other[1]


def test_explain_dry_run() -> None:
    found = explain(status("active", dry_run=True))
    assert found is not None and found[0] == "info" and found[1].startswith("Simulation")


def test_explain_hides_raw_exceptions() -> None:
    capture = explain(status("capture", detail="mss: XGetImage() failed"))
    stopped = explain(status("stopped", detail="RuntimeError: boom"))
    assert capture is not None and "mss" not in capture[1]
    assert stopped is not None and "boom" not in stopped[1]
    assert stopped[1].endswith("Quittez le panneau puis relancez-le.")


def test_missing_hint_names_the_answers_without_template() -> None:
    assert missing_hint(frozenset()) == ""
    assert missing_hint(frozenset({"ready"})) == (
        f"Pas de modèle pour «{NBSP}Prêt{NBSP}»{NBSP}: voir docs/TEMPLATES.md."
    )
    both = missing_hint(frozenset({"ready", "not_yet"}))
    assert f"«{NBSP}Prêt{NBSP}» et «{NBSP}Pas encore{NBSP}»" in both


def test_counters_group_the_dialog_answers() -> None:
    rows = counters_from(config_mod.load_config(use_local=False))
    assert rows[-1] == ("Toujours là", ("still_here_ready", "still_here_not_yet"))
    assert all(len(slugs) == 1 for _, slugs in rows[:-1])


LEFT_SCREEN = (-1920, 114, 0, 1194)


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        (-1900, 200, (-1900, 200)),  # déjà dedans
        (-100, 200, (-320, 200)),  # déborde à droite
        (-1900, 900, (-1900, 694)),  # déborde en bas
        (-3000, 0, (-1920, 114)),  # hors de l'écran en haut à gauche
    ],
)
def test_clamp_keeps_the_window_on_its_screen(x: int, y: int, expected: tuple[int, int]) -> None:
    assert clamp(x, y, 320, 500, LEFT_SCREEN) == expected


def test_clamp_keeps_the_header_visible_when_too_tall() -> None:
    assert clamp(0, 0, 320, 2000, (0, 0, 1920, 1080)) == (0, 0)


def test_default_home_sits_under_the_lobby_bar() -> None:
    assert default_home((100, 50, 2020, 1130), 1.0) == (124, 170)
    assert default_home((0, 0, 1920, 1080), 1.5) == (36, 180)


def test_focus_step_remembers_other_windows() -> None:
    assert focus_step(42, ours=False, other=0) == (0, 42)
    assert focus_step(0, ours=False, other=42) == (0, 42)


def test_focus_step_gives_the_foreground_back() -> None:
    assert focus_step(99, ours=True, other=42) == (42, 42)
    assert focus_step(99, ours=True, other=0) == (0, 0)


class _Engine:
    """EngineController ne touche pas au moteur à sa construction."""


def test_totals_add_the_current_session_except_in_simulation(tmp_path: Path) -> None:
    stats = tmp_path / "sessions.jsonl"
    lines = [{"actions": 10, "uptime_s": 3600}, {"actions": 5, "uptime_s": 1800}]
    stats.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    cfg = config_mod.load_config(use_local=False, overrides={"engine": {"stats_file": str(stats)}})
    controller = EngineController(_Engine(), cfg, None)  # type: ignore[arg-type]
    started = time.time() - 600

    sessions, clicks, seconds = controller.totals(status(actions=3, started_at=started))
    assert (sessions, clicks) == (3, 18)
    assert seconds == pytest.approx(3600 + 1800 + 600, abs=5)

    dry = controller.totals(status(actions=3, started_at=started, dry_run=True))
    assert dry == (2, 15, pytest.approx(5400))


# --- premier plan rendu, souris traversante, panneau unique ----------------------
GAME, OTHER, PANEL = 7, 42, 99
GAME_PID, OTHER_PID, OWN_PID = 70, 420, 990


class FakeWin32:
    """Premier plan, curseur et fenêtres simulés ; ``alive`` = fenêtres qui existent."""

    def __init__(self, foreground: int = PANEL) -> None:
        self.fg = foreground
        self.alive = {GAME, OTHER, PANEL}
        self.cursor = (500, 500, False, False)  # x, y, caché, bloqué
        self.rect = (0, 0, 320, 44)
        self.through: list[bool] = []

    def foreground(self) -> int:
        return self.fg

    def pid(self, hwnd: int) -> int:
        return {GAME: GAME_PID, OTHER: OTHER_PID, PANEL: OWN_PID}.get(hwnd, 0)

    def ours(self, hwnd: int) -> bool:
        return hwnd == PANEL

    def give_back(self, hwnd: int) -> bool:
        if hwnd in self.alive and hwnd != PANEL:
            self.fg = hwnd
            return True
        return False

    def cursor_state(self) -> tuple[int, int, bool, bool]:
        return self.cursor

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        return self.rect

    def set_mouse_through(self, hwnd: int, through: bool) -> None:
        self.through.append(through)


def guard(win32: FakeWin32, *, other: int = OTHER, game: int = GAME) -> SimpleNamespace:
    """Juste ce qu'utilisent les gardes du panneau, sans fenêtre Tk."""
    return SimpleNamespace(
        win32=win32,
        hwnd=PANEL,
        _other=other,
        _game=lambda: game,
        _holding=False,
        _through=False,
        _drag=None,
    )


def test_give_back_targets_prefer_the_previous_window_then_the_game() -> None:
    assert give_back_targets(OTHER, GAME) == [OTHER, GAME]
    assert give_back_targets(0, GAME) == [GAME]
    assert give_back_targets(GAME, GAME) == [GAME]
    assert give_back_targets(0, 0) == []


def test_foreground_goes_back_to_the_previous_window() -> None:
    win32 = FakeWin32()
    panel = guard(win32)
    Panel._guard_focus(panel)  # type: ignore[arg-type]
    assert win32.fg == OTHER


def test_foreground_goes_to_the_game_when_the_previous_window_is_gone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    win32 = FakeWin32()
    win32.alive.discard(OTHER)  # console du lanceur, déjà fermée
    panel = guard(win32)
    Panel._guard_focus(panel)  # type: ignore[arg-type]
    assert win32.fg == GAME
    assert panel._other == GAME
    assert "rendu au jeu" in caplog.text


def test_stuck_foreground_is_reported_once(caplog: pytest.LogCaptureFixture) -> None:
    win32 = FakeWin32()
    win32.alive = {PANEL}
    panel = guard(win32, game=0)
    for _ in range(5):
        Panel._guard_focus(panel)  # type: ignore[arg-type]
    assert caplog.text.count("ne peut pas le rendre") == 1
    win32.fg = OTHER  # l'utilisateur clique ailleurs : un nouvel épisode sera signalé
    Panel._guard_focus(panel)  # type: ignore[arg-type]
    assert panel._holding is False


@pytest.mark.parametrize(
    "case",  # jeu devant, curseur caché, bloqué, sur le panneau, état d'avant -> attendu
    [
        (False, True, True, False, True, False),  # jeu derrière : panneau cliquable
        (True, False, False, False, True, False),  # menus : curseur visible
        (True, True, False, False, False, True),  # partie : curseur caché
        (True, False, True, True, False, True),  # partie : curseur bloqué dans le jeu
        (True, False, False, True, True, True),  # sur le panneau : on garde l'état
        (True, False, False, True, False, False),
    ],
)
def test_mouse_through(case: tuple[bool, bool, bool, bool, bool, bool]) -> None:
    *signals, expected = case
    assert mouse_through(*signals) is expected


def test_mouse_goes_through_the_panel_during_a_match() -> None:
    win32 = FakeWin32(foreground=GAME)
    panel = guard(win32)
    win32.cursor = (900, 500, True, False)  # partie : curseur caché, hors du panneau
    Panel._guard_mouse(panel)  # type: ignore[arg-type]
    win32.cursor = (100, 20, False, False)  # passé sous le panneau, qui montre sa flèche
    Panel._guard_mouse(panel)  # type: ignore[arg-type]
    assert win32.through == [True]
    win32.cursor = (900, 500, False, False)  # menu ouvert, curseur visible ailleurs
    Panel._guard_mouse(panel)  # type: ignore[arg-type]
    assert win32.through == [True, False]


def test_mouse_never_goes_through_when_the_game_is_behind() -> None:
    win32 = FakeWin32(foreground=OTHER)  # on travaille ailleurs
    win32.cursor = (900, 500, True, True)
    panel = guard(win32)
    Panel._guard_mouse(panel)  # type: ignore[arg-type]
    assert win32.through == []


class FakeFinder:
    """Autres panneaux ; ``busy`` : essais du verrou refusés avant qu'il se libère."""

    def __init__(
        self,
        panels: list[tuple[int, int]],
        *,
        closes: bool = True,
        busy: int = 0,
        appears: tuple[int, int] | None = None,
    ) -> None:
        self.panels = list(panels)
        self.closes = closes
        self.busy = busy
        self.appears = appears  # fenêtre d'un panneau qui démarre, créée au 1er refus
        self.closed: list[int] = []
        self.locked = False

    def other_panels(self) -> list[tuple[int, int]]:
        return list(self.panels)

    def close_and_wait(self, hwnd: int, pid: int, timeout_s: float) -> bool:
        self.closed.append(pid)
        if self.closes:
            self.panels.remove((hwnd, pid))
        return self.closes

    def take_lock(self, wait_s: float) -> bool:
        if self.busy:
            self.busy -= 1
            if self.appears is not None:
                self.panels.append(self.appears)
                self.appears = None
            return False
        self.locked = True
        return True


def replace(finder: FakeFinder, timeout_s: float = 8.0) -> None:
    replace_other_panels(finder, timeout_s, sleep=lambda _s: None)


def test_launching_a_panel_closes_the_one_already_open() -> None:
    finder = FakeFinder([(11, 20664)])
    replace(finder)
    assert finder.closed == [20664]
    assert finder.locked


def test_launch_is_cancelled_if_the_old_panel_stays() -> None:
    finder = FakeFinder([(11, 20664)], closes=False)
    with pytest.raises(RuntimeError, match="20664"):
        replace(finder, timeout_s=0.0)
    assert finder.closed == [20664]  # une seule demande, pas une par tour


def test_nothing_to_close() -> None:
    finder = FakeFinder([])
    replace(finder)
    assert finder.closed == []
    assert finder.locked


def test_waits_for_the_old_panel_to_release_the_lock() -> None:
    # fenêtre fermée, mais le moteur de l'ancien enregistre encore sa session
    finder = FakeFinder([(11, 20664)], busy=2)
    replace(finder)
    assert finder.locked


def test_a_panel_still_starting_is_closed_once_its_window_appears() -> None:
    # deux lancements rapprochés : l'autre tient le verrou sans fenêtre encore
    finder = FakeFinder([], busy=1, appears=(12, 31000))
    replace(finder)
    assert finder.closed == [31000]
    assert finder.locked


def test_a_panel_starting_without_window_is_reported() -> None:
    finder = FakeFinder([], busy=99)
    with pytest.raises(RuntimeError, match="démarrer"):
        replace(finder, timeout_s=0.0)


# --- mises à jour -----------------------------------------------------------------
def test_update_notice_follows_the_update() -> None:
    from fortnite_auto_replay.panel import update_notice  # noqa: PLC0415
    from fortnite_auto_replay.updater import UpdateState  # noqa: PLC0415

    assert update_notice(UpdateState()) is None
    severity, message, action = update_notice(UpdateState("available", "0.3.0"))  # type: ignore[misc]
    assert (severity, action) == ("info", "Mettre à jour")
    assert "0.3.0" in message
    downloading = update_notice(UpdateState("downloading", "0.3.0", progress=0.42))
    assert downloading is not None
    assert downloading[1].endswith(f"42{NBSP}%")
    assert downloading[2] == ""  # pas de bouton pendant le téléchargement


def test_update_failure_hides_technical_detail() -> None:
    from fortnite_auto_replay.panel import update_notice  # noqa: PLC0415
    from fortnite_auto_replay.updater import UpdateState  # noqa: PLC0415

    failed = update_notice(
        UpdateState("failed", "0.3.0", error="téléchargement interrompu (<urlopen error 11001>)")
    )
    assert failed is not None
    severity, message, action = failed
    assert (severity, action) == ("caution", "Réessayer")
    assert "urlopen" not in message
    assert "téléchargement interrompu" in message
