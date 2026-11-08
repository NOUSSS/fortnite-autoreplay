"""Lecture des journaux de Fortnite et estimation des coffres ouverts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fortnite_auto_replay import config as config_mod
from fortnite_auto_replay.chests import (
    ChestTracker,
    Estimate,
    FileState,
    Milestone,
    Store,
    estimate,
    first_stamp,
    load_store,
    minutes_between,
    save_store,
    scan_file,
    scan_logs,
)
from fortnite_auto_replay.epic import LinkState
from fortnite_auto_replay.panel import ChestsState, chests_line, chests_note, chests_view

MARKER = "/Fortnite.com/GameFeatures/BRRoot"
DAY = datetime(2026, 11, 3, tzinfo=timezone.utc)


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute, second=second)


def stamp(when: datetime) -> str:
    return f"[{when:%Y.%m.%d-%H.%M.%S}:123][  0]"


def header() -> str:
    """En-tête sans horodatage, comme celui du jeu (« Log file open… »)."""
    return "﻿Log file open, 11/03/26 11:00:00\r\nLogInit: Build: ++Fortnite+Release-43.00\r\n"


def line(when: datetime, text: str) -> str:
    return f"{stamp(when)}{text}\r\n"


def lobby(when: datetime) -> str:
    return line(when, "LogLoad: LoadMap: /Game/Maps/Frontend?closed")


def battle(when: datetime) -> str:
    return line(
        when,
        "LogLoad: LoadMap: 18.157.16.111:9026/Delilah/Maps/Delilah_Terrain?EncryptionToken=x"
        f"?VerseURI={MARKER}?game=x",
    )


def creative(when: datetime) -> str:
    return line(when, "LogLoad: LoadMap: 13.38.1.1:7777/Island/Maps/Island?VerseURI=/Other/Root")


def write(path: Path, *chunks: str) -> Path:
    path.write_bytes("".join(chunks).encode("utf-8"))
    return path


def append(path: Path, *chunks: str) -> None:
    with path.open("ab") as handle:
        handle.write("".join(chunks).encode("utf-8"))


# --- lecture d'un journal ---------------------------------------------------------
def test_a_battle_royale_match_runs_from_its_map_to_the_lobby(tmp_path: Path) -> None:
    log = write(
        tmp_path / "FortniteGame.log",
        header(),
        lobby(at(10)),
        battle(at(10, 5)),
        line(at(10, 10), "LogFort: rien"),
        lobby(at(10, 20)),
        creative(at(10, 30)),
        lobby(at(10, 45)),
        battle(at(11)),
        lobby(at(11, 12)),
    )
    state = FileState()
    found = scan_file(log, state, MARKER.encode(), finished=False)
    assert found == [(at(10, 5), at(10, 20)), (at(11), at(11, 12))]
    assert state.offset == log.stat().st_size
    assert state.open is None


def test_a_match_still_running_stays_open_until_the_next_read(tmp_path: Path) -> None:
    log = write(tmp_path / "FortniteGame.log", header(), lobby(at(10)), battle(at(10, 5)))
    state = FileState()
    assert scan_file(log, state, MARKER.encode(), finished=False) == []
    assert state.open == at(10, 5)

    append(log, line(at(10, 15), "LogFort: tir"), lobby(at(10, 22)))
    assert scan_file(log, state, MARKER.encode(), finished=False) == [(at(10, 5), at(10, 22))]


def test_an_unfinished_last_line_is_read_next_time(tmp_path: Path) -> None:
    log = write(tmp_path / "FortniteGame.log", header(), battle(at(10)), lobby(at(10, 9))[:-2])
    state = FileState()
    assert scan_file(log, state, MARKER.encode(), finished=False) == []
    assert state.open == at(10)

    append(log, "\r\n")
    assert scan_file(log, state, MARKER.encode(), finished=False) == [(at(10), at(10, 9))]


def test_a_game_closed_mid_match_ends_at_the_last_line(tmp_path: Path) -> None:
    log = write(
        tmp_path / "FortniteGame.log",
        header(),
        battle(at(10)),
        line(at(10, 7, 30), "LogExit: Exiting."),
    )
    state = FileState()
    assert scan_file(log, state, MARKER.encode(), finished=True) == [(at(10), at(10, 7, 30))]
    assert state.open is None


def test_a_shorter_file_is_read_again_from_the_start(tmp_path: Path) -> None:
    log = write(tmp_path / "FortniteGame.log", header(), battle(at(10)), lobby(at(10, 9)))
    state = FileState(offset=10_000_000, open=at(9))
    assert scan_file(log, state, MARKER.encode(), finished=False) == [(at(10), at(10, 9))]


def test_first_stamp_skips_the_header(tmp_path: Path) -> None:
    log = write(tmp_path / "FortniteGame.log", header(), lobby(at(10, 31, 23)), battle(at(11)))
    assert first_stamp(log) == at(10, 31, 23)
    assert first_stamp(write(tmp_path / "empty.log", header())) is None


# --- plusieurs journaux -------------------------------------------------------------
def test_a_journal_renamed_by_the_game_is_not_read_twice(tmp_path: Path) -> None:
    current = write(tmp_path / "FortniteGame.log", header(), lobby(at(10)), battle(at(10, 5)))
    store = Store()
    assert scan_logs(store, tmp_path, "FortniteGame*.log", MARKER) == 0
    assert store.tracked_since == at(10)

    # le jeu relancé renomme l'ancien journal et en commence un autre
    append(current, line(at(10, 14), "LogExit: Exiting."))
    current.rename(tmp_path / "FortniteGame-backup-2026.11.03-10.14.00.log")
    write(
        tmp_path / "FortniteGame.log", header(), lobby(at(12)), battle(at(12, 2)), lobby(at(12, 9))
    )

    assert scan_logs(store, tmp_path, "FortniteGame*.log", MARKER) == 2
    assert store.sorted_matches() == [(at(10, 5), at(10, 14)), (at(12, 2), at(12, 9))]
    assert scan_logs(store, tmp_path, "FortniteGame*.log", MARKER) == 0
    assert store.tracked_since == at(10)


def test_the_store_survives_a_round_trip(tmp_path: Path) -> None:
    store = Store(
        tracked_since=at(9),
        files={"2026-11-03T09:00:00+00:00": FileState(1234, at(11))},
        matches={at(10): at(10, 20)},
    )
    path = tmp_path / "logs" / "matches.json"
    save_store(store, path)
    assert load_store(path) == store


def test_an_unreadable_store_starts_over(tmp_path: Path) -> None:
    path = tmp_path / "matches.json"
    assert load_store(path) == Store()
    path.write_text("{pas du json", encoding="utf-8")
    assert load_store(path) == Store()
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert load_store(path) == Store()


# --- estimation ------------------------------------------------------------------------
MATCHES = [
    (at(10), at(10, 20)),  # 20 min entre les paliers 0 et 100
    (at(11), at(11, 30)),  # 30 min
    (at(13), at(13, 10)),  # 10 min après le dernier palier
    (at(14), at(14, 30)),  # 30 min
]


def test_minutes_between_clips_the_matches() -> None:
    assert minutes_between(MATCHES, at(10, 10), at(11, 15)) == pytest.approx(25)


def test_the_rate_comes_from_the_milestones_and_the_rest_is_extrapolated() -> None:
    points = [Milestone(0, at(9)), Milestone(100, at(12))]
    result = estimate(MATCHES, points, tracked_since=at(8), now=at(15))
    # 100 coffres en 50 minutes, puis 40 minutes de partie : 100 + 80
    assert result == Estimate(total=180, confirmed=100, matches=2, per_minute=2.0)


def test_nothing_after_now_is_counted() -> None:
    points = [Milestone(0, at(9)), Milestone(100, at(12))]
    result = estimate(MATCHES, points, tracked_since=at(8), now=at(14, 15))
    assert result is not None
    assert result.matches == 1
    assert result.total == 100 + 2 * (10 + 15)


def test_intervals_before_the_journals_are_left_out() -> None:
    # 0 → 100 tombe avant le plus ancien journal lu : ses parties sont inconnues
    points = [Milestone(0, at(6)), Milestone(100, at(9)), Milestone(200, at(12))]
    result = estimate(MATCHES, points, tracked_since=at(8), now=at(15))
    assert result is not None
    assert result.per_minute == pytest.approx(2.0)
    assert result.total == 200 + 80


def test_no_estimate_without_a_measured_interval() -> None:
    assert estimate(MATCHES, [Milestone(100, at(12))], at(8), at(15)) is None
    assert estimate(MATCHES, [], at(8), at(15)) is None
    assert estimate(MATCHES, [Milestone(0, at(9)), Milestone(100, at(12))], None, at(15)) is None
    # dernier palier avant les journaux : les parties jouées depuis sont en partie inconnues
    late = [Milestone(0, at(9)), Milestone(100, at(12))]
    assert estimate(MATCHES, late, tracked_since=at(12, 30), now=at(15)) is None


def test_the_total_is_rounded_to_ten_and_never_below_the_milestone() -> None:
    points = [Milestone(0, at(9)), Milestone(1000, at(12))]
    few = [(at(10), at(10, 30)), (at(12), at(12, 0, 6))]  # 6 s après le palier
    result = estimate(few, points, at(8), at(15))
    assert result is not None
    assert result.total == 1000


# --- suivi et config -------------------------------------------------------------------
def test_the_tracker_reads_saves_and_estimates(tmp_path: Path) -> None:
    logs = tmp_path / "Logs"
    logs.mkdir()
    write(
        logs / "FortniteGame.log",
        header(),
        lobby(at(8)),
        *(chunk for begin, end in MATCHES for chunk in (battle(begin), lobby(end))),
    )
    cfg = config_mod.ChestsConfig(
        logs_dir=str(logs),
        milestones=[
            {"count": 0, "at": "2026-11-03T09:00:00Z"},
            {"count": 100, "at": "2026-11-03T12:00:00Z"},
        ],
    )
    store_path = tmp_path / "matches.json"
    tracker = ChestTracker(cfg, store_path, clock=lambda: at(15))
    assert tracker.estimate() is None
    tracker.refresh()
    assert tracker.estimate() == Estimate(total=180, confirmed=100, matches=2, per_minute=2.0)
    assert len(load_store(store_path).matches) == 4


def test_milestones_are_checked_when_loading_the_config() -> None:
    good = {"chests": {"milestones": [{"count": 1000, "at": "2026-11-04T18:47:36Z"}]}}
    cfg = config_mod.load_config(use_local=False, overrides=good)
    assert cfg.chests.milestones[0]["count"] == 1000
    for bad in (
        {"count": -1, "at": "2026-11-04T18:47:36Z"},
        {"count": 5, "at": "hier"},
        {"count": 5},
    ):
        with pytest.raises(config_mod.ConfigError):
            config_mod.load_config(use_local=False, overrides={"chests": {"milestones": [bad]}})
    with pytest.raises(config_mod.ConfigError, match="fuseau"):
        config_mod.parse_utc("2026-11-04T18:47:36")


def test_parse_utc_reads_z_and_offsets() -> None:
    assert config_mod.parse_utc("2026-11-04T19:47:36+01:00") == datetime(
        2026, 11, 4, 18, 47, 36, tzinfo=timezone.utc
    )
    assert config_mod.parse_utc("2026-11-04T18:47:36.042Z") - datetime(
        2026, 11, 4, 18, 47, 36, tzinfo=timezone.utc
    ) == timedelta(microseconds=42000)


# --- texte du panneau --------------------------------------------------------------------
def test_chests_line() -> None:
    nbsp = chr(0xA0)  # espace insécable
    assert chests_line(None) == ""
    line_ = chests_line(Estimate(total=2770, confirmed=1000, matches=158, per_minute=0.71))
    assert line_ == f"Environ 2{nbsp}770, dont 1{nbsp}000{nbsp}confirmés"
    assert chests_line(Estimate(total=1000, confirmed=1000, matches=0, per_minute=0.7)) == (
        f"1{nbsp}000{nbsp}confirmés"
    )


# --- rythme par défaut, minimum, fourchette ------------------------------------------------
def test_without_a_measured_interval_the_default_rate_is_used() -> None:
    points = [Milestone(100, at(12))]
    result = estimate(MATCHES, points, tracked_since=at(8), now=at(15), default_rate=1.0)
    assert result is not None
    assert not result.measured
    assert result.total == 100 + 40  # 40 minutes de partie après le palier
    assert result.partial_since is None


def test_journals_starting_after_the_last_milestone_give_a_minimum() -> None:
    points = [Milestone(0, at(6)), Milestone(1000, at(9))]
    result = estimate(MATCHES, points, tracked_since=at(10, 30), now=at(15), default_rate=2.0)
    assert result is not None
    assert result.partial_since == at(10, 30)
    assert result.total == 1000 + 2 * (30 + 10 + 30)  # seules les parties lues
    assert result.matches == 3
    assert (result.low, result.high) == (0, 0)
    assert chests_line(result).startswith("Plus de 1")


def test_two_long_intervals_give_a_range() -> None:
    matches = [(at(9), at(10)), (at(11), at(12)), (at(13), at(13, 30))]  # 60, 60, 30 min
    points = [Milestone(0, at(8)), Milestone(120, at(10, 30)), Milestone(300, at(12, 30))]
    result = estimate(matches, points, tracked_since=at(7), now=at(14))
    assert result is not None
    assert result.per_minute == pytest.approx(300 / 120)
    assert result.total == 300 + 80  # 2,5 coffres par minute sur 30 minutes, à la dizaine
    assert (result.low, result.high) == (360, 390)  # 2 et 3 coffres par minute


def test_a_short_interval_does_not_widen_the_range() -> None:
    matches = [(at(9), at(9, 5)), (at(11), at(12)), (at(13), at(13, 30))]
    points = [Milestone(0, at(8)), Milestone(25, at(10)), Milestone(145, at(12, 30))]
    result = estimate(matches, points, tracked_since=at(7), now=at(14))
    assert result is not None
    assert (result.low, result.high) == (0, 0)  # 0 → 25 trop court : un seul intervalle retenu


def test_an_unchanged_count_slows_the_rate_down() -> None:
    # palier 100, puis le compteur lu sur le compte toujours à 100 après 30 minutes de partie
    points = [Milestone(0, at(9)), Milestone(100, at(10, 30)), Milestone(100, at(12, 30))]
    result = estimate(MATCHES, points, tracked_since=at(8), now=at(15))
    assert result is not None
    # sans l'intervalle 100 → 100, le rythme serait de 100 / 20
    assert result.per_minute == pytest.approx(100 / (20 + 30))


# --- suivi : paliers donnés en cours de route ------------------------------------------------
def test_new_milestones_are_applied_at_the_next_reading(tmp_path: Path) -> None:
    logs = tmp_path / "Logs"
    logs.mkdir()
    write(
        logs / "FortniteGame.log",
        header(),
        lobby(at(8)),
        *(chunk for begin, end in MATCHES for chunk in (battle(begin), lobby(end))),
    )
    cfg = config_mod.ChestsConfig(logs_dir=str(logs), default_rate=0.0)
    tracker = ChestTracker(cfg, tmp_path / "matches.json", clock=lambda: at(15))
    tracker.refresh()
    assert tracker.scanned
    assert not tracker.has_milestones
    assert tracker.estimate() is None

    tracker.set_milestones([Milestone(100, at(12)), Milestone(0, at(9))])
    assert tracker.has_milestones
    assert not tracker.scanned
    tracker.refresh()
    assert tracker.scanned
    assert tracker.estimate() == Estimate(total=180, confirmed=100, matches=2, per_minute=2.0)


def test_default_rate_is_checked() -> None:
    with pytest.raises(config_mod.ConfigError, match="default_rate"):
        config_mod.load_config(use_local=False, overrides={"chests": {"default_rate": -1}})


# --- section du panneau ------------------------------------------------------------------------
def test_chests_notes() -> None:
    nbsp = chr(0xA0)  # espace insécable
    ranged = Estimate(2790, 1000, 159, 0.71, low=2630, high=3210)
    assert chests_note(ranged) == f"Entre 2{nbsp}630 et 3{nbsp}210"
    assumed = Estimate(1420, 1000, 40, 0.71, measured=False)
    assert chests_note(assumed) == f"Au rythme supposé de 0,7{nbsp}coffre par minute"
    since = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    partial = Estimate(1840, 1000, 80, 0.71, measured=False, partial_since=since)
    assert chests_note(partial) == f"Journaux du jeu lus depuis le 6{nbsp}novembre"
    assert chests_note(Estimate(2790, 1000, 159, 0.71)) == ""


def test_chests_view_follows_the_epic_link_first() -> None:
    ranged = Estimate(2790, 1000, 159, 0.71, low=2630, high=3210)
    idle = LinkState()
    invite = chests_view(ChestsState(None, False, True, idle))
    assert invite.notice is not None
    assert invite.notice[2] == "Se connecter à Epic"
    waiting = chests_view(ChestsState(None, False, True, LinkState("waiting")))
    assert waiting.notice is not None
    assert waiting.notice[2] == "Annuler"
    reading = chests_view(ChestsState(None, False, True, LinkState("reading")))
    assert reading.notice is not None
    assert reading.notice[2] == ""
    failed = chests_view(ChestsState(None, False, True, LinkState("failed", "délai dépassé")))
    assert failed.notice is not None
    assert failed.notice[0] == "caution"
    assert failed.notice[1].endswith(": délai dépassé.")
    assert chests_view(ChestsState(None, True, False, LinkState("done"))).line.startswith(
        "Lecture des journaux"
    )
    assert chests_view(ChestsState(None, True, True, idle)).line == "Aucun journal du jeu trouvé"
    shown = chests_view(ChestsState(ranged, True, True, idle))
    assert shown.line.startswith("Environ 2")
    assert shown.note.startswith("Entre")
    assert shown.notice is None
