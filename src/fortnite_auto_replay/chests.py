"""Coffres ouverts, comptés dans les replays de Fortnite.

Quand l'enregistrement des replays est activé (Paramètres, Jeu, Rediffusions),
Fortnite écrit un fichier ``.replay`` par partie dans ``chests.demos_dir``. Il
contient ce que le serveur a envoyé, dont l'état de chaque coffre : fouillé
(``bAlreadySearched``) ou détruit (``bDestroyed``), et par qui
(``SearchingPawn``). Le lecteur de replays (``replay-reader.exe``, construit
depuis ``replay-reader/`` avec l'analyse de ChestCounter) en tire les coffres
ouverts ou cassés par le joueur qui a enregistré la partie, une ligne JSON par
replay.

Le jeu n'écrit la clé de déchiffrement d'un replay qu'à la fin de la partie :
une partie se compte quelques secondes après sa fin, une fois son replay
terminé et stable. Les parties comptées sont gardées dans ``chests.store_file``
(le jeu efface ses vieux replays) ; chaque replay n'est lu qu'une fois, et une
même partie (``session_id``) ne compte qu'une fois. Une nouvelle version du
lecteur relit les replays encore présents, et les parties relues remplacent
les anciennes.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ChestsConfig, ConfigError, parse_utc

log = logging.getLogger(__name__)

_STORE_VERSION = 1
_MAGIC = 0x1CA2E27F  # début de tout fichier .replay
# Un replay terminé (plus « en direct », clé écrite) se lit après ce calme.
FINISHED_QUIET_S = 4.0
QUIET_S = 10.0  # replay terminé dont la clé n'a pas pu être lue
OLD_S = 60.0  # replay terminé et intouché depuis : complet à coup sûr
UNKNOWN_QUIET_S = 120.0  # en-tête illisible : prudence
# Le jeu complète le replay d'une partie en cours toutes les 25 s environ.
# Encore « en direct » mais intouché depuis : enregistrement coupé (jeu fermé).
ABANDONED_S = 600.0
READ_TIMEOUT_S = 180.0

INCOMPLETE = "incomplet"
OTHER_MODE = "autre mode"
ERROR = "erreur"


# --- en-tête d'un replay --------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Header:
    live: bool  # partie encore en cours d'enregistrement
    key_missing: bool  # chiffré et clé pas encore écrite (fin de partie)


def read_header(path: Path) -> Header | None:  # noqa: PLR0911 - un retour par cas
    """État d'enregistrement d'un replay ; ``None`` si l'en-tête est illisible."""
    try:
        with path.open("rb") as handle:
            data = handle.read(4096)
    except OSError:
        return None
    try:
        magic, version = struct.unpack_from("<II", data, 0)
        if magic != _MAGIC:
            return None
        pos = 8
        if version >= 7:  # liste des versions d'objets personnalisées
            (count,) = struct.unpack_from("<i", data, pos)
            if not 0 <= count <= 64:
                return None
            pos += 4 + count * 20
        pos += 12  # durée, version réseau, changelist
        (length,) = struct.unpack_from("<i", data, pos)  # nom (FString)
        pos += 4 + (-2 * length if length < 0 else length)
        (live,) = struct.unpack_from("<I", data, pos)
        pos += 4 + 8 + 4  # direct, horodatage, compressé
        (encrypted,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if not encrypted:
            return Header(live=bool(live), key_missing=False)
        (key_length,) = struct.unpack_from("<i", data, pos)
        pos += 4
        if not 0 < key_length <= 256 or pos + key_length > len(data):
            return Header(live=bool(live), key_missing=True)
        key = data[pos : pos + key_length]
        return Header(live=bool(live), key_missing=not any(key))
    except struct.error:
        return None


def file_key(path: Path, stat: os.stat_result) -> str:
    """Identité d'un replay : nom, taille et date (un fichier réécrit est relu)."""
    return f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}"


# --- parties comptées -------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class MatchChests:
    session: str
    start: datetime | None  # UTC
    chests: int  # coffres ouverts ou cassés par le joueur
    destroyed: int  # dont cassés
    counted_at: datetime  # UTC


@dataclass(slots=True)
class Store:
    reader: int = 0  # version du lecteur qui a lu les replays de ``files``
    files: dict[str, str] = field(default_factory=dict)  # clé du replay -> session ou état
    matches: dict[str, MatchChests] = field(default_factory=dict)  # session -> partie


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_store(path: Path) -> Store:
    """Parties gardées ; un fichier absent ou illisible repart de zéro."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != _STORE_VERSION:
            raise ValueError(f"version {raw.get('version')!r}")
        matches = {}
        for item in raw["matches"]:
            match = MatchChests(
                session=str(item["session"]),
                start=parse_utc(item["start"]) if item.get("start") else None,
                chests=int(item["chests"]),
                destroyed=int(item.get("destroyed", 0)),
                counted_at=parse_utc(item["counted_at"]),
            )
            matches[match.session] = match
        files = {str(key): str(value) for key, value in raw["files"].items()}
        return Store(reader=int(raw.get("reader", 0)), files=files, matches=matches)
    except FileNotFoundError:
        return Store()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ConfigError) as exc:
        log.warning("%s illisible (%s) : les replays présents seront relus", path.name, exc)
        return Store()


def save_store(store: Store, path: Path) -> None:
    """Écriture atomique : fichier temporaire, puis remplacement."""
    payload: dict[str, Any] = {
        "version": _STORE_VERSION,
        "reader": store.reader,
        "files": dict(sorted(store.files.items())),
        "matches": [
            {
                "session": m.session,
                "start": _iso(m.start) if m.start else None,
                "chests": m.chests,
                "destroyed": m.destroyed,
                "counted_at": _iso(m.counted_at),
            }
            for m in sorted(store.matches.values(), key=_order)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", "utf-8")
    temporary.replace(path)


def _order(match: MatchChests) -> datetime:
    return match.start or match.counted_at


# --- lecteur de replays -----------------------------------------------------------------
class ReaderError(Exception):
    pass


class ReaderStopped(ReaderError):
    """Lecture interrompue par l'arrêt du suivi : rien n'est retenu de ce replay."""


_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
_IDLE = 0x00000040  # IDLE_PRIORITY_CLASS : ne prend rien au jeu


class Reader:
    """``replay-reader.exe`` : version, et lecture d'un replay.

    :meth:`kill` arrête la lecture en cours : le panneau qui se ferme (ou
    s'installe) ne laisse pas tourner le lecteur, qui tiendrait son dossier.
    """

    def __init__(self, exe: Path, run: Callable[[list[str], float], str] | None = None) -> None:
        self.exe = exe
        self._run = run or self._spawn
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._killed = False

    def kill(self) -> None:
        with self._lock:
            self._killed = True
            process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def _spawn(self, command: list[str], timeout: float) -> str:
        flags = _NO_WINDOW | _IDLE if sys.platform == "win32" else 0
        with self._lock:
            if self._killed:
                raise ReaderStopped("suivi arrêté")
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=flags,
                )
            except OSError as exc:
                raise ReaderError(f"lecteur de replays introuvable ({exc})") from exc
            self._process = process
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise ReaderError(f"lecture trop longue (plus de {timeout:.0f} s)") from exc
        finally:
            with self._lock:
                self._process = None
        if self._killed:
            raise ReaderStopped("suivi arrêté")
        if process.returncode != 0:
            raise ReaderError(f"lecteur de replays en échec ({process.returncode}) {err.strip()}")
        return out

    def version(self) -> int:
        text = self._run([str(self.exe), "--version"], 30.0).strip()
        try:
            return int(text)
        except ValueError as exc:
            raise ReaderError(f"version du lecteur illisible : {text!r}") from exc

    def read(self, path: Path) -> dict[str, Any]:
        lines = self._run([str(self.exe), str(path)], READ_TIMEOUT_S).splitlines()
        try:
            result = json.loads(lines[-1])
        except (IndexError, ValueError) as exc:
            raise ReaderError("réponse du lecteur illisible") from exc
        if not isinstance(result, dict):
            raise ReaderError("réponse du lecteur illisible")
        return result


# --- résumé pour le panneau -----------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Summary:
    total: int  # coffres de toutes les parties comptées
    matches: int
    last: int | None  # coffres de la dernière partie
    since: datetime | None  # début de la première partie comptée (UTC)
    recording: bool  # une partie en cours d'enregistrement
    replays: bool  # au moins un replay vu dans le dossier (enregistrement activé)
    failed: bool  # le dernier replay lu n'a pas pu l'être


def summarize(store: Store, *, recording: bool, replays: bool, failed: bool) -> Summary:
    ordered = sorted(store.matches.values(), key=_order)
    return Summary(
        total=sum(m.chests for m in ordered),
        matches=len(ordered),
        last=ordered[-1].chests if ordered else None,
        since=_order(ordered[0]) if ordered else None,
        recording=recording,
        replays=replays or bool(ordered),
        failed=failed,
    )


# --- suivi -------------------------------------------------------------------------------
class ChestTracker:
    """Surveille le dossier des replays et compte chaque partie terminée.

    Le panneau lit :meth:`summary` à chaque tour ; la lecture des replays se
    fait dans le thread du suivi, toutes les ``scan_interval_s`` secondes.
    """

    def __init__(
        self,
        cfg: ChestsConfig,
        store_path: Path,
        reader: Reader,
        *,
        clock: Callable[[], float] = time.time,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.cfg = cfg
        self.store_path = store_path
        self.reader = reader
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._summary: Summary | None = None
        self._unavailable = ""  # lecteur absent ou inutilisable
        self._stop = threading.Event()
        self._store: Store | None = None
        self._sizes: dict[str, int] = {}  # taille de chaque replay au tour précédent
        self._failed = False

    def summary(self) -> Summary | None:
        """``None`` tant que le premier tour n'est pas fini."""
        with self._lock:
            return self._summary

    @property
    def unavailable(self) -> str:
        with self._lock:
            return self._unavailable

    def _ready(self, path: Path, stat: os.stat_result, header: Header | None) -> bool:
        quiet = self._clock() - stat.st_mtime
        stable = self._sizes.get(path.name) == stat.st_size
        self._sizes[path.name] = stat.st_size
        if header is None:
            return stable and quiet > UNKNOWN_QUIET_S
        if header.live:
            return quiet > ABANDONED_S
        if quiet > OLD_S:
            return True
        if not header.key_missing:
            return stable and quiet >= FINISHED_QUIET_S
        return stable and quiet > QUIET_S

    def _open_store(self) -> Store:
        if self._store is None:
            store = load_store(self.store_path)
            version = self.reader.version()
            if version != store.reader:
                # nouveau lecteur : les replays encore présents sont relus
                if store.reader:
                    log.info("coffres : lecteur %d -> %d, replays relus", store.reader, version)
                store.files.clear()
                store.reader = version
            self._store = store
        return self._store

    def refresh(self) -> Summary:
        """Un tour : lit les replays terminés, garde les parties, refait le résumé."""
        store = self._open_store()
        demos = self.cfg.resolved_demos_dir()
        try:
            paths = sorted(demos.glob("*.replay"), key=lambda p: p.name)
        except OSError:
            paths = []
        recording = False
        changed = not self.store_path.exists()
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            key = file_key(path, stat)
            if key in store.files:
                continue
            header = read_header(path)
            if not self._ready(path, stat, header):
                recording = recording or (header is not None and header.live)
                continue
            store.files[key] = self._count(path, header, store)
            changed = True
        self._sizes = {k: v for k, v in self._sizes.items() if (demos / k).exists()}
        if changed:
            save_store(store, self.store_path)
        result = summarize(store, recording=recording, replays=bool(paths), failed=self._failed)
        with self._lock:
            self._summary = result
        return result

    def _count(self, path: Path, header: Header | None, store: Store) -> str:
        """Lit un replay prêt ; rend l'état gardé pour ce fichier."""
        if header is not None and header.live and header.key_missing:
            log.info("coffres : %s incomplet (jeu fermé pendant la partie)", path.name)
            return INCOMPLETE
        try:
            result = self.reader.read(path)
        except ReaderStopped:
            raise
        except ReaderError as exc:
            log.warning("coffres : %s non lu (%s)", path.name, exc)
            self._failed = True
            return ERROR
        if not result.get("ok"):
            log.warning("coffres : %s illisible (%s)", path.name, result.get("error"))
            self._failed = True
            return ERROR
        self._failed = False
        if not result.get("battle_royale", True):
            log.info("coffres : %s n'est pas une partie de Battle Royale", path.name)
            return OTHER_MODE
        session = str(result.get("session_id") or path.name)
        try:
            start = parse_utc(result["start"]) if result.get("start") else None
        except ConfigError:
            start = None
        previous = store.matches.get(session)
        match = MatchChests(
            session=session,
            start=start,
            chests=int(result.get("chests", 0)),
            destroyed=int(result.get("destroyed", 0)),
            counted_at=previous.counted_at if previous else self._now(),
        )
        store.matches[session] = match
        log.info("coffres : %d dans %s (dont %d cassés)", match.chests, path.name, match.destroyed)
        return session

    def start(self) -> None:
        threading.Thread(target=self._watch, name="coffres", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.reader.kill()

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except ReaderStopped:
                return
            except ReaderError as exc:
                with self._lock:
                    self._unavailable = str(exc)
                log.warning("coffres : %s", exc)
                return  # sans lecteur, rien à compter jusqu'au prochain lancement
            except Exception:  # un replay inattendu ne doit pas arrêter le suivi
                log.exception("lecture des replays impossible")
            self._stop.wait(self.cfg.scan_interval_s)
