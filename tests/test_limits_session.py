"""Limite de perda do dia e sessão do dia (com fuso de Brasília)."""

from datetime import date, datetime

import pytest

from cryptoagent.account.limits import evaluate_limits
from cryptoagent.session import daily
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import UTC, datetime_to_ms

CANDIDATES = ["ETHUSDT", "ETHBRL"]


def test_limite_sem_perda():
    lim = evaluate_limits(1000, 30, 0, divisor=3)
    assert lim.remaining_brl == 30 and lim.risk_per_trade_brl == pytest.approx(10) and not lim.blocked


def test_perda_consome_o_limite_e_risco_nunca_passa_do_que_resta():
    lim = evaluate_limits(1000, 30, -25, divisor=3)
    assert lim.loss_used_brl == 25 and lim.remaining_brl == pytest.approx(5)
    assert lim.risk_per_trade_brl == pytest.approx(5)
    assert lim.pnl_pct == pytest.approx(-2.5)


def test_bloqueia_ao_atingir_a_perda_maxima():
    assert evaluate_limits(1000, 30, -30, divisor=3).blocked
    assert evaluate_limits(1000, 30, -31, divisor=3).remaining_brl == 0


def test_lucro_nao_amplia_o_limite_por_padrao():
    assert evaluate_limits(1000, 30, 50, divisor=3).remaining_brl == 30
    assert evaluate_limits(1000, 30, 50, divisor=3, profit_extends=True).remaining_brl == 80


def test_valores_invalidos():
    with pytest.raises(ValueError):
        evaluate_limits(0, 30, 0, divisor=3)


@pytest.fixture
def conn(tmp_path):
    c = agent_db.connect(tmp_path / "agent.sqlite")
    yield c
    c.close()


def test_sessao_salva_e_lida(conn):
    s = daily.save_session(conn, date(2026, 10, 22), capital_brl=1000, max_loss_brl=30, par_operacao="ETHBRL", saldo_em="BRL", candidates=CANDIDATES)
    assert daily.get_session(conn, date(2026, 10, 22)) == s
    assert daily.get_session(conn, date(2026, 10, 23)) is None


def test_nao_pode_aumentar_perda_maxima_no_meio_do_dia(conn):
    kw = dict(par_operacao="ETHBRL", saldo_em="BRL", candidates=CANDIDATES)
    daily.save_session(conn, date(2026, 10, 22), capital_brl=1000, max_loss_brl=30, **kw)
    daily.save_session(conn, date(2026, 10, 22), capital_brl=1200, max_loss_brl=20, **kw)  # reduzir: ok
    with pytest.raises(daily.SessionError):
        daily.save_session(conn, date(2026, 10, 22), capital_brl=1200, max_loss_brl=25, **kw)
    assert daily.get_session(conn, date(2026, 10, 22)).max_loss_brl == 20


def test_nao_pode_trocar_de_par_no_meio_do_dia(conn):
    daily.save_session(conn, date(2026, 10, 22), capital_brl=1000, max_loss_brl=30, par_operacao="ETHBRL", saldo_em="BRL", candidates=CANDIDATES)
    with pytest.raises(daily.SessionError):
        daily.save_session(conn, date(2026, 10, 22), capital_brl=1000, max_loss_brl=30, par_operacao="ETHUSDT", saldo_em="USDT", candidates=CANDIDATES)


@pytest.mark.parametrize(
    "capital,loss,par,saldo",
    [(0, 10, "ETHBRL", "BRL"), (1000, 0, "ETHBRL", "BRL"), (1000, 1000, "ETHBRL", "BRL"), (1000, 10, "BTCBRL", "BRL"), (1000, 10, "ETHBRL", "USDT")],
)
def test_validacoes_da_sessao(conn, capital, loss, par, saldo):
    with pytest.raises(daily.SessionError):
        daily.save_session(conn, date(2026, 10, 22), capital_brl=capital, max_loss_brl=loss, par_operacao=par, saldo_em=saldo, candidates=CANDIDATES)


def test_dia_da_sessao_segue_brasilia_e_nao_utc():
    # 02:30 UTC de 23/10 ainda é 22/10 em Brasília (23:30)
    t = datetime_to_ms(datetime(2026, 10, 23, 2, 30, tzinfo=UTC))
    assert daily.today("America/Sao_Paulo", "00:00", t) == date(2026, 10, 22)
    assert daily.today("America/Sao_Paulo", "00:00", t + 3_600_000) == date(2026, 10, 23)
