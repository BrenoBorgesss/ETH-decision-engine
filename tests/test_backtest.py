"""Backtest: atalhos exatos, sem olhar o futuro, custos e métricas."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.backtest.data import load_backtest_data
from cryptoagent.backtest.metrics import buy_and_hold, compute_metrics, max_drawdown_pct
from cryptoagent.backtest.simulator import Trade, simulate
from cryptoagent.config import Config
from cryptoagent.data.history import HistoryStore
from cryptoagent.timeutils import UTC, datetime_to_ms

START = datetime_to_ms(datetime(2026, 1, 1, tzinfo=UTC))
MIN = 60_000


def synthetic_1m(days: int, seed: int = 3) -> pd.DataFrame:
    """Preço com reversão à média e choques (para gerar esticamentos e voltas)."""
    rng = np.random.default_rng(seed)
    n = days * 1440
    x = np.zeros(n)
    for i in range(1, n):
        shock = rng.normal(0, 0.012) if rng.random() < 0.004 else 0.0
        x[i] = x[i - 1] * 0.996 + rng.normal(0, 0.0012) + shock
    close = 2000 * np.exp(x + np.linspace(0, 0.05, n))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0004, n))
    lo = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0004, n))
    vol = rng.uniform(5, 15, n)
    t = START + np.arange(n) * MIN
    return pd.DataFrame(
        {
            "open_time": t,
            "open": open_,
            "high": hi,
            "low": lo,
            "close": close,
            "volume": vol,
            "close_time": t + MIN - 1,
            "quote_volume": vol * close,
            "trades": 5,
            "taker_buy_base": vol * rng.uniform(0.3, 0.7, n),
            "taker_buy_quote": 0.0,
        }
    )


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    step = minutes * MIN
    g = df.groupby((df["open_time"] - START) // step)
    out = pd.DataFrame(
        {
            "open_time": g["open_time"].first(),
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "quote_volume": g["quote_volume"].sum(),
            "trades": g["trades"].sum(),
            "taker_buy_base": g["taker_buy_base"].sum(),
            "taker_buy_quote": 0.0,
        }
    )
    out["close_time"] = out["open_time"] + step - 1
    return out.reset_index(drop=True)


def make_cfg(tmp_path, one_min: pd.DataFrame) -> Config:
    store = HistoryStore(tmp_path / "hist")
    for tf, m in (("1m", 1), ("5m", 5), ("15m", 15), ("1h", 60), ("4h", 240)):
        store.save("ETHUSDT", tf, one_min if m == 1 else resample(one_min, m))
    cfg = Config.model_validate(
        {
            "historico": {"pasta": str(tmp_path / "hist")},
            # dados sintéticos curtos: contexto no 1h para os indicadores aquecerem a tempo
            "estrategia": {
                "tf_contexto": "1h",
                "confianca_minima": {"lateral": 30, "transicao": 30, "tendencia_alta": 30, "tendencia_baixa": 30},
            },
            "indicadores": {"janela_percentil": 100},
        }
    )
    cfg._root = tmp_path
    return cfg


COSTS = CostSchedule("ETHUSDT", 0.001, {h: 0.002 for h in range(24)}, [], 0.01, 0.05, "teste")
FREE = CostSchedule("ETHUSDT", 0.0, {h: 0.0 for h in range(24)}, [], 0.0, 0.0, "teste")


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bt")
    one = synthetic_1m(30)
    cfg = make_cfg(tmp, one)
    data = load_backtest_data(cfg, "ETHUSDT")
    return cfg, data, one, tmp


def run(cfg, data, costs=COSTS, **kw):
    return simulate(
        data,
        cfg.estrategia,
        cfg.indicadores,
        costs,
        capital=1000,
        loss_pct_day=3,
        divisor=3,
        spread_max_pct=0.15,
        season_on=False,
        **kw,
    )


def key(trades):
    return [(t.entry_ms, t.exit_ms, round(t.pnl, 9), t.exit_reason) for t in trades]


def test_dados_sinteticos_geram_operacoes(setup):
    cfg, data, _, _ = setup
    r = run(cfg, data)
    assert r.stats.candidates > 50
    assert len(r.trades) >= 3, r.stats.blocks


def test_atalhos_dao_exatamente_o_mesmo_resultado_que_chamar_o_motor_em_tudo(setup):
    cfg, data, _, _ = setup
    fast = run(cfg, data, prefilter=True)
    slow = run(cfg, data, prefilter=False)
    assert key(fast.trades) == key(slow.trades)


def test_simulacao_nao_olha_o_futuro(setup, tmp_path):
    cfg, data, one, _ = setup
    full = run(cfg, data)
    cut = START + 24 * 86_400_000
    cfg_cut = make_cfg(tmp_path, one[one["open_time"] < cut].reset_index(drop=True))
    part = run(cfg_cut, load_backtest_data(cfg_cut, "ETHUSDT"))
    limit = cut - 6 * 3_600_000  # operações já encerradas bem antes do corte
    assert key([t for t in part.trades if t.exit_ms < limit]) == key([t for t in full.trades if t.exit_ms < limit])
    assert any(t.exit_ms < limit for t in full.trades)


def test_custos_entram_em_toda_operacao(setup):
    cfg, data, _, _ = setup
    with_costs = run(cfg, data)
    for t in with_costs.trades:
        gross = t.qty * t.exit_price
        assert t.pnl == pytest.approx(gross - gross * 0.001 - t.size - t.size * 0.001)
        assert t.fees == pytest.approx(t.size * 0.001 + gross * 0.001)
    free = run(cfg, data, costs=FREE)
    assert free.trades and all(t.fees == 0 for t in free.trades)
    assert all(t.pnl == pytest.approx(t.qty * t.exit_price - t.size) for t in free.trades)


def test_stop_executa_com_slippage_e_nunca_acima_do_stop(setup):
    cfg, data, _, _ = setup
    for t in run(cfg, data).trades:
        if t.exit_reason == "stop":
            assert t.exit_price <= t.stop * (1 - 0.0005) + 1e-9


def trade(ret, pnl_pct, day):
    t0 = START + day * 86_400_000
    return Trade(
        t0,
        t0 + 3_600_000,
        100,
        100,
        1,
        100,
        pnl_pct,
        pnl_pct,
        ret,
        "alvo1" if ret > 0 else "stop",
        50,
        "lateral",
        99,
        101,
        0.2,
    )


def test_metricas_a_mao():
    trades = [trade(0.02, 2.0, 0), trade(-0.01, -1.0, 1), trade(-0.01, -1.0, 2), trade(0.03, 3.0, 3)]
    m = compute_metrics(trades, START, START + 10 * 86_400_000, 1000)
    assert m["operacoes"] == 4 and m["operacoes_por_dia"] == pytest.approx(0.4)
    assert m["taxa_acerto_pct"] == 50
    assert m["relacao_ganho_perda"] == pytest.approx(2.5 / 1.0)
    assert m["retorno_total_pct"] == pytest.approx((1.02 * 0.99 * 0.99 * 1.03 - 1) * 100)
    assert m["drawdown_max_pct"] == pytest.approx((0.99 * 0.99 - 1) * 100)
    assert m["pior_sequencia_perdas"] == 2
    assert m["fator_lucro"] == pytest.approx(5 / 2)
    assert compute_metrics([], START, START + 86_400_000, 1000)["operacoes"] == 0
    lo, hi = m["expectativa_ic95_pct"]
    assert lo < m["expectativa_pct"] < hi and lo < 0 < hi  # 4 operações: o intervalo inclui zero


def test_sharpe_usa_os_dias_do_fuso_local():
    """B4: uma operação que fecha às 22h de Brasília do dia anterior ao primeiro dia UTC não pode sumir."""
    inicio = START  # meia-noite UTC = 21h do dia anterior em Brasília
    t = Trade(
        inicio + 3_600_000, inicio + 7_200_000, 100, 101, 1, 100, 1, 1, 0.01, "alvo1", 50, "lateral", 99, 101, 0.2
    )
    m = compute_metrics([t], inicio, inicio + 3 * 86_400_000, 1000)
    # com o dia em UTC, o dia local da operação (31/12) ficava fora da série: todos os dias valiam 0
    # e o Sharpe saía indefinido (NaN). Com os dias locais, a operação entra e o Sharpe é calculado.
    assert m["operacoes"] == 1 and not np.isnan(m["sharpe"])


def test_drawdown_e_comprar_e_segurar():
    assert max_drawdown_pct(pd.Series([1100.0, 880.0, 1200.0]), 1000) == pytest.approx(-20)
    t = np.array([0, 1, 2, 3])
    bh = buy_and_hold(t, np.array([100.0, 120.0, 90.0, 110.0]), 0, 4)
    assert bh["retorno_total_pct"] == pytest.approx((1.1 * 0.999 - 1.001) * 100)  # mesma conta de uma operação
    com_custos = buy_and_hold(
        t, np.array([100.0, 120.0, 90.0, 110.0]), 0, 4, fee=0.001, half_spread=0.0002, slip_pct=0.01
    )
    assert com_custos["retorno_total_pct"] == pytest.approx((110 * 0.9997 / (100 * 1.0003) * 0.999 - 1.001) * 100)
    assert bh["drawdown_max_pct"] == pytest.approx(-25)


def test_custos_congelados_vao_e_voltam_do_arquivo_sem_mudar(tmp_path):
    """Reprodutibilidade: os custos gravados num arquivo voltam idênticos, e o resultado também."""
    from cryptoagent.backtest.costs import load_schedules, save_schedules

    odd = CostSchedule("ETHBRL", 0.00075, {h: 0.01 + h / 1000 for h in range(24)}, [3, 14], 0.01, 0.05, "lida da conta")
    path = tmp_path / "pasta" / "custos.json"
    save_schedules(path, [COSTS, odd])
    back = load_schedules(path)
    assert back == {"ETHUSDT": COSTS, "ETHBRL": odd}
    assert list(back["ETHBRL"].spread_by_hour) == list(range(24))  # inteiros, em ordem numérica
    assert back["ETHBRL"].roundtrip_pct(14) == odd.roundtrip_pct(14)
    first = path.read_text(encoding="utf-8")
    save_schedules(path, [odd, COSTS])  # a ordem de entrada não muda o arquivo
    assert path.read_text(encoding="utf-8") == first


def test_arquivo_de_custos_invalido_e_recusado(tmp_path):
    import json

    from cryptoagent.backtest.costs import load_schedules, save_schedules

    path = tmp_path / "custos.json"
    save_schedules(path, [COSTS])
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["pares"]["ETHUSDT"]["spread_by_hour"]["23"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="24 horas"):
        load_schedules(path)
    data["versao"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="versão desconhecida"):
        load_schedules(path)
