"""Sinais extras (fora do conjunto mínimo): cada um com o caso que pontua e o que não pontua.

Eles só entram na decisão se estiverem em `estrategia.sinais_ativos`; o backtest mede cada um na ablação.
"""

import numpy as np
import pandas as pd
import pytest

from cryptoagent.config import Estrategia
from cryptoagent.engine.context import DecisionContext, MarketView, RiskView
from cryptoagent.engine.signals import SIGNALS, ramp, score_signals
from cryptoagent.indicators.structure import Level

P = Estrategia()


def ctx(*, g=None, s=None, price=100.0, levels=(), book=None):
    frames = {P.tf_gatilho: g if g is not None else pd.DataFrame(), P.tf_setup: s if s is not None else pd.DataFrame()}
    market = MarketView(0, price, frames, list(levels), book_imbalance=book)
    return DecisionContext("ETHBRL", "ETHBRL", 1.0, market, RiskView([], 10.0, 1000.0), 0.2)


def test_rampa_nas_duas_direcoes_e_valor_ausente():
    assert ramp(2.0, 1.0, 3.0) == 0.5 and ramp(25, 40, 20) == 0.75
    assert ramp(5, 1, 3) == 1.0 and ramp(0, 1, 3) == 0.0
    assert ramp(float("nan"), 1, 3) == 0.0 and ramp(None, 1, 3) == 0.0
    assert ramp(2, 2, 2) == 1.0 and ramp(1, 2, 2) == 0.0


def test_stoch_rsi_so_pontua_cruzamento_para_cima_na_zona_baixa():
    def g(k0, k1, d0, d1):
        return pd.DataFrame({"stochrsi_k": [k0, k1], "stochrsi_d": [d0, d1]})

    assert SIGNALS["stoch_rsi"](ctx(g=g(10, 25, 15, 18), s=None), P).score == 1.0
    assert SIGNALS["stoch_rsi"](ctx(g=g(50, 70, 55, 60)), P).score == 0.0  # cruzou, mas fora da zona baixa
    assert SIGNALS["stoch_rsi"](ctx(g=g(10, 12, 5, 15)), P).score == 0.0  # não cruzou
    falta = SIGNALS["stoch_rsi"](ctx(g=g(10, 25, 15, 18).iloc[:1]), P)
    assert falta.available is False and falta.score == 0.0


def test_divergencia_e_pavio_olham_so_os_candles_recentes():
    s = pd.DataFrame({"bull_div": [True] + [False] * 8})  # a divergência ficou 9 candles atrás
    assert SIGNALS["divergencia"](ctx(s=s), P).score == 0.0
    s.loc[8, "bull_div"] = True
    assert SIGNALS["divergencia"](ctx(s=s), P).score == 1.0
    g = pd.DataFrame({"bull_rejection": [True, False, False]})
    assert SIGNALS["pavio"](ctx(g=g), P).score == 0.0
    g.loc[2, "bull_rejection"] = True
    assert SIGNALS["pavio"](ctx(g=g), P).score == 1.0


def test_fluxo_taker_so_conta_quando_o_pico_foi_de_venda():
    def g(peak, now):
        return pd.DataFrame(
            {"taker_sell_ratio": [0.5, peak, 0.5, 0.5, 0.5, 0.5], "sell_vol_vs_peak": [1, 1, 1, 1, 1, now]}
        )

    assert SIGNALS["fluxo_taker"](ctx(g=g(0.7, 0.3)), P).score == 1.0  # venda secou: 30% do pico
    assert SIGNALS["fluxo_taker"](ctx(g=g(0.7, 0.55)), P).score == pytest.approx(0.5)
    assert SIGNALS["fluxo_taker"](ctx(g=g(0.5, 0.3)), P).score == 0.0  # o pico não foi de venda


def test_suporte_perto_pontua_mais_e_sem_suporte_nao_pontua():
    s = pd.DataFrame({"atr": [2.0]})
    perto = SIGNALS["suporte"](ctx(s=s, levels=[Level(99.5, 3, 10, "pivos")]), P)
    assert perto.score == pytest.approx(0.75) and "3 toque(s)" in perto.detail
    longe = SIGNALS["suporte"](ctx(s=s, levels=[Level(90.0, 1, 10, "pivos")]), P)
    assert longe.score == 0.0
    acima = SIGNALS["suporte"](ctx(s=s, levels=[Level(101.0, 1, 10, "pivos")]), P)
    assert acima.score == 0.0 and "sem suporte" in acima.detail
    sem_atr = SIGNALS["suporte"](ctx(s=pd.DataFrame({"atr": [np.nan]}), levels=[Level(99.5, 3, 10, "pivos")]), P)
    assert sem_atr.score == 0.0


def test_livro_de_ofertas_fica_fora_da_conta_quando_nao_existe():
    sem = SIGNALS["order_book"](ctx(), P)
    assert sem.available is False and "indisponível" in sem.detail
    assert SIGNALS["order_book"](ctx(book=0.2), P).score == pytest.approx(0.5)
    assert "vendedores dominam" in SIGNALS["order_book"](ctx(book=-0.3), P).detail
    # na nota combinada, sinal indisponível não entra no denominador
    p = P.model_copy(update={"sinais_ativos": ["pavio", "order_book"]})
    g = pd.DataFrame({"bull_rejection": [False, True]})
    combined, scores = score_signals(ctx(g=g), p)
    assert combined == 1.0 and [s.available for s in scores] == [True, False]
