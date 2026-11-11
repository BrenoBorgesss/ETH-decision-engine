"""Roda o pacote completo de testes de um par e devolve tudo para o relatório.

1. Estratégia com os parâmetros PADRÃO do config, medida fora da amostra (sem otimização).
2. Walk-forward: para cada mês de teste, escolhe na grade a combinação com melhor expectativa nos
   6 meses anteriores (treino) e mede no mês seguinte. Os meses de teste juntos dão o resultado.
3. Trava de distância mais baixa (1,5× e 1,0× o custo), lado a lado com a padrão (2,5×).
4. Ablação: conjunto mínimo + cada sinal extra, + todos, e sem o filtro de horário.
5. Comparações: comprar e segurar, e a versão ingênua sem filtros.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from cryptoagent.backtest.baseline import naive_band
from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.backtest.data import BacktestData
from cryptoagent.backtest.metrics import buy_and_hold, compute_metrics, mean_ci95
from cryptoagent.backtest.simulator import SimResult, Trade, simulate
from cryptoagent.config import Config, Estrategia
from cryptoagent.seasonality.validation import Fold, make_folds

EXTRAS_TESTABLE = ["stoch_rsi", "divergencia", "pavio", "fluxo_taker", "suporte"]
EXTRAS_UNTESTABLE = {"order_book": "não existe histórico do order book: só dá para avaliar ao vivo (paper trading)"}


@dataclass
class Variant:
    """Uma variante testada: nome, métricas e observação."""

    name: str
    metrics: dict
    note: str = ""


@dataclass
class PairReport:
    """Resultado completo do backtest de um par: variante padrão, walk-forward, travas de distância, ablação e comparações."""

    pair: str
    costs: CostSchedule
    folds: list[Fold]
    oos_start: int
    oos_end: int
    default: Variant
    default_result: SimResult
    walkforward: Variant | None = None
    wf_choices: list[dict] = field(default_factory=list)
    distances: list[Variant] = field(default_factory=list)
    ablation: list[Variant] = field(default_factory=list)
    untestable: dict[str, str] = field(default_factory=dict)
    naive: Variant | None = None
    buy_hold: dict | None = None
    target_coverage: dict = field(default_factory=dict)
    block_counts: dict = field(default_factory=dict)
    candidates: int = 0
    elapsed_s: float = 0.0


def _in_window(trades: list[Trade], a: int, b: int) -> list[Trade]:
    return [t for t in trades if a <= t.entry_ms < b]


def run_pair(
    cfg: Config,
    data: BacktestData,
    costs: CostSchedule,
    *,
    quick: bool = False,
    progress: Callable[[str], None] = print,
) -> PairReport:
    """Roda o pacote de testes de um par (ver a descrição do módulo). Com `quick=True`, pula a grade do walk-forward e a ablação."""
    t0 = time.perf_counter()
    bt, p0, tz = cfg.backtest, cfg.estrategia, cfg.fuso.local
    common = {
        "ind": cfg.indicadores,
        "costs": costs,
        "capital": bt.capital,
        "loss_pct_day": bt.perda_max_dia_pct,
        "divisor": cfg.risco.divisor_perda_diaria,
        "spread_max_pct": cfg.custos.spread_max_pct,
    }
    s5 = data.frames[p0.tf_gatilho]
    valid_times = s5.loc[s5["close_time"] >= data.first_valid_ms, "open_time"]
    folds = make_folds(valid_times, bt.meses_treino, bt.meses_teste)
    if not folds:
        raise ValueError("histórico curto demais para o walk-forward (precisa de treino + pelo menos 1 mês de teste)")
    oos_a, oos_b = folds[0].test_start_ms, folds[-1].test_end_ms

    def metrics_oos(trades: list[Trade]) -> dict:
        return compute_metrics(_in_window(trades, oos_a, oos_b), oos_a, oos_b, bt.capital, tz)

    progress(f"{data.trade}: estratégia padrão (todo o histórico)...")
    default_res = simulate(data, p0, label="padrão", **common)
    rep = PairReport(
        data.trade,
        costs,
        folds,
        oos_a,
        oos_b,
        Variant("parâmetros padrão (sem otimização)", metrics_oos(default_res.trades)),
        default_res,
    )
    rep.candidates = default_res.stats.candidates
    rep.block_counts = dict(default_res.stats.blocks.most_common())
    mult = np.array(default_res.stats.target_cost_multiples)
    if mult.size:
        rep.target_coverage = {
            "sinais_tecnicos": int(mult.size),
            "pct_alvo_cobre_custo": float((mult >= 1).mean() * 100),
            "pct_alvo_cobre_2_5x": float((mult >= p0.distancia_alvo_min_custo).mean() * 100),
            "mediana_multiplos": float(np.median(mult)),
        }

    naive = naive_band(
        data,
        costs,
        tf=p0.tf_setup,
        capital=bt.capital,
        loss_pct_day=bt.perda_max_dia_pct,
        divisor=cfg.risco.divisor_perda_diaria,
        start_ms=oos_a,
        end_ms=oos_b,
    )
    rep.naive = Variant(naive.label, compute_metrics(naive.trades, oos_a, oos_b, bt.capital, tz))
    mean_half_spread = float(np.mean(list(costs.spread_by_hour.values()))) / 200
    rep.buy_hold = buy_and_hold(
        data.t_open_time,
        data.t_close,
        oos_a,
        oos_b,
        fee=costs.fee,
        half_spread=mean_half_spread,
        slip_pct=costs.slip_market_pct,
    )

    for dist in bt.distancias_alternativas:
        progress(f"{data.trade}: trava de distância {dist:g}× o custo...")
        r = simulate(
            data,
            p0.model_copy(update={"distancia_alvo_min_custo": dist}),
            start_ms=oos_a,
            end_ms=oos_b,
            label=f"dist {dist:g}",
            **common,
        )
        rep.distances.append(
            Variant(f"trava de distância {dist:g}× o custo", compute_metrics(r.trades, oos_a, oos_b, bt.capital, tz))
        )

    if quick:
        rep.elapsed_s = time.perf_counter() - t0
        return rep

    # ---- walk-forward na grade
    keys = list(bt.grade)
    combos = [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*(bt.grade[k] for k in keys))]
    runs = []
    for n, combo in enumerate(combos, 1):
        progress(f"{data.trade}: grade {n}/{len(combos)} {combo}...")
        runs.append((combo, simulate(data, p0.model_copy(update=combo), label=str(combo), **common)))
    test_trades: list[Trade] = []
    for f in folds:
        best, best_score, best_n = None, -np.inf, 0
        for combo, res in runs:
            tr = _in_window(res.trades, f.train_start_ms, f.train_end_ms)
            if len(tr) < bt.operacoes_min_treino:
                continue
            score = float(np.mean([t.pnl_pct for t in tr]))
            if score > best_score:
                best, best_score, best_n = (combo, res), score, len(tr)
        if best is None:
            rep.wf_choices.append(
                {
                    "teste_inicio": f.test_start_ms,
                    "escolha": "nenhuma combinação com operações suficientes no treino: mês sem operar",
                    "treino_ops": 0,
                }
            )
            continue
        if best_score <= bt.expectativa_min_treino_pct:
            # Até a melhor combinação perdeu dinheiro no treino: operar seria apostar contra a evidência.
            rep.wf_choices.append(
                {
                    "teste_inicio": f.test_start_ms,
                    "escolha": f"nenhuma combinação ganhou no treino (melhor: {best_score:+.3f}% por operação em {best_n}): mês sem operar",
                    "treino_ops": best_n,
                    "treino_expectativa_pct": best_score,
                    "treino_expectativa_ic95_pct": list(
                        mean_ci95([t.pnl_pct for t in _in_window(best[1].trades, f.train_start_ms, f.train_end_ms)])
                    ),
                    "teste_ops": 0,
                }
            )
            continue
        chosen = _in_window(best[1].trades, f.test_start_ms, f.test_end_ms)
        test_trades += chosen
        tm = compute_metrics(chosen, f.test_start_ms, f.test_end_ms, bt.capital, tz)
        rep.wf_choices.append(
            {
                "teste_inicio": f.test_start_ms,
                "escolha": best[0],
                "treino_ops": best_n,
                "treino_expectativa_pct": best_score,
                "treino_expectativa_ic95_pct": list(
                    mean_ci95([t.pnl_pct for t in _in_window(best[1].trades, f.train_start_ms, f.train_end_ms)])
                ),
                "teste_ops": tm["operacoes"],
                "teste_expectativa_pct": tm["expectativa_pct"],
                "teste_retorno_pct": tm["retorno_total_pct"],
            }
        )
    rep.walkforward = Variant(
        "walk-forward (parâmetros escolhidos só no treino)", compute_metrics(test_trades, oos_a, oos_b, bt.capital, tz)
    )

    # ---- ablação (no período fora da amostra)
    def ablate(name: str, p: Estrategia, season_on: bool = True, note: str = "") -> None:
        progress(f"{data.trade}: ablação {name}...")
        r = simulate(data, p, start_ms=oos_a, end_ms=oos_b, season_on=season_on, label=name, **common)
        rep.ablation.append(Variant(name, compute_metrics(r.trades, oos_a, oos_b, bt.capital, tz), note))

    base_signals = list(p0.sinais_ativos)
    rep.ablation.append(Variant(f"conjunto mínimo ({', '.join(base_signals)})", rep.default.metrics))
    for extra in EXTRAS_TESTABLE:
        if extra not in base_signals:
            ablate(f"+ {extra}", p0.model_copy(update={"sinais_ativos": [*base_signals, extra]}))
    ablate(
        "+ todos os extras testáveis",
        p0.model_copy(update={"sinais_ativos": base_signals + [e for e in EXTRAS_TESTABLE if e not in base_signals]}),
    )
    ablate("sem o filtro de horário", p0, season_on=False)
    rep.untestable = dict(EXTRAS_UNTESTABLE)
    rep.elapsed_s = time.perf_counter() - t0
    return rep
