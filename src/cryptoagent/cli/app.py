"""Monta o analisador de argumentos (todos os comandos) e a função `main`.

Os comandos são referenciados por nome ("módulo:função") e só são importados na hora de rodar.
Assim `--help` e `--version` respondem sem carregar pandas, httpx e o resto do programa.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from rich.markup import escape

from cryptoagent import DISCLAIMER, __version__
from cryptoagent.cli._console import console
from cryptoagent.intervals import INTERVAL_MS

log = logging.getLogger("cryptoagent")


def load_command(ref: str) -> Callable:
    """Importa e devolve o comando indicado por "módulo:função" (módulo dentro de `cryptoagent.cli`)."""
    module, name = ref.split(":")
    return getattr(importlib.import_module(f"cryptoagent.cli.{module}"), name)


# -------------------------------------------------------------------- main

# Comandos que funcionam no modo demonstração: só leem o histórico de exemplo, sem rede e sem chave.
DEMO_COMMANDS = {
    "historico:cmd_history_check": "historico verificar",
    "indicadores:cmd_indicators_validate": "indicadores validar",
    "backtest:cmd_backtest": "backtest --rapido",
    "modelo:cmd_model_train": "modelo treinar",
    "paper:cmd_paper_replay": "paper replay",
    "aprendizado:cmd_learning_cycle": "aprendizado reavaliar",
    "aprendizado:cmd_learning_versions": "aprendizado versoes",
    "aprendizado:cmd_learning_panorama": "aprendizado panorama",
}
DEMO_COSTS_RELATIVE = Path("exemplos") / "custos_demo.json"


def build_parser() -> argparse.ArgumentParser:
    """Monta o analisador de argumentos com todos os comandos e subcomandos."""
    parser = argparse.ArgumentParser(prog="cryptoagent", description="Assistente de decisão cripto (somente leitura).")
    parser.add_argument("--config", type=Path, default=None, help="caminho do config.yaml")
    parser.add_argument("--sobrepor", type=Path, default=None, help="YAML com campos que substituem os do config (ex.: uma hipótese)")
    parser.add_argument(
        "--demo", action="store_true",
        help="modo demonstração: usa os candles de exemplo do repositório, sem internet e sem chave (comandos: " + ", ".join(DEMO_COMMANDS.values()) + ")",
    )
    parser.add_argument("--version", action="version", version=f"cryptoagent {__version__}")
    sub = parser.add_subparsers(dest="grupo")

    dados = sub.add_parser("dados", help="dados de mercado").add_subparsers(dest="acao")
    p = dados.add_parser("status", help="foto dos dados agora e saúde")
    p.set_defaults(func="dados:cmd_status")
    p = dados.add_parser("ao-vivo", help="acompanha preço e candles pelo WebSocket")
    p.add_argument("--segundos", type=float, default=None, help="para depois de N segundos")
    p.set_defaults(func="dados:cmd_live")

    hist = sub.add_parser("historico", help="histórico de candles").add_subparsers(dest="acao")
    p = hist.add_parser("baixar", help="baixa/atualiza o histórico")
    p.add_argument("--simbolos", nargs="*", default=None)
    p.add_argument("--meses", type=int, default=None)
    p.set_defaults(func="historico:cmd_history_download")
    p = hist.add_parser("verificar", help="cobertura e buracos")
    p.add_argument("--simbolos", nargs="*", default=None)
    p.set_defaults(func="historico:cmd_history_check")

    custos = sub.add_parser("custos", help="coleta e resumo de custos").add_subparsers(dest="acao")
    p = custos.add_parser("coletar", help="coleta spread/profundidade/slippage (deixe rodando)")
    p.add_argument("--segundos", type=float, default=None, help="para depois de N segundos (teste)")
    p.set_defaults(func="custos:cmd_costs_collect")
    p = custos.add_parser("resumo", help="quantos dias já foram coletados")
    p.set_defaults(func="custos:cmd_costs_summary")

    indic = sub.add_parser("indicadores", help="indicadores técnicos").add_subparsers(dest="acao")
    p = indic.add_parser("ver", help="indicadores do último candle fechado (dados ao vivo)")
    p.add_argument("--tf", default="15m", choices=list(INTERVAL_MS))
    p.add_argument("--simbolo", default=None)
    p.set_defaults(func="indicadores:cmd_indicators_show")
    p = indic.add_parser("validar", help="confere os indicadores em todo o histórico baixado")
    p.add_argument("--simbolo", default=None)
    p.set_defaults(func="indicadores:cmd_indicators_validate")

    p = sub.add_parser("analisar", help="decisão agora: COMPRAR / VENDER / ESPERAR, com motivos e níveis")
    p.set_defaults(func="decisao:cmd_analyze")

    p = sub.add_parser("painel", help="painel ao vivo no navegador + alertas")
    p.add_argument("--porta", type=int, default=None)
    p.add_argument("--sem-navegador", dest="sem_navegador", action="store_true", help="não abre o navegador sozinho")
    p.set_defaults(func="painel:cmd_panel")

    alertas = sub.add_parser("alertas", help="alertas").add_subparsers(dest="acao")
    p = alertas.add_parser("testar", help="manda um alerta de teste (terminal + Windows)")
    p.set_defaults(func="painel:cmd_alert_test")

    paper = sub.add_parser("paper", help="paper trading: sinais ao vivo com carteira virtual").add_subparsers(dest="acao")
    p = paper.add_parser("rodar", help="roda o paper trading (deixe aberto)")
    p.add_argument("--ciclos", type=int, default=None, help="para depois de N minutos (teste)")
    p.add_argument("--capital", type=float, default=None, help="capital virtual inicial em R$ (padrão: paper.capital)")
    p.add_argument("--perda-max", dest="perda_max", type=float, default=None, help="perda máxima virtual por dia em R$ (padrão: paper.perda_max_dia_brl)")
    p.set_defaults(func="paper:cmd_paper_run")
    p = paper.add_parser("relatorio", help="sinais registrados x o que o mercado fez")
    p.set_defaults(func="paper:cmd_paper_report")
    p = paper.add_parser("replay", help="paper trading sobre o histórico, com o relógio acelerado (sem internet)")
    p.add_argument("--inicio", default=None, help="primeiro dia (AAAA-MM-DD, horário local); padrão: os últimos dias completos do histórico")
    p.add_argument("--dias", type=int, default=2, help="quantos dias simular (padrão: 2)")
    p.add_argument("--variantes", nargs="*", default=None, help="variantes a simular (padrão: paper.variantes do config)")
    p.add_argument("--capital", type=float, default=None, help="capital virtual inicial em R$")
    p.add_argument("--perda-max", dest="perda_max", type=float, default=None, help="perda máxima virtual por dia em R$")
    p.add_argument("--custos-arquivo", default=None, metavar="ARQUIVO.json", help="custos congelados (os mesmos do backtest)")
    p.add_argument("--detalhes", action="store_true", help="mostra todas as decisões, não só compras, vendas e avisos")
    p.set_defaults(func="paper:cmd_paper_replay")
    p = paper.add_parser("resetar", help="guarda uma cópia do banco da simulação e recomeça a carteira virtual do zero")
    p.add_argument("--confirmar", action="store_true", help="sem isto, só mostra o que seria feito")
    p.set_defaults(func="paper:cmd_paper_reset")

    p = sub.add_parser("backtest", help="testa a estratégia no histórico (walk-forward, ablação, comparações)")
    p.add_argument("--pares", nargs="*", default=None, help="pares de operação (padrão: backtest.pares do config)")
    p.add_argument("--rapido", action="store_true", help="só parâmetros padrão + comparações (sem grade nem ablação)")
    p.add_argument(
        "--custos-arquivo", default=None, metavar="ARQUIVO.json",
        help="custos congelados: se o arquivo existir, usa os custos dele (não lê a conta nem o banco de custos); se não existir, grava nele os custos desta execução",
    )
    p.set_defaults(func="backtest:cmd_backtest")

    modelo = sub.add_parser("modelo", help="modelo desafiador (regressão logística) avaliado contra as regras").add_subparsers(dest="acao")
    p = modelo.add_parser("treinar", help="monta o conjunto de dados, avalia em walk-forward e salva o modelo")
    p.add_argument("--par", default=None, help="par de operação (padrão: o último de backtest.pares)")
    p.add_argument(
        "--custos-arquivo", default=None, metavar="ARQUIVO.json",
        help="custos congelados: se o arquivo existir, usa os custos dele; se não existir, grava nele os custos desta execução",
    )
    p.set_defaults(func="modelo:cmd_model_train")

    aprend = sub.add_parser("aprendizado", help="aprendizado contínuo: campeão x desafiantes, versões e panoramas").add_subparsers(dest="acao")
    p = aprend.add_parser("reavaliar", help="roda um ciclo de reavaliação (campeão x desafiantes, mudança de regime, panoramas)")
    p.add_argument("--ate", default=None, metavar="AAAA-MM-DD", help="ponto no tempo: usa só dados anteriores a este dia (horário local)")
    p.add_argument("--custos-arquivo", default=None, metavar="ARQUIVO.json", help="custos congelados (os mesmos do backtest)")
    p.set_defaults(func="aprendizado:cmd_learning_cycle")
    p = aprend.add_parser("versoes", help="histórico de versões da estratégia, com as métricas de cada avaliação")
    p.set_defaults(func="aprendizado:cmd_learning_versions")
    p = aprend.add_parser("panorama", help="panorama por cenário e calibração da confiança (do último ciclo)")
    p.add_argument("-n", type=int, default=15, help="quantos cenários mostrar")
    p.set_defaults(func="aprendizado:cmd_learning_panorama")

    sinais = sub.add_parser("sinais", help="histórico de sinais e associação com suas compras").add_subparsers(dest="acao")
    p = sinais.add_parser("listar", help="últimos sinais e compras associadas")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(func="decisao:cmd_signals_list")
    p = sinais.add_parser("associar", help="confirma ou corrige o sinal de uma compra")
    p.add_argument("--par", required=True)
    p.add_argument("--operacao", required=True, type=int, help="id da operação (coluna Operação em sinais listar)")
    p.add_argument("--sinal", required=True, help="número do sinal, ou 'nenhum'")
    p.set_defaults(func="decisao:cmd_signals_associate")

    saz = sub.add_parser("sazonalidade", help="análise por horário").add_subparsers(dest="acao")
    p = saz.add_parser("analisar", help="perfil por hora/dia + validação walk-forward + mapa de calor")
    p.add_argument("--simbolos", nargs="*", default=None)
    p.set_defaults(func="sazonalidade:cmd_season_analyze")
    p = saz.add_parser("agora", help="o movimento de agora x o histórico da mesma hora")
    p.add_argument("--simbolo", default=None)
    p.set_defaults(func="sazonalidade:cmd_season_now")

    sessao = sub.add_parser("sessao", help="perguntas do início do dia").add_subparsers(dest="acao")
    p = sessao.add_parser("iniciar", help="responde (ou ajusta) a sessão de hoje")
    p.add_argument("--capital", type=float, default=None, help="capital de hoje em R$")
    p.add_argument("--perda-max", dest="perda_max", type=float, default=None, help="perda máxima de hoje em R$")
    p.add_argument("--par", default=None, help="par em que vai operar (ETHBRL ou ETHUSDT)")
    p.add_argument("--saldo", default=None, choices=["USDT", "BRL", "usdt", "brl"], help="onde mantém o saldo da operação")
    p.set_defaults(func="sessao:cmd_session_start")
    p = sessao.add_parser("ver", help="mostra a sessão de hoje")
    p.set_defaults(func="sessao:cmd_session_show")

    conta = sub.add_parser("conta", help="conta da Binance (somente leitura)").add_subparsers(dest="acao")
    p = conta.add_parser("permissoes", help="confere se a chave é somente leitura")
    p.set_defaults(func="conta:cmd_account_permissions")
    p = conta.add_parser("status", help="saldo, operações do dia, resultado e limites")
    p.set_defaults(func="conta:cmd_account_status")

    diario = sub.add_parser("diario", help="diário manual (contingência sem chave)").add_subparsers(dest="acao")
    p = diario.add_parser("adicionar", help="registra uma operação feita por você")
    p.add_argument("--lado", required=True, choices=["compra", "venda"])
    p.add_argument("--qtd", required=True, type=float, help="quantidade de ETH")
    p.add_argument("--preco", required=True, type=float, help="preço por ETH na moeda do par")
    p.add_argument("--taxa", type=float, default=None, help="taxa paga, na moeda do par (padrão: estimada pelo config)")
    p.add_argument("--par", default=None)
    p.add_argument("--nota", default=None)
    p.set_defaults(func="diario:cmd_journal_add")
    p = diario.add_parser("listar", help="lista o diário")
    p.add_argument("--par", default=None)
    p.set_defaults(func="diario:cmd_journal_list")
    p = diario.add_parser("remover", help="remove um registro do diário")
    p.add_argument("--id", required=True, type=int)
    p.set_defaults(func="diario:cmd_journal_remove")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada do programa: lê os argumentos, o config e as chaves, liga o log e executa o comando. Devolve o código de saída."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    if args.demo:
        if args.sobrepor is not None:
            console.print("[red]--demo e --sobrepor não podem ser usados juntos.[/]")
            return 1
        if args.func not in DEMO_COMMANDS:
            console.print("[yellow]Este comando precisa de dados ao vivo e não roda no modo demonstração.[/]")
            console.print("Comandos disponíveis com --demo: " + "; ".join(f"cryptoagent --demo {c}" for c in DEMO_COMMANDS.values()))
            return 2
    # Só agora, com o comando escolhido, carrega o resto do programa.
    from cryptoagent import config
    from cryptoagent.account.permissions import ExcessivePermissions
    from cryptoagent.logging_setup import LOG_FILE, setup_logging
    from cryptoagent.security.redact import redact

    try:
        overlay = (args.config.resolve().parent.parent if args.config else config.find_root()) / config.DEMO_RELATIVE if args.demo else args.sobrepor
        cfg = config.load_config(args.config, overlay)
    except Exception as exc:
        console.print(f"[red]Erro no arquivo de configuração:[/] {escape(str(exc))}")
        return 1
    if args.demo:
        secrets = config.Secrets()  # o modo demonstração nem lê o .env
        if args.func in ("backtest:cmd_backtest", "modelo:cmd_model_train", "paper:cmd_paper_replay", "aprendizado:cmd_learning_cycle") and args.custos_arquivo is None:
            args.custos_arquivo = str(cfg.path(DEMO_COSTS_RELATIVE))
        console.print("[bold cyan]MODO DEMONSTRAÇÃO[/]: candles de exemplo do repositório, sem internet e sem chave da Binance.")
    else:
        secrets = config.load_secrets(cfg.root)
    setup_logging(cfg, secrets)
    try:
        return asyncio.run(load_command(args.func)(cfg, secrets, args)) or 0
    except KeyboardInterrupt:
        console.print("Encerrado pelo usuário.")
        return 130
    except ExcessivePermissions as exc:
        log.error("Recusado: chave com permissões além de leitura")
        console.print(f"[bold red]RECUSADO:[/] {escape(str(exc))}")
        return 3
    except Exception as exc:
        log.exception("Erro não tratado")
        console.print(f"[red]Erro:[/] {escape(redact(exc))}")
        console.print(f"[dim]Detalhes técnicos em {cfg.path(cfg.logs.pasta) / LOG_FILE}[/]")
        return 1
    finally:
        if args.func != "decisao:cmd_analyze":  # a decisão já imprime o aviso junto com o sinal
            console.print(f"[dim]{DISCLAIMER}[/]")
