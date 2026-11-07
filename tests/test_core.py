"""Tests unitaires des briques sans dépendance écran."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fortnite_auto_replay import config as config_mod
from fortnite_auto_replay import vision
from fortnite_auto_replay.capture import Frame
from fortnite_auto_replay.templates import TemplateLibrary


def _image_with_patch(
    seed: int = 0, size: tuple[int, int] = (120, 200)
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    image = rng.integers(60, 200, size=(size[0], size[1], 3), dtype=np.uint8)
    patch = image[40:80, 60:140].copy()
    return image, patch


def test_best_match_finds_exact_patch() -> None:
    image, patch = _image_with_patch()
    match = vision.best_match(image, patch, "bouton", threshold=0.9)
    assert match is not None
    assert match.score > 0.99
    assert (match.x, match.y) == (60, 40)


def test_best_match_respects_threshold() -> None:
    image, patch = _image_with_patch()
    noise = np.random.default_rng(99).integers(0, 255, size=patch.shape, dtype=np.uint8)
    assert vision.best_match(image, noise, "bruit", threshold=0.99) is None


def test_resize_keeps_ratio() -> None:
    image, _ = _image_with_patch()
    out = vision.resize(image, 0.5)
    assert out.shape[0] == 60
    assert out.shape[1] == 100


def test_crop_clamps() -> None:
    image, _ = _image_with_patch()
    out = vision.crop(image, -10, -10, 30, 25)
    assert out.shape[:2] == (25, 30)


def test_frame_scale_mapping() -> None:
    frame = Frame(image=np.zeros((10, 10, 3), np.uint8), left=100, top=200, scale=0.5, ts=0.0)
    assert frame.to_screen(10, 20) == (120, 240)


def test_config_defaults_are_valid() -> None:
    cfg = config_mod.load_config(use_local=False)
    assert cfg.vision.threshold > 0.5
    assert next(s.slug for s in cfg.active_states()) == "still_here_ready"


def test_config_rejects_bad_threshold() -> None:
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_config(use_local=False, overrides={"vision": {"threshold": 2.0}})


def test_config_rejects_duplicate_states() -> None:
    raw = json.loads(config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    raw["states"] = [raw["states"][0], raw["states"][0]]
    path = config_mod.DEFAULT_CONFIG_PATH.with_name("dup.json")
    path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        with pytest.raises(config_mod.ConfigError):
            config_mod.load_config(path, use_local=False)
    finally:
        path.unlink(missing_ok=True)


def test_library_ignores_flat_templates_and_reports_missing(tmp_path: Path) -> None:
    _image, patch = _image_with_patch()
    folder = tmp_path / "replay"
    folder.mkdir()
    vision.save_image(folder / "ok.png", patch)
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    library = TemplateLibrary(tmp_path).load(["replay", "ready"])
    assert library.count() == 1
    assert library.states == ["replay"]
    assert library.missing == ["ready"]


def test_library_skips_uniform_template(tmp_path: Path) -> None:
    folder = tmp_path / "ready"
    folder.mkdir()
    vision.save_image(folder / "flat.png", np.full((30, 100, 3), 128, np.uint8))
    library = TemplateLibrary(tmp_path).load(["ready"])
    assert library.count() == 0
    assert library.states == []
    assert library.errors


def test_only_chosen_still_here_button_is_active() -> None:
    cfg = config_mod.load_config(use_local=False)
    slugs = [s.slug for s in cfg.active_states()]
    assert "still_here_ready" in slugs
    assert "still_here_not_yet" not in slugs
    cfg.engine.still_here = "ignore"
    assert not any(s.still_here for s in cfg.active_states())
    assert {"still_here_ready", "still_here_not_yet"} <= {s.slug for s in cfg.enabled_states()}


def test_config_rejects_unknown_still_here_answer() -> None:
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_config(use_local=False, overrides={"engine": {"still_here": "oui"}})


def test_update_local_config_keeps_other_keys(tmp_path: Path) -> None:
    local = tmp_path / "local.json"
    local.write_text('{"vision": {"threshold": 0.85}, "engine": {"confirm_frames": 3}}')
    config_mod.update_local_config({"engine": {"still_here": "not_yet"}}, local)
    config_mod.update_local_config({"panel": {"position": [10, 20]}}, local)
    data = json.loads(local.read_text(encoding="utf-8"))
    assert data == {
        "vision": {"threshold": 0.85},
        "engine": {"confirm_frames": 3, "still_here": "not_yet"},
        "panel": {"position": [10, 20]},
    }
    assert not local.with_name("local.json.tmp").exists()
