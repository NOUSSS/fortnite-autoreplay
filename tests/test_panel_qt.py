"""Le panneau Qt dans une vraie boucle d'événements, hors écran (rien ne s'affiche)."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import mss
import pytest
from PIL import Image
from PySide6.QtCore import QTimer

from fortnite_auto_replay import config as config_mod
from fortnite_auto_replay.panel import Panel, qt_app
from fortnite_auto_replay.panel_glass import GlassFeed, GlassWorker, blur
from fortnite_auto_replay.panel_style import qcolor
from fortnite_auto_replay.panel_widgets import Button
from fortnite_auto_replay.tools.panel_shots import SCENES, DemoController, DemoUpdates


def test_quit_ends_the_event_loop_with_windows_shown() -> None:
    """Fenêtres montrées, l'arrêt demandé (Quitter, F9, WM_CLOSE) sort de la boucle.

    ``QApplication.quit`` redemanderait la fermeture des fenêtres, que le panneau
    refuse pour passer par l'arrêt du moteur : il tournerait sans fin.
    """
    app = qt_app()
    cfg = config_mod.load_config(None, use_local=False)
    controller = DemoController(cfg)
    panel = Panel(controller, cfg.panel, persist=False, follow_cursor=False, show=False)
    panel.window.show()
    panel.window.close()  # WM_CLOSE : refusé par la fenêtre, arrêt du moteur demandé
    assert controller.closing
    QTimer.singleShot(0, panel._poll)
    QTimer.singleShot(3000, lambda: app.exit(1))  # filet : la boucle ne finirait pas
    try:
        assert app.exec() == 0
    finally:
        panel.window.hide()


@pytest.mark.parametrize("phase", ["available", "failed"])
def test_update_notice_shows_its_message(phase: str) -> None:
    """Mise à jour proposée ou échouée : le bandeau s'affiche, pas seulement sa marge."""
    qt_app()
    cfg = config_mod.load_config(None, use_local=False)
    controller = DemoController(cfg)
    scene = next(s for s in SCENES if s.update == phase)
    controller.scene = scene
    updates = DemoUpdates(controller)
    panel = Panel(
        controller, cfg.panel, persist=False, updates=updates, follow_cursor=False, show=False
    )
    panel.expanded = True
    panel._render(controller.status())
    assert panel.update_notice.isVisibleTo(panel.card)
    assert panel.update_notice.height() > 0


def test_update_notice_keeps_one_button() -> None:
    """« Mettre à jour » devient « Réessayer » : l'ancien bouton disparaît aussitôt."""
    qt_app()
    cfg = config_mod.load_config(None, use_local=False)
    controller = DemoController(cfg)
    panel = Panel(controller, cfg.panel, persist=False, follow_cursor=False, show=False)
    notice = panel.update_notice
    notice.show_message("info", "La version 0.6.0 est disponible.", ("Mettre à jour", print))
    notice.show_message("caution", "La mise à jour a échoué.", ("Réessayer", print))
    shown = [b for b in notice.findChildren(Button) if b.isVisibleTo(notice)]
    assert shown == [notice.button]


class _Shot:
    size = (16, 16)
    bgra = bytes(16 * 16 * 4)


class _FlakySct:
    """Capture refusée une fois (session verrouillée), puis rétablie."""

    calls = 0

    def grab(self, _zone: dict[str, int]) -> _Shot:
        _FlakySct.calls += 1
        if _FlakySct.calls == 1:
            raise OSError("BitBlt")
        return _Shot()

    def close(self) -> None:
        pass


def test_glass_comes_back_after_a_failed_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mss, "MSS", _FlakySct, raising=False)
    feed = GlassFeed()
    assert feed.grab(0, 0, 16, 16) is None
    assert feed.grab(0, 0, 16, 16) is not None


def test_qcolor_keeps_the_colour_transparency() -> None:
    assert qcolor("#80FFFFFF").alpha() == 128
    assert qcolor("#80FFFFFF", 0.5).alpha() == 64
    assert qcolor("#0B1715", 0.58).alphaF() == pytest.approx(0.58, abs=0.01)


def test_blur_works_on_a_small_image() -> None:
    image = Image.new("RGB", (320, 440), (200, 40, 40))
    small = blur(image)
    assert small.size == (40, 55)
    assert blur(Image.new("RGB", (5, 5))).size == (5, 5)  # trop petite pour être réduite


class _FakeFeed:
    def __init__(self) -> None:
        self.zones: list[tuple[int, int, int, int]] = []

    def grab(self, x: int, y: int, width: int, height: int) -> None:
        self.zones.append((x, y, width, height))


def test_glass_worker_captures_off_the_panel_thread() -> None:
    """Les captures partent dans le thread « verre » ; le résultat revient au panneau."""
    app = qt_app()
    worker = GlassWorker()
    feed = _FakeFeed()
    worker._feed = feed  # type: ignore[assignment]
    received: list[object] = []
    worker.ready.connect(received.append)
    try:
        assert worker.request([("pill", (-1900, 8, 200, 32))])
        deadline = time.monotonic() + 2
        while not received and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
    finally:
        worker.stop()
    assert received == [[("pill", None)]]
    assert feed.zones == [(-1900, 8, 200, 32)]  # écran de gauche : x négatif
