"""Interface en ligne de commande."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from . import __version__, diagnostics, history, updater, vision
from . import config as config_mod
from .actions import Actions, InputError
from .capture import CaptureError, ScreenCapture
from .chests import ChestTracker
from .engine import Engine
from .hotkeys import HotkeyListener
from .logging_setup import LOG_DIR, setup_logging
from .templates import TemplateLibrary
from .window import GameWindow, WindowError, ensure_dpi_awareness

log = logging.getLogger("fortnite_auto_replay.cli")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="fichier de configuration alternatif")
    parser.add_argument("--no-local", action="store_true", help="ignorer config/local.json")
    parser.add_argument("--fps", type=float, help="fréquence de capture cible")
    parser.add_argument("--threshold", type=float, help="seuil de correspondance (0.3-0.999)")
    parser.add_argument("--roi", help="région d'intérêt left,top,width,height")
    parser.add_argument("--monitor", type=int, help="index du moniteur (0 = tous)")
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="ne pas suivre la fenêtre du jeu (capture du moniteur, aucun contrôle du focus)",
    )
    parser.add_argument("--dry-run", action="store_true", help="détecter sans cliquer")
    parser.add_argument("--debug", action="store_true", help="sauvegarder les frames annotées")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs de débogage")


def _build_config(args: argparse.Namespace) -> config_mod.Config:
    overrides: dict[str, object] = {}
    if args.fps is not None:
        overrides.setdefault("capture", {})["target_fps"] = args.fps  # type: ignore[index]
    if args.monitor is not None:
        overrides.setdefault("capture", {})["monitor"] = args.monitor  # type: ignore[index]
    if args.roi:
        try:
            roi = [int(part) for part in args.roi.split(",")]
        except ValueError as exc:
            raise config_mod.ConfigError(f"--roi invalide : {args.roi!r}") from exc
        overrides.setdefault("capture", {})["roi"] = roi  # type: ignore[index]
    if args.threshold is not None:
        overrides.setdefault("vision", {})["threshold"] = args.threshold  # type: ignore[index]
    if args.debug:
        overrides.setdefault("engine", {})["save_debug_frames"] = True  # type: ignore[index]
    if args.no_window:
        overrides.setdefault("window", {})["enabled"] = False  # type: ignore[index]
    return config_mod.load_config(args.config, overrides=overrides, use_local=not args.no_local)


def _prepare_library(cfg: config_mod.Config) -> TemplateLibrary:
    """Templates de tous les états activés : la réponse au dialogue peut changer en route."""
    library = TemplateLibrary(cfg.resolved_template_dir())
    library.load([s.slug for s in cfg.enabled_states()])
    log.info(library.summary())
    for message in library.errors:
        log.warning("template en erreur : %s", message)
    return library


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    if cfg.engine.start_paused and args.no_pause:
        cfg.engine.start_paused = False
    if args.max_actions:
        cfg.engine.max_actions = args.max_actions
    library = _prepare_library(cfg)
    if not library.states:
        log.error(
            "aucun template trouvé dans %s — lance 'python -m "
            "fortnite_auto_replay.tools.capture_templates'",
            cfg.resolved_template_dir(),
        )
        return 2

    listener: HotkeyListener | None = None
    try:
        window = GameWindow(cfg.window) if cfg.window.enabled else None
        roi_window = window if cfg.window.use_as_roi else None
        with ScreenCapture(cfg.capture, window=roi_window) as capture:
            actions = Actions(cfg.actions, dry_run=args.dry_run)
            engine = Engine(cfg, capture, actions, library, dry_run=args.dry_run, window=window)

            if cfg.hotkeys.enabled:
                listener = HotkeyListener(active=game_in_front(window))
                listener.bind(cfg.hotkeys.toggle, engine.toggle)
                listener.bind(cfg.hotkeys.quit, engine.stop)
                listener.start()
                log.info(
                    "raccourcis : %s = pause/reprise, %s = quitter",
                    cfg.hotkeys.toggle,
                    cfg.hotkeys.quit,
                )
            if cfg.engine.start_paused:
                log.info("démarrage en pause — appuie sur %s pour lancer", cfg.hotkeys.toggle)

            engine.run()
            print(json.dumps(engine.stats.as_dict(), indent=2, ensure_ascii=False))
            return 0
    except (CaptureError, InputError, RuntimeError, config_mod.ConfigError) as exc:
        # WindowError hérite de RuntimeError
        log.error("échec : %s", exc)
        return 1
    finally:
        if listener is not None:
            listener.stop()


def game_in_front(window: GameWindow | None) -> Callable[[], bool] | None:
    """Condition des raccourcis : jeu au premier plan (F8 tapé ailleurs ne compte pas)."""
    if window is None:
        return None
    return lambda: window.focus() == "foreground"


def _check_window(cfg: config_mod.Config) -> GameWindow | None:
    """Fenêtre du jeu pour ``check`` ; à défaut, ``check`` capture le moniteur."""
    if not cfg.window.enabled or not cfg.window.use_as_roi:
        return None
    window = GameWindow(cfg.window)
    if window.region() is None:
        log.warning("fenêtre du jeu introuvable ou réduite : capture du moniteur entier")
        return None
    if window.focus() != "foreground":
        log.warning("le jeu n'est pas au premier plan : la capture montre ce qui le recouvre")
    return window


def cmd_check(args: argparse.Namespace) -> int:
    """Capture une frame, affiche les meilleurs scores, n'agit pas."""
    cfg = _build_config(args)
    library = _prepare_library(cfg)
    if not library.states:
        log.error("aucun template chargé")
        return 2

    try:
        with ScreenCapture(cfg.capture, window=_check_window(cfg)) as capture:
            frame = capture.grab()
            log.info("frame %dx%d via %s", frame.width, frame.height, capture.backend_name)
            report: dict[str, list[dict[str, object]]] = {}
            matches: list[vision.Match] = []
            gray = vision.to_gray(frame.image)
            for state in cfg.enabled_states():
                entries: list[dict[str, object]] = []
                prepared = library.prepared(
                    state.slug,
                    (frame.width, frame.height),
                    frame.window_size,
                    frame.scale,
                    cfg.vision.zone_margin,
                )
                for item in prepared:
                    match = vision.best_match(
                        gray,
                        item.image,
                        label=item.name,
                        threshold=0.01,
                        scales=cfg.vision.scales,
                        backend=cfg.vision.backend,
                    )
                    if match is None:
                        continue
                    left, top = frame.to_screen(match.x, match.y)
                    entry: dict[str, object] = {
                        "template": item.name,
                        "score": round(match.score, 4),
                        # position à l'écran : left, top, largeur, hauteur
                        "pos": [
                            left,
                            top,
                            round(match.width / frame.scale),
                            round(match.height / frame.scale),
                        ],
                    }
                    if item.zone is not None:
                        cx, cy = match.center
                        zl, zt, zr, zb = item.zone
                        entry["in_zone"] = zl <= cx < zr and zt <= cy < zb
                    entries.append(entry)
                    if match.score >= cfg.vision.threshold:
                        matches.append(match)
                entries.sort(key=lambda e: float(e["score"]), reverse=True)
                report[state.slug] = entries

            print(json.dumps(report, indent=2, ensure_ascii=False))
            out = cfg.resolved_debug_dir() / f"check-{int(time.time())}.png"
            vision.save_image(out, vision.annotate(frame.image, matches))
            log.info("frame annotée : %s", out)
            log.info("%d détection(s) au-dessus du seuil %.2f", len(matches), cfg.vision.threshold)
            return 0 if matches else 3
    except (CaptureError, InputError, WindowError) as exc:
        log.error("échec : %s", exc)
        return 1


def cmd_window(args: argparse.Namespace) -> int:
    """Affiche la fenêtre du jeu repérée, sans rien capturer ni cliquer."""
    cfg = _build_config(args)
    try:
        window = GameWindow(cfg.window)
    except WindowError as exc:
        log.error("échec : %s", exc)
        return 1
    found = window.locate()
    if found is None:
        log.error("aucune fenêtre %s visible", " / ".join(cfg.window.process_names))
        return 3
    report = asdict(found)
    report["focus"] = window.focus()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Totaux des sessions enregistrées (hors dry-run)."""
    cfg = _build_config(args)
    path = cfg.resolved_stats_file()
    if path is None:
        log.error("historique désactivé (engine.stats_file vide)")
        return 2
    print(json.dumps(history.summarize(history.read_sessions(path)), indent=2, ensure_ascii=False))
    return 0


def cmd_chests(args: argparse.Namespace) -> int:
    """Lit la suite des journaux du jeu et affiche l'estimation des coffres ouverts."""
    cfg = _build_config(args)
    tracker = ChestTracker(cfg.chests, cfg.resolved_matches_file())
    store = tracker.refresh()
    estimate = tracker.estimate()
    report = {
        "journaux": str(cfg.chests.resolved_logs_dir()),
        "parties_lues": len(store.matches),
        "depuis": store.tracked_since.isoformat() if store.tracked_since else None,
        "paliers": len(cfg.chests.milestones),
        "estimation": asdict(estimate) if estimate else None,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if estimate is None:
        log.info("pas d'estimation : il faut deux paliers lus dans les journaux (voir README)")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    print(config_mod.describe(cfg))
    print(f"\ntemplates : {cfg.resolved_template_dir()}")
    return 0


def _panel_error(message: str) -> int:
    """Erreur de démarrage du panneau : lancé sans console, il doit l'afficher."""
    log.error("panneau : %s", message)
    _show_error(
        f"Le panneau n\u2019a pas pu démarrer\u00a0: {message}.\n\nDétail dans logs/bot.log."
    )
    return 1


def _show_error(text: str) -> None:
    """Message d'erreur dans une boîte de dialogue : lancé sans console, rien ne s'afficherait."""
    try:
        import tkinter  # noqa: PLC0415
        from tkinter import messagebox  # noqa: PLC0415

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("Fortnite AutoReplay", text, parent=root)
        root.destroy()
    except Exception:  # pas d'affichage possible : le log suffit
        log.exception("message d'erreur non affiché")


def cmd_panel(args: argparse.Namespace) -> int:
    """Panneau de contrôle au-dessus du jeu, moteur dans un thread."""
    from . import panel  # noqa: PLC0415 - tkinter seulement pour cette commande

    try:
        cfg = _build_config(args)
    except config_mod.ConfigError as exc:
        return _panel_error(f"configuration invalide ({exc})")
    if args.no_pause:  # relance après une mise à jour : le bot tournait
        cfg.engine.start_paused = False
    library = _prepare_library(cfg)
    if not library.states:
        return _panel_error(f"aucun modèle de bouton dans {cfg.resolved_template_dir()}")
    updates = _panel_updates(cfg, args.argv)
    try:
        code = panel.run_panel(cfg, library, dry_run=args.dry_run, updates=updates)
    except (InputError, RuntimeError) as exc:  # WindowError hérite de RuntimeError
        return _panel_error(str(exc))
    finally:
        if updates is not None:
            updates.stop()
    if updates is not None and updates.install_requested:
        _install_update(updates)
    return code


def _panel_updates(cfg: config_mod.Config, argv: list[str]) -> updater.Updates | None:
    """Suivi des mises à jour : exécutable empaqueté seulement (les sources suivent git)."""
    root = updater.frozen_root()
    if root is None or not cfg.update.enabled:
        return None
    return updater.Updates(root, cfg.update.manifest_url, relaunch_args=relaunch_options(argv))


def relaunch_options(argv: list[str]) -> list[str]:
    """Options du panneau à reprendre quand il se relance (``--dry-run``, ``--config``…).

    Sans la commande ``panel`` (l'exécutable ouvre le panneau quand il ne reçoit
    que des options) ni ``--no-pause``, ajouté seulement si le bot tournait. Le
    chemin de ``--config`` devient absolu : la relance part du dossier de
    l'application.
    """
    options = argv[1:] if argv[:1] == ["panel"] else list(argv)
    kept: list[str] = []
    pending_config = False
    for option in options:
        if pending_config:
            kept.append(str(Path(option).resolve()))
            pending_config = False
        elif option == "--config":
            kept.append(option)
            pending_config = True
        elif option.startswith("--config="):
            kept.append("--config=" + str(Path(option.split("=", 1)[1]).resolve()))
        elif option != "--no-pause":
            kept.append(option)
    return kept


def _install_update(updates: updater.Updates) -> None:
    """Panneau fermé, moteur arrêté : remplace le programme, relance la nouvelle version.

    En cas d'échec, tout est remis en place et la version actuelle est relancée.
    """
    try:
        updates.install_pending()
    except updater.UpdateError as exc:
        log.error("mise à jour : %s", exc)
        _show_error(
            f"La mise à jour n\u2019a pas pu s\u2019installer\u00a0: {exc}.\n\n"
            "La version actuelle est conservée, détail dans logs/bot.log."
        )
        updater.relaunch(updates.root, updates.relaunch_args)  # en pause


def cmd_update(args: argparse.Namespace) -> int:
    """Vérifie la dernière version publiée ; ``--install`` l'installe (exécutable seulement)."""
    cfg = _build_config(args)
    try:
        release = updater.check(args.manifest or cfg.update.manifest_url)
    except updater.UpdateError as exc:
        log.error("mise à jour : %s", exc)
        return 1
    if release is None:
        print(f"Version {__version__} à jour.")
        return 0
    print(f"Version {release.version} disponible (version actuelle : {__version__}).")
    if not args.install:
        return 0
    root = updater.frozen_root()
    if root is None:
        log.error("mise à jour : seulement depuis l'exécutable (depuis les sources : git pull)")
        return 2
    folder = root / updater.UPDATE_DIR / release.version
    try:
        archive = updater.download(release, folder / f"FortniteAutoReplay-{release.version}.zip")
        updater.install(updater.extract(archive, folder / "new"), root)
    except updater.UpdateError as exc:
        log.error("mise à jour : %s", exc)
        return 1
    log.info("mise à jour : version %s installée", release.version)
    if not args.no_restart:
        updater.relaunch(root, [])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fnreplay",
        description="Bot de rejeu automatique Fortnite par reconnaissance d'écran.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="lancer la boucle de rejeu")
    _add_common(run)
    run.add_argument("--no-pause", action="store_true", help="démarrer sans attendre le raccourci")
    run.add_argument("--max-actions", type=int, help="s'arrêter après N clics")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="vérifier la détection sur une capture unique")
    _add_common(check)
    check.set_defaults(func=cmd_check)

    show = sub.add_parser("config", help="afficher la configuration effective")
    _add_common(show)
    show.set_defaults(func=cmd_config)

    win = sub.add_parser("window", help="afficher la fenêtre du jeu repérée")
    _add_common(win)
    win.set_defaults(func=cmd_window)

    panel = sub.add_parser("panel", help="panneau de contrôle au-dessus du jeu")
    _add_common(panel)
    panel.add_argument("--no-pause", action="store_true", help="démarrer le bot sans attendre")
    panel.set_defaults(func=cmd_panel)

    update = sub.add_parser("update", help="vérifier ou installer la dernière version")
    _add_common(update)
    update.add_argument("--manifest", help="adresse du manifeste (défaut : update.manifest_url)")
    update.add_argument("--install", action="store_true", help="télécharger puis installer")
    update.add_argument("--no-restart", action="store_true", help="ne pas relancer après")
    update.set_defaults(func=cmd_update)

    stats = sub.add_parser("stats", help="totaux des sessions enregistrées")
    _add_common(stats)
    stats.set_defaults(func=cmd_stats)

    chests = sub.add_parser("chests", help="estimation des coffres ouverts (journaux du jeu)")
    _add_common(chests)
    chests.set_defaults(func=cmd_chests)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.argv = list(sys.argv[1:] if argv is None else argv)  # repris à la relance du panneau
    setup_logging("DEBUG" if args.verbose else "INFO")
    diagnostics.install(LOG_DIR)
    ensure_dpi_awareness()
    try:
        return int(args.func(args))
    except config_mod.ConfigError as exc:
        log.error("configuration invalide : %s", exc)
        return 2
    except KeyboardInterrupt:
        log.info("interrompu par l'utilisateur")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
