"""Controle de risco de ponta a ponta (Etapa 9): sessão do dia, perda máxima, posição pelo saldo e VENDER = sair.

Usa a análise ao vivo completa (`analyze_live`, caminho da CONTA REAL) contra uma Binance simulada: candles da
amostra do repositório e uma conta falsa, somente leitura. Nenhuma chamada sai para a rede.
"""

import asyncio
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import SecretStr

from cryptoagent.config import DEMO_RELATIVE, Secrets, load_config
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.engine import live
from cryptoagent.engine.decision import decide
from cryptoagent.session import daily
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import UTC, datetime_to_ms
from fake_binance import FakeBinance, FakeClock
from test_engine import P, ctx, frame, good_frames

ROOT = Path(__file__).resolve().parents[1]
SECRETS = Secrets(binance_api_key=SecretStr("k" * 20), binance_api_secret=SecretStr("s" * 20))
QUANDO = datetime(2026, 9, 1, 15, 15, 7, tzinfo=UTC)  # 12:15 em Brasília; as regras compram neste instante
NOW = datetime_to_ms(QUANDO)
T_HOJE = datetime_to_ms(datetime(2026, 9, 1, 13, 0, tzinfo=UTC))  # 10:00 em Brasília, mesmo dia


def trade(i, side, price, qty, t):
    quote = price * qty
    return {
        "id": i,
        "orderId": i,
        "price": str(price),
        "qty": str(qty),
        "quoteQty": str(quote),
        "commission": f"{quote * 0.001:.8f}",
        "commissionAsset": "BRL",
        "time": t,
        "isBuyer": side == "buy",
        "isMaker": False,
    }


def analyze(tmp_path, *, trades=(), balances=None, session=(1000.0, 30.0), validar=False):
    cfg = load_config(ROOT / "config" / "config.yaml", ROOT / DEMO_RELATIVE)
    cfg.conta.banco = str(tmp_path / "agent.sqlite")
    cfg.aprendizado.pasta = str(tmp_path / "sem_aprendizado")
    cfg.backtest.relatorio_pasta = str(tmp_path / "sem_backtest")
    cfg.confiabilidade.exigir_validacao = validar
    conn = agent_db.connect(tmp_path / "agent.sqlite")
    if session:
        daily.save_session(
            conn,
            date(2026, 9, 1),
            capital_brl=session[0],
            max_loss_brl=session[1],
            par_operacao="ETHBRL",
            saldo_em="BRL",
            candidates=cfg.mercado.pares_candidatos,
            now=NOW,
        )
    clock = FakeClock(NOW)
    server = FakeBinance(
        clock,
        account={"balances": balances or [{"asset": "BRL", "free": "1000", "locked": "0"}], "trades": list(trades)},
    )

    async def go():
        async def nosleep(_s):
            return None

        async with BinanceReadOnlyClient(
            cfg.binance, SECRETS, transport=server.transport(), sleep=nosleep, clock=clock
        ) as client:
            return await live.analyze_live(cfg, SECRETS, client, conn)

    try:
        return asyncio.run(go())
    finally:
        conn.close()


# ------------------------------------------------------------------ 9.1 sessão do dia


def test_sem_as_respostas_do_dia_o_agente_espera(tmp_path):
    res = analyze(tmp_path, session=None)
    assert res.decision.action == "ESPERAR"
    assert any("Sessão do dia não respondida" in b for b in res.decision.blocks)


def test_com_a_sessao_do_dia_e_setup_de_compra_o_agente_compra_com_o_risco_informado(tmp_path):
    res = analyze(tmp_path)
    d = res.decision
    assert d.action == "COMPRAR", d.blocks
    assert res.account.limits.risk_per_trade_brl == pytest.approx(10)  # R$ 30 ÷ 3
    # tamanho: no stop, perde no máximo o risco por operação (e nunca aplica mais que o capital efetivo)
    assert d.levels.size_brl == pytest.approx(
        min(10 / (d.levels.risk_net_pct / 100), res.account.effective_capital_brl)
    )
    assert d.levels.size_brl * d.levels.risk_net_pct / 100 <= 10 + 1e-9


# ------------------------------------------------------------------ 9.2 perda máxima


def test_perda_maxima_atingida_bloqueia_a_compra_mas_mostra_a_leitura(tmp_path):
    perdedoras = [
        trade(1, "buy", 13000, 0.05, T_HOJE),
        trade(2, "sell", 12000, 0.05, T_HOJE + 60_000),
    ]  # −R$ 50 + taxas
    res = analyze(tmp_path, trades=perdedoras)
    d = res.decision
    assert res.account.limits.blocked and res.account.pnl_brl < -30
    assert d.action == "ESPERAR" and any("PERDA MÁXIMA DO DIA ATINGIDA" in b for b in d.blocks)
    assert d.levels is not None  # a leitura técnica continua visível; só a ação fica travada


def test_perda_maxima_atingida_ainda_permite_vender_para_reduzir_o_risco(tmp_path):
    # comprou caro hoje: posição aberta com prejuízo maior que a perda máxima e preço abaixo do stop pelas regras
    compra = [trade(1, "buy", 14000, 0.05, T_HOJE)]
    saldo = [
        {"asset": "BRL", "free": "300", "locked": "0"},
        {"asset": "ETH", "free": "0.05", "locked": "0"},
    ]  # taxa paga em BRL
    res = analyze(tmp_path, trades=compra, balances=saldo)
    d = res.decision
    assert res.account.limits.blocked
    assert d.action == "VENDER" and d.exit_reason == "stop"
    assert "0,050000 ETH" in d.position_note  # vender = sair da posição da operação


# ------------------------------------------------------------------ 9.3 posição e VENDER


def test_posicao_que_nao_existe_no_saldo_bloqueia(tmp_path):
    compra = [trade(1, "buy", 12500, 0.05, T_HOJE)]
    res = analyze(
        tmp_path, trades=compra, balances=[{"asset": "BRL", "free": "400", "locked": "0"}]
    )  # o ETH não está na conta
    assert res.decision.action == "ESPERAR"
    assert any("maior que o saldo de ETH" in b for b in res.decision.blocks)


def test_eth_que_ja_estava_na_conta_nao_vira_posicao(tmp_path):
    saldo = [{"asset": "BRL", "free": "1000", "locked": "0"}, {"asset": "ETH", "free": "3", "locked": "0"}]
    res = analyze(tmp_path, balances=saldo)
    assert res.account.day.open_qty == 0 and res.decision.action == "COMPRAR"
    assert any("fora da operação" in w for w in res.account.warnings)


def test_sem_posicao_o_motor_nunca_manda_vender():
    """Spot, sem venda a descoberto: VENDER só existe para sair de uma posição."""
    rng = np.random.default_rng(5)
    for _ in range(300):
        f = good_frames()
        f["15m"]["bb_pctb"] = float(rng.uniform(-0.5, 1.5))
        f["15m"]["dist_vwap_atr"] = float(rng.uniform(-4, 4))
        f["15m"]["rsi"] = float(rng.uniform(5, 95))
        f["5m"]["rsi"] = float(rng.uniform(5, 95))
        f["1h"] = frame(
            3,
            close=100.0,
            ema_50=float(rng.uniform(95, 105)),
            adx=float(rng.uniform(5, 50)),
            plus_di=float(rng.uniform(5, 40)),
            minus_di=float(rng.uniform(5, 40)),
            structure="mista",
            regime="lateral",
        )
        d = decide(ctx(f, price=float(rng.uniform(95, 105))), P)
        assert d.action in ("COMPRAR", "ESPERAR")


# ------------------------------------------------------------------ 9.2 na simulação


def test_simulacao_bloqueia_novas_compras_depois_da_perda_maxima_do_dia(tmp_path):
    """Replay de 08/09 (amostra): a 1ª compra sai no stop e a 2ª (10:45) sai no alvo.

    Com risco por operação = perda máxima do dia (divisor 1) e perda máxima de R$ 3, o stop da 1ª já consome o limite
    (o stop executa com slippage maior que o previsto no tamanho). Então a 2ª compra não pode acontecer.
    """
    import sqlite3

    from test_replay import COSTS, demo_cfg, ms, run_replay

    cfg = demo_cfg(tmp_path)
    cfg.paper.perda_max_dia_brl = 3.0
    cfg.risco.divisor_perda_diaria = 1.0
    db = tmp_path / "replay.sqlite"
    _, _lines = run_replay(cfg, db, ms(2026, 9, 8, 9), ms(2026, 9, 8, 13))
    conn = sqlite3.connect(db)
    trades = conn.execute("SELECT exit_reason, pnl FROM paper_trades").fetchall()
    bloqueados = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE details_json LIKE '%PERDA MÁXIMA DO DIA ATINGIDA%'"
    ).fetchone()[0]
    conn.close()
    assert [t[0] for t in trades] == ["stop"] and trades[0][1] <= -3
    assert bloqueados > 0  # depois do stop, as análises seguintes ficaram travadas pela perda máxima virtual
    assert COSTS is not None and pd.notna(trades[0][1])
