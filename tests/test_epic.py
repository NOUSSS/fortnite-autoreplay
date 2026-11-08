"""Lecture des paliers de la quête de coffres sur le compte Epic (réseau simulé)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import pytest

from fortnite_auto_replay import epic
from fortnite_auto_replay.chests import Milestone
from fortnite_auto_replay.epic import EpicError, EpicLink, LinkState

NOW = datetime(2026, 11, 8, 17, 0, tzinfo=timezone.utc)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def stage(index: int, count: int, created: str, changed: str, state: str = "Claimed") -> Any:
    name = f"quest_s43_treasurehunt_count_{index:02d}"
    return {
        "templateId": f"Quest:{name}",
        "attributes": {
            "quest_state": state,
            "creation_time": created,
            "last_state_change_time": changed,
            f"completion_{name}_obj0": count,
            "level": -1,
        },
    }


def profile(*stages: Any, extra: dict[str, Any] | None = None) -> Any:
    items = {f"id-{i}": item for i, item in enumerate(stages)}
    items.update(extra or {})
    return {"profileChanges": [{"profile": {"items": items}}]}


# étapes relevées sur un vrai compte (saison 43)
REAL = (
    stage(1, 25, "2026-11-01T11:50:05.457Z", "2026-11-02T13:50:22.241Z"),
    stage(2, 100, "2026-11-02T13:47:05.710Z", "2026-11-02T16:25:06.222Z"),
    stage(3, 250, "2026-11-02T16:21:16.916Z", "2026-11-03T11:12:15.797Z"),
    stage(4, 500, "2026-11-03T11:00:52.086Z", "2026-11-03T16:21:17.936Z"),
    stage(5, 1000, "2026-11-03T16:06:43.826Z", "2026-11-04T18:47:36.042Z"),
)


# --- paliers tirés du profil ----------------------------------------------------------
def test_each_stage_ends_when_the_next_one_is_created() -> None:
    other = {"x": {"templateId": "Quest:quest_s43_treasurehunt_rarity_05", "attributes": {}}}
    points = epic.milestones_from_profile(profile(*reversed(REAL), extra=other), NOW)
    assert points == [
        Milestone(0, utc("2026-11-01T11:50:05.457Z")),
        Milestone(25, utc("2026-11-02T13:47:05.710Z")),
        Milestone(100, utc("2026-11-02T16:21:16.916Z")),
        Milestone(250, utc("2026-11-03T11:00:52.086Z")),
        Milestone(500, utc("2026-11-03T16:06:43.826Z")),
        Milestone(1000, utc("2026-11-04T18:47:36.042Z")),  # dernière : son changement d'état
    ]


def test_a_stage_in_progress_gives_its_count_now() -> None:
    running = stage(3, 173, "2026-11-02T16:21:16.916Z", "2026-11-02T16:21:16.916Z", "Active")
    points = epic.milestones_from_profile(profile(*REAL[:2], running), NOW)
    assert points[-2:] == [Milestone(100, utc("2026-11-02T16:21:16.916Z")), Milestone(173, NOW)]


def test_a_stage_just_started_still_says_no_chest_since() -> None:
    running = stage(3, 100, "2026-11-02T16:21:16.916Z", "2026-11-02T16:21:16.916Z", "Active")
    points = epic.milestones_from_profile(profile(*REAL[:2], running), NOW)
    assert points[-1] == Milestone(100, NOW)


def test_a_stage_counting_from_zero_is_left_out() -> None:
    running = stage(3, 12, "2026-11-02T16:21:16.916Z", "2026-11-02T16:21:16.916Z", "Active")
    points = epic.milestones_from_profile(profile(*REAL[:2], running), NOW)
    assert points[-1].count == 100


def test_the_latest_season_wins() -> None:
    old = {
        "old": {
            "templateId": "Quest:quest_s42_treasurehunt_count_01",
            "attributes": {
                "quest_state": "Claimed",
                "creation_time": "2026-09-01T00:00:00Z",
                "last_state_change_time": "2026-09-02T00:00:00Z",
                "completion_quest_s42_treasurehunt_count_01_obj0": 25,
            },
        }
    }
    points = epic.milestones_from_profile(profile(*REAL, extra=old), NOW)
    assert points[0].at == utc("2026-11-01T11:50:05.457Z")


def test_an_account_without_the_quest_says_so() -> None:
    with pytest.raises(EpicError) as error:
        epic.milestones_from_profile(profile(), NOW)
    assert error.value.reason == "aucune quête de coffres sur ce compte"
    with pytest.raises(EpicError):
        epic.milestones_from_profile({"profileChanges": []}, NOW)


# --- connexion ------------------------------------------------------------------------
class Instant(threading.Event):
    """Attente sans délai : les tests ne dorment pas."""

    def wait(self, timeout: float | None = None) -> bool:
        return self.is_set()


class FakeEpic:
    """Serveurs d'Epic simulés : une réponse par étape, les requêtes gardées."""

    def __init__(self, polls: list[tuple[int, Any]], profile_status: int = 200) -> None:
        self.polls = list(polls)
        self.profile_status = profile_status
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, Any]:
        self.calls.append((method, url, headers, body))
        if url.endswith("/oauth/token") and body == b"grant_type=client_credentials":
            return 200, {"access_token": "client-token"}
        if url.endswith("/deviceAuthorization"):
            return 200, {
                "device_code": "dev-123",
                "verification_uri_complete": "https://www.epicgames.com/activate?userCode=ABCD",
                "interval": 10,
                "expires_in": 600,
            }
        if url.endswith("/oauth/token"):
            status, data = self.polls.pop(0)
            if isinstance(data, Exception):
                raise data
            return status, data
        if "QueryProfile" in url:
            return self.profile_status, profile(*REAL) if self.profile_status == 200 else {}
        if method == "DELETE":
            return 204, None
        raise AssertionError(url)

    def killed(self) -> bool:
        return any(method == "DELETE" for method, *_ in self.calls)


PENDING = (400, {"errorCode": "errors.com.epicgames.account.oauth.authorization_pending"})
TOKEN = (200, {"access_token": "user-token", "account_id": "acc/1"})


def test_sign_in_reads_the_milestones_and_closes_the_session() -> None:
    fake = FakeEpic([PENDING, PENDING, TOKEN])
    opened: list[str] = []
    points = epic.sign_in(opened.append, Instant(), fake)
    assert opened == ["https://www.epicgames.com/activate?userCode=ABCD"]
    assert points is not None
    assert points[-1].count == 1000
    profile_call = next(c for c in fake.calls if "QueryProfile" in c[1])
    assert "/acc%2F1/client/QueryProfile?profileId=athena" in profile_call[1]
    assert profile_call[2]["Authorization"] == "bearer user-token"
    assert fake.calls[-1][0] == "DELETE"
    assert fake.calls[-1][1].endswith("/sessions/kill/user-token")


def test_the_session_is_closed_even_when_the_profile_is_refused() -> None:
    fake = FakeEpic([TOKEN], profile_status=403)
    with pytest.raises(EpicError) as error:
        epic.sign_in(lambda _url: None, Instant(), fake)
    assert error.value.reason == "Epic a refusé la demande"
    assert fake.killed()


def test_cancel_stops_the_wait_without_a_session() -> None:
    cancel = Instant()
    cancel.set()
    fake = FakeEpic([])
    assert epic.sign_in(lambda _url: None, cancel, fake) is None
    assert not fake.killed()


def test_a_refused_confirmation_is_an_error() -> None:
    refused = (400, {"errorCode": "errors.com.epicgames.account.oauth.authorization_declined"})
    with pytest.raises(EpicError, match="refusé"):
        epic.sign_in(lambda _url: None, Instant(), FakeEpic([refused]))


def test_a_short_network_failure_is_tolerated_but_not_a_long_one() -> None:
    down = (0, EpicError("Epic ne répond pas", "hors ligne"))
    assert epic.sign_in(lambda _url: None, Instant(), FakeEpic([down, down, TOKEN])) is not None
    with pytest.raises(EpicError, match="ne répond pas"):
        epic.sign_in(lambda _url: None, Instant(), FakeEpic([down, down, down]))


def test_the_wait_ends_when_the_code_expires() -> None:
    ticks = iter(range(0, 10_000, 100))
    login = epic.DeviceLogin("https://x", "dev", interval_s=10, expires_s=250)
    fake = FakeEpic([PENDING] * 5)
    with pytest.raises(EpicError) as error:
        epic.wait_for_token(login, Instant(), fake, clock=lambda: float(next(ticks)))
    assert error.value.reason == "délai dépassé"


def test_slow_down_lengthens_the_interval() -> None:
    waits: list[float] = []

    class Recorder(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            waits.append(float(timeout or 0))
            return False

    slow = (400, {"errorCode": "errors.com.epicgames.account.oauth.slow_down"})
    login = epic.DeviceLogin("https://x", "dev", interval_s=10, expires_s=600)
    epic.wait_for_token(login, Recorder(), FakeEpic([slow, TOKEN]))
    assert waits == [10, 15]


# --- depuis le panneau ------------------------------------------------------------------
def test_the_link_hands_over_the_milestones_and_ends_done() -> None:
    got: list[list[Milestone]] = []
    link = EpicLink(got.append, send=FakeEpic([TOKEN]), open_page=lambda _url: None)
    link._cancel = Instant()
    link.connect()
    link.join(5)
    assert link.state() == LinkState("done")
    assert got[0][-1].count == 1000


def test_the_link_reports_a_failure_in_a_few_words() -> None:
    link = EpicLink(lambda _p: None, send=FakeEpic([TOKEN], profile_status=500))
    link._open_page = lambda _url: None
    link._cancel = Instant()
    link.connect()
    link.join(5)
    assert link.state() == LinkState("failed", "Epic a refusé la demande")
