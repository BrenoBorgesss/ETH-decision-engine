"""Motor de decisão: cada regra testada isoladamente, com cenários montados à mão."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from cryptoagent import DISCLAIMER
from cryptoagent.config import Estrategia
from cryptoagent.engine.context import DecisionContext, MarketView, PositionView, RiskView, SeasonView, closed_rows
from cryptoagent.engine.decision import decide
from cryptoagent.engine.signals import ramp
from cryptoagent.indicators.structure import Level

P = Estrategia()
T = 1_800_000_000_000
MIN = 60_000


def frame(n, **cols):
    """DataFrame com n candles; colunas constantes ou listas (a última posição é o candle mais recente)."""
    base = {"close_time": [T - (n - i) * MIN for i in range(n)]}
    for k, v in cols.items():
        base[k] = v if isinstance(v, list) else [v] * n
    return pd.DataFrame(base)


def good_frames(price=100.0):
    """Cenário de compra 'de livro': esticado para baixo, gatilho armado, maiores favoráveis."""
    h4 = frame(3, close=105.0, ema_50=104.0, ema_200=100.0, ema_50_slope=0.1)
    h1 = frame(3, close=102.0, ema_50=101.0, adx=15.0, plus_di=22.0, minus_di=18.0, structure="mista", regime="lateral")
    s15 = frame(
        10,
        close=price,
        vwap=103.0,
        atr=1.0,
        dist_vwap_atr=-3.0,
        bb_pctb=-0.2,
        bb_mid=102.0,
        bb_upper=104.0,
        rsi=22.0,
        regime="lateral",
        bull_div=False,
    )
    g5 = frame(
        14,
        close=[100.5] * 12 + [99.0, 99.6],
        low=[99.5] * 12 + [98.6, 99.2],
        bb_pctb=[0.3] * 12 + [-0.1, 0.05],
        bb_lower=99.3,
        atr=0.5,
        rsi=35.0,
        stochrsi_k=10.0,
        stochrsi_d=12.0,
        bull_rejection=False,
        taker_sell_ratio=0.5,
        sell_vol_vs_peak=0.9,
    )
    m1 = frame(15, low=99.0, close=99.6)
    return {"4h": h4, "1h": h1, "15m": s15, "5m": g5, "1m": m1}


def ctx(frames=None, *, blocked=(), position=None, season=1.0, cost=0.2, warnings=(), price=99.6, levels=()):
    market = MarketView(T, price, frames or good_frames(), list(levels), spread_pct=0.01)
    risk = RiskView(list(blocked), risk_per_trade_brl=10.0, capital_brl=1000.0, data_warnings=list(warnings))
    return DecisionContext(
        "ETHUSDT", "ETHBRL", 5.0, market, risk, cost, position, SeasonView(season, ["horário x"] if season != 1 else [])
    )


def test_cenario_de_livro_gera_comprar_com_niveis_coerentes():
    d = decide(ctx(), P)
    assert d.action == "COMPRAR", d.blocks
    lv = d.levels
    assert lv.stop < lv.entry < lv.target1 <= lv.target2
    assert lv.target1 == 102.0  # média mais próxima acima: meio das bandas (102) antes do VWAP (103)
    assert lv.rr >= P.rr_minimo
    # tamanho: no stop, perde exatamente o risco por operação (R$ 10)
    assert lv.size_brl * lv.risk_net_pct / 100 == pytest.approx(10.0)
    assert d.disclaimer == DISCLAIMER
    assert 3 <= len(d.reasons_for) + len(d.reasons_against)


def test_confianca_e_o_produto_das_parcelas():
    d = decide(ctx(season=0.8), P)
    expected = 100 * np.prod([p.value for p in d.parts])
    assert d.confidence == pytest.approx(expected)
    assert [p.name for p in d.parts] == ["sinais", "timeframes maiores", "qualidade dos dados", "horário"]
    # vwap −3 ATR = 1; %B −0,2 = 1; RSI 22 = (40 − 22) ÷ (40 − 20) = 0,9 -> média 0,967
    assert d.parts[0].value == pytest.approx((1 + 1 + 0.9) / 3)
    assert d.confidence == pytest.approx(100 * (2.9 / 3) * 1.0 * 1.0 * 0.8)


def test_qualquer_trava_da_conta_vira_esperar():
    d = decide(ctx(blocked=["PERDA MÁXIMA DO DIA ATINGIDA"]), P)
    assert d.action == "ESPERAR" and "PERDA MÁXIMA DO DIA ATINGIDA" in d.blocks
    assert d.parts and d.levels is not None  # a leitura técnica continua visível, só sem efeito


def test_dado_faltando_vira_esperar():
    f = good_frames()
    f["15m"].loc[f["15m"].index[-1], "rsi"] = np.nan
    d = decide(ctx(f), P)
    assert d.action == "ESPERAR" and any("indefinidos" in b for b in d.blocks)
    f = good_frames()
    del f["1h"]
    assert decide(ctx(f), P).action == "ESPERAR"


def test_incerteza_alta_vira_esperar():
    d = decide(ctx(warnings=["a", "b", "c"]), P)  # qualidade 0,85 < 0,9
    assert d.action == "ESPERAR" and any("incerteza" in b for b in d.blocks)


def test_spread_alto_vira_esperar():
    c = ctx()
    c = replace(c, market=replace(c.market, spread_pct=0.5))
    d = decide(c, P, spread_max_pct=0.15)
    assert d.action == "ESPERAR" and any("spread" in b for b in d.blocks)


def test_queda_forte_no_1h_bloqueia_compra():
    f = good_frames()
    f["1h"] = frame(
        3, close=95.0, ema_50=101.0, adx=40.0, plus_di=10.0, minus_di=35.0, structure="baixa", regime="tendencia_baixa"
    )
    d = decide(ctx(f), P)
    assert d.action == "ESPERAR" and any("queda forte" in b for b in d.blocks)


def test_queda_sustentada_4h_com_estrutura_de_baixa_no_1h_bloqueia():
    f = good_frames()
    f["4h"] = frame(3, close=95.0, ema_50=98.0, ema_200=100.0, ema_50_slope=-0.3)
    f["1h"] = frame(
        3, close=95.0, ema_50=97.0, adx=22.0, plus_di=15.0, minus_di=20.0, structure="baixa", regime="transicao"
    )
    d = decide(ctx(f), P)
    assert any("queda sustentada" in b for b in d.blocks)


def test_sem_gatilho_anti_faca_nao_compra():
    f = good_frames()
    f["5m"].loc[f["5m"].index[-1], "bb_pctb"] = -0.3  # ainda caindo, fora da banda
    f["5m"].loc[f["5m"].index[-1], "close"] = 98.8
    f["1m"]["low"] = list(np.linspace(100, 98, 15))  # sem fundo mais alto no 1m
    d = decide(ctx(f), P)
    assert d.action == "ESPERAR" and any("gatilho" in b for b in d.blocks)


def test_fundo_mais_alto_no_1m_tambem_arma_o_gatilho():
    f = good_frames()
    f["5m"].loc[f["5m"].index[-1], "bb_pctb"] = -0.05  # 5m ainda não voltou para dentro
    f["1m"]["low"] = [98.0] * 12 + [98.5, 98.6, 98.7]
    d = decide(ctx(f), P)
    assert d.trigger.ok and "fundo mais alto" in d.trigger.detail


def test_trava_de_distancia_minima_ate_o_alvo():
    d = decide(ctx(cost=1.0), P)  # custo alto: alvo a 2,4% < 2,5 × 1%
    assert d.action == "ESPERAR" and any("mínimo" in b and "custo" in b for b in d.blocks)


def test_resistencia_antes_da_media_vira_alvo_1():
    d = decide(ctx(levels=[Level(100.8, 3, 0, "pivos")]), P)
    assert d.levels.target1 == 100.8 and "resistência" in d.levels.target1_source


def test_regime_de_alta_volatilidade_bloqueia():
    f = good_frames()
    f["15m"]["regime"] = "alta_volatilidade"
    assert decide(ctx(f), P).action == "ESPERAR"


def test_tendencia_de_baixa_no_15m_exige_confianca_maior():
    f = good_frames()
    f["15m"]["regime"] = "tendencia_baixa"
    d = decide(ctx(f, season=0.65), P)  # 100 × 1 × 1 × 1 × 0,65 = 65 < 70
    assert d.action == "ESPERAR" and any("abaixo do mínimo" in b for b in d.blocks)
    f["15m"]["regime"] = "lateral"
    assert decide(ctx(f, season=0.65), P).action == "COMPRAR"  # 65 >= 55


def test_sem_esticamento_nao_compra():
    f = good_frames()
    f["15m"]["dist_vwap_atr"] = 0.2
    f["15m"]["bb_pctb"] = 0.6
    f["15m"]["rsi"] = 55.0
    d = decide(ctx(f), P)
    assert d.action == "ESPERAR" and d.parts[0].value == 0


def pos(**kw):
    base = dict(
        qty=0.1, avg_cost=99.0, opened_ms=T - 30 * MIN, stop=98.0, target1=102.0, target2=104.0, source="sinal #1"
    )
    base.update(kw)
    return PositionView(**base)


def test_com_posicao_stop_atingido_vender():
    d = decide(ctx(position=pos(), price=97.9), P)
    assert d.action == "VENDER" and d.exit_reason == "stop" and d.confidence == 100


def test_com_posicao_alvo_atingido_vender():
    d = decide(ctx(position=pos(), price=102.1), P)
    assert d.action == "VENDER" and d.exit_reason == "alvo1"


def test_com_posicao_tempo_maximo_vender():
    d = decide(ctx(position=pos(opened_ms=T - 200 * MIN), price=100.0), P)
    assert d.action == "VENDER" and d.exit_reason == "tempo"


def test_com_posicao_esticado_para_cima_vender():
    f = good_frames()
    f["15m"]["bb_pctb"] = 1.1
    f["15m"]["dist_vwap_atr"] = 2.5
    f["5m"]["rsi"] = 78.0
    d = decide(ctx(f, position=pos(target1=None, target2=None), price=101.0), P)
    assert d.action == "VENDER" and d.exit_reason == "esticado_cima"


def test_com_posicao_invalidacao_por_queda_forte_vender():
    f = good_frames()
    f["1h"] = frame(
        3, close=95.0, ema_50=101.0, adx=40.0, plus_di=10.0, minus_di=35.0, structure="baixa", regime="tendencia_baixa"
    )
    d = decide(ctx(f, position=pos(), price=99.0), P)
    assert d.action == "VENDER" and d.exit_reason == "invalidacao"


def test_com_posicao_sem_regra_de_saida_espera_e_mantem():
    d = decide(ctx(position=pos(), price=100.0), P)
    assert d.action == "ESPERAR" and "manter" in d.reasons_for[0]
    assert "stop 490,00" in d.position_note  # textos no par de operação: 98 × razão 5


def limit_ctx(**kw):
    c = ctx(**kw)
    return replace(c, risk=replace(c.risk, daily_limit_reason="PERDA MÁXIMA DO DIA ATINGIDA"))


def test_perda_maxima_sem_posicao_bloqueia_compra():
    d = decide(limit_ctx(), P)
    assert d.action == "ESPERAR" and "PERDA MÁXIMA DO DIA ATINGIDA" in d.blocks


def test_perda_maxima_com_posicao_permite_vender_no_stop():
    d = decide(limit_ctx(position=pos(), price=97.0), P)
    assert d.action == "VENDER" and d.exit_reason == "stop"
    assert "COMPRAR bloqueado até o próximo dia" in d.blocks


def test_perda_maxima_com_posicao_permite_vender_por_tempo_e_invalidacao():
    assert decide(limit_ctx(position=pos(opened_ms=T - 200 * MIN), price=100.0), P).exit_reason == "tempo"
    f = good_frames()
    f["1h"] = frame(
        3, close=95.0, ema_50=101.0, adx=40.0, plus_di=10.0, minus_di=35.0, structure="baixa", regime="tendencia_baixa"
    )
    assert decide(limit_ctx(frames=f, position=pos(), price=99.0), P).exit_reason == "invalidacao"


def test_perda_maxima_com_posicao_no_alvo_fica_em_esperar():
    # alvo não está na lista de saídas que reduzem risco: só ESPERAR (a OCO na Binance cuida do alvo)
    d = decide(limit_ctx(position=pos(), price=102.5), P)
    assert d.action == "ESPERAR" and "PERDA MÁXIMA DO DIA ATINGIDA" in d.blocks


def test_perda_maxima_com_dado_velho_fica_em_esperar_mesmo_no_stop():
    d = decide(limit_ctx(position=pos(), price=97.0, blocked=["candles 5m: candle atrasado"]), P)
    assert d.action == "ESPERAR"


def test_posicao_sem_sinal_usa_niveis_das_regras():
    d = decide(ctx(position=pos(stop=None, target1=None, target2=None, source="regras"), price=100.0), P)
    assert "stop 490,00" in d.position_note  # (99 − 2 × ATR(5m) 0,5) × razão 5
    assert "alvo 1 510,00" in d.position_note  # meio das bandas 102 × 5


def test_rampa():
    assert ramp(1.0, 1.0, 3.0) == 0 and ramp(3.0, 1.0, 3.0) == 1 and ramp(2.0, 1.0, 3.0) == 0.5
    assert ramp(30, 40, 20) == 0.5 and ramp(50, 40, 20) == 0 and ramp(np.nan, 0, 1) == 0


def test_closed_rows_nao_inclui_candle_aberto():
    f = pd.DataFrame({"close_time": [10, 20, 30, 40]})
    assert list(closed_rows(f, 30, 10)["close_time"]) == [10, 20]  # o de 30 ainda não fechou "antes" de 30
    assert list(closed_rows(f, 31, 2)["close_time"]) == [20, 30]


def test_sinais_extras_entram_quando_ativados():
    p = Estrategia(sinais_ativos=["vwap", "bollinger", "rsi", "stoch_rsi", "order_book"])
    d = decide(ctx(), p)
    names = {s.name: s for s in d.signals}
    assert set(names) == {"vwap", "bollinger", "rsi", "stoch_rsi", "order_book"}
    assert not names["order_book"].available  # sem livro (como no backtest): não conta na média
    assert d.parts[0].value < 1  # stoch_rsi sem cruzamento puxa a nota para baixo


def test_config_recusa_sinal_desconhecido():
    with pytest.raises(ValueError):
        Estrategia(sinais_ativos=["vwap", "bola_de_cristal"])


def test_com_posicao_os_suportes_e_resistencias_nao_mudam_a_decisao():
    """B2: o backtest passa a lista de níveis vazia quando há posição; o ao vivo passa os níveis.
    Isso só é equivalente se nenhuma regra de saída usar os níveis. Este teste garante que não usa."""
    niveis = [Level(98.5, 3, 5, "pivos"), Level(100.8, 2, 6, "pivos"), Level(101.5, 1, 7, "dia_anterior_maxima")]
    cenarios = [
        dict(position=pos(), price=97.9),
        dict(position=pos(), price=102.1),
        dict(position=pos(opened_ms=T - 200 * MIN), price=100.0),
        dict(position=pos(), price=100.2),
        dict(position=pos(stop=None, target1=None, target2=None), price=100.2),
    ]
    for c in cenarios:
        sem, com = decide(ctx(**c), P), decide(ctx(levels=niveis, **c), P)
        assert (sem.action, sem.exit_reason, sem.confidence, sem.blocks) == (
            com.action,
            com.exit_reason,
            com.confidence,
            com.blocks,
        )
