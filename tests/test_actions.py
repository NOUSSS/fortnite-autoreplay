"""Clic en arrière-plan avec des appels Win32 factices : aucun message ni curseur réel."""

from __future__ import annotations

import pytest

from fortnite_auto_replay.actions import Actions, BackgroundClicker, InputError
from fortnite_auto_replay.config import ActionConfig

HWND = 77
ORIGIN = (100, 50)  # coin de la zone client du jeu à l'écran
MOVE, DOWN, UP = 0x0200, 0x0201, 0x0202
ACTIVATE, ACTIVATEAPP = 0x0006, 0x001C
WAKE = [(ACTIVATEAPP, 1, 0, 0)]  # le jeu se croit actif
SLEEP = [(ACTIVATEAPP, 0, 0, 0)]  # puis revient à son état réel


class FakeInput:
    """Fenêtre à ``ORIGIN`` ; ``moves`` = positions successives d'une souris que l'on bouge."""

    def __init__(self, *, moves: list[tuple[int, int]] | None = None, alive: bool = True) -> None:
        self.alive = alive
        self.position = (-800, 900)  # sur l'écran de gauche, où l'on travaille
        self.moves = list(moves or [])
        self.buttons = False
        self.posted: list[tuple[int, int, int, int]] = []  # message, wparam, x, y
        self.cursor_moves: list[tuple[int, int]] = []
        self.cursor_during_post: list[tuple[int, int]] = []

    def is_window(self, hwnd: int) -> bool:
        return self.alive and hwnd == HWND

    def to_client(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        return x - ORIGIN[0], y - ORIGIN[1]

    def post(self, hwnd: int, message: int, wparam: int, x: int, y: int) -> None:
        assert hwnd == HWND
        self.posted.append((message, wparam, x, y))
        self.cursor_during_post.append(self.position)

    def cursor(self) -> tuple[int, int]:
        if self.moves:
            self.position = self.moves.pop(0)
        return self.position

    def set_cursor(self, x: int, y: int) -> None:
        self.position = (x, y)
        self.cursor_moves.append((x, y))

    def buttons_down(self) -> bool:
        return self.buttons


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_clicker(api: FakeInput, **cfg: object) -> BackgroundClicker:
    clock = Clock()
    return BackgroundClicker(ActionConfig(**cfg), api, sleep=clock.sleep, clock=clock)  # type: ignore[arg-type]


def kinds(api: FakeInput) -> list[int]:
    return [m[0] for m in api.posted if m[0] in (MOVE, DOWN, UP)]


def test_message_mode_posts_a_click_and_never_touches_the_cursor() -> None:
    api = FakeInput()
    assert make_clicker(api, background="message").click(HWND, 600, 450)
    click = [(MOVE, 0, 500, 400), (DOWN, 1, 500, 400), (UP, 0, 500, 400)]
    assert api.posted == WAKE + click + SLEEP
    assert api.cursor_moves == []


def test_window_activation_is_never_posted() -> None:
    # traité par défaut, WM_ACTIVATE donne le focus à la fenêtre : premier plan possible
    api = FakeInput()
    assert make_clicker(api).click(HWND, 600, 450)
    assert all(m[0] != ACTIVATE for m in api.posted)


def test_button_pressed_while_the_game_wakes_up_defers_the_click() -> None:
    class PressesDuringWake(FakeInput):
        def post(self, hwnd: int, message: int, wparam: int, x: int, y: int) -> None:
            super().post(hwnd, message, wparam, x, y)
            if message == ACTIVATEAPP and wparam:
                self.buttons = True  # un clic sur l'autre écran pendant le réveil

    api = PressesDuringWake()
    assert not make_clicker(api).click(HWND, 600, 450)
    assert api.posted == WAKE + SLEEP
    assert api.cursor_moves == []


def test_activation_can_be_turned_off() -> None:
    api = FakeInput()
    assert make_clicker(api, background="message", background_activate=False).click(HWND, 600, 450)
    assert [m[0] for m in api.posted] == [MOVE, DOWN, UP]


def test_game_goes_back_to_sleep_even_when_the_click_fails() -> None:
    class FailsOnDown(FakeInput):
        def post(self, hwnd: int, message: int, wparam: int, x: int, y: int) -> None:
            if message == DOWN:
                raise InputError("refusé")
            super().post(hwnd, message, wparam, x, y)

    api = FailsOnDown()
    with pytest.raises(InputError):
        make_clicker(api).click(HWND, 600, 450)
    assert api.posted[-1:] == SLEEP
    assert api.position == (-800, 900)


def test_cursor_mode_hovers_the_button_then_puts_the_cursor_back() -> None:
    api = FakeInput()
    assert make_clicker(api).click(HWND, 600, 450)
    assert kinds(api) == [MOVE, DOWN, UP]
    during = zip(api.posted, api.cursor_during_post, strict=True)
    mouse = {pos for message, pos in during if message[0] in (MOVE, DOWN, UP)}
    assert mouse == {(600, 450)}  # le jeu voit le curseur sur le bouton
    # réveil et retour à l'état réel : curseur encore (ou déjà) rendu
    assert api.cursor_during_post[0] == api.cursor_during_post[-1] == (-800, 900)
    assert api.cursor_moves == [(600, 450), (-800, 900)]
    assert api.position == (-800, 900)


def test_mouse_moved_during_the_click_keeps_its_motion() -> None:
    class UserMoves(FakeInput):
        def post(self, hwnd: int, message: int, wparam: int, x: int, y: int) -> None:
            super().post(hwnd, message, wparam, x, y)
            if message == UP:  # la main reprend la souris pendant le clic
                self.position = (self.position[0] + 30, self.position[1] - 10)

    api = UserMoves()
    assert make_clicker(api).click(HWND, 600, 450)
    assert api.position == (-770, 890)  # point de départ + geste, pas de saut en arrière


def test_clamped_cursor_still_returns_to_the_same_pixel() -> None:
    class Clamping(FakeInput):
        def set_cursor(self, x: int, y: int) -> None:
            super().set_cursor(min(x, 590), y)  # Windows rogne la position demandée

    api = Clamping()
    assert make_clicker(api).click(HWND, 600, 450)
    assert api.position == (-800, 900)


def test_double_click_posts_two_presses() -> None:
    api = FakeInput()
    assert make_clicker(api, background="message").click(HWND, 600, 450, clicks=2)
    assert kinds(api) == [MOVE, DOWN, UP, DOWN, UP]


def test_moving_mouse_defers_the_click() -> None:
    # la souris bouge à chaque lecture : jamais immobile assez longtemps
    api = FakeInput(moves=[(-800 + i, 900) for i in range(1000)])
    assert not make_clicker(api).click(HWND, 600, 450)
    assert api.posted == []
    assert api.cursor_moves == []


def test_pressed_button_defers_the_click() -> None:
    api = FakeInput()
    api.buttons = True  # glisser-déposer en cours sur l'autre écran
    assert not make_clicker(api).click(HWND, 600, 450)
    assert api.posted == []
    assert api.cursor_moves == []


def test_mouse_that_stops_moving_gets_the_click() -> None:
    api = FakeInput(moves=[(-800, 900), (-790, 900), (-780, 900)])
    assert make_clicker(api).click(HWND, 600, 450)
    assert api.cursor_moves == [(600, 450), (-780, 900)]


def test_pause_during_the_wait_abandons_the_click() -> None:
    # la souris bouge, le joueur met en pause, puis la souris s'immobilise
    api = FakeInput(moves=[(-800 + i, 900) for i in range(5)])
    assert not make_clicker(api).click(HWND, 600, 450, cancel=lambda: not api.moves)
    assert api.posted == []
    assert api.cursor_moves == []


def test_closed_window_raises() -> None:
    api = FakeInput(alive=False)
    with pytest.raises(InputError):
        make_clicker(api).click(HWND, 600, 450)
    assert api.posted == []


def test_cursor_is_put_back_even_when_posting_fails() -> None:
    class Refusing(FakeInput):
        def post(self, hwnd: int, message: int, wparam: int, x: int, y: int) -> None:
            raise InputError("refusé")

    api = Refusing()
    with pytest.raises(InputError):
        make_clicker(api).click(HWND, 600, 450)
    assert api.position == (-800, 900)


def test_actions_route_background_clicks_to_the_window() -> None:
    api = FakeInput()
    clicker = make_clicker(api, background="message")
    actions = Actions(ActionConfig(click_offset=[2, 3]), dry_run=True, background=clicker)
    actions.dry_run = False  # pas de backend d'entrée réelle : seul le chemin posté sert
    assert actions.click(600, 450, window=HWND) == (602, 453)
    assert (UP, 0, 502, 403) in api.posted


def test_actions_pass_the_cancel_check_on() -> None:
    api = FakeInput()  # souris immobile : seul l'abandon empêche le clic
    actions = Actions(ActionConfig(), dry_run=True, background=make_clicker(api))
    actions.dry_run = False
    assert actions.click(600, 450, window=HWND, cancel=lambda: True) is None
    assert api.posted == []
    assert api.cursor_moves == []


def test_actions_report_a_deferred_click() -> None:
    api = FakeInput()
    api.buttons = True
    actions = Actions(ActionConfig(), dry_run=True, background=make_clicker(api))
    actions.dry_run = False
    assert actions.click(600, 450, window=HWND) is None
