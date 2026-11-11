"""Carrega o histórico e prepara tudo uma vez só (indicadores, preços do par de operação, horário)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cryptoagent.backtest.season_pit import SeasonPIT
from cryptoagent.config import Config
from cryptoagent.data.history import HistoryStore
from cryptoagent.indicators.compute import compute_indicators
from cryptoagent.timeutils import local_hour_weekday_series

REQUIRED = {  # por papel do timeframe na estratégia
    "tf_contexto": ["ema_50", "ema_200", "ema_50_slope"],
    "tf_confirmacao": ["adx", "plus_di", "minus_di", "ema_50"],
    "tf_setup": ["vwap", "atr", "dist_vwap_atr", "bb_pctb", "bb_mid", "rsi", "atr_pctl"],
    "tf_gatilho": ["bb_pctb", "bb_lower", "atr", "rsi"],
}


@dataclass
class BacktestData:
    analysis: str
    trade: str
    frames: dict[str, pd.DataFrame]  # par de análise, com indicadores (o 1m fica sem indicadores)
    close_times: dict[str, np.ndarray]
    t_open_time: np.ndarray  # candles de 1m do par de OPERAÇÃO (execução)
    t_open: np.ndarray
    t_high: np.ndarray
    t_low: np.ndarray
    t_close: np.ndarray
    ratio_time: np.ndarray  # razão preço de operação ÷ preço de análise, por candle de 1m
    ratio: np.ndarray
    prev_day: np.ndarray  # por linha do 15m: [máxima, mínima, fechamento] do dia anterior (Brasília)
    decision_hours: np.ndarray  # hora local de cada decisão (fim de cada candle de 5m)
    first_valid_ms: int  # primeiro instante em que todos os indicadores estão definidos
    decision_tf: str = "5m"  # timeframe do gatilho: uma decisão a cada fechamento dele
    season: SeasonPIT | None = None

    @property
    def last_ms(self) -> int:
        return int(self.close_times[self.decision_tf][-1]) + 1


def _prev_day_table(s15: pd.DataFrame) -> np.ndarray:
    agg = s15.groupby("trading_day", sort=False).agg(h=("high", "max"), l=("low", "min"), c=("close", "last"))
    prev = agg.shift(1)
    return prev.loc[s15["trading_day"]].to_numpy(dtype=float)


def _first_valid(frames: dict[str, pd.DataFrame], p) -> int:
    first = 0
    for role, cols in REQUIRED.items():
        f = frames[getattr(p, role)]
        ok = f[cols].notna().all(axis=1).to_numpy()
        idx = int(np.argmax(ok)) if ok.any() else len(f) - 1
        first = max(first, int(f["close_time"].iloc[idx]) + 1)
    return first


def load_backtest_data(cfg: Config, trade_symbol: str, *, season_cost_pct: float | None = None) -> BacktestData:
    p, tz, day_start = cfg.estrategia, cfg.fuso.local, cfg.fuso.inicio_dia_local
    store = HistoryStore(cfg.path(cfg.historico.pasta))
    sym = cfg.mercado.simbolo_analise
    frames: dict[str, pd.DataFrame] = {}
    for tf in p.timeframes:
        raw = store.load(sym, tf)
        if raw.empty:
            raise FileNotFoundError(f"sem histórico de {sym} {tf}: rode cryptoagent historico baixar")
        frames[tf] = raw if tf == p.tf_timing else compute_indicators(raw, cfg.indicadores, cfg.regime, tz_name=tz, day_start=day_start)
    close_times = {tf: f["close_time"].to_numpy() for tf, f in frames.items()}

    trade_1m = store.load(trade_symbol, "1m")
    if trade_1m.empty:
        raise FileNotFoundError(f"sem histórico de {trade_symbol} 1m: rode cryptoagent historico baixar")
    a1 = frames[p.tf_timing][["open_time", "close"]]
    joined = trade_1m[["open_time", "close"]].merge(a1, on="open_time", suffixes=("_t", "_a"))

    s5 = frames[p.tf_gatilho]
    hours = local_hour_weekday_series(s5["close_time"] + 1, tz)["hour"].to_numpy()
    season = None
    if season_cost_pct is not None:
        t15 = store.load(trade_symbol, cfg.sazonalidade.timeframe)
        if len(t15) > 1000:
            season = SeasonPIT.build(t15, season_cost_pct, cfg.sazonalidade, tz, cfg.indicadores.atr)

    return BacktestData(
        analysis=sym,
        trade=trade_symbol,
        frames=frames,
        close_times=close_times,
        t_open_time=trade_1m["open_time"].to_numpy(),
        t_open=trade_1m["open"].to_numpy(),
        t_high=trade_1m["high"].to_numpy(),
        t_low=trade_1m["low"].to_numpy(),
        t_close=trade_1m["close"].to_numpy(),
        ratio_time=joined["open_time"].to_numpy(),
        ratio=(joined["close_t"] / joined["close_a"]).to_numpy(),
        prev_day=_prev_day_table(frames[p.tf_setup]),
        decision_hours=hours,
        first_valid_ms=_first_valid(frames, p),
        decision_tf=p.tf_gatilho,
        season=season,
    )
