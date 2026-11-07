"""Capture : choix de l'écran, rognage avec bonne origine, réouverture, image en cache."""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pytest

from fortnite_auto_replay import capture as capture_mod
from fortnite_auto_replay.capture import (
    CaptureError,
    DxgiOutput,
    ScreenCapture,
    _DxcamBackend,
    pick_output,
)
from fortnite_auto_replay.config import CaptureConfig

PRIMARY = (0, 0, 1920, 1080)
LEFT_SCREEN = (-1920, 114, 0, 1194)  # second écran à gauche, décalé vers le bas
OUTPUTS = [DxgiOutput(0, 0, PRIMARY), DxgiOutput(0, 1, LEFT_SCREEN)]


def test_pick_output_by_center_then_overlap() -> None:
    assert pick_output(OUTPUTS, (100, 100, 900, 700)).output == 0
    assert pick_output(OUTPUTS, (-1500, 300, -100, 900)).output == 1
    # centre dans le vide (au-dessus du second écran) : plus grand recouvrement
    assert pick_output(OUTPUTS, (-1900, 0, -100, 200)).output == 1
    with pytest.raises(CaptureError):
        pick_output(OUTPUTS, (5000, 5000, 5100, 5100))


class FakeBackend:
    """Backend « dxcam » sur l'écran qui contient la cible, avec échecs programmables."""

    opened: ClassVar[list[FakeBackend]] = []
    failures_left: ClassVar[int] = 0

    def __init__(self, target: tuple[int, int, int, int]) -> None:
        self.name = "dxcam"
        self.bounds = pick_output(OUTPUTS, target).rect
        self.regions: list[tuple[int, int, int, int]] = []
        FakeBackend.opened.append(self)

    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray:
        if FakeBackend.failures_left:
            FakeBackend.failures_left -= 1
            raise RuntimeError("accès DXGI perdu")
        self.regions.append(region)
        return np.zeros((region[3] - region[1], region[2] - region[0], 3), np.uint8)

    def close(self) -> None:
        pass


class FakeWindow:
    def __init__(self, rect: tuple[int, int, int, int] | None) -> None:
        self.rect = rect

    def region(self) -> tuple[int, int, int, int] | None:
        return self.rect


@pytest.fixture
def fake_backends(monkeypatch: pytest.MonkeyPatch) -> type[FakeBackend]:
    FakeBackend.opened = []
    FakeBackend.failures_left = 0
    monkeypatch.setattr(capture_mod, "_FACTORIES", {"dxcam": FakeBackend})
    monkeypatch.setattr(capture_mod, "_ORDER", ("dxcam",))
    monkeypatch.setattr(capture_mod, "monitor_bounds", lambda index: (0, 0, 1920, 1080))
    return FakeBackend


def make_capture(window: Any, **cfg: Any) -> ScreenCapture:
    return ScreenCapture(CaptureConfig(scale=cfg.pop("scale", 1.0), **cfg), window=window)


def test_window_inside_screen_keeps_window_size(fake_backends: type[FakeBackend]) -> None:
    frame = make_capture(FakeWindow((100, 50, 1380, 770))).grab()
    assert (frame.left, frame.top, frame.width, frame.height) == (100, 50, 1280, 720)
    assert frame.window_size == (1280, 720)


def test_window_overflowing_screen_is_cropped_with_real_origin(
    fake_backends: type[FakeBackend],
) -> None:
    # fenêtre tirée à gauche du bord de l'écran principal
    frame = make_capture(FakeWindow((-300, 100, 1300, 1000))).grab()
    assert (frame.left, frame.top) == (0, 100)
    assert (frame.width, frame.height) == (1300, 900)
    assert frame.window_size is None  # la frame ne montre qu'une partie du jeu
    assert frame.to_screen(500, 10) == (500, 110)


def test_scale_and_roi_relative_to_window(fake_backends: type[FakeBackend]) -> None:
    frame = make_capture(FakeWindow((100, 50, 1380, 770)), scale=0.5, roi=[80, 40, 400, 200]).grab()
    assert (frame.left, frame.top, frame.width, frame.height) == (180, 90, 200, 100)
    assert frame.window_size is None  # roi : pas la zone client entière
    assert frame.to_screen(10, 10) == (200, 110)


def test_moving_game_to_other_screen_reopens_capture(fake_backends: type[FakeBackend]) -> None:
    window = FakeWindow((100, 100, 1380, 820))
    cap = make_capture(window)
    cap.grab()
    window.rect = (-1700, 200, -420, 920)
    frame = cap.grab()
    assert len(fake_backends.opened) == 2
    assert fake_backends.opened[-1].bounds == LEFT_SCREEN
    assert (frame.left, frame.top) == (-1700, 200)


def test_repeated_failures_reopen_backend(fake_backends: type[FakeBackend]) -> None:
    cap = make_capture(FakeWindow((0, 0, 1920, 1080)))
    fake_backends.failures_left = 3
    for _ in range(3):
        with pytest.raises(CaptureError, match="RuntimeError"):
            cap.grab()
    assert len(fake_backends.opened) == 2
    assert cap.grab().width == 1920


def test_missing_window_is_a_capture_error(fake_backends: type[FakeBackend]) -> None:
    window = FakeWindow((0, 0, 1920, 1080))
    cap = make_capture(window)
    window.rect = None
    with pytest.raises(CaptureError, match="introuvable"):
        cap.grab()


class FakeCamera:
    def __init__(self, *frames: np.ndarray | None) -> None:
        self.frames = list(frames)

    def grab(self) -> np.ndarray | None:
        return self.frames.pop(0) if self.frames else None


def test_dxcam_reuses_last_image_when_screen_is_still() -> None:
    first = np.full((4, 6, 3), 7, np.uint8)
    backend = _DxcamBackend((0, 0, 6, 4), camera=FakeCamera(first, None, None), bounds=(0, 0, 6, 4))
    assert backend.grab((0, 0, 6, 4)).sum() == first.sum()
    assert backend.grab((2, 1, 6, 4)).shape == (3, 4, 3)  # écran immobile : même image


def test_dxcam_detects_resolution_change() -> None:
    camera = FakeCamera(np.zeros((8, 8, 3), np.uint8))
    backend = _DxcamBackend((0, 0, 4, 4), camera=camera, bounds=(0, 0, 4, 4))
    with pytest.raises(CaptureError, match="résolution"):
        backend.grab((0, 0, 4, 4))


def test_dxcam_crop_uses_screen_origin() -> None:
    image = np.arange(10 * 20, dtype=np.uint8).reshape(10, 20)
    frame = np.dstack([image] * 3)
    backend = _DxcamBackend(LEFT_SCREEN, camera=FakeCamera(frame), bounds=(-20, 5, 0, 15))
    crop = backend.grab((-15, 7, -10, 9))
    assert np.array_equal(crop[:, :, 0], image[2:4, 5:10])
