"""Anti-AFK avec fenêtre, entrées et horloge factices : aucune touche réelle."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from test_chests import write_replay

from fortnite_auto_replay.antiafk import AntiAfk, key_code, live_replay
from fortnite_auto_replay.config import AntiAfkConfig, Config, ConfigError

HWND = 42
NOW = 1_700_000_000.0  # heure murale des replays


class Game:
    def __init__(self, focus: str = "background", hwnd: int = HWND) -> None:
        self.state = focus
        self.handle = hwnd

    def focus(self) -> str:
        return self.state

    def hwnd(self) -> int:
        return self.handle


class Keys:
    def __init__(self) -> None:
        self.pressed: list[tuple[int, int, float, bool]] = []

    def press_key(self, hwnd: int, vk: int, hold_s: float, *, wake: bool) -> None:
        self.pressed.append((hwnd, vk, hold_s, wake))


class User:
    def __init__(self, idle: float = 999.0, held: bool = False) -> None:
        self.idle = idle
        self.held = held

    def idle_s(self) -> float:
        return self.idle

    def cursor_held(self) -> bool:
        return self.held


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(
    demos: Path,
    *,
    game: Game | None = None,
    user: User | None = None,
    keys: Keys | None = None,
    background: bool = True,
    **cfg: object,
) -> tuple[AntiAfk, Keys, Clock]:
    clock = Clock()
    keys = keys or Keys()
    afk = AntiAfk(
        AntiAfkConfig(enabled=True, **cfg),  # type: ignore[arg-type]
        demos,
        game or Game(),
        keys,
        user or User(),
        background=background,
        clock=clock,
        wall=lambda: NOW + clock.now,
        rng=random.Random(1),
    )
    return afk, keys, clock


def in_match(folder: Path) -> Path:
    return write_replay(folder, "partie.replay", mtime=NOW - 10, live=True, key=b"\0" * 32)


def test_key_names() -> None:
    assert key_code("w") == ord("W")
    assert key_code("SPACE") == 0x20
    assert key_code("5") == ord("5")
    with pytest.raises(ValueError):
        key_code("ESC")  # ouvrirait le menu
    with pytest.raises(ValueError):
        key_code("é")


def test_live_replay_needs_a_recent_live_file(tmp_path: Path) -> None:
    assert not live_replay(tmp_path, NOW, 60)
    write_replay(tmp_path, "vieux.replay", mtime=NOW - 5, live=False)
    assert not live_replay(tmp_path, NOW, 60)  # partie terminée
    write_replay(tmp_path, "coupé.replay", mtime=NOW - 4, live=True, key=b"\0" * 32)
    assert live_replay(tmp_path, NOW, 60)
    assert not live_replay(tmp_path, NOW + 120, 60)  # plus écrit : jeu fermé
    assert not live_replay(tmp_path / "absent", NOW, 60)


def test_presses_a_configured_key_in_a_match_in_background(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, keys, _ = make(tmp_path)
    afk.step()
    assert len(keys.pressed) == 1
    hwnd, vk, hold, wake = keys.pressed[0]
    assert hwnd == HWND and wake  # second plan : jeu réveillé le temps de l'appui
    assert vk in {key_code(k) for k in AntiAfkConfig().keys}
    assert 0.1 <= hold <= 0.3


def test_waits_the_interval_between_presses(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, keys, clock = make(tmp_path, interval_s=[25.0, 25.0])
    afk.step()
    clock.now = 24.0
    afk.step()
    assert len(keys.pressed) == 1
    clock.now = 25.0
    afk.step()
    assert len(keys.pressed) == 2


def test_nothing_outside_a_match(tmp_path: Path) -> None:
    write_replay(tmp_path, "fini.replay", mtime=NOW - 10, live=False)
    afk, keys, _ = make(tmp_path)
    afk.step()
    assert keys.pressed == []


def test_nothing_when_disabled_or_game_hidden(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, keys, _ = make(tmp_path)
    afk.set_enabled(False)
    afk.step()
    assert keys.pressed == []
    assert afk.cfg.enabled is False
    afk.set_enabled(True)
    for focus in ("missing", "hidden"):
        afk._window.state = focus  # type: ignore[attr-defined]
        afk.step()
    assert keys.pressed == []


def test_background_skipped_when_foreground_is_required(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, keys, _ = make(tmp_path, background=False)
    afk.step()
    assert keys.pressed == []


def test_foreground_waits_for_the_player_to_be_idle(tmp_path: Path) -> None:
    in_match(tmp_path)
    user = User(idle=5.0)
    afk, keys, _ = make(tmp_path, game=Game("foreground"), user=user, idle_s=30.0)
    afk.step()
    assert keys.pressed == []  # le joueur joue
    user.idle = 31.0
    afk.step()
    assert len(keys.pressed) == 1
    assert keys.pressed[0][3] is False  # premier plan : pas de réveil, le jeu est actif


def test_foreground_cursor_held_counts_as_a_match_without_replays(tmp_path: Path) -> None:
    user = User(held=False)
    afk, keys, clock = make(tmp_path, game=Game("foreground"), user=user)
    afk.step()
    assert keys.pressed == []  # salon : curseur visible, aucun replay
    user.held = True
    clock.now = 1.0
    afk.step()
    assert len(keys.pressed) == 1


def test_cursor_is_not_a_sign_in_background(tmp_path: Path) -> None:
    afk, keys, _ = make(tmp_path, user=User(held=True))
    afk.step()
    assert keys.pressed == []


def test_no_window_no_press(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, keys, _ = make(tmp_path, game=Game(hwnd=0))
    afk.step()
    assert keys.pressed == []


def test_dry_run_presses_nothing(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk = AntiAfk(AntiAfkConfig(enabled=True), tmp_path, Game(), None, User(), dry_run=True)
    afk.step()  # journalisé seulement


def test_config_validation() -> None:
    Config().validate()
    assert AntiAfkConfig().enabled is False  # à activer soi-même
    for bad in (
        {"keys": []},
        {"keys": ["ESC"]},
        {"interval_s": [10.0, 5.0]},
        {"interval_s": [1.0, 5.0]},
        {"hold_s": [0.1]},
        {"hold_s": [0.1, 5.0]},
    ):
        with pytest.raises(ConfigError):
            AntiAfkConfig(**bad).validate()  # type: ignore[arg-type]
