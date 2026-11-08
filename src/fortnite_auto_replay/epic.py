"""Paliers de la quête de coffres, lus sur le compte Epic du joueur.

Connexion par code d'appareil : l'application ouvre la page d'activation
d'Epic dans le navigateur, le joueur confirme, et l'application lit son profil
Fortnite (``athena``). Le jeton ne sert qu'à cette lecture : il n'est ni
écrit ni journalisé, et la session est fermée aussitôt.

Les étapes de la quête (``quest_s<saison>_treasurehunt_count_<n>``) donnent
les paliers : une étape franchie crée la suivante, dont la date de création
est donc celle du palier ; la dernière n'a que la date de son changement
d'état. Une étape en cours donne son compteur à l'heure de la lecture.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from . import __version__
from .chests import Milestone
from .config import ConfigError, parse_utc

log = logging.getLogger(__name__)

# Client public du jeu Fortnite sur Switch, livré avec le jeu et publié de longue
# date : le seul qui accepte la connexion par code d'appareil. Ce n'est pas un
# secret du joueur.
_CLIENT = ("98f7e42c2e3a4f86a74eb43fbb41ed39", "0a2449a2-001a-451e-afec-3e812901c4d7")
_OAUTH = "https://account-public-service-prod.ol.epicgames.com/account/api/oauth"
_PROFILE = "https://mcp-gc.live.fngw.ol.epicgames.com/fortnite/api/game/v2/profile"
_FORM = "application/x-www-form-urlencoded"
_PENDING = "errors.com.epicgames.account.oauth.authorization_pending"
_SLOW_DOWN = "errors.com.epicgames.account.oauth.slow_down"
_USER_AGENT = f"FortniteAutoReplay/{__version__}"
_TIMEOUT_S = 20.0
_NETWORK_RETRIES = 3  # erreurs réseau d'affilée tolérées pendant l'attente de la confirmation
_STAGE = re.compile(r"^quest:quest_s(\d+)_treasurehunt_count_(\d+)$")
_DONE = frozenset({"claimed", "completed"})
_UNEXPECTED = "réponse inattendue d\u2019Epic"

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any]]


class EpicError(Exception):
    """Échec de la lecture du compte ; ``reason`` est la raison courte montrée au joueur."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason} ({detail})" if detail else reason)
        self.reason = reason


def _send(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, Any]:
    """Requête HTTPS : (statut, JSON décodé ou None)."""
    request = urllib.request.Request(
        url, data=body, method=method, headers={"User-Agent": _USER_AGENT, **headers}
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EpicError("Epic ne répond pas", str(exc)) from exc
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def _error_code(data: Any) -> str:
    return str(data.get("errorCode", "")) if isinstance(data, dict) else ""


def _refused(step: str, status: int, data: Any) -> EpicError:
    return EpicError("Epic a refusé la demande", f"{step} : HTTP {status} {_error_code(data)}")


def _basic() -> str:
    pair = f"{_CLIENT[0]}:{_CLIENT[1]}".encode()
    return "basic " + base64.b64encode(pair).decode("ascii")


@dataclass(frozen=True, slots=True)
class DeviceLogin:
    url: str  # page d'activation, code compris
    device_code: str
    interval_s: float
    expires_s: float


def start_login(send: Transport = _send) -> DeviceLogin:
    """Demande un code d'appareil ; la page rendue est celle où le joueur confirme."""
    status, data = send(
        "POST",
        f"{_OAUTH}/token",
        {"Authorization": _basic(), "Content-Type": _FORM},
        b"grant_type=client_credentials",
    )
    if status != 200 or not isinstance(data, dict) or "access_token" not in data:
        raise _refused("jeton du client", status, data)
    status, device = send(
        "POST",
        f"{_OAUTH}/deviceAuthorization",
        {"Authorization": f"bearer {data['access_token']}", "Content-Type": _FORM},
        b"",
    )
    if status != 200 or not isinstance(device, dict):
        raise _refused("code d'appareil", status, device)
    try:
        return DeviceLogin(
            url=str(device["verification_uri_complete"]),
            device_code=str(device["device_code"]),
            interval_s=max(1.0, float(device.get("interval", 10))),
            expires_s=float(device.get("expires_in", 600)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EpicError(_UNEXPECTED, f"code d'appareil : {exc}") from exc


def wait_for_token(
    login: DeviceLogin,
    cancel: threading.Event,
    send: Transport = _send,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """Attend la confirmation du joueur ; None si l'attente est annulée."""
    deadline = clock() + login.expires_s
    interval = login.interval_s
    body = urllib.parse.urlencode(
        {"grant_type": "device_code", "device_code": login.device_code}
    ).encode()
    failures = 0
    while clock() < deadline:
        if cancel.wait(interval):
            return None
        try:
            status, data = send(
                "POST", f"{_OAUTH}/token", {"Authorization": _basic(), "Content-Type": _FORM}, body
            )
        except EpicError:
            failures += 1
            if failures >= _NETWORK_RETRIES:
                raise
            continue
        failures = 0
        if status == 200 and isinstance(data, dict) and "access_token" in data:
            return data
        code = _error_code(data)
        if code == _SLOW_DOWN:
            interval += 5
        elif code != _PENDING:
            raise _refused("confirmation", status, data)
    raise EpicError("délai dépassé", "code d'appareil expiré")


def end_session(token: dict[str, Any], send: Transport = _send) -> None:
    """Ferme la session ouverte pour la lecture ; un échec ne fait que se journaliser."""
    access = token.get("access_token", "")
    try:
        status, _ = send(
            "DELETE",
            f"{_OAUTH}/sessions/kill/{access}",
            {"Authorization": f"bearer {access}"},
            None,
        )
    except EpicError as exc:
        log.warning("compte Epic : session non fermée (%s)", exc)
        return
    if status not in (200, 204):
        log.warning("compte Epic : session non fermée (HTTP %s)", status)


def _progress(attributes: dict[str, Any]) -> int:
    """Compteur de l'étape : la valeur de son objectif (``completion_…``)."""
    values = [v for k, v in attributes.items() if k.startswith("completion_")]
    numbers = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    return max(numbers, default=0)


def _when(attributes: dict[str, Any], key: str) -> datetime:
    try:
        return parse_utc(str(attributes[key]))
    except (KeyError, ConfigError) as exc:
        raise EpicError(_UNEXPECTED, f"date {key} : {exc}") from exc


def milestones_from_profile(profile: Any, now: datetime) -> list[Milestone]:
    """Paliers de la quête de coffres de la saison la plus récente du profil ``athena``."""
    try:
        items = profile["profileChanges"][0]["profile"]["items"]
    except (KeyError, IndexError, TypeError) as exc:
        raise EpicError(_UNEXPECTED, f"profil : {exc}") from exc
    seasons: dict[int, dict[int, dict[str, Any]]] = {}
    for item in items.values():
        found = _STAGE.match(str(item.get("templateId", "")).lower())
        if found:
            stages = seasons.setdefault(int(found[1]), {})
            stages[int(found[2])] = item.get("attributes") or {}
    if not seasons:
        raise EpicError("aucune quête de coffres sur ce compte")
    stages = seasons[max(seasons)]
    quests = [stages[index] for index in sorted(stages)]
    points = [Milestone(0, _when(quests[0], "creation_time"))]
    for index, attributes in enumerate(quests):
        count = _progress(attributes)
        if str(attributes.get("quest_state", "")).lower() not in _DONE:
            # étape en cours : son compteur maintenant (cumulé, comme ceux des étapes
            # franchies). Inchangé depuis le palier, il dit encore quelque chose : aucun
            # coffre pendant les parties jouées depuis.
            if count >= points[-1].count:
                points.append(Milestone(count, max(now, points[-1].at)))
            break
        following = quests[index + 1] if index + 1 < len(quests) else None
        at = (
            _when(following, "creation_time")
            if following is not None
            else _when(attributes, "last_state_change_time")
        )
        if count > points[-1].count and at >= points[-1].at:
            points.append(Milestone(count, at))
    return points


def read_milestones(
    token: dict[str, Any], send: Transport = _send, now: datetime | None = None
) -> list[Milestone]:
    """Lit le profil ``athena`` du compte connecté et en tire les paliers."""
    account = urllib.parse.quote(str(token.get("account_id", "")), safe="")
    headers = {
        "Authorization": f"bearer {token.get('access_token', '')}",
        "Content-Type": "application/json",
    }
    url = f"{_PROFILE}/{account}/client/QueryProfile?profileId=athena&rvn=-1"
    status, profile = send("POST", url, headers, b"{}")
    if status != 200:
        raise _refused("profil", status, profile)
    return milestones_from_profile(profile, now or datetime.now(timezone.utc))


def sign_in(
    open_page: Callable[[str], object],
    cancel: threading.Event,
    send: Transport = _send,
    on_reading: Callable[[], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> list[Milestone] | None:
    """Connexion, lecture des paliers, fermeture de la session ; None si annulé."""
    login = start_login(send)
    open_page(login.url)
    token = wait_for_token(login, cancel, send, clock)
    if token is None:
        return None
    if on_reading is not None:
        on_reading()
    try:
        return read_milestones(token, send)
    finally:
        end_session(token, send)


LinkPhase = Literal["idle", "waiting", "reading", "done", "failed"]


@dataclass(frozen=True, slots=True)
class LinkState:
    phase: LinkPhase = "idle"
    error: str = ""  # raison courte, pour le panneau


class EpicLink:
    """Connexion au compte Epic depuis le panneau, dans un thread à part.

    ``on_milestones`` reçoit les paliers lus (dans ce thread) ; le panneau lit
    :meth:`state` à chaque tour.
    """

    def __init__(
        self,
        on_milestones: Callable[[list[Milestone]], None],
        *,
        send: Transport = _send,
        open_page: Callable[[str], object] = webbrowser.open,
    ) -> None:
        self._on_milestones = on_milestones
        self._send = send
        self._open_page = open_page
        self._lock = threading.Lock()
        self._state = LinkState()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def state(self) -> LinkState:
        with self._lock:
            return self._state

    def _set(self, phase: LinkPhase, error: str = "") -> None:
        with self._lock:
            self._state = LinkState(phase, error)

    def connect(self) -> None:
        """Lance la connexion ; sans effet si une connexion est déjà en cours."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._cancel.clear()
            self._state = LinkState("waiting")
            self._thread = threading.Thread(target=self._run, name="epic", daemon=True)
            self._thread.start()

    def cancel(self) -> None:
        """Abandonne l'attente de la confirmation (la lecture, elle, va à son terme)."""
        self._cancel.set()

    def join(self, timeout: float) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self) -> None:
        log.info("compte Epic : connexion demandée")
        try:
            points = sign_in(
                self._open_page,
                self._cancel,
                self._send,
                on_reading=lambda: self._set("reading"),
            )
            if points is None:
                log.info("compte Epic : connexion annulée")
                self._set("idle")
                return
            self._on_milestones(points)
        except EpicError as exc:
            log.warning("compte Epic : %s", exc)
            self._set("failed", exc.reason)
            return
        except Exception:  # le panneau doit le dire, pas rester en attente
            log.exception("compte Epic : lecture impossible")
            self._set("failed", "erreur inattendue")
            return
        log.info("compte Epic : %d palier(s) lu(s), session fermée", len(points))
        self._set("done")
