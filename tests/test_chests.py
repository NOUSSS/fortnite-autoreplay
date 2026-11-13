"""Compteur des coffres ouverts, d'après les replays de Fortnite (lecteur simulé)."""

from __future__ import annotations

import json
import os
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from fortnite_auto_replay import config as config_mod
from fortnite_auto_replay.chests import (
    ERROR,
    INCOMPLETE,
    OTHER_MODE,
    ChestTracker,
    Header,
    MatchChests,
    Reader,
    ReaderError,
    ReaderStopped,
    Store,
    Summary,
    load_store,
    read_header,
    save_store,
    summarize,
)
from fortnite_auto_replay.config import ChestsConfig
from fortnite_auto_replay.panel import (
    NO_MATCH_YET,
    READ_FAILED,
    READ_FAILED_SHORT,
    READER_DOWN,
    READER_DOWN_SHORT,
    READING_REPLAYS,
    RECORDING,
    REPLAYS_OFF,
    REPLAYS_OFF_SHORT,
    C,
    ChestsState,
    chests_view,
    match_time,
)

NOW = 1_800_000_000.0  # horloge simulée (secondes)
UTC_NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def header_bytes(
    *, live: bool, key: bytes | None = b"\x01" * 32, version: int = 7, name: str = "Unsaved"
) -> bytes:
    """En-tête de replay comme Fortnite l'écrit (``key=None`` : non chiffré)."""
    data = struct.pack("<II", 0x1CA2E27F, version)
    if version >= 7:
        data += struct.pack("<i", 1) + b"\0" * 20
    data += struct.pack("<III", 600_000, 39, 123)
    encoded = name.encode() + b"\0"
    data += struct.pack("<i", len(encoded)) + encoded
    data += struct.pack("<IqI", int(live), 0, 1)
    if key is None:
        return data + struct.pack("<I", 0)
    return data + struct.pack("<Ii", 1, len(key)) + key


def write_replay(folder: Path, name: str, *, mtime: float, **header: Any) -> Path:
    path = folder / name
    path.write_bytes(header_bytes(**header) + b"\0" * 64)
    os.utime(path, (mtime, mtime))
    return path


class FakeReader(Reader):
    """Lecteur simulé : réponses par nom de fichier, lectures notées."""

    def __init__(self, answers: dict[str, dict[str, Any]], version: int = 15) -> None:
        super().__init__(Path("replay-reader.exe"))
        self.answers = answers
        self._version = version
        self.reads: list[str] = []

    def version(self) -> int:
        return self._version

    def read(self, path: Path) -> dict[str, Any]:
        self.reads.append(path.name)
        answer = self.answers[path.name]
        if isinstance(answer, Exception):
            raise answer
        return answer


def match(session: str, chests: int, *, start: str = "2026-11-10T10:00:00Z", **extra: Any) -> dict:
    return {
        "ok": True,
        "session_id": session,
        "start": start,
        "battle_royale": True,
        "chests": chests,
        "destroyed": 1,
        **extra,
    }


@pytest.fixture
def demos(tmp_path: Path) -> Path:
    folder = tmp_path / "Demos"
    folder.mkdir()
    return folder


def tracker_for(tmp_path: Path, demos: Path, reader: FakeReader) -> ChestTracker:
    cfg = ChestsConfig(demos_dir=str(demos))
    return ChestTracker(
        cfg, tmp_path / "chests.json", reader, clock=lambda: NOW, now=lambda: UTC_NOW
    )


# --- en-tête ----------------------------------------------------------------------------
def test_read_header_live_and_key(tmp_path: Path) -> None:
    path = tmp_path / "a.replay"
    path.write_bytes(header_bytes(live=True, key=b"\0" * 32))
    assert read_header(path) == Header(live=True, key_missing=True)
    path.write_bytes(header_bytes(live=False))
    assert read_header(path) == Header(live=False, key_missing=False)
    path.write_bytes(header_bytes(live=False, key=None, version=6))
    assert read_header(path) == Header(live=False, key_missing=False)


def test_read_header_rejects_other_files(tmp_path: Path) -> None:
    path = tmp_path / "a.replay"
    path.write_bytes(b"pas un replay")
    assert read_header(path) is None
    path.write_bytes(header_bytes(live=False)[:30])  # tronqué
    assert read_header(path) is None
    assert read_header(tmp_path / "absent.replay") is None


# --- parties gardées ------------------------------------------------------------------
def test_store_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "chests.json"
    store = Store(
        reader=15,
        files={"a.replay|10|5": "s1", "b.replay|3|1": INCOMPLETE},
        matches={"s1": MatchChests("s1", UTC_NOW, 8, 2, UTC_NOW)},
    )
    save_store(store, path)
    assert load_store(path) == store


def test_unreadable_store_starts_over(tmp_path: Path) -> None:
    path = tmp_path / "chests.json"
    path.write_text("{", encoding="utf-8")
    assert load_store(path) == Store()
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert load_store(path) == Store()
    assert load_store(tmp_path / "absent.json") == Store()


# --- suivi ------------------------------------------------------------------------------
def test_counts_finished_replays_once(tmp_path: Path, demos: Path) -> None:
    write_replay(demos, "old.replay", mtime=NOW - 3600, live=False)
    reader = FakeReader({"old.replay": match("s1", 9)})
    tracker = tracker_for(tmp_path, demos, reader)
    summary = tracker.refresh()
    assert (summary.total, summary.matches, summary.last) == (9, 1, 9)
    assert summary.since == datetime(2026, 11, 10, 10, tzinfo=timezone.utc)
    tracker.refresh()
    assert reader.reads == ["old.replay"]
    # un nouveau suivi (relance du panneau) ne relit pas non plus
    again = tracker_for(tmp_path, demos, reader)
    assert again.refresh().total == 9
    assert reader.reads == ["old.replay"]


def test_live_replay_waits_for_the_end(tmp_path: Path, demos: Path) -> None:
    path = write_replay(demos, "now.replay", mtime=NOW - 5, live=True, key=b"\0" * 32)
    reader = FakeReader({"now.replay": match("s1", 4)})
    tracker = tracker_for(tmp_path, demos, reader)
    summary = tracker.refresh()
    assert summary.recording and summary.replays and summary.total == 0
    # fin de partie : plus en direct, clé écrite ; lu une fois sa taille stable
    path.write_bytes(header_bytes(live=False) + b"\0" * 4096)  # fin du replay écrite
    os.utime(path, (NOW - 5, NOW - 5))
    assert tracker.refresh().total == 0  # taille changée depuis le tour d'avant
    summary = tracker.refresh()
    assert (summary.total, summary.recording) == (4, False)


def test_abandoned_recording_is_incomplete(tmp_path: Path, demos: Path) -> None:
    write_replay(demos, "cut.replay", mtime=NOW - 3600, live=True, key=b"\0" * 32)
    reader = FakeReader({})
    tracker = tracker_for(tmp_path, demos, reader)
    summary = tracker.refresh()
    assert reader.reads == []
    assert summary.total == 0 and not summary.recording
    assert INCOMPLETE in load_store(tmp_path / "chests.json").files.values()


def test_other_modes_and_errors(tmp_path: Path, demos: Path) -> None:
    write_replay(demos, "a.replay", mtime=NOW - 3600, live=False)
    write_replay(demos, "b.replay", mtime=NOW - 3500, live=False)
    reader = FakeReader(
        {
            "a.replay": match("s1", 3, battle_royale=False),
            "b.replay": {"ok": False, "error": "InvalidReplayException"},
        }
    )
    tracker = tracker_for(tmp_path, demos, reader)
    summary = tracker.refresh()
    assert (summary.total, summary.failed) == (0, True)
    assert sorted(load_store(tmp_path / "chests.json").files.values()) == sorted(
        [ERROR, OTHER_MODE]
    )


def test_same_match_counts_once(tmp_path: Path, demos: Path) -> None:
    write_replay(demos, "a.replay", mtime=NOW - 3600, live=False)
    write_replay(demos, "copy.replay", mtime=NOW - 3000, live=False)
    reader = FakeReader({"a.replay": match("s1", 6), "copy.replay": match("s1", 6)})
    assert tracker_for(tmp_path, demos, reader).refresh().total == 6


def test_new_reader_rereads_present_replays(tmp_path: Path, demos: Path) -> None:
    write_replay(demos, "a.replay", mtime=NOW - 3600, live=False)
    tracker_for(tmp_path, demos, FakeReader({"a.replay": match("s1", 6)}, version=15)).refresh()
    # le replay a disparu, mais la partie comptée reste
    store = load_store(tmp_path / "chests.json")
    store.matches["gone"] = MatchChests("gone", None, 5, 0, UTC_NOW)
    save_store(store, tmp_path / "chests.json")
    newer = FakeReader({"a.replay": match("s1", 7)}, version=16)
    summary = tracker_for(tmp_path, demos, newer).refresh()
    assert newer.reads == ["a.replay"]
    assert (summary.total, summary.matches) == (12, 2)


def test_no_demos_folder(tmp_path: Path) -> None:
    tracker = tracker_for(tmp_path, tmp_path / "absent", FakeReader({}))
    summary = tracker.refresh()
    assert not summary.replays and summary.total == 0


def test_missing_reader_stops_the_watch(tmp_path: Path, demos: Path) -> None:
    def broken(command: list[str], timeout: float) -> str:
        raise ReaderError("lecteur de replays introuvable")

    cfg = ChestsConfig(demos_dir=str(demos), scan_interval_s=2)
    tracker = ChestTracker(cfg, tmp_path / "chests.json", Reader(Path("x.exe"), run=broken))
    tracker._watch()  # rend la main sans boucler
    assert tracker.unavailable == "lecteur de replays introuvable"


def test_reader_parses_last_json_line() -> None:
    calls: list[list[str]] = []

    def run(command: list[str], timeout: float) -> str:
        calls.append(command)
        if command[-1] == "--version":
            return "15\n"
        return 'bruit\n{"ok": true, "chests": 3}\n'

    reader = Reader(Path("r.exe"), run=run)
    assert reader.version() == 15
    assert reader.read(Path("a.replay")) == {"ok": True, "chests": 3}
    bad = Reader(Path("r.exe"), run=lambda c, t: "")
    with pytest.raises(ReaderError):
        bad.read(Path("a.replay"))


# --- config -------------------------------------------------------------------------------
def test_old_estimate_settings_are_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    default = tmp_path / "default.json"
    default.write_text(config_mod.DEFAULT_CONFIG_PATH.read_text("utf-8"), encoding="utf-8")
    old = {"chests": {"milestones": [{"count": 1000, "at": "2026-11-01T00:00:00Z"}]}}
    (tmp_path / "local.json").write_text(json.dumps(old), encoding="utf-8")
    cfg = config_mod.load_config(default)
    assert cfg.chests.enabled
    assert "milestones" not in caplog.text


# --- panneau ------------------------------------------------------------------------------
def summary(**kw: Any) -> Summary:
    base: dict[str, Any] = {
        "total": 137,
        "matches": 18,
        "last": 9,
        "since": datetime(2026, 11, 6, 10, tzinfo=timezone.utc),
        "recording": False,
        "replays": True,
        "failed": False,
    }
    return Summary(**{**base, **kw})


def test_summary_lists_the_last_five_matches_newest_first() -> None:
    def at(hour: int) -> datetime:
        return datetime(2026, 11, 10, hour, tzinfo=timezone.utc)

    store = Store()
    for hour in (9, 14, 11, 16, 10, 13, 12):  # dans le désordre
        store.matches[f"s{hour}"] = MatchChests(f"s{hour}", at(hour), hour, 0, at(hour))
    summary = summarize(store, recording=False, replays=True, failed=False)
    assert summary.recent == tuple((at(h), h) for h in (16, 14, 13, 12, 11))
    assert summarize(Store(), recording=False, replays=True, failed=False).recent == ()


def test_match_time_is_local_with_a_short_month() -> None:
    nbsp = chr(0xA0)
    when = datetime(2026, 9, 3, 18, 7).astimezone()  # heure locale
    assert match_time(when) == f"3{nbsp}sept. 18:07"


def test_chests_view() -> None:
    nbsp = chr(0xA0)
    reading = chests_view(ChestsState(None))
    assert (reading.state, reading.value, reading.dot) == (READING_REPLAYS, "", C.neutral)
    down = chests_view(ChestsState(None, "absent"))
    assert (down.state, down.notice) == (READER_DOWN_SHORT, ("caution", READER_DOWN))
    off = summary(total=0, matches=0, last=None, since=None, replays=False)
    shown = chests_view(ChestsState(off))
    assert (shown.state, shown.value, shown.dot) == (REPLAYS_OFF_SHORT, "", C.caution)
    assert shown.notice == ("caution", REPLAYS_OFF)
    none = chests_view(ChestsState(summary(total=0, matches=0, last=None, since=None)))
    assert (none.state, none.value, none.note, none.dot) == (NO_MATCH_YET, "0", "", C.success)
    counted = chests_view(ChestsState(summary()))
    assert (counted.value, counted.dot, counted.notice) == ("137", C.success, None)
    assert counted.state == f"9{nbsp}coffres à la dernière partie"
    assert counted.note == f"18{nbsp}parties depuis le 6{nbsp}novembre"
    assert chests_view(ChestsState(summary(recording=True))).state == RECORDING
    failed = chests_view(ChestsState(summary(failed=True)))
    assert (failed.state, failed.dot) == (READ_FAILED_SHORT, C.caution)
    assert failed.notice == ("caution", READ_FAILED)
    one = chests_view(ChestsState(summary(total=1, matches=1, last=1)))
    assert one.state == f"1{nbsp}coffre à la dernière partie"
    assert one.note == f"1{nbsp}partie depuis le 6{nbsp}novembre"
    games = summary(recent=((datetime(2026, 11, 13, 18, 12).astimezone(), 1),))
    assert chests_view(ChestsState(games)).games == ((f"13{nbsp}nov. 18:12", f"1{nbsp}coffre"),)
    assert counted.games == ()
    big = chests_view(ChestsState(summary(total=1234)))
    assert big.value == f"1{nbsp}234"


def test_kill_stops_a_running_read() -> None:
    reader = Reader(Path(sys.executable))
    failures: list[Exception] = []

    def read() -> None:
        try:
            reader._spawn([sys.executable, "-c", "import time; time.sleep(30)"], 60)
        except ReaderError as exc:
            failures.append(exc)

    worker = threading.Thread(target=read)
    started = time.monotonic()
    worker.start()
    while reader._process is None and time.monotonic() - started < 10:
        time.sleep(0.01)
    reader.kill()
    worker.join(10)
    assert not worker.is_alive() and time.monotonic() - started < 10
    assert len(failures) == 1 and isinstance(failures[0], ReaderStopped)
    with pytest.raises(ReaderStopped):  # plus aucune lecture après l'arrêt
        reader.version()


def test_reader_timeout_and_exit_code() -> None:
    reader = Reader(Path(sys.executable))
    with pytest.raises(ReaderError, match="trop longue"):
        reader._spawn([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
    with pytest.raises(ReaderError, match="échec"):
        reader._spawn([sys.executable, "-c", "raise SystemExit(3)"], 30)
    assert reader._spawn([sys.executable, "-c", "print(15)"], 30).strip() == "15"
