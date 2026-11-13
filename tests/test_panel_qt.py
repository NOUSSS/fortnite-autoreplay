"""Le panneau Qt dans une vraie boucle d'événements, hors écran (rien ne s'affiche)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer

from fortnite_auto_replay import config as config_mod
from fortnite_auto_replay.panel import Panel, qt_app
from fortnite_auto_replay.tools.panel_shots import DemoController


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
