"""Livro da operação: posição e resultado do dia SÓ com as operações da estratégia.

Regras (pedidas por você):
- Só o "capital de operação" conta como posição. Compras além do teto e vendas de ETH
  que estava fora da operação são ignoradas (e contadas, para aparecer como aviso).
- O resultado do dia = operações realizadas hoje + posição de operação aberta.
  Depósitos, saques e o resto da carteira não entram (só usamos operações executadas).
- Custo médio por FIFO (o primeiro ETH comprado é o primeiro vendido), com taxas.
- Posição que vem de ontem: para o resultado de HOJE, ela é reavaliada pelo preço do
  início do dia (00:00 de Brasília). Assim o ganho/perda de ontem não conta no limite de hoje.
- Posição aberta é avaliada pelo preço de VENDA atual (bid) menos a taxa de venda:
  é o que você receberia saindo agora (visão conservadora).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

EPS = 1e-12


@dataclass(frozen=True)
class LedgerTrade:
    trade_id: str
    time_ms: int
    side: Literal["buy", "sell"]
    qty: float  # quantidade bruta negociada (ETH)
    price: float  # preço em moeda de cotação (BRL ou USDT)
    fee_quote: float  # taxa total convertida para a moeda de cotação
    fee_in_base: float = 0.0  # parte da taxa paga no próprio ETH (na compra, reduz o ETH recebido)
    fee_estimated: bool = False
    source: str = "binance"

    def __post_init__(self) -> None:
        if self.qty <= 0 or self.price <= 0 or self.fee_quote < 0 or self.fee_in_base < 0:
            raise ValueError(f"operação inválida: {self}")
        if self.side == "buy" and self.fee_in_base >= self.qty:
            raise ValueError("taxa em ETH maior que a quantidade comprada")


@dataclass
class Lot:
    qty: float
    cost: float  # custo por ETH em moeda de cotação, já com a taxa da compra
    trade_id: str
    time_ms: int = 0


@dataclass
class Attribution:
    trade_id: str
    op_qty: float  # quanto entrou/saiu da operação
    ignored_qty: float  # quanto ficou fora (teto atingido ou ETH de fora da operação)
    realized_quote: float = 0.0


class OperationLedger:
    def __init__(self, cap_base: float | None = None, cap_quote: float | None = None):
        if cap_base is not None and cap_base <= 0:
            raise ValueError("teto em ETH precisa ser positivo")
        if cap_quote is not None and cap_quote <= 0:
            raise ValueError("teto em moeda precisa ser positivo")
        self.cap_base = cap_base
        self.cap_quote = cap_quote
        self.lots: deque[Lot] = deque()
        self.realized = 0.0
        self.fees = 0.0
        self.ignored_buy_qty = 0.0
        self.ignored_sell_qty = 0.0
        self.trades = 0
        self.estimated_fees = False

    @property
    def open_qty(self) -> float:
        return sum(l.qty for l in self.lots)

    @property
    def open_cost(self) -> float:
        return sum(l.qty * l.cost for l in self.lots)

    @property
    def avg_cost(self) -> float | None:
        q = self.open_qty
        return self.open_cost / q if q > EPS else None

    def _room(self, cost_per_unit: float) -> float:
        room = math.inf
        if self.cap_base is not None:
            room = min(room, self.cap_base - self.open_qty)
        if self.cap_quote is not None:
            room = min(room, (self.cap_quote - self.open_cost) / cost_per_unit)
        return max(0.0, room)

    def apply(self, t: LedgerTrade) -> Attribution:
        self.trades += 1
        self.estimated_fees |= t.fee_estimated
        if t.side == "buy":
            net_qty = t.qty - t.fee_in_base
            other_fees = max(0.0, t.fee_quote - t.fee_in_base * t.price)  # taxa paga fora do ETH (BNB ou cotação)
            cost_per_unit = (t.price * t.qty + other_fees) / net_qty
            take = min(net_qty, self._room(cost_per_unit))
            if take > EPS:
                self.lots.append(Lot(take, cost_per_unit, t.trade_id, t.time_ms))
                self.fees += t.fee_quote * take / net_qty
            ignored = net_qty - take
            self.ignored_buy_qty += ignored
            return Attribution(t.trade_id, take, ignored)

        take = min(t.qty, self.open_qty)
        ignored = t.qty - take
        self.ignored_sell_qty += ignored
        if take <= EPS:
            return Attribution(t.trade_id, 0.0, t.qty)
        proceeds_per_unit = (t.price * t.qty - t.fee_quote) / t.qty
        realized, remaining = 0.0, take
        while remaining > EPS and self.lots:
            lot = self.lots[0]
            q = min(lot.qty, remaining)
            realized += q * (proceeds_per_unit - lot.cost)
            lot.qty -= q
            remaining -= q
            if lot.qty <= EPS:
                self.lots.popleft()
        self.realized += realized
        self.fees += t.fee_quote * take / t.qty
        return Attribution(t.trade_id, take, ignored, realized)

    def rebase(self, price: float) -> None:
        """Reavalia a posição aberta por um preço (usado no início do dia)."""
        for lot in self.lots:
            lot.cost = price

    def reset_period(self) -> None:
        self.realized = 0.0
        self.fees = 0.0
        self.ignored_buy_qty = 0.0
        self.ignored_sell_qty = 0.0
        self.trades = 0
        self.estimated_fees = False

    def unrealized(self, exit_price: float, exit_fee_rate: float) -> float:
        net_exit = exit_price * (1 - exit_fee_rate)
        return sum(l.qty * (net_exit - l.cost) for l in self.lots)


class MissingDayStartPrice(Exception):
    """Há posição vinda de ontem, mas não há preço do início do dia para reavaliá-la."""


@dataclass(frozen=True)
class DayResult:
    realized_quote: float
    unrealized_quote: float
    fees_quote: float
    trades_today: int
    open_qty: float
    avg_cost_real: float | None  # custo médio real da posição aberta (sem reavaliação)
    carried_qty: float  # posição que veio do dia anterior
    ignored_buy_qty: float
    ignored_sell_qty: float
    fees_estimated: bool
    mark_price: float
    day_start_price: float | None
    attributions: list[Attribution] = field(default_factory=list)
    open_lots: list[tuple[str, float, int]] = field(default_factory=list)  # (id da compra, quantidade, horário)

    @property
    def opened_ms(self) -> int | None:
        """Horário da compra mais antiga ainda aberta."""
        return min(t for _, _, t in self.open_lots) if self.open_lots else None

    @property
    def pnl_quote(self) -> float:
        return self.realized_quote + self.unrealized_quote


def compute_day(
    trades: Iterable[LedgerTrade],
    *,
    day_start_ms: int,
    day_end_ms: int,
    exit_price: float,
    exit_fee_rate: float,
    day_start_price: float | None,
    cap_base: float | None = None,
    cap_quote: float | None = None,
) -> DayResult:
    ordered = sorted(trades, key=lambda t: (t.time_ms, t.trade_id))
    before = [t for t in ordered if t.time_ms < day_start_ms]
    today = [t for t in ordered if day_start_ms <= t.time_ms < day_end_ms]

    day = OperationLedger(cap_base, cap_quote)
    real = OperationLedger(cap_base, cap_quote)  # mesma sequência, sem reavaliação (custo médio real)
    for t in before:
        day.apply(t)
        real.apply(t)
    carried = day.open_qty
    if carried > EPS:
        if day_start_price is None:
            raise MissingDayStartPrice("há posição de ontem, mas falta o preço das 00:00 para reavaliar")
        day.rebase(day_start_price)
    day.reset_period()
    attributions = []
    for t in today:
        attributions.append(day.apply(t))
        real.apply(t)

    return DayResult(
        realized_quote=day.realized,
        unrealized_quote=day.unrealized(exit_price, exit_fee_rate),
        fees_quote=day.fees,
        trades_today=day.trades,
        open_qty=day.open_qty,
        avg_cost_real=real.avg_cost,
        carried_qty=carried,
        ignored_buy_qty=day.ignored_buy_qty,
        ignored_sell_qty=day.ignored_sell_qty,
        fees_estimated=day.estimated_fees,
        mark_price=exit_price,
        day_start_price=day_start_price,
        attributions=attributions,
        open_lots=[(l.trade_id, l.qty, l.time_ms) for l in real.lots],
    )
