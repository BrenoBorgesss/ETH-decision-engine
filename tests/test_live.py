"""Análise ao vivo (engine/live.py) com uma Binance de mentira e a carteira virtual: sem rede, sem conta."""

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from cryptoagent.config import DEMO_RELATIVE, Secrets, load_config
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.engine import live
from cryptoagent.engine.live import PaperView, analyze_live
from cryptoagent.timeutils import UTC, datetime_to_ms
from fake_binance import FakeBinance, FakeClock

ROOT = Path(__file__).resolve().parents[1]


def run_analysis(monkeypatch, when: datetime, *, paper: PaperView | None = None, latency_s: float = 0.0):
    clock = FakeClock(datetime_to_ms(when))
    cfg = load_config(ROOT / "config" / "config.yaml", ROOT / DEMO_RELATIVE)
    cfg.aprendizado.pasta = str(ROOT / "tests" / "_sem_aprendizado")  # pasta inexistente: nenhum ciclo de aprendizado interfere
    server = FakeBinance(clock, latency_s=latency_s)
    paper = paper or PaperView("ETHBRL", fee=0.001, capital=1000.0, risk_per_trade=10.0)

    async def go():
        async def no_sleep(_s):
            return None

        async with BinanceReadOnlyClient(cfg.binance, Secrets(), transport=server.transport(), sleep=no_sleep, clock=clock) as client:
            return await analyze_live(cfg, Secrets(), client, sqlite3.connect(":memory:"), paper=paper)

    return asyncio.run(go()), server, cfg


QUANDO = datetime(2026, 9, 15, 12, 0, 7, tzinfo=UTC)
QUANDO_COMPRA = datetime(2026, 9, 1, 15, 15, 7, tzinfo=UTC)  # instante em que o backtest da amostra também compra


def test_analise_ao_vivo_monta_o_contexto_com_candles_fechados(monkeypatch):
    res, server, cfg = run_analysis(monkeypatch, QUANDO)
    assert res.health.ok, res.health.problems
    assert res.decision.action in ("COMPRAR", "VENDER", "ESPERAR")
    assert res.account is None  # com carteira virtual, a conta real nem é lida
    now = datetime_to_ms(QUANDO)
    assert set(res.ctx.market.frames) == set(cfg.estrategia.timeframes)
    for frame in res.ctx.market.frames.values():
        assert (frame["close_time"] < now).all()  # nada do candle ainda aberto
        assert "rsi" in frame.columns
    assert res.ratio == pytest.approx(1.0)  # análise e operação no mesmo par
    assert res.trade_bid < res.ctx.market.price < res.trade_ask
    assert res.ctx.market.book_imbalance is not None and res.ctx.market.funding_rate == pytest.approx(0.01)
    assert server.calls["/api/v3/klines"] == len(cfg.estrategia.timeframes)
    assert server.calls["/api/v3/ticker/bookTicker"] == 1 and server.calls["/api/v3/depth"] == 1


def test_analise_ao_vivo_tem_o_resultado_esperado_neste_instante(monkeypatch):
    """Foto do comportamento: qualquer mudança de desempenho precisa manter exatamente isto."""
    res, _, _ = run_analysis(monkeypatch, QUANDO_COMPRA)
    d = res.decision
    foto = {
        "acao": d.action,
        "confianca": round(d.confidence, 6),
        "regime": d.regime,
        "preco": round(d.price, 4),
        "travas": sorted(d.strategy_gates),
        "sinais": {s.name: round(s.score, 6) for s in d.signals},
        "niveis": [round(x, 4) for x in (d.levels.entry, d.levels.stop, d.levels.target1, d.levels.target2, d.levels.size_brl)],
        "verificacoes": [(c.name, c.ok) for c in res.health.checks],
    }
    assert foto == FOTO_ESPERADA


def test_candle_atrasado_vira_esperar(monkeypatch):
    depois_do_fim = datetime(2026, 10, 2, 6, 0, 0, tzinfo=UTC)  # a amostra acaba em 01/10 23:59
    res, _, _ = run_analysis(monkeypatch, depois_do_fim)
    assert not res.health.ok and res.decision.action == "ESPERAR"
    assert any("candle atrasado" in p for p in res.health.problems)


def test_posicao_virtual_abaixo_do_stop_vira_vender(monkeypatch):
    posicao = PaperView(
        "ETHBRL", fee=0.001, capital=1000.0, risk_per_trade=10.0, qty=0.05, entry_price=99_999.0,
        opened_ms=datetime_to_ms(QUANDO) - 600_000, stop=99_000.0, target1=100_500.0, target2=101_000.0,
    )
    res, _, _ = run_analysis(monkeypatch, QUANDO, paper=posicao)
    assert res.decision.action == "VENDER" and res.decision.exit_reason == "stop"


FOTO_ESPERADA: dict = {
    "acao": "COMPRAR",
    "confianca": 72.519463,
    "regime": "tendencia_baixa",
    "preco": 12577.18,
    "travas": [],
    "sinais": {"vwap": 1.0, "bollinger": 0.47243, "rsi": 0.817658},
    "niveis": [12577.18, 12545.971, 12694.7025, 12857.7576, 1000.0],
    "verificacoes": [
        ("relógio do PC", True),
        ("candles 4h", True),
        ("candles 1h", True),
        ("candles 15m", True),
        ("candles 5m", True),
        ("candles 1m", True),
        ("preço ETHBRL", True),
        ("derivativos", True),
        ("análise por horário", False),
    ],
}


def test_cache_de_indicadores_reaproveita_so_quando_os_candles_sao_os_mesmos():
    import pandas as pd

    from cryptoagent.indicators.cache import IndicatorCache
    from cryptoagent.indicators.compute import compute_indicators

    cfg = load_config(ROOT / "config" / "config.yaml", ROOT / DEMO_RELATIVE)
    kw = dict(tz_name=cfg.fuso.local, day_start=cfg.fuso.inicio_dia_local)
    todos = pd.read_parquet(ROOT / "exemplos" / "historico" / "ETHBRL" / "1h.parquet")
    df = todos.iloc[:600].reset_index(drop=True)
    cache = IndicatorCache()
    direto = compute_indicators(df, cfg.indicadores, cfg.regime, **kw)
    primeiro = cache.compute(("ETHBRL", "1h"), df, cfg.indicadores, cfg.regime, **kw)
    segundo = cache.compute(("ETHBRL", "1h"), df, cfg.indicadores, cfg.regime, **kw)
    pd.testing.assert_frame_equal(primeiro, direto)
    pd.testing.assert_frame_equal(segundo, direto)
    assert (cache.hits, cache.misses) == (1, 1)
    segundo.loc[0, "close"] = -1.0  # quem recebe pode mexer na sua cópia sem estragar o cache
    pd.testing.assert_frame_equal(cache.compute(("ETHBRL", "1h"), df, cfg.indicadores, cfg.regime, **kw), direto)

    mais_um = todos.iloc[1:601].reset_index(drop=True)  # chegou um candle novo: recalcula
    pd.testing.assert_frame_equal(
        cache.compute(("ETHBRL", "1h"), mais_um, cfg.indicadores, cfg.regime, **kw), compute_indicators(mais_um, cfg.indicadores, cfg.regime, **kw)
    )
    alterado = df.copy()
    alterado.loc[300, "close"] += 0.01  # mesmo tamanho e mesmos horários, um valor diferente: recalcula
    cache.compute(("ETHBRL", "1h"), alterado, cfg.indicadores, cfg.regime, **kw)
    outro_parametro = cfg.indicadores.model_copy(update={"rsi": cfg.indicadores.rsi + 1})
    cache.compute(("ETHBRL", "1h"), alterado, outro_parametro, cfg.regime, **kw)  # parâmetro diferente: recalcula
    assert cache.misses == 4


def test_segunda_analise_seguida_da_o_mesmo_resultado_usando_o_cache(monkeypatch):
    live.INDICATOR_CACHE.clear()
    primeira, _, _ = run_analysis(monkeypatch, QUANDO_COMPRA)
    assert live.INDICATOR_CACHE.misses == 5 and live.INDICATOR_CACHE.hits == 0
    segunda, _, _ = run_analysis(monkeypatch, QUANDO_COMPRA)
    assert live.INDICATOR_CACHE.hits == 5
    assert (segunda.decision.action, segunda.decision.confidence) == (primeira.decision.action, primeira.decision.confidence)
    assert segunda.decision.levels == primeira.decision.levels


def test_derivativos_fora_do_ar_vira_aviso_e_nao_derruba_a_analise(monkeypatch):
    from fake_binance import FakeBinance

    original = FakeBinance._handle

    async def sem_futuros(self, request):
        if "/fapi/" in str(request.url):
            return httpx.Response(503, text="fora do ar")
        return await original(self, request)

    monkeypatch.setattr(FakeBinance, "_handle", sem_futuros)
    res, _, _ = run_analysis(monkeypatch, QUANDO_COMPRA)
    assert res.health.ok and res.ctx.market.funding_rate is None
    assert any(w.startswith("derivativos") for w in res.health.warnings)
