"""Custo de ida e volta e ponto de equilíbrio."""

import pytest

from cryptoagent.costs.roundtrip import FxConversion, LegCosts, leg_from_market, round_trip


def test_sem_custos():
    rt = round_trip(LegCosts(), LegCosts())
    assert rt.cost_pct == pytest.approx(0) and rt.breakeven_pct == pytest.approx(0)


def test_so_taxas_de_0_1_por_cento():
    rt = round_trip(LegCosts(fee=0.001), LegCosts(fee=0.001))
    assert rt.factor == pytest.approx(0.999**2)
    assert rt.cost_pct == pytest.approx(0.1999)
    assert rt.breakeven_pct == pytest.approx((1 / 0.998001 - 1) * 100)
    assert rt.breakeven_pct > rt.cost_pct  # empatar exige um pouco mais que o custo
    assert rt.cost_brl(1000) == pytest.approx(1.999)


def test_spread_a_partir_do_livro():
    leg = leg_from_market(0.0, 99.9, 100.1)
    assert leg.half_spread == pytest.approx(0.001)
    rt = round_trip(leg, leg)
    assert rt.cost_pct == pytest.approx((1 - 0.999 / 1.001) * 100)
    assert rt.components_pct["spread"] == pytest.approx(rt.cost_pct)


def test_tudo_junto_e_componentes():
    buy = LegCosts(fee=0.001, half_spread=0.0002, slippage=0.0003)
    sell = LegCosts(fee=0.001, half_spread=0.0002, slippage=0.0001)
    rt = round_trip(buy, sell)
    expected_k = (0.999 / (1.0002 * 1.0003)) * (0.999 * 0.9998 * 0.9999)
    assert rt.factor == pytest.approx(expected_k)
    assert set(rt.components_pct) == {"taxas", "spread", "slippage"}
    assert sum(rt.components_pct.values()) == pytest.approx(rt.cost_pct, rel=1e-2)


def test_cambio_a_cada_operacao_x_uma_vez_no_dia():
    trade = LegCosts(fee=0.001)
    fx_leg = LegCosts(fee=0.001, half_spread=0.0005)
    each = round_trip(trade, trade, FxConversion(fx_leg, fx_leg, trades_per_conversion=1))
    once = round_trip(trade, trade, FxConversion(fx_leg, fx_leg, trades_per_conversion=5))
    no_fx = round_trip(trade, trade)
    k_fx = (0.999 / 1.0005) * (0.999 * 0.9995)
    assert each.factor == pytest.approx(no_fx.factor * k_fx)
    assert once.factor == pytest.approx(no_fx.factor * k_fx ** (1 / 5))
    assert no_fx.cost_pct < once.cost_pct < each.cost_pct
    assert each.components_pct["cambio"] == pytest.approx((1 - k_fx) * 100)


def test_validacoes():
    with pytest.raises(ValueError):
        LegCosts(fee=0.1 * 10)
    with pytest.raises(ValueError):
        LegCosts(slippage=-0.01)
    with pytest.raises(ValueError):
        FxConversion(LegCosts(), trades_per_conversion=0)
