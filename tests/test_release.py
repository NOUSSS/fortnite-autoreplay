"""Publication : notes de version, page rendue complète."""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
NBSP = "\u00a0"


@pytest.fixture(scope="module")
def release():
    sys.path.insert(0, str(PACKAGING))
    try:
        yield importlib.import_module("release")
    finally:
        sys.path.remove(str(PACKAGING))


def test_french_spaces(release) -> None:
    text = release.french_spaces("Prêt : oui ; « Jouer » ? non !")
    assert text == f"Prêt{NBSP}: oui{NBSP}; «{NBSP}Jouer{NBSP}»{NBSP}? non{NBSP}!"
    assert release.french_spaces(text) == text  # déjà corrigé : inchangé


def test_changelog_has_the_current_version(release) -> None:
    version, date = release.current_release()
    notes = next(n for v, d, n in release.read_changelog() if v == version)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
    assert notes
    assert all(" :" not in note and "« " not in note for note in notes)


def test_long_date_and_size(release) -> None:
    assert release.long_date("2026-11-08") == f"8{NBSP}novembre 2026"
    assert release.size_text(74_600_000) == f"75{NBSP}Mo"


def test_site_is_fully_rendered(release, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(release, "SITE_OUT", tmp_path / "site")
    version, date = release.current_release()
    out = release.render_site(version, f"FortniteAutoReplay-{version}.zip", 75_000_000, date)
    page = (out / "index.html").read_text("utf-8")
    assert "{{" not in page
    assert f'href="releases/FortniteAutoReplay-{version}.zip"' in page
    assert f"Version {version}" in page
    assert (out / "styles.css").exists()
    assert not list(out.glob("*.md"))


def test_site_keeps_published_archives(release, tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "site"
    monkeypatch.setattr(release, "SITE_OUT", out)
    (out / "releases").mkdir(parents=True)
    (out / "releases" / "FortniteAutoReplay-0.1.0.zip").write_bytes(b"zip")
    (out / "ancien.html").write_text("x", "utf-8")
    version, date = release.current_release()
    release.render_site(version, "a.zip", 1, date)
    assert (out / "releases" / "FortniteAutoReplay-0.1.0.zip").exists()
    assert not (out / "ancien.html").exists()
