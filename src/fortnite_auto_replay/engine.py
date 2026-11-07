"""Boucle principale : capture → détection → décision → clic.

Machine à états volontairement simple : à chaque frame on cherche tous les
boutons connus, on garde le plus prioritaire au-dessus du seuil, on exige
plusieurs frames consécutives identiques, puis on clique si les cooldowns le
permettent. Rien n'est bloquant : une détection ratée n'arrête pas le bot.

Garde-fous :

- **focus** : tant que la fenêtre du jeu n'est pas au premier plan, rien n'est
  analysé ni cliqué (sinon les clics partiraient dans une autre fenêtre) ;
- **clic sans effet** : si un bouton reste visible sans interruption après
  ``max_ineffective_clicks`` clics, le bot se met en pause et sauve la frame ;
- **détection douteuse** : un score juste sous le seuil fait sauver la frame,
  pour recadrer un template ou ajuster ``vision.threshold`` ;
- **bouton couvert** : si une autre fenêtre (le panneau, une notification) est
  sous le point du clic, le clic est annulé : il partirait dans cette fenêtre.

L'état courant se lit par :meth:`Engine.status`, depuis un autre thread (panneau).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

import numpy as np

from . import history, sounds, vision
from .capture import CaptureError, Frame
from .config import STILL_HERE_ANSWERS, Config, StateConfig
from .templates import Prepared, Template, TemplateLibrary

if TYPE_CHECKING:
    from .window import Focus

log = logging.getLogger(__name__)

_FOCUS_POLL_S = 0.25
_PAUSE_POLL_S = 0.1
_CAPTURE_RETRY_S = 0.2
_CAPTURE_ERROR_LOG_S = 10.0  # une même erreur n'est répétée dans le journal qu'à cet intervalle
_STEP_RETRY_S = 0.5
_RECENT_CLICKS = 20
_COVERED_SHOW_S = 3.0  # durée pendant laquelle un clic annulé reste signalé

# actif | en pause | bloqué (clic sans effet) | bloqué (erreurs) | jeu absent |
# jeu en arrière-plan | capture en échec | bouton couvert | arrêté
Phase = Literal[
    "active", "paused", "stuck", "errors", "no_game", "background", "capture", "covered", "stopped"
]


class FrameSource(Protocol):
    def grab(self) -> Frame: ...


class Clicker(Protocol):
    def click(self, x: int, y: int, *, clicks: int = 1) -> tuple[int, int]: ...


class FocusSource(Protocol):
    def focus(self) -> Focus: ...

    def covering(self, x: int, y: int) -> tuple[int, str] | None:
        """``None`` si le point écran tombe sur le jeu, sinon (pid, exécutable) qui le couvre."""
        ...


@dataclass(frozen=True, slots=True)
class ClickRecord:
    ts: float
    slug: str
    label: str


@dataclass(frozen=True, slots=True)
class Status:
    """Instantané de l'état du bot, sûr à lire depuis un autre thread."""

    phase: Phase
    detail: str  # cause d'une pause automatique, d'une capture en échec, d'un clic annulé
    started_at: float
    actions: int
    per_state: dict[str, int]
    recent: tuple[ClickRecord, ...]  # du plus récent au plus ancien
    still_here: str
    dry_run: bool


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
    stuck: int = 0
    doubts_saved: int = 0
    errors: int = 0
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
            "stuck": self.stuck,
            "doubts_saved": self.doubts_saved,
            "errors": self.errors,
            "per_state": dict(self.per_state),
            "uptime_s": round(self.uptime_s, 1),
        }


def _beep() -> None:
    """Alerte sonore système, sans fenêtre ni prise de focus."""
    try:
        import winsound  # noqa: PLC0415 - Windows uniquement

        winsound.MessageBeep(winsound.MB_ICONHAND)
    except Exception:  # pragma: no cover - pas de son disponible
        log.debug("bip impossible")


def _no_alert() -> None:
    pass


def _no_sound(_cue: sounds.Cue) -> None:
    pass


def _better(best: Detection | None, other: Detection | None) -> Detection | None:
    if other is None:
        return best
    if best is None or other.match.score > best.match.score:
        return other
    return best


class Engine:
    def __init__(
        self,
        cfg: Config,
        capture: FrameSource,
        actions: Clicker,
        library: TemplateLibrary,
        *,
        dry_run: bool = False,
        window: FocusSource | None = None,
        alert: Callable[[], None] | None = None,
        sound: Callable[[sounds.Cue], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.capture = capture
        self.actions = actions
        self.library = library
        self.dry_run = dry_run

        # tous les états qui ont un template, réponses au dialogue comprises :
        # la réponse choisie peut changer en cours de route (set_still_here)
        self._all_states = [s for s in cfg.enabled_states() if library.for_state(s.slug)]
        if not self._all_states:
            raise RuntimeError("aucun état avec template : impossible de démarrer")
        self.states = self._select_states()

        self._game = window  # pour vérifier qu'un clic tombe bien sur le jeu
        self._window = window if cfg.window.require_foreground else None
        if alert is None:
            alert = _beep if cfg.engine.beep_on_stuck else _no_alert
        self._alert = alert
        if sound is None:
            sound = sounds.cue if cfg.engine.sound_on_toggle else _no_sound
        self._sound = sound

        self.stats = Stats()
        self._candidate: str | None = None
        self._candidate_hits = 0
        self._last_action_at = 0.0
        self._last_action_per_state: dict[str, float] = {}
        self._last_seen: dict[str, tuple[int, int, int, int]] = {}
        self._streak_slug: str | None = None  # dernier bouton cliqué, toujours visible depuis
        self._streak = 0
        self._last_doubt_at: dict[str, float] = {}
        self._last_detection_at = time.time()
        self._focus: Focus | None = None
        self._capture_error: str | None = None
        self._capture_error_logged_at = 0.0
        self._last_full_scan = float("-inf")
        self._step_errors = 0  # erreurs inattendues consécutives
        self._step_error_logged_at = float("-inf")
        self._covered: tuple[float, str] | None = None  # (instant, message) du dernier clic annulé
        self._covered_logged_at = float("-inf")
        self._recent: deque[ClickRecord] = deque(maxlen=_RECENT_CLICKS)
        self._lock = threading.Lock()  # compteurs et clics récents, lus par status()
        self._pause_reason: Literal["user", "stuck", "errors"] = "user"
        self._pause_detail = ""
        self._finished = False
        self._paused = threading.Event()
        if cfg.engine.start_paused:
            self._paused.set()
        self._stop = threading.Event()
        self._debug_dir = cfg.resolved_debug_dir()
        if cfg.engine.save_debug_frames:
            self._debug_dir.mkdir(parents=True, exist_ok=True)

    # --- contrôle externe ---------------------------------------------
    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self, reason: Literal["user", "stuck", "errors"] = "user", detail: str = "") -> None:
        self._pause_reason, self._pause_detail = reason, detail
        self._paused.set()
        log.info("bot en pause")

    def resume(self) -> None:
        self._pause_reason, self._pause_detail = "user", ""
        self._paused.clear()
        self._reset_tracking()
        log.info("bot actif")

    def toggle(self) -> None:
        """Pause ou reprise (F8, bouton du panneau), annoncée par deux notes."""
        if self.paused:
            self.resume()
            self._sound("resume")
        else:
            self.pause()
            self._sound("pause")

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _select_states(self) -> list[StateConfig]:
        active = {s.slug for s in self.cfg.active_states()}
        return [s for s in self._all_states if s.slug in active]

    def still_here_answers(self) -> list[str]:
        """Réponses au dialogue possibles : celles dont le bouton a un template, et ``ignore``."""
        buttons = {s.still_here for s in self._all_states if s.still_here}
        return [a for a in STILL_HERE_ANSWERS if a in buttons or a == "ignore"]

    def set_still_here(self, answer: str) -> None:
        """Change la réponse au dialogue « Vous êtes toujours là ? », bot en marche."""
        if answer not in self.still_here_answers():
            raise ValueError(f"réponse impossible : {answer!r} (pas de template ou inconnue)")
        self.cfg.engine.still_here = answer
        self.states = self._select_states()  # remplacée d'un bloc : sûr pour la boucle
        log.info("dialogue « toujours là » : réponse %s", answer)

    def status(self) -> Status:
        phase: Phase
        detail = ""
        if self._finished:
            phase = "stopped"
        elif self._paused.is_set():
            phase = "paused" if self._pause_reason == "user" else self._pause_reason
            detail = self._pause_detail
        elif self._focus == "missing":
            phase = "no_game"
        elif self._focus == "background":
            phase = "background"
        elif self._capture_error is not None:
            phase, detail = "capture", self._capture_error
        elif self._covered is not None and time.time() - self._covered[0] < _COVERED_SHOW_S:
            phase, detail = "covered", self._covered[1]
        else:
            phase = "active"
        with self._lock:
            return Status(
                phase=phase,
                detail=detail,
                started_at=self.stats.started_at,
                actions=self.stats.actions,
                per_state=dict(self.stats.per_state),
                recent=tuple(reversed(self._recent)),
                still_here=self.cfg.engine.still_here,
                dry_run=self.dry_run,
            )

    def _reset_tracking(self) -> None:
        """Oublie ce qui dépend des frames précédentes (après pause ou perte de focus)."""
        self._candidate = None
        self._candidate_hits = 0
        self._streak_slug = None
        self._streak = 0
        self._last_detection_at = time.time()

    # --- focus --------------------------------------------------------
    def _has_focus(self) -> bool:
        if self._window is None:
            return True
        focus = self._window.focus()
        if focus != self._focus:
            if focus == "foreground":
                if self._focus is not None:
                    log.info("jeu au premier plan : reprise")
            elif focus == "missing":
                log.info("fenêtre du jeu introuvable : en attente")
            else:
                log.info("le jeu n'est pas au premier plan : en attente")
            self._focus = focus
        return focus == "foreground"

    # --- recherche ----------------------------------------------------
    def _floor(self) -> float:
        """Score minimal retenu : le seuil, moins la marge de doute."""
        return max(0.0, self.cfg.vision.threshold - self.cfg.engine.doubt_margin)

    def _prepared(self, state: StateConfig, frame: Frame) -> list[Prepared]:
        return self.library.prepared(
            state.slug,
            (frame.width, frame.height),
            frame.window_size,
            frame.scale,
            self.cfg.vision.zone_margin,
        )

    def _match(
        self, state: StateConfig, item: Prepared, image: np.ndarray, offset: tuple[int, int]
    ) -> Detection | None:
        match = vision.best_match(
            image,
            item.image,
            label=f"{state.slug}:{item.name}",
            threshold=self._floor(),
            scales=self.cfg.vision.scales,
            backend=self.cfg.vision.backend,
        )
        if match is None:
            return None
        match.x += offset[0]
        match.y += offset[1]
        return Detection(state=state, template=item.template, match=match)

    def _search(
        self, gray: np.ndarray, frame: Frame, state: StateConfig, full_scan: bool
    ) -> Detection | None:
        """Meilleure correspondance d'un état au-dessus du plancher de doute.

        Ordre : là où le bouton vient d'être vu, puis la zone attendue de chaque
        template (ou toute la frame s'il n'en a pas, ou lors d'un balayage
        complet). Un bouton absent de sa position connue la fait oublier.
        """
        prepared = self._prepared(state, frame)
        best: Detection | None = None
        known = self._last_seen.get(state.slug)
        if known is not None:
            pad = self.cfg.vision.padding
            x, y, w, h = known
            left, top = max(0, x - pad), max(0, y - pad)
            region = vision.crop(gray, left, top, x + w + pad, y + h + pad)
            if region.size:
                for item in prepared:
                    best = _better(best, self._match(state, item, region, (left, top)))
                if best is not None and best.match.score >= self.cfg.vision.threshold:
                    return best
            del self._last_seen[state.slug]

        for item in prepared:
            if item.zone is None or full_scan or not self.cfg.vision.use_zones:
                best = _better(best, self._match(state, item, gray, (0, 0)))
            else:
                left, top, right, bottom = item.zone
                zone = gray[top:bottom, left:right]
                best = _better(best, self._match(state, item, zone, (left, top)))
        return best

    def _full_scan_due(self) -> bool:
        interval = self.cfg.vision.full_scan_interval_s
        if not interval:
            return False
        now = time.monotonic()
        if now - self._last_full_scan < interval:
            return False
        self._last_full_scan = now
        return True

    def _detect(self, frame: Frame) -> tuple[list[Detection], list[Detection]]:
        """Détections franches (score ≥ seuil) et douteuses (juste sous le seuil)."""
        gray = vision.to_gray(frame.image)  # une seule conversion par frame
        full_scan = self._full_scan_due()
        found: list[Detection] = []
        doubtful: list[Detection] = []
        for state in self.states:
            detection = self._search(gray, frame, state, full_scan)
            if detection is None:
                continue
            match = detection.match
            if match.score >= self.cfg.vision.threshold:
                self._last_seen[state.slug] = (match.x, match.y, match.width, match.height)
                found.append(detection)
            else:
                doubtful.append(detection)
        return found, doubtful

    # --- décision -----------------------------------------------------
    def _cooldown_ok(self, state: StateConfig, now: float) -> bool:
        if now - self._last_action_at < self.cfg.engine.action_cooldown_s:
            return False
        return now - self._last_action_per_state.get(state.slug, 0.0) >= state.cooldown_s

    def _confirm(self, state: StateConfig | None) -> bool:
        if state is None:
            self._candidate = None
            self._candidate_hits = 0
            return False
        if state.slug == self._candidate:
            self._candidate_hits += 1
        else:
            self._candidate = state.slug
            self._candidate_hits = 1
        need = self.cfg.engine.confirm_frames if state.require_confirm else 1
        return self._candidate_hits >= need

    def _track_streak(self, seen: list[Detection]) -> None:
        """La série de clics s'arrête dès que le bouton cliqué disparaît."""
        if self._streak_slug is not None and all(d.state.slug != self._streak_slug for d in seen):
            self._streak_slug = None
            self._streak = 0

    def _is_ineffective(self, state: StateConfig) -> bool:
        limit = self.cfg.engine.max_ineffective_clicks
        return bool(limit) and self._streak_slug == state.slug and self._streak >= limit

    def _on_stuck(self, detection: Detection, frame: Frame) -> None:
        slug = detection.state.slug
        path = self._snapshot(frame, [detection], f"stuck-{slug}", ".png")
        log.error(
            "'%s' toujours visible après %d clics : pause (%s pour reprendre), frame : %s",
            slug,
            self._streak,
            self.cfg.hotkeys.toggle,
            path,
        )
        self.stats.stuck += 1
        self._alert()
        self.pause(
            "stuck",
            f"«\u00a0{detection.state.label}\u00a0» toujours visible après {self._streak} clics",
        )
        self._reset_tracking()

    def _click_covered(self, state: StateConfig, x: int, y: int) -> bool:
        """Vrai si une autre fenêtre que le jeu est sous le point : le clic y partirait."""
        if self._game is None:
            return False
        cover = self._game.covering(x, y)
        if cover is None:
            self._covered = None
            return False
        pid, process = cover
        who = "Le panneau" if pid == os.getpid() else (process or "Une autre fenêtre")
        message = f"{who} couvre le bouton «\u00a0{state.label}\u00a0»"
        now = time.time()
        self._covered = (now, message)
        if now - self._covered_logged_at >= _CAPTURE_ERROR_LOG_S:
            log.warning("clic annulé en %dx%d : %s", x, y, message)
            self._covered_logged_at = now
        self._candidate = None  # nouvelle confirmation avant le prochain essai
        self._candidate_hits = 0
        return True

    def _act(self, detection: Detection, frame: Frame) -> bool:
        if not self._has_focus():  # le focus a pu partir pendant l'analyse
            return False
        state = detection.state
        if state.click == "center":
            x, y = detection.match.center
        else:
            x, y = detection.match.x, detection.match.y
        screen_x, screen_y = frame.to_screen(x, y)
        if self._click_covered(state, screen_x, screen_y):
            return False
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
        with self._lock:
            self.stats.actions += 1
            self.stats.per_state[state.slug] = self.stats.per_state.get(state.slug, 0) + 1
            self._recent.append(ClickRecord(now, state.slug, state.label))
        if self._streak_slug == state.slug:
            self._streak += 1
        else:
            self._streak_slug, self._streak = state.slug, 1
        self._candidate = None
        self._candidate_hits = 0
        self._last_seen.pop(state.slug, None)  # la scène change : on repart plein écran
        self._stop.wait(state.post_action_sleep_s)
        if self.cfg.engine.max_actions and self.stats.actions >= self.cfg.engine.max_actions:
            log.info("limite de %d actions atteinte", self.cfg.engine.max_actions)
            self.stop()
        return True

    # --- frames sauvées -----------------------------------------------
    @staticmethod
    def _stamp() -> str:
        now = time.time()
        millis = int(now * 1000) % 1000
        return time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{millis:03d}"

    def _snapshot(
        self, frame: Frame, detections: list[Detection], prefix: str, suffix: str
    ) -> Path | None:
        path = self._debug_dir / f"{prefix}-{self._stamp()}{suffix}"
        try:
            vision.save_image(path, vision.annotate(frame.image, [d.match for d in detections]))
        except (vision.VisionError, OSError) as exc:
            log.warning("frame non sauvée (%s)", exc)
            return None
        return path

    def _save_debug(self, frame: Frame, detections: list[Detection]) -> None:
        stamp = self._stamp()
        vision.save_image(self._debug_dir / f"{stamp}_raw.png", frame.image)
        annotated = vision.annotate(frame.image, [d.match for d in detections])
        vision.save_image(self._debug_dir / f"{stamp}_annot.png", annotated)

    def _save_doubts(self, frame: Frame, doubtful: list[Detection]) -> None:
        cfg = self.cfg.engine
        if self.stats.doubts_saved >= cfg.doubt_max_snapshots:
            return
        now = time.time()
        due = [
            d
            for d in doubtful
            if now - self._last_doubt_at.get(d.state.slug, float("-inf")) >= cfg.doubt_interval_s
        ]
        if not due:
            return
        slugs = "+".join(d.state.slug for d in due)
        path = self._snapshot(frame, due, f"doubt-{slugs}", ".jpg")
        if path is None:
            return
        for d in due:
            self._last_doubt_at[d.state.slug] = now
        self.stats.doubts_saved += 1
        scores = ", ".join(f"{d.state.slug} {d.match.score:.3f}" for d in due)
        log.info(
            "détection douteuse (%s < seuil %.2f) : frame %s",
            scores,
            self.cfg.vision.threshold,
            path,
        )

    # --- boucle -------------------------------------------------------
    def _capture_failed(self, exc: CaptureError) -> None:
        self.stats.captures_failed += 1
        message = str(exc)
        now = time.time()
        if (
            message != self._capture_error
            or now - self._capture_error_logged_at >= _CAPTURE_ERROR_LOG_S
        ):
            log.warning("capture échouée : %s", message)
            self._capture_error = message
            self._capture_error_logged_at = now

    def _capture_ok(self) -> None:
        if self._capture_error is not None:
            log.info("capture rétablie")
            self._capture_error = None

    def _step(self, frame: Frame) -> None:
        found, doubtful = self._detect(frame)
        found.sort(key=lambda d: (-d.state.priority, -d.match.score))

        if self.cfg.engine.save_debug_frames and found:
            self._save_debug(frame, found)
        if doubtful:
            self._save_doubts(frame, doubtful)
        self._track_streak(found + doubtful)
        if found:
            self._last_detection_at = time.time()

        top = found[0] if found else None
        if top is None:
            self._confirm(None)
            return
        if not self._confirm(top.state):
            return
        if not self._cooldown_ok(top.state, time.time()):
            log.debug("'%s' détecté mais en cooldown", top.state.slug)
            return
        if self._is_ineffective(top.state):
            self._on_stuck(top, frame)
            return
        self._act(top, frame)

    def _step_failed(self) -> None:
        """Une erreur inattendue ne doit pas arrêter un bot laissé sans surveillance."""
        self.stats.errors += 1
        self._step_errors += 1
        now = time.monotonic()
        if self._step_errors == 1 or now - self._step_error_logged_at >= _CAPTURE_ERROR_LOG_S:
            log.exception("erreur pendant l'analyse d'une frame (%d de suite)", self._step_errors)
            self._step_error_logged_at = now
        limit = self.cfg.engine.max_step_errors
        if limit and self._step_errors >= limit:
            log.error(
                "%d erreurs de suite : pause (%s pour reprendre)",
                self._step_errors,
                self.cfg.hotkeys.toggle,
            )
            count, self._step_errors = self._step_errors, 0
            self._alert()
            self.pause("errors", f"{count} erreurs de suite, détail dans logs/bot.log")
            self._reset_tracking()

    def _idle_expired(self) -> bool:
        limit = self.cfg.engine.stop_after_idle_s
        if limit and time.time() - self._last_detection_at > limit:
            log.info("aucune détection depuis %.0fs : arrêt", limit)
            return True
        return False

    def run(self) -> Stats:
        interval = 1.0 / self.cfg.capture.target_fps
        log.info("boucle démarrée (%.1f fps visés)", self.cfg.capture.target_fps)
        self._last_detection_at = time.time()

        try:
            while not self._stop.is_set():
                started = time.time()
                if self._paused.is_set():
                    self._stop.wait(_PAUSE_POLL_S)
                    continue
                if not self._has_focus():
                    self._reset_tracking()
                    self._stop.wait(_FOCUS_POLL_S)
                    continue

                try:
                    frame = self.capture.grab()
                except CaptureError as exc:
                    self._capture_failed(exc)
                    self._stop.wait(_CAPTURE_RETRY_S)
                    continue
                self._capture_ok()

                self.stats.frames += 1
                try:
                    self._step(frame)
                except Exception:
                    self._step_failed()
                    self._stop.wait(_STEP_RETRY_S)
                    continue
                self._step_errors = 0
                if self._idle_expired():
                    break

                elapsed = time.time() - started
                if elapsed < interval:
                    self._stop.wait(interval - elapsed)
        finally:
            log.info("boucle terminée : %s", self.stats.as_dict())
            self._record_session()
            self._finished = True
        return self.stats

    def _record_session(self) -> None:
        path = self.cfg.resolved_stats_file()
        if path is None or self.dry_run or self.stats.frames == 0:
            return
        try:
            history.append_session(path, self.stats.started_at, self.stats.as_dict())
        except OSError as exc:
            log.warning("historique non écrit dans %s (%s)", path, exc)
