"""Templates : mise à l'échelle, zones attendues, JSON, outil de capture."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from fortnite_auto_replay import vision
from fortnite_auto_replay.capture import Frame
from fortnite_auto_replay.templates import Template, TemplateLibrary, prepare
from fortnite_auto_replay.tools.capture_templates import crop_template, next_index


def _template(**kwargs: object) -> Template:
    image = np.random.default_rng(0).integers(0, 255, (90, 320), dtype=np.uint8)
    return Template(state_slug="ready", path=Path("ready_01.png"), image=image, **kwargs)  # type: ignore[arg-type]


def test_prepare_without_metadata_follows_capture_scale() -> None:
    item = prepare(_template(), (960, 540), (1920, 1080), 0.5, 0.12)
    assert item is not None
    assert item.image.shape == (45, 160)
    assert item.zone is None


def test_prepare_scales_to_window_height_and_places_zone() -> None:
    # pris à 1920x1080, joué en 2560x1440, analysé à demi-échelle
    tpl = _template(window=(1920, 1080), box=(1500, 950, 320, 90))
    item = prepare(tpl, (1280, 720), (2560, 1440), 0.5, 0.05)
    assert item is not None
    assert item.image.shape == (60, 213)  # 90 x 1440/1080 x 0.5
    left, top, right, bottom = item.zone  # type: ignore[misc]
    cx, cy = (1500 + 160) / 1920 * 1280, (950 + 45) / 1080 * 720
    assert left < cx - 213 / 2 and right > cx + 213 / 2
    assert top < cy - 30 and bottom == 720  # rognée au bord de la frame


def test_prepare_drops_templates_too_small() -> None:
    assert prepare(_template(), (100, 50), None, 0.05, 0.1) is None


def test_metadata_is_read_and_bad_json_ignored(tmp_path: Path) -> None:
    folder = tmp_path / "ready"
    folder.mkdir()
    image = np.dstack([_template().image] * 3)
    vision.save_image(folder / "ready_01.png", image)
    (folder / "ready_01.json").write_text(
        json.dumps({"window": [1920, 1080], "box": [1500, 950, 320, 90]}), encoding="utf-8"
    )
    vision.save_image(folder / "ready_02.png", image)
    (folder / "ready_02.json").write_text('{"window": [0, 0]}', encoding="utf-8")

    library = TemplateLibrary(tmp_path).load(["ready"])
    first, second = library.for_state("ready")
    assert first.window == (1920, 1080)
    assert first.box == (1500, 950, 320, 90)
    assert second.window is None  # JSON invalide : template gardé, sans zone
    assert "1 avec zone attendue" in library.summary()


def test_prepared_is_cached_per_frame_size(tmp_path: Path) -> None:
    folder = tmp_path / "ready"
    folder.mkdir()
    vision.save_image(folder / "ready_01.png", np.dstack([_template().image] * 3))
    library = TemplateLibrary(tmp_path).load(["ready"])
    a = library.prepared("ready", (960, 540), None, 0.5, 0.1)
    assert library.prepared("ready", (960, 540), None, 0.5, 0.1) is a
    assert library.prepared("ready", (640, 360), None, 0.5, 0.1) is not a


def _frame(window_size: tuple[int, int] | None) -> Frame:
    image = np.arange(200 * 300 * 3, dtype=np.uint32).reshape(200, 300, 3).astype(np.uint8)
    return Frame(image=image, left=1000, top=500, scale=1.0, ts=0.0, window_size=window_size)


def test_crop_template_centers_on_cursor_and_records_box() -> None:
    image, meta = crop_template(_frame((300, 200)), (1150, 600), (40, 20))
    assert image.shape == (20, 40, 3)
    assert meta == {"window": [300, 200], "box": [130, 90, 40, 20]}


def test_crop_template_stays_inside_frame() -> None:
    _, meta = crop_template(_frame((300, 200)), (1002, 699), (40, 20))
    assert meta is not None
    assert meta["box"] == [0, 180, 40, 20]
    _, meta = crop_template(_frame(None), (1150, 600), (40, 20))
    assert meta is None  # la frame ne couvre pas toute la fenêtre


def test_next_index_never_overwrites(tmp_path: Path) -> None:
    for name in ("ready_01.png", "ready_03.png", "ready_notes.png"):
        (tmp_path / name).write_bytes(b"")
    assert next_index(tmp_path, "ready") == 4
    assert next_index(tmp_path / "vide", "ready") == 1
