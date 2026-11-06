"""Boucle principale : capture → détection → décision → clic.

Machine à états volontairement simple : à chaque frame on cherche tous les
boutons connus, on garde le plus prioritaire au-dessus du seuil, on exige
plusieurs frames consécutives identiques, puis on clique si les cooldowns le
permettent. Rien n'est bloquant : une détection ratée n'arrête pas le bot.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import vision
from .actions import Actions
from .capture import CaptureError, Frame, ScreenCapture
from .config import Config, StateConfig
from .templates import Template, TemplateLibrary

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Detection:
    state: StateConfig
    template: Template
    match: vision.Match


@dataclass(slots=True)
class Stats:
    frames: int = 0
    captures_failed: int = 0
    actions: int = 0
    per_state: dict[str, int] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)

    @property
    def uptime_s(self) -> float:
        return time.time() - self.started_at

    def as_dict(self) -> dict[str, object]:
        return {
            "frames": self.frames,
            "captures_failed": self.captures_failed,
            "actions": self.actions,
            "per_state": dict(self.per_state),
            "uptime_s": round(self.uptime_s, 1),
        }


class Engine:
    def __init__(
        self,
        cfg: Config,
        capture: ScreenCapture,
        actions: Actions,
        library: TemplateLibrary,
        *,
        dry_run: bool = False,
    ) -> None:
        self.cfg = cfg
        self.capture = capture
        self.actions = actions
        self.library = library
        self.dry_run = dry_run

        self.states = [s for s in cfg.active_states() if library.for_state(s.slug)]
        if not self.states:
            raise RuntimeError("aucun état avec template : impossible de démarrer")

        self.stats = Stats()
        self._candidate: str | None = None
        self._candidate_hits = 0
        self._last_action_at = 0.0
        self._last_action_per_state: dict[str, float] = {}
        self._last_seen: dict[str, tuple[int, int, int, int]] = {}
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._debug_dir = Path(cfg.engine.debug_dir)
        if cfg.engine.save_debug_frames:
            self._debug_dir.mkdir(parents=True, exist_ok=True)

    # --- contrôle externe ---------------------------------------------
    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        self._paused.set()
        log.info("bot en pause")

    def resume(self) -> None:
        self._paused.clear()
        self._candidate = None
        self._candidate_hits = 0
        log.info("bot actif")

    def toggle(self) -> None:
        self.pause() if not self.paused else self.resume()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # --- recherche ----------------------------------------------------
    def _search(self, frame: Frame, state: StateConfig) -> Detection | None:
        """Cherche les templates d'un état, en se limitant à la zone connue."""
        best: Detection | None = None
        offset_x = offset_y = 0
        region = frame.image
        if state.slug in self._last_seen:
            pad = self.cfg.vision.padding
            x, y, w, h = self._last_seen[state.slug]
            offset_x, offset_y = x - pad, y - pad
            region = vision.crop(frame.image, offset_x, offset_y, x + w + pad, y + h + pad)
            if region.size == 0:
                region = frame.image
                offset_x = offset_y = 0

        for template in self.library.for_state(state.slug):
            match = vision.best_match(
                region,
                template.image,
                label=f"{state.slug}:{template.name}",
                threshold=self.cfg.vision.threshold,
                scales=self.cfg.vision.scales,
                backend=self.cfg.vision.backend,
            )
            if match is None:
                continue
            match.x += offset_x
            match.y += offset_y
            if best is None or match.score > best.match.score:
                best = Detection(state=state, template=template, match=match)
        return best

    def _detect(self, frame: Frame) -> list[Detection]:
        found: list[Detection] = []
        for state in self.states:
            detection = self._search(frame, state)
            if detection is not None:
                self._last_seen[state.slug] = (
                    detection.match.x,
                    detection.match.y,
                    detection.match.width,
                    detection.match.height,
                )
                found.append(detection)
        return found

    # --- décision -----------------------------------------------------
    def _cooldown_ok(self, state: StateConfig, now: float) -> bool:
        if now - self._last_action_at < self.cfg.engine.action_cooldown_s:
            return False
        return now - self._last_action_per_state.get(state.slug, 0.0) >= state.cooldown_s

    def _confirm(self, slug: str | None) -> bool:
        if slug is None:
            self._candidate = None
            self._candidate_hits = 0
            return False
        if slug == self._candidate:
            self._candidate_hits += 1
        else:
            self._candidate = slug
            self._candidate_hits = 1
        need = self.cfg.engine.confirm_frames if self._state(slug).require_confirm else 1
        return self._candidate_hits >= need

    def _state(self, slug: str) -> StateConfig:
        for state in self.states:
            if state.slug == slug:
                return state
        raise KeyError(slug)

    def _act(self, detection: Detection, frame: Frame) -> None:
        state = detection.state
        if state.click == "center":
            x, y = detection.match.center
        else:
            x, y = detection.match.x, detection.match.y
        screen_x, screen_y = frame.to_screen(x, y)
        log.info(
            "état '%s' (%.2f) -> clic %dx en %sx%s",
            state.slug,
            detection.match.score,
            state.clicks,
            screen_x,
            screen_y,
        )
        self.actions.click(screen_x, screen_y, clicks=state.clicks)
        now = time.time()
        self._last_action_at = now
        self._last_action_per_state[state.slug] = now
        self.stats.actions += 1
        self.stats.per_state[state.slug] = self.stats.per_state.get(state.slug, 0) + 1
        self._candidate = None
        self._candidate_hits = 0
        self._last_seen.pop(state.slug, None)  # la scène change : on repart plein écran
        time.sleep(state.post_action_sleep_s)
        if self.cfg.engine.max_actions and self.stats.actions >= self.cfg.engine.max_actions:
            log.info("limite de %d actions atteinte", self.cfg.engine.max_actions)
            self.stop()

    # --- boucle -------------------------------------------------------
    def _save_debug(self, frame: Frame, detections: list[Detection]) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        vision.save_image(self._debug_dir / f"{stamp}_raw.png", frame.image)
        annotated = vision.annotate(frame.image, [d.match for d in detections])
        vision.save_image(self._debug_dir / f"{stamp}_annot.png", annotated)

    def run(self) -> Stats:
        interval = 1.0 / self.cfg.capture.target_fps
        log.info(
            "boucle démarrée (%.1f fps ciblé, %d fps réel max)",
            self.cfg.capture.target_fps,
            int(1 / interval),
        )
        if self.cfg.engine.start_paused:
            self._paused.set()
        last_idle_check = time.time()

        try:
            while not self._stop.is_set():
                started = time.time()
                if self._paused.is_set():
                    time.sleep(0.1)
                    continue

                try:
                    frame = self.capture.grab()
                except CaptureError as exc:
                    self.stats.captures_failed += 1
                    log.warning("capture échouée : %s", exc)
                    time.sleep(0.2)
                    continue

                self.stats.frames += 1
                detections = self._detect(frame)
                detections.sort(key=lambda d: (-d.state.priority, -d.match.score))

                if self.cfg.engine.save_debug_frames and detections:
                    self._save_debug(frame, detections)

                top = detections[0] if detections else None
                if top is not None and self._confirm(top.state.slug):
                    if self._cooldown_ok(top.state, time.time()):
                        self._act(top, frame)
                    else:
                        log.debug("'%s' détecté mais en cooldown", top.state.slug)
                elif top is None:
                    self._confirm(None)

                if self.cfg.engine.stop_after_idle_s:
                    if detections:
                        last_idle_check = time.time()
                    elif time.time() - last_idle_check > self.cfg.engine.stop_after_idle_s:
                        log.info(
                            "aucune détection depuis %.0fs : arrêt",
                            self.cfg.engine.stop_after_idle_s,
                        )
                        break

                elapsed = time.time() - started
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        finally:
            log.info("boucle terminée : %s", self.stats.as_dict())
        return self.stats
