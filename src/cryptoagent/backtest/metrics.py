"""Métricas de resultado. Tudo já com taxas, spread e slippage."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pandas as pd

from cryptoagent.backtest.simulator import Trade
from cryptoagent.timeutils import local_trading_day, local_trading_day_series

DAY_MS = 86_400_000


def equity_curve(trades: list[Trade], capital: float) -> pd.DataFrame:
    """Patrimônio após cada operação, compondo o retorno sobre o patrimônio de cada uma."""
    eq = capital
    rows = []
    for t in sorted(trades, key=lambda x: x.exit_ms):
        eq *= 1 + t.ret_equity
        rows.append((t.exit_ms, eq))
    return pd.DataFrame(rows, columns=["time_ms", "equity"])


def max_drawdown_pct(equity: pd.Series, capital: float) -> float:
    """Maior queda (%) do patrimônio em relação ao pico anterior, partindo do capital inicial. Valor negativo ou zero."""
    if equity.empty:
        return 0.0
    series = pd.concat([pd.Series([capital]), equity.reset_index(drop=True)])
    peak = series.cummax()
    return float(((series / peak - 1).min()) * 100)


def mean_ci95(values: np.ndarray | list[float]) -> tuple[float, float]:
    """Intervalo de 95% da média (aproximação normal: média ± 1,96 × desvio ÷ raiz de n). NaN com menos de 2 valores.

    É uma aproximação: supõe operações independentes e não corrige caudas gordas. Serve para mostrar
    o tamanho da incerteza; se o intervalo inclui zero, a amostra não distingue ganho de perda.
    """
    x = np.asarray(values, dtype=float)
    if x.size < 2:
        return float("nan"), float("nan")
    half = 1.96 * float(x.std(ddof=1)) / math.sqrt(x.size)
    return float(x.mean() - half), float(x.mean() + half)


def compute_metrics(
    trades: list[Trade], start_ms: int, end_ms: int, capital: float, tz: str = "America/Sao_Paulo"
) -> dict:
    """Métricas de um conjunto de operações no período: retorno, drawdown, acerto, fator de lucro, expectativa, Sharpe diário anualizado, taxas e motivos de saída."""
    days = max(1.0, (end_ms - start_ms) / DAY_MS)
    n = len(trades)
    base = {
        "operacoes": n,
        "dias": days,
        "operacoes_por_dia": n / days,
        "retorno_total_pct": 0.0,
        "drawdown_max_pct": 0.0,
        "taxa_acerto_pct": float("nan"),
        "ganho_medio_pct": float("nan"),
        "perda_media_pct": float("nan"),
        "relacao_ganho_perda": float("nan"),
        "fator_lucro": float("nan"),
        "expectativa_pct": float("nan"),
        "expectativa_ic95_pct": [float("nan"), float("nan")],
        "sharpe": float("nan"),
        "tempo_medio_min": float("nan"),
        "pior_sequencia_perdas": 0,
        "taxas_pagas": 0.0,
        "saidas": {},
    }
    if n == 0:
        return base
    pnl_pct = np.array([t.pnl_pct for t in trades])
    wins, losses = pnl_pct[pnl_pct > 0], pnl_pct[pnl_pct <= 0]
    eq = equity_curve(trades, capital)
    total = (eq["equity"].iloc[-1] / capital - 1) * 100

    # Sharpe: retornos diários (dias sem operação contam como 0, por escolha: o dinheiro parado rende 0
    # na conta), anualizado com 365 dias (cripto negocia todo dia). Os dias são do fuso local, os
    # mesmos das operações: o período vai do dia local do início ao dia local do fim (exclusivo).
    tdf = pd.DataFrame({"exit_ms": [t.exit_ms for t in trades], "r": [t.ret_equity for t in trades]})
    tdf["day"] = local_trading_day_series(tdf["exit_ms"], tz)
    daily = tdf.groupby("day")["r"].apply(lambda r: float(np.prod(1 + r) - 1))
    first_day, last_day = local_trading_day(start_ms, tz), local_trading_day(max(start_ms, end_ms - 1), tz)
    all_days = pd.date_range(first_day, max(last_day, *daily.index), freq="D").date
    daily = daily.reindex(all_days, fill_value=0.0)
    sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(365)) if daily.std(ddof=1) > 0 else float("nan")

    streak = worst = 0
    for x in pnl_pct:
        streak = streak + 1 if x <= 0 else 0
        worst = max(worst, streak)
    gross_win = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in trades if t.pnl <= 0)
    base.update(
        retorno_total_pct=float(total),
        drawdown_max_pct=max_drawdown_pct(eq["equity"], capital),
        taxa_acerto_pct=float(len(wins) / n * 100),
        ganho_medio_pct=float(wins.mean()) if len(wins) else float("nan"),
        perda_media_pct=float(losses.mean()) if len(losses) else float("nan"),
        relacao_ganho_perda=float(wins.mean() / abs(losses.mean()))
        if len(wins) and len(losses) and losses.mean() != 0
        else float("nan"),
        fator_lucro=float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        expectativa_pct=float(pnl_pct.mean()),
        expectativa_ic95_pct=list(mean_ci95(pnl_pct)),
        sharpe=sharpe,
        tempo_medio_min=float(np.mean([(t.exit_ms - t.entry_ms) / 60_000 for t in trades])),
        pior_sequencia_perdas=int(worst),
        taxas_pagas=float(sum(t.fees for t in trades)),
        saidas=dict(Counter(t.exit_reason for t in trades)),
    )
    return base


def buy_and_hold(
    times: np.ndarray,
    closes: np.ndarray,
    start_ms: int,
    end_ms: int,
    *,
    fee: float = 0.001,
    half_spread: float = 0.0,
    slip_pct: float = 0.0,
) -> dict:
    """Comprar no início e segurar até o fim, com os MESMOS custos de uma operação do backtest.

    Compra a mercado no primeiro fechamento (meio spread + slippage + taxa) e vende a mercado no último.
    `fee` é a fração por lado; `half_spread`, fração do preço; `slip_pct`, em %.
    """
    a = int(np.searchsorted(times, start_ms, side="left"))
    b = int(np.searchsorted(times, end_ms, side="left")) - 1
    if b <= a:
        return {"retorno_total_pct": float("nan"), "drawdown_max_pct": float("nan")}
    px = closes[a : b + 1]
    entry = px[0] * (1 + half_spread + slip_pct / 100)
    exit_ = px[-1] * (1 - half_spread - slip_pct / 100)
    ret = (exit_ / entry * (1 - fee) - 1 - fee) * 100  # mesma conta de close_pnl, por real aplicado
    dd = float(((px / np.maximum.accumulate(px)) - 1).min() * 100)
    return {"retorno_total_pct": float(ret), "drawdown_max_pct": dd}
