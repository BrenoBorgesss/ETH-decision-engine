"""Regra de confiança (6.4): compra firme só com confiança acima do mínimo E estratégia validada no backtest."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from cryptoagent.backtest.provenance import parameters, params_hash
from cryptoagent.config import load_config
from cryptoagent.engine.context import RiskView
from cryptoagent.engine.decision import decide
from cryptoagent.engine.reliability import PREFIX, check_reliability
from test_engine import P, ctx, pos

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000_000
DAY = 86_400_000


def cfg_with_report(tmp_path, metricas=None, *, same_params=True, age_days=1, pair="ETHBRL"):
    cfg = load_config(ROOT / "config" / "config.yaml")
    cfg.backtest.relatorio_pasta = str(tmp_path)
    if metricas is not None:
        (tmp_path / "backtest.json").write_text(json.dumps({pair: {"padrao": {"metricas": metricas}}}), encoding="utf-8")
        h = params_hash(parameters(cfg)) if same_params else "outro"
        (tmp_path / "backtest.meta.json").write_text(json.dumps({"parametros_sha256": h, "gerado_em_ms": NOW - age_days * DAY}), encoding="utf-8")
    return cfg


BOM = {"operacoes": 45, "expectativa_pct": 0.21, "fator_lucro": 1.4, "expectativa_ic95_pct": [0.03, 0.39]}
ATUAL = {"operacoes": 13, "expectativa_pct": -0.28, "fator_lucro": 0.36, "expectativa_ic95_pct": [-0.63, 0.07]}  # resultado real do ETHBRL


def test_estrategia_validada_libera_a_compra(tmp_path):
    r = check_reliability(cfg_with_report(tmp_path, BOM), "ETHBRL", NOW)
    assert r.ok and r.block_reason is None


def test_resultado_atual_do_ethbrl_nao_passa_e_explica_cada_criterio(tmp_path):
    r = check_reliability(cfg_with_report(tmp_path, ATUAL), "ETHBRL", NOW)
    assert not r.ok and len(r.reasons) == 4
    assert r.block_reason.startswith(PREFIX)
    assert "13 operações" in r.block_reason and "fator de lucro" in r.block_reason and "intervalo de 95%" in r.block_reason


@pytest.mark.parametrize(
    "mudanca, trecho",
    [
        ({"operacoes": 29}, "29 operações"),
        ({"expectativa_pct": 0.0}, "média por operação"),
        ({"fator_lucro": 0.99}, "fator de lucro"),
        ({"expectativa_ic95_pct": [-0.01, 0.4]}, "intervalo de 95%"),
    ],
)
def test_cada_criterio_sozinho_reprova(tmp_path, mudanca, trecho):
    r = check_reliability(cfg_with_report(tmp_path, {**BOM, **mudanca}), "ETHBRL", NOW)
    assert not r.ok and len(r.reasons) == 1 and trecho in r.reasons[0]


def test_sem_relatorio_parametros_trocados_ou_relatorio_velho_reprovam(tmp_path):
    assert "não há backtest" in check_reliability(cfg_with_report(tmp_path), "ETHBRL", NOW).reasons[0]
    (tmp_path / "b").mkdir()
    assert "outros parâmetros" in check_reliability(cfg_with_report(tmp_path / "b", BOM, same_params=False), "ETHBRL", NOW).reasons[0]
    (tmp_path / "c").mkdir()
    assert "dias" in check_reliability(cfg_with_report(tmp_path / "c", BOM, age_days=60), "ETHBRL", NOW).reasons[0]
    (tmp_path / "d").mkdir()
    assert "não há backtest de ETHUSDT" in check_reliability(cfg_with_report(tmp_path / "d", BOM), "ETHUSDT", NOW).reasons[0]


def test_criterio_desligavel(tmp_path):
    cfg = cfg_with_report(tmp_path)
    cfg.confiabilidade.exigir_validacao = False
    assert check_reliability(cfg, "ETHBRL", NOW).ok


def test_estrategia_nao_validada_transforma_compra_em_aguardar_mas_nao_bloqueia_venda():
    motivo = f"{PREFIX}: 13 operações fora da amostra (mínimo 30)"
    compra = ctx()
    assert decide(compra, P).action == "COMPRAR"
    travada = replace(compra, risk=replace(compra.risk, validation_reason=motivo))
    d = decide(travada, P)
    assert d.action == "ESPERAR" and motivo in d.blocks and d.strategy_gates.get("validacao") == motivo
    assert d.levels is not None  # a leitura técnica continua visível
    venda = ctx(position=pos(), price=97.9)
    venda = replace(venda, risk=replace(venda.risk, validation_reason=motivo))
    assert decide(venda, P).action == "VENDER"


def test_limiar_de_confianca_e_configuravel():
    d = decide(ctx(), P)
    assert d.action == "COMPRAR"
    exigente = P.model_copy(update={"confianca_minima": {k: d.confidence + 1 for k in P.confianca_minima}})
    alto = decide(ctx(), exigente)
    assert alto.action == "ESPERAR" and "confianca" in alto.strategy_gates
    no_limite = P.model_copy(update={"confianca_minima": {k: d.confidence for k in P.confianca_minima}})
    assert decide(ctx(), no_limite).action == "COMPRAR"


def test_riskview_padrao_nao_trava():
    assert RiskView([], None, None).validation_reason is None
