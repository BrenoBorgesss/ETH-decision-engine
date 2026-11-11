"""Carteira virtual de uma variante da estratégia (patrimônio, posição, perda do dia).

Usa as MESMAS regras de execução do backtest (backtest/execution.py).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from cryptoagent.backtest.execution import buy_fill, close_pnl, oco_check, sell_fill
from cryptoagent.engine.decision import Decision
from cryptoagent.engine.live import PaperView
from cryptoagent.timeutils import local_trading_day, now_ms

LIMIT_TEXT = "PERDA MÁXIMA DO DIA ATINGIDA (carteira virtual)"


@dataclass
class PaperCosts:
    """Custos da carteira virtual: taxa por lado e slippage de mercado e de stop."""
    fee: float  # fração por lado
    slip_market_pct: float
    slip_stop_pct: float


class PaperBook:
    """Carteira virtual de uma variante, guardada no SQLite (tabelas paper_state e paper_trades). Nunca lê nem usa o saldo real."""
    def __init__(self, conn: sqlite3.Connection, variant: str, symbol: str, *, capital: float, loss_pct: float, divisor: float,
                 costs: PaperCosts, tz: str, day_start: str):
        self.conn, self.variant, self.symbol = conn, variant, symbol
        self.capital, self.loss_pct, self.divisor = capital, loss_pct, divisor
        self.costs, self.tz, self.day_start = costs, tz, day_start
        if self._state() is None:
            conn.execute(
                "INSERT INTO paper_state (variant, equity, day, day_start_equity, last_decision_close_ms, started_ms) VALUES (?,?,?,?,?,?)",
                (variant, capital, None, capital, 0, now_ms()),
            )
            conn.commit()

    # ------------------------------------------------------------------ estado

    def _state(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM paper_state WHERE variant = ?", (self.variant,)).fetchone()

    @property
    def equity(self) -> float:
        """Patrimônio virtual atual."""
        return float(self._state()["equity"])

    @property
    def last_decision_close_ms(self) -> int:
        """Fechamento do último candle de gatilho já analisado (0 se nenhum)."""
        return int(self._state()["last_decision_close_ms"] or 0)

    def mark_decided(self, candle_close_ms: int) -> None:
        """Registra que o candle de gatilho com este fechamento já foi analisado."""
        self.conn.execute("UPDATE paper_state SET last_decision_close_ms = ? WHERE variant = ?", (candle_close_ms, self.variant))
        self.conn.commit()

    def open_trade(self) -> sqlite3.Row | None:
        """Operação virtual aberta desta variante, ou None."""
        return self.conn.execute("SELECT * FROM paper_trades WHERE variant = ? AND exit_ms IS NULL ORDER BY id DESC LIMIT 1", (self.variant,)).fetchone()

    def _roll_day(self, t_ms: int) -> sqlite3.Row:
        st = self._state()
        day = local_trading_day(t_ms, self.tz, self.day_start).isoformat()
        if st["day"] != day:
            self.conn.execute("UPDATE paper_state SET day = ?, day_start_equity = equity WHERE variant = ?", (day, self.variant))
            self.conn.commit()
            st = self._state()
        return st

    def day_pnl(self, t_ms: int, mark_mid: float | None, half_spread: float) -> float:
        """Resultado do dia: operações fechadas hoje + posição aberta avaliada pelo preço de venda."""
        st = self._roll_day(t_ms)
        realized = self.conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM paper_trades WHERE variant = ? AND exit_ms IS NOT NULL AND exit_ms >= ?",
            (self.variant, self._day_start_ms(st["day"])),
        ).fetchone()[0]
        unreal = 0.0
        pos = self.open_trade()
        if pos is not None and mark_mid:
            unreal, _ = close_pnl(pos["qty"], pos["size"], pos["fee_in"], sell_fill(mark_mid, half_spread, self.costs.slip_market_pct), self.costs.fee)
        return float(realized) + unreal

    def _day_start_ms(self, day_iso: str) -> int:
        from datetime import date

        from cryptoagent.timeutils import trading_day_start_ms

        return trading_day_start_ms(date.fromisoformat(day_iso), self.tz, self.day_start)

    def view(self, t_ms: int, mark_mid: float | None, half_spread: float) -> PaperView:
        """Foto da carteira virtual para o motor de decisão: capital, risco por operação, limite do dia e posição aberta."""
        st = self._roll_day(t_ms)
        max_loss = float(st["day_start_equity"]) * self.loss_pct / 100
        limit = LIMIT_TEXT if self.day_pnl(t_ms, mark_mid, half_spread) <= -max_loss else None
        pos = self.open_trade()
        return PaperView(
            trade_symbol=self.symbol,
            fee=self.costs.fee,
            capital=self.equity,
            risk_per_trade=max_loss / self.divisor,
            daily_limit_reason=limit,
            qty=pos["qty"] if pos else None,
            entry_price=pos["entry_price"] if pos else None,
            opened_ms=pos["entry_ms"] if pos else None,
            stop=pos["stop"] if pos else None,
            target1=pos["target1"] if pos else None,
            target2=pos["target2"] if pos else None,
            source=f"paper #{pos['id']}" if pos else "paper",
        )

    # ---------------------------------------------------------------- operações

    def open(self, d: Decision, *, ratio: float, bid: float, ask: float, t_ms: int, signal_id: int | None,
             bypassed: dict[str, str] | None = None) -> int | None:
        """Abre uma compra virtual a mercado com os níveis da decisão. Devolve o id, ou None se não há níveis ou tamanho, ou se já existe posição aberta."""
        if d.levels is None or not d.levels.size_brl or self.open_trade() is not None:
            return None
        mid, half = (bid + ask) / 2, (ask - bid) / (ask + bid)
        fill = buy_fill(mid, half, self.costs.slip_market_pct)
        size = min(d.levels.size_brl, self.equity)
        lv = d.levels
        cur = self.conn.execute(
            """INSERT INTO paper_trades (variant, symbol, signal_id, entry_ms, entry_price, qty, size, fee_in, stop, target1, target2,
                   confidence, regime, equity_at_entry, checked_until_ms, bypassed_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.variant, self.symbol, signal_id, t_ms, fill, size / fill, size, size * self.costs.fee, lv.stop * ratio, lv.target1 * ratio,
             lv.target2 * ratio, d.confidence, d.regime, self.equity, t_ms, json.dumps(bypassed, ensure_ascii=False) if bypassed is not None else None),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def close(self, *, fill: float, t_ms: int, reason: str) -> float:
        """Fecha a posição virtual ao preço dado, grava o resultado e atualiza o patrimônio. Devolve o resultado líquido."""
        pos = self.open_trade()
        assert pos is not None
        pnl, fee_out = close_pnl(pos["qty"], pos["size"], pos["fee_in"], fill, self.costs.fee)
        self.conn.execute(
            """UPDATE paper_trades SET exit_ms = ?, exit_price = ?, exit_reason = ?, pnl = ?, pnl_pct = ?, fees = ?, ret_equity = ?
               WHERE id = ?""",
            (t_ms, fill, reason, pnl, pnl / pos["size"] * 100, pos["fee_in"] + fee_out, pnl / pos["equity_at_entry"], pos["id"]),
        )
        self.conn.execute("UPDATE paper_state SET equity = equity + ? WHERE variant = ?", (pnl, self.variant))
        self.conn.commit()
        return pnl

    def close_market(self, *, bid: float, ask: float, t_ms: int, reason: str) -> float:
        """Fecha a posição virtual a mercado, com meio spread e slippage. Devolve o resultado líquido."""
        mid, half = (bid + ask) / 2, (ask - bid) / (ask + bid)
        return self.close(fill=sell_fill(mid, half, self.costs.slip_market_pct), t_ms=t_ms, reason=reason)

    def check_oco(self, bars_1m: pd.DataFrame, half_spread: float) -> str | None:
        """Confere a OCO virtual nos candles de 1m FECHADOS desde a última verificação. Devolve o motivo se fechou."""
        pos = self.open_trade()
        if pos is None or bars_1m.empty:
            return None
        bars = bars_1m[bars_1m["open_time"] >= pos["checked_until_ms"] - 60_000].reset_index(drop=True)
        bars = bars[bars["close_time"] >= pos["checked_until_ms"]].reset_index(drop=True)
        if bars.empty:
            return None
        hit = oco_check(
            bars["open"].to_numpy(), bars["high"].to_numpy(), bars["low"].to_numpy(),
            stop=pos["stop"], target=pos["target1"], half_spread=half_spread, slip_stop_pct=self.costs.slip_stop_pct,
        )
        if hit is not None:
            self.close(fill=hit.fill, t_ms=int(bars["close_time"].iloc[hit.index]) + 1, reason=hit.reason)
            return hit.reason
        self.conn.execute("UPDATE paper_trades SET checked_until_ms = ? WHERE id = ?", (int(bars["close_time"].iloc[-1]) + 1, pos["id"]))
        self.conn.commit()
        return None

    def closed_trades(self) -> list[sqlite3.Row]:
        """Operações virtuais fechadas desta variante, em ordem de entrada."""
        return self.conn.execute("SELECT * FROM paper_trades WHERE variant = ? AND exit_ms IS NOT NULL ORDER BY entry_ms", (self.variant,)).fetchall()


def to_backtest_trades(rows: list[sqlite3.Row]):
    """Converte para o formato do backtest, para usar as mesmas métricas."""
    from cryptoagent.backtest.simulator import Trade

    return [
        Trade(r["entry_ms"], r["exit_ms"], r["entry_price"], r["exit_price"], r["qty"], r["size"], r["pnl"], r["pnl_pct"], r["ret_equity"],
              r["exit_reason"], r["confidence"] or 0.0, r["regime"] or "", r["stop"] or np.nan, r["target1"] or np.nan, r["fees"] or 0.0)
        for r in rows
    ]
