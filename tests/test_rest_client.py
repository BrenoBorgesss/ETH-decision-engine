"""Cliente REST: somente leitura, assinatura correta, limites de requisição e segredos protegidos."""

import asyncio
import hashlib
import hmac
import logging
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from cryptoagent.config import Binance, Secrets
from cryptoagent.data.rest import (
    ALLOWED_PATHS,
    BinanceError,
    BinanceReadOnlyClient,
    ForbiddenEndpoint,
    MissingCredentials,
    RateLimited,
)

KEY = "testkey_ABCDEFGHIJKLMNOP"
SECRET = "testsecret_QRSTUVWXYZ012345"
SECRETS = Secrets(binance_api_key=SecretStr(KEY), binance_api_secret=SecretStr(SECRET))


class FakeClock:
    def __init__(self, start=1_800_000_000_000):
        self.t = start

    def __call__(self):
        return self.t


def make_client(handler, secrets=None, clock=None, **cfg):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    client = BinanceReadOnlyClient(
        Binance(**cfg), secrets, transport=httpx.MockTransport(handler), sleep=fake_sleep, clock=clock or FakeClock()
    )
    return client, sleeps


def run(coro):
    return asyncio.run(coro)


def test_endpoint_de_ordem_e_bloqueado_antes_de_sair_do_pc():
    calls = []
    client, _ = make_client(lambda req: calls.append(req) or httpx.Response(200, json={}), SECRETS)
    for path in ("/api/v3/order", "/sapi/v1/capital/withdraw/apply", "/api/v3/order/oco", "/fapi/v1/order"):
        with pytest.raises(ForbiddenEndpoint):
            run(client.get(path, {"symbol": "ETHUSDT"}))
    assert calls == []


def test_cliente_nao_tem_metodos_de_escrita():
    client, _ = make_client(lambda req: httpx.Response(200, json={}))
    for name in ("post", "put", "delete", "patch", "order", "new_order", "create_order", "withdraw", "transfer"):
        assert not hasattr(client, name)


def test_lista_permitida_nao_tem_nada_de_ordem_saque_ou_transferencia():
    for path in ALLOWED_PATHS:
        low = path.lower()
        assert "order" not in low and "withdraw" not in low and "transfer" not in low and "listenkey" not in low


def test_so_usa_metodo_get():
    methods = []

    def handler(req):
        methods.append(req.method)
        return httpx.Response(200, json={"serverTime": 1_800_000_000_000})

    client, _ = make_client(handler, SECRETS)
    run(client.get("/api/v3/time"))
    run(client.get("/api/v3/account"))
    assert set(methods) == {"GET"}


def test_assinatura_hmac_correta_e_chave_no_cabecalho():
    captured = {}

    def handler(req):
        captured["url"] = str(req.url)
        captured["key"] = req.headers.get("X-MBX-APIKEY")
        return httpx.Response(200, json={"balances": []})

    client, _ = make_client(handler, SECRETS)
    run(client.get("/api/v3/account", {"omitZeroBalances": "true"}))
    query = urlsplit(captured["url"]).query
    unsigned, signature = query.rsplit("&signature=", 1)
    expected = hmac.new(SECRET.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
    assert signature == expected
    assert captured["key"] == KEY
    params = dict(parse_qsl(unsigned))
    assert params["timestamp"] == "1800000000000" and params["recvWindow"] == "5000"
    assert SECRET not in captured["url"]


def test_endpoint_assinado_sem_chave_falha_claramente():
    client, _ = make_client(lambda req: httpx.Response(200, json={}), None)
    with pytest.raises(MissingCredentials):
        run(client.get("/api/v3/account"))


def test_erro_nao_expoe_url_assinada_nem_segredos(caplog):
    def handler(req):
        return httpx.Response(400, json={"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."})

    client, _ = make_client(handler, SECRETS)
    with caplog.at_level(logging.DEBUG), pytest.raises(BinanceError) as info:
        run(client.get("/api/v3/account"))
    message = str(info.value)
    assert "/api/v3/account" in message and "-2015" in message
    for leaked in ("signature", SECRET, KEY, "timestamp="):
        assert leaked not in message
        assert leaked not in caplog.text


def test_429_espera_retry_after_e_tenta_de_novo():
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "7"}, json={"code": -1003, "msg": "too many"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    client, sleeps = make_client(lambda req: next(responses))
    resp = run(client.get("/api/v3/ping"))
    assert resp.data == {"ok": 1}
    assert 7.0 in sleeps


def test_418_para_imediatamente():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(418, json={"code": -1003, "msg": "banned"})

    client, _ = make_client(handler)
    with pytest.raises(RateLimited):
        run(client.get("/api/v3/ping"))
    assert len(calls) == 1


def test_erro_500_e_falha_de_rede_sao_repetidos():
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("caiu")
        if state["n"] == 2:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json={"ok": True})

    client, _ = make_client(handler)
    assert run(client.get("/api/v3/ping")).data == {"ok": True}
    assert state["n"] == 3


def test_desiste_depois_do_maximo_de_tentativas():
    client, _ = make_client(lambda req: httpx.Response(503, text="x"), max_tentativas=3)
    with pytest.raises(BinanceError):
        run(client.get("/api/v3/ping"))


def test_pausa_antes_de_estourar_o_limite_de_peso():
    def handler(req):
        return httpx.Response(200, headers={"x-mbx-used-weight-1m": "4799"}, json={})

    clock = FakeClock(1_800_000_000_000)  # início exato de um minuto
    client, sleeps = make_client(handler, clock=clock)  # pausa acima de 80% de 6000 = 4800
    run(client.get("/api/v3/ping"))
    assert sleeps == []
    run(client.get("/api/v3/klines", {"symbol": "ETHUSDT", "interval": "1m"}))  # peso 2 -> 4801 > 4800
    assert len(sleeps) == 1 and 55 < sleeps[0] <= 61


def test_ressincroniza_relogio_no_erro_1021():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_800_000_002_000})
        if calls.count("/api/v3/account") == 1:
            return httpx.Response(400, json={"code": -1021, "msg": "Timestamp outside recvWindow"})
        return httpx.Response(200, json={"balances": []})

    client, _ = make_client(handler, SECRETS)
    resp = run(client.get("/api/v3/account"))
    assert resp.data == {"balances": []}
    assert client.time_offset_ms == 2000


def test_relogio_da_binance_corrige_pc_atrasado():
    # PC 31 s atrasado (caso real encontrado na primeira execução)
    client, _ = make_client(lambda req: httpx.Response(200, json={"serverTime": 1_800_000_031_000}))
    offset, _ = run(client.sync_time())
    assert offset == 31_000
    assert client.server_now_ms() == 1_800_000_031_000


def test_klines_retorna_dataframe_tipado():
    rows = [
        [
            1_800_000_000_000 + i * 60_000,
            "100",
            "101",
            "99",
            "100.5",
            "10",
            1_800_000_059_999 + i * 60_000,
            "1000",
            50,
            "6",
            "600",
            "0",
        ]
        for i in range(3)
    ]
    client, _ = make_client(lambda req: httpx.Response(200, json=rows))
    df, resp = run(client.klines("ETHUSDT", "1m", limit=3))
    assert len(df) == 3 and df["close"].dtype == "float64" and df["open_time"].dtype == "int64"
    assert resp.source == "binance-rest:/api/v3/klines"
