"""Historique des sessions : une ligne JSON par session dans ``logs/sessions.jsonl``.

Les sessions en dry-run ne sont pas enregistrées : elles ne cliquent pas.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_COUNTERS = ("frames", "captures_failed", "actions", "stuck", "doubts_saved")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def append_session(path: Path, started_at: float, stats: dict[str, Any]) -> None:
    entry = {"started_at": _iso(started_at), "ended_at": _iso(datetime.now().timestamp())}
    entry.update(stats)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_sessions(path: Path) -> list[dict[str, Any]]:
    """Sessions enregistrées ; une ligne illisible est ignorée avec un avertissement."""
    if not path.exists():
        return []
    sessions: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s:%d illisible, ignorée", path.name, number)
                continue
            if isinstance(entry, dict):
                sessions.append(entry)
    return sessions


def summarize(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Totaux sur toutes les sessions, plus la dernière telle quelle."""
    totals: dict[str, Any] = {"sessions": len(sessions)}
    for key in _COUNTERS:
        totals[key] = sum(int(s.get(key, 0) or 0) for s in sessions)
    uptime_s = sum(float(s.get("uptime_s", 0) or 0) for s in sessions)
    totals["uptime_h"] = round(uptime_s / 3600, 2)
    per_state: dict[str, int] = {}
    for session in sessions:
        for slug, count in (session.get("per_state") or {}).items():
            per_state[slug] = per_state.get(slug, 0) + int(count)
    totals["per_state"] = per_state
    totals["last"] = sessions[-1] if sessions else None
    return totals
