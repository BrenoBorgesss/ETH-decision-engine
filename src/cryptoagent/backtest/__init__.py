"""Backtest: o MESMO motor de decisão do modo ao vivo, rodando candle a candle no histórico.

Regras de honestidade:
- Só candles fechados antes de cada decisão; nada do futuro (testado).
- Taxas, spread e slippage em toda entrada e saída.
- Walk-forward: parâmetros escolhidos no treino e medidos em meses que o treino não viu.
- Todos os resultados aparecem, inclusive os ruins.
"""
