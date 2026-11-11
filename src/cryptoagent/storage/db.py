"""Banco SQLite local da coleta de custos (o resto do agente usa storage/agent_db.py)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

COST_SCHEMA = """
CREATE TABLE IF NOT EXISTS book_samples (
    ts_ms INTEGER NOT NULL,          -- horário UTC da amostra
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,            -- ws, ws+bookTicker, rest...
    bid REAL, ask REAL, bid_qty REAL, ask_qty REAL,
    spread_pct REAL,
    depth_bid_quote_5 REAL, depth_ask_quote_5 REAL,
    depth_bid_quote_20 REAL, depth_ask_quote_20 REAL,
    fx_usdtbrl REAL,                 -- cotação usada para converter os tamanhos em R$
    slippage_json TEXT,              -- slippage simulado por tamanho de ordem em R$
    book_age_ms INTEGER,
    ticker_age_ms INTEGER
);
CREATE INDEX IF NOT EXISTS ix_book_samples_symbol_ts ON book_samples(symbol, ts_ms);

CREATE TABLE IF NOT EXISTS book_snapshots (
    ts_ms INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    bids_json TEXT NOT NULL,
    asks_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_book_snapshots_symbol_ts ON book_snapshots(symbol, ts_ms);

CREATE TABLE IF NOT EXISTS collector_events (
    ts_ms INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- start, stop, rest_fallback, error
    detail TEXT
);
"""

_SAMPLE_COLUMNS = [
    "ts_ms",
    "symbol",
    "source",
    "bid",
    "ask",
    "bid_qty",
    "ask_qty",
    "spread_pct",
    "depth_bid_quote_5",
    "depth_ask_quote_5",
    "depth_bid_quote_20",
    "depth_ask_quote_20",
    "fx_usdtbrl",
    "slippage_json",
    "book_age_ms",
    "ticker_age_ms",
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(COST_SCHEMA)
    return conn


def insert_samples(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    values = [
        tuple(json.dumps(r[c]) if c == "slippage_json" else r.get(c) for c in _SAMPLE_COLUMNS) for r in rows
    ]
    if values:
        placeholders = ",".join("?" * len(_SAMPLE_COLUMNS))
        conn.executemany(f"INSERT INTO book_samples ({','.join(_SAMPLE_COLUMNS)}) VALUES ({placeholders})", values)
        conn.commit()
    return len(values)


def insert_snapshot(conn: sqlite3.Connection, ts_ms: int, symbol: str, source: str, bids: list, asks: list) -> None:
    conn.execute(
        "INSERT INTO book_snapshots (ts_ms, symbol, source, bids_json, asks_json) VALUES (?,?,?,?,?)",
        (ts_ms, symbol, source, json.dumps(bids), json.dumps(asks)),
    )
    conn.commit()


def log_event(conn: sqlite3.Connection, ts_ms: int, kind: str, detail: str = "") -> None:
    conn.execute("INSERT INTO collector_events (ts_ms, kind, detail) VALUES (?,?,?)", (ts_ms, kind, detail[:500]))
    conn.commit()
