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
terminé et stable. Les parties comptées sont gardées en deux copies (le jeu
efface ses vieux replays) : ``chests.store_file``, hors du dossier de
l'application (dans ``%APPDATA%``), et ``chests.backup_file`` à côté d'elle ;
une copie effacée se refait depuis l'autre. Chaque replay n'est lu qu'une fois, et une
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
from collections.abc import Callable, Sequence
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


_BAD_ITEM = (ValueError, KeyError, TypeError, AttributeError, ConfigError)


def _read_match(item: Any) -> MatchChests:
    return MatchChests(
        session=str(item["session"]),
        start=parse_utc(item["start"]) if item.get("start") else None,
        chests=int(item["chests"]),
        destroyed=int(item.get("destroyed", 0)),
        counted_at=parse_utc(item["counted_at"]),
    )


def _read_store(path: Path) -> Store | None:
    """Parties d'un fichier ; ``None`` s'il n'existe pas, :class:`ValueError` s'il est illisible.

    Une partie mal formée est sautée sans perdre les autres ; un fichier d'une
    version plus récente (application revenue en arrière) garde ses parties.
    Un fichier inaccessible (verrouillé un instant) lève :class:`OSError` : il
    ne doit pas être réécrit sans avoir été lu.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as exc:
        raise ValueError(str(exc)) from exc
    try:
        raw = json.loads(text)
        version = raw.get("version")
        if not isinstance(version, int) or version < 1:
            raise ValueError(f"version {version!r}")
        items = raw["matches"]
        if not isinstance(items, list):
            raise TypeError("matches n'est pas une liste")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(str(exc)) from exc
    matches: dict[str, MatchChests] = {}
    for item in items:
        try:
            match = _read_match(item)
        except _BAD_ITEM as exc:
            log.warning("%s : partie illisible sautée (%s)", path.name, exc)
            continue
        matches[match.session] = match
    files: dict[str, str] = {}
    reader = 0
    if version == _STORE_VERSION:  # sinon les replays présents seront relus
        try:
            files = {str(key): str(value) for key, value in raw.get("files", {}).items()}
            reader = int(raw.get("reader", 0))
        except _BAD_ITEM:
            files, reader = {}, 0
    return Store(reader=reader, files=files, matches=matches)


def _set_aside(path: Path) -> None:
    """Copie d'un fichier illisible, avant qu'il soit réécrit : rien ne s'efface sans trace."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        copy = path.with_name(f"{path.stem}.illisible-{stamp}{path.suffix}")
        if not copy.exists():
            copy.write_bytes(path.read_bytes())
    except OSError as exc:
        log.warning("%s : copie du fichier illisible impossible (%s)", path.name, exc)


def load_store(*paths: Path) -> Store:
    """Parties gardées, réunies depuis chaque copie (la première l'emporte).

    Une copie effacée (dossier de l'application supprimé, réinstallation,
    nettoyage) se retrouve dans l'autre ; une copie illisible est mise de côté
    avant d'être réécrite. Rien de lisible : on repart de zéro, les replays
    encore présents sont relus. :class:`OSError` si une copie existe mais ne
    s'ouvre pas : le suivi réessaie au tour suivant.
    """
    base: Store | None = None
    for path in paths:
        try:
            store = _read_store(path)
        except ValueError as exc:
            log.warning("%s illisible (%s) : copie gardée à côté", path, exc)
            _set_aside(path)
            continue
        if store is None:
            continue
        if base is None:
            base = store
            continue
        for session, match in store.matches.items():
            base.matches.setdefault(session, match)
        if store.reader == base.reader:
            for key, value in store.files.items():
                base.files.setdefault(key, value)
    return base or Store()


def save_store(store: Store, *paths: Path) -> None:
    """Écrit chaque copie (atomique : fichier temporaire, puis remplacement).

    Une copie qui ne s'écrit pas n'empêche pas les autres ; :class:`OSError`
    seulement si aucune ne s'est écrite.
    """
    payload = _payload(store)
    errors: list[OSError] = []
    for path in paths:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(payload, "utf-8")
            temporary.replace(path)
        except OSError as exc:
            log.warning("%s : écriture impossible (%s)", path, exc)
            errors.append(exc)
    if errors and len(errors) == len(paths):
        raise errors[0]


def _payload(store: Store) -> str:
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
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


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
    # toutes les parties comptées, la plus récente d'abord : (début UTC, coffres)
    recent: tuple[tuple[datetime, int], ...] = ()
    counting: bool = False  # une partie quittée, son replay pas encore lu
    interrupted: bool = False  # la dernière partie s'est arrêtée jeu fermé : non comptée
    progress: tuple[int, int] | None = None  # plusieurs replays à lire : (lus, à lire)


def summarize(
    store: Store,
    *,
    recording: bool,
    replays: bool,
    failed: bool,
    counting: bool = False,
    interrupted: bool = False,
    progress: tuple[int, int] | None = None,
) -> Summary:
    ordered = sorted(store.matches.values(), key=_order)
    recent = tuple((_order(m), m.chests) for m in reversed(ordered))
    return Summary(
        total=sum(m.chests for m in ordered),
        matches=len(ordered),
        last=ordered[-1].chests if ordered else None,
        since=_order(ordered[0]) if ordered else None,
        recording=recording,
        replays=replays or bool(ordered),
        failed=failed,
        recent=recent,
        counting=counting,
        interrupted=interrupted,
        progress=progress,
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
        store_path: Path | Sequence[Path],
        reader: Reader,
        *,
        clock: Callable[[], float] = time.time,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.cfg = cfg
        # copies de l'historique : la première l'emporte, chacune réécrite si elle manque
        self.store_paths = (store_path,) if isinstance(store_path, Path) else tuple(store_path)
        self.reader = reader
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._summary: Summary | None = None
        self._unavailable = ""  # lecteur absent ou inutilisable
        self._stop = threading.Event()
        self._store: Store | None = None
        self._rewrite = False
        self._first = True  # premier tour : les parties d'avant le lancement
        self._sizes: dict[str, int] = {}  # taille de chaque replay au tour précédent
        self._failed = False
        self._interrupted = False
        # date du replay le plus récent déjà traité : lui seul dit si la dernière partie
        # a échoué ou s'est interrompue (un replay coupé n'est traité qu'au bout de 10 min)
        self._newest = float("-inf")

    def summary(self) -> Summary | None:
        """``None`` tant que rien n'est à montrer (le premier tour se repère par ``_first``)."""
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
            store = load_store(*self.store_paths)
            version = self.reader.version()
            if version != store.reader:
                # nouveau lecteur : les replays encore présents sont relus
                if store.reader:
                    log.info("coffres : lecteur %d -> %d, replays relus", store.reader, version)
                store.files.clear()
                store.reader = version
            self._store = store
            self._rewrite = True  # copies réunies, illisibles ou absentes : toutes réécrites
            if store.matches:  # l'historique gardé se montre avant toute lecture
                self._publish(store, recording=False, replays=True, counting=False)
        return self._store

    def refresh(self) -> Summary:
        """Un tour : lit les replays terminés, garde les parties, refait le résumé."""
        store = self._open_store()
        demos = self.cfg.resolved_demos_dir()
        try:
            paths = sorted(demos.glob("*.replay"), key=lambda p: p.name)
        except OSError:
            paths = []
        recording = waiting = False
        ready: list[tuple[Path, str, Header | None, float]] = []
        changed, self._rewrite = self._rewrite, False
        changed = changed or not all(path.exists() for path in self.store_paths)
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            key = file_key(path, stat)
            if key in store.files:
                continue
            header = read_header(path)
            if self._ready(path, stat, header):
                ready.append((path, key, header, stat.st_mtime))
            elif header is not None:
                # en direct : partie en cours ; sinon partie quittée, replay en train de se finir
                recording = recording or header.live
                waiting = waiting or not header.live
        first = self._first
        # les plus récents d'abord : la dernière partie se voit sans attendre les autres
        ready.sort(key=lambda item: item[3], reverse=True)
        # premier tour (parties jouées application fermée, nouveau lecteur) ou plusieurs
        # replays : une progression ; sinon le comptage de la partie qui vient de finir
        many = first or len(ready) > 1
        for done, (path, key, header, mtime) in enumerate(ready):
            self._publish(
                store,
                recording=recording,
                replays=True,
                counting=not many,
                progress=(done, len(ready)) if many else None,
            )
            newest = mtime >= self._newest
            self._newest = max(self._newest, mtime)
            store.files[key] = self._count(path, header, store, live=not first, newest=newest)
            self._save(store)  # gardé au fil de la lecture : rien à relire si on quitte
        self._first = False
        self._sizes = {k: v for k, v in self._sizes.items() if (demos / k).exists()}
        if changed:
            self._save(store)
        return self._publish(store, recording=recording, replays=bool(paths), counting=waiting)

    def _save(self, store: Store) -> None:
        try:
            save_store(store, *self.store_paths)
        except OSError:
            self._rewrite = True  # réessayé au tour suivant
            raise

    def _publish(
        self,
        store: Store,
        *,
        recording: bool,
        replays: bool,
        counting: bool,
        progress: tuple[int, int] | None = None,
    ) -> Summary:
        result = summarize(
            store,
            recording=recording,
            replays=replays,
            failed=self._failed,
            counting=counting,
            interrupted=self._interrupted,
            progress=progress,
        )
        with self._lock:
            self._summary = result
        return result

    def _count(
        self, path: Path, header: Header | None, store: Store, *, live: bool, newest: bool
    ) -> str:
        """Lit un replay prêt ; rend l'état gardé pour ce fichier.

        ``live`` : partie jouée panneau ouvert. Une partie interrompue ne se
        signale qu'alors ; celles d'avant le lancement ne sont plus d'actualité.
        ``newest`` : replay le plus récent traité jusqu'ici ; un plus ancien,
        traité en retard, ne change pas l'état de la dernière partie.
        """
        if header is not None and header.live and header.key_missing:
            log.info("coffres : %s incomplet (jeu fermé pendant la partie)", path.name)
            if newest:
                self._interrupted, self._failed = live, False
            return INCOMPLETE
        try:
            result = self.reader.read(path)
        except ReaderStopped:
            raise
        except ReaderError as exc:
            log.warning("coffres : %s non lu (%s)", path.name, exc)
            if newest:
                self._failed, self._interrupted = True, False
            return ERROR
        if not result.get("ok"):
            log.warning("coffres : %s illisible (%s)", path.name, result.get("error"))
            if newest:
                self._failed, self._interrupted = True, False
            return ERROR
        if newest:
            self._failed = self._interrupted = False
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
