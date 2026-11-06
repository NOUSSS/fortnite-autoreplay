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
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.json"
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config" / "local.json"


class ConfigError(ValueError):
    """Configuration absente, illisible ou incohérente."""


@dataclass(slots=True)
class CaptureConfig:
    backend: str = "auto"  # auto | dxcam | mss | pil
    monitor: int = 1
    roi: list[int] | None = None  # [left, top, width, height], None = plein écran
    target_fps: float = 8.0
    scale: float = 1.0  # réduction appliquée après capture (0.5 = 2x plus rapide)

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
    padding: int = 8  # marge autour de la zone de clic, pour la recherche en pyramide

    def validate(self) -> None:
        if self.backend not in {"auto", "opencv", "numpy"}:
            raise ConfigError(f"vision.backend inconnu : {self.backend!r}")
        if not 0.3 <= self.threshold <= 0.999:
            raise ConfigError("vision.threshold doit être dans [0.3, 0.999]")
        if not self.scales:
            raise ConfigError("vision.scales ne peut pas être vide")
        if any(not 0.3 <= s <= 2.0 for s in self.scales):
            raise ConfigError("vision.scales doit rester dans [0.3, 2.0]")


@dataclass(slots=True)
class ActionConfig:
    backend: str = "auto"  # auto | pydirectinput | win32 | pyautogui
    move_before_click: bool = True
    move_duration_s: float = 0.06
    click_hold_s: float = 0.05
    click_offset: list[int] = field(default_factory=lambda: [0, 0])
    restore_cursor: bool = False

    def validate(self) -> None:
        if self.backend not in {"auto", "pydirectinput", "win32", "pyautogui"}:
            raise ConfigError(f"actions.backend inconnu : {self.backend!r}")
        if len(self.click_offset) != 2:
            raise ConfigError("actions.click_offset doit être [dx, dy]")


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

    def validate(self) -> None:
        if self.click not in {"center", "template_origin"}:
            raise ConfigError(f"states.{self.slug}.click invalide : {self.click!r}")
        if self.clicks < 1:
            raise ConfigError(f"states.{self.slug}.clicks doit être >= 1")
        if self.cooldown_s < 0:
            raise ConfigError(f"states.{self.slug}.cooldown_s doit être >= 0")


# États livrés : la boucle de rejeu Fortnite.
DEFAULT_STATES: tuple[StateConfig, ...] = (
    StateConfig(slug="pass_battle", label="Passer", priority=100, cooldown_s=4.0),
    StateConfig(
        slug="replay", label="Rejouer", priority=90, cooldown_s=3.0, post_action_sleep_s=1.5
    ),
    StateConfig(
        slug="continue", label="Continuer", priority=80, cooldown_s=3.0, post_action_sleep_s=1.5
    ),
    StateConfig(slug="ready", label="Prêt", priority=70, cooldown_s=5.0, post_action_sleep_s=2.0),
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

    def validate(self) -> None:
        if self.confirm_frames < 1:
            raise ConfigError("engine.confirm_frames doit être >= 1")
        if self.max_actions < 0:
            raise ConfigError("engine.max_actions doit être >= 0")


@dataclass(slots=True)
class HotkeyConfig:
    enabled: bool = True
    toggle: str = "F8"
    quit: str = "F9"


@dataclass(slots=True)
class Config:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    actions: ActionConfig = field(default_factory=ActionConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
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
        seen: set[str] = set()
        for state in self.states:
            state.validate()
            if state.slug in seen:
                raise ConfigError(f"état dupliqué : {state.slug}")
            seen.add(state.slug)

    def active_states(self) -> list[StateConfig]:
        """États activés, du plus prioritaire au moins prioritaire."""
        return sorted((s for s in self.states if s.enabled), key=lambda s: -s.priority)

    def resolved_template_dir(self) -> Path:
        path = Path(self.template_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path


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
        hotkeys=_to_dataclass(HotkeyConfig, raw.get("hotkeys", {})),
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
        "hotkeys": cfg.hotkeys,
    }
    for name, section in sections.items():
        payload[name] = {f.name: getattr(section, f.name) for f in fields(section)}
    payload["states"] = [{f.name: getattr(s, f.name) for f in fields(s)} for s in cfg.states]
    payload["template_dir"] = cfg.template_dir
    payload["log_level"] = cfg.log_level
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
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
            },
            "actions": {
                "backend": cfg.actions.backend,
                "move_before_click": cfg.actions.move_before_click,
            },
            "engine": {
                "confirm_frames": cfg.engine.confirm_frames,
                "action_cooldown_s": cfg.engine.action_cooldown_s,
                "start_paused": cfg.engine.start_paused,
            },
            "states": [
                {"slug": s.slug, "label": s.label, "priority": s.priority, "enabled": s.enabled}
                for s in cfg.active_states()
            ],
        },
        indent=2,
        ensure_ascii=False,
    )
