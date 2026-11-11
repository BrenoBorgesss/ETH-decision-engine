"""Status da conta ponta a ponta, com uma Binance simulada (nenhuma chamada real)."""

import asyncio
from datetime import date, datetime

import httpx
import pytest
from pydantic import SecretStr

from cryptoagent.account import journal
from cryptoagent.account.binance_account import parse_commission, sync_trades
from cryptoagent.account.permissions import ExcessivePermissions
from cryptoagent.account.status import AccountMonitor
from cryptoagent.config import Config, Secrets
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.session import daily
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import UTC, datetime_to_ms

NOW = datetime_to_ms(datetime(2026, 10, 22, 15, 0, tzinfo=UTC))  # 12:00 em Brasília
DAY_START = datetime_to_ms(datetime(2026, 10, 22, 3, 0, tzinfo=UTC))  # 00:00 em Brasília
T_TRADE = datetime_to_ms(datetime(2026, 10, 22, 13, 0, tzinfo=UTC))
SECRETS = Secrets(binance_api_key=SecretStr("k" * 20), binance_api_secret=SecretStr("s" * 20))
READ_ONLY = {"ipRestrict": True, "enableReading": True, "enableWithdrawals": False, "enableSpotAndMarginTrading": False}

TRADES_ETHBRL = [
    {"id": 1, "orderId": 10, "price": "11800", "qty": "0.05", "quoteQty": "590", "commission": "0.00005", "commissionAsset": "ETH", "time": T_TRADE, "isBuyer": True, "isMaker": False},
    {"id": 2, "orderId": 11, "price": "12100", "qty": "0.02", "quoteQty": "242", "commission": "0.242", "commissionAsset": "BRL", "time": T_TRADE + 60_000, "isBuyer": False, "isMaker": False},
]


class FakeBinance:
    def __init__(self, *, restrictions=None, trades=None, account_status=200, balances=None):
        self.restrictions = restrictions or READ_ONLY
        self.trades = TRADES_ETHBRL if trades is None else trades
        self.account_status = account_status
        self.balances = balances or [{"asset": "BRL", "free": "500", "locked": "0"}, {"asset": "ETH", "free": "2.02995", "locked": "0"}]
        self.paths = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, q = req.url.path, dict(req.url.params)
        self.paths.append(path)
        assert req.method == "GET"
        if path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": NOW})
        if path == "/sapi/v1/account/apiRestrictions":
            return httpx.Response(200, json=self.restrictions)
        if path == "/api/v3/ticker/bookTicker":
            books = {"ETHBRL": ("12000", "12010"), "ETHUSDT": ("2400", "2400.1"), "USDTBRL": ("5.0", "5.02")}
            bid, ask = books[q["symbol"]]
            return httpx.Response(200, json={"symbol": q["symbol"], "bidPrice": bid, "askPrice": ask})
        if path == "/api/v3/klines":
            t = int(q["startTime"])
            opens = {"ETHBRL": "11900", "ETHUSDT": "2380"}
            o = opens.get(q["symbol"], "1")
            return httpx.Response(200, json=[[t, o, o, o, o, "1", t + 59_999, "1", 1, "1", "1", "0"]])
        if path == "/api/v3/account":
            if self.account_status != 200:
                return httpx.Response(self.account_status, json={"code": -1000, "msg": "erro interno"})
            return httpx.Response(200, json={"balances": self.balances})
        if path == "/api/v3/account/commission":
            return httpx.Response(200, json={"standardCommission": {"maker": "0.001", "taker": "0.001"}, "discount": {"enabledAccount": False}})
        if path == "/api/v3/myTrades":
            start, end = int(q.get("startTime", 0)), int(q.get("endTime", 10**15))
            return httpx.Response(200, json=[t for t in self.trades if start <= t["time"] <= end and q["symbol"] == q["symbol"]])
        return httpx.Response(404, json={"code": -1, "msg": "rota não simulada"})


@pytest.fixture
def conn(tmp_path):
    c = agent_db.connect(tmp_path / "agent.sqlite")
    yield c
    c.close()


def refresh(conn, fake, *, secrets=SECRETS, cfg=None, session=True, par="ETHBRL", saldo="BRL", capital=1000, max_loss=30):
    cfg = cfg or Config()
    if session:
        daily.save_session(conn, date(2026, 10, 22), capital_brl=capital, max_loss_brl=max_loss, par_operacao=par, saldo_em=saldo, candidates=cfg.mercado.pares_candidatos, now=NOW)

    async def nosleep(_):
        return None

    async def go():
        async with BinanceReadOnlyClient(cfg.binance, secrets, transport=httpx.MockTransport(fake.handler), sleep=nosleep, clock=lambda: NOW) as client:
            return await AccountMonitor(cfg, secrets, client, conn).refresh()

    return asyncio.run(go())


def expected_ethbrl_pnl():
    cost = 11800 * 0.05 / 0.04995
    realized = 0.02 * ((242 - 0.242) / 0.02 - cost)
    unrealized = 0.02995 * (12000 * 0.999 - cost)
    return realized + unrealized


def test_caminho_feliz_resultado_do_dia(conn):
    fake = FakeBinance()
    st = refresh(conn, fake)
    assert st.reasons == []
    assert st.verified and st.symbol == "ETHBRL"
    assert st.day.open_qty == pytest.approx(0.02995)
    assert st.pnl_brl == pytest.approx(expected_ethbrl_pnl())
    assert st.limits.remaining_brl == pytest.approx(30)  # lucro não amplia o limite
    assert st.limits.risk_per_trade_brl == pytest.approx(10)
    # o ETH de fora da operação (2 ETH) NÃO entra no disponível
    assert st.available_brl == pytest.approx(500 + 0.02995 * 12000)
    assert any("maior que o disponível" in w for w in st.warnings)
    assert st.effective_capital_brl == pytest.approx(st.available_brl)


def test_sem_sessao_do_dia_bloqueia(conn):
    st = refresh(conn, FakeBinance(), session=False)
    assert st.blocked and any("Sessão do dia" in r for r in st.reasons)


def test_sem_chave_bloqueia_sem_presumir_valores(conn):
    fake = FakeBinance()
    st = refresh(conn, fake, secrets=Secrets())
    assert st.blocked and any("Chave da Binance ausente" in r for r in st.reasons)
    assert st.pnl_brl is None and st.balances is None
    assert "/api/v3/account" not in fake.paths


def test_chave_com_permissao_de_trading_recusa_rodar(conn):
    with pytest.raises(ExcessivePermissions):
        refresh(conn, FakeBinance(restrictions={**READ_ONLY, "enableSpotAndMarginTrading": True}))


def test_chave_com_saque_recusa_rodar(conn):
    with pytest.raises(ExcessivePermissions):
        refresh(conn, FakeBinance(restrictions={**READ_ONLY, "enableWithdrawals": True}))


def test_falha_na_leitura_do_saldo_bloqueia(conn):
    st = refresh(conn, FakeBinance(account_status=500))
    assert st.blocked and any("Falha ao ler a conta" in r for r in st.reasons)
    assert st.pnl_brl is None


def test_perda_maxima_atingida_bloqueia(conn):
    losing = [
        {"id": 1, "orderId": 1, "price": "12000", "qty": "0.05", "quoteQty": "600", "commission": "0.6", "commissionAsset": "BRL", "time": T_TRADE, "isBuyer": True},
        {"id": 2, "orderId": 2, "price": "11300", "qty": "0.05", "quoteQty": "565", "commission": "0.565", "commissionAsset": "BRL", "time": T_TRADE + 1, "isBuyer": False},
    ]
    st = refresh(conn, FakeBinance(trades=losing), max_loss=30)
    assert st.pnl_brl == pytest.approx(565 - 0.565 - 600.6)
    assert st.limits.blocked
    assert any("PERDA MÁXIMA DO DIA ATINGIDA" in r for r in st.reasons)


def test_ethusdt_converte_resultado_pelo_cambio(conn):
    trades = [{"id": 5, "orderId": 5, "price": "2390", "qty": "0.1", "quoteQty": "239", "commission": "0.239", "commissionAsset": "USDT", "time": T_TRADE, "isBuyer": True}]
    fake = FakeBinance(trades=trades, balances=[{"asset": "USDT", "free": "100", "locked": "0"}, {"asset": "ETH", "free": "0.1", "locked": "0"}])
    cfg = Config.model_validate({"capital_operacao": {"modo": "brl", "valor": 5000}})
    st = refresh(conn, fake, par="ETHUSDT", saldo="USDT", cfg=cfg)
    fx = (5.0 + 5.02) / 2
    pnl_usdt = 0.1 * 2400 * 0.999 - (239 + 0.239)
    assert st.fx_usdtbrl == pytest.approx(fx)
    assert st.pnl_brl == pytest.approx(pnl_usdt * fx)


def test_teto_do_capital_de_operacao_em_reais_vale_tambem_no_ethusdt(conn):
    # Teto de R$ 1.000 = 1000 / 5,01 USDT. A compra de 239,239 USDT passa do teto: só parte entra.
    trades = [{"id": 5, "orderId": 5, "price": "2390", "qty": "0.1", "quoteQty": "239", "commission": "0.239", "commissionAsset": "USDT", "time": T_TRADE, "isBuyer": True}]
    st = refresh(conn, FakeBinance(trades=trades, balances=[{"asset": "USDT", "free": "100", "locked": "0"}, {"asset": "ETH", "free": "0.1", "locked": "0"}]), par="ETHUSDT", saldo="USDT")
    cap_usdt = 1000 / ((5.0 + 5.02) / 2)
    assert st.day.open_qty == pytest.approx(cap_usdt / 2392.39)
    assert st.day.ignored_buy_qty == pytest.approx(0.1 - cap_usdt / 2392.39)
    assert any("passaram do teto" in w for w in st.warnings)


def test_diario_manual_funciona_sem_chave_mas_nao_e_verificado(conn):
    cfg = Config.model_validate({"conta": {"modo": "diario_manual"}})
    journal.add_manual_trade(conn, symbol="ETHBRL", side="buy", qty=0.05, price=11800, fee_quote=0.59, time_ms=T_TRADE)
    st = refresh(conn, FakeBinance(), secrets=Secrets(), cfg=cfg)
    assert not st.verified and st.reasons == []
    assert any("NÃO verificado" in w for w in st.warnings)
    assert st.day.open_qty == pytest.approx(0.05)
    assert st.pnl_brl == pytest.approx(0.05 * 12000 * 0.999 - 590.59)


def test_comissao_com_desconto_bnb():
    info = parse_commission(
        "ETHBRL",
        {
            "standardCommission": {"maker": "0.001", "taker": "0.001"},
            "taxCommission": {"maker": "0", "taker": "0.0001"},
            "discount": {"enabledAccount": True, "enabledSymbol": True, "discountAsset": "BNB", "discount": "0.75"},
        },
    )
    assert info.taker == pytest.approx(0.0011)
    assert info.effective("taker", has_bnb=True) == pytest.approx(0.0011 * 0.75)
    assert info.effective("taker", has_bnb=False) == pytest.approx(0.0011)


def test_sincronizacao_em_janelas_de_24h_e_sem_duplicar(conn):
    day = 86_400_000
    trades = [
        {"id": i, "orderId": i, "price": "100", "qty": "1", "quoteQty": "100", "commission": "0.1", "commissionAsset": "BRL", "time": DAY_START + i * day // 2, "isBuyer": True}
        for i in range(5)
    ]
    fake = FakeBinance(trades=trades)
    windows = []

    def handler(req):
        if req.url.path == "/api/v3/myTrades":
            q = dict(req.url.params)
            windows.append((int(q["startTime"]), int(q["endTime"])))
            assert int(q["endTime"]) - int(q["startTime"]) < day  # limite da Binance: 24 h
        return fake.handler(req)

    async def nosleep(_):
        return None

    async def price_at(symbol, t):
        return None

    async def go(now):
        async with BinanceReadOnlyClient(Config().binance, SECRETS, transport=httpx.MockTransport(handler), sleep=nosleep, clock=lambda: now) as client:
            return await sync_trades(client, conn, "ETHBRL", server_now_ms=now, start_ms=DAY_START, price_at=price_at, fallback_fee_rate=0.001)

    asyncio.run(go(DAY_START + 2 * day + 1000))
    assert len(windows) == 3
    asyncio.run(go(DAY_START + 2 * day + 5000))  # roda de novo: não duplica
    count = conn.execute("SELECT COUNT(*) FROM account_trades").fetchone()[0]
    assert count == 5


# ------------------------------------------------------------- Etapa 9: posição x saldo de ETH


def test_posicao_conferida_com_o_saldo_de_eth(conn):
    """9.3: a posição vem do histórico de operações, mas precisa existir no saldo de ETH lido da conta."""
    st = refresh(conn, FakeBinance())
    assert st.reasons == [] and any("2.00000000 ETH na conta estão fora da operação" in w for w in st.warnings)
    sumiu = FakeBinance(balances=[{"asset": "BRL", "free": "500", "locked": "0"}, {"asset": "ETH", "free": "0.01", "locked": "0"}])
    st = refresh(conn, sumiu)
    assert st.blocked and any("maior que o saldo de ETH na conta" in r for r in st.reasons)
    assert st.limits is None  # nada é calculado em cima de uma posição que não existe


def test_eth_em_ordem_aberta_conta_como_saldo(conn):
    travado = FakeBinance(balances=[{"asset": "BRL", "free": "500", "locked": "0"}, {"asset": "ETH", "free": "0", "locked": "0.02995"}])
    st = refresh(conn, travado)
    assert st.reasons == [] and st.day.open_qty == pytest.approx(0.02995)


def test_saldo_lido_ha_muito_tempo_bloqueia(conn, monkeypatch):
    """B7: conta.saldo_max_s agora é aplicado (antes existia no config e nenhum código lia)."""
    from cryptoagent.data import rest

    monkeypatch.setattr(rest.BinanceReadOnlyClient, "local_now_ms", lambda self: NOW + 121_000)
    st = refresh(conn, FakeBinance())
    assert st.blocked and any("saldo velha demais" in r for r in st.reasons)
