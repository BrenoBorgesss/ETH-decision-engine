"""Indicadores: valores de referência publicados, cálculos à mão e implementações ingênuas independentes."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from cryptoagent.config import Indicadores, Regime
from cryptoagent.indicators.compute import compute_indicators
from cryptoagent.indicators.core import ema, rma, sma
from cryptoagent.indicators.momentum import macd, rsi, stoch_rsi
from cryptoagent.indicators.regime import classify_regime
from cryptoagent.indicators.structure import (
    divergences,
    market_structure,
    nearest_levels,
    previous_day_levels,
    support_resistance,
    swing_points,
    wick_rejection,
)
from cryptoagent.indicators.trend import adx
from cryptoagent.indicators.volatility import atr, bollinger, true_range
from cryptoagent.indicators.volume import book_imbalance, relative_volume, taker_flow, vwap_daily
from cryptoagent.timeutils import UTC, datetime_to_ms

# Exemplo clássico de RSI(14) da StockCharts (ChartSchool), com os valores publicados.
STOCKCHARTS_CLOSES = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931,
    46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521,
    45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314,
]
STOCKCHARTS_RSI = [
    70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46, 41.87, 45.46,
    37.30, 33.08, 37.77,
]


def candles(n=1500, seed=7, start=None, step=300_000):
    """Candles sintéticos (passeio aleatório) com horários reais em UTC."""
    rng = np.random.default_rng(seed)
    start = start or datetime_to_ms(datetime(2026, 10, 20, 0, 0, tzinfo=UTC))
    close = 2400 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0015, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0015, n))
    vol = rng.uniform(50, 150, n)
    t = start + np.arange(n) * step
    return pd.DataFrame(
        {
            "open_time": t,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "close_time": t + step - 1,
            "quote_volume": vol * close,
            "trades": 10,
            "taker_buy_base": vol * rng.uniform(0.3, 0.7, n),
            "taker_buy_quote": 0.0,
        }
    )


# ------------------------------------------------------------------- médias


def test_sma_e_ema_a_mao():
    s = pd.Series(np.arange(1.0, 11.0))
    assert sma(s, 3).iloc[2] == 2 and np.isnan(sma(s, 3).iloc[1])
    e = ema(s, 3)  # semente = média(1,2,3) = 2; alfa 0,5
    assert np.isnan(e.iloc[1])
    assert list(e.iloc[2:6]) == [2.0, 3.0, 4.0, 5.0]


def test_rma_igual_a_formula_de_wilder():
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8])
    r = rma(s, 4)
    expected = [np.nan] * 3 + [2.5]
    for x in s.iloc[4:]:
        expected.append((expected[-1] * 3 + x) / 4)
    np.testing.assert_allclose(r, expected, equal_nan=True)


# --------------------------------------------------------------------- RSI


def test_rsi_bate_com_o_exemplo_publicado_da_stockcharts():
    r = rsi(pd.Series(STOCKCHARTS_CLOSES), 14)
    assert r.iloc[:14].isna().all()
    np.testing.assert_allclose(r.iloc[14:].to_numpy(), STOCKCHARTS_RSI, atol=0.02)


def test_rsi_extremos():
    up = pd.Series(np.arange(1.0, 40.0))
    assert rsi(up, 14).iloc[-1] == 100
    assert rsi(up[::-1].reset_index(drop=True), 14).iloc[-1] == 0
    assert rsi(pd.Series([5.0] * 30), 14).iloc[-1] == 50


def naive_ema(values, n):
    out, prev = [], None
    for i, v in enumerate(values):
        if i < n - 1:
            out.append(np.nan)
        elif i == n - 1:
            prev = float(np.mean(values[:n]))
            out.append(prev)
        else:
            prev = prev + 2 / (n + 1) * (v - prev)
            out.append(prev)
    return np.array(out)


def test_macd_igual_a_implementacao_ingenua():
    df = candles(300)
    m = macd(df["close"])
    line = naive_ema(df["close"].to_list(), 12) - naive_ema(df["close"].to_list(), 26)
    # atol: o MACD é a diferença de dois números ~2400, então sobra ruído de ponto flutuante ~1e-12
    np.testing.assert_allclose(m["macd"], line, equal_nan=True, atol=1e-9)
    valid = line[~np.isnan(line)]
    sig = np.r_[[np.nan] * (len(line) - len(valid)), naive_ema(list(valid), 9)]
    np.testing.assert_allclose(m["macd_signal"], sig, equal_nan=True, atol=1e-9)


def test_stoch_rsi_fica_entre_0_e_100():
    s = stoch_rsi(candles(600)["close"])
    k = s["stochrsi_k"].dropna()
    assert len(k) > 500 and k.between(0, 100).all()


# ------------------------------------------------------------- volatilidade


def test_true_range_e_atr_a_mao():
    df = pd.DataFrame({"high": [10.0, 12, 11, 15], "low": [9.0, 10, 8, 13], "close": [9.5, 11, 9, 14]})
    # TR: 1; max(2, 2.5, 0.5)=2.5; max(3, 0, 3)=3; max(2, 6, 4)=6
    assert list(true_range(df)) == [1.0, 2.5, 3.0, 6.0]
    a = atr(df, 3)
    assert np.isnan(a.iloc[1])
    assert a.iloc[2] == pytest.approx((1 + 2.5 + 3) / 3)
    assert a.iloc[3] == pytest.approx((a.iloc[2] * 2 + 6) / 3)


def test_bollinger_a_mao():
    bb = bollinger(pd.Series([1.0, 2, 3, 4, 5]), 5, 2.0)
    std = np.sqrt(2)  # desvio populacional de 1..5
    assert bb["bb_mid"].iloc[-1] == 3
    assert bb["bb_upper"].iloc[-1] == pytest.approx(3 + 2 * std)
    assert bb["bb_pctb"].iloc[-1] == pytest.approx((5 - (3 - 2 * std)) / (4 * std))
    flat = bollinger(pd.Series([2.0] * 5), 5)
    assert np.isnan(flat["bb_pctb"].iloc[-1])  # sem largura: %B indefinido


# ---------------------------------------------------------------------- ADX


def naive_adx(df, n):
    """ADX como no livro de Wilder: somas suavizadas (TR14, +DM14, −DM14)."""
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    tr, pdm, mdm = [], [], []
    for i in range(1, len(df)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
        up, down = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
    s_tr, s_p, s_m = sum(tr[:n]), sum(pdm[:n]), sum(mdm[:n])
    dxs, adx_out = [], [np.nan] * len(df)
    for j in range(n - 1, len(tr)):
        if j > n - 1:
            s_tr = s_tr - s_tr / n + tr[j]
            s_p = s_p - s_p / n + pdm[j]
            s_m = s_m - s_m / n + mdm[j]
        pdi, mdi = 100 * s_p / s_tr, 100 * s_m / s_tr
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi))
        if len(dxs) == n:
            adx_val = float(np.mean(dxs))
            adx_out[j + 1] = adx_val
        elif len(dxs) > n:
            adx_val = (adx_val * (n - 1) + dxs[-1]) / n
            adx_out[j + 1] = adx_val
    return np.array(adx_out)


def test_adx_igual_ao_metodo_original_de_wilder():
    df = candles(400)
    ours = adx(df, 14)["adx"].to_numpy()
    ref = naive_adx(df, 14)
    assert np.isnan(ours[26]) and not np.isnan(ours[27])  # primeiro ADX no candle 2n − 1
    np.testing.assert_allclose(ours, ref, equal_nan=True, rtol=1e-9)


# ------------------------------------------------------------------- volume


def test_vwap_recomeca_as_00h_de_brasilia():
    t0 = datetime_to_ms(datetime(2026, 10, 22, 2, 0, tzinfo=UTC))  # 23:00 em Brasília (dia 21)
    hour = 3_600_000
    df = pd.DataFrame(
        {
            "open_time": [t0, t0 + hour, t0 + 2 * hour],  # 23:00, 00:00 e 01:00 em Brasília
            "high": [10.0, 20, 30],
            "low": [10.0, 20, 30],
            "close": [10.0, 20, 30],
            "volume": [1.0, 1, 3],
        }
    )
    v = vwap_daily(df)
    assert v["vwap"].iloc[0] == 10
    assert v["vwap"].iloc[1] == 20  # recomeçou às 00:00 de Brasília (03:00 UTC)
    assert v["vwap"].iloc[2] == pytest.approx((20 * 1 + 30 * 3) / 4)
    assert str(v["trading_day"].iloc[0]) == "2026-10-21" and str(v["trading_day"].iloc[1]) == "2026-10-22"


def test_volume_relativo_nao_inclui_o_candle_atual():
    rv = relative_volume(pd.Series([10.0, 10, 10, 40]), 3)
    assert rv.iloc[3] == 4 and np.isnan(rv.iloc[2])


def test_fluxo_taker():
    df = pd.DataFrame({"volume": [10.0, 20, 10], "taker_buy_base": [5.0, 4, 8]})
    f = taker_flow(df, 2)
    assert list(f["taker_sell_vol"]) == [5.0, 16.0, 2.0]
    assert f["taker_sell_ratio"].iloc[1] == pytest.approx(0.8)
    assert f["sell_vol_vs_peak"].iloc[2] == pytest.approx(2 / 16)


def test_desequilibrio_do_book():
    assert book_imbalance([(100, 3)], [(101, 1)]) == pytest.approx((300 - 101) / 401)
    assert book_imbalance([], []) == 0


# ---------------------------------------------------------------- estrutura


def series_df(lows, highs=None):
    lows = np.asarray(lows, dtype=float)
    highs = np.asarray(highs if highs is not None else lows + 1, dtype=float)
    return pd.DataFrame({"open": (lows + highs) / 2, "high": highs, "low": lows, "close": (lows + highs) / 2})


def test_pivo_so_aparece_depois_de_confirmado():
    lows = [10, 9, 8, 7, 8, 9, 10, 11]  # fundo em j=3
    sw = swing_points(series_df(lows), k=2)
    assert sw["swing_low"].notna().sum() == 1
    assert sw["swing_low"].iloc[5] == 7 and sw["swing_low_pos"].iloc[5] == 3  # confirmado em j + k = 5
    assert sw["swing_low"].iloc[:5].isna().all()


def test_estrutura_e_divergencia_altista():
    # dois fundos: o segundo mais baixo (6 < 7)
    lows = [10, 9, 8, 7, 8, 9, 10, 9, 8, 7.5, 6, 7, 8, 9, 10]
    df = series_df(lows)
    sw = swing_points(df, k=2)
    osc = pd.Series(50.0, index=df.index)
    osc.iloc[3], osc.iloc[10] = 20.0, 30.0  # oscilador com fundo MAIS ALTO no segundo fundo
    div = divergences(df, sw, osc, k=2)
    assert div["bull_div"].sum() == 1 and bool(div["bull_div"].iloc[12])
    osc.iloc[10] = 10.0  # agora acompanha a queda: sem divergência
    assert divergences(df, sw, osc, k=2)["bull_div"].sum() == 0
    st = market_structure(sw)
    assert st["last_swing_low"].iloc[-1] == 6


def test_pavio_de_rejeicao():
    df = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [96.0], "close": [100.5]})
    w = wick_rejection(df, 0.5)
    assert w["lower_wick_ratio"].iloc[0] == pytest.approx(4 / 5)
    assert bool(w["bull_rejection"].iloc[0]) and not bool(w["bear_rejection"].iloc[0])


def test_suportes_e_resistencias_agrupam_pivos_proximos():
    sw = pd.DataFrame(
        {
            "swing_high": [np.nan, 110.0, np.nan, 110.4, np.nan, np.nan],
            "swing_high_pos": [np.nan, 1, np.nan, 3, np.nan, np.nan],
            "swing_low": [100.0, np.nan, 100.3, np.nan, 95.0, np.nan],
            "swing_low_pos": [0, np.nan, 2, np.nan, 4, np.nan],
        }
    )
    levels = support_resistance(pd.DataFrame(index=sw.index), sw, at_pos=5, atr_value=1.0, tolerance_atr=0.5)
    prices = sorted(round(lv.price, 2) for lv in levels)
    assert prices == [95.0, 100.15, 110.2]
    sup, res = nearest_levels(levels, 104)
    assert sup.price == pytest.approx(100.15) and sup.touches == 2
    assert res.price == pytest.approx(110.2)


def test_nivel_nao_escorrega_com_pivos_encadeados():
    # pivôs a cada 0,4 (tolerância 0,5): antes tudo virava UM nível de 100 a 104
    prices = [100 + 0.4 * i for i in range(11)]
    sw = pd.DataFrame(
        {"swing_high": np.nan, "swing_high_pos": np.nan, "swing_low": prices, "swing_low_pos": range(11)}, index=range(11)
    )
    levels = support_resistance(pd.DataFrame(index=sw.index), sw, at_pos=10, atr_value=1.0, tolerance_atr=0.5, max_levels=20)
    assert len(levels) > 3
    assert all(lv.touches <= 3 for lv in levels)


def test_niveis_do_dia_anterior_em_brasilia():
    t0 = datetime_to_ms(datetime(2026, 10, 21, 3, 0, tzinfo=UTC))  # 00:00 de 21/10 em Brasília
    day = 86_400_000
    df = pd.DataFrame(
        {
            "open_time": [t0, t0 + day // 2, t0 + day, t0 + day + 1000],
            "high": [105.0, 108, 101, 102],
            "low": [100.0, 103, 99, 100],
            "close": [104.0, 107, 100, 101],
        }
    )
    lv = {x.source: x.price for x in previous_day_levels(df, 3, "America/Sao_Paulo", "00:00")}
    assert lv == {"dia_anterior_maxima": 108, "dia_anterior_minima": 100, "dia_anterior_fechamento": 107}
    assert previous_day_levels(df, 1, "America/Sao_Paulo", "00:00") == []


# ------------------------------------------------------------------- regime


def test_regras_de_regime():
    s = lambda *v: pd.Series(v, dtype=float)
    reg = classify_regime(
        adx=s(30, 30, 15, 22, 30, np.nan),
        plus_di=s(25, 10, 20, 20, 25, 20),
        minus_di=s(10, 25, 20, 20, 10, 20),
        atr_pctl=s(50, 50, 50, 50, 95, 50),
    )
    assert list(reg) == ["tendencia_alta", "tendencia_baixa", "lateral", "transicao", "alta_volatilidade", "indefinido"]


# -------------------------------------------------- tudo junto, sem olhar o futuro


def test_nenhum_indicador_olha_o_futuro():
    """Calcular com só os primeiros m candles dá exatamente o mesmo resultado que com todos."""
    df = candles(1200)
    full = compute_indicators(df, Indicadores(), Regime())
    for m in (700, 950):
        part = compute_indicators(df.iloc[:m], Indicadores(), Regime())
        pd.testing.assert_frame_equal(part, full.iloc[:m], check_exact=False, rtol=1e-9)


def test_compute_indicators_tem_valores_validos_depois_do_aquecimento():
    p = Indicadores()
    out = compute_indicators(candles(1500), p, Regime())
    tail = out.iloc[p.warmup_bars :]
    for col in ("ema_200", "rsi", "macd", "stochrsi_k", "atr", "bb_pctb", "adx", "vwap", "dist_vwap_atr", "rvol", "atr_pctl"):
        assert tail[col].notna().all(), col
    assert tail["rsi"].between(0, 100).all()
    assert set(tail["regime"]) <= {"tendencia_alta", "tendencia_baixa", "lateral", "alta_volatilidade", "transicao"}
