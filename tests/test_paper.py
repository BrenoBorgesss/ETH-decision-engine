"""Paper trading: carteira virtual com as mesmas regras de execução do backtest."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent.backtest.execution import buy_fill, close_pnl, oco_check, sell_fill
from cryptoagent.engine.decision import Decision
from cryptoagent.engine.rules import Levels
from cryptoagent.paper.book import LIMIT_TEXT, EntryRecord, PaperBook, PaperCosts
from cryptoagent.paper.report import evaluate_signals
from cryptoagent.storage import sim_db

T = 1_800_000_000_000
MIN = 60_000
COSTS = PaperCosts(0.001, 0.01, 0.05)


@pytest.fixture
def conn(tmp_path):
    c = sim_db.connect(tmp_path / "simulacao.sqlite")
    yield c
    c.close()


def book(conn, **kw):
    base = dict(capital=1000, max_loss_brl=30, divisor=3, costs=COSTS, tz="America/Sao_Paulo", day_start="00:00", now_ms=T)
    base.update(kw)
    return PaperBook(conn, "padrao", "ETHBRL", **base)


def buy_decision(entry=100.0, stop=99.0, t1=102.0, size=500.0):
    d = Decision("COMPRAR", 70.0, "ETHUSDT", "ETHBRL", T, entry)
    d.levels = Levels(entry, stop, t1, 104.0, 1.8, 0.7, 1.4, 2.0, size, "VWAP do dia")
    d.regime = "lateral"
    return d


def rec(signal_id=None):
    return EntryRecord(decision_ms=T, decision_price=100.0, signal_id=signal_id, reason="teste")


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
    tid, why = b.open(buy_decision(), ratio=1.0, bid=99.9, ask=100.1, t_ms=T, record=rec(7))
    assert tid and why is None and b.open_trade()["signal_id"] == 7
    assert b.open(buy_decision(), ratio=1.0, bid=99.9, ask=100.1, t_ms=T, record=rec(8))[0] is None  # uma posição por vez
    assert b.check_oco(bars([(100.2, 101.0, 99.5, 100.8)]), 0.001) is None
    assert b.check_oco(bars([(100.2, 101.0, 99.5, 100.8), (101.0, 102.5, 100.9, 102.2)]), 0.001) == "alvo1"
    closed = b.closed_trades()[0]
    assert closed["exit_price"] == 102.0 and closed["pnl"] > 0
    assert b.equity == pytest.approx(1000 + closed["pnl"])


def test_stop_virtual_e_perda_maxima_do_dia(conn):
    b = book(conn, max_loss_brl=5)  # perda máxima virtual de R$ 5
    b.open(buy_decision(size=1000.0), ratio=1.0, bid=99.9, ask=100.1, t_ms=T, record=rec())
    assert b.check_oco(bars([(99.5, 99.6, 98.5, 98.7)]), 0.001) == "stop"
    view = b.view(T + 10 * MIN, 99.0, 0.001)
    assert view.daily_limit_reason == LIMIT_TEXT  # perdeu mais de R$ 5
    assert view.qty is None


def test_niveis_traduzidos_para_o_par_de_operacao(conn):
    b = book(conn)
    b.open(buy_decision(entry=2400, stop=2390, t1=2420), ratio=5.0, bid=11_999, ask=12_001, t_ms=T, record=rec())
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
    b.open(buy_decision(), ratio=1.0, bid=99.9, ask=100.1, t_ms=T, record=EntryRecord(T, 100.0, 1, "x", bypassed={"tendencia": "x"}))
    b.check_oco(bars([(99.5, 99.6, 98.5, 98.7)]), 0.001)  # stop
    b.open(buy_decision(), ratio=1.0, bid=99.9, ask=100.1, t_ms=T + 5 * MIN, record=EntryRecord(T + 5 * MIN, 100.0, 2, "x", bypassed={}))
    later = bars([(100.0, 102.5, 99.9, 102.0)])
    later[["open_time", "close_time"]] += 5 * MIN
    b.check_oco(later, 0.001)  # alvo
    g = {s.gate: s for s in gate_stats(b.closed_trades())}["tendencia"]
    assert (g.n_blocked, g.n_passed) == (1, 1)
    assert g.avg_blocked_pct < 0 < g.avg_passed_pct


# ------------------------------------------------------------ Etapa 7: execução realista e registro


def test_b1_oco_nao_usa_o_pedaco_do_minuto_anterior_a_entrada(conn):
    """Compra executada 5 s depois do início do minuto: a mínima desse minuto pode ter acontecido ANTES da compra."""
    b = book(conn)
    b.open(buy_decision(), ratio=1.0, bid=99.9, ask=100.1, t_ms=T + 5_000, record=rec())
    assert b.open_trade()["checked_until_ms"] == T + MIN  # primeiro candle inteiro depois da compra
    # no candle da compra a mínima furou o stop (99), mas pode ter sido antes da compra: não conta
    assert b.check_oco(bars([(100.0, 100.2, 98.8, 100.1)]), 0.001) is None
    assert b.open_trade() is not None
    # o candle seguinte, inteiro depois da compra, conta
    assert b.check_oco(bars([(100.0, 100.2, 98.8, 100.1), (100.1, 100.3, 98.9, 99.0)]), 0.001) == "stop"


def test_regras_de_tamanho_do_par_arredondam_e_recusam(conn):
    from cryptoagent.paper.filters import SymbolFilters

    f = SymbolFilters("ETHBRL", min_qty=0.0001, step=0.0001, max_market_qty=10.0, min_notional=10.0, tick=0.01)
    assert f.check(0.123456, 100.0) == (0.1234, None)
    assert f.check(0.05, 100.0)[1].startswith("ordem de 5.00 abaixo do valor mínimo")
    assert f.check(0.00005, 1_000_000.0)[1].startswith("quantidade")
    assert f.check(50.0, 100.0) == (10.0, None)  # limite de quantidade numa ordem a mercado
    b = book(conn, filters=f)
    tid, why = b.open(buy_decision(size=500.0), ratio=1.0, bid=99.9, ask=100.1, t_ms=T, record=rec())
    pos = b.open_trade()
    assert tid and pos["qty"] == pytest.approx(round(pos["qty"], 4)) and pos["size"] == pytest.approx(pos["qty"] * pos["entry_price"])
    b.close(fill=101.0, t_ms=T + MIN, reason="x")
    tid, why = b.open(buy_decision(size=5.0), ratio=1.0, bid=99.9, ask=100.1, t_ms=T + 2 * MIN, record=rec(9))
    assert tid is None and "abaixo do valor mínimo" in why
    ev = conn.execute("SELECT kind, detail, signal_id FROM paper_events").fetchall()
    assert [(e["kind"], e["signal_id"]) for e in ev] == [("ordem_recusada", 9)]


def test_filtros_lidos_da_resposta_real_da_binance():
    from cryptoagent.paper.filters import SymbolFilters

    resp = {"symbols": [{"symbol": "ETHBRL", "filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01000000", "maxPrice": "200000.00000000", "tickSize": "0.01000000"},
        {"filterType": "LOT_SIZE", "minQty": "0.00010000", "maxQty": "45000.00000000", "stepSize": "0.00010000"},
        {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00000000", "maxQty": "10.44144375", "stepSize": "0.00000000"},
        {"filterType": "NOTIONAL", "minNotional": "10.00000000", "applyMinToMarket": True, "maxNotional": "9000000.00000000"},
    ]}]}
    f = SymbolFilters.from_exchange_info(resp, "ETHBRL")
    assert (f.min_qty, f.step, f.max_market_qty, f.min_notional, f.tick) == (0.0001, 0.0001, 10.44144375, 10.0, 0.01)
    with pytest.raises(ValueError):
        SymbolFilters.from_exchange_info(resp, "BTCBRL")


def test_perda_maxima_do_dia_em_reais_e_capital_inicial_preservado(conn):
    b = book(conn, capital=2000, max_loss_brl=50)
    assert b.view(T, 100.0, 0.001).risk_per_trade == pytest.approx(50 / 3)
    again = book(conn, capital=5000, max_loss_brl=40)  # mesma variante, outro capital pedido
    assert again.initial_capital == 2000 and again.equity == 2000  # o capital só muda com "paper resetar"
    assert again.max_loss_day == 40  # a perda do dia pode ser ajustada


def test_operacao_guarda_tudo_do_momento_da_compra(conn):
    b = book(conn)
    record = EntryRecord(T, 100.0, 3, "preço 3 ATR abaixo do VWAP", features={"setup_rsi": 25.0}, scenario={"regime": "lateral", "hora_local": 10})
    b.open(buy_decision(), ratio=1.0, bid=100.4, ask=100.6, t_ms=T + 1000, record=record)
    b.close_market(bid=101.9, ask=102.1, t_ms=T + 10 * MIN, reason="alvo1", decision_ms=T + 10 * MIN - 1000)
    r = conn.execute("SELECT * FROM paper_trades").fetchone()
    assert (r["decision_ms"], r["decision_price"], r["entry_ms"], r["signal_id"]) == (T, 100.0, T + 1000, 3)
    assert r["entry_price"] > 100.5  # executado no preço DEPOIS do atraso, com meio spread e slippage
    assert r["entry_reason"].startswith("preço 3 ATR") and '"setup_rsi": 25.0' in r["features_json"] and '"lateral"' in r["scenario_json"]
    assert r["exit_decision_ms"] == T + 10 * MIN - 1000 and r["pnl"] == pytest.approx(r["qty"] * r["exit_price"] * 0.999 - r["size"] - r["fee_in"])


def test_banco_da_simulacao_nao_tem_tabelas_da_conta_real(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"account_trades", "manual_trades", "daily_sessions"}
    assert {"paper_state", "paper_trades", "paper_events", "signals"} <= tables
