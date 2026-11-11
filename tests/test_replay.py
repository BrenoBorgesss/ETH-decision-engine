"""Replay e reset da simulação: mesmo laço do paper ao vivo, sobre o histórico, sem rede e sem a conta real."""

import argparse
import asyncio
import contextlib
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.config import DEMO_RELATIVE, Secrets, load_config
from cryptoagent.data.rest import BinanceError, MissingCredentials
from cryptoagent.paper.replay import ReplayClient
from cryptoagent.paper.runner import run_paper
from cryptoagent.timeutils import datetime_to_ms

ROOT = Path(__file__).resolve().parents[1]
SP = ZoneInfo("America/Sao_Paulo")
COSTS = CostSchedule("ETHBRL", 0.001, dict.fromkeys(range(24), 0.04), [], 0.01, 0.05, "teste")


def demo_cfg(tmp_path):
    cfg = load_config(ROOT / "config" / "config.yaml", ROOT / DEMO_RELATIVE)
    cfg.conta.banco = str(tmp_path / "conta_real_nao_pode_existir.sqlite")
    cfg.sazonalidade.relatorio_pasta = str(tmp_path / "sem_perfil")
    cfg.aprendizado.pasta = str(tmp_path / "sem_aprendizado")
    return cfg


def ms(y, mo, d, h, mi=0):
    return datetime_to_ms(datetime(y, mo, d, h, mi, tzinfo=SP))


def run_replay(cfg, db, start, end):
    client = ReplayClient(cfg, start_ms=start, costs=COSTS)
    lines = []
    asyncio.run(
        run_paper(
            cfg, Secrets(), fee=0.001, db_path=db, client=client, until_ms=end, say=lines.append, sleep=client.advance
        )
    )
    return client, lines


def test_replay_roda_o_paper_sobre_o_historico_sem_rede_e_sem_a_conta(tmp_path):
    cfg = demo_cfg(tmp_path)
    amostra = sorted((ROOT / "exemplos" / "historico").rglob("*.parquet"))
    antes = {p: p.stat().st_mtime_ns for p in amostra}
    db = tmp_path / "replay.sqlite"
    client, lines = run_replay(cfg, db, ms(2026, 9, 8, 9), ms(2026, 9, 8, 13))
    conn = sqlite3.connect(db)
    trades = conn.execute("SELECT decision_ms, entry_ms, exit_reason, pnl FROM paper_trades ORDER BY id").fetchall()
    n_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    conn.close()
    # 4 h = 48 candles de 5m: uma decisão por candle fechado
    assert n_signals == 48
    # as mesmas duas compras do replay do dia inteiro (10:15 stop, 10:45 alvo), no horário local
    assert [(datetime.fromtimestamp(t[0] / 1000, SP).strftime("%H:%M"), t[2]) for t in trades] == [
        ("10:15", "stop"),
        ("10:45", "alvo1"),
    ]
    assert all(t[1] - t[0] == 1000 for t in trades)  # executadas 1 s depois da decisão (atraso de execução)
    assert not (tmp_path / "conta_real_nao_pode_existir.sqlite").exists()
    assert {p: p.stat().st_mtime_ns for p in amostra} == antes  # o histórico não foi alterado
    assert "/api/v3/account" not in client.requests and client.requests["/api/v3/klines"] > 0
    assert any("Nenhuma ordem real é enviada" in x for x in lines)


def test_replay_e_reprodutivel(tmp_path):
    cfg = demo_cfg(tmp_path)
    a, b = tmp_path / "a.sqlite", tmp_path / "b.sqlite"
    run_replay(cfg, a, ms(2026, 9, 8, 10), ms(2026, 9, 8, 11))
    run_replay(cfg, b, ms(2026, 9, 8, 10), ms(2026, 9, 8, 11))
    q = "SELECT decision_ms, entry_ms, entry_price, qty, exit_ms, exit_price, pnl FROM paper_trades ORDER BY id"
    with contextlib.closing(sqlite3.connect(a)) as ca, contextlib.closing(sqlite3.connect(b)) as cb:
        assert ca.execute(q).fetchall() == cb.execute(q).fetchall()


def test_cliente_de_replay_so_entrega_o_passado(tmp_path):
    cfg = demo_cfg(tmp_path)
    t = ms(2026, 9, 8, 10, 15) + 5_000
    client = ReplayClient(cfg, start_ms=t, costs=COSTS)

    async def go():
        df, _ = await client.klines("ETHBRL", "5m", limit=10)
        assert (
            len(df) == 10
            and (df["close_time"] < t).all()
            and int(df["close_time"].iloc[-1]) == ms(2026, 9, 8, 10, 15) - 1
        )
        one, _ = await client.klines("ETHBRL", "1m", limit=1000)
        assert int(one["open_time"].iloc[-1]) == ms(2026, 9, 8, 10, 14)  # o minuto em curso (10:15) não aparece
        bt = (await client.get("/api/v3/ticker/bookTicker", {"symbol": "ETHBRL"})).data
        bid, ask = float(bt["bidPrice"]), float(bt["askPrice"])
        assert ask / bid == pytest.approx((1 + 0.0002) / (1 - 0.0002))  # spread de 0,04% da tabela de custos
        with pytest.raises(MissingCredentials):
            await client.get("/api/v3/account")
        with pytest.raises(BinanceError):
            await client.get("/fapi/v1/premiumIndex", {"symbol": "ETHBRL"})
        await client.advance(60)
        assert client.server_now_ms() == t + 60_000

    asyncio.run(go())


def test_resetar_guarda_copia_e_so_age_com_confirmacao(tmp_path, capsys):
    from cryptoagent.cli.paper import cmd_paper_reset

    cfg = demo_cfg(tmp_path)
    db = tmp_path / "simulacao.sqlite"
    cfg.paper.banco = str(db)
    run_replay(cfg, db, ms(2026, 9, 8, 10), ms(2026, 9, 8, 11))
    assert asyncio.run(cmd_paper_reset(cfg, Secrets(), argparse.Namespace(confirmar=False))) == 1
    assert db.exists()
    assert asyncio.run(cmd_paper_reset(cfg, Secrets(), argparse.Namespace(confirmar=True))) == 0
    assert not db.exists()
    backups = list(tmp_path.glob("simulacao_antes_reset_*.sqlite"))
    assert len(backups) == 1
    with contextlib.closing(sqlite3.connect(backups[0])) as c:
        assert c.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 12


def test_perda_maxima_precisa_ser_menor_que_o_capital(tmp_path):
    cfg = demo_cfg(tmp_path)
    client = ReplayClient(cfg, start_ms=ms(2026, 9, 8, 10), costs=COSTS)
    with pytest.raises(ValueError):
        asyncio.run(
            run_paper(
                cfg,
                Secrets(),
                fee=0.001,
                capital=100,
                max_loss_brl=100,
                db_path=tmp_path / "x.sqlite",
                client=client,
                max_cycles=1,
            )
        )
