# Dados de exemplo (modo demonstração)

Esta pasta permite rodar o projeto **sem internet e sem chave da Binance**:

```
cryptoagent --demo historico verificar
cryptoagent --demo indicadores validar
cryptoagent --demo backtest --rapido
```

## O que tem aqui

| Arquivo | Conteúdo |
|---|---|
| `historico/ETHBRL/1m.parquet`, `5m`, `15m`, `1h` | Candles do par ETH/BRL de 25/06/2026 a 01/10/2026 (UTC), copiados sem alteração do histórico público da Binance (data.binance.vision). |
| `historico/ETHBRL/4h.parquet` | Candles de 4h montados pela soma dos candles de 1h (4 por vez, alinhados em UTC). |
| `custos_demo.json` | Custos usados no backtest do demo: taxa e spread **padrão do config** (não são os de nenhuma conta). |

Cada candle tem só dados de mercado: horário (ms, UTC), abertura, máxima, mínima, fechamento
(em R$), volume, número de negócios e volume comprador. **Não há nenhum dado de conta, saldo,
ordem ou chave.**

## Como a amostra foi gerada

`python scripts/gerar_amostra_demo.py`, depois de `cryptoagent historico baixar`. O script
recorta o período, monta o 4h e reprova a amostra se a validação de qualidade
(`src/cryptoagent/data/validation.py`) achar qualquer problema.

## Limites do demo

- Análise e operação usam o mesmo par (ETHBRL). No uso normal, os sinais são calculados no
  ETHUSDT (mais líquido) e convertidos para o par de operação.
- O período fora da amostra é só setembro de 2026, com 1 mês de treino. É uma demonstração de
  que o programa roda, não uma avaliação da estratégia.
- Comandos que dependem de dados ao vivo (`analisar`, `painel`, `paper`, `conta`) não rodam
  com `--demo`.
