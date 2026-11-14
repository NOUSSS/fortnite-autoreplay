"""Anti-AFK avec fenêtre, entrées et horloge factices : aucune touche ni souris réelles."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from test_chests import write_replay

from fortnite_auto_replay.antiafk import AntiAfk, Win32UserInput, key_code, live_replay
from fortnite_auto_replay.config import AntiAfkConfig, Config, ConfigError

HWND = 42
REGION = (100, 50, 1100, 650)  # zone client du jeu, centre (600, 350)
NOW = 1_700_000_000.0  # heure murale des replays


class Game:
    def __init__(self, focus: str = "background", hwnd: int = HWND) -> None:
        self.state = focus
        self.handle = hwnd

    def focus(self) -> str:
        return self.state

    def hwnd(self) -> int:
        return self.handle

    def region(self) -> tuple[int, int, int, int] | None:
        return REGION if self.handle else None


class Posted:
    """Messages postés au jeu : ("key", vk, durée, réveil) ou ("click", bouton, x, y, réveil)."""

    def __init__(self) -> None:
        self.sent: list[tuple[object, ...]] = []

    def press_key(self, hwnd: int, vk: int, hold_s: float, *, wake: bool) -> None:
        assert hwnd == HWND
        self.sent.append(("key", vk, hold_s, wake))

    def press_button(
        self, hwnd: int, x: int, y: int, button: str, hold_s: float, *, wake: bool
    ) -> None:
        assert hwnd == HWND
        assert 0 < hold_s <= 0.6
        self.sent.append(("click", button, x, y, wake))


class User:
    def __init__(self, idle: float = 999.0, held: bool = False) -> None:
        self.idle = idle
        self.held = held
        self.moves: list[tuple[int, int]] = []
        self.on_nudge: object = None

    def idle_s(self) -> float:
        return self.idle

    def cursor_held(self) -> bool:
        return self.held

    def nudge(self, dx: int, dy: int) -> None:
        self.moves.append((dx, dy))
        if callable(self.on_nudge):
            self.on_nudge()


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
    background: bool = True,
    seed: int = 1,
    **cfg: object,
) -> tuple[AntiAfk, Posted, Clock]:
    clock = Clock()
    posted = Posted()
    afk = AntiAfk(
        AntiAfkConfig(enabled=True, **cfg),  # type: ignore[arg-type]
        demos,
        game or Game(),
        posted,
        user or User(),
        background=background,
        clock=clock,
        wall=lambda: NOW + clock.now,
        rng=random.Random(seed),
        sleep=lambda _s: None,
    )
    return afk, posted, clock


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
    afk, posted, _ = make(tmp_path, gestures=["keys"])
    afk.step()
    assert len(posted.sent) == 1
    kind, vk, hold, wake = posted.sent[0]
    assert kind == "key" and wake  # second plan : jeu réveillé le temps de l'appui
    assert vk in {key_code(k) for k in AntiAfkConfig().keys}
    assert 0.1 <= hold <= 0.3  # type: ignore[operator]


def test_clicks_the_center_of_the_game(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, posted, _ = make(tmp_path, gestures=["clicks"])
    afk.step()
    assert posted.sent == [("click", posted.sent[0][1], 600, 350, True)]
    assert posted.sent[0][1] in ("left", "right")


def test_background_never_turns_the_camera(tmp_path: Path) -> None:
    # au second plan, la vraie souris appartient à la fenêtre où l'on travaille
    in_match(tmp_path)
    user = User(held=True)
    afk, posted, clock = make(tmp_path, user=user, interval_s=[2.0, 2.0])
    for second in range(0, 60, 2):
        clock.now = float(second)
        afk.step()
    assert user.moves == []
    assert {s[0] for s in posted.sent} == {"key", "click"}


def test_camera_only_option_does_nothing_in_background(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, posted, _ = make(tmp_path, gestures=["camera"])
    afk.step()
    assert posted.sent == []


def test_camera_turns_by_small_steps_in_foreground(tmp_path: Path) -> None:
    user = User(held=True)
    afk, posted, _ = make(
        tmp_path, game=Game("foreground"), user=user, gestures=["camera"], turn_px=[300, 300]
    )
    afk.step()
    assert posted.sent == []
    assert len(user.moves) == 12
    assert abs(sum(dx for dx, _ in user.moves)) == 300
    assert all(abs(dx) <= 30 for dx, _ in user.moves)


def test_camera_stops_as_soon_as_the_game_loses_the_foreground(tmp_path: Path) -> None:
    user = User(held=True)
    game = Game("foreground")
    afk, _, _ = make(tmp_path, game=game, user=user, gestures=["camera"])

    def alt_tab() -> None:
        if len(user.moves) == 3:
            game.state = "background"

    user.on_nudge = alt_tab
    afk.step()
    assert len(user.moves) == 3


def test_camera_stops_when_the_player_takes_the_mouse(tmp_path: Path) -> None:
    user = User(held=True)
    afk, _, _ = make(tmp_path, game=Game("foreground"), user=user, gestures=["camera"])

    def player_moves() -> None:
        user.idle = 0.0

    user.on_nudge = player_moves
    afk.step()
    assert len(user.moves) == 1


def test_foreground_with_a_menu_open_only_presses_keys(tmp_path: Path) -> None:
    # curseur visible en partie : carte ou inventaire ouverts, un clic y toucherait un menu
    in_match(tmp_path)
    user = User(held=False)
    afk, posted, clock = make(tmp_path, game=Game("foreground"), user=user, interval_s=[2, 2])
    for second in range(0, 40, 2):
        clock.now = float(second)
        afk.step()
    assert posted.sent and {s[0] for s in posted.sent} == {"key"}
    assert user.moves == []
    assert all(s[3] is False for s in posted.sent)  # premier plan : pas de réveil


def test_waits_the_interval_between_gestures(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, posted, clock = make(tmp_path, interval_s=[25.0, 25.0])
    afk.step()
    clock.now = 24.0
    afk.step()
    assert len(posted.sent) == 1
    clock.now = 25.0
    afk.step()
    assert len(posted.sent) == 2


def test_nothing_outside_a_match(tmp_path: Path) -> None:
    write_replay(tmp_path, "fini.replay", mtime=NOW - 10, live=False)
    afk, posted, _ = make(tmp_path)
    afk.step()
    assert posted.sent == []


def test_nothing_when_disabled_or_game_hidden(tmp_path: Path) -> None:
    in_match(tmp_path)
    game = Game()
    afk, posted, _ = make(tmp_path, game=game)
    afk.set_enabled(False)
    afk.step()
    assert posted.sent == []
    assert afk.cfg.enabled is False
    afk.set_enabled(True)
    for focus in ("missing", "hidden"):
        game.state = focus
        afk.step()
    assert posted.sent == []


def test_background_skipped_when_foreground_is_required(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, posted, _ = make(tmp_path, background=False)
    afk.step()
    assert posted.sent == []


def test_foreground_waits_for_the_player_to_be_idle(tmp_path: Path) -> None:
    user = User(idle=5.0, held=True)
    afk, posted, _ = make(tmp_path, game=Game("foreground"), user=user, idle_s=30.0)
    afk.step()
    assert posted.sent == [] and user.moves == []  # le joueur joue
    user.idle = 31.0
    afk.step()
    assert len(posted.sent) + bool(user.moves) == 1


def test_foreground_cursor_held_counts_as_a_match_without_replays(tmp_path: Path) -> None:
    user = User(held=False)
    afk, posted, clock = make(tmp_path, game=Game("foreground"), user=user, gestures=["keys"])
    afk.step()
    assert posted.sent == []  # salon : curseur visible, aucun replay
    user.held = True
    clock.now = 1.0
    afk.step()
    assert len(posted.sent) == 1


def test_cursor_is_not_a_sign_in_background(tmp_path: Path) -> None:
    afk, posted, _ = make(tmp_path, user=User(held=True))
    afk.step()
    assert posted.sent == []


def test_no_window_no_gesture(tmp_path: Path) -> None:
    in_match(tmp_path)
    afk, posted, _ = make(tmp_path, game=Game(hwnd=0))
    afk.step()
    assert posted.sent == []


def test_dry_run_sends_nothing(tmp_path: Path) -> None:
    user = User(held=True)
    afk = AntiAfk(AntiAfkConfig(enabled=True), tmp_path, Game("foreground"), None, user)
    for _ in range(5):
        afk._next_at = float("-inf")
        afk.step()  # journalisé seulement
    assert user.moves == []


def test_config_validation() -> None:
    Config().validate()
    assert AntiAfkConfig().enabled is False  # à activer soi-même
    for bad in (
        {"keys": []},
        {"keys": ["ESC"]},
        {"gestures": []},
        {"gestures": ["dance"]},
        {"interval_s": [10.0, 5.0]},
        {"interval_s": [1.0, 5.0]},
        {"hold_s": [0.1]},
        {"hold_s": [0.1, 5.0]},
        {"click_hold_s": [0.1, 3.0]},
        {"turn_px": [0, 100]},
    ):
        with pytest.raises(ConfigError):
            AntiAfkConfig(**bad).validate()  # type: ignore[arg-type]


def test_own_mouse_steps_are_told_apart_from_the_player() -> None:
    user = Win32UserInput.__new__(Win32UserInput)  # sans user32 : seul le calcul compte
    user._own = (0xFFFFFF00, 0x00000040)  # rotation à cheval sur le retour à zéro du compteur
    assert user._ours(0xFFFFFF10)
    assert user._ours(0x00000020)
    assert not user._ours(0x00000100)  # après la rotation : le joueur
    assert not user._ours(0xFFFFFE00)  # avant
    user._own = None
    assert not user._ours(5)
