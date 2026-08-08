from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class DukascopyMergeManifest:
    schema_version: int
    created_at: str
    ask_source: str
    ask_sha256: str
    bid_source: str
    bid_sha256: str
    output: str
    output_sha256: str
    row_count: int
    first_timestamp: str
    last_timestamp: str
    timestamp_column: str
    price_column: str


def merge_side_exports(ask_path: Path, bid_path: Path, output_path: Path) -> DukascopyMergeManifest:
    """Merge matching Dukascopy ASK/BID quote exports, refusing ambiguous alignment."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".partial")
    rows = 0
    first = last = ""
    try:
        with (
            ask_path.open("r", encoding="utf-8-sig", newline="") as ask_stream,
            bid_path.open("r", encoding="utf-8-sig", newline="") as bid_stream,
            temporary.open("w", encoding="utf-8", newline="") as output_stream,
        ):
            ask_reader = csv.DictReader(ask_stream)
            bid_reader = csv.DictReader(bid_stream)
            timestamp_column = _shared_timestamp_column(ask_reader, bid_reader)
            price_column = _price_column(ask_reader, bid_reader)
            writer = csv.DictWriter(
                output_stream,
                fieldnames=["occurred_at", "bid", "ask", "bid_volume", "ask_volume"],
            )
            writer.writeheader()
            while True:
                ask = next(ask_reader, None)
                bid = next(bid_reader, None)
                if ask is None and bid is None:
                    break
                if ask is None or bid is None:
                    raise ValueError("ASK and BID exports have different row counts")
                timestamp = ask[timestamp_column]
                if timestamp != bid[timestamp_column]:
                    raise ValueError(f"row {rows + 2}: ASK/BID timestamps do not align")
                _require_flat_quote(ask, rows + 2)
                _require_flat_quote(bid, rows + 2)
                ask_price = float(ask[price_column])
                bid_price = float(bid[price_column])
                if ask_price < bid_price:
                    raise ValueError(f"row {rows + 2}: ask is below bid")
                writer.writerow(
                    {
                        "occurred_at": timestamp,
                        "bid": bid_price,
                        "ask": ask_price,
                        "bid_volume": bid.get("Volume", ""),
                        "ask_volume": ask.get("Volume", ""),
                    }
                )
                first = first or timestamp
                last = timestamp
                rows += 1
        if rows == 0:
            raise ValueError("Dukascopy exports contain no rows")
        temporary.replace(output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    manifest = DukascopyMergeManifest(
        schema_version=1,
        created_at=datetime.now(UTC).isoformat(),
        ask_source=ask_path.name,
        ask_sha256=_sha256(ask_path),
        bid_source=bid_path.name,
        bid_sha256=_sha256(bid_path),
        output=output_path.name,
        output_sha256=_sha256(output_path),
        row_count=rows,
        first_timestamp=first,
        last_timestamp=last,
        timestamp_column=timestamp_column,
        price_column=price_column,
    )
    output_path.with_suffix(output_path.suffix + ".merge.json").write_text(
        json.dumps(asdict(manifest), indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _shared_timestamp_column(
    ask_reader: csv.DictReader[str], bid_reader: csv.DictReader[str]
) -> str:
    ask_fields = ask_reader.fieldnames or []
    bid_fields = bid_reader.fieldnames or []
    candidates = [field for field in ask_fields if field in bid_fields and field not in _FIELDS]
    if len(candidates) != 1:
        raise ValueError("cannot identify one shared Dukascopy timestamp column")
    return candidates[0]


def _price_column(ask_reader: csv.DictReader[str], bid_reader: csv.DictReader[str]) -> str:
    for candidate in ("Close", "OPEN", "Open", "close"):
        ask_has_column = candidate in (ask_reader.fieldnames or [])
        bid_has_column = candidate in (bid_reader.fieldnames or [])
        if ask_has_column and bid_has_column:
            return candidate
    raise ValueError("cannot identify Dukascopy quote price column")


def _require_flat_quote(row: dict[str, str], row_number: int) -> None:
    available = [row[name] for name in ("Open", "High", "Low", "Close") if name in row]
    if len(available) == 4 and len(set(available)) != 1:
        raise ValueError(f"row {row_number}: export contains aggregated OHLC bars, not ticks")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_FIELDS = {"Open", "High", "Low", "Close", "Volume"}
