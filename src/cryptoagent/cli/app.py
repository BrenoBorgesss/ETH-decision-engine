"""Monta o analisador de argumentos (todos os comandos) e a função `main`."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.markup import escape

from cryptoagent import DISCLAIMER, __version__
from cryptoagent.account.permissions import ExcessivePermissions
from cryptoagent.cli._shared import console, log
from cryptoagent.cli.backtest import cmd_backtest
from cryptoagent.cli.conta import cmd_account_permissions, cmd_account_status
from cryptoagent.cli.custos import cmd_costs_collect, cmd_costs_summary
from cryptoagent.cli.dados import cmd_live, cmd_status
from cryptoagent.cli.decisao import cmd_analyze, cmd_signals_associate, cmd_signals_list
from cryptoagent.cli.diario import cmd_journal_add, cmd_journal_list, cmd_journal_remove
from cryptoagent.cli.historico import cmd_history_check, cmd_history_download
from cryptoagent.cli.indicadores import cmd_indicators_show, cmd_indicators_validate
from cryptoagent.cli.painel import cmd_alert_test, cmd_panel
from cryptoagent.cli.paper import cmd_paper_report, cmd_paper_run
from cryptoagent.cli.sazonalidade import cmd_season_analyze, cmd_season_now
from cryptoagent.cli.sessao import cmd_session_show, cmd_session_start
from cryptoagent.config import load_config, load_secrets
from cryptoagent.logging_setup import LOG_FILE, setup_logging
from cryptoagent.security.redact import redact
from cryptoagent.timeutils import INTERVAL_MS

# -------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    """Monta o analisador de argumentos com todos os comandos e subcomandos."""
    parser = argparse.ArgumentParser(prog="cryptoagent", description="Assistente de decisão cripto (somente leitura).")
    parser.add_argument("--config", type=Path, default=None, help="caminho do config.yaml")
    parser.add_argument("--sobrepor", type=Path, default=None, help="YAML com campos que substituem os do config (ex.: uma hipótese)")
    parser.add_argument("--version", action="version", version=f"cryptoagent {__version__}")
    sub = parser.add_subparsers(dest="grupo")

    dados = sub.add_parser("dados", help="dados de mercado").add_subparsers(dest="acao")
    p = dados.add_parser("status", help="foto dos dados agora e saúde")
    p.set_defaults(func=cmd_status)
    p = dados.add_parser("ao-vivo", help="acompanha preço e candles pelo WebSocket")
    p.add_argument("--segundos", type=float, default=None, help="para depois de N segundos")
    p.set_defaults(func=cmd_live)

    hist = sub.add_parser("historico", help="histórico de candles").add_subparsers(dest="acao")
    p = hist.add_parser("baixar", help="baixa/atualiza o histórico")
    p.add_argument("--simbolos", nargs="*", default=None)
    p.add_argument("--meses", type=int, default=None)
    p.set_defaults(func=cmd_history_download)
    p = hist.add_parser("verificar", help="cobertura e buracos")
    p.add_argument("--simbolos", nargs="*", default=None)
    p.set_defaults(func=cmd_history_check)

    custos = sub.add_parser("custos", help="coleta e resumo de custos").add_subparsers(dest="acao")
    p = custos.add_parser("coletar", help="coleta spread/profundidade/slippage (deixe rodando)")
    p.add_argument("--segundos", type=float, default=None, help="para depois de N segundos (teste)")
    p.set_defaults(func=cmd_costs_collect)
    p = custos.add_parser("resumo", help="quantos dias já foram coletados")
    p.set_defaults(func=cmd_costs_summary)

    indic = sub.add_parser("indicadores", help="indicadores técnicos").add_subparsers(dest="acao")
    p = indic.add_parser("ver", help="indicadores do último candle fechado (dados ao vivo)")
    p.add_argument("--tf", default="15m", choices=list(INTERVAL_MS))
    p.add_argument("--simbolo", default=None)
    p.set_defaults(func=cmd_indicators_show)
    p = indic.add_parser("validar", help="confere os indicadores em todo o histórico baixado")
    p.add_argument("--simbolo", default=None)
    p.set_defaults(func=cmd_indicators_validate)

    p = sub.add_parser("analisar", help="decisão agora: COMPRAR / VENDER / ESPERAR, com motivos e níveis")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("painel", help="painel ao vivo no navegador + alertas")
    p.add_argument("--porta", type=int, default=None)
    p.add_argument("--sem-navegador", dest="sem_navegador", action="store_true", help="não abre o navegador sozinho")
    p.set_defaults(func=cmd_panel)

    alertas = sub.add_parser("alertas", help="alertas").add_subparsers(dest="acao")
    p = alertas.add_parser("testar", help="manda um alerta de teste (terminal + Windows)")
    p.set_defaults(func=cmd_alert_test)

    paper = sub.add_parser("paper", help="paper trading: sinais ao vivo com carteira virtual").add_subparsers(dest="acao")
    p = paper.add_parser("rodar", help="roda o paper trading (deixe aberto)")
    p.add_argument("--ciclos", type=int, default=None, help="para depois de N minutos (teste)")
    p.set_defaults(func=cmd_paper_run)
    p = paper.add_parser("relatorio", help="sinais registrados x o que o mercado fez")
    p.set_defaults(func=cmd_paper_report)

    p = sub.add_parser("backtest", help="testa a estratégia no histórico (walk-forward, ablação, comparações)")
    p.add_argument("--pares", nargs="*", default=None, help="pares de operação (padrão: backtest.pares do config)")
    p.add_argument("--rapido", action="store_true", help="só parâmetros padrão + comparações (sem grade nem ablação)")
    p.add_argument(
        "--custos-arquivo", default=None, metavar="ARQUIVO.json",
        help="custos congelados: se o arquivo existir, usa os custos dele (não lê a conta nem o banco de custos); se não existir, grava nele os custos desta execução",
    )
    p.set_defaults(func=cmd_backtest)

    sinais = sub.add_parser("sinais", help="histórico de sinais e associação com suas compras").add_subparsers(dest="acao")
    p = sinais.add_parser("listar", help="últimos sinais e compras associadas")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(func=cmd_signals_list)
    p = sinais.add_parser("associar", help="confirma ou corrige o sinal de uma compra")
    p.add_argument("--par", required=True)
    p.add_argument("--operacao", required=True, type=int, help="id da operação (coluna Operação em sinais listar)")
    p.add_argument("--sinal", required=True, help="número do sinal, ou 'nenhum'")
    p.set_defaults(func=cmd_signals_associate)

    saz = sub.add_parser("sazonalidade", help="análise por horário").add_subparsers(dest="acao")
    p = saz.add_parser("analisar", help="perfil por hora/dia + validação walk-forward + mapa de calor")
    p.add_argument("--simbolos", nargs="*", default=None)
    p.set_defaults(func=cmd_season_analyze)
    p = saz.add_parser("agora", help="o movimento de agora x o histórico da mesma hora")
    p.add_argument("--simbolo", default=None)
    p.set_defaults(func=cmd_season_now)

    sessao = sub.add_parser("sessao", help="perguntas do início do dia").add_subparsers(dest="acao")
    p = sessao.add_parser("iniciar", help="responde (ou ajusta) a sessão de hoje")
    p.add_argument("--capital", type=float, default=None, help="capital de hoje em R$")
    p.add_argument("--perda-max", dest="perda_max", type=float, default=None, help="perda máxima de hoje em R$")
    p.add_argument("--par", default=None, help="par em que vai operar (ETHBRL ou ETHUSDT)")
    p.add_argument("--saldo", default=None, choices=["USDT", "BRL", "usdt", "brl"], help="onde mantém o saldo da operação")
    p.set_defaults(func=cmd_session_start)
    p = sessao.add_parser("ver", help="mostra a sessão de hoje")
    p.set_defaults(func=cmd_session_show)

    conta = sub.add_parser("conta", help="conta da Binance (somente leitura)").add_subparsers(dest="acao")
    p = conta.add_parser("permissoes", help="confere se a chave é somente leitura")
    p.set_defaults(func=cmd_account_permissions)
    p = conta.add_parser("status", help="saldo, operações do dia, resultado e limites")
    p.set_defaults(func=cmd_account_status)

    diario = sub.add_parser("diario", help="diário manual (contingência sem chave)").add_subparsers(dest="acao")
    p = diario.add_parser("adicionar", help="registra uma operação feita por você")
    p.add_argument("--lado", required=True, choices=["compra", "venda"])
    p.add_argument("--qtd", required=True, type=float, help="quantidade de ETH")
    p.add_argument("--preco", required=True, type=float, help="preço por ETH na moeda do par")
    p.add_argument("--taxa", type=float, default=None, help="taxa paga, na moeda do par (padrão: estimada pelo config)")
    p.add_argument("--par", default=None)
    p.add_argument("--nota", default=None)
    p.set_defaults(func=cmd_journal_add)
    p = diario.add_parser("listar", help="lista o diário")
    p.add_argument("--par", default=None)
    p.set_defaults(func=cmd_journal_list)
    p = diario.add_parser("remover", help="remove um registro do diário")
    p.add_argument("--id", required=True, type=int)
    p.set_defaults(func=cmd_journal_remove)
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
    try:
        cfg = load_config(args.config, args.sobrepor)
    except Exception as exc:
        console.print(f"[red]Erro no arquivo de configuração:[/] {escape(str(exc))}")
        return 1
    secrets = load_secrets(cfg.root)
    setup_logging(cfg, secrets)
    try:
        return asyncio.run(args.func(cfg, secrets, args)) or 0
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
        if args.func is not cmd_analyze:  # a decisão já imprime o aviso junto com o sinal
            console.print(f"[dim]{DISCLAIMER}[/]")
