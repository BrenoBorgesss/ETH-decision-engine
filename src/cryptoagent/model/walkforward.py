"""Avaliação walk-forward do modelo desafiador contra as regras (o campeão atual).

Para cada mês de teste (as MESMAS janelas do backtest):
1. treina só com momentos anteriores cujo resultado já era conhecido antes do mês de teste;
2. prevê a probabilidade dos momentos do mês de teste.

Depois, duas medidas fora da amostra:
- qualidade da previsão: AUC (0,5 = sorteio; 1 = perfeito), erro de Brier e uma tabela de calibração;
- resultado de operar: o simulador do backtest roda com a regra "compra se for momento candidato e a
  probabilidade prevista pelo modelo do mês for >= limiar", com os mesmos custos, uma posição por vez
  e a mesma perda máxima do dia. Comparado com as regras e com comprar e segurar no mesmo período.

Nada é embaralhado: o tempo só anda para a frente.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.backtest.data import BacktestData
from cryptoagent.backtest.metrics import buy_and_hold, compute_metrics, mean_ci95
from cryptoagent.backtest.simulator import simulate
from cryptoagent.config import Config
from cryptoagent.engine.context import DecisionContext
from cryptoagent.engine.decision import Decision
from cryptoagent.model.features import FEATURES, extract_features, is_candidate
from cryptoagent.model.logistic import LogisticModel, train
from cryptoagent.seasonality.validation import Fold, make_folds

CALIBRATION_BINS = (0.0, 0.3, 0.4, 0.5, 0.6, 1.0)


@dataclass
class FoldOutcome:
    """Um mês de teste: tamanho do treino, previsões e qualidade da previsão."""

    fold: Fold
    n_train: int
    n_test: int
    base_rate_train: float | None
    auc: float | None
    model: LogisticModel | None
    note: str = ""


@dataclass
class ChallengerReport:
    """Resultado completo da avaliação do desafiador."""

    pair: str
    threshold: float
    n_dataset: int
    folds: list[FoldOutcome]
    oos_start: int
    oos_end: int
    auc_oos: float | None
    brier_oos: float | None
    brier_baseline_oos: float | None  # Brier de quem sempre prevê a taxa de acerto do treino
    base_rate_oos: float | None
    calibration: list[dict] = field(default_factory=list)
    signals_oos: dict = field(default_factory=dict)  # resultado médio por momento candidato: todos x escolhidos
    rules: dict = field(default_factory=dict)
    challenger: dict = field(default_factory=dict)
    buy_hold: dict = field(default_factory=dict)
    coefficients: list[tuple[str, float]] = field(default_factory=list)  # do último modelo, maiores primeiro


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None


def evaluation_folds(cfg: Config, data: BacktestData, until_ms: int | None = None) -> list[Fold]:
    """Janelas do walk-forward (as mesmas do backtest), só com candles anteriores a `until_ms` quando informado."""
    p, bt = cfg.estrategia, cfg.backtest
    s5 = data.frames[p.tf_gatilho]
    ok = s5["close_time"] >= data.first_valid_ms
    if until_ms is not None:
        ok &= s5["close_time"] < until_ms
    return make_folds(s5.loc[ok, "open_time"], bt.meses_treino, bt.meses_teste)


def evaluate(
    cfg: Config, data: BacktestData, costs: CostSchedule, dataset: pd.DataFrame, *, until_ms: int | None = None
) -> ChallengerReport:
    """Walk-forward completo do desafiador (ver a descrição do módulo).

    `until_ms` (ponto no tempo): só usa o que se sabia até aquele instante. As janelas de teste terminam antes
    dele e as operações que ainda não tinham terminado nele ficam de fora.
    """
    mc, bt, p, tz = cfg.modelo, cfg.backtest, cfg.estrategia, cfg.fuso.local
    if until_ms is not None:
        dataset = dataset[dataset["fim_ms"] < until_ms]
    folds = evaluation_folds(cfg, data, until_ms)
    if not folds:
        raise ValueError("histórico curto demais para o walk-forward (precisa de treino + pelo menos 1 mês de teste)")
    oos_a, oos_b = folds[0].test_start_ms, folds[-1].test_end_ms

    outcomes: list[FoldOutcome] = []
    tested: list[pd.DataFrame] = []
    for f in folds:
        tr = dataset[(dataset["t_ms"] >= f.train_start_ms) & (dataset["fim_ms"] < f.test_start_ms)]
        te = dataset[(dataset["t_ms"] >= f.test_start_ms) & (dataset["t_ms"] < f.test_end_ms)].copy()
        if len(tr) < mc.exemplos_min_treino or tr["rotulo"].nunique() < 2:
            outcomes.append(
                FoldOutcome(
                    f,
                    len(tr),
                    len(te),
                    None,
                    None,
                    None,
                    f"treino com {len(tr)} exemplos (mínimo {mc.exemplos_min_treino}): mês sem modelo",
                )
            )
            continue
        model = train(tr, FEATURES, seed=mc.semente, regularization_c=mc.regularizacao_c)
        te["prob"] = model.predict_proba(te) if len(te) else []
        te["base_treino"] = model.base_rate
        tested.append(te)
        auc = _auc(te["rotulo"].to_numpy(), te["prob"].to_numpy()) if len(te) else None
        outcomes.append(FoldOutcome(f, len(tr), len(te), model.base_rate, auc, model))

    rep = ChallengerReport(
        data.trade, mc.limiar_probabilidade, len(dataset), outcomes, oos_a, oos_b, None, None, None, None
    )
    if tested:
        allt = pd.concat(tested, ignore_index=True)
        y, prob = allt["rotulo"].to_numpy(), allt["prob"].to_numpy()
        rep.auc_oos = _auc(y, prob)
        rep.brier_oos = float(np.mean((prob - y) ** 2))
        rep.brier_baseline_oos = float(np.mean((allt["base_treino"].to_numpy() - y) ** 2))
        rep.base_rate_oos = float(y.mean())
        bins = pd.cut(allt["prob"], CALIBRATION_BINS, include_lowest=True, right=False)
        for interval, g in allt.groupby(bins, observed=True):
            rep.calibration.append(
                {
                    "faixa": f"{interval.left:.0%}–{interval.right:.0%}",
                    "momentos": len(g),
                    "prob_media": float(g["prob"].mean()),
                    "acerto_real": float(g["rotulo"].mean()),
                    "resultado_medio_pct": float(g["pnl_pct"].mean()),
                }
            )
        chosen = allt[allt["prob"] >= mc.limiar_probabilidade]
        rep.signals_oos = {
            "todos": {
                "momentos": len(allt),
                "acerto": float(allt["rotulo"].mean()),
                "resultado_medio_pct": float(allt["pnl_pct"].mean()),
                "ic95_pct": list(mean_ci95(allt["pnl_pct"])),
            },
            "escolhidos": {
                "momentos": len(chosen),
                "acerto": float(chosen["rotulo"].mean()) if len(chosen) else None,
                "resultado_medio_pct": float(chosen["pnl_pct"].mean()) if len(chosen) else None,
                "ic95_pct": list(mean_ci95(chosen["pnl_pct"])),
            },
        }
        last = next(o.model for o in reversed(outcomes) if o.model is not None)
        rep.coefficients = sorted(zip(last.features, last.coef, strict=True), key=lambda x: abs(x[1]), reverse=True)

    # ---- operar de verdade no simulador (uma posição por vez, perda máxima do dia, mesmos custos)
    starts = np.array([o.fold.test_start_ms for o in outcomes])

    def model_at(t: int) -> LogisticModel | None:
        i = int(np.searchsorted(starts, t, side="right")) - 1
        if i < 0 or t >= outcomes[i].fold.test_end_ms:
            return None
        return outcomes[i].model

    def entry_rule(t: int, ctx: DecisionContext, d: Decision) -> bool:
        if not is_candidate(d):
            return False
        model = model_at(t)
        return model is not None and model.predict_one(extract_features(ctx, d, p, tz)) >= mc.limiar_probabilidade

    common = {
        "capital": bt.capital,
        "loss_pct_day": bt.perda_max_dia_pct,
        "divisor": cfg.risco.divisor_perda_diaria,
        "spread_max_pct": cfg.custos.spread_max_pct,
    }
    rules = simulate(data, p, cfg.indicadores, costs, label="regras", **common)
    chall = simulate(data, p, cfg.indicadores, costs, label="desafiador", entry_rule=entry_rule, **common)

    def window(trades: list) -> list:
        return [t for t in trades if oos_a <= t.entry_ms < oos_b and (until_ms is None or t.exit_ms < until_ms)]

    rep.rules = compute_metrics(window(rules.trades), oos_a, oos_b, bt.capital, tz)
    rep.challenger = compute_metrics(window(chall.trades), oos_a, oos_b, bt.capital, tz)
    half = float(np.mean(list(costs.spread_by_hour.values()))) / 200
    rep.buy_hold = buy_and_hold(
        data.t_open_time, data.t_close, oos_a, oos_b, fee=costs.fee, half_spread=half, slip_pct=costs.slip_market_pct
    )
    return rep


def final_model(cfg: Config, dataset: pd.DataFrame, info: dict) -> LogisticModel:
    """Modelo treinado com TODO o conjunto de dados (o que seria usado daqui para a frente)."""
    mc = cfg.modelo
    return train(dataset, FEATURES, seed=mc.semente, regularization_c=mc.regularizacao_c, info=info)
