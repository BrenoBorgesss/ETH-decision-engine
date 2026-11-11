"""Diário manual de operações (contingência quando não há chave da Binance).

Só vale com `conta.modo: diario_manual` no config. O resultado calculado a partir dele
aparece sempre como "não verificado", porque o agente não tem como conferir.
"""

from __future__ import annotations

import sqlite3

from cryptoagent.account.ledger import LedgerTrade
from cryptoagent.timeutils import now_ms


def add_manual_trade(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    fee_quote: float,
    time_ms: int | None = None,
    note: str = "",
) -> int:
    if side not in ("buy", "sell"):
        raise ValueError("lado deve ser compra ou venda")
    LedgerTrade("check", time_ms or 0, side, qty, price, fee_quote)  # type: ignore[arg-type]  # valida os números
    cur = conn.execute(
        "INSERT INTO manual_trades (symbol, time_ms, side, qty, price, fee_quote, note, created_ms) VALUES (?,?,?,?,?,?,?,?)",
        (symbol, time_ms if time_ms is not None else now_ms(), side, qty, price, fee_quote, note, now_ms()),
    )
    conn.commit()
    return int(cur.lastrowid)


def remove_manual_trade(conn: sqlite3.Connection, trade_id: int) -> bool:
    cur = conn.execute("DELETE FROM manual_trades WHERE id = ?", (trade_id,))
    conn.commit()
    return cur.rowcount > 0


def list_manual_trades(conn: sqlite3.Connection, symbol: str, since_ms: int = 0) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM manual_trades WHERE symbol = ? AND time_ms >= ? ORDER BY time_ms, id", (symbol, since_ms)).fetchall()


def manual_ledger_trades(conn: sqlite3.Connection, symbol: str) -> list[LedgerTrade]:
    return [
        LedgerTrade(f"m{r['id']}", r["time_ms"], r["side"], r["qty"], r["price"], r["fee_quote"], source="manual")
        for r in list_manual_trades(conn, symbol)
    ]
