"""Configuration typée, chargée depuis JSON avec surcharge locale.

Deux fichiers : ``config/default.json`` (versionné, valeurs de référence) et
``config/local.json`` (non versionné, écrasement profond). Toute valeur peut
aussi être forcée par la CLI.

Aucune valeur métier n'est codée en dur dans les autres modules : tout passe
par :class:`Config`.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _project_root() -> Path:
    """Racine des fichiers de l'application : config, templates, logs, captures.

    Depuis les sources, la racine du dépôt. Dans l'exécutable empaqueté
    (PyInstaller), le dossier de l'exécutable : le zip distribué y place
    ``config/`` et ``templates/``, et les logs s'y écrivent.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _project_root()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config" / "local.json"


class ConfigError(ValueError):
    """Configuration absente, illisible ou incohérente."""


# Réponses possibles au dialogue « Vous êtes toujours là ? » d'un groupe.
STILL_HERE_BUTTONS = ("ready", "not_yet")  # un bouton du dialogue chacune
STILL_HERE_ANSWERS = (*STILL_HERE_BUTTONS, "ignore")


@dataclass(slots=True)
class CaptureConfig:
    backend: str = "auto"  # auto | dxcam | mss | pil
    monitor: int = 1
    roi: list[int] | None = None  # [left, top, width, height], None = plein écran
    target_fps: float = 12.0
    # réduction appliquée après capture : 0.5 divise par ~5 le coût du matching
    scale: float = 0.5

    def validate(self) -> None:
        if self.backend not in {"auto", "dxcam", "mss", "pil"}:
            raise ConfigError(f"capture.backend inconnu : {self.backend!r}")
        if not 1.0 <= self.target_fps <= 60.0:
            raise ConfigError("capture.target_fps doit être dans [1, 60]")
        if not 0.25 <= self.scale <= 1.0:
            raise ConfigError("capture.scale doit être dans [0.25, 1]")
        if self.roi is not None and (len(self.roi) != 4 or min(self.roi[2:]) <= 0):
            raise ConfigError("capture.roi doit être [left, top, width>0, height>0]")


@dataclass(slots=True)
class VisionConfig:
    backend: str = "auto"  # auto | opencv | numpy
    threshold: float = 0.82
    scales: tuple[float, ...] = (1.0,)
    padding: int = 8  # marge (px de frame) autour d'un bouton déjà vu, recherché au même endroit
    use_zones: bool = True  # chercher chaque template dans sa zone attendue (JSON du template)
    zone_margin: float = 0.12  # marge de la zone attendue, en fraction de la frame
    full_scan_interval_s: float = 2.0  # balayage complet périodique malgré les zones ; 0 = jamais

    def validate(self) -> None:
        if self.backend not in {"auto", "opencv", "numpy"}:
            raise ConfigError(f"vision.backend inconnu : {self.backend!r}")
        if not 0.3 <= self.threshold <= 0.999:
            raise ConfigError("vision.threshold doit être dans [0.3, 0.999]")
        if not self.scales:
            raise ConfigError("vision.scales ne peut pas être vide")
        if any(not 0.3 <= s <= 2.0 for s in self.scales):
            raise ConfigError("vision.scales doit rester dans [0.3, 2.0]")
        if not 0.0 <= self.zone_margin <= 1.0:
            raise ConfigError("vision.zone_margin doit être dans [0, 1]")
        if self.full_scan_interval_s < 0:
            raise ConfigError("vision.full_scan_interval_s doit être >= 0")


@dataclass(slots=True)
class ActionConfig:
    backend: str = "auto"  # auto | pydirectinput | win32 | pyautogui
    move_before_click: bool = True
    move_duration_s: float = 0.06
    click_hold_s: float = 0.05
    click_offset: list[int] = field(default_factory=lambda: [0, 0])
    restore_cursor: bool = False
    # jeu visible mais sans le focus : clic posté dans la file de messages de sa fenêtre,
    # qui n'est jamais activée. « cursor » pose aussi le curseur sur le bouton le temps du
    # clic, puis le remet où il était (le jeu calcule le survol avec le vrai curseur) ;
    # « message » ne touche jamais au curseur.
    background: str = "cursor"  # cursor | message
    # au second plan, Fortnite se met en veille (30 i/s, « inactif ») et ignore la souris :
    # il se croit actif le temps du clic (WM_ACTIVATEAPP), sans que sa fenêtre le soit
    background_activate: bool = True
    background_wait_s: float = 0.1  # laisse au jeu quelques images entre chaque étape du clic
    mouse_idle_s: float = 0.25  # souris immobile, sans bouton enfoncé, avant de déplacer le curseur

    def validate(self) -> None:
        if self.backend not in {"auto", "pydirectinput", "win32", "pyautogui"}:
            raise ConfigError(f"actions.backend inconnu : {self.backend!r}")
        if len(self.click_offset) != 2:
            raise ConfigError("actions.click_offset doit être [dx, dy]")
        if self.background not in {"cursor", "message"}:
            raise ConfigError(f"actions.background inconnu : {self.background!r}")
        if self.background_wait_s < 0 or self.mouse_idle_s < 0:
            raise ConfigError("actions.background_wait_s et mouse_idle_s doivent être positifs")


@dataclass(slots=True)
class StateConfig:
    """Un état = un texte de bouton à reconnaître et l'action associée."""

    slug: str
    label: str
    priority: int
    click: str = "center"  # center | template_origin — où cliquer dans la détection
    require_confirm: bool = True
    cooldown_s: float = 2.0
    post_action_sleep_s: float = 0.8
    clicks: int = 1
    enabled: bool = True
    # réponse au dialogue « Vous êtes toujours là ? » que donne ce bouton (ready |
    # not_yet) ; l'état n'est actif que si c'est la réponse choisie (engine.still_here)
    still_here: str = ""

    def validate(self) -> None:
        if self.click not in {"center", "template_origin"}:
            raise ConfigError(f"states.{self.slug}.click invalide : {self.click!r}")
        if self.still_here not in {"", *STILL_HERE_BUTTONS}:
            raise ConfigError(f"states.{self.slug}.still_here invalide : {self.still_here!r}")
        if self.clicks < 1:
            raise ConfigError(f"states.{self.slug}.clicks doit être >= 1")
        if self.cooldown_s < 0:
            raise ConfigError(f"states.{self.slug}.cooldown_s doit être >= 0")


# États livrés : la boucle de rejeu Fortnite.
DEFAULT_STATES: tuple[StateConfig, ...] = (
    StateConfig(slug="pass_battle", label="Passer", priority=100, cooldown_s=4.0),
    # fin de partie : retour au salon puis « Prêt », plutôt que « Rejouer » (désactivé)
    StateConfig(
        slug="replay",
        label="Rejouer",
        priority=90,
        cooldown_s=3.0,
        post_action_sleep_s=1.5,
        enabled=False,
    ),
    StateConfig(
        slug="back_to_lobby",
        label="Retour au salon",
        priority=90,
        cooldown_s=3.0,
        post_action_sleep_s=1.5,
    ),
    StateConfig(
        slug="continue", label="Continuer", priority=80, cooldown_s=3.0, post_action_sleep_s=1.5
    ),
    StateConfig(slug="ready", label="Prêt", priority=70, cooldown_s=5.0, post_action_sleep_s=2.0),
    # « Jouer » à la place de « Prêt » en solo ou en chef de groupe
    StateConfig(slug="play", label="Jouer", priority=70, cooldown_s=5.0, post_action_sleep_s=2.0),
    # dialogue modal, avec compte à rebours : il passe avant tout le reste
    StateConfig(
        slug="still_here_ready",
        label="Toujours là\u00a0: Prêt",
        priority=110,
        cooldown_s=3.0,
        post_action_sleep_s=1.0,
        still_here="ready",
    ),
    StateConfig(
        slug="still_here_not_yet",
        label="Toujours là\u00a0: Pas encore",
        priority=110,
        cooldown_s=3.0,
        post_action_sleep_s=1.0,
        still_here="not_yet",
    ),
)


@dataclass(slots=True)
class EngineConfig:
    confirm_frames: int = 2
    action_cooldown_s: float = 1.0  # cooldown global entre deux clics, tous états confondus
    max_actions: int = 0  # 0 = illimité
    save_debug_frames: bool = False
    debug_dir: str = "captures"
    start_paused: bool = True
    stop_after_idle_s: float = 0.0  # 0 = jamais
    # pause si le même bouton reste visible après N clics (clic sans effet) ; 0 = jamais
    max_ineffective_clicks: int = 5
    beep_on_stuck: bool = True
    # frame sauvée quand un score tombe dans [threshold - doubt_margin, threshold[ ; 0 = jamais
    doubt_margin: float = 0.06
    doubt_interval_s: float = 60.0  # au plus une sauvegarde par état et par intervalle
    doubt_max_snapshots: int = 20  # par session
    stats_file: str = "logs/sessions.jsonl"  # "" = pas d'historique
    max_step_errors: int = 10  # erreurs inattendues de suite avant la pause ; 0 = jamais
    # réponse au dialogue « Vous êtes toujours là ? » : ready | not_yet | ignore
    still_here: str = "ready"
    sound_on_toggle: bool = True  # deux notes, montantes à la reprise, descendantes à la pause

    def validate(self) -> None:
        if self.still_here not in STILL_HERE_ANSWERS:
            raise ConfigError(
                f"engine.still_here doit valoir {', '.join(STILL_HERE_ANSWERS)} "
                f"(reçu {self.still_here!r})"
            )
        if self.confirm_frames < 1:
            raise ConfigError("engine.confirm_frames doit être >= 1")
        if self.max_actions < 0:
            raise ConfigError("engine.max_actions doit être >= 0")
        if self.max_ineffective_clicks < 0:
            raise ConfigError("engine.max_ineffective_clicks doit être >= 0")
        if not 0.0 <= self.doubt_margin <= 0.3:
            raise ConfigError("engine.doubt_margin doit être dans [0, 0.3]")
        if self.doubt_interval_s < 0 or self.doubt_max_snapshots < 0:
            raise ConfigError("engine.doubt_interval_s et doubt_max_snapshots doivent être >= 0")
        if self.max_step_errors < 0:
            raise ConfigError("engine.max_step_errors doit être >= 0")


@dataclass(slots=True)
class WindowConfig:
    """Fenêtre du jeu : sert de zone de capture et de garde avant chaque clic."""

    enabled: bool = True
    process_names: list[str] = field(default_factory=lambda: ["FortniteClient-Win64-Shipping.exe"])
    window_class: str = "UnrealWindow"  # "" = toutes les classes
    use_as_roi: bool = True  # capturer la zone client du jeu plutôt que le moniteur
    # True : ne rien faire tant que le jeu n'a pas le focus ; False : cliquer aussi quand
    # il est visible au second plan (clic envoyé à sa fenêtre, voir actions.background)
    require_foreground: bool = False

    def validate(self) -> None:
        if self.enabled and not self.process_names:
            raise ConfigError("window.process_names ne peut pas être vide")


@dataclass(slots=True)
class HotkeyConfig:
    enabled: bool = True
    toggle: str = "F8"
    quit: str = "F9"


@dataclass(slots=True)
class PanelConfig:
    """Panneau de contrôle (``fnreplay panel``)."""

    topmost: bool = True  # au-dessus de toutes les fenêtres, jeu compris
    # invisible pour les captures d'écran (celle du bot comprise) : le bot ne le
    # confond jamais avec le jeu, même posé par-dessus
    exclude_from_capture: bool = True
    position: list[int] | None = None  # [x, y] écran ; None = coin libre près du jeu
    collapsed: bool = False  # réduit à sa ligne d'état

    def validate(self) -> None:
        if self.position is not None and len(self.position) != 2:
            raise ConfigError("panel.position doit être [x, y] ou null")


@dataclass(slots=True)
class UpdateConfig:
    """Mises à jour de l'exécutable empaqueté (voir ``updater``)."""

    enabled: bool = True  # vérifier au lancement, puis toutes les 6 h
    manifest_url: str = "https://autoreplay.feitanx.com/latest.json"

    def validate(self) -> None:
        if not self.manifest_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ConfigError("update.manifest_url doit être une adresse https")


def parse_utc(value: str) -> datetime:
    """Date ISO 8601 avec fuseau (« Z » compris, que Python 3.10 ne lit pas), en UTC."""
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ConfigError(f"date illisible : {value!r}") from exc
    if parsed.tzinfo is None:
        raise ConfigError(f"date sans fuseau : {value!r} (ajouter Z pour UTC)")
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class ChestsConfig:
    """Compteur des coffres ouverts, d'après les replays de Fortnite (voir ``chests``)."""

    enabled: bool = True  # compter les coffres dans les replays
    demos_dir: str = "%LOCALAPPDATA%/FortniteGame/Saved/Demos"  # variables d'environnement lues
    reader: str = "replay-reader/replay-reader.exe"  # lecteur de replays (relatif à l'appli)
    store_file: str = "logs/chests.json"  # parties comptées (le jeu efface ses vieux replays)
    scan_interval_s: float = 10.0

    def validate(self) -> None:
        if self.scan_interval_s < 2:
            raise ConfigError("chests.scan_interval_s doit être >= 2")
        if not self.reader:
            raise ConfigError("chests.reader ne peut pas être vide")

    def resolved_demos_dir(self) -> Path:
        return Path(os.path.expandvars(self.demos_dir))


@dataclass(slots=True)
class Config:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    actions: ActionConfig = field(default_factory=ActionConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    panel: PanelConfig = field(default_factory=PanelConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    chests: ChestsConfig = field(default_factory=ChestsConfig)
    states: list[StateConfig] = field(
        default_factory=lambda: [copy.copy(s) for s in DEFAULT_STATES]
    )
    template_dir: str = "templates"
    log_level: str = "INFO"

    def validate(self) -> None:
        self.capture.validate()
        self.vision.validate()
        self.actions.validate()
        self.engine.validate()
        self.window.validate()
        self.panel.validate()
        self.update.validate()
        self.chests.validate()
        seen: set[str] = set()
        for state in self.states:
            state.validate()
            if state.slug in seen:
                raise ConfigError(f"état dupliqué : {state.slug}")
            seen.add(state.slug)

    def enabled_states(self) -> list[StateConfig]:
        """États activés, réponses au dialogue comprises (templates à charger)."""
        return sorted((s for s in self.states if s.enabled), key=lambda s: -s.priority)

    def active_states(self) -> list[StateConfig]:
        """États recherchés, du plus prioritaire au moins prioritaire.

        Des boutons du dialogue « Vous êtes toujours là ? », seul celui de la
        réponse choisie (``engine.still_here``) est recherché.
        """
        answer = self.engine.still_here
        return [s for s in self.enabled_states() if s.still_here in ("", answer)]

    def resolved_template_dir(self) -> Path:
        return resolve_path(self.template_dir)

    def resolved_debug_dir(self) -> Path:
        return resolve_path(self.engine.debug_dir)

    def resolved_stats_file(self) -> Path | None:
        return resolve_path(self.engine.stats_file) if self.engine.stats_file else None

    def resolved_chests_file(self) -> Path:
        return resolve_path(self.chests.store_file)

    def resolved_reader(self) -> Path:
        return resolve_path(self.chests.reader)


def resolve_path(value: str) -> Path:
    """Chemin de config : absolu tel quel, relatif à la racine du projet sinon."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


# réglages de l'estimation d'avant le compteur (0.4.0), laissés dans config/local.json
_OLD_CHESTS_KEYS = frozenset({"logs_dir", "log_glob", "match_marker", "default_rate", "milestones"})


def _to_dataclass(cls: type, raw: dict[str, Any]) -> Any:
    """Instancie ``cls`` depuis un dict, en ignorant les clés inconnues."""
    known = {f.name for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known:
            log.warning("clé de config ignorée : %s.%s", cls.__name__, key)
            continue
        kwargs[key] = value
    return cls(**kwargs)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration introuvable : {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON invalide dans {path} : {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} doit contenir un objet JSON")
    return data


def load_config(
    path: str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    use_local: bool = True,
) -> Config:
    """Charge la configuration puis valide l'ensemble."""
    source = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = _read_json(source)
    if use_local:
        local = source.with_name("local.json")
        if local.exists() and local != source:
            raw = _deep_merge(raw, _read_json(local))
    if overrides:
        raw = _deep_merge(raw, overrides)

    states_raw = raw.pop("states", None)
    cfg = Config(
        capture=_to_dataclass(CaptureConfig, raw.get("capture", {})),
        vision=_to_dataclass(VisionConfig, raw.get("vision", {})),
        actions=_to_dataclass(ActionConfig, raw.get("actions", {})),
        engine=_to_dataclass(EngineConfig, raw.get("engine", {})),
        window=_to_dataclass(WindowConfig, raw.get("window", {})),
        hotkeys=_to_dataclass(HotkeyConfig, raw.get("hotkeys", {})),
        panel=_to_dataclass(PanelConfig, raw.get("panel", {})),
        update=_to_dataclass(UpdateConfig, raw.get("update", {})),
        chests=_to_dataclass(
            ChestsConfig,
            {k: v for k, v in raw.get("chests", {}).items() if k not in _OLD_CHESTS_KEYS},
        ),
        template_dir=raw.get("template_dir", "templates"),
        log_level=raw.get("log_level", "INFO"),
    )
    if isinstance(cfg.vision.scales, list):
        cfg.vision.scales = tuple(float(s) for s in cfg.vision.scales)
    if states_raw is not None:
        cfg.states = [_to_dataclass(StateConfig, s) for s in states_raw]

    cfg.validate()
    return cfg


def save_local_config(cfg: Config, path: str | Path = LOCAL_CONFIG_PATH) -> Path:
    """Écrit la config courante dans le fichier local (surcharge)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    sections = {
        "capture": cfg.capture,
        "vision": cfg.vision,
        "actions": cfg.actions,
        "engine": cfg.engine,
        "window": cfg.window,
        "hotkeys": cfg.hotkeys,
        "panel": cfg.panel,
        "update": cfg.update,
        "chests": cfg.chests,
    }
    for name, section in sections.items():
        payload[name] = {f.name: getattr(section, f.name) for f in fields(section)}
    payload["states"] = [{f.name: getattr(s, f.name) for f in fields(s)} for s in cfg.states]
    payload["template_dir"] = cfg.template_dir
    payload["log_level"] = cfg.log_level
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def update_local_config(changes: dict[str, Any], path: str | Path = LOCAL_CONFIG_PATH) -> Path:
    """Fusionne ``changes`` dans le fichier local, sans toucher au reste.

    Seules les clés données sont écrites : les autres valeurs continuent de
    suivre ``default.json``. Écriture atomique (fichier temporaire puis
    remplacement), pour ne jamais laisser un JSON tronqué.
    """
    target = Path(path)
    current = _read_json(target) if target.exists() else {}
    merged = _deep_merge(current, changes)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def describe(cfg: Config) -> str:
    return json.dumps(
        {
            "capture": {
                "backend": cfg.capture.backend,
                "roi": cfg.capture.roi,
                "target_fps": cfg.capture.target_fps,
                "scale": cfg.capture.scale,
            },
            "vision": {
                "backend": cfg.vision.backend,
                "threshold": cfg.vision.threshold,
                "scales": list(cfg.vision.scales),
                "use_zones": cfg.vision.use_zones,
                "full_scan_interval_s": cfg.vision.full_scan_interval_s,
            },
            "actions": {
                "backend": cfg.actions.backend,
                "move_before_click": cfg.actions.move_before_click,
                "background": cfg.actions.background,
                "background_activate": cfg.actions.background_activate,
            },
            "engine": {
                "confirm_frames": cfg.engine.confirm_frames,
                "action_cooldown_s": cfg.engine.action_cooldown_s,
                "start_paused": cfg.engine.start_paused,
                "max_ineffective_clicks": cfg.engine.max_ineffective_clicks,
                "doubt_margin": cfg.engine.doubt_margin,
                "still_here": cfg.engine.still_here,
            },
            "window": {
                "enabled": cfg.window.enabled,
                "process_names": cfg.window.process_names,
                "use_as_roi": cfg.window.use_as_roi,
                "require_foreground": cfg.window.require_foreground,
            },
            "states": [
                {"slug": s.slug, "label": s.label, "priority": s.priority, "enabled": s.enabled}
                for s in cfg.active_states()
            ],
        },
        indent=2,
        ensure_ascii=False,
    )
