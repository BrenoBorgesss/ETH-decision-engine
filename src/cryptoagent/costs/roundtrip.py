"""Custo de ida e volta (compra + venda) e ponto de equilíbrio.

Modelo multiplicativo exato:
  compra: paga  meio × (1 + meio_spread) × (1 + slippage) e recebe quantidade × (1 − taxa)
  venda:  recebe meio × (1 − meio_spread) × (1 − slippage), e o valor × (1 − taxa)

  retorno líquido = (meio_saída / meio_entrada) × K
  custo de ida e volta (preço parado) = 1 − K
  ponto de equilíbrio (quanto o preço precisa subir para empatar) = 1/K − 1

Câmbio (ETHUSDT operado a partir de R$): converte BRL→USDT antes e, se quiser,
USDT→BRL depois. Se a conversão for feita uma vez para N operações, o custo dela é
dividido entre as N operações (fator elevado a 1/N).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LegCosts:
    fee: float = 0.0  # fração (0,001 = 0,1%)
    half_spread: float = 0.0  # fração do preço médio
    slippage: float = 0.0  # fração

    def __post_init__(self) -> None:
        for name in ("fee", "half_spread", "slippage"):
            value = getattr(self, name)
            if not 0 <= value < 1:
                raise ValueError(f"{name} precisa estar entre 0 e 1 (fração), recebido {value}")


def buy_factor(leg: LegCosts) -> float:
    return (1 - leg.fee) / ((1 + leg.half_spread) * (1 + leg.slippage))


def sell_factor(leg: LegCosts) -> float:
    return (1 - leg.fee) * (1 - leg.half_spread) * (1 - leg.slippage)


@dataclass(frozen=True)
class FxConversion:
    to_quote: LegCosts  # ex.: BRL -> USDT (compra de USDT no USDTBRL)
    back: LegCosts | None = None  # USDT -> BRL; None = mantém em USDT
    trades_per_conversion: int = 1  # 1 = converte a cada operação

    def __post_init__(self) -> None:
        if self.trades_per_conversion < 1:
            raise ValueError("trades_per_conversion precisa ser >= 1")


@dataclass(frozen=True)
class RoundTrip:
    factor: float  # K: quanto sobra de cada 1 aplicado, com o preço parado
    cost_pct: float  # (1 − K) × 100
    breakeven_pct: float  # (1/K − 1) × 100
    components_pct: dict[str, float]  # custo de cada parte isolada, em %

    def cost_brl(self, capital_brl: float) -> float:
        return capital_brl * (1 - self.factor)


def round_trip(buy: LegCosts, sell: LegCosts, fx: FxConversion | None = None) -> RoundTrip:
    k = buy_factor(buy) * sell_factor(sell)
    components = {
        "taxas": (1 - (1 - buy.fee) * (1 - sell.fee)) * 100,
        "spread": (1 - (1 - sell.half_spread) / (1 + buy.half_spread)) * 100,
        "slippage": (1 - (1 - sell.slippage) / (1 + buy.slippage)) * 100,
    }
    if fx is not None:
        k_fx = buy_factor(fx.to_quote) * (sell_factor(fx.back) if fx.back else 1.0)
        k_fx_per_trade = k_fx ** (1 / fx.trades_per_conversion)
        k *= k_fx_per_trade
        components["cambio"] = (1 - k_fx_per_trade) * 100
    return RoundTrip(k, (1 - k) * 100, (1 / k - 1) * 100, components)


def leg_from_market(fee: float, bid: float, ask: float, slippage_pct: float = 0.0) -> LegCosts:
    """Monta os custos de uma ponta a partir do livro: meio-spread = (ask − meio) / meio."""
    if bid <= 0 or ask < bid:
        raise ValueError("bid/ask inválidos")
    return LegCosts(fee=fee, half_spread=(ask - bid) / (ask + bid), slippage=max(slippage_pct, 0.0) / 100)
