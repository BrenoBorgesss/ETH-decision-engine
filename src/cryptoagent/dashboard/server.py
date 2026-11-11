"""Servidor local do painel (só 127.0.0.1).

Proteção: as rotas que ALTERAM algo (sessão do dia, associação) exigem o cabeçalho X-Painel-Token,
um valor aleatório gerado a cada execução e embutido só na página servida aqui. Outro site aberto
no navegador não consegue ler a página nem mandar esse cabeçalho (o navegador bloqueia), então não
consegue mudar nada. Nenhuma rota envia ordens: o agente é somente leitura.
"""

from __future__ import annotations

import secrets as pysecrets
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cryptoagent.security.redact import redact
from cryptoagent.session.daily import SessionError

STATIC = Path(__file__).parent / "static"


class ServiceLike(Protocol):
    def snapshot(self) -> dict: ...
    def candles_payload(self, tf: str) -> dict: ...
    def season_payload(self) -> dict: ...
    def save_session(self, data: dict) -> dict: ...
    def associate(self, data: dict) -> None: ...


def create_app(service: ServiceLike, token: str | None = None) -> FastAPI:
    token = token or pysecrets.token_urlsafe(24)
    app = FastAPI(title="Agente cripto (somente leitura)", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def check(x_painel_token: str | None) -> None:
        if not x_painel_token or not pysecrets.compare_digest(x_painel_token, token):
            raise HTTPException(status_code=403, detail="token do painel ausente ou inválido")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    # Rotas "async" de propósito: rodam no mesmo loop (e na mesma thread) do serviço ao vivo,
    # que é dono da conexão SQLite. Rotas comuns iriam para outra thread e o SQLite recusa.
    @app.get("/api/estado")
    async def estado() -> JSONResponse:
        return JSONResponse(service.snapshot(), headers={"Cache-Control": "no-store"})

    @app.get("/api/candles")
    async def candles(tf: str = "15m") -> JSONResponse:
        return JSONResponse(service.candles_payload(tf))

    @app.get("/api/sazonalidade")
    async def sazonalidade() -> JSONResponse:
        return JSONResponse(service.season_payload())

    @app.post("/api/sessao")
    async def sessao(request: Request, x_painel_token: str | None = Header(default=None)) -> JSONResponse:
        check(x_painel_token)
        data = await request.json()
        try:
            return JSONResponse({"ok": True, "sessao": service.save_session(data)})
        except (SessionError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=redact(exc)) from None

    @app.post("/api/associar")
    async def associar(request: Request, x_painel_token: str | None = Header(default=None)) -> JSONResponse:
        check(x_painel_token)
        data = await request.json()
        try:
            service.associate(data)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=redact(exc)) from None
        return JSONResponse({"ok": True})

    return app
