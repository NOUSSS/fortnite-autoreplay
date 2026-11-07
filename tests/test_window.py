"""Choix de la fenêtre du jeu, région capturée, historique, options CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from fortnite_auto_replay import cli, history
from fortnite_auto_replay.capture import CaptureError, apply_roi
from fortnite_auto_replay.config import WindowConfig
from fortnite_auto_replay.window import WindowCandidate, pick_window

GAME = "FortniteClient-Win64-Shipping.exe"


def _candidate(
    hwnd: int,
    process: str = GAME,
    class_name: str = "UnrealWindow",
    *,
    visible: bool = True,
    rect: tuple[int, int, int, int] | None = (0, 0, 1920, 1080),
) -> WindowCandidate:
    return WindowCandidate(
        hwnd=hwnd,
        pid=hwnd * 10,
        process=process,
        class_name=class_name,
        title="Fortnite",
        visible=visible,
        rect=rect,
    )


def test_pick_window_ignores_launcher_and_hidden_windows() -> None:
    candidates = [
        _candidate(1, "FortniteClient-Win64-Shipping_EAC_EOS.exe", "SDL_app"),
        _candidate(2, class_name="IME", visible=False),
        _candidate(3, "CrashReportClient.exe", "CRC Analytics"),
        _candidate(4),
    ]
    found = pick_window(candidates, WindowConfig())
    assert found is not None
    assert found.hwnd == 4


def test_pick_window_prefers_largest_and_matches_case_insensitively() -> None:
    candidates = [
        _candidate(1, GAME.lower(), rect=(0, 0, 800, 600)),
        _candidate(2, GAME.upper(), rect=(0, 0, 2560, 1440)),
        _candidate(3, rect=None),  # réduite
    ]
    found = pick_window(candidates, WindowConfig())
    assert found is not None
    assert found.hwnd == 2


def test_pick_window_class_filter_can_be_disabled() -> None:
    candidates = [_candidate(1, class_name="Autre")]
    assert pick_window(candidates, WindowConfig()) is None
    found = pick_window(candidates, WindowConfig(window_class=""))
    assert found is not None


def test_pick_window_none_when_absent() -> None:
    assert pick_window([_candidate(1, "explorer.exe", "CabinetWClass")], WindowConfig()) is None


def test_apply_roi() -> None:
    base = (100, 50, 1100, 650)
    assert apply_roi(base, None) == base
    assert apply_roi(base, [10, 20, 300, 200]) == (110, 70, 410, 270)
    # dépasse à droite et en bas : rogné
    assert apply_roi(base, [900, 500, 400, 400]) == (1000, 550, 1100, 650)
    with pytest.raises(CaptureError):
        apply_roi(base, [2000, 0, 100, 100])


def test_summarize_empty_and_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "sessions.jsonl"
    assert history.summarize(history.read_sessions(path))["sessions"] == 0
    path.write_text(
        '{"actions": 3, "uptime_s": 3600, "per_state": {"ready": 3}}\n'
        "pas du json\n"
        '{"actions": 1, "uptime_s": 1800, "per_state": {"ready": 1, "replay": 2}}\n',
        encoding="utf-8",
    )
    summary = history.summarize(history.read_sessions(path))
    assert summary["sessions"] == 2
    assert summary["actions"] == 4
    assert summary["uptime_h"] == 1.5
    assert summary["per_state"] == {"ready": 4, "replay": 2}


def test_no_window_flag_disables_window() -> None:
    args = cli.build_parser().parse_args(["config", "--no-local", "--no-window"])
    assert cli._build_config(args).window.enabled is False
    args = cli.build_parser().parse_args(["config", "--no-local"])
    assert cli._build_config(args).window.enabled is True
