"""Paper trading: carteira virtual com as mesmas regras de execução do backtest."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent.backtest.execution import buy_fill, close_pnl, oco_check, sell_fill
from cryptoagent.engine.decision import Decision
from cryptoagent.engine.rules import Levels
from cryptoagent.paper.book import LIMIT_TEXT, PaperBook, PaperCosts
from cryptoagent.paper.report import evaluate_signals
from cryptoagent.storage import agent_db

T = 1_800_000_000_000
MIN = 60_000
COSTS = PaperCosts(0.001, 0.01, 0.05)


@pytest.fixture
def conn(tmp_path):
    c = agent_db.connect(tmp_path / "agent.sqlite")
    yield c
    c.close()


def book(conn, **kw):
    base = dict(capital=1000, loss_pct=3, divisor=3, costs=COSTS, tz="America/Sao_Paulo", day_start="00:00")
    base.update(kw)
    return PaperBook(conn, "padrao", "ETHBRL", **base)


def buy_decision(entry=100.0, stop=99.0, t1=102.0, size=500.0):
    d = Decision("COMPRAR", 70.0, "ETHUSDT", "ETHBRL", T, entry)
    d.levels = Levels(entry, stop, t1, 104.0, 1.8, 0.7, 1.4, 2.0, size, "VWAP do dia")
    d.regime = "lateral"
    return d


def bars(rows):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["open_time"] = [T + i * MIN for i in range(len(df))]
    df["close_time"] = df["open_time"] + MIN - 1
    return df


def test_execucao_compartilhada():
    assert buy_fill(100, 0.001, 0.01) == pytest.approx(100 * 1.0011)
    assert sell_fill(100, 0.001, 0.01) == pytest.approx(100 * 0.9989)
    pnl, fee_out = close_pnl(1.0, 100.0, 0.1, 102.0, 0.001)
    assert fee_out == pytest.approx(0.102) and pnl == pytest.approx(102 - 0.102 - 100 - 0.1)
    # stop e alvo no mesmo candle: pior caso (stop)
    hit = oco_check(np.array([100.0]), np.array([103.0]), np.array([98.0]), stop=99, target=102, half_spread=0, slip_stop_pct=0)
    assert hit.reason == "stop" and hit.fill == 99
    # abriu abaixo do stop (gap): executa na abertura
    hit = oco_check(np.array([97.0]), np.array([97.5]), np.array([96.0]), stop=99, target=102, half_spread=0, slip_stop_pct=0)
    assert hit.fill == 97


def test_compra_e_alvo_virtual(conn):
    b = book(conn)
    tid = b.open(buy_decision(), ratio=1.0, bid=99.9, ask=100.1, t_ms=T, signal_id=7)
    assert tid and b.open_trade()["signal_id"] == 7
    assert b.open(buy_decision(), ratio=1.0, bid=99.9, ask=100.1, t_ms=T, signal_id=8) is None  # uma posição por vez
    assert b.check_oco(bars([(100.2, 101.0, 99.5, 100.8)]), 0.001) is None
    assert b.check_oco(bars([(100.2, 101.0, 99.5, 100.8), (101.0, 102.5, 100.9, 102.2)]), 0.001) == "alvo1"
    closed = b.closed_trades()[0]
    assert closed["exit_price"] == 102.0 and closed["pnl"] > 0
    assert b.equity == pytest.approx(1000 + closed["pnl"])


def test_stop_virtual_e_perda_maxima_do_dia(conn):
    b = book(conn, loss_pct=0.5)  # perda máxima virtual de R$ 5
    b.open(buy_decision(size=1000.0), ratio=1.0, bid=99.9, ask=100.1, t_ms=T, signal_id=None)
    assert b.check_oco(bars([(99.5, 99.6, 98.5, 98.7)]), 0.001) == "stop"
    view = b.view(T + 10 * MIN, 99.0, 0.001)
    assert view.daily_limit_reason == LIMIT_TEXT  # perdeu mais de R$ 5
    assert view.qty is None


def test_niveis_traduzidos_para_o_par_de_operacao(conn):
    b = book(conn)
    b.open(buy_decision(entry=2400, stop=2390, t1=2420), ratio=5.0, bid=11_999, ask=12_001, t_ms=T, signal_id=None)
    pos = b.open_trade()
    assert pos["stop"] == pytest.approx(2390 * 5) and pos["target1"] == pytest.approx(2420 * 5)


def test_avaliacao_dos_sinais_contra_o_mercado():
    sig = pd.DataFrame(
        [
            {"id": 1, "time_ms": T, "action": "COMPRAR", "confidence": 70.0, "entry": 100.0, "stop": 99.0, "target1": 102.0},
            {"id": 2, "time_ms": T, "action": "ESPERAR", "confidence": 30.0, "entry": 100.0, "stop": 99.5, "target1": 103.0},
            {"id": 3, "time_ms": T, "action": "ESPERAR", "confidence": 20.0, "entry": None, "stop": None, "target1": None},
        ]
    )
    b = bars([(100, 101, 99.6, 100.5), (100.5, 102.3, 100.4, 102.0), (102, 102.1, 99.0, 99.2)])
    out = {o.signal_id: o for o in evaluate_signals(sig, b, horizon_h=1, now=T + 10 * 3_600_000)}
    assert out[1].outcome == "alvo com lucro"
    assert out[2].outcome == "stop primeiro"  # a trava evitou uma operação que daria stop
    assert 3 not in out  # sem níveis, não há o que avaliar
    assert out[1].mfe_pct == pytest.approx(2.3) and out[1].mae_pct == pytest.approx(-1.0)


def test_alvo_que_nao_paga_o_custo_nao_conta_como_acerto():
    from cryptoagent.paper.report import signal_cost_pct

    # alvo a 0,05% e custo de 0,2%: tocar no alvo = prejuízo
    sig = pd.DataFrame([
        {"id": 1, "time_ms": T, "action": "ESPERAR", "confidence": 20.0, "entry": 100.0, "stop": 99.0, "target1": 100.05,
         "rr": None, "details_json": '{"cost_pct": 0.2}'},
        # sinal antigo sem cost_pct: o custo sai do risco/retorno gravado (alvo 2%, stop 1%, custo 0,2% -> rr 1,8/1,2)
        {"id": 2, "time_ms": T, "action": "ESPERAR", "confidence": 20.0, "entry": 100.0, "stop": 99.0, "target1": 102.0,
         "rr": 1.8 / 1.2, "details_json": "{}"},
    ])
    assert signal_cost_pct(next(sig.itertuples())) == 0.2
    assert signal_cost_pct(list(sig.itertuples())[1]) == pytest.approx(0.2)
    b = bars([(100, 102.5, 99.9, 102.0)])
    out = {o.signal_id: o.outcome for o in evaluate_signals(sig, b, horizon_h=1, now=T + 10 * 3_600_000)}
    assert out == {1: "alvo sem lucro", 2: "alvo com lucro"}


def test_variante_de_estudo_so_ignora_travas_de_estrategia(conn):
    from cryptoagent.engine.decision import study_entry_ok
    from cryptoagent.paper.report import gate_stats

    d = buy_decision()
    d.action = "ESPERAR"
    d.blocks = ["filtro de tendência: 1h em queda"]
    d.strategy_gates = {"tendencia": "filtro de tendência: 1h em queda"}
    assert study_entry_ok(d)
    d.blocks.append("dados atrasados")  # trava de SEGURANÇA: nunca é ignorada
    assert not study_entry_ok(d)
    d.blocks = list(d.strategy_gates.values()) + [LIMIT_TEXT]  # perda máxima do dia também não
    assert not study_entry_ok(d)
    d.strategy_gates["distancia_alvo"] = "alvo 1 a só 0,05%"  # trava de CUSTO: nunca é ignorada
    d.blocks = list(d.strategy_gates.values())
    assert not study_entry_ok(d)
    del d.strategy_gates["distancia_alvo"]
    d.blocks, d.levels = list(d.strategy_gates.values()), None  # sem stop/alvo, não compra
    assert not study_entry_ok(d)

    b = book(conn)
    b.open(buy_decision(), ratio=1.0, bid=99.9, ask=100.1, t_ms=T, signal_id=1, bypassed={"tendencia": "x"})
    b.check_oco(bars([(99.5, 99.6, 98.5, 98.7)]), 0.001)  # stop
    b.open(buy_decision(), ratio=1.0, bid=99.9, ask=100.1, t_ms=T + 5 * MIN, signal_id=2, bypassed={})
    later = bars([(100.0, 102.5, 99.9, 102.0)])
    later[["open_time", "close_time"]] += 5 * MIN
    b.check_oco(later, 0.001)  # alvo
    g = {s.gate: s for s in gate_stats(b.closed_trades())}["tendencia"]
    assert (g.n_blocked, g.n_passed) == (1, 1)
    assert g.avg_blocked_pct < 0 < g.avg_passed_pct
