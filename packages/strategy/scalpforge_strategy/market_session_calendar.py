from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class SessionWindow:
    weekday: int
    start: time
    end: time


@dataclass(frozen=True)
class SessionCalendar:
    calendar_id: str
    instrument: str
    venue: str
    timezone: tzinfo
    effective_from: date
    effective_to_exclusive: date
    weekly_open_windows: tuple[SessionWindow, ...]
    closed_intervals: tuple[tuple[datetime, datetime], ...]
    source_url: str
    source_retrieved_at: str
    source_sha256: str
    config_sha256: str

    def classify(self, start_utc: datetime, end_utc: datetime) -> str:
        if end_utc <= start_utc:
            raise ValueError("interval end must be after start")
        if not self._covers(start_utc) or not self._covers(end_utc - timedelta(microseconds=1)):
            return "calendar_out_of_effective_range"
        if self._overlaps_explicit_closure(start_utc, end_utc):
            return "scheduled_closed"
        if self._fully_open(start_utc, end_utc):
            return "expected_open_quote_silence"
        return "scheduled_closed"

    def _covers(self, value: datetime) -> bool:
        local_date = value.astimezone(self.timezone).date()
        return self.effective_from <= local_date < self.effective_to_exclusive

    def _overlaps_explicit_closure(self, start: datetime, end: datetime) -> bool:
        return any(
            max(start, closed_start) < min(end, closed_end)
            for closed_start, closed_end in self.closed_intervals
        )

    def _fully_open(self, start: datetime, end: datetime) -> bool:
        overlaps = self._open_overlaps(start, end)
        cursor = start
        for open_start, open_end in sorted(overlaps):
            if open_start > cursor:
                return False
            cursor = max(cursor, open_end)
            if cursor >= end:
                return True
        return False

    def _open_overlaps(self, start: datetime, end: datetime):
        overlaps = []
        local_start = start.astimezone(self.timezone)
        local_end = end.astimezone(self.timezone)
        day = local_start.date() - timedelta(days=1)
        final_day = local_end.date()
        while day <= final_day:
            for window in self.weekly_open_windows:
                if window.weekday != day.weekday():
                    continue
                window_start = datetime.combine(day, window.start, self.timezone)
                end_day = day + timedelta(days=1) if window.end <= window.start else day
                window_end = datetime.combine(end_day, window.end, self.timezone)
                open_start, open_end = window_start.astimezone(UTC), window_end.astimezone(UTC)
                if max(start, open_start) < min(end, open_end):
                    overlaps.append((max(start, open_start), min(end, open_end)))
            day += timedelta(days=1)
        return overlaps


def load_session_calendar(path: Path) -> SessionCalendar:
    raw = path.read_bytes()
    payload = json.loads(raw)
    required = {
        "calendar_id", "instrument", "venue", "timezone", "effective_from",
        "effective_to_exclusive", "weekly_open_windows", "source_url",
        "source_retrieved_at", "source_sha256", "verified",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"calendar missing required fields: {sorted(missing)}")
    if payload["verified"] is not True:
        raise ValueError("session calendar must be explicitly verified against its source")
    if not str(payload["source_url"]).startswith("https://"):
        raise ValueError("calendar source_url must use HTTPS")
    if len(str(payload["source_sha256"])) != 64:
        raise ValueError("calendar source_sha256 must be a full SHA-256 digest")
    timezone_name = str(payload["timezone"])
    if timezone_name in {"UTC", "Etc/UTC"}:
        timezone = UTC
    else:
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown calendar timezone: {timezone_name}") from exc
    windows = tuple(
        SessionWindow(int(item["weekday"]), _clock(item["start"]), _clock(item["end"]))
        for item in payload["weekly_open_windows"]
    )
    if not windows or any(window.weekday not in range(7) for window in windows):
        raise ValueError("calendar requires valid weekly open windows")
    closures = tuple(
        (_instant(item["start"], timezone), _instant(item["end"], timezone))
        for item in payload.get("closed_intervals", [])
    )
    if any(end <= start for start, end in closures):
        raise ValueError("closed calendar interval has invalid bounds")
    return SessionCalendar(
        str(payload["calendar_id"]), str(payload["instrument"]), str(payload["venue"]),
        timezone, date.fromisoformat(payload["effective_from"]),
        date.fromisoformat(payload["effective_to_exclusive"]), windows, closures,
        str(payload["source_url"]), str(payload["source_retrieved_at"]),
        str(payload["source_sha256"]), hashlib.sha256(raw).hexdigest(),
    )


def load_jforex_offline_manifest(path: Path) -> SessionCalendar:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("source") != "IDataService.getOfflineTimeDomains(from,to,instrument)":
        raise ValueError("manifest is not an instrument-specific JForex offline export")
    if payload.get("source_scope") != "instrument_specific_offline_domains":
        raise ValueError("JForex manifest source scope is not instrument specific")
    if payload.get("read_only") is not True or payload.get("external_non_executable") is not True:
        raise ValueError("JForex offline artifact lacks required safety flags")
    root = path.resolve().parent
    csv_path = (root / str(payload["csv"])).resolve()
    if not csv_path.is_relative_to(root):
        raise ValueError("offline CSV escapes its manifest directory")
    csv_bytes = csv_path.read_bytes()
    digest = hashlib.sha256(csv_bytes).hexdigest()
    if digest != payload.get("sha256"):
        raise ValueError("JForex offline CSV checksum mismatch")
    closures = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("instrument") != payload.get("instrument"):
                raise ValueError("offline CSV instrument differs from manifest")
            if row.get("scope") != "jforex_instrument_offline_domain":
                raise ValueError("offline CSV contains an unsupported scope")
            closures.append((_instant(row["start_utc"], UTC), _instant(row["end_utc"], UTC)))
    if len(closures) != int(payload["interval_count"]):
        raise ValueError("offline CSV row count differs from manifest")
    start = _instant(str(payload["start_utc"]), UTC)
    end = _instant(str(payload["end_utc_exclusive"]), UTC)
    return SessionCalendar(
        "jforex-offline-" + digest[:16], str(payload["instrument"]),
        str(payload["venue"]), UTC, start.date(), end.date() + timedelta(days=1),
        tuple(SessionWindow(day, time(0), time(0)) for day in range(7)),
        tuple(closures),
        "https://www.dukascopy.com/client/javadoc3/com/dukascopy/api/IDataService.html",
        "captured_by_authenticated_jforex_strategy", digest,
        hashlib.sha256(raw + csv_bytes).hexdigest(),
    )


def _clock(value: str) -> time:
    return time.fromisoformat(value)


def _instant(value: str, timezone: tzinfo) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)
