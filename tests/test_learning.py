"""Aprendizado contínuo: critérios de promoção, registro de versões, drift, panoramas, ciclo e uso ao vivo."""

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.config import DEMO_RELATIVE, Aprendizado, load_config
from cryptoagent.engine.context import SeasonView
from cryptoagent.engine.decision import decide
from cryptoagent.learning import panorama as pano
from cryptoagent.learning import registry as regmod
from cryptoagent.learning.criteria import judge
from cryptoagent.learning.drift import psi_categorical, psi_numeric, simulation_drift
from cryptoagent.learning.state import LearningState, apply_model_champion, clear_cache, load_state
from cryptoagent.model.features import FEATURES
from cryptoagent.model.logistic import LogisticModel
from test_engine import P, ctx, pos

ROOT = Path(__file__).resolve().parents[1]
COSTS = CostSchedule("ETHBRL", 0.001, {h: 0.04 for h in range(24)}, [], 0.01, 0.05, "teste")
A = Aprendizado()

CAMPEAO = {"operacoes": 40, "expectativa_pct": 0.05, "retorno_total_pct": 2.0, "drawdown_max_pct": -3.0}
BOM = {"operacoes": 45, "expectativa_pct": 0.20, "retorno_total_pct": 6.0, "drawdown_max_pct": -2.5}


# ------------------------------------------------------------------ critérios


def test_desafiante_bom_e_promovido():
    v = judge(BOM, CAMPEAO, 120, A, drift=False)
    assert v.promote and not v.failed and len(v.passed) == 7


@pytest.mark.parametrize(
    "mudanca, dias, drift, trecho",
    [
        ({"operacoes": 29}, 120, False, "operações"),
        ({}, 59, False, "dias"),
        ({"expectativa_pct": 0.09}, 120, False, "melhoria mínima"),
        ({"retorno_total_pct": 1.9}, 120, False, "retorno"),
        ({"drawdown_max_pct": -3.01}, 120, False, "drawdown"),
        ({}, 120, True, "mudança de regime"),
    ],
)
def test_cada_criterio_sozinho_impede_a_promocao(mudanca, dias, drift, trecho):
    v = judge({**BOM, **mudanca}, CAMPEAO, dias, A, drift=drift)
    assert not v.promote and len(v.failed) == 1 and trecho in v.failed[0]


def test_menos_ruim_mas_ainda_no_prejuizo_nao_e_promovido():
    campeao = {"operacoes": 40, "expectativa_pct": -0.30, "retorno_total_pct": -5.0, "drawdown_max_pct": -6.0}
    menos_ruim = {"operacoes": 40, "expectativa_pct": -0.05, "retorno_total_pct": -1.0, "drawdown_max_pct": -2.0}
    v = judge(menos_ruim, campeao, 120, A, drift=False)
    assert not v.promote and any("precisa passar de" in f for f in v.failed)


# ------------------------------------------------------------------ registro


def test_registro_guarda_versoes_avaliacoes_e_troca_de_campeao(tmp_path):
    reg = regmod.Registry()
    v1 = reg.add("regras", "regras", None, 1, state="campeao")
    v2 = reg.add("modelo", "modelo", "modelo_logistico", 2)
    v2.evaluations.append(regmod.Evaluation(1, 3, 3, 0, 3, {"expectativa_pct": float("nan"), "operacoes": 3}, "p", "d", "rejeitado", ["x"]))
    old = reg.promote(v2, 5)
    assert old is v1 and v1.state == "aposentado" and reg.champion is v2 and v2.promoted_ms == 5
    regmod.save(tmp_path, reg)
    back = regmod.load(tmp_path)
    assert back.champion.id == 2 and back.versions[1].last.metrics["expectativa_pct"] is None  # NaN vira null no JSON
    assert back.find("modelo", "modelo_logistico").id == 2


# ------------------------------------------------------------------ drift


def test_psi_estavel_e_mudanca_forte():
    rng = np.random.default_rng(3)
    ref = rng.normal(0, 1, 3000)
    assert psi_numeric(ref, rng.normal(0, 1, 1000)) < 0.05
    assert psi_numeric(ref, rng.normal(1.5, 1, 1000)) > 0.25
    assert psi_numeric(ref, ref[:10]) is None  # amostra pequena: não compara
    a = pd.Series(["lateral"] * 80 + ["tendencia_alta"] * 20)
    b = pd.Series(["lateral"] * 20 + ["tendencia_alta"] * 80)
    assert psi_categorical(a, a) == pytest.approx(0) and psi_categorical(a, b) > 0.25


def test_psi_desconta_o_acaso_das_amostras_pequenas():
    rng = np.random.default_rng(7)
    valores = [psi_numeric(rng.normal(0, 1, 364), rng.normal(0, 1, 51)) for _ in range(200)]
    assert np.mean(valores) < 0.10  # mesma distribuição: perto de zero depois do desconto
    assert psi_numeric(rng.normal(0, 1, 364), rng.normal(1.5, 1, 51)) > 0.25  # mudança real continua detectada


def test_drift_so_alerta_quando_o_ultimo_periodo_e_incomum():
    from cryptoagent.learning.drift import candidate_drift

    rng = np.random.default_rng(11)
    feats = [f"x{i}" for i in range(15)]
    day = 86_400_000
    t = np.sort(rng.integers(0, 180 * day, 600))
    base = pd.DataFrame({"t_ms": t, **{f: rng.normal(0, 1, len(t)) for f in feats}})
    falsos = sum(candidate_drift(base.assign(**{f: rng.normal(0, 1, len(t)) for f in feats}), feats, 180 * day, 30, 150, 0.25).alert for _ in range(10))
    assert falsos <= 1  # nada mudou: no máximo um alarme falso em 10
    mudou = base.copy()
    mudou.loc[mudou["t_ms"] >= 150 * day, "x3"] += 2.0
    item = candidate_drift(mudou, feats, 180 * day, 30, 150, 0.25)
    assert item.alert and "x3" in item.detail and "incomum" in item.detail


def test_simulacao_muito_abaixo_do_backtest_e_alerta():
    assert simulation_drift([-0.5] * 9, [0.0, 0.2]).value is None  # menos de 10 operações
    item = simulation_drift([-0.6, -0.5, -0.7, -0.4, -0.55, -0.65, -0.5, -0.6, -0.45, -0.5], [0.01, 0.30])
    assert item.alert and "simulação" in item.detail
    assert not simulation_drift([0.1, -0.1] * 6, [-0.2, 0.3]).alert


# ------------------------------------------------------------------ panoramas


def test_wilson_e_estatisticas_de_um_grupo():
    lo, hi = pano.wilson(30, 100)
    assert lo == pytest.approx(0.2189, abs=1e-3) and hi == pytest.approx(0.3958, abs=1e-3)
    assert all(math.isnan(x) for x in pano.wilson(0, 0))
    g = pano.group_stats(pd.DataFrame({"pnl_pct": [1.0, -0.5, -0.5, 2.0]}))
    assert g["casos"] == 4 and g["acerto"] == 0.5 and g["ganho_medio_pct"] == 1.5 and g["perda_media_pct"] == -0.5 and g["media_pct"] == 0.5


def test_cenario_inedito_reduz_a_confianca_aos_poucos():
    pan = {"cenarios": {pano.scenario_key("lateral", 10, 50): {"casos": 15}}}
    assert pano.moment(pan, "lateral", 10.5, 50, min_cases=30, floor=0.7).factor == pytest.approx(0.85)
    assert pano.moment(pan, "lateral", 20, 50, min_cases=30, floor=0.7).factor == pytest.approx(0.7)  # noite: nenhum caso
    pan["cenarios"][pano.scenario_key("lateral", 10, 50)]["casos"] = 30
    assert pano.moment(pan, "lateral", 10, 50, min_cases=30, floor=0.7).factor == 1
    assert pano.moment(None, "lateral", 10, 50, min_cases=30, floor=0.7).factor == 1  # sem ciclo: nada muda
    assert pano.scenario_key("lateral", 5.9, 80) == "lateral | madrugada | volatilidade alta"


def test_fator_de_cenario_entra_na_confianca():
    base = decide(ctx(), P)
    novo = ctx()
    novo = replace(novo, novelty=SeasonView(0.7, ["cenário pouco visto"]))
    d = decide(novo, P)
    assert d.parts[-1].name == "cenário" and d.confidence == pytest.approx(base.confidence * 0.7)


def test_svg_de_calibracao_tem_os_pontos():
    svg = pano.calibration_svg([(0.3, 0.25, 0.2, 0.3, 50), (0.7, 0.4, 0.3, 0.5, 20)], "teste")
    assert svg.startswith("<svg") and svg.count("<circle") == 2


# ------------------------------------------------------------------ campeão ao vivo


def fake_model(intercept):
    n = len(FEATURES)
    return LogisticModel(list(FEATURES), [0.0] * n, [1.0] * n, [0.0] * n, intercept, 0, 0, 100, 0.3, 42, 0.5)


def test_modelo_campeao_decide_a_compra_nos_momentos_candidatos():
    cfg = load_config()
    c = ctx()
    d = decide(c, P)
    assert d.action == "COMPRAR"
    alto = LearningState(None, fake_model(5.0), None, "")
    baixo = LearningState(None, fake_model(-5.0), None, "")
    assert apply_model_champion(d, c, cfg, alto).action == "COMPRAR"
    d_baixo = apply_model_champion(decide(c, P), c, cfg, baixo)
    assert d_baixo.action == "ESPERAR" and "modelo" in d_baixo.strategy_gates
    # estratégia em teste continua valendo mesmo com o modelo confiante
    travada = replace(c, risk=replace(c.risk, validation_reason="AGUARDAR (estratégia em teste): x"))
    assert apply_model_champion(decide(travada, P), travada, cfg, alto).action == "ESPERAR"
    # com posição aberta, o modelo não mexe
    venda = ctx(position=pos(), price=97.9)
    assert apply_model_champion(decide(venda, P), venda, cfg, alto).action == "VENDER"


# ------------------------------------------------------------------ ciclo completo (amostra do repositório)


@pytest.fixture
def demo(tmp_path):
    cfg = load_config(ROOT / "config" / "config.yaml", ROOT / DEMO_RELATIVE)
    cfg.aprendizado.pasta = str(tmp_path / "learning")
    clear_cache()
    yield cfg
    clear_cache()


def test_ciclo_avalia_campeao_e_desafiante_e_grava_tudo(demo):
    from cryptoagent.learning.cycle import run_cycle

    until = 1_790_900_000_000  # 01/10/2026: fim da amostra
    r = run_cycle(demo, COSTS, until_ms=until, clock_ms=until, progress=lambda m: None)
    assert r.number == 1 and not r.promoted
    names = {e.version.id: e.version.last.decision for e in r.entries}
    assert names[1] == "campeão (referência)" and names[2] in ("rejeitado", "promovido", "aprovado, mas outro desafiante foi melhor")
    folder = Path(demo.aprendizado.pasta)
    assert (folder / "registro.json").is_file() and (folder / "panorama.json").is_file() and (folder / "aprendizado.html").is_file()
    assert list((folder / "modelos").glob("modelo-ciclo001.json"))
    pan = pano.load(folder)
    assert pan["momentos"] == r.dataset_size and pan["calibracao_regras"]
    r2 = run_cycle(demo, COSTS, until_ms=until, clock_ms=until, progress=lambda m: None)
    reg = regmod.load(folder)
    assert r2.number == 2 and len(reg.cycles) == 2 and all(len(v.evaluations) == 2 for v in reg.versions)
    # mesmos dados, mesmo ponto no tempo: mesmas métricas (reprodutível)
    assert reg.versions[0].evaluations[0].metrics == reg.versions[0].evaluations[1].metrics


def test_ciclo_ponto_no_tempo_so_usa_o_passado(demo):
    from cryptoagent.learning.cycle import run_cycle

    # antes de existir um mês de teste completo, não há o que avaliar: erro claro, nada gravado
    with pytest.raises(ValueError, match="curto demais"):
        run_cycle(demo, COSTS, until_ms=1_788_300_000_000, clock_ms=1_788_300_000_000, progress=lambda m: None)
    assert not (Path(demo.aprendizado.pasta) / "registro.json").exists()
    corte = 1_790_812_800_000 + 3_600_000  # 01/10/2026 01:00 UTC: setembro já fechou
    r = run_cycle(demo, COSTS, until_ms=corte, clock_ms=corte, progress=lambda m: None)
    assert r.oos_end <= corte and pano.load(Path(demo.aprendizado.pasta))["ate_ms"] <= corte
    assert all(e.version.last.until_ms <= corte for e in r.entries)


def test_promocao_troca_o_campeao_e_o_ao_vivo_passa_a_usar_o_novo(demo, monkeypatch):
    """Mecânica da promoção, com o veredito forçado (os dados reais não promovem ninguém)."""
    from cryptoagent.learning import cycle
    from cryptoagent.learning.criteria import Verdict

    monkeypatch.setattr(cycle, "judge", lambda *a, **k: Verdict(True, ["forçado no teste"], []))
    until = 1_790_900_000_000
    r = cycle.run_cycle(demo, COSTS, until_ms=until, clock_ms=until, progress=lambda m: None)
    assert r.promoted and r.champion_before.id == 1 and r.champion_after.id == 2
    reg = regmod.load(Path(demo.aprendizado.pasta))
    assert reg.versions[0].state == "aposentado" and reg.champion.id == 2 and reg.champion.last.decision == "promovido"
    state = load_state(demo)
    assert state.champion.id == 2 and state.model is not None and not state.is_default


def test_reavaliacao_automatica_dispara_na_virada_do_dia_e_por_operacoes(tmp_path, monkeypatch):
    import asyncio

    from cryptoagent.paper.runner import _AutoLearning
    from cryptoagent.storage import sim_db

    cfg = load_config()
    cfg.aprendizado.reavaliar_a_cada_operacoes = 2
    conn = sim_db.connect(tmp_path / "s.sqlite")
    started = []
    monkeypatch.setattr(_AutoLearning, "_run", lambda self, trigger: started.append(trigger))

    async def go():
        auto = _AutoLearning(cfg, conn, tmp_path / "s.sqlite", 0.001, lambda m: None)
        day1 = 1_788_300_000_000
        auto.maybe_start(day1)  # primeira vez: só marca o dia
        await asyncio.sleep(0)
        for i in range(2):
            conn.execute("INSERT INTO paper_trades (variant, symbol, decision_ms, decision_price, entry_ms, entry_price, qty, size, fee_in, "
                         "equity_at_entry, checked_until_ms, exit_ms, pnl) VALUES ('padrao','ETHBRL',1,1,1,1,1,1,0,1,1,2,0)")
        conn.commit()
        auto.maybe_start(day1 + 60_000)
        await auto.task
        auto.maybe_start(day1 + 120_000)  # nada novo
        auto.maybe_start(day1 + 86_400_000)  # virou o dia
        await auto.task

    asyncio.run(go())
    from cryptoagent.timeutils import local_trading_day

    assert started == ["2 operações virtuais fechadas", f"fim do dia {local_trading_day(1_788_300_000_000, cfg.fuso.local).isoformat()}"]
