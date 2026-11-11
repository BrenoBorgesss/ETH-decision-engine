"""Estrutura de preço: pivôs (topos e fundos), suportes/resistências, divergências e pavios.

Pivôs só aparecem quando CONFIRMADOS: um fundo no candle j só é conhecido no candle j + k,
depois de k candles sem nova mínima. Isso evita "ver o futuro" no backtest.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cryptoagent.timeutils import local_trading_day_series


def swing_points(df: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """Pivôs confirmados.

    Topo no candle j: máxima maior que as k anteriores e maior ou igual às k seguintes.
    Fundo no candle j: mínima menor que as k anteriores e menor ou igual às k seguintes.
    As colunas swing_high/swing_low ficam preenchidas no candle j + k (confirmação),
    com o preço do pivô; swing_high_pos/swing_low_pos guardam a posição j.
    """
    if k < 1:
        raise ValueError("k precisa ser >= 1")
    high, low = df["high"], df["low"]
    win = 2 * k + 1
    center_max = high.rolling(win, center=True, min_periods=win).max()
    center_min = low.rolling(win, center=True, min_periods=win).min()
    left_max = high.shift(1).rolling(k, min_periods=k).max()
    left_min = low.shift(1).rolling(k, min_periods=k).min()
    is_high = (high == center_max) & (high > left_max)
    is_low = (low == center_min) & (low < left_min)

    pos = pd.Series(np.arange(len(df)), index=df.index, dtype="float64")
    conf_high = is_high.shift(k, fill_value=False).astype(bool)
    conf_low = is_low.shift(k, fill_value=False).astype(bool)
    return pd.DataFrame(
        {
            "swing_high": high.shift(k).where(conf_high),
            "swing_high_pos": (pos - k).where(conf_high),
            "swing_low": low.shift(k).where(conf_low),
            "swing_low_pos": (pos - k).where(conf_low),
        },
        index=df.index,
    )


def _last_two(points: pd.Series, index: pd.Index) -> tuple[pd.Series, pd.Series]:
    """Último e penúltimo pivô conhecidos em cada candle."""
    known = points.dropna()
    return known.reindex(index).ffill(), known.shift(1).reindex(index).ffill()


def market_structure(swings: pd.DataFrame) -> pd.DataFrame:
    """Topos e fundos ascendentes (alta), descendentes (baixa) ou misturados."""
    idx = swings.index
    last_h, prev_h = _last_two(swings["swing_high"], idx)
    last_l, prev_l = _last_two(swings["swing_low"], idx)
    hh, hl = last_h > prev_h, last_l > prev_l
    lh, ll = last_h < prev_h, last_l < prev_l
    structure = pd.Series("indefinida", index=idx, dtype="object")
    structure[hh & hl] = "alta"
    structure[lh & ll] = "baixa"
    structure[(hh & ll) | (lh & hl)] = "mista"
    return pd.DataFrame({"last_swing_high": last_h, "last_swing_low": last_l, "structure": structure}, index=idx)


def divergences(df: pd.DataFrame, swings: pd.DataFrame, osc: pd.Series, k: int, max_gap: int = 60) -> pd.DataFrame:
    """Divergências entre preço e um oscilador (ex.: RSI), marcadas no candle de confirmação.

    Altista: fundo mais baixo no preço, mas oscilador com fundo mais alto (queda perdendo força).
    Baixista: topo mais alto no preço, mas oscilador com topo mais baixo.
    Só compara pivôs a no máximo `max_gap` candles de distância.
    """
    osc_at_pivot = osc.shift(k)
    out = {}
    for kind, price_col, pos_col, cmp_price, cmp_osc in (
        ("bull_div", "swing_low", "swing_low_pos", np.less, np.greater),
        ("bear_div", "swing_high", "swing_high_pos", np.greater, np.less),
    ):
        mask = swings[price_col].notna()
        price = swings.loc[mask, price_col]
        pos = swings.loc[mask, pos_col]
        o = osc_at_pivot[mask]
        flag = cmp_price(price, price.shift(1)) & cmp_osc(o, o.shift(1)) & ((pos - pos.shift(1)) <= max_gap)
        out[kind] = flag.reindex(df.index, fill_value=False).astype(bool)
    return pd.DataFrame(out, index=df.index)


def wick_rejection(df: pd.DataFrame, min_wick_ratio: float = 0.5) -> pd.DataFrame:
    """Pavio de rejeição.

    Altista: pavio inferior >= `min_wick_ratio` do tamanho do candle e fechamento na metade de cima
    (vendedores empurraram para baixo e compradores devolveram).
    """
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).where(h > l)
    lower = (np.minimum(o, c) - l) / rng
    upper = (h - np.maximum(o, c)) / rng
    mid = (h + l) / 2
    return pd.DataFrame(
        {
            "lower_wick_ratio": lower,
            "upper_wick_ratio": upper,
            "bull_rejection": ((lower >= min_wick_ratio) & (c >= mid)).fillna(False).astype(bool),
            "bear_rejection": ((upper >= min_wick_ratio) & (c <= mid)).fillna(False).astype(bool),
        },
        index=df.index,
    )


# ------------------------------------------------------ suportes e resistências


@dataclass(frozen=True)
class Level:
    price: float
    touches: int  # quantos pivôs formaram o nível
    last_pos: int  # posição do pivô mais recente do nível
    source: str  # "pivos" ou "dia_anterior_*"


def support_resistance(
    df: pd.DataFrame,
    swings: pd.DataFrame,
    *,
    at_pos: int,
    atr_value: float,
    tolerance_atr: float = 0.5,
    lookback: int = 300,
    max_levels: int = 6,
) -> list[Level]:
    """Níveis de preço onde houve vários topos/fundos, conhecidos ATÉ o candle `at_pos`.

    Pivôs próximos (dentro de `tolerance_atr` × ATR) viram um único nível. Mais toques = mais forte.
    """
    return support_resistance_arrays(
        swings["swing_high"].to_numpy(dtype=float),
        swings["swing_high_pos"].to_numpy(dtype=float),
        swings["swing_low"].to_numpy(dtype=float),
        swings["swing_low_pos"].to_numpy(dtype=float),
        at_pos=at_pos,
        atr_value=atr_value,
        tolerance_atr=tolerance_atr,
        lookback=lookback,
        max_levels=max_levels,
    )


def support_resistance_arrays(
    sh: np.ndarray,
    sh_pos: np.ndarray,
    sl: np.ndarray,
    sl_pos: np.ndarray,
    *,
    at_pos: int,
    atr_value: float,
    tolerance_atr: float = 0.5,
    lookback: int = 300,
    max_levels: int = 6,
) -> list[Level]:
    """Mesmo cálculo de `support_resistance`, recebendo as colunas já como arrays (rápido no backtest)."""
    if atr_value <= 0 or np.isnan(atr_value):
        return []
    start = max(0, at_pos - lookback + 1)
    points: list[tuple[float, int]] = []
    for prices, positions in ((sh, sh_pos), (sl, sl_pos)):
        w = prices[start : at_pos + 1]
        mask = ~np.isnan(w)
        points.extend(zip(w[mask].tolist(), positions[start : at_pos + 1][mask].astype(int).tolist()))
    points.sort()
    tol = tolerance_atr * atr_value
    clusters: list[list[tuple[float, int]]] = []
    sums: list[float] = []
    for p in points:
        # compara com a MÉDIA do grupo (não com o último pivô), para o nível não "escorregar"
        if clusters and abs(p[0] - sums[-1] / len(clusters[-1])) <= tol:
            clusters[-1].append(p)
            sums[-1] += p[0]
        else:
            clusters.append([p])
            sums.append(p[0])
    levels = [Level(s / len(c), len(c), max(x[1] for x in c), "pivos") for c, s in zip(clusters, sums)]
    levels.sort(key=lambda lv: (lv.touches, lv.last_pos), reverse=True)
    return sorted(levels[:max_levels], key=lambda lv: lv.price)


def previous_day_levels(
    df: pd.DataFrame, at_pos: int, tz_name: str, day_start: str, days: pd.Series | None = None
) -> list[Level]:
    """Máxima, mínima e fechamento do dia de operação ANTERIOR (horário de Brasília).

    `days` (opcional) é a coluna de dia de operação já calculada, para não recalcular no backtest.
    """
    sub = df.iloc[: at_pos + 1]
    days = (days.iloc[: at_pos + 1] if days is not None else local_trading_day_series(sub["open_time"], tz_name, day_start))
    earlier = days < days.iloc[-1]
    if not earlier.any():
        return []
    is_prev = (days == days[earlier].iloc[-1]).to_numpy()
    rows = sub[is_prev]
    last = int(np.flatnonzero(is_prev)[-1])
    return [
        Level(float(rows["high"].max()), 1, last, "dia_anterior_maxima"),
        Level(float(rows["low"].min()), 1, last, "dia_anterior_minima"),
        Level(float(rows["close"].iloc[-1]), 1, last, "dia_anterior_fechamento"),
    ]


def nearest_levels(levels: list[Level], price: float) -> tuple[Level | None, Level | None]:
    """(suporte mais próximo abaixo do preço, resistência mais próxima acima)."""
    below = [lv for lv in levels if lv.price < price]
    above = [lv for lv in levels if lv.price > price]
    return (max(below, key=lambda lv: lv.price) if below else None, min(above, key=lambda lv: lv.price) if above else None)
