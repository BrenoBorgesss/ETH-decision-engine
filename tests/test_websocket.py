"""WebSocket: entrega das mensagens, erro no tratamento não derruba a conexão, reconexão e silêncio.

Usa um servidor WebSocket local (127.0.0.1, porta livre): nada sai da máquina.
Observação: o `stop` é conferido entre mensagens. Com a Binance (várias mensagens por segundo) isso é imediato;
aqui o servidor fecha a conexão ao fim de cada teste para o laço perceber o `stop` na hora.
"""

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from cryptoagent.data.websocket import BinanceStream


def msg(stream, data):
    return json.dumps({"stream": stream, "data": data})


async def run_with_server(server_handler, stream_kwargs, until, timeout=10.0):
    """Sobe o servidor local, roda o BinanceStream até `until(recebidas)` e devolve o estado NAQUELE momento."""
    received = []
    fim = asyncio.Event()

    async def server(conn):
        await server_handler(conn)
        await fim.wait()  # mantém a conexão aberta até o teste terminar

    async with serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        stop = asyncio.Event()

        async def handler(stream, data, t):
            received.append((stream, data, t))
            if data.get("explode"):
                raise RuntimeError("falha ao tratar a mensagem")

        ws = BinanceStream(f"ws://127.0.0.1:{port}", ["ethbrl@kline_1m", "ethbrl@bookTicker"], handler, **stream_kwargs)
        task = asyncio.create_task(ws.run(stop))
        try:
            async with asyncio.timeout(timeout):
                while not until(received):
                    await asyncio.sleep(0.01)
            state = {"reconnects": ws.reconnects, "last_error": ws.last_error, "connected": ws.connected}
        finally:
            stop.set()
            fim.set()
            await asyncio.wait_for(task, timeout=5)
    return ws, received, state


def test_entrega_mensagens_ignora_as_incompletas_e_sobrevive_a_erro_no_tratamento():
    paths = []

    async def server(conn):
        paths.append(conn.request.path)
        await conn.send(msg("ethbrl@kline_1m", {"k": 1}))
        await conn.send(json.dumps({"result": None, "id": 1}))  # resposta sem stream: ignorada
        await conn.send(msg("ethbrl@bookTicker", {"explode": True}))  # o tratamento falha...
        await conn.send(msg("ethbrl@kline_1m", {"k": 2}))  # ...e a conexão continua

    ws, got, state = asyncio.run(run_with_server(server, {}, lambda r: len(r) >= 3))
    assert [(s, d) for s, d, _ in got] == [
        ("ethbrl@kline_1m", {"k": 1}),
        ("ethbrl@bookTicker", {"explode": True}),
        ("ethbrl@kline_1m", {"k": 2}),
    ]
    assert paths == ["/stream?streams=ethbrl@kline_1m/ethbrl@bookTicker"]
    assert state == {"reconnects": 0, "last_error": None, "connected": True}
    assert ws.last_message_ms == got[-1][2]
    assert ws.connected is False  # depois de parar


def test_reconecta_quando_o_servidor_derruba_a_conexao():
    conexoes = []

    async def server(conn):
        conexoes.append(1)
        await conn.send(msg("ethbrl@kline_1m", {"conexao": len(conexoes)}))
        if len(conexoes) == 1:
            await conn.close(code=1011, reason="queda de teste")

    _, got, state = asyncio.run(run_with_server(server, {"max_backoff_s": 0.05}, lambda r: len(r) >= 2))
    assert [d["conexao"] for _, d, _ in got] == [1, 2]
    assert state["reconnects"] == 1 and "ConnectionClosed" in state["last_error"]


def test_silencio_longo_e_tratado_como_conexao_morta(monkeypatch):
    conexoes = []

    async def server(conn):
        conexoes.append(1)
        if len(conexoes) >= 2:  # a primeira conexão fica muda
            await conn.send(msg("ethbrl@kline_1m", {"voltou": True}))

    monkeypatch.setattr(BinanceStream, "SILENCE_TIMEOUT_S", 0.3)
    _, got, state = asyncio.run(run_with_server(server, {"max_backoff_s": 0.05}, lambda r: len(r) >= 1))
    assert got[0][1] == {"voltou": True}
    assert state["reconnects"] == 1 and "sem mensagens há 0.3 s" in state["last_error"]


def test_sem_streams_e_recusado():
    with pytest.raises(ValueError, match="nenhum stream"):
        BinanceStream("ws://127.0.0.1:1", [], lambda *a: None)
