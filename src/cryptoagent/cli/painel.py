"""Comandos `painel` e `alertas testar`."""

from __future__ import annotations

import argparse
import asyncio

from rich.markup import escape

from cryptoagent.cli._shared import console
from cryptoagent.config import Config, Secrets
from cryptoagent.timeutils import now_ms

# ------------------------------------------------------------------- painel


async def cmd_panel(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    import webbrowser

    import uvicorn

    from cryptoagent.dashboard.server import create_app
    from cryptoagent.dashboard.service import LiveService

    service = LiveService(cfg, secrets, console)
    app = create_app(service)
    port = args.porta or cfg.painel.porta
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    url = f"http://127.0.0.1:{port}/"
    console.print(
        f"Painel em [bold]{url}[/] (só neste computador). Análise a cada fechamento de {', '.join(cfg.painel.recalcular_ao_fechar)}"
        + (f" e a cada {cfg.painel.analise_extra_a_cada_s:g} s" if cfg.painel.analise_extra_a_cada_s else "")
        + ". Ctrl+C para parar."
    )
    if cfg.painel.abrir_navegador and not args.sem_navegador:
        asyncio.get_running_loop().call_later(3, webbrowser.open, url)
    await service.run(extra_tasks=[asyncio.create_task(server.serve())])
    server.should_exit = True
    if service.fatal:
        console.print(f"[bold red]RECUSADO:[/] {escape(service.fatal)}")
        return 3
    return 0


async def cmd_alert_test(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    from cryptoagent.alerts.notify import Alert, Alerter

    Alerter(cfg.alertas, console, cfg.fuso.local).send(Alert(now_ms(), "Teste de alerta do agente", "Se você está vendo isto, as notificações funcionam."))
    console.print("Enviado. Se a notificação do Windows não apareceu, confira em Configurações → Sistema → Notificações se o Windows PowerShell pode notificar.")
    return 0
