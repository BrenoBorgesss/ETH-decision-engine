"""Registro de operações: diário manual, ligação compra -> sinal e níveis da posição aberta."""

import pytest

from cryptoagent.account import journal
from cryptoagent.engine.decision import Decision
from cryptoagent.engine.journal import auto_associate, levels_for_open_lots, save_signal, set_association
from cryptoagent.engine.rules import Levels
from cryptoagent.storage import agent_db

MIN = 60_000
T0 = 1_788_000_000_000


@pytest.fixture
def conn(tmp_path):
    c = agent_db.connect(tmp_path / "agent.sqlite")
    yield c
    c.close()


def signal(conn, t, action="COMPRAR", mode="ao_vivo", stop=11_800.0, t1=12_100.0, t2=12_300.0):
    d = Decision(action, 70.0, "ETHBRL", "ETHBRL", t, 12_000.0)
    if action == "COMPRAR":
        d.levels = Levels(12_000.0, stop, t1, t2, 0.6, 1.8, 0.33, 0.83, 100.0, "VWAP do dia")
    return save_signal(conn, d, ratio=1.0, mode=mode)


def buy(conn, trade_id, t, is_buyer=1):
    conn.execute(
        """INSERT INTO account_trades (symbol, trade_id, order_id, time_ms, is_buyer, price, qty, quote_qty, commission,
               commission_asset, is_maker, fee_quote, fee_estimated)
           VALUES ('ETHBRL', ?, 1, ?, ?, 12000, 0.01, 120, 0.12, 'BRL', 0, 0.12, 0)""",
        (trade_id, t, is_buyer),
    )
    conn.commit()


def assoc(conn, trade_id):
    r = conn.execute("SELECT signal_id, association FROM account_trades WHERE trade_id = ?", (trade_id,)).fetchone()
    return r["signal_id"], r["association"]


def test_diario_manual_valida_registra_lista_e_apaga(conn):
    with pytest.raises(ValueError, match="compra ou venda"):
        journal.add_manual_trade(conn, symbol="ETHBRL", side="short", qty=0.1, price=12000, fee_quote=0)
    with pytest.raises(ValueError):
        journal.add_manual_trade(conn, symbol="ETHBRL", side="buy", qty=0, price=12000, fee_quote=0)
    with pytest.raises(ValueError):
        journal.add_manual_trade(conn, symbol="ETHBRL", side="buy", qty=0.1, price=-1, fee_quote=0)
    a = journal.add_manual_trade(conn, symbol="ETHBRL", side="buy", qty=0.1, price=12000, fee_quote=1.2, time_ms=T0)
    b = journal.add_manual_trade(
        conn, symbol="ETHBRL", side="sell", qty=0.1, price=12100, fee_quote=1.21, time_ms=T0 + MIN
    )
    journal.add_manual_trade(conn, symbol="ETHUSDT", side="buy", qty=0.1, price=2000, fee_quote=0.2, time_ms=T0)
    assert [r["id"] for r in journal.list_manual_trades(conn, "ETHBRL")] == [a, b]
    assert [r["id"] for r in journal.list_manual_trades(conn, "ETHBRL", since_ms=T0 + 1)] == [b]
    ledger = journal.manual_ledger_trades(conn, "ETHBRL")
    assert [(t.trade_id, t.side, t.source) for t in ledger] == [(f"m{a}", "buy", "manual"), (f"m{b}", "sell", "manual")]
    assert journal.remove_manual_trade(conn, a) is True
    assert journal.remove_manual_trade(conn, a) is False  # já não existe
    assert [r["id"] for r in journal.list_manual_trades(conn, "ETHBRL")] == [b]


def test_compra_liga_ao_ultimo_comprar_ao_vivo_ate_30_min_antes(conn):
    antigo = signal(conn, T0 - 40 * MIN)  # fora da janela
    recente = signal(conn, T0 - 10 * MIN)
    signal(conn, T0 - 5 * MIN, mode="paper")  # sinal da simulação nunca é ligado a compra real
    signal(conn, T0 + 1 * MIN)  # depois da compra: não vale
    buy(conn, 1, T0)
    buy(conn, 2, T0 + 60 * MIN)  # nenhum COMPRAR na janela
    buy(conn, 3, T0, is_buyer=0)  # venda: não é ligada
    assert auto_associate(conn, "ETHBRL") == 1
    assert assoc(conn, 1) == (str(recente), "automatica")
    assert assoc(conn, 2) == (None, "sem_sinal")
    assert assoc(conn, 3) == (None, "pendente")
    assert antigo != recente


def test_voce_confirma_ou_corrige_a_ligacao(conn):
    s1, s2 = signal(conn, T0 - 10 * MIN), signal(conn, T0 - 20 * MIN)
    buy(conn, 1, T0)
    auto_associate(conn, "ETHBRL")
    set_association(conn, "ETHBRL", 1, s1)
    assert assoc(conn, 1) == (str(s1), "confirmada")
    set_association(conn, "ETHBRL", 1, s2)
    assert assoc(conn, 1) == (str(s2), "corrigida")
    set_association(conn, "ETHBRL", 1, None)
    assert assoc(conn, 1) == (None, "corrigida")
    with pytest.raises(ValueError, match="não encontrada"):
        set_association(conn, "ETHBRL", 99, s1)
    with pytest.raises(ValueError, match="não existe"):
        set_association(conn, "ETHBRL", 1, 12345)


def test_niveis_da_posicao_vem_do_sinal_da_compra_aberta_mais_recente(conn):
    s_old = signal(conn, T0 - 25 * MIN, stop=11_700, t1=12_050, t2=12_200)
    s_new = signal(conn, T0 + 50 * MIN, stop=11_900, t1=12_150, t2=12_400)
    buy(conn, 1, T0)
    buy(conn, 2, T0 + 60 * MIN)
    auto_associate(conn, "ETHBRL")
    assert levels_for_open_lots(conn, "ETHBRL", ["1", "2"]) == (11_900, 12_150, 12_400, f"sinal #{s_new}")
    assert levels_for_open_lots(conn, "ETHBRL", ["1", "m7"]) == (11_700, 12_050, 12_200, f"sinal #{s_old}")
    assert levels_for_open_lots(conn, "ETHBRL", ["m7"]) is None  # registro manual não tem sinal
    set_association(conn, "ETHBRL", 2, None)
    assert levels_for_open_lots(conn, "ETHBRL", ["2"]) is None  # você desligou a compra do sinal


def test_spread_medido_so_vale_nas_horas_com_amostras_suficientes(tmp_path):
    from cryptoagent.backtest.costs import build_schedule, hourly_spreads
    from cryptoagent.storage import db as costs_db
    from cryptoagent.timeutils import local_hour_weekday_series

    path = tmp_path / "costs.sqlite"
    conn = costs_db.connect(path)
    base = 1_788_000_000_000 - 1_788_000_000_000 % 3_600_000  # início de uma hora UTC

    def sample(t, spread):
        return {"ts_ms": t, "symbol": "ETHBRL", "source": "ws", "spread_pct": spread, "slippage_json": {}}

    rows = [sample(base + i * 1000, 0.02 + 0.001 * (i % 3)) for i in range(60)]
    rows += [sample(base + 3_600_000 + i * 1000, 0.5) for i in range(10)]
    costs_db.insert_samples(conn, rows)
    conn.close()
    import pandas as pd

    h1, h2 = (
        int(local_hour_weekday_series(pd.Series([t]), "America/Sao_Paulo")["hour"].iloc[0])
        for t in (base, base + 3_600_000)
    )
    medido = hourly_spreads(path, "ETHBRL", "America/Sao_Paulo", min_samples=50)
    assert medido == {h1: pytest.approx(0.021)}  # a hora com só 10 amostras fica de fora
    assert hourly_spreads(path, "ETHUSDT", "America/Sao_Paulo", 50) == {}
    assert hourly_spreads(tmp_path / "nao_existe.sqlite", "ETHBRL", "America/Sao_Paulo", 50) == {}
    s = build_schedule(
        "ETHBRL",
        0.001,
        0.04,
        path,
        "America/Sao_Paulo",
        min_samples=50,
        slip_market=0.01,
        slip_stop=0.05,
        fee_source="teste",
    )
    assert s.spread_by_hour[h1] == pytest.approx(0.021) and s.spread_by_hour[h2] == 0.04
    assert s.measured_hours == [h1] and len(s.spread_by_hour) == 24
