"""Estimation des coffres ouverts, d'après les parties lues dans les journaux de Fortnite.

Le jeu ne compte les coffres que dans la quête de la chasse au trésor, qui
s'arrête à son dernier palier (1 000). Les paliers atteints et leur date
(``chests.milestones``, lus sur le compte Epic par :mod:`epic`) donnent un
nombre de coffres par minute de partie ; les minutes jouées depuis le dernier
palier, lues dans les journaux du jeu, donnent l'estimation. Sans intervalle
mesurable entre deux paliers, ``chests.default_rate`` sert de rythme.

Mesuré sur un joueur entre ses paliers 250, 500 et 1 000 : 0,88 puis 0,65
coffre par minute. Les minutes au sol (du saut du bus à la mort) ou le nombre
de parties ne varient pas moins d'un intervalle à l'autre : l'écart vient de
la façon de jouer, pas de la mesure. Les minutes de partie, les plus simples à
lire, sont gardées, et la fourchette montre cet écart.

Une partie de Battle Royale va de la ligne ``LogLoad: LoadMap:`` qui charge sa
carte depuis un serveur (VerseURI ``chests.match_marker``) au chargement de
carte suivant, le salon en général. Fortnite n'en garde qu'une quinzaine de
journaux : les parties lues sont gardées dans ``chests.store_file``, et chaque
journal n'est lu qu'une fois, à partir de là où la lecture précédente s'est
arrêtée. Un journal se reconnaît à l'horodatage de sa première ligne, qui ne
change pas quand le jeu le renomme en ``FortniteGame-backup-….log``.

Les horodatages des journaux sont en UTC, comme les dates des paliers.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

from .config import ChestsConfig, ConfigError, parse_utc

log = logging.getLogger(__name__)

_LOAD = b"LogLoad: LoadMap: "
_STAMP = re.compile(rb"\[(\d{4})\.(\d\d)\.(\d\d)-(\d\d)\.(\d\d)\.(\d\d):\d{3}\]")
_LINE_STAMP = re.compile(rb"^" + _STAMP.pattern, re.MULTILINE)  # en début de ligne seulement
_CHUNK = 4 * 1024 * 1024  # lecture par blocs : la recherche se fait en C, pas ligne à ligne
# première ligne horodatée : après l'en-tête du journal, vers 68 Kio (constaté en 43.00)
_HEAD = 1024 * 1024
_TAIL = 64 * 1024  # la dernière ligne horodatée est dans cette fin de fichier
_STORE_VERSION = 1
RANGE_MIN_CHESTS = 100  # intervalle assez long pour borner la fourchette (25 coffres : trop bruité)

Match = tuple[datetime, datetime]  # début, fin (UTC)


@dataclass(frozen=True, slots=True)
class Milestone:
    count: int
    at: datetime  # UTC


@dataclass(frozen=True, slots=True)
class Estimate:
    total: int  # arrondi à la dizaine, jamais sous le dernier palier
    confirmed: int  # dernier palier atteint
    matches: int  # parties jouées depuis ce palier (ou depuis le début des journaux)
    per_minute: float  # coffres par minute de partie
    low: int = 0  # fourchette (rythmes extrêmes des intervalles mesurés), 0 sans fourchette
    high: int = 0
    measured: bool = True  # rythme mesuré entre vos paliers, sinon rythme par défaut
    partial_since: datetime | None = None  # journaux lus depuis cette date seulement : minimum


def milestones_from(cfg: ChestsConfig) -> list[Milestone]:
    """Paliers de la config, du plus ancien au plus récent."""
    points = [Milestone(int(m["count"]), parse_utc(m["at"])) for m in cfg.milestones]
    return sorted(points, key=lambda m: m.at)


def _stamp(raw: bytes) -> datetime | None:
    found = _STAMP.match(raw)
    if found is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in found.groups())
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


# --- lecture des journaux -------------------------------------------------------
@dataclass(slots=True)
class FileState:
    """Où en est la lecture d'un journal : octets lus, partie ouverte à cet endroit."""

    offset: int = 0
    open: datetime | None = None


def first_stamp(path: Path) -> datetime | None:
    """Horodatage de la première ligne : l'identité du journal."""
    with path.open("rb") as handle:
        head = handle.read(_HEAD)
    found = _LINE_STAMP.search(head)
    return _stamp(head[found.start() :]) if found else None


def _last_stamp(path: Path) -> datetime | None:
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        handle.seek(max(0, size - _TAIL))
        tail = handle.read()
    stamps = list(_LINE_STAMP.finditer(tail))
    return _stamp(tail[stamps[-1].start() :]) if stamps else None


def _lines(data: bytes, state: FileState, marker: bytes, found: list[Match]) -> None:
    """Chargements de carte d'un bloc de lignes entières (``data`` finit par un saut de ligne)."""
    pos = data.find(_LOAD)
    while pos >= 0:
        start = data.rfind(b"\n", 0, pos) + 1
        end = data.find(b"\n", pos)
        when = _stamp(data[start:pos])
        if when is not None:
            if state.open is not None:  # tout chargement de carte termine la partie en cours
                found.append((state.open, when))
                state.open = None
            target = data[pos + len(_LOAD) : end]
            # une partie se charge depuis un serveur (« ip:port/… »), pas depuis un chemin local
            if marker in target and not target.startswith(b"/"):
                state.open = when
        pos = data.find(_LOAD, end)


def scan_file(path: Path, state: FileState, marker: bytes, *, finished: bool) -> list[Match]:
    """Parties terminées depuis ``state.offset``, qui avance jusqu'à la dernière ligne entière.

    ``finished`` : le jeu n'écrit plus dans ce journal (un plus récent existe).
    Une partie encore ouverte à sa fin (jeu fermé ou planté en pleine partie)
    se termine alors à la dernière ligne horodatée.
    """
    found: list[Match] = []
    with path.open("rb") as handle:
        if handle.seek(0, 2) < state.offset:  # plus court que la lecture d'avant : relu
            state.offset, state.open = 0, None
        handle.seek(state.offset)
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            cut = chunk.rfind(b"\n") + 1
            if cut == 0:
                if len(chunk) < _CHUNK:  # dernière ligne pas encore finie
                    break
                state.offset += len(chunk)  # ligne démesurée, sans chargement de carte utile
                continue
            _lines(chunk[:cut], state, marker, found)
            state.offset += cut
            handle.seek(state.offset)
            time.sleep(0)  # laisse la main au moteur entre deux blocs
    if finished and state.open is not None:
        last = _last_stamp(path)
        if last is not None and last > state.open:
            found.append((state.open, last))
        state.open = None
    return found


# --- parties gardées --------------------------------------------------------------
@dataclass(slots=True)
class Store:
    """Parties lues et avancement de la lecture de chaque journal."""

    tracked_since: datetime | None = None  # début du plus ancien journal lu
    files: dict[str, FileState] = field(default_factory=dict)  # clé : première ligne, en ISO
    matches: dict[datetime, datetime] = field(default_factory=dict)  # début -> fin

    def add(self, found: Iterable[Match]) -> None:
        for start, end in found:
            self.matches[start] = end

    def sorted_matches(self) -> list[Match]:
        return sorted(self.matches.items())


def load_store(path: Path) -> Store:
    """Parties gardées ; un fichier absent ou illisible repart de zéro (les journaux sont relus)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != _STORE_VERSION:
            raise ValueError(f"version {raw.get('version')!r}")
        since = raw.get("tracked_since")
        return Store(
            tracked_since=parse_utc(since) if since else None,
            files={
                key: FileState(
                    int(value["offset"]), parse_utc(value["open"]) if value["open"] else None
                )
                for key, value in raw["files"].items()
            },
            matches={parse_utc(start): parse_utc(end) for start, end in raw["matches"]},
        )
    except FileNotFoundError:
        return Store()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ConfigError) as exc:
        log.warning("%s illisible (%s) : les journaux du jeu seront relus", path.name, exc)
        return Store()


def save_store(store: Store, path: Path) -> None:
    """Écriture atomique : fichier temporaire, puis remplacement."""
    payload: dict[str, Any] = {
        "version": _STORE_VERSION,
        "tracked_since": _iso(store.tracked_since) if store.tracked_since else None,
        "files": {
            key: {"offset": s.offset, "open": _iso(s.open) if s.open else None}
            for key, s in sorted(store.files.items())
        },
        "matches": [[_iso(start), _iso(end)] for start, end in store.sorted_matches()],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    temporary.replace(path)


def scan_logs(store: Store, logs_dir: Path, pattern: str, marker: str) -> int:
    """Lit la suite de chaque journal du dossier ; nombre de parties ajoutées.

    Le journal le plus récent peut encore être écrit par le jeu : une partie
    ouverte à sa fin reste ouverte jusqu'à la lecture suivante.
    """
    journals: list[tuple[datetime, Path]] = []
    for path in logs_dir.glob(pattern):
        try:
            begun = first_stamp(path)
        except OSError as exc:
            log.debug("journal %s ignoré (%s)", path.name, exc)
            continue
        if begun is not None:
            journals.append((begun, path))
    journals.sort()
    before = len(store.matches)
    for index, (begun, path) in enumerate(journals):
        key = _iso(begun)
        state = store.files.setdefault(key, FileState())
        try:
            store.add(scan_file(path, state, marker.encode(), finished=index < len(journals) - 1))
        except OSError as exc:  # renommé ou effacé par le jeu entre-temps : lu au tour suivant
            log.debug("journal %s non lu (%s)", path.name, exc)
        if store.tracked_since is None or begun < store.tracked_since:
            store.tracked_since = begun
    return len(store.matches) - before


# --- estimation -------------------------------------------------------------------
def minutes_between(matches: Sequence[Match], start: datetime, end: datetime) -> float:
    """Minutes de partie comprises entre ``start`` et ``end``."""
    seconds = sum(
        max(0.0, (min(stop, end) - max(begin, start)).total_seconds()) for begin, stop in matches
    )
    return seconds / 60


def _tens(value: float) -> int:
    return round(value / 10) * 10


def estimate(
    matches: Sequence[Match],
    milestones: Sequence[Milestone],
    tracked_since: datetime | None,
    now: datetime,
    default_rate: float = 0.0,
) -> Estimate | None:
    """Dernier palier, plus les minutes jouées depuis au rythme mesuré entre les paliers.

    Seuls comptent les intervalles entre paliers dont toutes les parties ont
    été lues (après ``tracked_since``) : sans elles, le rythme serait faussé.
    Le rythme est celui de tous ces intervalles réunis (coffres sur minutes) ;
    la fourchette va du plus lent au plus rapide d'entre eux, s'il y en a deux
    d'au moins :data:`RANGE_MIN_CHESTS` coffres. Sans intervalle mesuré, le
    rythme par défaut sert. Si les journaux commencent après le dernier palier,
    les parties d'avant sont inconnues : l'estimation n'est qu'un minimum.
    """
    points = sorted(milestones, key=lambda m: m.at)
    if tracked_since is None or not points:
        return None
    chests = minutes = 0.0
    rates: list[float] = []
    for before, after in pairwise(points):
        gained = after.count - before.count
        if before.at < tracked_since or gained < 0:
            continue
        span = minutes_between(matches, before.at, after.at)
        if span > 0:
            chests += gained
            minutes += span
            if gained >= RANGE_MIN_CHESTS:
                rates.append(gained / span)
    measured = minutes > 0
    if measured:
        rate = chests / minutes
    elif default_rate > 0:
        rate = default_rate
    else:
        return None
    last = points[-1]
    partial = last.at < tracked_since
    start = tracked_since if partial else last.at
    played = minutes_between(matches, start, now)
    total = max(last.count, _tens(last.count + rate * played))
    low = high = 0
    if not partial and len(rates) >= 2:
        low = max(last.count, _tens(last.count + min(rates) * played))
        high = max(last.count, _tens(last.count + max(rates) * played))
    return Estimate(
        total=total,
        confirmed=last.count,
        matches=sum(1 for _, stop in matches if start < stop <= now),
        per_minute=rate,
        low=low,
        high=high,
        measured=measured,
        partial_since=tracked_since if partial else None,
    )


# --- suivi en arrière-plan -----------------------------------------------------------
class ChestTracker:
    """Relit les journaux au lancement puis toutes les ``scan_interval_s`` secondes.

    Le panneau lit :meth:`estimate` à chaque tour ; la lecture des journaux se
    fait dans le thread du suivi. De nouveaux paliers (lus sur le compte Epic)
    se donnent par :meth:`set_milestones` : le suivi recalcule aussitôt.
    """

    def __init__(
        self,
        cfg: ChestsConfig,
        store_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cfg = cfg
        self.store_path = store_path
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._milestones = milestones_from(cfg)
        self._generation = 0  # change à chaque nouveau jeu de paliers
        self._scanned = False  # estimation calculée avec les paliers actuels
        self._lock = threading.Lock()
        self._estimate: Estimate | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._store: Store | None = None

    def estimate(self) -> Estimate | None:
        with self._lock:
            return self._estimate

    @property
    def has_milestones(self) -> bool:
        with self._lock:
            return bool(self._milestones)

    @property
    def scanned(self) -> bool:
        """Vrai une fois l'estimation calculée avec les paliers actuels."""
        with self._lock:
            return self._scanned

    def set_milestones(self, points: Iterable[Milestone]) -> None:
        with self._lock:
            self._milestones = sorted(points, key=lambda m: m.at)
            self._generation += 1
            self._scanned = False
        self._wake.set()

    def refresh(self) -> Store:
        """Lit la suite des journaux, garde les parties, recalcule l'estimation."""
        with self._lock:
            milestones, generation = list(self._milestones), self._generation
        if self._store is None:
            self._store = load_store(self.store_path)
        store = self._store
        read_before = {key: (s.offset, s.open) for key, s in store.files.items()}
        logs_dir = self.cfg.resolved_logs_dir()
        added = scan_logs(store, logs_dir, self.cfg.log_glob, self.cfg.match_marker)
        read_after = {key: (s.offset, s.open) for key, s in store.files.items()}
        if read_after != read_before or not self.store_path.exists():
            save_store(store, self.store_path)
        if added:
            log.info("coffres : %d partie(s) lue(s) dans les journaux du jeu", added)
        result = estimate(
            store.sorted_matches(),
            milestones,
            store.tracked_since,
            self._clock(),
            self.cfg.default_rate,
        )
        with self._lock:
            if generation == self._generation:  # sinon, de nouveaux paliers : le tour suivant
                self._estimate = result
                self._scanned = True
        return store

    def start(self) -> None:
        threading.Thread(target=self._watch, name="coffres", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:  # un journal inattendu ne doit pas arrêter le suivi
                log.exception("lecture des journaux du jeu impossible")
            self._wake.wait(self.cfg.scan_interval_s)
            self._wake.clear()  # un réveil arrivé avant est couvert par la lecture qui suit
