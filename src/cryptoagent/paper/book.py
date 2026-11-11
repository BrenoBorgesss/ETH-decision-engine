"""Carteira virtual de uma variante da estratégia (patrimônio em R$, posição, perda do dia).

- Usa as MESMAS regras de execução do backtest (`backtest/execution.py`): meio spread + slippage na
  entrada e na saída a mercado, taxa nos dois lados, OCO conferida candle a candle no 1m com o pior
  caso quando stop e alvo caem no mesmo candle.
- Ordem virtual que a corretora recusaria (abaixo do mínimo de quantidade ou de valor) também é
  recusada, e o fato fica registrado (`paper/filters.py`).
- A OCO só confere candles de 1m que começaram DEPOIS da execução da compra: o pedaço do minuto
  anterior à entrada não pode disparar stop nem alvo (correção do problema B1 do diagnóstico).
- Nunca lê nem usa o saldo real: tudo vive no banco da simulação (`storage/sim_db.py`).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from cryptoagent.backtest.execution import buy_fill, close_pnl, oco_check, sell_fill
from cryptoagent.engine.decision import Decision
from cryptoagent.engine.live import PaperView
from cryptoagent.paper.filters import SymbolFilters
from cryptoagent.timeutils import local_trading_day, trading_day_start_ms

if TYPE_CHECKING:  # só para as anotações (evita importação circular com o backtest)
    from cryptoagent.backtest.simulator import Trade

LIMIT_TEXT = "PERDA MÁXIMA DO DIA ATINGIDA (carteira virtual)"
MIN_MS = 60_000


@dataclass
class PaperCosts:
    """Custos da carteira virtual: taxa por lado e slippage de mercado e de stop."""

    fee: float  # fração por lado
    slip_market_pct: float
    slip_stop_pct: float


@dataclass(frozen=True)
class EntryRecord:
    """Tudo que se sabe no momento de uma compra virtual, para ficar registrado junto com ela."""

    decision_ms: int
    decision_price: float  # preço médio (bid/ask) quando o motor decidiu
    signal_id: int | None
    reason: str
    features: dict | None = None
    scenario: dict | None = None
    bypassed: dict[str, str] | None = None


def first_full_minute(t_ms: int) -> int:
    """Início do primeiro candle de 1m que começa em `t_ms` ou depois (o primeiro inteiramente após a execução)."""
    return -(-t_ms // MIN_MS) * MIN_MS


class PaperBook:
    """Carteira virtual de uma variante, guardada no banco da simulação. Valores em R$."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        variant: str,
        symbol: str,
        *,
        capital: float,
        max_loss_brl: float,
        divisor: float,
        costs: PaperCosts,
        tz: str,
        day_start: str,
        now_ms: int,
        filters: SymbolFilters | None = None,
    ) -> None:
        self.conn, self.variant, self.symbol = conn, variant, symbol
        self.divisor, self.costs, self.tz, self.day_start, self.filters = divisor, costs, tz, day_start, filters
        st = self._state()
        if st is None:
            conn.execute(
                """INSERT INTO paper_state (variant, equity, initial_capital, max_loss_day, day, day_start_equity, last_decision_close_ms, started_ms)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (variant, capital, capital, max_loss_brl, None, capital, 0, now_ms),
            )
            conn.commit()
        else:
            # O capital inicial de uma carteira que já existe não muda (para mudar: paper resetar).
            # A perda máxima do dia pode ser ajustada a qualquer momento.
            conn.execute("UPDATE paper_state SET max_loss_day = ? WHERE variant = ?", (max_loss_brl, variant))
            conn.commit()
        self.requested_capital = capital

    # ------------------------------------------------------------------ estado

    def _state(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM paper_state WHERE variant = ?", (self.variant,)).fetchone()

    @property
    def equity(self) -> float:
        """Patrimônio virtual atual (R$)."""
        return float(self._state()["equity"])

    @property
    def initial_capital(self) -> float:
        """Capital virtual com que a carteira começou (R$)."""
        return float(self._state()["initial_capital"])

    @property
    def max_loss_day(self) -> float:
        """Perda máxima virtual por dia (R$)."""
        return float(self._state()["max_loss_day"])

    @property
    def started_ms(self) -> int:
        """Quando a carteira virtual começou."""
        return int(self._state()["started_ms"])

    @property
    def last_decision_close_ms(self) -> int:
        """Fechamento do último candle de gatilho já analisado (0 se nenhum)."""
        return int(self._state()["last_decision_close_ms"] or 0)

    def mark_decided(self, candle_close_ms: int) -> None:
        """Registra que o candle de gatilho com este fechamento já foi analisado."""
        self.conn.execute(
            "UPDATE paper_state SET last_decision_close_ms = ? WHERE variant = ?", (candle_close_ms, self.variant)
        )
        self.conn.commit()

    def open_trade(self) -> sqlite3.Row | None:
        """Operação virtual aberta desta variante, ou None."""
        return self.conn.execute(
            "SELECT * FROM paper_trades WHERE variant = ? AND exit_ms IS NULL ORDER BY id DESC LIMIT 1", (self.variant,)
        ).fetchone()

    def log_event(self, t_ms: int, kind: str, detail: str, signal_id: int | None = None) -> None:
        """Registra um fato que não virou operação (ex.: ordem recusada)."""
        self.conn.execute(
            "INSERT INTO paper_events (time_ms, variant, kind, detail, signal_id) VALUES (?,?,?,?,?)",
            (t_ms, self.variant, kind, detail, signal_id),
        )
        self.conn.commit()

    def _roll_day(self, t_ms: int) -> sqlite3.Row:
        st = self._state()
        day = local_trading_day(t_ms, self.tz, self.day_start).isoformat()
        if st["day"] != day:
            self.conn.execute(
                "UPDATE paper_state SET day = ?, day_start_equity = equity WHERE variant = ?", (day, self.variant)
            )
            self.conn.commit()
            st = self._state()
        return st

    def day_pnl(self, t_ms: int, mark_mid: float | None, half_spread: float) -> float:
        """Resultado do dia em R$: operações fechadas hoje + posição aberta avaliada pelo preço de venda."""
        from datetime import date

        st = self._roll_day(t_ms)
        start = trading_day_start_ms(date.fromisoformat(st["day"]), self.tz, self.day_start)
        realized = self.conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM paper_trades WHERE variant = ? AND exit_ms IS NOT NULL AND exit_ms >= ?",
            (self.variant, start),
        ).fetchone()[0]
        unreal = 0.0
        pos = self.open_trade()
        if pos is not None and mark_mid:
            unreal, _ = close_pnl(
                pos["qty"],
                pos["size"],
                pos["fee_in"],
                sell_fill(mark_mid, half_spread, self.costs.slip_market_pct),
                self.costs.fee,
            )
        return float(realized) + unreal

    def view(self, t_ms: int, mark_mid: float | None, half_spread: float) -> PaperView:
        """Foto da carteira virtual para o motor de decisão: capital, risco por operação, limite do dia e posição aberta."""
        max_loss = self.max_loss_day
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

    def open(
        self, d: Decision, *, ratio: float, bid: float, ask: float, t_ms: int, record: EntryRecord
    ) -> tuple[int | None, str | None]:
        """Compra virtual a mercado com os níveis da decisão, ao preço (bid/ask) do momento da EXECUÇÃO `t_ms`.

        Devolve (id da operação, None) ou (None, motivo) quando não abre: sem níveis ou tamanho, posição já
        aberta ou ordem fora das regras de tamanho do par (este último caso também fica em paper_events).
        """
        if d.levels is None or not d.levels.size_brl:
            return None, "sem níveis ou tamanho"
        if self.open_trade() is not None:
            return None, "já existe posição virtual aberta"
        mid, half = (bid + ask) / 2, (ask - bid) / (ask + bid)
        fill = buy_fill(mid, half, self.costs.slip_market_pct)
        qty = min(d.levels.size_brl, self.equity) / fill
        if self.filters is not None:
            qty, why = self.filters.check(qty, fill)
            if why:
                self.log_event(t_ms, "ordem_recusada", f"compra recusada: {why}", record.signal_id)
                return None, why
        size = qty * fill
        lv = d.levels
        cur = self.conn.execute(
            """INSERT INTO paper_trades (variant, symbol, signal_id, decision_ms, decision_price, entry_ms, entry_price, qty, size, fee_in,
                   stop, target1, target2, confidence, regime, entry_reason, features_json, scenario_json, equity_at_entry, checked_until_ms,
                   bypassed_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self.variant,
                self.symbol,
                record.signal_id,
                record.decision_ms,
                record.decision_price,
                t_ms,
                fill,
                qty,
                size,
                size * self.costs.fee,
                lv.stop * ratio,
                lv.target1 * ratio,
                lv.target2 * ratio,
                d.confidence,
                d.regime,
                record.reason,
                json.dumps(record.features, ensure_ascii=False) if record.features is not None else None,
                json.dumps(record.scenario, ensure_ascii=False) if record.scenario is not None else None,
                self.equity,
                first_full_minute(t_ms),
                json.dumps(record.bypassed, ensure_ascii=False) if record.bypassed is not None else None,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid), None

    def close(self, *, fill: float, t_ms: int, reason: str, decision_ms: int | None = None) -> float:
        """Fecha a posição virtual ao preço dado, grava o resultado e atualiza o patrimônio. Devolve o resultado líquido em R$."""
        pos = self.open_trade()
        assert pos is not None
        pnl, fee_out = close_pnl(pos["qty"], pos["size"], pos["fee_in"], fill, self.costs.fee)
        self.conn.execute(
            """UPDATE paper_trades SET exit_decision_ms = ?, exit_ms = ?, exit_price = ?, exit_reason = ?, pnl = ?, pnl_pct = ?, fees = ?,
                   ret_equity = ? WHERE id = ?""",
            (
                decision_ms if decision_ms is not None else t_ms,
                t_ms,
                fill,
                reason,
                pnl,
                pnl / pos["size"] * 100,
                pos["fee_in"] + fee_out,
                pnl / pos["equity_at_entry"],
                pos["id"],
            ),
        )
        self.conn.execute("UPDATE paper_state SET equity = equity + ? WHERE variant = ?", (pnl, self.variant))
        self.conn.commit()
        return pnl

    def close_market(self, *, bid: float, ask: float, t_ms: int, reason: str, decision_ms: int | None = None) -> float:
        """Fecha a posição virtual a mercado, com meio spread e slippage. Devolve o resultado líquido em R$."""
        mid, half = (bid + ask) / 2, (ask - bid) / (ask + bid)
        return self.close(
            fill=sell_fill(mid, half, self.costs.slip_market_pct), t_ms=t_ms, reason=reason, decision_ms=decision_ms
        )

    def check_oco(self, bars_1m: pd.DataFrame, half_spread: float) -> str | None:
        """Confere a OCO virtual nos candles de 1m FECHADOS que começaram depois da última verificação. Devolve o motivo se fechou."""
        pos = self.open_trade()
        if pos is None or bars_1m.empty:
            return None
        bars = bars_1m[bars_1m["open_time"] >= pos["checked_until_ms"]].reset_index(drop=True)
        if bars.empty:
            return None
        hit = oco_check(
            bars["open"].to_numpy(),
            bars["high"].to_numpy(),
            bars["low"].to_numpy(),
            stop=pos["stop"],
            target=pos["target1"],
            half_spread=half_spread,
            slip_stop_pct=self.costs.slip_stop_pct,
        )
        if hit is not None:
            self.close(fill=hit.fill, t_ms=int(bars["close_time"].iloc[hit.index]) + 1, reason=hit.reason)
            return hit.reason
        self.conn.execute(
            "UPDATE paper_trades SET checked_until_ms = ? WHERE id = ?",
            (int(bars["close_time"].iloc[-1]) + 1, pos["id"]),
        )
        self.conn.commit()
        return None

    def closed_trades(self) -> list[sqlite3.Row]:
        """Operações virtuais fechadas desta variante, em ordem de entrada."""
        return self.conn.execute(
            "SELECT * FROM paper_trades WHERE variant = ? AND exit_ms IS NOT NULL ORDER BY entry_ms", (self.variant,)
        ).fetchall()


def to_backtest_trades(rows: list[sqlite3.Row]) -> list[Trade]:
    """Converte para o formato do backtest, para usar as mesmas métricas."""
    from cryptoagent.backtest.simulator import Trade

    return [
        Trade(
            r["entry_ms"],
            r["exit_ms"],
            r["entry_price"],
            r["exit_price"],
            r["qty"],
            r["size"],
            r["pnl"],
            r["pnl_pct"],
            r["ret_equity"],
            r["exit_reason"],
            r["confidence"] or 0.0,
            r["regime"] or "",
            r["stop"] or np.nan,
            r["target1"] or np.nan,
            r["fees"] or 0.0,
        )
        for r in rows
    ]
