"""Moteur de bout en bout avec capture, clics et fenêtre factices (aucune entrée réelle)."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from fortnite_auto_replay import config as config_mod
from fortnite_auto_replay import history, vision
from fortnite_auto_replay.capture import CaptureError, Frame
from fortnite_auto_replay.engine import Engine
from fortnite_auto_replay.templates import TemplateLibrary

FRAME_SIZE = (360, 640)  # hauteur, largeur
BUTTON_SIZE = (40, 120)
ORIGIN = (100, 200)  # coin de la frame sur l'écran


def _texture(seed: int, size: tuple[int, int]) -> np.ndarray:
    gray = np.random.default_rng(seed).integers(30, 230, size=size, dtype=np.uint8)
    return np.dstack([gray, gray, gray])


BACKGROUND = _texture(1, FRAME_SIZE)
BUTTONS = {
    "replay": _texture(10, BUTTON_SIZE),
    "ready": _texture(11, BUTTON_SIZE),
    "still_here_ready": _texture(12, BUTTON_SIZE),
    "still_here_not_yet": _texture(13, BUTTON_SIZE),
}


def make_frame(**positions: tuple[int, int]) -> Frame:
    """Frame avec les boutons demandés, ex. ``make_frame(replay=(50, 40))``."""
    image = BACKGROUND.copy()
    for slug, (x, y) in positions.items():
        h, w = BUTTONS[slug].shape[:2]
        image[y : y + h, x : x + w] = BUTTONS[slug]
    return Frame(image=image, left=ORIGIN[0], top=ORIGIN[1], scale=1.0, ts=0.0)


def screen_center(x: int, y: int) -> tuple[int, int]:
    h, w = BUTTON_SIZE
    return ORIGIN[0] + x + w // 2, ORIGIN[1] + y + h // 2


class FakeCapture:
    def __init__(self, frames: list[Frame]) -> None:
        self.frames = list(frames)
        self.grabbed = 0
        self.on_exhausted: Callable[[], None] = lambda: None

    def grab(self) -> Frame:
        if self.grabbed >= len(self.frames):
            self.on_exhausted()
            raise CaptureError("fin des frames de test")
        frame = self.frames[self.grabbed]
        self.grabbed += 1
        return frame


class FakeClicker:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []

    def click(self, x: int, y: int, *, clicks: int = 1) -> tuple[int, int]:
        self.clicks.append((x, y))
        return x, y


class FakeWindow:
    """Renvoie les états de focus donnés, puis répète le dernier.

    ``cover`` = ce qui couvre tout point cliqué (``None`` : le jeu reçoit le clic).
    """

    def __init__(self, *states: str, cover: tuple[int, str] | None = None) -> None:
        self.states = list(states)
        self.cover = cover

    def focus(self) -> Any:
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def covering(self, x: int, y: int) -> tuple[int, str] | None:
        return self.cover


def _state(slug: str, priority: int) -> dict[str, Any]:
    return {
        "slug": slug,
        "label": slug,
        "priority": priority,
        "cooldown_s": 0.0,
        "post_action_sleep_s": 0.0,
    }


def make_config(
    tmp_path: Path, states: list[dict[str, Any]] | None = None, **engine: Any
) -> config_mod.Config:
    engine_cfg: dict[str, Any] = {
        "confirm_frames": 2,
        "action_cooldown_s": 0.0,
        "start_paused": False,
        "max_ineffective_clicks": 0,
        "beep_on_stuck": False,
        "sound_on_toggle": False,
        "doubt_margin": 0.0,
        "stats_file": "",
        "debug_dir": str(tmp_path / "captures"),
    }
    engine_cfg.update(engine)
    return config_mod.load_config(
        use_local=False,
        overrides={
            "capture": {"target_fps": 60},
            "vision": {"threshold": 0.9},
            "engine": engine_cfg,
            "states": states or [_state("replay", 90), _state("ready", 70)],
        },
    )


def make_library(tmp_path: Path, templates: dict[str, np.ndarray] | None = None) -> TemplateLibrary:
    templates = BUTTONS if templates is None else templates
    for slug, image in templates.items():
        vision.save_image(tmp_path / "templates" / slug / f"{slug}_01.png", image)
    return TemplateLibrary(tmp_path / "templates").load(list(templates))


def make_engine(
    tmp_path: Path,
    frames: list[Frame],
    *,
    window: FakeWindow | None = None,
    templates: dict[str, np.ndarray] | None = None,
    dry_run: bool = False,
    alert: Callable[[], None] | None = None,
    states: list[dict[str, Any]] | None = None,
    **engine: Any,
) -> tuple[Engine, FakeCapture, FakeClicker]:
    capture = FakeCapture(frames)
    clicker = FakeClicker()
    eng = Engine(
        make_config(tmp_path, states, **engine),
        capture,
        clicker,
        make_library(tmp_path, templates),
        dry_run=dry_run,
        window=window,
        alert=alert,
    )
    capture.on_exhausted = eng.stop
    return eng, capture, clicker


def run_until(eng: Engine, condition: Callable[[], bool], timeout: float = 5.0) -> None:
    """Lance la boucle dans un thread et l'arrête quand ``condition`` devient vraie."""
    thread = threading.Thread(target=eng.run, daemon=True)
    thread.start()
    deadline = threading.Event()
    waited = 0.0
    while not condition() and waited < timeout and thread.is_alive():
        deadline.wait(0.02)
        waited += 0.02
    eng.stop()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_clicks_center_after_confirmation(tmp_path: Path) -> None:
    eng, _, clicker = make_engine(tmp_path, [make_frame(replay=(50, 40))] * 3)
    stats = eng.run()
    # frame 1 : 1re confirmation, frame 2 : clic, frame 3 : la confirmation repart à zéro
    assert clicker.clicks == [screen_center(50, 40)]
    assert stats.per_state == {"replay": 1}


def test_highest_priority_wins(tmp_path: Path) -> None:
    frames = [make_frame(replay=(50, 40), ready=(300, 200))] * 2
    eng, _, clicker = make_engine(tmp_path, frames)
    eng.run()
    assert clicker.clicks == [screen_center(50, 40)]


def test_moved_button_is_found_outside_known_zone(tmp_path: Path) -> None:
    frames = [make_frame(replay=(50, 40)), make_frame(replay=(400, 250))]
    eng, _, clicker = make_engine(tmp_path, frames)
    eng.run()
    assert clicker.clicks == [screen_center(400, 250)]


def test_known_zone_near_frame_edge_keeps_coordinates(tmp_path: Path) -> None:
    # bouton collé au bord : la zone connue (± padding) est rognée par la frame
    frames = [make_frame(replay=(0, 0))] * 2
    eng, _, clicker = make_engine(tmp_path, frames)
    eng.run()
    assert clicker.clicks == [screen_center(0, 0)]


@pytest.mark.parametrize("focus", ["background", "missing"])
def test_nothing_happens_without_focus(tmp_path: Path, focus: str) -> None:
    frames = [make_frame(replay=(50, 40))] * 4
    eng, capture, clicker = make_engine(tmp_path, frames, window=FakeWindow(focus))
    timer = threading.Timer(0.4, eng.stop)
    timer.start()
    eng.run()
    timer.cancel()
    assert capture.grabbed == 0
    assert clicker.clicks == []


def test_resumes_when_game_gets_focus(tmp_path: Path) -> None:
    frames = [make_frame(replay=(50, 40))] * 2
    window = FakeWindow("background", "background", "foreground")
    eng, _, clicker = make_engine(tmp_path, frames, window=window)
    eng.run()
    assert clicker.clicks == [screen_center(50, 40)]


def test_no_focus_check_when_not_required(tmp_path: Path) -> None:
    frames = [make_frame(replay=(50, 40))] * 2
    capture = FakeCapture(frames)
    clicker = FakeClicker()
    cfg = make_config(tmp_path)
    cfg.window.require_foreground = False
    eng = Engine(cfg, capture, clicker, make_library(tmp_path), window=FakeWindow("background"))
    capture.on_exhausted = eng.stop
    eng.run()
    assert len(clicker.clicks) == 1


def test_pauses_when_clicks_have_no_effect(tmp_path: Path) -> None:
    alerts: list[bool] = []
    frames = [make_frame(replay=(50, 40))] * 30
    eng, _, clicker = make_engine(
        tmp_path, frames, max_ineffective_clicks=2, alert=lambda: alerts.append(True)
    )
    run_until(eng, lambda: eng.paused)
    assert eng.paused
    assert len(clicker.clicks) == 2
    assert eng.stats.stuck == 1
    assert alerts == [True]
    assert len(list((tmp_path / "captures").glob("stuck-replay-*.png"))) == 1


def test_click_streak_resets_when_button_disappears(tmp_path: Path) -> None:
    present, absent = make_frame(replay=(50, 40)), make_frame()
    frames = [present, present, absent] * 3
    eng, _, clicker = make_engine(tmp_path, frames, max_ineffective_clicks=1)
    eng.run()
    assert len(clicker.clicks) == 3
    assert eng.stats.stuck == 0
    assert not eng.paused


def test_doubtful_match_saves_one_frame_and_does_not_click(tmp_path: Path) -> None:
    noise = np.random.default_rng(5).integers(-70, 70, size=BUTTON_SIZE)
    blurred = np.clip(BUTTONS["replay"][:, :, 0].astype(int) + noise, 0, 255).astype(np.uint8)
    template = np.dstack([blurred] * 3)
    frame = make_frame(replay=(50, 40))
    score = vision.best_match(frame.image, template, "test", threshold=0.0)
    assert score is not None
    assert 0.4 < score.score < 0.95

    eng, _, clicker = make_engine(
        tmp_path,
        [frame] * 5,
        templates={"replay": template, "ready": BUTTONS["ready"]},
        doubt_margin=0.1,
    )
    eng.cfg.vision.threshold = score.score + 0.03
    eng.run()
    assert clicker.clicks == []
    assert eng.stats.doubts_saved == 1  # une seule par état et par intervalle
    assert len(list((tmp_path / "captures").glob("doubt-replay-*.jpg"))) == 1


def test_session_history_is_recorded(tmp_path: Path) -> None:
    stats_file = tmp_path / "logs" / "sessions.jsonl"
    frames = [make_frame(replay=(50, 40))] * 2
    for _ in range(2):
        eng, _, _ = make_engine(tmp_path, frames, stats_file=str(stats_file))
        eng.run()
    sessions = history.read_sessions(stats_file)
    assert len(sessions) == 2
    summary = history.summarize(sessions)
    assert summary["actions"] == 2
    assert summary["per_state"] == {"replay": 2}


def test_dry_run_session_is_not_recorded(tmp_path: Path) -> None:
    stats_file = tmp_path / "sessions.jsonl"
    frames = [make_frame(replay=(50, 40))] * 2
    eng, _, _ = make_engine(tmp_path, frames, dry_run=True, stats_file=str(stats_file))
    eng.run()
    assert not stats_file.exists()


# --- zones attendues et mise à l'échelle ----------------------------------
WINDOW = (FRAME_SIZE[1], FRAME_SIZE[0])  # largeur, hauteur : la frame couvre tout le jeu


def window_frame(**positions: tuple[int, int]) -> Frame:
    frame = make_frame(**positions)
    frame.window_size = WINDOW
    return frame


def zoned_engine(
    tmp_path: Path,
    frames: list[Frame],
    *,
    template: np.ndarray | None = None,
    meta: dict[str, Any] | None = None,
    full_scan_interval_s: float = 0.0,
) -> tuple[Engine, FakeClicker]:
    folder = tmp_path / "templates" / "replay"
    vision.save_image(folder / "replay_01.png", BUTTONS["replay"] if template is None else template)
    meta = meta or {"window": list(WINDOW), "box": [50, 40, 120, 40]}
    (folder / "replay_01.json").write_text(json.dumps(meta), encoding="utf-8")
    library = TemplateLibrary(tmp_path / "templates").load(["replay", "ready"])
    cfg = make_config(tmp_path)
    cfg.vision.zone_margin = 0.05
    cfg.vision.full_scan_interval_s = full_scan_interval_s
    capture = FakeCapture(frames)
    clicker = FakeClicker()
    eng = Engine(cfg, capture, clicker, library)
    capture.on_exhausted = eng.stop
    return eng, clicker


def test_button_in_expected_zone_is_clicked(tmp_path: Path) -> None:
    eng, clicker = zoned_engine(tmp_path, [window_frame(replay=(60, 50))] * 2)
    eng.run()
    assert clicker.clicks == [screen_center(60, 50)]


def test_button_outside_zone_needs_full_scan(tmp_path: Path) -> None:
    away = window_frame(replay=(400, 250))
    eng, clicker = zoned_engine(tmp_path / "sans", [away] * 4)
    eng.run()
    assert clicker.clicks == []  # aucun balayage complet : hors zone, jamais vu

    # 1re frame = balayage complet, puis le bouton est suivi à sa nouvelle place
    eng, clicker = zoned_engine(tmp_path / "avec", [away] * 2, full_scan_interval_s=60)
    eng.run()
    assert clicker.clicks == [screen_center(400, 250)]


def test_template_from_larger_window_is_scaled_down(tmp_path: Path) -> None:
    # template pris dans une fenêtre 1280x720, jeu affiché en 640x360
    big = np.repeat(np.repeat(BUTTONS["replay"], 2, axis=0), 2, axis=1)
    meta = {"window": [1280, 720], "box": [100, 80, 240, 80]}
    frames = [window_frame(replay=(50, 40))] * 2
    eng, clicker = zoned_engine(tmp_path, frames, template=big, meta=meta)
    eng.run()
    assert clicker.clicks == [screen_center(50, 40)]


class BrokenClicker(FakeClicker):
    def click(self, x: int, y: int, *, clicks: int = 1) -> tuple[int, int]:
        raise RuntimeError("souris débranchée")


def test_unexpected_errors_pause_instead_of_crashing(tmp_path: Path) -> None:
    alerts: list[bool] = []
    capture = FakeCapture([make_frame(replay=(50, 40))] * 30)
    eng = Engine(
        make_config(tmp_path, max_step_errors=2),
        capture,
        BrokenClicker(),
        make_library(tmp_path),
        alert=lambda: alerts.append(True),
    )
    capture.on_exhausted = eng.stop
    run_until(eng, lambda: eng.paused)
    assert eng.paused
    assert eng.stats.errors == 2
    assert alerts == [True]


# --- dialogue « Vous êtes toujours là ? » -----------------------------------
def dialog_states() -> list[dict[str, Any]]:
    return [
        _state("replay", 90),
        {**_state("still_here_ready", 110), "still_here": "ready"},
        {**_state("still_here_not_yet", 110), "still_here": "not_yet"},
    ]


DIALOG = {"still_here_ready": (50, 40), "still_here_not_yet": (300, 40)}


@pytest.mark.parametrize("answer", ["ready", "not_yet", "ignore"])
def test_still_here_answer_picks_its_button(tmp_path: Path, answer: str) -> None:
    frames = [make_frame(**DIALOG)] * 2
    eng, _, clicker = make_engine(tmp_path, frames, states=dialog_states(), still_here=answer)
    eng.run()
    expected = [] if answer == "ignore" else [screen_center(*DIALOG[f"still_here_{answer}"])]
    assert clicker.clicks == expected


def test_still_here_answer_changes_while_running(tmp_path: Path) -> None:
    frames = [make_frame(**DIALOG)] * 2
    eng, _, clicker = make_engine(tmp_path, frames, states=dialog_states(), still_here="ready")
    eng.set_still_here("not_yet")
    eng.run()
    assert clicker.clicks == [screen_center(*DIALOG["still_here_not_yet"])]
    assert eng.status().still_here == "not_yet"


def test_still_here_answer_needs_a_template(tmp_path: Path) -> None:
    templates = {"replay": BUTTONS["replay"], "still_here_ready": BUTTONS["still_here_ready"]}
    eng, _, _ = make_engine(tmp_path, [], states=dialog_states(), templates=templates)
    assert eng.still_here_answers() == ["ready", "ignore"]
    with pytest.raises(ValueError):
        eng.set_still_here("not_yet")


# --- état lu par le panneau ---------------------------------------------------
def test_status_follows_pause_clicks_and_stop(tmp_path: Path) -> None:
    frames = [make_frame(replay=(50, 40))] * 2
    eng, _, _ = make_engine(tmp_path, frames, start_paused=True)
    assert eng.status().phase == "paused"
    eng.resume()
    eng.run()
    status = eng.status()
    assert status.phase == "stopped"
    assert status.actions == 1
    assert status.per_state == {"replay": 1}
    assert [(c.slug, c.label) for c in status.recent] == [("replay", "replay")]


def test_status_explains_stuck_pause(tmp_path: Path) -> None:
    frames = [make_frame(replay=(50, 40))] * 30
    eng, _, _ = make_engine(tmp_path, frames, max_ineffective_clicks=2)
    run_until(eng, lambda: eng.paused)
    eng._finished = False  # on lit l'état tel qu'il était, boucle en pause
    status = eng.status()
    assert status.phase == "stuck"
    assert "replay" in status.detail
    assert "2 clics" in status.detail


@pytest.mark.parametrize(
    ("cover", "who"),
    [((4242, "Discord.exe"), "Discord.exe"), ((os.getpid(), "python.exe"), "panneau")],
)
def test_covered_button_is_not_clicked(tmp_path: Path, cover: tuple[int, str], who: str) -> None:
    frames = [make_frame(replay=(50, 40))] * 6
    window = FakeWindow("foreground", cover=cover)
    eng, _, clicker = make_engine(tmp_path, frames, window=window)
    seen: list[str] = []

    def covered() -> bool:
        status = eng.status()
        if status.phase == "covered":
            seen.append(status.detail)
        return bool(seen)

    run_until(eng, covered)
    assert clicker.clicks == []
    assert seen
    assert who in seen[0]


def test_toggle_announces_pause_and_resume(tmp_path: Path) -> None:
    cues: list[str] = []
    capture = FakeCapture([])
    eng = Engine(
        make_config(tmp_path), capture, FakeClicker(), make_library(tmp_path), sound=cues.append
    )
    eng.toggle()
    eng.toggle()
    assert cues == ["pause", "resume"]
    assert not eng.paused
