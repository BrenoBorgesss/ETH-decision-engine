"""Conjunto de dados do modelo: cada momento candidato do histórico, suas variáveis e o que aconteceu depois.

Rótulo ("deu certo"): comprando a mercado na abertura do candle de 1m seguinte e deixando a OCO
(stop + alvo 1) na corretora, a operação terminou com resultado POSITIVO depois de taxa, spread e
slippage. Se nem o stop nem o alvo forem atingidos em `tempo_max_min`, vende a mercado nesse
instante. É a mesma execução do backtest (`backtest/execution.py`), com uma simplificação: as
saídas antecipadas das regras (esticado para cima, invalidação) não entram no rótulo.

Cada linha guarda também `fim_ms`, o instante em que o resultado ficou conhecido. No treino só
entram linhas cujo resultado já era conhecido antes do período de teste (sem vazamento do rótulo).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.backtest.data import BacktestData
from cryptoagent.backtest.execution import buy_fill, close_pnl, oco_check, sell_fill
from cryptoagent.backtest.simulator import simulate
from cryptoagent.config import Config
from cryptoagent.engine.context import DecisionContext
from cryptoagent.engine.decision import Decision
from cryptoagent.model.features import FEATURES, extract_features, is_candidate

MIN_MS = 60_000


@dataclass(frozen=True)
class Outcome:
    """Resultado de uma compra hipotética: resultado líquido (%), motivo e instante de saída."""
    pnl_pct: float
    reason: str
    exit_ms: int


def simulate_outcome(data: BacktestData, costs: CostSchedule, t: int, stop_an: float, target_an: float, ratio: float, hour: int, hold_min: float) -> Outcome | None:
    """Compra a mercado no 1m seguinte a `t`, com OCO (stop, alvo 1) no par de operação e saída por tempo. None se faltar dado."""
    k = int(np.searchsorted(data.t_open_time, t, side="left"))
    if k >= len(data.t_open):
        return None
    entry_ms = int(data.t_open_time[k])
    end = int(np.searchsorted(data.t_open_time, entry_ms + hold_min * MIN_MS, side="left"))
    if end >= len(data.t_open):
        return None  # o histórico acaba antes do prazo: resultado desconhecido
    half = costs.half_spread(hour)
    fill = buy_fill(float(data.t_open[k]), half, costs.slip_market_pct)
    size = 100.0
    qty, fee_in = size / fill, size * costs.fee
    hit = oco_check(
        data.t_open[k:end], data.t_high[k:end], data.t_low[k:end], stop=stop_an * ratio, target=target_an * ratio,
        half_spread=half, slip_stop_pct=costs.slip_stop_pct,
    )
    if hit is not None:
        exit_fill, reason, exit_ms = hit.fill, hit.reason, int(data.t_open_time[k + hit.index]) + MIN_MS
    else:
        exit_fill, reason, exit_ms = sell_fill(float(data.t_open[end]), half, costs.slip_market_pct), "tempo", int(data.t_open_time[end])
    pnl, _ = close_pnl(qty, size, fee_in, exit_fill, costs.fee)
    return Outcome(pnl / size * 100, reason, exit_ms)


def build_dataset(cfg: Config, data: BacktestData, costs: CostSchedule, *, start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
    """Percorre o histórico com o mesmo simulador do backtest (sem abrir operações) e devolve uma linha por momento candidato.

    Colunas: `t_ms`, as variáveis de `FEATURES`, `pnl_pct`, `motivo`, `fim_ms`, `rotulo` (1 = resultado positivo),
    `acao_regras` (o que as regras decidiram), `confianca_regras`, `regime` e `hora_local` (cenário do momento).
    """
    p, tz = cfg.estrategia, cfg.fuso.local
    rows: list[dict] = []

    def observe(t: int, ctx: DecisionContext, d: Decision) -> None:
        if not is_candidate(d):
            return
        assert d.levels is not None
        j = int(np.searchsorted(data.close_times[data.decision_tf], t - 1, side="left"))
        hour = int(data.decision_hours[min(j, len(data.decision_hours) - 1)])
        out = simulate_outcome(data, costs, t, d.levels.stop, d.levels.target1, ctx.trade_price_ratio, hour, p.tempo_max_min)
        if out is None:
            return
        row = {"t_ms": t, **extract_features(ctx, d, p, tz)}
        row.update(pnl_pct=out.pnl_pct, motivo=out.reason, fim_ms=out.exit_ms, rotulo=int(out.pnl_pct > 0),
                   acao_regras=d.action, confianca_regras=d.confidence, regime=d.regime, hora_local=hour)
        rows.append(row)

    bt = cfg.backtest
    simulate(
        data, p, cfg.indicadores, costs, capital=bt.capital, loss_pct_day=bt.perda_max_dia_pct, divisor=cfg.risco.divisor_perda_diaria,
        spread_max_pct=cfg.custos.spread_max_pct, start_ms=start_ms, end_ms=end_ms, label="conjunto de dados do modelo",
        observer=observe, open_positions=False,
    )
    columns = ["t_ms", *FEATURES, "pnl_pct", "motivo", "fim_ms", "rotulo", "acao_regras", "confianca_regras", "regime", "hora_local"]
    return pd.DataFrame(rows, columns=columns)
