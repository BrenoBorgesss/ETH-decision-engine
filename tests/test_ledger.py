"""Livro da operação: resultado do dia só com as operações da estratégia."""

import pytest

from cryptoagent.account.ledger import LedgerTrade, MissingDayStartPrice, OperationLedger, compute_day

DAY = 86_400_000
D0 = 1_800_000_000_000  # início do "dia" nos testes
D1 = D0 + DAY


def buy(i, t, qty, price, fee=0.0, fee_in_base=0.0):
    return LedgerTrade(str(i), t, "buy", qty, price, fee, fee_in_base)


def sell(i, t, qty, price, fee=0.0):
    return LedgerTrade(str(i), t, "sell", qty, price, fee)


def day(trades, exit_price, start_price=None, fee=0.0, **caps):
    return compute_day(trades, day_start_ms=D0, day_end_ms=D1, exit_price=exit_price, exit_fee_rate=fee, day_start_price=start_price, **caps)


def test_ida_e_volta_com_lucro_e_taxas():
    # compra 0,1 ETH a 10.000 (taxa R$ 1), vende a 10.100 (taxa R$ 1,01)
    r = day([buy(1, D0 + 1, 0.1, 10_000, 1.0), sell(2, D0 + 2, 0.1, 10_100, 1.01)], exit_price=10_100)
    assert r.realized_quote == pytest.approx(1010 - 1.01 - 1000 - 1.0)
    assert r.open_qty == pytest.approx(0) and r.unrealized_quote == pytest.approx(0)
    assert r.fees_quote == pytest.approx(2.01)
    assert r.pnl_quote == pytest.approx(7.99)
    assert r.trades_today == 2


def test_taxa_paga_em_eth_reduz_a_quantidade():
    # compra 1 ETH a 100, taxa 0,001 ETH (= 0,1 em cotação)
    r = day([buy(1, D0 + 1, 1.0, 100, fee=0.1, fee_in_base=0.001)], exit_price=100)
    assert r.open_qty == pytest.approx(0.999)
    assert r.avg_cost_real == pytest.approx(100 / 0.999)
    assert r.unrealized_quote == pytest.approx(0.999 * 100 - 100)


def test_fifo_em_vendas_parciais():
    trades = [buy(1, D0 + 1, 1, 100), buy(2, D0 + 2, 1, 110), sell(3, D0 + 3, 1.5, 120)]
    r = day(trades, exit_price=120)
    # vende 1 do lote a 100 (+20) e 0,5 do lote a 110 (+5)
    assert r.realized_quote == pytest.approx(25)
    assert r.open_qty == pytest.approx(0.5)
    assert r.avg_cost_real == pytest.approx(110)
    assert r.unrealized_quote == pytest.approx(5)


def test_posicao_aberta_avaliada_pelo_bid_menos_taxa_de_venda():
    r = day([buy(1, D0 + 1, 1, 100)], exit_price=105, fee=0.001)
    assert r.unrealized_quote == pytest.approx(105 * 0.999 - 100)


def test_teto_em_eth_ignora_o_excesso():
    trades = [buy(1, D0 + 1, 0.3, 100), buy(2, D0 + 2, 0.3, 100)]
    r = day(trades, exit_price=100, cap_base=0.5)
    assert r.open_qty == pytest.approx(0.5)
    assert r.ignored_buy_qty == pytest.approx(0.1)


def test_teto_em_moeda_ignora_o_excesso():
    r = day([buy(1, D0 + 1, 2, 100)], exit_price=100, cap_quote=150)
    assert r.open_qty == pytest.approx(1.5)
    assert r.ignored_buy_qty == pytest.approx(0.5)


def test_venda_de_eth_de_fora_da_operacao_e_ignorada():
    # vendeu 1 ETH que você já tinha antes (o livro da operação estava vazio)
    r = day([sell(1, D0 + 1, 1, 100)], exit_price=100)
    assert r.realized_quote == 0 and r.ignored_sell_qty == pytest.approx(1)


def test_posicao_de_ontem_e_reavaliada_pelo_preco_das_00h():
    # comprou ontem a 100; à meia-noite valia 110; hoje vendeu a 115.
    # Para o limite de HOJE só contam os +5 de hoje (os +10 de ontem não).
    trades = [buy(1, D0 - 1000, 1, 100), sell(2, D0 + 1, 1, 115)]
    r = day(trades, exit_price=115, start_price=110)
    assert r.carried_qty == pytest.approx(1)
    assert r.realized_quote == pytest.approx(5)


def test_posicao_de_ontem_sem_preco_das_00h_bloqueia():
    with pytest.raises(MissingDayStartPrice):
        day([buy(1, D0 - 1000, 1, 100)], exit_price=100, start_price=None)


def test_operacoes_de_ontem_nao_entram_no_resultado_de_hoje():
    trades = [buy(1, D0 - 2000, 1, 100), sell(2, D0 - 1000, 1, 90), buy(3, D0 + 1, 1, 100)]
    r = day(trades, exit_price=100)
    assert r.realized_quote == 0 and r.trades_today == 1 and r.carried_qty == 0


def test_operacao_de_amanha_e_ignorada():
    r = day([buy(1, D1 + 1, 1, 100)], exit_price=100)
    assert r.trades_today == 0 and r.open_qty == 0


def test_operacao_invalida_e_recusada():
    with pytest.raises(ValueError):
        LedgerTrade("x", 0, "buy", 0, 100, 0)
    with pytest.raises(ValueError):
        LedgerTrade("x", 0, "buy", 1, 100, 0, fee_in_base=1)


def test_tetos_invalidos():
    with pytest.raises(ValueError):
        OperationLedger(cap_base=0)
