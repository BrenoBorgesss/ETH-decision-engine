"""Cliente REST da Binance SOMENTE LEITURA.

Garantias (verificadas nos testes):
- Só existe requisição GET. Não há método para ordens, saques ou transferências.
- Cada caminho precisa estar em ALLOWED_PATHS; qualquer outro é recusado antes de
  sair do computador.
- A chave e o secret só são usados para assinar a requisição. Mensagens de erro e
  logs mostram apenas o caminho (ex.: /api/v3/account), nunca a URL assinada.
- Respeita o limite de peso por minuto da Binance (pausa antes de estourar),
  trata 429 (espera o Retry-After) e 418 (para tudo: IP bloqueado).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import pandas as pd

from cryptoagent.config import Binance as BinanceCfg
from cryptoagent.config import Secrets
from cryptoagent.data.models import klines_to_frame
from cryptoagent.data.retry import STATUS_IP_BANNED, STATUS_RATE_LIMITED, RetryPolicy, should_retry_status
from cryptoagent.timeutils import now_ms

log = logging.getLogger(__name__)

# O httpx registra a URL completa (com a assinatura) em INFO. Silenciamos aqui mesmo,
# sem depender da configuração de logs de quem usa o cliente.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Caminho -> peso aproximado (consumo do limite por minuto).
PUBLIC_SPOT: dict[str, int] = {
    "/api/v3/ping": 1,
    "/api/v3/time": 1,
    "/api/v3/exchangeInfo": 20,
    "/api/v3/ticker/price": 2,
    "/api/v3/ticker/bookTicker": 2,
    "/api/v3/ticker/24hr": 2,
    "/api/v3/depth": 5,
    "/api/v3/klines": 2,
    "/api/v3/aggTrades": 4,
}
# Endpoints assinados, todos de LEITURA (usados na etapa da conta).
SIGNED_READ: dict[str, int] = {
    "/api/v3/account": 20,
    "/api/v3/myTrades": 20,
    "/api/v3/account/commission": 20,
    "/sapi/v1/account/apiRestrictions": 1,
}
PUBLIC_FUTURES: dict[str, int] = {
    "/fapi/v1/premiumIndex": 1,
    "/fapi/v1/openInterest": 1,
    "/fapi/v1/fundingRate": 1,
    "/futures/data/openInterestHist": 1,
}
ALLOWED_PATHS: dict[str, int] = {**PUBLIC_SPOT, **SIGNED_READ, **PUBLIC_FUTURES}


# Explicação em português para os erros mais comuns da Binance.
ERROR_HINTS: dict[int, str] = {
    -2015: (
        "Chave inválida, IP não autorizado ou permissão faltando. Se você ligou a restrição por IP, confira se o IP "
        "cadastrado na Binance é o seu IP PÚBLICO atual (comando: Invoke-RestMethod https://ipv4.icanhazip.com)."
    ),
    -2014: "Formato da chave inválido: confira BINANCE_API_KEY no .env (sem espaços nem aspas).",
    -1022: "Assinatura inválida: confira BINANCE_API_SECRET no .env (sem espaços nem aspas).",
    -1021: "Relógio do PC fora de sincronia com a Binance: sincronize o relógio do Windows.",
}


class BinanceError(Exception):
    """Erro numa consulta à Binance. A mensagem traz só o caminho do endpoint (nunca a URL assinada) e, quando há, uma dica em português."""
    def __init__(self, message: str, *, path: str, status: int | None = None, code: int | None = None) -> None:
        self.path = path
        self.status = status
        self.code = code
        detail = "".join(x for x in (f", status {status}" if status else "", f", código {code}" if code else ""))
        hint = f" -> {ERROR_HINTS[code]}" if code in ERROR_HINTS else ""
        super().__init__(f"{message} (endpoint {path}{detail}){hint}")


class ForbiddenEndpoint(BinanceError):
    """Caminho fora da lista somente leitura."""


class MissingCredentials(BinanceError):
    """Endpoint assinado sem chave no .env."""


class RateLimited(BinanceError):
    """418: a Binance bloqueou o IP temporariamente. Não insistimos."""


@dataclass(frozen=True)
class ApiResponse:
    """Resposta de uma consulta: dados, caminho, horário de chegada, latência e peso usado no minuto."""
    data: object
    path: str
    received_ms: int
    latency_ms: float
    used_weight_1m: int | None

    @property
    def source(self) -> str:
        """Identificação da fonte, no formato binance-rest:<caminho>."""
        return f"binance-rest:{self.path}"


class BinanceReadOnlyClient:
    """Cliente REST da Binance que só faz GET em caminhos de leitura (ver a descrição do módulo)."""
    def __init__(
        self,
        cfg: BinanceCfg,
        secrets: Secrets | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._cfg = cfg
        self._secrets = secrets
        self._http = httpx.AsyncClient(
            timeout=cfg.timeout_s,
            transport=transport,
            headers={"User-Agent": "cryptoagent/0.1 (somente-leitura)"},
        )
        self._sleep = sleep
        self._clock = clock
        self._retry = RetryPolicy(
            attempts=cfg.max_tentativas, base_s=cfg.espera_inicial_s, max_s=cfg.espera_max_s,
            retry_after_default_s=cfg.espera_429_padrao_s, retry_after_max_s=cfg.espera_429_max_s,
        )
        self._used_weight = 0
        self._weight_minute = -1
        self.time_offset_ms = 0  # relógio da Binance - relógio local

    async def __aenter__(self) -> BinanceReadOnlyClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Fecha a conexão HTTP."""
        await self._http.aclose()

    @property
    def used_weight_1m(self) -> int:
        """Peso da API usado no minuto atual, segundo a última resposta."""
        return self._used_weight

    def server_now_ms(self) -> int:
        """Agora, no relógio da Binance (relógio local corrigido pelo último sync_time).

        Use para comparar com horários da corretora (ex.: fechamento de candle).
        Para "há quanto tempo chegou" use o relógio local, que é o mesmo do received_ms.
        """
        return self._clock() + self.time_offset_ms

    # ------------------------------------------------------------------ núcleo

    async def get(self, path: str, params: dict | None = None) -> ApiResponse:
        """Faz um GET num caminho permitido, assinando se for endpoint de conta. Respeita o limite de peso e repete em falha de rede, 429 e erro 5xx; levanta BinanceError nos demais casos."""
        if path not in ALLOWED_PATHS:
            raise ForbiddenEndpoint("Endpoint fora da lista somente leitura; requisição bloqueada", path=path)
        signed = path in SIGNED_READ
        if signed and not (self._secrets and self._secrets.has_binance):
            raise MissingCredentials("Chave da Binance ausente no .env", path=path)
        base = self._cfg.futures_url if path.startswith(("/fapi/", "/futures/")) else self._cfg.rest_url
        weight = ALLOWED_PATHS[path]

        last_error: BinanceError | None = None
        resynced = False
        for attempt in range(self._retry.attempts):
            await self._respect_weight(weight)
            url, headers = self._build_request(base, path, params, signed)
            t0 = time.perf_counter()
            try:
                resp = await self._http.get(url, headers=headers)
            except httpx.TransportError as exc:
                last_error = BinanceError(f"Falha de rede ({type(exc).__name__})", path=path)
                log.warning("Falha de rede em %s (tentativa %d): %s", path, attempt + 1, type(exc).__name__)
                await self._backoff(attempt)
                continue
            latency_ms = (time.perf_counter() - t0) * 1000
            received = self._clock()
            self._track_weight(resp, received)

            if resp.status_code == 200:
                return ApiResponse(resp.json(), path, received, latency_ms, self._used_weight)

            code, msg = _parse_error(resp)
            if resp.status_code == STATUS_IP_BANNED:
                raise RateLimited("A Binance bloqueou o IP temporariamente por excesso de requisições", path=path, status=STATUS_IP_BANNED, code=code)
            if resp.status_code == STATUS_RATE_LIMITED:
                wait = self._retry.retry_after(resp.headers)
                log.warning("Limite de requisições atingido em %s; aguardando %.0f s", path, wait)
                last_error = BinanceError("Limite de requisições atingido", path=path, status=STATUS_RATE_LIMITED, code=code)
                await self._sleep(wait)
                continue
            if should_retry_status(resp.status_code):
                last_error = BinanceError("Erro no servidor da Binance", path=path, status=resp.status_code, code=code)
                log.warning("Erro %d da Binance em %s (tentativa %d)", resp.status_code, path, attempt + 1)
                await self._backoff(attempt)
                continue
            if code == -1021 and signed and not resynced:
                # Timestamp fora da janela: relógio do PC adiantado/atrasado. Ressincroniza uma vez.
                resynced = True
                await self.sync_time()
                continue
            raise BinanceError(f"A Binance recusou a consulta: {msg}", path=path, status=resp.status_code, code=code)

        raise last_error or BinanceError("Falhou após várias tentativas", path=path)

    def _build_request(self, base: str, path: str, params: dict | None, signed: bool) -> tuple[str, dict[str, str]]:
        query = dict(params or {})
        headers: dict[str, str] = {}
        if signed:
            assert self._secrets and self._secrets.binance_api_key and self._secrets.binance_api_secret
            query["timestamp"] = self._clock() + self.time_offset_ms
            query["recvWindow"] = self._cfg.recv_window_ms
            qs = urlencode(query)
            secret = self._secrets.binance_api_secret.get_secret_value().encode()
            qs += "&signature=" + hmac.new(secret, qs.encode(), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = self._secrets.binance_api_key.get_secret_value()
        else:
            qs = urlencode(query)
        return f"{base}{path}" + (f"?{qs}" if qs else ""), headers

    async def _respect_weight(self, weight: int) -> None:
        now = self._clock()
        minute = now // 60_000
        if minute != self._weight_minute:
            self._weight_minute = minute
            self._used_weight = 0
        threshold = self._cfg.limite_peso_por_minuto * self._cfg.pausar_acima_de_pct_peso / 100
        if self._used_weight + weight > threshold:
            wait = (60_000 - now % 60_000 + 500) / 1000
            log.warning("Peso da API em %d/%d; pausando %.1f s", self._used_weight, self._cfg.limite_peso_por_minuto, wait)
            await self._sleep(wait)
            self._weight_minute = self._clock() // 60_000
            self._used_weight = 0

    def _track_weight(self, resp: httpx.Response, received_ms: int) -> None:
        header = resp.headers.get("x-mbx-used-weight-1m")
        if header and header.isdigit():
            self._used_weight = int(header)
            self._weight_minute = received_ms // 60_000

    async def _backoff(self, attempt: int) -> None:
        await self._sleep(self._retry.delay(attempt))

    # ------------------------------------------------------------ utilidades

    async def sync_time(self) -> tuple[int, float]:
        """Mede a diferença entre o relógio do PC e o da Binance. Retorna (offset_ms, ida_e_volta_ms)."""
        t0 = self._clock()
        resp = await self.get("/api/v3/time")
        t1 = self._clock()
        server = int(resp.data["serverTime"])  # type: ignore[index]
        self.time_offset_ms = server - (t0 + t1) // 2
        return self.time_offset_ms, float(t1 - t0)

    async def klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 500,
    ) -> tuple[pd.DataFrame, ApiResponse]:
        """Candles do par e intervalo como DataFrame normalizado, junto com a resposta bruta."""
        params: dict[str, object] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        resp = await self.get("/api/v3/klines", params)
        return klines_to_frame(resp.data), resp  # type: ignore[arg-type]


def _parse_error(resp: httpx.Response) -> tuple[int | None, str]:
    try:
        body = resp.json()
        return int(body.get("code")), str(body.get("msg", ""))[:200]
    except Exception:
        return None, f"HTTP {resp.status_code}"

