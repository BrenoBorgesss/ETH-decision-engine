"""Comandos de terminal.

cryptoagent dados status              foto dos dados agora (preços, candles, derivativos, saúde)
cryptoagent dados ao-vivo             acompanha preço e candles pelo WebSocket
cryptoagent historico baixar          baixa/atualiza o histórico (Parquet)
cryptoagent historico verificar       mostra cobertura e buracos do histórico
cryptoagent custos coletar            coleta spread/profundidade/slippage (deixe rodando dias)
cryptoagent custos resumo             quantos dias de coleta existem e spread médio
cryptoagent sessao iniciar            responde as perguntas do dia (capital e perda máxima)
cryptoagent sessao ver                mostra a sessão de hoje
cryptoagent conta permissoes          confere se a chave é SOMENTE leitura
cryptoagent conta status              saldo, operações do dia, resultado e limites
cryptoagent diario adicionar|listar|remover   diário manual (contingência sem chave)

Cada grupo de comandos mora em um módulo deste pacote; `app` monta tudo.
"""

from cryptoagent.cli._console import console
from cryptoagent.cli.app import build_parser, main

__all__ = ["build_parser", "console", "main"]
