"""Relatório da carteira virtual: patrimônio, métricas, comprar e segurar, desfecho dos sinais e calibração."""

from pathlib import Path

import pandas as pd
import pytest

from cryptoagent.paper.report import load_backtest_json, variant_report
from cryptoagent.storage import sim_db

SAMPLE_1M = Path(__file__).resolve().parents[1] / "exemplos" / "historico" / "ETHBRL" / "1m.parquet"


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    from test_replay import demo_cfg, ms, run_replay

    tmp = tmp_path_factory.mktemp("relatorio")
    cfg = demo_cfg(tmp)
    db = tmp / "sim.sqlite"
    start, end = ms(2026, 9, 8, 9), ms(2026, 9, 8, 13)
    run_replay(cfg, db, start, end)
    bars = pd.read_parquet(SAMPLE_1M)
    return db, bars[(bars["open_time"] >= start) & (bars["open_time"] < end + 6 * 3_600_000)], start, end


def report(db, bars, end, **kw):
    conn = sim_db.connect(db)
    try:
        args = {"horizon_h": 4, "min_trades": 2, "backtest_json": None, "trade_symbol": "ETHBRL", "now": end}
        args.update(kw)
        return variant_report(conn, "padrao", 999.0, bars, **args)
    finally:
        conn.close()


def test_patrimonio_e_metricas_batem_com_as_operacoes_fechadas(replay):
    db, bars, start, end = replay
    rep = report(db, bars, end)
    conn = sim_db.connect(db)
    pnls = [r[0] for r in conn.execute("SELECT pnl FROM paper_trades WHERE variant='padrao' AND exit_ms IS NOT NULL")]
    conn.close()
    assert rep.initial_capital == 1000  # vem do banco (capital do início), não do argumento
    assert rep.metrics["operacoes"] == len(pnls) == 2
    assert rep.equity == pytest.approx(1000 + sum(pnls))
    assert rep.started_ms <= start + 60_000 and rep.end_ms == end
    assert rep.open_trade is None and rep.enough is True
    assert report(db, bars, end, min_trades=3).enough is False
    assert rep.rejected_orders == 0
    assert sum(rep.signals_by_action.values()) == 48  # um sinal por candle de 5m em 4 horas


def test_comprar_e_segurar_so_com_custos_informados(replay):
    db, bars, _, end = replay
    sem = report(db, bars, end)
    com = report(db, bars, end, costs=(0.001, 0.0002, 0.01))
    assert sem.buy_hold == {}
    first, last = bars[bars["open_time"] >= sem.started_ms].iloc[0], bars[bars["open_time"] < end].iloc[-1]
    entrada = float(first["close"]) * (1 + 0.0002 + 0.0001)
    saida = float(last["close"]) * (1 - 0.0002 - 0.0001)
    esperado = (saida / entrada * (1 - 0.001) - 1 - 0.001) * 100  # mesma conta de uma operação do backtest
    assert com.buy_hold["retorno_total_pct"] == pytest.approx(esperado)
    assert com.buy_hold["retorno_total_pct"] < (float(last["close"]) / float(first["close"]) - 1) * 100


def test_desfecho_dos_sinais_e_calibracao_somam_o_mesmo_total(replay):
    db, bars, _, end = replay
    rep = report(db, bars, end)
    decididos = sum(
        n
        for acao in rep.outcomes.values()
        for desfecho, n in acao.items()
        if desfecho in ("alvo com lucro", "alvo sem lucro", "stop primeiro")
    )
    assert sum(n for _, n, _ in rep.calibration) == decididos
    assert all(0 <= pct <= 100 for _, _, pct in rep.calibration)


def test_compara_com_a_expectativa_do_backtest_quando_ha_relatorio(replay, tmp_path):
    db, bars, _, end = replay
    bt = {"ETHBRL": {"padrao": {"metricas": {"expectativa_pct": -0.28, "operacoes": 13}}}}
    rep = report(db, bars, end, backtest_json=bt)
    assert rep.backtest_expectancy_pct == -0.28 and rep.backtest_trades == 13
    assert report(db, bars, end, backtest_json=bt, trade_symbol="ETHUSDT").backtest_expectancy_pct is None
    assert load_backtest_json(tmp_path / "nao_existe.json") is None
    (tmp_path / "bt.json").write_text('{"ETHBRL": {}}', encoding="utf-8")
    assert load_backtest_json(tmp_path / "bt.json") == {"ETHBRL": {}}


def test_variante_sem_nada_no_banco_comeca_do_capital_informado(replay):
    db, bars, _, end = replay
    conn = sim_db.connect(db)
    try:
        rep = variant_report(conn, "estudo", 500.0, bars, 4, 2, None, "ETHBRL", now=end)
    finally:
        conn.close()
    assert rep.equity == 500.0 and rep.metrics["operacoes"] == 0 and rep.signals_by_action == {}


def test_aprendizado_le_as_operacoes_virtuais_so_depois_do_corte(replay, tmp_path):
    from cryptoagent.learning.cycle import simulation_rows

    db, _, start, _ = replay
    rows, pnls = simulation_rows(db, after_ms=0)
    assert len(pnls) == 2 and len(rows) == 2  # as duas operações fechadas, com as características gravadas na entrada
    assert (rows["t_ms"] > start).all()
    corte = int(rows["t_ms"].min())
    depois, todas = simulation_rows(db, after_ms=corte)
    assert len(depois) == 1 and len(todas) == 2  # os resultados % continuam todos (para o drift)
    assert simulation_rows(tmp_path / "nao_existe.sqlite", 0)[0].empty
    vazio, sem_pnls = simulation_rows(None, 0)
    assert vazio.empty and sem_pnls == []
