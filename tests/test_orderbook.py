"""Slippage simulado a partir de um order book de exemplo."""

import pytest

from cryptoagent.costs.orderbook import depth_quote, simulate_market_buy, simulate_market_sell, spread_pct

ASKS = [(100.0, 1.0), (101.0, 1.0), (102.0, 10.0)]
BIDS = [(99.0, 1.0), (98.0, 1.0), (97.0, 10.0)]


def test_compra_dentro_do_primeiro_nivel_nao_tem_slippage():
    fill = simulate_market_buy(ASKS, 50)
    assert fill.avg_price == pytest.approx(100.0)
    assert fill.slippage_pct == pytest.approx(0.0)
    assert fill.complete and fill.levels_used == 1


def test_compra_que_atravessa_dois_niveis():
    # 100 USDT no nível 100 (1 ETH) + 50 USDT no nível 101 (0,49505 ETH)
    fill = simulate_market_buy(ASKS, 150)
    base = 1 + 50 / 101
    assert fill.filled_base == pytest.approx(base)
    assert fill.avg_price == pytest.approx(150 / base)
    assert fill.slippage_pct == pytest.approx((150 / base / 100 - 1) * 100)
    assert fill.slippage_pct == pytest.approx(0.3311, abs=1e-4)
    assert fill.complete and fill.levels_used == 2


def test_compra_maior_que_o_livro_fica_incompleta():
    fill = simulate_market_buy(ASKS, 10_000)
    assert not fill.complete
    assert fill.filled_quote == pytest.approx(100 + 101 + 1020)


def test_venda_que_atravessa_dois_niveis():
    fill = simulate_market_sell(BIDS, 1.5)
    assert fill.avg_price == pytest.approx((99 + 49) / 1.5)
    assert fill.slippage_pct == pytest.approx((1 - (148 / 1.5) / 99) * 100)
    assert fill.complete


def test_spread_e_profundidade():
    assert spread_pct(99.9, 100.1) == pytest.approx(0.2)
    assert depth_quote(ASKS, 2) == pytest.approx(201)
    with pytest.raises(ValueError):
        spread_pct(100.1, 99.9)  # livro cruzado


def test_entradas_invalidas():
    with pytest.raises(ValueError):
        simulate_market_buy([], 10)
    with pytest.raises(ValueError):
        simulate_market_buy(ASKS, 0)
    with pytest.raises(ValueError):
        simulate_market_sell(BIDS, -1)
