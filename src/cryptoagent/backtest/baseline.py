"""Versão ingênua para comparação: compra sempre que o 15m fecha abaixo da banda inferior.

Sem filtro de tendência, sem gatilho anti-faca, sem trava de distância, sem horário.
Sai no meio das bandas (alvo), 1 ATR(15m) abaixo da entrada (stop) ou depois de 3 h.
Mesmos custos, mesmo tamanho por risco. Serve para mostrar se os filtros ajudam.
"""

from __future__ import annotations

import numpy as np

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.backtest.data import BacktestData
from cryptoagent.backtest.simulator import SimResult, SimStats, Trade


def naive_band(
    data: BacktestData,
    costs: CostSchedule,
    *,
    tf: str,
    capital: float,
    loss_pct_day: float,
    divisor: float,
    start_ms: int,
    end_ms: int,
    hold_min: float = 180,
) -> SimResult:
    """Versão ingênua para comparação: compra sempre que o %B fica abaixo de 0, sem nenhum filtro, com stop a 1 ATR e alvo no meio das bandas, por no máximo `hold_min` minutos. Usa os mesmos custos."""
    f = data.frames[tf]
    ct = data.close_times[tf]
    trades: list[Trade] = []
    equity = capital
    busy_until = 0
    i0 = int(np.searchsorted(ct, max(start_ms, data.first_valid_ms) - 1, side="left"))
    pctb, atr_, mid, close = (f[c].to_numpy() for c in ("bb_pctb", "atr", "bb_mid", "close"))
    for i in range(i0, len(f)):
        t = int(ct[i]) + 1
        if t > end_ms:
            break
        if t < busy_until or not (pctb[i] < 0) or not atr_[i] > 0:
            continue
        ri = int(np.searchsorted(data.ratio_time, t - 60_000, side="right")) - 1
        ratio = float(data.ratio[max(ri, 0)])
        k = int(np.searchsorted(data.t_open_time, t, side="left"))
        if k >= len(data.t_open):
            break
        j5 = int(np.searchsorted(data.close_times[data.decision_tf], t - 1, side="left"))
        hour = int(data.decision_hours[min(j5, len(data.decision_hours) - 1)])
        entry = float(data.t_open[k] * (1 + costs.half_spread(hour) + costs.slip_market_pct / 100))
        stop = (close[i] - atr_[i]) * ratio
        target = mid[i] * ratio
        risk_pct = max(1e-6, (1 - stop / entry) * 100 + costs.roundtrip_pct(hour))
        size = min(capital * loss_pct_day / 100 / divisor / (risk_pct / 100), equity)
        qty = size / entry
        fee_in = size * costs.fee
        end_k = int(np.searchsorted(data.t_open_time, int(data.t_open_time[k]) + hold_min * 60_000, side="left"))
        lo, hi = data.t_low[k:end_k], data.t_high[k:end_k]
        hs = np.flatnonzero(lo <= stop)
        ht = np.flatnonzero(hi >= target)
        if hs.size and (not ht.size or hs[0] <= ht[0]):
            j = k + hs[0]
            fill, reason = min(data.t_open[j], stop) * (1 - costs.slip_stop_pct / 100 - costs.half_spread(hour)), "stop"
        elif ht.size:
            j = k + ht[0]
            fill, reason = max(data.t_open[j], target), "alvo1"
        else:
            j = min(end_k, len(data.t_open) - 1)
            fill, reason = data.t_open[j] * (1 - costs.half_spread(hour) - costs.slip_market_pct / 100), "tempo"
        gross = qty * fill
        fee_out = gross * costs.fee
        pnl = gross - fee_out - size - fee_in
        trades.append(
            Trade(
                int(data.t_open_time[k]),
                int(data.t_open_time[j]) + 60_000,
                entry,
                float(fill),
                qty,
                size,
                pnl,
                pnl / size * 100,
                pnl / equity,
                reason,
                0.0,
                "",
                stop,
                target,
                fee_in + fee_out,
            )
        )
        equity += pnl
        busy_until = int(data.t_open_time[j]) + 60_000
    return SimResult(
        "versão ingênua (banda inferior, sem filtros)",
        trades,
        SimStats(),
        max(start_ms, data.first_valid_ms),
        end_ms,
        capital,
    )
