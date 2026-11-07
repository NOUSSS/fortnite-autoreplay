"""Logique du panneau sans fenêtre : textes, géométrie, garde du premier plan, totaux."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from fortnite_auto_replay import config as config_mod
from fortnite_auto_replay.engine import Status
from fortnite_auto_replay.panel import (
    NBSP,
    EngineController,
    clamp,
    counters_from,
    default_home,
    duration,
    explain,
    focus_step,
    header_detail,
    missing_hint,
    number,
    plural,
    primary_label,
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
        ("background", "caution"),
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
