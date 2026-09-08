from __future__ import annotations

"""Durable, idempotent custody for genuine M165 broker observations.

This store has no MetaTrader5 dependency and no broker-write surface.  It accepts
only fully-formed BrokerExecutionObservation rows, deduplicates by immutable
fingerprint, and refuses broker/symbol mixing inside one custody database.
"""

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .broker_calibration import (
    BrokerCalibrationPolicy,
    BrokerEconomicsCalibration,
    BrokerExecutionObservation,
    TradeSide,
    calibrate_broker_economics,
)


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
CREATE TABLE IF NOT EXISTS observations (
    fingerprint TEXT PRIMARY KEY,
    broker_profile_fingerprint TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    point_size REAL NOT NULL,
    bid REAL NOT NULL,
    ask REAL NOT NULL,
    requested_price REAL NOT NULL,
    fill_price REAL NOT NULL,
    volume_lots REAL NOT NULL,
    commission REAL NOT NULL,
    fee REAL NOT NULL,
    swap REAL NOT NULL,
    evidence_fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS idx_observations_broker_symbol_time
ON observations (broker_profile_fingerprint, symbol, observed_at, fingerprint);
"""


@dataclass(frozen=True, slots=True)
class ObservationImportResult:
    inserted: int
    duplicates: int
    total: int
    broker_profile_fingerprint: str
    symbol: str
    calibration: BrokerEconomicsCalibration

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    promotion_authority = False


class M165ObservationCustody:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "M165ObservationCustody":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _existing_identity(self) -> tuple[str, str] | None:
        row = self._db.execute(
            "SELECT broker_profile_fingerprint, symbol FROM observations ORDER BY rowid LIMIT 1"
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    def import_rows(
        self,
        rows: Iterable[BrokerExecutionObservation],
        *,
        policy: BrokerCalibrationPolicy = BrokerCalibrationPolicy(),
    ) -> ObservationImportResult:
        incoming = tuple(rows)
        if not incoming:
            raise ValueError("custody import requires observations")
        brokers = {row.broker_profile_fingerprint for row in incoming}
        symbols = {row.symbol for row in incoming}
        if len(brokers) != 1 or len(symbols) != 1:
            raise ValueError("custody import cannot mix broker profiles or symbols")
        broker = next(iter(brokers))
        symbol = next(iter(symbols))
        existing = self._existing_identity()
        if existing is not None and existing != (broker, symbol):
            raise ValueError("custody database is bound to a different broker profile or symbol")

        inserted = duplicates = 0
        with self._db:
            for row in incoming:
                cursor = self._db.execute(
                    """
                    INSERT OR IGNORE INTO observations (
                        fingerprint, broker_profile_fingerprint, symbol, side,
                        observed_at, point_size, bid, ask, requested_price,
                        fill_price, volume_lots, commission, fee, swap,
                        evidence_fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.fingerprint,
                        row.broker_profile_fingerprint,
                        row.symbol,
                        row.side.value,
                        row.observed_at.isoformat(),
                        row.point_size,
                        row.bid,
                        row.ask,
                        row.requested_price,
                        row.fill_price,
                        row.volume_lots,
                        row.commission,
                        row.fee,
                        row.swap,
                        row.evidence_fingerprint,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    duplicates += 1

        all_rows = self.load_all()
        calibration = calibrate_broker_economics(
            all_rows,
            broker_profile_fingerprint=broker,
            symbol=symbol,
            policy=policy,
        )
        return ObservationImportResult(inserted, duplicates, len(all_rows), broker, symbol, calibration)

    def load_all(self) -> tuple[BrokerExecutionObservation, ...]:
        rows = self._db.execute(
            """
            SELECT broker_profile_fingerprint, symbol, side, observed_at,
                   point_size, bid, ask, requested_price, fill_price,
                   volume_lots, commission, fee, swap, evidence_fingerprint
            FROM observations
            ORDER BY observed_at, fingerprint
            """
        ).fetchall()
        return tuple(
            BrokerExecutionObservation(
                broker_profile_fingerprint=str(row[0]),
                symbol=str(row[1]),
                side=TradeSide(str(row[2])),
                observed_at=datetime.fromisoformat(str(row[3])),
                point_size=float(row[4]),
                bid=float(row[5]),
                ask=float(row[6]),
                requested_price=float(row[7]),
                fill_price=float(row[8]),
                volume_lots=float(row[9]),
                commission=float(row[10]),
                fee=float(row[11]),
                swap=float(row[12]),
                evidence_fingerprint=None if row[13] is None else str(row[13]),
            )
            for row in rows
        )

    def summary_payload(self, *, policy: BrokerCalibrationPolicy = BrokerCalibrationPolicy()) -> dict[str, object]:
        rows = self.load_all()
        if not rows:
            return {
                "protocol": "dusty-m165-observation-custody-v1",
                "observation_count": 0,
                "authority": {"broker_write": False, "live_write": False, "retry": False, "promotion": False},
            }
        broker = rows[0].broker_profile_fingerprint
        symbol = rows[0].symbol
        calibration = calibrate_broker_economics(
            rows,
            broker_profile_fingerprint=broker,
            symbol=symbol,
            policy=policy,
        )
        return {
            "protocol": "dusty-m165-observation-custody-v1",
            "broker_profile_fingerprint": broker,
            "symbol": symbol,
            "observation_count": len(rows),
            "distinct_days": len({row.observed_at.date().isoformat() for row in rows}),
            "sides": sorted({row.side.value for row in rows}),
            "observation_fingerprints": [row.fingerprint for row in rows],
            "calibration": calibration.payload,
            "authority": {"broker_write": False, "live_write": False, "retry": False, "promotion": False},
        }
