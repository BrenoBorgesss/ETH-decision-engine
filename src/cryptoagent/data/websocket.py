"""WebSocket da Binance com reconexão automática.

- Usa o "combined stream" (/stream?streams=a/b/c): cada mensagem diz de qual stream veio.
- Reconecta sozinho, com espera crescente (1 s, 2 s, 4 s... até 60 s) e um pouco de
  aleatoriedade para não martelar o servidor.
- A Binance encerra conexões com 24 h; reconectamos antes, com 23 h.
- Se ficar 30 s sem nenhuma mensagem, considera a conexão morta e reconecta.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import connect

from cryptoagent.timeutils import now_ms

log = logging.getLogger(__name__)

Handler = Callable[[str, dict, int], Awaitable[None] | None]


class BinanceStream:
    """Conexão WebSocket com a Binance para vários streams, com reconexão automática. Só recebe dados; nunca envia ordens."""

    MAX_CONNECTION_S = 23 * 3600
    SILENCE_TIMEOUT_S = 30

    def __init__(
        self, ws_url: str, streams: list[str], handler: Handler, *, name: str = "ws", max_backoff_s: float = 60
    ) -> None:
        if not streams:
            raise ValueError("nenhum stream informado")
        self.url = f"{ws_url}/stream?streams=" + "/".join(streams)
        self.name = name
        self._handler = handler
        self._max_backoff = max_backoff_s
        self.connected = False
        self.last_message_ms: int | None = None
        self.reconnects = 0
        self.last_error: str | None = None

    async def run(self, stop: asyncio.Event) -> None:
        """Mantém a conexão até `stop` ser acionado, entregando cada mensagem ao handler e reconectando com espera crescente quando cai."""
        backoff = 1.0
        while not stop.is_set():
            try:
                async with connect(self.url, ping_interval=20, ping_timeout=20, open_timeout=10, max_queue=2048) as ws:
                    self.connected = True
                    started = time.monotonic()
                    log.info("WebSocket %s conectado", self.name)
                    while not stop.is_set():
                        if time.monotonic() - started > self.MAX_CONNECTION_S:
                            log.info("WebSocket %s: reconexão preventiva (23 h)", self.name)
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=self.SILENCE_TIMEOUT_S)
                        except TimeoutError:
                            raise ConnectionError(f"sem mensagens há {self.SILENCE_TIMEOUT_S} s") from None
                        received = now_ms()
                        msg = json.loads(raw)
                        stream, data = msg.get("stream"), msg.get("data")
                        if stream is None or data is None:
                            continue
                        self.last_message_ms = received
                        await self._dispatch(stream, data, received)
                        if time.monotonic() - started > 60:
                            backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("WebSocket %s caiu: %s", self.name, self.last_error)
            finally:
                self.connected = False

            if stop.is_set():
                break
            self.reconnects += 1
            delay = min(backoff, self._max_backoff) * (0.5 + random.random())
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, self._max_backoff)

    async def _dispatch(self, stream: str, data: dict, received: int) -> None:
        try:
            result = self._handler(stream, data, received)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Erro no tratamento de UMA mensagem não derruba a conexão.
            log.exception("Erro ao processar mensagem do stream %s", stream)
