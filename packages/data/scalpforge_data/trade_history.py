from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


@dataclass(frozen=True)
class TradeHistoryManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    source_file: str
    source_sha256: str
    source_system: str
    source_role: str
    account_alias: str
    broker: str
    timezone_assumption: str
    row_count: int
    closed_trade_count: int
    first_entry_utc: str | None
    last_exit_utc: str | None
    duplicate_ticket_count: int
    partitions: list[str]
    contains_credentials: bool = False
    research_only: bool = True
    external_non_executable: bool = True


@dataclass(frozen=True)
class TradeHistoryImportResult:
    manifest: TradeHistoryManifest
    manifest_path: str


class TradeHistoryCsvNormalizer:
    """Normalize authorized MT4/MT5-style trade exports for behavioral research."""

    ALIASES = {
        "ticket": ("ticket", "order", "position", "deal", "trade_id"),
        "symbol": ("symbol", "item", "instrument"),
        "side": ("side", "type", "direction", "action"),
        "volume": ("volume", "lots", "lot", "size"),
        "opened_at": ("opened_at", "open_time", "time", "entry_time"),
        "entry_price": ("entry_price", "open_price", "price", "price_open"),
        "stop_loss": ("stop_loss", "sl", "s_l"),
        "take_profit": ("take_profit", "tp", "t_p"),
        "closed_at": ("closed_at", "close_time", "exit_time", "time_close"),
        "exit_price": ("exit_price", "close_price", "price_close"),
        "profit": ("profit", "p_l", "pnl", "net_profit"),
        "commission": ("commission", "comm"),
        "swap": ("swap",),
        "magic": ("magic", "magic_number"),
        "comment": ("comment", "comments"),
    }
    SOURCE_ROLES = {"provider_master", "copied_account", "manual_account", "unknown"}
    ORIGINS = {"provider", "copier", "manual", "ea_local", "unknown"}

    def normalize(
        self,
        source_path: Path,
        output_root: Path,
        *,
        source_system: str,
        source_role: str,
        account_alias: str,
        broker: str,
        source_timezone: str,
        entry_origin: str = "unknown",
        exit_origin: str = "unknown",
    ) -> TradeHistoryImportResult:
        if source_role not in self.SOURCE_ROLES:
            raise ValueError(f"unsupported source role: {source_role}")
        if entry_origin not in self.ORIGINS or exit_origin not in self.ORIGINS:
            raise ValueError("unsupported entry or exit origin")
        if not account_alias.strip() or not source_system.strip():
            raise ValueError("source system and anonymized account alias are required")
        if re.fullmatch(r"\d{6,}", account_alias.strip()):
            raise ValueError("account alias appears to be a real account number")
        timezone = _timezone(source_timezone)
        source_hash = _sha256(source_path)
        identity = json.dumps(
            {
                "schema_version": 1,
                "source_sha256": source_hash,
                "source_system": source_system,
                "source_role": source_role,
                "account_alias": account_alias,
                "broker": broker,
                "timezone": source_timezone,
                "entry_origin": entry_origin,
                "exit_origin": exit_origin,
            },
            sort_keys=True,
        ).encode()
        dataset_id = "trade-observations-" + hashlib.sha256(identity).hexdigest()[:16]
        root = output_root / dataset_id
        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            manifest = TradeHistoryManifest(
                **json.loads(manifest_path.read_text(encoding="utf-8"))
            )
            return TradeHistoryImportResult(manifest, str(manifest_path))

        rows, duplicate_count = self._read(
            source_path,
            timezone,
            source_system,
            source_role,
            account_alias,
            broker,
            entry_origin,
            exit_origin,
        )
        root.mkdir(parents=True, exist_ok=False)
        data_path = root / "trades.parquet"
        pq.write_table(pa.Table.from_pylist(rows), data_path, compression="zstd")
        entries = [row["opened_at"] for row in rows]
        exits = [row["closed_at"] for row in rows if row["closed_at"] is not None]
        manifest = TradeHistoryManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            source_file=source_path.name,
            source_sha256=source_hash,
            source_system=source_system,
            source_role=source_role,
            account_alias=account_alias,
            broker=broker,
            timezone_assumption=source_timezone,
            row_count=len(rows),
            closed_trade_count=len(exits),
            first_entry_utc=min(entries).isoformat() if entries else None,
            last_exit_utc=max(exits).isoformat() if exits else None,
            duplicate_ticket_count=duplicate_count,
            partitions=[str(data_path)],
        )
        manifest_path.write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")
        return TradeHistoryImportResult(manifest, str(manifest_path))

    def _read(
        self,
        path: Path,
        timezone: tzinfo,
        source_system: str,
        source_role: str,
        account_alias: str,
        broker: str,
        entry_origin: str,
        exit_origin: str,
    ) -> tuple[list[dict[str, object]], int]:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None:
                raise ValueError("trade history has no header")
            columns = {_key(name): name for name in reader.fieldnames}
            resolved = {
                field: next((columns[name] for name in aliases if name in columns), None)
                for field, aliases in self.ALIASES.items()
            }
            required = ("ticket", "symbol", "side", "volume", "opened_at", "entry_price")
            missing = [field for field in required if resolved[field] is None]
            if missing:
                raise ValueError(f"trade history missing columns: {', '.join(missing)}")
            rows: list[dict[str, object]] = []
            tickets: set[str] = set()
            duplicates = 0
            for row_number, raw in enumerate(reader, start=2):
                ticket = _text(raw, resolved["ticket"])
                if not ticket:
                    raise ValueError(f"row {row_number}: ticket is empty")
                if ticket in tickets:
                    duplicates += 1
                    continue
                tickets.add(ticket)
                side = _side(_text(raw, resolved["side"]), row_number)
                opened = _timestamp(_text(raw, resolved["opened_at"]), timezone, row_number)
                closed_text = _text(raw, resolved["closed_at"])
                closed = _timestamp(closed_text, timezone, row_number) if closed_text else None
                volume = _number(raw, resolved["volume"], row_number, required=True)
                entry = _number(raw, resolved["entry_price"], row_number, required=True)
                exit_price = _number(raw, resolved["exit_price"], row_number)
                if volume <= 0 or entry <= 0:
                    raise ValueError(f"row {row_number}: volume and entry price must be positive")
                if closed is not None and closed < opened:
                    raise ValueError(f"row {row_number}: close precedes open")
                if closed is not None and (exit_price is None or exit_price <= 0):
                    raise ValueError(f"row {row_number}: closed trade requires exit price")
                profit = _number(raw, resolved["profit"], row_number) or 0.0
                commission = _number(raw, resolved["commission"], row_number) or 0.0
                swap = _number(raw, resolved["swap"], row_number) or 0.0
                rows.append(
                    {
                        "ticket": ticket,
                        "account_alias": account_alias,
                        "source_system": source_system,
                        "source_role": source_role,
                        "broker": broker,
                        "symbol": _text(raw, resolved["symbol"]).upper(),
                        "side": side,
                        "volume_lots": volume,
                        "opened_at": opened,
                        "entry_price": entry,
                        "recorded_stop_loss": _number(raw, resolved["stop_loss"], row_number),
                        "recorded_take_profit": _number(raw, resolved["take_profit"], row_number),
                        "closed_at": closed,
                        "exit_price": exit_price,
                        "reported_profit": profit,
                        "commission": commission,
                        "swap": swap,
                        "reported_net_profit": profit + commission + swap,
                        "duration_seconds": (closed - opened).total_seconds() if closed else None,
                        "magic_number": _text(raw, resolved["magic"]),
                        "comment_sha256": _optional_hash(_text(raw, resolved["comment"])),
                        "entry_origin": entry_origin,
                        "exit_origin": exit_origin,
                        "entry_inferred": entry_origin == "unknown",
                        "exit_inferred": exit_origin == "unknown",
                    }
                )
        if not rows:
            raise ValueError("trade history contains no usable trades")
        rows.sort(key=lambda item: (item["opened_at"], item["ticket"]))
        return rows, duplicates


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _text(row: dict[str, str], column: str | None) -> str:
    return row.get(column, "").strip() if column else ""


def _number(
    row: dict[str, str], column: str | None, row_number: int, required: bool = False
) -> float | None:
    value = _text(row, column).replace(",", "")
    if not value:
        if required:
            raise ValueError(f"row {row_number}: required numeric value is empty")
        return None
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"row {row_number}: invalid number {value!r}") from error


def _side(value: str, row_number: int) -> str:
    normalized = value.strip().lower()
    if normalized in {"buy", "long", "0"}:
        return "buy"
    if normalized in {"sell", "short", "1"}:
        return "sell"
    raise ValueError(f"row {row_number}: unsupported trade side {value!r}")


def _timestamp(value: str, timezone: tzinfo, row_number: int) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M"):
            try:
                parsed = datetime.strptime(normalized, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"row {row_number}: invalid timestamp {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)


def _timezone(value: str) -> tzinfo:
    if value.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unknown timezone: {value}") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest() if value else ""
