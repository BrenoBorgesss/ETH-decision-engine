"""Walk-forward do backtest: a escolha dos parâmetros usa só o mês de treino.

O simulador é trocado por um falso que devolve operações com resultado conhecido em cada mês,
para conferir a regra de escolha sem depender do mercado.
"""

import itertools
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from cryptoagent.backtest import runner
from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.backtest.simulator import SimResult, SimStats, Trade
from cryptoagent.config import Config
from cryptoagent.seasonality.validation import make_folds
from cryptoagent.timeutils import UTC, datetime_to_ms

H = 3_600_000
START = datetime_to_ms(datetime(2026, 1, 1, tzinfo=UTC))
END = datetime_to_ms(datetime(2026, 5, 2, tzinfo=UTC))  # o mês de teste precisa estar completo
COSTS = CostSchedule("ETHBRL", 0.001, dict.fromkeys(range(24), 0.04), [], 0.01, 0.05, "teste")

# resultado por operação (%) e número de operações, por combinação e por mês (0 = jan ... 3 = abr)
PLANO = {
    1.5: {0: (0.5, 25), 1: (-0.2, 25), 2: (0.3, 25), 3: (0.4, 25)},
    2.5: {0: (0.1, 25), 1: (-0.1, 25), 2: (0.9, 5), 3: (-0.3, 25)},
}


def fake_data():
    t = np.arange(START, END, H)
    frame = pd.DataFrame({"open_time": t, "close_time": t + H - 1})
    return SimpleNamespace(
        frames={"5m": frame},
        first_valid_ms=START,
        trade="ETHBRL",
        t_open_time=t,
        t_close=np.linspace(12_000, 13_000, len(t)),
    )


def month_windows():
    edges = [datetime_to_ms(datetime(2026, m, 1, tzinfo=UTC)) for m in range(1, 6)]
    return list(itertools.pairwise(edges))


def trades_for(dist):
    out = []
    for month, (a, b) in enumerate(month_windows()):
        pnl_pct, n = PLANO[dist][month]
        for k in range(n):
            t = a + (k + 1) * (b - a) // (n + 2)
            pnl = 100 * pnl_pct / 100
            out.append(
                Trade(t, t + H, 12_000, 12_000, 0.01, 100, pnl, pnl_pct, pnl / 1000, "alvo1", 60, "lateral", 0, 0, 0.2)
            )
    return out


@pytest.fixture
def cfg():
    c = Config.model_validate({})
    c.backtest.meses_treino, c.backtest.meses_teste = 1, 1
    c.backtest.grade = {"distancia_alvo_min_custo": [1.5, 2.5]}
    c.backtest.operacoes_min_treino = 20
    c.backtest.expectativa_min_treino_pct = 0.0
    c.backtest.distancias_alternativas = []
    return c


@pytest.fixture
def chamadas(monkeypatch):
    calls = []

    def fake_simulate(data, p, ind, costs, *, start_ms=None, end_ms=None, season_on=True, label="", **kw):
        calls.append({"label": label, "start_ms": start_ms, "season_on": season_on, "sinais": list(p.sinais_ativos)})
        grade_run = label.startswith("{")
        trades = trades_for(p.distancia_alvo_min_custo) if grade_run else []
        return SimResult(label, trades, SimStats(), start_ms or START, end_ms or END, 1000)

    monkeypatch.setattr(runner, "simulate", fake_simulate)
    monkeypatch.setattr(
        runner, "naive_band", lambda *a, **k: SimResult("faixa ingênua", [], SimStats(), START, END, 1000)
    )
    return calls


def test_escolhe_pelo_treino_pula_mes_sem_ganho_e_ignora_combinacao_com_poucas_operacoes(cfg, chamadas):
    folds = make_folds(fake_data().frames["5m"]["open_time"], 1, 1)
    assert len(folds) == 3  # treino jan -> teste fev; fev -> mar; mar -> abr
    rep = runner.run_pair(cfg, fake_data(), COSTS, progress=lambda _: None)
    c1, c2, c3 = rep.wf_choices

    # fev: 1,5 ganhou mais no treino de jan (+0,5% x +0,1%), mesmo sendo PIOR no próprio fev (-0,2% x -0,1%)
    assert c1["escolha"] == {"distancia_alvo_min_custo": 1.5}
    assert c1["treino_ops"] == 25 and c1["treino_expectativa_pct"] == pytest.approx(0.5)
    assert c1["teste_ops"] == 25 and c1["teste_expectativa_pct"] == pytest.approx(-0.2)

    # mar: no treino (fev) as duas perderam; a melhor foi -0,1% <= 0 -> mês sem operar
    assert c2["escolha"].startswith("nenhuma combinação ganhou no treino") and c2["teste_ops"] == 0
    assert c2["treino_expectativa_pct"] == pytest.approx(-0.1)

    # abr: 2,5 tinha +0,9% em mar, mas só 5 operações (< 20): não pode ser escolhida
    assert c3["escolha"] == {"distancia_alvo_min_custo": 1.5} and c3["treino_ops"] == 25

    assert rep.walkforward.metrics["operacoes"] == 50  # 25 em fev + 25 em abr; março ficou parado
    assert rep.walkforward.metrics["expectativa_pct"] == pytest.approx((-0.2 + 0.4) / 2)
    assert len([c for c in chamadas if c["label"].startswith("{")]) == 2  # cada combinação simulada uma vez


def test_sem_operacoes_suficientes_no_treino_nenhum_mes_opera(cfg, chamadas):
    cfg.backtest.operacoes_min_treino = 1000
    rep = runner.run_pair(cfg, fake_data(), COSTS, progress=lambda _: None)
    assert all(c["escolha"].startswith("nenhuma combinação com operações suficientes") for c in rep.wf_choices)
    assert rep.walkforward.metrics["operacoes"] == 0


def test_ablacao_testa_cada_sinal_extra_e_o_filtro_de_horario_so_fora_da_amostra(cfg, chamadas):
    rep = runner.run_pair(cfg, fake_data(), COSTS, progress=lambda _: None)
    base = list(cfg.estrategia.sinais_ativos)
    extras = [e for e in runner.EXTRAS_TESTABLE if e not in base]
    nomes = [v.name for v in rep.ablation]
    assert nomes[0].startswith("conjunto mínimo")
    assert nomes[1:] == [f"+ {e}" for e in extras] + ["+ todos os extras testáveis", "sem o filtro de horário"]
    ablacoes = [c for c in chamadas if c["label"] in nomes]
    assert all(c["start_ms"] == rep.oos_start for c in ablacoes)  # só no período fora da amostra
    assert [c["season_on"] for c in ablacoes][-1] is False
    assert rep.untestable == runner.EXTRAS_UNTESTABLE


def test_modo_rapido_pula_grade_e_ablacao(cfg, chamadas):
    rep = runner.run_pair(cfg, fake_data(), COSTS, quick=True, progress=lambda _: None)
    assert rep.wf_choices == [] and rep.ablation == [] and rep.walkforward is None
    assert [c["label"] for c in chamadas] == ["padrão"]


def test_historico_curto_demais_e_recusado(cfg, chamadas):
    d = fake_data()
    d.frames["5m"] = d.frames["5m"].iloc[: 24 * 20]  # 20 dias
    with pytest.raises(ValueError, match="histórico curto demais"):
        runner.run_pair(cfg, d, COSTS, progress=lambda _: None)
