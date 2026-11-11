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
    """Comando `painel`: sobe o painel local em 127.0.0.1 e o serviço de análise ao vivo. Devolve o código de saída do processo."""
    import webbrowser

    import uvicorn

    from cryptoagent.dashboard.server import create_app
    from cryptoagent.dashboard.service import LiveService

    if getattr(args, "demo", False):
        service = _demo_service(cfg, args)
    else:
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


def _demo_service(cfg: Config, args: argparse.Namespace) -> object:
    """Painel da demonstração: replay acelerado da amostra, com carteira virtual (ver dashboard/demo.py)."""
    from datetime import datetime, timedelta

    from cryptoagent.backtest.costs import load_schedules
    from cryptoagent.dashboard.demo import DemoService
    from cryptoagent.timeutils import datetime_to_ms, zone

    start = datetime.fromisoformat(args.inicio or "2026-09-08T09:30").replace(tzinfo=zone(cfg.fuso.local)) + timedelta(seconds=cfg.paper.atraso_s)
    start_ms = datetime_to_ms(start)
    costs = load_schedules(cfg.path("exemplos/custos_demo.json"))[cfg.paper.par]
    console.print(
        f"[bold cyan]Painel de demonstração[/]: carteira VIRTUAL sobre o histórico de exemplo, a partir de {start:%d/%m/%Y %H:%M}, "
        f"{args.velocidade:g} minuto(s) simulado(s) por segundo. Nada é real."
    )
    return DemoService(cfg, console, start_ms=start_ms, end_ms=start_ms + args.horas * 3_600_000, costs=costs, minutes_per_second=args.velocidade)


async def cmd_alert_test(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `alertas testar`: envia um aviso de teste. Devolve o código de saída do processo."""
    from cryptoagent.alerts.notify import Alert, Alerter

    Alerter(cfg.alertas, console, cfg.fuso.local).send(Alert(now_ms(), "Teste de alerta do agente", "Se você está vendo isto, as notificações funcionam."))
    console.print("Enviado. Se a notificação do Windows não apareceu, confira em Configurações → Sistema → Notificações se o Windows PowerShell pode notificar.")
    return 0
