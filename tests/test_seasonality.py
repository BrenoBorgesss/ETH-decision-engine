"""Análise por horário: perfis, validação fora da amostra, comparação com o histórico e multiplicador."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from cryptoagent.config import Sazonalidade
from cryptoagent.seasonality.compare import (
    activity_label,
    candle_vs_same_hour,
    context_multiplier,
    day_range_vs_history,
    percentile_of,
)
from cryptoagent.seasonality.cost_model import build_cost_model
from cryptoagent.seasonality.profiles import add_buckets, bucket_stats, classify
from cryptoagent.seasonality.validation import make_folds, validate
from cryptoagent.timeutils import UTC, datetime_to_ms, local_trading_day_series

HOUR_MS = 3_600_000
START = datetime_to_ms(datetime(2025, 1, 1, 3, 0, tzinfo=UTC))  # 00:00 de 01/01/2025 em Brasília (quarta)


def synthetic(
    days=400, step=900_000, seed=1, busy_hours=(14, 15), busy_until_ms=None, base=0.0015, busy=0.006, day_shape=0.0
):
    """Candles de 15m em que algumas horas (Brasília) têm amplitude bem maior.

    `day_shape` > 0 dá um formato ao dia (amplitude crescendo ao longo das horas), como no mercado real.
    """
    rng = np.random.default_rng(seed)
    n = days * 86_400_000 // step
    t = START + np.arange(n) * step
    hours = ((t - START) // HOUR_MS) % 24  # START é 00:00 local, sem horário de verão em 2025-26
    amp = base * (1 + day_shape * hours / 23)
    is_busy = np.isin(hours, busy_hours)
    if busy_until_ms is not None:
        is_busy &= t < busy_until_ms
    amp[is_busy] = busy
    amp *= rng.uniform(0.6, 1.4, n)
    price = 2000.0
    return pd.DataFrame(
        {
            "open_time": t,
            "open": price,
            "high": price * (1 + amp / 2),
            "low": price * (1 - amp / 2),
            "close": price,
            "volume": 10.0,
            "close_time": t + step - 1,
            "quote_volume": 20_000.0,
            "trades": 1,
            "taker_buy_base": 5.0,
            "taker_buy_quote": 0.0,
        }
    )


def test_buckets_usam_hora_de_brasilia():
    df = synthetic(days=2)
    b = add_buckets(df)
    assert b.loc[0, "hour"] == 0 and b.loc[0, "weekday"] == 2  # 01/01/2025 foi quarta
    assert b.loc[4, "hour"] == 1
    assert b.loc[0, "range_pct"] == pytest.approx((df.loc[0, "high"] - df.loc[0, "low"]) / 2000 * 100)


def test_classificacao_compensa_e_nao_compensa():
    b = add_buckets(synthetic(days=60))
    stats = bucket_stats(b, ["hour"], cost_pct=0.2, folga=1.5)  # limiar 0,3%
    cls = classify(stats, 50, 30)
    assert cls[14] == "compensa" and cls[15] == "compensa"
    assert cls[3] == "nao_compensa"
    assert stats.loc[14, "pct_above_cost"] == 100 and stats.loc[3, "pct_above_cost"] == 0


def test_janelas_walk_forward_teste_sempre_depois_do_treino():
    b = add_buckets(synthetic(days=400))
    folds = make_folds(b["open_time"], 6, 1)
    assert len(folds) >= 5
    for f in folds:
        assert f.train_start_ms < f.train_end_ms == f.test_start_ms < f.test_end_ms
    assert all(a.test_start_ms < b.test_start_ms for a, b in zip(folds, folds[1:]))
    assert folds[-1].test_end_ms <= int(b["open_time"].max())  # só meses de teste completos


def test_padrao_persistente_e_mantido():
    s = Sazonalidade()
    report = validate(add_buckets(synthetic(days=400)), 0.2, s)
    kept = {(p.key, p.claim) for p in report.kept("hora")}
    assert ("14h", "compensa") in kept and ("03h", "nao_compensa") in kept


def test_formato_do_dia_que_se_repete_e_reconhecido():
    report = validate(add_buckets(synthetic(days=400, busy_hours=(), day_shape=1.0)), 0.2, Sazonalidade())
    assert report.profile_repeats and min(report.spearman) > 0.8
    flat = validate(add_buckets(synthetic(days=400, busy_hours=())), 0.2, Sazonalidade())
    assert not flat.profile_repeats  # sem formato real, não pode "achar" um


def test_padrao_que_some_fora_da_amostra_e_descartado():
    # as 14h/15h só eram agitadas nos primeiros 7 meses: depois o padrão some
    cut = START + 210 * 86_400_000
    s = Sazonalidade()
    report = validate(add_buckets(synthetic(days=400, busy_until_ms=cut)), 0.2, s)
    by_key = {(p.key, p.claim): p for p in report.patterns}
    assert ("14h", "compensa") in by_key
    assert not by_key[("14h", "compensa")].kept
    assert by_key[("14h", "compensa")] in report.discarded("hora")


def test_ruido_puro_nao_gera_padrao_de_dia_da_semana():
    s = Sazonalidade()
    report = validate(add_buckets(synthetic(days=400, busy_hours=())), 0.2, s)
    assert report.kept("dia_semana") == []
    assert report.kept("hora_dia") == []


def test_percentil_e_rotulo():
    assert percentile_of(5, [1, 2, 3, 4]) == 100
    assert percentile_of(0, [1, 2, 3, 4]) == 0
    assert percentile_of(2, [1, 2, 3]) == pytest.approx(50)
    assert np.isnan(percentile_of(1, []))
    assert activity_label(10, 20, 80) == "abaixo do normal"
    assert activity_label(50, 20, 80) == "dentro do normal"
    assert activity_label(95, 20, 80) == "acima do normal"


def test_ultimo_candle_comparado_so_com_o_passado_da_mesma_hora():
    df = synthetic(days=30, busy_hours=())
    df.loc[df.index[-1], "high"] = 2000 * 1.01  # último candle muito agitado
    b = add_buckets(df)
    c = candle_vs_same_hour(b, days=20, low=20, high=80)
    assert c.label == "acima do normal" and c.percentile == 100
    last_hour = b["hour"].iloc[-1]
    assert (
        c.sample_size
        == ((b["hour"] == last_hour) & (b["open_time"] >= b["open_time"].iloc[-1] - 20 * 86_400_000)).sum() - 1
    )


def test_variacao_do_dia_ate_o_mesmo_horario():
    df = synthetic(days=10, busy_hours=())
    b = add_buckets(df)
    days = local_trading_day_series(b["open_time"])
    c = day_range_vs_history(b, days, days=9, low=20, high=80)
    assert c.sample_size == 9
    assert 0 <= c.percentile <= 100


def test_multiplicador_so_usa_padroes_validados():
    s = Sazonalidade()
    assert context_multiplier("nao_compensa", True, "dentro do normal", s).factor == pytest.approx(0.6)
    assert context_multiplier("nao_compensa", False, "dentro do normal", s).factor == 1.0
    assert context_multiplier("compensa", True, "abaixo do normal", s).factor == pytest.approx(0.8)
    assert context_multiplier(None, False, None, s).factor == 1.0
    for cls in ("compensa", "neutro", "nao_compensa"):
        for act in ("abaixo do normal", "dentro do normal", "acima do normal"):
            assert context_multiplier(cls, True, act, s).factor <= 1.0  # nunca aumenta a confiança


def test_custo_usa_config_sem_coleta(tmp_path):
    cm = build_cost_model("ETHBRL", 0.001, {"ETHBRL": 0.04}, tmp_path / "nao_existe.sqlite")
    assert cm.spread_pct == 0.04 and "config" in cm.spread_source
    assert cm.breakeven_pct == pytest.approx((1 / ((0.999 / 1.0002) * (0.999 * 0.9998)) - 1) * 100)
